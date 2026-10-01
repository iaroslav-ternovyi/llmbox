"""`llmbox fit`: the hardware half of a recipe, fitted to one machine in seconds, without downloading the model.

A recipe has two layers (docs/product.md §3):
  portable - model file, chat template, sampling, anti-loop, KV type. They set the quality score, so
             fit never changes them.
  hardware - context, batch, threads, prompt cache, slots, speculative decoding, local paths. Speed and memory only.
fit reads the model's shape (GGUF header), the target host and, when the recipe was measured somewhere, the ratio
measured / predicted on that box (it carries what the formula misses: the MTP speed-up, kernel quirks).

The context is the one hardware knob that can touch quality: the score was measured with the recipe's context, and the
long-document tasks need most of it. fit keeps that context when it fits and shows what a smaller one would buy; below
it, the long-document part of the score no longer applies, and fit says so. When the model does not fit at all, fit
names what would (a smaller quant, a quantized KV cache) and picks neither: each is another recipe with its own score.
"""
from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import dataclass, field

from . import estimate as E
from .hosts import HOME

# dotted paths; a table name covers everything under it
PORTABLE = ("description", "notes", "model.hf_repo", "model.file", "model.sha256", "chat", "sampling", "antiloop",
            "placement.kv_type")
HARDWARE = ("model.path", "runtime", "placement.ctx", "placement.fit", "placement.fit_target_mib", "placement.n_cpu_moe", "placement.flash_attn",
            "placement.load_mode", "placement.batch", "placement.ubatch", "placement.slots", "placement.kv_unified",
            "placement.cache_ram", "placement.cache_ram_headroom_mib", "placement.cache_reuse", "placement.kv_offload",
            "speculative", "serve", "extra")   # speculative decoding is lossless: whether it pays is a speed question
MIN_CTX = 8192
LONGDOC_TOKENS = 200_000      # the suite's longest documents (long-document block, suite v0.9)
SHAPES = os.path.join(HOME, "shapes")


def _get(d: dict, path: str):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _put(d: dict, path: str, v) -> None:
    ks = path.split(".")
    for k in ks[:-1]:
        d = d.setdefault(k, {})
    d[ks[-1]] = copy.deepcopy(v)


def layer(r: dict, paths: tuple) -> dict:
    out: dict = {}
    for p in paths:
        v = _get(r, p)
        if v is not None:
            _put(out, p, v)
    return out


# ---- inputs: model shape, calibration ---------------------------------------------------------------------------------

def shape_for(r: dict, host=None) -> E.ModelShape:
    """The model's shape: cached by file name, else from Hugging Face (header ranges only), else from the host's copy."""
    m = r["model"]
    fname = os.path.basename(m.get("file") or m.get("path") or "")
    cp = os.path.join(SHAPES, fname + ".json")
    if fname and os.path.exists(cp) and not E.stale(json.load(open(cp))):
        return E.ModelShape(**json.load(open(cp)))
    sh = None
    if m.get("hf_repo") and m.get("file"):
        from . import hf
        f = next((f for f in hf.list_gguf(m["hf_repo"]) if f.name == m["file"] or os.path.basename(f.name) == fname), None)
        if f:
            sh = E.analyze(hf.read_headers(f))
    if sh is None and host is not None and m.get("path"):
        from . import speed
        sh = speed.shape_on_host(host, m["path"])
    if sh is None:
        raise SystemExit(f"{r['id']}: cannot read the model header (no cache, not on Hugging Face, no host copy)")
    os.makedirs(SHAPES, exist_ok=True)
    json.dump(dict(sh.__dict__), open(cp, "w"))
    return sh


@dataclass
class Calibration:
    k2: float = 1.0               # measured / predicted decode at shallow depth
    kd: float = 1.0               # the same deep in the context
    deep_k: int = 88              # depth (thousands of tokens) the deep figure refers to
    source: str = "uncalibrated: formula only"
    measured: dict = field(default_factory=dict)
    term: str = ""                # what k2/kd scale: "" the whole speed; "cpu" / "gpu" only the time of what was read from
                                  # RAM / on the card on the measured machine. The formula's card side and fixed per-layer part
                                  # are fitted to public runs; a ratio of the whole made a 5090 look 50% faster than measured.

    def tps(self, shape: E.ModelShape, hw: E.HostSpec, depth: int, deep: bool = False, **kw) -> float:
        """Calibrated decode speed `depth` tokens into the context (deep: with the deep factor)."""
        k = self.kd if deep else self.k2
        p = E.plan(shape, hw, depth=depth, cpu_k=k if self.term == "cpu" else 1.0, gpu_k=k if self.term == "gpu" else 1.0, **kw)
        return p.decode_tps_at_depth * (1.0 if self.term else k)


def speculative(r: dict) -> bool:
    sp = r.get("speculative") or {}
    return bool(sp.get("type")) and (sp.get("draft_max") or 0) > 0


def solve(r: dict, shape: E.ModelShape, hw: E.HostSpec, measured_tps: float, depth: int, term: str | None = None, **kw) -> tuple[float, str]:
    """(factor, term) that makes the formula give the measured speed on the machine it was measured on: the factor goes on
    the time of what that machine read from RAM when that was a real share of it (the less certain side), else on the
    card's. A recipe with speculative decoding keeps a ratio of the whole: drafting saves whole passes, fixed part included."""
    p = E.plan(shape, hw, depth=depth, **kw)
    ratio = measured_tps / p.decode_tps_at_depth
    if speculative(r):
        return ratio, ""
    cpu, gpu, fixed = p.parts
    term = term or ("cpu" if cpu >= 0.25 * sum(p.parts) else "gpu")
    part, rest = (cpu, gpu + fixed) if term == "cpu" else (gpu, cpu + fixed)
    k = (1 / measured_tps - rest) / part if part > 0 else 0
    return (k, term) if 0.2 <= k <= 5 else (ratio, "")


def calibration(r: dict, shape: E.ModelShape, ref_host: str) -> Calibration:
    """Ratio measured / predicted on the box where this recipe was measured (newest result with a speed figure)."""
    from . import hosts, registry, report, results
    if ref_host == registry.REGISTRY or r.get("reference"):   # a published recipe: its reference measurement travels with it
        return registry.calibration(r, shape)
    recs = [x for x in results.load_all(ref_host) if (x.get("recipe") or {}).get("id") == r["id"]
            and ((x.get("summary") or {}).get("speed") or {}).get("decode_tps")]
    if not recs:
        return Calibration()
    rec = max(recs, key=lambda x: x.get("created", ""))
    sp = rec["summary"]["speed"]
    hw = hosts.spec(hosts.load(ref_host))
    ctx, kv = r["placement"]["ctx"] or shape.context_length, r["placement"]["kv_type"]
    k2, term = solve(r, shape, hw, sp["decode_tps"], 2000, ctx=ctx, kv_type=kv)
    deep = [(report._depth_k(k), d["decode_tps"]) for k, d in (sp.get("by_depth") or {}).items()
            if report._depth_k(k) >= 24 and d.get("decode_tps")]
    dk, dv = max(deep) if deep else (88, None)
    kd, t2 = solve(r, shape, hw, dv, int(dk * 1000), term=term or None, ctx=ctx, kv_type=kv) if dv else (k2, term)
    if t2 != term:   # one form for both figures: the deep one could not be solved on the same side
        kd = k2
    return Calibration(k2, kd, int(dk), f"calibrated on 1 run on {ref_host} ({rec.get('created', '')[:10]})",
                       {"decode_tps": sp["decode_tps"], "deep_tps": dv, "by_depth": {str(int(round(k))): v for k, v in sorted(deep)}}, term)


# ---- the fit ----------------------------------------------------------------------------------------------------------

@dataclass
class Fit:
    fits: bool
    ctx: int = 0
    want_ctx: int = 0             # the context the score was measured with
    ubatch: int = 2048
    threads: int = 0
    cpu_affinity: str = ""
    plan: E.Plan | None = None
    tps: float = 0.0              # calibrated, short chat
    tps_deep: float = 0.0         # calibrated, deep_k thousand tokens in context
    deep_k: int = 88
    calibration: Calibration = field(default_factory=Calibration)
    reason: str = ""
    options: list[str] = field(default_factory=list)       # faster choices that keep the recipe (speed only)
    warnings: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)  # when it does not fit: other recipes, never picked here


def _ctx_ladder(top: int) -> list[int]:
    out, c = [], top
    while c >= MIN_CTX:
        out.append(c)
        c //= 2
    return out


def fit(r: dict, shape: E.ModelShape, hw: E.HostSpec, cores: int | None = None, cal: Calibration | None = None,
        ctx: int | None = None, same_host: bool = False) -> Fit:
    cal = cal or Calibration()
    kv = r["placement"]["kv_type"]
    want = r["placement"]["ctx"] or shape.context_length or 32768
    top = min(ctx or want, shape.context_length or ctx or want)
    threads = cores or (r["runtime"]["threads"] if same_host else 0)   # the recipe's thread count is its own box's
    if cores and cores >= 6 and shape.is_moe and not same_host:
        threads = cores - 1   # experts on the CPU: leave the thread that drives the card a core (8-core box, K2 2026-09-30: 8 threads -10%, 7 = 4 on decode, faster prompts)
    base = dict(want_ctx=want, threads=threads, cpu_affinity=r["runtime"]["cpu_affinity"] if same_host else "",
                calibration=cal, deep_k=cal.deep_k)
    chosen = None
    for c in _ctx_ladder(top):     # context first (it carries the long-document score), then prompt speed:
        for ub in (2048, 1024, 512):   # a smaller compute buffer frees VRAM for a longer context
            p = E.plan(shape, hw, ctx=c, kv_type=kv, ubatch=ub, depth=cal.deep_k * 1000)
            if p.fits:
                chosen = (c, ub, p)
                break
        if chosen:
            break
    if not chosen:
        p = E.plan(shape, hw, ctx=MIN_CTX, kv_type=kv, ubatch=512)
        f = Fit(fits=False, reason=p.reason, plan=p, **base)
        f.alternatives = _alternatives(shape, hw, kv)
        return f
    c, ub, p = chosen
    f = Fit(fits=True, ctx=c, ubatch=ub, plan=p, tps=cal.tps(shape, hw, 2000, ctx=c, kv_type=kv, ubatch=ub),
            tps_deep=cal.tps(shape, hw, cal.deep_k * 1000, deep=True, ctx=c, kv_type=kv, ubatch=ub), **base)
    if c < want:
        f.warnings.append(f"context {c // 1024}k is below the {want // 1024}k the score was measured with"
                          + (f": documents over ~{int(c * 0.85) // 1000}k tokens will not fit, so the long-document score does not apply"
                             if c < LONGDOC_TOKENS else ""))
    # what a smaller context would buy (speed only): shown, not chosen
    for c2 in _ctx_ladder(c)[1:3]:
        p2 = E.plan(shape, hw, ctx=c2, kv_type=kv, ubatch=ub, depth=2000)
        gain = p2.decode_tps_at_depth / E.plan(shape, hw, ctx=c, kv_type=kv, ubatch=ub, depth=2000).decode_tps_at_depth - 1
        if p2.fits and gain >= 0.05:
            f.options.append(f"--ctx {c2 // 1024}k: ~{cal.tps(shape, hw, 2000, ctx=c2, kv_type=kv, ubatch=ub):.0f} tok/s (+{gain * 100:.0f}%)"
                             + (f", but documents over ~{int(c2 * 0.85) // 1000}k tokens no longer fit" if c2 < LONGDOC_TOKENS else ""))
    return f


def _alternatives(shape: E.ModelShape, hw: E.HostSpec, kv: str) -> list[str]:
    """What would make it fit. Hardware first (keeps the recipe and its score), then other recipes."""
    import math
    out = []
    p = E.plan(shape, hw, ctx=MIN_CTX, kv_type=kv, ubatch=512)
    vram_ok = not p.reason.startswith("VRAM") and not p.reason.startswith("dense")
    if vram_ok:   # only RAM is short: say how much would do
        need = (p.ram_used_mib + hw.ram_headroom_mib) / 1024
        out.append(f"with {math.ceil(need / 8) * 8} GB of system RAM this recipe fits as it is (same score)")
    elif kv in ("f16", "bf16") and E.plan(shape, hw, ctx=32768, kv_type="q8_0", ubatch=512).fits:
        out.append("a q8_0 KV cache would fit at 32k: that is a quality variant of this recipe and needs its own score")
    if not shape.is_moe:
        out.append(f"dense model: all {shape.total_bytes / 2**30:.1f} GB must sit in VRAM")
    out.append("a smaller quant of the same model: another recipe with its own score")
    return out


# ---- output -----------------------------------------------------------------------------------------------------------

def apply(r: dict, f: Fit, models_dir: str | None = None, server: str | None = None) -> dict:
    """The recipe with its hardware layer replaced by the fit (portable layer untouched)."""
    out = copy.deepcopy(r)
    out["placement"].update(ctx=f.ctx, ubatch=f.ubatch, batch=max(f.ubatch, r["placement"]["batch"]), slots=1, cache_ram="auto")
    out["runtime"].update(threads=f.threads, cpu_affinity=f.cpu_affinity)
    if server:
        out["runtime"]["server"] = server
    if models_dir and r["model"].get("file"):
        out["model"]["path"] = os.path.join(models_dir, os.path.basename(r["model"]["file"]))
    return out


def to_toml(r: dict, header: list[str] | None = None) -> str:
    """Minimal TOML writer for recipe dicts (scalars, lists of scalars, nested tables)."""
    def val(v) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, list):
            return "[" + ", ".join(val(x) for x in v) + "]"
        return json.dumps(str(v), ensure_ascii=False)
    lines = [f"# {h}" for h in header or []]
    def table(d: dict, prefix: str) -> None:
        scal = {k: v for k, v in d.items() if not isinstance(v, dict)}
        subs = {k: v for k, v in d.items() if isinstance(v, dict)}
        if prefix and (scal or not subs):
            lines.append(f"[{prefix}]")
        lines.extend(f"{k} = {val(v)}" for k, v in scal.items())
        for k, v in subs.items():
            table(v, f"{prefix}.{k}" if prefix else k)
    table({k: v for k, v in r.items() if k != "id"}, "")
    return "\n".join(lines) + "\n"


def render(rid: str, r: dict, f: Fit, target: str, shape: E.ModelShape, hw: E.HostSpec) -> str:
    port = layer(r, PORTABLE)
    sam = " · ".join(f"{k.replace('_', '-')} {v}" for k, v in (port.get("sampling") or {}).items() if k != "max_tokens")
    al = r["antiloop"]
    out = [f"{rid} on {target}  ({hw.vram_mib / 1024:.0f} GB VRAM · {hw.ram_mib / 1024:.0f} GB RAM @ {hw.ram_bw_gbs:.0f} GB/s)", "",
           "same on every box (sets the score):",
           f"  model        {r['model'].get('hf_repo', '')} / {r['model'].get('file', '')}",
           f"  sampling     {sam or 'server defaults'}",
           f"  KV cache     {r['placement']['kv_type']}"
           + (f"   ·   speculative {r['speculative']['type']}" if r["speculative"]["type"] else "")
           + (f"   ·   reasoning reserve {al['reasoning_budget']}" if al["reasoning_budget"] >= 0 else ""), ""]
    if not f.fits:
        out += [f"DOES NOT FIT: {f.reason}", "", "what would make it fit (nothing is picked for you):"]
        out += [f"  - {a}" for a in f.alternatives]
        return "\n".join(out)
    p = f.plan
    kvgb = shape.kv_bytes_per_token(r["placement"]["kv_type"]) * f.ctx / 2**30
    out += ["fitted to this box (speed only):",
            f"  context      {f.ctx // 1024}k" + ("" if f.ctx == f.want_ctx else f"   (recipe: {f.want_ctx // 1024}k)") + f"   ·   KV {kvgb:.1f} GB",
            f"  placement    llama.cpp --fit: {p.gpu_expert_frac * 100:.0f}% of the experts on the GPU, {p.ram_used_mib / 1024:.1f} GB in RAM"
            if shape.is_moe else f"  placement    everything on the GPU ({p.vram_used_mib / 1024:.1f} GB)",
            f"  batch        {max(f.ubatch, r['placement']['batch'])} / ubatch {f.ubatch}",
            f"  threads      {f.threads or 'llama.cpp default'}" + (f" on cores {f.cpu_affinity}" if f.cpu_affinity else ""),
            "  prompt cache auto (RAM the model leaves free)", "",
            f"predicted: ~{f.tps:.0f} tok/s in a short chat, ~{f.tps_deep:.0f} tok/s with {f.deep_k}k in context",
            f"           {f.calibration.source}"]
    if f.warnings:
        out += [""] + [f"! {w}" for w in f.warnings]
    if f.options:
        out += ["", "faster, same recipe:"] + [f"  {o}" for o in f.options]
    return "\n".join(out)


def stamp(target: str, hw: E.HostSpec, f: Fit) -> list[str]:
    return [f"Fitted by `llmbox fit` on {time.strftime('%Y-%m-%d')} for {target}: {hw.vram_mib / 1024:.0f} GB VRAM, "
            f"{hw.ram_mib / 1024:.0f} GB RAM @ {hw.ram_bw_gbs:.0f} GB/s. Hardware layer only; the portable layer is the recipe's.",
            f"Predicted ~{f.tps:.0f} tok/s short chat, ~{f.tps_deep:.0f} at {f.deep_k}k ({f.calibration.source})."]
