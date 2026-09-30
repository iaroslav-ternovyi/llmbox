"""The recipe registry: the measured recipes, published with the site, so any machine can install a model with the
settings its score was measured with.

The site carries recipes/index.json and recipes/<id>.toml. A published recipe is the whole recipe with the reference
box's private details taken out (paths, the server binary's location, CPU pinning), plus a [reference] table: the
machine it was measured on and its speed there. `fit` uses that measurement to calibrate its prediction for another
machine, and `install` fits the hardware layer to the target; the portable layer (model file, sampling, template,
anti-loop, KV type) is what the score belongs to and stays as published.

`llmbox recipe pull` stores the published recipes under the pseudo host "registry" (~/.llmbox/recipes/registry), so
`llmbox install <id> --from registry --host <yours>` works like an install from the reference box.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from . import fit as F
from . import hosts, recipe as rc

REGISTRY = "registry"
DEFAULT_URL = os.environ.get("LLMBOX_SITE", "http://127.0.0.1:8766")   # the site; the public address once it is hosted
SCHEMA = 1


def published(host: str, rid: str) -> dict:
    """The recipe as anyone can use it: flattened (no extends), without the box's paths, with the reference speed."""
    r = rc.load(host, rid)
    out = {k: v for k, v in r.items() if k not in ("id", "extends")}
    m = dict(out["model"])
    m["path"] = os.path.basename(m.get("file") or m.get("path") or "")   # install puts it in the target's models folder
    if not m.get("sha256"):   # the file's identity, from the newest run that hashed it
        from . import results
        runs = [x for x in results.load_all(host) if (x.get("recipe") or {}).get("id") == rid and (x.get("model") or {}).get("sha256")]
        if runs:
            m["sha256"] = max(runs, key=lambda x: x.get("created", ""))["model"]["sha256"]
    out["model"] = m
    rt = dict(out["runtime"])
    rt["server"] = "llama-server"   # the target's own build of the engine (runtime.engine)
    rt["cpu_affinity"] = ""
    rt["threads"] = 0               # fitted on the target
    out["runtime"] = rt
    ref = reference(host, r)
    if ref:
        out["reference"] = ref
    return out


def reference(host: str, r: dict) -> dict:
    """The machine the recipe was measured on and its speed there (newest speed figure), for fit's calibration."""
    shape = F.shape_for(r)
    cal = F.calibration(r, shape, host)
    if not cal.measured.get("decode_tps"):
        return {}
    hw = hosts.spec(hosts.load(host))
    gpu = ((hosts.load(host).get("hw") or {}).get("gpus") or [{}])[0].get("name", "")
    ctx = r["placement"]["ctx"] or shape.context_length
    return {"gpu": gpu, "vram_mib": hw.vram_mib, "ram_mib": hw.ram_mib, "ram_bw_gbs": hw.ram_bw_gbs, "vram_bw_gbs": hw.vram_bw_gbs,
            "ctx": ctx, "kv_type": r["placement"]["kv_type"], "decode_tps": cal.measured["decode_tps"],
            "deep_tps": cal.measured.get("deep_tps") or 0.0, "deep_k": cal.deep_k, "measured": cal.source.rsplit("(", 1)[-1].rstrip(")"),
            "by_depth": cal.measured.get("by_depth") or {}}   # every measured depth (k tokens -> tok/s): the formula's slope is not K2's


def calibration(r: dict, shape, at_k: int | None = None) -> F.Calibration:
    """fit's calibration from a published recipe's [reference] (the reference box is not a registered host here).
    at_k: calibrate the deep figure on the measured depth nearest this many thousand tokens (default: the deepest)."""
    from . import estimate as E
    ref = r.get("reference") or {}
    if not ref.get("decode_tps"):
        return F.Calibration()
    hw = E.HostSpec(vram_mib=ref["vram_mib"], ram_mib=ref["ram_mib"], ram_bw_gbs=ref["ram_bw_gbs"], vram_bw_gbs=ref["vram_bw_gbs"])
    ctx, kv = ref.get("ctx") or shape.context_length, ref.get("kv_type") or r["placement"]["kv_type"]
    k2 = ref["decode_tps"] / E.plan(shape, hw, ctx=ctx, kv_type=kv, depth=2000).decode_tps_at_depth
    dk, dv = ref.get("deep_k") or 88, ref.get("deep_tps")
    bd = {int(k): v for k, v in (ref.get("by_depth") or {}).items() if v}
    if at_k and bd:
        dk = min(bd, key=lambda k: abs(k - at_k))
        dv = bd[dk]
    kd = dv / E.plan(shape, hw, ctx=ctx, kv_type=kv, depth=int(dk * 1000)).decode_tps_at_depth if dv else k2
    return F.Calibration(k2, kd, int(dk), f"calibrated on the reference {ref.get('gpu') or 'box'} ({ref.get('measured', '')})",
                         {"decode_tps": ref["decode_tps"], "deep_tps": dv})


def export(host: str, rids: list[str], out_dir: str, meta: dict | None = None) -> list[str]:
    """recipes/<id>.toml for every measured recipe and recipes/index.json listing them; returns the files written.
    meta: {rid: {name, score, range, uses}} from the ranking, carried in the index (llmbox pick reads it)."""
    d = os.path.join(out_dir, "recipes")
    os.makedirs(d, exist_ok=True)
    written, index = [], []
    for rid in rids:
        try:
            p = published(host, rid)
        except (OSError, ValueError, SystemExit) as e:   # a recipe that no longer loads is left out, not the site
            print(f"registry: {rid} skipped: {e}")
            continue
        sh = os.path.join(F.SHAPES, os.path.basename(p["model"]["file"] or "") + ".json")
        if p["model"].get("file") and os.path.exists(sh):   # the model's shape: pick and fit need no Hugging Face call
            os.makedirs(os.path.join(d, "shapes"), exist_ok=True)
            open(os.path.join(d, "shapes", os.path.basename(sh)), "w").write(open(sh).read())
            written.append(os.path.join(d, "shapes", os.path.basename(sh)))
        path = os.path.join(d, f"{rid}.toml")
        open(path, "w").write(F.to_toml(p, [f"llmbox recipe {rid}: install with `llmbox recipe pull && llmbox install {rid} --from registry --host <yours>`",
                                            "the hardware layer (context, threads, placement) is fitted to your machine on install"]))
        written.append(path)
        m = p["model"]
        index.append(dict({"id": rid, "name": rid}, **(meta or {}).get(rid, {}), hf_repo=m.get("hf_repo"), file=m.get("file"),
                          sha256=m.get("sha256"), engine=p["runtime"].get("engine", "llama.cpp"), reference=p.get("reference") or {}))
    # the calibrated task bank of the released suite: `llmbox test` on another machine picks its tasks with it
    from . import irt, suite
    ch = irt.canonical(suite.content_hash())
    bank = {}
    if os.path.exists(irt.bank_path(ch)):
        open(os.path.join(d, f"bank-{ch}.json"), "w").write(open(irt.bank_path(ch)).read())
        written.append(os.path.join(d, f"bank-{ch}.json"))
        bank = {"version": suite.VERSION, "hash": ch, "file": f"bank-{ch}.json"}
    ip = os.path.join(d, "index.json")
    json.dump({"schema": SCHEMA, "built": time.strftime("%Y-%m-%dT%H:%M:%S"), "recipes": index, "suite": bank}, open(ip, "w"), indent=1)
    keep = {os.path.basename(x) for x in written} | {"index.json"}   # (an old bank-*.json goes too)
    for sub in (d, os.path.join(d, "shapes")):   # recipes the ranking no longer has
        for f in os.listdir(sub) if os.path.isdir(sub) else []:
            if f not in keep and f.endswith((".toml", ".json")):
                os.remove(os.path.join(sub, f))
    return written + [ip]


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "llmbox"}), timeout=60) as r:
        return r.read()


def pull(url: str = DEFAULT_URL, only: list[str] | None = None, out=print) -> list[str]:
    """Fetch the published recipes into ~/.llmbox/recipes/registry; returns their ids."""
    base = url.rstrip("/")
    idx = json.loads(_get(f"{base}/recipes/index.json"))
    if idx.get("schema") != SCHEMA:
        raise SystemExit(f"{base}: registry schema {idx.get('schema')}, this llmbox reads {SCHEMA} - update llmbox")
    d = rc.recipes_dir(REGISTRY)
    os.makedirs(d, exist_ok=True)
    got = []
    for e in idx["recipes"]:
        if only and e["id"] not in only:
            continue
        text = _get(f"{base}/recipes/{e['id']}.toml").decode()
        open(os.path.join(d, f"{e['id']}.toml"), "w").write(text)
        got.append(e["id"])
        sh = os.path.join(F.SHAPES, f"{e['file']}.json")
        if e.get("file") and not os.path.exists(sh):
            try:
                body = _get(f"{base}/recipes/shapes/{urllib.parse.quote(e['file'])}.json")
                os.makedirs(F.SHAPES, exist_ok=True)
                open(sh, "wb").write(body)
            except OSError:   # no published shape: fit reads the header from Hugging Face
                pass
    json.dump(idx, open(os.path.join(d, "index.json"), "w"), indent=1)   # scores for llmbox pick
    b = idx.get("suite") or {}
    if b.get("file"):   # the task bank, for `llmbox test`
        from . import irt
        os.makedirs(os.path.dirname(irt.bank_path(b["hash"])), exist_ok=True)
        open(irt.bank_path(b["hash"]), "wb").write(_get(f"{base}/recipes/{b['file']}"))
    out(f"{len(got)} recipes from {base} in {d}")
    return got
