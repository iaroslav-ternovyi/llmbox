"""Reasoning levels 9-10 (v0.11): aimed at the frontier. Levels 7-8 were solved by the frontier in seconds; these need a
systematic procedure over a space too big to eyeball (houses puzzles whose arrangements must be counted, scheduling with
skills and speeds, portfolios with near-optimal combinations to count), long state evolution where one slip propagates
(Python traces with semantics traps and a ledger loop), and exact calendar arithmetic across DST changes of several
zones. Every item asks several questions (credit per answer), so a slip costs one answer, not the item. Measured on Opus
5.5 (default effort, 2026-09-29, one item each): logic L10 0.4, schedule L10 0.75, budget L10 0.8; arith L10 0.875 (its
level-10 design before this pass is the current one); dates, table, code_trace L10 1.0.

Called from reasoning.py at level >= 9 (levels 1-8 stay as they were).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import itertools
import re
import warnings

from .common import Item, final_answer, multi_check, num, rng
from .reasoning import (BLOCK, TASKS, _PETS, _REL, _SearchLimit, _cents, _instr, _num_check, _rcpsp_min, _str_check,
                        _zebra_count)


def _norm(s: str | None) -> str:
    s = (s or "").strip().strip("`*").strip()
    s = s[:-1] if s.endswith(".") and not s.endswith("..") else s
    return re.sub(r"\s+", "", s).replace('"', "'")


def _repr_check(expected: str):
    """A printed Python value, compared as text without whitespace ("[1, 2]" == "[1,2]"; quotes ' and " alike)."""
    def check(text: str, _t=None) -> float:
        return 1.0 if _norm(final_answer(text)) == _norm(expected) else 0.0
    return check


def _utc_check(expected: str):
    """The first YYYY-MM-DDTHH:MM in the answer is the instant (a trailing Z or seconds do not matter)."""
    def check(text: str, _t=None) -> float:
        m = re.search(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", final_answer(text) or "")
        return 1.0 if m and f"{m.group(1)}T{m.group(2)}Z" == expected else 0.0
    return check


# ---------------------------------------------------------------- logic: puzzles with several arrangements - count them

def _zebra_counting(r, level: int):
    """A hidden arrangement and relational clues (all true in it) that leave `lo`-`hi` arrangements, pruned so that no
    clue can go without changing the count. Returns (n, cats, values, house, clues, count)."""
    n, cats, lo, hi = (5, 4, 3, 8) if level == 9 else (6, 4, 5, 12)
    vals = [r.sample(["Ana", "Ben", "Carla", "Dmitri", "Elif", "Farid", "Greta", "Hugo", "Iris", "Jonas"], n),
            r.sample(["tea", "coffee", "juice", "water", "cocoa", "milk", "kefir"], n),
            r.sample(["Lisbon", "Oslo", "Kyiv", "Lima", "Quito", "Riga", "Tunis"], n), r.sample(_PETS, n)]
    house = {(c, v): p for c in range(cats) for v, p in enumerate(r.sample(range(n), n))}
    ents = sorted(house)
    pool, same, other = [], [], []
    for e1, e2 in itertools.combinations(ents, 2):
        if e1[0] == e2[0]:
            continue
        a, b = house[e1], house[e2]
        if a == b:
            same.append(("same", e1, e2))
            if e1[0] != 0 or r.random() < 0.3:
                pool.append(("same", e1, e2))
            continue
        other.append(("same", e1, e2))
        pool.append(("diff", e1, e2))
        if abs(a - b) == 1:
            pool += [("next", e1, e2), ("left", e1, e2) if a < b else ("left", e2, e1)]
        pool.append(("before", e1, e2) if a < b else ("before", e2, e1))
        if abs(a - b) == 2:
            pool.append(("gap", e1, e2))
    for e in ents:
        pool.append(("notpos", e, r.choice([k for k in range(n) if k != house[e]])))
        if house[e] in (0, n - 1):
            pool.append(("end", e))
    for _ in range(40):
        t, f = r.choice(same), r.choice(other)
        if len({t[1], t[2], f[1], f[2]}) == 4:
            pool.append(("xor", t, f) if r.random() < 0.5 else ("xor", f, t))
    for _ in range(40):
        e1, e2, e3 = r.sample(ents, 3)
        if min(house[e2], house[e3]) < house[e1] < max(house[e2], house[e3]):
            pool.append(("between", e1, e2, e3))
    r.shuffle(pool)
    chosen, count = [], None
    for cl in pool:
        c = _zebra_count(n, cats, chosen + [cl], limit=hi + 1)
        if c < lo:
            continue   # this clue would leave too few arrangements
        chosen.append(cl)
        if c <= hi:
            count = c
            break
    if count is None:
        return None
    for cl in r.sample(chosen, len(chosen)):
        trial = [x for x in chosen if x is not cl]
        if _zebra_count(n, cats, trial, limit=count + 1) == count:
            chosen = trial
    return n, cats, vals, house, chosen, count


def logic(seed: int, level: int) -> Item:
    """Houses in a row with names, drinks, cities and pets (5 houses at 9, 6 at 10), relational clues that leave several
    arrangements (3-8 at 9, 5-12 at 10), none of them removable. Questions count arrangements: all of them, those where
    someone lives in a given house, those where two attributes share a house, (10) those where two people are
    neighbours, and one fact that holds in every arrangement. Exact counts from the propagation solver."""
    r = rng(BLOCK, f"logic{level}", seed)
    out = None
    while out is None:
        out = _zebra_counting(r, level)
    n, cats, vals, house, chosen, total = out

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
        if t == "notpos":
            return f"{subj(cl[1])} does not live in house {cl[2] + 1}."
        if t == "end":
            return f"{subj(cl[1])} lives in one of the two end houses."
        if t == "xor":
            return (f"Either {subj(cl[1][1], False)} {pred(cl[1][2])}, or {subj(cl[2][1], False)} {pred(cl[2][2])}, "
                    f"but not both.")
        return f"{subj(cl[1])} lives somewhere between {subj(cl[2], False)} and {subj(cl[3], False)}."

    ents = sorted(house)
    cnt = lambda extra: _zebra_count(n, cats, chosen + [extra], limit=total + 1)

    def partial(make):   # a condition that holds in some arrangements but not all
        for _ in range(300):
            cl = make()
            c = cnt(cl)
            if 0 < c < total:
                return cl, c
        return cl, cnt(cl)
    r.shuffle(chosen)
    qs, expected = ["How many different arrangements satisfy all the clues?"], [total]
    e_, k_ = None, None
    cl, c = partial(lambda: ("pos", r.choice(ents), r.randrange(n)))
    qs.append(f"In how many of those arrangements does {subj(cl[1], False)} live in house {cl[2] + 1}?")
    expected.append(c)

    def pair_same():
        e1, e2 = r.sample(ents, 2)
        while e1[0] == e2[0]:
            e1, e2 = r.sample(ents, 2)
        return ("same", min(e1, e2), max(e1, e2))
    cl, c = partial(pair_same)
    qs.append(f"In how many of them is this true: {subj(cl[1], False)} {pred(cl[2])}?")
    expected.append(c)
    if level >= 10:
        cl, c = partial(lambda: ("next",) + tuple(r.sample([e for e in ents if e[0] == 0], 2)))
        qs.append(f"In how many of them does {subj(cl[1], False)} live next to {subj(cl[2], False)}?")
        expected.append(c)
    forced = [(e, k) for e in r.sample(ents, len(ents)) for k in [house[e]] if cnt(("pos", e, k)) == total
              and not any(x[0] == "end" and x[1] == e for x in chosen)]
    if forced:
        e_, k_ = forced[0]
        qs.append(f"{subj(e_)} lives in the same house in every arrangement. Which house number?")
        expected.append(k_ + 1)
    else:
        cl, c = partial(lambda: ("before",) + tuple(r.sample(ents, 2)))
        qs.append(f"In how many of them does {subj(cl[1], False)} live somewhere to the left of {subj(cl[2], False)}?")
        expected.append(c)
    intro = (f"{n} friends live in {n} houses in a row, numbered 1 to {n} from left to right, one per house. Each has a "
             f"different name ({', '.join(vals[0])}), drinks a different beverage ({', '.join(vals[1])}), comes from a "
             f"different city ({', '.join(vals[2])}) and owns a different pet ({', '.join(vals[3])}). 'Next to' means in "
             f"a neighbouring house; 'to the left of' means in a lower-numbered house; 'between' means strictly between.")
    prompt = (intro + "\nClues:\n" + "\n".join(f"{i + 1}. {text(c)}" for i, c in enumerate(chosen))
              + "\nThese clues do not determine a single arrangement: several arrangements (complete assignments of every "
              "name, drink, city and pet to the houses) satisfy all of them.\nQuestions:\n"
              + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs)) + _instr(len(qs)))
    return Item(f"{BLOCK}.logic.L{level}.{seed}", BLOCK, "logic", [{"role": "user", "content": prompt}],
                multi_check([_num_check(e, 0) for e in expected]),
                meta={"expected": expected, "clues": len(chosen), "arrangements": total, "level": level})


# ---------------------------------------------------------------- dates: SLA deadlines in business hours across DST

_OFFICES = {   # name: (zone, business periods as (start, end) local minutes)
    "Madrid": ("Europe/Madrid", [(9 * 60, 14 * 60), (15 * 60, 18 * 60)]),
    "New York": ("America/New_York", [(9 * 60, 17 * 60)]),
    "London": ("Europe/London", [(9 * 60, 12 * 60 + 30), (13 * 60 + 30, 17 * 60 + 30)]),
    "Sydney": ("Australia/Sydney", [(8 * 60 + 30, 12 * 60 + 30), (13 * 60 + 30, 16 * 60 + 30)]),
}


_HALF = {"Madrid": 14 * 60, "New York": 13 * 60, "London": 12 * 60 + 30, "Sydney": 12 * 60 + 30}   # half-day closing


def _offset_text(zone: str, lo: dt.datetime, hi: dt.datetime) -> str:
    """'UTC+1 until 2026-03-29 01:00 UTC, then UTC+2' for the transitions of zone between lo and hi (UTC)."""
    from zoneinfo import ZoneInfo
    z = ZoneInfo(zone)
    fmt = lambda off: "UTC" + ("+" if off >= dt.timedelta(0) else "-") + str(abs(int(off.total_seconds() // 3600)))
    t, off, parts = lo, lo.astimezone(z).utcoffset(), []
    parts.append(fmt(off))
    while t < hi:
        t += dt.timedelta(hours=1)
        o = t.astimezone(z).utcoffset()
        if o != off:
            parts.append(f"until {t:%Y-%m-%d %H:%M} UTC, then {fmt(o)}")
            off = o
    return " ".join(parts)


def _business(offices: list, holidays: dict, half: dict, lo: dt.date, hi: dt.date) -> list:
    """Merged UTC intervals when at least one office is open (holidays closed; a half day closes at _HALF)."""
    from zoneinfo import ZoneInfo
    iv = []
    for name in offices:
        zone, periods = _OFFICES[name]
        z = ZoneInfo(zone)
        d = lo
        while d <= hi:
            if d.weekday() < 5 and d not in holidays.get(name, ()):
                for a, b in periods:
                    if d in half.get(name, ()):
                        b = min(b, _HALF[name])
                        if b <= a:
                            break
                    s = dt.datetime(d.year, d.month, d.day, a // 60, a % 60, tzinfo=z).astimezone(dt.timezone.utc)
                    e = dt.datetime(d.year, d.month, d.day, b // 60, b % 60, tzinfo=z).astimezone(dt.timezone.utc)
                    iv.append((s, e))
            d += dt.timedelta(days=1)
    iv.sort()
    merged = []
    for s, e in iv:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _minus(intervals: list, holes: list) -> list:
    """intervals minus holes (all [start, end) in UTC)."""
    out = []
    for s, e in intervals:
        pieces = [(s, e)]
        for hs, he in holes:
            nxt = []
            for a, b in pieces:
                if he <= a or b <= hs:
                    nxt.append((a, b))
                    continue
                if a < hs:
                    nxt.append((a, hs))
                if he < b:
                    nxt.append((he, b))
            pieces = nxt
        out += pieces
    return out


def _deadline(intervals: list, opened: dt.datetime, minutes: int, pauses: list = ()) -> dt.datetime:
    intervals = _minus(intervals, list(pauses)) if pauses else intervals
    left = minutes
    for s, e in intervals:
        if e <= opened:
            continue
        s = max(s, opened)
        if (e - s).total_seconds() / 60 >= left:
            return s + dt.timedelta(minutes=left)
        left -= int((e - s).total_seconds() // 60)
    raise ValueError("window too short")


_DST_WINDOWS = {   # (offices, first day of the window): a DST change (and at level 10 a mismatch of two zones) inside it
    9: [(["Madrid"], dt.date(2026, 3, 23)), (["Madrid"], dt.date(2026, 10, 19)), (["New York"], dt.date(2026, 3, 2)),
        (["New York"], dt.date(2026, 10, 26)), (["London"], dt.date(2027, 3, 22)), (["Sydney"], dt.date(2026, 3, 30)),
        (["Sydney"], dt.date(2026, 9, 28)), (["London"], dt.date(2026, 10, 19))],
    10: [(["New York", "Madrid", "Sydney"], dt.date(2026, 3, 26)), (["Sydney", "London", "New York"], dt.date(2026, 3, 25)),
         (["Madrid", "New York", "Sydney"], dt.date(2026, 10, 1)), (["New York", "London", "Sydney"], dt.date(2026, 10, 21)),
         (["London", "Sydney", "New York"], dt.date(2027, 3, 10))],
}
_DST_WINDOWS[9] = [   # level 9: two offices in different zones, a DST change of at least one of them inside the window
    (["New York", "Madrid"], dt.date(2026, 3, 5)), (["New York", "Madrid"], dt.date(2026, 3, 23)),
    (["Madrid", "New York"], dt.date(2026, 10, 22)), (["New York", "London"], dt.date(2027, 3, 11)),
    (["London", "New York"], dt.date(2026, 10, 22)), (["Sydney", "London"], dt.date(2026, 3, 26))]


def dates(seed: int, level: int) -> Item:
    """Support tickets with an SLA in business hours, counted while at least one of two (9) or three (10) offices in
    different time zones is open (follow the sun) - lunch breaks, weekends, holidays, half days, and DST changes that the
    zones make on different dates - and not while the ticket is paused waiting for the customer. 6 (9) or 8 (10)
    independent deadlines, credit per deadline."""
    r = rng(BLOCK, f"dates{level}", seed)
    offices, day0 = r.choice(_DST_WINDOWS[level])
    days = [day0 + dt.timedelta(days=k) for k in range(14)]
    holidays, half = {}, {}
    for name in offices:
        workdays = [d for d in days[1:12] if d.weekday() < 5]
        hs = r.sample(workdays, 1)
        holidays[name] = set(hs)
        half[name] = {r.choice([d for d in workdays if d not in hs])}
    intervals = _business(offices, holidays, half, day0 - dt.timedelta(days=1), day0 + dt.timedelta(days=30))
    n_t = 6 if level == 9 else 8
    tickets = []
    for k in range(n_t):
        opened = dt.datetime(day0.year, day0.month, day0.day, tzinfo=dt.timezone.utc) + dt.timedelta(
            minutes=r.randint(0, 10 * 24 * 60))
        opened = opened.replace(minute=opened.minute - opened.minute % 5)
        sla = r.choice([8, 10, 12, 16, 20, 24, 30]) * 60 + r.choice([0, 0, 15, 30, 45])
        pauses, after = [], opened
        for _ in range(r.choice([0, 1, 1, 2])):
            ps = after + dt.timedelta(minutes=5 * r.randint(6, 12 * 24))
            after = ps + dt.timedelta(minutes=15 * r.randint(4, 80))
            pauses.append((ps, after))
        tickets.append((f"T{k + 1}", opened, sla, pauses, _deadline(intervals, opened, sla, pauses)))
    lo = dt.datetime(day0.year, day0.month, day0.day, tzinfo=dt.timezone.utc) - dt.timedelta(days=1)
    hi = lo + dt.timedelta(days=32)
    fmt_p = lambda a, b: f"{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}"
    lines = []
    for name in offices:
        zone, periods = _OFFICES[name]
        lines.append(f"- {name} office: open Monday to Friday " + " and ".join(fmt_p(a, b) for a, b in periods)
                     + f" local time. Local time is {_offset_text(zone, lo, hi)}. Closed on "
                     + " and ".join(f"{d.isoformat()} ({d:%A})" for d in sorted(holidays[name]))
                     + f"; on {next(iter(half[name])).isoformat()} it closes at {_HALF[name] // 60:02d}:{_HALF[name] % 60:02d} "
                     "(half day).")
    rule = ("The SLA clock runs whenever at least one of the offices is open (follow the sun); it stops only when all "
            "of them are closed, and while the ticket is paused (waiting for the customer), even if an office is open.")
    fmt_u = lambda x: f"{x:%Y-%m-%d %H:%M}"
    tl = "\n".join(f"- {t}: arrives {o:%Y-%m-%d %H:%M} UTC ({o:%A}), SLA {s // 60} h" + (f" {s % 60} min" if s % 60 else "")
                   + ("".join(f"; paused {fmt_u(a)} to {fmt_u(b)} UTC" for a, b in ps))
                   for t, o, s, ps, _d in tickets)
    qs = [f"When is the deadline of {t}?" for t, *_ in tickets]
    prompt = ("A support team promises answers within a number of business hours (the SLA).\n" + "\n".join(lines)
              + f"\n{rule} A ticket that arrives while no office is open starts its clock at the next opening. The deadline "
              "is the moment the clock reaches the SLA; if that is exactly the end of an opening period, the deadline is "
              f"that moment.\nTickets:\n{tl}\nQuestions (give each deadline in UTC as YYYY-MM-DDTHH:MMZ):\n"
              + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs)) + _instr(len(qs)))
    expected = [d.strftime("%Y-%m-%dT%H:%MZ") for *_x, d in tickets]
    if any(a < o for _t, o, _s, ps, _d in tickets for a, _b in ps):
        return dates(seed + 10_000, level)
    return Item(f"{BLOCK}.dates.L{level}.{seed}", BLOCK, "dates", [{"role": "user", "content": prompt}],
                multi_check([_utc_check(e) for e in expected]), meta={"expected": expected, "level": level})


# ---------------------------------------------------------------- table: prices and discounts in effect on the order date

def table(seed: int, level: int) -> Item:
    """Orders joined to a price history and a customer discount history by date (the row in effect on the order date),
    cancelled orders out, returns that only count within 30 days, per-line rounding: totals per region, the counted
    returns and the best customer (90 orders at 9); level 10 has 140 orders and a loyalty discount that depends on each
    customer's running quantity over the whole table, and asks for the best month too. 7 (9) or 8 (10) results."""
    from decimal import Decimal as D
    r = rng(BLOCK, f"table{level}", seed)
    regions = ["North", "South", "East", "West"]
    cats = r.sample(["tools", "garden", "kitchen", "office", "toys"], 3)
    prods = {f"P{i + 1}": r.choice(cats) for i in range(8)}
    y0 = dt.date(2026, 1, 1)
    day = lambda k: y0 + dt.timedelta(days=k)
    prices = []
    for p in prods:
        prices.append((p, y0, D(r.randint(500, 9000)) / 100))
        for when in sorted(r.sample(range(20, 170), r.randint(1, 2))):
            prices.append((p, day(when), D(r.randint(500, 9000)) / 100))
    custs = {f"C{10 + i}": regions[i % 4] if i < 4 else r.choice(regions) for i in range(12)}
    tiers = []
    for c in r.sample(sorted(custs), 6):
        for j, when in enumerate(sorted(r.sample(range(0, 170), r.randint(1, 2)))):
            tiers.append((c, day(when), r.choice([5, 10, 15, 20] + ([0] if j else []))))
    tiers.sort(key=lambda t: (t[1], t[0]))
    n_o = 90 if level == 9 else 140
    loyal_at = r.choice([40, 50, 60]) if level >= 10 else None
    orders = sorted([(day(r.randint(0, 180)), r.choice(sorted(custs)), r.choice(sorted(prods)), r.randint(1, 12),
                      r.choice(["paid"] * 6 + ["cancelled"])) for _ in range(n_o)])
    orders = [(f"O-{101 + i}",) + o for i, o in enumerate(orders)]
    lo, hi = day(r.randint(20, 50)), day(r.randint(130, 160))

    def in_effect(rows, key, when):
        cur = None
        for k_, d_, v_ in sorted(rows, key=lambda t: t[1]):
            if k_ == key and d_ <= when:
                cur = v_
        return cur

    before = {}   # customer's paid quantity before each order, in table order (orders are sorted by date)
    run = {}
    for o in orders:
        before[o[0]] = run.get(o[2], 0)
        if o[5] == "paid":
            run[o[2]] = run.get(o[2], 0) + o[4]

    def net(o) -> D:
        oid, when, c, p, q, _st = o
        disc = in_effect(tiers, c, when) or 0
        loyal = D(95) / 100 if loyal_at is not None and before[oid] >= loyal_at else 1
        return _cents(q * in_effect(prices, p, when) * (100 - disc) / 100 * loyal)

    returns = []
    if True:
        paid = [o for o in orders if o[5] == "paid"]
        for o in r.sample(paid, 12 if level == 9 else 16):
            returns.append((o[0], o[1] + dt.timedelta(days=r.choice([3, 10, 25, 29, 30, 31, 45])), r.randint(1, o[4])))
        returns.sort(key=lambda t: t[1])

    def refund(o) -> D:
        tot = D(0)
        for oid, when, q in returns:
            if oid == o[0] and (when - o[1]).days <= 30:
                tot += _cents(net(o) * q / o[4])
        return tot

    sel = [o for o in orders if o[5] == "paid" and lo <= o[1] <= hi]
    per_region = {g: D(0) for g in regions}
    per_cat, per_cust, per_month = {}, {}, {}
    for o in sel:
        v = net(o) - refund(o)
        per_region[custs[o[2]]] += v
        per_cat[prods[o[3]]] = per_cat.get(prods[o[3]], 0) + v
        per_cust[o[2]] = per_cust.get(o[2], 0) + v
        per_month[o[1].strftime("%Y-%m")] = per_month.get(o[1].strftime("%Y-%m"), 0) + v
    best_month = sorted(per_month.items(), key=lambda kv: -kv[1])
    best_cat = sorted(per_cat.items(), key=lambda kv: -kv[1])
    best_cust = sorted(per_cust.items(), key=lambda kv: -kv[1])
    thin = any(sum(1 for o in sel if custs[o[2]] == g) < 2 for g in regions)
    if thin or best_month[0][1] == best_month[1][1] or best_cust[0][1] == best_cust[1][1]:
        return table(seed + 10_000, level)
    expected = [float(per_region[g]) for g in regions]
    checks = [_num_check(e, 0.011) for e in expected]
    qs = [f"What is the total net revenue of customers in the {g} region?" for g in regions]
    counted = sum(1 for oid, when, q in returns for o in sel if o[0] == oid and (when - o[1]).days <= 30)
    expected += [counted, best_cust[0][0], float(best_cust[0][1])]
    checks += [_num_check(counted, 0), _str_check(best_cust[0][0]), _num_check(float(best_cust[0][1]), 0.011)]
    qs += ["How many of the returns reduce the revenue of these orders?",
           "Which customer has the highest total net revenue? (customer id only)", "What is that customer's total?"]
    if level >= 10:
        expected.append(best_month[0][0])
        checks.append(_str_check(best_month[0][0]))
        qs.append("In which month (YYYY-MM) is the total net revenue of these orders highest?")
    ptab = "product | category\n" + "\n".join(f"{p} | {c}" for p, c in prods.items())
    htab = "product | price from | unit price (EUR)\n" + "\n".join(f"{p} | {d_.isoformat()} | {v:.2f}" for p, d_, v in prices)
    ctab = "customer | region\n" + "\n".join(f"{c} | {g}" for c, g in custs.items())
    ttab = "customer | discount from | discount %\n" + "\n".join(f"{c} | {d_.isoformat()} | {v}" for c, d_, v in tiers)
    otab = "order | date | customer | product | quantity | status\n" + "\n".join(
        f"{o[0]} | {o[1].isoformat()} | {o[2]} | {o[3]} | {o[4]} | {o[5]}" for o in orders)
    rtab = ("\n\nReturns:\nreturn date | order | quantity returned\n"
            + "\n".join(f"{when.isoformat()} | {oid} | {q}" for oid, when, q in returns)) if returns else ""
    rules = ("A price or discount row applies from its date (inclusive) until the next row for the same product or customer; "
             "customers without a discount row in effect pay the full price. An order line's net amount is quantity x unit "
             "price in effect on the order date x (1 - discount in effect on the order date), rounded to cents. Cancelled "
             "orders do not count.")
    if loyal_at is not None:
        rules += (f" Loyalty: once a customer has bought at least {loyal_at} units in earlier paid orders (all orders in the "
                  "table count, in date order; for orders on the same date, in table order), each further order line of that "
                  "customer gets another 5% off, applied after the discount and before rounding.")
    if returns:
        rules += (" A return counts only if its date is at most 30 days after the order date; it then reduces the order by "
                  "(order net amount x quantity returned / quantity ordered), rounded to cents. Later returns are ignored.")
    prompt = (f"Products:\n{ptab}\n\nPrice history:\n{htab}\n\nCustomers:\n{ctab}\n\nDiscount history:\n{ttab}\n\n"
              f"Orders:\n{otab}{rtab}\n\n{rules}\nConsider the orders dated {lo.isoformat()} to {hi.isoformat()} "
              "(inclusive); 'net revenue' is the sum of their net amounts" + (" after counted returns" if returns else "")
              + ".\nQuestions:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs)) + "\nGive amounts to 2 decimals."
              + _instr(len(qs)))
    return Item(f"{BLOCK}.table.L{level}.{seed}", BLOCK, "table", [{"role": "user", "content": prompt}], multi_check(checks),
                meta={"expected": expected, "level": level})


# ---------------------------------------------------------------- schedule: people with skills and speeds

def _skills_min(names: list, people: list, dur: dict, pre: dict, release: dict, groups: list, avail: dict,
                max_nodes: int = 0, greedy_only: bool = False) -> int:
    """Exact minimum number of days when task t takes dur[t][p] days if person p does it (None: p cannot), people work on
    one task at a time from day avail[p] (0-based), tasks in a group exclude each other and release[t] is an earliest
    start. Some optimal schedule is active; listing its tasks by (start, index) and placing each on its person at the
    earliest feasible time reproduces it, so a depth-first search over (task, person) choices in that order is exact. It
    prunes lists that cannot end active and bounds by chains of fastest durations and remaining work. greedy_only: the
    best of a few 'most urgent task to whoever finishes it first' plans instead."""
    n, m = len(names), len(people)
    ix = {t: i for i, t in enumerate(names)}
    D = [[dur[t][p] for p in people] for t in names]
    mind = [min(d for d in row if d is not None) for row in D]
    rel = [release.get(t, 0) for t in names]
    preds = [[ix[q] for q in pre[t]] for t in names]
    succs = [[j for j in range(n) if i in preds[j]] for i in range(n)]
    topo, seen = [], set()
    while len(topo) < n:
        for i in range(n):
            if i not in seen and all(q in seen for q in preds[i]):
                topo.append(i)
                seen.add(i)
    tail = [0] * n
    for i in reversed(topo):
        tail[i] = mind[i] + max([tail[j] for j in succs[i]] + [0])
    grp = [[g for g, mem in enumerate(groups) if t in mem] for t in names]
    av = [avail.get(p, 0) for p in people]
    horizon = sum(max(d for d in row if d is not None) for row in D) + max(rel + av) + 1
    pbusy = [[False] * horizon for _ in people]
    gbusy = [[False] * horizon for _ in groups]
    start, who = [-1] * n, [-1] * n

    def earliest(i: int, p: int, lo: int) -> int:
        d = D[i][p]
        s = max([lo, rel[i], av[p]] + [start[q] + D[q][who[q]] for q in preds[i]])
        while True:
            bad = -1
            for u in range(s + d - 1, s - 1, -1):
                if pbusy[p][u] or any(gbusy[g][u] for g in grp[i]):
                    bad = u
                    break
            if bad < 0:
                return s
            s = bad + 1

    def mark(i: int, p: int, s: int, v: bool) -> None:
        start[i], who[i] = (s, p) if v else (-1, -1)
        for u in range(s, s + D[i][p]):
            pbusy[p][u] = v
            for g in grp[i]:
                gbusy[g][u] = v

    def greedy(prio) -> int:
        order, span = [], 0
        while len(order) < n:
            i = max((i for i in range(n) if start[i] < 0 and all(start[q] >= 0 for q in preds[i])), key=prio)
            p, s = min(((p, earliest(i, p, 0)) for p in range(m) if D[i][p] is not None),
                       key=lambda ps: (ps[1] + D[i][ps[0]], ps[0]))
            mark(i, p, s, True)
            order.append((i, p, s))
            span = max(span, s + D[i][p])
        for i, p, s in order:
            mark(i, p, s, False)
        return span

    best = [min(greedy(lambda i: tail[i]), greedy(lambda i: (tail[i], -i)), greedy(lambda i: -mind[i]))]
    if greedy_only:
        return best[0]
    nodes = [0]

    def dfs(done: int, last_s: int, last_i: int, span: int) -> None:
        nodes[0] += 1
        if max_nodes and nodes[0] > max_nodes:
            raise _SearchLimit
        if done == (1 << n) - 1:
            best[0] = span
            return
        head, lb, left = [0] * n, span, 0
        for i in topo:
            if not done >> i & 1:
                head[i] = max([last_s, rel[i]] + [start[q] + D[q][who[q]] if done >> q & 1 else head[q] + mind[q]
                                                  for q in preds[i]])
                lb = max(lb, head[i] + tail[i])
                left += mind[i]
        committed = sum(max(0, start[j] + D[j][who[j]] - last_s) for j in range(n) if done >> j & 1)
        idle = sum(max(0, av[p] - last_s) for p in range(m))
        lb = max(lb, last_s + -(-(left + committed + idle) // m))
        for mem in groups:
            rest = [ix[t] for t in mem if not done >> ix[t] & 1]
            if rest:
                lb = max(lb, min(head[i] for i in rest) + sum(mind[i] for i in rest))
        if lb >= best[0]:
            return
        cands = []
        for i in range(n):
            if done >> i & 1 or any(not done >> q & 1 for q in preds[i]):
                continue
            for p in range(m):
                if D[i][p] is None:
                    continue
                s = earliest(i, p, 0)
                if s + D[i][p] <= last_s:
                    return   # could run entirely before the last start: this list cannot end active
                if s < last_s:
                    s = earliest(i, p, last_s)
                if (s, i) > (last_s, last_i) and s + D[i][p] + tail[i] - mind[i] < best[0]:
                    cands.append((s, -tail[i], i, p))
        for s, _t, i, p in sorted(cands):
            if s + D[i][p] + tail[i] - mind[i] >= best[0]:
                continue
            mark(i, p, s, True)
            dfs(done | 1 << i, s, i, max(span, s + D[i][p]))
            mark(i, p, s, False)
    dfs(0, 0, -1, 0)
    return best[0]


def schedule(seed: int, level: int) -> Item:
    """A project for three people with different skills: each task can be done only by some of them, each at their own
    speed; prerequisites, a shared test lab (and at 10 a staging server) and release days. Questions: the minimum number
    of days with everyone, with one person away, when every task goes to its fastest person, and (10) when one person
    only joins on a later day. Instances are redrawn until the 'most urgent task to whoever finishes it first' plan loses
    to the optimum somewhere. Exact by branch and bound."""
    r = rng(BLOCK, f"schedule{level}", seed)
    n = 9 if level == 9 else 11
    people = ["Ana", "Ben", "Cy"]
    for _ in range(80):
        names = r.sample(TASKS, n)
        dur = {}
        for t in names:
            base = r.randint(2, 7)
            can = r.sample(people, r.choice([1, 2, 2, 3, 3]))
            dur[t] = {p: (max(1, base + r.choice([-2, -1, 0, 0, 1, 2, 3])) if p in can else None) for p in people}
        pre = {t: sorted(r.sample(names[:i], min(i, r.choice([0, 0, 1, 1, 2])))) for i, t in enumerate(names)}
        lab = r.sample(names, 3)
        server = r.sample([t for t in names if t not in lab], 2) if level >= 10 else []
        groups = [lab] + ([server] if server else [])
        rel = {t: r.randint(2, 6) for t in r.sample(names, 1 if level == 9 else 2)}
        release = {t: k - 1 for t, k in rel.items()}
        away = r.choice(people)
        late, late_day = r.choice(people), r.randint(4, 8)
        if any(sum(dur[t][p] is not None for t in names) < 3 for p in people):
            continue
        if any(all(dur[t][p] is None for p in people if p != away) for t in names):
            continue   # every task must stay possible without `away`
        fastest = {t: {p: (dur[t][p] if p == min((q for q in people if dur[t][q] is not None), key=lambda q: dur[t][q])
                           else None) for p in people} for t in names}
        variants = [(people, dur, {}), ([p for p in people if p != away], dur, {}), (people, fastest, {})]
        if level >= 10:
            variants.append((people, dur, {late: late_day - 1}))
        try:
            expected = [_skills_min(names, ps, d, pre, release, groups, av, max_nodes=15000) for ps, d, av in variants]
        except _SearchLimit:
            continue
        greedy = [_skills_min(names, ps, d, pre, release, groups, av, greedy_only=True) for ps, d, av in variants]
        if expected[0] < expected[2] and len(set(expected)) >= 3 and any(g > e for g, e in zip(greedy, expected)):
            break
    else:
        return schedule(seed + 10_000, level)
    rows = []
    for t in r.sample(names, n):
        cells = " | ".join(str(dur[t][p]) if dur[t][p] is not None else "-" for p in people)
        rows.append(f"{t} | {', '.join(pre[t]) if pre[t] else '-'} | {cells}")
    table_ = "task | comes after | " + " | ".join(f"days if done by {p}" for p in people) + "\n" + "\n".join(rows)
    extra = [f"The {lab[0]}, {lab[1]} and {lab[2]} tasks all need the test lab, which fits only one task at a time."]
    if server:
        extra.append(f"The {server[0]} and {server[1]} tasks both need the staging server, so they cannot run at the same time.")
    extra.append("Days are numbered from 1. " + " ".join(f"The {t} task cannot start before day {k}." for t, k in rel.items()))
    qs = ["With all three people, what is the minimum number of days needed to finish all tasks?",
          f"If {away} is away for the whole project, what is the minimum number of days?",
          "If every task must be done by the person who is fastest at it (ties: the one listed first in the table header), "
          "what is the minimum number of days?"]
    if level >= 10:
        qs.append(f"With all three people, but {late} can only start working on day {late_day}, what is the minimum number "
                  "of days?")
    prompt = ("Three people - " + ", ".join(people) + " - run a project. A '-' means that person cannot do the task.\n"
              + table_ + "\n\nEach task is done by one person from start to finish, takes that person's number of full "
              "days and cannot be split or shared. A task can start only when all tasks it comes after are finished. A "
              "person works on one task at a time. " + " ".join(extra) + "\nQuestions:\n"
              + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs)) + _instr(len(qs)))
    return Item(f"{BLOCK}.schedule.L{level}.{seed}", BLOCK, "schedule", [{"role": "user", "content": prompt}],
                multi_check([_num_check(e, 0) for e in expected]), meta={"expected": expected, "level": level})


# ---------------------------------------------------------------- budget: bigger portfolios, exact branch and bound

def _portfolio(P: dict, money: int, forced: tuple = (), at_least: int | None = None, stop_above: int = 0,
               collect: bool = False):
    """(best value, lowest cost among the best, that combination) of a portfolio problem: P holds cost, value, people, cap_ppl, reqs (a needs
    b), req_any (a needs one of bs), excl, one_of, at_most [(members, k)], syn [(a, b, bonus)], disc [(a, b, drop): b is
    cheaper when a is funded]. Depth-first over projects by value density with a fractional-knapsack bound. With
    at_least: the number of combinations whose total value is at least that (instead); with stop_above, counting stops
    as soon as the count exceeds it (the caller only needs to know it is too many); with collect, the list of those
    combinations' values instead of the count (None when counting stopped)."""
    n = len(P["cost"])
    order = sorted(range(n), key=lambda i: -P["value"][i] / P["cost"][i])
    cost, value, ppl = P["cost"], P["value"], P["ppl"]
    excl = {i: [b for a, b in P["excl"] if a == i] + [a for a, b in P["excl"] if b == i] for i in range(n)}
    syn_bonus = sum(v for _a, _b, v in P["syn"])
    drops = sum(k for _a, _b, k in P["disc"])
    best = [(-1, 0, ())]   # (value, -cost, projects)
    found, values = [0], []

    def full_cost(ch: set) -> int:
        return sum(cost[i] for i in ch) - sum(k for a, b, k in P["disc"] if a in ch and b in ch)

    def full_value(ch: set) -> int:
        return sum(value[i] for i in ch) + sum(v for a, b, v in P["syn"] if a in ch and b in ch)

    def ok(ch: set) -> bool:
        if any(a in ch and b not in ch for a, b in P["reqs"]) or any(a in ch and not set(bs) & ch for a, bs in P["req_any"]):
            return False
        return bool(set(P["one_of"]) & ch) and all(f in ch for f in forced)

    def dfs(k: int, ch: set, c: int, v: int, p: int) -> None:
        if k == n:
            if ok(ch):
                fc, fv = full_cost(ch), full_value(ch)
                if at_least is not None:
                    if fc <= money and fv >= at_least:
                        found[0] += 1
                        values.append(fv)
                    if stop_above and found[0] > stop_above:
                        raise _SearchLimit
                elif fc <= money and (fv, -fc) > best[0][:2]:
                    best[0] = (fv, -fc, tuple(sorted(ch)))
            return
        room, bound = money + drops - c, v + syn_bonus
        for i in order[k:]:
            if cost[i] <= room:
                room -= cost[i]
                bound += value[i]
            else:
                bound += value[i] * room / cost[i]
                break
        if bound < (best[0][0] if at_least is None else at_least):
            return
        i = order[k]
        if (c + cost[i] <= money + drops and p + ppl[i] <= P["cap_ppl"] and not any(j in ch for j in excl[i])
                and all(len(ch & set(m)) < mx or i not in m for m, mx in P["at_most"])):
            ch.add(i)
            dfs(k + 1, ch, c + cost[i], v + value[i], p + ppl[i])
            ch.discard(i)
        if i not in forced:
            dfs(k + 1, ch, c, v, p)
    try:
        dfs(0, set(), 0, 0, 0)
    except _SearchLimit:
        if collect:
            return None
    if collect:
        return values
    return found[0] if at_least is not None else (best[0][0], -best[0][1], best[0][2])


def budget(seed: int, level: int) -> Item:
    """20 (9) or 24 (10) projects under a money budget and a people cap with requires / excludes / at-least-one /
    at-most-k-of rules, value synergies and a cost that drops when another project is funded; level 10 adds 'requires one
    of', a second at-most group and a question with a project that must be funded. Results: the best value, the best with
    a larger budget, the lowest cost reaching the best value, how many combinations reach at least a value a little below
    the best (a count that needs a systematic search) and at 10 the best value with a forced project."""
    r = rng(BLOCK, f"budget{level}", seed)
    n = 20 if level == 9 else 24
    ids = [chr(65 + i) for i in range(n)]
    P = {"cost": [r.randint(5, 40) for _ in ids], "value": [5 * r.randint(2, 18) for _ in ids],
         "ppl": [r.randint(1, 4) for _ in ids]}
    money = int(sum(P["cost"]) * r.uniform(0.35, 0.45))
    P["cap_ppl"] = int(sum(P["ppl"]) * 0.42)
    seen, pairs = set(), []
    while len(pairs) < 20:
        a, b = r.sample(range(n), 2)
        if frozenset((a, b)) not in seen:
            seen.add(frozenset((a, b)))
            pairs.append((a, b))
    n_req, n_ex = (6, 5) if level == 9 else (7, 6)
    P["reqs"], P["excl"] = pairs[:n_req], pairs[n_req:n_req + n_ex]
    rest = pairs[n_req + n_ex:]
    P["syn"] = [(a, b, r.choice([10, 15, 20, 25])) for a, b in rest[:2]]
    P["disc"] = [(a, b, min(r.choice([4, 6, 8]), P["cost"][b] - 1)) for a, b in rest[2:3 if level == 9 else 4]]
    P["one_of"] = r.sample(range(n), 3)
    P["at_most"] = [(r.sample(range(n), 4), 2)] + ([(r.sample(range(n), 5), 2)] if level >= 10 else [])
    P["req_any"] = [(a, r.sample([i for i in range(n) if i != a], 2)) for a in r.sample(range(n), 2)] if level >= 10 else []
    best, cheapest, combo = _portfolio(P, money)
    if best < 0:
        return budget(seed + 10_000, level)
    for extra in r.sample([10, 15, 20, 25], 4) + [30, 40]:   # a budget increase that changes the answer
        more = _portfolio(P, money + extra)[0]
        if more > best:
            break
    else:
        return budget(seed + 10_000, level)
    lo_c, hi_c = (3, 15) if level == 9 else (6, 25)
    vals, done_span = [], 0
    for span in (10, 20, 40):   # values of the near-optimal combinations; a count of them needs a systematic search
        v = _portfolio(P, money, at_least=best - span, stop_above=200, collect=True)
        if v is None:
            break
        vals, done_span = v, span
    gap, near = 0, 0
    for g in range(5, done_span + 1, 5):   # the widest gap whose count stays small (exact: g <= the collected span)
        c = sum(1 for x in vals if x >= best - g)
        if c > hi_c:
            break
        gap, near = g, c
    if near < lo_c:
        return budget(seed + 10_000, level)
    expected = [best, near, more, cheapest]
    qs = ["What is the maximum total value that can be funded?",
          f"How many different combinations (original budget, all rules) have a total value of at least {best - gap}?",
          f"If the budget were {extra}k EUR larger (all other rules unchanged), what would the maximum total value be?",
          "With the original budget, what does a combination with the maximum total value from question 1 cost in total? If "
          "several combinations reach that value, give the lowest cost among them."]
    if level >= 10:   # a project outside the best combination whose forcing lowers the value
        for f in r.sample([i for i in range(n) if i not in combo], n - len(combo)):
            forced_best = _portfolio(P, money, forced=(f,))[0]
            if 0 < forced_best < best:
                break
        else:
            return budget(seed + 10_000, level)
        expected.append(forced_best)
        qs.append(f"With the original budget, if project {ids[f]} must be funded, what is the maximum total value?")
    rows = "\n".join(f"- {p}: cost {P['cost'][i]}k EUR, value {P['value'][i]}, needs {P['ppl'][i]} "
                     f"{'person' if P['ppl'][i] == 1 else 'people'}" for i, p in enumerate(ids))
    rules = ([f"{ids[a]} can only be funded if {ids[b]} is funded too" for a, b in P["reqs"]]
             + [f"{ids[a]} can only be funded if {ids[bs[0]]} or {ids[bs[1]]} (or both) is funded" for a, bs in P["req_any"]]
             + [f"{ids[a]} and {ids[b]} cannot both be funded" for a, b in P["excl"]]
             + [f"at least one of {', '.join(ids[i] for i in P['one_of'])} must be funded"]
             + [f"at most {k} of {', '.join(ids[i] for i in m)} can be funded" for m, k in P["at_most"]]
             + [f"if both {ids[a]} and {ids[b]} are funded, together they are worth {v} more" for a, b, v in P["syn"]]
             + [f"if {ids[a]} is funded, {ids[b]} costs {k}k EUR less" for a, b, k in P["disc"]])
    prompt = (f"A company can fund some of these projects:\n{rows}\n\nThe total budget is {money}k EUR and only "
              f"{P['cap_ppl']} people are available (each funded project needs its people full-time). Rules:\n"
              + "\n".join(f"- {x}" for x in rules) + "\nQuestions:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs))
              + _instr(len(qs)))
    return Item(f"{BLOCK}.budget.L{level}.{seed}", BLOCK, "budget", [{"role": "user", "content": prompt}],
                multi_check([_num_check(e, 0) for e in expected]), meta={"expected": expected, "level": level})


# ---------------------------------------------------------------- arith: settling a group trip

def _min_transfers(bal: dict) -> int:
    """Fewest payments that bring every balance to zero: people with a nonzero balance minus the largest number of
    disjoint groups whose balances sum to zero (exact over subsets)."""
    xs = [v for v in bal.values() if v != 0]
    k = len(xs)
    full = (1 << k) - 1
    sums = [0] * (1 << k)
    for m in range(1, 1 << k):
        low = m & -m
        sums[m] = sums[m ^ low] + xs[low.bit_length() - 1]
    best = [0] * (1 << k)   # most zero-sum groups a zero-sum set can be cut into
    for m in range(1, 1 << k):
        if sums[m] != 0:
            continue
        sub, b = (m - 1) & m, 1
        while sub:
            if sums[sub] == 0 and best[sub] and best[m ^ sub]:
                b = max(b, best[sub] + best[m ^ sub])
            sub = (sub - 1) & m
        best[m] = b
    return k - best[full] if k else 0


def arith(seed: int, level: int) -> Item:
    """A group trip: expenses paid by one person and shared equally, by shares, or with one person's fixed part and the
    rest equal; parts in whole cents (rounded down, leftover cents one each in the listed order), USD expenses converted
    first; level 10 adds tips split like the bill and more people. Questions: several people's balances, the total, and
    the fewest transfers that settle everything (the groups that settle among themselves are hidden behind a pair of
    expenses that cancel). 6 (9) or 8 (10) results."""
    from decimal import ROUND_HALF_UP, Decimal as D
    r = rng(BLOCK, f"arith{level}", seed)
    n_p, n_e = (6, 10) if level == 9 else (8, 14)
    people = r.sample(["Ana", "Ben", "Carla", "Dmitri", "Elif", "Farid", "Greta", "Hugo", "Iris", "Jonas"], n_p)
    cut = [people[:3], people[3:]] if level == 9 else [people[:3], people[3:6], people[6:]]
    rate = D(r.choice(["0.86", "0.91", "0.93"]))
    what = ["dinner", "taxi", "hotel", "museum", "groceries", "boat trip", "concert", "car rental", "breakfast", "wine",
            "bike hire", "tapas", "ferry", "cooking class", "parking", "souvenirs"]
    paid = {p: 0 for p in people}
    owed = {p: 0 for p in people}
    lines, total = [], 0

    def spread(cents: int, who: list, weights: list) -> dict:
        W = sum(weights)
        parts = {p: cents * w // W for p, w in zip(who, weights)}
        for p in who[:cents - sum(parts.values())]:
            parts[p] += 1
        return parts

    for e in range(n_e):
        grp = cut[e % len(cut)]
        payer = r.choice(grp)
        who = r.sample(grp, r.randint(2, len(grp)))
        usd = r.random() < 0.3
        amount = D(r.randint(1200, 32000)) / 100
        cents = int((amount * rate * 100).quantize(D("1"), rounding=ROUND_HALF_UP)) if usd else int(amount * 100)
        tip = level >= 10 and r.random() < 0.35
        kind = r.choice(["equal", "shares", "fixed"])
        desc = f"{payer} paid {'$' if usd else ''}{amount:.2f}{'' if usd else ' EUR'} for the {r.choice(what)}"
        if tip:
            desc += " plus a 10% tip (the tip is added to the bill and shared the same way)"
            cents += int((D(cents) / 10).quantize(D("1"), rounding=ROUND_HALF_UP))
        if kind == "equal":
            parts = spread(cents, who, [1] * len(who))
            desc += f", shared equally by {', '.join(who)}"
        elif kind == "shares":
            ws = [r.randint(1, 3) for _ in who]
            parts = spread(cents, who, ws)
            desc += ", shared by shares: " + ", ".join(f"{p} {w} share{'s' if w > 1 else ''}" for p, w in zip(who, ws))
        else:
            fixed = r.randint(cents // 10, cents // 3)
            first, others = who[0], who[1:]
            parts = {first: fixed, **spread(cents - fixed, others, [1] * len(others))}
            desc += (f"; {first}'s part is {fixed / 100:.2f} EUR{' (tip included)' if tip else ''} and the rest is shared "
                     f"equally by {', '.join(others)}")
        paid[payer] += cents
        total += cents
        for p, v in parts.items():
            owed[p] += v
        lines.append(desc)
    a, b = r.choice(cut[0]), r.choice(cut[1])   # a pair of expenses that cancel: the groups stay hidden
    x = r.randint(1500, 6000)
    for payer, other, thing in ((a, b, "train ticket"), (b, a, "concert ticket")):
        paid[payer] += x
        owed[other] += x
        total += x
        lines.insert(r.randint(0, len(lines)), f"{payer} paid {x / 100:.2f} EUR for {other}'s {thing} ({other} alone)")
    bal = {p: paid[p] - owed[p] for p in people}
    asked = r.sample(people, 4 if level == 9 else 6)
    expected = [bal[p] / 100 for p in asked] + [total / 100, _min_transfers(bal)]
    qs = [f"What is {p}'s balance in EUR (paid minus owed; negative if {p} owes money)?" for p in asked]
    qs += ["How much did the whole trip cost in EUR (all expenses, converted)?",
           "What is the smallest number of payments between people that settles every balance exactly?"]
    prompt = (f"{len(people)} friends ({', '.join(people)}) share the costs of a trip. Expenses:\n"
              + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
              + f"\n\nRules: a USD amount is first converted to EUR at {rate} EUR per USD and rounded to cents (half up)"
              + ("; a 10% tip is rounded to cents (half up) and added before sharing" if level >= 10 else "")
              + ". A shared amount is split in whole cents: each part is rounded down to the cent, then the cents left over "
              "are given one each to the people in the order listed for that expense. Each person's balance is what they "
              "paid minus the sum of their parts.\nQuestions:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs))
              + "\nGive amounts to 2 decimals." + _instr(len(qs)))
    checks = [_num_check(e, 0.005) for e in expected[:-1]] + [_num_check(expected[-1], 0)]
    return Item(f"{BLOCK}.arith.L{level}.{seed}", BLOCK, "arith", [{"role": "user", "content": prompt}], multi_check(checks),
                meta={"expected": expected, "level": level})


# ---------------------------------------------------------------- code_trace: Python semantics traps, one printed line each

def _snippets(r, i: int, n_xs: int, steps: int = 24) -> dict:
    """Trap snippets over the shared list xs; each is code ending in one print. Suffix i keeps names apart. Every call
    draws the same number of random values, so the snippet picked does not shift the others."""
    k = r.randint(3, min(7, n_xs - 2))
    s = {}
    s["gen"] = (f"g{i} = (v * {r.randint(2, 5)} for v in xs if v % {r.randint(2, 4)} == {r.randint(0, 1)})\n"
                f"a{i} = sum(g{i})\nb{i} = sum(g{i})\nprint([a{i}, b{i}])")
    s["late"] = (f"fs{i} = [lambda y: y * k + {r.randint(1, 9)} for k in range({r.randint(3, 6)})]\n"
                 f"gs{i} = [lambda y, k=k: y * k for k in range({r.randint(3, 6)})]\n"
                 f"print([fs{i}[0]({r.randint(2, 9)}), fs{i}[1]({r.randint(2, 9)}), gs{i}[-1]({r.randint(2, 9)}), len(gs{i})])")
    s["default"] = (f"def push{i}(v, bag=[]):\n    bag.append(v)\n    return sum(bag)\n\n\n"
                    f"out{i} = [push{i}(v) for v in xs[:{k}]]\nout{i}.append(push{i}({r.randint(50, 99)}, []))\n"
                    f"out{i}.append(push{i}({r.randint(1, 9)}))\nprint(out{i}[-3:])")
    a, b, c, d = r.sample([2, 3, 4, 5, 7], 4)
    s["finally"] = (f"def f{i}(v):\n    try:\n        if v % {a} == 0:\n            return v // {b}\n"
                    f"        if v % {c} == 0:\n            return v // (v - v)\n        return -v\n"
                    f"    except ZeroDivisionError:\n        return 'zero'\n    finally:\n        if v % {d} == 0:\n"
                    f"            return 'fin'\n\n\nprint([f{i}(v) for v in xs[:{k + 1}]])")
    s["classattr"] = (f"class Box{i}:\n    items = []\n    n = {r.randint(1, 9)}\n\n    def __init__(self, v):\n"
                      f"        self.items.append(v)\n        self.n += v\n        Box{i}.n += {r.randint(1, 3)}\n\n\n"
                      f"bs{i} = [Box{i}(v) for v in xs[:{k}]]\n"
                      f"print([len(bs{i}[0].items), bs{i}[0].n, bs{i}[-1].n, Box{i}.n])")
    order = r.choice(["B{i}, C{i}", "C{i}, B{i}"]).format(i=i)
    c_body = r.choice(["'C' + super().f()", "'C'"])
    b_body = r.choice(["'B' + super().f()", "super().f() + 'b'"])
    s["mro"] = (f"class A{i}:\n    def f(self):\n        return 'A'\n\n\nclass B{i}(A{i}):\n    def f(self):\n"
                f"        return {b_body}\n\n\nclass C{i}(A{i}):\n    def f(self):\n        return {c_body}\n\n\n"
                f"class D{i}({order}):\n    def f(self):\n        return super().f() + 'D'\n\n\n"
                f"print([D{i}().f(), ''.join(k.__name__[0] for k in D{i}.__mro__)])")
    every = steps // (3 if steps < 30 else 4)
    s["ledger"] = (f"bal{i} = {{'ana': {r.randint(5, 30)}, 'ben': {r.randint(5, 30)}, 'cy': {r.randint(5, 30)}}}\n"
                   f"who{i} = sorted(bal{i})\nfor step in range(1, {steps + 1}):\n    src = who{i}[step % 3]\n"
                   f"    dst = who{i}[step * {r.randint(2, 5)} % 3]\n"
                   f"    amt = step * {r.randint(3, 9)} % {r.randint(9, 17)} - {r.randint(2, 5)}\n"
                   f"    if src != dst and 0 < amt <= bal{i}[src]:\n        bal{i}[src] -= amt\n        bal{i}[dst] += amt\n"
                   f"    elif amt < 0:\n        bal{i}[src] += amt // 2\n    if step % {every} == 0:\n"
                   f"        print(sorted(bal{i}.items(), key=lambda kv: (-kv[1], kv[0])))")
    h, h2, fl = r.choice([2, 4, 6, 8]), r.choice([1, 3, 5, 7]), r.randint(2, 9)
    na, nb = r.randint(7, 40), r.randint(2, 6)
    s["numbers"] = (f"vals{i} = [round({h}.5), round(-{h2}.5), int(-{fl}.{r.randint(1, 9)}), -{na} // {nb}, -{na} % {nb}, "
                    f"{na} // -{nb}.0, divmod(-{na}, {nb}), True + True * {r.randint(2, 5)}]\nprint(vals{i})")
    s["any"] = (f"it{i} = iter(xs)\nhit{i} = any(v > {r.randint(5, 20)} for v in it{i})\n"
                f"print([hit{i}, len(list(it{i})), next(it{i}, 'end')])")
    s["zipmap"] = (f"m{i} = map(lambda v: v + {r.randint(1, 9)}, xs)\npairs{i} = list(zip(m{i}, xs[{r.randint(2, 5)}:]))\n"
                   f"print([len(pairs{i}), next(m{i}, None), pairs{i}[-1][0]])")
    w, hh = r.randint(3, 5), r.randint(3, 5)
    s["alias"] = (f"row{i} = [0] * {w}\ngrid{i} = [row{i}] * {hh}\n"
                  f"grid{i}[{r.randint(0, hh - 1)}][{r.randint(0, w - 1)}] = {r.randint(2, 9)}\ngrid{i}[0] = [1] * {w}\n"
                  f"print([sum(map(sum, grid{i})), row{i}.count(0)])")
    s["tuple"] = (f"t{i} = ([{r.randint(1, 9)}], {r.randint(1, 9)})\ntry:\n    t{i}[0] += [{r.randint(1, 9)}, {r.randint(1, 9)}]\n"
                  f"except TypeError:\n    t{i}[0].append(len(t{i}[0]))\nprint(t{i})")
    entries = r.sample(["1: 'int'", f"{r.randint(2, 5)}: 'k'", "True: 'bool'", "1.0: 'float'", "0: 'zero'", "False: 'no'",
                        "0.0: 'fz'"], 7)
    s["dictkeys"] = f"d{i} = {{{', '.join(entries)}}}\nprint([len(d{i}), d{i}[1], d{i}[0], list(d{i})])"
    m = r.randint(2, 4)
    s["stable"] = (f"pairs{i} = [(v % {m}, j) for j, v in enumerate(xs[:{k + 2}])]\n"
                   f"print([j for _, j in sorted(pairs{i}, key=lambda p: p[0], reverse=True)])")
    s["nonlocal"] = (f"def counter{i}(start):\n    n = start\n\n    def step(d={r.randint(2, 5)}):\n        nonlocal n\n"
                     f"        n += d\n        return n\n    return step\n\n\n"
                     f"c{i} = counter{i}({r.randint(1, 9)})\nc2{i} = counter{i}(0)\n"
                     f"print([c{i}(), c{i}({r.randint(5, 20)}), c2{i}(), c{i}()])")
    ea = r.randint(2, 4)
    s["tryflow"] = (f"log{i} = []\n\n\ndef run{i}(v):\n    try:\n        log{i}.append('t')\n        if v % {ea} == 0:\n"
                    f"            raise ValueError(v)\n    except ValueError:\n        log{i}.append('e')\n        return 1\n"
                    f"    else:\n        log{i}.append('l')\n        return 2\n    finally:\n        log{i}.append('f')\n\n\n"
                    f"res{i} = [run{i}(v) for v in xs[:{k}]]\nprint([''.join(log{i}), sum(res{i})])")
    s["send"] = (f"def acc{i}():\n    total = 0\n    while True:\n        v = yield total\n        if v is None:\n"
                 f"            return total * 10\n        total += v\n\n\n"
                 f"g{i} = acc{i}()\nnext(g{i})\nouts{i} = [g{i}.send(v) for v in xs[:{k}]]\ntry:\n    g{i}.send(None)\n"
                 f"except StopIteration as e:\n    outs{i}.append(e.value)\nprint(outs{i}[-2:])")
    ca, cb = sorted(r.sample(range(-5, 20), 2))
    s["chain"] = f"print(sum(1 for v in xs if {ca} < v <= {cb} != v % {r.randint(3, 7)}))"
    return s


def code_trace(seed: int, level: int) -> Item:
    """A script of snippets built on Python semantics traps (generator exhaustion, late-binding closures, mutable
    defaults, finally overriding return, shared class attributes, MRO with super, banker's rounding and floor division,
    partly consumed iterators, list aliasing, tuple +=, equal dict keys, stable reverse sorts, nonlocal,
    try/except/else/finally, generator send/return) plus a ledger loop of 24 (9) or 40 (10) steps whose state is
    printed at checkpoints (one slip there carries on). 10 (9) or 14 (10) printed lines."""
    r = rng(BLOCK, f"code_trace{level}", seed)
    n_xs, n_snip, steps = (10, 7, 24) if level == 9 else (14, 10, 40)
    xs = [r.randint(-9, 30) for _ in range(n_xs)]
    names = sorted(x for x in _snippets(r, 0, n_xs) if x != "ledger")
    picks = r.sample(names, n_snip)
    picks.insert(r.randrange(len(picks) + 1), "ledger")   # the long loop is always there
    parts = [f"xs = {xs}"]
    for i, name in enumerate(picks):
        parts.append(_snippets(r, i + 1, n_xs, steps)[name])
    src = "\n\n\n".join(parts) + "\n"
    buf = io.StringIO()
    with warnings.catch_warnings(), contextlib.redirect_stdout(buf):
        warnings.simplefilter("ignore")
        exec(compile(src, "<trace>", "exec"), {})  # our own generated code, not model output
    expected = buf.getvalue().rstrip("\n").split("\n")
    prompt = (f"What does this Python 3 program print? It prints {len(expected)} lines. Trace it carefully and give every "
              f"line exactly as Python prints it.\n\n```python\n{src}```"
              + _instr(len(expected)).replace("per question", "per printed line, in order"))
    return Item(f"{BLOCK}.code_trace.L{level}.{seed}", BLOCK, "code_trace", [{"role": "user", "content": prompt}],
                multi_check([_repr_check(e) for e in expected]), meta={"expected": expected, "level": level})
