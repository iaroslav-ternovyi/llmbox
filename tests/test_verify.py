"""llmbox verify re-grades a saved answer from the task rebuilt from its id: a true score matches, a forged one is
caught, a timeout is 'no_answer', explain is left to the reader. Run: python3 tests/test_verify.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import suite, validate, verify  # noqa: E402

it = suite.BLOCKS["reasoning"]["arith"](5, 3)
final = validate.oracle(it)
rows = [{"id": it.id, "block": "reasoning", "score": 1.0, "final": final},
        {"id": it.id, "block": "reasoning", "score": 1.0, "final": "ANSWER 1: 0"},            # forged: says 1.0, is not
        {"id": it.id, "block": "reasoning", "score": 0.0, "final": "", "error": "timed out"},
        {"id": "explain.config.L4.7", "block": "explain", "score": 0.5, "final": "text"}]
rec = {"kind": "suite", "suite": {"version": suite.VERSION, "content_hash": suite.content_hash()}, "rows": rows}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(rec, f)
res = verify.run_one(f.name)
os.unlink(f.name)
got = [r["status"] for r in res["rows"]]
ok = got == ["match", "mismatch", "no_answer", "reader"] and res["rows"][1]["server"] < 1.0
print("all passed" if ok else f"FAILED: {got} {res['rows']}")
sys.exit(0 if ok else 1)
