"""llmbox host agent: runs ON the inference host, stdlib only, prints one JSON document to stdout.

The controller ships this file (and gguf.py) to the host on every call, so nothing needs installing there.
Commands:
  hwinfo                      GPUs, CPU, RAM, disk, runtimes found, serving layer (llama-swap) state
  busy                        is the box serving/benchmarking right now? (never measure when busy)
  bandwidth [seconds]         sustained RAM read bandwidth (compiles a tiny C probe with gcc -O3 -fopenmp)
  gguf-header <path>          header of a local GGUF (kv, big-array summaries, tensor directory)
  probe-server <json>         start llama-server with the given argv on a free port, measure speed, stop it
  unload                      ask llama-swap to unload its models (frees the GPU for a probe)
"""
from __future__ import annotations

import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request


def sh(cmd: str, timeout: int = 30) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def hwinfo() -> dict:
    gpus = []
    q = sh("nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version,pcie.link.gen.max,pcie.link.width.max,"
           "power.limit,power.default_limit --format=csv,noheader,nounits")
    for line in filter(None, q.splitlines()):
        f = [x.strip() for x in line.split(",")]
        gpus.append({"vendor": "nvidia", "name": f[0], "vram_mib": int(float(f[1])), "vram_used_mib": int(float(f[2])),
                     "driver": f[3], "pcie_gen": f[4], "pcie_width": f[5], "power_limit_w": f[6], "power_default_w": f[7]})
    mem = {}
    if os.path.exists("/proc/meminfo"):
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) // 1024
    cpu_model = ""
    if os.path.exists("/proc/cpuinfo"):
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    runtimes = []
    for path in sorted(set(glob.glob(os.path.expanduser("~/*/build/bin/llama-server")) + [shutil.which("llama-server") or ""])):
        if path and os.path.exists(path):
            ver = sh(f"'{path}' --version 2>&1 | head -2", timeout=20)
            runtimes.append({"path": path, "version": ver.replace("\n", " | ")})
    swap = {}
    for cfg in glob.glob(os.path.expanduser("~/llama-swap/config.yaml")):
        swap["config"] = cfg
    try:
        with urllib.request.urlopen("http://localhost:8080/running", timeout=3) as r:
            swap["running"] = json.load(r).get("running", [])
            swap["url"] = "http://localhost:8080"
    except Exception:
        pass
    du = shutil.disk_usage(os.path.expanduser("~"))
    return {
        "hostname": platform.node(), "os": platform.platform(), "python": sys.version.split()[0],
        "cpu": {"model": cpu_model, "threads": os.cpu_count()},
        "ram_mib": mem.get("MemTotal", 0), "ram_available_mib": mem.get("MemAvailable", 0),
        "gpus": gpus, "runtimes": runtimes, "llama_swap": swap,
        "disk_free_gib": round(du.free / 2**30, 1), "models_dir_guess": os.path.expanduser("~/models"),
    }


def busy() -> dict:
    running = []
    try:
        with urllib.request.urlopen("http://localhost:8080/running", timeout=3) as r:
            running = [m.get("model") for m in json.load(r).get("running", [])]
    except Exception:
        pass
    util = sh("nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits")
    load1 = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
    servers = sh("pgrep -fa llama-server | grep -v pgrep | wc -l")
    return {"llama_swap_running": running, "gpu": util, "load1": round(load1, 2),
            "llama_server_procs": int(servers or 0), "busy": bool(running) or int(servers or 0) > 0 or load1 > 2.0}


_BW_C = r"""
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <omp.h>
int main(int argc, char **argv) {
    size_t n = (size_t)1 << 28;            /* 2 GiB of uint64 */
    double secs = argc > 1 ? atof(argv[1]) : 3.0;
    uint64_t *a = aligned_alloc(64, n * 8);
    #pragma omp parallel for
    for (size_t i = 0; i < n; i++) a[i] = i;
    double best = 0; double t_end = omp_get_wtime() + secs; uint64_t sink = 0;
    while (omp_get_wtime() < t_end) {
        double t0 = omp_get_wtime(); uint64_t s = 0;
        #pragma omp parallel for reduction(+:s)
        for (size_t i = 0; i < n; i++) s += a[i];
        double gbs = n * 8 / (omp_get_wtime() - t0) / 1e9; sink += s; if (gbs > best) best = gbs;
    }
    printf("{\"ram_read_gbs\": %.1f, \"threads\": %d, \"sink\": %llu}\n", best, omp_get_max_threads(), (unsigned long long)(sink & 1));
    free(a); return 0;
}
"""


def bandwidth(seconds: float = 3.0) -> dict:
    if busy()["busy"]:
        return {"error": "host is busy (model serving or high load) - not measuring"}
    cc = shutil.which("gcc") or shutil.which("cc")
    if not cc:
        return {"error": "no C compiler on host; pass --ram-bw manually"}
    d = tempfile.mkdtemp(prefix="llmbox-bw-")
    src, exe = os.path.join(d, "bw.c"), os.path.join(d, "bw")
    open(src, "w").write(_BW_C)
    r = subprocess.run([cc, "-O3", "-march=native", "-fopenmp", src, "-o", exe], capture_output=True, text=True)
    if r.returncode:
        return {"error": "compile failed", "stderr": r.stderr[-500:]}
    out = subprocess.run([exe, str(seconds)], capture_output=True, text=True, timeout=120).stdout
    shutil.rmtree(d, ignore_errors=True)
    return json.loads(out)


def gguf_header(path: str) -> dict:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gguf  # shipped next to this file
    size = os.path.getsize(path)
    h = gguf.read_header(gguf.file_fetcher(path), size)
    return {"version": h.version, "kv": h.kv, "arrays": {k: list(v) for k, v in h.arrays.items()},
            "tensors": [[t.name, list(t.shape), t.ggml_type, t.offset, t.nbytes] for t in h.tensors],
            "data_start": h.data_start, "header_bytes": h.header_bytes, "file_size": size}


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(url: str, body: dict, timeout: int = 1800) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


_WORDS = ("river stone paper signal matrix lantern orbit garden copper silver window thread engine harbor canvas "
          "meadow circuit falcon marble prism vector tunnel ember summit glacier compass beacon quartz willow").split()


def _filler(n_tokens: int, seed: int) -> str:
    """Deterministic pseudo-random prose (~1 token per word) - unique per seed so no prompt cache can help."""
    import random
    rnd = random.Random(seed)
    words = [rnd.choice(_WORDS) for _ in range(n_tokens)]
    return " ".join(words)


_CODE_PROMPT = ("Write a complete, well-commented Python module that implements an LRU cache with TTL expiry, "
                "thread safety and a small command-line demo. Include type hints and docstrings.")


def probe_server(spec: dict) -> dict:
    """spec = {server, args (without --port), affinity, model_files, headroom_mib, depths, gen_tokens, prefill_tokens}"""
    if busy()["busy"] and not spec.get("force"):
        return {"error": "host is busy (model serving or high load) - not measuring"}
    total_mib = 0
    for line in open("/proc/meminfo"):
        if line.startswith("MemTotal"):
            total_mib = int(line.split()[1]) // 1024
    files_mib = sum(os.path.getsize(f) for f in spec.get("model_files", [])) // 2**20
    cram = max(2048, min(48000, total_mib - files_mib - int(spec.get("headroom_mib", 4096))))
    port = _free_port()
    args = [a.replace("$CRAM", str(cram)) for a in spec["args"]]
    cmd = ([ "taskset", "-c", spec["affinity"]] if spec.get("affinity") else []) + [spec["server"], "--port", str(port)] + args
    log_path = tempfile.mktemp(prefix="llmbox-probe-", suffix=".log")
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=open(log_path, "w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    out = {"cmd": cmd, "cache_ram_mib": cram}
    try:
        while True:
            if proc.poll() is not None:
                out["error"] = f"server exited with code {proc.returncode}"
                return out
            try:
                with urllib.request.urlopen(base + "/health", timeout=5) as r:
                    if r.status == 200:
                        break
            except Exception:
                pass
            if time.time() - t0 > spec.get("load_timeout", 900):
                out["error"] = "server did not become healthy"
                return out
            time.sleep(2)
        out["load_seconds"] = round(time.time() - t0, 1)
        n_gen = int(spec.get("gen_tokens", 400))
        # decode speed on natural code generation (speculative decoding acceptance depends on content)
        dec = []
        for i in range(int(spec.get("repeats", 2))):
            r = _post(base + "/completion", {"prompt": _CODE_PROMPT + f" (variant {i})", "n_predict": n_gen, "temperature": 0,
                                             "cache_prompt": False, "ignore_eos": True})
            t = r.get("timings", {})
            dec.append({"tps": t.get("predicted_per_second"), "n": t.get("predicted_n"),
                        "draft_n": t.get("draft_n"), "draft_accepted": t.get("draft_n_accepted")})
        out["decode"] = dec
        # prefill speed
        r = _post(base + "/completion", {"prompt": _filler(int(spec.get("prefill_tokens", 12000)), 1), "n_predict": 1,
                                         "cache_prompt": False})
        t = r.get("timings", {})
        out["prefill"] = {"tps": t.get("prompt_per_second"), "n": t.get("prompt_n")}
        # decode at depth: long unique prefix, then generate
        out["depth"] = []
        for d in spec.get("depths", [32000]):
            r = _post(base + "/completion", {"prompt": _filler(d, 100 + d) + "\n\n" + _CODE_PROMPT, "n_predict": 200,
                                             "temperature": 0, "cache_prompt": False, "ignore_eos": True})
            t = r.get("timings", {})
            out["depth"].append({"depth": t.get("prompt_n"), "prefill_tps": t.get("prompt_per_second"),
                                 "decode_tps": t.get("predicted_per_second")})
        try:
            with urllib.request.urlopen(base + "/props", timeout=10) as r:
                props = json.load(r)
            out["server_props"] = {k: props.get(k) for k in ("build_info", "total_slots", "model_path")}
        except Exception:
            pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
        log = open(log_path, errors="replace").read()
        out["log_tail"] = "\n".join(l for l in log.splitlines()
                                      if any(k in l for k in ("offload", "buffer size", "KV self size", "fit", "CUDA0", "error")))[-4000:]
        os.unlink(log_path)
    return out


def main(argv: list[str]) -> None:
    cmd, args = (argv[0], argv[1:]) if argv else ("hwinfo", [])
    if cmd == "hwinfo":
        out = hwinfo()
    elif cmd == "busy":
        out = busy()
    elif cmd == "bandwidth":
        out = bandwidth(float(args[0]) if args else 3.0)
    elif cmd == "gguf-header":
        out = gguf_header(args[0])
    elif cmd == "unload":
        try:
            with urllib.request.urlopen("http://localhost:8080/unload", timeout=60) as r:
                out = {"unloaded": r.read().decode()[:100]}
        except Exception as e:
            out = {"error": str(e)}
    elif cmd == "probe-server":
        out = probe_server(json.loads(args[0]))
    else:
        out = {"error": f"unknown command {cmd}"}
    json.dump(out, sys.stdout, default=str)


if __name__ == "__main__":
    main(sys.argv[1:])
