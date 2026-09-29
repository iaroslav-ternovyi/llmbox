"""knowledge.js: deterministic items built fast, the oracle scores 1, UNKNOWN everywhere scores the idk credit, invented
answers score 0, right answers in the usual shapes still score 1; the emulator on programs whose Node output is known; and,
when Node is installed, a sample of generated programs on real Node plus every made-up API checked to be missing.
Run: python3 tests/test_knowledge_js.py   (the full check against Node: python3 tests/real_programs.py js)"""
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import suite  # noqa: E402
from llmbox.suite import knowledge as K, knowledge_js as J  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


# the quick tier and the bank-based kinds are untouched
check("quick tier unchanged", K.QUICK == ["python", "shell", "codes"] and len(suite.QUICK_ITEMS) == 32, (K.QUICK, len(suite.QUICK_ITEMS)))
check("registered", K.KINDS["js"] is J.gen and suite.max_level("knowledge", "js") == 10)


def lines(it, answers):
    return "ANSWERS\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(answers, 1))


def variants(q, e):
    """The oracle answer as models write it."""
    if q["fake"]:
        return ["NONEXISTENT", "**NONEXISTENT**", "NONEXISTENT (no such method in Node 22)", "throws TypeError",
                "TypeError: x is not a function"]
    if q["mode"] == "seq":
        labs = e.split()
        return [e, f"`{e}`", ", ".join(labs), " -> ".join(labs), " → ".join(labs), "[" + ", ".join(labs) + "]",
                " ".join(f'"{x}"' for x in labs), f"**{e}**", e + "."]
    if q["mode"] == "throws":
        return [e, "TypeError", "throws `TypeError`", "throws TypeError: Do not know how to serialize a BigInt"]
    loose = re.sub(r"\[ ", "[", re.sub(r" \]", "]", e)).replace("'", '"')
    return [e, f"`{e}`", loose, re.sub(r", ", ",", e), e + " (Node's util.inspect format)"]


n = 0
slow = 0.0
for level in range(1, 11):
    for seed in range(1, 31):
        t0 = time.time()
        a = J.gen(seed, level)
        slow = max(slow, time.time() - t0)
        b = J.gen(seed, level)
        n += 1
        qs, exp = a.meta["questions"], a.meta["expected"]
        nq, nf = len(qs), sum(q["fake"] for q in qs)
        tag = f"js L{level} s{seed}"
        check(f"{tag} deterministic", a.messages == b.messages and exp == b.meta["expected"])
        check(f"{tag} {nq} questions, 2 made up", nq == {7: 10, 8: 10, 9: 10, 10: 12}.get(level, 8) and nf == 2, (nq, nf))
        check(f"{tag} one hidden made-up call from level 9", sum(q["src"] == "hidden" for q in qs) == (level >= 9))
        check(f"{tag} oracle", a.check(K.oracle(a)) == 1.0, K.oracle(a))
        check(f"{tag} all UNKNOWN", abs(a.check(lines(a, ["UNKNOWN"] * nq)) - J.IDK) < 1e-9)
        check(f"{tag} empty", a.check("") == 0.0)
        check(f"{tag} bluff", a.check(lines(a, ["z9 z8"] * nq)) == 0.0)
        half = [("[ 12345 ]" if q["fake"] else e) for q, e in zip(qs, exp)]
        check(f"{tag} invented", abs(a.check(lines(a, half)) - (nq - nf) / nq) < 1e-9)
        bd = J.breakdown(a, lines(a, half))
        check(f"{tag} breakdown", bd["right"] == nq - nf and bd["invented"] == nf, bd)
        for k in range(5):   # right answers in other shapes
            ans = [variants(q, e)[k % len(variants(q, e))] for q, e in zip(qs, exp)]
            check(f"{tag} format {k}", a.check(lines(a, ans)) == 1.0, [(e, v) for q, e, v in zip(qs, exp, ans) if J.credit(q, v) < 1])
        for i, (q, e) in enumerate(zip(qs, exp)):   # near misses are wrong
            if q["mode"] == "seq" and len(q["accept"]["seq"]) >= 2:
                s = q["accept"]["seq"]
                j = next((j for j in range(len(s) - 1) if s[j] != s[j + 1]), None)
                if j is not None:
                    sw = s[:j] + [s[j + 1], s[j]] + s[j + 2:]
                    check(f"{tag} q{i + 1} swapped labels wrong", J.verdict(q, " ".join(sw)) == "wrong")
                check(f"{tag} q{i + 1} a label missing wrong", J.verdict(q, " ".join(s[:-1])) == "wrong")
            if not q["fake"]:
                check(f"{tag} q{i + 1} NONEXISTENT on a real one", J.verdict(q, "NONEXISTENT") == "wrong")
            if q["mode"] == "line":
                check(f"{tag} q{i + 1} a changed line wrong", J.verdict(q, e.replace("1", "2", 1) if "1" in e else e + " 0") == "wrong")
print(f"{n} items generated, slowest {slow * 1000:.0f} ms")

# ---- the emulator on programs whose output Node is known to give ---------------------------------------------------------
L_ = lambda x: ("log", x)
known = [
    # nextTick before promise reactions; a nextTick queued by a reaction waits for the microtask queue to empty
    ({"fns": {}, "main": [("chain", ("res",), [("then", [L_("p1"), ("tick", [L_("t2")]), ("chain", ("res",), [("then", [L_("p3")])], None)])], None),
                          ("tick", [L_("t1")]), L_("s1")]}, "s1 t1 p1 p3 t2"),
    # returning a promise from a handler costs two more ticks
    ({"fns": {}, "main": [("chain", ("res",), [("then", [L_("a1"), ("return", ("res",))]), ("then", [L_("a2")])], None),
                          ("chain", ("res",), [("then", [L_("b1")]), ("then", [L_("b2")]), ("then", [L_("b3")]), ("then", [L_("b4")])], None)]},
     "a1 b1 b2 b3 a2 b4"),
    # await on a plain value: one tick; on a thenable: its then runs a tick later, the function one tick after that
    ({"fns": {"f": [L_("f1"), ("await", ("thenable", [L_("th")])), L_("f2")]},
      "main": [("chain", ("call", "f"), [], None), ("chain", ("res",), [("then", [L_("b1")]), ("then", [L_("b2")]), ("then", [L_("b3")])], None)]},
     "f1 th b1 f2 b2 b3"),
    # .finally passes the value on three ticks later
    ({"fns": {}, "main": [("chain", ("res",), [("finally", [L_("a1")]), ("then", [L_("a2")])], None),
                          ("chain", ("res",), [("then", [L_("b1")]), ("then", [L_("b2")]), ("then", [L_("b3")]), ("then", [L_("b4")])], None)]},
     "a1 b1 b2 b3 a2 b4"),
    # setTimeout(0) is setTimeout(1): one list, the order they were set
    ({"fns": {}, "main": [("timeout", [L_("t1")], 1), ("timeout", [L_("t0")], 0)]}, "t1 t0"),
    # microtasks run after each timer callback, not after the whole list
    ({"fns": {}, "main": [("timeout", [L_("a"), ("chain", ("res",), [("then", [L_("ap")])], None)], 0), ("timeout", [L_("b")], 0)]}, "a ap b"),
]
ref = [("chain", ("res",), [("then", [L_(f"b{i}")]) for i in range(1, 5)], None)]   # a tick counter
known += [   # levels 9-10 (each checked on Node 22, 24 and 25)
    ({"fns": {}, "main": [("chain", ("all", [("thenable", [L_("t")]), ("res",)]), [("then", [L_("A")])], None)] + ref}, "t b1 b2 A b3 b4"),
    ({"fns": {}, "main": [("chain", ("res",), [("then", [L_("h1"), ("await", ("null",)), L_("h2")], "async"), ("then", [L_("A")])], None)] + ref},
     "h1 b1 h2 b2 b3 A b4"),
    # 'exit' listeners: V8 still empties the microtask queue, Node runs no nextTick and no timer
    ({"fns": {}, "main": [("onexit", [L_("e1"), ("tick", [L_("e2")]), ("micro", [L_("e3")]), ("timeout", [L_("e4")], 0), L_("e5")])] + ref},
     "b1 b2 b3 b4 e1 e5 e3"),
    # a once-listener for 'beforeExit' gets a full drain and may start the loop again
    ({"fns": {}, "main": [("beforeexit", [L_("x1"), ("tick", [L_("x2")]), ("micro", [L_("x3")]), ("timeout", [L_("x4")], 0)]),
                          ("onexit", [L_("e1")])] + ref}, "b1 b2 b3 b4 x1 x2 x3 x4 e1"),
]
for prog, want in known:
    got = " ".join(J.run_program(prog))
    check(f"emulator {want}", got == want, got)
# an unhandled rejection ends the process when that drain is over: the nextTick queued before it still runs, the timer not
lp = J.Loop({"fns": {}, "main": [("timeout", [L_("t1")], 0), ("chain", ("res",), [("then", [L_("p1"), ("tick", [L_("n1")]),
                                                                                              ("chain", ("rejp",), [], None)])], None)]},
            allow_crash=True)
check("crash", " ".join(lp.execute()) == "p1 n1" and lp.crashed)
for bad in [{"fns": {}, "main": [("chain", ("rejp",), [("then", [L_("x")])], None)]},                  # never caught
            {"fns": {}, "main": [("timeout", [L_("a")], 0), ("immediate", [L_("b")])]},              # a race in real Node
            {"fns": {}, "main": [("timeout", [L_("a")], 50), ("timeout", [L_("b")], 0)]}]:           # overtakes: a stall turns it
    try:
        J.run_program(bad)
        check(f"refused {bad['main'][0][0]}", False)
    except J.Invalid:
        pass

# JavaScript values: number printing and parseInt as the spec has them
for x, s in [(0.1 + 0.2, "0.30000000000000004"), (1e21, "1e+21"), (1e20, "100000000000000000000"), (1e-7, "1e-7"),
             (0.000001, "0.000001"), (123e-20, "1.23e-18"), (-0.0, "0"), (2 ** 53 + 2.0, "9007199254740994")]:
    check(f"num_str {s}", J.num_str(x) == s, J.num_str(x))
for args, v in [(("08",), 8.0), (("0x1f",), 31.0), (("1e3",), 1.0), ((5e-7,), 5.0), ((J.NULL, 36.0), 1112745.0), (("-0",), -0.0)]:
    got = J.parse_int(*args)
    check(f"parseInt{args}", got == v and str(got) == str(v), got)

# ---- real Node, when installed: a sample of generated programs, and the made-up APIs really missing ------------------------
nodes = [p for p in [os.path.expanduser(f"~/.nvm/versions/node/{v}/bin/node") for v in ("v22.22.3", "v24.14.1", "v25.9.0")]
         + [shutil.which("node") or ""] if p and os.path.exists(p)]
if nodes:
    d = tempfile.mkdtemp()
    probe = os.path.join(d, "probe.js")   # each made-up call must throw "TypeError: ... is not a function" by itself
    srcs = [f"console.log({c});" for c in J.FAKE_SYNC] + [x.format(a="a1") for x in J.FAKE_ASYNC]
    open(probe, "w").write("const r = [];\nfor (const src of " + repr(srcs).replace("\\'", "'") + ") {\n"
                           "  try { eval(src); r.push('ran: ' + src); } catch (e) {\n"
                           "    if (!(e instanceof TypeError && / is not a function/.test(e.message))) r.push(src + ': ' + e); } }\n"
                           "console.log(JSON.stringify(r));\n")
    for node in nodes:
        out = subprocess.run([node, probe], capture_output=True, text=True)
        ver = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
        check(f"made-up APIs missing in node {ver}", out.stdout.strip() == "[]", out.stdout[-500:] + out.stderr[-500:])
    node = nodes[0]
    sample = [q for lv in range(1, 11) for q in J.gen(1, lv).meta["questions"]]
    for i, q in enumerate(sample):
        f = os.path.join(d, f"{i}.js")
        open(f, "w").write(q["text"] + "\n")
        p = subprocess.run([node, f], capture_output=True, text=True, timeout=60)
        if q["fake"] or q["mode"] == "throws":
            ok = p.returncode != 0 and p.stdout == "" and "TypeError" in p.stderr
        elif q["mode"] == "seq":
            ok = p.stdout.split() == q["accept"]["seq"]
        else:
            ok = p.stdout == q["accept"]["line"] + "\n"
        check(f"node runs L{q['level']} {q['text'][:60]!r}", ok, f"ours {J.oracle_answer(q)!r}, node {p.stdout[:300]!r} {p.stderr[-200:]}")
    print(f"{len(sample)} questions run on node {subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip()}; "
          f"made-up APIs checked on {len(nodes)} Node versions")
else:
    print("no Node found: the real-Node part skipped")

print("all passed" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
