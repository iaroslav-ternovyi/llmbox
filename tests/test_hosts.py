"""Host profiles (llmbox/hosts.py): detecting a machine again keeps what the profile had besides detection (the model
server's address, power limits); a Mac's memory speed is its chip's figure and its GPU gets the unified memory macOS
gives it; the endpoint comes from the profile, never a fixed address.
Run: python3 tests/test_hosts.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import hosts, hwclass, results  # noqa: E402

hosts.HOME = tempfile.mkdtemp()
HW = {"nv": {"hostname": "x", "os": "Linux", "python": "3.12", "cpu": {"model": "Ryzen", "threads": 8, "cores": 8}, "ram_mib": 65536,
             "gpus": [{"vendor": "nvidia", "name": "NVIDIA GeForce RTX 4090", "vram_mib": 24564}], "runtimes": [], "llama_swap": {}},
      "mac": {"hostname": "m", "os": "macOS", "python": "3.12", "cpu": {"model": "Apple M4 Max", "threads": 16, "cores": 16}, "ram_mib": 131072,
              "gpus": [{"vendor": "apple", "name": "Apple M4 Max", "vram_mib": 98304, "gpu_cores": 40, "unified": True}], "runtimes": [], "llama_swap": {}}}


class Stub:
    kind = "nv"

    def __init__(self, name, ssh=None):
        pass

    def agent(self, cmd, *a, **k):
        return HW[Stub.kind] if cmd == "hwinfo" else {"ram_read_gbs": 72.0, "threads": 8}


hosts.Host = Stub
p = hosts.detect("rig", "me@rig")
assert p["ram_bw"]["ram_read_gbs"] == 72.0 and p["vram_bw_gbs"] == 1008
p["endpoint"], p["dense_power_limit_w"] = "http://10.0.0.5:8080", 300
hosts.save("rig", p)
p2 = hosts.detect("rig", "me@rig", measure=False)
assert p2["endpoint"] == "http://10.0.0.5:8080" and p2["dense_power_limit_w"] == 300, "re-detection dropped the profile's own keys"
assert p2["ram_bw"]["ram_read_gbs"] == 72.0, "an earlier measurement is kept when none is taken now"
assert hosts.endpoint("rig") == "http://10.0.0.5:8080" and hosts.endpoint("rig", agent=True) == "http://rig:8081"

Stub.kind = "mac"
m = hosts.detect("mac", None)
assert m["ram_bw"] == {"ram_read_gbs": 546.0, "source": "Apple spec"} and m["vram_bw_gbs"] == 546.0, m["ram_bw"]
s = hosts.spec(m)
assert s.vram_mib == 98304 and s.ram_mib == 131072 - 98304 and s.ram_bw_gbs == 546.0
assert hwclass.of_host(results.host_fingerprint(m)) == "apple-m4-max-40c-128g|metal", hwclass.of_host(results.host_fingerprint(m))
assert hosts.endpoint("mac") == "http://localhost:8080"
assert hosts.apple_bw("Apple M4 Max", 32) == 410 and hosts.apple_bw("Apple M2", 10) == 100 and hosts.apple_bw("Apple M3 Max", 30) == 300
print("all passed")
