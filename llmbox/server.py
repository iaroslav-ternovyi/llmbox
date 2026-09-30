"""`llmbox serve`: the intake of submitted measurements (stdlib only, like the rest of llmbox).

  POST /api/v1/seed        {client} -> {seed}: the seed a quality test (`llmbox test`) draws its tasks from
  POST /api/v1/runs        a gzip JSON bundle from `llmbox submit` -> 202 {id, status, url}
  GET  /api/v1/runs/<id>   the submission's status: received / accepted / rejected (with the reason per record)
  POST /api/v1/login/github {access_token} from `llmbox login` (GitHub device flow) -> {key, handle}: the server checks
                           who the token belongs to (GET api.github.com/user), keeps the GitHub id, issues its own key
                           and never keeps the token
  GET  /api/v1/me          (Authorization: Bearer <key>) the account: handle, public or not, what it sent
  POST /api/v1/me          {public: true|false}: show the GitHub name on the profile page, or only the handle
  POST /api/v1/me/forget   delete the account and every result it sent (GDPR erasure)
  POST /api/v1/me/logout   revoke this key
  GET  /api/v1/login/web/start?return=<site page>   sign in on the site: to GitHub (web flow, needs the app's
                           secret in $LLMBOX_GITHUB_SECRET) and back to /callback, which issues a key and returns to the
                           site page with a one-time ticket in the #fragment (no cookie: the site and the API are two
                           origins); POST /api/v1/login/web/redeem {ticket} -> {key, handle}
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

import contextlib
import gzip
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import hwclass, results
from .hosts import HOME

MAX_BODY = 20 * 2**20
MAX_UNPACKED = 200 * 2**20    # a gzip body may not unpack to more (a small bomb would otherwise fill the memory)
PER_HOUR = 30                 # submissions per install id and per address
COMMUNITY = "community"
DB_SCHEMA = """CREATE TABLE IF NOT EXISTS submissions (
  id TEXT PRIMARY KEY, received TEXT, client TEXT, addr TEXT, bytes INTEGER, records INTEGER,
  status TEXT, reason TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS seeds (seed INTEGER PRIMARY KEY, client TEXT, issued TEXT, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, github_id INTEGER UNIQUE, login TEXT, handle TEXT UNIQUE,
  public INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS keys (hash TEXT PRIMARY KEY, user_id INTEGER, created TEXT, last_used TEXT);
CREATE TABLE IF NOT EXISTS web (token TEXT PRIMARY KEY, kind TEXT, value TEXT, expires REAL)"""
GH_AUTHORIZE = "https://github.com/login/oauth/authorize"
GH_TOKEN = "https://github.com/login/oauth/access_token"
# site pages a web sign-in may return to (the public site; the local one for development); $LLMBOX_SITE_ORIGINS adds more
SITE_ORIGINS = ["https://llmbox.pages.dev", "http://127.0.0.1:8766", "http://localhost:8766"] + \
    [o for o in os.environ.get("LLMBOX_SITE_ORIGINS", "").split(",") if o]
MISMATCH_MAX = 0.2            # share of re-graded answers that may differ before a quality run is rejected
SEEDS_PER_DAY = 20


class Intake:
    def __init__(self, data: str):
        self.data = os.path.expanduser(data)
        os.makedirs(os.path.join(self.data, "inbox"), exist_ok=True)
        self.lock = threading.Lock()
        with self._db() as c:
            c.executescript(DB_SCHEMA)
            if "user_id" not in {r[1] for r in c.execute("PRAGMA table_info(submissions)")}:
                c.execute("ALTER TABLE submissions ADD COLUMN user_id INTEGER")
        sp = os.path.join(self.data, "salt")   # handles are hashes of the GitHub id with this server's own salt
        if not os.path.exists(sp):
            import secrets
            fd = os.open(sp, os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(fd, secrets.token_hex(16).encode())
            os.close(fd)
        self.salt = open(sp).read().strip()
        self.github_user = github_user   # tests swap in a stub

    def _db(self) -> sqlite3.Connection:
        c = sqlite3.connect(os.path.join(self.data, "intake.db"), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def recent(self, client: str, addr: str) -> int:
        since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3600))
        with self._db() as c:
            return c.execute("SELECT count(*) FROM submissions WHERE received > ? AND (client = ? OR addr = ?)", (since, client, addr)).fetchone()[0]

    # ---- accounts: GitHub login, the llmbox key, the profile's visibility, erasure --------------------------------------

    def login(self, raw: bytes) -> tuple[int, dict]:
        try:
            tok = str(json.loads(raw or b"{}").get("access_token") or "")
        except ValueError:
            return 400, {"error": "not JSON"}
        gh = self.github_user(tok) if tok else None
        if not gh or not gh.get("id"):
            return 401, {"error": "GitHub did not accept that token"}
        return 200, self._issue(gh)

    def _issue(self, gh: dict) -> dict:
        """The account of a GitHub user (made on the first sign-in) and a new llmbox key for it."""
        import secrets
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        handle = "u-" + hashlib.sha256(f"{self.salt}:{gh['id']}".encode()).hexdigest()[:8]
        key = "lbx_" + secrets.token_urlsafe(32)
        with self.lock, self._db() as c:
            c.execute("INSERT INTO users (github_id, login, handle, public, created) VALUES (?,?,?,0,?) "
                      "ON CONFLICT(github_id) DO UPDATE SET login = excluded.login", (gh["id"], gh.get("login"), handle, now))
            uid, public = c.execute("SELECT id, public FROM users WHERE github_id = ?", (gh["id"],)).fetchone()
            c.execute("INSERT INTO keys VALUES (?,?,?,?)", (hashlib.sha256(key.encode()).hexdigest(), uid, now, now))
        self.export_users()
        return {"key": key, "handle": handle, "login": gh.get("login"), "public": bool(public)}

    def _stash(self, kind: str, value: str, ttl: float = 600) -> str:
        import secrets
        t = secrets.token_urlsafe(24)
        with self._db() as c:
            c.execute("DELETE FROM web WHERE expires < ?", (time.time(),))
            c.execute("INSERT INTO web VALUES (?,?,?,?)", (t, kind, value, time.time() + ttl))
        return t

    def _take(self, kind: str, token: str) -> str | None:
        """A one-time value: read and deleted; None when unknown, used or expired."""
        with self.lock, self._db() as c:
            r = c.execute("SELECT value, expires FROM web WHERE token = ? AND kind = ?", (token, kind)).fetchone()
            c.execute("DELETE FROM web WHERE token = ?", (token,))
        return r[0] if r and r[1] >= time.time() else None

    def web_start(self, api: str, ret: str) -> tuple[int, dict, str | None]:
        """(status, body, redirect): off to GitHub, remembering where on the site to come back to."""
        from urllib.parse import urlencode, urlsplit
        cid, secret = os.environ.get("LLMBOX_GITHUB_CLIENT_ID"), os.environ.get("LLMBOX_GITHUB_SECRET")
        if not cid or not secret:
            return 503, {"error": "sign-in on the site is not set up on this server yet; `llmbox login` works from a terminal"}, None
        o = urlsplit(ret)
        if f"{o.scheme}://{o.netloc}" not in SITE_ORIGINS:
            return 400, {"error": "not a page of the llmbox site"}, None
        state = self._stash("state", ret)
        q = urlencode({"client_id": cid, "redirect_uri": f"{api}/api/v1/login/web/callback", "state": state, "scope": "", "allow_signup": "true"})
        return 302, {}, f"{GH_AUTHORIZE}?{q}"

    def web_callback(self, api: str, code: str, state: str) -> tuple[int, dict, str | None]:
        """GitHub is back with a code: trade it for a token (the app's secret), look up the user, issue a key, and send
        the browser back to the site page with a one-time ticket for it."""
        import urllib.parse
        import urllib.request
        ret = self._take("state", state or "")
        if not ret or not code:
            return 400, {"error": "that sign-in link expired or was already used: start again from the site"}, None
        body = urllib.parse.urlencode({"client_id": os.environ.get("LLMBOX_GITHUB_CLIENT_ID", ""), "client_secret": os.environ.get("LLMBOX_GITHUB_SECRET", ""),
                                       "code": code, "redirect_uri": f"{api}/api/v1/login/web/callback"}).encode()
        try:
            req = urllib.request.Request(GH_TOKEN, data=body, method="POST", headers={"Accept": "application/json", "User-Agent": "llmbox"})
            with urllib.request.urlopen(req, timeout=20) as r:
                tok = json.loads(r.read()).get("access_token")
        except (OSError, ValueError):
            tok = None
        gh = self.github_user(tok) if tok else None
        if not gh:
            return 401, {"error": "GitHub did not confirm the sign-in"}, None
        acc = self._issue(gh)   # the GitHub token ends here: only the id and name are kept
        ticket = self._stash("ticket", json.dumps(acc), ttl=120)
        return 302, {}, f"{ret.split('#')[0]}#ticket={ticket}"

    def web_redeem(self, raw: bytes) -> tuple[int, dict]:
        try:
            t = str(json.loads(raw or b"{}").get("ticket") or "")
        except ValueError:
            return 400, {"error": "not JSON"}
        v = self._take("ticket", t)
        return (200, json.loads(v)) if v else (400, {"error": "that ticket expired or was already used: sign in again"})

    def revoke(self, auth: str) -> tuple[int, dict]:
        with self._db() as c:
            c.execute("DELETE FROM keys WHERE hash = ?", (hashlib.sha256(auth[7:].strip().encode()).hexdigest(),))
        return 200, {"signed_out": True}

    def user_of(self, auth: str | None) -> dict | None:
        """The account of an 'Authorization: Bearer <key>' header, or None."""
        if not auth or not auth.startswith("Bearer "):
            return None
        h = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
        with self._db() as c:
            r = c.execute("SELECT u.* FROM keys k JOIN users u ON u.id = k.user_id WHERE k.hash = ?", (h,)).fetchone()
            if r:
                c.execute("UPDATE keys SET last_used = ? WHERE hash = ?", (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), h))
        return dict(r) if r else None

    def me(self, user: dict, raw: bytes | None = None) -> tuple[int, dict]:
        if raw:
            try:
                pub = bool(json.loads(raw).get("public"))
            except ValueError:
                return 400, {"error": "not JSON"}
            with self._db() as c:
                c.execute("UPDATE users SET public = ? WHERE id = ?", (int(pub), user["id"]))
            user = dict(user, public=int(pub))
            self.export_users()
        with self._db() as c:
            n = c.execute("SELECT count(*), coalesce(sum(records), 0) FROM submissions WHERE user_id = ?", (user["id"],)).fetchone()
        return 200, {"handle": user["handle"], "login": user["login"], "public": bool(user["public"]), "submissions": n[0], "records": n[1]}

    def forget(self, user: dict, save_host: str = COMMUNITY) -> tuple[int, dict]:
        """Every result the account sent, its submissions, keys and the account itself: gone."""
        gone = 0
        for p, r in results.files(save_host):
            if (r.get("submission") or {}).get("user") == user["handle"]:
                os.remove(p)
                gone += 1
        with self.lock, self._db() as c:
            for (sid,) in c.execute("SELECT id FROM submissions WHERE user_id = ?", (user["id"],)).fetchall():
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(self.data, "inbox", f"{sid}.json.gz"))
            c.execute("DELETE FROM submissions WHERE user_id = ?", (user["id"],))
            c.execute("DELETE FROM keys WHERE user_id = ?", (user["id"],))
            c.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        with contextlib.suppress(Exception):
            from . import db
            db.sync()
        self.export_users()
        return 200, {"deleted_results": gone, "account": "deleted"}

    def export_users(self) -> None:
        """users.json for the site's profile pages: handle -> the GitHub name only when the person made it public."""
        with self._db() as c:
            us = {r["handle"]: {"login": r["login"] if r["public"] else None, "public": bool(r["public"])}
                  for r in c.execute("SELECT handle, login, public FROM users")}
        tmp = os.path.join(self.data, "users.json.tmp")
        json.dump(us, open(tmp, "w"))
        os.replace(tmp, os.path.join(self.data, "users.json"))

    def receive(self, raw: bytes, addr: str, user: dict | None = None) -> tuple[int, dict]:
        try:
            if raw[:2] == b"\x1f\x8b":
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                raw = d.decompress(raw, MAX_UNPACKED)
                if d.unconsumed_tail:
                    return 413, {"error": f"the bundle unpacks to more than {MAX_UNPACKED // 2**20} MB"}
            b = json.loads(raw)
        except (OSError, ValueError, EOFError, zlib.error):
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
                c.execute("INSERT INTO submissions (id, received, client, addr, bytes, records, status, reason, detail, user_id) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?)", (sid, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), client, a, len(raw),
                                                           len(b["records"]), "received", "", "[]", (user or {}).get("id")))
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
            handles = {r[0]: r[1] for r in c.execute("SELECT s.id, u.handle FROM submissions s JOIN users u ON u.id = s.user_id "
                                                     "WHERE s.status = 'received'")}
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
                    if sid not in handles:   # a quality run counts toward a score only from someone signed in
                        flags.append("anonymous")
                rec = dict(rec, submission={"id": sid, "client": hashlib.sha256(b["client"].encode()).hexdigest()[:12],
                                            "user": handles.get(sid), "received": time.strftime("%Y-%m-%dT%H:%M:%S"), "flags": flags})
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


def github_user(token: str) -> dict | None:
    """{id, login} of a GitHub token's owner (GET https://api.github.com/user), or None."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request("https://api.github.com/user", headers={"Authorization": f"Bearer {token}", "User-Agent": "llmbox",
                                                                        "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        return {"id": int(d["id"]), "login": d.get("login")}
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None


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

        def _api(self) -> str:   # this server's public address (behind Caddy: https and the real host name)
            return os.environ.get("LLMBOX_API_URL") or f"{self.headers.get('X-Forwarded-Proto') or 'http'}://{self.headers.get('Host')}"

        def _redirect(self, code: int, obj: dict, loc: str | None) -> None:
            if not loc:
                return self._json(code, obj)
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_OPTIONS(self):   # the site's pages call the API from another origin with a key: the browser asks first
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Max-Age", "86400")
            self.end_headers()

        def do_GET(self):
            from urllib.parse import parse_qs, urlsplit
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/api/v1/login/web/start":
                return self._redirect(*intake.web_start(self._api(), q.get("return", "")))
            if u.path == "/api/v1/login/web/callback":
                return self._redirect(*intake.web_callback(self._api(), q.get("code", ""), q.get("state", "")))
            if self.path == "/api/v1/health":
                return self._json(200, {"ok": True})
            if self.path == "/api/v1/me":
                u = intake.user_of(self.headers.get("Authorization"))
                return self._json(*intake.me(u)) if u else self._json(401, {"error": "not signed in (llmbox login)"})
            if self.path.startswith("/api/v1/runs/"):
                st = intake.status(self.path.rsplit("/", 1)[-1][:32])
                return self._json(200, st) if st else self._json(404, {"error": "no such submission"})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if self.path == "/api/v1/seed" and n <= 4096:
                return self._json(*intake.seed(self.rfile.read(n)))
            if self.path == "/api/v1/login/github" and n <= 4096:
                return self._json(*intake.login(self.rfile.read(n)))
            if self.path == "/api/v1/login/web/redeem" and n <= 4096:
                return self._json(*intake.web_redeem(self.rfile.read(n)))
            if self.path in ("/api/v1/me", "/api/v1/me/forget", "/api/v1/me/logout") and n <= 4096:
                u = intake.user_of(self.headers.get("Authorization"))
                if not u:
                    return self._json(401, {"error": "not signed in (llmbox login)"})
                body = self.rfile.read(n)
                if self.path.endswith("logout"):
                    return self._json(*intake.revoke(self.headers["Authorization"]))
                return self._json(*(intake.forget(u) if self.path.endswith("forget") else intake.me(u, body or None)))
            if self.path != "/api/v1/runs":
                return self._json(404, {"error": "not found"})
            if not 0 < n <= MAX_BODY:
                return self._json(413, {"error": f"body must be 1 byte to {MAX_BODY // 2**20} MB"})
            # behind Caddy: the last X-Forwarded-For entry is the one Caddy added (a client can send its own first ones)
            addr = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or self.client_address[0]
            u = None
            if self.headers.get("Authorization"):
                u = intake.user_of(self.headers.get("Authorization"))
                if not u:
                    return self._json(401, {"error": "that llmbox key is not valid any more: llmbox login"})
            code, obj = intake.receive(self.rfile.read(n), addr, u)
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
