"""What a benchmark run really ran with, captured on the inference host: settings, model hash, build, telemetry.

begin() (before the first item): loads the model with a tiny request, then records the server's argv (/proc) and /props,
and starts a telemetry sampler on the host (GPU temp / power / util / VRAM, CPU temp, RAM, every 5 s).
end() (after the run): records the settings again - a run whose settings changed underway is marked inconsistent -,
stops the sampler, hashes the model file (cached on the host) and diffs the settings against the recipe. Differences
are split into speed-only (placement, batch, context...: the recipe's quality score still applies) and quality
(sampling, KV type, reasoning, model file...: a variant that needs its own score).
Every step is best effort: a failure is recorded in the result, it never fails the run.
"""
from __future__ import annotations

import os
import time

from . import client, hosts, recipe as rc

# flags that change only speed / memory, never the model's output distribution (speculative decoding is lossless)
SPEED_ONLY = {"--port", "-c", "--ctx-size", "-b", "--batch-size", "-ub", "--ubatch-size", "--parallel", "-np", "--kv-unified",
              "--threads", "-t", "--threads-batch", "--cache-ram", "--cache-reuse", "--fit", "--fit-target", "--load-mode",
              "-ngl", "--n-gpu-layers", "--n-cpu-moe", "-ot", "--override-tensor", "--flash-attn", "-fa", "--no-mmap", "--mlock",
              "--spec-type", "--spec-draft-n-max", "--spec-draft-n-min", "--cont-batching", "--no-cont-batching", "--mmap"}
SAMPLING_KEYS = ("temperature", "top_k", "top_p", "min_p", "typical_p", "presence_penalty", "frequency_penalty", "repeat_penalty",
                 "repeat_last_n", "dry_multiplier", "dry_base", "dry_allowed_length", "xtc_probability", "top_n_sigma", "n_predict",
                 "samplers", "reasoning_format")


def _settings(h, served: str) -> dict:
    st = h.agent("server-settings", served, timeout=60)
    p = st.get("props") or {}
    if p.get("params"):
        p["params"] = {k: p["params"][k] for k in SAMPLING_KEYS if k in p["params"]}
    return st


def begin(host_name: str, base_url: str, served: str, api_key: str | None = None) -> dict:
    ctx: dict = {"host_name": host_name, "served": served, "errors": []}
    try:
        client.run_chat(base_url, served, [{"role": "user", "content": "Say OK."}], max_tokens=8, api_key=api_key, timeout=900)
    except Exception as e:   # the run itself will surface a dead endpoint; here it only means no settings snapshot
        ctx["errors"].append(f"warm-up: {e}"[:200])
    try:
        h = hosts.host_of(hosts.load(host_name))
        ctx["h"] = h
        ctx["start"] = _settings(h, served)
        f = f"/tmp/llmbox-telemetry-{os.getpid()}-{int(time.time())}.csv"
        ctx["telemetry"] = h.agent("telemetry-start", f, "5", timeout=60)
    except Exception as e:
        ctx["errors"].append(f"begin: {e}"[:300])
    return ctx


def diff_argv(r: dict, argv: list[str]) -> list[dict]:
    """Flag-level differences between a recipe and a server's real argv (argv[0] = the binary)."""
    want, have = rc._flags(rc.server_args(rc._merge(rc.DEFAULTS, r), port="0")), rc._flags(argv[1:])
    out = []
    for f in sorted(set(want) | set(have)):
        if f == "--port":
            continue
        w, v = want.get(f, []), have.get(f, [])
        if f in rc.REPEATABLE:
            w, v = sorted(w), sorted(v)
        same = len(w) == len(v) and all(len(x) == len(y) and all(rc._same(a, b) for a, b in zip(x, y)) for x, y in zip(w, v))
        if not same:
            out.append({"flag": f, "recipe": [" ".join(x) for x in w], "run": [" ".join(x) for x in v],
                        "class": "speed" if f in SPEED_ONLY else "quality"})
    rs = rc._merge(rc.DEFAULTS, r)["runtime"]["server"]
    if argv and rs and os.path.basename(argv[0]) != os.path.basename(rs):
        out.append({"flag": "(binary)", "recipe": [rs], "run": [argv[0]], "class": "quality"})
    return out


def end(ctx: dict, recipe: dict | None) -> dict:
    """The record's 'runtime', 'telemetry' and model hash."""
    h = ctx.get("h")
    out: dict = {"runtime": {}, "telemetry": None, "sha256": None, "errors": list(ctx.get("errors", []))}
    if not h:
        return out
    try:
        fin = _settings(h, ctx["served"])
        st = ctx.get("start") or {}
        argv = st.get("argv") or fin.get("argv")
        props = st.get("props") or fin.get("props") or {}
        consistent = bool(st.get("argv")) and st.get("argv") == fin.get("argv") and (st.get("props") or {}).get("params") == (fin.get("props") or {}).get("params")
        rt = {"llama_cpp_build": props.get("build_info"), "argv": argv, "n_ctx": props.get("n_ctx"), "slots": props.get("total_slots"),
              "sampling_defaults": props.get("params"), "chat_template_sha": props.get("chat_template_sha"),
              "model_path": props.get("model_path"), "settings_consistent": consistent}
        if recipe and recipe.get("model") and argv:
            d = diff_argv(recipe, argv)
            rt["diff_vs_recipe"] = d
            rt["variant"] = "same recipe" if not d else ("speed-only changes" if all(x["class"] == "speed" for x in d) else "quality variant")
        out["runtime"] = rt
    except Exception as e:
        out["errors"].append(f"settings: {e}"[:300])
    try:
        t = ctx.get("telemetry") or {}
        if t.get("pid"):
            out["telemetry"] = h.agent("telemetry-stop", t["file"], str(t["pid"]), timeout=120)
    except Exception as e:
        out["errors"].append(f"telemetry: {e}"[:300])
    try:
        path = (out["runtime"] or {}).get("model_path") or ((recipe or {}).get("model") or {}).get("path")
        if path:
            out["sha256"] = h.agent("sha256", path, timeout=1800)
    except Exception as e:
        out["errors"].append(f"sha256: {e}"[:300])
    return out
