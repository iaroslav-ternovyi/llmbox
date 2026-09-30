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

# VRAM (MiB) of the common size of each card, for what-if fits without a host profile (8 GB variants: pass --vram-gb)
GPU_VRAM_MIB = {
    "RTX 5090": 32607, "RTX 5080": 16303, "RTX 5070 Ti": 16303, "RTX 5070": 12227, "RTX 5060 Ti": 16311,
    "RTX 4090": 24564, "RTX 4080 SUPER": 16376, "RTX 4080": 16376, "RTX 4070 Ti SUPER": 16376, "RTX 4070 Ti": 12282,
    "RTX 4070 SUPER": 12282, "RTX 4070": 12282, "RTX 4060 Ti": 16380, "RTX 3090 Ti": 24576, "RTX 3090": 24576,
    "RTX 3080 Ti": 12288, "RTX 3060": 12288,
}


# Apple silicon memory bandwidth, GB/s (Apple's specs; M5 Pro/Max: newsroom 2026-03): the chip, then by GPU cores
APPLE_BW = {"M1": 68, "M1 Pro": 200, "M1 Max": 400, "M1 Ultra": 800, "M2": 100, "M2 Pro": 200, "M2 Max": 400, "M2 Ultra": 800,
            "M3": 100, "M3 Pro": 150, "M3 Max": {30: 300, 40: 400}, "M3 Ultra": 819, "M4": 120, "M4 Pro": 273,
            "M4 Max": {32: 410, 40: 546}, "M5": 153, "M5 Pro": 307, "M5 Max": {32: 460, 40: 614}}


def apple_bw(chip: str, gpu_cores: int = 0) -> float | None:
    """'Apple M4 Max' with 40 GPU cores -> 546."""
    v = _lookup(APPLE_BW, chip.replace("Apple ", ""))
    if isinstance(v, dict):
        return float(v.get(gpu_cores) or max(v.values()) if gpu_cores >= max(v) else min(v.values()))
    return float(v) if v else None


def _lookup(table: dict, name: str):
    for key in sorted(table, key=len, reverse=True):  # longest match first ("5070 Ti" before "5070")
        if key.lower() in name.lower():
            return table[key]
    return None


def gpu_vram(name: str) -> int | None:
    return _lookup(GPU_VRAM_MIB, name)


def gpu_bw(name: str) -> float | None:
    v = _lookup(GPU_BW, name)
    return float(v) if v else None


def endpoint(name: str | None = None, agent: bool = False) -> str:
    """Where a host serves its models (OpenAI-compatible; agent=True: the Anthropic-compatible agent proxy):
    $LLMBOX_ENDPOINT / $LLMBOX_AGENT_ENDPOINT, else the profile's "endpoint" / "agent_endpoint", else llama-swap's port on
    the host's ssh address (this machine: localhost). No name: the only registered host."""
    env = os.environ.get("LLMBOX_AGENT_ENDPOINT" if agent else "LLMBOX_ENDPOINT")
    if env:
        return env
    if not name:
        d = os.path.join(HOME, "hosts")
        names = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []
        name = names[0] if len(names) == 1 else None
    prof = load(name) if name and os.path.exists(path(name)) else {}
    if prof.get("agent_endpoint" if agent else "endpoint"):
        return prof["agent_endpoint" if agent else "endpoint"]
    port = 8081 if agent else int((((prof.get("hw") or {}).get("llama_swap") or {}).get("url") or "http://x:8080").rsplit(":", 1)[1])
    addr = (prof.get("ssh") or "localhost").split("@")[-1]
    return f"http://{addr}:{port}"


def box_ssh(name: str = "box") -> str:
    """The ssh address of the reference box, for the developer tools and tests that check tasks on real programs there:
    $LLMBOX_BOX_SSH, else the registered host's."""
    s = os.environ.get("LLMBOX_BOX_SSH") or (load(name).get("ssh") if os.path.exists(path(name)) else None)
    if not s:
        raise SystemExit(f"no ssh address for {name}: `llmbox host add {name} --ssh user@host` or set LLMBOX_BOX_SSH")
    return s


def default_host() -> str | None:
    """The machine a command means when none is named: the only registered one, else the only one that is this
    computer (no ssh) - a remote box registered beside it does not make "this computer" ambiguous."""
    ns = names()
    if len(ns) == 1:
        return ns[0]
    local = [n for n in ns if not load(n).get("ssh")]
    return local[0] if len(local) == 1 else None


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
    apple = bool(info["gpus"]) and info["gpus"][0].get("vendor") == "apple"
    if not bw and measure and not apple:   # a Mac's memory speed is the chip's own figure (below)
        m = h.agent("bandwidth", "3", timeout=600)
        if "ram_read_gbs" in m:
            bw = {"ram_read_gbs": m["ram_read_gbs"], "source": "measured", "threads": m.get("threads")}
        else:
            bw = {"ram_read_gbs": None, "source": m.get("error", "not measured")}
    old = load(name) if os.path.exists(path(name)) else {}
    gpu = info["gpus"][0] if info["gpus"] else None
    if gpu and gpu.get("vendor") == "apple" and not ram_bw:   # unified memory: its speed is the chip's (Apple's figure)
        bw = {"ram_read_gbs": apple_bw(gpu["name"], gpu.get("gpu_cores") or 0), "source": "Apple spec"}
    if (not bw or not bw.get("ram_read_gbs")) and old.get("ram_bw", {}).get("ram_read_gbs"):
        bw = old["ram_bw"]  # keep an earlier measurement rather than losing it
    prof = dict(old, **{   # what the profile had besides detection (endpoints, power limits, notes) stays
        "name": name, "ssh": ssh, "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "hw": info,
        "ram_bw": bw or {"ram_read_gbs": None, "source": "not measured"},
        "vram_bw_gbs": (bw or {}).get("ram_read_gbs") if gpu and gpu.get("vendor") == "apple" else (gpu_bw(gpu["name"]) if gpu else None)
                       or old.get("vram_bw_gbs"),
    })
    save(name, prof)
    return prof


def spec(prof: dict, ram_headroom_mib: int = 4096) -> HostSpec:
    gpus = prof["hw"]["gpus"]
    bw = (prof.get("ram_bw") or {}).get("ram_read_gbs")
    if not bw:
        raise SystemExit(f"host {prof['name']}: RAM bandwidth unknown - run `llmbox host add {prof['name']} ... --ram-bw <GB/s>` "
                         "or measure while the host is idle")
    if gpus and gpus[0].get("unified"):   # Apple: the GPU's share of unified memory is its "VRAM", the rest is RAM at the same speed
        return HostSpec(vram_mib=gpus[0]["vram_mib"], ram_mib=max(0, prof["hw"]["ram_mib"] - gpus[0]["vram_mib"]),
                        ram_bw_gbs=float(bw), vram_bw_gbs=float(prof.get("vram_bw_gbs") or bw), ram_headroom_mib=ram_headroom_mib)
    return HostSpec(vram_mib=gpus[0]["vram_mib"] if gpus else 0, ram_mib=prof["hw"]["ram_mib"],
                    ram_bw_gbs=float(bw), vram_bw_gbs=float(prof.get("vram_bw_gbs") or 500.0),
                    ram_headroom_mib=ram_headroom_mib)


def free_up(h, wait_s: int = 240) -> dict:
    """Unload llama-swap's idle model and wait until the host is really idle: no llama-server left and the 1-minute
    load average back under the busy bar (it lags a benchmark by a minute or two). Returns the last busy() report."""
    import time as _t
    h.agent("unload", timeout=120)
    t0 = _t.time()
    while True:
        b = h.agent("busy", timeout=60)
        if not b.get("busy") or _t.time() - t0 > wait_s:
            return b
        _t.sleep(10)
