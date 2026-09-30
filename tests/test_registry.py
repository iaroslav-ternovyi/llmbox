"""The recipe registry (llmbox/registry.py): what the site publishes carries no path, user or CPU pinning of the
reference box, keeps the portable layer that the score belongs to, and comes back through `recipe pull` as a recipe
that fits another machine with the reference speed as calibration.
Run: python3 tests/test_registry.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import fit as F, recipe as rc, registry  # noqa: E402

src = "box"
rids = [r for r in ("qwen36-al", "k2-medium") if r in rc.ids(src)]
assert rids, "needs the box's recipes"
out = tempfile.mkdtemp()
files = registry.export(src, rids, out, {r: r.upper() for r in rids})
idx = json.load(open(os.path.join(out, "recipes", "index.json")))
assert [e["id"] for e in idx["recipes"]] == rids and idx["schema"] == registry.SCHEMA
for rid in rids:
    text = open(os.path.join(out, "recipes", f"{rid}.toml")).read()
    assert "/home/" not in text and "mixer" not in text, f"{rid}: a path of the box leaked"
    orig = rc.load(src, rid)
    pub = rc._merge(rc.DEFAULTS, __import__("tomllib").loads(text))
    for p in F.PORTABLE:   # what the score belongs to is published as measured
        if p not in ("description", "notes"):
            assert F._get(pub, p) == F._get(orig, p) or p == "model.sha256", (rid, p)
    assert pub["runtime"]["threads"] == 0 and pub["runtime"]["cpu_affinity"] == "" and pub["runtime"]["server"] == "llama-server"
    assert pub["runtime"].get("engine", "llama.cpp") == orig["runtime"].get("engine", "llama.cpp")
    assert "extends" not in pub

# pull into a scratch home (file:// is a site too), then fit: the published reference calibrates the prediction
from llmbox import hosts  # noqa: E402
hosts.HOME = rc.HOME = tempfile.mkdtemp()   # recipe.py took HOME at import
got = registry.pull("file://" + out, out=lambda m: None)
assert got == rids, got
r = rc.load(registry.REGISTRY, rids[0])
cal = F.calibration(r, F.shape_for(r), registry.REGISTRY)
assert cal.measured.get("decode_tps") and "reference" in cal.source, cal
print(f"{len(rids)} recipes published and pulled; {rids[0]}: {cal.source}, k {cal.k2:.2f}")
print("all passed")
