"""Code writing with hidden tests (Python / JavaScript). Specs vary per seed; tests come from a reference implementation.

Model code runs in a sandbox: macOS sandbox-exec (no network, writes only to its temp dir), or bubblewrap on Linux
when available. Without a sandbox the block refuses to run rather than executing untrusted code.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile

from .common import Item, rng, strip_think

BLOCK = "code"


def _ref_rle(s: str, k: int, sep: str) -> str:
    out, i = [], 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] == s[i]:
            j += 1
        n = j - i
        out.append(f"{n}{sep}{s[i]}" if n >= k else s[i] * n)
        i = j
    return "".join(out)


def _ref_merge(iv: list, g: int) -> list:
    res = []
    for a, b in sorted(iv):
        if res and a - res[-1][1] <= g:
            res[-1][1] = max(res[-1][1], b)
        else:
            res.append([a, b])
    return res


def _ref_levels(lines: list, kw: str) -> dict:
    out = {}
    for ln in lines:
        m = re.match(r"^(DEBUG|INFO|WARN|ERROR)\s+\[(\d{2}:\d{2}:\d{2})\]\s+(.*)$", ln)
        if m and kw.lower() in m.group(3).lower():
            out[m.group(1)] = out.get(m.group(1), 0) + 1
    return dict(sorted(out.items()))


def _ref_base(n: int, b: int, digits: str) -> str:
    if n == 0:
        return digits[0]
    neg, n, s = n < 0, abs(n), ""
    while n:
        s = digits[n % b] + s
        n //= b
    return ("-" if neg else "") + s


def _ref_topk(rows: list, k: int) -> list:
    tot = {}
    for key, v in rows:
        tot[key] = tot.get(key, 0) + v
    return [key for key, _ in sorted(tot.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def _spec(kind: str, r):
    if kind == "rle":
        k, sep = r.randint(2, 4), r.choice(["", "x", ":"])
        cases = ["".join(r.choice("aab") * r.randint(1, 6) for _ in range(r.randint(1, 6))) for _ in range(10)] + [""]
        desc = (f"compress(s: string) -> string. Replace every run of the same character of length >= {k} with "
                f"'<count>{sep}<char>' (e.g. 'aaaa' -> '4{sep}a' when {k} <= 4); shorter runs stay unchanged.")
        return "compress", desc, [[c] for c in cases], [_ref_rle(c, k, sep) for c in cases]
    if kind == "merge":
        g = r.randint(0, 3)
        cases = [[[a, a + r.randint(0, 5)] for a in (r.randint(0, 40) for _ in range(r.randint(0, 7)))] for _ in range(10)]
        desc = (f"merge(intervals: list of [start, end] integer pairs) -> list of [start, end]. Sort by start and merge two "
                f"intervals when the next start minus the current end is <= {g}. Return the merged list sorted by start.")
        return "merge", desc, [[c] for c in cases], [_ref_merge(c, g) for c in cases]
    if kind == "levels":
        kw = r.choice(["timeout", "disk", "retry", "auth"])
        pool = ["INFO [10:00:01] started", f"ERROR [10:01:02] {kw} while calling db", f"WARN [10:02:03] {kw.upper()} rising",
                f"DEBUG [10:03:04] no {kw} here", "garbage line", f"ERROR[10:05:06] {kw} (bad format)", f"INFO [1:02:03] {kw}",
                f"ERROR [11:00:00] second {kw} error", f"TRACE [10:00:00] {kw}"]
        cases = [[r.choice(pool) for _ in range(r.randint(0, 9))] for _ in range(10)]
        desc = (f"count_levels(lines: list of strings) -> object/dict mapping level to count. A valid line is "
                f"'<LEVEL> [HH:MM:SS] <message>' with LEVEL in DEBUG, INFO, WARN, ERROR, exactly one space before '[', two-digit "
                f"hour/minute/second, and a space after ']'. Count valid lines whose message contains '{kw}' (case-insensitive). "
                f"Omit levels with zero count; keys sorted alphabetically.")
        return "count_levels", desc, [[c] for c in cases], [_ref_levels(c, kw) for c in cases]
    if kind == "base":
        b = r.randint(3, 9)
        digits = "".join(r.sample("0123456789ABCDEFGHJK", b))
        cases = [r.randint(-500, 5000) for _ in range(10)] + [0]
        desc = (f"to_base(n: integer) -> string. Write n in base {b} using the digit alphabet '{digits}' (index = digit value, "
                f"so zero is '{digits[0]}'). Negative numbers get a leading '-'.")
        return "to_base", desc, [[c] for c in cases], [_ref_base(c, b, digits) for c in cases]
    k = r.randint(2, 4)
    cases = [[[r.choice("abcdefg"), r.randint(1, 9)] for _ in range(r.randint(0, 12))] for _ in range(10)]
    desc = (f"top_keys(rows: list of [key: string, value: integer]) -> list of keys. Sum values per key and return the "
            f"{k} keys with the largest sums, ties broken alphabetically; fewer if there are fewer keys.")
    return "top_keys", desc, [[c] for c in cases], [_ref_topk(c, k) for c in cases]


_PY_HARNESS = """import json, sys
sys.path.insert(0, '.')
from solution import {fn}
cases = json.load(open('tests.json'))
ok = 0
for args, exp in cases:
    try:
        got = {fn}(*args)
        ok += json.dumps(got, sort_keys=True) == json.dumps(exp, sort_keys=True)
    except Exception:
        pass
print(json.dumps({{"passed": ok, "total": len(cases)}}))
"""
_JS_HARNESS = """const fs = require('fs');
const mod = require('./solution.js');
const fn = (typeof mod === 'function') ? mod : (mod.{fn} || mod.default || Object.values(mod).find(v => typeof v === 'function'));
const norm = v => JSON.stringify(v, (k, x) => (x && typeof x === 'object' && !Array.isArray(x)) ? Object.keys(x).sort().reduce((o, key) => (o[key] = x[key], o), {{}}) : x);
const cases = JSON.parse(fs.readFileSync('tests.json', 'utf8'));
let ok = 0;
for (const [args, exp] of cases) {{ try {{ if (norm(fn(...args)) === norm(exp)) ok++; }} catch (e) {{}} }}
console.log(JSON.stringify({{passed: ok, total: cases.length}}));
"""


def _sandbox_cmd(d: str, argv: list[str]) -> list[str] | None:
    if platform.system() == "Darwin" and shutil.which("sandbox-exec"):
        prof = (f'(version 1)(allow default)(deny network*)(deny file-write*)(allow file-write* (subpath "{d}"))'
                f'(allow file-write* (literal "/dev/null"))')
        return ["sandbox-exec", "-p", prof] + argv
    if shutil.which("bwrap"):
        return ["bwrap", "--ro-bind", "/", "/", "--bind", d, d, "--unshare-net", "--dev", "/dev", "--proc", "/proc", "--chdir", d] + argv
    return None


def _extract_code(text: str, lang: str) -> str:
    t = strip_think(text)
    blocks = re.findall(r"```(\w*)\n(.*?)```", t, re.S)
    pref = [b for tag, b in blocks if tag.lower() in (("python", "py") if lang == "python" else ("javascript", "js", "node"))]
    if pref:
        return pref[-1]
    if blocks:
        return blocks[-1][1]
    return t


def _run(lang: str, fn: str, code: str, tests: list, timeout: int = 20) -> float:
    d = os.path.realpath(tempfile.mkdtemp(prefix="llmbox-code-"))
    try:
        json.dump(tests, open(os.path.join(d, "tests.json"), "w"))
        if lang == "python":
            open(os.path.join(d, "solution.py"), "w").write(code)
            open(os.path.join(d, "harness.py"), "w").write(_PY_HARNESS.format(fn=fn))
            argv = ["python3", "harness.py"]
        else:
            if "module.exports" not in code and "exports." not in code:
                code += f"\nmodule.exports = {{ {fn} }};\n"
            open(os.path.join(d, "solution.js"), "w").write(code)
            open(os.path.join(d, "harness.js"), "w").write(_JS_HARNESS.format(fn=fn))
            argv = ["node", "harness.js"]
        cmd = _sandbox_cmd(d, argv)
        if cmd is None:
            raise RuntimeError("no sandbox available (sandbox-exec or bwrap) - refusing to run model code")
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=timeout)
        m = re.search(r"\{.*\}", p.stdout)
        res = json.loads(m.group(0)) if m else {"passed": 0, "total": len(tests)}
        return res["passed"] / res["total"]
    except subprocess.TimeoutExpired:
        return 0.0
    finally:
        shutil.rmtree(d, ignore_errors=True)



# ---------------- harder kinds (levels 3-5): stateful, parsing, edge cases ----------------

def _ref_expr(src: str, env: dict) -> int:
    """Integer expression evaluator: + - * / (truncate toward zero) % ^ (right-assoc power), unary minus, parentheses,
    variables, max(a,b)/min(a,b)."""
    toks, i = [], 0
    while i < len(src):
        c = src[i]
        if c.isspace():
            i += 1
        elif c.isdigit():
            j = i
            while j < len(src) and src[j].isdigit():
                j += 1
            toks.append(("n", int(src[i:j]))); i = j
        elif c.isalpha():
            j = i
            while j < len(src) and src[j].isalnum():
                j += 1
            toks.append(("id", src[i:j])); i = j
        else:
            toks.append(("op", c)); i += 1
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else ("eof", None)

    def take():
        t = peek(); pos[0] += 1; return t

    def primary():
        t = take()
        if t[0] == "n":
            return t[1]
        if t[0] == "id":
            if peek() == ("op", "("):
                take(); a = expr(); take(); b = expr(); take()   # ( a , b )
                return max(a, b) if t[1] == "max" else min(a, b)
            return env[t[1]]
        if t == ("op", "-"):
            return -unary_pow()
        if t == ("op", "("):
            v = expr(); take(); return v
        raise ValueError(t)

    def unary_pow():
        base = primary()
        if peek() == ("op", "^"):
            take(); return base ** unary_pow()
        return base

    def term():
        v = unary_pow()
        while peek()[0] == "op" and peek()[1] in "*/%":
            op = take()[1]; rhs = unary_pow()
            if op == "*": v *= rhs
            elif op == "/": v = int(v / rhs)
            else: v = v - rhs * int(v / rhs)
        return v

    def expr():
        v = term()
        while peek()[0] == "op" and peek()[1] in "+-":
            op = take()[1]; rhs = term(); v = v + rhs if op == "+" else v - rhs
        return v
    return expr()


def _gen_expr(r, level: int, depth: int = 0) -> str:
    if depth > 1 + level // 2 or r.random() < 0.3:
        if level >= 4 and r.random() < 0.3:
            return r.choice(["x", "y", "z"])
        return str(r.randint(0, 12))
    a, b = _gen_expr(r, level, depth + 1), _gen_expr(r, level, depth + 1)
    ops = ["+", "-", "*", "/", "%"] + (["^"] if level >= 4 else [])
    op = r.choice(ops)
    if op == "^":
        return f"({a})^{r.randint(0, 3)}"
    if op in "/%":
        b = str(r.randint(1, 9))
    e = f"{a} {op} {b}"
    if level >= 5 and r.random() < 0.2:
        e = f"{r.choice(['max', 'min'])}({a}, {b})"
    if r.random() < 0.25:
        e = f"-({e})"
    return f"({e})" if r.random() < 0.5 else e


def _ref_lru(ops: list, cap: int, ttl: int | None) -> list:
    from collections import OrderedDict
    store, out = OrderedDict(), []
    for op in ops:
        kind, t = op[0], op[-1]
        if ttl is not None:
            for k in [k for k, (v, ts) in store.items() if t - ts >= ttl]:
                del store[k]
        if kind == "put":
            _, k, v, _t = op
            if k in store:
                del store[k]
            elif len(store) >= cap:
                store.popitem(last=False)
            store[k] = (v, t)
        elif kind in ("get", "peek"):
            k = op[1]
            if k in store:
                out.append(store[k][0])
                if kind == "get":
                    store.move_to_end(k)
            else:
                out.append(-1)
    return out


def _ref_csv(text: str, group: str, value: str, min_value: int) -> dict:
    import csv, io
    rows = list(csv.DictReader(io.StringIO(text)))
    out = {}
    for row in rows:
        try:
            v = int(row[value])
        except (ValueError, TypeError):
            continue
        if v >= min_value:
            out[row[group]] = out.get(row[group], 0) + v
    return dict(sorted(out.items()))


def _ref_rooms(meetings: list, buf: int, assign: bool):
    free_at, result = [], []
    order = sorted(range(len(meetings)), key=lambda i: (meetings[i][0], i))
    rooms_of = [None] * len(meetings)
    for i in order:
        s_, e = meetings[i]
        room = next((k for k, t in enumerate(free_at) if t <= s_), None)
        if room is None:
            free_at.append(0); room = len(free_at) - 1
        free_at[room] = e + buf
        rooms_of[i] = room
    return rooms_of if assign else len(free_at)


def _hard_spec(kind: str, r, level: int):
    if kind == "expr":
        cases = []
        while len(cases) < 12:
            e = _gen_expr(r, level)
            env = {"x": r.randint(-5, 9), "y": r.randint(1, 6), "z": r.randint(-3, 3)}
            try:
                v = _ref_expr(e, env)
            except (ZeroDivisionError, KeyError, ValueError, RecursionError):
                continue
            if abs(v) < 10**9:
                cases.append([[e, env], v])
        feats = "+ - * / % and parentheses, unary minus"
        if level >= 4:
            feats += ", ^ (power, right-associative, binds tighter than unary minus: -2^2 = -4) and variables from `env`"
        if level >= 5:
            feats += ", functions max(a, b) and min(a, b)"
        desc = (f"evaluate(expression: string, env: object/dict of variable values) -> integer. Integers only; supports {feats}. "
                f"'/' divides and truncates toward zero; '%' is the remainder with the sign of the dividend (a - b*trunc(a/b)). "
                f"Usual precedence: ^ > unary minus > * / % > + -, left-associative except ^. Do NOT use eval.")
        return "evaluate", desc, [c[0] for c in cases], [c[1] for c in cases]
    if kind == "lru":
        cap = r.randint(2, 4)
        ttl = r.randint(3, 6) if level >= 4 else None
        keys = ["a", "b", "c", "d", "e"]
        cases = []
        for _ in range(10):
            ops, t = [], 0
            for _ in range(r.randint(6, 12 + 2 * level)):
                t += r.randint(0, 2) if ttl else 0
                k = r.choice(keys)
                kind_ = r.choice(["put", "get", "get"] + (["peek"] if level >= 5 else []))
                ops.append(["put", k, r.randint(1, 99), t] if kind_ == "put" else [kind_, k, t])
            cases.append([[ops], _ref_lru(ops, cap, ttl)])
        desc = (f"run_cache(ops: list) -> list of integers. Simulate an LRU cache with capacity {cap}. Each op is "
                f"[\"put\", key, value, time] or [\"get\", key, time]" + (" or [\"peek\", key, time]" if level >= 5 else "") +
                ". put inserts/overwrites (overwriting refreshes recency, never evicts); when inserting a NEW key into a full cache, "
                "evict the least recently used entry first. get returns the value (or -1) and marks the key as most recently used"
                + ("; peek returns the value (or -1) WITHOUT changing recency" if level >= 5 else "")
                + (f". Entries expire: before processing any op at time t, remove every entry whose last put time ts satisfies "
                   f"t - ts >= {ttl} (gets do not refresh the expiry)" if ttl else ". Times are always 0 here and can be ignored")
                + ". Return the results of all get/peek ops in order.")
        return "run_cache", desc, [c[0] for c in cases], [c[1] for c in cases]
    if kind == "csv":
        group, value = "region", "units"
        min_value = r.randint(1, 20)
        cases = []
        for _ in range(10):
            lines = ["id,region,units,note"]
            for i in range(r.randint(3, 6 + level)):
                reg = r.choice(["North", "South", '"East, Upper"', "West"])
                units = r.choice([str(r.randint(0, 60)), "", "n/a"]) if level >= 4 else str(r.randint(0, 60))
                note = r.choice(["ok", '"said ""hi"""', '"a,b"', ""])
                lines.append(f"{i},{reg},{units},{note}")
            text = "\n".join(lines) + "\n"
            cases.append([[text], _ref_csv(text, group, value, min_value)])
        desc = (f"units_by_region(csv_text: string) -> object/dict. Parse RFC-4180 CSV with a header row (fields may be quoted; "
                f"quoted fields can contain commas and doubled quotes \"\"). Sum the integer 'units' per 'region' for rows with "
                f"units >= {min_value}" + ("; skip rows whose units field is empty or not an integer" if level >= 4 else "")
                + ". Keys are region names without quotes, sorted alphabetically; omit regions with no qualifying rows.")
        return "units_by_region", desc, [c[0] for c in cases], [c[1] for c in cases]
    buf = r.randint(0, 15)
    assign = level >= 4
    cases = []
    for _ in range(10):
        ms = []
        for _ in range(r.randint(1, 5 + level)):
            s_ = r.randint(0, 20) * 15
            ms.append([s_, s_ + r.choice([15, 30, 45, 60, 90])])
        cases.append([[ms], _ref_rooms(ms, buf, assign)])
    if assign:
        desc = (f"assign_rooms(meetings: list of [start, end] minutes) -> list of room indices (same order as input). Rooms need "
                f"{buf} minutes of cleaning after each meeting (a room freed at end+{buf} can host a meeting starting exactly then). "
                f"Process meetings by start time (ties: input order) and give each the LOWEST-index room that is free; open a new "
                f"room (next index, starting at 0) only when none is free.")
        return "assign_rooms", desc, [c[0] for c in cases], [c[1] for c in cases]
    desc = (f"min_rooms(meetings: list of [start, end] minutes) -> integer. Minimum number of rooms so that no two meetings overlap "
            f"in one room, where a room needs {buf} minutes of cleaning after each meeting (free again at end+{buf}).")
    return "min_rooms", desc, [c[0] for c in cases], [c[1] for c in cases]


HARD = ("expr", "lru", "csv", "rooms")


def make(kind: str):
    def gen(seed: int, level: int = 3) -> Item:
        if level >= 9 and kind in HARD:   # levels 9-10: code_l9.py
            from . import code_l9
            return code_l9.gen(kind, seed, level)
        if level >= 7 and kind in HARD:
            return _gen7(kind, seed, level)
        r = rng(BLOCK, f"{kind}{level}", seed)
        lang = r.choice(["python", "javascript"])
        fn, desc, args, exp = _hard_spec(kind, r, level) if kind in HARD else _spec(kind, r)
        tests = [[a, e] for a, e in zip(args, exp)]
        example = max(tests, key=lambda t: len(json.dumps(t[0]))) if kind in HARD else tests[0]
        lang_name = "Python 3" if lang == "python" else "JavaScript (Node.js, CommonJS)"
        call = f"{fn}({', '.join(json.dumps(a) for a in example[0])})"
        prompt = (f"Implement this function in {lang_name}:\n\n{desc}\n\nExample: {call} returns {json.dumps(example[1])}.\n"
                  f"Use only the standard library. Reply with a single ```{'python' if lang == 'python' else 'javascript'}``` "
                  f"code block containing the complete function" + (" (export it with module.exports)." if lang != "python" else "."))

        def check(text, _t=None, lang=lang, fn=fn, tests=tests) -> float:
            # v0.10: the share of hidden tests passed (v0.5-0.9 strict all-or-nothing: 0/1 items made the block the
            # noisiest per minute of the suite; levels, not strictness, now keep strong models off the ceiling)
            return _run(lang, fn, _extract_code(text, lang), tests)
        return Item(f"{BLOCK}.{kind}.L{level}.{seed}", BLOCK, kind, [{"role": "user", "content": prompt}], check, lang=lang,
                    meta={"fn": fn, "lang": lang, "tests": len(tests), "level": level})
    return gen


SIMPLE = ("rle", "merge", "levels", "base", "topk")


# ---- expert level (6): cron schedules across time zones and DST ---------------------------------------------------

def _cron_field(spec: str, lo: int, hi: int) -> set[int]:
    out = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, st = part.split("/")
            step = int(st)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = b = int(part)
        out.update(range(a, b + 1, step))
    return out


def _ref_cron(expr: str, tz: str, start: str, n: int) -> list[str]:
    """Reference: walk UTC minute by minute, judge each minute on the local wall clock. Nonexistent local times never
    occur in the walk (spring-forward gap -> skipped); a repeated local time (fall-back) counts only at its first
    occurrence (fold == 0)."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    mi, ho, dom, mo, dow = expr.split()
    M, H, D, MO = _cron_field(mi, 0, 59), _cron_field(ho, 0, 23), _cron_field(dom, 1, 31), _cron_field(mo, 1, 12)
    W = {x % 7 for x in _cron_field(dow, 0, 7)}
    dom_any, dow_any = dom == "*", dow == "*"
    z = ZoneInfo(tz)
    t = _dt.datetime.fromisoformat(start.replace("Z", "+00:00")).replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
    out = []
    while len(out) < n:
        loc = t.astimezone(z)
        if loc.fold == 0 and loc.minute in M and loc.hour in H and loc.month in MO:
            dm, dw = loc.day in D, (loc.weekday() + 1) % 7 in W
            day = True if dom_any and dow_any else dw if dom_any else dm if dow_any else (dm or dw)
            if day:
                out.append(t.strftime("%Y-%m-%dT%H:%MZ"))
        t += _dt.timedelta(minutes=1)
    return out


_CRON_CASES = [
    ("30 2 * * *", "Europe/Madrid", "2026-03-27T12:00Z"),   # 29 Mar: 02:30 does not exist
    ("30 2 * * *", "Europe/Madrid", "2026-10-23T12:00Z"),   # 25 Oct: 02:30 happens twice
    ("0 2 * * *", "America/New_York", "2026-03-06T12:00Z"), # 8 Mar gap
    ("0 1 * * *", "America/New_York", "2026-10-30T12:00Z"), # 1 Nov overlap
    ("0 9 1,15 * 1", "America/New_York", "2026-06-01T00:00Z"),  # day-of-month OR Monday
    ("*/20 9-10 * * 1-5", "Europe/Madrid", "2026-09-25T06:00Z"),
    ("15 3 * * 0", "Europe/Madrid", "2026-10-18T00:00Z"),
    ("0 0 31 * *", "Asia/Tokyo", "2026-04-01T00:00Z"),
    ("5 4 * 2 *", "Europe/Madrid", "2026-01-30T00:00Z"),
    ("0 12 * * 7", "Australia/Sydney", "2026-04-01T00:00Z"),   # 7 = Sunday; Sydney leaves DST on 5 Apr
    ("45 23 28-31 * *", "America/New_York", "2026-02-20T00:00Z"),
    ("0 */6 * * *", "Europe/London", "2026-03-28T20:00Z"),
]


def cron(seed: int, level: int = 6) -> Item:
    """Expert: next run times of a 5-field cron schedule in an IANA time zone, across DST changes. Credit per case."""
    if level >= 9:   # levels 9-10: code_l9.py
        from . import code_l9
        return code_l9.gen("cron", seed, level)
    if level >= 7:
        return _cron7(seed, level)
    r = rng(BLOCK, f"cron{level}", seed)
    cases = r.sample(_CRON_CASES, 10)
    tests = [[[e, tz, st, 5], _ref_cron(e, tz, st, 5)] for e, tz, st in cases]
    desc = ("next_runs(expr: str, tz: str, start: str, n: int) -> list[str]. expr is a standard 5-field cron schedule "
            "'minute hour day-of-month month day-of-week' (numbers, '*', lists 'a,b', ranges 'a-b', steps '*/s' and "
            "'a-b/s'; day-of-week 0-7 with 0 and 7 = Sunday). The schedule is evaluated on the local wall clock of the IANA "
            "time zone tz. If both day-of-month and day-of-week are restricted (neither is '*'), a day matches when EITHER "
            "matches (classic cron). Return the next n run times strictly after start (UTC, 'YYYY-MM-DDTHH:MMZ'), in UTC, "
            "format 'YYYY-MM-DDTHH:MMZ'. Daylight-saving rules: a local time that does not exist (spring-forward gap) is "
            "skipped; a local time that occurs twice (fall-back) runs only once, at its first occurrence. Use zoneinfo.")
    example = tests[0]
    prompt = (f"Implement this function in Python 3:\n\n{desc}\n\nExample: next_runs({json.dumps(example[0][0])}, "
              f"{json.dumps(example[0][1])}, {json.dumps(example[0][2])}, 5) returns {json.dumps(example[1])}.\n"
              "Use only the standard library. Reply with a single ```python``` code block containing the complete function.")

    def check(text, _t=None, tests=tests) -> float:
        return _run("python", "next_runs", _extract_code(text, "python"), tests, timeout=120)   # share of the 10 cases
    return Item(f"{BLOCK}.cron.L{level}.{seed}", BLOCK, "cron", [{"role": "user", "content": prompt}], check, lang="python",
                meta={"fn": "next_runs", "lang": "python", "tests": len(tests), "level": level,
                      "expected": [t[1][0] for t in tests]})


KINDS = {k: make(k) for k in SIMPLE + HARD}
KINDS["cron"] = cron


# ---------------- levels 7-8 (v0.11): the stateful / parsing kinds with more interacting rules ------------------------
# Levels 1-6 stay exactly as they were (answers to them are pooled across suite versions); level >= 7 of a HARD kind
# branches to _gen7 with its own random stream. Each spec is a list of rules that interact, and the hidden tests aim at
# those rules one by one, so the share passed grades how many of them a solution gets right. The reference functions
# below compute the hidden tests and are the oracle's reply (validate); _JS7 are their JavaScript ports.
MAX_LEVEL = 10   # 9-10: code_l9.py


def _ref7_expr(src: str, env: dict, level: int):
    """C-like precedence with ?:, ||, && and comparisons (1/0), lazy branches, "error" for division by zero or an unknown
    variable where evaluated; level 8: ';'-separated statements with let bindings, variadic max/min and abs."""
    import re
    toks = re.findall(r"\d+|[A-Za-z_]\w*|&&|\|\||==|!=|<=|>=|\S", src)
    pos = [0]
    levels = [("||",), ("&&",), ("==", "!="), ("<", "<=", ">", ">="), ("+", "-"), ("*", "/", "%")]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

    def ternary():
        c = binary(0)
        if peek() == "?":
            take()
            a = ternary()
            take()                                   # ':'
            return ("?", c, a, ternary())
        return c

    def binary(k):
        if k == len(levels):
            return unary()
        left = binary(k + 1)
        while peek() in levels[k]:
            op = take()
            left = (op, left, binary(k + 1))
        return left

    def unary():
        if peek() in ("-", "!"):
            op = take()
            return ("u" + op, unary())
        return power()

    def power():
        base = primary()
        if peek() == "^":
            take()
            return ("^", base, unary())
        return base

    def primary():
        t = take()
        if t == "(":
            e = ternary()
            take()                                   # ')'
            return e
        if t.isdigit():
            return ("num", int(t))
        if peek() == "(":
            take()
            args = [ternary()]
            while peek() == ",":
                take()
                args.append(ternary())
            take()                                   # ')'
            return ("call", t, args)
        return ("var", t)

    stmts = []
    while True:
        if level >= 8 and peek() == "let":
            take()
            name = take()
            take()                                   # '='
            stmts.append(("let", name, ternary()))
        else:
            stmts.append(("expr", ternary()))
        if peek() != ";":
            break
        take()

    class Fail(Exception):
        pass

    def tdiv(a, b):
        q = abs(a) // abs(b)
        return q if (a < 0) == (b < 0) else -q

    def ev(e, scope):
        k = e[0]
        if k == "num":
            v = e[1]
        elif k == "var":
            if e[1] not in scope:
                raise Fail
            v = scope[e[1]]
        elif k == "u-":
            v = -ev(e[1], scope)
        elif k == "u!":
            v = int(ev(e[1], scope) == 0)
        elif k == "?":
            v = ev(e[2], scope) if ev(e[1], scope) != 0 else ev(e[3], scope)
        elif k == "&&":
            v = int(ev(e[1], scope) != 0 and ev(e[2], scope) != 0)
        elif k == "||":
            v = int(ev(e[1], scope) != 0 or ev(e[2], scope) != 0)
        elif k == "call":
            args = [ev(a, scope) for a in e[2]]
            v = max(args) if e[1] == "max" else min(args) if e[1] == "min" else abs(args[0])
        else:
            a, b = ev(e[1], scope), ev(e[2], scope)
            if k in ("/", "%") and b == 0:
                raise Fail
            if k == "+":
                v = a + b
            elif k == "-":
                v = a - b
            elif k == "*":
                v = a * b
            elif k == "/":
                v = tdiv(a, b)
            elif k == "%":
                v = a - b * tdiv(a, b)
            elif k == "^":
                v = a ** b
            else:
                v = int({"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[k])
        if level >= 8:
            v = (v + 2 ** 31) % 2 ** 32 - 2 ** 31   # signed 32-bit wrap-around
        elif abs(v) > 2 ** 31:
            raise OverflowError(v)               # the generator drops such cases (JavaScript numbers stay exact)
        return v

    scope = dict(env)
    try:
        v = None
        for st in stmts:
            if st[0] == "let":
                v = ev(st[2], scope)
                scope[st[1]] = v
            else:
                v = ev(st[1], scope)
        return v
    except Fail:
        return "error"


def _ref7_lru(ops: list, cap: int, ttl: int, level: int) -> list:
    """LRU with per-entry ttl, del and resize; level 8: a total-size limit, sliding expiry on get, and size reports."""
    from collections import OrderedDict
    store, out = OrderedDict(), []           # key -> [value, size, clock start, ttl]; last = most recently used
    for op in ops:
        kind = op[0]
        if kind == "put":
            if level >= 8:
                key, value, size, t = op[1:5]
                life = op[5] if len(op) > 5 else ttl
            else:
                key, value, t = op[1:4]
                size, life = 1, op[4] if len(op) > 4 else ttl
        else:
            t = op[-1]
        for k in [k for k, e in store.items() if t - e[2] >= e[3]]:
            del store[k]
        if kind == "put":
            if level >= 8:
                if size > cap:
                    continue
                store.pop(key, None)
                store[key] = [value, size, t, life]
                while sum(e[1] for e in store.values()) > cap:
                    store.popitem(last=False)
            else:
                if key in store:
                    del store[key]
                elif len(store) >= cap:
                    store.popitem(last=False)
                store[key] = [value, 1, t, life]
        elif kind in ("get", "peek"):
            e = store.get(op[1])
            out.append(e[0] if e else -1)
            if e and kind == "get":
                store.move_to_end(op[1])
                if level >= 8:
                    e[2] = t
        elif kind == "del":
            out.append(0 if store.pop(op[1], None) is None else 1)
        elif kind == "resize":
            cap, n0 = op[1], len(store)
            while (sum(e[1] for e in store.values()) if level >= 8 else len(store)) > cap:
                store.popitem(last=False)
            out.append(n0 - len(store))
        else:                                    # "size"
            out.append(sum(e[1] for e in store.values()))
    return out


def _ref7_csv(text: str, min_units: int, level: int) -> dict:
    """A hand-written RFC 4180 reader (CRLF, quoted commas, quotes and line breaks), header columns by name in any order,
    validation per record; level 8: comment lines, void records, the last record per id wins, and the max per region."""
    import re
    records, i, n = [], 0, len(text)
    while i < n:
        if level >= 8 and text[i] == "#":        # a comment line (only at the start of a record, never inside quotes)
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        row = []
        while True:
            field = ""
            if i < n and text[i] == '"':
                i += 1
                while i < n:
                    if text[i] == '"':
                        if i + 1 < n and text[i + 1] == '"':
                            field += '"'
                            i += 2
                        else:
                            i += 1
                            break
                    else:
                        field += text[i]
                        i += 1
            while i < n and text[i] not in ",\r\n":
                field += text[i]
                i += 1
            row.append(field)
            if i < n and text[i] == ",":
                i += 1
                continue
            if i < n and text[i] == "\r":
                i += 1
            if i < n and text[i] == "\n":
                i += 1
            break
        records.append(row)
    if not records:
        return {}
    head = [h.strip().lower() for h in records[0]]
    kept = []
    for rec in records[1:]:
        if len(rec) != len(head):
            continue
        get = lambda name: rec[head.index(name)].strip()
        region, units = get("region"), get("units")
        if not region or not re.fullmatch(r"[+-]?[0-9]+", units):
            continue
        if level >= 8 and get("status").lower() == "void":
            continue
        kept.append((get("id"), region, int(units)))
    if level >= 8:
        last = {rid: k for k, (rid, _r, _u) in enumerate(kept)}
        kept = [x for k, x in enumerate(kept) if last[x[0]] == k]
    out = {}
    for _rid, region, units in kept:
        if units < min_units:
            continue
        e = out.setdefault(region, {"units": 0, "rows": 0})
        e["units"] += units
        e["rows"] += 1
        if level >= 8:
            e["max"] = max(e.get("max", units), units)
    return out


def _ref7_rooms(rooms: list, meetings: list, maint: list, buf: int, big: int, level: int) -> list:
    """Fixed rooms with capacities: smallest room that fits and is free (longer cleaning after big meetings), -1 when
    none; level 8: maintenance windows, organizers who cannot be in two meetings at once, sticky rooms per organizer."""
    order = sorted(range(len(meetings)), key=lambda i: (meetings[i][0], i))
    free, last, busy_until, out = [0] * len(rooms), {}, {}, [-1] * len(meetings)
    for i in order:
        s, e, a = meetings[i][:3]
        org = meetings[i][3] if level >= 8 else None
        if level >= 8 and busy_until.get(org, -1) > s:
            continue   # the organizer's previous placed meeting is still running
        ok = [k for k in range(len(rooms)) if rooms[k] >= a and free[k] <= s
              and not any(m[0] == k and s < m[2] and m[1] < e for m in maint)]
        if not ok:
            continue
        k = last[org] if level >= 8 and last.get(org) in ok else min(ok, key=lambda k: (rooms[k], k))
        out[i] = k
        free[k] = e + (2 * buf if a > big else buf)
        if level >= 8:
            last[org], busy_until[org] = k, e
    return out


_JS7 = {
    "expr": r"""
function evaluate(src, env) {
  const level = P.level;
  const toks = src.match(/\d+|[A-Za-z_]\w*|&&|\|\||==|!=|<=|>=|\S/g) || [];
  let pos = 0;
  const peek = () => (pos < toks.length ? toks[pos] : null);
  const take = () => toks[pos++];
  const levels = [["||"], ["&&"], ["==", "!="], ["<", "<=", ">", ">="], ["+", "-"], ["*", "/", "%"]];
  function ternary() {
    const c = binary(0);
    if (peek() === "?") { take(); const a = ternary(); take(); return ["?", c, a, ternary()]; }
    return c;
  }
  function binary(k) {
    if (k === levels.length) return unary();
    let left = binary(k + 1);
    while (peek() !== null && levels[k].includes(peek())) { const op = take(); left = [op, left, binary(k + 1)]; }
    return left;
  }
  function unary() {
    if (peek() === "-" || peek() === "!") { const op = take(); return ["u" + op, unary()]; }
    return power();
  }
  function power() {
    const base = primary();
    if (peek() === "^") { take(); return ["^", base, unary()]; }
    return base;
  }
  function primary() {
    const t = take();
    if (t === "(") { const e = ternary(); take(); return e; }
    if (/^\d+$/.test(t)) return ["num", parseInt(t, 10)];
    if (peek() === "(") {
      take(); const args = [ternary()];
      while (peek() === ",") { take(); args.push(ternary()); }
      take(); return ["call", t, args];
    }
    return ["var", t];
  }
  const stmts = [];
  while (true) {
    if (level >= 8 && peek() === "let") { take(); const name = take(); take(); stmts.push(["let", name, ternary()]); }
    else stmts.push(["expr", ternary()]);
    if (peek() !== ";") break;
    take();
  }
  const FAIL = {};
  const tdiv = (a, b) => { const q = Math.floor(Math.abs(a) / Math.abs(b)); return (a < 0) === (b < 0) ? q : -q; };
  const cmp = { "==": (a, b) => a === b, "!=": (a, b) => a !== b, "<": (a, b) => a < b, "<=": (a, b) => a <= b,
                ">": (a, b) => a > b, ">=": (a, b) => a >= b };
  function ev(e, scope) {
    const k = e[0];
    let v;
    if (k === "num") v = e[1];
    else if (k === "var") { if (!Object.prototype.hasOwnProperty.call(scope, e[1])) throw FAIL; v = scope[e[1]]; }
    else if (k === "u-") v = -ev(e[1], scope);
    else if (k === "u!") v = ev(e[1], scope) === 0 ? 1 : 0;
    else if (k === "?") v = ev(e[1], scope) !== 0 ? ev(e[2], scope) : ev(e[3], scope);
    else if (k === "&&") v = (ev(e[1], scope) !== 0 && ev(e[2], scope) !== 0) ? 1 : 0;
    else if (k === "||") v = (ev(e[1], scope) !== 0 || ev(e[2], scope) !== 0) ? 1 : 0;
    else if (k === "call") {
      const args = e[2].map(a => ev(a, scope));
      v = e[1] === "max" ? Math.max(...args) : e[1] === "min" ? Math.min(...args) : Math.abs(args[0]);
    } else {
      const a = ev(e[1], scope), b = ev(e[2], scope);
      if ((k === "/" || k === "%") && b === 0) throw FAIL;
      if (k === "+") v = a + b;
      else if (k === "-") v = a - b;
      else if (k === "*") v = level >= 8 ? Math.imul(a, b) : a * b;
      else if (k === "/") v = tdiv(a, b);
      else if (k === "%") v = a - b * tdiv(a, b);
      else if (k === "^") {
        if (level >= 8) { v = 1; for (let i = 0; i < b; i++) v = Math.imul(v, a); } else v = a ** b;
      } else v = cmp[k](a, b) ? 1 : 0;
    }
    if (level >= 8) v = v | 0;
    return v === 0 ? 0 : v;
  }
  const scope = Object.assign({}, env);
  try {
    let v = null;
    for (const st of stmts) {
      if (st[0] === "let") { v = ev(st[2], scope); scope[st[1]] = v; } else v = ev(st[1], scope);
    }
    return v;
  } catch (x) { if (x === FAIL) return "error"; throw x; }
}
module.exports = { evaluate };
""",
    "lru": r"""
function run_cache(ops) {
  let cap = P.cap;
  const level = P.level, store = new Map(), out = [];
  const total = () => { let s = 0; for (const e of store.values()) s += e.size; return s; };
  for (const op of ops) {
    const kind = op[0];
    let t, key, value, size, life;
    if (kind === "put") {
      if (level >= 8) { [, key, value, size, t] = op; life = op.length > 5 ? op[5] : P.ttl; }
      else { [, key, value, t] = op; size = 1; life = op.length > 4 ? op[4] : P.ttl; }
    } else t = op[op.length - 1];
    for (const [k, e] of [...store.entries()]) if (t - e.ts >= e.ttl) store.delete(k);
    if (kind === "put") {
      if (level >= 8) {
        if (size > cap) continue;
        store.delete(key);
        store.set(key, { value, size, ts: t, ttl: life });
        while (total() > cap) store.delete(store.keys().next().value);
      } else {
        if (store.has(key)) store.delete(key);
        else if (store.size >= cap) store.delete(store.keys().next().value);
        store.set(key, { value, size: 1, ts: t, ttl: life });
      }
    } else if (kind === "get" || kind === "peek") {
      const e = store.get(op[1]);
      out.push(e ? e.value : -1);
      if (e && kind === "get") { store.delete(op[1]); store.set(op[1], e); if (level >= 8) e.ts = t; }
    } else if (kind === "del") out.push(store.delete(op[1]) ? 1 : 0);
    else if (kind === "resize") {
      cap = op[1];
      const n0 = store.size;
      while ((level >= 8 ? total() : store.size) > cap) store.delete(store.keys().next().value);
      out.push(n0 - store.size);
    } else out.push(total());
  }
  return out;
}
module.exports = { run_cache };
""",
    "csv": r"""
function summarize(text) {
  const level = P.level, n = text.length, records = [];
  let i = 0;
  while (i < n) {
    if (level >= 8 && text[i] === "#") { const j = text.indexOf("\n", i); i = j < 0 ? n : j + 1; continue; }
    const row = [];
    while (true) {
      let field = "";
      if (i < n && text[i] === '"') {
        i++;
        while (i < n) {
          if (text[i] === '"') {
            if (i + 1 < n && text[i + 1] === '"') { field += '"'; i += 2; } else { i++; break; }
          } else { field += text[i]; i++; }
        }
      }
      while (i < n && text[i] !== "," && text[i] !== "\r" && text[i] !== "\n") { field += text[i]; i++; }
      row.push(field);
      if (i < n && text[i] === ",") { i++; continue; }
      if (i < n && text[i] === "\r") i++;
      if (i < n && text[i] === "\n") i++;
      break;
    }
    records.push(row);
  }
  if (!records.length) return {};
  const head = records[0].map(h => h.trim().toLowerCase());
  let kept = [];
  for (const rec of records.slice(1)) {
    if (rec.length !== head.length) continue;
    const get = name => rec[head.indexOf(name)].trim();
    const region = get("region"), units = get("units");
    if (!region || !/^[+-]?[0-9]+$/.test(units)) continue;
    if (level >= 8 && get("status").toLowerCase() === "void") continue;
    kept.push([get("id"), region, parseInt(units, 10)]);
  }
  if (level >= 8) {
    const last = new Map();
    kept.forEach((x, k) => last.set(x[0], k));
    kept = kept.filter((x, k) => last.get(x[0]) === k);
  }
  const out = {};
  for (const [, region, units] of kept) {
    if (units < P.min) continue;
    const e = out[region] || (out[region] = { units: 0, rows: 0 });
    e.units += units;
    e.rows += 1;
    if (level >= 8) e.max = Math.max(e.max === undefined ? units : e.max, units);
  }
  return out;
}
module.exports = { summarize };
""",
    "rooms": r"""
function assign_rooms(rooms, meetings, maint) {
  maint = maint || [];
  const order = meetings.map((m, i) => i).sort((a, b) => meetings[a][0] - meetings[b][0] || a - b);
  const free = rooms.map(() => 0), out = meetings.map(() => -1), last = new Map(), busyUntil = new Map();
  for (const i of order) {
    const [s, e, a] = meetings[i];
    const org = meetings[i][3];
    if (P.level >= 8 && busyUntil.has(org) && busyUntil.get(org) > s) continue;
    const ok = [];
    rooms.forEach((cap, k) => {
      if (cap >= a && free[k] <= s && !maint.some(m => m[0] === k && s < m[2] && m[1] < e)) ok.push(k);
    });
    if (!ok.length) continue;
    let k;
    if (P.level >= 8 && last.has(org) && ok.includes(last.get(org))) k = last.get(org);
    else k = ok.reduce((b, x) => (rooms[x] < rooms[b] || (rooms[x] === rooms[b] && x < b)) ? x : b);
    out[i] = k;
    free[k] = e + (a > P.big ? 2 * P.buf : P.buf);
    if (P.level >= 8) { last.set(org, k); busyUntil.set(org, e); }
  }
  return out;
}
module.exports = { assign_rooms };
""",
}


def _gen7_expr(r, depth: int, names: list) -> str:
    """A random expression over the level-7 grammar; subexpressions are parenthesized only sometimes, so precedence and
    associativity decide the value."""
    if depth <= 0 or r.random() < 0.2:
        return r.choice(names) if names and r.random() < 0.4 else str(r.randint(0, 12))
    sub = lambda: _gen7_expr(r, depth - 1, names)
    wrap = lambda s: s if re.fullmatch(r"\w+", s) else f"({s})"
    op = r.choice(["+", "-", "*", "/", "%", "<", "<=", ">", ">=", "==", "!=", "&&", "||", "?", "u-", "u!", "^", "max", "min"])
    if op == "u-":
        return "-" + wrap(sub())
    if op == "u!":
        return "!" + wrap(sub())
    if op == "^":
        return f"{wrap(sub())}^{r.randint(0, 3)}"
    if op in ("max", "min"):
        return f"{op}({sub()}, {sub()})"
    loose = lambda: sub() if r.random() < 0.5 else wrap(sub())
    if op == "?":
        return f"{loose()} ? {loose()} : {loose()}"
    if op in ("/", "%"):
        return f"{loose()} {op} {r.randint(1, 9) if r.random() < 0.7 else wrap(sub())}"
    return f"{loose()} {op} {loose()}"


def _spec7(kind: str, r, level: int):
    """(function name, description, [[args, expected], ...], params) for a level-7/8 item."""
    if kind == "expr":
        env = lambda: {"x": r.randint(-5, 9), "y": r.randint(1, 6), "z": r.randint(-3, 3)}
        n_ = lambda lo=2, hi=9: r.randint(lo, hi)
        edge = [
            lambda: (f"-{n_()}^2 + {n_()}", env()),                                   # ^ binds tighter than unary minus
            lambda: (f"{n_(2, 3)}^{n_(1, 2)}^{n_(0, 2)}", env()),                     # ^ is right-associative
            lambda: (f"-{n_(7, 30)} / {n_(2, 5)} * {n_()}", env()),                   # truncation toward zero, left to right
            lambda: (f"-{n_(7, 30)} % {n_(2, 5)} + {n_(7, 30)} % -{n_(2, 5)}", env()),  # remainder takes the dividend's sign
            lambda: (lambda a, b, d, c: (f"{a} < {a + b} == {d + c} < {d}", env()))(n_(1, 5), n_(1, 4), n_(2, 6), n_(1, 5)),
            # comparisons bind tighter than ==: (1) == (0) is 0, left to right would give 1
            lambda: (f"{n_(1, 9)} || 0 && 0", env()),                                 # && binds tighter than ||
            lambda: (f"0 && {n_()} / 0", env()),                                      # && short-circuits
            lambda: (f"x > {n_(-3, 3)} || y / (z - z)", env()),                       # || short-circuits (or errors)
            lambda: (f"0 ? {n_()} : {n_()} ? {n_()} : {n_()}", env()),                # ?: is right-associative
            lambda: (f"{n_()} ? 0 ? {n_()} : {n_()} : {n_()}", env()),                # nested in the middle
            lambda: (f"x == x + 1 ? {n_()} / 0 : x - y", env()),                      # only the chosen branch runs
            lambda: (f"!{n_(0, 1)} + !!{n_()} - !0 * {n_()}", env()),                 # ! binds tighter than * and +
            lambda: (f"--{n_()} - -{n_()}", env()),                                   # repeated unary minus
            lambda: (f"max({n_()}, {n_()} / (y - y))", env()),                        # arguments are always evaluated
            lambda: (f"0 ? q : {n_()}", env()),                                       # unknown name, skipped
            lambda: ("q * 0 + x", env()),                                              # unknown name, evaluated
            lambda: (f"(x > y) + (x >= y) * 2 + (x != y) * 4 + {n_()} % {n_()} ^ 2", env()),
            lambda: (f"-x ^ 2 + (-x) ^ 2 - min(x, -{n_()}) * max(y, {n_()})", env()),
        ]
        edge8 = [
            lambda: (f"let a = {n_()} * 2; let b = a - {n_()}; a * b", env()),
            lambda: (f"let x = x + {n_()}; x * x - y", env()),                         # the right side sees the old x
            lambda: (f"let t = 0; t ? {n_()} / t : {n_()}", env()),
            lambda: (f"let u = {n_()} / 0; {n_()}", env()),                           # an unused let still errors
            lambda: (f"let m = max({n_()}, {n_()}, {n_()}); m - min(m, {n_()}, {n_()})", env()),
            lambda: (f"abs(-{n_()}) + abs({n_()} - {n_(10, 20)}) * abs(z)", env()),
            lambda: ("let y = y * 2; let y = y + 1; y", env()),
            lambda: (f"let k = {n_(2, 4)};\n  k ^ 2 - k", env()),
            lambda: (f"let a_1 = {n_()}; let b2 = a_1 * a_1; b2 - a_1 * z", env()),
            lambda: (f"let v = 0 || {n_(0, 2)} && {n_(0, 2)}; v + (v ? 10 : 20)", env()),
            lambda: (f"max({n_()}) + min({n_()})", env()),
            lambda: (f"let w = x < 0 ? -x : x; let x = 7; w * 10 + x", env()),
        ]
        wrap8 = [   # signed 32-bit wrap-around
            lambda: (f"2147483647 + {n_(1, 9)}", env()),
            lambda: (f"-2147483647 - {n_(2, 9)}", env()),
            lambda: (f"{r.randint(40000, 99999)} * {r.randint(40000, 99999)}", env()),
            lambda: ("(-2147483647 - 1) / -1", env()),
            lambda: (f"2^31 + {n_()}", env()),
            lambda: (f"let big = {r.randint(46341, 99999)}; big * big % 1000", env()),
            lambda: (f"{n_(2, 3)}^{r.randint(20, 32)} - 1", env()),
            lambda: ("abs(-2147483647 - 1) + 0 * x", env()),
        ]
        picks = r.sample(edge, 9) + (r.sample(edge8, 6) + r.sample(wrap8, 3) if level >= 8 else [])
        cases = []
        for f in picks:
            src_, env_ = f()
            cases.append([[src_, env_], _ref7_expr(src_, env_, level)])
        while len(cases) < (16 if level == 7 else 24):
            env_ = env()
            if level >= 8 and r.random() < 0.7:
                names, parts = ["x", "y", "z"], []
                for k in range(r.randint(1, 3)):
                    nm = r.choice(["a", "b", "c", "t1", "x", "acc_2"])
                    parts.append(f"let {nm} = {_gen7_expr(r, 2, names)}")
                    names = sorted(set(names + [nm]))
                parts.append(_gen7_expr(r, 3, names))
                src_ = "; ".join(parts)
            else:
                src_ = _gen7_expr(r, 4, ["x", "y", "z"])
            try:
                v = _ref7_expr(src_, env_, level)
            except (OverflowError, RecursionError):
                continue
            if v != "error":
                cases.append([[src_, env_], v])
        first = next(i for i, c in enumerate(cases) if c[1] != "error" and len(c[0][0]) > 12)
        cases.insert(0, cases.pop(first))
        rules = [
            "evaluate(expression: string, env: object/dict of integer variables) -> integer, or the string \"error\".",
            "Integers only. Operators from LOWEST to HIGHEST precedence:",
            "  1. c ? a : b   conditional, right-associative (a ? b : c ? d : e means a ? b : (c ? d : e)); only the chosen branch is evaluated",
            "  2. ||          logical or, left-associative; result 1 or 0; the right side is evaluated only when the left side is 0",
            "  3. &&          logical and, left-associative; result 1 or 0; the right side is evaluated only when the left side is not 0",
            "  4. == !=       left-associative, result 1 or 0",
            "  5. < <= > >=   left-associative, result 1 or 0",
            "  6. + -         left-associative",
            "  7. * / %       left-associative; '/' truncates toward zero; '%' is a - b*trunc(a/b) (sign of the dividend)",
            "  8. - !         prefix unary minus and logical not (!v is 1 if v is 0, else 0); they may repeat (--3 is 3)",
            "  9. ^           power, right-associative (2^3^2 = 2^9) and tighter than a unary operator before it (-2^2 = -4); exponents are never negative",
            "Operands: integer literals, variables from env, parenthesized expressions and calls "
            + ("max(...) and min(...) with one or more arguments and abs(v)." if level >= 8 else "max(a, b) and min(a, b)."),
            "Any value other than 0 counts as true. Whitespace may appear between any tokens.",
            "Errors: if a division or '%' by zero happens, or a variable that is not defined is used, in a part that is actually "
            "evaluated, the result is the string \"error\" (parts skipped by ?:, && and || do not count; call arguments are "
            "always evaluated)."]
        if level >= 8:
            rules += [
                "Integers are signed 32-bit: the result of every operation and call wraps around in two's complement "
                "(2147483647 + 1 = -2147483648, 65536 * 65536 = 0, 2^31 = -2147483648, abs(-2147483647 - 1) = -2147483648); "
                "literals are at most 2147483647 and exponents are small.",
                "Programs: the input is one or more statements separated by ';'. A statement is either 'let NAME = expression' "
                "or an expression. let evaluates its expression with the bindings so far, then binds NAME for the following "
                "statements (hiding an env variable or an earlier let of that name). The result is the value of the last "
                "statement (for a let: the value it bound). An error in any statement makes the result \"error\", even if the "
                "value is never used.",
                "Names are letters, digits and '_' not starting with a digit; let, max, min and abs are never variable names."]
        rules.append("Do NOT use eval or Function.")
        return "evaluate", "\n".join(rules), cases, {"level": level}
    if kind == "lru":
        cap, ttl = (r.randint(2, 4), r.randint(4, 7)) if level == 7 else (r.randint(6, 9), r.randint(4, 7))
        keys = ["a", "b", "c", "d", "e"]
        cases = []
        for _ in range(12):
            ops, t = [], 0
            for _ in range(r.randint(12, 20) if level == 7 else r.randint(16, 24)):
                t += r.choice([0, 0, 1, 1, 2, 3])
                kind_ = r.choices(["put", "get", "peek", "del", "resize", "size"], [5, 5, 2, 1.5, 0.8, 1.2 if level >= 8 else 0])[0]
                k = r.choice(keys)
                if kind_ == "put":
                    if level >= 8:
                        op = ["put", k, r.randint(1, 99), r.choice([1, 1, 2, 2, 3, 4, cap + 1]), t]
                        op += [r.choice([1, 2, 9])] if r.random() < 0.2 else []
                    else:
                        op = ["put", k, r.randint(1, 99), t] + ([r.choice([1, 2, 9])] if r.random() < 0.2 else [])
                elif kind_ == "resize":
                    op = ["resize", r.randint(1, 5) if level == 7 else r.randint(3, 10), t]
                elif kind_ == "size":
                    op = ["size", t]
                else:
                    op = [kind_, k, t]
                ops.append(op)
            cases.append([[ops], _ref7_lru(ops, cap, ttl, level)])
        if level == 7:
            desc = (f"run_cache(ops: list) -> list of integers. Simulate an LRU cache that holds at most {cap} entries, with a "
                    f"default time-to-live of {ttl}. Times are integers and never decrease. Ops:\n"
                    "- [\"put\", key, value, time] or [\"put\", key, value, time, ttl]: store the value. The entry's expiry clock "
                    "starts at this time, with the given ttl or the default. Overwriting a key that is in the cache replaces its "
                    "value and ttl, restarts its clock, makes it the most recently used entry and never evicts anything. Adding a "
                    "NEW key to a full cache first evicts the least recently used entry.\n"
                    "- [\"get\", key, time]: the value, or -1. A hit makes the key the most recently used (its expiry clock is NOT "
                    "restarted).\n"
                    "- [\"peek\", key, time]: the value, or -1, without changing anything.\n"
                    "- [\"del\", key, time]: remove the key; 1 if it was in the cache, else 0.\n"
                    "- [\"resize\", capacity, time]: the cache now holds at most capacity (>= 1) entries; evict least recently used "
                    "entries until it fits; returns how many were evicted.\n"
                    "Expiry: before handling ANY op at time t, remove every entry whose clock started at ts with t - ts >= its ttl.\n"
                    "Return the results of all get, peek, del and resize ops, in order.")
        else:
            desc = (f"run_cache(ops: list) -> list of integers. Simulate an LRU cache whose entries have sizes: the total size "
                    f"of its entries may not exceed the capacity, initially {cap}. The default time-to-live is {ttl}. Times are "
                    "integers and never decrease. Ops:\n"
                    "- [\"put\", key, value, size, time] or [\"put\", key, value, size, time, ttl]: if size is larger than the "
                    "current capacity, the put is ignored (an entry already stored for the key stays as it is). Otherwise the key "
                    "gets this value and size, its expiry clock starts at this time with the given ttl or the default, and it "
                    "becomes the most recently used entry; then least recently used entries OTHER than this key are evicted until "
                    "the total size fits the capacity.\n"
                    "- [\"get\", key, time]: the value, or -1. A hit makes the key the most recently used AND restarts its expiry "
                    "clock at this time (same ttl).\n"
                    "- [\"peek\", key, time]: the value, or -1, without changing anything.\n"
                    "- [\"del\", key, time]: remove the key; 1 if it was in the cache, else 0.\n"
                    "- [\"resize\", capacity, time]: set a new capacity; evict least recently used entries until the total size "
                    "fits; returns how many were evicted.\n"
                    "- [\"size\", time]: the total size of the entries in the cache.\n"
                    "Expiry: before handling ANY op at time t, remove every entry whose clock started at ts with t - ts >= its ttl.\n"
                    "Return the results of all get, peek, del, resize and size ops, in order.")
        return "run_cache", desc, cases, {"cap": cap, "ttl": ttl, "level": level}
    if kind == "csv":
        min_units = r.randint(1, 20) if level == 7 else r.randint(1, 12)
        cases = []
        for _ in range(12):
            cols = ["id", "region", "units", "note"] + (["status"] if level >= 8 else [])
            cols = r.sample(cols, len(cols))
            head = [r.choice([c, c.upper(), c.capitalize(), f" {c}", f"{c} "]) for c in cols]
            nl = r.choice(["\n", "\r\n"])
            lines = []
            if level >= 8 and r.random() < 0.4:
                lines.append(r.choice(["# export v2, units per region", "#comment"]))
            lines.append(",".join(head))
            for i in range(r.randint(5, 10) if level == 7 else r.randint(7, 12)):
                if r.random() < 0.08:
                    lines.append("")
                    continue
                if level >= 8 and r.random() < 0.15:
                    fake = {"id": str(r.randint(1, 9)), "region": "North", "units": str(r.randint(20, 60)), "note": "x", "status": "ok"}
                    lines.append("#" + ",".join(fake[c] for c in cols))
                    continue
                val = {"id": str(r.randint(1, 8)) if level >= 8 else str(i + 1),
                       "region": r.choice(["North", "South", '"East, Upper"', " West", "North ", '"South"', "West", ""]),
                       "units": r.choice([str(r.randint(0, 60))] * 6 + [f" {r.randint(0, 60)} ", f"+{r.randint(0, 60)}",
                                                                          f"-{r.randint(1, 9)}", "", "n/a", "3.5", "1e3", f'"{r.randint(0, 60)}"']),
                       "note": r.choice(["ok", '"said ""hi"""', '"a,b"', "", f'"two{nl}lines"', '"x, ""y"", z"']
                                        + (['"first line\n# still the note"'] if level >= 8 else [])),
                       "status": r.choice(["ok", "", "paid", "ok", "VOID", " void ", "voided"])}
                fields = [val[c] for c in cols]
                if r.random() < 0.08:
                    fields = fields[:-1] if r.random() < 0.5 else fields + ["extra"]
                lines.append(",".join(fields))
            text = nl.join(lines) + (nl if r.random() < 0.7 else "")
            cases.append([[text], _ref7_csv(text, min_units, level)])
        cols_txt = "id, region, units, status and note" if level >= 8 else "id, region, units and note"
        desc = ("summarize(csv_text: string) -> object/dict.\n"
                "Parsing (RFC 4180): records are separated by \\n or \\r\\n and fields by commas. A field may be enclosed in "
                "double quotes; then it can contain commas, line breaks and doubled quotes (\"\" stands for one \"). The first "
                f"record is the header: its column names, trimmed of spaces and compared case-insensitively, are {cols_txt}, "
                "in any order.\n")
        if level >= 8:
            desc += ("Comments: a line that starts with '#' outside a quoted field is a comment and is ignored entirely (commas "
                     "and quotes in it mean nothing); comments may appear anywhere, also before the header. A '#' at the start "
                     "of a line INSIDE a quoted field is just text.\n")
        desc += ("Validation: skip a data record if its number of fields differs from the header's (this includes blank "
                 "lines), if its region is empty after trimming spaces, or if its units field, trimmed of spaces, is not an "
                 "integer (an optional + or - sign and digits only: \"\", \"n/a\", \"3.5\" and \"1e3\" are not integers)"
                 + (", or if its status, trimmed and case-insensitive, is exactly \"void\"" if level >= 8 else "") + ".\n")
        if level >= 8:
            desc += ("Duplicates: among the records that pass validation, when several have the same id (trimmed), only the "
                     "LAST one counts, even if its units are below the minimum below.\n")
        desc += (f"Result: keep the records with units >= {min_units} and return, per region (trimmed, case-sensitive), "
                 + ("{\"units\": sum of units, \"rows\": number of records, \"max\": largest units}" if level >= 8 else
                    "{\"units\": sum of units, \"rows\": number of records}")
                 + ". Regions without kept records do not appear. Do not assume a trailing newline.")
        return "summarize", desc, cases, {"min": min_units, "level": level}
    # rooms
    buf, big = r.choice([15, 30]), r.choice([8, 10, 12])
    orgs = ["ana", "ben", "cy", "dee"]
    cases = []
    for _ in range(12):
        rooms = [r.choice([4, 6, 8, 10, 12, 20]) for _ in range(r.randint(3, 5))]
        ms = []
        for _ in range(r.randint(6, 12)):
            s_ = r.randint(0, 36) * 15
            m = [s_, s_ + r.choice([15, 30, 45, 60, 90, 120]), r.randint(1, 22)]
            ms.append(m + [r.choice(orgs)] if level >= 8 else m)
        if level >= 8:
            maint = []
            for _ in range(r.randint(1, 3)):
                s_ = r.randint(0, 36) * 15
                maint.append([r.randrange(len(rooms)), s_, s_ + r.choice([30, 60, 90])])
            cases.append([[rooms, ms, maint], _ref7_rooms(rooms, ms, maint, buf, big, level)])
        else:
            cases.append([[rooms, ms], _ref7_rooms(rooms, ms, [], buf, big, level)])
    if level == 7:
        desc = ("assign_rooms(rooms: list of capacities, meetings: list of [start, end, attendees]) -> list of room indices, "
                "one per meeting in input order. Times are minutes.\n")
    else:
        desc = ("assign_rooms(rooms: list of capacities, meetings: list of [start, end, attendees, organizer], maintenance: "
                "list of [room, start, end]) -> list of room indices, one per meeting in input order. Times are minutes.\n")
    desc += ("- Room i holds at most rooms[i] people. Process the meetings by start time (ties: input order).\n"
             f"- After each meeting a room needs {buf} minutes of cleaning, or {2 * buf} minutes if that meeting had more than "
             f"{big} attendees; it is free again at end + cleaning (a meeting may start exactly then).\n")
    if level >= 8:
        desc += ("- An organizer cannot be in two meetings at once: if the organizer's previously placed meeting (in processing "
                 "order) ends after this meeting starts, this meeting gets -1 without looking at rooms (ending exactly at "
                 "the start is fine).\n")
        desc += ("- A meeting may not overlap a maintenance window of its room: [start, end) intervals, so touching is fine "
                 "(a meeting may end exactly when a window starts, or start exactly when it ends). Cleaning time may overlap a "
                 "window.\n")
    desc += ("- A room can take a meeting when it is big enough and free" + (" and has no maintenance during it" if level >= 8 else "")
             + ".\n")
    if level >= 8:
        desc += ("- Sticky rooms: if the room of the organizer's most recent previously PLACED meeting (in processing order) can "
                 "take the meeting, use that room.\n- Otherwise pick")
    else:
        desc += "- Pick"
    desc += (" the room with the smallest capacity among those that can take the meeting (ties: lowest index).\n"
             "- If no room can take it, the meeting gets -1 and uses no room.")
    return "assign_rooms", desc, cases, {"buf": buf, "big": big, "level": level}


def _gen7(kind: str, seed: int, level: int) -> Item:
    r = rng(BLOCK, f"{kind}{level}", seed)
    lang = r.choice(["python", "javascript"])
    fn, desc, tests, params = _spec7(kind, r, level)
    example = max(tests, key=lambda t: (len(t[1]), sum(v["rows"] for v in t[1].values()))) if kind == "csv" else tests[0]
    lang_name = "Python 3" if lang == "python" else "JavaScript (Node.js, CommonJS)"
    call = f"{fn}({', '.join(json.dumps(a) for a in example[0])})"
    prompt = (f"Implement this function in {lang_name}:\n\n{desc}\n\nExample: {call} returns {json.dumps(example[1])}.\n"
              f"Use only the standard library. Reply with a single ```{'python' if lang == 'python' else 'javascript'}``` "
              f"code block containing the complete function" + (" (export it with module.exports)." if lang != "python" else "."))

    def check(text, _t=None, lang=lang, fn=fn, tests=tests) -> float:
        return _run(lang, fn, _extract_code(text, lang), tests)   # the share of hidden tests passed
    return Item(f"{BLOCK}.{kind}.L{level}.{seed}", BLOCK, kind, [{"role": "user", "content": prompt}], check, lang=lang,
                meta={"fn": fn, "lang": lang, "tests": len(tests), "level": level, "params": params})


def _ref_cron7(expr: str, tz: str, start: str, n: int) -> list:
    """Levels 7-8 reference: cron with month / day names, L, L-k, nW, LW (day of month), dL and d#k (day of week), walked
    day by day on the local calendar. A local time that does not exist is skipped, a repeated one counts once, at its
    first occurrence (fold 0) - the same runs as the level-6 minute walk."""
    import calendar
    import datetime as _dt
    from zoneinfo import ZoneInfo
    months = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
    days = {d: i for i, d in enumerate("SUN MON TUE WED THU FRI SAT".split())}

    def val(tok, names):
        tok = tok.upper()
        return names[tok] if tok in names else int(tok)

    def field(spec, lo, hi, names):
        out = set()
        for part in spec.split(","):
            step = 1
            if "/" in part:
                part, st = part.split("/")
                step = int(st)
            if part == "*":
                a, b = lo, hi
            elif "-" in part:
                a, b = (val(x, names) for x in part.split("-"))
            else:
                a = b = val(part, names)
            out.update(range(a, b + 1, step))
        return out

    def weekday_near(y, m, k):   # nW: the Monday-Friday day nearest to day k, never leaving the month
        last = calendar.monthrange(y, m)[1]
        if k > last:
            return None
        d = _dt.date(y, m, k)
        if d.weekday() == 5:
            return d - _dt.timedelta(days=1) if k > 1 else d + _dt.timedelta(days=2)
        if d.weekday() == 6:
            return d + _dt.timedelta(days=1) if k < last else d - _dt.timedelta(days=2)
        return d

    mi, ho, dom, mo, dow = expr.split()
    M, H, MO = sorted(field(mi, 0, 59, {})), sorted(field(ho, 0, 23, {})), field(mo, 1, 12, months)

    def dom_ok(d):
        last = calendar.monthrange(d.year, d.month)[1]
        for part in dom.upper().split(","):
            if part == "L":
                hit = d.day == last
            elif part.startswith("L-"):
                hit = d.day == last - int(part[2:])
            elif part == "LW":
                lw = _dt.date(d.year, d.month, last)
                while lw.weekday() >= 5:
                    lw -= _dt.timedelta(days=1)
                hit = d == lw
            elif part.endswith("W"):
                hit = weekday_near(d.year, d.month, int(part[:-1])) == d
            else:
                hit = d.day in field(part, 1, 31, {})
            if hit:
                return True
        return False

    def dow_ok(d):
        w, last = (d.weekday() + 1) % 7, calendar.monthrange(d.year, d.month)[1]
        for part in dow.upper().split(","):
            if part.endswith("L"):
                hit = w == int(part[:-1]) % 7 and d.day + 7 > last
            elif "#" in part:
                k, nth = part.split("#")
                hit = w == int(k) % 7 and (d.day - 1) // 7 + 1 == int(nth)
            else:
                hit = w in {x % 7 for x in field(part, 0, 7, days)}
            if hit:
                return True
        return False

    z, utc = ZoneInfo(tz), _dt.timezone.utc
    t0 = _dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
    day, out = t0.astimezone(z).date() - _dt.timedelta(days=1), []
    for _ in range(20000):
        if day.month in MO:
            dm, dw = dom_ok(day), dow_ok(day)
            if (dom == "*" and dow == "*") or (dw if dom == "*" else dm if dow == "*" else dm or dw):
                for h in H:
                    for m in M:
                        loc = _dt.datetime(day.year, day.month, day.day, h, m, tzinfo=z)
                        u = loc.astimezone(utc)
                        if u.astimezone(z).replace(tzinfo=None) != loc.replace(tzinfo=None) or u <= t0:
                            continue   # a time in the spring-forward gap, or not after start
                        out.append(u.strftime("%Y-%m-%dT%H:%MZ"))
                        if len(out) == n:
                            return out
        day += _dt.timedelta(days=1)
    return out


_CRON7_CASES = [
    ("0 9 * * MON-FRI", "Europe/Madrid", "2026-03-26T12:00Z"),
    ("30 2 * * 0L", "Europe/Madrid", "2026-01-20T00:00Z"),            # last Sunday: 29 Mar has no 02:30, 25 Oct has two
    ("15 10 * * 1#2", "America/New_York", "2026-02-01T00:00Z"),       # second Monday
    ("0 12 * JAN,APR,JUL,OCT 5#1", "Asia/Tokyo", "2026-01-01T00:00Z"),
    ("0 8 1,L * *", "Australia/Sydney", "2026-03-25T00:00Z"),         # Sydney leaves DST on 5 Apr
    ("45 23 L * 6", "America/New_York", "2026-01-15T00:00Z"),         # last day OR Saturday
    ("30 1 * * 0#1", "America/New_York", "2026-09-15T00:00Z"),        # 1 Nov 01:30 happens twice
    ("*/30 1 * MAR SUN", "Europe/London", "2026-03-20T00:00Z"),       # 29 Mar 01:00-01:59 does not exist
    ("0 0 L FEB *", "UTC", "2026-01-01T00:00Z"),                      # leap years
    ("20 4 * * tue,Thu", "Asia/Kolkata", "2026-06-29T00:00Z"),        # names are case-insensitive
    ("0 22 L * 5L", "Europe/Berlin", "2026-04-01T00:00Z"),            # last day OR last Friday
    ("5 0 * * 7#5", "America/Los_Angeles", "2026-01-01T00:00Z"),      # fifth Sunday, 7 = Sunday
    ("0 2 * oct-dec 0L", "Europe/Madrid", "2026-09-01T00:00Z"),       # 25 Oct 02:00 happens twice
    ("0 */8 L * *", "Australia/Sydney", "2026-09-29T00:00Z"),
]
_CRON8_CASES = [
    ("0 9 15W * *", "Europe/Madrid", "2026-02-01T00:00Z"),            # 15 Feb and 15 Mar are Sundays
    ("0 9 1W * *", "America/New_York", "2026-07-15T00:00Z"),          # 1 Aug is a Saturday: Monday 3 Aug, not 31 Jul
    ("30 17 LW * *", "Asia/Tokyo", "2026-01-01T00:00Z"),
    ("0 12 L-2 * *", "UTC", "2026-01-20T00:00Z"),
    ("0 2 31W * *", "Europe/Madrid", "2026-01-01T00:00Z"),            # only 31-day months; 31 May is a Sunday
    ("0 2 * * MON-FRI/2", "Europe/Madrid", "2026-10-20T00:00Z"),      # a step on a named range
    ("30 1 LW,15 * 0#1", "America/New_York", "2026-10-10T00:00Z"),
    ("0 0 29W 2 *", "UTC", "2026-01-01T00:00Z"),                      # 29 Feb 2032 is a Sunday: Friday 27 Feb
    ("0 12 L-30 * *", "UTC", "2026-01-01T00:00Z"),                    # only months with 31 days (day 1)
    ("0 3 1W,L * *", "Australia/Sydney", "2026-09-25T00:00Z"),        # Sydney enters DST on 4 Oct
    ("30 2 L-1 * 0#5", "Europe/Madrid", "2026-03-01T00:00Z"),
    ("0 6 LW JAN-JUN/2 *", "Europe/London", "2026-01-01T00:00Z"),
]


def _cron7(seed: int, level: int) -> Item:
    """Levels 7-8: the level-6 task plus month / day names, L, dL and d#k (7); nW, LW, L-k and steps on named ranges (8)."""
    r = rng(BLOCK, f"cron{level}", seed)
    cases = r.sample(_CRON7_CASES, 10) if level == 7 else r.sample(_CRON7_CASES, 3) + r.sample(_CRON8_CASES, 7)
    r.shuffle(cases)
    tests = [[[e, tz, st, 5], _ref_cron7(e, tz, st, 5)] for e, tz, st in cases]
    desc = ("next_runs(expr: str, tz: str, start: str, n: int) -> list[str]. expr is a 5-field cron schedule "
            "'minute hour day-of-month month day-of-week'. Every field takes numbers, '*', lists 'a,b', ranges 'a-b' and steps "
            "'*/s' and 'a-b/s'. Months may be written as names JAN-DEC and days of the week as SUN-SAT (case-insensitive, also "
            "in lists and ranges); day-of-week numbers are 0-7 with 0 and 7 = Sunday. Special entries (any of them may appear "
            "in a list):\n"
            "- day-of-month 'L': the last day of the month\n"
            "- day-of-week 'dL' (d a number): the last such weekday of the month (5L = the last Friday)\n"
            "- day-of-week 'd#k' (d a number): the k-th such weekday of the month (1#2 = the second Monday; no match in a "
            "month without a k-th one)\n")
    if level >= 8:
        desc += ("- day-of-month 'L-k': k days before the last day (L-1 = the second-to-last day; no match if that is before "
                 "the 1st)\n"
                 "- day-of-month 'nW': the Monday-Friday day nearest to day n of the same month: a Saturday moves to the Friday "
                 "before and a Sunday to the Monday after, but never into another month (Saturday the 1st moves to Monday the "
                 "3rd, Sunday the last day to the Friday before); no match in a month without day n\n"
                 "- day-of-month 'LW': the last Monday-Friday day of the month\n")
    desc += ("If both day-of-month and day-of-week are restricted (neither is '*'), a day matches when EITHER matches (classic "
             "cron). The schedule is evaluated on the local wall clock of the IANA time zone tz. Return the next n run times "
             "strictly after start (UTC, 'YYYY-MM-DDTHH:MMZ'), in UTC, format 'YYYY-MM-DDTHH:MMZ'. Daylight-saving rules: a "
             "local time that does not exist (spring-forward gap) is skipped; a local time that occurs twice (fall-back) runs "
             "only once, at its first occurrence. Use zoneinfo.")
    example = tests[0]
    prompt = (f"Implement this function in Python 3:\n\n{desc}\n\nExample: next_runs({json.dumps(example[0][0])}, "
              f"{json.dumps(example[0][1])}, {json.dumps(example[0][2])}, 5) returns {json.dumps(example[1])}.\n"
              "Use only the standard library. Reply with a single ```python``` code block containing the complete function.")

    def check(text, _t=None, tests=tests) -> float:
        return _run("python", "next_runs", _extract_code(text, "python"), tests, timeout=120)   # share of the 10 cases
    return Item(f"{BLOCK}.cron.L{level}.{seed}", BLOCK, "cron", [{"role": "user", "content": prompt}], check, lang="python",
                meta={"fn": "next_runs", "lang": "python", "tests": len(tests), "level": level,
                      "expected": [t[1][0] for t in tests], "params": {"level": level}})


def oracle(it: Item) -> str | None:
    """The reference solution as a reply (validate): levels 7-8 of the HARD kinds and cron; other items have none."""
    import inspect
    p = it.meta.get("params")
    if p is None:
        return None
    if p.get("level", 0) >= 9:
        from . import code_l9
        return code_l9.oracle(it)
    if it.kind == "cron":
        return f"```python\n{inspect.getsource(_ref_cron7)}\n\nnext_runs = _ref_cron7\n```"
    if it.kind not in HARD:
        return None
    if it.meta["lang"] == "javascript":
        return f"```javascript\nconst P = {json.dumps(p)};\n{_JS7[it.kind].strip()}\n```"
    ref = {"expr": _ref7_expr, "lru": _ref7_lru, "csv": _ref7_csv, "rooms": _ref7_rooms}[it.kind]
    call = {"expr": f"def evaluate(expression, env):\n    return _ref7_expr(expression, env, {p['level']})",
            "lru": f"def run_cache(ops):\n    return _ref7_lru(ops, {p.get('cap')}, {p.get('ttl')}, {p['level']})",
            "csv": f"def summarize(csv_text):\n    return _ref7_csv(csv_text, {p.get('min')}, {p['level']})",
            "rooms": f"def assign_rooms(rooms, meetings, maintenance=()):\n    return _ref7_rooms(rooms, meetings, list(maintenance), "
                     f"{p.get('buf')}, {p.get('big')}, {p['level']})"}[it.kind]
    return f"```python\n{inspect.getsource(ref)}\n\n{call}\n```"
