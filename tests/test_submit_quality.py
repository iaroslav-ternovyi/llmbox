"""A quality run sent by `llmbox test` (llmbox/server.py, end to end over HTTP in a scratch home): the server gives the
seed, grades every answer again and keeps its own score, leaves explanations for the reader, flags a run on a seed of
its own, rejects a run whose answers grade differently, and the pool adds the accepted answers to the reference recipe
the run measured the same way.
Run: python3 tests/test_submit_quality.py   (re-grades one real run of the box: ~1 min)"""
import copy
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import results  # noqa: E402

src = next(p for p, r in results.files("box") if os.path.basename(p) == "2026-09-30T134040-suite-k2-medium.json")
real = json.load(open(src))
real_home = results.HOME

home = tempfile.mkdtemp()
os.environ["LLMBOX_NO_DB"] = "1"
from llmbox import hosts, irt, recipe as rc, server, submit  # noqa: E402
hosts.HOME = submit.HOME = server.HOME = results.HOME = rc.HOME = irt.HOME = home
submit.LEDGER = os.path.join(home, "submitted.json")
os.makedirs(os.path.join(home, "recipes", "box"))
for rid in ("k2-horizon", "k2-medium"):   # the reference recipes a person's run can join
    shutil.copy(os.path.join(real_home, "recipes", "box", f"{rid}.toml"), os.path.join(home, "recipes", "box"))
os.makedirs(os.path.join(home, "irt"))
for f in os.listdir(os.path.join(real_home, "irt")):   # banks and family fingerprints of the released suites
    if f.startswith(("bank-", "fp-", "snapshots")):
        shutil.copy(os.path.join(real_home, "irt", f), os.path.join(home, "irt"))

intake = server.Intake(os.path.join(home, "intake"))
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(intake))
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}"

seed = submit.fresh_seed(url)
assert isinstance(seed, int) and seed >= 10**6, seed
graded = [i for i, x in enumerate(real["rows"]) if x["block"] != "explain" and x.get("final") is not None and not x.get("error")]
assert len(graded) >= 5, graded

good = copy.deepcopy(real)
good["id"], good["suite"]["seed0"] = "good-run", 7000 + 1000 * seed
k = graded[0]
told = good["rows"][k]["score"]
good["rows"][k]["score"] = 0.0 if told > 0.5 else 1.0     # one answer claims another grade: the server's counts
own = copy.deepcopy(real)
own["id"], own["suite"]["seed0"] = "own-seed", 7000 + 1000 * 12345
fake = copy.deepcopy(real)
fake["id"] = "fake-run"
for i in graded:   # every graded answer claims the opposite: not what the suite gave
    fake["rows"][i]["score"] = 0.0 if fake["rows"][i]["score"] > 0.5 else 1.0
res = submit.send(submit.bundle([good, own, fake]), url)
assert intake.ingest() == [res["id"]]
st = intake.status(res["id"])
assert [d["status"] for d in st["detail"]] == ["accepted", "accepted", "rejected"], st["detail"]
assert st["detail"][1]["flags"] == ["self-seeded"] and "grade differently" in st["detail"][2]["reason"], st["detail"]

filed = {r["id"]: r for _p, r in results.files(server.COMMUNITY)}
g = filed["good-run"]
assert g["rows"][k]["score"] == told and g["rows"][k]["verified"], (told, g["rows"][k])
assert all(x.get("pending") for x in g["rows"] if x["block"] == "explain"), "explanations wait for the reader"
assert g["verified"]["graded"] >= 5 and g["verified"]["differed"] == 1, g["verified"]

p = irt.pool(("community",))
assert ("k2-medium", "box") in p, list(p)                   # joined the reference recipe
runs = {x["_run"] for x in p[("k2-medium", "box")]}
assert runs == {g["created"]}, runs                          # the self-seeded run is filed, not pooled
srv.shutdown()
print(f"server re-graded {g['verified']['graded']} answers, kept its own score, pooled 1 of 3 runs")
print("all passed")
