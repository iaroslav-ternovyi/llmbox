"""Submitting speed measurements (llmbox/submit.py) to the intake (llmbox/server.py), end to end over HTTP in a
scratch home: nothing private leaves, the intake files accepted records as community results with their hardware
class, rejects what it cannot use, rate-limits, and marks an implausible figure as an outlier.
Run: python3 tests/test_submit.py"""
import copy
import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import results  # noqa: E402

# a real speed record of the box, read before the home moves
real = next((r for _p, r in results.files("box") if r.get("kind") == "speed" and (r.get("speed") or {}).get("decode_tps")), None)
assert real, "needs a saved speed record of the box"

home = tempfile.mkdtemp()
os.environ["LLMBOX_NO_DB"] = "1"   # the scratch home has no results database: read its files
from llmbox import hosts, server, submit  # noqa: E402
hosts.HOME = submit.HOME = server.HOME = results.HOME = home
submit.LEDGER = os.path.join(home, "submitted.json")
os.makedirs(os.path.join(home, "hosts"))
json.dump({"name": "box", "ssh": "someone@secretbox", "hw": {"hostname": "secretbox"}}, open(os.path.join(home, "hosts", "box.json"), "w"))

rec = copy.deepcopy(real)
rec["raw"]["cmd"] = ["/home/someone/llama.cpp/build/bin/llama-server", "-m", "/home/someone/models/x.gguf"]
rec["raw"]["log_tail"] = "main: loading on secretbox for someone"
rec["recipe"]["notes"] = {"lines": ["Alice's own remark"]}
b = submit.bundle([rec])
text = json.dumps(b)
for w in ("/home/someone", "secretbox", "someone", "Alice"):
    assert w not in text, f"{w!r} left the machine"
assert "~/models/x.gguf" in text and b["records"][0]["host"]["gpu"] == real["host"]["gpu"]
# a machine whose user is called llmbox (the server's): llmbox's own words and the answers stay as they are
r2 = submit.scrub({"schema": results.SCHEMA, "tool": {"name": "llmbox"}, "rows": [{"final": "ran on secretbox"}]}, ["llmbox", "secretbox"])
assert r2["schema"] == results.SCHEMA and r2["tool"]["name"] == "llmbox" and r2["rows"][0]["final"] == "ran on secretbox", r2

intake = server.Intake(os.path.join(home, "intake"))
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(intake))
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}"

res = submit.send(b, url)
assert res["status"] == "received" and res["id"], res
bad = copy.deepcopy(rec)
bad["id"], bad["speed"]["decode_tps"] = "bad-record", 50000.0
wild = copy.deepcopy(rec)
wild["id"] = "wild-record"
wild["speed"]["decode_tps"] = 3 * ((wild.get("prediction") or {}).get("decode_tps_no_spec") or 30)
res2 = submit.send(submit.bundle([bad, wild]), url)
assert intake.ingest() == [res["id"], res2["id"]]

st = json.loads(urllib.request.urlopen(f"{url}/api/v1/runs/{res['id']}").read())
assert st["status"] == "accepted", st
# what the site's queue page (r/<id> before it exists) reads: where a received run stands and when it should be live;
# an accepted one waits for the next publish, a failed publish says delayed and keeps it for the next try, a successful
# one marks it published and the waiting fields go
assert res["page"] == f"r/{res['id']}" and res["ahead"] == 0 and res["live_at"].endswith("Z") and res["site_within_min"] >= 1, res
assert st["page"] == f"r/{res['id']}" and st["published"] is None and st["live_at"].endswith("Z") and st["delayed"] is False, st
def boom(ids):
    raise OSError("upload failed")
left, _t = server.publish(intake, [[res["id"]]], boom)
assert left == [[res["id"]]] and intake.status(res["id"])["delayed"] is True and not intake.status(res["id"])["published"]
left, _t = server.publish(intake, left, lambda ids: False)   # the deploy command failed
assert left == [[res["id"]]] and intake.status(res["id"])["delayed"] is True
left, _t = server.publish(intake, left, lambda ids: True)
st = intake.status(res["id"])
assert left == [] and st["published"] and "live_at" not in st and "delayed" not in st, st
assert intake.status(res2["id"])["delayed"] is False   # the last publish worked
st2 = intake.status(res2["id"])
assert [d["status"] for d in st2["detail"]] == ["rejected", "accepted"] and st2["detail"][1]["flags"] == ["outlier"], st2
filed = [r for _p, r in results.files(server.COMMUNITY)]
assert len(filed) == 2, [r.get("id") for r in filed]
assert all(r["submission"]["client"] != b["client"] for r in filed)   # the install id is hashed
assert filed[0]["host"]["class"].startswith("rtx-5070-12g|"), filed[0]["host"]["class"]

import gzip  # noqa: E402
bomb = gzip.compress(b"[" + b"0," * (110 * 2**20) + b"0]")   # ~0.3 MB that unpacks to 220 MB
assert intake.receive(bomb, "9.9.9.9")[0] == 413, "a gzip bomb was unpacked"
# hostile or broken records: rejected one by one, none blocks the submissions behind it, none reaches a path or a page
evil = [1, dict(rec, id="abs-path", created="/private/tmp/x"), dict(rec, id="xss-ram", host=dict(rec["host"], ram_gib="<img src=x onerror=alert(1)>")),
        dict(rec, id="bad-speed", speed=dict(rec["speed"], decode_tps="fast")), dict(rec, id="bad-recipe", recipe=dict(rec["recipe"], id="../../etc"))]
r_bad = submit.send(submit.bundle([x for x in evil if x != 1]), url)
raw_bad = intake.receive(gzip.compress(json.dumps({"schema": "llmbox.submission/1", "client": "x" * 32, "records": [1]}).encode()), "8.8.8.8")
r_ok = submit.send(submit.bundle([dict(rec, id="after-the-bad-ones")]), url)
done = intake.ingest()
assert r_ok["id"] in done and raw_bad[1]["id"] in done and r_bad["id"] in done, (done, raw_bad)
assert intake.status(raw_bad[1]["id"])["status"] == "rejected" and intake.status(r_ok["id"])["status"] == "accepted"
assert "page" not in intake.status(raw_bad[1]["id"]) and "live_at" not in intake.status(raw_bad[1]["id"])   # no page to wait for
assert all(d["status"] == "rejected" for d in intake.status(r_bad["id"])["detail"]), intake.status(r_bad["id"])
assert not os.path.exists("/private/tmp/x-speed-" + str(rec["recipe"]["id"]) + ".json")
# a result card the site build gave up on: health is 503 for a day, so the uptime monitor alerts
import urllib.error  # noqa: E402
os.makedirs(os.path.join(home, "cards"))
open(os.path.join(home, "cards", "abcdef012345.failed"), "w").write("rsvg-convert: no IBM Plex Sans")
try:
    urllib.request.urlopen(f"{url}/api/v1/health")
    raise AssertionError("health was ok with a failed card")
except urllib.error.HTTPError as e:
    assert e.code == 503 and json.loads(e.read()) == {"ok": False, "cards_failed_24h": 1}
os.utime(os.path.join(home, "cards", "abcdef012345.failed"), (0, 0))   # more than a day ago: ok again
assert json.loads(urllib.request.urlopen(f"{url}/api/v1/health").read()) == {"ok": True}
server.PER_HOUR = 2
try:
    submit.send(b, url)
    raise AssertionError("third submission in an hour was taken")
except SystemExit as e:
    assert "429" in str(e), e
srv.shutdown()

# request addresses: a keyed hash (not the bare sha256 anyone can undo for IPv4), gone after two days
import hashlib  # noqa: E402
k = intake.addr_key("203.0.113.7")
assert k != hashlib.sha256(b"203.0.113.7").hexdigest()[:16] and k == intake.addr_key("203.0.113.7"), k
with intake._db() as c:
    c.execute("UPDATE submissions SET received = '2020-01-01T00:00:00'")
    c.execute("INSERT OR IGNORE INTO seeds VALUES (?,?,?,1)", (-1, "addr:" + k, "2020-01-01T00:00:00"))
intake.prune()
with intake._db() as c:
    assert c.execute("SELECT count(*) FROM submissions WHERE addr != ''").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM seeds WHERE client LIKE 'addr:%'").fetchone()[0] == 0
print("all passed")
