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
assert H.key("AMD Radeon RX 7900 XTX", 24560, 60, vendor="amd").endswith("|rocm")

assert H.label("rtx-5070-12g|ram-45-65|cuda") == "RTX 5070 12 GB · RAM 45–65 GB/s", H.label("rtx-5070-12g|ram-45-65|cuda")
assert H.label("2x-rtx-3090-24g|ram-65-85|cuda") == "2 × RTX 3090 24 GB · RAM 65–85 GB/s"
assert H.label("cpu-only|ram-65-85|cpu") == "no graphics card · RAM 65–85 GB/s"
assert H.label("apple-m4-max-40c-128g|metal") == "Apple M4 Max · 40-core GPU · 128 GB", H.label("apple-m4-max-40c-128g|metal")
assert H.label("apple-m2-24g|metal") == "Apple M2 · 24 GB", H.label("apple-m2-24g|metal")
assert H.gpu_chip("Intel(R) Arc(TM) A770 Graphics") == "arc-a770"

fp = {"gpu": "NVIDIA GeForce RTX 5070", "vram_gib": 11.9, "ram_read_gbs": 59.0}
assert H.of_host(fp) == "rtx-5070-12g|ram-45-65|cuda"
print("all passed")
