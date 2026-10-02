"""llama.cpp without compiling it: the official release build for this computer, from github.com/ggml-org/llama.cpp.

Which build: an NVIDIA card gets the CUDA build its driver can run (the driver's highest CUDA version, from nvidia-smi:
13.4, else 12.8), in the variant with the CUDA runtime inside, so only the driver is needed; a Mac the Metal build; an
AMD card ROCm or Vulkan; no card the CPU build. The archive is checked against the sha256 GitHub publishes for it
(the release's asset digest) and unpacked into ~/.llmbox/engines/<build>/ on the machine; `llmbox host add` then finds
its llama-server like any other."""
from __future__ import annotations

import json
import re
import shlex
import urllib.request
from . import UA

RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=12"
DIR = "$HOME/.llmbox/engines"   # in the host's shell


def asset_pattern(prof: dict) -> tuple[str, str]:
    """(regex for the release asset, what it is) for this machine."""
    hw = prof["hw"]
    g = (hw.get("gpus") or [{}])[0]
    arm = "arm64" in (hw.get("os") or "").lower() or "aarch64" in (hw.get("os") or "").lower()
    arch = "arm64" if arm else "x64"
    if g.get("vendor") == "apple":   # an Apple GPU means Apple silicon, whatever this Python was built for (an x86 one under Rosetta says x86_64)
        return r"^llama-b\d+-bin-macos-arm64\.tar\.gz$", "Metal (Mac)"
    if g.get("vendor") == "nvidia":
        cuda = float(re.match(r"(\d+\.\d+)", str(hw.get("cuda_driver") or "0.0")).group(1)) if hw.get("cuda_driver") else 0.0
        for v in ("13.4", "12.8"):
            if cuda >= float(v) and not (v == "12.8" and arm):
                return rf"^llama-b\d+-bin-ubuntu-cuda-{re.escape(v)}-{arch}\.tar\.gz$", f"CUDA {v} with its runtime (your driver supports CUDA {cuda:g})"
        return rf"^llama-b\d+-bin-ubuntu-vulkan-{arch}\.tar\.gz$", f"Vulkan (the driver supports CUDA {cuda:g}: update it to 580+ for the CUDA build)"
    if g.get("vendor") == "amd":
        return rf"^llama-b\d+-bin-ubuntu-vulkan-{arch}\.tar\.gz$", "Vulkan (AMD)"
    return rf"^llama-b\d+-bin-ubuntu-{arch}\.tar\.gz$", "CPU only (no graphics card found)"


def latest(pattern: str) -> dict:
    """The newest release asset matching the pattern: {tag, name, url, sha256, size}."""
    req = urllib.request.Request(RELEASES, headers={"Accept": "application/vnd.github+json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        rels = json.loads(r.read())
    for rel in rels:
        for a in rel.get("assets") or []:
            if re.match(pattern, a["name"]) and str(a.get("digest") or "").startswith("sha256:"):
                return {"tag": rel["tag_name"], "name": a["name"], "url": a["browser_download_url"],
                        "sha256": a["digest"].split(":", 1)[1], "size": a["size"]}
    raise SystemExit(f"no llama.cpp release build matches {pattern} among the latest releases")


def install(h, prof: dict, out=print, stream: bool = False) -> str:
    """Download, check and unpack the build on the host (a CUDA build with its CUDA runtime beside it: then only the
    driver is needed); the path of its llama-server."""
    pat, what = asset_pattern(prof)
    parts = [latest(pat)]
    if "-cuda-" in parts[0]["name"]:   # the release ships the CUDA runtime separately: cudart-llama-bN-bin-<same>.tar.gz
        parts.append(latest("^" + re.escape("cudart-" + parts[0]["name"]).replace(re.escape(parts[0]["tag"]), r"b\d+") + "$"))
    tag = parts[0]["tag"]
    out(f"llama.cpp {tag}: {what}, {sum(p['size'] for p in parts) // 2**20} MB")
    d = f"{DIR}/{tag}"
    steps = [f"mkdir -p {DIR} {d}", "S=$(command -v sha256sum >/dev/null && echo sha256sum || echo 'shasum -a 256')"]
    for a in parts:
        tmp = f"{DIR}/{a['name']}.part"
        steps += [f"curl -L --fail -C - {'-#' if stream else '-s'} -o {tmp} {shlex.quote(a['url'])}",
                  f"echo \"{a['sha256']}  {tmp}\" | $S -c - >/dev/null", f"tar -xzf {tmp} -C {d} --strip-components=1", f"rm -f {tmp}"]
    steps += [f"(xattr -dr com.apple.quarantine {d} 2>/dev/null || true)", f"ls {d}/llama-server"]
    cmd = " && ".join(steps)
    if stream:
        if h.stream(cmd, timeout=3600):
            raise SystemExit("the download or its sha256 check failed (run it again: the download resumes)")
    else:
        r = h.run(cmd, timeout=3600)
        if r.returncode:
            raise SystemExit(f"the download or its sha256 check failed: {r.stderr.strip()[-300:]}")
    path = h.run(f"echo {d}/llama-server", timeout=30).stdout.strip()
    v = h.run(f"{path} --version 2>&1 | head -2", timeout=60).stdout.strip().replace("\n", " | ")
    out(f"installed {path} (sha256 checked) - {v[:120]}")
    return path
