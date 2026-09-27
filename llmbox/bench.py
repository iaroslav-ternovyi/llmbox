"""`llmbox bench`: run the standard suite against a served model and turn it into three numbers:
capability (0-100 ± 95% CI), speed (tok/s + typical agent step), and solved tasks per hour on this hardware."""
from __future__ import annotations

import gzip
import json
import os
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import client, suite
from .suite.common import Item

TYPICAL_STEP = {"new_prompt_tokens": 3000, "output_tokens": 600}   # one agent turn: read tool output, think, act


# blocks whose grade depends only on the final answer text: a saved run can be re-graded after a grader fix
TEXT_GRADED = {"longctx", "writing", "reasoning", "code", "techhelp", "knowledge"}

# wall-clock per item (v0.7; v0.6 had none): after it no new request starts and the item is graded on the state
# reached. dev7: agentic 15 min
# (was 45): a quick run is 30-40 min, and one 36-min bug hunt made a run take 82 (a time budget, not a token cap -
# reasoning loops are caught by content)
DEADLINE_S = {"agentic": 900}

# full thinking of every step, one gzip file per item (~/.llmbox/traces/<run>/<item>.json.gz): for loop / budget audits
TRACES = os.path.join(os.path.expanduser("~"), ".llmbox", "traces")
# the reasoning-budget message of the anti-loop launchers: its presence means the budget cut the thinking
BUDGET_MSG = "I have reasoned enough"


def run_item(base_url: str, model: str, it: Item, api_key: str | None = None, deadline_scale: float = 1.0,
             trace_dir: str | None = None) -> dict:
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
        # deferred items (explanations) are graded by the reader model after the whole run, in one go: grade_deferred
        score = 0.0 if it.meta.get("deferred") else float(it.check(res["final"], res))
        err = None
    except Exception as e:  # a crash/timeout of one item scores 0 and is reported, it does not stop the run
        res, score, err = {"timings": [], "usage": {}, "seconds": round(time.time() - t0, 1), "finish_reason": None,
                           "steps": 0, "final": ""}, 0.0, str(e)[:300]
    thinking = _thinking(res)
    if trace_dir and thinking:
        _save_trace(trace_dir, it.id, thinking, res)
    return {"id": it.id, "block": it.block, "kind": it.kind, "lang": it.lang, "score": round(score, 4), "error": err,
            **({"pending": it.meta["deferred"]} if it.meta.get("deferred") and not err else {}),
            "seconds": res["seconds"], "steps": res.get("steps"), "finish_reason": res.get("finish_reason"),
            "usage": res.get("usage"), "timings": res.get("timings"), "final": res.get("final") or "",
            "final_tail": (res.get("final") or "")[-300:], "reasoning_tail": _reasoning_tail(res), "expected": it.meta.get("expected"),
            "reasoning_cut": sum(BUDGET_MSG in t for t in thinking),
            "max_reply_tokens": max([t.get("predicted_n") or 0 for t in res.get("timings") or []] or [0]),
            "tool_calls": [{"name": c.get("name"), "args": _clip(c.get("args")), "result": _clip(c.get("result"), 300)}
                           for c in (res.get("tool_calls") or [])][:400]}


def grade_deferred(base_url: str, items: list, rows: list[dict], out=None, progress=print) -> None:
    """Score the rows whose grading needs the reader model (suite/explain.py), in place. Run after all other items so
    the served model is swapped for the reader once. A reader failure leaves the row pending with an error; a resumed
    run grades it without re-running the item."""
    from . import reader
    reader.configure(base_url)
    by_id = {it.id: it for it in items}
    todo = [r for r in rows if r.get("pending") and r["id"] in by_id]
    if todo:
        progress(f"  grading {len(todo)} explanation(s) with the reader model {reader.MODEL}")
    for r in todo:
        it = by_id[r["id"]]
        try:
            r["score"] = round(float(it.check(r["final"], r)), 4)
            r.pop("pending", None)
            r.pop("reader_error", None)
        except Exception as e:
            r["reader_error"] = str(e)[:300]
            progress(f"  reader failed on {it.id}: {r['reader_error'][:120]}")
            continue
        if out:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
            out.flush()
        progress(f"  [reader] {r['score']:4.2f}  {it.id}")


def _clip(v, n: int = 2000):
    """Tool-call log for audits: keep it small (agent file writes can be large)."""
    s = json.dumps(v, ensure_ascii=False, default=str)
    return v if len(s) <= n else s[:n] + "…"


def _thinking(res: dict) -> list[str]:
    return [m["reasoning_content"] for m in res.get("messages") or [] if m.get("role") == "assistant" and m.get("reasoning_content")]


def _save_trace(trace_dir: str, item_id: str, thinking: list[str], res: dict) -> None:
    try:
        os.makedirs(trace_dir, exist_ok=True)
        with gzip.open(os.path.join(trace_dir, item_id + ".json.gz"), "wt", encoding="utf-8") as f:
            json.dump({"id": item_id, "thinking": thinking, "finish_reason": res.get("finish_reason"),
                       "reply_tokens": [t.get("predicted_n") for t in res.get("timings") or []]}, f, ensure_ascii=False)
    except OSError:
        pass   # an audit aid: never fail a run over it


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


def rescore(rec: dict) -> dict:
    """A saved suite record scored with the current block weights (the weights change between suite versions; the
    tasks and their scores do not). The number the run published is kept as summary.capability_published."""
    rows = [r for r in rec.get("rows") or [] if not r.get("pending")]
    s = rec.get("summary") or {}
    if not rows or s.get("weights_used") == suite.WEIGHTS:
        return rec
    new = dict(s, capability=round(capability(rows), 1), capability_ci95=[round(x, 1) for x in capability_ci(rows)],
               capability_published=s.get("capability_published", s.get("capability")), weights_used=dict(suite.WEIGHTS))
    return dict(rec, summary=new)


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
            "reasoning_cut_items": sum(1 for r in rows if r.get("reasoning_cut")),
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
    trace_dir = os.path.join(TRACES, os.path.splitext(os.path.basename(jsonl_path))[0]) if jsonl_path else None
    if parallel > 1:
        rows = _run_parallel(base_url, model, items, done, api_key, parallel, out, progress, trace_dir)
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
            if out and os.path.abspath(resume) != os.path.abspath(jsonl_path or ""):   # carry reused rows into this run's own jsonl
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
            progress(f"  [{i:3d}/{len(items)}] {row['score']:4.2f}  (reused{', regraded from ' + str(row['regraded_from']) if 'regraded_from' in row else ''})  {it.id}")
            continue
        row = run_item(base_url, model, it, api_key, trace_dir=trace_dir)
        rows.append(row)
        if out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
        progress(f"  [{i:3d}/{len(items)}] {row['score']:4.2f}  {row['seconds']:6.1f}s  {it.id}"
                 + (f"  ERROR {row['error'][:80]}" if row["error"] else ""))
    grade_deferred(base_url, suite.build(tier, seed0, blocks), rows, out, progress)
    s = summarize(rows, time.time() - t0)
    if parallel > 1:   # per-request speeds were measured under contention: the caller replaces them with a 1-stream probe
        s["parallel"] = parallel
    return {"suite": {"name": "llmbox-standard", "version": suite.VERSION, "content_hash": suite.content_hash(), "tier": tier, "seed0": seed0,
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


def _run_parallel(base_url: str, model: str, items: list, done: dict, api_key, n: int, out, progress,
                  trace_dir: str | None = None) -> list[dict]:
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
        futs = {ex.submit(run_item, base_url, model, it, api_key, float(n), trace_dir): it for it in par}
        for f in as_completed(futs):
            record(futs[f], f.result())
    for it in seq:
        record(it, run_item(base_url, model, it, api_key, trace_dir=trace_dir))
    return [rows[it.id] for it in items]


# Probe context: this package's sources frozen on 2026-09-27 (the live sources changed the prompt with every commit).
PROBE_CORPUS = os.path.join(os.path.dirname(__file__), "probe_corpus.txt.gz")
PROBE_METHOD = "single-stream probe v2: frozen corpus, 3 seeded code tasks per depth, median decode"
# Code tasks with long answers (> 512 tokens), so every sample is a full-length decode; fixed seeds keep the sampled
# text, and with it the speculative acceptance, close from run to run.
PROBE_TASKS = [
    ("Rewrite this function with full type hints, a docstring, input validation and clear error messages. "
     "Output only the code.\n\n```python\ndef merge_intervals(xs):\n    xs = sorted(xs)\n    out = []\n    for a, b in xs:\n"
     "        if out and a <= out[-1][1]:\n            out[-1][1] = max(out[-1][1], b)\n        else:\n            out.append([a, b])\n"
     "    return out\n```", 11),
    ("Write thorough pytest unit tests for this function: normal cases, edge cases and errors, one test per behaviour. "
     "Output only the code.\n\n```python\ndef parse_duration(s):\n    units = {'ms': 0.001, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}\n"
     "    total, num = 0.0, ''\n    for ch in s.replace(' ', ''):\n        if ch.isdigit() or ch == '.':\n            num += ch\n"
     "        else:\n            total += float(num) * units[ch]\n            num = ''\n    return total\n```", 22),
    ("Refactor this class into a small module: split the responsibilities into helpers, add type hints and docstrings, "
     "and keep the behaviour. Output only the code.\n\n```python\nclass Cache:\n    def __init__(self, n, ttl):\n"
     "        self.n, self.ttl, self.d = n, ttl, {}\n    def get(self, k, now):\n        v = self.d.get(k)\n"
     "        if v is None or now - v[1] > self.ttl:\n            self.d.pop(k, None)\n            return None\n        return v[0]\n"
     "    def put(self, k, v, now):\n        if len(self.d) >= self.n:\n            self.d.pop(min(self.d, key=lambda x: self.d[x][1]))\n"
     "        self.d[k] = (v, now)\n```", 33),
]


def speed_probe(base_url: str, model: str, depths: tuple = (2000, 32000, 96000), api_key: str | None = None,
                repeats: int = 3, gen_tokens: int = 512) -> dict:
    """Single-stream speed at several context depths: what one user feels. Content is real code and the answer is
    code, so speculative decoding (MTP) sees realistic text - random words made MTP models look ~35% slower than on
    real tasks (Tiel 42.8 vs ~64 tok/s). Sampling stays the recipe's (greedy flatters MTP), so one sample of a few
    hundred tokens swung +-15%; now each depth decodes `repeats` seeded tasks and reports the median. The first request
    of a depth has a unique prefix (a real prefill, measured); the others reuse it from the prompt cache."""
    src = gzip.open(PROBE_CORPUS, "rt").read()
    run_id = f"{time.time():.0f}"
    res = {}
    client.run_chat(base_url, model, [{"role": "user", "content": "Say OK."}], max_tokens=8, api_key=api_key,
                    extra={"chat_template_kwargs": {"enable_thinking": False}})   # load / warm up
    for d in depths:
        body = (src * (1 + (d * 4) // len(src)))[: d * 3]   # ~3 chars per code token
        prefix = f"# probe {d} {run_id}\n" + body + "\n\n"
        runs = []
        for i in range(repeats):
            task, seed = PROBE_TASKS[i % len(PROBE_TASKS)]
            out = client.run_chat(base_url, model, [{"role": "user", "content": prefix + task}], max_tokens=gen_tokens, api_key=api_key,
                                  extra={"chat_template_kwargs": {"enable_thinking": False}, "seed": seed})
            t = (out.get("timings") or [{}])[-1]
            runs.append(t)
        first = runs[0]
        # a sample that stopped early is too short to time (and has no speculative steady state)
        dec = [t["predicted_per_second"] for t in runs if t.get("predicted_per_second") and (t.get("predicted_n") or 0) >= gen_tokens // 4]
        fresh = (first.get("prompt_n") or 0) >= 0.9 * (first.get("ctx") or 1)   # a cache hit would flatter the prefill
        med = statistics.median(dec) if dec else 0
        res[d] = {"ctx": first.get("ctx"), "prefill_tps": round(first.get("prompt_per_second") or 0, 1) if fresh else 0,
                  "decode_tps": round(med, 1), "decode_runs": [round(x, 1) for x in dec],
                  "spread_pct": round(100 * (max(dec) - min(dec)) / med, 1) if len(dec) > 1 and med else None}
    shallow, mid = res[depths[0]], res[depths[min(1, len(depths) - 1)]]
    step = TYPICAL_STEP["new_prompt_tokens"] / mid["prefill_tps"] + TYPICAL_STEP["output_tokens"] / shallow["decode_tps"] \
        if mid["prefill_tps"] and shallow["decode_tps"] else None
    dec = shallow["decode_tps"]
    return {"decode_tps": dec, "prefill_tps": mid["prefill_tps"], "typical_agent_step_s": round(step, 1) if step else None,
            "label": None if not dec else ("fast" if dec >= 50 else "comfortable" if dec >= 25 else "slow"),
            "by_depth": {f"{v['ctx'] // 1000 if v['ctx'] else d // 1000}k": {k: v[k] for k in ("decode_tps", "prefill_tps", "decode_runs", "spread_pct")}
                         for d, v in res.items()},
            "method": PROBE_METHOD}


def run_adaptive(base_url: str, model: str, bank, budget_min: float = 45.0, target: float = 5.0, prior: tuple = (0.0, 1.5),
                 seed0: int = 7000, api_key: str | None = None, progress=print, jsonl_path: str | None = None,
                 min_per_block: int = 1, max_per_family: int = 3) -> dict:
    """Adaptive run (llmbox/irt.py): after every task, the family with the most information per expected second at the
    current estimate; fresh seeds, so a family can be drawn again (at most max_per_family times). Stops when the 95%
    interval of the capability is within +-target points or the time budget is spent. The capability is reported on
    the quick suite's scale (expected weighted score of its task families at the estimated theta)."""
    from . import irt
    obs, rows, counts, per_fam = [], [], {}, {}
    out = open(jsonl_path, "a") if jsonl_path else None
    trace_dir = os.path.join(TRACES, os.path.splitext(os.path.basename(jsonl_path))[0]) if jsonl_path else None
    t0, slowness, n = time.time(), 1.0, 0
    est = irt.block_estimate(bank, obs, prior)
    cap, lo, hi = est["capability"], est["lo"], est["hi"]
    while True:
        spent = time.time() - t0
        if spent >= budget_min * 60 or (obs and (hi - lo) / 2 <= target):
            break
        short = [b for b in bank.weights if counts.get(b, 0) < min_per_block]
        if short:   # every block gets its minimum first, cheapest informative family of that block
            full = {f for f in bank.a if bank.block[f] not in short or per_fam.get(f, 0) >= max_per_family}
            fam = irt.next_family(bank, est["theta"], full, slowness)
        else:
            fam = irt.next_family_blocks(bank, est, per_fam, slowness, max_per_family)
        if fam is None:
            break
        blk, kind, lvl = fam.split(".")
        n += 1
        it = suite.BLOCKS[blk][kind](seed0 + n, int(lvl[1:]))
        row = run_item(base_url, model, it, api_key, trace_dir=trace_dir)
        row["family"] = fam
        rows.append(row)
        if out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
        per_fam[fam] = per_fam.get(fam, 0) + 1
        counts[blk] = counts.get(blk, 0) + 1
        if not row.get("error") and not row.get("pending"):   # explanations are scored by the reader after the loop
            obs.append((fam, max(0.0, min(1.0, float(row["score"])))))
        ratios = [r["seconds"] / bank.seconds[r["family"]] for r in rows if bank.seconds.get(r["family"])]
        slowness = statistics.median(ratios) if ratios else 1.0
        est = irt.block_estimate(bank, obs, prior)
        cap, lo, hi = est["capability"], est["lo"], est["hi"]
        progress(f"  [{n:3d}] {row['score']:.2f} {row['seconds']:6.1f}s  {fam:26s} -> {cap:5.1f} ({lo:.0f}-{hi:.0f})  "
                 f"{(time.time() - t0) / 60:5.1f} min")
    pend = [r for r in rows if r.get("pending")]
    if pend:
        items = [suite.BLOCKS[r["family"].split(".")[0]][r["family"].split(".")[1]](int(r["id"].rsplit(".", 1)[1]), int(r["family"].split(".")[2][1:])) for r in pend]
        grade_deferred(base_url, items, rows, out, progress)
        obs += [(r["family"], max(0.0, min(1.0, float(r["score"])))) for r in pend if not r.get("pending")]
        est = irt.block_estimate(bank, obs, prior)
        cap, lo, hi = est["capability"], est["lo"], est["hi"]
    if out:
        out.close()
    s = summarize(rows, time.time() - t0) if rows else {}
    s.update({"capability": round(cap, 1), "capability_ci95": [round(lo, 1), round(hi, 1)],
              "blocks": {b: round(100 * v["score"], 1) for b, v in est["blocks"].items()},
              "irt": {"theta": round(est["theta"], 3), "sd": round(est["theta_sd"], 3), "prior": list(prior), "items": len(rows),
                      "families": per_fam, "block_n": {b: v["n"] for b, v in est["blocks"].items()}}})
    return {"suite": {"version": suite.VERSION, "tier": "adaptive", "seed0": seed0, "content_hash": suite.content_hash(),
                      "weights": suite.WEIGHTS, "budget_min": budget_min, "target": target}, "summary": s, "rows": rows}
