"""Knowledge block: deterministic items, the oracle scores 1, UNKNOWN everywhere scores the idk credit, invented answers
score 0, and the grader accepts the usual ways models write a right answer. Run: python3 tests/test_knowledge.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox.suite import knowledge as K  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


def q_of(src, text_part, fake=False):
    return next(q for q in K.bank()["questions"] if q["src"] == src and text_part in q["text"] and q["fake"] == fake)


# the bank itself: every level has enough questions for an item, and every question has an oracle answer
for kind in K.KINDS:
    for level in range(1, 6):
        check(f"{kind} L{level} real pool", len(K._pool(kind, level, False)) >= K.PER_ITEM)
        check(f"{kind} L{level} fake pool", len(K._pool(kind, level, True)) >= 3)

n = 0
for kind, gen in K.KINDS.items():
    for level in range(1, 6):
        for seed in range(1, 31):
            a, b = gen(seed, level), gen(seed, level)
            n += 1
            nf = sum(q["fake"] for q in a.meta["questions"])
            check(f"{kind} L{level} s{seed} deterministic", a.messages == b.messages)
            check(f"{kind} L{level} s{seed} 8 questions, 1-3 fake", len(a.meta["questions"]) == 8 and 1 <= nf <= 3)
            check(f"{kind} L{level} s{seed} oracle", a.check(K.oracle(a)) == 1.0, K.oracle(a))
            idk = sum(q.get("idk", K.IDK) for q in a.meta["questions"]) / 8
            check(f"{kind} L{level} s{seed} all UNKNOWN", abs(a.check("ANSWERS\n" + "\n".join(f"{i}. UNKNOWN" for i in range(1, 9))) - idk) < 1e-9)
            check(f"{kind} L{level} s{seed} empty", a.check("") == 0.0)
            bluff = "ANSWERS\n" + "\n".join(f"{i}. 12345zz" for i in range(1, 9))
            check(f"{kind} L{level} s{seed} bluff", a.check(bluff) == 0.0)
            # everything confidently claimed to exist: the made-up questions score 0, the real ones are right
            half = "ANSWERS\n" + "\n".join(f"{i}. {'12345zz' if q['fake'] else e}" for i, (q, e) in enumerate(zip(a.meta["questions"], a.meta["expected"]), 1))
            check(f"{kind} L{level} s{seed} invented", abs(a.check(half) - (8 - nf) / 8) < 1e-9)
            bd = K.breakdown(a, half)
            check(f"{kind} L{level} s{seed} breakdown", bd["right"] == 8 - nf and bd["invented"] == nf, bd)
print(f"{n} items generated")

# grading: the ways models write right answers
V = K.verdict
cases = [
    (q_of("python", 'os.path.splitext("archive.tar.gz")'), ['("archive.tar", ".gz")', "`('archive.tar', '.gz')`", "('archive.tar', '.gz') — splits at the last dot"], ["('archive', '.tar.gz')"]),
    (q_of("python", 'round(2.5)'), ["2", "**2**"], ["3", "2.0"]),
    (q_of("python", 'int("3.5")'), ["raises ValueError", "ValueError: invalid literal for int()", "Raises `ValueError`"], ["3", "raises TypeError"]),
    (q_of("python", 'json.loads("\'a\'")'), ["raises json.JSONDecodeError", "raises ValueError"], ["'a'"]),
    (q_of("python", '"abc".removeprefix("a")'), ["'bc'", '"bc"', "bc"], ["NONEXISTENT", "raises AttributeError"]),
    (q_of("python", '{"a": 1} | {"a": 2, "b": 3}'), ["{'a': 2, 'b': 3}", '{"b": 3, "a": 2}'], ["{'a': 1, 'b': 3}"]),
    (q_of("python", 'json.dumps({"a": None})'), ['\'{"a": null}\'', '{"a": null}'], ["'{\"a\": None}'"]),
    (q_of("python", '"abc".contains("b")', fake=True), ["NONEXISTENT", "raises AttributeError", "NONEXISTENT (use 'b' in 'abc')"], ["True"]),
    (q_of("python", "datetime.timedelta(months=1)", fake=True), ["NONEXISTENT", "raises TypeError"], ["datetime.timedelta(days=30)"]),
    (q_of("shell", "printf '10\\n9\\n' | sort | head -1"), ["10", "`10`"], ["9"]),
    (q_of("shell", "printf 'a\\na\\nb\\n' | uniq -c | head -1"), ["      2 a", "2 a"], ["2"]),
    (q_of("shell", "sort --natural", fake=True), ["NONEXISTENT", "nothing - sort: unrecognized option '--natural'", "error: unrecognized option"], ["a"]),
    (q_of("jq", "jq -r '.b'"), ["null"], ["", "error"]),
    (q_of("http", "HTTP status 404?"), ["Not Found", "404 Not Found", "not found"], ["Missing"]),
    (q_of("http", "HTTP status 413?"), ["Payload Too Large", "Content Too Large", "Request Entity Too Large"], ["Too Large"]),
    (q_of("http", "HTTP status 437?", fake=True), ["NONEXISTENT", "NONEXISTENT - not assigned"], ["Too Many Connections"]),
    (q_of("signal", "status 137"), ["SIGKILL", "SIGKILL (9)", "KILL", "9"], ["SIGTERM", "SIGKILL or SIGTERM"]),
    (q_of("errno", "Resource temporarily unavailable"), ["EAGAIN", "EWOULDBLOCK", "EAGAIN (EWOULDBLOCK)"], ["EBUSY"]),
    (q_of("errno", "ECONNREFUSED on Linux"), ["111"], ["61", "110 or 111"]),
    (q_of("port", "PostgreSQL"), ["5432", "5432/tcp", "TCP 5432"], ["5433"]),
    (q_of("port", "Brindleport", fake=True), ["NONEXISTENT", "UNKNOWN"], ["6380"]),
]
for q, rights, wrongs in cases:
    for a in rights:
        check(f"right {q['text'][:40]!r} <- {a!r}", V(q, a) == "right" or (q["fake"] and a == "UNKNOWN" and K.credit(q, a) == 1.0), V(q, a))
    for a in wrongs:
        check(f"wrong {q['text'][:40]!r} <- {a!r}", V(q, a) == "wrong", V(q, a))
check("idk credit", K.credit(cases[0][0], "UNKNOWN") == K.IDK)

# ---- levels 7-8: combined questions, every answer derived from bank entries --------------------------------------------
import ast
import re
import shutil

bq = {(q["kind"], q["text"]): q for q in K.bank()["questions"]}


def codes_int(phrase):
    """The number a codes phrase asks for, looked up in the bank independently of knowledge._codes_facts."""
    m = re.fullmatch(r"the default (TCP|UDP) port of (.+)", phrase)
    if m:
        return bq[("codes", f"What is the default {m.group(1)} port of {m.group(2)}?")]["accept"]["int"]
    m = re.fullmatch(r"the errno number of (E\w+) on Linux", phrase)
    if m:
        return bq[("codes", phrase.replace("the errno", "What is the errno") + "?")]["accept"]["int"]
    m = re.fullmatch(r"the number of signal (SIG\w+) on x86-64 Linux", phrase)
    if m:
        return bq[("codes", f"What is the number of signal {m.group(1)} on x86-64 Linux?")]["accept"]["int"]
    m = re.fullmatch(r"the exit status bash reports for a process killed by (SIG\w+)", phrase)
    if m:
        return 128 + bq[("codes", f"What is the number of signal {m.group(1)} on x86-64 Linux?")]["accept"]["int"]
    m = re.fullmatch(r'the HTTP status code with the reason phrase "(.+)"', phrase)
    if m:
        return bq[("codes", f'Which HTTP status code has the reason phrase "{m.group(1)}"?')]["accept"]["int"]
    m = re.fullmatch(r"the exit status (.+?) (returns|reports|has)(.*)", phrase)   # levels 9-10: measured exit statuses
    if m:
        verb = {"returns": "return", "reports": "report", "has": "have"}[m.group(2)]
        return bq[("codes", f"What exit status does {m.group(1)} {verb}{m.group(3)}?")]["accept"]["int"]
    m = re.fullmatch(r'the errno number that goes with the error message "(.+)" on Linux', phrase)
    names = bq[("codes", f'Which errno name goes with the error message "{m.group(1)}" on Linux?')]["accept"]["names"]
    return next(bq[("codes", f"What is the errno number of {n} on Linux?")]["accept"]["int"] for n in names
                if ("codes", f"What is the errno number of {n} on Linux?") in bq)


combos = {"python": {}, "shell": {}}
n = 0
SHAPE = {7: [1] * 4 + [2] * 4, 8: [1] * 2 + [2] * 3 + [3] * 3, 9: [1, 2, 3, 3, 3, 3, 4, 4], 10: [3] * 4 + [4] * 6}
for kind, gen in K.KINDS.items():
    for level in (7, 8, 9, 10):
        for seed in range(1, 61):
            a, b = gen(seed, level), gen(seed, level)
            n += 1
            qs = a.meta["questions"]
            nq, nf = len(qs), sum(q["fake"] for q in qs)
            check(f"{kind} L{level} s{seed} deterministic", a.messages == b.messages)
            check(f"{kind} L{level} s{seed} {12 if level == 10 else 10} questions, 2 fake", nq == (12 if level == 10 else 10) and nf == 2)
            shape = [4] * 4 + [5] * 6 if (kind, level) == ("codes", 10) else SHAPE[level]
            check(f"{kind} L{level} s{seed} shape", sorted(len(q.get("parts", [1])) for q in qs if not q["fake"]) == shape)
            check(f"{kind} L{level} s{seed} oracle", a.check(K.oracle(a)) == 1.0, K.oracle(a))
            idk = sum(q.get("idk", K.IDK) for q in qs) / nq
            check(f"{kind} L{level} s{seed} all UNKNOWN", abs(a.check("ANSWERS\n" + "\n".join(f"{i}. UNKNOWN" for i in range(1, nq + 1))) - idk) < 1e-9)
            check(f"{kind} L{level} s{seed} empty", a.check("") == 0.0)
            check(f"{kind} L{level} s{seed} bluff", a.check("ANSWERS\n" + "\n".join(f"{i}. 12345zz" for i in range(1, nq + 1))) == 0.0)
            half = "ANSWERS\n" + "\n".join(f"{i}. {'12345zz' if q['fake'] else e}" for i, (q, e) in enumerate(zip(qs, a.meta["expected"]), 1))
            check(f"{kind} L{level} s{seed} invented", abs(a.check(half) - (nq - nf) / nq) < 1e-9)
            bd = K.breakdown(a, half)
            check(f"{kind} L{level} s{seed} breakdown", bd["right"] == nq - nf and bd["invented"] == nf, bd)
            # every part is a bank fact (or a codes fact derived from them), used once per item, and the answer follows
            seen = []
            for q in qs:
                if q.get("src") != "combo":
                    seen.append(q["text"])
                    continue
                seen += q["parts"]
                if kind == "codes":
                    want = tuple(codes_int(p) for p in q["parts"])
                    check(f"codes combo {q['text'][:50]}", q["accept"]["repr"] == ", ".join(map(str, want)), q["accept"])
                    continue
                ps = [bq[(kind, p)] for p in q["parts"]]
                if q["fake"]:   # levels 9-10, python: a made-up call last, after parts that do not raise
                    check(f"hidden fake {q['text'][:50]}", level >= 9 and kind == "python" and ps[-1]["fake"]
                          and not any(p["fake"] or p["accept"].get("exc") for p in ps[:-1]) and K.oracle_answer(q) == "NONEXISTENT"
                          and q["accept"]["exc"] == ps[-1]["accept"]["exc"])
                    combos[kind][q["text"]] = q
                    continue
                check(f"{kind} combo parts real", all(not p["fake"] and p["level"] >= 5 for p in ps))
                if kind == "python" and level >= 9:   # one level-7 fact per combination, never behind a part that raises
                    at = [i for i, p in enumerate(ps) if p["level"] == 7]
                    check(f"python L{level} one level-7 part {q['text'][:50]}", len(at) == 1 and not any(
                        p["accept"].get("exc") for p in ps[:at[0]]), [p["level"] for p in ps])
                if kind == "python":
                    exc = next((p["accept"]["exc"] for p in ps if p["accept"].get("exc")), None)
                    want = f"raises {exc[0]}" if exc else "(" + ", ".join(p["accept"]["repr"] for p in ps) + ")"
                    check(f"python combo {q['text'][:50]}", K.oracle_answer(q) == want, (K.oracle_answer(q), want))
                    check(f"python combo parses {q['text'][:50]}", isinstance(ast.parse(q["text"], mode="eval").body, ast.Tuple))
                else:
                    want = " ".join(p["accept"]["texts"][0] for p in ps)
                    check(f"shell combo {q['text'][:50]}", q["accept"]["texts"] == [K._ws(want)], q["accept"])
                    check(f"shell combo form {q['text'][:50]}", q["text"] == 'echo "' + " ".join(f"$({p})" for p in q["parts"]) + '"')
                combos[kind][q["text"]] = q
            check(f"{kind} L{level} s{seed} no fact twice", len(seen) == len(set(seen)), seen)
            if kind == "codes":
                keys = [re.sub(r"^the (?:number of signal|exit status bash reports for a process killed by) ", "sig ", p) for q in qs
                        for p in q.get("parts", [])]
                keys = [k.split(" on ")[0] for k in keys]
                check(f"codes L{level} s{seed} no signal twice", len(keys) == len(set(keys)), keys)
                status = [int(m) for q in qs if not q.get("parts") for m in re.findall(r"exited with status (\d+)", q["text"])]
                killed = [codes_int(p) for q in qs for p in q.get("parts", []) if "killed by" in p]
                check(f"codes L{level} s{seed} status not given away", not set(status) & set(killed), (status, killed))
print(f"{n} level 7-10 items generated")

# python combos on the real interpreters, as the bank was built (every 3.12+ CPython found here, two hash seeds)
texts = sorted(combos["python"])
ran = []
for py in ("python3.12", "python3.13", "python3.14"):
    if not shutil.which(py):
        continue
    for hs in ("0", "1"):
        os.environ["PYTHONHASHSEED"] = hs
        res = K._remote(K._PY_RUNNER, [K.PY_MODULES, texts], None, py)
        for t, r_ in zip(texts, res):
            got = ("raises " + r_["exc"][0]) if r_.get("exc") else r_.get("repr")
            check(f"{py} runs {t[:60]}", K.verdict(combos["python"][t], got) == "right", f"ran: {got}, ours: {K.oracle_answer(combos['python'][t])}")
        ran.append(f"{py}/{hs}")
os.environ.pop("PYTHONHASHSEED", None)
print(f"{len(texts)} python combos run on {', '.join(ran) or 'no local CPython 3.12+'}")

# grading of combined answers
pyq = K._combine("python", [q_of("python", "~-5"), q_of("python", '"{:.0f}".format(2.5)')], 7)
for ans, want in [("(4, '2')", "right"), ('(4, "2")', "right"), ("4, '2'", "right"), ("(4, 2)", "wrong"), ("(4, '3')", "wrong"),
                  ("UNKNOWN", "idk"), ("raises ValueError", "wrong")]:
    check(f"python combo answer {ans!r}", V(pyq, ans) == want, V(pyq, ans))
pye = K._combine("python", [q_of("python", "~-5"), q_of("python", 'int("3.5")')], 7)
check("python combo raises", V(pye, "raises ValueError") == "right" and V(pye, "(4, 3)") == "wrong")
cq = K._combine("codes", [{"int": 39, "phrase": "a", "key": "a"}, {"int": 425, "phrase": "b", "key": "b"}], 7)
for ans, want in [("39, 425", "right"), ("(39, 425)", "right"), ("39,425", "right"), ("425, 39", "wrong"), ("39", "wrong"),
                  ("UNKNOWN", "idk")]:
    check(f"codes combo answer {ans!r}", V(cq, ans) == want, V(cq, ans))
sq = K._combine("shell", [q_of("shell", "seq -w 8 10 | head -1"), q_of("jq", "echo '[1,2]' | jq 'add / length'")], 7)
for ans, want in [("08 1.5", "right"), ("`08 1.5`", "right"), ("08  1.5", "right"), ("8 1.5", "wrong")]:
    check(f"shell combo answer {ans!r}", V(sq, ans) == want, V(sq, ans))

# answer block parsing: bold numbers, a header, a later correction wins, the reasoning above is ignored
txt = "1. maybe 3\nlet me think\n**ANSWERS**\n**1.** 2\n2) `x`\n- 3: y\n"
got = K.answers(txt, 3)
check("parse", got == {1: "2", 2: "`x`", 3: "y"}, got)

print("all passed" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
