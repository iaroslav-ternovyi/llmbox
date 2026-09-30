"""Hardware classes: which machines count as "the same as yours" when speeds from different people are compared.

A class is the graphics card (vendor chip + memory, how many), the speed of system RAM in buckets (a MoE model's
experts are read from RAM, so two RTX 3090 boxes with DDR4 and DDR5 are not the same machine for it) and the backend.
Names are normalised from day one: LocalScore keyed on the raw strings and one model's page ended up with 826
separate "accelerators" (docs/roadmap.md §4). The raw string is kept next to the key for display and for audits.

  key("NVIDIA GeForce RTX 5070", 12227, ram_gbs=59)  ->  "rtx-5070-12g|ram-45-65|cuda"
  label(...)                                           ->  "RTX 5070 12 GB · RAM 45–65 GB/s"
"""
from __future__ import annotations

import re

RAM_EDGES = (30, 45, 65, 85, 120)   # GB/s read: DDR4 dual channel ~30-45, DDR5 ~45-85, fast DDR5 / quad channel beyond

_DROP = re.compile(r"\b(nvidia|geforce|amd|ati|radeon(?= (rx|pro|vii|r9|hd))|intel\(r\)|intel|corporation|graphics|gpu|"
                   r"series|\(tm\)|\(r\))\b", re.I)


def gpu_chip(name: str) -> str:
    """'NVIDIA GeForce RTX 4090 Laptop GPU' -> 'rtx-4090-laptop'; 'AMD Radeon RX 7900 XTX' -> 'rx-7900-xtx'."""
    s = (name or "").replace("™", "").replace("®", "")
    b = re.search(r"\[([^\]]*(geforce|radeon|rtx|gtx|rx |arc|quadro|tesla)[^\]]*)\]", s, re.I)
    s = b.group(1) if b else s                           # lspci: "GB205 [GeForce RTX 5070]" names the card in brackets
    s = re.sub(r"\[.*?\]|\(.*?\)", " ", s)
    s = _DROP.sub(" ", s)
    s = re.sub(r"\b(\d+)\s*gb\b", " ", s, flags=re.I)   # the memory size is its own part of the key
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    s = re.sub(r"-(max-q|design)$", "", s)
    return s or "unknown"


def vram_gb(vram_mib: float) -> int:
    """Marketed size: 12227 MiB usable -> 12, 24564 -> 24, 11264 -> 11, 8188 -> 8."""
    return int(round((vram_mib or 0) / 1024))


def ram_bucket(gbs: float | None) -> str:
    if not gbs:
        return "ram-unknown"
    lo = 0
    for e in RAM_EDGES:
        if gbs < e:
            return f"ram-{lo}-{e}" if lo else f"ram-under-{e}"
        lo = e
    return f"ram-{lo}-plus"


def backend(vendor: str, engine: str = "") -> str:
    v = (vendor or "").lower()
    return {"nvidia": "cuda", "amd": "rocm", "apple": "metal", "intel": "sycl"}.get(v, "cpu" if not v else v)


def key(gpu: str, vram_mib: float, ram_gbs: float | None = None, vendor: str = "nvidia", count: int = 1,
        apple_gpu_cores: int = 0) -> str:
    if vendor == "apple" or (gpu or "").lower().startswith("apple"):
        chip = gpu_chip(gpu).removeprefix("apple-")
        cores = f"-{apple_gpu_cores}c" if apple_gpu_cores else ""
        return f"apple-{chip}{cores}-{vram_gb(vram_mib)}g|metal"   # unified memory: its speed is the chip's
    g = f"{gpu_chip(gpu)}-{vram_gb(vram_mib)}g" if gpu else "cpu-only"
    return f"{count}x-{g}|{ram_bucket(ram_gbs)}|{backend(vendor if gpu else '')}" if count > 1 else f"{g}|{ram_bucket(ram_gbs)}|{backend(vendor if gpu else '')}"


def of_host(fp: dict) -> str:
    """The class of a result's host fingerprint (results.host_fingerprint)."""
    return key(fp.get("gpu") or "", (fp.get("vram_gib") or 0) * 1024, fp.get("ram_read_gbs"), fp.get("gpu_vendor") or "nvidia",
               int(fp.get("gpu_count") or 1), int(fp.get("apple_gpu_cores") or 0))


def label(k: str) -> str:
    """'rtx-5070-12g|ram-45-65|cuda' -> 'RTX 5070 12 GB · RAM 45–65 GB/s'."""
    parts = k.split("|")
    g = parts[0]
    m = re.match(r"(?:(\d+)x-)?(.+)-(\d+)g$", g)
    if g == "cpu-only" or not m:
        head = "no graphics card" if g == "cpu-only" else g
    else:
        n, chip, gb = m.groups()
        words = [w.upper() if re.search(r"\d", w) or len(w) <= 3 else w.capitalize() for w in chip.split("-")]
        head = f"{n + ' × ' if n else ''}{' '.join(words)} {gb} GB"
        a = re.match(r"apple-(m\d+)(?:-(pro|max|ultra))?(?:-(\d+)c)?$", chip)
        if a:   # unified memory: the chip, its GPU cores, the memory
            head = f"Apple {a.group(1).upper()}{' ' + a.group(2).capitalize() if a.group(2) else ''}" \
                   f"{f' · {a.group(3)}-core GPU' if a.group(3) else ''} · {gb} GB"
    ram = next((p for p in parts[1:] if p.startswith("ram-")), "")
    if ram and ram != "ram-unknown":
        r = ram[4:].replace("under-", "< ").replace("-plus", "+").replace("-", "–")
        head += f" · RAM {r} GB/s"
    return head
