"""Explanations left ungraded by cloud runs, graded later when the box is free.

A frontier run (--endpoint claude-code) writes its answers in the cloud, but the explain block is graded by the reader
model on the box (llmbox/reader.py) - the same reader for every model, so the scores compare. Grading right after the
run collided with speed work on the box (2026-09-29: the reader swapped with a probe server and a model's measurement
hung). So a cloud run leaves those rows pending, and the queue worker grades them between jobs when nothing else uses
the box. Grading uses the suite code of the run's own version (its queue snapshot): the quiz must be the one the
explanation was written for.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

from .hosts import HOME

SCRIPT = r'''
import importlib.util, json, os, sys, time
cur = os.environ.get("LLMBOX_CURRENT_READER")
if cur:   # the reader is grading machinery, not the suite: the newest one (its fixes) grades every version's tasks
    import llmbox
    spec = importlib.util.spec_from_file_location("llmbox.reader", cur)
    llmbox.reader = sys.modules["llmbox.reader"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(llmbox.reader)
from llmbox import bench, suite
path, url = sys.argv[1], sys.argv[2]
rec = json.load(open(path))
rows = rec["rows"]
items = []
for r in rows:
    if r.get("pending"):
        block, kind, lv, seed = r["id"].split(".")
        items.append(suite.BLOCKS[block][kind](int(seed), int(lv[1:])))
bench.grade_deferred(url, items, rows, None, lambda m: print(m, flush=True))
left = sum(1 for r in rows if r.get("pending"))
s = bench.summarize(rows, rec["summary"].get("wall_minutes", 0) * 60)
keep = {k: rec["summary"][k] for k in ("speed", "irt", "parallel") if k in rec["summary"]}
if rec["suite"].get("tier") == "adaptive":   # the IRT estimate is redone by `llmbox irt rescore`; keep its fields
    keep.update({k: rec["summary"][k] for k in ("capability", "capability_ci95", "blocks") if k in rec["summary"]})
rec["summary"] = dict(s, **keep)
rec["graded_later"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
json.dump(rec, open(path, "w"), indent=1)
print(f"left pending: {left}")
'''


def records() -> list[str]:
    """Result files with rows still waiting for the reader."""
    out = []
    for f in sorted(glob.glob(os.path.join(HOME, "results", "*", "*-suite-*.json"))):
        try:
            rec = json.load(open(f))
        except ValueError:
            continue
        if any(r.get("pending") for r in rec.get("rows") or []):
            out.append(f)
    return out


def grade(path: str, progress=print) -> bool:
    """Grade one record's pending rows with the reader, using the suite code of the run's version. True when none is
    left pending."""
    from . import famfp, reader, suite
    rec = json.load(open(path))
    ch = (rec.get("suite") or {}).get("content_hash")
    code = None if ch == suite.content_hash() else famfp.snapshot_for(ch) if ch else None
    if ch and ch != suite.content_hash() and not code:
        progress(f"{os.path.basename(path)}: no snapshot of suite {ch} - cannot grade its explanations")
        return False
    url = os.environ.get("LLMBOX_READER_URL") or reader.default_url()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", LLMBOX_READER_URL=url or "")
    if code:
        env["PYTHONPATH"] = code
        env["LLMBOX_CURRENT_READER"] = reader.__file__
    p = subprocess.run([sys.executable, "-c", SCRIPT, path, url or ""], capture_output=True, text=True, env=env,
                       cwd=code or os.path.dirname(os.path.dirname(os.path.abspath(__file__))), timeout=3600)
    for line in (p.stdout + p.stderr).strip().splitlines()[-12:]:
        progress(f"  {line}")
    return p.returncode == 0 and "left pending: 0" in p.stdout


def grade_all(progress=print) -> int:
    n = 0
    for f in records():
        progress(f"grading pending explanations: {os.path.basename(f)}")
        n += grade(f, progress)
    return n
