"""`llmbox doctor`: is this computer ready, and if not, the one command that fixes each problem (clig.dev: errors
that say what to do next)."""
from __future__ import annotations

import os
import shutil
import sys
import urllib.request

from . import hosts, registry, submit


def checks(host: str | None = None) -> list[tuple[bool | None, str, str]]:
    """(ok / not ok / just so you know, what, the fix)."""
    out: list = []
    v = sys.version_info
    out.append((v >= (3, 12), f"Python {v.major}.{v.minor}", "llmbox needs 3.12+: sudo apt install python3.12 python3.12-venv, or brew install python@3.12"))
    names = hosts.names()
    name = host or next((n for n in names if not hosts.load(n).get("ssh")), None) or (names[0] if len(names) == 1 else None)
    if not name:   # a first run: nothing wrong, the guided start looks at the computer first
        out.append((None, "this computer has not been looked at yet", "llmbox   (the guided start: detects it, picks a model, installs it)"))
    else:
        out += _machine(name)
    return out + _reach()


def _machine(name: str) -> list:
    out: list = []
    prof = hosts.load(name)
    hw = prof["hw"]
    g = (hw.get("gpus") or [{}])[0]
    drv = ("WSL uses the Windows driver: install or update it in Windows from nvidia.com (580 or newer), then `wsl --shutdown`"
           if hw.get("wsl") else None)
    if not hw.get("gpus"):
        out.append((None, "no graphics card found: models run on the CPU (slow)", drv or "an NVIDIA card needs its driver: sudo ubuntu-drivers install, then reboot"))
    elif g.get("vendor") == "nvidia":
        cuda = hw.get("cuda_driver")
        if not cuda:   # a profile from before llmbox read it
            out.append((None, f"{g['name']}, driver {g.get('driver', '?')}", f"llmbox host add {name}   (re-reads the driver's CUDA version)"))
        else:
            ok = tuple(int(x) for x in cuda.split(".")[:2]) >= (12, 8)
            out.append((ok, f"{g['name']}, driver {g.get('driver', '?')} (CUDA {cuda})",
                        drv or "a driver with CUDA 12.8+ (570 or newer): sudo ubuntu-drivers install, then reboot"))
    elif g.get("vendor") == "apple":
        out.append((None, f"{g['name']} with {hw['ram_mib'] // 1024} GB: llmbox picks for Macs; running models on a Mac is coming", "-"))
    rts = hw.get("runtimes") or []
    out.append((bool(rts), f"llama.cpp: {rts[-1]['path']}" if rts else "llama.cpp: not found",
                "llmbox start installs the official build for this computer (or: build it, then llmbox host add " + name + ")"))
    bw = (prof.get("ram_bw") or {}).get("ram_read_gbs")
    out.append((bool(bw), f"memory speed: {bw} GB/s ({(prof.get('ram_bw') or {}).get('source')})" if bw else "memory speed: unknown",
                f"llmbox host add {name}   (measures it; the machine should be idle)"))
    win = (hw.get("wsl") or {}).get("windows_ram_mib")
    if win and hw.get("ram_mib", 0) < 0.85 * win:   # WSL2 gives Linux half the PC's memory unless told otherwise
        give = max(hw["ram_mib"] // 1024, win // 1024 - 8)
        out.append((None, f"WSL2 gives Linux {hw['ram_mib'] / 1024:.0f} of this PC's {win / 1024:.0f} GB: bigger models keep part of themselves in RAM",
                    f"in Windows, %UserProfile%\\.wslconfig: [wsl2] then memory={give}GB; run `wsl --shutdown`, open Ubuntu again, then `llmbox host add {name}`"))
    free = hw.get("disk_free_gib") or 0
    out.append((free >= 30, f"disk: {free:.0f} GB free", "models take 5-60 GB each: free some space in your home folder"))
    return out


def _reach() -> list:
    out: list = []
    for what, url in (("the site (model list)", registry.DEFAULT_URL.rstrip("/") + "/recipes/index.json"), ("the results server", submit.DEFAULT_SERVER.rstrip("/") + "/api/v1/health")):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "llmbox"}), timeout=10):
                out.append((True, f"reaches {what}", ""))
        except OSError as e:
            out.append((None, f"cannot reach {what} ({str(e)[:60]})", "check the network; everything else still works offline"))
    if shutil.which("llmbox") is None and os.path.exists(os.path.expanduser("~/.local/bin/llmbox")):
        out.append((False, "the llmbox command is not on your PATH", 'export PATH="$HOME/.local/bin:$PATH"   (new terminals: the installer added it to your shell\'s startup file, unless LLMBOX_NO_MODIFY_PATH was set)'))
    return out


def run(host: str | None = None, out=print) -> int:
    bad = 0
    for ok, what, fix in checks(host):
        mark = {True: "ok ", False: "✗  ", None: "·  "}[ok]
        out(f"{mark} {what}" + (f"\n     {'fix:' if ok is False else '→'} {fix}" if ok is False or (ok is None and fix not in ("", "-")) else ""))
        bad += ok is False
    out("\nready: `llmbox` picks, installs and starts the best model for this computer" if not bad else f"\n{bad} thing{'s' if bad > 1 else ''} to fix first")
    return 1 if bad else 0
