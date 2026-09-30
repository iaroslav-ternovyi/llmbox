"""How narrow a 40-minute adaptive run gets, by the real run loop (bench.run_adaptive) on synthetic models: a model is a
true theta plus block offsets (the bank's tau), it answers a task correctly with the bank's probability for its block,
and takes the family's median time times its slowness (log-normal noise), up to the task's deadline (a 0 past it).
The clock is virtual, the tasks are not generated. Prints, per model type and coverage rule, the median half-width of
the 95% range, the median |estimate - truth| and the tasks answered.
Run: python3 tests/sim_adaptive.py [runs per cell, default 30]   (a tool, not a test: ~1-2 min)"""
import math
import os
import random
import statistics
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import bench, irt, suite  # noqa: E402

bank = irt.load(irt.canonical(suite.content_hash()))
CLOCK = [0.0]
bench.time = SimpleNamespace(time=lambda: CLOCK[0], sleep=lambda s: None)


class Blocks(dict):   # suite.BLOCKS[block][kind](seed, level) -> a stand-in item
    def __missing__(self, blk):
        return {k: (lambda seed, lvl, b=blk, k=k: SimpleNamespace(id=f"{b}.{k}.L{lvl}.{seed}", block=b, kind=k))
                for k in {f.split(".")[1] for f in bank.a if bank.block[f] == blk}}


bench.suite = SimpleNamespace(BLOCKS=Blocks(), VERSION=suite.VERSION, WEIGHTS=suite.WEIGHTS, content_hash=suite.content_hash)
bench.reader_now = lambda url: False


def truth_cap(etas):
    on = set(bank.scale) if bank.scale else set(bank.a)
    fams = {b: [f for f in bank.a if bank.block[f] == b and f in on] for b in bank.weights}
    w = sum(v for b, v in bank.weights.items() if fams[b])
    return 100 * sum(bank.weights[b] * sum(bank.p(f, etas[b]) for f in fams[b]) / len(fams[b]) for b in bank.weights if fams[b]) / w


def one(theta, slowness, coverage, rnd, budget=40):
    etas = {b: theta + rnd.gauss(0, bank.tau) for b in bank.weights}

    def stub(base_url, model, it, api_key=None, deadline_scale=1.0, trace_dir=None):
        fam = f"{it.block}.{it.kind}.{it.id.split('.')[2]}"
        sec = bank.seconds[fam] * slowness * math.exp(rnd.gauss(0, 0.4))
        limit = 900 if it.block == "agentic" else 1800
        CLOCK[0] += min(sec, limit)
        if sec > limit:
            return {"id": it.id, "block": it.block, "kind": it.kind, "score": 0.0, "seconds": limit, "error": "timed out", "final": None}
        ok = rnd.random() < bank.p(fam, etas[it.block])
        return {"id": it.id, "block": it.block, "kind": it.kind, "score": 1.0 if ok else 0.0, "seconds": sec, "error": None, "final": "x"}
    bench.run_item = stub
    CLOCK[0] = 0.0
    r = bench.run_adaptive("http://sim", "sim", bank, budget_min=budget, target=0.0, explore=0, progress=lambda m: None, min_per_block=coverage)
    s = r["summary"]
    return (s["capability_ci95"][1] - s["capability_ci95"][0]) / 2, abs(s["capability"] - truth_cap(etas)), s["irt"]["items"]


# slowness against the bank's median seconds, set so a run answers about what real 40-minute runs did on the box:
# K2-Horizon ~10 tasks, the top MoE models ~20-30
MODELS = [("strong, slow (K2 here)", 0.9, 8.0), ("strong, fast", 0.9, 2.5), ("middle", 0.4, 2.0), ("weak", -1.0, 1.5), ("frontier", 2.2, 1.0)]


def cell(args):
    name, th, slow, cov, n = args
    rnd = random.Random(sum(map(ord, name)))   # the same models for every rule
    return name, cov, [one(th, slow, cov, rnd) for _ in range(n)]
if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print("coverage 1 = one answer per block first (the rule), 0 = none; tried and dropped 2026-10-01: choosing coverage by value per second"
          " (K2-like +-7.6 vs 6.1), skipping answers over a fifth of the budget (+-7.3)")
    print(f"{'model':24s} {'coverage':9s} {'±half':>6s} {'|err|':>6s} {'tasks':>6s}   ({n} runs each, 40 min)")
    from multiprocessing import Pool
    with Pool() as pool:
        for name, cov, res in pool.map(cell, [(name, th, slow, cov, n) for name, th, slow in MODELS for cov in (1, 0)]):
            print(f"{name:24s} {cov!s:9s} {statistics.median(r[0] for r in res):6.1f} {statistics.median(r[1] for r in res):6.1f} "
                  f"{statistics.median(r[2] for r in res):6.0f}", flush=True)
