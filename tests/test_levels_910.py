"""Levels 9-10 of the code and reasoning blocks (v0.11, aimed at the frontier): deterministic items graded per answer /
per hidden test, full credit for the reference and none for nothing, and every exact solver behind them checked against
an independent brute force on small cases. Levels 1-8 staying as they were is tests/test_levels_stable.py.
Run: python3 tests/test_levels_910.py"""
import datetime as dt
import itertools
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox.suite import code as C, code_l9 as C9, reasoning as R, reasoning_l9 as R9  # noqa: E402
from llmbox.suite.common import rng  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {str(detail)[:300]}")


check("MAX_LEVEL", R.MAX_LEVEL == 10 and C.MAX_LEVEL == 10)

# reasoning: several numbered results per item, credit per result
for kind, gen in R.KINDS.items():
    for lv in (9, 10):
        it, again = gen(4, lv), gen(4, lv)
        check(f"{kind} L{lv} deterministic", it.messages == again.messages and it.meta == again.meta)
        exp = it.meta["expected"]
        lines = [f"ANSWER {i + 1}: {e}" for i, e in enumerate(exp)]
        check(f"{kind} L{lv} several results", len(exp) >= 3, len(exp))
        check(f"{kind} L{lv} oracle", abs(it.check("\n".join(reversed(lines))) - 1) < 1e-9)
        check(f"{kind} L{lv} one right", abs(it.check(lines[0]) - 1 / len(exp)) < 1e-9)
        check(f"{kind} L{lv} empty", it.check("") == 0.0)

# code: the reference passes every hidden test, a stub none; levels 7-8 keep their own oracle
for kind in ("expr", "lru", "csv", "rooms", "cron"):
    for lv in (9, 10):
        it, again = C.KINDS[kind](4, lv), C.KINDS[kind](4, lv)
        check(f"{kind} L{lv} deterministic", it.messages == again.messages and it.meta == again.meta)
        cases, fn = C9.cases_of(it), it.meta["fn"]
        got = C9._run9(fn, C._extract_code(C.oracle(it), "python"), cases)
        check(f"{kind} L{lv} reference (scale cases too)", got == 1.0, got)
        check(f"{kind} L{lv} case count", len(cases) == it.meta["tests"], (len(cases), it.meta["tests"]))
        check(f"{kind} L{lv} stub", it.check(f"```python\ndef {fn}(*a):\n    return None\n```") == 0.0)
    check(f"{kind} L8 oracle is the level-8 one", C.oracle(C.KINDS[kind](4, 8)) != C.oracle(C.KINDS[kind](4, 9)))

rnd = random.Random(5)

# budget: branch and bound against enumerating every subset
for trial in range(60):
    n = rnd.randint(6, 11)
    P = {"cost": [rnd.randint(5, 40) for _ in range(n)], "value": [rnd.randint(10, 90) for _ in range(n)],
         "ppl": [rnd.randint(1, 4) for _ in range(n)]}
    P["cap_ppl"] = int(sum(P["ppl"]) * 0.5)
    pairs = list({frozenset(rnd.sample(range(n), 2)) for _ in range(12)})
    pairs = [tuple(sorted(p)) for p in pairs]
    rnd.shuffle(pairs)
    P["reqs"], P["excl"] = pairs[:2], pairs[2:4]
    P["syn"] = [(a, b, rnd.choice([10, 20])) for a, b in pairs[4:5]]
    P["disc"] = [(a, b, min(5, P["cost"][b] - 1)) for a, b in pairs[5:6]]
    P["one_of"] = rnd.sample(range(n), 2)
    P["at_most"] = [(rnd.sample(range(n), 3), 1)]
    P["req_any"] = [(rnd.randrange(n), rnd.sample(range(n), 2))]
    P["req_any"] = [(a, [b for b in bs if b != a] or [(a + 1) % n]) for a, bs in P["req_any"]]
    money = int(sum(P["cost"]) * 0.45)
    forced = (rnd.randrange(n),) if rnd.random() < 0.4 else ()
    best = (-1, 0)
    for mask in range(1 << n):
        s = {i for i in range(n) if mask >> i & 1}
        if sum(P["ppl"][i] for i in s) > P["cap_ppl"] or not s & set(P["one_of"]) or any(f not in s for f in forced):
            continue
        if any(a in s and b not in s for a, b in P["reqs"]) or any(a in s and b in s for a, b in P["excl"]):
            continue
        if any(a in s and not set(bs) & s for a, bs in P["req_any"]) or any(len(s & set(m)) > k for m, k in P["at_most"]):
            continue
        c = sum(P["cost"][i] for i in s) - sum(k for a, b, k in P["disc"] if a in s and b in s)
        v = sum(P["value"][i] for i in s) + sum(k for a, b, k in P["syn"] if a in s and b in s)
        if c <= money and (v, -c) > best:
            best = (v, -c)
    got = R9._portfolio(P, money, forced)
    check(f"portfolio {trial}", (got[0], -got[1]) == best, (got, best))
    if best[0] >= 0:   # counting the combinations at or above a value
        floor_ = best[0] - rnd.choice([0, 10, 25])
        cnt = 0
        for mask in range(1 << n):
            s = {i for i in range(n) if mask >> i & 1}
            if sum(P["ppl"][i] for i in s) > P["cap_ppl"] or not s & set(P["one_of"]) or any(f not in s for f in forced):
                continue
            if any(a in s and b not in s for a, b in P["reqs"]) or any(a in s and b in s for a, b in P["excl"]):
                continue
            if any(a in s and not set(bs) & s for a, bs in P["req_any"]) or any(len(s & set(m)) > k for m, k in P["at_most"]):
                continue
            c = sum(P["cost"][i] for i in s) - sum(k for a, b, k in P["disc"] if a in s and b in s)
            v = sum(P["value"][i] for i in s) + sum(k for a, b, k in P["syn"] if a in s and b in s)
            cnt += c <= money and v >= floor_
        check(f"portfolio count {trial}", R9._portfolio(P, money, forced, at_least=floor_) == cnt)

# fewest transfers: exact partitions against the subset recursion
def partitions(xs):
    if not xs:
        yield []
        return
    head, rest = xs[0], xs[1:]
    for k in range(len(rest) + 1):
        for comb in itertools.combinations(range(len(rest)), k):
            block = [head] + [rest[i] for i in comb]
            left = [rest[i] for i in range(len(rest)) if i not in comb]
            for p in partitions(left):
                yield [block] + p


for trial in range(80):
    k = rnd.randint(2, 7)
    xs = [rnd.randint(-5, 5) * 100 for _ in range(k - 1)]
    xs.append(-sum(xs))
    bal = {f"p{i}": v for i, v in enumerate(xs)}
    nz = [v for v in xs if v]
    groups = max((len(p) for p in partitions(nz) if all(sum(b) == 0 for b in p)), default=0)
    check(f"transfers {trial}", R9._min_transfers(bal) == (len(nz) - groups if nz else 0), xs)

# SLA deadlines: merged opening intervals against a clock that ticks minute by minute
from zoneinfo import ZoneInfo  # noqa: E402
for trial in range(12):
    offices = rnd.choice([["Madrid"], ["New York", "Madrid"], ["Sydney", "London"]])
    day0 = dt.date(2026, 3, 1) + dt.timedelta(days=rnd.randint(0, 40))
    hol = {o: {day0 + dt.timedelta(days=rnd.randint(1, 10))} for o in offices}
    half = {o: {day0 + dt.timedelta(days=rnd.randint(1, 10))} for o in offices}
    iv = R9._business(offices, hol, half, day0 - dt.timedelta(days=1), day0 + dt.timedelta(days=30))

    def open_at(u):
        for o in offices:
            zone, periods = R9._OFFICES[o]
            loc = u.astimezone(ZoneInfo(zone))
            d, mins = loc.date(), loc.hour * 60 + loc.minute
            if d.weekday() >= 5 or d in hol[o]:
                continue
            end_ = R9._HALF[o] if d in half[o] else 24 * 60
            if any(a <= mins < min(b, end_) for a, b in periods):
                return True
        return False
    opened = dt.datetime(day0.year, day0.month, day0.day, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=rnd.randint(0, 9000))
    sla = rnd.choice([240, 495, 960])
    ps = opened + dt.timedelta(minutes=rnd.randint(30, 3000))
    pauses = [(ps, ps + dt.timedelta(minutes=rnd.randint(60, 1500)))] if rnd.random() < 0.6 else []
    t, left = opened, sla
    while left:
        if open_at(t) and not any(a <= t < b for a, b in pauses):
            left -= 1
        t += dt.timedelta(minutes=1)
    check(f"deadline {trial}", R9._deadline(iv, opened, sla, pauses) == t, (offices, opened, sla, pauses))

# code_trace: the expected lines are what a fresh interpreter prints
for lv in (9, 10):
    it = R.KINDS["code_trace"](6, lv)
    src = it.messages[0]["content"].split("```python\n")[1].split("```")[0]
    out = subprocess.run([sys.executable, "-W", "ignore", "-c", src], capture_output=True, text=True, timeout=60).stdout
    check(f"code_trace L{lv} subprocess", out.rstrip("\n").split("\n") == it.meta["expected"])

# logic: every clue holds in the hidden arrangement; the counter's exact counts (not just 0 / 1 / many) agree with
# brute force; the schedule solver with skills agrees with a day-by-day search
for lv in (9, 10):
    out = None
    r_ = rng(R.BLOCK, f"logic{lv}", 3)
    while out is None:
        out = R9._zebra_counting(r_, lv)
    n, cats, vals, house, chosen, total = out
    check(f"logic L{lv} count", R._zebra_count(n, cats, chosen, limit=100) == total and total > 1)
    P = lambda e: house[e]
    for cl in chosen:
        t = cl[0]
        ok = (P(cl[1]) == cl[2] if t == "pos" else P(cl[1]) != cl[2] if t == "notpos" else P(cl[1]) in (0, n - 1)
              if t == "end" else R._REL[t](P(cl[1]), P(cl[2])) if t in R._REL else
              (P(cl[1][1]) == P(cl[1][2])) != (P(cl[2][1]) == P(cl[2][2])) if t == "xor" else
              min(P(cl[2]), P(cl[3])) < P(cl[1]) < max(P(cl[2]), P(cl[3])))
        check(f"logic L{lv} clue holds {cl}", ok)
N, CATS = 3, 5
ents = [(c, v) for c in range(CATS) for v in range(N)]
perms = list(itertools.permutations(range(N)))
for trial in range(60):
    clues = []
    for _ in range(rnd.randint(3, 10)):
        t = rnd.choice(["pos", "same", "diff", "next", "before", "xor", "between"])
        if t == "pos":
            clues.append((t, rnd.choice(ents), rnd.randrange(N)))
        elif t == "xor":
            a, b, c, d = rnd.sample(ents, 4)
            clues.append(("xor", ("same", a, b), ("same", c, d)))
        elif t == "between":
            clues.append(("between",) + tuple(rnd.sample(ents, 3)))
        else:
            clues.append((t,) + tuple(rnd.sample(ents, 2)))
    cnt = 0
    for pos in itertools.product(perms, repeat=CATS):
        H = lambda e: pos[e[0]][e[1]]
        good = True
        for cl in clues:
            t = cl[0]
            if t == "pos":
                good = H(cl[1]) == cl[2]
            elif t in R._REL:
                good = R._REL[t](H(cl[1]), H(cl[2]))
            elif t == "xor":
                good = (H(cl[1][1]) == H(cl[1][2])) != (H(cl[2][1]) == H(cl[2][2]))
            else:
                good = min(H(cl[2]), H(cl[3])) < H(cl[1]) < max(H(cl[2]), H(cl[3]))
            if not good:
                break
        cnt += good
        if cnt > 2:
            break
    check(f"puzzle count 5 categories {trial}", min(cnt, 2) == min(R._zebra_count(N, CATS, clues), 2), clues)
N2, CATS2 = 4, 3
ents2 = [(c, v) for c in range(CATS2) for v in range(N2)]
perms2 = list(itertools.permutations(range(N2)))
for trial in range(60):
    clues = []
    for _ in range(rnd.randint(2, 6)):
        t = rnd.choice(["notpos", "diff", "next", "before", "gap", "xor", "between"])
        if t == "notpos":
            clues.append((t, rnd.choice(ents2), rnd.randrange(N2)))
        elif t == "xor":
            a, b, c, d = rnd.sample(ents2, 4)
            clues.append(("xor", ("same", a, b), ("same", c, d)))
        elif t == "between":
            clues.append(("between",) + tuple(rnd.sample(ents2, 3)))
        else:
            clues.append((t,) + tuple(rnd.sample(ents2, 2)))
    cnt = 0
    for pos in itertools.product(perms2, repeat=CATS2):
        H = lambda e: pos[e[0]][e[1]]
        ok = True
        for cl in clues:
            t = cl[0]
            ok = (H(cl[1]) != cl[2] if t == "notpos" else R._REL[t](H(cl[1]), H(cl[2])) if t in R._REL else
                  (H(cl[1][1]) == H(cl[1][2])) != (H(cl[2][1]) == H(cl[2][2])) if t == "xor" else
                  min(H(cl[2]), H(cl[3])) < H(cl[1]) < max(H(cl[2]), H(cl[3])))
            if not ok:
                break
        cnt += ok
    check(f"exact count {trial}", R._zebra_count(N2, CATS2, clues, limit=10 ** 6) == cnt, (cnt, clues))


def brute_skills(names, people, dur, pre, release, groups, avail):
    n = len(names)
    ix = {t: i for i, t in enumerate(names)}
    preds = [[ix[q] for q in pre[t]] for t in names]
    grp = [{g for g, m in enumerate(groups) if t in m} for t in names]
    layer, t = {(0, ())}, 0
    while True:
        nxt = set()
        for done, running in layer:
            started = done | sum(1 << i for i, _p, _r in running)
            free = [p for p in people if p not in {q for _i, q, _r in running} and avail.get(p, 0) <= t]
            elig = [i for i in range(n) if not started >> i & 1 and all(done >> q & 1 for q in preds[i])
                    and release.get(names[i], 0) <= t]
            used = set().union(*[grp[i] for i, _p, _r in running]) if running else set()
            for k in range(min(len(free), len(elig)) + 1):
                for sub in itertools.combinations(elig, k):
                    gs = [g for i in sub for g in grp[i]]
                    if len(gs) != len(set(gs)) or set(gs) & used:
                        continue
                    for ps in itertools.permutations(free, k):
                        if any(dur[names[i]][q] is None for i, q in zip(sub, ps)):
                            continue
                        run = [(i, q, r_ - 1) for i, q, r_ in running] + [(i, q, dur[names[i]][q] - 1) for i, q in zip(sub, ps)]
                        nd = done | sum(1 << i for i, _q, r_ in run if r_ == 0)
                        if nd == (1 << n) - 1:
                            return t + 1
                        nxt.add((nd, tuple(sorted(x for x in run if x[2] > 0))))
        layer, t = nxt, t + 1


for trial in range(80):
    n = rnd.randint(4, 7)
    names = [f"t{i}" for i in range(n)]
    people = ["A", "B", "C"][:rnd.randint(2, 3)]
    dur = {t: {q: (rnd.randint(1, 4) if q in can else None) for q in people}
           for t, can in ((t, rnd.sample(people, rnd.randint(1, len(people)))) for t in names)}
    pre = {t: sorted(rnd.sample(names[:i], min(i, rnd.choice([0, 1, 1, 2])))) for i, t in enumerate(names)}
    groups = [rnd.sample(names, min(n, 3))] if rnd.random() < 0.7 else []
    release = {t: rnd.randint(0, 4) for t in rnd.sample(names, rnd.randint(0, 2))}
    avail = {q: rnd.randint(0, 3) for q in rnd.sample(people, rnd.randint(0, 1))}
    a = R9._skills_min(names, people, dur, pre, release, groups, avail)
    check(f"skills schedule {trial}", a == brute_skills(names, people, dur, pre, release, groups, avail))

# code scale references: the fast versions give the same results as the plain ones
for trial in range(120):
    rr = random.Random(trial)
    lv = rr.choice([9, 10])
    keys = [("bulk:" if lv >= 10 else "") + f"k{i}" for i in range(rr.randint(3, 60))]
    ops, t = [], 0
    for _ in range(rr.randint(50, 300)):
        t += rr.choice([0, 0, 1, 2])
        x, k = rr.random(), rr.choice(keys)
        ops.append(["put", k, rr.randint(1, 99), t] + ([rr.choice([2, 5, 50])] if rr.random() < 0.3 else []) if x < 0.45 else
                   ["get", k, t] if x < 0.8 else ["peek", k, t] if x < 0.88 else ["del", k, t] if x < 0.95 else
                   ["resize", rr.randint(1, 40), t])
    cap, ttl, age = rr.randint(2, 40), rr.randint(3, 9), rr.randint(3, 7)
    check(f"lru fast {trial}", C9._ref9_lru(ops, cap, ttl, age, {"bulk": 10 ** 6}, lv) == C9._lru_fast(ops, cap, ttl, age, lv))
    nr = rr.randint(2, 6)
    rooms = [rr.choice([4, 6, 8, 10, 12, 20]) for _ in range(nr)]
    ms = [[s_, s_ + rr.choice([15, 30, 60, 120]), rr.randint(1, 22), rr.randint(1, 3)]
          for s_ in (rr.randint(0, 60) * 15 for _ in range(rr.randint(5, 100)))]
    maint = [[rr.randrange(nr), s_, s_ + 60] for s_ in (rr.randint(0, 60) * 15 for _ in range(rr.randint(0, 3)))] if lv >= 10 else []
    check(f"rooms fast {trial}", C9._ref9_rooms(rooms, ms, maint, 15, 10, lv) == C9._rooms_fast(rooms, ms, maint, 15, 10, lv))

print("all passed" if not failed else f"{failed} failed")
sys.exit(1 if failed else 0)
