"""`llmbox optimize <recipe>`: tune a recipe, apply the winners, and show what that buys against stock llama.cpp.

Three steps per recipe, all on its host:
1. tune (llmbox/tune.py) unless it was tuned already - the measured winners go into the recipe and the llama-swap entry;
2. stock vs llmbox, back to back with one method (speed.measure): the same model file and context run once the way
   `llama-server -m model.gguf -c <ctx>` runs it (llama.cpp's own defaults: --fit with a 1024 MiB margin, f16 KV cache,
   -ub 512, no speculative decoding) and once as the tuned recipe. The pair is saved as an "optimize" record - the
   site's "what the optimizer does" numbers come only from these records;
3. the ranking's 1-stream speed re-measured on the served recipe (`llmbox probe`), so every model in the ranking is
   measured the same way, after its tune.
"""
from __future__ import annotations

import json
import os
import time

from . import hosts, recipe as rc, results, speed, tune

# what llama-server does when only the model and the context are given (llama.cpp b8xxx, 2026-09: `llama-server --help`)
STOCK = ["placement.fit=true", "placement.fit_target_mib=1024", 'placement.kv_type="f16"', "placement.ubatch=512",
         "placement.batch=2048", 'speculative.type=""', "speculative.draft_max=0"]
DEPTH = 32000


def _tuned(host: str, rid: str) -> dict | None:
    p = os.path.join(hosts.HOME, "tuned", host, f"{rid}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def compare(host: str, rid: str, out=print, repeats: int = 3) -> dict:
    """Stock llama.cpp vs the recipe as served, measured back to back (stock first, then llmbox, then stock again: a
    drift of the box shows up as a gap between the two stock runs)."""
    r = rc.load(host, rid)
    sampling = {k: v for k, v in {"temperature": r["sampling"].get("temp"), "top_p": r["sampling"].get("top_p"),
                                   "top_k": r["sampling"].get("top_k"), "min_p": r["sampling"].get("min_p")}.items() if v is not None}
    runs = {}
    for name, ov in (("stock", STOCK), ("llmbox", []), ("stock again", STOCK)):
        t = time.time()
        m = speed.measure(host, rid, overrides=ov, depths=[DEPTH], unload=True, save=True, sampling=sampling, repeats=repeats)
        row = tune._row(name, m)
        runs[name] = row
        out(f"  {name:12s} " + (f"ERROR {row['error']}" if row["error"] else
            f"{row['decode'] or 0:6.1f} tok/s  {row['deep'] or 0:6.1f} @32k  prefill {row['prefill'] or 0:6.0f}  step {row['step_s'] or 0:5.2f} s")
            + f"   ({time.time() - t:.0f} s)")
    s1, s2, lb = runs["stock"], runs["stock again"], runs["llmbox"]
    ok = not any(x["error"] for x in runs.values())
    stock = {k: (round((s1[k] + s2[k]) / 2, 1) if s1.get(k) and s2.get(k) else s1.get(k) or s2.get(k)) for k in ("decode", "deep", "prefill", "step_s")}
    drift = abs(s1["decode"] - s2["decode"]) / stock["decode"] if ok and s1.get("decode") and s2.get("decode") else None
    prof = hosts.load(host)
    rec = results.new("optimize", prof, recipe=r, model={k: r["model"].get(k) for k in ("hf_repo", "file", "path", "sha256")})
    tu = _tuned(host, rid) or {}
    rec["summary"] = {"stock": stock, "llmbox": {k: lb.get(k) for k in ("decode", "deep", "prefill", "step_s", "acceptance")},
                      "stock_flags": tune.flags_of(STOCK), "tuned_flags": tu.get("applied_flags") or [], "drift": round(drift, 3) if drift is not None else None,
                      "gain_decode": round(lb["decode"] / stock["decode"] - 1, 3) if ok and stock.get("decode") and lb.get("decode") else None,
                      "gain_deep": round(lb["deep"] / stock["deep"] - 1, 3) if ok and stock.get("deep") and lb.get("deep") else None,
                      "gain_step": round(1 - lb["step_s"] / stock["step_s"], 3) if ok and stock.get("step_s") and lb.get("step_s") else None}
    rec["runs"] = runs
    path = results.save(host, rec)
    s = rec["summary"]
    if ok:
        out(f"  stock {stock['decode']:.1f} -> llmbox {lb['decode']:.1f} tok/s ({s['gain_decode'] * 100:+.0f}%), at 32k "
            f"{stock['deep'] or 0:.1f} -> {lb['deep'] or 0:.1f}" + (f", drift between the stock runs {drift * 100:.1f}%" if drift is not None else ""))
    out(f"  saved {path}")
    return rec


def run(host: str, rid: str, retune: bool = False, out=print, endpoint: str = "http://192.0.2.10:8080") -> dict:
    prof = hosts.load(host)
    h = hosts.host_of(prof)
    busy = hosts.free_up(h)
    if busy.get("busy"):
        out(f"{rid}: host busy ({busy}) - not measuring")
        return {"rid": rid, "error": "host busy"}
    if retune or not _tuned(host, rid):
        res = tune.run(host, rid, out=out)
        if res.get("error"):
            return {"rid": rid, "error": res["error"]}
        if res.get("chosen"):
            tune.apply(host, rid, out=out)
    rec = compare(host, rid, out=out)
    # the ranking's speed: the served recipe through the same probe as every other model
    from . import bench, runinfo
    ep = endpoint
    cap = runinfo.begin(host, ep, rid)
    sp = bench.speed_probe(ep, rid)
    info = runinfo.end(cap, rc.load(host, rid))
    p = results.new("probe", prof, recipe=rc.load(host, rid), model=rec["model"])
    p["summary"], p["runtime"], p["telemetry"] = {"speed": sp}, info["runtime"], info["telemetry"]
    p["tuned"] = bool((_tuned(host, rid) or {}).get("applied_flags"))
    out(f"  probe: {sp['decode_tps']} tok/s short, " + " | ".join(f"{k} {v['decode_tps']}" for k, v in (sp.get("by_depth") or {}).items()))
    out(f"  saved {results.save(host, p)}")
    return {"rid": rid, "optimize": rec["summary"], "probe": sp}
