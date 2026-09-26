"""`llmbox bench`: run the standard suite against a served model and turn it into three numbers:
capability (0-100 ± 95% CI), speed (tok/s + typical agent step), and solved tasks per hour on this hardware."""
from __future__ import annotations

import json
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import client, suite
from .suite.common import Item

TYPICAL_STEP = {"new_prompt_tokens": 3000, "output_tokens": 600}   # one agent turn: read tool output, think, act


# blocks whose grade depends only on the final answer text: a saved run can be re-graded after a grader fix
TEXT_GRADED = {"longctx", "writing", "reasoning", "code"}

# wall-clock limit per item (seconds): a model that needs longer is graded on what it reached (v0.7; v0.6 had none)
DEADLINE_S = {"agentic": 2700}


def run_item(base_url: str, model: str, it: Item, api_key: str | None = None, deadline_scale: float = 1.0) -> dict:
    """base_url is an OpenAI-compatible endpoint, or "claude-code[:effort]" for a frontier reference run through the
    user's Claude subscription (see frontier.py). deadline_scale: > 1 when items share the server (parallel slots)."""
    t0 = time.time()
    deadline = it.meta.get("deadline_s", DEADLINE_S.get(it.block, 1800)) * deadline_scale
    try:
        if base_url.startswith("claude-code"):
            from . import frontier
            effort = base_url.split(":", 1)[1] if ":" in base_url else None
            res = frontier.run_item(model, it, effort=effort, deadline_s=deadline)
        else:
            res = client.run_chat(base_url, model, it.messages, tools=it.tools, tool_impl=it.tool_impl,
                                  max_tokens=it.max_tokens, max_steps=it.meta.get("max_steps", 16), api_key=api_key,
                                  deadline_s=deadline, followups=it.meta.get("followups"))
        score = float(it.check(res["final"], res))
        err = None
    except Exception as e:  # a crash/timeout of one item scores 0 and is reported, it does not stop the run
        res, score, err = {"timings": [], "usage": {}, "seconds": round(time.time() - t0, 1), "finish_reason": None,
                           "steps": 0, "final": ""}, 0.0, str(e)[:300]
    return {"id": it.id, "block": it.block, "kind": it.kind, "lang": it.lang, "score": round(score, 4), "error": err,
            "seconds": res["seconds"], "steps": res.get("steps"), "finish_reason": res.get("finish_reason"),
            "usage": res.get("usage"), "timings": res.get("timings"), "final": res.get("final") or "",
            "final_tail": (res.get("final") or "")[-300:], "reasoning_tail": _reasoning_tail(res), "expected": it.meta.get("expected"),
            "tool_calls": [{"name": c.get("name"), "args": _clip(c.get("args")), "result": _clip(c.get("result"), 300)}
                           for c in (res.get("tool_calls") or [])][:400]}


def _clip(v, n: int = 2000):
    """Tool-call log for audits: keep it small (agent file writes can be large)."""
    s = json.dumps(v, ensure_ascii=False, default=str)
    return v if len(s) <= n else s[:n] + "…"


def _reasoning_tail(res: dict) -> str:
    for m in reversed(res.get("messages") or []):
        if m.get("role") == "assistant" and m.get("reasoning_content"):
            return m["reasoning_content"][-1500:]
    return ""


def _block_scores(rows: list[dict]) -> dict:
    out = {}
    for b in suite.WEIGHTS:
        s = [r["score"] for r in rows if r["block"] == b]
        if s:
            out[b] = sum(s) / len(s)
    return out


def capability(rows: list[dict]) -> float:
    bs = _block_scores(rows)
    w = {b: suite.WEIGHTS[b] for b in bs}
    tot = sum(w.values())
    return 100 * sum(bs[b] * w[b] for b in bs) / tot if tot else 0.0


def capability_ci(rows: list[dict], n_boot: int = 2000, seed: int = 7) -> tuple[float, float]:
    """95% bootstrap CI, resampling items within each block (blocks keep their weights)."""
    r = random.Random(seed)
    by = {}
    for x in rows:
        by.setdefault(x["block"], []).append(x)
    vals = []
    for _ in range(n_boot):
        sample = [r.choice(v) for v in by.values() for _ in v]
        vals.append(capability(sample))
    vals.sort()
    return vals[int(0.025 * n_boot)], vals[int(0.975 * n_boot)]


DEPTH_BUCKETS = [(0, 8192, "0-8k"), (8192, 32768, "8-32k"), (32768, 98304, "32-96k"), (98304, 10**9, "96k+")]


def _by_depth(rows: list[dict]) -> dict:
    """Decode and prefill speed by context depth of the request (long agent sessions and long documents run deep,
    where attention over the KV cache slows every token). Needs the per-request 'ctx' recorded since v0.7 (2026-09-26)."""
    acc = {name: [0.0, 0.0, 0.0, 0.0, 0] for *_r, name in DEPTH_BUCKETS}
    for r in rows:
        for t in r.get("timings") or []:
            c = t.get("ctx")
            if c is None:
                continue
            name = next(n for lo, hi, n in DEPTH_BUCKETS if lo <= c < hi)
            a = acc[name]
            if t.get("predicted_n") and t.get("predicted_per_second"):
                a[0] += t["predicted_n"]
                a[1] += t["predicted_n"] / t["predicted_per_second"]
                a[4] += 1
            if t.get("prompt_n") and t.get("prompt_per_second") and t["prompt_n"] > 256:
                a[2] += t["prompt_n"]
                a[3] += t["prompt_n"] / t["prompt_per_second"]
    return {n: {"decode_tps": round(a[0] / a[1], 1) if a[1] else None, "prefill_tps": round(a[2] / a[3], 1) if a[3] else None,
                "requests": a[4]} for n, a in acc.items() if a[4] or a[3]}


def speed(rows: list[dict]) -> dict:
    gen_n = gen_s = pp_n = pp_s = 0.0
    dec = []
    for r in rows:
        for t in r.get("timings") or []:
            if t.get("predicted_n") and t.get("predicted_per_second"):
                gen_n += t["predicted_n"]
                gen_s += t["predicted_n"] / t["predicted_per_second"]
                dec.append(t["predicted_per_second"])
            if t.get("prompt_n") and t.get("prompt_per_second") and t["prompt_n"] > 256:
                pp_n += t["prompt_n"]
                pp_s += t["prompt_n"] / t["prompt_per_second"]
    decode = gen_n / gen_s if gen_s else None
    prefill = pp_n / pp_s if pp_s else None
    step = (TYPICAL_STEP["new_prompt_tokens"] / prefill + TYPICAL_STEP["output_tokens"] / decode) if decode and prefill else None
    label = None if decode is None else ("fast" if decode >= 50 else "comfortable" if decode >= 25 else "slow")
    return {"decode_tps": round(decode, 1) if decode else None, "prefill_tps": round(prefill, 1) if prefill else None,
            "by_depth": _by_depth(rows),
            "decode_tps_median_request": round(statistics.median(dec), 1) if dec else None,
            "typical_agent_step_s": round(step, 1) if step else None, "label": label,
            "output_tokens": int(sum((r.get("usage") or {}).get("completion_tokens") or 0 for r in rows))}


def summarize(rows: list[dict], wall_s: float) -> dict:
    lo, hi = capability_ci(rows)
    cap = capability(rows)
    solved = sum(r["score"] for r in rows)
    return {"capability": round(cap, 1), "capability_ci95": [round(lo, 1), round(hi, 1)],
            "blocks": {b: round(100 * v, 1) for b, v in _block_scores(rows).items()},
            "items": len(rows), "solved": round(solved, 2), "errors": sum(1 for r in rows if r["error"]),
            "wall_minutes": round(wall_s / 60, 1), "solved_per_hour": round(solved / (wall_s / 3600), 1) if wall_s else None,
            "speed": speed(rows)}


def run(base_url: str, model: str, tier: str = "quick", seed0: int = 0, blocks: list[str] | None = None,
        api_key: str | None = None, progress=print, jsonl_path: str | None = None, resume: str | None = None,
        rerun: list[str] | None = None, parallel: int = 1) -> dict:
    """resume: a jsonl of an interrupted run of the same suite - its finished items are reused, not re-run (items that
    ended in an error, e.g. a subscription limit, are re-run)."""
    items = suite.build(tier, seed0, blocks)
    done = {}
    if resume:
        for line in open(resume):
            r = json.loads(line)
            if not r.get("error") and r["id"] not in (rerun or []):   # rerun: items whose grader was fixed since
                done[r["id"]] = r
    rows = []
    t0 = time.time() - sum(r["seconds"] for r in done.values())
    out = open(jsonl_path, "a") if jsonl_path else None
    if parallel > 1:
        rows = _run_parallel(base_url, model, items, done, api_key, parallel, out, progress)
        items = []   # all handled above
    for i, it in enumerate(items, 1):
        if it.id in done:
            row = done[it.id]
            if it.block in TEXT_GRADED and row.get("final") is not None:   # re-grade with the current (possibly fixed) grader
                new_score = round(float(it.check(row["final"], row)), 4)
                if new_score != row["score"]:
                    row = dict(row, score=new_score, regraded_from=row["score"])
            elif getattr(it, "tool_impl", None) and hasattr(it.tool_impl, "__self__") and hasattr(it.tool_impl.__self__, "cleanup"):
                it.tool_impl.__self__.cleanup()   # unused agentic workspace
            rows.append(row)
            progress(f"  [{i:3d}/{len(items)}] {row['score']:4.2f}  (reused{', regraded from ' + str(row['regraded_from']) if 'regraded_from' in row else ''})  {it.id}")
            continue
        row = run_item(base_url, model, it, api_key)
        rows.append(row)
        if out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
        progress(f"  [{i:3d}/{len(items)}] {row['score']:4.2f}  {row['seconds']:6.1f}s  {it.id}"
                 + (f"  ERROR {row['error'][:80]}" if row["error"] else ""))
    s = summarize(rows, time.time() - t0)
    if parallel > 1:   # per-request speeds were measured under contention: the caller replaces them with a 1-stream probe
        s["parallel"] = parallel
    return {"suite": {"name": "llmbox-standard", "version": suite.VERSION, "tier": tier, "seed0": seed0,
                      "weights": suite.WEIGHTS, "blocks": sorted(blocks) if blocks else None}, "summary": s, "rows": rows}


def regrade(rec: dict) -> tuple[dict, list[str]]:
    """Re-score the text-graded rows of a saved suite result with the current graders (after a grader fix).
    Rows that depend on a tool world or a workspace are kept (they need a re-run). Returns (new record, changes)."""
    su = rec["suite"]
    items = {it.id: it for it in suite.build(su["tier"], su.get("seed0", 0), blocks=sorted(TEXT_GRADED))}
    rows, changes = [], []
    for r in rec["rows"]:
        it = items.get(r["id"])
        if it is not None and r.get("final") is not None and not r.get("error"):
            ns = round(float(it.check(r["final"], r)), 4)
            if ns != r["score"]:
                changes.append(f"{r['id']}: {r['score']} -> {ns}")
                r = dict(r, score=ns, regraded_from=r["score"])
        rows.append(r)
    wall = rec["summary"].get("wall_minutes", 0) * 60
    new = dict(rec, rows=rows, summary=summarize(rows, wall))
    new["summary"]["speed"] = rec["summary"].get("speed", new["summary"]["speed"])
    new["regraded"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return new, changes


def _run_parallel(base_url: str, model: str, items: list, done: dict, api_key, n: int, out, progress) -> list[dict]:
    """Capability run with n concurrent items on a server with n slots (unified KV). Long-document items still run one
    at a time afterwards (two 200k-token prompts do not fit one KV pool). Item deadlines scale by n."""
    lock = threading.Lock()
    rows: dict = {}
    counter = [0]

    def record(it, row, reused=False):
        with lock:
            rows[it.id] = row
            counter[0] += 1
            if out and not reused:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
            took = "(reused)" if reused else f"{row['seconds']:6.1f}s"
            progress(f"  [{counter[0]:3d}/{len(items)}] {row['score']:4.2f}  {took}  {it.id}"
                     + (f"  ERROR {row['error'][:80]}" if row.get("error") else ""))

    todo = []
    for it in items:
        if it.id in done:
            record(it, done[it.id], reused=True)
        else:
            todo.append(it)
    par = [it for it in todo if it.block != "longctx"]
    seq = [it for it in todo if it.block == "longctx"]
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = {ex.submit(run_item, base_url, model, it, api_key, float(n)): it for it in par}
        for f in as_completed(futs):
            record(futs[f], f.result())
    for it in seq:
        record(it, run_item(base_url, model, it, api_key))
    return [rows[it.id] for it in items]


def speed_probe(base_url: str, model: str, depths: tuple = (2000, 32000, 96000), api_key: str | None = None) -> dict:
    """Single-stream speed at several context depths (unique prompts, so no cache hits): what one user feels.
    Used when the capability run was parallel, and to fill speed-by-depth for older results."""
    words = ("the quick brown fox jumps over lazy dogs while engineers measure throughput latency bandwidth memory "
             "cache river mountain signal").split()
    res = {}
    client.run_chat(base_url, model, [{"role": "user", "content": "Say OK."}], max_tokens=8, api_key=api_key,
                    extra={"chat_template_kwargs": {"enable_thinking": False}})   # load / warm up
    for d in depths:
        r = random.Random(d)
        text = f"[probe {d} {time.time()}] " + " ".join(r.choice(words) for _ in range(int(d / 1.25)))
        out = client.run_chat(base_url, model, [{"role": "user", "content": text + "\nNow write a short story about a lighthouse keeper."}],
                              max_tokens=256, api_key=api_key, extra={"chat_template_kwargs": {"enable_thinking": False}})
        t = (out.get("timings") or [{}])[-1]
        res[d] = {"ctx": t.get("ctx"), "prefill_tps": round(t.get("prompt_per_second") or 0, 1),
                  "decode_tps": round(t.get("predicted_per_second") or 0, 1)}
    shallow, mid = res[depths[0]], res[depths[min(1, len(depths) - 1)]]
    step = TYPICAL_STEP["new_prompt_tokens"] / mid["prefill_tps"] + TYPICAL_STEP["output_tokens"] / shallow["decode_tps"] \
        if mid["prefill_tps"] and shallow["decode_tps"] else None
    dec = shallow["decode_tps"]
    return {"decode_tps": dec, "prefill_tps": mid["prefill_tps"], "typical_agent_step_s": round(step, 1) if step else None,
            "label": None if not dec else ("fast" if dec >= 50 else "comfortable" if dec >= 25 else "slow"),
            "by_depth": {f"{v['ctx'] // 1000 if v['ctx'] else d // 1000}k": {"decode_tps": v["decode_tps"], "prefill_tps": v["prefill_tps"]}
                         for d, v in res.items()},
            "method": "single-stream probe"}
