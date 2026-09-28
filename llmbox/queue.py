"""`llmbox queue`: a persistent job queue for benchmark runs (replaces hand-written batch scripts).

Jobs live in SQLite (~/.llmbox/queue.db). One worker at a time (file lock). Before each job the worker checks that the
host's GPU is present and that no other benchmark is running; while a job runs, a watchdog polls the GPU and, if it
disappears, stops the job and marks it `interrupted`. Interrupted jobs resume automatically (bench --resume on the job's
own jsonl: finished items are reused, errored items re-run) once the GPU is back. Every job runs from a frozen suite
snapshot (`llmbox snapshot <git tag>`), so a whole campaign uses exactly one version of the tasks and graders.
Events (start / done / interrupted / failed) are appended to ~/.llmbox/queue/events.log for monitoring.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from . import hosts
from .hosts import HOME

DB = os.path.join(HOME, "queue.db")
QDIR = os.path.join(HOME, "queue")
EVENTS = os.path.join(QDIR, "events.log")
PAUSE = os.path.join(QDIR, "PAUSE")
SNAPSHOTS = os.path.join(HOME, "snapshots")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GPU_POLL_S, GPU_FAILS_TO_STOP, MAX_ATTEMPTS = 30, 2, 3

SCHEMA = """CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, created TEXT, status TEXT, priority INTEGER DEFAULT 0, model TEXT, suite TEXT, tier TEXT,
  host TEXT, args TEXT, attempts INTEGER DEFAULT 0, started TEXT, finished TEXT, jsonl TEXT, log TEXT, result TEXT, note TEXT)"""


def _db() -> sqlite3.Connection:
    os.makedirs(QDIR, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute(SCHEMA)
    return c


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def event(msg: str) -> None:
    os.makedirs(QDIR, exist_ok=True)
    with open(EVENTS, "a") as f:
        f.write(f"{_now()} {msg}\n")
    print(f"{_now()} {msg}", flush=True)


# ---- snapshots: a frozen copy of the code at a git tag -------------------------------------------------------------

def snapshot(tag: str) -> str:
    """Extract git tag `tag` into ~/.llmbox/snapshots/<tag> (idempotent) and return the path."""
    dst = os.path.join(SNAPSHOTS, tag)
    if os.path.isdir(os.path.join(dst, "llmbox")):
        return dst
    commit = subprocess.run(["git", "-C", REPO, "rev-list", "-n", "1", tag], capture_output=True, text=True, check=True).stdout.strip()
    os.makedirs(dst, exist_ok=True)
    arch = subprocess.run(["git", "-C", REPO, "archive", tag], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", dst], input=arch, check=True)
    with open(os.path.join(dst, "SNAPSHOT"), "w") as f:
        f.write(f"{tag} {commit}\n")
    return dst


# ---- queue operations ---------------------------------------------------------------------------------------------

def add(model: str, suite: str, tier: str = "quick", host: str = "box", args: list[str] | None = None,
        priority: int = 0, note: str = "") -> int:
    with _db() as c:
        cur = c.execute("INSERT INTO jobs (created, status, priority, model, suite, tier, host, args, note) VALUES (?,?,?,?,?,?,?,?,?)",
                        (_now(), "queued", priority, model, suite, tier, host, json.dumps(args or []), note))
        return cur.lastrowid


def jobs(all_: bool = False) -> list[sqlite3.Row]:
    with _db() as c:
        q = "SELECT * FROM jobs" + ("" if all_ else " WHERE status NOT IN ('done','cancelled')") + " ORDER BY id"
        return c.execute(q).fetchall()


def set_status(job_id: int, status: str, **kw) -> None:
    cols = ", ".join(f"{k}=?" for k in kw)
    with _db() as c:
        c.execute(f"UPDATE jobs SET status=?{', ' + cols if cols else ''} WHERE id=?", (status, *kw.values(), job_id))


def progress(job: sqlite3.Row) -> str:
    """'[12/26] last item' from the job log."""
    try:
        lines = [l for l in open(job["log"]).read().splitlines() if re.match(r"\s*\[\s*\d+/\d+\]", l)]
        return lines[-1].strip() if lines else "starting"
    except (OSError, TypeError):
        return ""


# ---- worker -------------------------------------------------------------------------------------------------------

def gpu_ok(host: str) -> bool:
    try:
        h = hosts.host_of(hosts.load(host))
        return "GPU 0" in h.run("nvidia-smi -L", timeout=30).stdout
    except Exception:
        return False


def _other_bench_running() -> bool:
    """Another benchmark on the box from this machine. Frontier reference runs (--endpoint claude-code...) do not touch
    the box and do not count."""
    out = subprocess.run(["pgrep", "-fl", "llmbox.cli bench|probe_update.py|speed_probe"], capture_output=True, text=True).stdout
    if any(int(line.split()[0]) != os.getpid() and "claude-code" not in line for line in out.splitlines() if line.strip()):
        return True
    # speed work on the box (tune / optimize / probe / speed) holds every job, cloud runs too: their explanations are
    # graded by the reader model on the box, and a reader loaded next to a probe server ran it out of VRAM (2026-09-29)
    out = subprocess.run(["pgrep", "-fl", "llmbox.cli (tune|optimize|probe|speed) "], capture_output=True, text=True).stdout
    return any(int(line.split()[0]) != os.getpid() for line in out.splitlines() if line.strip())


DENSE_POWER_LIMIT_W = 180   # gpu-box: four Xid 79 crashes, all with a dense model near 250 W (2026-09-14..26)


def power_hold(job: sqlite3.Row) -> str | None:
    """Why a job must wait before it may start, or None. A dense model sits entirely on the GPU and holds it at full
    power for minutes; on the reference box that makes the GPU fall off the bus unless its power limit is capped
    (host profile key `dense_power_limit_w`, default 180 W). MoE models, which keep most weights in RAM, pass."""
    args = json.loads(job["args"] or "[]")
    rid = args[args.index("--recipe") + 1] if "--recipe" in args else None
    if not rid:
        return None
    try:
        from . import fit, recipe as rc
        prof = hosts.load(job["host"])
        shape = fit.shape_for(rc.load(job["host"], rid))
    except (OSError, ValueError, SystemExit):
        return None
    if shape.is_moe:
        return None
    cap = float(prof.get("dense_power_limit_w") or DENSE_POWER_LIMIT_W)
    try:
        out = hosts.host_of(prof).run("nvidia-smi --query-gpu=power.limit --format=csv,noheader,nounits", timeout=30).stdout
        limit = float(out.split()[0])
    except (Exception,):
        return f"dense model: GPU power limit unreadable, needs <= {cap:.0f} W"
    if limit > cap + 1:
        return f"dense model on a GPU capped at {limit:.0f} W: run `sudo nvidia-smi -pl {cap:.0f}` on {job['host']} first"
    return None


def _next_job(holds: dict | None = None) -> sqlite3.Row | None:
    """The first runnable job by priority; jobs held back (see power_hold) are skipped and their reason recorded."""
    with _db() as c:
        cands = c.execute("SELECT * FROM jobs WHERE status IN ('queued','interrupted') ORDER BY priority DESC, id").fetchall()
    for job in cands:
        why = power_hold(job)
        if holds is not None:
            if why and holds.get(job["id"]) != why:
                event(f"job {job['id']} {job['model']} held: {why}")
            holds[job["id"]] = why
        if not why:
            return job
    return None


def _run_job(job: sqlite3.Row) -> None:
    jid = job["id"]
    snap = snapshot(job["suite"])
    jsonl = job["jsonl"] or os.path.join(QDIR, f"job-{jid}.jsonl")
    log = job["log"] or os.path.join(QDIR, f"job-{jid}.log")
    args = json.loads(job["args"] or "[]")
    cmd = [sys.executable, "-u", "-m", "llmbox.cli", "bench", job["model"], "--tier", job["tier"], "--host", job["host"],
           "--jsonl", jsonl, *args]
    if os.path.exists(jsonl) and os.path.getsize(jsonl) > 0:
        cmd += ["--resume", jsonl]   # continue an interrupted job: reuse finished items, re-run errored ones
    env = dict(os.environ, PYTHONPATH=snap, PYTHONDONTWRITEBYTECODE="1")
    set_status(jid, "running", started=_now(), attempts=job["attempts"] + 1, jsonl=jsonl, log=log)
    event(f"job {jid} {job['model']} [{job['suite']} {job['tier']} {' '.join(args)}] started (attempt {job['attempts'] + 1})")
    with open(log, "a") as lf:
        lf.write(f"\n=== {_now()} attempt {job['attempts'] + 1}: {' '.join(cmd)}\n")
        lf.flush()
        p = subprocess.Popen(["caffeinate", "-ims", *cmd], cwd=snap, env=env, stdout=lf, stderr=subprocess.STDOUT,
                             start_new_session=True)
    lost = threading.Event()

    def watchdog():
        fails = 0
        while p.poll() is None:
            time.sleep(GPU_POLL_S)
            if p.poll() is not None:
                return
            fails = 0 if gpu_ok(job["host"]) else fails + 1
            if fails >= GPU_FAILS_TO_STOP:
                lost.set()
                event(f"job {jid} {job['model']}: GPU lost - stopping the run")
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                return
    threading.Thread(target=watchdog, daemon=True).start()
    rc = p.wait()
    text = open(log).read()
    saved = re.findall(r"saved (\S+\.json)", text)
    with _db() as c:
        cancelled = (c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone() or {"status": ""})["status"] == "cancelled"
    if cancelled and not (rc == 0 and saved):   # `queue cancel` + kill: a stopped job must not come back as a retry
        event(f"job {jid} {job['model']} stopped (cancelled)")
    elif lost.is_set():
        set_status(jid, "interrupted", note="GPU lost; resumes when the GPU is back")
    elif rc == 0 and saved:
        summary = next((l.strip() for l in reversed(text.splitlines()) if "capability" in l), "")
        set_status(jid, "done", finished=_now(), result=saved[-1])
        event(f"job {jid} {job['model']} done: {summary[:200]}")
    else:
        attempts = job["attempts"] + 1
        st = "queued" if attempts < MAX_ATTEMPTS else "failed"
        set_status(jid, st, note=f"exit {rc}")
        event(f"job {jid} {job['model']} {'will retry' if st == 'queued' else 'FAILED'} (exit {rc}); see {log}")


def run(until_empty: bool = False) -> None:
    """The worker loop. Exactly one per machine (lock file)."""
    os.makedirs(QDIR, exist_ok=True)
    lock = open(os.path.join(QDIR, "worker.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("another queue worker is already running")
    lock.write(str(os.getpid()))
    lock.flush()
    event(f"worker started (pid {os.getpid()})")
    waiting = ""
    holds: dict = {}
    while True:
        if os.path.exists(PAUSE):
            reason = "paused (remove ~/.llmbox/queue/PAUSE to continue)"
        else:
            try:
                job = _next_job(holds)
            except Exception as e:   # a bad job must not kill the worker silently (2026-09-27: 4 h idle after a TypeError)
                reason = f"worker error while picking a job: {type(e).__name__}: {str(e)[:200]} - retrying every minute"
                if reason != waiting:
                    event(reason)
                    waiting = reason
                time.sleep(60)
                continue
            if job is None:
                if until_empty:
                    event("queue empty - worker exits")
                    return
                reason = "queue empty"
            elif _other_bench_running():
                reason = "another benchmark is running on this machine"
            elif not gpu_ok(job["host"]):
                reason = f"waiting for the GPU on {job['host']}"
            else:
                waiting = ""
                _run_job(job)
                continue
        if reason != waiting:
            event(reason)
            waiting = reason
        time.sleep(60)
