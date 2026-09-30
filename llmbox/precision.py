"""Where one more run narrows the ranking most: `llmbox queue precision`.

A model's 95% range shrinks with its answers (about as 1/sqrt(n)), and a 40-minute run adds as many answers as the
model is fast enough to give (K2-Horizon ~9, a fast MoE ~30). The ranking is decided at the top, where models are not
measurably apart yet: a run counts double there. Runs are handed out one at a time to the model whose range the next
run would narrow most, until the budget is spent or every range is within the target."""
from __future__ import annotations

import math

from . import report


def plan(host: str = "box", jobs: int = 8, target: float = 3.0, skip: set | None = None) -> list[dict]:
    pool = report.current_pool()
    ms = []
    for (rid, h), p in pool.items():
        if h != host or not report.ranked_now(rid, h) or rid in (skip or set()):
            continue
        lo, hi = p["score"]["ci95"]
        ms.append({"rid": rid, "n": p["score"]["n"], "half": (hi - lo) / 2, "cap": p["score"]["capability"], "lo": lo, "hi": hi,
                   "per_run": max(5, round(p["score"]["n"] / max(1, p["runs"]))), "runs": 0})
    if not ms:
        return []
    top = max(ms, key=lambda m: m["cap"])
    for m in ms:   # not measurably apart from the leader: where the ranking is still open
        m["top"] = m["hi"] >= top["lo"]
        # its place is open when its range overlaps a neighbour's; a model clearly below everyone gains little from a run
        m["open"] = any(o is not m and o["lo"] <= m["hi"] and m["lo"] <= o["hi"] for o in ms)
    def gain(m):   # how much the next run would narrow this range (the 1/sqrt(n) law): x2 at the top, x0.25 when its place is settled
        n = m["n"] + m["runs"] * m["per_run"]
        now = m["half"] * math.sqrt(m["n"] / n)
        after = m["half"] * math.sqrt(m["n"] / (n + m["per_run"]))
        return (now - after) * (2 if m["top"] else 1 if m["open"] else 0.25) if now > target else 0.0
    for _ in range(jobs):
        best = max(ms, key=gain)
        if gain(best) <= 0:
            break
        best["runs"] += 1
    for m in ms:
        m["half_after"] = m["half"] * math.sqrt(m["n"] / (m["n"] + m["runs"] * m["per_run"]))
    return sorted((m for m in ms if m["runs"]), key=lambda m: (-m["top"], -m["runs"], m["rid"]))
