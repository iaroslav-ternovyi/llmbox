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

# answer block parsing: bold numbers, a header, a later correction wins, the reasoning above is ignored
txt = "1. maybe 3\nlet me think\n**ANSWERS**\n**1.** 2\n2) `x`\n- 3: y\n"
got = K.answers(txt, 3)
check("parse", got == {1: "2", 2: "`x`", 3: "y"}, got)

print("all passed" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
