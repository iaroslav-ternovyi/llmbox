"""The speed formula on a Mac deep in the context (estimate.metal_attention_s), held to llama-bench runs on an M2 Max
(tests/fixtures/metal_depth.json): Llama-3.2-3B (head width 128, attention on every layer), Qwen3.5-9B (width 256, a
hybrid with 8 attention layers of 32), Gemma-3-12B (width 256, 40 of 48 layers windowed) and Gemma-4-E2B (512 global,
256 windowed). The time a token gains 16k-32k into the context is within 20% of the run's for all four, and the speed
there within 15% for the three whose short-chat speed the formula has right (Gemma 4's short chat is predicted
111 tok/s against 71 measured: a separate gap, its per-layer embedding tables). Before the Metal term the deep speed was 2-2.5x too fast. CUDA and
Vulkan are untouched, and a shape read before the query dims were recorded falls back to its attention layers.
Run: python3 tests/test_metal_depth.py"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from llmbox import estimate as E, pick  # noqa: E402

fx = json.load(open(os.path.join(HERE, "fixtures", "metal_depth.json")))["models"]
hw = pick.entry_spec("Mac M2 Max", 32, 60.0)
for name, m in fx.items():
    s = E.ModelShape(**m["shape"])
    base = E.plan(s, hw, ctx=65536, kv_type="q8_0", depth=0).decode_tps
    for d, tg in m["tg"].items():
        d = int(d)
        if d < 16384:
            continue
        p = E.plan(s, hw, ctx=65536, kv_type="q8_0", depth=d).decode_tps_at_depth
        gained, measured = 1 / p - 1 / base, 1 / tg - 1 / m["tg"]["0"]
        assert abs(gained / measured - 1) < 0.20, (name, d, round(gained * 1e3, 2), round(measured * 1e3, 2))
        if not name.startswith("gemma-4"):
            assert abs(p / tg - 1) < 0.15, (name, d, round(p, 1), tg)
    # without the term (a CUDA-like backend at the Mac's bandwidth) the formula hardly slows down at depth
    flat = E.plan(s, E.HostSpec(**dict(hw.__dict__, backend="cuda")), ctx=65536, kv_type="q8_0", depth=32768)
    assert flat.decode_tps_at_depth / m["tg"]["32768"] > 1.8, (name, flat.decode_tps_at_depth)
    assert E.metal_attention_s(s, E.HostSpec(**dict(hw.__dict__, backend="vulkan")), 32768) == 0
    # a chip with half the bandwidth (half the GPU cores) pays twice the attention time
    half = E.HostSpec(**dict(hw.__dict__, vram_bw_gbs=200.0))
    assert abs(E.metal_attention_s(s, half, 32768) / E.metal_attention_s(s, hw, 32768) - 2) < 1e-9
# an older cached shape: no query dims, the fallback from its attention layers (3 query heads per KV head)
q = fx["Qwen3.5-9B-UD-Q4_K_XL"]["shape"]
old = E.ModelShape(**{k: v for k, v in q.items() if k not in ("q_full_dim", "q_swa_dim", "kv_full_dim", "kv_swa_dim")})
assert old.q_dims() == (8 * 3 * 4 * 256, 0.0) and E.stale({"arch": "qwen35", "attn_layers": 8}) and not E.stale(dict(q))
assert E.metal_attention_ps(128) == 3.2 and E.metal_attention_ps(64) == 3.2 and E.metal_attention_ps(512) == 21.4 == E.metal_attention_ps(1024)
assert 3.2 < E.metal_attention_ps(192) < 20.3 < E.metal_attention_ps(384) < 21.4
print("all passed")
