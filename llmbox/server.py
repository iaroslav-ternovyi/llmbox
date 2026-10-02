"""`llmbox serve`: the intake of submitted measurements (stdlib only, like the rest of llmbox).

  POST /api/v1/seed        {client} -> {seed}: the seed a quality test (`llmbox test`) draws its tasks from
  POST /api/v1/runs        a gzip JSON bundle from `llmbox submit` -> 202 {id, status, url, page, ahead, live_at}
  GET  /api/v1/runs/<id>   the submission's status: received / accepted / rejected (with the reason per record), and
                           for the site's queue page (r/<id> before it exists): published (when its page went out),
                           live_at (when it should) and delayed (the last publish failed)
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
  GET  /api/v1/health      ok; 503 while a result card could not be drawn in the last day (the uptime monitor alerts)

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
import re
import subprocess
import hashlib
import hmac
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
CREATE TABLE IF NOT EXISTS web (token TEXT PRIMARY KEY, kind TEXT, value TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS publishes (at REAL, ok INTEGER, n INTEGER)"""
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
            cols = {r[1] for r in c.execute("PRAGMA table_info(submissions)")}
            if "user_id" not in cols:
                c.execute("ALTER TABLE submissions ADD COLUMN user_id INTEGER")
            if "published" not in cols:   # when the site that shows this submission went out
                c.execute("ALTER TABLE submissions ADD COLUMN published TEXT")
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

    def addr_key(self, addr: str) -> str:
        """A request's address for rate limits only: keyed with this server's salt and the day (an unkeyed hash of an IPv4
        address is undone by trying all 4 billion), and cleared after two days (prune)."""
        day = time.strftime("%Y-%m-%d", time.gmtime())
        return hmac.new(f"{self.salt}:{day}".encode(), (addr or "").encode(), hashlib.sha256).hexdigest()[:16]

    ERRORS_KEEP_DAYS = 30
    ERRORS_MAX_BYTES = 5 * 2**20

    def site_error(self, raw: bytes) -> tuple[int, dict]:
        """An error in one of the site's scripts: the message, the page, the build - no address, no browser string; kept
        30 days (prune), at most 5 MB (a flood is dropped, not stored)."""
        try:
            d = json.loads(raw or b"{}")
        except ValueError:
            return 400, {"error": "not JSON"}
        if not isinstance(d, dict):
            return 400, {"error": "not an object"}
        p = os.path.join(self.data, "site-errors.jsonl")
        if os.path.exists(p) and os.path.getsize(p) > self.ERRORS_MAX_BYTES:
            return 202, {"status": "dropped"}
        row = {"t": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), "msg": str(d.get("msg") or "")[:300],
               "page": str(d.get("page") or "")[:120], "src": str(d.get("src") or "")[:160], "build": str(d.get("build") or "")[:40]}
        with self.lock, open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return 202, {"status": "kept"}

    def prune(self) -> None:
        """Address keys older than two days go: the limits look back an hour (submissions) and a day (seeds)."""
        before = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 2 * 86400))
        with self.lock, self._db() as c:
            c.execute("UPDATE submissions SET addr = '' WHERE received < ? AND addr != ''", (before,))
            c.execute("DELETE FROM seeds WHERE client LIKE 'addr:%' AND issued < ?", (before,))
        p = os.path.join(self.data, "site-errors.jsonl")   # site script errors: 30 days
        if os.path.exists(p):
            old = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - self.ERRORS_KEEP_DAYS * 86400))
            with self.lock:
                rows = [l for l in open(p, encoding="utf-8") if l[7:26] >= old]   # '{"t": "<iso>"...'
                with open(p + ".tmp", "w", encoding="utf-8") as f:
                    f.writelines(rows)
                os.replace(p + ".tmp", p)

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
        if not app_token(tok):   # with the app's secret here: a token of this app only, not any GitHub token
            return 401, {"error": "that token was not issued to llmbox's GitHub app: sign in with llmbox login"}
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
        if any(ord(c) < 32 or c in "\\ " for c in ret) or len(ret) > 400:
            return 400, {"error": "not a page of the llmbox site"}, None
        o = urlsplit(ret)
        if f"{o.scheme}://{o.netloc}" not in SITE_ORIGINS:
            return 400, {"error": "not a page of the llmbox site"}, None
        state = self._stash("state", o.geturl())
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
        removed = []
        with self.lock, self._db() as c:
            for sid, st in c.execute("SELECT id, status FROM submissions WHERE user_id = ?", (user["id"],)).fetchall():
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(self.data, "inbox", f"{sid}.json.gz"))
                if st == "accepted":   # it had a page: the page says it was removed, its card goes
                    removed.append(sid)
            c.execute("DELETE FROM submissions WHERE user_id = ?", (user["id"],))
            c.execute("DELETE FROM keys WHERE user_id = ?", (user["id"],))
            c.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        withdraw(removed, "owner")
        with open(os.path.join(self.data, "erased.txt"), "a") as f:   # a run still being checked, or a stale copy, stays out
            f.write(user["handle"] + "\n")
        with contextlib.suppress(Exception):
            from . import db
            db.sync()
        self.export_users()
        open(os.path.join(self.data, "rebuild"), "w").close()   # the site without it at the next loop of the intake
        return 200, {"deleted_results": gone, "account": "deleted"}

    def erased(self, handle: str) -> bool:
        p = os.path.join(self.data, "erased.txt")
        return os.path.exists(p) and handle in open(p).read().split()

    def export_users(self) -> None:
        """users.json for the site's profile pages: handle -> the GitHub name only when the person made it public."""
        with self._db() as c:
            us = {r["handle"]: {"login": r["login"] if r["public"] else None, "public": bool(r["public"])}
                  for r in c.execute("SELECT handle, login, public FROM users")}
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=self.data, prefix=".users-", suffix=".json")   # two sign-ins at once each write their own
        with os.fdopen(fd, "w") as f:
            json.dump(us, f)
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
        a = self.addr_key(addr)   # the address itself is not kept
        with self.lock:
            if self.recent(client, a) >= PER_HOUR:
                return 429, {"error": f"more than {PER_HOUR} submissions in an hour; try later"}
            sid = uuid.uuid4().hex[:12]
            open(os.path.join(self.data, "inbox", f"{sid}.json.gz"), "wb").write(gzip.compress(json.dumps(b).encode()))
            with self._db() as c:
                c.execute("INSERT INTO submissions (id, received, client, addr, bytes, records, status, reason, detail, user_id) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?)", (sid, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), client, a, len(raw),
                                                           len(b["records"]), "received", "", "[]", (user or {}).get("id")))
        return 202, {"id": sid, "status": "received", "url": f"/api/v1/runs/{sid}", "page": f"r/{sid}", **self.queue(sid)}

    def seed(self, raw: bytes, addr: str = "") -> tuple[int, dict]:
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
            a = self.addr_key(addr)
            if c.execute("SELECT count(*) FROM seeds WHERE (client = ? OR client = ?) AND issued > ?", (client, "addr:" + a, since)).fetchone()[0] >= SEEDS_PER_DAY:
                return 429, {"error": f"more than {SEEDS_PER_DAY} tests a day from this install"}
            s = 10**6 + secrets.randbelow(10**9)
            now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            c.execute("INSERT OR IGNORE INTO seeds VALUES (?,?,?,0)", (s, client, now))
            c.execute("INSERT OR IGNORE INTO seeds VALUES (?,?,?,1)", (-s, "addr:" + a, now))   # the address's count (no seed)
        return 200, {"seed": s}

    def seeded(self, client: str, seed) -> bool:
        """A seed this server gave this install and no run used yet (it is used now)."""
        with self.lock, self._db() as c:
            ok = c.execute("SELECT 1 FROM seeds WHERE seed = ? AND client = ? AND used = 0", (seed, client)).fetchone() is not None
            if ok:
                c.execute("UPDATE seeds SET used = 1 WHERE seed = ?", (seed,))
            return ok

    def queue(self, sid: str) -> dict:
        """Where a received submission stands: how many are checked before it (a quality run is re-graded in the sandbox,
        a few minutes each), and when the site should show it: the first publish after the check (at most every
        PUBLISH_EVERY_S), plus the build and upload."""
        with self._db() as c:
            r = c.execute("SELECT rowid FROM submissions WHERE id = ?", (sid,)).fetchone()   # arrival order (timestamps are to the second)
            ahead = c.execute("SELECT count(*) FROM submissions WHERE status = 'received' AND rowid < ?", (r[0],)).fetchone()[0] if r else 0
        return {"ahead": ahead, **self._live(time.time() + CHECK_S * (ahead + 1))}

    def _live(self, checked: float) -> dict:
        """When a submission checked by `checked` (epoch seconds) should be on the site: live_at (UTC, for the site's queue
        page, which shows it in the visitor's own time) and site_within_min (for the CLI)."""
        last, _ok = self.last_publish()
        at = max(checked, last + PUBLISH_EVERY_S) + BUILD_S
        return {"live_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)), "site_within_min": max(1, round((at - time.time()) / 60))}

    def last_publish(self) -> tuple[float, bool]:
        """(when, whether it worked) of the last publish attempt; (0, True) before the first."""
        with self._db() as c:
            r = c.execute("SELECT at, ok FROM publishes ORDER BY rowid DESC LIMIT 1").fetchone()
        return (r[0], bool(r[1])) if r else (0.0, True)

    def published(self, ids: list[str], ok: bool) -> None:
        """A publish attempt (the serve loop, after the site build and its upload): on success the submissions it carried
        are live, so their queue pages stop waiting; on failure every waiting page says the publish is delayed."""
        now = time.time()
        with self._db() as c:
            c.execute("INSERT INTO publishes VALUES (?,?,?)", (now, int(ok), len(ids)))
            if ok and ids:
                c.executemany("UPDATE submissions SET published = ? WHERE id = ? AND published IS NULL",
                              [(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), i) for i in ids])
            c.execute("DELETE FROM publishes WHERE rowid NOT IN (SELECT rowid FROM publishes ORDER BY rowid DESC LIMIT 100)")

    def status(self, sid: str) -> dict | None:
        with self._db() as c:
            r = c.execute("SELECT id, received, records, status, reason, detail, published FROM submissions WHERE id = ?", (sid,)).fetchone()
        if not r:
            return None
        out = dict(r, detail=json.loads(r["detail"] or "[]"))
        if r["status"] != "rejected":
            out["page"] = f"r/{sid}"
            if not r["published"]:
                out.update(self.queue(sid) if r["status"] == "received" else self._live(time.time()))
                out["delayed"] = not self.last_publish()[1]
        return out

    def ingest(self, save_host: str = COMMUNITY, predict=None) -> list[str]:
        """Check every received submission and file its accepted records; returns the submission ids done."""
        self.prune()
        with self._db() as c:
            todo = [r["id"] for r in c.execute("SELECT id FROM submissions WHERE status = 'received' ORDER BY received")]
            handles = {r[0]: r[1] for r in c.execute("SELECT s.id, u.handle FROM submissions s JOIN users u ON u.id = s.user_id "
                                                     "WHERE s.status = 'received'")}
        done = []
        for sid in todo:   # one submission at a time, each on its own: a bad one is rejected, never stuck in front of the others
            try:
                status, reason, detail = self._ingest_one(sid, handles.get(sid), save_host, predict)
            except Exception as e:
                status, reason, detail = "rejected", f"could not be processed: {type(e).__name__}", []
                print(f"ingest {sid}: {type(e).__name__}: {str(e)[:200]}", flush=True)
            if status is None:   # held (no sandbox here for a quality run): stays received for an intake that has one
                continue
            with self._db() as c:
                c.execute("UPDATE submissions SET status = ?, reason = ?, detail = ? WHERE id = ?", (status, reason, json.dumps(detail), sid))
            done.append(sid)
        return done

    def _ingest_one(self, sid: str, handle: str | None, save_host: str, predict) -> tuple:
        """(status, reason, detail) of one submission; status None = hold it (a quality run and no sandbox)."""
        b = json.loads(gzip.decompress(open(os.path.join(self.data, "inbox", f"{sid}.json.gz"), "rb").read()))
        if any(isinstance(r, dict) and r.get("kind") == "suite" for r in b["records"]) and not sandboxed():
            print(f"ingest {sid}: a quality run and no sandbox (LLMBOX_SANDBOX): held", flush=True)
            return None, "", []
        detail, ok = [], 0
        for n, rec in enumerate(b["records"]):
            why = check(rec)
            if why:
                detail.append({"record": str(rec.get("id"))[:36] if isinstance(rec, dict) else "?", "status": "rejected", "reason": why})
                continue
            flags = outlier_flags(rec, predict)
            if rec.get("kind") == "suite":
                rec, why, qflags = self.regrade(rec, b["client"])
                if why:
                    detail.append({"record": str(rec.get("id"))[:36], "status": "rejected", "reason": why})
                    continue
                flags += qflags
                if not handle:   # a quality run counts toward a score only from someone signed in
                    flags.append("anonymous")
            if handle and self.erased(handle):   # the account was deleted while this run was being checked
                return "rejected", "the account was deleted", []
            rec = dict(rec, submission={"id": sid, "client": hashlib.sha256(b["client"].encode()).hexdigest()[:12],
                                        "user": handle, "received": time.strftime("%Y-%m-%dT%H:%M:%S"), "flags": flags})
            rec["host"] = dict(rec["host"], **{"class": hwclass.of_host(rec["host"])})
            # named by the submission, never by what it says (its time or recipe id cannot choose a path or overwrite a record)
            results.save(save_host, rec, name=f"{sid}-{n:02d}-{rec['kind']}.json")
            ok += 1
            detail.append({"record": rec["id"][:36], "status": "accepted", "flags": flags})
        return ("accepted" if ok else "rejected"), f"{ok} of {len(b['records'])} records accepted", detail


    def regrade(self, rec: dict, client: str) -> tuple[dict, str | None, list[str]]:
        """A quality run with the server's own grades: (record, why it is rejected or None, flags)."""
        from . import verify
        work = os.path.join(self.data, "work")
        os.makedirs(work, exist_ok=True)
        p = os.path.join(work, f"{uuid.uuid4().hex}.json")
        json.dump(rec, open(p, "w"))
        try:
            res = verify.run_one(p, rec, timeout=1200)
        except subprocess.TimeoutExpired:
            return rec, "re-grading took over 20 minutes", []
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


def withdraw(sids: list[str], reason: str) -> None:
    """Runs taken off the site: at its owner's request (account erasure, reason "owner") or after review ("review").
    Each one's frozen card spec (<HOME>/cards/<id>.json) is deleted and its id listed in cards/removed.jsonl with the
    reason only, so the next build replaces r/<id>.html with a short "removed" page and deletes r/<id>.png."""
    if not sids:
        return
    d = os.path.join(HOME, "cards")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "removed.jsonl"), "a") as f:
        for sid in sids:
            for ext in (".json", ".failed"):
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(d, sid + ext))
            f.write(json.dumps({"id": sid, "reason": reason, "at": time.strftime("%Y-%m-%d")}) + "\n")


def cards_failed(within_s: int = 86400) -> int:
    """Cards the site build gave up on (<HOME>/cards/<id>.failed, written after its third failed try) in the last day:
    the health check reports them so the uptime monitor raises an alert."""
    import glob
    now = time.time()
    return sum(1 for p in glob.glob(os.path.join(HOME, "cards", "*.failed")) if now - os.path.getmtime(p) < within_s)


def sandboxed() -> bool:
    """A stranger's quality run is re-graded only inside the sandbox (LLMBOX_SANDBOX); LLMBOX_UNSANDBOXED_OK=1 is for
    tests on a machine without bubblewrap."""
    return bool(os.environ.get("LLMBOX_SANDBOX") or os.environ.get("LLMBOX_UNSANDBOXED_OK"))


def app_token(token: str) -> bool:
    """Whether GitHub issued this token to llmbox's own app (POST /applications/{client_id}/token, basic auth with the
    app's secret). Without the secret configured here the check cannot run: any token that names a user is taken."""
    import base64
    import urllib.error
    import urllib.request
    cid, secret = os.environ.get("LLMBOX_GITHUB_CLIENT_ID"), os.environ.get("LLMBOX_GITHUB_SECRET")
    if not cid or not secret:
        return True
    req = urllib.request.Request(f"https://api.github.com/applications/{cid}/token", data=json.dumps({"access_token": token}).encode(),
                                 method="POST", headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode(),
                                                         "Accept": "application/vnd.github+json", "User-Agent": "llmbox"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


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


ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")
WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{4}|Z)?$")
REC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{2,63}$")
ROW_ID = re.compile(r"^[a-z]+\.[a-z0-9_]+\.L\d{1,2}\.\d{1,15}$")


def _num(x, lo: float, hi: float) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and lo <= x <= hi


def _text(x, n: int = 200, need: bool = True) -> bool:
    return (x is None and not need) or (isinstance(x, str) and 0 < len(x) <= n)


def check(rec: dict) -> str | None:
    """Why a submitted record cannot be used, or None. Strict about types and sizes: a record is filed and later read
    by every site build and every erasure, so one malformed field would break them for everyone."""
    if not isinstance(rec, dict):
        return "a record must be an object"
    if rec.get("schema") != results.SCHEMA:
        return "unknown record schema"
    if rec.get("kind") not in ("speed", "optimize", "suite"):
        return f"kind {str(rec.get('kind'))[:20]!r} is not taken (speed, optimize and suite are)"
    if not isinstance(rec.get("id"), str) or not REC_ID.match(rec["id"]):
        return "the record id is missing or malformed"
    if not isinstance(rec.get("created"), str) or not WHEN.match(rec["created"]):
        return "the record's time is missing or malformed"
    h = rec.get("host")
    if not isinstance(h, dict) or h.get("id") == "cloud":
        return "the record does not say what machine it ran on" if not isinstance(h, dict) else "cloud records are the site's own"
    if not (_text(h.get("cpu")) and _num(h.get("ram_gib"), 0.5, 65536) and _text(h.get("gpu"), 120, need=False)
            and (h.get("vram_gib") is None or _num(h.get("vram_gib"), 0, 4096)) and (h.get("threads") is None or _num(h.get("threads"), 1, 4096))
            and (h.get("ram_read_gbs") is None or _num(h.get("ram_read_gbs"), 0.1, 20000)) and _text(h.get("os"), 200, need=False)
            and (h.get("gpu_count") is None or _num(h.get("gpu_count"), 0, 64))):
        return "the machine's description has a missing or malformed field"
    m, rc_ = rec.get("model"), rec.get("recipe")
    if not isinstance(m, dict) or not (_text(m.get("file"), 200) or _text(m.get("path"), 400)):
        return "no model file named"
    if not isinstance(rc_, dict) or not isinstance(rc_.get("id"), str) or not ID.match(rc_["id"]):
        return "the recipe id is missing or malformed"
    if len(json.dumps(rec)) > (15 if rec.get("kind") == "suite" else 5) * 2**20:
        return "record too large"
    # what the site's pages and result cards read besides the machine: the recipe's tables and the server line built
    # from them, the llama.cpp build, the client's own prediction, a quality run's summary
    if not all(isinstance(rc_.get(k, {}), dict) for k in ("runtime", "placement", "speculative", "sampling", "chat", "antiloop", "serve", "extra", "model")):
        return "the recipe has a malformed table"
    if not _text((rc_.get("model") or {}).get("path"), 400, need=False) or not _text((rc_.get("runtime") or {}).get("engine"), 40, need=False):
        return "the recipe has a malformed field"
    args = (rc_.get("extra") or {}).get("args", [])
    if not isinstance(args, list) or len(args) > 64 or not all(isinstance(x, (str, int, float)) and len(str(x)) <= 200 for x in args):
        return "the recipe's extra arguments are malformed"
    rt = rec.get("runtime", {})
    if not isinstance(rt, dict) or not _text(rt.get("llama_cpp_build"), 40, need=False):
        return "the runtime description is malformed"
    pr = rec.get("prediction", {})
    if not isinstance(pr, dict) or not all(v is None or _num(v, 0, 10**6) for k, v in pr.items() if k.endswith("_tps") or "tps_" in k):
        return "the prediction is malformed"
    if rec.get("kind") == "suite":
        from . import irt
        su = rec.get("suite")
        if not isinstance(su, dict) or irt.canonical(su.get("content_hash")) not in irt.RELEASES:
            return "the suite is not a released version: update llmbox"
        if su.get("tier") != "adaptive" or not _num(su.get("seed0"), 0, 10**13):
            return "a quality run must be an adaptive test with its seed"
        rows = rec.get("rows")
        if not isinstance(rows, list) or not 0 < len(rows) <= 400 or not all(isinstance(r, dict) and isinstance(r.get("id"), str)
                                                                            and ROW_ID.match(r["id"]) for r in rows):
            return "a quality run must carry its answers, each with a task id"
        sm = rec.get("summary", {})
        ci = sm.get("capability_ci95") if isinstance(sm, dict) else None
        if not isinstance(sm, dict) or (sm.get("capability") is not None and not _num(sm.get("capability"), 0, 100)) or \
                (ci is not None and not (isinstance(ci, list) and len(ci) == 2 and all(_num(x, 0, 100) for x in ci))):
            return "the quality run's summary is malformed"
        return None
    sp = (rec.get("speed") if rec.get("kind") == "speed" else ((rec.get("runs") or {}).get("llmbox") or {})) or {}
    if not isinstance(sp, dict):
        return "speed figures missing"
    dec = sp.get("decode_tps") if rec.get("kind") == "speed" else sp.get("decode")
    pre = sp.get("prefill_tps") if rec.get("kind") == "speed" else sp.get("prefill")
    if not _num(dec, 0.01, 2000) or (pre is not None and not _num(pre, 0.01, 100_000)):
        return "speed figures missing or out of range"
    for d in sp.get("depth") or []:
        if not isinstance(d, dict) or not all(d.get(k) is None or _num(d.get(k), 0, 10**7) for k in ("depth", "decode_tps", "prefill_tps")):
            return "a depth measurement is malformed"
    return None


def predicted(rec: dict) -> float | None:
    """What the speed formula predicts for a submitted speed record, worked out here from the published recipe and the
    picker entry the machine belongs to (never the client's own figure, which the sender could set). None when the
    machine is outside the picker or the recipe is not published: then the client's figure stands (no credit, no map
    cell depends on those)."""
    try:
        from . import fit as F, pick, registry
        from .hosts import HOME as _H   # noqa: F401  (the box's recipes and shapes are synced here)
        host = rec.get("host") or {}
        cls = host.get("class") or hwclass.of_host(host)
        name = hwclass.display_class(cls)
        if not name:
            return None
        r = registry.published("box", (rec.get("recipe") or {})["id"])
        shape = F.shape_for(r)
        ram_bw = min(float(host.get("ram_read_gbs") or 60.0), 130.0)
        hw = pick.entry_spec(name, int(host.get("ram_gib") or 64), ram_bw)
        cal = registry.calibration(r, shape)
        if hwclass.entry_kind(name) in ("mac", "chip"):
            cal = F.Calibration(deep_k=cal.deep_k)
        f = F.fit(r, shape, hw, cal=cal)
        return f.tps if f.fits else None
    except (OSError, ValueError, KeyError, TypeError, SystemExit):
        return None


def outlier_flags(rec: dict, predict=None) -> list[str]:
    """'outlier' when the decode figure is over twice what the formula predicts for that machine (predict(rec) -> tok/s)."""
    if rec.get("kind") != "speed":
        return []
    pred = (predict(rec) if predict else None) or ((rec.get("prediction") or {}).get("decode_tps_no_spec"))
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
            if self.path == "/api/v1/health":   # not ok (503) while a result card could not be drawn in the last day
                n = cards_failed()
                return self._json(503, {"ok": False, "cards_failed_24h": n}) if n else self._json(200, {"ok": True})
            if self.path == "/api/v1/me":
                u = intake.user_of(self.headers.get("Authorization"))
                return self._json(*intake.me(u)) if u else self._json(401, {"error": "not signed in (llmbox login)"})
            if self.path.startswith("/api/v1/runs/"):
                st = intake.status(self.path.rsplit("/", 1)[-1][:32])
                return self._json(200, st) if st else self._json(404, {"error": "no such submission"})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if self.path == "/api/v1/err" and n <= 4096:   # a site script's error (sendBeacon, text/plain: no preflight)
                return self._json(*intake.site_error(self.rfile.read(n)))
            if self.path == "/api/v1/seed" and n <= 4096:
                return self._json(*intake.seed(self.rfile.read(n), self.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or self.client_address[0]))
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


def _code_version() -> str:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip()


# a publish (site build + upload) at most this often: a rush of submissions would otherwise publish every 30 s and use
# up the host's deployment allowance; what arrives in between goes out together
PUBLISH_EVERY_S = int(os.environ.get("LLMBOX_PUBLISH_EVERY_S", "900"))
CHECK_S = 240    # one submission's check, a quality run's re-grade included (an estimate for waiting times)
BUILD_S = 120    # a site build and its upload


def publish(intake: Intake, waiting: list[list[str]], on_accept) -> tuple[list[list[str]], float]:
    """One publish of everything accepted since the last: on_accept(ids) builds and uploads the site (False, or an
    exception, when the upload failed); the attempt is recorded for the queue pages. A failed publish keeps its
    submissions for the next try, one interval later. Returns (still waiting, when this attempt was made)."""
    batch = [i for x in waiting for i in x]
    try:
        ok = on_accept(batch) is not False   # an on_accept that returns nothing counts as published
    except Exception as e:
        print(f"publish error: {e}", flush=True)
        ok = False
    intake.published(batch, ok)
    return ([] if ok else [batch]), time.time()


def serve(data: str, port: int = 8767, bind: str = "127.0.0.1", every_s: int = 30, on_accept=None,
          publish_every_s: int = PUBLISH_EVERY_S) -> None:
    """Run the intake; a thread ingests new submissions every every_s seconds and calls on_accept(ids) after any, or
    when <data>/rebuild asks (deploy/sync.sh, an erasure) - at most once per publish_every_s, with everything since the
    last call. When the code under it changes (git pull), it exits after the loop so its service manager starts the new code."""
    intake = Intake(data)
    started = _code_version()

    def loop():
        waiting, last = [], 0.0
        while True:
            try:
                ids = intake.ingest(predict=predicted)
                req = os.path.join(intake.data, "rebuild")
                if ids or os.path.exists(req):
                    waiting.append(ids or [])
                    with contextlib.suppress(OSError):
                        os.remove(req)
                if waiting and on_accept and time.time() - last >= publish_every_s:
                    waiting, last = publish(intake, waiting, on_accept)
            except Exception as e:   # one bad bundle must not stop the intake
                print(f"ingest error: {e}", flush=True)
            if started and _code_version() != started:
                print("new code: restarting", flush=True)
                os._exit(0)
            time.sleep(every_s)
    threading.Thread(target=loop, daemon=True).start()
    print(f"llmbox intake on http://{bind}:{port}/api/v1/ (data {intake.data})")
    ThreadingHTTPServer((bind, port), handler(intake)).serve_forever()
