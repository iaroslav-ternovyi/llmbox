"""Result records: one JSON document per measurement, in a schema meant to be shared (the future public leaderboard).

Everything needed to reproduce and compare is inside: host fingerprint, runtime build, exact model file (repo, file,
sha256), the fully resolved recipe, and the measured numbers next to the prediction.
"""
from __future__ import annotations

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
    return path


def load_all(host: str | None = None) -> list[dict]:
    root = os.path.join(HOME, "results")
    out = []
    for h in ([host] if host else (os.listdir(root) if os.path.isdir(root) else [])):
        d = os.path.join(root, h)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.endswith(".json"):
                out.append(json.load(open(os.path.join(d, f))))
    return out
