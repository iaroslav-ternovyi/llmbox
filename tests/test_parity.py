"""The speed formula exists twice: llmbox/estimate.py + fit.py (the CLI, the server's outlier check, the result card)
and llmbox/site/assets/plan.js (the pages' box picker and the hardware map). This holds them to each other on seven real
model shapes (tests/fixtures/parity.json: dense, mixture-of-experts, sliding-window, MTP and hybrid models) x every
picker entry x two RAM sizes: the same verdict on fitting, the same context, and speeds within 1%. It also holds the
hardware class a picked box is filed under (plan.js classOf) to hwclass.key, and a Mac's memory to hwclass.mac_memory.
Needs Node (CI has it); skipped without.
Run: python3 tests/test_parity.py"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from llmbox import estimate as E, fit as F, hwclass, pick  # noqa: E402
from llmbox.site import data  # noqa: E402

if not shutil.which("node"):
    print("skipped: no node")
    sys.exit(0)

fx = json.load(open(os.path.join(HERE, "fixtures", "parity.json")))["models"]
RAM, BW = (32, 96), 60.0
cases, py = [], {}
shapes = {}
for rid, m in fx.items():
    shape = E.ModelShape(**m["shape"])
    cal = F.Calibration(**m["cal"])
    shapes[rid] = data.js_shape(shape, m["kv_type"], m["ctx"] or shape.context_length, cal)
    r = {"placement": {"kv_type": m["kv_type"], "ctx": m["ctx"]}, "runtime": {"threads": 8, "cpu_affinity": ""}}
    for g in data.GPUS:
        for ram in RAM:
            uni = hwclass.entry_kind(g[0]) in ("mac", "chip")
            f = F.fit(r, shape, pick.entry_spec(g[0], ram, BW), cal=F.Calibration(deep_k=cal.deep_k) if uni else cal)
            py[f"{rid}|{g[0]}|{ram}"] = {"fits": f.fits, "ctx": f.ctx if f.fits else None,
                                        "t2": f.tps if f.fits else None, "td": f.tps_deep if f.fits else None}
            cases.append([rid, g[0], ram])

PLAN = os.path.join(HERE, "..", "llmbox", "site", "assets", "plan.js")
script = f"""
const fs = require("fs"), vm = require("vm");
globalThis.DATA = {json.dumps({"gpus": data.GPUS, "gpuClass": data.gpu_classes(), "ramEdges": list(hwclass.RAM_EDGES)})};
vm.runInThisContext(fs.readFileSync({json.dumps(PLAN)}, "utf8"));
const shapes = {json.dumps(shapes)}, cases = {json.dumps(cases)}, out = {{}}, cls = {{}}, mem = {{}};
for (const [rid, name, ram] of cases) {{
  const g = DATA.gpus.find(x => x[0] === name), p = forBox(shapes[rid], boxFrom(g, ram, {BW}));
  out[`${{rid}}|${{name}}|${{ram}}`] = p.fits ? {{ fits: true, ctx: p.ctx, t2: p.t2, td: p.td }} : {{ fits: false, ctx: null, t2: null, td: null }};
}}
for (const g of DATA.gpus) for (const bw of [28, 40, 60, 75, 100, 130]) {{ const k = classOf(boxFrom(g, 64, bw)); if (k) cls[`${{g[0]}}|${{bw}}`] = k; }}
for (const g of DATA.gpus) if (g[6]) for (const want of [8, 16, 27, 36, 64, 100, 200, 600]) mem[`${{g[0]}}|${{want}}`] = macMem(g[6], want);
console.log(JSON.stringify({{ out, cls, mem }}));
"""
r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
assert r.returncode == 0, r.stderr[-2000:]
js = json.loads(r.stdout)

bad = []
for k, p in py.items():
    j = js["out"][k]
    if p["fits"] != j["fits"] or p["ctx"] != j["ctx"]:
        bad.append(f"{k}: python fits={p['fits']} ctx={p['ctx']}, plan.js fits={j['fits']} ctx={j['ctx']}")
    elif p["fits"]:
        for f in ("t2", "td"):
            if abs(p[f] - j[f]) > 0.01 * p[f]:
                bad.append(f"{k}: {f} python {p[f]:.2f}, plan.js {j[f]:.2f} ({(j[f] / p[f] - 1) * 100:+.1f}%)")
fits = sum(1 for p in py.values() if p["fits"])
assert not bad, f"{len(bad)} of {len(py)} differ:\n" + "\n".join(bad[:25])

# the class a picked box's measurements are filed under, as people's runs are keyed
for k, v in js["cls"].items():
    name, bw = k.rsplit("|", 1)
    g = next(x for x in data.GPUS if x[0] == name)
    want = hwclass.key(name, g[1], float(bw), vendor="amd" if hwclass.entry_kind(name) == "amd" else "nvidia")
    assert v == want, (k, v, want)
assert len(js["cls"]) == 42 * 6, len(js["cls"])   # every discrete card; a unified-memory box has no class there yet
# a Mac's memory: the size it is sold with nearest the one asked for, a tie to the smaller
for k, v in js["mem"].items():
    name, want = k.rsplit("|", 1)
    assert v == hwclass.mac_memory(name, int(want)), (k, v, hwclass.mac_memory(name, int(want)))
print(f"all passed ({len(py)} model x box x RAM cases, {fits} fit; {len(js['cls'])} classes; {len(js['mem'])} Mac sizes)")
