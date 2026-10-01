"""The guided start (llmbox/wizard.py) as far as its plan, for the box, and the recommendation rule (pick.choose): the
best score, unless a model not measurably apart from it, at most 5 points below, runs 1.3x as fast 32k into a session.
Run: python3 tests/test_wizard.py   (needs the box's profile and `llmbox recipe pull`)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import pick, wizard  # noqa: E402

lines = []
assert wizard.main(host="box", plan_only=True, out=lines.append) == 0
text = "\n".join(lines)
assert text.startswith("This computer: RTX 5070 12 GB"), text
# the box has every model installed: a second visit - what is here, and whether something better came out
assert "On this computer:" in text and "more" in text and ("is still the best for it" in text or "Better for it now:" in text), text
from llmbox import recipe as rc  # noqa: E402
ids = rc.ids
rc.ids = lambda host: []   # a first visit: the plan with its numbers and the why
lines = []
assert wizard.main(host="box", plan_only=True, out=lines.append) == 0
rc.ids = ids
text = "\n".join(lines)
assert "Best for it:" in text and "why:" in text and "tokens/s" in text and "On this computer" not in text, text

x = lambda i, s, t2, td, tied=True: {"id": i, "use_score": s, "t2": t2, "td": td, "tied": tied}
best, why = pick.choose([x("a", 88, 55, 52), x("b", 87, 60, 58), x("c", 84, 90, 80)])
assert best["id"] == "c" and "1.5x" in why, (best, why)          # 4 points below, 1.5x as fast: the trade is worth it
best, _ = pick.choose([x("a", 88, 55, 52), x("b", 80, 120, 110)])
assert best["id"] == "a", "8 points below is too far to trade for speed"
best, _ = pick.choose([x("a", 88, 55, 52), x("b", 86, 70, 60)])
assert best["id"] == "a", "15% faster is not enough to give up score"
best, _ = pick.choose([x("a", 88, 55, 52), x("b", 86, 90, 80, tied=False)])
assert best["id"] == "a", "measurably below the best: never the pick"
# a model on an engine the machine does not have (ik_llama.cpp: no release builds) is never the recommendation
best, _ = pick.choose([dict(x("k2", 90, 80, 70), needs="ik_llama.cpp"), x("a", 88, 55, 52)])
assert best["id"] == "a", best
assert pick.engines_of({"hw": {"runtimes": [{"path": "/h/ik_llama.cpp/build/bin/llama-server"}]}}) == {"llama.cpp", "ik_llama.cpp"}
assert pick.engines_of(None) == {"llama.cpp"}
# doctor on a first run (nothing detected yet): not a failure - it points at the guided start and still checks the rest
from llmbox import doctor, hosts  # noqa: E402
names = hosts.names
hosts.names = lambda: []
items = doctor.checks()
hosts.names = names
first = next(i for i in items if "looked at" in i[1])
assert first[0] is None and first[2].startswith("llmbox "), first
assert any("reaches" in i[1] or "cannot reach" in i[1] for i in items), items   # the network checks still run

print(lines[2] if len(lines) > 2 else text)
print("all passed")
