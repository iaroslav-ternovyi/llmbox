"""Prediction vs measurement on the reference box: every new calibration must keep all models within ~15%.

Measured = llama-server decode tok/s at shallow depth, speculative decoding OFF, 1 slot, the context and KV type
of the production launcher (agent-bench notes, 2026-09-24/25).
Run: python3 tests/validate_estimate.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import estimate as E  # noqa: E402
from llmbox.gguf import GGUFHeader, Tensor  # noqa: E402
from llmbox.host import Host  # noqa: E402

from llmbox.hosts import box_ssh, load  # noqa: E402
M = os.environ.get("LLMBOX_MODELS_DIR") or load("box")["hw"]["models_dir_guess"].rstrip("/") + "/"
CASES = [  # label, gguf path (first split part), ctx, kv type, ubatch, measured tok/s
    ("Tiel UD-Q4_K_XL", M + "tiel-coder/Tiel-Coder-35B-A3B-MTP-UD-Q4_K_XL.gguf", 262144, "f16", 2048, 59.0),
    ("Occamy Q4_K_M", M + "occamy/occamy-1.0-Q4_K_M-mtp-graft.gguf", 262144, "q8_0", 2048, 77.0),
    ("Occamy Q6_K", M + "occamy/occamy-1.0-Q6_K-mtp-graft.gguf", 262144, "q8_0", 2048, 57.5),
    ("Nex Q4_K_M", M + "nex-n2.5-mini/nex-agi_Nex-N2.5-mini-Q4_K_M.gguf", 262144, "q8_0", 2048, 59.2),
    ("Ling IQ3_XXS", M + "ling-3.0-flash/Ling-3.0-flash-IQ3_XXS-00001-of-00002.gguf", 262144, "q8_0", 2048, 31.0),
    ("Bonsai PQ2_0 (dense)", M + "bonsai2/Ternary-Bonsai-2-27B-PQ2_0.gguf", 131072, "q4_0", 512, 67.0),
]
HW = E.HostSpec(vram_mib=12227, ram_mib=62852, ram_bw_gbs=59.0, vram_bw_gbs=672.0)
CACHE = os.path.expanduser("~/.cache/llmbox/headers")


def header(host: Host, path: str) -> GGUFHeader:
    os.makedirs(CACHE, exist_ok=True)
    cf = os.path.join(CACHE, path.strip("/").replace("/", "__") + ".json")
    if os.path.exists(cf):
        d = json.load(open(cf))
    else:
        d = host.agent("gguf-header", path)
        json.dump(d, open(cf, "w"))
    return GGUFHeader(version=d["version"], kv=d["kv"], arrays={k: tuple(v) for k, v in d["arrays"].items()},
                      tensors=[Tensor(n, tuple(s), t, o, b) for n, s, t, o, b in d["tensors"]], data_start=d["data_start"])


def main() -> int:
    host = Host("box", ssh=box_ssh())
    worst = 0.0
    for label, path, ctx, kvt, ub, meas in CASES:
        paths = [path] + ([path.replace("00001-of-00002", "00002-of-00002")] if "00001-of-" in path else [])
        s = E.analyze([header(host, p) for p in paths])
        p = E.plan(s, HW, ctx=ctx, kv_type=kvt, ubatch=ub)
        err = p.decode_tps / meas - 1
        worst = max(worst, abs(err))
        print(f"{label:21s} {s.arch:11s} {s.total_bytes/2**30:5.1f} GiB  experts {s.expert_bytes/2**30:5.1f}  "
              f"kv-layers {s.attn_layers:2d}/{s.n_layers}  kv {s.kv_bytes_per_token(kvt)/1024:5.1f} KB/tok  "
              f"active {s.active_params/1e9:4.1f}B  experts-on-GPU {p.gpu_expert_frac:4.0%}  fits {p.fits!s:5s}  "
              f"pred {p.decode_tps:5.1f}  meas {meas:5.1f}  err {err:+5.0%}")
    print(f"worst error {worst:.0%}")
    return 0 if worst <= 0.15 else 1


if __name__ == "__main__":
    sys.exit(main())
