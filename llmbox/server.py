"""`llmbox serve`: the intake of submitted measurements (stdlib only, like the rest of llmbox).

  POST /api/v1/seed        {client} -> {seed}: the seed a quality test (`llmbox test`) draws its tasks from
  POST /api/v1/runs        a gzip JSON bundle from `llmbox submit` -> 202 {id, status, url}
  GET  /api/v1/runs/<id>   the submission's status: received / accepted / rejected (with the reason per record)
  GET  /api/v1/health      ok

Bundles are stored as sent (<data>/inbox/<id>.json.gz) and listed in <data>/intake.db. `ingest()` (a thread of the
server, or `llmbox serve --ingest-once`) checks each record and files the accepted ones as results of the pseudo
host "community" (~/.llmbox/results/community), where the site reads them.

A quality run (kind suite, from `llmbox test`) is never taken at its word: every answer is graded again here
(llmbox/verify.py, inside the sandbox $LLMBOX_SANDBOX: the answers are code that runs), the server's score replaces the
client's, explanations wait for the reader model on the reference box (pending), an answer that cannot be re-graded is
left out, and a run with more than a fifth of its answers graded differently is rejected. The run must use a released
suite and a seed this server gave that install ("self-seeded" otherwise: filed, not pooled).

Checks for a speed record (speed cannot be proven, docs/roadmap.md §4): the schema and kind; the host fingerprint
has a card or says it has none; the numbers are positive and not absurd (under 2000 tok/s decode, 100k prefill);
the model file is named. A figure more than twice what the formula predicts for that machine is kept but marked
"outlier" - the site leaves it out of the medians until a second machine of the class confirms it.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import hwclass, results
from .hosts import HOME

MAX_BODY = 20 * 2**20
PER_HOUR = 30                 # submissions per install id and per address
COMMUNITY = "community"
DB_SCHEMA = """CREATE TABLE IF NOT EXISTS submissions (
  id TEXT PRIMARY KEY, received TEXT, client TEXT, addr TEXT, bytes INTEGER, records INTEGER,
  status TEXT, reason TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS seeds (seed INTEGER PRIMARY KEY, client TEXT, issued TEXT, used INTEGER DEFAULT 0)"""
MISMATCH_MAX = 0.2            # share of re-graded answers that may differ before a quality run is rejected
SEEDS_PER_DAY = 20


class Intake:
    def __init__(self, data: str):
        self.data = os.path.expanduser(data)
        os.makedirs(os.path.join(self.data, "inbox"), exist_ok=True)
        self.lock = threading.Lock()
        with self._db() as c:
            c.executescript(DB_SCHEMA)

    def _db(self) -> sqlite3.Connection:
        c = sqlite3.connect(os.path.join(self.data, "intake.db"), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def recent(self, client: str, addr: str) -> int:
        since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3600))
        with self._db() as c:
            return c.execute("SELECT count(*) FROM submissions WHERE received > ? AND (client = ? OR addr = ?)", (since, client, addr)).fetchone()[0]

    def receive(self, raw: bytes, addr: str) -> tuple[int, dict]:
        try:
            b = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
        except (OSError, ValueError, EOFError):
            return 400, {"error": "not a gzip JSON bundle"}
        if not isinstance(b, dict) or b.get("schema") != "llmbox.submission/1" or not isinstance(b.get("records"), list):
            return 400, {"error": "unknown bundle schema (update llmbox)"}
        client = str(b.get("client") or "")[:64]
        if len(client) < 16:
            return 400, {"error": "no install id"}
        a = hashlib.sha256(addr.encode()).hexdigest()[:16]   # the address itself is not kept
        with self.lock:
            if self.recent(client, a) >= PER_HOUR:
                return 429, {"error": f"more than {PER_HOUR} submissions in an hour; try later"}
            sid = uuid.uuid4().hex[:12]
            open(os.path.join(self.data, "inbox", f"{sid}.json.gz"), "wb").write(gzip.compress(json.dumps(b).encode()))
            with self._db() as c:
                c.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?)",
                          (sid, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), client, a, len(raw), len(b["records"]), "received", "", "[]"))
        return 202, {"id": sid, "status": "received", "url": f"/api/v1/runs/{sid}"}

    def seed(self, raw: bytes) -> tuple[int, dict]:
        """A fresh seed for this install's next quality test (the tasks cannot be prepared in advance)."""
        import secrets
        try:
            client = str(json.loads(raw or b"{}").get("client") or "")[:64]
        except ValueError:
            return 400, {"error": "not JSON"}
        if len(client) < 16:
            return 400, {"error": "no install id"}
        since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 86400))
        with self.lock, self._db() as c:
            if c.execute("SELECT count(*) FROM seeds WHERE client = ? AND issued > ?", (client, since)).fetchone()[0] >= SEEDS_PER_DAY:
                return 429, {"error": f"more than {SEEDS_PER_DAY} tests a day from this install"}
            s = 10**6 + secrets.randbelow(10**9)
            c.execute("INSERT OR IGNORE INTO seeds VALUES (?,?,?,0)", (s, client, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())))
        return 200, {"seed": s}

    def seeded(self, client: str, seed) -> bool:
        with self._db() as c:
            return c.execute("SELECT 1 FROM seeds WHERE seed = ? AND client = ?", (seed, client)).fetchone() is not None

    def status(self, sid: str) -> dict | None:
        with self._db() as c:
            r = c.execute("SELECT id, received, records, status, reason, detail FROM submissions WHERE id = ?", (sid,)).fetchone()
        return dict(r, detail=json.loads(r["detail"] or "[]")) if r else None

    def ingest(self, save_host: str = COMMUNITY, predict=None) -> list[str]:
        """Check every received submission and file its accepted records; returns the submission ids done."""
        with self._db() as c:
            todo = [r["id"] for r in c.execute("SELECT id FROM submissions WHERE status = 'received' ORDER BY received")]
        done = []
        for sid in todo:
            b = json.loads(gzip.decompress(open(os.path.join(self.data, "inbox", f"{sid}.json.gz"), "rb").read()))
            detail, ok = [], 0
            for rec in b["records"]:
                why = check(rec)
                if why:
                    detail.append({"record": str(rec.get("id"))[:36], "status": "rejected", "reason": why})
                    continue
                flags = outlier_flags(rec, predict)
                if rec.get("kind") == "suite":
                    rec, why, qflags = self.regrade(rec, b["client"])
                    if why:
                        detail.append({"record": str(rec.get("id"))[:36], "status": "rejected", "reason": why})
                        continue
                    flags += qflags
                rec = dict(rec, submission={"id": sid, "client": hashlib.sha256(b["client"].encode()).hexdigest()[:12],
                                            "received": time.strftime("%Y-%m-%dT%H:%M:%S"), "flags": flags})
                rec["host"] = dict(rec["host"], **{"class": hwclass.of_host(rec["host"])})
                results.save(save_host, rec)
                ok += 1
                detail.append({"record": rec["id"][:36], "status": "accepted", "flags": flags})
            status = "accepted" if ok else "rejected"
            with self._db() as c:
                c.execute("UPDATE submissions SET status = ?, reason = ?, detail = ? WHERE id = ?",
                          (status, f"{ok} of {len(b['records'])} records accepted", json.dumps(detail), sid))
            done.append(sid)
        return done


    def regrade(self, rec: dict, client: str) -> tuple[dict, str | None, list[str]]:
        """A quality run with the server's own grades: (record, why it is rejected or None, flags)."""
        from . import verify
        work = os.path.join(self.data, "work")
        os.makedirs(work, exist_ok=True)
        p = os.path.join(work, f"{uuid.uuid4().hex}.json")
        json.dump(rec, open(p, "w"))
        try:
            res = verify.run_one(p, rec)
        finally:
            os.remove(p)
        if res.get("error"):
            return rec, f"could not be re-graded: {res['error'][:200]}", []
        rows = [dict(x) for x in rec.get("rows") or []]
        graded = diff = 0
        for v in res["rows"]:
            x = rows[v["n"]]
            if v["status"] in ("match", "mismatch"):
                graded += 1
                diff += v["status"] == "mismatch"
                x.update(score=v["server"], verified=True)
            elif v["status"] in ("reader", "pending"):
                x.update(pending=True, score=None)       # the reader on the reference box grades it
            elif v["status"] != "no_answer":
                x.update(error=f"not verified: {v.get('reason') or v['status']}"[:200], score=None)
        if graded and diff / graded > MISMATCH_MAX:
            return rec, f"{diff} of {graded} answers grade differently here: not the answers the suite gave", []
        s0 = (rec.get("suite") or {}).get("seed0") or 0   # bench: seed0 = 7000 + 1000 * --seed
        flags = [] if (s0 - 7000) % 1000 == 0 and self.seeded(client, (s0 - 7000) // 1000) else ["self-seeded"]
        rec = dict(rec, rows=rows, verified={"graded": graded, "differed": diff, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        far = off_score(rec)
        if far:   # the same file and settings answer like another model: filed, held out of the score
            rec["verified"]["off"] = far
            flags.append("outlier")
        return rec, None, flags


def off_score(rec: dict) -> dict | None:
    """A person's run whose 95% range does not meet the reference recipe's pooled range: the same model file with the
    same portable settings should score the same anywhere, so a run clearly above it (answers from a stronger model)
    or below it (a broken setup) is held out of the score. None when it agrees, or there is nothing to compare with."""
    from . import irt, report
    bank = irt.bank_for((rec.get("suite") or {}).get("content_hash"))
    home = irt.community_home(rec, irt._recipe_ids("box"))
    if not bank or not home:
        return None
    rows = [x for x in (irt.counted(r) for r in rec.get("rows") or []) if x]
    run = irt.score_rows(bank, rows) if rows else None
    ref = (report.current_pool().get((home, "box")) or {}).get("score")
    if not run or not ref or not run.get("ci95") or not ref.get("ci95"):
        return None
    (lo, hi), (rlo, rhi) = run["ci95"], ref["ci95"]
    if lo > rhi or hi < rlo:
        return {"run": run["capability"], "run_ci": run["ci95"], "recipe": home, "pooled": ref["capability"], "pooled_ci": ref["ci95"]}
    return None


def check(rec: dict) -> str | None:
    """Why a submitted record cannot be used, or None."""
    if rec.get("schema") != results.SCHEMA:
        return "unknown record schema"
    if rec.get("kind") not in ("speed", "optimize", "suite"):
        return f"kind {rec.get('kind')!r} is not taken (speed, optimize and suite are)"
    h = rec.get("host") or {}
    if not h.get("cpu") or not h.get("ram_gib") or h.get("gpu") is None and h.get("vram_gib"):
        return "the record does not say what machine it ran on"
    if h.get("id") == "cloud":
        return "cloud records are the site's own"
    m = rec.get("model") or {}
    if not (m.get("file") or m.get("path")):
        return "no model file named"
    if rec.get("kind") == "suite":
        from . import irt
        su = rec.get("suite") or {}
        if irt.canonical(su.get("content_hash")) not in irt.RELEASES:
            return f"suite {su.get('version')} ({su.get('content_hash')}) is not a released version: update llmbox"
        if su.get("tier") != "adaptive" or not rec.get("rows"):
            return "a quality run must be an adaptive test with its answers"
        return None if len(json.dumps(rec)) <= 15 * 2**20 else "record too large"
    sp = (rec.get("speed") if rec.get("kind") == "speed" else ((rec.get("runs") or {}).get("llmbox") or {})) or {}
    dec = sp.get("decode_tps") if rec.get("kind") == "speed" else sp.get("decode")
    pre = sp.get("prefill_tps") if rec.get("kind") == "speed" else sp.get("prefill")
    if not dec or not (0 < dec < 2000) or (pre is not None and not (0 < pre < 100_000)):
        return "speed figures missing or out of range"
    if len(json.dumps(rec)) > 5 * 2**20:
        return "record too large"
    return None


def outlier_flags(rec: dict, predict=None) -> list[str]:
    """'outlier' when the decode figure is over twice what the formula predicts for that machine (predict(rec) -> tok/s)."""
    if rec.get("kind") != "speed":
        return []
    pred = predict(rec) if predict else ((rec.get("prediction") or {}).get("decode_tps_no_spec"))
    dec = (rec.get("speed") or {}).get("decode_tps")
    return ["outlier"] if pred and dec and dec > 2 * pred else []


def handler(intake: Intake):
    class H(BaseHTTPRequestHandler):
        server_version = "llmbox"

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/v1/health":
                return self._json(200, {"ok": True})
            if self.path.startswith("/api/v1/runs/"):
                st = intake.status(self.path.rsplit("/", 1)[-1][:32])
                return self._json(200, st) if st else self._json(404, {"error": "no such submission"})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if self.path == "/api/v1/seed" and n <= 4096:
                return self._json(*intake.seed(self.rfile.read(n)))
            if self.path != "/api/v1/runs":
                return self._json(404, {"error": "not found"})
            if not 0 < n <= MAX_BODY:
                return self._json(413, {"error": f"body must be 1 byte to {MAX_BODY // 2**20} MB"})
            addr = self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]
            code, obj = intake.receive(self.rfile.read(n), addr)
            self._json(code, obj)

        def log_message(self, fmt, *args):   # one line per request, no addresses
            print(f"{time.strftime('%H:%M:%S')} {self.command} {self.path} {args[1] if len(args) > 1 else ''}")
    return H


def serve(data: str, port: int = 8767, bind: str = "127.0.0.1", every_s: int = 30, on_accept=None) -> None:
    """Run the intake; a thread ingests new submissions every every_s seconds and calls on_accept(ids) after any."""
    intake = Intake(data)

    def loop():
        while True:
            try:
                ids = intake.ingest()
                if ids and on_accept:
                    on_accept(ids)
            except Exception as e:   # one bad bundle must not stop the intake
                print(f"ingest error: {e}")
            time.sleep(every_s)
    threading.Thread(target=loop, daemon=True).start()
    print(f"llmbox intake on http://{bind}:{port}/api/v1/ (data {intake.data})")
    ThreadingHTTPServer((bind, port), handler(intake)).serve_forever()
