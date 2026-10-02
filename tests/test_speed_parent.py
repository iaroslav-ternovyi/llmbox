"""A recipe that extends another without changing what its speed depends on (file, runtime, placement, extra flags,
speculative decoding) shares its parent's speed probe: k2-medium is k2-horizon with a shorter thinking, and showed
"—" for speed while k2-horizon showed 28. One that changes the placement keeps its own (none, here).
Run: python3 tests/test_speed_parent.py"""
import os
import sys
import tempfile

home = tempfile.mkdtemp()
os.environ["LLMBOX_HOME"] = home
os.environ["LLMBOX_NO_DB"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import recipe, report  # noqa: E402

d = os.path.join(home, "recipes", "box")
os.makedirs(d)
open(os.path.join(d, "base.toml"), "w").write('[model]\nfile = "m.gguf"\n[placement]\nctx = 131072\n')
open(os.path.join(d, "think.toml"), "w").write('extends = "base"\ndescription = "shorter thinking"\n[chat]\ntemplate_kwargs = {reasoning_effort = "medium"}\n')
open(os.path.join(d, "bigctx.toml"), "w").write('extends = "base"\n[placement]\nctx = 262144\n')
assert recipe.speed_parent("box", "think") == "base"
assert recipe.speed_parent("box", "bigctx") is None, "a placement change makes its own speed"
assert recipe.speed_parent("box", "base") is None and recipe.speed_parent("box", "missing") is None

probe = {"kind": "probe", "recipe": {"id": "base"}, "created": "2026-10-01T10:00:00", "summary": {"speed": {"decode_tps": 28.3}}}
recs = [probe]
assert report.speed_probe(recs, "think", "box") is probe
assert report.speed_probe(recs, "bigctx", "box") is None
suite = {"kind": "suite", "recipe": {"id": "think"}, "summary": {"speed": {}}}
out = report.with_probe(suite, recs, "box")
assert out["summary"]["speed"]["decode_tps"] == 28.3 and out["speed_as"] == "base", out
assert "speed_as" not in report.with_probe(dict(suite, recipe={"id": "base"}), recs, "box")
print("all passed")
