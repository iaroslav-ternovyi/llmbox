"""Which failed answers count as a 0 and which are left out (irt.counted, decided 2026-09-30): no answer within the time
limit and a task that does not fit the context are the model's result; a crashed or restarting server is not.
Run: python3 tests/test_real_zero.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import irt  # noqa: E402

row = {"id": "longctx.lookup.L5.7004", "block": "longctx", "score": 0.0, "seconds": 1800.0}
t = irt.counted(dict(row, error="timed out"))
assert t and t["score"] == 0.0 and t["error"] is None and t["zero"] == "time", t
c = irt.counted(dict(row, error="request (95012 tokens) exceeds the available context size (65536 tokens)"))
assert c and c["zero"] == "context", c
assert irt.counted(dict(row, error="HTTP Error 502: Bad Gateway")) is None
assert irt.counted(dict(row, error="the box restarted during the task (booted 2026-09-30 12:07 UTC)")) is None
assert irt.counted(dict(row, error="<urlopen error timed out>")) is None   # the server was unreachable
assert irt.counted(dict(row, error=None, pending=True)) is None
ok = irt.counted(dict(row, error=None, score=0.8))
assert ok and ok["score"] == 0.8 and "zero" not in ok
print("all passed")
