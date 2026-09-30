"""A small persistent cache for pure, slow results: loop scans of saved thinking (site pages flag looped replies),
bootstrap intervals of saved runs. One JSON file per namespace in ~/.llmbox/cache; a key says everything the value
depends on (a file's size and mtime, the rows and weights), so a stale entry is never read - it just stops being hit."""
from __future__ import annotations

import atexit
import hashlib
import json
import os

from .hosts import HOME

DIR = os.path.join(HOME, "cache")
_MEM: dict = {}
_DIRTY: set = set()


def _load(ns: str) -> dict:
    if ns not in _MEM:
        try:
            _MEM[ns] = json.load(open(os.path.join(DIR, ns + ".json")))
        except (OSError, ValueError):
            _MEM[ns] = {}
    return _MEM[ns]


def key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def memo(ns: str, k: str, fn):
    """fn() once per key, kept across processes."""
    d = _load(ns)
    if k not in d:
        d[k] = fn()
        _DIRTY.add(ns)
    return d[k]


@atexit.register
def save() -> None:
    for ns in list(_DIRTY):
        try:
            os.makedirs(DIR, exist_ok=True)
            tmp = os.path.join(DIR, f".{ns}.{os.getpid()}.json")
            json.dump(_MEM[ns], open(tmp, "w"))
            os.replace(tmp, os.path.join(DIR, ns + ".json"))
            _DIRTY.discard(ns)
        except OSError:
            pass
