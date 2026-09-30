"""An adaptive run with --block draws tasks from those blocks only (2026-09-30: a K2 run for four missing blocks drew a
long-document task first and lost 30 minutes to its deadline). The model is a stub: every task scores 0.5 in 1 s.
Run: python3 tests/test_adaptive_blocks.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import bench, irt, suite  # noqa: E402

bank = irt.bank_for(suite.content_hash())
assert bank, "needs the calibrated bank of this suite (llmbox irt calibrate)"
known = {f.rsplit(".", 1)[0] for f in bank.a} | {f"{b}.{k}" for b, k, _l in suite.QUICK_ITEMS}
bank = irt.with_provisional(bank, suite.families(known))

drawn = []


def stub(base_url, model, it, api_key=None, deadline_scale=1.0, trace_dir=None):
    drawn.append(it.id)
    return {"id": it.id, "block": it.block, "kind": it.kind, "score": 0.5, "seconds": 1.0, "error": None, "final": "x"}


bench.run_item = stub
want = ["code", "techhelp"]
jl = os.path.join(tempfile.mkdtemp(), "run.jsonl")
res = bench.run_adaptive("http://stub", "stub", bank, budget_min=0.05, target=0.1, jsonl_path=jl, blocks=want, progress=lambda m: None)
got = {i.split(".")[0] for i in drawn}
assert drawn, "no task drawn"
assert got <= set(want), f"tasks from other blocks: {sorted(got - set(want))}"
assert got == set(want), f"a wanted block got no task: {sorted(set(want) - got)}"
assert res["suite"]["blocks"] == sorted(want), res["suite"].get("blocks")
print(f"{len(drawn)} tasks, all from {sorted(got)}")
print("all passed")
