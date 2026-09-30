"""`llmbox pick`: the site's question in the terminal - what should I run on this machine?

Every measured recipe from the registry (the site's recipes/index.json, pulled with `llmbox recipe pull`), fitted to
this host the way `install` would fit it: does it fit, at what context, how fast (predicted from the model's shape and
this machine's memory speeds, calibrated on the reference box's measurement). Ranked by the score for the chosen use;
models whose 95% ranges overlap the best one are not measurably apart, so among them the fastest is the pick.
"""
from __future__ import annotations

import json
import os

from . import estimate as E
from . import fit as F
from . import hosts, hwclass, recipe as rc, registry

LONG = 32000
USES = {"all": None, "coding": "Coding", "agents": "Agents & tools", "ask": "Ask & learn", "docs": "Documents & writing"}


def index() -> dict:
    p = os.path.join(rc.recipes_dir(registry.REGISTRY), "index.json")
    if not os.path.exists(p):
        raise SystemExit("no recipes here yet: `llmbox recipe pull` fetches the site's")
    return json.load(open(p))


FASTER, CLOSE = 1.3, 5.0   # the pick trades score for speed only for a model this much faster and this close to the best


def engines_of(prof: dict | None) -> set:
    """The engines a machine can run models with: llama.cpp always (llmbox installs its official build), others only
    when a build of them is there (ik_llama.cpp has no release builds to install)."""
    rts = ((prof or {}).get("hw") or {}).get("runtimes") or []
    return {"llama.cpp"} | ({"ik_llama.cpp"} if any("ik_llama" in (r.get("path") or "") for r in rts) else set())


def choose(ok: list[dict]) -> tuple[dict, str]:
    """The recommendation among the models that fit (best score first) and why: the best score, unless a model not
    measurably apart from it and at most CLOSE points below runs at least FASTER times as fast 32k into a session.
    A model needing an engine this machine does not have is never the recommendation."""
    ok = [x for x in ok if not x.get("needs")] or ok
    top = ok[0]
    long = lambda x: min(x["t2"], x["td"] or x["t2"])
    quick = [x for x in ok[1:] if x.get("tied") and x["use_score"] >= top["use_score"] - CLOSE and long(x) >= FASTER * long(top)]
    if quick:
        b = max(quick, key=long)
        return b, f"{top['use_score'] - b['use_score']:.0f} points below the best score, not measurably apart from it, and {long(b) / long(top):.1f}x as fast here"
    return top, "the best score among the models that fit"


def rank(hw: E.HostSpec, cores: int | None, use: str = "all", cls: str | None = None, mac: bool = False,
         engines: set | None = None) -> list[dict]:
    """cls: this machine's hardware class (llmbox/hwclass.py): where people measured a model on the same class, their
    median replaces the prediction (measured=machines)."""
    rows = []
    for e in index()["recipes"]:
        try:
            r = rc.load(registry.REGISTRY, e["id"])
            shape = F.shape_for(r)
        except (OSError, ValueError, SystemExit) as ex:
            rows.append(dict(e, fits=False, why=f"cannot read the model: {ex}"))
            continue
        # the reference box's measured/predicted ratio is about an NVIDIA card streaming experts over PCIe: not a Mac's
        f = F.fit(r, shape, hw, cores=cores, cal=F.Calibration() if mac else registry.calibration(r, shape))
        cal = F.Calibration() if mac else registry.calibration(r, shape, at_k=LONG // 1000)
        # one depth for every model (32k: an agent a few steps in), calibrated with the measured deep ratio
        t32 = E.plan(shape, hw, ctx=f.ctx, kv_type=r["placement"]["kv_type"], ubatch=f.ubatch, depth=LONG).decode_tps_at_depth * cal.kd \
            if f.fits and f.ctx >= LONG else None
        score = e.get("score") if USES[use] is None else (e.get("uses") or {}).get(USES[use])
        eng = e.get("engine") or "llama.cpp"
        row = dict(e, fits=f.fits, why=f.reason if not f.fits else "", ctx=f.ctx, t2=f.tps, td=t32, measured=0,
                   use_score=score, size_gb=round((shape.total_bytes or 0) / 1e9, 1),
                   needs=eng if eng not in (engines or {"llama.cpp"}) else None)
        m = (e.get("measured") or {}).get(cls) if cls else None
        if m and m[0]:
            row.update(fits=True, why="", t2=m[0], td=m[1] or row["td"], measured=m[3])
        rows.append(row)
    fit_ = sorted((x for x in rows if x["fits"] and x.get("use_score") is not None), key=lambda x: -x["use_score"])
    if fit_ and fit_[0].get("range") and USES[use] is None:   # ranges are measured for the whole score, not per use
        lo = fit_[0]["range"][0]
        for x in fit_:   # overlapping 95% ranges: not measurably apart from the best
            x["tied"] = bool(x.get("range")) and x["range"][1] >= lo
    return fit_ + [x for x in rows if not x["fits"] or x.get("use_score") is None]


def run(host: str | None, use: str, what_if: tuple | None = None, out=print) -> int:
    if what_if:
        gpu, vram_gb, ram_gb, ram_bw = what_if
        vram = int(vram_gb * 1024) if vram_gb else hosts.gpu_vram(gpu)
        if not vram:
            raise SystemExit(f"unknown VRAM for {gpu!r}: pass --vram-gb")
        hw = E.HostSpec(vram_mib=vram, ram_mib=int(ram_gb * 1024), ram_bw_gbs=ram_bw, vram_bw_gbs=hosts.gpu_bw(gpu) or 500.0)
        cores, where = None, f"{gpu} · {ram_gb:g} GB RAM @ {ram_bw:g} GB/s"
        host = "<your host>"

        cls = hwclass.key(gpu, vram, ram_bw)
    else:
        prof = hosts.load(host)
        hw = hosts.spec(prof)
        cpu = prof["hw"].get("cpu") or {}
        cores, where = cpu.get("cores") or cpu.get("threads"), f"{host} ({hw.vram_mib / 1024:.0f} GB VRAM · {hw.ram_mib / 1024:.0f} GB RAM @ {hw.ram_bw_gbs:g} GB/s)"
        from . import results
        cls = hwclass.of_host(results.host_fingerprint(prof))
    mac = (cls or "").startswith("apple-")
    rows = rank(hw, cores, use, cls, mac, engines_of(None if what_if else prof))
    ok = [x for x in rows if x["fits"] and x.get("use_score") is not None]
    out(f"What to run on {where}, for {USES[use] or 'all work'} ({len(ok)} of {len(rows)} models fit):\n")
    out(f"  {'model':52s} {'% of Opus':>13s} {'tok/s':>6s} {'@32k':>5s} {'context':>7s}")
    for x in ok:
        rg = f" ({x['range'][0]:.0f}-{x['range'][1]:.0f})" if x.get("range") and USES[use] is None else ""
        mark = "m" if x.get("measured") else "~"
        out(f"{'*' if x.get('tied') else ' '} {x['name'][:52]:52s} {x['use_score']:5.1f}{rg:>8s} {mark}{x['t2']:5.0f} {x['td'] or 0:5.0f} {x['ctx'] // 1024:5d}k"
            + (f"  needs {x['needs']}" if x.get("needs") else ""))
    if ok:
        best, why = choose(ok)
        out(f"\n* not measurably apart from the best score.  The pick: {best['name']} ({best['t2']:.0f} tok/s, {best['td'] or best['t2']:.0f} at 32k) - {why}")
        out(f"  llmbox start {best['id']}")
    skip = [x for x in rows if not x["fits"]]
    if skip:
        out(f"\ndo not fit: " + ", ".join(f"{x['name']} ({x.get('size_gb') or '?'} GB)" for x in skip))
    if mac:
        out("\nMac: speeds are rough - llama.cpp on Metal is not measured here yet (llmbox runs models on Linux + NVIDIA for now)")
    n = sum(1 for x in ok if x.get("measured"))
    out(f"\nm = measured on machines like this one ({hwclass.label(cls) if cls else '?'}; {n} of the models), ~ = predicted "
        "from the model's shape and this machine's memory speeds. `llmbox test <id>` measures one and sends it")
    return 0
