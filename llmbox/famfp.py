"""Per-family fingerprints: which answers from older suite versions still count for the current one.

A suite version changes some task families and leaves the rest alone (v0.11: the tools block and new levels 7-8). The
answers a model gave to an unchanged family are as good as new ones, so the IRT pools them across versions instead of
asking every model to run everything again. A family (block.kind.Llevel) is unchanged when

- the tasks it generates are the same: messages, tool schemas and task metadata for three fixed seeds, and
- its grading is the same: common.py (shared answer parsing) and the module's GRADING number, which a grader change
  bumps; for blocks graded on a tool world or a workspace (tools, agentic) also the generator's own source, since their
  checks live inside the generator and cannot be re-run on a saved answer.

Old versions are fingerprinted from their queue snapshots (~/.llmbox/snapshots/<tag>) in a subprocess, with THIS
module's code run against THAT version's suite package, and cached per suite content hash (~/.llmbox/irt/fp-<hash>.json).
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

from .hosts import HOME

SEEDS = (1, 7001, 11001)
WORLD_GRADED = ("tools", "agentic")

SCRIPT = r'''
import hashlib, inspect, json, os, sys
from llmbox import suite
from llmbox.suite import common

def clean(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0])) if not callable(x)}
    return type(v).__name__        # objects (worlds, workspaces): their content shows up in the messages

base = hashlib.sha256(open(common.__file__, "rb").read()).hexdigest()
out = {}
for fam in json.loads(sys.argv[1]):
    try:
        block, kind, lv = fam.rsplit(".", 2)
        gen = suite.BLOCKS[block][kind]
        h = hashlib.sha256(base.encode())
        mod = inspect.getmodule(gen)
        h.update(str(getattr(mod, "GRADING", 0)).encode())
        if block in %(world)r:
            h.update(inspect.getsource(gen).encode())
        for seed in %(seeds)r:
            it = gen(seed, int(lv[1:]))
            h.update(json.dumps(clean([it.messages, it.tools, it.meta]), sort_keys=True).encode())
        out[fam] = h.hexdigest()[:12]
    except Exception as e:           # a family this version does not have
        out[fam] = None
print(json.dumps(out))
''' % {"world": WORLD_GRADED, "seeds": SEEDS}


def _run(families: list[str], pythonpath: str | None) -> dict:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    cwd = pythonpath or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = subprocess.run([sys.executable, "-c", SCRIPT, json.dumps(sorted(families))], capture_output=True, text=True,
                       env=env, cwd=cwd, timeout=1800)
    if p.returncode:
        raise RuntimeError(f"fingerprint run failed: {p.stderr[-400:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def _cache_path(content_hash: str) -> str:
    return os.path.join(HOME, "irt", f"fp-{content_hash}.json")


def snapshot_for(content_hash: str) -> str | None:
    """The queue snapshot whose suite has this content hash (cached map ~/.llmbox/irt/snapshots.json)."""
    mp = os.path.join(HOME, "irt", "snapshots.json")
    m = json.load(open(mp)) if os.path.exists(mp) else {}
    if content_hash in m:
        return m[content_hash]
    for d in sorted(glob.glob(os.path.join(HOME, "snapshots", "*"))):
        if d in m.values():
            continue
        p = subprocess.run([sys.executable, "-c", "from llmbox import suite; print(suite.content_hash())"], capture_output=True,
                           text=True, env=dict(os.environ, PYTHONPATH=d, PYTHONDONTWRITEBYTECODE="1"), cwd=d, timeout=120)
        if p.returncode == 0:
            m[p.stdout.strip()] = d
    os.makedirs(os.path.dirname(mp), exist_ok=True)
    json.dump(m, open(mp, "w"), indent=1)
    return m.get(content_hash)


def fingerprints(content_hash: str, families: set[str]) -> dict:
    """{family: fingerprint or None} for the suite with this content hash; the current working tree when it is the
    current suite, else its snapshot. Cached per hash; only missing families are computed."""
    from . import suite
    cp = _cache_path(content_hash)
    have = json.load(open(cp)) if os.path.exists(cp) else {}
    todo = [f for f in families if f not in have]
    if todo:
        if content_hash == suite.content_hash():
            have.update(_run(todo, None))
        else:
            snap = snapshot_for(content_hash)
            have.update(_run(todo, snap) if snap else {f: None for f in todo})
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        json.dump(have, open(cp, "w"), indent=1, sort_keys=True)
    return {f: have.get(f) for f in families}
