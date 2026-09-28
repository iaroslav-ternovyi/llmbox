"""Levels 7-8 of the code and reasoning blocks (v0.11): deterministic items graded per answer / per hidden test, full
credit for the reference and none for nothing, and the exact solvers behind them agree with brute force on small cases.
Levels 1-6 staying as they were is tests/test_levels_stable.py. Run: python3 tests/test_levels_78.py"""
import copy
import itertools
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox.suite import code as C, reasoning as R  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


check("MAX_LEVEL", R.MAX_LEVEL == 8 and C.MAX_LEVEL == 8)

# reasoning: several numbered results per item, credit per result
for kind, gen in R.KINDS.items():
    for lv in (7, 8):
        it, again = gen(5, lv), gen(5, lv)
        check(f"{kind} L{lv} deterministic", it.messages == again.messages and it.meta == again.meta)
        exp = it.meta["expected"]
        lines = [f"ANSWER {i + 1}: {e}" for i, e in enumerate(exp)]
        check(f"{kind} L{lv} several results", len(exp) >= 3, len(exp))
        check(f"{kind} L{lv} oracle", abs(it.check("\n".join(reversed(lines))) - 1) < 1e-9)
        check(f"{kind} L{lv} one right", abs(it.check(lines[0]) - 1 / len(exp)) < 1e-9)
        check(f"{kind} L{lv} empty", it.check("") == 0.0)

# code: the reference (Python, and its JavaScript port) passes every hidden test; a stub passes none
for kind in ("expr", "lru", "csv", "rooms", "cron"):
    for lv in (7, 8):
        it = C.KINDS[kind](5, lv)
        tests, fn = it.check.__defaults__[-1], it.meta["fn"]
        for lang in ("python",) if kind == "cron" else ("python", "javascript"):
            it2 = copy.copy(it)
            it2.meta = dict(it.meta, lang=lang)
            got = C._run(lang, fn, C._extract_code(C.oracle(it2), lang), tests, timeout=120)
            check(f"{kind} L{lv} reference {lang}", got == 1.0, got)
        stub = (f"```python\ndef {fn}(*a):\n    return None\n```" if it.lang == "python" else
                f"```javascript\nfunction {fn}() {{ return null; }}\nmodule.exports = {{ {fn} }};\n```")
        check(f"{kind} L{lv} stub", it.check(stub) == 0.0)
        check(f"{kind} L{lv} older levels have no oracle", C.oracle(C.KINDS[kind](5, 6)) is None)

# the exact makespan solver (schedule) against an exhaustive day-by-day search
rnd = random.Random(3)


def brute_makespan(names, dur, pre, release, groups, cap):
    n, ix = len(names), {t: i for i, t in enumerate(names)}
    d = [dur[t] for t in names]
    preds = [[ix[p] for p in pre[t]] for t in names]
    grp = [{g for g, m in enumerate(groups) if t in m} for t in names]
    layer, t = {(0, ())}, 0
    while True:
        nxt = set()
        for done, running in layer:
            started = done | sum(1 << i for i, _ in running)
            elig = [i for i in range(n) if not started >> i & 1 and all(done >> p & 1 for p in preds[i])
                    and release.get(names[i], 0) <= t]
            used = set().union(*[grp[i] for i, _ in running]) if running else set()
            for k in range(min(cap - len(running), len(elig)) + 1):
                for sub in itertools.combinations(elig, k):
                    gs = [g for i in sub for g in grp[i]]
                    if len(gs) != len(set(gs)) or set(gs) & used:
                        continue
                    run = [(i, r - 1) for i, r in running] + [(i, d[i] - 1) for i in sub]
                    nd = done | sum(1 << i for i, r in run if r == 0)
                    if nd == (1 << n) - 1:
                        return t + 1
                    nxt.add((nd, tuple(sorted((i, r) for i, r in run if r > 0))))
        layer, t = nxt, t + 1


for trial in range(40):
    n = rnd.randint(4, 7)
    names = [f"t{i}" for i in range(n)]
    dur = {t: rnd.randint(1, 5) for t in names}
    pre = {t: sorted(rnd.sample(names[:i], min(i, rnd.choice([0, 0, 1, 2])))) for i, t in enumerate(names)}
    groups = [rnd.sample(names, 3)] + ([rnd.sample(names, 2)] if rnd.random() < 0.5 else [])
    release = {t: rnd.randint(0, 5) for t in rnd.sample(names, rnd.randint(0, 2))}
    cap = rnd.choice([1, 2, 3, n])
    a, b = R._rcpsp_min(names, dur, pre, release, groups, cap), brute_makespan(names, dur, pre, release, groups, cap)
    check(f"makespan {trial}", a == b, (a, b, dur, pre, groups, release, cap))

# the houses-puzzle counter (logic) against brute force: 4 houses, 3 categories, every clue type
N, CATS = 4, 3
ents = [(c, v) for c in range(CATS) for v in range(N)]
perms = list(itertools.permutations(range(N)))


def holds(cl, pos):
    P = lambda e: pos[e[0]][e[1]]
    t = cl[0]
    if t == "pos":
        return P(cl[1]) == cl[2]
    if t == "notpos":
        return P(cl[1]) != cl[2]
    if t == "end":
        return P(cl[1]) in (0, N - 1)
    if t in R._REL:
        return R._REL[t](P(cl[1]), P(cl[2]))
    if t == "xor":
        return (P(cl[1][1]) == P(cl[1][2])) != (P(cl[2][1]) == P(cl[2][2]))
    return min(P(cl[2]), P(cl[3])) < P(cl[1]) < max(P(cl[2]), P(cl[3]))


def rand_clue():
    t = rnd.choice(["pos", "notpos", "end", "same", "diff", "next", "left", "before", "gap", "xor", "xor", "between", "between"])
    if t in ("pos", "notpos"):
        return (t, rnd.choice(ents), rnd.randrange(N))
    if t == "end":
        return (t, rnd.choice(ents))
    if t == "xor":
        a, b, c, d = rnd.sample(ents, 4)
        return ("xor", ("same", a, b), ("same", c, d))
    if t == "between":
        return ("between",) + tuple(rnd.sample(ents, 3))
    return (t,) + tuple(rnd.sample(ents, 2))


for trial in range(150):
    clues = [rand_clue() for _ in range(rnd.randint(4, 14))]
    cnt = 0
    for pos in itertools.product(perms, repeat=CATS):
        cnt += all(holds(cl, pos) for cl in clues)
        if cnt > 2:
            break
    check(f"puzzle count {trial}", min(cnt, 2) == min(R._zebra_count(N, CATS, clues), 2), clues)

print("all passed" if not failed else f"{failed} failed")
sys.exit(1 if failed else 0)
