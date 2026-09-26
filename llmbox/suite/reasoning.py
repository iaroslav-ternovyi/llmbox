"""Reasoning without outside knowledge (practical guidance / math / logic): exact answers, 5 difficulty levels.

Level scales the number of interacting rules and the amount of state to track, so strong models do not saturate.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import itertools
import re

from .common import Item, final_answer, num, rng

BLOCK = "reasoning"
INSTR = "\n\nThink it through, then finish with a final line exactly in the form:\nANSWER: <answer>"


def _num_check(expected: float, tol: float = 0.01):
    def check(text: str, _t=None) -> float:
        v = num(final_answer(text))
        return 1.0 if v is not None and abs(v - expected) <= tol else 0.0
    return check


def _str_check(expected: str):
    """Exact word answer, tolerant to markup and filler ("It is Friday." == "Friday", "**Ana**" == "Ana")."""
    filler = {"it", "is", "the", "a", "on", "answer", "name"}

    def words(s: str) -> list[str]:
        return [w for w in re.findall(r"[\w-]+", (s or "").lower()) if w not in filler]

    def check(text: str, _t=None) -> float:
        return 1.0 if words(final_answer(text)) == words(expected) else 0.0
    return check


def arith(seed: int, level: int = 3) -> Item:
    """Order pricing with interacting rules; level adds items and rules."""
    r = rng(BLOCK, f"arith{level}", seed)
    names = ["notebooks", "pens", "backpacks", "calculators", "folders", "markers", "lamps", "chairs", "staplers", "rulers"]
    items = r.sample(names, 2 + level)
    qty = {n: r.randint(2, 14) for n in items}
    price = {n: round(r.uniform(1.5, 60), 2) for n in items}
    rules, texts = [], []
    disc_item = items[0]
    d1 = r.choice([10, 15, 20])
    texts.append(f"{disc_item} are {d1}% off")
    rules.append(("pct", disc_item, d1))
    if level >= 2:
        bulk_item, bulk_min, bulk_price = items[1], r.randint(5, 10), None
        bulk_price = round(price[items[1]] * r.choice([0.7, 0.8]), 2)
        texts.append(f"if you buy at least {bulk_min} {bulk_item}, ALL of them cost ${bulk_price:.2f} each instead")
        rules.append(("bulk", bulk_item, bulk_min, bulk_price))
    if level >= 3:
        bogo = items[2]
        texts.append(f"{bogo} are buy-2-get-1-free (every third unit is free)")
        rules.append(("bogo", bogo))
    exempt = items[-1] if level >= 4 else None
    ship_thr, ship_fee = r.choice([150, 250, 400]), r.choice([9.9, 14.5, 19.0])
    tax = r.choice([7, 10, 21])
    coupon = r.choice([5, 8, 12]) if level >= 5 else None

    line = {}
    for n in items:
        p, q = price[n], qty[n]
        for rule in rules:
            if rule[0] == "bulk" and rule[1] == n and q >= rule[2]:
                p = rule[3]
        billable = q - q // 3 if any(rl[0] == "bogo" and rl[1] == n for rl in rules) else q
        amt = p * billable
        for rule in rules:
            if rule[0] == "pct" and rule[1] == n:
                amt *= (1 - rule[2] / 100)
        line[n] = amt
    goods = sum(line.values())
    if coupon:
        goods -= coupon
    taxed = sum(v for n, v in line.items() if n != exempt) - (coupon or 0)
    total = taxed * (1 + tax / 100) + (line[exempt] if exempt else 0) + (0 if goods >= ship_thr else ship_fee)
    expected = round(total, 2)
    lst = "\n".join(f"- {qty[n]} {n} at ${price[n]:.2f} each" for n in items)
    prompt = (f"An office orders:\n{lst}\nRules, applied per product line in this order: " + "; ".join(texts) + ". "
              + (f"After that, a ${coupon} coupon is subtracted from the order (it reduces the taxable amount). " if coupon else "")
              + f"Sales tax is {tax}%" + (f", but {exempt} are tax-exempt" if exempt else "") + ". "
              f"Shipping costs ${ship_fee} unless the goods total (after discounts{' and coupon' if coupon else ''}, before tax) is at least ${ship_thr}; "
              f"shipping is not taxed. What is the final amount to pay, rounded to cents?" + INSTR)
    return Item(f"{BLOCK}.arith.L{level}.{seed}", BLOCK, "arith", [{"role": "user", "content": prompt}], _num_check(expected, 0.011),
                meta={"expected": expected, "level": level})


def dates(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"dates{level}", seed)
    start = dt.date(2026, 1, 1) + dt.timedelta(days=r.randint(0, 700))
    workdays = r.randint(10 + 10 * level, 25 + 15 * level)
    holidays = sorted({start + dt.timedelta(days=r.randint(1, workdays + 30)) for _ in range(level)})
    weekend = {5, 6}
    extra = ""
    if level >= 3:
        wd = r.choice([0, 4])  # the team also does not work on Mondays or Fridays in some weeks
        extra = f" In addition, the team never works on the first {['Monday', 'Friday'][wd // 4]} of any month."
    d, n = start, 0
    while n < workdays:
        d += dt.timedelta(days=1)
        if d.weekday() in weekend or d in holidays:
            continue
        if level >= 3 and d.weekday() == wd and d.day <= 7:
            continue
        n += 1
    answer = d.isoformat()
    tail = " On what date (YYYY-MM-DD) is the last working day?"
    if level >= 5:
        answer = d.strftime("%A")
        tail = " On which weekday (e.g. Monday) does the last working day fall?"
    prompt = (f"A project starts on {start.isoformat()} ({start.strftime('%A')}). It needs {workdays} working days, "
              f"counting from the day AFTER the start date. Working days are Monday to Friday, except these public "
              f"holidays: {', '.join(h.isoformat() for h in holidays)}.{extra}" + tail + INSTR)
    return Item(f"{BLOCK}.dates.L{level}.{seed}", BLOCK, "dates", [{"role": "user", "content": prompt}], _str_check(answer),
                meta={"expected": answer, "level": level})


def logic(seed: int, level: int = 3) -> Item:
    """N people in a row of houses, each with a drink and a city; indirect + positional clues until unique."""
    r = rng(BLOCK, f"logic{level}", seed)
    n = 4 if level <= 2 else 5
    people = r.sample(["Ana", "Ben", "Carla", "Dmitri", "Elif", "Farid", "Greta", "Hugo", "Iris", "Jonas"], n)
    drinks = r.sample(["tea", "coffee", "juice", "water", "cocoa", "milk", "kefir"], n)
    cities = r.sample(["Lisbon", "Oslo", "Kyiv", "Lima", "Quito", "Riga", "Tunis"], n)
    # truth: house order = order of `order`; each person has drink/city
    order = r.sample(people, n)
    truth = {p: (drinks[i], cities[i], order.index(p)) for i, p in enumerate(r.sample(people, n))}
    positional = level >= 4
    if positional:
        sols = [{p: (dp[i], cp[i], hp.index(p)) for i, p in enumerate(people)}
                for hp in itertools.permutations(people) for dp in itertools.permutations(drinks) for cp in itertools.permutations(cities)
                if True] if n <= 4 else None
    # For 5 people with positions the space is too big to enumerate naively; solve with nested filtering instead.
    def space():
        hs = list(itertools.permutations(people)) if positional else [tuple(order)]
        for hp in hs:
            for dp in itertools.permutations(drinks):
                for cp in itertools.permutations(cities):
                    yield {p: (dp[i], cp[i], hp.index(p)) for i, p in enumerate(people)}

    def clues():
        for p in people:
            d, c, h = truth[p]
            yield (f"{p} does not drink {r.choice([x for x in drinks if x != d])}.", None)
            yield (f"{p} does not live in {r.choice([x for x in cities if x != c])}.", None)
            alt = sorted([c, r.choice([x for x in cities if x != c])])
            yield (f"{p} lives in either {alt[0]} or {alt[1]}.", None)
            yield (f"The person who drinks {d} lives in {c}.", None)
            if positional:
                q = next(x for x, v in truth.items() if v[2] == (h + 1 if h + 1 < n else h - 1))
                yield (f"{p} lives right next to {q}.", None)
                if h > 0:
                    left = next(x for x, v in truth.items() if v[2] == h - 1)
                    yield (f"The person who drinks {truth[left][0]} lives immediately to the left of {p}.", None)
                yield (f"{p} does not live in house {r.choice([k + 1 for k in range(n) if k != h])} (houses are numbered 1-{n} from the left).", None)

    import re as _re

    def pred(text):
        m = _re.match(r"(\w+) does not drink (\w+)\.", text)
        if m: return lambda s, a=m.group(1), b=m.group(2): s[a][0] != b
        m = _re.match(r"(\w+) does not live in (\w+)\.$", text)
        if m: return lambda s, a=m.group(1), b=m.group(2): s[a][1] != b
        m = _re.match(r"(\w+) lives in either (\w+) or (\w+)\.", text)
        if m: return lambda s, a=m.group(1), b=m.group(2), c=m.group(3): s[a][1] in (b, c)
        m = _re.match(r"The person who drinks (\w+) lives in (\w+)\.", text)
        if m: return lambda s, d=m.group(1), c=m.group(2): any(v[0] == d and v[1] == c for v in s.values())
        m = _re.match(r"(\w+) lives right next to (\w+)\.", text)
        if m: return lambda s, a=m.group(1), b=m.group(2): abs(s[a][2] - s[b][2]) == 1
        m = _re.match(r"The person who drinks (\w+) lives immediately to the left of (\w+)\.", text)
        if m: return lambda s, d=m.group(1), b=m.group(2): any(v[0] == d and v[2] == s[b][2] - 1 for v in s.values())
        m = _re.match(r"(\w+) does not live in house (\d+)", text)
        if m: return lambda s, a=m.group(1), k=int(m.group(2)): s[a][2] != k - 1
        raise ValueError(text)

    pool = [(t, pred(t)) for t, _ in clues()]
    r.shuffle(pool)
    # greedy clue selection with incremental filtering over the full solution space
    sols = list(space())
    chosen = []
    for text, fn in pool:
        if len(sols) == 1:
            break
        new = [s for s in sols if fn(s)]
        if len(new) < len(sols):
            chosen.append(text)
            sols = new
    if len(sols) != 1:
        return logic(seed + 10_000, level)
    if positional:
        target = r.choice(people)
        answer = str(truth[target][2] + 1)
        q = f"In which house number does the person who drinks {truth[target][0]} live?"
    else:
        td = r.choice(drinks)
        answer = next(p for p, v in truth.items() if v[0] == td)
        q = f"Who drinks {td}? Answer with the name only."
    intro = (f"{n} friends ({', '.join(people)}) each drink a different beverage ({', '.join(drinks)}) and live in a different "
             f"city ({', '.join(cities)})" + (f"; they also live in {n} houses in a row, one per house." if positional else "."))
    prompt = intro + " Clues:\n" + "\n".join(f"{i+1}. {c}" for i, c in enumerate(chosen)) + f"\n{q}" + INSTR
    chk = _num_check(int(answer), 0) if positional else _str_check(answer)   # "House 2" / "2" are the same answer
    return Item(f"{BLOCK}.logic.L{level}.{seed}", BLOCK, "logic", [{"role": "user", "content": prompt}], chk,
                meta={"expected": answer, "clues": len(chosen), "level": level})


def code_trace(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"code_trace{level}", seed)
    data = [r.randint(-9, 25) for _ in range(5 + 2 * level)]
    k, m = r.randint(2, 4), r.randint(3, 9)
    body = [f"def f(xs):",
            f"    acc, seen, stack = 0, {{}}, []",
            f"    for i, x in enumerate(xs):",
            f"        if x % {k} == 0 and x not in seen:",
            f"            seen[x] = i",
            f"            acc += x * (i + 1)",
            f"        elif x > {m}:",
            f"            acc -= x // 2" + ("" if level < 2 else "\n            stack.append(x)"),
            f"        else:",
            f"            acc += 1" + ("" if level < 3 else "\n            if stack:\n                acc += stack.pop() % 7"),
            ]
    if level >= 4:
        body += [f"    for key in sorted(seen, reverse=True)[:{r.randint(2, 3)}]:",
                 f"        acc ^= key * (seen[key] + 1)"]
    if level >= 5:
        body += ["    def g(n, depth=0):",
                 "        return n if n < 10 or depth > 5 else g(sum(int(c) for c in str(n)) + depth, depth + 1)",
                 "    acc = acc * 3 + g(abs(acc))"]
    body += ["    return acc", "", f"print(f({data}))"]
    src = "\n".join(body) + "\n"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(src, {})  # our own generated code, not model output
    expected = int(buf.getvalue().strip())
    prompt = f"What does this Python program print? Trace it carefully.\n\n```python\n{src}```" + INSTR
    return Item(f"{BLOCK}.code_trace.L{level}.{seed}", BLOCK, "code_trace", [{"role": "user", "content": prompt}],
                _num_check(expected, 0.0), meta={"expected": expected, "level": level})


def table(seed: int, level: int = 3) -> Item:
    """Two tables (orders + customers); filter, join, group, pick; level grows rows and conditions."""
    r = rng(BLOCK, f"table{level}", seed)
    regions = ["North", "South", "East", "West"]
    custs = {f"K{10 + i}": (r.choice(regions), r.choice(["retail", "wholesale"])) for i in range(6 + 2 * level)}
    rows = [(f"R-{100 + i}", r.choice(list(custs)), r.randint(1, 12), round(r.uniform(50, 900), 2), r.choice(["paid", "refunded"]))
            for i in range(10 + 8 * level)]
    reg = r.choice(regions)
    seg = r.choice(["retail", "wholesale"])
    months = sorted(r.sample(range(1, 13), 2))
    sel = [x for x in rows if custs[x[1]][0] == reg and months[0] <= x[2] <= months[1]
           and (level < 2 or x[4] == "paid") and (level < 3 or custs[x[1]][1] == seg)]
    if not sel:
        return table(seed + 10_000, level)
    per = {}
    for x in sel:
        per[x[1]] = per.get(x[1], 0) + x[3]
    best = max(per.items(), key=lambda kv: (round(kv[1], 2), kv[0]))
    ctab = "customer | region | segment\n" + "\n".join(f"{k} | {v[0]} | {v[1]}" for k, v in custs.items())
    otab = "order | customer | month | amount | status\n" + "\n".join(f"{a} | {b} | {c} | {d:.2f} | {e}" for a, b, c, d, e in rows)
    cond = [f"customers in the {reg} region", f"orders with month {months[0]}-{months[1]} (inclusive)"]
    if level >= 2:
        cond.append("only paid orders (ignore refunded)")
    if level >= 3:
        cond.append(f"only {seg} customers")
    prompt = (f"Customers:\n{ctab}\n\nOrders:\n{otab}\n\nConsider " + ", ".join(cond) + ". Which customer has the highest "
              f"total order amount under these conditions, and what is that total? Final answer format: <customer>; <total "
              f"rounded to 2 decimals>" + INSTR)

    def check(text: str, _t=None) -> float:
        parts = [p.strip() for p in (final_answer(text) or "").split(";")]
        ok_id = bool(parts) and parts[0].upper() == best[0]
        ok_sum = len(parts) > 1 and num(parts[1]) is not None and abs(num(parts[1]) - round(best[1], 2)) <= 0.011
        return 1.0 if ok_id and ok_sum else 0.0
    return Item(f"{BLOCK}.table.L{level}.{seed}", BLOCK, "table", [{"role": "user", "content": prompt}], check,
                meta={"expected": f"{best[0]}; {best[1]:.2f}", "level": level})


TASKS = ["design", "backend", "frontend", "tests", "docs", "migration", "review", "deploy", "audit", "training", "survey",
         "pricing", "hiring", "security", "analytics", "support"]


def schedule(seed: int, level: int = 3) -> Item:
    """Project planning: tasks with durations, prerequisites and (level 5) a pair that cannot overlap, done by W people.
    The minimum makespan needs real search - a greedy plan is usually a day or two late. Verified exactly: every optimal
    schedule is reproduced by placing tasks in order of their optimal start times at the earliest feasible moment, so a
    search over precedence-consistent orders with that placement is exact."""
    r = rng(BLOCK, f"schedule{level}", seed)
    n, w = 4 + level, 2 if level < 4 else 3
    names = r.sample(TASKS, n)
    dur = {t: r.randint(1, 8) for t in names}
    pre = {t: sorted(r.sample(names[:i], min(i, r.choice([0, 1, 1, 2])))) for i, t in enumerate(names)}
    clash = tuple(r.sample([t for t in names if not pre[t]] + names[-3:], 2)) if level >= 5 else None
    if clash and (clash[0] in pre[clash[1]] or clash[1] in pre[clash[0]] or clash[0] == clash[1]):
        return schedule(seed + 10_000, level)
    best = [sum(dur.values()) + 1]

    def dfs(done: dict, free: list):
        span = max(list(done.values()) + [0])
        if span >= best[0]:
            return
        if len(done) == n:
            best[0] = span
            return
        for t in names:
            if t in done or any(p not in done for p in pre[t]):
                continue
            ready = max([done[p] for p in pre[t]] + [0])
            if clash and t in clash:
                other = clash[1] if t == clash[0] else clash[0]
                if other in done:
                    ready = max(ready, done[other])
            k = min(range(w), key=lambda i: free[i])
            start = max(ready, free[k])
            nf = list(free)
            nf[k] = start + dur[t]
            done[t] = start + dur[t]
            dfs(done, sorted(nf))
            del done[t]
    dfs({}, [0] * w)
    answer = best[0]
    lines = [f"- {t}: {dur[t]} day{'s' if dur[t] > 1 else ''}" + (f", after {' and '.join(pre[t])}" if pre[t] else "") for t in r.sample(names, n)]
    prompt = (f"A team of {w} people must complete these tasks:\n" + "\n".join(lines) + "\n\nEach task is done by one person from "
              "start to finish, takes the given number of full days and cannot be split or shared. A task can start only when "
              "all tasks it comes after are finished. A person works on one task at a time."
              + (f" The {clash[0]} and {clash[1]} tasks need the same test lab, so they cannot be worked on at the same time." if clash else "")
              + " What is the minimum number of days needed to finish all tasks?" + INSTR)
    return Item(f"{BLOCK}.schedule.L{level}.{seed}", BLOCK, "schedule", [{"role": "user", "content": prompt}], _num_check(answer, 0),
                meta={"expected": answer, "level": level})


def budget(seed: int, level: int = 3) -> Item:
    """Portfolio choice: maximize value under a budget (and a people cap from level 4) with requires / excludes /
    at-least-one constraints. Verified by enumerating all subsets."""
    r = rng(BLOCK, f"budget{level}", seed)
    n = 6 + 2 * level
    ids = [chr(65 + i) for i in range(n)]
    cost = {p: r.randint(5, 40) for p in ids}
    value = {p: r.randint(10, 90) for p in ids}
    ppl = {p: r.randint(1, 4) for p in ids}
    cap_money = int(sum(cost.values()) * r.uniform(0.38, 0.5))
    cap_ppl = int(sum(ppl.values()) * 0.45) if level >= 4 else None
    reqs = [tuple(r.sample(ids, 2)) for _ in range(level)]                 # (a, b): a requires b
    excl = [tuple(r.sample(ids, 2)) for _ in range(max(0, level - 1))]      # not both
    one_of = tuple(r.sample(ids, 3)) if level >= 5 else None                # at least one
    best, best_set = -1, None
    for mask in range(1 << n):
        s = {ids[i] for i in range(n) if mask >> i & 1}
        if sum(cost[p] for p in s) > cap_money or (cap_ppl and sum(ppl[p] for p in s) > cap_ppl):
            continue
        if any(a in s and b not in s for a, b in reqs) or any(a in s and b in s for a, b in excl):
            continue
        if one_of and not (set(one_of) & s):
            continue
        v = sum(value[p] for p in s)
        if v > best:
            best, best_set = v, s
    rows = "\n".join(f"- {p}: cost {cost[p]}k EUR, value {value[p]}" + (f", needs {ppl[p]} {'person' if ppl[p] == 1 else 'people'}" if cap_ppl else "") for p in ids)
    rules = [f"{a} can only be funded if {b} is funded too" for a, b in reqs] + [f"{a} and {b} cannot both be funded" for a, b in excl]
    if one_of:
        rules.append(f"at least one of {', '.join(one_of)} must be funded")
    prompt = (f"A company can fund some of these projects:\n{rows}\n\nThe total budget is {cap_money}k EUR"
              + (f" and only {cap_ppl} people are available (each funded project needs its people full-time)" if cap_ppl else "")
              + ". Rules:\n" + "\n".join(f"- {x}" for x in rules)
              + "\nWhich combination maximizes the total value? Give the maximum total value." + INSTR)
    return Item(f"{BLOCK}.budget.L{level}.{seed}", BLOCK, "budget", [{"role": "user", "content": prompt}], _num_check(best, 0),
                meta={"expected": best, "set": sorted(best_set), "level": level})


KINDS = {"arith": arith, "dates": dates, "logic": logic, "code_trace": code_trace, "table": table, "schedule": schedule, "budget": budget}
# the quick tier keeps the kinds that still discriminate at level 5 (v0.5: Tiel solved every older kind at level 5)
QUICK = ["arith", "logic", "code_trace", "schedule", "budget"]
