"""A person's run page (llmbox/site/run.py user_run_page, removed_page) and how publish.finish treats it, on the
made-up runs of test_card: the sentence and place line, the card or its worded state (waiting, retrying, failed),
DOWNLOAD CARD named for the entry, COPY LINK, the suggested title and post links, "Your machine?" preset to the
run's entry; the quality line per case (agrees, mismatch, held, anonymous, speed only); NOT CONFIRMED; "no longer the
first"; the card drawn earlier vs today; the settings without the sender's model path; hostile text escaped. The
removed page says why and stays out of search and the sitemap; a drawn run's page carries its card as the social
preview, with the card's text as alt; nested pages get their own canonical address. runpage.js's line per machine.
Run: python3 tests/test_runpage.py"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
home = tempfile.mkdtemp()
os.environ["LLMBOX_HOME"] = home
os.environ["LLMBOX_SITE"] = "https://llmbox.pages.dev"
from llmbox import hwclass  # noqa: E402
from llmbox.site import board as B, card, publish, run as R  # noqa: E402

# the made-up runs of test_card (its records, board and model metadata), without its draw checks
src = open(os.path.join(HERE, "test_card.py")).read().split("\ns = spec(\"aaaaaaaaaaaa\")")[0]
ns = {"__file__": os.path.join(HERE, "test_card.py")}
exec(compile(src, "test_card.py", "exec"), ns)
b, recs, META, SITE = ns["b"], ns["recs"], ns["META"], ns["SITE"]
machines = [[n, e["slug"], e["kind"], (e.get("best") or {}).get("name"), (e.get("best") or {}).get("predicted"), False, e["testable"]]
            for n, e in b["entries"].items()]
for r in recs.values():   # the recipe each run used, as a record carries it (a model path on the sender's disk)
    r[0]["recipe"] = dict(r[0]["recipe"], placement={"ctx": 65536, "kv_type": "q8_0"}, model={"path": "/home/alice/secret/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"})


def page(sid, state="drawn", frozen=None):
    live = card.spec(b, sid, recs[sid], META, SITE, now="2026-10-20T10:00:00Z", wait=False)
    return R.user_run_page(sid, b, recs[sid], live, frozen, state, machines, SITE), live


html, live = page("aaaaaaaaaaaa")
text = re.sub(r"<[^>]+>", "", html)
assert 'RTX 3060 12 GB · 64 GB RAM runs Qwen3.6-35B-A3B at <span class="amb">46</span> tok/s' in html, html[:2000]
assert "faster than 100% of 5 machines like it" in text and 'class="strip"' in html
assert 'download="llmbox-rtx-3060-12gb-46tps.png"' in html and 'src="r/aaaaaaaaaaaa.png"' in html and "COPY LINK" in html
assert 'data-copy="https://llmbox.pages.dev/r/aaaaaaaaaaaa"' in html and "reddit.com/r/LocalLLaMA/submit" in html and "x.com/intent/post?text=" in html
assert re.search(r'<option value="rtx-3060-12gb" selected>RTX 3060 12 GB</option>', html) and '<base href="/">' in html
assert "quality: 82% of Claude Opus 5.5 · this run agrees ✓" in text
assert "/home/alice" not in html and "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf" in html and "--port" not in html   # the file name only
assert 'alt="RTX 3060 12 GB · 64 GB RAM runs Qwen3.6-35B-A3B UD-Q4_K_XL at 46 tokens per second' in html
# the card drawn earlier said something else: both, dated
old = dict(live, place={"kind": "others", "text": "4 others like it: 40 · 41 · 42 · 43 tok/s"}, drawn="2026-10-14T09:00:00Z")
assert "Card drawn 2026-10-14: 4 others like it: 40 · 41 · 42 · 43 tok/s. Today: faster than 100% of 5 machines like it." in page("aaaaaaaaaaaa", frozen=old)[0]
# card states in words; COPY LINK stays, DOWNLOAD CARD goes
for st, words in (("waiting", "once this run's quality answers are graded"), ("retrying", "retried at the next build"), ("failed", "could not be drawn; the site's owner")):
    h = page("aaaaaaaaaaaa", st)[0]
    assert words in h and "DOWNLOAD CARD" not in h and "COPY LINK" in h and "r/aaaaaaaaaaaa.png" not in h, st
# the badge, and "no longer the first"
h = page("bbbbbbbbbbbb")[0]
assert '<span class="badge">FIRST ON THIS CARD</span>' in h and "speed only: this run had no quality test" in h
h = page("cccccccccccc", frozen=dict(card.spec(b, "cccccccccccc", recs["cccccccccccc"], META, SITE), badge="FIRST ON THIS CARD", drawn="2026-10-12T00:00:00Z"))[0]
assert "no longer the first here (a run received earlier was published after it)" in h
# quality: a mismatch, held for review, anonymous
assert "does not match the model's published score" in page("dddddddddddd")[0]
recs["dddddddddddd"][1]["submission"]["flags"] = ["self-seeded"]
h = page("dddddddddddd")[0]
assert "quality held for review: its tasks were not the ones the server drew for it" in h and 'href="method.html#trust"' in h
recs["dddddddddddd"][1]["submission"]["flags"] = ["anonymous"]
assert "quality not counted: sent without an account" in page("dddddddddddd")[0]
# not confirmed: the badge, the line, the link; a malformed build tag is not printed
h = page("999999999999")[0]
assert '<span class="badge">NOT CONFIRMED</span>' in h and "left out of comparisons until someone repeats it" in h and "rm -rf" not in h
# outside the picker: no entry preset, no hardware page in the crumb
h = page("ffffffffffff")[0]
assert '<option value="" selected>choose yours</option>' in h and "hw-" not in h.split("</h1>")[0]
# hostile text from a record stays text
recs["eeeeeeeeeeee"][0]["host"] = dict(recs["eeeeeeeeeeee"][0]["host"], cpu='<script>alert(1)</script>', gpu='"><img src=x>')
h = page("eeeeeeeeeeee")[0]
assert "<script>alert(1)" not in h and "<img src=x>" not in h and "&lt;script&gt;" in h

# publish.finish: the run page's own image and alt, the removed page out of search and the sitemap, nested addresses
out = tempfile.mkdtemp()
os.makedirs(os.path.join(out, "r"))
pa, pr = os.path.join(out, "r", "aaaaaaaaaaaa.html"), os.path.join(out, "r", "bbbbbbbbbbbb.html")
open(pa, "w").write(page("aaaaaaaaaaaa")[0])
open(pr, "w").write(R.removed_page("bbbbbbbbbbbb", "owner"))
publish.finish(out, [pa, pr], {"r/aaaaaaaaaaaa.html": ("r/aaaaaaaaaaaa.png", R._alt(live))}, {"r/bbbbbbbbbbbb.html"})
ha, hr = open(pa).read(), open(pr).read()
assert '<link rel="canonical" href="https://llmbox.pages.dev/r/aaaaaaaaaaaa">' in ha, re.findall(r'<link rel="canonical"[^>]+>', ha)
assert 'og:image" content="https://llmbox.pages.dev/r/aaaaaaaaaaaa.png"' in ha and 'og:image:alt" content="RTX 3060 12 GB' in ha and "noindex" not in ha
assert "Its owner removed it." in hr and '<meta name="robots" content="noindex">' in hr and 'og:image" content="https://llmbox.pages.dev/og.png"' in hr
sm = open(os.path.join(out, "sitemap.xml")).read()
assert "/r/aaaaaaaaaaaa<" in sm and "bbbbbbbbbbbb" not in sm, sm
csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', ha).group(1)
assert "'unsafe-inline'" not in csp.split("style-src")[0] and "sha256-" in csp, csp   # the inline DATA by hash only

if shutil.which("node"):
    r = subprocess.run(["node", "-e", f"const m = require({json.dumps(os.path.join(HERE, '..', 'llmbox', 'site', 'assets', 'runpage.js'))});"
                        "console.log(JSON.stringify([m.bestLine(['RTX 3060 12 GB','rtx-3060-12gb','card','Qwen3.6',40.2,true,true]),"
                        "m.bestLine(['RX 7900 XTX 24 GB','rx-7900-xtx-24gb','amd','Gemma',80.6,false,false]), m.bestLine(['X','x','card',null,null,false,true])]))"],
                       capture_output=True, text=True)
    a, d, n = json.loads(r.stdout)
    assert a == {"line": "best here: Qwen3.6 · 40 tok/s", "page": "hw-rtx-3060-12gb.html", "feed": "feeds/rtx-3060-12gb.xml", "name": "RTX 3060 12 GB"}, a
    assert d["line"] == "best here: Gemma · ~81 tok/s predicted · AMD timing comes after launch" and n["line"] == "nothing fits at 64 GB of RAM", (d, n)
print("all passed")
