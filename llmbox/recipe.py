"""Recipes: one declarative file per model setup on a host. Launch scripts and serving entries are generated from them.

A recipe is TOML (readable, diffable, comment-friendly). `extends = "<id>"` inherits another recipe and overrides
fields, e.g. the daily 3-slot entry extends the 1-slot benchmark entry. Every value may carry its source in `notes`.
"""
from __future__ import annotations

import copy
import json
import os
import shlex
import tomllib

from .hosts import HOME

LLAMA_SWAP_CONFIG = "~/llama-swap/config.yaml"
_ARGV_OF = os.path.join(os.path.dirname(__file__), "hostside", "argv_of.sh")

DEFAULTS = {
    "runtime": {"server": "llama-server", "cpu_affinity": "", "threads": 0, "engine": "llama.cpp"},
    "placement": {"ctx": 0, "kv_type": "q8_0", "fit": True, "fit_target_mib": 256, "flash_attn": True, "load_mode": "none",
                  "batch": 2048, "ubatch": 2048, "slots": 1, "kv_unified": False, "cache_ram": "auto",
                  "cache_ram_headroom_mib": 4096, "cache_reuse": 256, "kv_offload": True, "n_cpu_moe": 0},
    "speculative": {"type": "", "draft_max": 0},
    "sampling": {},
    "chat": {"template_kwargs": {}, "jinja": True},
    "antiloop": {"marker_ids": [], "marker_bias": 0.5, "reasoning_budget": -1,
                 "budget_message": "I have reasoned enough. I will now act on my best plan.",
                 "dry_think_only": False, "dry_multiplier": 0.8, "dry_base": 1.75, "dry_allowed_length": 24,
                 "dry_penalty_last_n": 4096, "reasoning_loop": 0},
    "serve": {"wait_vram_below_mib": 600, "ttl": 3600, "aliases": []},
    "extra": {"args": []},
}
SAMPLING_FLAGS = {"temp": "--temp", "top_p": "--top-p", "top_k": "--top-k", "min_p": "--min-p",
                  "presence_penalty": "--presence-penalty", "repeat_penalty": "--repeat-penalty", "max_tokens": "-n"}


def recipes_dir(host: str) -> str:
    return os.path.join(HOME, "recipes", host)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def load(host: str, rid: str, _seen: tuple = ()) -> dict:
    if rid in _seen:
        raise ValueError(f"recipe extends cycle: {' -> '.join(_seen + (rid,))}")
    with open(os.path.join(recipes_dir(host), f"{rid}.toml"), "rb") as f:
        r = tomllib.load(f)
    if r.get("extends"):
        r = _merge(load(host, r["extends"], _seen + (rid,)), {k: v for k, v in r.items() if k != "extends"})
    r["id"] = rid
    return _merge(DEFAULTS, r)


SPEED_KEYS = ("model", "runtime", "placement", "extra", "speculative")   # what a recipe's speed depends on


def speed_parent(host: str, rid: str) -> str | None:
    """The recipe this one extends when it changes nothing its speed depends on (k2-medium: k2-horizon's file, placement
    and threads with a shorter thinking), so the parent's speed measurement is this one's too; else None."""
    try:
        with open(os.path.join(recipes_dir(host), f"{rid}.toml"), "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return raw.get("extends") if raw.get("extends") and not any(k in raw for k in SPEED_KEYS) else None


def load_set(host: str, rid: str, overrides: list[str] | None = None) -> dict:
    """The recipe with `--set key=value` overrides applied, values read as JSON when they are (a variant: llmbox test
    --set, llmbox bench --set)."""
    r = load(host, rid)
    for o in overrides or []:
        k, v = o.split("=", 1)
        d = r
        for p in k.split(".")[:-1]:
            d = d.setdefault(p, {})
        try:
            v = json.loads(v)
        except ValueError:
            pass
        d[k.split(".")[-1]] = v
    return r


def ids(host: str) -> list[str]:
    d = recipes_dir(host)
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".toml")) if os.path.isdir(d) else []


def server_args(r: dict, port: str = "$PORT") -> list[str]:
    """llama-server argv (without the binary). `$CRAM` is resolved by the launcher when cache_ram = "auto"."""
    p, sp, ch, al = r["placement"], r["speculative"], r["chat"], r["antiloop"]
    a = ["--port", port, "-m", r["model"]["path"]]
    if p["fit"]:
        a += ["--fit", "on", "--fit-target", str(p["fit_target_mib"])]
    else:   # explicit placement (ik_llama.cpp's --fit crashes on K2 Horizon): every layer on the card, the experts of the first N in RAM
        a += ["-ngl", "999"] + (["--n-cpu-moe", str(p["n_cpu_moe"])] if p.get("n_cpu_moe") else [])
    a += ["--flash-attn", "on" if p["flash_attn"] else "off", "--cache-type-k", p["kv_type"], "--cache-type-v", p["kv_type"]]
    if p["load_mode"]:   # "" = do not pass (builds without the flag, e.g. the PrismML fork)
        a += ["--load-mode", p["load_mode"]]
    a += ["-c", str(p["ctx"]), "-b", str(p["batch"]), "-ub", str(p["ubatch"]), "--parallel", str(p["slots"])]
    if p["kv_unified"] != "auto":   # "auto" = leave it to the server default
        a.append("--kv-unified" if p["kv_unified"] else "--no-kv-unified")
    if not p.get("kv_offload", True):   # KV cache in system RAM: the whole card for weights, attention on the CPU
        a.append("--no-kv-offload")
    if r["runtime"]["threads"]:
        a += ["--threads", str(r["runtime"]["threads"])]
    for k, flag in SAMPLING_FLAGS.items():
        if k in r["sampling"]:
            a += [flag, _num(r["sampling"][k])]
    a += ["--cache-ram", "$CRAM" if p["cache_ram"] == "auto" else str(p["cache_ram"])]
    if ch["jinja"]:
        a.append("--jinja")
    if p["cache_reuse"]:
        a += ["--cache-reuse", str(p["cache_reuse"])]
    if sp["type"]:
        a += ["--spec-type", sp["type"], "--spec-draft-n-max", str(sp["draft_max"])]
    if ch["template_kwargs"]:
        a += ["--chat-template-kwargs", json.dumps(ch["template_kwargs"], separators=(",", ":"))]
    for tid in al["marker_ids"]:  # repeated flags accumulate; comma-separated values do not parse
        a += ["--logit-bias", f"{tid}-{_num(al['marker_bias'])}"]
    if al["dry_think_only"]:
        a += ["--dry-think-only", "--dry-multiplier", _num(al["dry_multiplier"]), "--dry-base", _num(al["dry_base"]),
              "--dry-allowed-length", str(al["dry_allowed_length"]), "--dry-penalty-last-n", str(al["dry_penalty_last_n"])]
    if al["reasoning_budget"] >= 0:
        a += ["--reasoning-budget", str(al["reasoning_budget"]), "--reasoning-budget-message", al["budget_message"]]
    if al.get("reasoning_loop"):   # content-based loop detector (local llama.cpp patch 0002)
        a += ["--reasoning-loop", str(al["reasoning_loop"])]
    a += [str(x) for x in r["extra"]["args"]]
    return _ik(a) if r["runtime"].get("engine") == "ik_llama.cpp" else a


# ik_llama.cpp (github.com/ikawrakow/ik_llama.cpp) spells some flags its own way and lacks others: --fit is a switch
# with --fit-margin, speculative stages are "--spec-type mtp:n_max=N", and it has no --load-mode, --kv-unified or
# --cache-reuse (its own prompt cache is --cache-ram). Checked on its llama-server --help, 2026-09-29.
_IK_DROP = {"--load-mode": 1, "--kv-unified": 0, "--no-kv-unified": 0, "--cache-reuse": 1}
_IK_SPEC = {"draft-mtp": "mtp"}


def _ik(a: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(a):
        x = a[i]
        if x == "--fit" and i + 1 < len(a) and a[i + 1] in ("on", "off"):
            out += ["--fit"] if a[i + 1] == "on" else []
            i += 2
        elif x == "--fit-target":
            out += ["--fit-margin", a[i + 1]]
            i += 2
        elif x in _IK_DROP:
            i += 1 + _IK_DROP[x]
        elif x == "--spec-type":
            n = a[i + 3] if i + 3 < len(a) and a[i + 2] == "--spec-draft-n-max" else None
            out += ["--spec-type", _IK_SPEC.get(a[i + 1], a[i + 1]) + (f":n_max={n}" if n else "")]
            i += 4 if n else 2
        else:
            out.append(x)
            i += 1
    return out


def _num(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def launcher(r: dict) -> str:
    """A self-contained bash launcher: `<script> <port> [extra llama-server args]`."""
    p, sv, rt = r["placement"], r["serve"], r["runtime"]
    lines = ["#!/usr/bin/env bash",
             f"# Generated by llmbox from recipe '{r['id']}'. Edit the recipe, not this file (`llmbox recipe render`).",
             *(f"# {n}" for n in r.get("notes", {}).get("lines", [])),
             'PORT="${1:?usage: $0 <port> [extra llama-server args]}"; shift || true']
    if p["cache_ram"] == "auto":
        lines += [f'MODEL={shlex.quote(r["model"]["path"])}',
                  "# host prompt cache = all RAM this model leaves free, minus headroom (clamped 2048..48000 MiB)",
                  'case "$MODEL" in *-00001-of-*.gguf) parts=("${MODEL%-00001-of-*}"-0*-of-*.gguf) ;; *) parts=("$MODEL") ;; esac',
                  'total=$(awk \'/^MemTotal/{print int($2/1024)}\' /proc/meminfo); files=0',
                  'for f in "${parts[@]}"; do [ -f "$f" ] || { echo "model file missing: $f" >&2; exit 1; }; files=$(( files + $(stat -c %s "$f") / 1048576 )); done',
                  f'CRAM=$(( total - files - {p["cache_ram_headroom_mib"]} )); [ $CRAM -lt 2048 ] && CRAM=2048; [ $CRAM -gt 48000 ] && CRAM=48000']
    if sv["wait_vram_below_mib"]:
        lines += ["# wait until the previous model has left the GPU (llama-swap swaps models)",
                  "for i in $(seq 1 90); do u=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1); "
                  f'[ -n "$u" ] && [ "$u" -lt {sv["wait_vram_below_mib"]} ] && break; sleep 1; done']
    args = server_args(r)
    quoted = " ".join(a if a in ("$PORT", "$CRAM") else ('"$PORT"' if a == "$PORT" else shlex.quote(a)) for a in args)
    quoted = quoted.replace("$PORT", '"$PORT"').replace("$CRAM", '"$CRAM"')
    prefix = f"taskset -c {rt['cpu_affinity']} " if rt["cpu_affinity"] else ""
    lines.append(f'exec {prefix}{shlex.quote(rt["server"])} {quoted} "$@"')
    return "\n".join(lines) + "\n"


def llama_swap_entry(r: dict, launcher_path: str) -> str:
    sv = r["serve"]
    out = [f"  {r['id']}:"]
    if r.get("description"):
        out.insert(0, f"  # {r['description']}")
    if sv["aliases"]:
        out += ["    aliases:"] + [f"      - {a}" for a in sv["aliases"]]
    out += [f"    cmd: {launcher_path} ${{PORT}}", f"    ttl: {sv['ttl']}"]
    return "\n".join(out) + "\n"


# ---- drift check: does the host really run what the recipe says? ------------------------------------------------------

def live_argv(h, entry: str, config: str = LLAMA_SWAP_CONFIG) -> list[str]:
    """The argv a llama-swap entry really starts with (its launcher chain run with `exec` stubbed, nothing is started)."""
    # the entry's cmd line, e.g. "start-tiel.sh ${PORT} --parallel 3 --kv-unified" (extra args are part of what runs)
    prog = '$0==id{f=1;next} f&&/cmd:/{sub(/^ *cmd: */,""); gsub(/[$][{]PORT[}]/,"5825"); print; exit}'
    cmd = (f"C=$(awk -v id={shlex.quote('  ' + entry + ':')} {shlex.quote(prog)} {config}); "
           f'[ -n "$C" ] || {{ echo "no llama-swap entry {entry}" >&2; exit 3; }}; '
           'env -i HOME="$HOME" PATH=/usr/bin:/bin bash -s $C')
    r = h.run(cmd, input=open(_ARGV_OF).read(), timeout=60)
    if r.returncode:
        raise RuntimeError(f"{entry}: {r.stderr.strip()[-300:]}")
    return r.stdout.rstrip("\n").split("\n")


def _isnum(t: str) -> bool:
    try:
        float(t)
        return True
    except ValueError:
        return False


REPEATABLE = {"--logit-bias", "-ot", "--override-tensor", "--lora", "--dry-sequence-breaker"}
_NEGATED = {"--no-kv-unified": "--kv-unified", "--no-kv-offload": "--kv-offload", "--no-jinja": "--jinja", "--no-mmap": "--mmap", "--no-cont-batching": "--cont-batching"}


def _flags(args: list[str]) -> dict:
    """{flag: [values of each occurrence]} with llama.cpp semantics: a repeated flag keeps its LAST value unless it is
    repeatable (logit biases, tensor overrides); `--no-x` and `--x` are one switch whose last form wins."""
    occ: list = []
    for t in args:
        if t.startswith("-") and not _isnum(t):
            occ.append([t, ()])
        elif occ:
            occ[-1][1] = occ[-1][1] + (t,)
    out: dict = {}
    for f, v in occ:
        if f in _NEGATED or f in _NEGATED.values():
            key = _NEGATED.get(f, f)
            out[key] = [("off",) if f in _NEGATED else ("on",)]
        elif f in REPEATABLE:
            out.setdefault(f, []).append(v)
        else:
            out[f] = [v]
    return out


def _same(a: str, b: str) -> bool:
    if "$CRAM" in (a, b) and (_isnum(a) or _isnum(b)):
        return True   # cache_ram = auto: the launcher computes it on the host
    return a == b or (_isnum(a) and _isnum(b) and float(a) == float(b))


def diff(r: dict, live: list[str]) -> list[str]:
    """Differences between the recipe and the host's real command line (order of flags ignored)."""
    rt = r["runtime"]
    want_prefix = (["taskset", "-c", rt["cpu_affinity"]] if rt["cpu_affinity"] else []) + [rt["server"]]
    n = len(want_prefix)
    out = []
    if live[:n] != want_prefix:
        out.append(f"runtime: recipe {' '.join(want_prefix)} | host {' '.join(live[:n])}")
    want, have = _flags(server_args(r, port="5825")), _flags(live[n:])
    for f in sorted(set(want) | set(have)):
        w, h = want.get(f, []), have.get(f, [])
        if f == "--logit-bias":
            w, h = sorted(w), sorted(h)
        if len(w) != len(h) or any(len(x) != len(y) or not all(_same(a, b) for a, b in zip(x, y)) for x, y in zip(w, h)):
            fmt = lambda v: " ".join(" ".join(x) for x in v) if v else "(absent)"
            out.append(f"{f}: recipe {fmt(w)} | host {fmt(h)}")
    return out
