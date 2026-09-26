"""`llmbox fit` invariants. Uses the model shapes cached in ~/.llmbox/shapes (written by `llmbox site` / `llmbox fit`).
Run: python3 tests/test_fit.py
"""
import os
import sys
import tomllib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import estimate as E  # noqa: E402
from llmbox import fit as F  # noqa: E402
from llmbox import recipe as rc  # noqa: E402

BOX = E.HostSpec(vram_mib=12227, ram_mib=62852, ram_bw_gbs=59.0, vram_bw_gbs=672.0)
BIG = E.HostSpec(vram_mib=24564, ram_mib=65536, ram_bw_gbs=75.0, vram_bw_gbs=1008.0)
SMALL = E.HostSpec(vram_mib=8192, ram_mib=16384, ram_bw_gbs=40.0, vram_bw_gbs=288.0)
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}{f'  ({detail})' if detail and not ok else ''}")


def keys(d: dict, p: str = "") -> set:
    return {k2 for k, v in d.items() for k2 in (keys(v, f"{p}{k}.") if isinstance(v, dict) and v else {f"{p}{k}"})}


def covered(key: str, paths: tuple) -> bool:
    return any(key == p or key.startswith(p + ".") for p in paths)


r = rc.load("box", "tiel-al")
if not os.path.exists(os.path.join(F.SHAPES, r["model"]["file"] + ".json")):
    sys.exit("no cached shape for tiel-al: run `llmbox fit tiel-al` once")
shape = F.shape_for(r)

# every recipe key belongs to exactly one layer
for rid in rc.ids("box"):
    ks = keys({k: v for k, v in rc.load("box", rid).items() if k != "id"}) - {"extends"}
    stray = sorted(k for k in ks if covered(k, F.PORTABLE) == covered(k, F.HARDWARE))
    check(f"{rid}: each key is portable xor hardware", not stray, ", ".join(stray))

# on its own box, fit reproduces the recipe's hardware layer
f = F.fit(r, shape, BOX, cores=8, same_host=True)
check("reference box keeps the recipe context", f.fits and f.ctx == r["placement"]["ctx"], f"{f.ctx}")
check("reference box keeps threads and pinning", (f.threads, f.cpu_affinity) == (8, "0-7"), f"{f.threads} {f.cpu_affinity}")

# the portable layer never changes, whatever the target
for name, hw in (("box", BOX), ("24 GB", BIG), ("8 GB", SMALL)):
    f = F.fit(r, shape, hw, cores=6)
    if f.fits:
        check(f"{name}: portable layer untouched", F.layer(F.apply(r, f), F.PORTABLE) == F.layer(r, F.PORTABLE))

# more VRAM -> more experts on the GPU -> faster
fb, fs = F.fit(r, shape, BOX), F.fit(r, shape, BIG)
check("24 GB card beats 12 GB", fs.tps > fb.tps and fs.plan.gpu_expert_frac > fb.plan.gpu_expert_frac, f"{fs.tps:.0f} vs {fb.tps:.0f}")
check("another box does not inherit the recipe's pinning", fs.cpu_affinity == "" and fs.threads == 0)

# too small: nothing picked, the way out is named
f = F.fit(r, shape, SMALL)
check("8 GB / 16 GB does not fit", not f.fits and f.alternatives and "RAM" in f.alternatives[0], f"{f.reason} {f.alternatives}")

# a smaller context than the score was measured with is flagged
f = F.fit(r, shape, BOX, ctx=65536)
check("64k is flagged against the long-document score", f.ctx == 65536 and any("long-document" in w for w in f.warnings), f"{f.warnings}")
check("faster options are offered, not chosen", F.fit(r, shape, BOX).options and F.fit(r, shape, BOX).ctx == r["placement"]["ctx"])

# TOML round trip
fitted = F.apply(r, F.fit(r, shape, BIG, cores=16), models_dir="/models", server="/opt/llama-server")
back = tomllib.loads(F.to_toml(fitted, header=["test"]))
check("TOML round trip", back == {k: v for k, v in fitted.items() if k != "id"})
check("paths point at the target box", back["model"]["path"] == "/models/" + r["model"]["file"] and back["runtime"]["server"] == "/opt/llama-server")

print(f"\n{'all passed' if not failed else f'{failed} FAILED'}")
sys.exit(1 if failed else 0)
