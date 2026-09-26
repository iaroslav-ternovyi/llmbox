"""`llmbox scout <hf-repo>`: which quants of a model fit a host, at what context, and how fast - before downloading."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import estimate as E
from . import hf

AGENT_MIN_TPS = 25.0   # below this an agent session crawls; flag it


@dataclass
class Row:
    file: hf.GGUFFile
    shape: E.ModelShape
    plan: E.Plan
    max_ctx: int
    at_depth: float
    at_native: float   # decode at depth with the full native context allocated (0 = does not fit)


def scout(repo: str, hw: E.HostSpec, kv_type: str = "q8_0", depth: int = 50_000, quants: list[str] | None = None) -> list[Row]:
    files = hf.list_gguf(repo)
    if quants:
        files = [f for f in files if any(q.upper() in f.quant for q in quants)]
    # one header per part is needed for the tensor directory of split models; fetch files in parallel
    with ThreadPoolExecutor(max_workers=4) as ex:
        shapes = list(ex.map(lambda f: E.analyze(hf.read_headers(f)), files))
    rows = []
    for f, shape in zip(files, shapes):
        ctx = E.max_context(shape, hw, kv_type=kv_type)
        p = E.plan(shape, hw, ctx=ctx, kv_type=kv_type, depth=depth)
        native = E.plan(shape, hw, ctx=shape.context_length or ctx, kv_type=kv_type, depth=depth)
        rows.append(Row(f, shape, p, ctx, p.decode_tps_at_depth, native.decode_tps_at_depth if native.fits else 0.0))
    return rows


def render(repo: str, rows: list[Row], hw: E.HostSpec, depth: int = 50_000) -> str:
    if not rows:
        return f"{repo}: no GGUF files found"
    s = rows[0].shape
    out = [f"{repo}  ·  arch {s.arch}  ·  {s.total_params/1e9:.1f}B total / {s.active_params/1e9:.1f}B active"
           f"{f'  ·  MoE {s.n_expert_used}/{s.n_expert} experts' if s.is_moe else '  ·  dense'}"
           f"  ·  native ctx {s.context_length:,}  ·  KV layers {s.attn_layers}/{s.n_layers}"
           f"{'  ·  MTP head ✓' if s.n_mtp_layers else ''}",
           f"host: {hw.vram_mib/1024:.0f} GiB VRAM, {hw.ram_mib/1024:.0f} GiB RAM @ {hw.ram_bw_gbs:.0f} GB/s",
           "",
           f"{'quant':14s} {'size':>8s} {'fits':>5s} {'ctx':>8s} {'exp@GPU':>7s} {'tok/s':>6s} {'@depth':>7s} {'@native':>8s}  note"]
    for r in rows:
        p = r.plan
        notes = [] if p.fits else [p.reason]
        if p.fits and r.at_depth < AGENT_MIN_TPS:
            notes.append("slow for agents")
        if r.shape.expert_cpu_eff < 0.85:
            notes.append("IQ types in experts: CPU-bound, slower than size suggests")
        if r.max_ctx < depth:
            notes.append(f"@depth taken at {r.max_ctx:,}")
        native = f"{r.at_native:8.1f}" if r.at_native else f"{'no fit':>8s}"
        out.append(f"{r.file.quant:14s} {r.file.size/2**30:7.1f}G {'yes' if p.fits else 'no':>5s} {r.max_ctx:>8,} "
                   f"{p.gpu_expert_frac:>7.0%} {p.decode_tps:>6.1f} {r.at_depth:>7.1f} {native}  {'; '.join(notes)}")
    if s.sampling:
        out += ["", "author sampling in GGUF: " + ", ".join(f"{k}={v:.3g}" if isinstance(v, float) else f"{k}={v}"
                                                        for k, v in s.sampling.items())]
    out += ["",
            "ctx     = largest context whose decode stays within 5% of the 32k config (more KV = fewer experts on the GPU)",
            f"tok/s   = predicted decode, speculative decoding OFF;  @depth = at {depth:,} tokens;  @native = same depth, full native context allocated",
            "accuracy: calibrated on 6 models / 3 architectures, error <= 10% on gpu-box"]
    return "\n".join(out)
