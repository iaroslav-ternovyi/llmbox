"""A picker entry's page and feed (llmbox/site/hw.py) on the made-up runs of test_card: the best model and its number
(measured: the group behind it named; predicted: "~", and "rough" on AMD), the measurements per model and comparison
group, recent runs linking to their pages, who was first (@login only when public), "be the first" with the badge it
earns, AMD's "after launch" state, the feed link in the head. The feed: well-formed RSS, the set wording, a guid that
does not depend on the site's address, what was measured before it started folded into its first item.
Run: python3 tests/test_hwpage.py"""
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
home = tempfile.mkdtemp()
os.environ["LLMBOX_HOME"] = home
from llmbox.site import board as B, hw  # noqa: E402

src = open(os.path.join(HERE, "test_card.py")).read().split("\ns = spec(\"aaaaaaaaaaaa\")")[0]
ns = {"__file__": os.path.join(HERE, "test_card.py")}
exec(compile(src, "test_card.py", "exec"), ns)
b, META, K3060 = ns["b"], ns["META"], ns["K3060"]   # (its model list names models by id: "qwen36-al")
SITE = "http://127.0.0.1:8770"   # a preview address: links use it, feed guids do not
users = {"u-000000a1": {"login": "octo", "public": True}, "u-0000000b": {"public": False}}

h = hw.hw_page("RTX 3060 12 GB", b, META, users, SITE)
t = re.sub(r"<[^>]+>", "", h)
assert "What runs best on an RTX 3060 12 GB" in t and "Best here: qwen36-al · 82% of Claude Opus 5.5 · 44 tok/s" in t, t[:900]
assert "the median of 6 machines (RAM 45–65 GB/s)" in t and "with 64 GB of DDR5-5600 RAM (60 GB/s)" in t, t[:900]
assert "first: @octo · first on this card" in t and 'href="r/000000000001.html"' in h
# every model here, best score first: the measured median where there is one, else the prediction, and the line that runs the best
assert re.search(r'<tr class="me">.*qwen36-al.*BEST HERE.*<td data-h="score">82%</td><td data-h="tok/s"><b>44</b> <span class="q">measured ×6</span>', h), h
assert re.search(r'qwen35-9b.*<b class="pred">~\d+</b> <span class="q">predicted</span>', h) and "install.sh | sh -s -- qwen36-al" in t
assert t.index("Every model on") < t.index("Recent runs") < t.index("Own one?")   # measuring it yourself comes last
assert 'href="r/aaaaaaaaaaaa.html"' in h and "not confirmed" in t   # recent runs, the outlier marked
assert '<link rel="alternate" type="application/rss+xml" title="llmbox · RTX 3060 12 GB" href="feeds/rtx-3060-12gb.xml">' in h
assert "Add yours" in t and "Measure it" not in t
h = hw.hw_page("RTX 4060 8 GB", b, META, users, SITE)
assert "first: u-0000000b · first on this card" in re.sub(r"<[^>]+>", "", h) and 'href="u-0000000b.html"' in h   # not public: the handle
h = hw.hw_page("RTX 3080 10 GB", b, META, users, SITE)
t = re.sub(r"<[^>]+>", "", h)
assert "Nobody has measured an RTX 3080 10 GB yet" in t and "FIRST ON THIS CARD" in t and '<b class="pred">~' in h, t[:900]
assert f"{SITE}/install.sh | sh\nllmbox test" in t and "first:" not in t
h = hw.hw_page("RX 7900 XTX 24 GB", b, META, users, SITE)
t = re.sub(r"<[^>]+>", "", h)
assert "tok/s, rough" in t and "Measuring on AMD comes after launch" in t and "llmbox test" not in t, t[:900]
h = hw.hw_page("Mac M3 Ultra", b, META, users, SITE)
assert "What runs best on a Mac M3 Ultra" in h and "with 96 GB of memory" in h and "FIRST ON THIS MAC" in h
h = hw.hw_page("RTX 5070 12 GB", b, META, users, SITE)
assert "first USER" not in h and "first user on this card" in h   # the box measured first

# feeds
log = os.path.join(home, "events.jsonl")
events, new = B.events(b, "2026-10-20T10:00:00Z", path=log)
B.append(new, log)
x = hw.feed("RTX 3060 12 GB", b, events, META, SITE)
items = [(i.find("title").text, i.find("guid").text) for i in ET.fromstring(x).iter("item")]
assert items == [("Feed started: RTX 3060 12 GB already measured on 6 machines; this feed lists what is measured from now on. Best: qwen36-al, 44 tok/s.",
                  "tag:llmbox.pages.dev,2026:rtx-3060-12gb/start/rtx-3060-12gb")], items
x = hw.feed("RTX 3080 10 GB", b, events, META, SITE)
(title, guid), = [(i.find("title").text, i.find("guid").text) for i in ET.fromstring(x).iter("item")]
assert re.fullmatch(r"Feed started: nobody has measured an RTX 3080 10 GB yet\. Predicted best: .+, ~\d+ tok/s\.", title), title
assert "AMD timing comes after launch; this feed will say when." in hw.feed("RX 7900 XTX 24 GB", b, events, META, SITE)
# a model measured after the feed started is an item; a new best is an item
b2 = B.build(ns["box"], ns["people"] + [ns["rec"](K3060, 33, "abababababab", "m8", at="2026-10-21T10:00:00", rid="qwen35-9b")], ns["MODELS"], 60.0)
events2, new2 = B.events(b2, "2026-10-21T12:00:00Z", path=log)
x = hw.feed("RTX 3060 12 GB", b2, events2, META, SITE)
root = ET.fromstring(x)
first = root.find("channel/item")
assert first.find("title").text == "RTX 3060 12 GB: Qwen3.5-9B UD-Q4_K_XL measured, 33 tok/s (1 machine)" and first.find("pubDate").text == "Wed, 21 Oct 2026 12:00:00 GMT", ET.tostring(first)
assert hw.item_text({"kind": "best", "entry": "RTX 3070 8 GB", "model": "M", "score": 89.4, "tps": 29.2, "predicted": True}, META) == "RTX 3070 8 GB: new best, M, 89% of Opus, ~29 tok/s"
assert root.find("channel/link").text == f"{SITE}/hw-rtx-3060-12gb"
print("all passed")
