"""The result card (llmbox/site/card.py) on made-up runs: the text of each state - five or more machines like it (a
strip and a share), one to four (their speeds), the first with a badge (holding the entry's credit; FIRST USER where
the reference box measured first), the first without one (the RAM named), hardware outside the picker (never a
badge), a speed over twice the prediction (NOT CONFIRMED, no place); the quality line only for a signed-in run that
matches the model's published score; the pick or the best model there. Hostile text stays text (well-formed SVG,
no image, no control characters), long names are cut, only a well-formed llama.cpp build tag is printed. A card is
frozen at its first draw and never redrawn; a failure is retried and given up after three builds; a removed run's
card is deleted. With rsvg-convert and IBM Plex here it renders a real 1200x630 PNG; without Plex it refuses to draw.
Run: python3 tests/test_card.py"""
import json
import os
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
home = tempfile.mkdtemp()
os.environ["LLMBOX_HOME"] = home
from llmbox import estimate as E, hwclass  # noqa: E402
from llmbox.site import board as B, card  # noqa: E402

card.CARDS = os.path.join(home, "cards")
fx = json.load(open(os.path.join(HERE, "fixtures", "parity.json")))["models"]


def model(rid, score, lo, hi):
    m = fx[rid]
    return ({"id": rid, "name": rid, "score": score, "range": [lo, hi]}, {"placement": {"kv_type": m["kv_type"], "ctx": m["ctx"]},
            "runtime": {"threads": 8, "cpu_affinity": ""}}, E.ModelShape(**m["shape"]))


MODELS = [model("qwen36-al", 82, 79, 85), model("qwen35-9b", 67, 63, 71)]
META = {"qwen36-al": {"name": "Qwen3.6-35B-A3B UD-Q4_K_XL", "score": 82.0, "cap": 0.8, "range": [79, 85]},
        "qwen35-9b": {"name": "Qwen3.5-9B UD-Q4_K_XL", "score": 67.0, "cap": 0.7, "range": [63, 71]}}
K3060, K5070, K4060 = hwclass.key("NVIDIA GeForce RTX 3060", 12288, 60), hwclass.key("NVIDIA GeForce RTX 5070", 12227, 59), hwclass.key("NVIDIA GeForce RTX 4060", 8188, 60)
KMAC = hwclass.key("Apple M2 Max", 32 * 1024, vendor="apple", apple_gpu_cores=38)


def rec(cls, t2, sid, machine, user=None, at="2026-10-10T10:00:00", flags=(), rid="qwen36-al", ref=False, ram=64, build="b11312", pred=None):
    r = {"kind": "speed", "recipe": {"id": rid}, "speed": {"decode_tps": t2, "depth": [{"depth": 32000, "decode_tps": round(t2 * 0.75)}]},
         "host": {"class": cls, "id": machine, "ram_gib": ram}, "runtime": {"llama_cpp_build": build}, "prediction": {"decode_tps_no_spec": pred or t2}}
    if ref:
        r["created"] = "2026-09-20T10:00:00+0200"
    else:
        r["submission"] = {"id": sid, "user": user, "received": at, "flags": list(flags)}
    return r


def suite(sid, lo, hi, user="u-0000000a", pending=False, flags=()):
    return {"kind": "suite", "summary": {"capability": (lo + hi) / 2, "capability_ci95": [lo, hi]},
            "rows": [{"id": "t1", "pending": pending}], "submission": {"id": sid, "user": user, "flags": list(flags)}}


box = [rec(K5070, 55, None, "box", ref=True)]
people = [rec(K3060, 40 + i, f"{i:012x}", f"m{i}", user="u-000000a1" if i == 1 else None, at=f"2026-10-1{i}T10:00:00")
          for i in range(1, 6)]   # five 3060s; the first of them signed in holds the credit there
people += [rec(K3060, 46, "aaaaaaaaaaaa", "me1", user="u-0000000a", at="2026-10-19T10:00:00"),      # the sixth: a strip
           rec(K4060, 30, "bbbbbbbbbbbb", "me2", user="u-0000000b", at="2026-10-11T10:00:00", ram=32),  # the first 4060: a badge
           rec(K4060, 31, "cccccccccccc", "me3", at="2026-10-12T10:00:00"),                          # one other there
           rec(K5070, 58, "dddddddddddd", "me4", user="u-0000000c", at="2026-10-12T12:00:00"),        # first user on the box's card
           rec(KMAC, 33, "eeeeeeeeeeee", "me5", at="2026-10-13T10:00:00", rid="qwen35-9b"),           # anonymous, first with it there
           rec(hwclass.key("NVIDIA GeForce RTX 3090", 24576, 60, count=2), 80, "ffffffffffff", "rig", user="u-0000000d"),
           rec(K3060, 400, "999999999999", "cheat", user="u-0000000e", flags=["outlier"], pred=28, build="b8000; rm -rf /")]
b = B.build(box, people, MODELS, 60.0)
recs = {r["submission"]["id"]: [r] for r in people}
recs["aaaaaaaaaaaa"].append(suite("aaaaaaaaaaaa", 0.78, 0.84))         # 0.81 x 82/0.8 = 83%: inside 79-85, agrees
recs["dddddddddddd"].append(suite("dddddddddddd", 0.50, 0.60))         # 51-62%: outside, no quality line
SITE = "https://llmbox.pages.dev"


def spec(sid, **k):
    return card.spec(b, sid, recs[sid], META, SITE, now="2026-10-20T10:00:00Z", **k)


s = spec("aaaaaaaaaaaa")
assert s["place"]["kind"] == "strip" and s["place"]["text"] == "faster than 100% of 5 machines like it" and len(s["place"]["others"]) == 5, s
assert s["hardware"] == "RTX 3060 12 GB · 64 GB RAM" and s["model"] == "Qwen3.6-35B-A3B" and s["quant"] == "UD-Q4_K_XL" and s["t2"] == 46 and s["deep"] == 34
assert s["quality"] == "quality: 82% of Claude Opus 5.5 · this run agrees ✓" and s["badge"] is None and s["last"] == "llmbox's pick for this machine", s
assert s["path"] == "/r/aaaaaaaaaaaa" and s["site"] == "llmbox.pages.dev" and s["build"] == "llama.cpp b11312 · CUDA · as of 2026-10-19", s
s = spec("bbbbbbbbbbbb")
assert s["badge"] == "FIRST ON THIS CARD" and s["place"] == {"kind": "others", "text": "1 other like it: 31 tok/s"} and s["hardware"].endswith("32 GB RAM"), s
s = spec("cccccccccccc")
assert s["badge"] is None and s["place"]["text"] == "1 other like it: 30 tok/s", s   # not signed in: no credit
s = spec("dddddddddddd")
assert s["badge"] == "FIRST USER ON THIS CARD" and s["quality"] is None and s["place"]["text"] == "1 other like it: 55 tok/s", s
s = spec("eeeeeeeeeeee")
assert s["badge"] is None and s["place"] == {"kind": "first", "text": f"the first with this model on {hwclass.label(KMAC)}"} and s["hardware"] == "Mac M2 Max · 64 GB", s
assert s["last"].startswith("best here: ") and s["build"].endswith("· METAL · as of 2026-10-13"), s
s = spec("ffffffffffff")
assert s["badge"] is None and s["hardware"].startswith("2 × RTX 3090 24 GB") and s["last"] is None, s
s = spec("999999999999")
assert s["badge"] == "NOT CONFIRMED" and s["outlier"] and s["place"]["kind"] == "outlier" and "(~28 tok/s)" in s["place"]["text"], s
assert s["build"].startswith("llama.cpp · ") and "rm -rf" not in json.dumps(s), s   # a malformed build tag is left out
# the quality line only for a quality run that counts: its own record's flags (the speed record's say nothing about it)
for flag, state in (("anonymous", "anonymous"), ("self-seeded", "held"), ("outlier", "held")):
    recs["aaaaaaaaaaaa"][1]["submission"]["flags"] = [flag]
    s = spec("aaaaaaaaaaaa")
    assert s["quality"] is None and s["q_state"] == state, (flag, s["quality"], s["q_state"])
recs["aaaaaaaaaaaa"][1]["submission"]["flags"] = []
assert spec("aaaaaaaaaaaa")["q_state"] == "agrees" and spec("dddddddddddd")["q_state"] == "differs" and spec("bbbbbbbbbbbb")["q_state"] is None
recs["aaaaaaaaaaaa"][1]["summary"] = [1, 2, 3]   # malformed (refused at the intake now; older files may have it): no crash
assert spec("aaaaaaaaaaaa")["q_state"] == "grading"
recs["aaaaaaaaaaaa"][1]["summary"] = {"capability": 0.81, "capability_ci95": [0.78, 0.84]}
# a quality run waits for its explanations (up to a day after it was received)
recs["aaaaaaaaaaaa"][1]["rows"][0]["pending"] = True
assert spec("aaaaaaaaaaaa") is None
recs["aaaaaaaaaaaa"][1]["rows"][0]["pending"] = False

# hostile text stays text
evil = dict(spec("aaaaaaaaaaaa"), hardware="RTX <img src=x onerror=alert(1)> & \"x\" \x00\x07\x1b[31m", model="M" * 300, quant="<b>")
t = card.svg(evil)
root = ET.fromstring(t)
assert "<image" not in t and "<img" not in t and "\x00" not in t and "\x1b" not in t and "&lt;img" in t, t[:500]
texts = "".join(root.itertext())
assert "<img src=x onerror=alert(1)>" in texts and "…" in texts, texts[:300]
assert card.build_tag("b8000") == "b8000" and card.build_tag("b8000; x") is None and card.build_tag(None) is None and card.build_tag("<b>") is None
assert card.norm("a\x00b\x1bc\nd") == "abc d"

# draw: frozen at the first draw, drawn once, a failure retried and given up after three builds, a removed run's card deleted
out = tempfile.mkdtemp()
calls = []
real_render = card.render
plex = card.fonts_ok() and bool(__import__("shutil").which("rsvg-convert"))
if not plex:
    try:
        card.render(card.svg(spec("aaaaaaaaaaaa")))
        raise AssertionError("drew without IBM Plex")
    except card.CardError as e:
        assert "IBM Plex" in str(e) or "rsvg-convert" in str(e), e
card.render = lambda text: (calls.append(text), b"\x89PNG fake")[1]
only = dict(b, runs={k: v for k, v in b["runs"].items() if k in ("aaaaaaaaaaaa", "bbbbbbbbbbbb")})
st = card.draw(only, recs, META, SITE, out, now="2026-10-20T10:00:00Z")
assert st == {"drawn": 2, "failed": 0, "waiting": 0, "skipped": 0}, st
frozen = json.load(open(os.path.join(card.CARDS, "aaaaaaaaaaaa.json")))
os.remove(os.path.join(out, "r", "aaaaaaaaaaaa.png"))   # the site folder lost: redrawn from the frozen spec
b2 = B.build(box, people + [rec(K3060, 90, "777777777777", "m9", at="2026-10-21T10:00:00")], MODELS, 60.0)
st = card.draw(dict(b2, runs=only["runs"]), recs, META, SITE, out, now="2026-10-22T10:00:00Z")
assert st == {"drawn": 1, "failed": 0, "waiting": 0, "skipped": 1}, st
assert calls[-1] == card.svg(frozen) and json.load(open(os.path.join(card.CARDS, "aaaaaaaaaaaa.json"))) == frozen   # same numbers as posted


def boom(text):
    raise card.CardError("rsvg-convert: broken")


card.render = boom
one = dict(b, runs={"cccccccccccc": b["runs"]["cccccccccccc"]})
for i in range(3):
    st = card.draw(one, recs, META, SITE, out)
    assert st["failed"] == 1, (i, st)
assert open(os.path.join(card.CARDS, "cccccccccccc.failed")).read() == "rsvg-convert: broken"
assert card.draw(one, recs, META, SITE, out)["skipped"] == 1   # given up: not retried
from llmbox import server  # noqa: E402
server.HOME = home
server.withdraw(["bbbbbbbbbbbb"], "owner")
card.draw(only, recs, META, SITE, out)
assert not os.path.exists(os.path.join(out, "r", "bbbbbbbbbbbb.png")) and not os.path.exists(os.path.join(card.CARDS, "bbbbbbbbbbbb.json"))
assert card.removed() == {"bbbbbbbbbbbb": "owner"}

# a real PNG where rsvg-convert and IBM Plex are installed (CI, the server)
card.render = real_render
if not plex:   # a machine that cannot draw at all (a laptop build) gives up on no card
    st = card.draw(dict(b, runs={"dddddddddddd": b["runs"]["dddddddddddd"]}), recs, META, SITE, out)
    assert st.get("unavailable") and not os.path.exists(os.path.join(card.CARDS, "dddddddddddd.tries")), st
if plex:
    png = card.render(card.svg(spec("aaaaaaaaaaaa")))
    w, h = struct.unpack(">II", png[16:24])
    assert png.startswith(b"\x89PNG") and (w, h) == (1200, 630), (w, h)
print("all passed" + ("" if plex else " (no IBM Plex here: the render itself was checked to refuse)"))
