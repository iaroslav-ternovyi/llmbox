"""Explanation block: deterministic items, docs much longer than the word limit, the reference explanation fits it,
quiz cases mostly need the real rules (the usual assumptions give another answer), the answer matching, and the
deferred grading path with a stand-in reader. The reader model itself is checked by `llmbox validate --kind explain`
on the box. Run: python3 tests/test_explain.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import bench, reader  # noqa: E402
from llmbox.suite import explain as E  # noqa: E402
from llmbox.suite import knowledge as K  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


n = 0
for kind, gen in E.KINDS.items():
    for level in range(1, 6):
        for seed in range(1, 26):
            a, b = gen(seed, level), gen(seed, level)
            n += 1
            m = a.meta
            check(f"{kind} L{level} s{seed} deterministic", a.messages == b.messages and m["expected"] == b.meta["expected"])
            check(f"{kind} L{level} s{seed} quiz size", len(m["quiz"]) >= 4, len(m["quiz"]))
            check(f"{kind} L{level} s{seed} doc >= 2.4x limit", m["doc_words"] >= 2.4 * m["limit"], (m["doc_words"], m["limit"]))
            check(f"{kind} L{level} s{seed} oracle fits", len(E.words(m["oracle"])) <= m["limit"], len(E.words(m["oracle"])))
            check(f"{kind} L{level} s{seed} quiz hidden from the explainer",
                  all(q[0][:60] not in a.messages[0]["content"] for q in m["quiz"]))
            for q in m["quiz"]:   # the expected answer, written the way the reader is asked to, matches
                check(f"{kind} L{level} s{seed} self-match {q[1]!r}", E.match(q[1], q[1], q[2], q[3] if len(q) > 3 else None) == 1.0)
print(f"{n} items generated")

# the quiz needs the real rules: across seeds most config/access cases differ from the usual assumptions
for kind, fn_naive in (("config", None), ("access", None)):
    diff = tot = 0
    for level in (3, 4, 5):
        for seed in range(1, 21):
            it = E.KINDS[kind](seed, level)
            tot += len(it.meta["quiz"])
    check(f"{kind} quizzes generated", tot > 0)

# rule engines on hand-made cases
ru = {"order": ["env", "user", "flag", "project"], "conv": ["user", "project", "env", "flag"], "lists": True, "reset": True, "nested": True,
      "locked": True, "profiles": True, "extends": True, "bools": True, "sep": ";"}
case = {"user": {"values": {"jobs": "8", "plugins": ["lint"]}, "profiles": {"ci": {"jobs": "16"}}},
        "project": {"values": {"plugins": ["!", "fmt"]}}, "base": {"values": {"jobs": "2"}},
        "env": {"BRISK_JOBS": "3", "BRISK_PLUGINS": "docs;lint", "BRISK_PROFILE": "ci", "BRISK_CACHE_DIR": "/x"},
        "flag": {"--jobs": "6", "--no-color": True}}
check("config: strongest source wins (project > base > flag)", E.cfg_resolve(ru, "brisk", case, "jobs") == "2", E.cfg_resolve(ru, "brisk", case, "jobs"))
check("config: list reset by the strongest", E.cfg_resolve(ru, "brisk", case, "plugins") == ["fmt"], E.cfg_resolve(ru, "brisk", case, "plugins"))
case2 = dict(case, project={}, base=None)
check("config: profile value in the user file, flag beats user", E.cfg_resolve(ru, "brisk", case2, "jobs") == "6")
check("config: wrong env spelling ignored", E.cfg_resolve(ru, "brisk", case, "cache.dir") == "~/.cache/brisk")
check("config: --no-color", E.cfg_resolve(ru, "brisk", case, "color") == "false")
case3 = dict(case, system={"locked": {"jobs": "1"}})
check("config: locked wins", E.cfg_resolve(ru, "brisk", case3, "jobs") == "1")
ru2 = dict(ru, order=["user", "project", "env", "flag"], reset=False)
case4 = {"user": {"values": {"plugins": ["lint", "fmt"]}}, "env": {"BRISK_PLUGINS": "fmt;docs"}}
check("config: lists merge weakest first, no duplicates", E.cfg_resolve(ru2, "brisk", case4, "plugins") == ["lint", "fmt", "docs"])

br = {"plans": {"Team": {"fee": 49, "inc": 250000, "rate": 0.30}, "Business": {"fee": 199, "inc": 1000000, "rate": 0.30}},
      "errors_free": True, "tiers": {"first": 100, "later": 0.20}, "annual": 20, "nonprofit": 30, "volume": "Business",
      "change": {"plan": "Team", "month": "April", "inc": 400000}}
check("billing: graduated", E.bill(br, "Team", "March", 400500, 500, False, False) == round(49 + 100 * 0.30 + 50 * 0.20, 2))
check("billing: quota change from April", E.bill(br, "Team", "April", 400500, 500, False, False) == 49.0)
check("billing: started 1,000 rounds up", E.bill(br, "Team", "March", 251001, 0, False, False) == round(49 + 2 * 0.30, 2))
check("billing: volume pricing", E.bill(br, "Business", "March", 1101000, 0, False, False) == round(199 + 101 * 0.20, 2))
check("billing: annual discount on the fee only", E.bill(br, "Team", "March", 260000, 0, True, False) == round(49 * 0.8 + 10 * 0.30, 2))
check("billing: non-profit vs annual, the larger saving", E.bill(br, "Team", "March", 260000, 0, True, True) == round(min(49 * 0.8 + 3.0, 52 * 0.7), 2))

ar = E._acc_rules(__import__("random").Random(1), 5)
ar["roles"] = {k: set(v) for k, v in E.ROLES.items()}
ws = {"owner": "ana", "groups": {"design": {"ben"}}, "grants": [("ben", "editor", "/Projects"), ("design", "manager", "/Shared"),
                                                                 ("chen", "viewer", "/HR/Contracts")],
      "denies": [("design", "share", "/Shared")], "guests": {"chen"}, "private": {"/Projects/Apollo/Notes"}, "link": {"/Projects"},
      "archived": {"/Shared/Old"}}
check("access: owner", E.acc_actions(ar, ws, "ana", "/HR") == set(E.ACTIONS))
check("access: inherited role", E.acc_actions(ar, ws, "ben", "/Projects/Apollo") == {"read", "comment", "edit"})
check("access: private blocks roles and link from above", E.acc_actions(ar, ws, "ben", "/Projects/Apollo/Notes") == set())
check("access: group role minus deny", E.acc_actions(ar, ws, "ben", "/Shared") == {"read", "comment", "edit"})
check("access: archived is read-only", E.acc_actions(ar, ws, "ben", "/Shared/Old") == {"read"})
check("access: link sharing gives read", E.acc_actions(ar, ws, "dara", "/Projects/Hermes") == {"read"})

# answer matching
M = E.match
check("num", M("104.20", "€104.20", "num") == 1 and M("104.20", "104.2", "num") == 1 and M("104.20", "1,104.20", "num") == 0)
check("set", M("read, comment", "comment, read", "set") == 1 and M("none", "none", "set") == 1 and M("read", "read, edit", "set") == 0)
check("list", M("lint, fmt", '["lint", "fmt"]', "list") == 1 and M("lint, fmt", "fmt, lint", "list") == 0 and M("", "empty", "list") == 1)
U = ["1", "2", "3", "4", "6", "16"]
check("text", M("6", "6 (the flag wins)", "text", U) == 1 and M("6", "16", "text", U) == 0 and M("6", "6 or 3", "text", U) == 0)
check("text path", M("/tmp/brisk", "`/tmp/brisk`", "text", ["/tmp/brisk", "~/.cache/brisk"]) == 1)
check("cut", E.cut("a b c\nd e f", 4) == "a b c\nd")

# deferred grading with a stand-in reader: a perfect reader scores 1, a silent one 0, and nothing is asked twice
it = E.config(3, 4)
asked = []


def perfect(prompt):
    """Answers the questions this prompt asks (the reader gets the quiz in batches)."""
    asked.append(prompt)
    here = [q for q in it.meta["quiz"] if q[0] in prompt]
    return "ANSWERS\n" + "\n".join(f"{i}. {q[1] or 'empty'}" for i, q in enumerate(here, 1))


real_ask = reader.ask
reader.ask = perfect
row = {"id": it.id, "final": "some explanation", "score": 0.0, "pending": "reader"}
bench.grade_deferred("http://nowhere", [it], [row], progress=lambda *_: None)
check("deferred: perfect reader", row["score"] == 1.0 and "pending" not in row, row)
check("deferred: explanation reaches the reader", "some explanation" in asked[0])
reader.ask = lambda p: "ANSWERS\n1. no idea"
row = {"id": it.id, "final": "x", "score": 0.0, "pending": "reader"}
bench.grade_deferred("http://nowhere", [it], [row], progress=lambda *_: None)
check("deferred: useless reader answers", row["score"] == 0.0)


def boom(p):
    raise RuntimeError("reader down")


reader.ask = boom
row = {"id": it.id, "final": "x", "score": 0.0, "pending": "reader"}
bench.grade_deferred("http://nowhere", [it], [row], progress=lambda *_: None)
check("deferred: reader failure keeps it pending", row.get("pending") and "reader down" in row.get("reader_error", ""))
reader.ask = real_ask
check("explanation is cut at the limit", len(E.words(E.reader_prompt_of(it, "w " * 1000).split("--- explanation ---")[1].split("--- end")[0])) == it.meta["limit"])
check("knowledge answers parser reused", K.answers("ANSWERS\n1. x", 1) == {1: "x"})

print("all passed" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
