"""Accounts (llmbox/account.py + llmbox/server.py), end to end over HTTP in a scratch home with a stand-in GitHub:
the device flow waits for the approval, the server keeps the GitHub id and issues its own key, a signed-in quality run
counts toward the score and an anonymous one is filed only, the profile page shows the GitHub name only when made
public, and forget deletes the account and every result it sent.
Run: python3 tests/test_accounts.py"""
import copy
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import results  # noqa: E402

src = next(p for p, r in results.files("box") if os.path.basename(p) == "2026-09-30T134040-suite-k2-medium.json")
real = json.load(open(src))
speed = copy.deepcopy(next(r for _p, r in results.files("box") if r.get("kind") == "speed" and (r.get("speed") or {}).get("decode_tps")))
real_home = results.HOME

home = tempfile.mkdtemp()
os.environ["LLMBOX_NO_DB"] = "1"
from llmbox import account, hosts, irt, recipe as rc, server, submit  # noqa: E402
from llmbox.site import people  # noqa: E402
hosts.HOME = submit.HOME = server.HOME = results.HOME = rc.HOME = irt.HOME = account.HOME = home
submit.LEDGER = os.path.join(home, "submitted.json")
os.makedirs(os.path.join(home, "recipes", "box"))
for rid in ("k2-horizon", "k2-medium"):
    shutil.copy(os.path.join(real_home, "recipes", "box", f"{rid}.toml"), os.path.join(home, "recipes", "box"))
os.makedirs(os.path.join(home, "irt"))
for f in os.listdir(os.path.join(real_home, "irt")):
    if f.startswith(("bank-", "fp-", "snapshots")):
        shutil.copy(os.path.join(real_home, "irt", f), os.path.join(home, "irt"))


class GitHub(BaseHTTPRequestHandler):   # the device flow: a code, one "pending", then the token
    polls = 0

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.endswith("/device/code"):
            body = {"device_code": "dc", "user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device", "interval": 1, "expires_in": 900}
        else:
            GitHub.polls += 1
            body = {"error": "authorization_pending"} if GitHub.polls == 1 else {"access_token": "tok-alice"}
        out = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


gh = ThreadingHTTPServer(("127.0.0.1", 0), GitHub)
threading.Thread(target=gh.serve_forever, daemon=True).start()
account.DEVICE_URL = f"http://127.0.0.1:{gh.server_address[1]}/login/device/code"
account.TOKEN_URL = f"http://127.0.0.1:{gh.server_address[1]}/login/oauth/access_token"
shown = []
assert account.github_device_token("cid", show=shown.append, sleep=lambda s: None) == "tok-alice" and "ABCD-1234" in shown[0]

intake = server.Intake(os.path.join(home, "intake"))
intake.github_user = lambda t: {"tok-alice": {"id": 101, "login": "alice"}, "tok-bob": {"id": 202, "login": "bob"}}.get(t)
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(intake))
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}"

acc = account.login(url, token="tok-alice")
assert acc["handle"].startswith("u-") and "alice" not in acc["handle"] and acc["public"] is False
assert oct(os.stat(os.path.join(home, "credentials.json")).st_mode & 0o777) == "0o600", "the key must be readable by its owner only"
me = account.whoami(url)
assert me["login"] == "alice" and me["submissions"] == 0
try:
    account.login(url, token="not-a-token")
    raise AssertionError("a token GitHub does not know signed in")
except SystemExit as e:
    assert "401" in str(e)

good = copy.deepcopy(real)
good["id"], good["suite"]["seed0"] = "alice-run", 7000 + 1000 * submit.fresh_seed(url)
speed["id"] = "alice-speed"
submit.send(submit.bundle([good, speed]), url)                      # signed in
key = account.key_for(url)
account.logout(url)
anon = copy.deepcopy(real)
anon["id"], anon["suite"]["seed0"] = "anon-run", 7000 + 1000 * submit.fresh_seed(url)
submit.send(submit.bundle([anon]), url)                              # anonymous
account._save({url: {"key": key, "handle": acc["handle"]}})
intake.ingest()
filed = {r["id"]: r for _p, r in results.files(server.COMMUNITY)}
assert filed["alice-run"]["submission"]["user"] == acc["handle"] and filed["alice-run"]["submission"]["flags"] == []
assert filed["anon-run"]["submission"]["user"] is None and filed["anon-run"]["submission"]["flags"] == ["anonymous"]
assert ("k2-medium", "box") in irt.pool(("community",)), "the signed-in run joins the model's score"

us = people.users(os.path.join(home, "intake", "users.json"))
pg = people.pages({"k2-medium": "K2-Horizon"}, {}, us)
page = pg[f"{acc['handle']}.html"]
assert "alice" not in page and acc["handle"] in page and "counts</b> toward the score" in page, "a private profile shows the handle only"
account.set_public(url, True)
page = people.pages({"k2-medium": "K2-Horizon"}, {}, people.users(os.path.join(home, "intake", "users.json")))[f"{acc['handle']}.html"]
assert "<h1>alice</h1>" in page
assert "people.html" in pg and acc["handle"] in pg["people.html"]

# signing in on the site: GitHub's web flow, back to the site page with a one-time ticket for the key
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402


class GitHubWeb(BaseHTTPRequestHandler):
    def do_POST(self):   # the code for a token, only with the app's secret
        body = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
        out = json.dumps({"access_token": "tok-bob"} if body.get("code") == "c0de" and body.get("client_secret") == "s3cret" else {"error": "bad"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


import urllib.parse  # noqa: E402
ghw = ThreadingHTTPServer(("127.0.0.1", 0), GitHubWeb)
threading.Thread(target=ghw.serve_forever, daemon=True).start()
server.GH_TOKEN = f"http://127.0.0.1:{ghw.server_address[1]}/token"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(NoRedirect)
def location(path):
    try:
        opener.open(url + path)
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")
    raise AssertionError("no redirect")


site = "http://127.0.0.1:8766/account.html"
code, _ = location("/api/v1/login/web/start?return=" + urllib.parse.quote(site))
assert code == 503, "without the app's secret the site's sign-in says it is not set up"
os.environ["LLMBOX_GITHUB_CLIENT_ID"], os.environ["LLMBOX_GITHUB_SECRET"] = "cid", "s3cret"
assert location("/api/v1/login/web/start?return=" + urllib.parse.quote("https://evil.example/x"))[0] == 400, "only the site's pages"
code, to = location("/api/v1/login/web/start?return=" + urllib.parse.quote(site))
q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(to).query))
assert code == 302 and to.startswith(server.GH_AUTHORIZE) and q["client_id"] == "cid" and q["redirect_uri"].endswith("/api/v1/login/web/callback")
code, back = location(f"/api/v1/login/web/callback?code=c0de&state={q['state']}")
assert code == 302 and back.startswith(site + "#ticket="), back
assert location(f"/api/v1/login/web/callback?code=c0de&state={q['state']}")[0] == 400, "a state is used once"
ticket = back.split("#ticket=")[1]
bob = account._call(url, "/api/v1/login/web/redeem", {"ticket": ticket})
assert bob["login"] == "bob" and bob["key"].startswith("lbx_")
try:
    account._call(url, "/api/v1/login/web/redeem", {"ticket": ticket})
    raise AssertionError("a ticket redeemed twice")
except SystemExit as e:
    assert "400" in str(e)
assert account._call(url, "/api/v1/me", key=bob["key"])["login"] == "bob"
account._call(url, "/api/v1/me/logout", {}, key=bob["key"])
try:
    account._call(url, "/api/v1/me", key=bob["key"])
    raise AssertionError("a signed-out key still works")
except SystemExit as e:
    assert "401" in str(e)
ghw.shutdown()

gone = account.forget(url)
assert gone["deleted_results"] == 2, gone
left = {r["id"] for _p, r in results.files(server.COMMUNITY)}
assert left == {"anon-run"}, left
assert ("k2-medium", "box") not in irt.pool(("community",)), "an anonymous run alone does not count"
assert account.key_for(url) is None
try:
    account._call(url, "/api/v1/me", key=key)
    raise AssertionError("a deleted account's key still works")
except SystemExit as e:
    assert "401" in str(e)
assert acc["handle"] not in json.load(open(os.path.join(home, "intake", "users.json")))
srv.shutdown()
gh.shutdown()
print("device flow, login, a counted and an anonymous run, private and public profile, forget: all passed")
print("all passed")
