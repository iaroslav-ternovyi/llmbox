"""Result records: one JSON document per measurement, in a schema meant to be shared (the future public leaderboard).

Everything needed to reproduce and compare is inside: host fingerprint, runtime build, exact model file (repo, file,
sha256), the fully resolved recipe, and the measured numbers next to the prediction.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid

from . import __version__
from .hosts import HOME

SCHEMA = "llmbox.result/1"


def host_fingerprint(prof: dict) -> dict:
    hw = prof["hw"]
    g = hw["gpus"][0] if hw["gpus"] else {}
    fp = {"gpu": g.get("name"), "vram_gib": round(g.get("vram_mib", 0) / 1024, 1), "gpu_driver": g.get("driver"),
          "gpu_power_limit_w": g.get("power_limit_w"), "cpu": hw["cpu"]["model"], "threads": hw["cpu"]["threads"],
          "ram_gib": round(hw["ram_mib"] / 1024, 1), "ram_read_gbs": (prof.get("ram_bw") or {}).get("ram_read_gbs"),
          "os": hw["os"]}
    fp["id"] = hashlib.sha256(json.dumps({k: fp[k] for k in ("gpu", "vram_gib", "cpu", "ram_gib")}, sort_keys=True).encode()).hexdigest()[:12]
    return fp


def new(kind: str, prof: dict, recipe: dict | None = None, model: dict | None = None, runtime: dict | None = None) -> dict:
    return {"schema": SCHEMA, "id": str(uuid.uuid4()), "kind": kind, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": {"name": "llmbox", "version": __version__}, "host": host_fingerprint(prof),
            "runtime": runtime or {}, "model": model or {}, "recipe": recipe or {}}


def save(host: str, rec: dict) -> str:
    d = os.path.join(HOME, "results", host)
    os.makedirs(d, exist_ok=True)
    name = f"{rec['created'][:19].replace(':', '')}-{rec['kind']}-{(rec.get('recipe') or {}).get('id', 'x')}.json"
    path = os.path.join(d, name)
    json.dump(rec, open(path, "w"), indent=1)
    try:   # into the results database right away (readers would take it in on their next look anyway)
        from . import db
        db.sync()
    except Exception:
        pass
    return path


_FROZEN: dict | None = None


@contextlib.contextmanager
def frozen():
    """Inside: every host's results are read once and handed out as shallow copies. A site build reads the same
    records dozens of times (rows, pools, pages) and nothing writes results meanwhile; callers only replace top-level
    keys of a record, never edit nested ones, so shallow copies keep the cached records clean."""
    global _FROZEN
    outer = _FROZEN
    _FROZEN = {} if outer is None else outer
    try:
        yield
    finally:
        _FROZEN = outer


def files(host: str | None = None) -> list[tuple[str, dict]]:
    """(path, record) of every result, per host directory in file-name order: from the results database
    (llmbox/db.py), or straight from the JSON files with LLMBOX_NO_DB=1."""
    if _FROZEN is not None:
        if host not in _FROZEN:
            _FROZEN[host] = _files(host)
        return [(p, dict(r)) for p, r in _FROZEN[host]]
    return _files(host)


def _files(host: str | None = None) -> list[tuple[str, dict]]:
    if not os.environ.get("LLMBOX_NO_DB"):
        from . import db
        return db.files(host)
    root = os.path.join(HOME, "results")
    out = []
    for h in ([host] if host else sorted(os.listdir(root)) if os.path.isdir(root) else []):
        d = os.path.join(root, h)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.endswith(".json"):
                try:
                    out.append((os.path.join(d, f), json.load(open(os.path.join(d, f)))))
                except ValueError:
                    continue
    return out


def load_all(host: str | None = None) -> list[dict]:
    return [rec for _p, rec in files(host)]
