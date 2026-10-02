"""The site build's people part (llmbox/site/build.py _people_runs) on made-up community records that pass the intake's
check: a run gets its page and its settings from the published recipe; a record accepted under an older, looser check
(a malformed runtime) is left out and stops nothing; a run whose page raises keeps its last page; an accepted run whose
model left the list keeps its address (its last page, else a short note out of search); a run with settings of its own
(llmbox test --set) gets a page that says so and points to the model page's variants; a removed run gets its
"removed" page. Run: python3 tests/test_people_runs.py"""
import copy
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
home = tempfile.mkdtemp()
os.environ["LLMBOX_HOME"] = home
os.environ["LLMBOX_SITE"] = "https://llmbox.pages.dev"
from llmbox import estimate as E, fit as F, hwclass, registry, report, results  # noqa: E402
import importlib  # noqa: E402
from llmbox.site import card, run as R  # noqa: E402
build = importlib.import_module("llmbox.site.build")   # (llmbox.site exports the build function under the same name)

card.CARDS = os.path.join(home, "cards")
fx = json.load(open(os.path.join(HERE, "fixtures", "parity.json")))["models"]
RECIPE = {"placement": {"kv_type": "q8_0", "ctx": 65536}, "runtime": {"threads": 0, "cpu_affinity": ""}, "model": {"path": "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"}}
registry.published = lambda host, rid: copy.deepcopy(RECIPE)
F.shape_for = lambda r, host=None: E.ModelShape(**fx["qwen36-al"]["shape"])
K3060 = hwclass.key("NVIDIA GeForce RTX 3060", 12288, 60)


def rec(sid, t2, machine, rid="qwen36-al", file="Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf", **extra):
    r = {"schema": results.SCHEMA, "kind": "speed", "id": f"rec-{sid}", "created": "2026-10-10T10:00:00+0200",
         "host": {"cpu": "x", "ram_gib": 64, "gpu": "NVIDIA GeForce RTX 3060", "vram_gib": 12, "class": K3060, "id": machine},
         "model": {"file": file}, "recipe": {"id": rid, "model": {"path": f"/home/x/{file}"}},
         "speed": {"decode_tps": t2, "depth": [{"depth": 32000, "decode_tps": t2 * 0.8}]}, "prediction": {"decode_tps_no_spec": 40.0},
         "submission": {"id": sid, "user": None, "received": "2026-10-10T10:00:00", "flags": []}}
    r.update(extra)
    return r


community = [rec("aaaaaaaaaaa1", 40, "m1"), rec("aaaaaaaaaaa2", 42, "m2"),
             rec("bbbbbbbbbbb1", 41, "m3", runtime="x"),                                         # accepted under a looser check
             rec("ccccccccccc1", 30, "m4", rid="old-model", file="Old.gguf"),                   # its model left the list
             rec("ddddddddddd1", 44, "m5"), rec("eeeeeeeeeeee", 39, "m6"),
             rec("fffffffffff1", 47, "m7", overrides=["placement.ubatch=1024"])]                # settings of its own (llmbox test --set)
report.results.files = lambda h: [("x", r) for r in (community if h == "community" else [])]
out = tempfile.mkdtemp()
os.makedirs(os.path.join(out, "r"))
open(os.path.join(out, "r", "ddddddddddd1.html"), "w").write("the page it had")   # the run whose page will raise
os.makedirs(card.CARDS)
open(os.path.join(card.CARDS, "removed.jsonl"), "w").write(json.dumps({"id": "eeeeeeeeeeee", "reason": "owner", "at": "2026-10-11"}) + "\n")
real_page = R.user_run_page
def page(sid, *a, **k):
    if sid == "ddddddddddd1":
        raise KeyError("speed")
    return real_page(sid, *a, **k)
R.user_run_page = page
meta = {"qwen36-al": {"name": "Qwen3.6-35B-A3B UD-Q4_K_XL", "score": 82.0, "cap": 80.0, "range": [79, 85]}}
b, written, images, noindex = build._people_runs(out, "box", ["qwen36-al"], meta, {"qwen36-al": "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"})
names = {os.path.relpath(p, out) for p in written}
v = open(os.path.join(out, "r", "fffffffffff1.html")).read()   # not on the board, not "not shown": its own settings, and where they are voted on
assert "with its own settings" in v and "ubatch 1024" in v and "llmbox test qwen36-al --set placement.ubatch=1024" in v and "recipe-qwen36-al.html#shared" in v, v[-1500:]
assert "r/fffffffffff1.html" in noindex and "fffffffffff1" not in b["runs"]
assert set(b["runs"]) == {"aaaaaaaaaaa1", "aaaaaaaaaaa2", "ddddddddddd1"}, set(b["runs"])   # the malformed and the removed are out
a = open(os.path.join(out, "r", "aaaaaaaaaaa1.html")).read()
assert "RTX 3060 12 GB" in a and 'data-copy="llama-server -m Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf' in a and "/home/x" not in a
assert "r/ddddddddddd1.html" in names and open(os.path.join(out, "r", "ddddddddddd1.html")).read() == "the page it had"
c = open(os.path.join(out, "r", "ccccccccccc1.html")).read()
assert "This result is not shown" in c and "r/ccccccccccc1.html" in noindex and "r/ccccccccccc1.html" in names
assert "r/bbbbbbbbbbb1.html" in names and "This result is not shown" in open(os.path.join(out, "r", "bbbbbbbbbbb1.html")).read()
assert "This result was removed" in open(os.path.join(out, "r", "eeeeeeeeeeee.html")).read() and "r/eeeeeeeeeeee.html" in noindex
print("all passed")
