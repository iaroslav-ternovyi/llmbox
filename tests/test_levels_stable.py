"""Levels 1-10 of every non-agentic task family must stay exactly as they were when tests/levels_baseline.json was taken:
answers to them are pooled across suite versions (llmbox/famfp.py), so a changed task or grader silently mixes two
different tasks into one IRT family. Adding levels must not touch the existing ones. Run: python3 tests/test_levels_stable.py
(an intended change: regenerate the baseline and bump the module's GRADING number if its grading changed)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import famfp  # noqa: E402

base = json.load(open(os.path.join(os.path.dirname(__file__), "levels_baseline.json")))
blocks = set(sys.argv[1:])   # optional: only these blocks
fams = sorted(f for f in base if not blocks or f.split(".")[0] in blocks)
now = famfp._run(fams, None)
changed = [f for f in fams if now.get(f) != base[f]]
for f in changed:
    print(f"CHANGED {f}: {base[f]} -> {now.get(f)}")
print(f"{len(fams)} families checked, " + ("all unchanged" if not changed else f"{len(changed)} changed"))
sys.exit(1 if changed else 0)
