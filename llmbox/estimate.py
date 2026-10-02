"""Predict how a GGUF model fits a host and how fast it decodes, from the header alone.

Model of llama.cpp with `--fit on` (the placement we use):
  GPU  <- every non-expert weight (attention, SSM, norms, shared experts, output head), KV cache, compute buffers,
          and as many expert layers as still fit;
  RAM  <- the remaining expert layers (pinned).
Decode is memory-bound, so time/token = bytes read from RAM / RAM bandwidth + bytes read from VRAM / VRAM bandwidth
+ a fixed per-token overhead. An MoE layer reads only expert_used/expert_count of its expert weights per token.
The constants are calibrated against measured runs (see CALIBRATION); keep them honest when new measurements arrive.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .gguf import GGUFHeader

# Measured on the reference box (RTX 5070 + DDR5, llama.cpp b11161). Efficiency = achieved / nominal bandwidth during decode.
RAM_EFFICIENCY = 0.80
# The GPU side, by backend and dense/MoE: (efficiency, fixed ms per layer per token: kernel launches and syncs). Fitted to
# public llama-bench tg128 runs with every weight on the card (depth 0), because on the reference box the experts in RAM
# hide the fixed part; tests/test_public_speeds.py holds the runs and checks the error per card.
#   CUDA dense: llama 7B Q4_0 on six Ada/Blackwell cards (llama.cpp discussion #15013)
#   CUDA MoE: gpt-oss 20B on six Ada/Blackwell cards (#15396): routing and expert kernels cost more per layer
#   Metal dense: llama 7B Q4_0 on nineteen M1-M5 chips (#4167); Metal MoE: community M4 Pro / M5 Max runs of 35B-A3B MoE
#   models (llm-bench.io, 2026-09)
#   Vulkan dense (AMD, RADV): llama 7B Q4_0 on seven Radeon cards and a Ryzen AI Max+ (#10879); Vulkan MoE: no table yet,
#   CUDA's MoE efficiency with a fixed part between the two public Radeon AI PRO R9700 runs (#19890)
DECODE = {("cuda", False): (0.90, 0.035), ("cuda", True): (0.72, 0.060), ("metal", False): (0.90, 0.14), ("metal", True): (0.80, 0.10),
          ("vulkan", False): (0.80, 0.022), ("vulkan", True): (0.72, 0.050)}
# Metal's attention at depth is compute-bound, not bandwidth-bound: each token of context costs every query head's width,
# in picoseconds per (query head x head width) per context token on an M2 Max (400 GB/s, 38 GPU cores), by head width.
# Measured 2026-10-02 with llama-bench b11344, flash attention, q8_0 KV: Qwen3.5-9B (width 256) 20.6 at 16k and 32k,
# Gemma-3-12B (256, windowed layers counted) 19.8, Llama-3.2-3B (128) 3.2, Gemma-4-E2B (512 global, 256 windowed) 21.4;
# what the KV reads already explain is taken out (tests/fixtures/metal_depth.json). From 256 up the cost per unit levels
# off; at 128 it is ~6x lower. CUDA shows nothing like it: the box loses 14% at 35k on Qwen3.5-9B, the M2 Max 52% at
# 32k. Other chips scale by their bandwidth, which tracks their GPU cores within a generation.
METAL_ATTN_PS = ((128, 3.2), (256, 20.3), (512, 21.4))
METAL_ATTN_REF_GBS = 400.0
# older NVIDIA generations reach less of their bandwidth (same tables: Ampere ~0.88, Turing ~0.85, Pascal ~0.6)
GPU_GENERATION = (("RTX 50", 1.0), ("RTX 40", 1.0), ("RTX PRO", 1.0), ("RTX 30", 0.88), ("RTX A", 0.88), ("A100", 0.88), ("A40", 0.88),
                  ("RTX 20", 0.85), ("TITAN RTX", 0.85), ("T4", 0.85), ("GTX 16", 0.75), ("GTX 10", 0.6), ("TITAN X", 0.6))


# Apple (#4167): an Ultra (two chips joined) reaches less of its bandwidth, the M5 generation more
APPLE_GENERATION = (("ULTRA", 0.82), ("M5", 1.3))


def gpu_generation(name: str) -> float:
    n = (name or "").upper().replace("NVIDIA ", "").replace("GEFORCE ", "")
    if re.match(r"^(APPLE |MAC )?M\d", n):
        return next((f for k, f in APPLE_GENERATION if k in n), 1.0)
    return next((f for k, f in GPU_GENERATION if k in n), 1.0)
GPU_RESERVE_MIB = 700              # driver + display + fit-target margin
COMPUTE_BUFFER_MIB = {512: 900, 1024: 1300, 2048: 2100}  # by -ub, measured-ish for 35B-A3B class

KV_BYTES = {"f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32, "q4_0": 18 / 32}
# sliding-window attention: every Nth layer is global, the rest keep only `sliding_window` tokens of KV (llama.cpp's
# per-architecture swa pattern; the GGUF carries the window but not the pattern)
SWA_PATTERN = {"gemma2": 2, "gemma3": 6, "gpt-oss": 2, "cohere2": 4}

_BLK = re.compile(r"^blk\.(\d+)\.")

# ggml types whose CPU dot products are compute-bound (lattice/codebook lookups) rather than bandwidth-bound.
# Calibrated: Ling IQ3_XXS decoded at ~0.55 of the bandwidth-bound prediction. IQ4_XS/IQ4_NL behave like K-quants.
_SLOW_CPU_TYPES = {16, 17, 18, 19, 21, 22, 29}  # IQ2_XXS, IQ2_XS, IQ3_XXS, IQ1_S, IQ3_S, IQ2_S, IQ1_M


def _cpu_eff(ggml_type: int) -> float:
    return 0.55 if ggml_type in _SLOW_CPU_TYPES else 1.0


@dataclass
class ModelShape:
    arch: str
    n_layers: int                     # transformer layers used for normal decoding (MTP/nextn layers excluded)
    n_mtp_layers: int
    attn_layers: int                  # layers with a KV cache
    kv_heads: int
    k_len: int
    v_len: int
    mla_kv_dim: int = 0               # MLA (compressed KV) models: per-layer latent size
    n_expert: int = 0
    n_expert_used: int = 0
    total_bytes: int = 0
    expert_bytes: int = 0             # routed experts (MoE)
    expert_bytes_by_layer: dict = field(default_factory=dict)
    nonexpert_bytes: int = 0          # everything read on the GPU per token (excl. embedding table, MTP layers)
    embed_bytes: int = 0
    sparse_bytes: int = 0             # per-layer token tables (Gemma PLE, Qwen3.8-Flash-Next n-grams): a few rows per
                                      # token, left mmapped on disk - neither GPU weights nor RAM the model must hold
    mtp_bytes: int = 0
    recurrent_state_bytes: int = 0    # per sequence, for hybrid SSM / linear-attention layers
    swa_layers: int = 0               # attention layers with a sliding window (their KV stops growing at swa_window)
    swa_window: int = 0
    kv_full_dim: int = 0              # sum over growing-KV layers of kv_heads * (k_len + v_len), per-layer exact
    kv_swa_dim: int = 0               # the same over the sliding-window layers (their own head count / key length)
    q_full_dim: int = 0               # sum over growing-KV layers of query heads * key length: attention's work per context
    q_swa_dim: int = 0                # token (Metal's depth cost); the same over the sliding-window layers
    k_swa_len: int = 0                # the sliding-window layers' key length where it differs (Gemma 4: 256 vs 512)
    output_tied: int = 0              # bytes of the embedding table used as the output layer (no output.weight: Gemma):
                                      # read whole on the GPU every token, unlike the embedding lookup's one row
    expert_cpu_eff: float = 1.0       # relative CPU dequant speed of the expert quant type (IQ* are slower)
    context_length: int = 0
    total_params: int = 0
    active_params: int = 0
    sampling: dict = field(default_factory=dict)

    @property
    def is_moe(self) -> bool:
        return self.n_expert > 1 and self.expert_bytes > 0

    def q_dims(self) -> tuple[float, float]:
        """(q_full_dim, q_swa_dim); a shape read before they were recorded: from its attention layers at a typical 3 query
        heads per KV head (Llama 3: 3, Qwen3.5: 4, Gemma 3: 2)."""
        if self.q_full_dim or self.q_swa_dim:
            return float(self.q_full_dim), float(self.q_swa_dim)
        per_layer = 3 * self.kv_heads * self.k_len
        return float((self.attn_layers - self.swa_layers) * per_layer), float(self.swa_layers * per_layer)

    def _kv_per_layer(self, kv_type: str) -> float:
        b = KV_BYTES.get(kv_type, 2.0)
        return self.mla_kv_dim * b if self.mla_kv_dim else self.kv_heads * (self.k_len + self.v_len) * b

    def kv_bytes_per_token(self, kv_type: str = "q8_0") -> float:
        """KV bytes per token of context, over the layers whose cache grows with the context (not the windowed ones)."""
        if self.kv_full_dim and not self.mla_kv_dim:
            return self.kv_full_dim * KV_BYTES.get(kv_type, 2.0)
        return (self.attn_layers - self.swa_layers) * self._kv_per_layer(kv_type)

    def kv_swa_bytes(self, kv_type: str = "q8_0", ctx: int | None = None) -> float:
        """Fixed KV of the sliding-window layers (up to the window, or ctx if smaller)."""
        w = min(self.swa_window, ctx) if ctx else self.swa_window
        if self.kv_swa_dim and not self.mla_kv_dim:
            return self.kv_swa_dim * w * KV_BYTES.get(kv_type, 2.0)
        return self.swa_layers * w * self._kv_per_layer(kv_type)


# architectures with per-layer token tables: a shape cached before sparse_bytes existed counted them as GPU weights
SPARSE_ARCHES = {"qwen4exp", "gemma4", "gemma3n"}


def stale(cached: dict) -> bool:
    """A cached shape to read again: one that lacks a field its kind of model needs."""
    return (cached.get("arch") in SPARSE_ARCHES and "sparse_bytes" not in cached) or \
        ("q_full_dim" not in cached and bool(cached.get("attn_layers"))) or \
        ("k_swa_len" not in cached and bool(cached.get("swa_layers"))) or "output_tied" not in cached


def analyze(headers: list[GGUFHeader]) -> ModelShape:
    h0 = headers[0]
    tensors = [t for h in headers for t in h.tensors]
    n_blocks = int(h0.get("block_count", 0))
    n_mtp = int(h0.get("nextn_predict_layers", 0) or 0)
    n_layers = n_blocks - n_mtp
    mtp_ids = set(range(n_layers, n_blocks))

    kv_heads = h0.get("attention.head_count_kv", 0)
    if isinstance(kv_heads, list):  # per-layer list in some hybrids: 0 = no attention in that layer
        kv_heads = max(kv_heads) if kv_heads else 0
    heads = int(h0.get("attention.head_count", 0) or 0)
    emb = int(h0.get("embedding_length", 0) or 0)
    k_len = int(h0.get("attention.key_length", 0) or (emb // heads if heads else 0))
    v_len = int(h0.get("attention.value_length", 0) or k_len)
    mla = int(h0.get("attention.kv_lora_rank", 0) or 0)
    mla_dim = (mla + int(h0.get("rope.dimension_count", 0) or 0)) if mla else 0

    s = ModelShape(arch=h0.arch, n_layers=n_layers, n_mtp_layers=n_mtp, attn_layers=0, kv_heads=int(kv_heads or 0),
                   k_len=k_len, v_len=v_len, mla_kv_dim=mla_dim,
                   n_expert=int(h0.get("expert_count", 0) or 0), n_expert_used=int(h0.get("expert_used_count", 0) or 0),
                   context_length=int(h0.get("context_length", 0) or 0),
                   sampling={k.split(".", 2)[2]: v for k, v in h0.kv.items() if k.startswith("general.sampling.")})
    layer_names: dict[int, set] = {}
    slow_time = 0.0  # bytes weighted by 1/efficiency of their quant type
    for t in tensors:
        s.total_bytes += t.nbytes
        s.total_params += t.n_elements
        m = _BLK.match(t.name)
        layer = int(m.group(1)) if m else None
        if layer is not None and layer in mtp_ids:
            s.mtp_bytes += t.nbytes
            continue
        if t.name == "token_embd.weight":
            s.embed_bytes += t.nbytes
            continue
        if t.name.startswith("per_layer_token_embd"):
            s.sparse_bytes += t.nbytes
            continue
        if layer is not None:
            layer_names.setdefault(layer, set()).add(t.name.split(".", 2)[2])
        if "_exps" in t.name:
            s.expert_bytes += t.nbytes
            s.expert_bytes_by_layer[layer] = s.expert_bytes_by_layer.get(layer, 0) + t.nbytes
            slow_time += t.nbytes / _cpu_eff(t.ggml_type)
            continue
        s.nonexpert_bytes += t.nbytes
    names = {t.name for t in tensors}
    if "output.weight" not in names and "token_embd.weight" in names:   # tied: the output projection is the embedding table
        s.output_tied = sum(t.nbytes for t in tensors if t.name == "token_embd.weight")
    if s.expert_bytes:  # mixed-type quants (e.g. unsloth UD): effective speed of the whole expert mix
        s.expert_cpu_eff = s.expert_bytes / slow_time
    # Which layers keep a per-token KV cache? Hybrids mix full attention with linear attention / SSM layers,
    # and the linear ones carry ssm_* tensors (and may still have attn_qkv / attn_k) but only a fixed-size state.
    kvh = h0.get("attention.head_count_kv", 0)
    if isinstance(kvh, list):
        s.attn_layers = sum(1 for i, v in enumerate(kvh) if v and i < n_layers)
    else:
        def has_kv(names: set) -> bool:
            if any(n.startswith("ssm_") for n in names):
                return False
            return any(n.startswith(("attn_k.", "attn_k_b", "attn_kv_a", "attn_qkv.", "attn_q.")) for n in names)
        s.attn_layers = sum(1 for i, names in layer_names.items() if i < n_layers and has_kv(names)) or n_layers
    # per-layer KV: heads may differ per layer (0 = no attention there), windowed layers may use shorter keys (Gemma 4)
    win = int(h0.get("attention.sliding_window", 0) or 0)
    heads = kvh if isinstance(kvh, list) else [int(kvh or 0)] * n_layers
    has_attn = [bool(heads[i]) if isinstance(kvh, list) else True for i in range(n_layers)]
    if not isinstance(kvh, list):   # scalar head count: attention layers were found from the tensors above
        att = [i for i, names in sorted(layer_names.items()) if i < n_layers]
        has_attn = [False] * n_layers
        for i in att[: s.attn_layers] if s.attn_layers < n_layers else range(n_layers):
            has_attn[i] = True
    pat_kv = h0.get("attention.sliding_window_pattern")
    if win and isinstance(pat_kv, list):
        swa = [bool(pat_kv[i]) if i < len(pat_kv) else False for i in range(n_layers)]
    elif win and SWA_PATTERN.get(h0.arch):
        p = SWA_PATTERN[h0.arch]
        swa = [(i + 1) % p != 0 for i in range(n_layers)]
    else:
        swa = [False] * n_layers
    ks = int(h0.get("attention.key_length_swa", 0) or k_len)
    vs = int(h0.get("attention.value_length_swa", 0) or v_len)
    if not mla_dim:
        s.kv_full_dim = sum(int(heads[i] or 0) * (k_len + v_len) for i in range(n_layers) if has_attn[i] and not swa[i])
        s.kv_swa_dim = sum(int(heads[i] or 0) * (ks + vs) for i in range(n_layers) if has_attn[i] and swa[i])
    qh = h0.get("attention.head_count", 0)
    qh = qh if isinstance(qh, list) else [int(qh or 0)] * n_layers
    s.q_full_dim = sum(int(qh[i] or 0) * k_len for i in range(min(n_layers, len(qh))) if has_attn[i] and not swa[i])
    s.q_swa_dim = sum(int(qh[i] or 0) * ks for i in range(min(n_layers, len(qh))) if has_attn[i] and swa[i])
    s.k_swa_len = ks if ks != k_len else 0
    if win and any(swa[i] and has_attn[i] for i in range(n_layers)):
        s.swa_window, s.swa_layers = win, sum(1 for i in range(n_layers) if swa[i] and has_attn[i])
    embed_params = sum(t.n_elements for t in tensors if t.name == "token_embd.weight" or t.name.startswith("per_layer_token_embd"))
    mtp_params = sum(t.n_elements for t in tensors if _in(t.name, mtp_ids))
    base = s.total_params - embed_params - mtp_params
    if s.is_moe:
        frac = s.n_expert_used / s.n_expert
        exp_params = sum(t.n_elements for t in tensors if "_exps" in t.name and not _in(t.name, mtp_ids))
        s.active_params = base - exp_params + int(exp_params * frac)
    else:
        s.active_params = base
    inner = int(h0.get("ssm.inner_size", 0) or 0)
    if inner:  # rough recurrent state (conv + ssm) per sequence, f32
        state = int(h0.get("ssm.state_size", 0) or 0)
        conv = int(h0.get("ssm.conv_kernel", 0) or 0)
        rec_layers = n_layers - s.attn_layers
        s.recurrent_state_bytes = rec_layers * (inner * state + inner * conv) * 4
    return s


def _in(name: str, ids: set) -> bool:
    m = _BLK.match(name)
    return bool(m) and int(m.group(1)) in ids


@dataclass
class HostSpec:
    vram_mib: int
    ram_mib: int
    ram_bw_gbs: float                 # measured sustained read bandwidth
    vram_bw_gbs: float
    ram_headroom_mib: int = 4096
    backend: str = "cuda"             # "metal": Apple unified memory; "vulkan": AMD (llmbox installs the Vulkan build there)
    gpu_eff: float = 1.0              # the card's generation (gpu_generation)


@dataclass
class Plan:
    ctx: int
    slots: int
    kv_type: str
    fits: bool
    reason: str
    gpu_expert_frac: float
    vram_used_mib: float
    ram_used_mib: float
    decode_tps: float                 # predicted at shallow depth, speculative decoding off
    decode_tps_at_depth: float
    depth: int
    parts: tuple = (0.0, 0.0, 0.0)    # seconds a token at `depth`: experts read from RAM, read on the card, fixed per layer


def plan(s: ModelShape, hw: HostSpec, ctx: int | None = None, slots: int = 1, kv_type: str = "q8_0",
         ubatch: int = 2048, depth: int = 50_000, cpu_k: float = 1.0, gpu_k: float = 1.0) -> Plan:
    """cpu_k / gpu_k scale the time of what is read from RAM / on the card (a recipe's calibration, fit.solve)."""
    ctx = ctx or s.context_length or 32768
    mib = 1 / 2**20
    kv = s.kv_bytes_per_token(kv_type) * ctx + s.kv_swa_bytes(kv_type, ctx) + s.recurrent_state_bytes * slots
    buf = COMPUTE_BUFFER_MIB.get(ubatch, 2100)
    gpu_fixed = (s.nonexpert_bytes + s.output_tied + kv) * mib + buf + GPU_RESERVE_MIB
    free_for_experts = hw.vram_mib - gpu_fixed
    if not s.is_moe:
        # dense: everything must live on the GPU (we don't run dense models split across RAM)
        need = gpu_fixed + s.embed_bytes * mib
        fits = need <= hw.vram_mib
        gpu_frac = 1.0
        ram_used = s.embed_bytes * mib
        reason = "fits in VRAM" if fits else f"dense model needs {need/1024:.1f} GiB VRAM"
        per_token_gpu = s.nonexpert_bytes + s.output_tied
        per_token_cpu = 0.0
    else:
        gpu_frac = max(0.0, min(1.0, free_for_experts / (s.expert_bytes * mib))) if s.expert_bytes else 1.0
        cpu_expert = s.expert_bytes * (1 - gpu_frac)
        ram_used = (cpu_expert + s.embed_bytes) * mib
        fits = free_for_experts > -1 and ram_used + hw.ram_headroom_mib <= hw.ram_mib
        reason = ("fits" if fits else
                  "VRAM too small for non-expert weights + KV" if free_for_experts <= -1 else
                  f"needs {(ram_used + hw.ram_headroom_mib)/1024:.1f} GiB RAM")
        frac = s.n_expert_used / s.n_expert
        per_token_cpu = cpu_expert * frac
        per_token_gpu = s.nonexpert_bytes + s.output_tied + s.expert_bytes * gpu_frac * frac
    vram_used = min(hw.vram_mib, gpu_fixed + (s.expert_bytes * gpu_frac * mib if s.is_moe else s.embed_bytes * mib))

    eff, ovh = DECODE[(hw.backend, s.is_moe)]

    def parts(d: int) -> tuple:
        kv_read = s.kv_bytes_per_token(kv_type) * d + s.kv_swa_bytes(kv_type, d)
        return (cpu_k * per_token_cpu / (hw.ram_bw_gbs * 1e9 * RAM_EFFICIENCY * s.expert_cpu_eff),
                gpu_k * (per_token_gpu + kv_read) / (hw.vram_bw_gbs * 1e9 * eff * hw.gpu_eff) + metal_attention_s(s, hw, d),
                s.n_layers * ovh / 1000)

    def tps(d: int) -> float:
        return 1 / sum(parts(d))

    return Plan(ctx=ctx, slots=slots, kv_type=kv_type, fits=fits, reason=reason, gpu_expert_frac=gpu_frac,
                vram_used_mib=vram_used, ram_used_mib=ram_used, decode_tps=tps(0),
                decode_tps_at_depth=tps(min(depth, ctx)), depth=min(depth, ctx), parts=parts(min(depth, ctx)))


def metal_attention_ps(head_width: int) -> float:
    """METAL_ATTN_PS at a head width: geometric between the measured widths, the nearest measured one outside them."""
    t = METAL_ATTN_PS
    if head_width <= t[0][0]:
        return t[0][1]
    for (w0, p0), (w1, p1) in zip(t, t[1:]):
        if head_width <= w1:
            return p0 * (p1 / p0) ** ((head_width - w0) / (w1 - w0))
    return t[-1][1]


def metal_attention_s(s: ModelShape, hw: HostSpec, d: int) -> float:
    """Seconds a token spends in attention `d` tokens into the context on a Mac, beyond reading the KV cache."""
    if hw.backend != "metal" or d <= 0:
        return 0.0
    qf, qs = s.q_dims()
    chip = METAL_ATTN_REF_GBS / (hw.vram_bw_gbs * hw.gpu_eff) * 1e-12
    return chip * (metal_attention_ps(s.k_len) * qf * d + metal_attention_ps(s.k_swa_len or s.k_len) * qs * min(d, s.swa_window or d))


def max_context(s: ModelShape, hw: HostSpec, kv_type: str = "q8_0", ubatch: int = 2048, floor_frac: float = 0.95) -> int:
    """Largest power-of-two-ish context (up to native) whose shallow decode stays within floor_frac of the 32k plan."""
    base = plan(s, hw, ctx=min(32768, s.context_length or 32768), kv_type=kv_type, ubatch=ubatch)
    best = base.ctx
    ctx = best
    while ctx < (s.context_length or 32768):
        ctx = min(ctx * 2, s.context_length or ctx * 2)
        p = plan(s, hw, ctx=ctx, kv_type=kv_type, ubatch=ubatch)
        if not p.fits or p.decode_tps < base.decode_tps * floor_frac:
            break
        best = ctx
    return best
