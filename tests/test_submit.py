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
st2 = intake.status(res2["id"])
assert [d["status"] for d in st2["detail"]] == ["rejected", "accepted"] and st2["detail"][1]["flags"] == ["outlier"], st2
filed = [r for _p, r in results.files(server.COMMUNITY)]
assert len(filed) == 2, [r.get("id") for r in filed]
assert all(r["submission"]["client"] != b["client"] for r in filed)   # the install id is hashed
assert filed[0]["host"]["class"].startswith("rtx-5070-12g|"), filed[0]["host"]["class"]

server.PER_HOUR = 2
try:
    submit.send(b, url)
    raise AssertionError("third submission in an hour was taken")
except SystemExit as e:
    assert "429" in str(e), e
srv.shutdown()
print("all passed")
