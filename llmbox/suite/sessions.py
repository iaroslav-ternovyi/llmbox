"""Multi-turn coding sessions, the way people actually talk to a coding assistant: short, informal requests (in Russian),
then follow-ups after every answer - "а ещё…", "не, округление не такое…", "апи поменялось…". The next message is sent
when the model finishes its answer; nothing in it depends on what the model did.

Each session is a small project (sessions/<name>/): starting files, the user's turns, hidden tests and a reference
solution. Level = how many turns the model gets (L1: 1 … L5: all 5). Hidden tests carry the turn that introduced their
behaviour and read LLMBOX_TURNS, so the model is graded on exactly what it was asked, with later changes overriding
earlier ones. Score = fraction of the applicable hidden tests passing on the final state of the workspace.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

from . import agentic
from .code import _sandbox_cmd
from .common import Item

BLOCK = "agentic"
_HERE = os.path.join(os.path.dirname(__file__), "sessions")
SYSTEM = ("You are a coding assistant working in the user's project directory. Use the tools to do what the user asks: "
          "look at the files, write the code, run it to check that it works. When you are done, reply briefly in the "
          "user's language without calling any tool. The user may come back with follow-up changes.")


class SessionWorkspace(agentic.Workspace):
    def __init__(self, name: str, turns: int):   # noqa: super().__init__ materializes mutation templates; not needed here
        self.src = os.path.join(_HERE, name)
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix=f"llmbox-session-{name}-"))
        shutil.copytree(os.path.join(self.src, "files"), self.dir, dirs_exist_ok=True)
        keep = os.path.join(self.dir, ".keep")
        if os.path.exists(keep):
            os.remove(keep)
        self.turns, self.calls, self.injected = turns, 0, []

    def results(self) -> dict:
        gdir = os.path.join(self.dir, "_hidden_tests")
        shutil.rmtree(gdir, ignore_errors=True)
        shutil.copytree(os.path.join(self.src, "hidden"), gdir)
        meta = json.load(open(os.path.join(self.src, "task.json")))
        for root, dirs, _files in os.walk(self.dir):
            for d in [d for d in dirs if d == "__pycache__"]:
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        cmd = _sandbox_cmd(self.dir, ["bash", "-c", meta["grade_cmd"]])
        try:
            p = subprocess.run(cmd, cwd=self.dir, capture_output=True, text=True, timeout=180,
                               env=dict(agentic._ENV, LLMBOX_TURNS=str(self.turns)))
            m = re.search(r"RESULTS (\{.*\})", p.stdout + p.stderr)
            return json.loads(m.group(1)) if m else {}
        except subprocess.TimeoutExpired:
            return {}
        finally:
            shutil.rmtree(gdir, ignore_errors=True)


def _sessions() -> list[str]:
    return sorted(d for d in os.listdir(_HERE) if os.path.isfile(os.path.join(_HERE, d, "task.json"))) if os.path.isdir(_HERE) else []


def make(name: str):
    meta = json.load(open(os.path.join(_HERE, name, "task.json")))

    def gen(seed: int, level: int = 3) -> Item:
        turns = meta["turns"][:max(1, min(level, len(meta["turns"])))]
        ws = SessionWorkspace(name, len(turns))

        def check(_text, _t=None, ws=ws) -> float:
            try:
                res = ws.results()
                return sum(res.values()) / len(res) if res else 0.0
            finally:
                ws.cleanup()
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": turns[0]}]
        return Item(f"{BLOCK}.{name}.L{level}.{seed}", BLOCK, name, msgs, check, tools=agentic.TOOLS, tool_impl=ws.tool,
                    max_tokens=32000, lang=meta.get("lang", "python"),
                    meta={"level": level, "followups": turns[1:], "turns": len(turns), "max_steps": 24 + 12 * len(turns),
                          "expected": f"{len(turns)} turns"})
    return gen


KINDS = {n: make(n) for n in _sessions()}
