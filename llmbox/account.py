"""Signing in: `llmbox login` with GitHub's device flow, the way `gh auth login` works in a terminal.

llmbox shows a short code; you enter it at github.com/login/device and approve. GitHub gives llmbox a token for this
app only, llmbox hands it to the server once, the server looks up who you are (your GitHub id) and gives back its own
key. The GitHub token is not kept anywhere; the key is in ~/.llmbox/credentials.json (readable by you only).

Signed in, your quality runs count toward the models' scores (anonymous ones are filed but not counted) and your
results get a profile page. It shows a handle (u-1a2b3c4d), not your GitHub name, unless you choose
`llmbox profile --public`. `llmbox forget` deletes the account and everything you sent.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .hosts import HOME

# the GitHub OAuth app llmbox signs in with (public: device flow needs no secret); $LLMBOX_GITHUB_CLIENT_ID overrides it
GITHUB_CLIENT_ID = os.environ.get("LLMBOX_GITHUB_CLIENT_ID", "")
DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"


def _cred_path() -> str:
    return os.path.join(HOME, "credentials.json")


def _creds() -> dict:
    try:
        return json.load(open(_cred_path()))
    except (OSError, ValueError):
        return {}


def _save(creds: dict) -> None:
    os.makedirs(HOME, exist_ok=True)
    fd = os.open(_cred_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(creds, f, indent=1)


def key_for(server: str) -> str | None:
    return (_creds().get(server.rstrip("/")) or {}).get("key")


def _post_form(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Accept": "application/json", "User-Agent": f"llmbox/{__version__}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def github_device_token(client_id: str, show=print, sleep=time.sleep) -> str:
    """GitHub's device flow: show the code, wait until it is approved, return the token."""
    d = _post_form(DEVICE_URL, {"client_id": client_id, "scope": ""})
    if "device_code" not in d:
        raise SystemExit(f"GitHub refused the sign-in: {d.get('error_description') or d.get('error') or d}")
    show(f"Open {d['verification_uri']} and enter the code {d['user_code']} (it is valid for {int(d.get('expires_in', 900)) // 60} minutes).")
    interval, deadline = int(d.get("interval", 5)), time.time() + int(d.get("expires_in", 900))
    while time.time() < deadline:
        sleep(interval)
        t = _post_form(TOKEN_URL, {"client_id": client_id, "device_code": d["device_code"],
                                   "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if t.get("access_token"):
            return t["access_token"]
        err = t.get("error")
        if err == "slow_down":
            interval += 5
        elif err != "authorization_pending":
            raise SystemExit({"expired_token": "the code expired: run llmbox login again",
                              "access_denied": "the sign-in was declined on GitHub"}.get(err, f"GitHub: {err}"))
    raise SystemExit("the code expired: run llmbox login again")


def _call(server: str, path: str, body: dict | None = None, key: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": f"llmbox/{__version__}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(server.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        raise SystemExit(f"{server}: {e.code} {msg or e.reason}")
    except urllib.error.URLError as e:
        raise SystemExit(f"{server}: not reachable ({e.reason})")


def login(server: str, client_id: str | None = None, show=print, token: str | None = None) -> dict:
    cid = client_id or GITHUB_CLIENT_ID
    if not token and not cid:
        raise SystemExit("this llmbox has no GitHub app to sign in with yet (set LLMBOX_GITHUB_CLIENT_ID)")
    tok = token or github_device_token(cid, show)
    acc = _call(server, "/api/v1/login/github", {"access_token": tok})   # the server keeps the GitHub id, not the token
    creds = _creds()
    creds[server.rstrip("/")] = {"key": acc["key"], "handle": acc["handle"], "login": acc.get("login"), "since": time.strftime("%Y-%m-%d")}
    _save(creds)
    return acc


def logout(server: str) -> bool:
    creds = _creds()
    gone = creds.pop(server.rstrip("/"), None) is not None
    _save(creds)
    return gone


def whoami(server: str) -> dict | None:
    k = key_for(server)
    return _call(server, "/api/v1/me", key=k) if k else None


def set_public(server: str, public: bool) -> dict:
    k = key_for(server)
    if not k:
        raise SystemExit("not signed in: llmbox login")
    return _call(server, "/api/v1/me", {"public": public}, key=k)


def forget(server: str) -> dict:
    k = key_for(server)
    if not k:
        raise SystemExit("not signed in: llmbox login")
    res = _call(server, "/api/v1/me/forget", {}, key=k)
    logout(server)
    return res
