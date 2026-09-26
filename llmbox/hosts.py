"""Host profiles: what a machine has (detected) plus what was measured on it. Stored as JSON under ~/.llmbox/hosts/."""
from __future__ import annotations

import json
import os
import time

from .estimate import HostSpec
from .host import Host

HOME = os.path.expanduser(os.environ.get("LLMBOX_HOME", "~/.llmbox"))

# Nominal memory bandwidth (GB/s) of common GPUs; decode on the GPU side scales with it.
GPU_BW = {
    "RTX 5090": 1792, "RTX 5080": 960, "RTX 5070 Ti": 896, "RTX 5070": 672, "RTX 5060 Ti": 448, "RTX 5060": 448,
    "RTX 4090": 1008, "RTX 4080 SUPER": 736, "RTX 4080": 717, "RTX 4070 Ti SUPER": 672, "RTX 4070 Ti": 504,
    "RTX 4070 SUPER": 504, "RTX 4070": 504, "RTX 4060 Ti": 288, "RTX 4060": 272,
    "RTX 3090 Ti": 1008, "RTX 3090": 936, "RTX 3080 Ti": 912, "RTX 3080": 760, "RTX 3070": 448, "RTX 3060": 360,
    "RTX PRO 6000": 1792, "RTX A6000": 768, "A100": 1935, "H100": 3350, "L40S": 864,
}


def gpu_bw(name: str) -> float | None:
    for key in sorted(GPU_BW, key=len, reverse=True):  # longest match first ("5070 Ti" before "5070")
        if key.lower() in name.lower():
            return float(GPU_BW[key])
    return None


def path(name: str) -> str:
    return os.path.join(HOME, "hosts", f"{name}.json")


def save(name: str, prof: dict) -> None:
    os.makedirs(os.path.dirname(path(name)), exist_ok=True)
    with open(path(name), "w") as f:
        json.dump(prof, f, indent=2)


def load(name: str) -> dict:
    with open(path(name)) as f:
        return json.load(f)


def names() -> list[str]:
    d = os.path.join(HOME, "hosts")
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []


def host_of(prof: dict) -> Host:
    return Host(prof["name"], ssh=prof.get("ssh"))


def detect(name: str, ssh: str | None, ram_bw: float | None = None, measure: bool = True) -> dict:
    """Detect hardware, measure RAM bandwidth if the host is idle, and store the profile."""
    h = Host(name, ssh=ssh)
    info = h.agent("hwinfo")
    bw = {"ram_read_gbs": ram_bw, "source": "manual"} if ram_bw else None
    if not bw and measure:
        m = h.agent("bandwidth", "3", timeout=600)
        if "ram_read_gbs" in m:
            bw = {"ram_read_gbs": m["ram_read_gbs"], "source": "measured", "threads": m.get("threads")}
        else:
            bw = {"ram_read_gbs": None, "source": m.get("error", "not measured")}
    old = load(name) if os.path.exists(path(name)) else {}
    if (not bw or not bw.get("ram_read_gbs")) and old.get("ram_bw", {}).get("ram_read_gbs"):
        bw = old["ram_bw"]  # keep an earlier measurement rather than losing it
    gpu = info["gpus"][0] if info["gpus"] else None
    prof = {
        "name": name, "ssh": ssh, "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "hw": info,
        "ram_bw": bw or {"ram_read_gbs": None, "source": "not measured"},
        "vram_bw_gbs": gpu_bw(gpu["name"]) if gpu else None,
    }
    save(name, prof)
    return prof


def spec(prof: dict, ram_headroom_mib: int = 4096) -> HostSpec:
    gpus = prof["hw"]["gpus"]
    bw = (prof.get("ram_bw") or {}).get("ram_read_gbs")
    if not bw:
        raise SystemExit(f"host {prof['name']}: RAM bandwidth unknown - run `llmbox host add {prof['name']} ... --ram-bw <GB/s>` "
                         "or measure while the host is idle")
    return HostSpec(vram_mib=gpus[0]["vram_mib"] if gpus else 0, ram_mib=prof["hw"]["ram_mib"],
                    ram_bw_gbs=float(bw), vram_bw_gbs=float(prof.get("vram_bw_gbs") or 500.0),
                    ram_headroom_mib=ram_headroom_mib)
