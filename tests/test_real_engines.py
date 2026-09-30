"""techhelp.sql and knowledge.regex: task kinds whose answer key is the real engine, run when the item is generated
(SQLite through Python's sqlite3, CPython's `re`). Checked here:

  items     levels 1-10 x N seeds: deterministic, the oracle scores 1, an empty or wrong answer ~0, the answer written in
            other common shapes (a preamble, padding, code fences, a markdown table, bold headers) still 1
  order     every SQL query gives the same rows with PRAGMA reverse_unordered_selects on and off (its ORDER BY is total)
  versions  the same scripts, queries and snippets on the Linux box (ssh <the box> 'python3 -': SQLite 3.46, CPython
            3.14) and on every other CPython 3.12+ found here must give the same answers as the generator's (the Mac:
            SQLite 3.53, CPython 3.12); without the box that part is skipped and says so
  grading   the cell and answer rules on hand-made cases

Run: python3 tests/test_real_engines.py [--seeds N] (default 50)
"""
import ast
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import validate as V  # noqa: E402
from llmbox.suite import knowledge as K, knowledge_regex as R, techhelp_sql as S  # noqa: E402

SEEDS = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 50
from llmbox.hosts import box_ssh  # noqa: E402
HOST = box_ssh()
failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


# ---- items -----------------------------------------------------------------------------------------------------------

sql_items, rx_items = [], []
for level in range(1, 11):
    for seed in range(1, SEEDS + 1):
        for gen, oracle, variants, bucket in ((S.sql, S.oracle, S.variants, sql_items), (R.regex, K.oracle, R.variants, rx_items)):
            a, b = gen(seed, level), gen(seed, level)
            bucket.append(a)
            tag = f"{a.kind} L{level} s{seed}"
            check(f"{tag} deterministic", a.messages == b.messages and a.meta["expected"] == b.meta["expected"])
            o = oracle(a)
            check(f"{tag} oracle", a.check(o) == 1.0, o[:300])
            check(f"{tag} empty", a.check("") == 0.0)
            for v in variants(o):
                check(f"{tag} format", a.check(v) == 1.0, v[:300])
            check(f"{tag} validate.oracle", V.oracle(a) == o)
        it = sql_items[-1]
        wrong = "RESULTS\n" + "\n".join(f"RESULT {i + 1}\nzz | 12345" for i in range(len(it.meta["expected"]))) + "\nEND"
        check(f"sql L{level} s{seed} wrong", it.check(wrong) == 0.0)
        one = S.oracle(it).split("\nRESULT 2\n")[0] + "\nEND"
        check(f"sql L{level} s{seed} one query", abs(it.check(one) - 1 / len(it.meta["expected"])) < 1e-9, it.check(one))
        it = rx_items[-1]
        qs = it.meta["questions"]
        nf = sum(q["fake"] for q in qs)
        check(f"regex L{level} s{seed} 2 made up", nf == 2 and len(qs) == {7: 10, 8: 10, 9: 10, 10: 12}.get(level, 8))
        check(f"regex L{level} s{seed} UNKNOWN", abs(it.check("ANSWERS\n" + "\n".join(f"{i}. UNKNOWN" for i in range(1, len(qs) + 1)))
                                                       - K.IDK) < 1e-9)
        check(f"regex L{level} s{seed} bluff", it.check("ANSWERS\n" + "\n".join(f"{i}. 'zz'" for i in range(1, len(qs) + 1))) == 0.0)
        half = "ANSWERS\n" + "\n".join(f"{i}. {'12345' if q['fake'] else e}" for i, (q, e) in enumerate(zip(qs, it.meta["expected"]), 1))
        check(f"regex L{level} s{seed} invented", abs(it.check(half) - (len(qs) - nf) / len(qs)) < 1e-9)
        bd = R.breakdown(it, half)
        check(f"regex L{level} s{seed} breakdown", bd["right"] == len(qs) - nf and bd["invented"] == nf, bd)
for lv in (1, 5, 10):
    for b, k in (("techhelp", "sql"), ("knowledge", "regex")):
        res = V.check_kind(b, k, lv, seeds=(1, 2, 3))
        check(f"validate {b}.{k} L{lv}", res["ok"] and len(res["format"]) >= 12, res)
print(f"{len(sql_items)} sql and {len(rx_items)} regex items generated")


def sql_script(it) -> str:
    """CREATE + INSERT and, at levels 9-10, the writes after them: exactly the SQL blocks the prompt shows before the queries."""
    blocks = re.findall(r"```sql\n(.*?)\n```", it.messages[0]["content"].split("\nQuery 1:\n")[0], re.S)
    check(f"{it.id} setup shown", "\n".join(blocks) == it.meta["setup"])
    return it.meta["setup"]


# ---- order: every ORDER BY is total ------------------------------------------------------------------------------------

n = 0
for it in sql_items:
    for q, exp in zip(it.meta["queries"], it.meta["expected"]):
        con = sqlite3.connect(":memory:")
        con.executescript("PRAGMA reverse_unordered_selects = ON;" + sql_script(it))
        res = S.run_query(con, q)
        n += 1
        check(f"{it.id} order", res != "ERROR" and [" | ".join(S.cell(v) for v in row) for row in res[1]] == exp, q)
print(f"{n} queries re-run with reverse_unordered_selects")

# ---- versions: other SQLite and CPython builds give the same answers -------------------------------------------------

SQL_RUNNER = r'''
import json, sqlite3, sys
out = []
for script, queries in json.loads(PAYLOAD):
    con = sqlite3.connect(":memory:")
    con.executescript(script)
    res = []
    for q in queries:
        try:
            res.append([[repr(v) for v in row] for row in con.execute(q).fetchall()])
        except sqlite3.Error as e:
            res.append("ERROR " + str(e))
    out.append(res)
print(json.dumps({"version": sqlite3.sqlite_version, "python": sys.version.split()[0], "results": out}))
'''
RX_RUNNER = r'''
import json, re, sys, warnings
out = []
for expr in json.loads(PAYLOAD):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            v = eval(expr, {"re": re})
        out.append({"repr": repr(v)})
    except re.error:
        out.append({"exc": "error"})
    except Exception as e:
        out.append({"exc": type(e).__name__})
print(json.dumps({"python": sys.version.split()[0], "results": out}))
'''


def run_py(cmd: list, runner: str, payload) -> dict | None:
    prog = runner.replace("PAYLOAD", repr(json.dumps(payload)))
    try:
        p = subprocess.run(cmd, input=prog, capture_output=True, text=True, timeout=600)
        return json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 else None
    except Exception:
        return None


def sql_versions(cmd: list, where: str) -> None:
    payload = [[sql_script(it), it.meta["queries"]] for it in sql_items]
    got = run_py(cmd, SQL_RUNNER, payload)
    if got is None:
        print(f"sql versions: {where} not reachable - skipped")
        return
    diff = 0
    for it, res in zip(sql_items, got["results"]):
        for i, (q, exp, rows) in enumerate(zip(it.meta["queries"], it.meta["expected"], res)):
            mine = "ERROR" if isinstance(rows, str) else [" | ".join(S.cell(ast.literal_eval(v)) for v in row) for row in rows]
            if mine != exp:
                diff += 1
                check(f"{it.id} query {i + 1} on {where}", False, f"{q}\nours {exp}\nthere {mine}")
    nq = sum(len(it.meta["queries"]) for it in sql_items)
    print(f"sql versions: {len(sql_items)} items / {nq} queries on {where} (SQLite {got['version']}, Python {got['python']}) "
          f"vs SQLite {sqlite3.sqlite_version} here: {diff} differ")


def rx_versions(cmd: list, where: str) -> None:
    exprs, accept = {}, {}
    for it in rx_items:
        for q in it.meta["questions"]:
            for e in [q["text"]] + q.get("parts", []):
                exprs[e] = R.evaluate(e)
    texts = sorted(exprs)
    got = run_py(cmd, RX_RUNNER, texts)
    if got is None:
        print(f"regex versions: {where} not reachable - skipped")
        return
    diff = 0
    for t, res in zip(texts, got["results"]):
        mine = exprs[t]
        if "exc" in mine:
            same = res.get("exc") in mine["exc"]
        else:
            try:
                same = "repr" in res and repr(K._canon(ast.literal_eval(res["repr"]))) == repr(K._canon(mine["value"]))
            except (ValueError, SyntaxError):
                same = res.get("repr") == repr(mine["value"])
        if not same:
            diff += 1
            check(f"regex {t} on {where}", False, f"ours {mine} there {res}")
    print(f"regex versions: {len(rx_items)} items / {len(texts)} distinct snippets on {where} (CPython {got['python']}) "
          f"vs CPython {sys.version.split()[0]} here: {diff} differ")


box = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", HOST, "python3 -"]
sql_versions(box, HOST)
rx_versions(box, HOST)
here = os.path.realpath(sys.executable)
for py in ("python3.12", "python3.13", "python3.14"):
    exe = shutil.which(py)
    if exe and os.path.realpath(exe) != here:
        rx_versions([exe, "-"], exe)
        sql_versions([exe, "-"], exe)

# ---- grading rules ------------------------------------------------------------------------------------------------------

ok = S.cell_ok
for v, good, bad in [(3, ["3", "+3"], ["3.0", "3.", "'3.0'", "three", "NULL"]), (3.0, ["3.0", "3.", "3.00", "3e0"], ["3", "NULL"]),
                     (2.3333333333333335, ["2.333333", "2.3333333333333335", "2.33333333"], ["2.33", "2.3333", "2"]),
                     (9.3e18, ["9.3e+18", "9.3e18", "9300000000000000000.0"], ["9300000000000000000"]),
                     (9200000000000000000, ["9200000000000000000"], ["9.2e+18", "9.2e18"]),
                     (None, ["NULL", "null"], ["", "None", "0"]), ("03", ["03"], ["3", "3.0"]), ("Books", ["Books"], ["books"]),
                     ("", [""], ["NULL"])]:
    for g in good:
        check(f"cell {v!r} <- {g!r}", ok(v, g))
    for g in bad:
        check(f"cell {v!r} <- {g!r} wrong", not ok(v, g))
exp, cols = [[(1, "a"), (2, "b"), (3, None)], [], [(1.5,)]], [["id", "name"], ["x"], ["avg"]]
chk = S.make_check(exp, cols)
cases = [("RESULTS\nRESULT 1\n1 | a\n2 | b\n3 | NULL\nRESULT 2\n(no rows)\nRESULT 3\n1.5\nEND", 1.0),
         ("RESULT 1\n| id | name |\n|---|---|\n| 1 | a |\n| 2 | b |\n| 3 | NULL |\nRESULT 2: (no rows)\nRESULT 3: 1.5", 1.0),
         ("RESULTS\nRESULT 1\n1 | a\n3 | NULL\nRESULT 2\n(no rows)\nRESULT 3\n1.5\nEND", (2 / 3 + 1 + 1) / 3),        # a row missing
         ("RESULTS\nRESULT 1\n1 | a\n2 | b\n2 | b\n3 | NULL\nRESULT 2\n(no rows)\nRESULT 3\n1.5\nEND", (3 / 4 + 1 + 1) / 3),   # one extra
         ("RESULTS\nRESULT 1\n1 | a\n2 | b\n3 |\nRESULT 2\n0\nRESULT 3\n2\nEND", (2 / 3) / 3),
         ("RESULT 1\n1 | a\n2 | b\n3 | NULL\n\nThe second query returns nothing because NOT IN meets a NULL.\n\nRESULT 2\n(no rows)\n"
          "RESULT 3\n1.5\n\nNote: AVG is REAL.", 1.0),
         ("draft:\nRESULT 1\n9 | z\nfinal:\nRESULTS\nRESULT 1\n1 | a\n2 | b\n3 | NULL\nRESULT 2\n(no rows)\nRESULT 3\nERROR\nEND", 2 / 3),
         ("RESULTS\n**RESULT 1**\n`1` | `a`\n2 | 'b'\n3 | NULL\n**RESULT 2**\n(empty)\n**RESULT 3**\n1.50\nEND", 1.0)]
for text, want in cases:
    check(f"sql grading {text[:60]!r}", abs(chk(text) - want) < 1e-9, chk(text))

rq = {"kind": "regex", "fake": False, "accept": R._accept({"value": ["a", ("b", "")]})}
for a, want in [("['a', ('b', '')]", "right"), ('["a", ("b", "")]', "right"), ("`['a', ('b', '')]`", "right"),
                ("['a', ['b', '']]", "wrong"), ("['a', ('b', None)]", "wrong"), ("['a',('b','')] — the group did not match", "right"),
                ("UNKNOWN", "idk"), ("NONEXISTENT", "wrong"), ("raises TypeError", "wrong")]:
    check(f"regex answer {a!r}", R.verdict(rq, a) == want, R.verdict(rq, a))
sq = {"kind": "regex", "fake": False, "accept": R._accept({"value": " a b "})}
for a, want in [("' a b '", "right"), ("' a  b '", "wrong"), ("a b", "wrong"), ("'a b'", "wrong")]:
    check(f"regex spaces {a!r}", R.verdict(sq, a) == want, R.verdict(sq, a))
for v, a, want in [("a-b", "a-b", "right"), ("42", "42", "wrong"), ("42", "'42'", "right"), ("None", "None", "wrong")]:
    check(f"regex plain {v!r} <- {a!r}", R.verdict({"fake": False, "accept": R._accept({"value": v})}, a) == want)
eq = {"kind": "regex", "fake": False, "accept": R._accept(R.evaluate("re.compile(r'a(?i)b')"))}
for a, want in [("raises re.error", "right"), ("raises re.PatternError", "right"), ("re.error: global flags not at the start", "right"),
                ("raises error", "right"), ("raises ValueError", "wrong"), ("NONEXISTENT", "wrong")]:
    check(f"regex error {a!r}", R.verdict(eq, a) == want, R.verdict(eq, a))
fq = R._question("re.findfirst(r'a', 'a')", 3, fake=True)
for a, want in [("NONEXISTENT", "right"), ("raises AttributeError", "right"), ("['a']", "wrong"), ("UNKNOWN", "idk")]:
    check(f"regex fake {a!r}", R.verdict(fq, a) == want, R.verdict(fq, a))
for f in R.FAKES[1] + R.FAKES[5] + R.FAKES[9]:
    check(f"made up really fails: {f}", R._question(f, 5, fake=True) is not None, R.evaluate(f))

print("all passed" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
