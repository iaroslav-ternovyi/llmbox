"""What people add to a model besides its measurements: settings they measured (variants of the published recipe),
votes on those settings and on llmbox's own, and comments. The intake keeps the comments and votes (server.py,
intake.db); the model pages show them, read from the API in the visitor's browser (assets/social.js).

A shared setting is never a claim: it is a run (`llmbox test <id> --set key=value`, signed in) whose overrides change
the published recipe, filed like any other run. Only the keys in SHAREABLE travel: what changes speed, memory or the
model's answers. The rest of a recipe (paths, the engine, free server flags) is the machine's own or could do harm when
copied, so a run that overrides it is filed but not offered to others.
"""
from __future__ import annotations

import hashlib
import json
import os
import re


def _int(lo: int, hi: int):
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _num(lo: float, hi: float):
    return lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def _bool(v) -> bool:
    return isinstance(v, bool)


def _one(*xs):
    return lambda v: isinstance(v, str) and v in xs


KV_TYPES = ("f32", "f16", "bf16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0", "iq4_nl")
SHAREABLE = {
    "placement.ctx": _int(2048, 4_194_304), "placement.kv_type": _one(*KV_TYPES),
    "placement.batch": _int(16, 65536), "placement.ubatch": _int(16, 65536),
    "placement.fit": _bool, "placement.fit_target_mib": _int(0, 65536), "placement.n_cpu_moe": _int(0, 1024),
    "placement.flash_attn": _bool, "placement.kv_offload": _bool, "placement.cache_reuse": _int(0, 1_000_000),
    "runtime.threads": _int(0, 1024),
    "speculative.type": _one("", "draft-mtp"), "speculative.draft_max": _int(0, 64),
    "sampling.temp": _num(0, 5), "sampling.top_p": _num(0, 1), "sampling.top_k": _int(0, 1000), "sampling.min_p": _num(0, 1),
    "sampling.presence_penalty": _num(-2, 2), "sampling.repeat_penalty": _num(0, 5),
    "antiloop.reasoning_budget": _int(-1, 1_000_000),
    "chat.template_kwargs.enable_thinking": _bool, "chat.template_kwargs.reasoning_effort": _one("low", "medium", "high"),
}
# keys that change the answers, not only the speed: a variant with one of them has a score only from its own quality run
QUALITY = ("placement.kv_type", "sampling.", "antiloop.", "chat.")
LABELS = {"placement.ctx": "context", "placement.kv_type": "KV cache", "placement.batch": "batch", "placement.ubatch": "ubatch",
          "placement.fit": "auto placement", "placement.fit_target_mib": "VRAM left free (MiB)", "placement.n_cpu_moe": "MoE layers in RAM",
          "placement.flash_attn": "flash attention", "placement.kv_offload": "KV on the GPU", "placement.cache_reuse": "cache reuse",
          "runtime.threads": "threads", "speculative.type": "speculative", "speculative.draft_max": "draft tokens",
          "sampling.temp": "temperature", "sampling.top_p": "top-p", "sampling.top_k": "top-k", "sampling.min_p": "min-p",
          "sampling.presence_penalty": "presence penalty", "sampling.repeat_penalty": "repeat penalty",
          "antiloop.reasoning_budget": "reasoning budget", "chat.template_kwargs.enable_thinking": "thinking",
          "chat.template_kwargs.reasoning_effort": "reasoning effort"}
MAX_OVERRIDES = 32


def parse(overrides) -> tuple[dict, list[str]]:
    """A run's overrides (["placement.ubatch=1024", ...], as `--set` takes them) -> (the shareable ones as {key: value},
    the keys that cannot be shared). Values are read as `--set` reads them (JSON, else the text)."""
    out, bad = {}, []
    if not isinstance(overrides, list):
        return {}, ["(not a list)"]
    for o in overrides[:MAX_OVERRIDES]:
        if not isinstance(o, str) or "=" not in o or len(o) > 200:
            bad.append(str(o)[:40])
            continue
        k, v = o.split("=", 1)
        try:
            v = json.loads(v)
        except ValueError:
            pass
        if k in SHAREABLE and SHAREABLE[k](v):
            out[k] = v
        else:
            bad.append(k[:60])
    return out, bad


def key_of(rid: str, settings: dict) -> str:
    """A variant's id: the recipe and its settings (two people who set the same values share one variant)."""
    return "v" + hashlib.sha256(json.dumps([rid, sorted(settings.items())]).encode()).hexdigest()[:10]


def changes_answers(settings: dict) -> bool:
    return any(k.startswith(QUALITY) for k in settings)


def flags(settings: dict) -> list[str]:
    """The `--set` arguments that reproduce a variant (what its COPY button gives)."""
    return [f"{k}={json.dumps(v) if not isinstance(v, str) else v}" for k, v in sorted(settings.items())]


def describe(settings: dict) -> str:
    def val(v):
        return "on" if v is True else "off" if v is False else "none" if v == "" else str(v)
    return " · ".join(f"{LABELS.get(k, k)} {val(v)}" for k, v in sorted(settings.items()))


def variants(host: str = "community") -> dict:
    """{rid: [variant]} from the signed-in runs with shareable overrides, newest first. A variant:
    {key, rid, settings, answers (it changes the answers), runs: [{by, machine, cls, t2, t32, pp, score, when, sid,
    outlier, kind}], by (who measured it first), first}. Anonymous runs and overrides off the list are not offered."""
    from . import hwclass, results
    out: dict = {}
    for _p, rec in results.files(host):
        if rec.get("kind") not in ("speed", "suite") or not rec.get("overrides"):
            continue
        sub = rec.get("submission") or {}
        if not sub.get("user"):
            continue
        settings, bad = parse(rec.get("overrides"))
        if bad or not settings:
            continue
        rid = (rec.get("recipe") or {}).get("id")
        if not isinstance(rid, str):
            continue
        k = key_of(rid, settings)
        v = out.setdefault(rid, {}).setdefault(k, {"key": k, "rid": rid, "settings": settings, "answers": changes_answers(settings), "runs": []})
        h = rec.get("host") or {}
        cls = h.get("class") or hwclass.of_host(h)
        sp = rec.get("speed") or {}
        deep = next((d.get("decode_tps") for d in sp.get("depth") or [] if 24000 <= (d.get("depth") or 0) < 48000), None)
        v["runs"].append({"by": sub["user"], "machine": hwclass.display_class(cls) or h.get("gpu") or h.get("cpu") or "?", "cls": cls,
                          "t2": sp.get("decode_tps") if rec["kind"] == "speed" else None, "t32": deep if rec["kind"] == "speed" else None,
                          "pp": sp.get("prefill_tps") if rec["kind"] == "speed" else None,
                          "score": (rec.get("summary") or {}).get("capability") if rec["kind"] == "suite" else None,
                          "when": str(rec.get("created") or "")[:19], "sid": sub.get("id"), "kind": rec["kind"],
                          "outlier": "outlier" in (sub.get("flags") or [])})
    res = {}
    for rid, vs in out.items():
        for v in vs.values():
            v["runs"].sort(key=lambda r: r["when"])
            v["by"], v["first"] = v["runs"][0]["by"], v["runs"][0]["when"]
        res[rid] = sorted(vs.values(), key=lambda v: v["first"], reverse=True)
    return res


def testers(host: str = "community") -> dict:
    """{(handle, rid): [machine names]}: who measured which model on what (the 'tested on' tag of a comment)."""
    from . import hwclass, results
    out: dict = {}
    for _p, rec in results.files(host):
        u = (rec.get("submission") or {}).get("user")
        rid = (rec.get("recipe") or {}).get("id")
        if not u or not isinstance(rid, str) or rec.get("kind") not in ("speed", "suite"):
            continue
        h = rec.get("host") or {}
        name = hwclass.display_class(h.get("class") or hwclass.of_host(h)) or h.get("gpu") or "other machine"
        ms = out.setdefault((u, rid), [])
        if name not in ms:
            ms.append(name)
    return out


# ---- comments ----------------------------------------------------------------------------------------------------------

MAX_COMMENT = 2000
MAX_LINES = 40
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


def clean(body) -> str | None:
    """A comment as it is kept: plain text (the page shows it as text, never as HTML), no control or direction
    characters, at most two blank lines in a row; None when it is empty or too long."""
    if not isinstance(body, str):
        return None
    t = _CTRL.sub("", body.replace("\r\n", "\n").replace("\r", "\n"))
    t = re.sub(r"\n{3,}", "\n\n", "\n".join(x.rstrip() for x in t.split("\n"))).strip()
    if not t or len(t) > MAX_COMMENT or t.count("\n") >= MAX_LINES:
        return None
    return t


def rid_known(rid: str) -> bool:
    """A model page exists for it (a published recipe of the reference box)."""
    from .hosts import HOME
    return isinstance(rid, str) and bool(re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,79}", rid)) and \
        os.path.exists(os.path.join(HOME, "recipes", "box", f"{rid}.toml"))
