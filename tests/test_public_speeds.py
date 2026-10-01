"""The decode formula (llmbox/estimate.py) against public llama-bench runs on cards we do not have: tg128 at depth 0 with
every weight on the card, so it checks the card side and the fixed per-layer part (the reference box hides that part
behind the experts it reads from RAM). The constants in estimate.DECODE and GPU_GENERATION were fitted to these tables;
a change to the formula has to keep them. Run: python3 tests/test_public_speeds.py   (prints the error per card)

Sources (2026-10-01):
  dense: llama 7B Q4_0, https://github.com/ggml-org/llama.cpp/discussions/15013
  MoE:   gpt-oss 20B MXFP4, https://github.com/ggml-org/llama.cpp/discussions/15396
  Apple: llama 7B Q4_0 on M1-M5, https://github.com/ggml-org/llama.cpp/discussions/4167
  AMD:   llama 7B Q4_0 on Vulkan (RADV), https://github.com/ggml-org/llama.cpp/discussions/10879
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import estimate as E  # noqa: E402

BW = {"RTX 5090": 1792, "RTX 4090": 1008, "RTX 5080": 960, "RTX 5070 Ti": 896, "RTX 4080 SUPER": 736, "RTX 4080": 717,
      "RTX 3090 Ti": 1008, "RTX 3090": 936, "RTX 3080": 760, "RTX 2080 Ti": 616, "RTX 5060 Ti": 448, "GTX 1080 Ti": 484}
DENSE = {"RTX 5090": 290.0, "RTX 4090": 186.2, "RTX 5080": 182.0, "RTX 5070 Ti": 176.9, "RTX 4080 SUPER": 148.3, "RTX 4080": 142.5,
         "RTX 3090 Ti": 171.2, "RTX 3090": 158.2, "RTX 3080": 139.7, "RTX 2080 Ti": 107.5, "GTX 1080 Ti": 62.5}
MOE = {"RTX 3090": 161.8, "RTX 4090": 222.0, "RTX 4080 SUPER": 186.5, "RTX 5060 Ti": 111.5, "RTX 5070 Ti": 189.5,
       "RTX 5080": 204.9, "RTX 5090": 282.5}
# Apple: (chip, bandwidth GB/s, t/s); unified memory, so the whole model is "on the card"
APPLE = [("M1", 68, 14.15), ("M1 Pro", 200, 36.41), ("M1 Max", 400, 61.19), ("M1 Ultra", 800, 83.73), ("M2", 100, 21.91),
         ("M2 Pro", 200, 38.86), ("M2 Max", 400, 65.95), ("M2 Ultra", 800, 94.27), ("M3", 100, 21.34), ("M3 Pro", 150, 30.74),
         ("M3 Max", 300, 56.58), ("M3 Max", 400, 66.31), ("M3 Ultra", 800, 92.14), ("M4", 120, 24.11), ("M4 Pro", 273, 50.74),
         ("M4 Max", 410, 69.95), ("M4 Max", 546, 83.06), ("M5 Pro", 307, 66.33), ("M5 Max", 614, 119.92)]
# AMD on Vulkan: (card, bandwidth GB/s, t/s); the 7900 XT's posted run sits below the 7800 XT's (an old run), hence the
# looser worst case
VULKAN = [("RX 7900 XTX", 960, 182.63), ("RX 7900 XT", 800, 123.18), ("RX 7800 XT", 624, 118.27), ("RX 6900 XT", 512, 108.0),
          ("RX 6800 XT", 512, 100.32), ("RX 6700 XT", 384, 83.88), ("RX 7600", 288, 58.03), ("Ryzen AI Max+ 395", 256, 53.59)]
# the two models as the formula sees them (bytes read per token from the GGUF headers)
LLAMA = E.ModelShape(arch="llama", n_layers=32, n_mtp_layers=0, attn_layers=32, kv_heads=32, k_len=128, v_len=128,
                     nonexpert_bytes=int(3.76e9), embed_bytes=int(0.07e9), total_bytes=int(3.83e9), context_length=4096)
OSS = E.ModelShape(arch="gpt-oss", n_layers=24, n_mtp_layers=0, attn_layers=24, kv_heads=8, k_len=64, v_len=64,
                   nonexpert_bytes=int(1.07e9), expert_bytes=int(10.18e9), n_expert=32, n_expert_used=4,
                   embed_bytes=int(0.3e9), total_bytes=int(11.9e9), context_length=4096)

for name, shape, table, worst in (("dense llama 7B", LLAMA, DENSE, 0.10), ("MoE gpt-oss 20B", OSS, MOE, 0.20)):
    errs = {}
    for gpu, measured in table.items():
        hw = E.HostSpec(vram_mib=32000, ram_mib=65536, ram_bw_gbs=60, vram_bw_gbs=BW[gpu], gpu_eff=E.gpu_generation(gpu))
        p = E.plan(shape, hw, ctx=4096, kv_type="q8_0", depth=0)
        assert p.gpu_expert_frac == 1.0, (gpu, "the runs had every weight on the card")
        errs[gpu] = p.decode_tps / measured - 1
    med = statistics.median(abs(e) for e in errs.values())
    print(f"{name:16s} median |error| {med:.0%}   " + "  ".join(f"{g.replace('RTX ', '')} {e:+.0%}" for g, e in errs.items()))
    assert med <= 0.06, f"{name}: the formula drifted from the public runs (median {med:.0%})"
    assert max(abs(e) for e in errs.values()) <= worst, f"{name}: a card is off by more than {worst:.0%}: {errs}"
errs = []
for chip, bw, measured in APPLE:
    hw = E.HostSpec(vram_mib=32000, ram_mib=0, ram_bw_gbs=bw, vram_bw_gbs=bw, backend="metal", gpu_eff=E.gpu_generation("Apple " + chip))
    errs.append((chip, E.plan(LLAMA, hw, ctx=4096, kv_type="q8_0", depth=0).decode_tps / measured - 1))
med = statistics.median(abs(e) for _, e in errs)
print(f"{'Apple llama 7B':16s} median |error| {med:.0%}   " + "  ".join(f"{c} {e:+.0%}" for c, e in errs))
assert med <= 0.05 and max(abs(e) for _, e in errs) <= 0.16, errs
errs = []
for card, bw, measured in VULKAN:
    hw = E.HostSpec(vram_mib=32000, ram_mib=65536, ram_bw_gbs=60, vram_bw_gbs=bw, backend="vulkan")
    errs.append((card, E.plan(LLAMA, hw, ctx=4096, kv_type="q8_0", depth=0).decode_tps / measured - 1))
med = statistics.median(abs(e) for _, e in errs)
print(f"{'AMD llama 7B':16s} median |error| {med:.0%}   " + "  ".join(f"{c.replace('RX ', '')} {e:+.0%}" for c, e in errs))
assert med <= 0.05 and max(abs(e) for _, e in errs) <= 0.25, errs
print("all passed")
