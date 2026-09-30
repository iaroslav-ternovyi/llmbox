"""`llmbox submit`: send your speed measurements to the shared results, so the site can say how fast a model is on
machines like yours (docs/roadmap.md §4-5).

What leaves the machine is the result records as saved, with everything that names you or the machine taken out:
the home folder in paths (/home/<you>/models/x.gguf -> ~/models/x.gguf), the user and host names wherever they
appear, the recipe's free-text notes, and the host profile's ssh address (records never carried it). What stays: the hardware (card, CPU, RAM and
its measured speed, OS, driver), the engine build and flags, the model file and its sha256, the numbers. `--dry-run`
prints the bundle instead of sending it.

Each machine sends under a random install id (~/.llmbox/install-id); the server uses it to count machines and to
rate-limit. Signed in (`llmbox login`), the results also go to your account and profile page, and quality runs count
toward the models' scores (anonymous ones are filed only). Speed cannot be checked, so the site shows medians and
flags outliers; quality answers are graded again by the server.
"""
from __future__ import annotations

import getpass
import gzip
import json
import os
import re
import socket
import urllib.error
import urllib.request
import uuid

from . import __version__, results
from .hosts import HOME

SCHEMA = "llmbox.submission/1"
KINDS = ("speed", "optimize", "suite")   # a suite run is re-graded by the server (llmbox/server.py)
from . import public
DEFAULT_SERVER = public.server()   # the intake (llmbox/public.py)
LEDGER = os.path.join(HOME, "submitted.json")


def install_id() -> str:
    p = os.path.join(HOME, "install-id")
    if not os.path.exists(p):
        os.makedirs(HOME, exist_ok=True)
        open(p, "w").write(uuid.uuid4().hex)
    return open(p).read().strip()


def _private_words(extra: tuple = ()) -> list[str]:
    """Names that must not leave: this user, this machine, and the users and hosts in the registered ssh addresses."""
    words = {getpass.getuser(), socket.gethostname().split(".")[0]}
    d = os.path.join(HOME, "hosts")
    for f in os.listdir(d) if os.path.isdir(d) else []:
        try:
            prof = json.load(open(os.path.join(d, f)))
        except (OSError, ValueError):
            continue
        ssh = prof.get("ssh") or ""
        words |= {x for x in re.split(r"[@:]", ssh) if x}
        words.add(((prof.get("hw") or {}).get("hostname") or "").split(".")[0])
    return sorted((w for w in words | set(extra) if w and len(w) >= 3), key=len, reverse=True)


COMMON = {"llmbox", "llama", "server", "model", "models", "local", "localhost", "ubuntu", "debian", "root", "admin", "user",
          "users", "home", "host", "box", "linux", "mac", "macbook", "desktop", "workstation", "runner", "cuda", "nvidia"}


def scrub(obj, words: list[str] | None = None):
    """The record with home folders shortened to ~ and private names replaced (whole words, not a word llmbox itself
    uses); ssh, hostname and free-text notes dropped. The answers (rows) are left exactly as the model gave them: the
    server grades them again, and a changed answer would grade differently."""
    words = _private_words() if words is None else words
    words = [w for w in words if w.lower() not in COMMON]
    pats = [(re.compile(r"(/home|/Users)/[^/\s\"']+"), "~")] + [(re.compile(rf"(?<![\w.-]){re.escape(w)}(?![\w-])", re.I), "user") for w in words]

    def s(x, top=False):
        if isinstance(x, str):
            for p, r in pats:
                x = p.sub(r, x)
            return x
        if isinstance(x, dict):
            return {k: (v if top and k == "rows" else s(v)) for k, v in x.items() if k not in ("ssh", "hostname", "notes")}
        if isinstance(x, list):
            return [s(v) for v in x]
        return x
    return s(obj, top=True)


def ledger() -> dict:
    try:
        return json.load(open(LEDGER))
    except (OSError, ValueError):
        return {}


def pending(host: str | None = None, kinds: tuple = KINDS) -> list[tuple[str, dict]]:
    """Records of these kinds not sent yet."""
    sent = ledger()
    return [(p, r) for p, r in results.files(host) if r.get("kind") in kinds and r.get("id") not in sent
            and (r.get("host") or {}).get("id") != "cloud"]


def bundle(records: list[dict]) -> dict:
    words = _private_words()
    return {"schema": SCHEMA, "client": install_id(), "llmbox": __version__, "records": [scrub(r, words) for r in records]}


def fresh_seed(server: str = DEFAULT_SERVER) -> int | None:
    """A seed from the server for the next quality test (its tasks cannot be prepared in advance); None if unreachable."""
    req = urllib.request.Request(server.rstrip("/") + "/api/v1/seed", data=json.dumps({"client": install_id()}).encode(),
                                 method="POST", headers={"Content-Type": "application/json", "User-Agent": f"llmbox/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(json.loads(r.read())["seed"])
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None


def send(b: dict, server: str = DEFAULT_SERVER, timeout: int = 120) -> dict:
    from . import account
    body = gzip.compress(json.dumps(b).encode())
    headers = {"Content-Type": "application/json", "Content-Encoding": "gzip", "User-Agent": f"llmbox/{__version__}"}
    if account.key_for(server):   # signed in: the results go to your account (quality runs count toward the scores)
        headers["Authorization"] = f"Bearer {account.key_for(server)}"
    req = urllib.request.Request(server.rstrip("/") + "/api/v1/runs", data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        raise SystemExit(f"{server}: {e.code} {msg or e.reason}")
    except urllib.error.URLError as e:
        raise SystemExit(f"{server}: not reachable ({e.reason})")


def ask_and_send(paths: list[str], host: str, server: str, yes: bool = False, out=print) -> bool:
    """On a terminal: send these records? y / n / s (show exactly what is sent, then ask again). --yes never sends:
    sending needs its own yes."""
    import sys
    if yes or not sys.stdin.isatty():
        out("not sent (answer yes on a terminal, or `llmbox submit` later)")
        return False
    while True:
        a = (input("Send it, so the site shows this hardware and counts the answers? (anonymous unless you signed in; "
                   "s = show exactly what is sent) [Y/n/s] ").strip().lower()[:1] or "y")
        if a == "s":
            run(paths, host, server, dry_run=True, out=out)
        elif a in "yn":
            break
    if a == "y":
        run(paths, host, server, dry_run=False, out=out)
    return a == "y"


def run(paths: list[str], host: str | None, server: str, dry_run: bool, out=print) -> int:
    todo = [(p, json.load(open(p))) for p in paths] if paths else pending(host)
    todo = [(p, r) for p, r in todo if r.get("kind") in KINDS]
    if not todo:
        out("nothing to send: every speed measurement was sent already (llmbox speed / optimize make new ones)")
        return 0
    b = bundle([r for _p, r in todo])
    if dry_run:
        print(json.dumps(b, indent=1))
        out(f"\n{len(todo)} record(s) above would go to {server} (nothing sent)")
        return 0
    res = send(b, server)
    led = ledger()
    for (_p, r) in todo:
        led[r["id"]] = res.get("id")
    json.dump(led, open(LEDGER, "w"), indent=1)
    out(f"sent {len(todo)} record(s): the server checks them within a minute - {server.rstrip('/')}{res['url']}" if res.get("url")
        else f"sent {len(todo)} record(s): submission {res.get('id')} {res.get('status')}")
    from . import registry
    rids = sorted({(r.get("recipe") or {}).get("id") for _p, r in todo} - {None})
    if rids:   # where it shows up (the site rebuilds after the check)
        out("on the site: " + ", ".join(f"{registry.DEFAULT_URL.rstrip('/')}/hardware-{rid}.html" for rid in rids[:3]))
    return 0
