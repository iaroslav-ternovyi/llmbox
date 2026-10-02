"""Comments, shared settings and votes (llmbox/social.py, server.py, site/model.py) in a scratch home: only shareable
overrides make a variant and only from someone signed in; a comment is plain text, rate-limited, tagged with the machines
its author measured the model on, hidden by three reports, and gone with its author's account; votes go on llmbox's
settings and on variants, never on one's own; the model page shows the variants with their numbers and the shell the
comments load into.
Run: python3 tests/test_social.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
home = tempfile.mkdtemp()
os.environ["LLMBOX_NO_DB"] = "1"
from llmbox import hosts, results, server, social  # noqa: E402
hosts.HOME = results.HOME = server.HOME = home
os.makedirs(os.path.join(home, "recipes", "box"))
open(os.path.join(home, "recipes", "box", "qwen36-al.toml"), "w").write('description = "x"\n')

fails = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        fails.append(what)


# ---- which overrides can be shared
ok, bad = social.parse(["placement.ubatch=1024", "speculative.draft_max=3", "sampling.temp=0.7"])
check(ok == {"placement.ubatch": 1024, "speculative.draft_max": 3, "sampling.temp": 0.7} and not bad, "shareable overrides parse to typed values")
ok, bad = social.parse(["model.path=/etc/passwd", "extra.args=[\"--host\",\"0.0.0.0\"]", "placement.ubatch=-5", "placement.kv_type=q9"])
check(not ok and len(bad) == 4, "paths, free flags and out-of-range values are not shareable")
check(social.key_of("a", {"x": 1, "y": 2}) == social.key_of("a", {"y": 2, "x": 1}) != social.key_of("b", {"x": 1, "y": 2}),
      "a variant's id is the recipe and its settings, in any order")
check(social.changes_answers({"sampling.temp": 0.7}) and not social.changes_answers({"placement.ubatch": 512}), "sampling changes the answers, ubatch does not")
check(social.flags({"placement.fit": False, "speculative.type": "draft-mtp"}) == ["placement.fit=false", "speculative.type=draft-mtp"],
      "COPY gives --set arguments that read back the same")
check(social.parse(social.flags({"placement.fit": False, "speculative.type": "draft-mtp"}))[0] == {"placement.fit": False, "speculative.type": "draft-mtp"},
      "the copied arguments parse to the same settings")

# ---- comment text
check(social.clean("  hi\r\n\n\n\nthere‮ ") == "hi\n\nthere", "control and direction characters go, blank runs shrink")
check(social.clean("") is None and social.clean("x" * 2001) is None and social.clean("a\n" * 45) is None and social.clean(5) is None,
      "empty, too long, too many lines and non-text are refused")
check(social.clean("<script>alert(1)</script>") == "<script>alert(1)</script>", "markup is kept as text (the page sets it as text)")

# ---- runs people sent: one with shareable overrides (signed in), one anonymous, one with a path override
d = os.path.join(home, "results", "community")
os.makedirs(d)


def rec(n, user, overrides, kind="speed", gpu="NVIDIA GeForce RTX 3060", vram=12, t2=50.0, cap=None):
    r = {"schema": results.SCHEMA, "id": f"r{n}", "kind": kind, "created": f"2026-10-0{n}T10:00:00", "recipe": {"id": "qwen36-al"},
         "host": {"gpu": gpu, "vram_gib": vram, "ram_gib": 64, "ram_read_gbs": 60}, "overrides": overrides,
         "submission": {"id": f"s{n}", "user": user, "flags": []}}
    if kind == "speed":
        r["speed"] = {"decode_tps": t2, "prefill_tps": 2000, "depth": [{"depth": 32000, "decode_tps": t2 * 0.8}]}
    else:
        r["summary"] = {"capability": cap}
    json.dump(r, open(os.path.join(d, f"s{n}-00-{kind}.json"), "w"))


rec(1, "u-aaaa1111", ["placement.ubatch=1024"], t2=60)
rec(2, None, ["placement.ubatch=512"])
rec(3, "u-bbbb2222", ["model.path=/x"])
rec(4, "u-bbbb2222", [], t2=48)                        # a plain run: makes bob a tester of the model
rec(5, "u-aaaa1111", ["placement.ubatch=1024"], kind="suite", cap=70.0)
vs = social.variants()
check(list(vs) == ["qwen36-al"] and len(vs["qwen36-al"]) == 1, "only the signed-in run with shareable settings makes a variant")
v = vs["qwen36-al"][0]
check(v["settings"] == {"placement.ubatch": 1024} and v["by"] == "u-aaaa1111" and len(v["runs"]) == 2, "its speed and quality runs are one variant")
check(v["runs"][0]["machine"] == "RTX 3060 12 GB" and v["runs"][0]["t2"] == 60 and v["runs"][1]["score"] == 70.0, "with the machine and the numbers")
ts = social.testers()
check(ts.get(("u-bbbb2222", "qwen36-al")) == ["RTX 3060 12 GB"], "who measured the model on what")

# ---- the intake: accounts, comments, reports, votes, erasure
it = server.Intake(os.path.join(home, "intake"))
alice = it.user_of("Bearer " + it._issue({"id": 1, "login": "alice"})["key"])
bob = it.user_of("Bearer " + it._issue({"id": 2, "login": "bob"})["key"])
carol, dave, erin = (it.user_of("Bearer " + it._issue({"id": i, "login": n})["key"]) for i, n in ((3, "carol"), (4, "dave"), (5, "erin")))
# the handles are hashes; the test runs were filed under made-up ones: point them at bob and alice
import sqlite3  # noqa: E402
c = sqlite3.connect(os.path.join(home, "intake", "intake.db"))
c.execute("UPDATE users SET handle = 'u-aaaa1111' WHERE login = 'alice'")
c.execute("UPDATE users SET handle = 'u-bbbb2222' WHERE login = 'bob'")
c.commit()
alice, bob = (dict(u, handle=h) for u, h in ((alice, "u-aaaa1111"), (bob, "u-bbbb2222")))

code, r = it.comment(bob, json.dumps({"rid": "qwen36-al", "body": "Runs fine at 1024 ubatch.\n\nNo loops."}).encode())
check(code == 201, f"a signed-in comment is taken ({code} {r})")
cid = r["id"]
check(it.comment(bob, json.dumps({"rid": "qwen36-al", "body": "Runs fine at 1024 ubatch.\n\nNo loops."}).encode())[0] == 409, "the same text twice is refused")
check(it.comment(bob, json.dumps({"rid": "nope", "body": "x"}).encode())[0] == 404, "a comment needs a model that has a page")
check(it.comment(bob, json.dumps({"rid": "qwen36-al", "body": " "}).encode())[0] == 400, "an empty comment is refused")
_, s = it.social("qwen36-al", None)
cm = s["comments"][0]
check(cm["by"] == "u-bbbb2222" and cm["name"] is None and cm["tested"] == ["RTX 3060 12 GB"] and not cm["mine"],
      "a comment shows the handle (GitHub name only when public) and the machines its author measured the model on")
check(s["votes"] == {"rid:qwen36-al": {"up": 0, "down": 0}, v["key"]: {"up": 0, "down": 0}} and s["me"] is None,
      "votes are counted on llmbox's settings and on each variant")
check(it.social("qwen36-al", bob)[1]["comments"][0]["mine"], "the author sees it as theirs")

# rate limit
for i in range(server.Intake.COMMENTS_PER_HOUR):
    it.comment(erin, json.dumps({"rid": "qwen36-al", "body": f"note {i}"}).encode())
check(it.comment(erin, json.dumps({"rid": "qwen36-al", "body": "one more"}).encode())[0] == 429, "at most 10 comments an hour")

# reports: own refused, three readers hide it, the author still sees it with why
check(it.report(bob, cid, b'{"reason": "spam"}')[0] == 400, "one cannot report one's own comment")
check(it.report(alice, cid, b'{"reason": "rude"}')[0] == 400, "a report needs one of the reasons")
for u in (alice, carol):
    it.report(u, cid, b'{"reason": "spam", "detail": "ad"}')
it.report(alice, cid, b'{"reason": "spam"}')   # the same reader twice counts once
check(any(x["id"] == cid for x in it.social("qwen36-al", None)[1]["comments"]), "two readers' reports do not hide a comment")
code, r = it.report(dave, cid, b'{"reason": "abuse"}')
check(r["hidden"] and not any(x["id"] == cid for x in it.social("qwen36-al", None)[1]["comments"]), "the third reader's report hides it")
mine = next(x for x in it.social("qwen36-al", bob)[1]["comments"] if x["id"] == cid)
check(mine["held"] and "operator" in mine["held"], "its author still sees it, with why")
check(it.moderation()[0]["id"] == cid and it.moderation()[0]["n"] == 3, "the operator's list has it first, with its reports")
it.moderate(cid, "show")
check(any(x["id"] == cid for x in it.social("qwen36-al", None)[1]["comments"]) and not it.moderation(), "shown again, its reports cleared")
it.moderate(cid, "hide", "advertising")
held = next(x for x in it.social("qwen36-al", bob)[1]["comments"] if x["id"] == cid)["held"]
check(held == "removed by the operator: advertising", "hidden by the operator, the author is told why")

# votes
check(it.vote(alice, json.dumps({"target": v["key"], "value": 1}).encode())[0] == 400, "no vote on settings one measured oneself")
code, r = it.vote(bob, json.dumps({"target": v["key"], "value": 1}).encode())
check(code == 200 and r["up"] == 1, "a vote on someone's settings")
it.vote(bob, json.dumps({"target": v["key"], "value": -1}).encode())
code, r = it.vote(carol, json.dumps({"target": "rid:qwen36-al", "value": 1}).encode())
check(it.social("qwen36-al", bob)[1]["votes"][v["key"]] == {"up": 0, "down": 1} and r["up"] == 1, "a second vote replaces the first")
check(it.social("qwen36-al", bob)[1]["mine"] == {v["key"]: -1}, "the account sees its own votes")
check(it.vote(bob, json.dumps({"target": "vdeadbeef00", "value": 1}).encode())[0] == 404, "no vote on something that does not exist")
check(it.vote(bob, json.dumps({"target": "rid:qwen36-al", "value": 2}).encode())[0] == 400, "a vote is 1, -1 or 0")
check(it.vote(bob, json.dumps({"target": "rid:qwen36-al", "value": True}).encode())[0] == 400, "true is not a vote")

# a ban stops posting and voting
it.ban("dave")
dave = it.user_of("Bearer " + it._issue({"id": 4, "login": "dave"})["key"])
check(it.comment(dave, json.dumps({"rid": "qwen36-al", "body": "hi"}).encode())[0] == 403 and
      it.vote(dave, json.dumps({"target": "rid:qwen36-al", "value": 1}).encode())[0] == 403, "a banned account cannot post or vote")
check(it.social("qwen36-al", dave)[1]["me"]["can_post"] is False, "and the page knows not to offer the form")

# deleting one's own comment; erasure takes everything
code, r = it.comment(carol, json.dumps({"rid": "qwen36-al", "body": "mine"}).encode())
check(it.uncomment(alice, r["id"])[0] == 404 and it.uncomment(carol, r["id"])[0] == 200, "only the author deletes a comment")
it.forget(bob)
s = it.social("qwen36-al", None)[1]
with it._db() as cx:
    left = [cx.execute(f"SELECT count(*) FROM {t} WHERE user_id = ?", (bob["id"],)).fetchone()[0] for t in ("comments", "votes", "reports")]
check(left == [0, 0, 0] and not any(x["by"] == "u-bbbb2222" for x in s["comments"]), "deleting the account deletes its comments, votes and reports")

# ---- over HTTP: CORS, a read without a key, a post without one
import threading  # noqa: E402
import urllib.request  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(it))
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
with urllib.request.urlopen(f"{base}/api/v1/models/qwen36-al/social") as resp:
    check(resp.status == 200 and resp.headers["Access-Control-Allow-Origin"] == "*" and "comments" in json.loads(resp.read()), "anyone can read a model's comments")
try:
    urllib.request.urlopen(urllib.request.Request(f"{base}/api/v1/comments", data=b'{"rid":"qwen36-al","body":"x"}', method="POST"))
    check(False, "a comment without a key is refused")
except urllib.error.HTTPError as e:
    check(e.code == 401, "a comment without a key is refused")
try:
    urllib.request.urlopen(f"{base}/api/v1/models/..%2Fetc/social")
    check(False, "a model id outside the pattern is not found")
except urllib.error.HTTPError as e:
    check(e.code == 404, "a model id outside the pattern is not found")
code, r = it.comment(alice, json.dumps({"rid": "qwen36-al", "body": "🙂" * 2000}, ensure_ascii=False).encode())
check(code == 201, "2000 emoji are a comment")
req = urllib.request.Request(f"{base}/api/v1/comments", data=json.dumps({"rid": "qwen36-al", "body": "🙂" * 1999 + "!"}, ensure_ascii=False).encode(),
                             method="POST", headers={"Authorization": "Bearer " + it._issue({"id": 1, "login": "alice"})["key"], "Content-Type": "application/json"})
with urllib.request.urlopen(req) as resp:
    check(resp.status == 201, "and fit in a request")
srv.shutdown()

# ---- check() refuses malformed overrides
base_rec = {"schema": results.SCHEMA, "id": "11111111-2222-3333-4444-555555555555", "kind": "speed", "created": "2026-10-02T10:00:00+0200",
            "host": {"cpu": "x", "ram_gib": 64}, "model": {"file": "m.gguf"}, "recipe": {"id": "qwen36-al"}, "speed": {"decode_tps": 50}}
check(server.check(dict(base_rec, overrides=["placement.ubatch=512"])) is None, "a record with overrides is taken")
check(server.check(dict(base_rec, overrides="placement.ubatch=512")) and server.check(dict(base_rec, overrides=[5])), "malformed overrides are refused")

# ---- the model page's panels
from llmbox.site import model as M  # noqa: E402
html = M._shared_panel("qwen36-al", vs["qwen36-al"], [{"class": v["runs"][0]["cls"], "t2": 50.0, "machines": 3}], {"u-aaaa1111": {"login": "alice", "public": True}},
                       {"t2": 52.0, "t32": 40.0, "score": 80.0, "machines": 3}, 1.02)
check("llmbox test qwen36-al --set placement.ubatch=1024" in html and "+20%" in html and ">alice<" in html and "71%" in html,
      "a variant shows its COPY command, its speed against llmbox's on the same card, who measured it and its score")
check(f"data-target='{v['key']}'" in html and "data-target='rid:qwen36-al'" in html, "vote cells for llmbox's settings and the variant")
empty = M._shared_panel("qwen36-al", [], None, {}, {"t2": 52.0}, None)
check("Nobody has shared other settings" in empty and "llmbox test qwen36-al --set" in empty, "with none yet it says how to share")
talk = M._talk_panel("qwen36-al")
check('id="comments" data-rid="qwen36-al"' in talk and "terms.html#comments" in talk, "the comments panel is a shell social.js fills")

print(f"\n{'all ok' if not fails else f'{len(fails)} failed'}")
sys.exit(1 if fails else 0)
