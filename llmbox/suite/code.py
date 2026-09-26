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


def _run(lang: str, fn: str, code: str, tests: list) -> float:
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
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=20)
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
            frac = _run(lang, fn, _extract_code(text, lang), tests)
            return 1.0 if frac >= 1.0 else 0.0   # strict: a function is correct only if every hidden test passes
        return Item(f"{BLOCK}.{kind}.L{level}.{seed}", BLOCK, kind, [{"role": "user", "content": prompt}], check, lang=lang,
                    meta={"fn": fn, "lang": lang, "tests": len(tests), "level": level})
    return gen


SIMPLE = ("rle", "merge", "levels", "base", "topk")
KINDS = {k: make(k) for k in SIMPLE + HARD}
