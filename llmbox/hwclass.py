"""Hardware classes: which machines count as "the same as yours" when speeds from different people are compared.

A class is the graphics card (vendor chip + memory, how many), the speed of system RAM in buckets (a MoE model's
experts are read from RAM, so two RTX 3090 boxes with DDR4 and DDR5 are not the same machine for it) and the backend.
Names are normalised from day one: LocalScore keyed on the raw strings and one model's page ended up with 826
separate "accelerators" (docs/roadmap.md §4). The raw string is kept next to the key for display and for audits.

  key("NVIDIA GeForce RTX 5070", 12227, ram_gbs=59)  ->  "rtx-5070-12g|ram-45-65|cuda"
  label(...)                                           ->  "RTX 5070 12 GB · RAM 45–65 GB/s"
"""
from __future__ import annotations

import functools
import re

RAM_EDGES = (30, 45, 65, 85, 120)   # GB/s read: DDR4 dual channel ~30-45, DDR5 ~45-85, fast DDR5 / quad channel beyond

_DROP = re.compile(r"\b(nvidia|geforce|amd|ati|radeon(?= (rx|pro|vii|r9|hd))|intel\(r\)|intel|corporation|graphics|gpu|"
                   r"series|\(tm\)|\(r\))\b", re.I)


def gpu_chip(name: str) -> str:
    """'NVIDIA GeForce RTX 4090 Laptop GPU' -> 'rtx-4090-laptop'; 'AMD Radeon RX 7900 XTX' -> 'rx-7900-xtx'."""
    s = (name or "")[:160].replace("™", "").replace("®", "")
    b = next((g for g in re.findall(r"\[([^\]]{1,80})\]", s) if re.search(r"geforce|radeon|rtx|gtx|rx |arc|quadro|tesla", g, re.I)), None)
    s = b or s                                           # lspci: "GB205 [GeForce RTX 5070]" names the card in brackets
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
    return {"nvidia": "cuda", "amd": "vulkan", "apple": "metal", "intel": "sycl"}.get(v, "cpu" if not v else v)   # AMD: the build llmbox installs


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


# common GPUs: VRAM (MiB) and memory bandwidth (GB/s), for the "your box" picker; by generation, then size. The 8 GB
# cards are here because mixture-of-experts models keep their experts in RAM: a 35B-A3B runs on them.
NVIDIA_CARDS = [("RTX 2080 Ti 11 GB", 11264, 616),
        ("RTX 3060 12 GB", 12288, 360), ("RTX 3060 Ti 8 GB", 8192, 448), ("RTX 3070 8 GB", 8192, 448), ("RTX 3070 Ti 8 GB", 8192, 608),
        ("RTX 3080 10 GB", 10240, 760), ("RTX 3080 12 GB", 12288, 912), ("RTX 3080 Ti 12 GB", 12288, 912), ("RTX 3090 24 GB", 24576, 936),
        ("RTX 3090 Ti 24 GB", 24576, 1008),
        ("RTX 4060 8 GB", 8188, 272), ("RTX 4060 Ti 8 GB", 8188, 288), ("RTX 4060 Ti 16 GB", 16380, 288), ("RTX 4070 12 GB", 12282, 504),
        ("RTX 4070 Super 12 GB", 12282, 504), ("RTX 4070 Ti 12 GB", 12282, 504), ("RTX 4070 Ti Super 16 GB", 16376, 672),
        ("RTX 4080 16 GB", 16376, 717), ("RTX 4080 Super 16 GB", 16376, 736), ("RTX 4090 24 GB", 24564, 1008),
        ("RTX 5060 8 GB", 8151, 448), ("RTX 5060 Ti 8 GB", 8151, 448), ("RTX 5060 Ti 16 GB", 16311, 448), ("RTX 5070 12 GB", 12227, 672),
        ("RTX 5070 Ti 16 GB", 16303, 896), ("RTX 5080 16 GB", 16303, 960), ("RTX 5090 32 GB", 32607, 1792),
        ("RTX PRO 6000 96 GB", 97887, 1792)]
# AMD cards (llmbox runs the Vulkan build of llama.cpp there): (name, VRAM MiB, GB/s)
AMD_CARDS = [("RX 6700 XT 12 GB", 12272, 384), ("RX 6800 XT 16 GB", 16368, 512), ("RX 6900 XT 16 GB", 16368, 512),
       ("RX 7600 8 GB", 8176, 288), ("RX 7600 XT 16 GB", 16368, 288), ("RX 7700 XT 12 GB", 12272, 432), ("RX 7800 XT 16 GB", 16368, 624),
       ("RX 7900 GRE 16 GB", 16368, 576), ("RX 7900 XT 20 GB", 20464, 800), ("RX 7900 XTX 24 GB", 24560, 960),
       ("RX 9060 XT 16 GB", 16304, 320), ("RX 9070 16 GB", 16304, 640), ("RX 9070 XT 16 GB", 16304, 640),
       ("Radeon AI PRO R9700 32 GB", 32624, 640)]
# AMD Ryzen AI Max (Strix Halo): unified memory like a Mac, but Linux and the Vulkan build - (name, 0, GB/s, "apu", largest GB)
APUS = [("Ryzen AI Max+ 395", 0, 256, "apu", 128), ("Ryzen AI Max 390", 0, 256, "apu", 64)]


# Apple Silicon: unified memory, so no VRAM/RAM split - (name, 0, memory bandwidth GB/s, "mac", largest memory GB).
# The sizes each chip is sold with are MAC_MEMORY below.
# Bandwidth: Apple's specs (M5 Pro 307, M5 Max 460 / 614: apple.com newsroom 2026-03). Nothing is measured on a Mac here.
MACS = [("Mac M1", 0, 68, "mac", 16), ("Mac M1 Pro", 0, 200, "mac", 32), ("Mac M1 Max", 0, 400, "mac", 64), ("Mac M1 Ultra", 0, 800, "mac", 128),
        ("Mac M2", 0, 100, "mac", 24), ("Mac M2 Pro", 0, 200, "mac", 32), ("Mac M2 Max", 0, 400, "mac", 96), ("Mac M2 Ultra", 0, 800, "mac", 192),
        ("Mac M3", 0, 100, "mac", 24), ("Mac M3 Pro", 0, 150, "mac", 36), ("Mac M3 Max 30-core GPU", 0, 300, "mac", 96),
        ("Mac M3 Max 40-core GPU", 0, 400, "mac", 128), ("Mac M3 Ultra", 0, 819, "mac", 512),
        ("Mac M4", 0, 120, "mac", 32), ("Mac M4 Pro", 0, 273, "mac", 64), ("Mac M4 Max 32-core GPU", 0, 410, "mac", 36),
        ("Mac M4 Max 40-core GPU", 0, 546, "mac", 128), ("Mac M5", 0, 153, "mac", 32), ("Mac M5 Pro", 0, 307, "mac", 64),
        ("Mac M5 Max 32-core GPU", 0, 460, "mac", 36), ("Mac M5 Max 40-core GPU", 0, 614, "mac", 128)]

# the unified memory each chip is sold with (GB), from Apple's tech specs (M5 Pro/Max: support.apple.com/126318); the
# picker offers only these for a Mac, and a map cell assumes the one nearest 64 GB (mac_memory)
MAC_MEMORY = {"Mac M1": (8, 16), "Mac M1 Pro": (16, 32), "Mac M1 Max": (32, 64), "Mac M1 Ultra": (64, 128),
              "Mac M2": (8, 16, 24), "Mac M2 Pro": (16, 32), "Mac M2 Max": (32, 64, 96), "Mac M2 Ultra": (64, 128, 192),
              "Mac M3": (8, 16, 24), "Mac M3 Pro": (18, 36), "Mac M3 Max 30-core GPU": (36, 96), "Mac M3 Max 40-core GPU": (48, 64, 128),
              "Mac M3 Ultra": (96, 256, 512), "Mac M4": (16, 24, 32), "Mac M4 Pro": (24, 48, 64), "Mac M4 Max 32-core GPU": (36,),
              "Mac M4 Max 40-core GPU": (48, 64, 128), "Mac M5": (16, 24, 32), "Mac M5 Pro": (24, 48, 64),
              "Mac M5 Max 32-core GPU": (36,), "Mac M5 Max 40-core GPU": (48, 64, 128)}

# each picker entry's address on the site (hw-<slug>.html), fixed: a renamed entry keeps its slug, so posted links and
# feed subscriptions keep working; a new entry adds one here
SLUGS = {"RTX 2080 Ti 11 GB": "rtx-2080-ti-11gb", "RTX 3060 12 GB": "rtx-3060-12gb", "RTX 3060 Ti 8 GB": "rtx-3060-ti-8gb",
         "RTX 3070 8 GB": "rtx-3070-8gb", "RTX 3070 Ti 8 GB": "rtx-3070-ti-8gb", "RTX 3080 10 GB": "rtx-3080-10gb",
         "RTX 3080 12 GB": "rtx-3080-12gb", "RTX 3080 Ti 12 GB": "rtx-3080-ti-12gb", "RTX 3090 24 GB": "rtx-3090-24gb",
         "RTX 3090 Ti 24 GB": "rtx-3090-ti-24gb", "RTX 4060 8 GB": "rtx-4060-8gb", "RTX 4060 Ti 8 GB": "rtx-4060-ti-8gb",
         "RTX 4060 Ti 16 GB": "rtx-4060-ti-16gb", "RTX 4070 12 GB": "rtx-4070-12gb", "RTX 4070 Super 12 GB": "rtx-4070-super-12gb",
         "RTX 4070 Ti 12 GB": "rtx-4070-ti-12gb", "RTX 4070 Ti Super 16 GB": "rtx-4070-ti-super-16gb", "RTX 4080 16 GB": "rtx-4080-16gb",
         "RTX 4080 Super 16 GB": "rtx-4080-super-16gb", "RTX 4090 24 GB": "rtx-4090-24gb", "RTX 5060 8 GB": "rtx-5060-8gb",
         "RTX 5060 Ti 8 GB": "rtx-5060-ti-8gb", "RTX 5060 Ti 16 GB": "rtx-5060-ti-16gb", "RTX 5070 12 GB": "rtx-5070-12gb",
         "RTX 5070 Ti 16 GB": "rtx-5070-ti-16gb", "RTX 5080 16 GB": "rtx-5080-16gb", "RTX 5090 32 GB": "rtx-5090-32gb",
         "RTX PRO 6000 96 GB": "rtx-pro-6000-96gb", "RX 6700 XT 12 GB": "rx-6700-xt-12gb", "RX 6800 XT 16 GB": "rx-6800-xt-16gb",
         "RX 6900 XT 16 GB": "rx-6900-xt-16gb", "RX 7600 8 GB": "rx-7600-8gb", "RX 7600 XT 16 GB": "rx-7600-xt-16gb",
         "RX 7700 XT 12 GB": "rx-7700-xt-12gb", "RX 7800 XT 16 GB": "rx-7800-xt-16gb", "RX 7900 GRE 16 GB": "rx-7900-gre-16gb",
         "RX 7900 XT 20 GB": "rx-7900-xt-20gb", "RX 7900 XTX 24 GB": "rx-7900-xtx-24gb", "RX 9060 XT 16 GB": "rx-9060-xt-16gb",
         "RX 9070 16 GB": "rx-9070-16gb", "RX 9070 XT 16 GB": "rx-9070-xt-16gb", "Radeon AI PRO R9700 32 GB": "radeon-ai-pro-r9700-32gb",
         "Mac M1": "mac-m1", "Mac M1 Pro": "mac-m1-pro", "Mac M1 Max": "mac-m1-max", "Mac M1 Ultra": "mac-m1-ultra", "Mac M2": "mac-m2",
         "Mac M2 Pro": "mac-m2-pro", "Mac M2 Max": "mac-m2-max", "Mac M2 Ultra": "mac-m2-ultra", "Mac M3": "mac-m3",
         "Mac M3 Pro": "mac-m3-pro", "Mac M3 Max 30-core GPU": "mac-m3-max-30-core", "Mac M3 Max 40-core GPU": "mac-m3-max-40-core",
         "Mac M3 Ultra": "mac-m3-ultra", "Mac M4": "mac-m4", "Mac M4 Pro": "mac-m4-pro", "Mac M4 Max 32-core GPU": "mac-m4-max-32-core",
         "Mac M4 Max 40-core GPU": "mac-m4-max-40-core", "Mac M5": "mac-m5", "Mac M5 Pro": "mac-m5-pro",
         "Mac M5 Max 32-core GPU": "mac-m5-max-32-core", "Mac M5 Max 40-core GPU": "mac-m5-max-40-core",
         "Ryzen AI Max+ 395": "ryzen-ai-max-plus-395", "Ryzen AI Max 390": "ryzen-ai-max-390"}


def entry_kind(name: str) -> str:
    """A picker entry's kind: "card" (NVIDIA), "amd" (AMD card), "mac" or "chip" (Ryzen AI Max)."""
    return "mac" if name.startswith("Mac ") else "chip" if name.startswith("Ryzen AI Max") else \
        "amd" if any(name == a[0] for a in AMD_CARDS) else "card"


def first_badge(name: str, after_reference: bool = False) -> str:
    """The "first" badge for a picker entry (the result card, the CLI's sign-in offer): per kind of machine, and FIRST
    USER ON THIS CARD where llmbox's own reference PC measured the entry before anyone."""
    if after_reference:
        return "FIRST USER ON THIS CARD"
    return {"mac": "FIRST ON THIS MAC", "chip": "FIRST ON THIS CHIP"}.get(entry_kind(name), "FIRST ON THIS CARD")


def mac_memory(name: str, want: int = 64) -> int:
    """The memory (GB) a Mac entry is taken to have: want if the chip is sold with it, else the nearest size it is
    sold with (a tie goes to the smaller)."""
    return min(MAC_MEMORY[name], key=lambda gb: (abs(gb - want), gb))


@functools.cache
def _entries_by_key() -> dict:
    """The picker entry of each discrete card's class prefix ("rtx-5070-12g", "rx-7900-xtx-24g") and of each Mac chip
    ("m3-max": [(cores, name), ...])."""
    cards = {key(n, v, vendor="amd" if any(n == a[0] for a in AMD_CARDS) else "nvidia").split("|")[0]: n
             for n, v, _b in NVIDIA_CARDS + AMD_CARDS}
    macs: dict = {}
    for m in MACS:
        a = re.match(r"Mac (M\d+)(?: (Pro|Max|Ultra))?(?: (\d+)-core GPU)?$", m[0])
        chip = a.group(1).lower() + (f"-{a.group(2).lower()}" if a.group(2) else "")
        macs.setdefault(chip, []).append((int(a.group(3)) if a.group(3) else 0, m[0]))
    return {"cards": cards, "macs": macs}


def display_class(k: str) -> str | None:
    """The picker entry a hardware class belongs to - the site's map cell, card page and feed, and what the CLI tells
    someone about their machine: 'rtx-5070-12g|ram-45-65|cuda' -> 'RTX 5070 12 GB', 'apple-m3-max-40c-128g|metal' ->
    'Mac M3 Max 40-core GPU'. None for a machine outside the picker: several cards, no graphics card, a card it does
    not list, a Mac whose GPU core count is needed and unknown. Those are the site's "Other machines"."""
    g = (k or "").split("|")[0]
    idx = _entries_by_key()
    if g in idx["cards"]:
        return idx["cards"][g]
    a = re.match(r"apple-(m\d+(?:-(?:pro|max|ultra))?)(?:-(\d+)c)?-\d+g$", g)
    if a:
        options = idx["macs"].get(a.group(1)) or []
        if len(options) == 1 and not options[0][0]:
            return options[0][1]
        return next((n for cores, n in options if a.group(2) and cores == int(a.group(2))), None)
    if re.match(r"(radeon-)?8060s-", g):   # Ryzen AI Max+ 395's GPU (Radeon 8060S); the 390 has the 8050S
        return "Ryzen AI Max+ 395"
    if re.match(r"(radeon-)?8050s-", g):
        return "Ryzen AI Max 390"
    return None
