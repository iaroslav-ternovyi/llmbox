"""techhelp.sql: what queries return on a small SQLite database. The database is given as CREATE TABLE + INSERT
statements (3-4 tables, 5-36 rows each), then 3-8 queries; the model writes each query's rows.

The answer key cannot be wrong: the generator builds the database in Python's sqlite3 (in memory, from exactly the SQL
the prompt shows) and runs the queries, so every expected row is real SQLite output. Levels 1-10 are spread by what
SQLite actually does that people and models get wrong, a fixed mix of rules per level (a seed changes the theme, names,
numbers and which rows hit a rule):

  1-2   filters, GROUP BY, HAVING, LIMIT / OFFSET, integer division
  3-4   NULL: `<>` drops NULL rows, COUNT(col) vs COUNT(*), AVG is REAL, NOT IN over a NULL is never true, a LEFT JOIN's
        condition in ON vs WHERE, NULLs sort first
  5-6   the NULL group, CASE without ELSE, UNION dedups NULLs, text affinity ('100' < '9', `badge > 50` compares text),
        `||` with NULL, RANK vs DENSE_RANK, the default window frame is RANGE (ties are included), LAG defaults,
        LIKE is case-insensitive (ASCII only), binary ORDER BY puts 'Zed' before 'amy'
  7-8   two rules at once: partitioned running sums, recursive CTEs, correlated subqueries, strftime on dates (text '03';
        a date that is not zero-padded gives NULL but still passes a text comparison), SUM of text is REAL,
        `LIMIT 2, 3` (offset first), `date(.., '+1 month')` from the 31st, ROUND half away from zero, ROUND(x, -1),
        ROWS vs RANGE frames, NOT IN vs NOT EXISTS
  9-10  aimed at the frontier: every query mixes three or more of the above over 28-36 rows, plus integer overflow to
        REAL, a scalar subquery that silently takes its first row, LAG's default that does not replace a NULL value,
        REAL values stored as INTEGER by column affinity, PERCENT_RANK / NTILE, non-ASCII names in LIKE and upper(),
        compound SELECTs run left to right, GROUP BY binding to a column rather than the alias of the same name,
        substr with a negative length, hex() of a number, printf rounding - and a history of 6 (9) writes before the
        queries (foreign keys declared but off, INSERT OR REPLACE giving a new id, OR IGNORE, upserts on NULL, a swap,
        CASE without ELSE in an UPDATE, a reused id, date arithmetic on bad dates): see _history

Every query's rules must bite: the generator also runs the naive reading of it (ROWS for the default frame, NULLs
last, the condition moved from ON to WHERE, ...) and draws new parameters until the real result differs.

Version safety: nothing newer than SQLite 3.30 (window functions, the WINDOW clause, RANGE offsets), the default
compile options, and every query ends in an ORDER BY that orders its rows totally; tests/test_real_engines.py re-runs
all queries with PRAGMA reverse_unordered_selects and against the Linux box's SQLite (3.46 vs the Mac's 3.53).

Grading: credit per query = the rows right (every cell) over max(rows expected, rows given), rows aligned by longest
common subsequence, so a missing or extra row costs one row, not the rest of the result. INTEGER cells must be written
as whole numbers and REAL cells with a decimal point (AVG, SUM of text and overflow are REAL; 7 / 2 is 3): the type is
part of the answer. Text compares exactly (case-sensitive), NULL as NULL.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace as NS

from .common import Item, rng, strip_think

BLOCK = "techhelp"
GRADING = 1

THEMES = [
    dict(P="departments", pz="city", E="employees", ep="dept_id", es="rating", ec="badge", ed="hired", eb="manager_id",
         T="expenses", te="employee_id", ta="amount", td="spent_on", tt="category", X="budgets", xp="dept_id", xm="month",
         xg="cap", pnames=["ops", "sales", "labs", "legal", "design", "infra", "support"],
         zones=["Lyon", "Oslo", "Porto", "Riga"], tags=["travel", "meals", "books", "gear"]),
    dict(P="stores", pz="region", E="customers", ep="store_id", es="points", ec="ref", ed="joined", eb="referred_by",
         T="orders", te="customer_id", ta="total", td="ordered_on", tt="status", X="targets", xp="store_id", xm="month",
         xg="goal", pnames=["central", "harbor", "airport", "mall", "station", "campus", "market"],
         zones=["north", "south", "east", "west"], tags=["paid", "shipped", "void", "refund"]),
    dict(P="racks", pz="room", E="hosts", ep="rack_id", es="load", ec="tag", ed="installed", eb="parent_id",
         T="incidents", te="host_id", ta="minutes", td="opened_on", tt="severity", X="slas", xp="rack_id", xm="month",
         xg="allowed", pnames=["r1a", "r1b", "r2a", "r2b", "r3a", "r3b", "r4a"],
         zones=["basement", "attic", "garage", "office"], tags=["low", "high", "crit", "info"]),
    dict(P="clubs", pz="building", E="students", ep="club_id", es="grade", ec="locker", ed="enrolled", eb="mentor_id",
         T="payments", te="student_id", ta="amount", td="paid_on", tt="method", X="quotas", xp="club_id", xm="month",
         xg="quota", pnames=["chess", "drama", "robotics", "choir", "rowing", "art", "debate"],
         zones=["annex", "main", "gym", "lab"], tags=["cash", "card", "bank", "app"]),
]
NAMES = ["amir", "bea", "cruz", "dana", "eli", "fay", "gus", "hana", "ivo", "jun", "kai", "lena", "milo", "nia", "otto",
         "pia", "quin", "rosa", "sami", "tove", "uma", "vik", "wren", "yara", "zeno"]
ODD_NAMES = ["émile", "Émile", "Ørjan", "Ñico", "élodie"]   # non-ASCII first letters: LIKE and upper() fold ASCII only
CODES = ["5", "7", "8", "9", "12", "18", "30", "42", "45", "60", "64", "85", "95", "100", "250", "300", "1000", "7000"]
SIZES = {1: (3, 5, 6), 2: (3, 6, 8), 3: (4, 7, 10), 4: (4, 8, 12), 5: (4, 9, 14), 6: (5, 10, 16), 7: (5, 11, 18),
         8: (5, 12, 22), 9: (6, 13, 28), 10: (6, 15, 36)}   # rows of P, E, T
D0 = date(2026, 1, 5)


# ---- the database ------------------------------------------------------------------------------------------------------

def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return repr(v)


def _data(r, level: int, th: dict) -> NS:
    nP, nE, nT = SIZES[level]
    pnames = r.sample(th["pnames"], nP)
    zones = [r.choice(th["zones"]) for _ in range(nP)]
    if level >= 3:
        zones[r.randrange(nP)] = None
    P = [(i + 1, pnames[i], zones[i]) for i in range(nP)]
    # entities: unique names (mixed case from level 5, one non-ASCII from 9), a parent that may be NULL or missing
    names = r.sample(NAMES, nE)
    if level >= 5:
        up = r.sample(range(nE), max(2, nE * 2 // 5))
        names = [n.capitalize() if i in up else n for i, n in enumerate(names)]
    if level >= 9:
        names[r.randrange(nE)] = r.choice(ODD_NAMES)
    used_p = r.sample([p[0] for p in P], nP - 1)          # one parent has no entities
    pids = [r.choice(used_p) for _ in range(nE)]
    idx = r.sample(range(nE), nE)
    if level >= 3:
        pids[idx[0]] = None
    if level >= 5:
        pids[idx[1]] = None
        pids[idx[2]] = nP + 2                              # points at no row of P
    scores = [r.randint(10, 99) for _ in range(nE)]
    if level >= 4:                                         # ties
        scores[idx[3]] = scores[idx[4]]
        scores[idx[5 % nE]] = scores[idx[4]] if level >= 6 else scores[idx[5 % nE]]
    if level >= 8:   # something to round: x.25 / x.75 to one decimal and x.5 to a whole number
        scores[idx[6]] = 8 * r.randint(2, 11) + 4
        scores[idx[7]] = 4 * r.randint(3, 24) + r.choice([1, 3])
    if level >= 9:
        scores[idx[6]] = r.randint(93, 99)                 # * 1e17 overflows to REAL
        scores[idx[7]] = r.randint(40, 92)
    if level >= 3:
        scores[idx[-1]] = None
    if level >= 6:
        scores[idx[-2]] = None
    codes = r.sample(CODES, nE)
    if level >= 8:
        codes[idx[-3]] = None
    since = [(date(2024, 1, 1) + timedelta(days=r.randrange(900))).isoformat() for _ in range(nE)]
    if level >= 7:   # the 31st before a shorter month, and 30 January of a leap year
        since[idx[0]] = f"{r.choice([2024, 2025])}-{r.choice(['01', '03', '05', '08', '10'])}-31"
        since[idx[1]] = "2024-01-30"
    boss = [None] * nE
    if level >= 7:
        for i in range(2, nE):
            boss[i] = r.choice(range(1, i + 1)) if r.random() < 0.85 else None
        if level >= 8:   # a subtree under a manager who is not in the table
            j = r.randrange(2, nE - 2)
            boss[j] = nE + 5
            boss[r.randrange(j + 1, nE)] = j + 1
    E = [(i + 1, names[i], pids[i], scores[i], codes[i], since[i], boss[i]) for i in range(nE)]
    # events
    eids_pool = [e[0] for e in E]
    lonely = set(r.sample(eids_pool, 1 if level < 9 else 2)) if level >= 3 else set()
    busy = [e for e in eids_pool if e not in lonely]
    eids = [r.choice(busy) for _ in range(nT)]
    ke = r.sample(range(nT), 2)
    if level >= 3:
        eids[ke[0]] = None
    if level >= 4:
        eids[ke[1]] = nE + 3
    amounts = [5 * r.randint(1, 19) for _ in range(nT)]
    ka = r.sample(range(nT), 6)
    if level >= 7:
        for j in ka[:2]:
            amounts[j] = amounts[j] + 2.5
    if level >= 3:
        amounts[ka[2]] = None
    if level >= 6:
        amounts[ka[3]] = None
    amt_src = [_lit(a) for a in amounts]
    if level >= 9:   # INTEGER affinity: 40.0 and '35' are stored as the integers 40 and 35
        v = 5 * r.randint(2, 18)
        amounts[ka[4]], amt_src[ka[4]] = v, repr(float(v))
        v = 5 * r.randint(2, 18)
        amounts[ka[5]], amt_src[ka[5]] = v, f"'{v}'"
    pool = r.sample(range(112), nT if level < 4 else max(4, nT * 2 // 3))
    days = [(D0 + timedelta(days=pool[i] if i < len(pool) else r.choice(pool))).isoformat() for i in range(nT)]
    kd = r.sample(range(nT), 2)
    if level >= 7:   # the same entity twice on one day: ties inside a partition
        for j1, j2 in [r.sample([j for j in range(nT) if j not in kd and eids[j] in busy], 2) for _ in range(2)]:
            eids[j2], days[j2] = eids[j1], days[j1]
    if level >= 7:
        days[kd[0]] = days[kd[0]] + " 09:30"
    if level >= 8:
        days[kd[1]] = f"2026-{r.choice([3, 4])}-{r.randint(1, 9)}"
    if level >= 9:   # the 31st: date(.., '+1 month') runs over into March
        days[r.choice([j for j in range(nT) if j not in kd and eids[j] in busy])] = "2026-01-31"
    tags = [r.choice(th["tags"]) for _ in range(nT)]
    kt = r.sample(range(nT), 4)
    if level >= 3:
        tags[kt[0]] = None
    if level >= 5:
        tags[kt[1]] = None
    if level >= 6:
        tags[kt[2]] = (tags[kt[3]] or th["tags"][0]).capitalize()
    T = [(i + 1, eids[i], amounts[i], days[i], tags[i]) for i in range(nT)]
    X = []
    if level >= 10:
        for p in r.sample(used_p, 3):
            for m in ("2026-02", "2026-03"):
                X.append((p, m, 10 * r.randint(3, 20)))
    return NS(P=P, E=E, T=T, X=X, amt_src=amt_src)


def _schema(N: NS, D: NS, level: int) -> str:
    fk = level >= 9   # declared, but foreign keys are off by default: nothing cascades, dangling ids stay
    out = [f"CREATE TABLE {N.P} (id INTEGER PRIMARY KEY, name TEXT NOT NULL, {N.pz} TEXT);",
           f"CREATE TABLE {N.E} (id INTEGER PRIMARY KEY, name TEXT NOT NULL{' UNIQUE' if fk else ''}, {N.ep} INTEGER"
           f"{f' REFERENCES {N.P}(id) ON DELETE CASCADE' if fk else ''}, {N.es} INTEGER, {N.ec} TEXT, "
           f"{N.ed} TEXT" + (f", {N.eb} INTEGER" if level >= 7 else "") + ");",
           f"CREATE TABLE {N.T} (id INTEGER PRIMARY KEY, {N.te} INTEGER{f' REFERENCES {N.E}(id) ON DELETE CASCADE' if fk else ''}, "
           f"{N.ta} INTEGER, {N.td} TEXT, {N.tt} TEXT);"]
    if D.X:
        out.append(f"CREATE TABLE {N.X} ({N.xp} INTEGER, {N.xm} TEXT, {N.xg} INTEGER);")
    rows = lambda t, rs: f"INSERT INTO {t} VALUES\n" + ",\n".join("  (" + ", ".join(rs_) + ")" for rs_ in rs) + ";"
    out.append(rows(N.P, [[_lit(v) for v in p] for p in D.P]))
    out.append(rows(N.E, [[_lit(v) for v in (e if level >= 7 else e[:6])] for e in D.E]))
    out.append(rows(N.T, [[_lit(t[0]), _lit(t[1]), D.amt_src[i], _lit(t[3]), _lit(t[4])] for i, t in enumerate(D.T)]))
    if D.X:
        out.append(rows(N.X, [[_lit(v) for v in x] for x in D.X]))
    return "\n".join(out)


def _connect(script: str) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript(script)
    return con


def run_query(con: sqlite3.Connection, sql: str):
    """(column names, rows) or 'ERROR'."""
    try:
        cur = con.execute(sql)
        return [d[0] for d in cur.description], [tuple(row) for row in cur.fetchall()]
    except sqlite3.Error:
        return "ERROR"


# ---- levels 9-10: what happened to the database before the queries ---------------------------------------------------
# (v0.11 hardening: Claude Opus answered the first levels 9-10 almost all right: 0.98 and 1.0.) A history of writes runs
# after the INSERTs, each chosen on the live database so it changes something: foreign keys are declared ON DELETE
# CASCADE but off by default (nothing cascades); INSERT OR REPLACE on a UNIQUE name deletes the row and inserts a new
# one with a new id (its events and reports now point at nothing); OR IGNORE skips only the conflicting row; an upsert
# adds to NULL; `SET a = b, b = a` swaps (every right side sees the old row) and column affinity converts both values;
# CASE without ELSE in an UPDATE writes NULL; a deleted maximum id is given out again; date(.., '+1 month') of the 31st
# runs over and of a date that is not zero-padded is NULL; `<> 'x'` leaves NULL rows alone. One slip carries into
# every query that reads the row.

def _rows(con, sql: str) -> list:
    return con.execute(sql).fetchall()


def _new_name(con, N, r) -> str:
    have = {x[0].lower() for x in _rows(con, f"SELECT name FROM {N.E}")}
    name = r.choice([n for n in NAMES if n not in have])
    return name.capitalize() if r.random() < 0.5 else name


def _new_row(con, N, r) -> str:
    pids = [x[0] for x in _rows(con, f"SELECT id FROM {N.P} ORDER BY id")]
    codes = {x[0] for x in _rows(con, f"SELECT {N.ec} FROM {N.E}")}
    return (f"{r.choice(pids)}, {r.randint(10, 99)}, '{r.choice([c for c in CODES if c not in codes])}', "
            f"'{(date(2025, 1, 1) + timedelta(days=r.randrange(500))).isoformat()}'")


def h_cascade(N, con, r):
    ps = [x[0] for x in _rows(con, f"SELECT DISTINCT {N.ep} FROM {N.E} WHERE {N.ep} IN (SELECT id FROM {N.P}) ORDER BY 1")]
    return f"DELETE FROM {N.P} WHERE id = {r.choice(ps)};"


def h_replace(N, con, r):
    es = _rows(con, f"SELECT id, name FROM {N.E} e WHERE EXISTS (SELECT 1 FROM {N.T} t WHERE t.{N.te} = e.id) ORDER BY id")
    _eid, name = r.choice(es)
    return f"INSERT OR REPLACE INTO {N.E} (name, {N.ep}, {N.es}, {N.ec}, {N.ed}) VALUES ('{name}', {_new_row(con, N, r)});"


def h_ignore(N, con, r):
    old = r.choice([x[0] for x in _rows(con, f"SELECT name FROM {N.E} ORDER BY id")])
    return (f"INSERT OR IGNORE INTO {N.E} (name, {N.ep}, {N.es}, {N.ec}, {N.ed}) VALUES\n  ('{old}', {_new_row(con, N, r)}),\n"
            f"  ('{_new_name(con, N, r)}', {_new_row(con, N, r)});")


def h_upsert(N, con, r):
    a = r.choice([x[0] for x in _rows(con, f"SELECT name FROM {N.E} WHERE {N.es} IS NOT NULL ORDER BY id")])
    b = r.choice([x[0] for x in _rows(con, f"SELECT name FROM {N.E} WHERE {N.es} IS NULL ORDER BY id")])
    d = r.choice([5, 10, 15])
    pair = [f"('{a}', {d})", f"('{b}', {d})"]
    r.shuffle(pair)
    return (f"INSERT INTO {N.E} (name, {N.es}) VALUES {', '.join(pair)}\n"
            f"  ON CONFLICT(name) DO UPDATE SET {N.es} = {N.es} + excluded.{N.es};")


def h_swap(N, con, r):
    k = r.choice([x[0] for x in _rows(con, f"SELECT id FROM {N.E} WHERE {N.es} IS NOT NULL AND {N.ec} IS NOT NULL ORDER BY id")])
    return f"UPDATE {N.E} SET {N.es} = {N.ec}, {N.ec} = {N.es} WHERE id = {k};"


def h_casenull(N, con, r):
    ps = [x[0] for x in _rows(con, f"SELECT {N.ep} FROM {N.E} WHERE {N.es} IS NOT NULL AND {N.ep} IS NOT NULL GROUP BY {N.ep} "
                                  f"HAVING MIN({N.es}) < MAX({N.es}) ORDER BY 1")]
    p = r.choice(ps)
    lo, hi = _rows(con, f"SELECT MIN({N.es}), MAX({N.es}) FROM {N.E} WHERE {N.ep} = {p}")[0]
    t = r.randint(lo, hi - 1)
    return f"UPDATE {N.E} SET {N.es} = CASE WHEN {N.es} > {t} THEN {N.es} - 10 END WHERE {N.ep} = {p};"


def h_reuse(N, con, r):
    eid = r.choice([x[0] for x in _rows(con, f"SELECT id FROM {N.E} ORDER BY id")])
    day = (D0 + timedelta(days=r.randrange(112))).isoformat()
    tag = r.choice([x[0] for x in _rows(con, f"SELECT DISTINCT {N.tt} FROM {N.T} WHERE {N.tt} IS NOT NULL ORDER BY 1")])
    return (f"DELETE FROM {N.T} WHERE id = (SELECT MAX(id) FROM {N.T});\n"
            f"INSERT INTO {N.T} ({N.te}, {N.ta}, {N.td}, {N.tt}) VALUES ({eid}, {5 * r.randint(1, 19)}, '{day}', '{tag}');")


def h_dateshift(N, con, r):
    odd = [x[0] for x in _rows(con, f"SELECT DISTINCT {N.te} FROM {N.T} WHERE {N.te} IN (SELECT id FROM {N.E}) AND "
                                    f"(length({N.td}) <> 10 OR substr({N.td}, 9, 2) >= '29') ORDER BY 1")]
    return f"UPDATE {N.T} SET {N.td} = date({N.td}, '+1 month') WHERE {N.te} = {r.choice(odd)};"


def h_double(N, con, r):
    x = r.choice([t[0] for t in _rows(con, f"SELECT DISTINCT {N.tt} FROM {N.T} WHERE {N.tt} IS NOT NULL ORDER BY 1")])
    return f"UPDATE {N.T} SET {N.ta} = {N.ta} * 2 WHERE {N.tt} <> '{x}';"


H = {f.__name__: f for f in [h_cascade, h_replace, h_ignore, h_upsert, h_swap, h_casenull, h_reuse, h_dateshift, h_double]}
HISTORY = {9: ["h_cascade", "h_replace", "h_upsert", "h_swap", "h_reuse", "h_dateshift"],
           10: ["h_cascade", "h_replace", "h_ignore", "h_upsert", "h_swap", "h_casenull", "h_reuse", "h_dateshift", "h_double"]}


def _history(N, con, r, level: int) -> list[str] | None:
    """The writes, each applied at once so the next one is chosen on the changed database; None if one cannot bite."""
    names = list(HISTORY.get(level, []))
    r.shuffle(names)
    out = []
    for name in names:
        before = [_rows(con, f"SELECT * FROM {t} ORDER BY rowid") for t in (N.P, N.E, N.T)]
        try:
            stmt = H[name](N, con, r)
            con.executescript(stmt)
        except (ValueError, IndexError, sqlite3.Error):
            return None
        if [_rows(con, f"SELECT * FROM {t} ORDER BY rowid") for t in (N.P, N.E, N.T)] == before:
            return None   # it changed nothing
        out.append(stmt)
    return out


# ---- queries: (sql, naive readings that must give another result, options) -----------------------------------------

def _nn(xs):
    return [x for x in xs if x is not None]


def _mid(r, xs):
    xs = sorted(set(xs))
    return xs[r.randrange(len(xs) // 4, max(len(xs) // 4 + 1, 3 * len(xs) // 4))]


def _window(D, r, lo: int, hi: int, col: str) -> str:
    """A condition on the event day (text comparison, as SQLite does it) that keeps lo..hi events, two on one day."""
    days = sorted({t[3] for t in D.T})
    cands = []
    for i in range(len(days)):
        for j in range(i + 1, len(days) + 1):
            rows = [t[3] for t in D.T if days[i] <= t[3] and (j == len(days) or t[3] < days[j])]
            if lo <= len(rows) <= hi and len(set(rows)) < len(rows):
                cands.append(f"{col} >= '{days[i]}'" + (f" AND {col} < '{days[j]}'" if j < len(days) else ""))
    return r.choice(cands)


def _with_events(D, n=2):
    cnt = {}
    for t in D.T:
        cnt[t[1]] = cnt.get(t[1], 0) + 1
    return [e[0] for e in D.E if cnt.get(e[0], 0) >= n]


def q_filter(N, D, r):
    k = _mid(r, _nn(e[3] for e in D.E))
    return f"SELECT name, {N.es} FROM {N.E} WHERE {N.es} >= {k} ORDER BY {N.es} DESC, name;", [], {}


def q_count(N, D, r):
    return f"SELECT {N.ep}, COUNT(*) FROM {N.E} GROUP BY {N.ep} ORDER BY {N.ep};", [], {}


def q_sum(N, D, r):
    k = r.choice(_with_events(D, 2))
    return f"SELECT COUNT(*), SUM({N.ta}), MAX({N.ta}) FROM {N.T} WHERE {N.te} = {k};", [], {}


def q_having(N, D, r):
    tot = {}
    for t in D.T:
        if t[2] is not None:
            tot[t[1]] = tot.get(t[1], 0) + t[2]
    k = _mid(r, list(tot.values())) - 1
    return (f"SELECT {N.te}, SUM({N.ta}) AS s FROM {N.T} GROUP BY {N.te} HAVING s > {k} ORDER BY s DESC, {N.te};",
            [], {"min_rows": 2})


def q_limit(N, D, r):
    return f"SELECT name, {N.es} FROM {N.E} ORDER BY {N.es} DESC, name LIMIT 3 OFFSET {r.choice([1, 2])};", [], {}


def q_intdiv(N, D, r):
    return (f"SELECT {N.ep}, COUNT(*), SUM({N.es}) / COUNT(*), SUM({N.es}) % COUNT(*) FROM {N.E} GROUP BY {N.ep} ORDER BY {N.ep};",
            [f"SELECT {N.ep}, COUNT(*), SUM({N.es}) * 1.0 / COUNT(*), SUM({N.es}) % COUNT(*) FROM {N.E} GROUP BY {N.ep} ORDER BY {N.ep};"], {})


def q_counts(N, D, r):
    return (f"SELECT COUNT(*), COUNT({N.es}), COUNT({N.ep}), COUNT(DISTINCT {N.ep}) FROM {N.E};",
            [f"SELECT COUNT(*), COUNT(*), COUNT(*), COUNT(DISTINCT IFNULL({N.ep}, 0)) FROM {N.E};"], {})


def q_ne(N, D, r):
    x = r.choice(_nn(t[4] for t in D.T))
    return (f"SELECT COUNT(*), SUM({N.ta}) FROM {N.T} WHERE {N.tt} <> '{x}';",
            [f"SELECT COUNT(*), SUM({N.ta}) FROM {N.T} WHERE {N.tt} IS NOT '{x}';"], {})


def q_avg(N, D, r):
    return (f"SELECT {N.ep}, COUNT({N.es}), SUM({N.es}) / COUNT({N.es}), AVG({N.es}) FROM {N.E} WHERE {N.ep} IS NOT NULL "
            f"GROUP BY {N.ep} ORDER BY {N.ep};",
            [f"SELECT {N.ep}, COUNT(*), SUM({N.es}) / COUNT(*), SUM({N.es}) / COUNT({N.es}) FROM {N.E} WHERE {N.ep} IS NOT NULL "
             f"GROUP BY {N.ep} ORDER BY {N.ep};"], {})


def q_nomatch(N, D, r):
    return (f"SELECT e.name FROM {N.E} e LEFT JOIN {N.T} t ON t.{N.te} = e.id WHERE t.id IS NULL ORDER BY e.name;", [], {})


def q_notin(N, D, r):
    return (f"SELECT name FROM {N.P} WHERE id NOT IN (SELECT {N.ep} FROM {N.E}) ORDER BY name;",
            [f"SELECT name FROM {N.P} WHERE id NOT IN (SELECT {N.ep} FROM {N.E} WHERE {N.ep} IS NOT NULL) ORDER BY name;"],
            {"empty_ok": True})


def q_onwhere(N, D, r):
    x = r.choice(_nn(t[4] for t in D.T))
    on = (f"SELECT e.name, COUNT(t.id), SUM(t.{N.ta}) FROM {N.E} e LEFT JOIN {N.T} t ON t.{N.te} = e.id AND t.{N.tt} = '{x}' "
          f"GROUP BY e.id ORDER BY e.name;")
    wh = (f"SELECT e.name, COUNT(t.id), SUM(t.{N.ta}) FROM {N.E} e LEFT JOIN {N.T} t ON t.{N.te} = e.id WHERE t.{N.tt} = '{x}' "
          f"GROUP BY e.id ORDER BY e.name;")
    return (on, [wh], {}) if r.random() < 0.5 else (wh, [on], {})


def q_nullsort(N, D, r):
    k = r.choice([3, 4])
    return (f"SELECT name, {N.es} FROM {N.E} ORDER BY {N.es}, name LIMIT {k};",
            [f"SELECT name, {N.es} FROM {N.E} ORDER BY {N.es} IS NULL, {N.es}, name LIMIT {k};"], {})


def q_coalesce(N, D, r):
    return (f"SELECT COALESCE({N.tt}, 'none') AS k, COUNT(*), COUNT({N.ta}), SUM({N.ta}) FROM {N.T} GROUP BY k ORDER BY k;",
            [f"SELECT COALESCE({N.tt}, 'none') AS k, COUNT(*), COUNT(*), TOTAL({N.ta}) FROM {N.T} GROUP BY k ORDER BY k;"], {})


def q_nullgroup(N, D, r):
    return (f"SELECT {N.tt}, COUNT(*), COUNT({N.ta}), SUM({N.ta}) FROM {N.T} GROUP BY {N.tt} ORDER BY {N.tt};",
            [f"SELECT {N.tt}, COUNT(*), COUNT({N.ta}), SUM({N.ta}) FROM {N.T} GROUP BY {N.tt} ORDER BY {N.tt} IS NULL, {N.tt};"], {})


def q_case(N, D, r):
    sc = sorted(_nn(e[3] for e in D.E))
    k1, k2 = sc[len(sc) * 3 // 4], sc[len(sc) // 3]
    if k2 >= k1:
        k2 = k1 - 1
    return (f"SELECT name, {N.es}, CASE WHEN {N.es} >= {k1} THEN 'high' WHEN {N.es} >= {k2} THEN 'mid' END FROM {N.E} ORDER BY name;",
            [f"SELECT name, {N.es}, CASE WHEN {N.es} >= {k1} THEN 'high' WHEN {N.es} >= {k2} THEN 'mid' ELSE 'low' END FROM {N.E} ORDER BY name;"], {})


def q_union(N, D, r):
    k = _mid(r, _nn(t[2] for t in D.T))
    j = r.choice(_with_events(D, 2))
    a = f"SELECT {N.tt} FROM {N.T} WHERE {N.ta} > {k} UNION SELECT {N.tt} FROM {N.T} WHERE {N.te} = {j} OR {N.te} IS NULL ORDER BY 1;"
    return a, [a.replace(" UNION ", " UNION ALL ")], {}


def q_textorder(N, D, r):
    return (f"SELECT name, {N.ec} FROM {N.E} ORDER BY {N.ec} DESC, name LIMIT 4;",
            [f"SELECT name, {N.ec} FROM {N.E} ORDER BY CAST({N.ec} AS INTEGER) DESC, name LIMIT 4;"], {})


def q_concat(N, D, r):
    return (f"SELECT e.name || ' @ ' || p.{N.pz} FROM {N.E} e LEFT JOIN {N.P} p ON p.id = e.{N.ep} ORDER BY e.id;",
            [f"SELECT e.name || ' @ ' || IFNULL(p.{N.pz}, '') FROM {N.E} e LEFT JOIN {N.P} p ON p.id = e.{N.ep} ORDER BY e.id;"], {})


def q_rank(N, D, r):
    return (f"SELECT name, {N.es}, RANK() OVER (ORDER BY {N.es} DESC), DENSE_RANK() OVER (ORDER BY {N.es} DESC) FROM {N.E} "
            f"WHERE {N.es} IS NOT NULL ORDER BY {N.es} DESC, name;",
            [f"SELECT name, {N.es}, DENSE_RANK() OVER (ORDER BY {N.es} DESC), RANK() OVER (ORDER BY {N.es} DESC) FROM {N.E} "
             f"WHERE {N.es} IS NOT NULL ORDER BY {N.es} DESC, name;"], {})


def q_running(N, D, r):
    w = _window(D, r, 5, 9, N.td)
    return (f"SELECT id, {N.td}, {N.ta}, SUM({N.ta}) OVER (ORDER BY {N.td}) FROM {N.T} WHERE {w} ORDER BY {N.td}, id;",
            [f"SELECT id, {N.td}, {N.ta}, SUM({N.ta}) OVER (ORDER BY {N.td}, id ROWS UNBOUNDED PRECEDING) FROM {N.T} WHERE {w} "
             f"ORDER BY {N.td}, id;"], {})


def q_lag(N, D, r):
    k = r.choice(_with_events(D, 3) or _with_events(D, 2))
    return (f"SELECT id, {N.ta}, {N.ta} - LAG({N.ta}) OVER (ORDER BY id), LAG({N.ta}, 2, 0) OVER (ORDER BY id) FROM {N.T} "
            f"WHERE {N.te} = {k} OR {N.ta} IS NULL ORDER BY id;",
            [f"SELECT id, {N.ta}, {N.ta} - LAG({N.ta}, 1, 0) OVER (ORDER BY id), LAG({N.ta}, 2) OVER (ORDER BY id) FROM {N.T} "
             f"WHERE {N.te} = {k} OR {N.ta} IS NULL ORDER BY id;"], {})


def q_like(N, D, r):
    letters = sorted({c for e in D.E for c in e[1].lower() if c.isascii() and c.isalpha()})
    ch = r.choice(letters)
    return (f"SELECT name FROM {N.E} WHERE name LIKE '%{ch}%' ORDER BY name;",
            [f"SELECT name FROM {N.E} WHERE name GLOB '*{ch}*' ORDER BY name;",
             f"SELECT name FROM {N.E} WHERE name LIKE '%{ch}%' ORDER BY name COLLATE NOCASE;"], {"min_rows": 2})


def q_affinity(N, D, r):
    k = r.choice([20, 40, 50, 60, 80, 90])
    return (f"SELECT name, {N.ec} FROM {N.E} WHERE {N.ec} > {k} ORDER BY name;",
            [f"SELECT name, {N.ec} FROM {N.E} WHERE CAST({N.ec} AS INTEGER) > {k} ORDER BY name;"], {})


def q_partrun(N, D, r):
    a, b = r.sample(_with_events(D, 3) or _with_events(D, 2), 2)
    return (f"SELECT {N.te}, {N.td}, {N.ta}, SUM({N.ta}) OVER (PARTITION BY {N.te} ORDER BY {N.td}) FROM {N.T} "
            f"WHERE {N.te} IN ({a}, {b}) OR ({N.te} IS NULL) ORDER BY {N.te}, {N.td}, id;",
            [f"SELECT {N.te}, {N.td}, {N.ta}, SUM({N.ta}) OVER (PARTITION BY {N.te} ORDER BY {N.td}, id ROWS UNBOUNDED PRECEDING) "
             f"FROM {N.T} WHERE {N.te} IN ({a}, {b}) OR ({N.te} IS NULL) ORDER BY {N.te}, {N.td}, id;"], {})


def q_chain(N, D, r):
    base = (f"WITH RECURSIVE chain(id, name, depth) AS (SELECT id, name, 0 FROM {N.E} WHERE {N.eb} IS NULL UNION ALL "
            f"SELECT e.id, e.name, c.depth + 1 FROM {N.E} e JOIN chain c ON e.{N.eb} = c.id) "
            f"SELECT name, depth FROM chain WHERE depth > 0 ORDER BY depth DESC, name ")
    return base + "LIMIT 1, 4;", [base + "LIMIT 1 OFFSET 4;", base + "LIMIT 4;"], {}


def q_corr(N, D, r):
    return (f"SELECT e.name, (SELECT COUNT(*) FROM {N.T} t WHERE t.{N.te} = e.id AND t.{N.ta} > (SELECT AVG({N.ta}) FROM {N.T})) "
            f"AS n FROM {N.E} e ORDER BY n DESC, e.name;",
            [f"SELECT e.name, (SELECT COUNT(*) FROM {N.T} t WHERE t.{N.te} = e.id AND t.{N.ta} > (SELECT TOTAL({N.ta}) / COUNT(*) "
             f"FROM {N.T})) AS n FROM {N.E} e ORDER BY n DESC, e.name;"], {})


def q_month(N, D, r):
    return (f"SELECT strftime('%m', {N.td}) AS m, COUNT(*), COUNT(DISTINCT {N.te}), SUM({N.ta}) FROM {N.T} GROUP BY m ORDER BY m;",
            [f"SELECT strftime('%m', {N.td}) AS m, COUNT(*), COUNT(DISTINCT IFNULL({N.te}, 0)), SUM({N.ta}) FROM {N.T} GROUP BY m ORDER BY m;"], {})


def q_sumtext(N, D, r):
    return (f"SELECT {N.ep}, SUM({N.ec}), MAX({N.ec}), MIN({N.ec} + 0), TOTAL({N.es}) FROM {N.E} GROUP BY {N.ep} ORDER BY {N.ep};",
            [f"SELECT {N.ep}, SUM(CAST({N.ec} AS INTEGER)), MAX(CAST({N.ec} AS INTEGER)), MIN({N.ec} + 0), TOTAL({N.es}) FROM {N.E} "
             f"GROUP BY {N.ep} ORDER BY {N.ep};"], {})


def q_datemath(N, D, r):
    return (f"SELECT name, {N.ed}, date({N.ed}, '+1 month'), julianday('2026-06-30') - julianday({N.ed}) FROM {N.E} "
            f"WHERE strftime('%d', {N.ed}) > '27' ORDER BY {N.ed}, name;", [], {"min_rows": 2})


def q_frames(N, D, r):
    days = sorted(t[3] for t in D.T if t[2] is not None)
    cut = days[min(len(days) - 1, r.randint(6, 8))]
    return (f"SELECT id, {N.ta}, SUM({N.ta}) OVER (ORDER BY {N.ta}, id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING), "
            f"SUM({N.ta}) OVER (ORDER BY {N.ta} RANGE BETWEEN 10 PRECEDING AND CURRENT ROW), COUNT(*) OVER (ORDER BY {N.ta}) "
            f"FROM {N.T} WHERE {N.ta} IS NOT NULL AND {N.td} < '{cut}' ORDER BY {N.ta}, id;",
            [f"SELECT id, {N.ta}, SUM({N.ta}) OVER (ORDER BY {N.ta}, id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING), "
             f"SUM({N.ta}) OVER (ORDER BY {N.ta}, id ROWS BETWEEN 1 PRECEDING AND CURRENT ROW), COUNT(*) OVER (ORDER BY {N.ta}, id) "
             f"FROM {N.T} WHERE {N.ta} IS NOT NULL AND {N.td} < '{cut}' ORDER BY {N.ta}, id;"], {"max_rows": 10})


def q_notexists(N, D, r):
    return (f"SELECT (SELECT COUNT(*) FROM {N.P} WHERE id NOT IN (SELECT {N.ep} FROM {N.E})), "
            f"(SELECT COUNT(*) FROM {N.P} p WHERE NOT EXISTS (SELECT 1 FROM {N.E} e WHERE e.{N.ep} = p.id)), "
            f"(SELECT COUNT(*) FROM {N.E} WHERE {N.ep} NOT IN (SELECT id FROM {N.P}));",
            [f"SELECT (SELECT COUNT(*) FROM {N.P} p WHERE NOT EXISTS (SELECT 1 FROM {N.E} e WHERE e.{N.ep} = p.id)), "
             f"(SELECT COUNT(*) FROM {N.P} p WHERE NOT EXISTS (SELECT 1 FROM {N.E} e WHERE e.{N.ep} = p.id)), "
             f"(SELECT COUNT(*) FROM {N.E} WHERE IFNULL({N.ep}, 0) NOT IN (SELECT id FROM {N.P}));"], {})


def q_badmonth(N, D, r):
    return (f"SELECT strftime('%Y-%m', {N.td}) AS m, COUNT(*), SUM({N.ta}), MIN({N.td}) FROM {N.T} GROUP BY m ORDER BY m;",
            [f"SELECT substr({N.td}, 1, 7) AS m, COUNT(*), SUM({N.ta}), MIN({N.td}) FROM {N.T} GROUP BY m ORDER BY m;"], {})


def q_round(N, D, r):
    return (f"SELECT name, {N.es}, ROUND({N.es} / 4.0, 1), ROUND({N.es} / 8.0), ROUND({N.es}, -1) FROM {N.E} "
            f"WHERE {N.es} % 4 <> 0 OR {N.es} % 8 = 4 ORDER BY name;", [], {"min_rows": 3, "need": "round"})


def q_limitnull(N, D, r):
    base = f"SELECT name, {N.es} FROM {N.E} ORDER BY {N.es} DESC, name "
    n = len(D.E)
    return base + f"LIMIT {n - 5}, 4;", [base + f"LIMIT {n - 5} OFFSET 4;", base.replace("DESC", "DESC NULLS FIRST") + f"LIMIT {n - 5}, 4;"], {}


def q_lastvalue(N, D, r):
    w = _window(D, r, 6, 10, N.td)
    return (f"SELECT id, {N.td}, COUNT(*) OVER (ORDER BY {N.td}), LAST_VALUE({N.td}) OVER (ORDER BY {N.td}), "
            f"NTILE(3) OVER (ORDER BY {N.td}, id) FROM {N.T} WHERE {w} ORDER BY {N.td}, id;",
            [f"SELECT id, {N.td}, COUNT(*) OVER (ORDER BY {N.td}, id), LAST_VALUE({N.td}) OVER (ORDER BY {N.td} ROWS BETWEEN "
             f"UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING), NTILE(3) OVER (ORDER BY {N.td}, id) FROM {N.T} WHERE {w} "
             f"ORDER BY {N.td}, id;"], {})


def q_havingnull(N, D, r):
    k = r.choice([2, 3])
    return (f"SELECT e.{N.ep}, COUNT(*), COUNT(t.id), SUM(t.{N.ta}) FROM {N.E} e LEFT JOIN {N.T} t ON t.{N.te} = e.id "
            f"GROUP BY e.{N.ep} HAVING COUNT(t.id) >= {k} OR SUM(t.{N.ta}) IS NULL ORDER BY e.{N.ep};",
            [f"SELECT e.{N.ep}, COUNT(*), COUNT(*), SUM(t.{N.ta}) FROM {N.E} e JOIN {N.T} t ON t.{N.te} = e.id "
             f"GROUP BY e.{N.ep} HAVING COUNT(t.id) >= {k} ORDER BY e.{N.ep};"], {})


# -- levels 9-10: several rules in every query --------------------------------------------------------------------------

def c_winjoin(N, D, r):
    per = {}
    for e in D.E:
        if e[2] is not None:
            per[e[2]] = per.get(e[2], 0) + sum(1 for t in D.T if t[1] == e[0])
    pairs = [(a, b) for a in per for b in per if a < b and 7 <= per[a] + per[b] <= 17]
    p1, p2 = r.choice(pairs)
    x = r.choice(_nn(t[4] for t in D.T))
    sql = (f"SELECT e.name, t.{N.td}, t.{N.ta},\n       SUM(t.{N.ta}) OVER (PARTITION BY e.id ORDER BY t.{N.td}) AS run,\n"
           f"       RANK() OVER (ORDER BY t.{N.ta} DESC) AS rk\nFROM {N.E} e JOIN {N.T} t ON t.{N.te} = e.id\n"
           f"WHERE t.{N.tt} <> '{x}' AND e.{N.ep} IN ({p1}, {p2})\nORDER BY e.name, t.{N.td}, t.id;")
    return (sql, [sql.replace(f"ORDER BY t.{N.td}) AS run", f"ORDER BY t.{N.td}, t.id ROWS UNBOUNDED PRECEDING) AS run"),
                  sql.replace(f"t.{N.tt} <> '{x}'", f"t.{N.tt} IS NOT '{x}'")], {"min_rows": 6, "max_rows": 16})


def c_leftagg(N, D, r):
    k = _mid(r, _nn(e[3] for e in D.E))
    sql = (f"SELECT p.name, COUNT(*), COUNT(e.id), SUM(e.{N.es}), AVG(e.{N.es}), MAX(e.{N.ec})\n"
           f"FROM {N.P} p LEFT JOIN {N.E} e ON e.{N.ep} = p.id AND e.{N.es} > {k}\nGROUP BY p.id\nORDER BY p.name;")
    return sql, [sql.replace(f"ON e.{N.ep} = p.id AND e.{N.es} > {k}\n", f"ON e.{N.ep} = p.id\nWHERE e.{N.es} > {k}\n")], {}


def c_notinunion(N, D, r):
    z = r.choice(_nn(p[2] for p in D.P))
    sql = (f"SELECT name AS v FROM {N.E} WHERE {N.ep} NOT IN (SELECT id FROM {N.P} WHERE {N.pz} = '{z}')\n"
           f"UNION\nSELECT {N.pz} FROM {N.P}\nORDER BY v;")
    return sql, [sql.replace(f"WHERE {N.ep} NOT IN", f"WHERE IFNULL({N.ep}, 0) NOT IN"), sql.replace("UNION", "UNION ALL"),
                 sql.replace("ORDER BY v", "ORDER BY v COLLATE NOCASE")], {}


def c_chainpath(N, D, r):
    sql = (f"WITH RECURSIVE chain(id, path, depth) AS (\n  SELECT id, name, 0 FROM {N.E} WHERE {N.eb} IS NULL\n  UNION ALL\n"
           f"  SELECT e.id, c.path || '>' || e.name, c.depth + 1 FROM {N.E} e JOIN chain c ON e.{N.eb} = c.id\n)\n"
           f"SELECT path, depth FROM chain WHERE depth >= 1 ORDER BY path;")
    return sql, [], {"min_rows": 4}


def c_scalar(N, D, r):
    k = _mid(r, _nn(t[2] for t in D.T))
    sql = (f"SELECT e.name,\n       (SELECT t.{N.ta} FROM {N.T} t WHERE t.{N.te} = e.id ORDER BY t.{N.td} DESC, t.id DESC) AS latest,\n"
           f"       (SELECT COUNT(*) FROM {N.T} t WHERE t.{N.te} = e.id AND t.{N.ta} > {k}) * 100\n"
           f"         / (SELECT COUNT(*) FROM {N.T} t WHERE t.{N.te} = e.id) AS pct\n"
           f"FROM {N.E} e\nORDER BY latest DESC, e.name\nLIMIT {len(D.E) - 6}, 5;")
    return sql, [sql.replace(f"LIMIT {len(D.E) - 6}, 5", f"LIMIT {len(D.E) - 6} OFFSET 5"), sql.replace(") * 100\n", ") * 100.0\n"),
                 sql.replace("ORDER BY latest DESC, e.name", "ORDER BY latest DESC NULLS FIRST, e.name")], {}


def c_roundover(N, D, r):
    sql = (f"SELECT {N.ep}, COUNT({N.es}), ROUND(AVG({N.es}), 1), ROUND(SUM({N.es}) / COUNT({N.es})), ROUND(MAX({N.es}), -1),\n"
           f"       MAX({N.es}) * 100000000000000000\nFROM {N.E}\nGROUP BY {N.ep}\nORDER BY {N.ep};")
    return sql, [], {"need": "overflow"}


def c_notingroups(N, D, r):
    p = r.choice(sorted({e[2] for e in D.E if e[2] is not None}))
    sql = (f"SELECT t.{N.tt}, COUNT(*), SUM(t.{N.ta}), MIN(t.{N.td})\nFROM {N.T} t\n"
           f"WHERE t.{N.te} NOT IN (SELECT e.id FROM {N.E} e WHERE e.{N.ep} = {p})\n  AND t.{N.te} IN (SELECT id FROM {N.E})\n"
           f"GROUP BY t.{N.tt}\nORDER BY t.{N.tt};")
    return sql, [sql.replace(f"WHERE t.{N.te} NOT IN", f"WHERE IFNULL(t.{N.te}, 0) NOT IN").replace(
        f"  AND t.{N.te} IN (SELECT id FROM {N.E})\n", "")], {}


def c_textcode(N, D, r):
    sql = (f"SELECT p.name,\n       (SELECT COUNT(*) FROM {N.E} e WHERE e.{N.ep} = p.id) AS n,\n"
           f"       (SELECT MAX({N.ec}) FROM {N.E} e WHERE e.{N.ep} = p.id) AS top,\n"
           f"       (SELECT SUM({N.ec}) FROM {N.E} e WHERE e.{N.ep} = p.id) AS s,\n"
           f"       (SELECT GROUP_CONCAT(c, '+') FROM (SELECT {N.ec} AS c FROM {N.E} e WHERE e.{N.ep} = p.id ORDER BY {N.ec})) AS codes\n"
           f"FROM {N.P} p\nWHERE p.id IN (SELECT {N.ep} FROM {N.E} WHERE {N.ec} > 50) OR p.{N.pz} IS NULL\nORDER BY top DESC, p.name;")
    return sql, [sql.replace(f"ORDER BY {N.ec})", f"ORDER BY CAST({N.ec} AS INTEGER))")], {}


def c_dates(N, D, r):
    sql = (f"SELECT strftime('%m', {N.td}) AS m, COUNT(*), SUM(strftime('%w', {N.td}) IN ('0', '6')) AS weekend,\n"
           f"       SUM({N.ta}), MAX({N.td})\nFROM {N.T}\nWHERE {N.td} >= '2026-02-01'\nGROUP BY m\nORDER BY m;")
    return sql, [sql.replace(f"WHERE {N.td} >= '2026-02-01'", f"WHERE date({N.td}) >= '2026-02-01'")], {}


def c_lagcase(N, D, r):
    ks = _with_events(D, 4) or _with_events(D, 3)
    a, b = r.sample(ks, 2)
    sql = (f"SELECT id, {N.ta},\n       LAG({N.ta}, 1, 0) OVER w,\n       LEAD({N.ta}) OVER w,\n"
           f"       CASE WHEN {N.ta} > LAG({N.ta}) OVER w THEN 'up' WHEN {N.ta} < LAG({N.ta}) OVER w THEN 'down' END,\n"
           f"       {N.ta} / 2\nFROM {N.T}\nWHERE {N.te} IN ({a}, {b}) OR {N.ta} IS NULL OR {N.ta} * 2 % 2 = 1\n"
           f"WINDOW w AS (PARTITION BY {N.te} ORDER BY {N.td}, id)\nORDER BY id;")
    return sql, [sql.replace(f"LAG({N.ta}, 1, 0) OVER w", f"IFNULL(LAG({N.ta}) OVER w, 0)"),
                 sql.replace(f"{N.ta} / 2\n", f"{N.ta} / 2.0\n")], {"min_rows": 6, "max_rows": 14}


def c_likeglob(N, D, r):
    firsts = sorted({e[1][0].lower() for e in D.E if e[1][0].isascii()})
    c = r.choice(firsts)
    sql = (f"SELECT name, upper(substr(name, 1, 1)) || lower(substr(name, 2)) AS nice, name LIKE '{c}%', name GLOB '{c.upper()}*',\n"
           f"       name > 'm'\nFROM {N.E}\nWHERE name LIKE '%a%' OR name LIKE '%e%' OR {N.ec} LIKE '1%'\nORDER BY name;")
    return sql, [sql.replace("ORDER BY name;", "ORDER BY name COLLATE NOCASE;")], {"need": "odd"}


def c_datecalc(N, D, r):
    sql = (f"SELECT name, {N.ed}, date({N.ed}, '+1 month'), date({N.ed}, 'start of month', '+1 month', '-1 day'),\n"
           f"       CAST(julianday('2026-06-30') - julianday({N.ed}) AS INTEGER) / 7, substr({N.ed}, 0, 5)\n"
           f"FROM {N.E}\nWHERE strftime('%d', {N.ed}) >= '28' OR {N.ed} < '2024-04-01'\nORDER BY {N.ed}, name;")
    return sql, [], {"min_rows": 3}


def c_frames(N, D, r):
    w = _window(D, r, 8, 12, N.td)
    sql = (f"SELECT id, {N.td}, {N.ta},\n       COUNT(*) OVER (ORDER BY {N.td}),\n"
           f"       AVG({N.ta}) OVER (ORDER BY {N.td}, id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING),\n"
           f"       NTILE(4) OVER (ORDER BY {N.td}, id),\n       PERCENT_RANK() OVER (ORDER BY {N.td})\n"
           f"FROM {N.T}\nWHERE {w}\nORDER BY {N.td}, id;")
    return sql, [sql.replace(f"COUNT(*) OVER (ORDER BY {N.td})", f"COUNT(*) OVER (ORDER BY {N.td}, id)")], {}


def c_targets(N, D, r):
    sql = (f"SELECT p.name, x.{N.xm}, x.{N.xg}, SUM(t.{N.ta}) AS got, SUM(t.{N.ta}) >= x.{N.xg} AS met\n"
           f"FROM {N.X} x JOIN {N.P} p ON p.id = x.{N.xp}\nLEFT JOIN {N.E} e ON e.{N.ep} = p.id\n"
           f"LEFT JOIN {N.T} t ON t.{N.te} = e.id AND strftime('%Y-%m', t.{N.td}) = x.{N.xm}\n"
           f"GROUP BY x.{N.xp}, x.{N.xm}\nORDER BY p.name, x.{N.xm};")
    return sql, [sql.replace(f"LEFT JOIN {N.T} t ON t.{N.te} = e.id AND strftime('%Y-%m', t.{N.td}) = x.{N.xm}\n",
                             f"LEFT JOIN {N.T} t ON t.{N.te} = e.id\nWHERE strftime('%Y-%m', t.{N.td}) = x.{N.xm}\n")], {}


def c_groupalias(N, D, r):
    """GROUP BY a name that is both an output alias and a column binds to the column: 'Books' and 'books' stay apart."""
    sql = (f"SELECT upper({N.tt}) AS {N.tt}, COUNT(*), SUM({N.ta}), max(MIN({N.ta}), 20)\nFROM {N.T}\nGROUP BY {N.tt}\n"
           f"ORDER BY 1, 2, 3, 4;")
    return sql, [sql.replace(f"GROUP BY {N.tt}\n", "GROUP BY 1\n")], {}


def c_textfuncs(N, D, r):
    """substr with a negative length counts back, hex() of a number is the hex of its text, IN applies the column's
    affinity to the list, printf rounds 11.25 half away from zero (C and Python give 11.2), printf of NULL is 0.0."""
    sql = (f"SELECT name, substr(name, 3, -2), hex({N.es} % 10), {N.ec} IN (7, 12, 100, 1000), printf('%.1f', {N.es} / 4.0)\n"
           f"FROM {N.E}\nWHERE {N.es} % 2 = 1 OR {N.ec} < '2'\nORDER BY name;")
    return sql, [], {"min_rows": 4, "need": "half"}


def c_setops(N, D, r):
    """Compound SELECTs run left to right ((A EXCEPT B) UNION C) and treat NULLs as equal."""
    k = _mid(r, _nn(t[2] for t in D.T))
    j = r.choice(_with_events(D, 2))
    a = f"SELECT {N.tt} FROM {N.T} WHERE {N.ta} > {k}"
    b = f"SELECT {N.tt} FROM {N.T} WHERE {N.te} = {j}"
    c = f"SELECT {N.tt} FROM {N.T} WHERE {N.ta} IS NULL OR {N.te} IS NULL"
    return (f"{a}\nEXCEPT\n{b}\nUNION\n{c}\nORDER BY 1;", [f"{a}\nEXCEPT\nSELECT * FROM ({b} UNION {c})\nORDER BY 1;",
                                                        f"{a}\nEXCEPT\n{b}\nUNION ALL\n{c}\nORDER BY 1;"], {"empty_ok": True})


Q = {f.__name__: f for f in [c_groupalias, c_textfuncs, c_setops,q_filter, q_count, q_sum, q_having, q_limit, q_intdiv, q_counts, q_ne, q_avg, q_nomatch, q_notin,
                             q_onwhere, q_nullsort, q_coalesce, q_nullgroup, q_case, q_union, q_textorder, q_concat, q_rank,
                             q_running, q_lag, q_like, q_affinity, q_partrun, q_chain, q_corr, q_month, q_sumtext, q_datemath,
                             q_frames, q_notexists, q_badmonth, q_round, q_limitnull, q_lastvalue, q_havingnull, c_winjoin,
                             c_leftagg, c_notinunion, c_chainpath, c_scalar, c_roundover, c_notingroups, c_textcode, c_dates,
                             c_lagcase, c_likeglob, c_datecalc, c_frames, c_targets]}
MIX = {
    1: ["q_filter", "q_count", "q_sum"],
    2: ["q_having", "q_limit", "q_intdiv"],
    3: ["q_counts", "q_ne", "q_avg", "q_nomatch"],
    4: ["q_notin", "q_onwhere", "q_nullsort", "q_coalesce"],
    5: ["q_nullgroup", "q_case", "q_union", "q_textorder", "q_concat"],
    6: ["q_rank", "q_running", "q_lag", "q_like", "q_affinity"],
    7: ["q_partrun", "q_chain", "q_corr", "q_month", "q_sumtext", "q_datemath"],
    8: ["q_frames", "q_notexists", "q_badmonth", "q_round", "q_limitnull", "q_lastvalue"],
    9: ["c_winjoin", "c_notinunion", "c_chainpath", "c_scalar", "c_roundover", "c_notingroups", "c_setops"],
    10: ["c_winjoin", "c_textcode", "c_dates", "c_lagcase", "c_likeglob", "c_textfuncs", "c_frames", "c_targets", "c_groupalias"],
}
# c_leftagg and c_datecalc: levels 9-10 before the v0.11 hardening (answered right by the frontier reference)


# ---- the item ----------------------------------------------------------------------------------------------------------

def cell(v) -> str:
    """How a value is written in an answer: NULL, whole numbers for INTEGER, repr for REAL, text as it is ('' for empty)."""
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return v if v else "''"
    return str(v)


def _usable(res, opts: dict) -> bool:
    if res == "ERROR":
        return False
    _cols, rows = res
    n = len(rows)
    if n == 0 and not opts.get("empty_ok"):
        return False
    if n < opts.get("min_rows", 1 if not opts.get("empty_ok") else 0) or n > opts.get("max_rows", 16):
        return False
    for row in rows:
        for v in row:
            if isinstance(v, str) and (v != v.strip() or "|" in v or "\n" in v or v.upper() == "NULL"):
                return False
            if isinstance(v, float) and v != v or v in (float("inf"), float("-inf")):
                return False
            if isinstance(v, bytes):
                return False
    return True


def _needs(res, need: str | None) -> bool:
    _cols, rows = res
    if need == "overflow":   # an INTEGER and a REAL (overflowed) product next to each other
        last = [row[-1] for row in rows if row[-1] is not None]
        return any(isinstance(v, int) for v in last) and any(isinstance(v, float) for v in last)
    if need == "round":      # a .25/.75 to round to one decimal, and an x.5 to round to a whole number
        vals = [row[1] for row in rows]
        return any(v % 4 in (1, 3) for v in vals) and any(v % 8 == 4 for v in vals)
    if need == "odd":
        return any(not row[0][0].isascii() for row in rows)
    if need == "half":       # printf of an x.25 or x.75 to one decimal
        return any(isinstance(row[-1], str) and "." in row[-1] for row in rows)
    return True


PROMPT_FMT = (
    "\n\nWhat does each query return? Think it through, then finish with the results in exactly this form:\n"
    "RESULTS\nRESULT 1\n<first row>\n<second row>\n...\nRESULT 2\n<first row>\n...\nEND\n"
    "One line per row, in the order SQLite returns the rows, the columns in the query's order separated by ` | `, no header "
    "line. Write NULL for NULL, text as it is (no quotes; an empty string as ''), INTEGER values as whole numbers (7), REAL "
    "values with a decimal point or an exponent as SQLite prints them (7.0, 2.5, 1.5e+19; long fractions to at least 6 "
    "decimals). If a query returns no rows, write (no rows); if it fails with an error, write ERROR.")


def sql(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"sql{level}", seed)
    th = THEMES[r.randrange(len(THEMES))]
    N = NS(**{k: v for k, v in th.items() if k not in ("pnames", "zones", "tags")})
    for _attempt in range(60):
        D = _data(r, level, th)
        script = _schema(N, D, level)
        con = _connect(script)
        history = _history(N, con, r, level) if level in HISTORY else []
        if history is None:
            con.close()
            continue
        if history:   # the queries' parameters are chosen on the changed tables
            D.P, D.E, D.T = (_rows(con, f"SELECT * FROM {t} ORDER BY id") for t in (N.P, N.E, N.T))
        queries = []
        for name in MIX[level]:
            for _try in range(12):
                try:
                    q, naive, opts = Q[name](N, D, r)
                except (ValueError, IndexError):   # this database has too few rows of the kind the query needs
                    continue
                res = run_query(con, q)
                if not _usable(res, opts) or not _needs(res, opts.get("need")):
                    continue
                if any(run_query(con, nv) == res for nv in naive):
                    continue
                queries.append((q, res))
                break
            else:
                break
        con.close()
        if len(queries) == len(MIX[level]):
            break
    else:
        raise RuntimeError(f"techhelp.sql L{level} seed {seed}: no database where every rule bites")
    r.shuffle(queries)
    body = "\n\n".join(f"Query {i + 1}:\n```sql\n{q}\n```" for i, (q, _res) in enumerate(queries))
    changes = ("Then these statements change it, one after another:\n\n```sql\n" + "\n".join(history) + "\n```\n\n") if history else ""
    prompt = (f"These statements create and fill a SQLite database (SQLite 3.46 or newer, default settings):\n\n"
              f"```sql\n{script}\n```\n\n{changes}Then each of these queries runs on it, on its own:\n\n{body}" + PROMPT_FMT)
    exp = [res[1] for _q, res in queries]
    cols = [res[0] for _q, res in queries]
    return Item(f"{BLOCK}.sql.L{level}.{seed}", BLOCK, "sql", [{"role": "user", "content": prompt}],
                make_check(exp, cols), max_tokens=32000,
                meta={"level": level, "setup": "\n".join([script] + history), "queries": [q for q, _res in queries],
                      "columns": cols, "expected": [[" | ".join(cell(v) for v in row) for row in rows] for rows in exp]})


# ---- grading -----------------------------------------------------------------------------------------------------------

_HEAD = re.compile(r"^[\s#>*_`-]*(?:RESULT|QUERY)\s*#?\s*(\d{1,2})\b\s*(?:\*\*|__)?\s*[:.)\-–—]?\s*(?:\*\*|__)?\s*(.*?)\s*$", re.I)
_RESULTS = re.compile(r"^[\s#>*_`-]*RESULTS\W*$", re.I)
_END = re.compile(r"^[\s#>*_`-]*END\W*$", re.I)
_NOROWS = re.compile(r"^[\s*_`(\[]*(?:no rows|empty|empty result|0 rows|no rows returned|empty set)[\s*_`)\].]*$", re.I)
_ERROR = re.compile(r"^[\s*_`(\[]*error\b", re.I)
_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _uncell(p: str) -> str:
    p = p.strip()
    for _ in range(2):
        m = re.fullmatch(r"`(.*)`", p) or re.fullmatch(r"\*\*(.*)\*\*", p)
        if m:
            p = m.group(1).strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in "'\"":
        p = p[1:-1]
    return p


def _cells(line: str) -> list[str]:
    s = line.strip()
    if len(s) > 1 and s.startswith("|") and s.endswith("|"):
        s = s[1:-1]
    return [_uncell(p) for p in s.split("|")]


def parse(text: str) -> dict:
    """{query number: list of rows (lists of cells) | 'ERROR'} from the answer; the last section per number wins."""
    lines = strip_think(text or "").splitlines()
    starts = [i for i, ln in enumerate(lines) if _RESULTS.match(ln)]
    if starts:
        lines = lines[starts[-1] + 1:]
    out: dict = {}
    i = 0
    while i < len(lines):
        m = _HEAD.match(lines[i])
        if not m:
            i += 1
            continue
        k, trail = int(m.group(1)), m.group(2).strip().strip("*_`").strip()
        rows, err, j = [], False, i + 1
        if trail and not trail.endswith(":") and not (trail.startswith("(") and not _NOROWS.match(trail)):
            if _ERROR.match(trail):
                err = True
            elif not _NOROWS.match(trail):
                rows.append(_cells(trail))
        while j < len(lines):
            ln = lines[j]
            if _HEAD.match(ln) or _END.match(ln) or _RESULTS.match(ln):
                break
            if not ln.strip():
                if rows:   # a blank line after rows ends the result, unless the rows go on after it
                    nxt = next((x for x in lines[j + 1:] if x.strip()), "")
                    if not nxt or _HEAD.match(nxt) or _END.match(nxt) or _FENCE.match(nxt) or len(_cells(nxt)) != len(rows[-1]) \
                            or len(rows[-1]) < 2:
                        break
                j += 1
                continue
            if _FENCE.match(ln) or _SEP.match(ln):
                j += 1
                continue
            if _NOROWS.match(ln):
                j += 1
                continue
            if _ERROR.match(ln) and not rows:
                err = True
                j += 1
                continue
            rows.append(_cells(ln))
            j += 1
        out[k] = "ERROR" if err and not rows else rows
        i = j
    return out


def cell_ok(v, got: str) -> bool:
    if v is None:
        return got.upper() == "NULL"
    if isinstance(v, bool) or isinstance(v, int):
        return bool(re.fullmatch(r"[+-]?\d+", got)) and int(got) == int(v)
    if isinstance(v, float):
        if not re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+(?:\.\d*)?[eE][+-]?\d+)", got):
            return False
        return abs(float(got) - v) <= 1.01e-6 * max(1.0, abs(v))
    if isinstance(v, str):
        return got == v
    return False


def _lcs(exp: list, got: list, eq) -> int:
    n, m = len(exp), len(got)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if eq(exp[i], got[j]) else max(dp[i + 1][j], dp[i][j + 1])
    return dp[0][0]


def query_score(exp, got, cols: list[str]) -> float:
    if got is None:
        return 0.0
    if exp == "ERROR" or got == "ERROR":
        return 1.0 if exp == got else 0.0
    if got and len(got[0]) == len(cols) and [c.lower() for c in got[0]] == [c.lower() for c in cols]:
        got = got[1:]   # a header line after all
    if not exp:
        return 1.0 if not got else 0.0
    eq = lambda e, g: len(g) == len(e) and all(cell_ok(v, x) for v, x in zip(e, g))
    return _lcs(exp, got, eq) / max(len(exp), len(got))


def make_check(exp: list, cols: list):
    def check(text: str, _t=None) -> float:
        got = parse(text)
        return sum(query_score(e, got.get(i + 1), c) for i, (e, c) in enumerate(zip(exp, cols))) / len(exp)
    return check


def oracle(it: Item) -> str:
    return "RESULTS\n" + "\n".join(f"RESULT {i + 1}\n" + ("\n".join(rows) if rows else "(no rows)") if rows != "ERROR"
                                   else f"RESULT {i + 1}\nERROR" for i, rows in enumerate(it.meta["expected"])) + "\nEND"


def variants(text: str) -> list[str]:
    """The oracle's answer in other shapes a model writes: a preamble, padded cells and blank lines, code fences, a
    markdown table, bold headers with colons and no RESULTS / END lines."""
    lines = text.splitlines()
    body = [ln for ln in lines if ln not in ("RESULTS", "END")]
    rowish = lambda ln: not _HEAD.match(ln)
    pad = "\n".join(("  " + ln.replace(" | ", "  |   ") + "   ") if rowish(ln) else ("\n" + ln + "  ") for ln in lines)
    table = "\n".join(("| " + ln + " |") if rowish(ln) and ln != "(no rows)" and ln not in ("RESULTS", "END") else ln for ln in lines)
    bold = "\n".join((f"**{ln}:**" if _HEAD.match(ln) else ln) for ln in body)
    fenced = re.sub(r"(RESULT \d+)\n", r"\1\n```\n", text).replace("\nRESULT", "\n```\nRESULT").replace("\nEND", "\n```\nEND")
    return ["Let me work through each query.\nQuery 1 filters rows first.\n\n" + text, pad, "```\n" + text + "\n```", table, bold, fenced]


KINDS = {"sql": sql}
