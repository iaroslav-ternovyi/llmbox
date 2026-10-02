"""Which official llama.cpp build a machine gets (llmbox/engine.py), without the network: an NVIDIA card the CUDA build its
driver can run plus that build's CUDA runtime, a Mac Metal, AMD Vulkan, no card the CPU build; an asset needs GitHub's
sha256 digest to be taken.
Run: python3 tests/test_engine.py"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import engine as E  # noqa: E402


def prof(vendor="nvidia", cuda=None, os_="Linux-6.8-x86_64"):
    g = [{"vendor": vendor, "name": "x", "vram_mib": 12000}] if vendor else []
    return {"hw": {"gpus": g, "cuda_driver": cuda, "os": os_}}


pat = lambda p: E.asset_pattern(p)[0]
assert "cuda-13\\.4-x64" in pat(prof(cuda="13.4")) and "cuda-12\\.8-x64" in pat(prof(cuda="13.3"))
assert "vulkan-x64" in pat(prof(cuda="12.2")) and "580" in E.asset_pattern(prof(cuda="12.2"))[1], "an old driver: Vulkan, and how to get CUDA"
assert "macos-arm64" in pat(prof("apple", os_="macOS-15-arm64-arm-64bit"))
assert "macos-arm64" in pat(prof("apple", os_="macOS-27.0-x86_64-i386-64bit"))   # an x86 Python under Rosetta on Apple silicon
assert "vulkan" in pat(prof("amd")) and pat(prof(None)).endswith("ubuntu-x64\\.tar\\.gz$")

REL = [{"tag_name": "b200", "assets": [
    {"name": "llama-b200-bin-ubuntu-cuda-12.8-x64.tar.gz", "browser_download_url": "u1", "digest": "sha256:aa", "size": 1},
    {"name": "cudart-llama-b200-bin-ubuntu-cuda-12.8-x64.tar.gz", "browser_download_url": "u2", "digest": "sha256:bb", "size": 2},
    {"name": "llama-b200-bin-ubuntu-vulkan-x64.tar.gz", "browser_download_url": "u3", "size": 3}]}]   # no digest: not taken


class Resp:
    def __init__(self, d):
        self.d = d

    def read(self):
        return json.dumps(self.d).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


E.urllib.request.urlopen = lambda req, timeout=60: Resp(REL)
a = E.latest(pat(prof(cuda="13.3")))
assert a["name"] == "llama-b200-bin-ubuntu-cuda-12.8-x64.tar.gz" and a["sha256"] == "aa"
try:
    E.latest(pat(prof(cuda="12.2")))
    raise AssertionError("an asset without a published sha256 was taken")
except SystemExit:
    pass


class Host:   # records the install command instead of running it
    is_local, cmds = False, []

    def run(self, cmd, timeout=0):
        Host.cmds.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "/home/u/.llmbox/engines/b200/llama-server" if cmd.startswith("echo") else "version: 1", "stderr": ""})()


E.install(Host(), prof(cuda="13.3"), out=lambda m: None)
c = Host.cmds[0]
assert c.index("llama-b200-bin-ubuntu-cuda") < c.index("cudart-llama-b200") and c.count("sha256sum") >= 1 and '"aa  ' in c and '"bb  ' in c, c

# a recipe's llama-server that is gone: another build the host has, else llmbox's own installed again; the recipe file
# is pointed at it; none at all is a clear stop, not a traceback from the agent
import tempfile, types  # noqa: E401,E402
from llmbox import hosts, install as inst, recipe as rc  # noqa: E402
home = tempfile.mkdtemp()
rc.recipes_dir = lambda h: home
OLD = "/Users/a/.llmbox/engines/b100/llama-server"
st = {"exists": set(), "after_detect": [], "installed": []}
runs = []
fake = types.SimpleNamespace(run=lambda cmd, timeout=0: (runs.append(cmd), types.SimpleNamespace(returncode=0 if any(p in cmd for p in st["exists"]) else 1, stdout="", stderr=""))[1])
hosts.load = lambda n: {"name": n, "ssh": None, "hw": {"runtimes": []}}
hosts.host_of = lambda prof: fake
hosts.detect = lambda n, ssh, measure=True: {"name": n, "hw": {"runtimes": list(st["after_detect"])}}
E.install = lambda h, prof, out=print, stream=False: (st["installed"].append(1), st["after_detect"].append({"path": "/Users/a/.llmbox/engines/b300/llama-server", "version": "version: 300"}))[0]
def recipe(server=OLD):
    open(os.path.join(home, "m.toml"), "w").write(f'[runtime]\nserver = "{server}"\nthreads = 0\n')
    return {"id": "m", "runtime": {"server": server, "engine": "llama.cpp"}}
said = []
st["exists"] = {OLD}
assert E.ensure_server("me", recipe(), out=said.append) == OLD and not said   # there: nothing to do
st.update(exists=set(), after_detect=[{"path": "/opt/llama/llama-server", "version": "version: 250"}], installed=[])
assert E.ensure_server("me", recipe(), out=said.append) == "/opt/llama/llama-server" and not st["installed"]
assert 'server = "/opt/llama/llama-server"' in open(os.path.join(home, "m.toml")).read() and "no longer at" in said[0]
st.update(after_detect=[], installed=[])
assert E.ensure_server("me", recipe(), out=said.append).endswith("b300/llama-server") and st["installed"] == [1]   # llmbox's own: again
st.update(after_detect=[], installed=[])
try:
    E.ensure_server("me", recipe("/home/a/llama.cpp/build/bin/llama-server"), out=said.append)
    raise AssertionError("no build anywhere must stop")
except SystemExit as e:
    assert "no other llama.cpp build" in str(e) and not st["installed"], e   # someone's own build: not replaced by a download
print("all passed")
