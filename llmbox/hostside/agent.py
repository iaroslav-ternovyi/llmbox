"""llmbox host agent: runs ON the inference host, stdlib only, prints one JSON document to stdout.

The controller ships this file (and gguf.py) to the host on every call, so nothing needs installing there.
Commands:
  hwinfo                      GPUs, CPU, RAM, disk, runtimes found, serving layer (llama-swap) state
  busy                        is the box serving/benchmarking right now? (never measure when busy)
  bandwidth [seconds]         sustained RAM read bandwidth (compiles a tiny C probe with gcc -O3 -fopenmp)
  gguf-header <path>          header of a local GGUF (kv, big-array summaries, tensor directory)
  probe-server <json>         start llama-server with the given argv on a free port, measure speed, stop it
  serve-start <json>          start llama-server detached (a free port unless given), wait until healthy; prints port + pid
  serve-stop <pid>            stop a server serve-start started (its process group)
  unload                      ask llama-swap to unload its models (frees the GPU for a probe)
  server-settings <port|model> what a running llama-server really uses: its argv (/proc) and /props (sampling, ctx, build)
  sha256 <path>               sha256 of a model file (all parts of a split model), cached by path + size + mtime
  telemetry-start <file> [s]  sample GPU/CPU/RAM every s seconds (default 5) into <file> in the background; prints the pid
  telemetry-stop <file> <pid> stop the sampler; aggregates + per-minute series of the samples
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


def _physical_cores() -> int | None:
    try:
        pairs = set()
        phys = core = None
        for ln in open("/proc/cpuinfo"):
            if ln.startswith("physical id"):
                phys = ln.split(":")[1].strip()
            elif ln.startswith("core id"):
                core = ln.split(":")[1].strip()
            elif not ln.strip() and core is not None:
                pairs.add((phys, core))
                phys = core = None
        return len(pairs) or None
    except OSError:
        return None


def hwinfo() -> dict:
    gpus = []
    q = sh("nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version,pcie.link.gen.max,pcie.link.width.max,"
           "power.limit,power.default_limit --format=csv,noheader,nounits")
    for line in filter(None, q.splitlines()):
        f = [x.strip() for x in line.split(",")]
        gpus.append({"vendor": "nvidia", "name": f[0], "vram_mib": int(float(f[1])), "vram_used_mib": int(float(f[2])),
                     "driver": f[3], "pcie_gen": f[4], "pcie_width": f[5], "power_limit_w": f[6], "power_default_w": f[7]})
    if not gpus and shutil.which("rocm-smi"):   # AMD: product name and VRAM (key names differ across ROCm versions)
        try:
            for card, d in json.loads(sh("rocm-smi --showproductname --showmeminfo vram --json") or "{}").items():
                name = next((v for k, v in d.items() if k.lower() in ("card series", "card model", "marketing name")), card)
                total = next((v for k, v in d.items() if "vram total memory" in k.lower()), 0)
                gpus.append({"vendor": "amd", "name": str(name), "vram_mib": int(int(total) / 2**20)})
        except (ValueError, TypeError):
            pass
    if platform.system() == "Darwin":   # Apple silicon: the GPU uses unified memory, macOS lets it wire ~2/3 to 3/4 of it
        mem_mib = int(sh("sysctl -n hw.memsize") or 0) // 2**20
        chip = sh("sysctl -n machdep.cpu.brand_string").strip()
        try:
            cores = int(json.loads(sh("system_profiler SPDisplaysDataType -json", timeout=60))["SPDisplaysDataType"][0].get("sppci_cores") or 0)
        except (ValueError, KeyError, IndexError):
            cores = 0
        gpus.append({"vendor": "apple", "name": chip, "vram_mib": int(mem_mib * (0.75 if mem_mib >= 36864 else 0.67)),
                     "gpu_cores": cores, "unified": True})
        du = shutil.disk_usage(os.path.expanduser("~"))
        return {"hostname": platform.node(), "os": platform.platform(), "python": sys.version.split()[0],
                "cpu": {"model": chip, "threads": os.cpu_count(), "cores": int(sh("sysctl -n hw.physicalcpu") or 0) or None},
                "ram_mib": mem_mib, "ram_available_mib": None, "gpus": gpus, "llama_swap": {},
                "runtimes": [{"path": p, "version": sh(f"'{p}' --version 2>&1 | head -2", timeout=20).replace(chr(10), " | ")}
                             for p in sorted({shutil.which("llama-server") or ""} - {""})],
                "disk_free_gib": round(du.free / 2**30, 1), "models_dir_guess": os.path.expanduser("~/models")}
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
        "cpu": {"model": cpu_model, "threads": os.cpu_count(), "cores": _physical_cores()},
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
    # by process name: a full-command-line match also caught this agent itself (a probe spec names the llama-server binary)
    servers = sh("pgrep -xc llama-server || true")
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
        samp = spec.get("sampling") or {}   # the recipe's sampling: speculative acceptance at temperature 0 flatters MTP
        # decode speed on natural code generation (speculative decoding acceptance depends on content)
        dec = []
        for i in range(int(spec.get("repeats", 2))):
            r = _post(base + "/completion", {"prompt": _CODE_PROMPT + f" (variant {i})", "n_predict": n_gen, "temperature": 0,
                                             "cache_prompt": False, "ignore_eos": True, **samp})
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
                                             "temperature": 0, "cache_prompt": False, "ignore_eos": True, **samp})
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


def serve_start(spec: dict) -> dict:
    """spec = {server, args (without --port/--host), affinity, model_files, headroom_mib, bind, port}: a llama-server
    that outlives this call (its own session), for a run that needs one for a while (llmbox test / run)."""
    total_mib = next((int(l.split()[1]) // 1024 for l in open("/proc/meminfo") if l.startswith("MemTotal")), 0)
    files_mib = sum(os.path.getsize(f) for f in spec.get("model_files", [])) // 2**20
    cram = max(2048, min(48000, total_mib - files_mib - int(spec.get("headroom_mib", 4096))))
    port = int(spec.get("port") or _free_port())
    args = [a.replace("$CRAM", str(cram)) for a in spec["args"]]
    cmd = (["taskset", "-c", spec["affinity"]] if spec.get("affinity") else []) + [spec["server"], "--port", str(port),
                                                                                    "--host", spec.get("bind") or "127.0.0.1"] + args
    log_path = os.path.join(tempfile.gettempdir(), f"llmbox-serve-{port}.log")
    proc = subprocess.Popen(cmd, stdout=open(log_path, "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    t0 = time.time()
    while True:
        if proc.poll() is not None:
            return {"error": f"server exited with code {proc.returncode}", "log": log_path, "cmd": cmd}
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                if r.status == 200:
                    return {"port": port, "pid": proc.pid, "log": log_path, "cmd": cmd, "load_seconds": round(time.time() - t0, 1)}
        except Exception:
            pass
        if time.time() - t0 > spec.get("load_timeout", 900):
            os.killpg(proc.pid, 15)
            return {"error": "server did not become healthy", "log": log_path, "cmd": cmd}
        time.sleep(2)


def serve_stop(pid: int) -> dict:
    import signal
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"stopped": pid, "was": "gone"}
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return {"stopped": pid}
        time.sleep(1)
    os.killpg(pid, signal.SIGKILL)
    return {"stopped": pid, "killed": True}


def _get(url: str, timeout: int = 10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def server_settings(target: str) -> dict:
    """target: a llama-server port, or a llama-swap model id (its upstream port is looked up in /running)."""
    port, via = None, "port"
    if target.isdigit():
        port = target
    else:
        via = "llama-swap"
        for m in (_get("http://localhost:8080/running") or {}).get("running", []):
            if m.get("model") == target and m.get("proxy"):
                port = m["proxy"].rsplit(":", 1)[-1].strip("/")
    out = {"target": target, "via": via, "port": port, "argv": None, "props": None}
    if not port:
        out["error"] = f"no running server for {target}"
        return out
    for pid in sh("pgrep -x llama-server").split():
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace").split("\0")[:-1]
        except OSError:
            continue
        if "--port" in argv and argv[argv.index("--port") + 1] == str(port):
            out["argv"], out["pid"] = argv, int(pid)
            break
    props = _get(f"http://localhost:{port}/props")
    if props:
        g = props.get("default_generation_settings") or {}
        out["props"] = {"build_info": props.get("build_info"), "model_path": props.get("model_path"), "n_ctx": g.get("n_ctx"),
                        "total_slots": props.get("total_slots"), "params": g.get("params") or {},
                        "chat_template_sha": __import__("hashlib").sha256((props.get("chat_template") or "").encode()).hexdigest()[:16]}
    return out


def sha256_file(path: str) -> dict:
    import hashlib
    parts = [path]
    if "-00001-of-" in path:
        n = int(path.split("-of-")[1][:5])
        parts = [path.replace("-00001-of-", f"-{i:05d}-of-") for i in range(1, n + 1)]
    cache_dir = os.path.expanduser("~/.cache/llmbox/sha256")
    os.makedirs(cache_dir, exist_ok=True)
    out = []
    for f in parts:
        st = os.stat(f)
        key = os.path.join(cache_dir, f"{os.path.basename(f)}.{st.st_size}.{int(st.st_mtime)}")
        if os.path.exists(key):
            out.append(open(key).read().strip())
            continue
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 24), b""):
                h.update(chunk)
        open(key, "w").write(h.hexdigest())
        out.append(h.hexdigest())
    return {"path": path, "sha256": out[0] if len(out) == 1 else out, "bytes": sum(os.path.getsize(f) for f in parts)}


def _cpu_temp() -> float | None:
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            name = open(f"{h}/name").read().strip()
        except OSError:
            continue
        if name in ("k10temp", "coretemp", "zenpower"):
            try:
                return int(open(f"{h}/temp1_input").read()) / 1000
            except OSError:
                pass
    return None


def telemetry_loop(path: str, interval: float) -> None:
    """Background sampler: one CSV line per sample (t, gpu °C, W, util %, VRAM MiB, CPU °C, RAM used MiB, load)."""
    q = "temperature.gpu,power.draw,utilization.gpu,memory.used"
    with open(path, "a", buffering=1) as f:
        while True:
            g = sh(f"nvidia-smi --query-gpu={q} --format=csv,noheader,nounits", timeout=10).splitlines()
            g = [x.strip() for x in (g[0].split(",") if g else ["", "", "", ""])]
            mi = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if l.split(":")[0] in ("MemTotal", "MemAvailable")}
            ram_used = (mi.get("MemTotal", 0) - mi.get("MemAvailable", 0)) // 1024
            f.write(",".join(str(x) for x in (round(time.time(), 1), *g, _cpu_temp() or "", ram_used, os.getloadavg()[0])) + "\n")
            time.sleep(interval)


def telemetry_start(path: str, interval: float = 5.0) -> dict:
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "telemetry-loop", path, str(interval)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"pid": p.pid, "file": path}


def telemetry_stop(path: str, pid: int) -> dict:
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    rows = []
    try:
        for l in open(path):
            v = l.strip().split(",")
            if len(v) == 8:
                rows.append([float(x) if x not in ("", "[N/A]") else None for x in v])
        os.unlink(path)
    except OSError:
        pass
    if not rows:
        return {"samples": 0}
    names = ["gpu_temp_c", "gpu_power_w", "gpu_util_pct", "vram_used_mib", "cpu_temp_c", "ram_used_mib", "load1"]
    out = {"samples": len(rows), "seconds": round(rows[-1][0] - rows[0][0]), "interval_s": round((rows[-1][0] - rows[0][0]) / max(1, len(rows) - 1), 1)}
    t0 = rows[0][0]
    for i, n in enumerate(names, 1):
        vals = [r[i] for r in rows if r[i] is not None]
        if not vals:
            continue
        per_min: dict = {}
        for r in rows:
            if r[i] is not None:
                per_min.setdefault(int((r[0] - t0) // 60), []).append(r[i])
        out[n] = {"avg": round(sum(vals) / len(vals), 1), "max": round(max(vals), 1), "min": round(min(vals), 1),
                  "per_min": [round(sum(v) / len(v), 1) for _, v in sorted(per_min.items())]}
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
    elif cmd == "serve-start":
        out = serve_start(json.loads(args[0]))
    elif cmd == "serve-stop":
        out = serve_stop(int(args[0]))
    elif cmd == "server-settings":
        out = server_settings(args[0])
    elif cmd == "sha256":
        out = sha256_file(args[0])
    elif cmd == "telemetry-start":
        out = telemetry_start(args[0], float(args[1]) if len(args) > 1 else 5.0)
    elif cmd == "telemetry-stop":
        out = telemetry_stop(args[0], int(args[1]))
    elif cmd == "telemetry-loop":
        telemetry_loop(args[0], float(args[1]))
        return
    else:
        out = {"error": f"unknown command {cmd}"}
    json.dump(out, sys.stdout, default=str)


if __name__ == "__main__":
    main(sys.argv[1:])
