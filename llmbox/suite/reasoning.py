"""Reasoning without outside knowledge (practical guidance / math / logic): exact answers, 8 difficulty levels (7-8: v0.11).

Level scales the number of interacting rules and the amount of state to track, so strong models do not saturate.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import itertools
import re

from .common import Item, final_answer, multi_check, num, rng

BLOCK = "reasoning"
INSTR = "\n\nThink it through, then finish with a final line exactly in the form:\nANSWER: <answer>"
# v0.10: the kinds in the quick tier ask for several results of the same problem (intermediate and final), credit per
# result - one 0/1 answer per item made reasoning the noisiest block per minute.


def _instr(n: int) -> str:
    return ("\n\nThink it through, then finish with one final line per question, exactly in the form:\n"
            + "\n".join(f"ANSWER {i + 1}: <answer>" for i in range(n)))


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
    if level >= 7:
        return _arith_hard(seed, level)
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
    lines_total = goods
    if coupon:
        goods -= coupon
    taxed = sum(v for n, v in line.items() if n != exempt) - (coupon or 0)
    total = taxed * (1 + tax / 100) + (line[exempt] if exempt else 0) + (0 if goods >= ship_thr else ship_fee)
    tax_amt = taxed * tax / 100
    expected = [round(lines_total, 2), round(tax_amt, 2), round(total, 2)]
    lst = "\n".join(f"- {qty[n]} {n} at ${price[n]:.2f} each" for n in items)
    prompt = (f"An office orders:\n{lst}\nRules, applied per product line in this order: " + "; ".join(texts) + ". "
              + (f"After that, a ${coupon} coupon is subtracted from the order (it reduces the taxable amount). " if coupon else "")
              + f"Sales tax is {tax}%" + (f", but {exempt} are tax-exempt" if exempt else "") + ". "
              f"Shipping costs ${ship_fee} unless the goods total (after discounts{' and coupon' if coupon else ''}, before tax) is at least ${ship_thr}; "
              f"shipping is not taxed.\n1. What is the total of the product lines after their discounts (before "
              + ("the coupon, " if coupon else "") + "tax and shipping)?\n2. How much sales tax is charged?\n"
              "3. What is the final amount to pay?\nRound each to cents." + _instr(3))
    return Item(f"{BLOCK}.arith.L{level}.{seed}", BLOCK, "arith", [{"role": "user", "content": prompt}],
                multi_check([_num_check(e, 0.011) for e in expected]), meta={"expected": expected, "level": level})


def dates(seed: int, level: int = 3) -> Item:
    if level >= 7:
        return _dates_hard(seed, level)
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
    if level >= 7:
        return _logic_hard(seed, level)
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
    qs, answers, checks = [], [], []
    for td in r.sample(drinks, 3):
        if positional:
            ans = str(next(v[2] for v in truth.values() if v[0] == td) + 1)
            qs.append(f"In which house number does the person who drinks {td} live?")
            checks.append(_num_check(int(ans), 0))   # "House 2" / "2" are the same answer
        else:
            ans = next(p for p, v in truth.items() if v[0] == td)
            qs.append(f"Who drinks {td}? Answer with the name only.")
            checks.append(_str_check(ans))
        answers.append(ans)
    intro = (f"{n} friends ({', '.join(people)}) each drink a different beverage ({', '.join(drinks)}) and live in a different "
             f"city ({', '.join(cities)})" + (f"; they also live in {n} houses in a row, one per house." if positional else "."))
    prompt = (intro + " Clues:\n" + "\n".join(f"{i+1}. {c}" for i, c in enumerate(chosen)) + "\nQuestions:\n"
              + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs)) + _instr(len(qs)))
    return Item(f"{BLOCK}.logic.L{level}.{seed}", BLOCK, "logic", [{"role": "user", "content": prompt}], multi_check(checks),
                meta={"expected": answers, "clues": len(chosen), "level": level})


def code_trace(seed: int, level: int = 3) -> Item:
    if level >= 7:
        return _code_trace_hard(seed, level)
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
            f"    print(acc)",
            f"    print(sum(seen.values()))",
            ]
    if level >= 4:
        body += [f"    for key in sorted(seen, reverse=True)[:{r.randint(2, 3)}]:",
                 f"        acc ^= key * (seen[key] + 1)",
                 f"    print(acc)"]
    if level >= 5:
        body += ["    def g(n, depth=0):",
                 "        return n if n < 10 or depth > 5 else g(sum(int(c) for c in str(n)) + depth, depth + 1)",
                 "    acc = acc * 3 + g(abs(acc))"]
    # below level 5 the return value equals the last printed line: not printed again
    body += ["    return acc", "", f"print(f({data}))" if level >= 5 else f"f({data})"]
    src = "\n".join(body) + "\n"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(src, {})  # our own generated code, not model output
    expected = [int(x) for x in buf.getvalue().split()]
    prompt = (f"What does this Python program print? It prints {len(expected)} lines; trace it carefully and give every line."
              f"\n\n```python\n{src}```" + _instr(len(expected)).replace("per question", "per printed line, in order"))
    return Item(f"{BLOCK}.code_trace.L{level}.{seed}", BLOCK, "code_trace", [{"role": "user", "content": prompt}],
                multi_check([_num_check(e, 0.0) for e in expected]), meta={"expected": expected, "level": level})


def table(seed: int, level: int = 3) -> Item:
    """Two tables (orders + customers); filter, join, group, pick; level grows rows and conditions."""
    if level >= 7:
        return _table_hard(seed, level)
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
    if level >= 7:
        return _schedule_hard(seed, level)
    r = rng(BLOCK, f"schedule{level}", seed)
    n, w = 4 + level, 2 if level < 4 else 3
    names = r.sample(TASKS, n)
    dur = {t: r.randint(1, 8) for t in names}
    pre = {t: sorted(r.sample(names[:i], min(i, r.choice([0, 1, 1, 2])))) for i, t in enumerate(names)}
    clash = tuple(r.sample([t for t in names if not pre[t]] + names[-3:], 2)) if level >= 5 else None
    if clash and (clash[0] in pre[clash[1]] or clash[1] in pre[clash[0]] or clash[0] == clash[1]):
        return schedule(seed + 10_000, level)
    def makespan(w: int) -> int:
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
        return best[0]
    # the critical path (as many people as tasks) first, then the team's minimum: partial credit for the first
    expected = [makespan(n), makespan(w)]
    lines = [f"- {t}: {dur[t]} day{'s' if dur[t] > 1 else ''}" + (f", after {' and '.join(pre[t])}" if pre[t] else "") for t in r.sample(names, n)]
    prompt = (f"A team of {w} people must complete these tasks:\n" + "\n".join(lines) + "\n\nEach task is done by one person from "
              "start to finish, takes the given number of full days and cannot be split or shared. A task can start only when "
              "all tasks it comes after are finished. A person works on one task at a time."
              + (f" The {clash[0]} and {clash[1]} tasks need the same test lab, so they cannot be worked on at the same time." if clash else "")
              + "\n1. With enough people to staff every task at once, what is the minimum number of days needed to finish all tasks?"
              f"\n2. With the team of {w}, what is the minimum number of days?" + _instr(2))
    return Item(f"{BLOCK}.schedule.L{level}.{seed}", BLOCK, "schedule", [{"role": "user", "content": prompt}],
                multi_check([_num_check(e, 0) for e in expected]), meta={"expected": expected, "level": level})


def budget(seed: int, level: int = 3) -> Item:
    """Portfolio choice: maximize value under a budget (and a people cap from level 4) with requires / excludes /
    at-least-one constraints. Verified by enumerating all subsets."""
    if level >= 7:
        return _budget_hard(seed, level)
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


# ---------------- levels 7-8 (v0.11): above the frontier's level-6 ceiling -------------------------------------------
# Levels 1-6 stay exactly as they were (answers to them are pooled across suite versions): every kind branches to its
# own generator below at level >= 7, with its own random stream. Harder through more interacting rules, deeper state
# and several graded results per item (numbered answers, credit per result), not through longer answers.
MAX_LEVEL = 8


def _cents(x):
    from decimal import ROUND_HALF_UP, Decimal
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money(x) -> str:
    return f"{x:.2f}"


def _date_check(expected: str):
    """The first YYYY-MM-DD in the answer is the date ("2027-10-21 (Thursday)" counts)."""
    def check(text: str, _t=None) -> float:
        m = re.search(r"\d{4}-\d{2}-\d{2}", final_answer(text) or "")
        return 1.0 if m and m.group(0) == expected else 0.0
    return check


def _arith_hard(seed: int, level: int) -> Item:
    """An invoice where every rule touches one product line (percent off, bulk price, buy-2-get-1, marginal quantity
    tiers, a free unit of one product per two of another), a capped percentage coupon on the taxable lines, per-unit
    bulky shipping on top of a threshold, stated rounding points; level 8 adds a reduced tax rate the coupon skips and a
    loyalty rebate. 5 (level 7) or 6 (level 8) chained results."""
    from decimal import Decimal as D
    r = rng(BLOCK, f"arith{level}", seed)
    names = ["notebooks", "pens", "backpacks", "calculators", "folders", "markers", "lamps", "chairs", "staplers", "rulers",
             "binders", "monitors", "scissors", "envelopes"]
    n = 8 if level == 7 else 10
    items = r.sample(names, n)
    qty = {x: r.randint(2, 16) for x in items}
    price = {x: D(r.randint(150, 6000)) / 100 for x in items}
    pct_i, bulk_i, bogo_i, tier_i, buy_i, free_i = items[:6]
    d1 = r.choice([10, 15, 20, 25])
    bulk_min = r.randint(5, 10)
    bulk_price = _cents(price[bulk_i] * D(r.choice(["0.7", "0.75", "0.8"])))
    qty[tier_i] = r.randint(7, 16)
    t1, t2 = r.choice([(10, 20), (10, 25), (5, 15), (15, 30)])
    exempt = items[-1]
    reduced = items[6:8] if level >= 8 else []
    rate = r.choice([7, 10, 19, 21])
    red_rate = r.choice([4, 5, 10]) if level >= 8 else 0
    c_pct = r.choice([5, 10, 15])
    fee = D(r.choice(["9.90", "14.50", "19.00", "24.90"]))
    bulky = r.choice([bogo_i, free_i] + items[6:])
    surcharge = D(r.choice(["2.50", "3.50", "4.00"]))

    line = {}
    for x in items:
        p, q = price[x], qty[x]
        if x == pct_i:
            amt = p * q * (100 - d1) / 100
        elif x == bulk_i:
            amt = (bulk_price if q >= bulk_min else p) * q
        elif x == bogo_i:
            amt = p * (q - q // 3)
        elif x == tier_i:
            amt = p * min(q, 5) + p * (100 - t1) / 100 * max(0, min(q, 10) - 5) + p * (100 - t2) / 100 * max(0, q - 10)
        elif x == free_i:
            amt = p * (q - min(q, qty[buy_i] // 2))
        else:
            amt = p * q
        line[x] = _cents(amt)
    lines_total = sum(line.values())
    std = [x for x in items if x != exempt and x not in reduced]
    base = sum(line[x] for x in std)
    cap = D(max(5, int(base * c_pct / 100 * D(str(round(r.uniform(0.75, 1.25), 2))))))
    coupon = min(_cents(base * c_pct / 100), cap)
    goods = lines_total - coupon
    tax = _cents((base - coupon) * rate / 100) + (_cents(sum(line[x] for x in reduced) * red_rate / 100) if reduced else 0)
    thr = int(round(float(goods) * r.uniform(0.85, 1.15) / 50)) * 50
    ship = (0 if goods >= thr else fee) + surcharge * qty[bulky]
    rebate = 5 * int(goods // 100) if level >= 8 else 0
    final = goods + tax + ship - rebate
    expected = [float(v) for v in [lines_total, coupon, tax, ship] + ([rebate] if level >= 8 else []) + [final]]

    lst = "\n".join(f"- {qty[x]} {x} at ${_money(price[x])} each" for x in items)
    rules = [f"- {pct_i}: {d1}% off.",
             f"- {bulk_i}: if at least {bulk_min} {bulk_i} are bought, all of them cost ${_money(bulk_price)} each instead.",
             f"- {bogo_i}: buy 2, get 1 free (every third unit is free).",
             f"- {tier_i}: quantity tiers - units 1-5 at the list price, units 6-10 at {t1}% off, every unit above 10 at {t2}% off.",
             f"- {free_i}: one of them is free for every 2 {buy_i} bought (never more free {free_i} than {free_i} bought)."]
    if level >= 8:
        red_txt = f"{reduced[0]} and {reduced[1]}"
        coupon_txt = (f"Coupon: {c_pct}% off the standard-rate product lines (every line except {reduced[0]}, {reduced[1]} and {exempt}), "
                      f"at most ${cap}, rounded to cents.")
        tax_txt = (f"Sales tax: {rate}% of the standard-rate lines minus the coupon; {red_txt} have the reduced rate of "
                   f"{red_rate}% on their line amounts (the coupon does not touch them); {exempt} are tax-exempt. Each rate's "
                   f"tax is rounded to cents separately.")
    else:
        coupon_txt = f"Coupon: {c_pct}% off the taxable product lines (every line except {exempt}), at most ${cap}, rounded to cents."
        tax_txt = f"Sales tax: {rate}% of the taxable product lines minus the coupon, rounded to cents; {exempt} are tax-exempt."
    ship_txt = (f"Shipping: ${_money(fee)}, but free when the goods total (all product lines minus the coupon) is at least "
                f"${thr}. On top of that, every one of the {bulky} shipped adds ${_money(surcharge)}, even when the base shipping "
                f"is free (free units are shipped too). Shipping is not taxed.")
    qs = ["What is the total of all product lines after their rules?", "How much is the coupon?",
          "How much sales tax is charged" + (" in total (both rates)?" if level >= 8 else "?"), "How much is the shipping?"]
    extra = ""
    if level >= 8:
        extra = ("\nLoyalty rebate: $5 for every full $100 of the goods total (all product lines minus the coupon); it is "
                 "subtracted from the amount to pay at the very end and changes neither the tax nor the shipping.")
        qs.append("How much is the loyalty rebate?")
    qs.append("What is the final amount to pay?")
    prompt = (f"An office orders:\n{lst}\nPricing rules (each one applies to a single product line):\n" + "\n".join(rules)
              + "\nEach product line's amount is rounded to cents (half up) once, after its rule; lines without a rule are "
              f"quantity times price.\n{coupon_txt}\n{tax_txt}\n{ship_txt}{extra}\nQuestions:\n"
              + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs)) + "\nGive every amount in dollars and cents." + _instr(len(qs)))
    return Item(f"{BLOCK}.arith.L{level}.{seed}", BLOCK, "arith", [{"role": "user", "content": prompt}],
                multi_check([_num_check(e, 0.011) for e in expected]), meta={"expected": expected, "level": level})


def _dates_hard(seed: int, level: int) -> Item:
    """Phases of a project on a working-day calendar with interacting rules: holidays, an office shutdown, the first
    Monday / last Friday of every month off, a minimum calendar gap between phases; level 8 adds weekend holidays that
    move to the nearest weekday, a four-day-week month and a release date for a third phase. 4-5 results."""
    r = rng(BLOCK, f"dates{level}", seed)
    start = dt.date(2026, 1, 1) + dt.timedelta(days=r.randint(0, 700))
    a_n, gap, b_n = r.randint(25, 40), r.randint(5, 12), r.randint(20, 35)
    c_n = r.randint(12, 22) if level >= 8 else 0
    span = int((a_n + b_n + c_n) * 1.5) + gap + 20
    day = lambda k: start + dt.timedelta(days=k)
    month_rule = r.choice(["first Monday", "last Friday"])
    shut0 = r.randint(10, span - 20)
    shutdown = [day(shut0 + i) for i in range(r.randint(4, 8))]
    short = None
    if level >= 8:   # a four-day-week month inside the project
        m = day(r.randint(span // 5, span // 2))
        short = (m.year, m.month)

    def regular(d: dt.date) -> bool:   # a working day before holidays
        if d.weekday() >= (4 if short == (d.year, d.month) else 5) or d in shutdown:
            return False
        if month_rule == "first Monday" and d.weekday() == 0 and d.day <= 7:
            return False
        if month_rule == "last Friday" and d.weekday() == 4 and (d + dt.timedelta(days=7)).month != d.month:
            return False
        return True

    hol, observed = [], {}
    n_hol, n_weekend = (6, 0) if level == 7 else (8, 3)
    while len(hol) < n_hol:
        h = day(r.randint(1, span))
        weekend = len([x for x in hol if x.weekday() >= 5]) < n_weekend
        if (h.weekday() >= 5) != weekend or h in hol or h in observed.values() or h in shutdown:
            continue
        obs = h - dt.timedelta(days=1) if h.weekday() == 5 else h + dt.timedelta(days=1) if h.weekday() == 6 else h
        if obs != h and (obs in observed.values() or obs in hol or not regular(obs)):
            continue   # a moved holiday always lands on an otherwise working day: no chains of moves to argue about
        hol.append(h)
        observed[h] = obs
    hol.sort()
    off = set(observed.values())

    def works(d: dt.date) -> bool:
        return regular(d) and d not in off

    def after(d: dt.date, k: int, include: bool) -> dt.date:   # the k-th working day after d (d itself counts if include)
        d = d if include else d + dt.timedelta(days=1)
        while True:
            if works(d):
                k -= 1
                if k == 0:
                    return d
            d += dt.timedelta(days=1)

    end_a = after(start, a_n, False)
    start_b = after(end_a + dt.timedelta(days=gap), 1, True)
    end_b = after(start_b, b_n, True)
    fmt = lambda d: f"{d:%B} {d.year}"
    if level >= 8:
        release = end_b + dt.timedelta(days=r.randint(-3, 8))
        start_c = after(max(end_b + dt.timedelta(days=1), release), 1, True)
        end_c = after(start_c, c_n, True)
        cm = short
    else:
        cm = (end_b.year, end_b.month)
    m0 = dt.date(cm[0], cm[1], 1)
    count = sum(works(m0 + dt.timedelta(days=i)) for i in range(31) if (m0 + dt.timedelta(days=i)).month == cm[1])
    expected = [end_a.isoformat(), start_b.isoformat(), end_b.isoformat()] + ([end_c.isoformat()] if level >= 8 else []) + [str(count)]

    rules = [f"Working days are Monday to Friday, except these public holidays: {', '.join(h.isoformat() for h in hol)}."]
    if level >= 8:
        rules.append("A holiday that falls on a Saturday is taken on the Friday before it instead, one that falls on a Sunday "
                     "on the Monday after it.")
        rules.append(f"In {fmt(m0)} the team works Monday to Thursday only.")
    rules.append(f"The office is closed from {shutdown[0].isoformat()} to {shutdown[-1].isoformat()} (inclusive).")
    rules.append(f"The team never works on the {month_rule} of any month.")
    phases = (f"Phase A needs {a_n} working days, counting from the day AFTER the start date. Phase B starts on the first "
              f"working day that is at least {gap} calendar days after the last day of phase A (e.g. 3 calendar days after the "
              f"10th is the 13th) and needs {b_n} working days, its first day included.")
    if level >= 8:
        phases += (f" Phase C starts on the first working day after the last day of phase B, but not before {release.isoformat()}, "
                   f"and needs {c_n} working days, its first day included.")
    qs = ["On which date does phase A end (its last working day)?", "On which date does phase B start?",
          "On which date does phase B end?"] + (["On which date does phase C end?"] if level >= 8 else [])
    qs.append(f"How many working days does {fmt(m0)} have in total under these rules (the whole month, not only project days)?")
    prompt = (f"A project starts on {start.isoformat()} ({start.strftime('%A')}). " + " ".join(rules) + "\n" + phases
              + "\nQuestions (dates as YYYY-MM-DD):\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs)) + _instr(len(qs)))
    checks = [_date_check(e) for e in expected[:-1]] + [_num_check(count, 0)]
    return Item(f"{BLOCK}.dates.L{level}.{seed}", BLOCK, "dates", [{"role": "user", "content": prompt}], multi_check(checks),
                meta={"expected": expected, "level": level})


_REL = {"same": lambda a, b: a == b, "diff": lambda a, b: a != b, "next": lambda a, b: abs(a - b) == 1,
        "left": lambda a, b: a == b - 1, "before": lambda a, b: a < b, "gap": lambda a, b: abs(a - b) == 2}


def _zebra_count(n: int, cats: int, clues: list, limit: int = 2) -> int:
    """Number of solutions (up to `limit`) of a houses puzzle. Entity (category c, value v) has index c*n+v and a bitmask
    domain of houses; binary clues propagate through lookup tables (domain of one side -> houses the other side can
    take), 3-4 entity clues by enumeration, and every category is all-different (naked and hidden singles). Search
    branches on the smallest domain."""
    full, n_ent = (1 << n) - 1, n * cats
    dom0, binary, nary = [full] * n_ent, [], []
    ix = lambda e: e[0] * n + e[1]
    for cl in clues:
        t = cl[0]
        if t == "pos":
            dom0[ix(cl[1])] &= 1 << cl[2]
        elif t == "notpos":
            dom0[ix(cl[1])] &= full ^ (1 << cl[2])
        elif t == "end":
            dom0[ix(cl[1])] &= 1 | 1 << (n - 1)
        elif t in _REL:
            f = _REL[t]
            sup = [sum(1 << q for q in range(n) if f(p, q)) for p in range(n)]      # house of the 1st -> houses of the 2nd
            supt = [sum(1 << p for p in range(n) if f(p, q)) for q in range(n)]     # house of the 2nd -> houses of the 1st
            for_1st, for_2nd = [0] * (1 << n), [0] * (1 << n)                      # domain of one side -> support on the other
            for m in range(1, 1 << n):
                low = m & -m
                for_1st[m] = for_1st[m ^ low] | supt[low.bit_length() - 1]
                for_2nd[m] = for_2nd[m ^ low] | sup[low.bit_length() - 1]
            binary.append((ix(cl[1]), ix(cl[2]), for_1st, for_2nd))
        elif t == "xor":   # (A == B) != (C == D), four different entities
            nary.append(("xor", [ix(e) for e in cl[1][1:] + cl[2][1:]]))
        else:              # A strictly between B and C
            nary.append(("between", [ix(e) for e in cl[1:]]))

    def between_mask(lo: int, hi: int) -> int:   # houses strictly between lo and hi
        return ((1 << hi) - 1) & ~((1 << (lo + 1)) - 1) if hi > lo + 1 else 0

    def revise(kind: str, ds: list) -> list:
        """Exact supports of a 3-4 entity clue as domain masks."""
        if kind == "xor":
            a, b, c, d = ds
            out = []
            for x, y, u, v in ((a, b, c, d), (b, a, c, d), (c, d, a, b), (d, c, a, b)):
                eq_other = bool(u & v)                        # the other pair can be equal
                ne_other = not (u == v and u & (u - 1) == 0)  # the other pair can differ
                out.append(((x & y) if ne_other else 0) | ((x & ~y if y & (y - 1) == 0 else x) if eq_other else 0))
            return out
        a, b, c = ds
        lo = lambda m: (m & -m).bit_length() - 1
        hi = lambda m: m.bit_length() - 1
        na = a & (between_mask(lo(b), hi(c)) | between_mask(lo(c), hi(b)))
        nb = nc = 0
        for p in range(n):
            if b >> p & 1 and (na & between_mask(p, hi(c)) or na & between_mask(lo(c), p)):
                nb |= 1 << p
            if c >> p & 1 and (na & between_mask(p, hi(b)) or na & between_mask(lo(b), p)):
                nc |= 1 << p
        return [na, nb, nc]

    def propagate(dom: list) -> bool:
        changed, seen = True, {}
        while changed:
            changed = False
            for i, j, for_1st, for_2nd in binary:
                di, dj = dom[i], dom[j]
                ni = di & for_1st[dj]
                nj = dj & for_2nd[ni]
                if not ni or not nj:
                    return False
                if ni != di or nj != dj:
                    dom[i], dom[j], changed = ni, nj, True
            for ci, (kind, es) in enumerate(nary):
                key = tuple(dom[e] for e in es)
                if seen.get(ci) == key:
                    continue   # nothing changed since this clue was last revised
                new = revise(kind, list(key))
                if kind == "between":   # revise once more: the new A domain can cut B and C further
                    new = revise(kind, new) if all(new) else new
                for k, e in enumerate(es):
                    if not new[k]:
                        return False
                    if new[k] != dom[e]:
                        dom[e], changed = new[k], True
                seen[ci] = tuple(dom[e] for e in es)
            for base in range(0, n_ent, n):   # all-different: houses taken by fixed entities, houses only one entity can take
                fixed = 0
                for e in range(base, base + n):
                    d = dom[e]
                    if d & (d - 1) == 0:
                        if fixed & d:
                            return False
                        fixed |= d
                once = twice = 0
                for e in range(base, base + n):
                    d = dom[e]
                    if d & (d - 1) and d & fixed:
                        d &= ~fixed
                        if not d:
                            return False
                        dom[e], changed = d, True
                    twice |= once & d
                    once |= d
                if once != full:
                    return False
                single = once & ~twice
                if single:
                    for e in range(base, base + n):
                        h = dom[e] & single
                        if h and dom[e] != h:
                            if h & (h - 1):
                                return False
                            dom[e], changed = h, True
        return True

    def search(dom: list) -> int:
        if not propagate(dom):
            return 0
        best, bk = -1, n + 1
        for e in range(n_ent):
            k = bin(dom[e]).count("1")
            if 1 < k < bk:
                best, bk = e, k
        if best < 0:
            return 1
        total, m = 0, dom[best]
        while m and total < limit:
            b = m & -m
            m ^= b
            nd = list(dom)
            nd[best] = b
            total += search(nd)
        return total
    return search(dom0)


_PETS = ["cat", "dog", "parrot", "fish", "rabbit", "turtle", "hamster"]


def _logic_hard(seed: int, level: int) -> Item:
    """Houses in a row with four categories (name, drink, city, pet): 5 houses at level 7, 6 at level 8. Clues are mostly
    relational (next to, directly / somewhere left of, one house between; level 8 adds 'between' and either-or-but-not-
    both), chosen at random until the solution is unique and then pruned to a minimal set: every clue is needed.
    4-5 questions about different houses."""
    r = rng(BLOCK, f"logic{level}", seed)
    n, vals, house, chosen = _zebra_puzzle(r, level)

    def subj(e, cap=True):
        x = vals[e[0]][e[1]]
        s = [x, f"the {x} drinker", f"the person from {x}", f"the {x} owner"][e[0]]
        return s[0].upper() + s[1:] if cap else s

    def pred(e, neg=False):
        x = vals[e[0]][e[1]]
        return [f"is {'not ' if neg else ''}{x}", f"{'does not drink' if neg else 'drinks'} {x}",
                f"is {'not ' if neg else ''}from {x}", f"{'does not own' if neg else 'owns'} the {x}"][e[0]]

    def text(cl) -> str:
        t = cl[0]
        if t == "same":
            return f"{subj(cl[1])} {pred(cl[2])}."
        if t == "diff":
            return f"{subj(cl[1])} {pred(cl[2], True)}."
        if t == "next":
            return f"{subj(cl[1])} lives next to {subj(cl[2], False)}."
        if t == "left":
            return f"{subj(cl[1])} lives directly to the left of {subj(cl[2], False)}."
        if t == "before":
            return f"{subj(cl[1])} lives somewhere to the left of {subj(cl[2], False)}."
        if t == "gap":
            return f"There is exactly one house between {subj(cl[1], False)} and {subj(cl[2], False)}."
        if t == "pos":
            return f"{subj(cl[1])} lives in house {cl[2] + 1}."
        if t == "notpos":
            return f"{subj(cl[1])} does not live in house {cl[2] + 1}."
        if t == "end":
            return f"{subj(cl[1])} lives in one of the two end houses."
        if t == "xor":
            return (f"Either {subj(cl[1][1], False)} {pred(cl[1][2])}, or {subj(cl[2][1], False)} {pred(cl[2][2])}, "
                    f"but not both.")
        return f"{subj(cl[1])} lives somewhere between {subj(cl[2], False)} and {subj(cl[3], False)}."

    r.shuffle(chosen)
    at = {(c, p): v for (c, v), p in house.items()}   # (category, house) -> value
    qs, expected, checks = [], [], []
    for p in r.sample(range(n), 4 if level == 7 else 5):
        c_from, c_to = r.sample(range(4), 2)
        e = (c_from, at[(c_from, p)])
        if r.random() < 0.2:
            qs.append(f"In which house does {subj(e, False)} live?")
            expected.append(str(p + 1))
            checks.append(_num_check(p + 1, 0))
            continue
        ans = vals[c_to][at[(c_to, p)]]
        if c_to == 0:
            q = f"Who {pred(e)}?"
        else:
            q = {1: f"What does {subj(e, False)} drink?", 2: f"Which city is {subj(e, False)} from?",
                 3: f"Which pet does {subj(e, False)} own?"}[c_to]
        qs.append(q[0].upper() + q[1:] + " (one word)")
        expected.append(ans)
        checks.append(_str_check(ans))
    intro = (f"{n} friends live in {n} houses in a row, numbered 1 to {n} from left to right, one per house. Each has a "
             f"different name ({', '.join(vals[0])}), drinks a different beverage ({', '.join(vals[1])}), comes from a "
             f"different city ({', '.join(vals[2])}) and owns a different pet ({', '.join(vals[3])}). 'Next to' means in a "
             f"neighbouring house; 'to the left of' means in a lower-numbered house.")
    prompt = (intro + "\nClues:\n" + "\n".join(f"{i + 1}. {text(c)}" for i, c in enumerate(chosen)) + "\nQuestions:\n"
              + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs)) + _instr(len(qs)))
    return Item(f"{BLOCK}.logic.L{level}.{seed}", BLOCK, "logic", [{"role": "user", "content": prompt}], multi_check(checks),
                meta={"expected": expected, "clues": len(chosen), "level": level})


def _zebra_puzzle(r, level: int):
    """The hidden solution and a minimal clue set for _logic_hard: (n, values per category, house of each entity, clues)."""
    n = 5 if level == 7 else 6
    vals = [r.sample(["Ana", "Ben", "Carla", "Dmitri", "Elif", "Farid", "Greta", "Hugo", "Iris", "Jonas"], n),
            r.sample(["tea", "coffee", "juice", "water", "cocoa", "milk", "kefir"], n),
            r.sample(["Lisbon", "Oslo", "Kyiv", "Lima", "Quito", "Riga", "Tunis"], n), r.sample(_PETS, n)]
    house = {(c, v): p for c in range(4) for v, p in enumerate(r.sample(range(n), n))}
    ents = sorted(house)
    pool = {"easy": [], "mid": [], "hard": []}
    same, other = [], []
    for e1, e2 in itertools.combinations(ents, 2):
        if e1[0] == e2[0]:
            continue
        a, b = house[e1], house[e2]
        if a == b:
            pool["easy" if e1[0] == 0 else "mid"].append(("same", e1, e2))
            same.append(("same", e1, e2))
            continue
        other.append(("same", e1, e2))
        pool["mid"].append(("diff", e1, e2))
        if abs(a - b) == 1:
            pool["hard"].append(("next", e1, e2))
            pool["hard"].append(("left", e1, e2) if a < b else ("left", e2, e1))
        pool["hard"].append(("before", e1, e2) if a < b else ("before", e2, e1))
        if abs(a - b) == 2:
            pool["hard"].append(("gap", e1, e2))
    for e in ents:
        pool["easy"].append(("pos", e, house[e]))
        pool["mid"].append(("notpos", e, r.choice([k for k in range(n) if k != house[e]])))
        if house[e] in (0, n - 1):
            pool["mid"].append(("end", e))
    if level >= 8:
        for _ in range(40):
            t, f = r.choice(same), r.choice(other)
            if len({t[1], t[2], f[1], f[2]}) == 4:
                pool["hard"].append(("xor", t, f) if r.random() < 0.5 else ("xor", f, t))
        for _ in range(40):
            e1, e2, e3 = r.sample(ents, 3)
            if min(house[e2], house[e3]) < house[e1] < max(house[e2], house[e3]):
                pool["hard"].append(("between", e1, e2, e3))
    weights = {"hard": 0.55, "mid": 0.3, "easy": 0.15} if level == 7 else {"hard": 0.7, "mid": 0.25, "easy": 0.05}
    tiers = {k: r.sample(v, len(v)) for k, v in pool.items()}
    chosen = []
    while True:
        ks = [k for k in ("hard", "mid", "easy") if tiers[k]]
        chosen.append(tiers[r.choices(ks, [weights[k] for k in ks])[0]].pop())
        if len(chosen) >= 2 * n and _zebra_count(n, 4, chosen) == 1:
            break
    cands = r.sample(chosen, len(chosen))
    cands.sort(key=lambda cl: 0 if cl[0] in ("pos",) or (cl[0] == "same" and cl[1][0] == 0) else 1)   # easy clues go first
    for cl in cands:
        trial = [x for x in chosen if x is not cl]
        if _zebra_count(n, 4, trial) == 1:
            chosen = trial

    return n, vals, house, chosen


def _code_trace_hard(seed: int, level: int) -> Item:
    """A program in phases, each printing a line: a three-mode state machine (floor division and modulo of negatives),
    a pointer walk until it revisits an index, grouping + sorting with a compound key, list aliasing through +=, and a
    memoized recursion; level 8 adds the state machine's trail checksum, a string-sorted key and late-binding closures
    with for-else. 6 (level 7) or 8 (level 8) printed lines, credit per line."""
    r = rng(BLOCK, f"code_trace{level}", seed)
    n = 14 if level == 7 else 16
    while True:
        data = [r.randint(-9, 25) for _ in range(n)]
        k1, k2, k3, k4 = r.randint(3, 5), r.randint(2, 4), r.randint(4, 7), r.randint(3, 5)
        t1, t2, a0 = r.randint(-3, 3), r.randint(40, 90), r.randint(-5, 5)
        mul, j0, s1, st, k5 = r.randint(2, 5), r.randint(0, n - 1), r.randint(0, 2), r.randint(2, 3), r.randint(3, 8)
        k6, c6, t3 = r.randint(3, 5), r.randint(1, 9), r.randint(10, 24)
        key3 = "lambda g: (str(sum(groups[g])), -g)" if level >= 8 else "lambda g: (-len(groups[g]), sum(groups[g]), g)"
        body = ["def f(xs):",
                f"    mode, acc, trail = 0, {a0}, []",
                "    for i, x in enumerate(xs):",
                "        if mode == 0:",
                f"            acc += x if x % {k1} else -x",
                f"            if x < {t1}:",
                "                mode = 1",
                "        elif mode == 1:",
                f"            acc -= x // {k2}",
                "            mode = 2 if x % 2 else 0",
                "        else:",
                f"            acc += x % {k3} * i",
                f"            if acc > {t2}:",
                "                acc //= 2",
                "                mode = 0",
                "        trail.append(mode)",
                "    print(acc)"]
        if level >= 8:
            body += ["    print(sum((i + 1) * m for i, m in enumerate(trail)))"]
        body += ["    n, j, seen, total = len(xs), " + str(j0) + ", [], 0",
                 "    while j not in seen:",
                 "        seen.append(j)",
                 "        total += xs[j]",
                 f"        j = (j * {mul} + xs[j]) % n",
                 "    print(len(seen))",
                 "    print(total)",
                 "    groups = {}",
                 "    for x in xs:",
                 f"        groups.setdefault(x % {k4}, []).append(x)",
                 f"    order = sorted(groups, key={key3})",
                 "    print(sum(g * (rank + 1) for rank, g in enumerate(order)))",
                 f"    a = xs[{s1}::{st}]",
                 "    b = a",
                 "    b += [acc % 10, total % 10]",
                 "    c = sorted(a, key=lambda v: (v % 3, -v))[:4]",
                 "    print(len(a) * 100 + sum(c))"]
        if level >= 8:
            body += [f"    fns = [lambda v: v * k + {c6} for k in range({k6})]",
                     "    s = sum(fn(i) for i, fn in enumerate(fns))",
                     "    for v in xs[::-1]:",
                     f"        if v > {t3} and v % 2 == 0:",
                     "            s += v",
                     "            break",
                     "    else:",
                     "        s -= 100",
                     "    print(s)"]
        body += ["    memo = {}",
                 "",
                 "    def h(m):",
                 "        if m < 3:",
                 "            return m + 1",
                 "        if m not in memo:",
                 "            memo[m] = h(m - 1) + (h(m // 3) if m % 2 else -h(m - 2))",
                 "        return memo[m]",
                 f"    return h(len(seen) + {k5})",
                 "",
                 f"print(f({data}))"]
        src = "\n".join(body) + "\n"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(src, {})  # our own generated code, not model output
        expected = [int(x) for x in buf.getvalue().split()]
        if max(abs(x) for x in expected) < 10 ** 6:
            break
    prompt = (f"What does this Python program print? It prints {len(expected)} lines; trace it carefully and give every line."
              f"\n\n```python\n{src}```" + _instr(len(expected)).replace("per question", "per printed line, in order"))
    return Item(f"{BLOCK}.code_trace.L{level}.{seed}", BLOCK, "code_trace", [{"role": "user", "content": prompt}],
                multi_check([_num_check(e, 0.0) for e in expected]), meta={"expected": expected, "level": level})


def _table_hard(seed: int, level: int) -> Item:
    """Customers, orders in three currencies, refunds and exchange rates: filter (two regions, a month range, cancelled
    orders out), net each order by its refunds, convert to EUR with rounding per order, then count / sum / rank; level 8
    has refunds in their own currency and ranks all regions too. 4 (level 7) or 6 (level 8) results."""
    from decimal import Decimal as D
    r = rng(BLOCK, f"table{level}", seed)
    regions = ["North", "South", "East", "West"]
    n_c, n_o, n_r = (12, 44, 9) if level == 7 else (14, 52, 12)
    custs = {f"K{10 + i}": (r.choice(regions), r.choice(["retail", "wholesale"])) for i in range(n_c)}
    rate = {"EUR": D("1"), "USD": D(r.choice(["0.85", "0.9", "0.92"])), "GBP": D(r.choice(["1.15", "1.17", "1.2"]))}
    orders = [(f"R-{100 + i}", r.choice(sorted(custs)), r.randint(1, 12), D(r.randint(5000, 90000)) / 100,
               r.choice(["EUR", "EUR", "USD", "GBP"]), r.choice(["paid"] * 5 + ["cancelled"])) for i in range(n_o)]
    refunds = []
    for i in range(n_r):
        o = r.choice(orders)
        cur = r.choice(["EUR", "USD", "GBP"]) if level >= 8 else o[4]
        refunds.append((f"F-{i + 1}", o[0], _cents(o[3] * D(r.randint(5, 45)) / 100), cur))
    sel_regions = sorted(r.sample(regions, 2))
    m1 = r.randint(1, 6)
    m2 = m1 + r.randint(4, 6)

    def net_eur(o) -> D:
        refs = [f for f in refunds if f[1] == o[0]]
        if level >= 8:
            return _cents(o[3] * rate[o[4]] - sum((f[2] * rate[f[3]] for f in refs), D(0)))
        return _cents((o[3] - sum((f[2] for f in refs), D(0))) * rate[o[4]])

    live = [o for o in orders if o[5] == "paid" and m1 <= o[2] <= m2]
    sel = [o for o in live if custs[o[1]][0] in sel_regions]
    per, per_region = {}, {}
    for o in sel:
        per[o[1]] = per.get(o[1], 0) + net_eur(o)
    for o in live:
        per_region[custs[o[1]][0]] = per_region.get(custs[o[1]][0], 0) + net_eur(o)
    top = sorted(per.items(), key=lambda kv: -kv[1])
    top_r = sorted(per_region.items(), key=lambda kv: -kv[1])
    if len(sel) < 6 or len(top) < 2 or top[0][1] == top[1][1] or len(top_r) < 2 or top_r[0][1] == top_r[1][1]:
        return _table_hard(seed + 10_000, level)
    expected = [len(sel), float(sum(per.values())), top[0][0], float(top[0][1])]
    checks = [_num_check(len(sel), 0), _num_check(expected[1], 0.011), _str_check(top[0][0]), _num_check(expected[3], 0.011)]
    if level >= 8:
        expected += [top_r[0][0], float(top_r[0][1])]
        checks += [_str_check(top_r[0][0]), _num_check(expected[5], 0.011)]
    ctab = "customer | region | segment\n" + "\n".join(f"{k} | {v[0]} | {v[1]}" for k, v in custs.items())
    otab = "order | customer | month | amount | currency | status\n" + "\n".join(
        f"{a} | {b} | {c} | {d:.2f} | {e} | {f}" for a, b, c, d, e, f in orders)
    ftab = ("refund | order | amount" + (" | currency" if level >= 8 else "") + "\n"
            + "\n".join(f"{a} | {b} | {c:.2f}" + (f" | {d}" if level >= 8 else "") for a, b, c, d in refunds))
    rtab = "currency | EUR per unit\n" + "\n".join(f"{k} | {v}" for k, v in rate.items())
    net = ("An order's net amount in EUR is its amount converted to EUR minus each of its refunds converted to EUR (refunds "
           "are in the currency given in their row), rounded to cents." if level >= 8 else
           "An order's net amount is its amount minus all its refunds (refunds are in the order's currency), converted to "
           "EUR and rounded to cents.")
    cond = (f"Consider orders of customers in the {sel_regions[0]} or {sel_regions[1]} region, with month {m1}-{m2} "
            f"(inclusive), and not cancelled (cancelled orders and their refunds are ignored).")
    qs = ["How many orders meet these conditions?", "What is their total net amount in EUR?",
          "Which customer has the highest total net amount in EUR among them? (customer id)",
          "What is that customer's total net amount in EUR?"]
    if level >= 8:
        qs += ["Now take ALL regions (same months, cancelled orders still ignored): which region has the highest total net "
               "amount in EUR? (region name only)", "What is that region's total net amount in EUR?"]
    prompt = (f"Customers:\n{ctab}\n\nOrders:\n{otab}\n\nRefunds:\n{ftab}\n\nExchange rates:\n{rtab}\n\n{net} {cond}\n"
              "Questions:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs)) + "\nGive amounts to 2 decimals."
              + _instr(len(qs)))
    return Item(f"{BLOCK}.table.L{level}.{seed}", BLOCK, "table", [{"role": "user", "content": prompt}], multi_check(checks),
                meta={"expected": expected, "level": level})


class _SearchLimit(Exception):
    """The exact search needed more nodes than allowed (deterministic: a node count, not a clock)."""


def _rcpsp_min(names: list, dur: dict, pre: dict, release: dict, groups: list, cap: int, max_nodes: int = 0) -> int:
    """Exact minimum makespan: tasks need one person each (at most `cap` at a time), tasks sharing a group are mutually
    exclusive, release days are earliest starts (0-based). Some optimal schedule is active (no task can start earlier
    without moving another), and listing an active schedule's tasks by (start, index) and placing each at its earliest
    feasible time reproduces it; so a depth-first search over such lists is exact. It prunes lists that cannot end
    active (a waiting task would fit entirely before the last start) and bounds by critical path, workload per person and
    per shared resource, starting from the best of a few greedy schedules."""
    import random as _random
    n = len(names)
    ix = {t: i for i, t in enumerate(names)}
    d = [dur[t] for t in names]
    rel = [release.get(t, 0) for t in names]
    preds = [[ix[p] for p in pre[t]] for t in names]
    succs = [[j for j in range(n) if i in preds[j]] for i in range(n)]
    topo, placed_ = [], set()
    while len(topo) < n:
        for i in range(n):
            if i not in placed_ and all(p in placed_ for p in preds[i]):
                topo.append(i)
                placed_.add(i)
    tail = [0] * n
    for i in reversed(topo):
        tail[i] = d[i] + max([tail[j] for j in succs[i]] + [0])
    grp = [[g for g, members in enumerate(groups) if t in members] for t in names]
    gmem = [[ix[t] for t in members] for members in groups]
    horizon = sum(d) + max(rel) + 1
    people = [0] * horizon
    busy = [[False] * horizon for _ in groups]
    start = [-1] * n

    def earliest(i: int, lo: int) -> int:
        s = max([lo, rel[i]] + [start[p] + d[p] for p in preds[i]])
        while True:
            bad = -1
            for u in range(s + d[i] - 1, s - 1, -1):
                if people[u] >= cap or any(busy[g][u] for g in grp[i]):
                    bad = u
                    break
            if bad < 0:
                return s
            s = bad + 1

    def place(i: int, s: int, k: int) -> None:
        start[i] = s if k > 0 else -1
        for u in range(s, s + d[i]):
            people[u] += k
            for g in grp[i]:
                busy[g][u] = k > 0

    def greedy(prio: list) -> int:
        done, span = [], 0
        while len(done) < n:
            i = max((i for i in range(n) if start[i] < 0 and all(start[p] >= 0 for p in preds[i])), key=lambda i: prio[i])
            s = earliest(i, 0)
            place(i, s, 1)
            done.append((i, s))
            span = max(span, s + d[i])
        for i, s in done:
            place(i, s, -1)
        return span

    rnd = _random.Random(n * 1000 + cap)
    best = [min([greedy(tail), greedy([tail[i] + d[i] for i in range(n)])]
                + [greedy([rnd.random() for _ in range(n)]) for _ in range(20)])]
    total, nodes = sum(d), [0]

    def dfs(done: int, last_s: int, last_i: int, span: int, left: int) -> None:
        nodes[0] += 1
        if max_nodes and nodes[0] > max_nodes:
            raise _SearchLimit
        if done == (1 << n) - 1:
            best[0] = span
            return
        head = [0] * n
        lb = span
        for i in topo:
            if not done >> i & 1:
                head[i] = max([last_s, rel[i]] + [start[p] + d[p] if done >> p & 1 else head[p] + d[p] for p in preds[i]])
                lb = max(lb, head[i] + tail[i])
        committed = sum(max(0, start[j] + d[j] - last_s) for j in range(n) if done >> j & 1)
        lb = max(lb, last_s + -(-(left + committed) // cap))
        for members in gmem:
            rest = [i for i in members if not done >> i & 1]
            if rest:
                used = sum(max(0, start[j] + d[j] - last_s) for j in members if done >> j & 1)
                lb = max(lb, last_s + used + sum(d[i] for i in rest), min(head[i] for i in rest) + sum(d[i] for i in rest))
        if lb >= best[0]:
            return
        cands = []
        for i in range(n):
            if done >> i & 1 or any(not done >> p & 1 for p in preds[i]):
                continue
            s = earliest(i, 0)
            if s + d[i] <= last_s:
                return   # i fits entirely before the last start: this list cannot end in an active schedule
            if s < last_s:
                s = earliest(i, last_s)
            if (s, i) > (last_s, last_i) and s + tail[i] < best[0]:
                cands.append((s, -tail[i], i))
        for s, _, i in sorted(cands):
            if s + tail[i] >= best[0]:
                continue
            place(i, s, 1)
            dfs(done | 1 << i, s, i, max(span, s + d[i]), left - d[i])
            place(i, s, -1)
    dfs(0, 0, -1, 0, total)
    return best[0]


def _schedule_hard(seed: int, level: int) -> Item:
    """Project planning with a shared test lab (three tasks, one at a time), release days and (level 8) a second shared
    resource and 12 tasks: the minimum number of days with unlimited people, with the team of 3 and with 2 people. Exact
    by branch and bound over active schedules."""
    r = rng(BLOCK, f"schedule{level}", seed)
    n = 10 if level == 7 else 12
    for _ in range(30):   # redraw until the team of 3 is slower than unlimited people (else question 2 repeats question 1)
        names = r.sample(TASKS, n)
        dur = {t: r.randint(1, 8) for t in names}
        pre = {t: sorted(r.sample(names[:i], min(i, r.choice([0, 0, 1, 1, 2])))) for i, t in enumerate(names)}
        short = [t for t in names if dur[t] <= 5]
        lab = r.sample(short, 3) if len(short) >= 3 else sorted(names, key=lambda t: dur[t])[:3]
        server = r.sample([t for t in names if t not in lab], 2) if level >= 8 else []
        rel = {t: r.randint(3, 9) for t in r.sample(names, 1 if level == 7 else 2)}   # earliest start, 1-based day
        groups = [lab] + ([server] if server else [])
        release = {t: k - 1 for t, k in rel.items()}
        try:   # near-partition instances take long to prove optimal: redraw them (a node budget keeps this deterministic)
            expected = [_rcpsp_min(names, dur, pre, release, groups, cap, max_nodes=20000) for cap in (n, 3, 2)]
        except _SearchLimit:
            continue
        if expected[0] < expected[1]:
            break
    lines = [f"- {t}: {dur[t]} day{'s' if dur[t] > 1 else ''}" + (f", after {' and '.join(pre[t])}" if pre[t] else "")
             for t in r.sample(names, n)]
    extra = [f"The {lab[0]}, {lab[1]} and {lab[2]} tasks all need the test lab, which fits only one task at a time."]
    if server:
        extra.append(f"The {server[0]} and {server[1]} tasks both need the staging server, so they cannot be worked on at the "
                     "same time either.")
    extra.append("Days are numbered from 1. " + " ".join(f"The {t} task cannot start before day {k}." for t, k in rel.items()))
    prompt = (f"A team of 3 people must complete these tasks:\n" + "\n".join(lines) + "\n\nEach task is done by one person "
              "from start to finish, takes the given number of full days and cannot be split or shared. A task can start only "
              "when all tasks it comes after are finished. A person works on one task at a time. " + " ".join(extra)
              + "\n1. With enough people to staff every task at once, what is the minimum number of days needed to finish all "
              "tasks?\n2. With the team of 3, what is the minimum number of days?\n3. If one of the three is away for the whole "
              "project and only 2 people work on it, what is the minimum number of days?" + _instr(3))
    return Item(f"{BLOCK}.schedule.L{level}.{seed}", BLOCK, "schedule", [{"role": "user", "content": prompt}],
                multi_check([_num_check(e, 0) for e in expected]), meta={"expected": expected, "level": level})


def _budget_hard(seed: int, level: int) -> Item:
    """Portfolio choice with requires / excludes / at-least-one / at-most-two-of rules, a people cap and value synergies;
    level 8 adds more projects and a cost that drops when another project is funded. Three results: the best value, the
    best value with a larger budget, the cheapest cost that reaches the best value. Exact by enumerating all subsets."""
    r = rng(BLOCK, f"budget{level}", seed)
    n = 15 if level == 7 else 17
    ids = [chr(65 + i) for i in range(n)]
    cost = [r.randint(5, 40) for _ in ids]
    value = [r.randint(10, 90) for _ in ids]
    ppl = [r.randint(1, 4) for _ in ids]
    cap_money = int(sum(cost) * r.uniform(0.38, 0.5))
    cap_ppl = int(sum(ppl) * 0.45)
    extra = r.choice([15, 20, 25])
    pairs = [tuple(r.sample(range(n), 2)) for _ in range(40)]
    seen, uniq = set(), []
    for a, b in pairs:
        if frozenset((a, b)) not in seen:
            seen.add(frozenset((a, b)))
            uniq.append((a, b))
    n_req, n_ex, n_syn = (5, 4, 1) if level == 7 else (6, 5, 2)
    reqs, excl = uniq[:n_req], uniq[n_req:n_req + n_ex]
    syn = [(a, b, r.choice([10, 15, 20, 25])) for a, b in uniq[n_req + n_ex:n_req + n_ex + n_syn]]
    disc = [(a, b, r.choice([4, 6, 8])) for a, b in uniq[n_req + n_ex + n_syn:n_req + n_ex + n_syn + 1]] if level >= 8 else []
    disc = [(a, b, min(k, cost[b] - 1)) for a, b, k in disc]
    one_of = r.sample(range(n), 3)
    at_most = r.sample(range(n), 4)
    bit = lambda i: 1 << i
    req_m = [(bit(a), bit(b)) for a, b in reqs]
    ex_m = [bit(a) | bit(b) for a, b in excl]
    syn_m = [(bit(a) | bit(b), v) for a, b, v in syn]
    disc_m = [(bit(a) | bit(b), k) for a, b, k in disc]
    one_m = sum(bit(i) for i in one_of)
    most_m = sum(bit(i) for i in at_most)
    N = 1 << n
    c_, v_, p_ = [0] * N, [0] * N, [0] * N
    results = []
    for m in range(1, N):
        low = m & -m
        i = low.bit_length() - 1
        prev = m ^ low
        c_[m], v_[m], p_[m] = c_[prev] + cost[i], v_[prev] + value[i], p_[prev] + ppl[i]
    for m in range(N):
        if p_[m] > cap_ppl or not m & one_m or bin(m & most_m).count("1") > 2:
            continue
        if any(m & a and not m & b for a, b in req_m) or any(m & e == e for e in ex_m):
            continue
        c = c_[m] - sum(k for mm, k in disc_m if m & mm == mm)
        if c > cap_money + extra:
            continue
        v = v_[m] + sum(k for mm, k in syn_m if m & mm == mm)
        results.append((v, c))
    if not any(c <= cap_money for _v, c in results):   # no feasible portfolio (never seen): another draw
        return _budget_hard(seed + 10_000, level)
    best1 = max(v for v, c in results if c <= cap_money)
    best2 = max(v for v, c in results)
    cheapest = min(c for v, c in results if v == best1 and c <= cap_money)
    expected = [best1, best2, cheapest]
    rows = "\n".join(f"- {p}: cost {cost[i]}k EUR, value {value[i]}, needs {ppl[i]} {'person' if ppl[i] == 1 else 'people'}"
                     for i, p in enumerate(ids))
    rules = ([f"{ids[a]} can only be funded if {ids[b]} is funded too" for a, b in reqs]
             + [f"{ids[a]} and {ids[b]} cannot both be funded" for a, b in excl]
             + [f"at least one of {', '.join(ids[i] for i in one_of)} must be funded",
                f"at most two of {', '.join(ids[i] for i in at_most)} can be funded"]
             + [f"if both {ids[a]} and {ids[b]} are funded, together they are worth {v} more (they share a platform)" for a, b, v in syn]
             + [f"if {ids[a]} is funded, {ids[b]} costs {k}k EUR less (it reuses {ids[a]}'s hardware)" for a, b, k in disc])
    prompt = (f"A company can fund some of these projects:\n{rows}\n\nThe total budget is {cap_money}k EUR and only {cap_ppl} "
              "people are available (each funded project needs its people full-time). Rules:\n" + "\n".join(f"- {x}" for x in rules)
              + "\nQuestions:\n1. What is the maximum total value that can be funded?\n2. If the budget were "
              f"{extra}k EUR larger (all other rules unchanged), what would the maximum total value be?\n3. With the original "
              "budget, what does a combination with the maximum total value from question 1 cost in total? If several "
              "combinations reach that value, give the lowest cost among them." + _instr(3))
    return Item(f"{BLOCK}.budget.L{level}.{seed}", BLOCK, "budget", [{"role": "user", "content": prompt}],
                multi_check([_num_check(e, 0) for e in expected]), meta={"expected": expected, "level": level})


KINDS = {"arith": arith, "dates": dates, "logic": logic, "code_trace": code_trace, "table": table, "schedule": schedule, "budget": budget}
# the quick tier keeps the kinds that still discriminate at level 5 (v0.5: Tiel solved every older kind at level 5)
QUICK = ["arith", "logic", "code_trace", "schedule", "budget"]
