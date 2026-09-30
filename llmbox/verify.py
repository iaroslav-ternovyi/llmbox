"""Server-side re-grading (step 2 of docs/results-db.md): a submitted run's score is recomputed, never trusted.

Each answer is re-graded against the task rebuilt from its id (block.kind.Llevel.seed) with the code of the run's own
suite version (its queue snapshot, llmbox/famfp.py), in a subprocess:
- text-graded blocks (longctx, writing, reasoning, code, techhelp, knowledge): the grader runs on the saved answer;
- tools and agentic: the recorded tool calls are replayed on a fresh copy of the task's world, then graded - only when
  the log is complete (bench._clip shortens big arguments, e.g. an agent's file writes: such a run cannot be replayed);
- explain: graded by the reader model; not re-run here (status "reader").

Statuses: match / mismatch (the server's score differs from the one the run saved) / reader / pending / no_answer (the
run recorded a timeout or crash: 0, nothing to re-grade) / unverifiable (with the reason) / error. Results go to the database (table verifications), per exact bundle version.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time

SCRIPT = r'''
import json, sys
from llmbox import suite
rec = json.load(open(sys.argv[1]))
WORLD = ("tools", "agentic")
out = []

def clipped(calls):
    return len(calls) >= 400 or any(isinstance(c.get("args"), str) and c["args"].endswith("…") for c in calls)

def cleanup(it):
    ws = getattr(it.tool_impl, "__self__", None)
    if ws is not None and hasattr(ws, "cleanup"):
        ws.cleanup()

for n, r in enumerate(rec.get("rows") or []):
    iid = r.get("id") or ""
    res = {"n": n, "id": iid, "block": r.get("block"), "client": r.get("score")}
    parts = iid.split(".")
    if len(parts) != 4 or not parts[2].startswith("L"):
        out.append(dict(res, status="unverifiable", reason="item id is not block.kind.Llevel.seed")); continue
    block, kind, lv, seed = parts
    if r.get("pending"):
        out.append(dict(res, status="pending", reason="explanation not graded yet")); continue
    if block == "explain":
        out.append(dict(res, status="reader", reason="graded by the reader model; not re-run")); continue
    if r.get("error") or r.get("final") is None:   # a timeout or a crash: scored 0, nothing to re-grade
        out.append(dict(res, status="no_answer", reason=str(r.get("error") or "no answer saved")[:120])); continue
    it = None
    try:
        it = suite.BLOCKS[block][kind](int(seed), int(lv[1:]))
        tr = r
        if block in WORLD:
            calls = r.get("tool_calls") or []
            if clipped(calls):
                out.append(dict(res, status="unverifiable", reason="tool-call log shortened (large arguments): cannot replay")); continue
            replay = []
            ws_dir = getattr(getattr(it.tool_impl, "__self__", None), "dir", None)
            for c in calls:
                args = c.get("args") or {}
                if ws_dir:   # absolute paths into the run's own temporary project point at the replay's project instead
                    import re
                    js = json.dumps(args)
                    js2 = re.sub(r"/[^\"\s]*?llmbox-(?:agent|session)-[A-Za-z0-9]+-[a-z0-9_]{8}", ws_dir.replace("\\", "/"), js)
                    args = json.loads(js2) if js2 != js else args
                try:
                    got = it.tool_impl(c.get("name"), args)
                except Exception as e:   # the client returns tool errors to the model the same way (client.run_chat)
                    got = {"error": str(e)}
                replay.append({"name": c.get("name"), "args": args, "result": got})
            tr = dict(r, tool_calls=replay)
        sc = round(float(it.check(r["final"], tr)), 4)
        out.append(dict(res, server=sc, status="match" if abs(sc - float(r.get("score") or 0)) < 1e-3 else "mismatch"))
    except Exception as e:
        out.append(dict(res, status="error", reason=f"{type(e).__name__}: {str(e)[:160]}"))
    finally:
        if it is not None:
            try:
                cleanup(it)
            except Exception:
                pass
print("@@VERIFY@@" + json.dumps(out))
'''


def run_one(path: str, rec: dict | None = None, timeout: int = 3600) -> dict:
    """Re-grade one result file. Returns {path, version, rows: [...], counts: {...}} or {path, error}."""
    from . import famfp, suite
    rec = rec or json.load(open(path))
    su = rec.get("suite") or {}
    ch = su.get("content_hash")
    if rec.get("kind") != "suite" or not ch:
        return {"path": path, "error": "not a suite run"}
    from . import irt
    same = irt.canonical(ch) == irt.canonical(suite.content_hash())   # the same tasks: the working tree grades them
    code = None if same else famfp.snapshot_for(ch)
    if not same and not code:
        return {"path": path, "error": f"no code of suite {su.get('version')} ({ch}): its snapshot is gone"}
    if os.environ.get("LLMBOX_SANDBOX"):   # a stranger's answers: nothing of this process's environment (its secrets) goes in
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    else:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if code:
        env["PYTHONPATH"] = code
    cwd = code or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    t = time.time()
    # a stranger's answers run as code (hidden tests, real programs): on the server inside the sandbox command
    # $LLMBOX_SANDBOX (deploy/sandbox.sh: no network, no home, limits), which gets the paths it must read
    box = (shlex.split(os.environ["LLMBOX_SANDBOX"]) + ["--read", os.path.abspath(path), "--read", cwd, "--read", sys.prefix]
           + (["--read", code] if code else []) + ["--"]) if os.environ.get("LLMBOX_SANDBOX") else []
    p = subprocess.run(box + [sys.executable, "-c", SCRIPT, os.path.abspath(path)], capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@VERIFY@@")), None)
    if not line:
        return {"path": path, "error": f"re-grading failed: {(p.stderr or p.stdout)[-300:]}"}
    rows = json.loads(line[len("@@VERIFY@@"):])
    counts: dict = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"path": path, "version": su.get("version"), "code": code or "working tree", "rows": rows, "counts": counts,
            "seconds": round(time.time() - t, 1)}


def save(res: dict) -> None:
    """Into the database, for the exact bundle version that was verified."""
    from . import db
    c = db.connect()
    db.sync(c)
    src = c.execute("SELECT run_id, sha FROM sources WHERE path=?", (res["path"],)).fetchone()
    if not src:
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with c:
        c.execute("DELETE FROM verifications WHERE run_id=? AND bundle=?", (src["run_id"], src["sha"]))
        c.executemany("INSERT INTO verifications VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      [(src["run_id"], r["n"], r["id"], r.get("block"), r.get("client"), r.get("server"), r["status"],
                        r.get("reason"), src["sha"], res.get("code"), now) for r in res["rows"]])
    c.close()
