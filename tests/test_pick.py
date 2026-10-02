"""llmbox pick (llmbox/pick.py) on the pulled registry: models that fit come first, ranked by the score for the use;
the "not measurably apart" mark follows the 95% ranges of the whole score only; a small machine fits fewer models.
Run: python3 tests/test_pick.py   (needs `llmbox recipe pull` once)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import estimate as E, pick  # noqa: E402

big = E.HostSpec(vram_mib=24576, ram_mib=65536, ram_bw_gbs=60.0, vram_bw_gbs=936.0)
small = E.HostSpec(vram_mib=8192, ram_mib=16384, ram_bw_gbs=40.0, vram_bw_gbs=272.0)
rows = pick.rank(big, 16, "all")
ok = [x for x in rows if x["fits"]]
assert ok and [x["use_score"] for x in ok] == sorted((x["use_score"] for x in ok), reverse=True)
assert ok[0].get("tied") and all(x["t2"] > 0 for x in ok) and all(x["td"] is None or x["td"] <= x["t2"] * 1.1 for x in ok)
lo = ok[0]["range"][0]
assert all(x.get("tied") == (x["range"][1] >= lo) for x in ok if x.get("range")), "tied = the range reaches the best one's"
assert not any(x.get("tied") for x in pick.rank(big, 16, "coding")), "no per-use ranges: no tie marks per use"
coding = [x for x in pick.rank(big, 16, "coding") if x["fits"]]
assert [x["use_score"] for x in coding] == sorted((x["use_score"] for x in coding), reverse=True)
assert sum(x["fits"] for x in pick.rank(small, 8, "all")) < len(ok), "an 8 GB card with 16 GB RAM fits fewer models"
print(f"{len(ok)} fit a 24 GB card, best {ok[0]['name']}")
print("all passed")

# the pick: a model too slow to read along is recommended only when nothing reaches reading speed (pick.USABLE_TPS)
from llmbox import pick as _p  # noqa: E402
_slow = {"id": "big", "name": "Big", "use_score": 90, "t2": 7.0, "td": 5.0, "tied": False}
_fast = {"id": "mid", "name": "Mid", "use_score": 80, "t2": 35.0, "td": 30.0, "tied": False}
b, why = _p.choose([_slow, _fast])
assert b["id"] == "mid" and "reading speed" in why and "Big scores higher at ~7" in why, (b, why)
b, why = _p.choose([_slow, dict(_fast, t2=12.0, td=10.0)])
assert b["id"] == "big" and "nothing here reaches 20" in why, (b, why)
print("usable-speed pick ok")
