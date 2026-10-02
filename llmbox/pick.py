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


def entry_spec(name: str, ram_gb: int = 64, ram_bw: float = 60.0) -> E.HostSpec:
    """A picker entry as a machine, the way the site's picker builds it (plan.js boxFrom): a card with its VRAM and
    bandwidth plus this much system RAM at this speed; a Mac or a Ryzen AI Max as unified memory (the size nearest
    ram_gb it is sold with), of which the GPU may use about 3/4 (2/3 on a Mac under 36 GB), at the chip's speed."""
    from . import hwclass
    kind = hwclass.entry_kind(name)
    if kind in ("mac", "chip"):
        g = next(x for x in hwclass.MACS + hwclass.APUS if x[0] == name)
        mem = (hwclass.mac_memory(name, ram_gb) if kind == "mac" else min(ram_gb, g[4])) * 1024
        vram = mem * (0.67 if kind == "mac" and mem < 36864 else 0.75)
        return E.HostSpec(vram_mib=vram, ram_mib=mem - vram, ram_bw_gbs=float(g[2]), vram_bw_gbs=float(g[2]),
                          backend="metal" if kind == "mac" else "vulkan", gpu_eff=E.gpu_generation(name) if kind == "mac" else 1.0)
    g = next(x for x in hwclass.NVIDIA_CARDS + hwclass.AMD_CARDS if x[0] == name)
    return E.HostSpec(vram_mib=g[1], ram_mib=ram_gb * 1024, ram_bw_gbs=float(ram_bw), vram_bw_gbs=float(g[2]),
                      backend="vulkan" if kind == "amd" else "cuda", gpu_eff=E.gpu_generation(name) if kind == "card" else 1.0)


def rank(hw: E.HostSpec, cores: int | None, use: str = "all", cls: str | None = None, mac: bool = False,
         engines: set | None = None) -> list[dict]:
    """cls: this machine's hardware class (llmbox/hwclass.py): where people measured a model on the same class, their
    median replaces the prediction (measured=machines)."""
    items = []
    for e in index()["recipes"]:
        try:
            r = rc.load(registry.REGISTRY, e["id"])
            items.append((e, r, F.shape_for(r)))
        except (OSError, ValueError, SystemExit) as ex:
            items.append((e, None, f"cannot read the model: {ex}"))
    return rank_rows(hw, cores, items, use, cls, mac, engines)


def rank_rows(hw: E.HostSpec, cores: int | None, items: list[tuple], use: str = "all", cls: str | None = None,
              mac: bool = False, engines: set | None = None) -> list[dict]:
    """rank() over given models: items are (model list entry, recipe, shape), or (entry, None, why it cannot be read).
    The site's map ranks its own build's models for each picker entry with it, as `llmbox pick` would there."""
    rows = []
    for e, r, shape in items:
        if r is None:
            rows.append(dict(e, fits=False, why=shape))
            continue
        # the reference box's measured/predicted ratio is about an NVIDIA card streaming experts over PCIe: not a Mac's
        ref = registry.calibration(r, shape)   # a Mac keeps only its depth: the box's factors are about its card and RAM
        f = F.fit(r, shape, hw, cores=cores, cal=F.Calibration(deep_k=ref.deep_k) if mac else ref)
        cal = F.Calibration() if mac else registry.calibration(r, shape, at_k=LONG // 1000)
        # one depth for every model (32k: an agent a few steps in), calibrated with the measured deep ratio
        t32 = cal.tps(shape, hw, LONG, deep=True, ctx=f.ctx, kv_type=r["placement"]["kv_type"], ubatch=f.ubatch) \
            if f.fits and f.ctx >= LONG else None
        score = e.get("score") if USES[use] is None else (e.get("uses") or {}).get(USES[use])
        eng = e.get("engine") or "llama.cpp"
        row = dict(e, fits=f.fits, why=f.reason if not f.fits else "", ctx=f.ctx, t2=f.tps, td=t32, measured=0,
                   use_score=score, size_gb=round((shape.total_bytes or 0) / 1e9, 1),
                   needs=eng if eng not in (engines or {"llama.cpp"}) else None)
        m = (e.get("measured") or {}).get(cls) if cls else None
        if m and m[0]:
            row.update(t2=m[0], td=m[1] or row["td"], measured=m[3])   # the class ignores RAM size: fitting stays this machine's
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
        hw = E.HostSpec(vram_mib=vram, ram_mib=int(ram_gb * 1024), ram_bw_gbs=ram_bw, vram_bw_gbs=hosts.gpu_bw(gpu, vram) or 500.0,
                        gpu_eff=E.gpu_generation(gpu), backend=hosts.gpu_backend(gpu))
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
        out("\nMac: speeds are rough until people with this Mac measure them (llama.cpp on Metal: llmbox test)")
    n = sum(1 for x in ok if x.get("measured"))
    out(f"\nm = measured on machines like this one ({hwclass.label(cls) if cls else '?'}; {n} of the models), ~ = predicted "
        "from the model's shape and this machine's memory speeds. `llmbox test <id>` measures one and sends it")
    return 0


def slow_note(measured: float, expected: float | None) -> str | None:
    """A run far below what its hardware does (under 40%): llama.cpp most likely ran on the CPU (a build without GPU
    support, a driver problem, the card busy with something else) - the commonest complaint about local benchmarks
    (LocalScore's, 2025), so it is said out loud instead of being reported as this computer's speed."""
    if expected and measured and measured < 0.4 * expected:
        return (f"  ! {measured:.0f} tokens/s is far below the ~{expected:.0f} this hardware does: the model is probably running on "
                "the CPU (a llama.cpp build without GPU support, a driver problem, or the card busy) - `llmbox doctor` checks")
    return None
