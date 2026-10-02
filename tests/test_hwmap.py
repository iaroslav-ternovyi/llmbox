"""The hardware map (llmbox/site/hwmap.py) on the made-up runs of test_card: a cell per picker entry (67), each a link to its hardware
page with a spoken label; frames by state (measured solid, nobody yet dashed, AMD none); ticks one per machine up to
10 then +N; "~" on predicted numbers only; the header counts; tiers by VRAM; the Mac matrix by chip and tier; a NEW
tag only for an entry first measured in the last day; "first: @login" only when public; other machines listed with
their newest run. Run: python3 tests/test_hwmap.py"""
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
os.environ["LLMBOX_HOME"] = tempfile.mkdtemp()
from llmbox.site import hwmap  # noqa: E402

src = open(os.path.join(HERE, "test_card.py")).read().split("\ns = spec(\"aaaaaaaaaaaa\")")[0]
ns = {"__file__": os.path.join(HERE, "test_card.py")}
exec(compile(src, "test_card.py", "exec"), ns)
b = ns["b"]
users = {"u-000000a1": {"login": "octo", "public": True}}
now = time.mktime(time.strptime("2026-10-11T20:00:00", "%Y-%m-%dT%H:%M:%S"))   # the 3060 was first measured 10 hours before
h = hwmap.map_panel(b, users, now)
cells = re.findall(r'<a class="cell (\w)" href="(hw-[a-z0-9-]+\.html)" data-e="([^"]+)"[^>]*aria-label="([^"]+)"', h)
N = 67   # the picker's entries (M5 Ultra and M6 joined in 2026-10)
assert len(cells) == N and len({c[1] for c in cells}) == N, len(cells)
by = {c[2]: c for c in cells}
assert by["RTX 3060 12 GB"][0] == "m" and by["RTX 3080 10 GB"][0] == "u" and by["RX 7900 XTX 24 GB"][0] == "x" and by["Ryzen AI Max+ 395"][0] == "x"
assert by["RTX 3060 12 GB"][3].startswith("RTX 3060 12 GB: measured on 6 machines, best model qwen36-al at 44 tokens per second"), by["RTX 3060 12 GB"][3]
assert "nobody has measured this card yet" in by["RTX 3080 10 GB"][3] and "nobody has measured this Mac yet" in by["Mac M1"][3]
assert by["RX 7900 XTX 24 GB"][3].startswith("RX 7900 XTX 24 GB: AMD timing comes after launch")
one = lambda name: re.search(rf'data-e="{re.escape(name)}".*?</a>', h).group(0)
# a cell says what its number is and its unit: measured (on how many machines) or predicted
assert ">measured ×6<" in one("RTX 3060 12 GB") and '<span class="n">44<small> tok/s</small></span>' in one("RTX 3060 12 GB") and 'title="first: @octo"' in one("RTX 3060 12 GB")
assert '<span class="n pred">~' in one("RTX 3080 10 GB") and ">predicted<" in one("RTX 3080 10 GB") and "tok/s" in one("RTX 3080 10 GB") \
    and "nobody has measured this card yet" in one("RTX 3080 10 GB")
assert 'class="new"' in one("RTX 3060 12 GB") and 'class="new"' not in one("RTX 5070 12 GB")   # first measured 10 h ago vs weeks ago
assert 'title="first: u-0000000b"' in one("RTX 4060 8 GB")   # not public: the handle
assert re.search(rf"<b>\d+</b> of {N} measured so far", h) and "AMD: predicted only for now" in h and "Own one of these?" in h
tiers = re.findall(r'<h3 class="sc">([^<]+)</h3><ul class="cells[^"]*">(.*?)</ul>', h, re.S)
names = {t: re.findall(r'data-e="([^"]+)"', u) for t, u in tiers}
assert list(names) == ["8 GB", "10–12 GB", "16 GB", "20–24 GB", "32 GB and more", "Mac", "Ryzen AI Max"], list(names)
assert names["8 GB"][0] == "RTX 3060 Ti 8 GB" and names["8 GB"][-1] == "RX 7600 8 GB" and "RTX 2080 Ti 11 GB" in names["10–12 GB"]
assert names["32 GB and more"] == ["RTX 5090 32 GB", "RTX PRO 6000 96 GB", "Radeon AI PRO R9700 32 GB"]
assert sum(len(v) for v in names.values()) == N
assert 'style="grid-row:4;grid-column:5"><a class="cell u" href="hw-mac-m3-max-40-core.html"' in h   # M3, the Max with more GPU cores
assert 'style="grid-row:3;grid-column:6"><a class="cell u" href="hw-mac-m2-ultra.html"' in h
# the other machines, each to its newest run
assert '<a href="r/ffffffffffff.html">2 × RTX 3090 24 GB</a> · 1' in h and f"Not in the picker yet, so not counted in the {N}." in h
assert "<script" not in h and "javascript:" not in h
print("all passed")
