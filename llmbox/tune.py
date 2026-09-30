"""`llmbox tune <recipe>`: measure the speed knobs the formula cannot settle, on the real box, and keep what wins.

`fit` places the model from its header; tune then tries, one at a time, the knobs whose effect depends on the machine
and the model's text: speculative decoding (on / off / draft depth - it pays only when the verification batch does not
pull many more experts from RAM), the VRAM margin left by --fit, and the prompt batch. Each variant is a fresh
llama-server measured with the recipe's own sampling (acceptance at temperature 0 flatters MTP). The winners are
then combined and measured once more.

A variant wins on the time of a typical agent step: read 3k new tokens, write 600 with 32k already in context. It must
beat the baseline by more than the run-to-run noise, measured by running the baseline twice. Every knob here is
speed-only; the context size is measured as a separate "fast profile" because it limits the longest document.
The box must be idle: tune unloads llama-swap's model and starts its own server on a free port.
"""
from __future__ import annotations

import json
import os
import time

from . import fit as F
from . import recipe as rc
from .bench import TYPICAL_STEP
from .hosts import HOME

DEPTH = 32000
MIN_GAIN = 0.03


def variants(r: dict, shape, cores: int = 0) -> list[tuple[str, list[str], bool]]:
    """(name, overrides, speed-only) for this recipe. Speed-only variants may be chosen; the others are reported.
    cores: the host's CPU cores, for the thread count of a MoE whose experts compute on the CPU."""
    out = [("baseline", [], True)]
    sp, pl = r["speculative"], r["placement"]
    if sp["type"]:
        out.append(("speculative off", ['speculative.type="none"'], True))
        for n in (1, 3):
            if n != sp["draft_max"]:
                out.append((f"draft {n}", [f"speculative.draft_max={n}"], True))
    elif shape.n_mtp_layers:
        out += [("MTP draft 2", ['speculative.type="draft-mtp"', "speculative.draft_max=2"], True)]
    if pl["fit"] and shape.is_moe:
        for m in (128, 512):
            if m != pl["fit_target_mib"]:
                out.append((f"VRAM margin {m} MiB", [f"placement.fit_target_mib={m}"], True))
    if not pl["fit"] and pl.get("n_cpu_moe"):   # explicit placement: one or two more expert layers on the card, or one fewer
        for d in (-2, -1, 1):
            if 0 < pl["n_cpu_moe"] + d <= shape.n_layers:
                out.append((f"experts of {pl['n_cpu_moe'] + d} layers in RAM", [f"placement.n_cpu_moe={pl['n_cpu_moe'] + d}"], True))
    if cores > 2 and shape.is_moe and (pl["fit"] or pl.get("n_cpu_moe")):
        # experts in RAM: decode is bound by RAM speed, which a few cores already saturate; one thread per core also
        # competes with the thread that drives the card (K2-Horizon on 8 cores, 2026-09-30: 4-7 threads 28.9 tok/s, 8 26.1)
        now = r["runtime"].get("threads") or cores
        for t in sorted({cores // 2, cores - 1, cores} - {now}):
            out.append((f"{t} CPU threads", [f"runtime.threads={t}"], True))
    if pl["ubatch"] != 1024:
        out.append(("prompt batch 1024", ["placement.ubatch=1024", "placement.batch=2048"], True))
    ctx = pl["ctx"] or shape.context_length
    if ctx and ctx > 131072:
        out.append((f"context {131072 // 1024}k (fast profile)", ["placement.ctx=131072"], False))
    if shape.is_moe and pl.get("kv_offload", True):
        # KV cache in system RAM: frees the card for experts (faster short answers) but the CPU does the attention, so
        # long prompts get slower; measured at the usual depth only, so reported for a decision, never chosen
        out.append(("KV cache in RAM (--no-kv-offload)", ["placement.kv_offload=false"], False))
    return out


def cores_of(host: str) -> int:
    from . import hosts
    return int(((hosts.load(host).get("hw") or {}).get("cpu") or {}).get("threads") or 0)


def _step_s(m: dict) -> float | None:
    sp = m.get("speed") or {}
    deep = next((d.get("decode_tps") for d in sp.get("depth") or [] if d.get("decode_tps")), None) or sp.get("decode_tps")
    if not deep or not sp.get("prefill_tps"):
        return None
    return TYPICAL_STEP["new_prompt_tokens"] / sp["prefill_tps"] + TYPICAL_STEP["output_tokens"] / deep


def _row(name: str, m: dict) -> dict:
    sp = m.get("speed") or {}
    deep = next((d.get("decode_tps") for d in sp.get("depth") or [] if d.get("decode_tps")), None)
    return {"name": name, "overrides": m.get("overrides", []), "error": m.get("error"), "decode": sp.get("decode_tps"),
            "deep": deep, "prefill": sp.get("prefill_tps"), "acceptance": sp.get("draft_acceptance"), "step_s": _step_s(m),
            "load_s": sp.get("load_seconds")}


def run(host: str, rid: str, out=print, measure=None) -> dict:
    from . import speed
    measure = measure or speed.measure
    r = rc.load(host, rid)
    from . import hosts
    shape = F.shape_for(r, host=hosts.host_of(hosts.load(host)))
    sampling = {k: v for k, v in {"temperature": r["sampling"].get("temp"), "top_p": r["sampling"].get("top_p"),
                                   "top_k": r["sampling"].get("top_k"), "min_p": r["sampling"].get("min_p")}.items() if v is not None}
    todo = variants(r, shape, cores=cores_of(host))
    rows = []

    def one(name: str, ov: list[str]) -> dict:
        t = time.time()
        try:
            m = measure(host, rid, overrides=ov, depths=[DEPTH], unload=True, save=True, sampling=sampling)
        except RuntimeError as e:   # the server died under this variant (gemma4, 2026-09-29): a failed variant, not a failed tune
            m = {"overrides": ov, "error": str(e).splitlines()[-1][-160:]}
        row = _row(name, m)
        rows.append(row)
        out(f"  {name:32s} " + (f"ERROR {row['error']}" if row["error"] else
            f"{row['decode'] or 0:6.1f} tok/s  {row['deep'] or 0:6.1f} @32k  prefill {row['prefill'] or 0:6.0f}  "
            f"step {row['step_s'] or 0:5.2f} s" + (f"  accept {row['acceptance'] * 100:.0f}%" if row["acceptance"] else "")
            + f"   ({time.time() - t:.0f} s)"))
        return row

    out(f"tune {rid} on {host}: {len(todo)} variants + a second baseline and the combination, sampling {sampling}")
    base = one("baseline", [])
    if base["error"]:
        out(f"stopped: {base['error']}")
        return {"rid": rid, "rows": rows, "error": base["error"]}
    tried = [(n, ov, ok, one(n, ov)) for n, ov, ok in todo[1:]]
    base2 = one("baseline again", [])
    b = [x["step_s"] for x in (base, base2) if x["step_s"]]
    ref = sum(b) / len(b)
    noise = abs(b[0] - b[-1]) / ref if len(b) == 2 else 0.0
    bar = max(MIN_GAIN, 2 * noise)
    wins = [(n, ov, row) for n, ov, ok, row in tried if ok and row["step_s"] and row["step_s"] < ref * (1 - bar)]
    # one winner per knob: the best speculative setting, the best margin, the best batch
    knob = lambda ov: "speculative" if ov[0].startswith("speculative") else ov[0].split("=")[0]
    best: dict = {}
    for n, ov, row in sorted(wins, key=lambda w: w[2]["step_s"]):
        best.setdefault(knob(ov), (n, ov, row))
    chosen = [ov for _, ov, _ in best.values()]
    combo = None
    if len(chosen) > 1:
        combo = one("combined winners", [o for ov in chosen for o in ov])
    final = combo if combo and combo["step_s"] and combo["step_s"] < min(w[2]["step_s"] for w in best.values()) else \
        (min(best.values(), key=lambda w: w[2]["step_s"])[2] if best else None)
    res = {"rid": rid, "host": host, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sampling": sampling, "noise": round(noise, 3),
           "bar": round(bar, 3), "baseline_step_s": round(ref, 3), "rows": rows,
           "chosen": final["overrides"] if final else [], "gain": round(1 - final["step_s"] / ref, 3) if final else 0.0}
    d = os.path.join(HOME, "tuned", host)
    os.makedirs(d, exist_ok=True)
    json.dump(res, open(os.path.join(d, f"{rid}.json"), "w"), indent=1)
    out(f"\nnoise {noise * 100:.1f}% -> a variant must be {bar * 100:.0f}% faster per agent step")
    if final:
        out(f"best: {' '.join(final['overrides'])}  step {final['step_s']:.2f} s vs {ref:.2f} s  (-{res['gain'] * 100:.0f}%)")
        out("apply to the recipe's hardware layer (llama-swap launcher/entry change: box must be idle, your call)")
    else:
        out("the recipe's settings are already the fastest measured")
    fast = next(((n, ov, row) for n, ov, ok, row in tried if not ok and ov[0].startswith("placement.ctx=")), None)   # KV-in-RAM is no daily profile
    if fast and fast[2]["step_s"]:
        daily = one("fast profile + winners", fast[1] + res["chosen"]) if res["chosen"] else fast[2]
        res["fast_profile"] = {"overrides": fast[1] + res["chosen"], "step_s": daily["step_s"], "decode": daily["decode"],
                               "gain": round(1 - daily["step_s"] / ref, 3) if daily["step_s"] else None}
        json.dump(res, open(os.path.join(d, f"{rid}.json"), "w"), indent=1)
        if daily["step_s"]:
            out(f"daily profile ({' '.join(res['fast_profile']['overrides'])}): {daily['decode'] or 0:.0f} tok/s, step {daily['step_s']:.2f} s "
                f"({res['fast_profile']['gain'] * 100:+.0f}%); documents over ~110k tokens no longer fit")
    return res


# ---- apply: put the measured winners into the recipe and the llama-swap entry ---------------------------------------

FLAG = {"speculative.type": "--spec-type", "speculative.draft_max": "--spec-draft-n-max", "placement.fit_target_mib": "--fit-target",
        "placement.ubatch": "-ub", "placement.batch": "-b", "placement.ctx": "-c", "runtime.threads": "--threads"}


SWITCH = {"placement.kv_offload": ("--kv-offload", "--no-kv-offload")}   # boolean settings: a flag without a value


def flags_of(overrides: list[str]) -> list[str]:
    out = []
    for o in overrides:
        k, v = o.split("=", 1)
        if k in SWITCH:
            out.append(SWITCH[k][0] if v.lower() == "true" else SWITCH[k][1])
            continue
        out += [FLAG[k], json.loads(v) if v.startswith('"') else v]
    return [str(x) for x in out]


def new_cmd_line(line: str, add: list[str], drop: list[str]) -> str:
    """`    cmd: <launcher> ${PORT} [extra]` with the flags of a previous tune removed and the new ones appended
    (llama.cpp takes the last value of a repeated flag, so appended flags win over the launcher's)."""
    head, _, rest = line.partition("cmd:")
    toks = rest.split()
    i = 0
    while i < len(drop):   # drop the flags a previous tune applied: flag/value pairs, or a lone switch
        if drop[i] in {f for pair in SWITCH.values() for f in pair}:
            if drop[i] in toks:
                toks.remove(drop[i])
            i += 1
            continue
        for j in range(len(toks) - 1):
            if i + 1 < len(drop) and toks[j] == drop[i] and toks[j + 1] == drop[i + 1]:
                del toks[j:j + 2]
                break
        i += 2
    return f"{head}cmd: {' '.join(toks + add)}"


def apply(host: str, rid: str, out=print, overrides: list[str] | None = None, unload: bool = True) -> bool:
    """Write the tuned overrides into the recipe file and the host's llama-swap entry, then verify with recipe check.
    The host must be idle (a config change makes llama-swap reload). Backups: config.yaml.bak-<ts>, <id>.toml.bak-<ts>."""
    import shlex
    import tomllib
    from . import hosts, speed
    path = os.path.join(HOME, "tuned", host, f"{rid}.json")
    res = json.load(open(path)) if os.path.exists(path) else {}
    ov = overrides if overrides is not None else res.get("chosen") or []
    if not ov:
        out(f"{rid}: nothing to apply")
        return True
    prof = hosts.load(host)
    h = hosts.host_of(prof)
    # right after a tune the box still holds what the measurements left loaded: unload it and wait for the probe
    # servers to exit before deciding the host is busy (2026-09-27: cyber-tiel's -9% was refused as "host busy")
    busy = hosts.free_up(h) if unload else h.agent("busy", timeout=60)
    if busy.get("busy"):
        out(f"{rid}: host busy - not touching the llama-swap config")
        return False
    add, drop = flags_of(ov), res.get("applied_flags") or []
    cfg = (prof["hw"].get("llama_swap") or {}).get("config") or os.path.expanduser(rc.LLAMA_SWAP_CONFIG)
    text = h.run(f"cat {shlex.quote(cfg)}", timeout=30).stdout
    lines = text.split("\n")
    try:
        i = lines.index(f"  {rid}:")
        j = next(k for k in range(i + 1, len(lines)) if lines[k].strip().startswith("cmd:"))
    except (ValueError, StopIteration):
        out(f"{rid}: no llama-swap entry")
        return False
    lines[j] = new_cmd_line(lines[j], add, drop)
    ts = time.strftime("%Y%m%d-%H%M%S")
    new = "\n".join(lines)
    r = h.run(f"cp {shlex.quote(cfg)} {shlex.quote(cfg)}.bak-{ts} && cat > {shlex.quote(cfg)}.tmp && mv {shlex.quote(cfg)}.tmp {shlex.quote(cfg)}",
              input=new, timeout=30)
    if r.returncode:
        out(f"{rid}: config write failed: {r.stderr[-200:]}")
        return False
    # the recipe file itself (its own keys only; `extends` stays)
    rp = os.path.join(rc.recipes_dir(host), f"{rid}.toml")
    own = tomllib.load(open(rp, "rb"))
    open(rp + f".bak-{ts}", "wb").write(open(rp, "rb").read())
    for o in ov:
        k, v = o.split("=", 1)
        speed.set_path(own, k, v)
    ext = own.pop("extends", None)
    open(rp, "w").write((f'extends = "{ext}"\n' if ext else "") + F.to_toml(own))
    d = rc.diff(rc.load(host, rid), rc.live_argv(h, rid))
    if d:
        h.run(f"cp {shlex.quote(cfg)}.bak-{ts} {shlex.quote(cfg)}", timeout=30)
        open(rp, "wb").write(open(rp + f".bak-{ts}", "rb").read())
        out(f"{rid}: check failed after applying, both restored:\n  " + "\n  ".join(d))
        return False
    if res:
        res["applied_flags"], res["applied_at"] = add, ts
        json.dump(res, open(path, "w"), indent=1)
    out(f"{rid}: applied {' '.join(add)} (recipe + llama-swap entry, check OK; backups .bak-{ts})")
    return True
