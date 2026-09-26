"""`llmbox speed <recipe>`: measure a recipe (or a variant of it) on its host and compare with the prediction."""
from __future__ import annotations

import copy
import json
import os
import statistics

from . import estimate as E
from . import hosts, recipe, results
from .gguf import GGUFHeader, Tensor


def set_path(r: dict, dotted: str, value: str) -> None:
    keys = dotted.split(".")
    d = r
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    try:
        v = json.loads(value)
    except ValueError:
        v = value
    d[keys[-1]] = v


def model_files(path: str) -> list[str]:
    if "-00001-of-" in path:
        n = int(path.split("-of-")[1][:5])
        return [path.replace("-00001-of-", f"-{i:05d}-of-") for i in range(1, n + 1)]
    return [path]


def shape_on_host(host, path: str) -> E.ModelShape:
    hs = []
    for p in model_files(path):
        d = host.agent("gguf-header", p)
        hs.append(GGUFHeader(version=d["version"], kv=d["kv"], arrays={k: tuple(v) for k, v in d["arrays"].items()},
                             tensors=[Tensor(n, tuple(s), t, o, b) for n, s, t, o, b in d["tensors"]], data_start=d["data_start"]))
    return E.analyze(hs)


def measure(host_name: str, rid: str, overrides: list[str] | None = None, depths: list[int] | None = None,
            unload: bool = False, save: bool = True, sampling: dict | None = None, repeats: int = 2) -> dict:
    prof = hosts.load(host_name)
    h = hosts.host_of(prof)
    r = recipe.load(host_name, rid)
    for o in overrides or []:
        k, v = o.split("=", 1)
        set_path(r, k, v)
    r = recipe._merge(recipe.DEFAULTS, r)
    if unload:
        h.agent("unload")
    args = recipe.server_args(r)
    i = args.index("--port")
    args = args[:i] + args[i + 2:]
    spec = {"server": r["runtime"]["server"], "args": args, "affinity": r["runtime"]["cpu_affinity"],
            "model_files": model_files(r["model"]["path"]), "headroom_mib": r["placement"]["cache_ram_headroom_mib"],
            "depths": depths or [32000], "gen_tokens": 400, "prefill_tokens": 12000, "repeats": repeats, "sampling": sampling or {}}
    m = h.agent("probe-server", json.dumps(spec), timeout=3600)
    rec = results.new("speed", prof, recipe=r, model={k: r["model"].get(k) for k in ("hf_repo", "file", "path", "sha256")})
    rec["overrides"] = overrides or []
    rec["raw"] = m
    if "error" in m:
        rec["error"] = m["error"]
    else:
        dec = [x["tps"] for x in m["decode"] if x.get("tps")]
        acc = [(x["draft_accepted"] or 0) / x["draft_n"] for x in m["decode"] if x.get("draft_n")]
        rec["speed"] = {"decode_tps": round(statistics.median(dec), 1) if dec else None,
                        "draft_acceptance": round(statistics.mean(acc), 3) if acc else None,
                        "prefill_tps": round(m["prefill"]["tps"] or 0, 1),
                        "depth": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()} for d in m["depth"]],
                        "load_seconds": m.get("load_seconds"), "cache_ram_mib": m.get("cache_ram_mib")}
        try:  # prediction for the same config (speculative decoding off in the model; flag it)
            shape = shape_on_host(h, r["model"]["path"])
            p = E.plan(shape, hosts.spec(prof), ctx=r["placement"]["ctx"], kv_type=r["placement"]["kv_type"],
                       ubatch=r["placement"]["ubatch"], depth=(depths or [32000])[0])
            rec["prediction"] = {"decode_tps_no_spec": round(p.decode_tps, 1), "decode_tps_at_depth_no_spec": round(p.decode_tps_at_depth, 1),
                                 "gpu_expert_frac": round(p.gpu_expert_frac, 3), "speculative": bool(r["speculative"]["type"])}
        except Exception as e:  # prediction is a bonus, never fail a measurement on it
            rec["prediction"] = {"error": str(e)}
    if save:
        rec["path"] = results.save(host_name, rec)
    return rec
