"""`llmbox serve`: the intake of submitted measurements (stdlib only, like the rest of llmbox).

  POST /api/v1/runs        a gzip JSON bundle from `llmbox submit` -> 202 {id, status, url}
  GET  /api/v1/runs/<id>   the submission's status: received / accepted / rejected (with the reason per record)
  GET  /api/v1/health      ok

Bundles are stored as sent (<data>/inbox/<id>.json.gz) and listed in <data>/intake.db. `ingest()` (a thread of the
server, or `llmbox serve --ingest-once`) checks each record and files the accepted ones as results of the pseudo
host "community" (~/.llmbox/results/community), where the site reads them.

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
  status TEXT, reason TEXT, detail TEXT)"""


class Intake:
    def __init__(self, data: str):
        self.data = os.path.expanduser(data)
        os.makedirs(os.path.join(self.data, "inbox"), exist_ok=True)
        self.lock = threading.Lock()
        with self._db() as c:
            c.execute(DB_SCHEMA)

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


def check(rec: dict) -> str | None:
    """Why a submitted record cannot be used, or None."""
    if rec.get("schema") != results.SCHEMA:
        return "unknown record schema"
    if rec.get("kind") not in ("speed", "optimize"):
        return f"kind {rec.get('kind')!r} is not taken yet (speed and optimize are)"
    h = rec.get("host") or {}
    if not h.get("cpu") or not h.get("ram_gib") or h.get("gpu") is None and h.get("vram_gib"):
        return "the record does not say what machine it ran on"
    if h.get("id") == "cloud":
        return "cloud records are the site's own"
    m = rec.get("model") or {}
    if not (m.get("file") or m.get("path")):
        return "no model file named"
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
            if self.path != "/api/v1/runs":
                return self._json(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
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
