"""The site's board (llmbox/site/board.py), on made-up records and two real model shapes: which picker entry a run
belongs to, machines like it (a flagged outlier left out, the reference box in), where a run stands, who is first on
an entry (signed in, by when it was received, never the box, never an outlier; the badge says FIRST USER where the box
measured first; erasing the holder passes it on), each cell's model and number (measured from the comparison class
with the most machines, else predicted), the machines outside the picker, and the feed events (each written once; a
new best only when the best changes).
Run: python3 tests/test_board.py"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from llmbox import estimate as E, fit as F, hwclass  # noqa: E402
from llmbox.site import board  # noqa: E402

fx = json.load(open(os.path.join(HERE, "fixtures", "parity.json")))["models"]


def model(rid, score, lo, hi):
    m = fx[rid]
    r = {"placement": {"kv_type": m["kv_type"], "ctx": m["ctx"]}, "runtime": {"threads": 8, "cpu_affinity": ""}}
    return ({"id": rid, "name": rid.upper(), "score": score, "range": [lo, hi]}, r, E.ModelShape(**m["shape"]))


MODELS = [model("qwen36-al", 82, 79, 85), model("qwen35-9b", 67, 63, 71)]
K3060 = hwclass.key("NVIDIA GeForce RTX 3060", 12288, 60)          # rtx-3060-12g|ram-45-65|cuda
K3060_DDR4 = hwclass.key("NVIDIA GeForce RTX 3060", 12288, 40)     # another comparison class of the same entry
K5070 = hwclass.key("NVIDIA GeForce RTX 5070", 12227, 59)


def run(cls, t2, rid="qwen36-al", machine="m1", sid=None, user=None, at="2026-10-10T10:00:00", flags=(), ref=False):
    rec = {"kind": "speed", "recipe": {"id": rid}, "speed": {"decode_tps": t2, "depth": [{"depth": 32000, "decode_tps": t2 * 0.8}]},
           "host": {"class": cls, "id": machine}}
    if ref:
        rec["created"] = "2026-09-20T10:00:00+0200"
    else:
        rec["submission"] = {"id": sid, "user": user, "received": at, "flags": list(flags)}
    return rec


box = [run(K5070, 55, machine="box", ref=True, rid="qwen36-al"), run(K5070, 40, machine="box", ref=True, rid="qwen35-9b")]
people = [run(K3060, 40, machine=f"m{i}", sid=f"s{i:011d}", user="u-0000000a" if i == 2 else None, at=f"2026-10-1{i}T10:00:00")
          for i in range(1, 7)]
people += [run(K3060, 200, machine="cheat", sid="s0000000fake", user="u-0000000b", at="2026-10-09T09:00:00", flags=["outlier"]),
           run(K3060_DDR4, 30, machine="ddr4", sid="s000000ddr40", at="2026-10-11T11:00:00"),
           run(K5070, 58, machine="u5070", sid="s000005070aa", user="u-0000000c", at="2026-10-12T12:00:00"),
           run(hwclass.key("NVIDIA GeForce RTX 3090", 24576, 60, count=2), 70, machine="rig", sid="s00000000rig", at="2026-10-13T00:00:00"),
           run(hwclass.key("", 0, 80, vendor=""), 9, machine="cpu", sid="s00000000cpu", at="2026-10-14T00:00:00")]
b = board.build(box, people, MODELS, 60.0)

assert len(b["entries"]) == 65 and set(b["entries"]) == set(hwclass.SLUGS)
# machines like it: per comparison class, each machine once; the outlier out; the box in
s = b["sets"][("qwen36-al", K3060)]
assert s["machines"] == 6 and s["values"] == [40.0] * 6 and s["median"] == 40.0, s
assert b["sets"][("qwen36-al", K5070)]["machines"] == 2   # the reference box and a person
# where a run stands among the others (itself left out); a percentile only from five others on
p = board.place(b, "qwen36-al", K3060, 45.0, machine="new")
assert p["m"] == 6 and p["faster_than"] == 100, p
p = board.place(b, "qwen36-al", K5070, 58.0, machine="u5070")
assert p["m"] == 1 and p["faster_than"] is None and p["others"] == [55.0], p
# one machine with two runs and nobody else: no "other" machine like it (its own median is not a neighbour)
b1 = board.build([], [run(K5070, 50, machine="solo", sid="s00000000s01", rid="qwen35-9b"), run(K5070, 52, machine="solo", sid="s00000000s02", rid="qwen35-9b")], MODELS, 60.0)
assert board.place(b1, "qwen35-9b", K5070, 50.0, machine="solo") == {"others": [], "m": 0, "faster_than": None}
# the first on an entry: signed in, by when it was received; the outlier (earlier, signed in) never; the box never
e3060 = b["entries"]["RTX 3060 12 GB"]
assert e3060["credit"]["sid"] == "s00000000002" and e3060["credit"]["badge"] == "FIRST ON THIS CARD", e3060["credit"]
assert e3060["machines"] == 7 and e3060["first_at"] == "2026-10-11T10:00:00", e3060   # m1..m6 and the DDR4 machine
e5070 = b["entries"]["RTX 5070 12 GB"]
assert e5070["reference"] and e5070["credit"]["sid"] == "s000005070aa" and e5070["credit"]["badge"] == "FIRST USER ON THIS CARD", e5070
# erasing the holder passes the credit on (the records are gone, the board is rebuilt): no other signed-in run on the 3060
b2 = board.build(box, [x for x in people if (x["submission"]["user"] != "u-0000000a")], MODELS, 60.0)
assert b2["entries"]["RTX 3060 12 GB"]["credit"] is None and b2["entries"]["RTX 3060 12 GB"]["machines"] == 6
# a cell: the pick's best model at the start values; measured from the class with the most machines, else predicted
c = e3060["best"]
assert c["rid"] == "qwen36-al" and c["measured"] == 40.0 and c["group"] == K3060 and c["machines"] == 6, c
c = b["entries"]["RTX 4060 8 GB"]["best"]
assert c and c["measured"] is None and c["predicted"] > 0, c
assert not b["entries"]["RX 7900 XTX 24 GB"]["testable"] and b["entries"]["Mac M2 Max"]["testable"]
# machines outside the picker: listed with machines and their newest run, not counted on any entry
assert [(o["label"], o["machines"], o["newest"]) for o in b["other"]] == [("2 × RTX 3090 24 GB", 1, "s00000000rig"), ("no graphics card", 1, "s00000000cpu")], b["other"]
assert all(r["entry"] is None for sid, r in b["runs"].items() if sid in ("s00000000rig", "s00000000cpu"))
assert b["runs"]["s00000000002"]["entry"] == "RTX 3060 12 GB" and "box" not in {r["machine"] for r in b["runs"].values()}

# feed events: the first build writes each feed's start and each model measured on an entry; a second build adds
# nothing; a changed best adds one "new best"
log = os.path.join(tempfile.mkdtemp(), "events.jsonl")
allv, new = board.events(b, "2026-10-15T00:00:00Z", path=log)
kinds = sorted({e["kind"] for e in new})
assert kinds == ["measured", "start"] and sum(e["kind"] == "start" for e in new) == 65, kinds
assert {e["id"] for e in new if e["kind"] == "measured"} >= {"measured/rtx-3060-12gb/qwen36-al", "measured/rtx-5070-12gb/qwen35-9b"}
assert all(e["seed"] for e in new if e["kind"] == "measured")   # measured before the feeds started: logged, not shown
start = next(e for e in new if e["id"] == "start/rtx-3060-12gb")
assert start["measured"] and start["rid"] == "qwen36-al" and start["tps"] == 40.0 and not start["predicted"], start
assert next(e for e in new if e["id"] == "start/rx-7900-xtx-24gb")["testable"] is False
board.append(new, log)
assert board.events(b, "2026-10-16T00:00:00Z", path=log)[1] == []
better = [model("qwen36-al", 70, 66, 74), model("qwen35-9b", 90, 87, 93)]   # new scores: the other model is best now
b3 = board.build(box, people, better, 60.0)
_all, new3 = board.events(b3, "2026-10-17T00:00:00Z", path=log)
best_events = [e for e in new3 if e["kind"] == "best"]
assert best_events and all(e["rid"] == "qwen35-9b" for e in best_events) and len(best_events) == len({e["entry"] for e in best_events}), best_events
board.append(new3, log)
b4 = board.build(box, people + [run(K3060, 33, rid="qwen35-9b", machine="m1", sid="s0000000new1", at="2026-10-18T10:00:00")], better, 60.0)
new4 = board.events(b4, "2026-10-18T00:00:00Z", path=log)[1]
assert [(e["id"], e["seed"]) for e in new4 if e["kind"] == "measured"] == [("measured/rtx-3060-12gb/qwen35-9b", False)], new4   # after: shown
assert [e for e in board.events(b3, "2026-10-18T00:00:00Z", path=log)[1] if e["kind"] == "best"] == []
print("all passed")
