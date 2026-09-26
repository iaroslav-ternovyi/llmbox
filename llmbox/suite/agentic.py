"""Long agentic coding: a multi-module project with injected bugs (and, at higher levels, a missing feature).

The model works like a coding agent: it gets bash / read / write / edit tools inside a sandboxed copy of the project
(no network, writes only inside the workspace), sees a partial test suite, and must make the HIDDEN full suite pass.
Tasks take tens of tool calls - this is the "long task" part of the suite.

Projects are templates whose candidate bug sites carry `#@MUT <id>: <buggy line>` markers; a seed picks which bugs are
injected, so every run gets a different combination. Grading = (hidden tests fixed - hidden tests broken) / hidden tests
failing at the start, so doing nothing scores 0; hidden tests are copied in only at grading time, so the agent cannot read or edit them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

from .code import _sandbox_cmd
from .common import Item, rng

BLOCK = "agentic"
_HERE = os.path.join(os.path.dirname(__file__), "projects")
MAX_OUT = 6000
_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1")

TOOLS = [
    {"type": "function", "function": {"name": "bash", "description": "Run a shell command in the project directory (no network). Returns exit code and output.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file (path relative to the project root).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a file with the given content.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Replace exactly one occurrence of old_text with new_text in a file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                    "required": ["path", "old_text", "new_text"]}}},
]
SYSTEM = ("You are an autonomous software engineer working in a project checkout. Use the tools to inspect the code, run "
          "the tests, and make changes. Work step by step until the task is complete, then reply with a short summary "
          "of what you changed (without calling any tool).")

_MUT = re.compile(r"^(?P<indent>\s*)(?P<code>.*?)\s*(?:#|//)@MUT (?P<id>[\w-]+): (?P<bug>.*)$")


def _materialize(src_dir: str, dst: str, bugs: set) -> list[str]:
    """Copy a project template, injecting the chosen bugs and stripping all mutation markers."""
    injected = []
    for root, _dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        if rel.startswith(("hidden", "_ref")):   # hidden tests and reference solutions never reach the agent
            continue
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in files:
            if f == "task.json":
                continue
            text = open(os.path.join(root, f)).read()
            out = []
            for line in text.splitlines():
                if re.match(r"^\s*(#|//) FEATURE-(BEGIN|END)", line) and not bugs.__contains__("__keep_markers__"):
                    out.append(line)   # kept until strip_feature decides; removed below if the feature is not stripped
                    continue
                m = _MUT.match(line)
                if m:
                    if m.group("id") in bugs:
                        out.append(m.group("indent") + m.group("bug"))
                        injected.append(m.group("id"))
                    else:
                        out.append(m.group("indent") + m.group("code"))
                else:
                    out.append(line)
            open(os.path.join(dst, rel, f), "w").write("\n".join(out) + "\n")
    return injected


class Workspace:
    def __init__(self, project: str, bugs: set, strip_feature: str | None):
        self.src = os.path.join(_HERE, project)
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix=f"llmbox-agent-{project}-"))
        self.injected = _materialize(self.src, self.dir, bugs)
        if strip_feature and "::" not in strip_feature:  # remove every FEATURE-BEGIN <name> ... FEATURE-END block
            for root, _d, files in os.walk(self.dir):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        t = open(fp).read()
                    except UnicodeDecodeError:
                        continue
                    new_t = re.sub(rf"(?ms)^[ \t]*(?:#|//) FEATURE-BEGIN {re.escape(strip_feature)}\n.*?^[ \t]*(?:#|//) FEATURE-END\n", "", t)
                    if new_t != t:
                        open(fp, "w").write(new_t)
        elif strip_feature:  # remove a function body so the agent must implement it from the spec
            p, name = strip_feature.split("::")
            path = os.path.join(self.dir, p)
            text = open(path).read()
            if p.endswith(".py"):
                text = re.sub(rf"(?ms)^(def {name}\(.*?\n)(?=^\S|\Z)",
                              lambda m: m.group(1).split("\n")[0] + "\n    raise NotImplementedError\n\n\n", text)
            else:
                i = text.index(f"function {name}(")
                j = text.index("{", i)
                depth, k = 0, j
                while True:
                    depth += {"{": 1, "}": -1}.get(text[k], 0)
                    if depth == 0:
                        break
                    k += 1
                text = text[:j] + '{\n  throw new Error("not implemented");\n}' + text[k + 1:]
            open(path, "w").write(text)
        for root, _d, files in os.walk(self.dir):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    t = open(fp).read()
                except UnicodeDecodeError:
                    continue
                new_t = re.sub(r"(?m)^[ \t]*(?:#|//) FEATURE-(?:BEGIN [\w-]+|END)\n", "", t)
                if new_t != t:
                    open(fp, "w").write(new_t)
        self.calls = 0

    def _path(self, p: str) -> str:
        full = os.path.realpath(os.path.join(self.dir, p))
        if not full.startswith(self.dir):
            raise ValueError("path outside the project")
        return full

    def tool(self, name: str, a: dict):
        self.calls += 1
        if name == "bash":
            cmd = _sandbox_cmd(self.dir, ["bash", "-c", a.get("command", "")])
            if cmd is None:
                return {"error": "no sandbox available"}
            try:
                p = subprocess.run(cmd, cwd=self.dir, capture_output=True, text=True, timeout=60, env=_ENV)
                out = (p.stdout + p.stderr)
                return {"exit_code": p.returncode, "output": out[-MAX_OUT:] if len(out) > MAX_OUT else out}
            except subprocess.TimeoutExpired:
                return {"exit_code": -1, "output": "command timed out after 60 s"}
        if name == "read_file":
            t = open(self._path(a["path"])).read()
            return t if len(t) <= 20000 else t[:20000] + "\n...[truncated]"
        if name == "write_file":
            p = self._path(a["path"])
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").write(a.get("content", ""))
            return {"ok": True}
        if name == "edit_file":
            p = self._path(a["path"])
            t = open(p).read()
            n = t.count(a.get("old_text", ""))
            if n != 1 or not a.get("old_text"):
                return {"error": f"old_text found {n} times; it must match exactly once"}
            open(p, "w").write(t.replace(a["old_text"], a.get("new_text", ""), 1))
            return {"ok": True}
        return {"error": f"unknown tool {name}"}

    def results(self) -> dict:
        """Per-test pass/fail of the HIDDEN suite (copied in only for this call, removed afterwards)."""
        hidden = os.path.join(self.src, "hidden")
        gdir = os.path.join(self.dir, "_hidden_tests")
        shutil.rmtree(gdir, ignore_errors=True)
        shutil.copytree(hidden, gdir)
        meta = json.load(open(os.path.join(self.src, "task.json")))
        for root, dirs, _files in os.walk(self.dir):   # stale bytecode could hide a same-size edit made within one second
            for d in [d for d in dirs if d == "__pycache__"]:
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        cmd = _sandbox_cmd(self.dir, ["bash", "-c", meta["grade_cmd"]])
        try:
            p = subprocess.run(cmd, cwd=self.dir, capture_output=True, text=True, timeout=180, env=_ENV)
            m = re.search(r"RESULTS (\{.*\})", p.stdout + p.stderr)
            return json.loads(m.group(1)) if m else {}
        except subprocess.TimeoutExpired:
            return {}
        finally:
            shutil.rmtree(gdir, ignore_errors=True)

    def grade(self, baseline: dict | None = None) -> float:
        """Without a baseline: fraction of hidden tests passing. With one: (fixed - broken) / initially failing."""
        now = self.results()
        if not now:
            return 0.0
        if baseline is None:
            return sum(now.values()) / len(now)
        failing = [t for t, ok in baseline.items() if not ok]
        if not failing:
            return 1.0 if all(now.get(t, False) for t in baseline) else 0.0
        fixed = sum(1 for t in failing if now.get(t))
        broken = sum(1 for t, ok in baseline.items() if ok and not now.get(t, False))
        return max(0.0, (fixed - broken) / len(failing))

    def cleanup(self):
        """Keeps a copy of the final workspace for audits (~/.llmbox/artifacts/<name>-<time>.tgz), then deletes it."""
        try:
            import tarfile
            import time as _t
            d = os.path.expanduser("~/.llmbox/artifacts")
            os.makedirs(d, exist_ok=True)
            base = os.path.basename(self.dir.rstrip("/"))
            with tarfile.open(os.path.join(d, f"{base}-{_t.strftime('%Y%m%d-%H%M%S')}.tgz"), "w:gz") as tf:
                tf.add(self.dir, arcname=base, filter=lambda ti: None if "node_modules" in ti.name or "__pycache__" in ti.name else ti)
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)


def _projects() -> list[str]:
    return sorted(d for d in os.listdir(_HERE) if os.path.isfile(os.path.join(_HERE, d, "task.json"))) if os.path.isdir(_HERE) else []


def make(project: str):
    meta = json.load(open(os.path.join(_HERE, project, "task.json")))

    def gen(seed: int, level: int = 3) -> Item:
        r = rng(BLOCK, f"{project}{level}", seed)
        implement = meta.get("kind") == "implement"
        if implement:
            level = max(3, level)          # from-scratch tasks start at level 3
            n_bugs, bugs = 0, set()
        else:
            per = meta.get("bugs_per_level")
            n_bugs = min(len(meta["mutations"]), per[level - 1] if per else max(1, level))
            bugs = set(r.sample(meta["mutations"], n_bugs))
        feature = meta.get("feature") if level >= 4 and meta.get("feature") else None
        ws = Workspace(project, bugs, (feature.get("strip") or feature.get("strip_js") or feature.get("blocks")) if feature else None)
        task = meta["prompt"].format(n_bugs=n_bugs)
        if level >= 5 and not implement:  # no safety net: remove the visible tests, the agent has to verify against the spec itself
            for d in ("tests", "test"):
                shutil.rmtree(os.path.join(ws.dir, d), ignore_errors=True)
            task += ("\n\nNOTE: this checkout has NO test suite (the tests/ directory was removed). The command above will not "
                     "find any tests; verify your fixes by writing and running your own checks against README.md.")
        if feature:
            task += "\n\nIn addition: " + feature["spec"]
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]

        prefixes = tuple(meta.get("levels_tests", {}).get(str(level), [])) if implement else ()

        def keep(res: dict) -> dict:
            if not prefixes:
                return res
            out = {}
            for k, v in res.items():   # python ids "...L4_Text.test_x", node test names "L4_filters chain"
                m = re.search(r"(?:^|\.)(L\d+_)", k)
                if m and m.group(1) in prefixes:
                    out[k] = v
            return out
        baseline = keep(ws.results())   # which hidden tests fail before the agent starts

        def check(_text, _t=None, ws=ws, baseline=baseline) -> float:
            try:
                now = keep(ws.results())
                if not now:
                    return 0.0
                failing = [t for t, ok in baseline.items() if not ok]
                if not failing:
                    return 1.0 if all(now.get(t, False) for t in baseline) else 0.0
                fixed = sum(1 for t in failing if now.get(t))
                broken = sum(1 for t, ok in baseline.items() if ok and not now.get(t, False))
                return max(0.0, (fixed - broken) / len(failing))
            finally:
                ws.cleanup()
        return Item(f"{BLOCK}.{project}.L{level}.{seed}", BLOCK, project, msgs, check, tools=TOOLS, tool_impl=ws.tool,
                    max_tokens=32000, lang=meta.get("lang", "python"),
                    meta={"level": level, "bugs": sorted(bugs), "feature": bool(feature), "max_steps": 40 + 16 * level,
                          "expected": sorted(bugs)})
    return gen


KINDS = {p: make(p) for p in _projects()}
