"""Speeds from other people's machines on the site (site.data.community_speeds), in a scratch home: a machine counts
once however many runs it sent, a class is the median of its machines, the spread shows from 5 machines on, an
outlier is left out and counted, a variant (--set) or another model file is not the recipe.
Run: python3 tests/test_community.py"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import results  # noqa: E402

real = next((r for _p, r in results.files("box") if r.get("kind") == "speed" and (r.get("recipe") or {}).get("id") == "tiel-al"
             and not r.get("overrides") and (r.get("speed") or {}).get("decode_tps")), None)
assert real, "needs a saved speed record of tiel-al on the box"

os.environ["LLMBOX_NO_DB"] = "1"
home = tempfile.mkdtemp()
from llmbox import hosts  # noqa: E402
hosts.HOME = results.HOME = home
from llmbox.site import data  # noqa: E402

results.save("box", real)
fname = os.path.basename(real["model"]["file"])
fp4090 = {"gpu": "NVIDIA GeForce RTX 4090", "vram_gib": 24.0, "cpu": "AMD Ryzen 9 7950X", "ram_gib": 64.0, "ram_read_gbs": 70.0}


def sent(machine: str, client: str, decode: float, deep: float, flags=(), **over):
    r = copy.deepcopy(real)
    r["id"] = f"{machine}-{client}-{decode}"
    r["created"] = f"2026-10-01T10:{len(os.listdir(os.path.join(home, 'results'))):02d}:{int(decode) % 60:02d}"
    r["host"] = dict(fp4090, id=machine)
    r["speed"] = {"decode_tps": decode, "prefill_tps": 5000.0, "depth": [{"depth": 33000, "decode_tps": deep, "prefill_tps": 4000.0}]}
    r["submission"] = {"id": "s", "client": client, "flags": list(flags)}
    r.update(over)
    results.save("community", r)


for i, (d, deep) in enumerate([(100, 90), (110, 95), (120, 100), (130, 105), (140, 110)]):
    sent(f"m{i}", f"c{i % 3}", d, deep)
sent("m0", "c0", 104, 92)                     # a second run of machine m0: still one machine
sent("m9", "c9", 900, 800, flags=["outlier"])  # left out, counted
sent("m8", "c8", 1, 1, overrides=["runtime.threads=2"])   # a variant, not the recipe
sent("m7", "c7", 1, 1, model=dict(real["model"], file="other.gguf"))   # another file

cs = data.community_speeds({"tiel-al": fname}, "box")["tiel-al"]
c4090 = next(c for c in cs if c["class"].startswith("rtx-4090-24g|ram-65-85"))
assert c4090["machines"] == 5 and c4090["people"] == 3 and c4090["runs"] == 6, c4090
assert c4090["t2"] == 120 and c4090["t32"] == 100, c4090   # m0 = median(100, 104) = 102 -> machines 102..140, median 120
assert c4090["p5"] and c4090["p95"] and c4090["p5"] < 110 < 130 < c4090["p95"], c4090
assert c4090["outliers"] == 1 and not c4090["ref"]
assert cs[0] is c4090, "the class with most machines first"
ref = next(c for c in cs if c["ref"])
assert ref["machines"] == 1 and ref["p5"] is None and ref["label"].startswith("RTX 5070 12 GB")
print("all passed")
