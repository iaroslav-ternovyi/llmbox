"""Hardware classes (llmbox/hwclass.py): the same card under the names different tools print lands in one class; a
different memory size, RAM speed or card count does not. Strings as nvidia-smi, lspci, rocm-smi and macOS print them.
Run: python3 tests/test_hwclass.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import hwclass as H  # noqa: E402

same = [("NVIDIA GeForce RTX 5070", 12227), ("GeForce RTX 5070", 12282), ("NVIDIA GeForce RTX(TM) 5070", 12227),
        ("NVIDIA Corporation GB205 [GeForce RTX 5070]", 12227)]
keys = {H.key(n, v, 59) for n, v in same}
assert keys == {"rtx-5070-12g|ram-45-65|cuda"}, keys

cases = {
    ("NVIDIA GeForce RTX 4090", 24564): "rtx-4090-24g",
    ("NVIDIA GeForce RTX 4090 Laptop GPU", 16376): "rtx-4090-laptop-16g",
    ("NVIDIA GeForce RTX 3060", 12288): "rtx-3060-12g",
    ("NVIDIA GeForce RTX 3060", 8192): "rtx-3060-8g",            # the 8 GB variant is another card
    ("NVIDIA GeForce GTX 1080 Ti", 11264): "gtx-1080-ti-11g",
    ("NVIDIA RTX A6000", 49140): "rtx-a6000-48g",
    ("Tesla P40", 24576): "tesla-p40-24g",
    ("NVIDIA RTX PRO 6000 Blackwell Workstation Edition", 97887): "rtx-pro-6000-blackwell-workstation-edition-96g",
    ("AMD Radeon RX 7900 XTX", 24560): "rx-7900-xtx-24g",
    ("Radeon RX 7900 XTX", 24560): "rx-7900-xtx-24g",
}
for (n, v), want in cases.items():
    got = H.key(n, v, 59).split("|")[0]
    assert got == want, (n, got, want)

assert H.key("NVIDIA GeForce RTX 3090", 24576, 38) != H.key("NVIDIA GeForce RTX 3090", 24576, 72), "DDR4 vs DDR5 box: not the same for a MoE"
assert H.key("NVIDIA GeForce RTX 3090", 24576, 60, count=2).startswith("2x-rtx-3090-24g|")
assert H.ram_bucket(59) == "ram-45-65" and H.ram_bucket(20) == "ram-under-30" and H.ram_bucket(400) == "ram-120-plus" and H.ram_bucket(None) == "ram-unknown"
assert H.key("", 0, 80, vendor="") == "cpu-only|ram-65-85|cpu"
assert H.key("Apple M4 Max", 128 * 1024, vendor="apple", apple_gpu_cores=40) == "apple-m4-max-40c-128g|metal"
assert H.key("AMD Radeon RX 7900 XTX", 24560, 60, vendor="amd").endswith("|vulkan")   # the build llmbox installs on AMD

assert H.label("rtx-5070-12g|ram-45-65|cuda") == "RTX 5070 12 GB · RAM 45–65 GB/s", H.label("rtx-5070-12g|ram-45-65|cuda")
assert H.label("2x-rtx-3090-24g|ram-65-85|cuda") == "2 × RTX 3090 24 GB · RAM 65–85 GB/s"
assert H.label("cpu-only|ram-65-85|cpu") == "no graphics card · RAM 65–85 GB/s"
assert H.label("apple-m4-max-40c-128g|metal") == "Apple M4 Max · 40-core GPU · 128 GB", H.label("apple-m4-max-40c-128g|metal")
assert H.label("apple-m2-24g|metal") == "Apple M2 · 24 GB", H.label("apple-m2-24g|metal")
assert H.gpu_chip("Intel(R) Arc(TM) A770 Graphics") == "arc-a770"

fp = {"gpu": "NVIDIA GeForce RTX 5070", "vram_gib": 11.9, "ram_read_gbs": 59.0}
assert H.of_host(fp) == "rtx-5070-12g|ram-45-65|cuda"

# the picker entry a machine belongs to (the site's map cell, card page and feed): every card is found from what its
# own driver reports, RAM speed and backend do not matter; Macs by chip, and by GPU cores where the picker splits them
for name, mib, _bw in H.NVIDIA_CARDS + H.AMD_CARDS:
    k = H.key(name, mib, 60, vendor="amd" if H.entry_kind(name) == "amd" else "nvidia")
    assert H.display_class(k) == name, (name, k, H.display_class(k))
assert H.display_class(H.of_host(fp)) == "RTX 5070 12 GB"
assert H.display_class(H.key("NVIDIA GeForce RTX 4070 Ti SUPER", 16376, 90)) == "RTX 4070 Ti Super 16 GB"
assert H.display_class(H.key("Apple M2 Max", 32 * 1024, vendor="apple", apple_gpu_cores=38)) == "Mac M2 Max"
assert H.display_class(H.key("Apple M3 Max", 128 * 1024, vendor="apple", apple_gpu_cores=40)) == "Mac M3 Max 40-core GPU"
assert H.display_class(H.key("Apple M3 Max", 96 * 1024, vendor="apple", apple_gpu_cores=30)) == "Mac M3 Max 30-core GPU"
assert H.display_class(H.key("Apple M3 Max", 96 * 1024, vendor="apple")) is None   # split by cores, cores unknown: other
assert H.display_class(H.key("AMD Radeon 8060S Graphics", 96 * 1024, 200, vendor="amd")) == "Ryzen AI Max+ 395"
for other in (H.key("NVIDIA GeForce RTX 3090", 24576, 60, count=2), H.key("", 0, 60, vendor=""),
              H.key("NVIDIA GeForce RTX 4090 Laptop GPU", 16376, 60), H.key("Intel(R) Arc(TM) A770 Graphics", 16384, 60, vendor="intel")):
    assert H.display_class(other) is None, other   # several cards, no card, a card the picker does not list: "Other machines"
assert H.display_class("") is None and H.display_class("nonsense") is None

# 67 entries (M5 Ultra and M6 joined 2026-10), each with a fixed address; a Mac's sizes are the ones Apple sells, and a cell takes the one nearest 64 GB
names = [n for n, *_ in H.NVIDIA_CARDS + H.AMD_CARDS + H.APUS + H.MACS]
assert len(names) == 67 and set(H.SLUGS) == set(names) and len(set(H.SLUGS.values())) == 67
assert H.SLUGS["RTX 4070 Ti Super 16 GB"] == "rtx-4070-ti-super-16gb" and H.SLUGS["Ryzen AI Max+ 395"] == "ryzen-ai-max-plus-395"
assert set(H.MAC_MEMORY) == {m[0] for m in H.MACS} and all(max(H.MAC_MEMORY[m[0]]) == m[4] for m in H.MACS)
assert (H.mac_memory("Mac M3 Ultra"), H.mac_memory("Mac M3 Max 30-core GPU"), H.mac_memory("Mac M2 Max"), H.mac_memory("Mac M1"),
        H.mac_memory("Mac M4 Max 32-core GPU")) == (96, 36, 64, 16, 36)
assert H.mac_memory("Mac M3 Pro", 27) == 18   # a tie goes to the smaller
assert [H.entry_kind(n) for n in ("RTX 3060 12 GB", "RX 7600 8 GB", "Mac M1", "Ryzen AI Max 390")] == ["card", "amd", "mac", "chip"]
print("all passed")
