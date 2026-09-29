"""knowledge.js: what a Node.js program prints. Generated programs that mix synchronous logs, process.nextTick, promise
reactions, queueMicrotask, async functions (await on plain values, resolved promises, thenables, other async calls),
promise chains that return promises (the extra ticks), .finally, rejections caught further down, setTimeout and
setImmediate - and one-line questions about the language's own quirks (default sort, parseInt, ==, typeof, float
printing, integers past 2^53, array holes, JSON.stringify). Two questions per item ask about an API that does not exist.

The answer key is an emulator written from the ECMAScript job semantics (PerformPromiseThen, promise reaction jobs,
NewPromiseResolveThenableJob, Await, Promise.prototype.finally) and Node's own loop (lib/internal/process/task_queues.js:
every nextTick before the microtask queue, both drained after each macrotask; lib/internal/timers.js: one list per
duration, lists by expiry; libuv: timers, poll, check), not from blog posts. tests/real_programs.py (`js`) runs every
generated program on real Node 22 several times and compares.

Races are never generated, and no order depends on how fast anything runs: in the main module `setTimeout(0)` against
`setImmediate` depends on how long the script took, and a timer set later with a shorter delay than one already waiting
overtakes it only if the gap between the two calls is short enough - a stall of the process (seen under load in the
real-program check with 25-50 ms margins) turns it around. So a timer is never set while a longer one of another
duration waits, and an immediate for a later check phase never waits together with a timer. Timers of one duration run
in the order they were set whatever the clock does.
Grading follows the knowledge block: right 1, UNKNOWN 1/3, wrong or invented 0; NONEXISTENT (or the TypeError) is right
for a made-up API.

Levels: 1-2 nextTick / then / queueMicrotask chains; 3 timers of equal delay (0 is 1) or setImmediate; 4 async/await on
plain values; 5 thenables, executors, delays; 6 returned promises and thenables, .finally, async functions that return a
promise; 7 rejections caught further down, try/await, a promise handled later from a callback, setImmediate inside a
timer; 8 longer, awaits of saved promises, sleeps. Claude Opus 5.5 answered level 8 at 0.9 and the first level 9 at 1.0
(2026-09-29), so levels 9-10 add Node's own behaviour on top of bigger programs: `.then(async () => ...)`,
Promise.all / allSettled / any / race, one program that dies of an unhandled rejection part-way (the labels after that
drain never print), and one with `process.on("exit")` / `process.once("beforeExit")` listeners (after 'exit' V8 still
empties the microtask queue, but no nextTick and no timer runs; a beforeExit listener can start the loop again).
Every such behaviour was checked on Node 22, 24 and 25 (the same everywhere).
"""
from __future__ import annotations

import datetime as dt
import math
import re
from collections import deque
from decimal import Decimal
from fractions import Fraction

from .common import Item, rng, strip_think

BLOCK = "knowledge"
KIND = "js"
IDK = 1 / 3

# ---- JavaScript values ---------------------------------------------------------------------------------------------------


class _Sentinel:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


UNDEF, NULL, HOLE, FUNC = _Sentinel("undefined"), _Sentinel("null"), _Sentinel("hole"), _Sentinel("function")


class JSArr(list):
    pass


class JSObj(dict):
    pass


class JSDate:
    def __init__(self, ms: int):
        self.ms = ms


class JSBig(int):
    pass


def num_str(x: float) -> str:
    """Number::toString(10): the shortest digits that round-trip, decimal between 1e-7 and 1e21, exponent otherwise."""
    if x != x:
        return "NaN"
    if x == 0:
        return "0"
    if x < 0:
        return "-" + num_str(-x)
    if math.isinf(x):
        return "Infinity"
    sign, digits, exp = Decimal(repr(float(x))).normalize().as_tuple()
    s = "".join(map(str, digits))
    k, n = len(s), len(s) + exp
    if k <= n <= 21:
        return s + "0" * (n - k)
    if 0 < n <= 21:
        return s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + s
    e = n - 1
    es = ("+" if e > 0 else "-") + str(abs(e))
    return (s + "e" + es) if k == 1 else (s[0] + "." + s[1:] + "e" + es)


def to_str(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    if isinstance(v, JSBig):
        return str(int(v))
    if isinstance(v, (int, float)):
        return num_str(float(v))
    if v is UNDEF:
        return "undefined"
    if v is NULL:
        return "null"
    if isinstance(v, JSArr):
        return ",".join("" if x in (UNDEF, NULL, HOLE) else to_str(x) for x in v)
    if isinstance(v, JSObj):
        return "[object Object]"
    raise TypeError(v)


_WS = " \t\n\r\x0b\x0c ﻿  "


def str_to_num(s: str) -> float:
    """StringToNumber: trimmed; empty is 0; 0x / 0o / 0b literals without a sign; a decimal literal with an optional
    sign; Infinity; anything else NaN (no numeric separators)."""
    t = s.strip(_WS)
    if t == "":
        return 0.0
    m = re.fullmatch(r"0([xXoObB])([0-9a-fA-F]+)", t)
    if m:
        base = {"x": 16, "o": 8, "b": 2}[m.group(1).lower()]
        try:
            return float(int(m.group(2), base))
        except ValueError:
            return math.nan
    m = re.fullmatch(r"([+-]?)(Infinity|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)", t)
    if not m:
        return math.nan
    if m.group(2) == "Infinity":
        return -math.inf if m.group(1) == "-" else math.inf
    return float(t)


def to_prim(v):
    return to_str(v) if isinstance(v, (JSArr, JSObj)) else v


def to_num(v) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)) and not isinstance(v, JSBig):
        return float(v)
    if isinstance(v, str):
        return str_to_num(v)
    if v is UNDEF:
        return math.nan
    if v is NULL:
        return 0.0
    if isinstance(v, (JSArr, JSObj)):
        return to_num(to_prim(v))
    raise TypeError(v)


def _tname(v) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if v is UNDEF:
        return "undefined"
    if v is NULL:
        return "null"
    return "object"


def loose_eq(x, y) -> bool:
    """IsLooselyEqual for the values questions use (arrays compare by identity: distinct literals)."""
    tx, ty = _tname(x), _tname(y)
    if tx == ty:
        if tx == "number":
            return float(x) == float(y)
        if tx == "object":
            return x is y
        return x == y
    if {tx, ty} <= {"undefined", "null"}:
        return True
    if tx == "number" and ty == "string":
        return float(x) == str_to_num(y)
    if tx == "string" and ty == "number":
        return str_to_num(x) == float(y)
    if tx == "boolean":
        return loose_eq(to_num(x), y)
    if ty == "boolean":
        return loose_eq(x, to_num(y))
    if tx in ("string", "number") and ty == "object":
        return loose_eq(x, to_prim(y))
    if tx == "object" and ty in ("string", "number"):
        return loose_eq(to_prim(x), y)
    return False


def less(x, y):
    """IsLessThan: True / False / None (undefined: a NaN was involved)."""
    px, py = to_prim(x), to_prim(y)
    if isinstance(px, str) and isinstance(py, str) and not isinstance(px, bool) and not isinstance(py, bool):
        return px < py
    nx, ny = to_num(px), to_num(py)
    if nx != nx or ny != ny:
        return None
    return nx < ny


def js_add(a, b):
    pa, pb = to_prim(a), to_prim(b)
    if isinstance(pa, str) or isinstance(pb, str):
        return to_str(pa) + to_str(pb)
    return to_num(pa) + to_num(pb)


def parse_int(v, radix=UNDEF) -> float:
    s = to_str(v).lstrip(_WS)
    sign = 1
    if s[:1] == "-":
        sign = -1
    if s[:1] in "+-" and s:
        s = s[1:]
    r = 0 if radix is UNDEF else int(to_num(radix))
    strip = True
    if r != 0:
        if r < 2 or r > 36:
            return math.nan
        if r != 16:
            strip = False
    else:
        r = 10
    if strip and s[:2] in ("0x", "0X"):
        s, r = s[2:], 16
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"[:r]
    z = ""
    for c in s:
        if c.lower() in digits:
            z += c
        else:
            break
    if not z:
        return math.nan
    n = int(z, r)
    if n == 0:
        return -0.0 if sign < 0 else 0.0
    return float(sign * n)


def to_fixed(x: float, f: int) -> str:
    if x != x:
        return "NaN"
    if abs(x) >= 1e21:
        return num_str(x)
    s = ""
    if x < 0:
        s, x = "-", -x
    n = math.floor(Fraction(x) * 10 ** f + Fraction(1, 2))   # the larger n on a tie
    m = str(n)
    if f:
        if len(m) <= f:
            m = "0" * (f + 1 - len(m)) + m
        m = m[:-f] + "." + m[-f:]
    return s + m


def math_round(x: float) -> float:
    if x != x or math.isinf(x):
        return x
    r = math.floor(Fraction(x) + Fraction(1, 2))
    if r == 0 and (x < 0 or (x == 0 and math.copysign(1, x) < 0)):
        return -0.0
    return float(r)


def _q(s: str) -> str:
    """How util.inspect quotes a string: single quotes unless the string holds one."""
    if "'" not in s:
        return "'" + s.replace("\\", "\\\\").replace("\n", "\\n") + "'"
    if '"' not in s:
        return '"' + s.replace("\\", "\\\\").replace("\n", "\\n") + '"'
    return "`" + s + "`"


_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")


def inspect(v, top: bool = False) -> str:
    """console.log's formatting of one argument (util.inspect for non-strings; small values stay on one line)."""
    if isinstance(v, str):
        return v if top else _q(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, JSBig):
        return f"{int(v)}n"
    if isinstance(v, (int, float)):
        x = float(v)
        return "-0" if x == 0 and math.copysign(1, x) < 0 else num_str(x)
    if v is UNDEF:
        return "undefined"
    if v is NULL:
        return "null"
    if isinstance(v, JSArr):
        parts, i = [], 0
        while i < len(v):
            if v[i] is HOLE:
                j = i
                while j < len(v) and v[j] is HOLE:
                    j += 1
                parts.append(f"<{j - i} empty item{'s' if j - i > 1 else ''}>")
                i = j
            else:
                parts.append(inspect(v[i]))
                i += 1
        return "[ " + ", ".join(parts) + " ]" if parts else "[]"
    if isinstance(v, JSObj):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{k if _IDENT.match(k) else _q(k)}: {inspect(x)}" for k, x in _ordered(v)) + " }"
    raise TypeError(v)


def _ordered(o: JSObj):
    """OrdinaryOwnPropertyKeys: integer keys ascending, then the other string keys in insertion order."""
    idx = [(k, x) for k, x in o.items() if re.fullmatch(r"0|[1-9]\d*", k) and int(k) < 2 ** 32 - 1]
    rest = [(k, x) for k, x in o.items() if not (re.fullmatch(r"0|[1-9]\d*", k) and int(k) < 2 ** 32 - 1)]
    return sorted(idx, key=lambda kv: int(kv[0])) + rest


class JSTypeError(Exception):
    pass


def _json_str(s: str) -> str:
    out = '"'
    for c in s:
        if c == '"':
            out += '\\"'
        elif c == "\\":
            out += "\\\\"
        elif c == "\n":
            out += "\\n"
        elif ord(c) < 0x20:
            out += "\\u%04x" % ord(c)
        else:
            out += c
    return out + '"'


def json_stringify(v):
    """JSON.stringify without replacer or indent: a string, or UNDEF (undefined / a function at the top)."""
    if isinstance(v, JSDate):
        return _json_str(dt.datetime.fromtimestamp(v.ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
                         + "%03dZ" % (v.ms % 1000))
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, JSBig):
        raise JSTypeError("Do not know how to serialize a BigInt")
    if isinstance(v, (int, float)):
        x = float(v)
        return num_str(x) if math.isfinite(x) else "null"
    if isinstance(v, str):
        return _json_str(v)
    if v is NULL:
        return "null"
    if v is UNDEF or v is FUNC:
        return UNDEF
    if isinstance(v, JSArr):
        return "[" + ",".join("null" if (x is HOLE or json_stringify(x) is UNDEF) else json_stringify(x) for x in v) + "]"
    if isinstance(v, JSObj):
        parts = []
        for k, x in _ordered(v):
            s = json_stringify(x)
            if s is not UNDEF:
                parts.append(_json_str(k) + ":" + s)
        return "{" + ",".join(parts) + "}"
    raise TypeError(v)


def js_sort_default(arr: JSArr) -> JSArr:
    vals = [x for x in arr if x not in (UNDEF, HOLE)]
    n_undef = sum(1 for x in arr if x is UNDEF)
    n_hole = sum(1 for x in arr if x is HOLE)
    vals.sort(key=lambda x: [ord(c) for c in to_str(x)])   # UTF-16 code units; the sort is stable
    return JSArr(vals + [UNDEF] * n_undef + [HOLE] * n_hole)


# ---- one-line questions: the language's own quirks ---------------------------------------------------------------------
# Each family draws an expression (JavaScript source) and its value computed with the functions above; a question is
# `console.log(e1, e2, ...)`, so its answer is one line: strings as they are, everything else as util.inspect shows it.
# Tiers: 0 easy (levels 1-3), 1 medium (4-6), 2 hard (7-10); a tier also draws from the tiers below.

def _arr_lit(xs) -> str:
    return "[" + ", ".join("" if x is HOLE else (x if isinstance(x, str) else num_str(x)) for x in xs) + "]"


def _a_sort(r, tier):
    """At most 6 elements: util.inspect puts a longer array of numbers in columns over several lines."""
    n = r.randint(3, 4 if tier == 0 else 5 if tier == 1 else 4)
    pool = [r.randint(1, 9), r.randint(10, 99), r.randint(100, 999), r.randint(1, 9), r.randint(10, 30), r.randint(1000, 5000)]
    nums = [float(x) for x in r.sample(pool, n)]
    if tier >= 1 and r.random() < 0.4:
        nums[r.randrange(n)] *= -1
    kind = r.choice(["plain", "plain", "num"] if tier == 0 else ["plain", "rev", "undef", "hole", "mixed"] if tier == 2 else
                    ["plain", "rev", "num"])
    code_items: list = [num_str(x) for x in nums]
    vals: list = list(nums)
    if kind == "undef":
        at = r.randrange(1, n)
        code_items.insert(at, "undefined")
        vals.insert(at, UNDEF)
    if kind == "hole":
        at = r.randrange(1, n)
        code_items.insert(at, "")
        vals.insert(at, HOLE)
        at2 = r.randrange(1, len(vals))
        code_items.insert(at2, "undefined")
        vals.insert(at2, UNDEF)
    if kind == "mixed":
        lits = {"\"b\"": "b", "\"B\"": "B", "\"a\"": "a", "\"10\"": "10", "true": True, "null": NULL}
        for s in r.sample(sorted(lits), 2):
            at = r.randrange(0, len(vals) + 1)
            code_items.insert(at, s)
            vals.insert(at, lits[s])
    lit = "[" + ", ".join(code_items) + "]"
    if kind == "num":
        return f"{lit}.sort((a, b) => a - b)", JSArr(sorted(vals, key=float))
    out = js_sort_default(JSArr(vals))
    if kind == "rev":
        return f"{lit}.sort().reverse()", JSArr(reversed(out))
    return f"{lit}.sort()", out


def _a_parseint(r, tier):
    n = r.randint(2, 99)
    easy = [f"\"{n}px\"", f"\"{n}.99\"", f"\"0{r.randint(1, 7)}\"", f"\"px{n}\"", f"\"  {n} \""]
    med = [f"\"0x{n:x}\"", f"\"{r.randint(1, 9)}e3\"", "\"-0\"", f"\"0b{r.randint(2, 7):b}\"", f"\"{n},5\"", f"\"{r.randint(2, 31):b}\", 2",
           f"\"{r.choice('xyz')}\", 36", f"\"{r.randint(8, 9)}\", 8", f"\"{r.randint(10, 250):x}\", 16", f"\"{n}\", 1"]
    hard = [f"0.000000{r.randint(1, 9)}", f"{r.randint(1, 9)}e21", "null, 36", "undefined, 36", "\"Infinity\"", f"\"0x{n:x}\", 10",
            f"\"-0x{n:x}\"", f"\"{n}\", 37", f"\"{n}\", 0", "true, 36", f"{n}.9e1", f"\"\\n {n}\""]
    pool = easy + (med if tier >= 1 else []) + (hard if tier >= 2 else [])
    if tier >= 1 and r.random() < 0.25:   # map passes the index as the radix
        base = r.choice(["1", "10", str(r.randint(2, 9)), "11"])
        items = [base if r.random() < 0.6 else str(r.randint(1, 20)) for _ in range(r.randint(3, 4))]
        return ("[" + ", ".join(f"\"{x}\"" for x in items) + "].map(parseInt)",
                JSArr(parse_int(x, float(i)) for i, x in enumerate(items)))
    code = r.choice(pool)
    return f"parseInt({code})", _eval_parseint_args(code)


def _eval_parseint_args(code: str) -> float:
    m = re.fullmatch(r'"((?:[^"\\]|\\.)*)"(?:, (\d+))?', code)
    if m:
        return parse_int(m.group(1).replace("\\n", "\n"), float(m.group(2)) if m.group(2) else UNDEF)
    m = re.fullmatch(r"(null|undefined|true|[\d.e]+)(?:, (\d+))?", code)
    v = {"null": NULL, "undefined": UNDEF, "true": True}.get(m.group(1))
    return parse_int(float(m.group(1)) if v is None else v, float(m.group(2)) if m.group(2) else UNDEF)


_NUM_POOL = [   # (source, value, tier)
    ('""', "", 0), ('" 12 "', " 12 ", 0), ('"12px"', "12px", 0), ("null", NULL, 0), ("undefined", UNDEF, 0), ("true", True, 0),
    ('"0x10"', "0x10", 1), ('"1e3"', "1e3", 1), ("[]", JSArr(), 1), ("[5]", JSArr([5.0]), 1), ("[1, 2]", JSArr([1.0, 2.0]), 1),
    ('".5"', ".5", 1), ('"5."', "5.", 1), ('"\\n"', "\n", 1), ('"0b101"', "0b101", 1), ("false", False, 1),
    ('"-0x10"', "-0x10", 2), ('"1_000"', "1_000", 2), ('"Infinity"', "Infinity", 2), ('"0o17"', "0o17", 2), ('["7"]', JSArr(["7"]), 2),
    ('"-0"', "-0", 2), ('" 1 2 "', " 1 2 ", 2), ('"+5"', "+5", 2), ("[[3]]", JSArr([JSArr([3.0])]), 2), ('"1e1000"', "1e1000", 2),
    ('"-Infinity"', "-Infinity", 2), ("[null]", JSArr([NULL]), 2), ('"0.0000001"', "0.0000001", 2)]


def _a_number(r, tier):
    src, v, _t = r.choice([x for x in _NUM_POOL if x[2] <= tier])
    return r.choice(["Number({})", "+{}"]).format(src), to_num(v)


_EQ_POOL = [("0", 0.0, 0), ("1", 1.0, 0), ('""', "", 0), ('"0"', "0", 0), ('"1"', "1", 0), ("null", NULL, 0), ("undefined", UNDEF, 0),
            ("false", False, 0), ("true", True, 0), ("NaN", math.nan, 0), ("[]", "ARR0", 1), ("[0]", "ARR1", 1), ("[1]", "ARR2", 1),
            ('" "', " ", 1), ('"abc"', "abc", 1), ('"true"', "true", 1), ('"\\t"', "\t", 2), ("[[]]", "ARR3", 2), ('[""]', "ARR4", 2),
            ("[null]", "ARR5", 2), ('"00"', "00", 2), ("-0", -0.0, 2), ('"1,2"', "1,2", 2), ("[1, 2]", "ARR6", 2), ("![]", False, 2),
            ('"10"', "10", 1), ('"9"', "9", 1), ("10", 10.0, 1), ("9", 9.0, 1)]
_ARRS = {"ARR0": lambda: JSArr(), "ARR1": lambda: JSArr([0.0]), "ARR2": lambda: JSArr([1.0]), "ARR3": lambda: JSArr([JSArr()]),
         "ARR4": lambda: JSArr([""]), "ARR5": lambda: JSArr([NULL]), "ARR6": lambda: JSArr([1.0, 2.0])}


def _cmp(op: str, a, b) -> bool:
    if op == "==":
        return loose_eq(a, b)
    if op == "!=":
        return not loose_eq(a, b)
    if op == "===":
        ta = _tname(a)
        return ta == _tname(b) and (float(a) == float(b) if ta == "number" else (a is b if ta == "object" else a == b))
    if op == "<":
        return less(a, b) is True
    if op == ">":
        return less(b, a) is True
    if op == "<=":
        return less(b, a) is False
    if op == ">=":
        return less(a, b) is False
    raise ValueError(op)


def _a_eq(r, tier):
    (sa, va, _), (sb, vb, _) = r.sample([x for x in _EQ_POOL if x[2] <= tier], 2)
    va = _ARRS[va]() if isinstance(va, str) and va in _ARRS else va
    vb = _ARRS[vb]() if isinstance(vb, str) and vb in _ARRS else vb
    op = r.choice(["=="] * 3 + ["!=", "==="] if tier == 0 else ["==", "==", "<", ">=", "<=", ">"] if tier == 2 else ["==", "==", "===", ">="])
    return f"{sa} {op} {sb}", _cmp(op, va, vb)


_TYPEOF = [("typeof null", "object", 0), ("typeof []", "object", 0), ("typeof NaN", "number", 0), ('typeof "1"', "string", 0),
           ("typeof undefined", "undefined", 0), ("typeof function () {}", "function", 1), ("typeof class {}", "function", 1),
           ("typeof Symbol()", "symbol", 1), ("typeof 1n", "bigint", 1), ("typeof typeof 1", "string", 1),
           ("typeof new Number(1)", "object", 1), ('typeof Number("1")', "number", 1), ("typeof notDeclaredAnywhere", "undefined", 1),
           ("typeof Math", "object", 2), ("typeof JSON", "object", 2), ("typeof Date", "function", 2), ("typeof new Date(0)", "object", 2),
           ("typeof require", "function", 2), ("typeof module", "object", 2), ("typeof exports", "object", 2),
           ("typeof globalThis", "object", 2), ("typeof Buffer", "function", 2), ("typeof function* () {}", "function", 2),
           ("typeof (async () => {})", "function", 2), ("typeof new Promise(() => {})", "object", 2), ("typeof /x/", "object", 2),
           ("typeof null ?? 1", "object", 2), ('typeof [] === "array"', False, 2), ('typeof new String("a")', "object", 2),
           ("typeof String(1)", "string", 2), ("typeof Object(1n)", "object", 2), ("typeof process.nextTick", "function", 2)]


def _a_typeof(r, tier):
    code, v, _t = r.choice([x for x in _TYPEOF if x[2] <= tier])
    return code, v


def _a_float(r, tier):
    dec = [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 1.1, 2.2, 3.3, 0.25, 0.5, 1.5, 4.35, 1.005, 9.95, 0.15, 0.45]
    kind = r.choice(["add", "add", "mul"] if tier == 0 else ["add", "sub", "mul", "fixed", "round"] if tier == 1 else
                    ["fixed", "round", "div", "mod", "big", "small", "fixed20", "minmax"])
    a, b = r.sample(dec, 2)
    if kind == "add":
        return f"{num_str(a)} + {num_str(b)}", a + b
    if kind == "sub":
        return f"{num_str(max(a, b))} - {num_str(min(a, b))}", max(a, b) - min(a, b)
    if kind == "mul":
        k = r.choice([3, 10, 100, 1000])
        return f"{num_str(a)} * {k}", a * k
    if kind == "fixed":
        x = r.choice([1.005, 2.5, 1.45, 8.345, 0.5, 1.5, 2.675, 10.235, 1.255, -1.5, -2.5, 0.125, 0.375])
        f = 0 if abs(x * 2) == int(abs(x * 2)) and r.random() < 0.6 else r.choice([1, 2])
        return (f"({num_str(x)}).toFixed({f})" if x < 0 else f"{num_str(x)}.toFixed({f})"), to_fixed(x, f)
    if kind == "round":
        x = r.choice([2.5, -2.5, -0.5, 0.5, 1.5, -1.5, -0.4, 3.5, -3.5, 0.49999999999999994])
        return f"Math.round({num_str(x)})", math_round(x)
    if kind == "div":
        a2, b2 = r.choice([(1, 3), (2, 3), (100, 3), (1, 0), (-1, 0), (0, 0), (10, 4), (7, 7)])
        return f"{a2} / {b2}", ((math.inf if a2 > 0 else -math.inf if a2 < 0 else math.nan) if b2 == 0 else a2 / b2)
    if kind == "mod":
        a2, b2 = r.choice([(-5, 2), (5, -2), (5.5, 2), (-7, 3), (5, 0), (-0.5, 1)])
        return f"{num_str(a2)} % {num_str(b2)}", (math.nan if b2 == 0 else math.fmod(a2, b2))
    if kind == "big":
        x, e = r.choice([123456789, 987654321, 5, 12345]), r.choice([12, 16, 20, 21, 22])
        return f"{x} * 1e{e}", x * float(f"1e{e}")
    if kind == "small":
        e, k = r.choice([6, 7, 8]), r.choice([1, 2, 5])
        return f"{k} / 1e{e}", k / float(f"1e{e}")
    if kind == "fixed20":
        x, n = r.choice([0.1, 0.2, 0.3, 0.7]), r.choice([17, 18, 20])
        return f"{num_str(x)}.toFixed({n})", to_fixed(x, n)
    args = r.choice([(), ("1", '"3"'), ("1", '"3"', "NaN"), ("[]",), ("[2]", "1"), ("null", "-1")])
    fn = r.choice(["max", "min"])
    lit = {"1": 1.0, '"3"': "3", "NaN": math.nan, "[]": JSArr(), "[2]": JSArr([2.0]), "null": NULL, "-1": -1.0}
    vals = [to_num(lit[a_]) for a_ in args]
    v = math.nan if any(x != x for x in vals) else (-math.inf if fn == "max" else math.inf) if not vals else \
        (max(vals) if fn == "max" else min(vals))
    return f"Math.{fn}({', '.join(args)})", v


def _a_safeint(r, tier):
    k = r.randint(1, 5)
    kind = r.choice(["max", "pow", "lit"] if tier < 2 else ["max", "pow", "lit", "eq", "big64", "bigint", "lit2"])
    if kind == "max":
        return f"Number.MAX_SAFE_INTEGER + {k}", float(2 ** 53 - 1) + k
    if kind == "pow":
        return f"2 ** 53 + {k}", float(2 ** 53) + k
    if kind in ("lit", "lit2"):
        n = 2 ** 53 + r.randint(1, 9) if kind == "lit" else r.choice([123456789012345678, 9999999999999999, 12345678901234567890])
        return str(n), float(str(n))
    if kind == "eq":
        a_, b_ = r.sample([1, 2, 3], 2)
        return f"Number.MAX_SAFE_INTEGER + {a_} === Number.MAX_SAFE_INTEGER + {b_}", float(2 ** 53 - 1) + a_ == float(2 ** 53 - 1) + b_
    if kind == "big64":
        e = r.choice([60, 64, 70])
        return f"2 ** {e}", float(2 ** e)
    n = 2 ** 53 + r.randint(1, 9)
    return f"{n}n + {k}n", JSBig(n + k)


def _a_holes(r, tier):
    n = r.randint(2, 5)
    kind = r.choice(["map", "spread", "from", "fill", "join"] if tier < 2 else
                    ["map", "keys", "len", "includes", "indexOf", "mapx", "concat", "filter", "tostring", "arrayof", "count", "json"])
    if kind == "map":
        return f"Array({n}).map(() => {r.randint(0, 9)})", JSArr([HOLE] * n)
    if kind == "spread":
        k = r.randint(1, 5)
        return f"[...Array({n})].map((_, i) => i * {k})", JSArr(float(i * k) for i in range(n))
    if kind == "from":
        k = r.randint(1, 5)
        return f"Array.from({{ length: {n} }}, (_, i) => i + {k})", JSArr(float(i + k) for i in range(n))
    if kind == "fill":
        v = r.randint(0, 9)
        return f"Array({n}).fill({v})", JSArr([float(v)] * n)
    if kind == "join":
        s = r.choice(["-", "+", "x"])
        return f"Array({n}).join(\"{s}\")", s * (n - 1)
    items = [float(r.randint(1, 9)) for _ in range(3)]
    at = r.randrange(1, 3)
    arr = items[:at] + [HOLE] + items[at:]
    lit = _arr_lit(arr)
    if kind == "keys":
        return f"Object.keys({lit})", JSArr(str(i) for i, x in enumerate(arr) if x is not HOLE)
    if kind == "len":
        return f"{lit}.length", float(len(arr))
    if kind == "includes":
        return f"{lit}.includes(undefined)", True
    if kind == "indexOf":
        return f"{lit}.indexOf(undefined)", -1.0
    if kind == "mapx":
        k = r.randint(2, 3)
        return f"{lit}.map((x) => x * {k})", JSArr(HOLE if x is HOLE else x * k for x in arr)
    if kind == "concat":
        return f"Array({n}).concat([{num_str(items[0])}])", JSArr([HOLE] * n + [items[0]])
    if kind == "filter":
        return f"{lit}.filter(() => true)", JSArr(x for x in arr if x is not HOLE)
    if kind == "tostring":
        return f"Array({n}).toString()", "," * (n - 1)
    if kind == "arrayof":
        v = r.randint(2, 9)
        return r.choice([(f"Array.of({v})", JSArr([float(v)])), (f"Array({v}).length", float(v)), (f"new Array(\"{v}\")", JSArr([str(v)]))])
    if kind == "count":
        return f"{lit}.reduce((c) => c + 1, 0)", float(sum(1 for x in arr if x is not HOLE))
    return f"JSON.stringify({lit})", json_stringify(JSArr(arr))


def _make_date(y, mo, d, h=0):
    """Date.UTC(y, mo, d, h): the month counts from 0 and every field overflows into the next."""
    base = dt.datetime(y + mo // 12, mo % 12 + 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=d - 1, hours=h)
    return JSDate(int(base.timestamp() * 1000))


def _a_json(r, tier):
    kind = r.choice(["undefprop", "undefarr", "nan", "top"] if tier == 0 else ["undefprop", "nums", "date", "func", "top", "dateobj"]
                    if tier == 1 else ["order", "date", "tojson", "map", "nested", "order", "bigint", "dateobj"])
    if kind == "undefprop":
        k = r.randint(1, 9)
        return f"JSON.stringify({{ a: undefined, b: {k} }})", json_stringify(JSObj(a=UNDEF, b=float(k)))
    if kind == "undefarr":
        k = r.randint(1, 9)
        return f"JSON.stringify([undefined, {k}])", json_stringify(JSArr([UNDEF, float(k)]))
    if kind == "nan":
        s, v = r.choice([("NaN", math.nan), ("Infinity", math.inf), ("-0", -0.0)])
        return f"JSON.stringify([{s}])", json_stringify(JSArr([v]))
    if kind == "top":
        s, v = r.choice([("undefined", UNDEF), ("NaN", math.nan), ('"a"', "a"), ("() => 1", FUNC), ("null", NULL)])
        return f"JSON.stringify({s})", json_stringify(v)
    if kind == "nums":
        return "JSON.stringify({ a: NaN, b: -Infinity, c: -0 })", json_stringify(JSObj(a=math.nan, b=-math.inf, c=-0.0))
    if kind in ("date", "dateobj"):
        y, mo = r.randint(2000, 2030), r.randint(0, 11)
        d = r.choice([r.randint(1, 28), 30, 31, 0, 32])
        h = r.choice([0, r.randint(1, 23), 24])
        code = f"new Date(Date.UTC({y}, {mo}, {d}{', ' + str(h) if h else ''}))"
        v = _make_date(y, mo, d, h)
        if kind == "dateobj":
            return f"JSON.stringify({{ at: {code}, n: undefined }})", json_stringify(JSObj(at=v, n=UNDEF))
        return f"JSON.stringify({code})", json_stringify(v)
    if kind == "func":
        return "JSON.stringify([function () {}, { f() {} }])", json_stringify(JSArr([FUNC, JSObj(f=FUNC)]))
    if kind == "order":
        keys = r.sample(["b", "a", "z", "2", "10", "1", "-1", "01", "9", "x1"], r.randint(4, 5))
        obj = JSObj((k, float(i)) for i, k in enumerate(keys))
        src = "{ " + ", ".join(f"{k if _IDENT.match(k) or re.fullmatch(r'[1-9]\d*|0', k) else _json_str(k)}: {i}"
                               for i, k in enumerate(keys)) + " }"
        return f"JSON.stringify({src})", json_stringify(obj)
    if kind == "tojson":
        k = r.randint(1, 9)
        return f"JSON.stringify({{ a: 1, toJSON() {{ return {k}; }} }})", num_str(k)
    if kind == "map":
        return "JSON.stringify([new Map([[1, 2]]), new Set([3])])", "[{},{}]"
    if kind == "nested":
        return ("JSON.stringify({ a: [undefined, () => 1, NaN], b: { c: undefined } })",
                json_stringify(JSObj(a=JSArr([UNDEF, FUNC, math.nan]), b=JSObj(c=UNDEF))))
    return "JSON.stringify({ n: 1n })", "THROW:TypeError"


_CAT_POOL = [('"1"', "1"), ("1", 1.0), ("2", 2.0), ("true", True), ("null", NULL), ("undefined", UNDEF), ("[]", "ARR0"),
             ("[1, 2]", "ARR6"), ('"a"', "a"), ("NaN", math.nan), ("{}", "OBJ"), ('"5"', "5"), ("[3]", "ARR7")]


def _a_concat(r, tier):
    for _ in range(50):
        n = 2 if tier == 0 else r.randint(2, 3) if tier == 1 else 3
        ops = [r.choice(["+", "+", "-", "*"] if tier else ["+", "+", "-"]) for _ in range(n - 1)]
        opnds = r.sample(_CAT_POOL, n)
        vals = [JSArr() if v == "ARR0" else JSArr([1.0, 2.0]) if v == "ARR6" else JSArr([3.0]) if v == "ARR7" else JSObj() if v == "OBJ"
                else v for _s, v in opnds]
        terms, tops = [vals[0]], []   # `*` binds tighter than `+` and `-`
        for op, v in zip(ops, vals[1:]):
            if op == "*":
                terms[-1] = to_num(terms[-1]) * to_num(v)
            else:
                terms.append(v)
                tops.append(op)
        acc = terms[0]
        for op, v in zip(tops, terms[1:]):
            acc = js_add(acc, v) if op == "+" else to_num(acc) - to_num(v)
        if isinstance(acc, str) and (acc == "" or " " in acc):
            continue
        return opnds[0][0] + "".join(f" {op} {s}" for op, (s, _v) in zip(ops, opnds[1:])), acc
    return '"b" + "a" + +"a" + "a"', "baNaNa"


_MISC = [("[NaN].indexOf(NaN)", -1.0), ("[NaN].includes(NaN)", True), ("Object.is(-0, 0)", False), ("-0 === 0", True),
         ("NaN === NaN", False), ("Object.is(NaN, NaN)", True), ('[1, 2, 3].includes("2")', False), ('"abc".substring(2, 0)', "ab"),
         ('"a-b-c".split("-", 2)', JSArr(["a", "b"])), ('"aaa".replace("a", "b")', "baa"), ('[10, 1, 5].indexOf("1")', -1.0),
         ('"5" * []', 0.0), ("[3] * [4]", 12.0), ('"10" / "4"', 2.5), ("010 + 1", 9.0), ('[1, [2, [3]]] + ""', "1,2,3"),
         ('"B" > "a"', False), ('"10" > "9"', False), ('"abc" < "abd"', True), ("0.1 * 3 === 0.3", False),
         ("[] + {}", "[object Object]"), ("[, 1].findIndex((x) => x === undefined)", 0.0), ('"abc".at(-1)', "c"),
         ("[1, 2, 3].at(-4)", UNDEF), ("Math.min() > Math.max()", True), ('"ab".repeat(0) === ""', True),
         ('parseFloat("3.14abc")', 3.14), ('parseFloat(".5.5")', 0.5), ("[, , 1].length", 3.0), ('"2" + 2 * "2"', "24")]


def _a_misc(r, tier):
    return r.choice(_MISC)


_FAMILIES = {"sort": _a_sort, "parseint": _a_parseint, "number": _a_number, "eq": _a_eq, "typeof": _a_typeof, "float": _a_float,
             "safeint": _a_safeint, "holes": _a_holes, "json": _a_json, "concat": _a_concat, "misc": _a_misc}


def semantic_question(r, level: int, n_atoms: int) -> dict:
    tier = 0 if level <= 3 else 1 if level <= 6 else 2
    fams = r.sample(sorted(_FAMILIES), n_atoms)
    codes, vals = [], []
    for f in fams:
        for _ in range(20):
            c, v = _FAMILIES[f](r, tier)
            thr = isinstance(v, str) and v.startswith("THROW:")
            if thr and n_atoms > 1:
                continue
            if thr or not (isinstance(v, str) and (v == "" or " " in v or "%" in v)):
                break
        codes.append(c)
        vals.append(v)
    code = f"console.log({', '.join(codes)});"
    thr = next((v for v in vals if isinstance(v, str) and v.startswith("THROW:")), None)
    q = {"kind": KIND, "src": "line", "level": level, "fake": False, "text": code, "parts": codes}
    if thr:
        return dict(q, mode="throws", accept={"exc": thr[6:]})
    return dict(q, mode="line", accept={"line": " ".join(inspect(v, top=True) for v in vals)})


# ---- the event loop -------------------------------------------------------------------------------------------------------
# A program is a main block and async function declarations. Statements (tuples):
#   ("log", L)  ("tick", B)  ("micro", B)  ("timeout", B, ms)  ("immediate", B)  ("chain", SRC, LINKS, var|None)
#   ("await", X)  ("try", B, B)  ("return", X)  ("throw",)  ("res", X|None) / ("rej",): the enclosing executor's resolve / reject
# SRC and X: ("num", n) ("null",) ("undef",) ("res",) = Promise.resolve() ("resv", X) = Promise.resolve(X) ("rejp",) =
#   Promise.reject(new Error("x")) ("thenable", B) = { then(resolve) { B; resolve(); } } ("call", f) ("var", v)
#   ("newp", B) = new Promise((resolve, reject) => { B }) ("sleep", ms) = new Promise((resolve) => setTimeout(resolve, ms))
# LINKS: ("then", B) ("catch", B) ("finally", B); a handler block may end with ("return", X) or ("throw",).

class _P:
    __slots__ = ("state", "value", "fr", "rr", "handled")

    def __init__(self):
        self.state, self.value, self.fr, self.rr, self.handled = "pending", None, [], [], False


class _Thenable:
    def __init__(self, block, env):
        self.block, self.env = block, env


class _Ret:
    def __init__(self, value):
        self.value = value


class JSThrow(Exception):
    def __init__(self, value):
        super().__init__(value)
        self.value = value


class Invalid(Exception):
    """The program would crash where it should not (an unhandled rejection), race (an order real Node does not
    guarantee) or run away."""


class _Crash(Exception):
    """Node 15+: a rejection still unhandled when the tick and microtask queues run dry ends the process (exit code 1)."""


class _TList:
    def __init__(self, msecs, expiry, lid):
        self.msecs, self.expiry, self.id, self.timers = msecs, expiry, lid, deque()


class Loop:
    def __init__(self, prog: dict, allow_crash: bool = False):
        self.fns = prog["fns"]
        self.main = prog["main"]
        self.allow_crash, self.crashed = allow_crash, False
        self.on_exit: list = []             # process.on("exit") listeners
        self.before_exit: list = []         # process.once("beforeExit") listeners
        self.out: list[str] = []
        self.jobs: deque = deque()          # V8's microtask queue
        self.ticks: deque = deque()         # process.nextTick's queue
        self.lists: dict[int, _TList] = {}  # one timer list per duration
        self.next_id = 0
        self.imm: list = []                 # setImmediate: for the next check phase
        self.now = 0                        # virtual milliseconds
        self.unhandled: list[_P] = []
        self.steps = 0
        self.gvars: dict = {}

    # -- promises (ECMAScript 27.2) --
    def fulfill(self, p, v):
        rs, p.state, p.value, p.fr, p.rr = p.fr, "fulfilled", v, None, None
        for h, cap in rs:
            self.jobs.append((self.reaction, h, cap, True, v))

    def reject(self, p, e):
        rs, p.state, p.value, p.fr, p.rr = p.rr, "rejected", e, None, None
        if not p.handled:
            self.unhandled.append(p)
        for h, cap in rs:
            self.jobs.append((self.reaction, h, cap, False, e))

    def resolving(self, p):
        done = [False]

        def resolve(x):
            if not done[0]:
                done[0] = True
                self._resolve(p, x)

        def rej(e):
            if not done[0]:
                done[0] = True
                self.reject(p, e)
        return resolve, rej

    def _resolve(self, p, x):
        if x is p:
            self.reject(p, "TypeError")
        elif isinstance(x, (_P, _Thenable)):   # a thenable: its then runs in a job of its own
            self.jobs.append((self.thenable_job, p, x))
        else:
            self.fulfill(p, x)

    def thenable_job(self, p, x):
        resolve, rej = self.resolving(p)
        if isinstance(x, _P):   # Promise.prototype.then(resolve, reject): a derived promise nobody sees
            self.perform_then(x, lambda v: resolve(v), lambda e: rej(e), self.capability())
            return
        try:
            self.run_sync(x.block, x.env)
            resolve(UNDEF)
        except JSThrow as e:
            rej(e.value)

    def perform_then(self, p, onf, onr, cap):
        if p.state == "pending":
            p.fr.append((onf, cap))
            p.rr.append((onr, cap))
        elif p.state == "fulfilled":
            self.jobs.append((self.reaction, onf, cap, True, p.value))
        else:
            if not p.handled and p in self.unhandled:   # a handler added after the rejection
                self.unhandled.remove(p)
            self.jobs.append((self.reaction, onr, cap, False, p.value))
        p.handled = True

    def reaction(self, h, cap, ok, arg):
        if h is None:
            val = arg
        else:
            try:
                val, ok = h(arg), True
            except JSThrow as e:
                val, ok = e.value, False
        if cap is not None:
            (cap[1] if ok else cap[2])(val)

    def capability(self):
        p = _P()
        return (p,) + self.resolving(p)

    def then(self, p, onf, onr):
        cap = self.capability()
        self.perform_then(p, onf, onr, cap)
        return cap[0]

    def promise_resolve(self, x):
        if isinstance(x, _P):
            return x
        cap = self.capability()
        cap[1](x)
        return cap[0]

    def finally_(self, p, on_finally):
        def then_finally(value):
            return self.then(self.promise_resolve(on_finally(UNDEF)), lambda _v: value, None)

        def catch_finally(reason):
            def thrower(_v):
                raise JSThrow(reason)
            return self.then(self.promise_resolve(on_finally(UNDEF)), thrower, None)
        return self.then(p, then_finally, catch_finally)

    # -- async functions (Await: PromiseResolve, then PerformPromiseThen without a derived promise) --
    def call_async(self, name):
        return self.call_block_async(self.fns[name], {"vars": self.gvars})

    def call_block_async(self, block, env):
        cap = self.capability()
        self.step(self.run(block, env), cap, True, None)
        return cap[0]

    def combinator(self, kind, xs, env):
        """Promise.all / allSettled / any / race (27.2.4): each element through Promise.resolve, then .then(...) on it;
        the result settles when the element functions say so."""
        cap = self.capability()
        n = len(xs)
        vals, left = [UNDEF] * n, [1]

        def finish():
            left[0] -= 1
            if left[0] == 0:
                if kind == "any":
                    cap[2]("AggregateError")
                else:
                    cap[1](JSArr(vals))
        items = [self.value(x, env) for x in xs]   # the array literal is evaluated before Promise.xxx walks it
        for i, x in enumerate(items):
            nxt = self.promise_resolve(x)
            if kind == "race":
                self.then(nxt, lambda v: cap[1](v), lambda e: cap[2](e))
                continue
            left[0] += 1
            called = [False]

            def once(f, called=called):
                def g(v):
                    if not called[0]:
                        called[0] = True
                        f(v)
                return g

            def store(v, i=i):
                vals[i] = v
                finish()
            if kind == "all":
                self.then(nxt, once(store), lambda e: cap[2](e))
            elif kind == "allSettled":
                self.then(nxt, once(store), once(store))
            else:   # any
                self.then(nxt, lambda v: cap[1](v), once(store))
        if kind != "race":
            finish()
        return cap[0]

    def step(self, gen, cap, ok, val):
        try:
            aw = gen.send(val) if ok else gen.throw(JSThrow(val))
        except StopIteration as s:
            cap[1](s.value.value if isinstance(s.value, _Ret) else UNDEF)
            return
        except JSThrow as e:
            cap[2](e.value)
            return
        self.perform_then(self.promise_resolve(aw), lambda v: self.step(gen, cap, True, v),
                          lambda e: self.step(gen, cap, False, e), None)

    # -- statements --
    def value(self, x, env):
        k = x[0]
        if k == "num":
            return float(x[1])
        if k == "null":
            return NULL
        if k == "undef":
            return UNDEF
        if k == "res":
            return self.promise_resolve(UNDEF)
        if k == "resv":
            return self.promise_resolve(self.value(x[1], env))
        if k == "rejp":
            p = _P()
            self.reject(p, "Error")
            return p
        if k == "thenable":
            return _Thenable(x[1], env)
        if k == "call":
            return self.call_async(x[1])
        if k == "var":
            return env["vars"][x[1]]
        if k == "newp":
            p = _P()
            res, rej = self.resolving(p)
            try:
                self.run_sync(x[1], dict(env, resolve=res, reject=rej))
            except JSThrow as e:
                rej(e.value)
            return p
        if k == "sleep":
            p = _P()
            res, _rej = self.resolving(p)
            self.set_timeout([("res", None)], dict(env, resolve=res), x[1])
            return p
        if k in ("all", "allSettled", "any", "race"):
            return self.combinator(k, x[1], env)
        raise ValueError(x)

    def handler(self, block, env, is_async: bool = False):
        if is_async:   # `async () => {...}`: the handler returns its promise, the chain waits for it
            return lambda _arg: self.call_block_async(block, env)

        def h(_arg):
            r = self.run_sync(block, env)
            return r.value if isinstance(r, _Ret) else UNDEF
        return h

    def chain(self, src, links, env):
        p = self.value(src, env)
        for ln in links:
            h = self.handler(ln[1], env, len(ln) > 2 and ln[2] == "async")
            p = self.then(p, h, None) if ln[0] == "then" else self.then(p, None, h) if ln[0] == "catch" else self.finally_(p, h)
        return p

    def run(self, block, env):
        for st in block:
            self.steps += 1
            if self.steps > 20000:
                raise Invalid("runaway")
            k = st[0]
            if k == "log":
                self.out.append(st[1])
            elif k == "tick":
                self.ticks.append((st[1], env))
            elif k == "micro":
                self.jobs.append((self.run_sync, st[1], env))
            elif k == "timeout":
                self.set_timeout(st[1], env, st[2])
            elif k == "immediate":
                self.imm.append((st[1], env))
                self.check_imm()
            elif k == "chain":
                p = self.chain(st[1], st[2], env)
                if st[3]:
                    env["vars"][st[3]] = p
            elif k == "await":
                yield self.value(st[1], env)
            elif k == "try":
                try:
                    r = yield from self.run(st[1], env)
                except JSThrow:
                    r = yield from self.run(st[2], env)
                if isinstance(r, _Ret):
                    return r
            elif k == "return":
                return _Ret(self.value(st[1], env))
            elif k == "throw":
                raise JSThrow("Error")
            elif k == "res":
                env["resolve"](UNDEF if st[1] is None else self.value(st[1], env))
            elif k == "rej":
                env["reject"]("Error")
            elif k == "onexit":
                self.on_exit.append((st[1], env))
            elif k == "beforeexit":
                self.before_exit.append((st[1], env))
            else:
                raise ValueError(st)
        return None

    def run_sync(self, block, env):
        g = self.run(block, env)
        try:
            next(g)
        except StopIteration as s:
            return s.value
        raise Invalid("await outside an async function")

    # -- Node's queues --
    def drain(self):
        """processTicksAndRejections: every nextTick callback, then the microtask queue to empty, again while ticks
        remain; then unhandled rejections are processed (Node 15+: the process dies)."""
        while True:
            while self.ticks:
                b, e = self.ticks.popleft()
                self.run_sync(b, e)
            while self.jobs:
                j = self.jobs.popleft()
                j[0](*j[1:])
                self.steps += 1
                if self.steps > 20000:
                    raise Invalid("runaway")
            if not self.ticks:
                break
        if any(not p.handled for p in self.unhandled):
            raise _Crash() if self.allow_crash else Invalid("unhandled rejection")
        self.unhandled = []

    def pending_timers(self):
        return [(s + L.msecs, L) for L in self.lists.values() for s, _b, _e in L.timers]

    def set_timeout(self, block, env, delay):
        ms = delay if 1 <= delay <= 2 ** 31 - 1 else 1
        exp = self.now + ms
        if any(L.msecs > ms for _e, L in self.pending_timers()):   # it would overtake a timer set earlier: a race
            raise Invalid("a shorter timer set while a longer one waits")
        L = self.lists.get(ms)
        if L is None:
            L = self.lists[ms] = _TList(ms, exp, self.next_id)
            self.next_id += 1
        L.timers.append((self.now, block, env))
        self.check_imm()

    def check_imm(self):
        """An immediate waiting for a later check phase never shares the loop with a timer (which runs first depends on
        the clock)."""
        if self.imm and self.pending_timers():
            raise Invalid("setImmediate and a timer both waiting")

    def run_timers(self):
        while True:
            due = [L for L in self.lists.values() if L.expiry <= self.now]
            if not due:
                return
            L = min(due, key=lambda x: (x.expiry, x.id))
            while L.timers:
                s, b, e = L.timers[0]
                if self.now - s < L.msecs:
                    L.expiry, L.id = max(s + L.msecs, self.now + 1), self.next_id
                    self.next_id += 1
                    break
                L.timers.popleft()
                self.run_sync(b, e)
                self.drain()
            if not L.timers and self.lists.get(L.msecs) is L:
                del self.lists[L.msecs]

    def loop(self):
        while self.lists or self.imm:
            if not self.imm:   # poll waits for the next timer
                self.now = min(L.expiry for L in self.lists.values())
            batch, self.imm = self.imm, []
            for b, e in batch:   # check phase
                self.run_sync(b, e)
                self.drain()
            self.check_imm()
            self.run_timers()
            if self.now > 5000:
                raise Invalid("too long")

    def execute(self) -> list[str]:
        try:
            self.run_sync(self.main, {"vars": self.gvars})
            self.drain()
            self.run_timers()
            self.loop()
            # the loop is empty: 'beforeExit' (a once-listener may schedule more work, then the loop runs again), then
            # 'exit' - after its listeners V8 still empties the microtask queue, but Node runs no nextTick and no timer
            while self.before_exit:
                batch, self.before_exit = self.before_exit, []
                for b, e in batch:
                    self.run_sync(b, e)
                self.drain()
                self.loop()
            for b, e in self.on_exit:
                self.run_sync(b, e)
            while self.jobs:
                j = self.jobs.popleft()
                j[0](*j[1:])
        except _Crash:
            if self.on_exit:
                raise Invalid("a crash with exit listeners")
            self.crashed = True
        return self.out


def run_program(prog: dict, allow_crash: bool = False) -> list[str]:
    return Loop(prog, allow_crash).execute()


# ---- printing a program as JavaScript ------------------------------------------------------------------------------------

def _jx(x, ind: str) -> str:
    k = x[0]
    if k == "num":
        return str(x[1])
    if k == "null":
        return "null"
    if k == "undef":
        return "undefined"
    if k == "res":
        return "Promise.resolve()"
    if k == "resv":
        return f"Promise.resolve({_jx(x[1], ind)})"
    if k == "rejp":
        return 'Promise.reject(new Error("x"))'
    if k == "thenable":
        body = " ".join(_js_stmt(s, "")[0] for s in x[1])
        return "{ then(resolve) { " + (body + " " if body else "") + "resolve(); } }"
    if k == "call":
        return f"{x[1]}()"
    if k == "var":
        return x[1]
    if k == "newp":
        rj = any(s[0] == "rej" for s in _walk(x[1]))
        inner = _js_block(x[1], ind + "  ")
        return f"new Promise((resolve{', reject' if rj else ''}) => {{\n" + "\n".join(inner) + f"\n{ind}}})"
    if k == "sleep":
        return f"new Promise((resolve) => setTimeout(resolve, {x[1]}))"
    if k in ("all", "allSettled", "any", "race"):
        return f"Promise.{k}([" + ", ".join(_jx(e, ind) for e in x[1]) + "])"
    raise ValueError(x)


def _walk(block):
    for s in block:
        yield s
        for part in s[1:]:
            if isinstance(part, list) and part and isinstance(part[0], tuple):
                yield from _walk(part)


def _cb(block, ind: str, is_async: bool = False) -> str:
    """An arrow callback: `() => console.log("a1")` for a single log, a block body otherwise."""
    a = "async " if is_async else ""
    if len(block) == 1 and block[0][0] == "log":
        return f'{a}() => console.log("{block[0][1]}")'
    if len(block) == 1 and block[0][0] == "return" and block[0][1][0] in ("res", "resv", "num", "call", "var", "rejp"):
        return f"{a}() => {_jx(block[0][1], ind)}"
    return f"{a}() => {{\n" + "\n".join(_js_block(block, ind + "  ")) + f"\n{ind}}}"


def _js_stmt(st, ind: str) -> list[str]:
    k = st[0]
    if k == "log":
        return [f'{ind}console.log("{st[1]}");']
    if k in ("tick", "micro", "immediate"):
        fn = {"tick": "process.nextTick", "micro": "queueMicrotask", "immediate": "setImmediate"}[k]
        return [f"{ind}{fn}({_cb(st[1], ind)});"]
    if k == "timeout":
        return [f"{ind}setTimeout({_cb(st[1], ind)}, {st[2]});"]
    if k == "chain":
        head = f"{ind}{'const ' + st[3] + ' = ' if st[3] else ''}{_jx(st[1], ind)}"
        if not st[2]:
            return [head + ";"]
        links = [f".{ln[0]}({_cb(ln[1], ind + '  ', len(ln) > 2 and ln[2] == 'async')})" for ln in st[2]]
        if len(links) == 1 and "\n" not in links[0] and len(head) + len(links[0]) < 100:
            return [head + links[0] + ";"]
        return [head] + [f"{ind}  {l_}" for l_ in links[:-1]] + [f"{ind}  {links[-1]};"]
    if k == "await":
        return [f"{ind}await {_jx(st[1], ind)};"]
    if k == "try":
        return [f"{ind}try {{"] + _js_block(st[1], ind + "  ") + [f"{ind}}} catch {{"] + _js_block(st[2], ind + "  ") + [f"{ind}}}"]
    if k == "return":
        return [f"{ind}return {_jx(st[1], ind)};"]
    if k == "throw":
        return [f'{ind}throw new Error("x");']
    if k == "res":
        return [f"{ind}resolve({'' if st[1] is None else _jx(st[1], ind)});"]
    if k == "rej":
        return [f'{ind}reject(new Error("x"));']
    if k == "onexit":
        return [f'{ind}process.on("exit", {_cb(st[1], ind)});']
    if k == "beforeexit":
        return [f'{ind}process.once("beforeExit", {_cb(st[1], ind)});']
    raise ValueError(st)


def _js_block(block, ind: str) -> list[str]:
    out = []
    for s in block:
        out += "\n".join(_js_stmt(s, ind)).split("\n")
    return out


def program_js(prog: dict) -> str:
    parts = []
    for name, body in prog["fns"].items():
        parts.append(f"async function {name}() {{\n" + "\n".join(_js_block(body, "  ")) + "\n}")
    parts.append("\n".join(_js_block(prog["main"], "")))
    return "\n\n".join(parts)


# ---- generating programs ---------------------------------------------------------------------------------------------------
# Each level adds constructs (cumulative); a seed changes the program. `mode` keeps the macrotasks apart: "micro" has none,
# "timer" has setTimeout (setImmediate only inside a timer callback, level 7+), "imm" has setImmediate and no timers.

_LETTERS = "abcdefghijkmnpqrstuvwxyz"   # no l / o: they read as 1 / 0
_FN_NAMES = ["load", "save", "sync", "fetchUser", "render", "flush", "retry", "init", "worker", "task", "step", "poll", "notify"]
SIZE = {1: (4, 6), 2: (6, 8), 3: (7, 10), 4: (8, 11), 5: (10, 13), 6: (11, 15), 7: (13, 17), 8: (15, 20), 9: (22, 30), 10: (30, 40)}


def _features(level: int) -> set[str]:
    f = {"tick", "then"}
    if level >= 2:
        f |= {"micro", "chain"}
    if level >= 3:
        f |= {"macro"}
    if level >= 4:
        f |= {"async", "await_val", "await_res"}
    if level >= 5:
        f |= {"await_thenable", "newp", "delays"}
    if level >= 6:
        f |= {"ret_promise", "ret_thenable", "finally", "async_ret", "fnthen", "await_call", "resthenable"}
    if level >= 7:
        f |= {"reject", "trycatch", "save", "imm_in_timer", "newp_defer", "throw_then"}
    if level >= 8:
        f |= {"sleep", "await_var", "fn_reuse", "async_throw"}
    if level >= 9:   # Claude Opus traced every level-9 program of the first version right: more of Node's own behaviour
        f |= {"async_handler", "combinator"}
    return f


class _PGen:
    def __init__(self, r, level: int, mode: str):
        self.r, self.level, self.mode = r, level, mode
        labs = [c + d for c in _LETTERS for d in "123456789"]
        r.shuffle(labs)
        self.labs = iter(labs)
        self.F = _features(level)
        self.fns: dict[str, list] = {}
        self.names = list(_FN_NAMES)
        r.shuffle(self.names)
        self.saved: list[str] = []
        self.saved_ok: list[str] = []   # the saved promises that do not reject
        self.nlog = 0
        self.in_timer = False
        self.maxdepth = 1 if level == 1 else 2 if level <= 4 else 3

    def log(self):
        self.nlog += 1
        return ("log", next(self.labs))

    def delay(self, nested: bool) -> int:
        if "delays" not in self.F:
            return self.r.choice([0, 0, 1])
        return self.r.choice([0, 1, 50] if nested else [0, 1, 0, 50, 100])

    def body(self, depth: int, size: int | None = None) -> list:
        r = self.r
        out = [self.log()] if r.random() < 0.85 else []
        n = size if size is not None else r.choice([0, 0, 1, 1, 2]) if depth < self.maxdepth else 0
        for _ in range(n):
            out.append(self.actor(depth))
            if r.random() < 0.3:
                out.append(self.log())
        return out or [self.log()]

    def ret(self):
        """What a then handler returns (None: nothing)."""
        r, F = self.r, self.F
        opts = [None] * 4
        if "ret_promise" in F:
            opts += [("res",), ("resv", ("num", r.randint(1, 9)))]
        if "ret_thenable" in F:
            opts += [("thenable", [self.log()] if r.random() < 0.6 else [])]
        return r.choice(opts)

    def links(self, depth: int, rejected: bool) -> list:
        r, F = self.r, self.F
        n = r.randint(1, 2 if self.level < 4 else 3 if self.level < 8 else 4)
        out, pending_rej = [], rejected
        for i in range(n):
            kinds = ["then"] * 4 + (["finally"] if "finally" in F else []) + (["catch"] if "reject" in F else [])
            k = r.choice(kinds)
            b = self.body(depth + 1, size=0 if depth + 1 >= self.maxdepth else None)
            if k in ("then", "catch"):
                rv = self.ret()
                if rv is not None:
                    b = b + [("return", rv)]
                elif "throw_then" in F and k == "then" and r.random() < 0.2:
                    b = b + [r.choice([("throw",), ("return", ("rejp",))])]
                    pending_rej = True
            if k == "then" and "async_handler" in F and r.random() < 0.3:   # .then(async () => {...}): awaited by the chain
                b = [self.log(), ("await", self.await_expr(depth + 1))] + ([self.log()] if r.random() < 0.8 else [])
                out.append((k, b, "async"))
                continue
            out.append((k, b))
            if k == "catch":
                pending_rej = False
        if pending_rej:   # a rejection is caught further down, never left unhandled
            out.append(("catch", [self.log()]))
            if r.random() < 0.5:
                out.append(("then", [self.log()]))
        return out

    def make_fn(self, depth: int) -> str:
        r, F = self.r, self.F
        done = sorted(n for n, b in self.fns.items() if b and b[-1] != ("throw",))
        if "fn_reuse" in F and done and r.random() < 0.2:
            return r.choice(done)
        name = self.names.pop() if self.names else f"f{len(self.fns) + 1}"
        self.fns[name] = []   # reserved: a nested call may not pick itself
        body = [self.log()] if r.random() < 0.8 else []
        for _ in range(r.randint(1, 2 if self.level < 7 else 3)):
            if "trycatch" in F and r.random() < 0.2:
                body.append(("try", [("await", ("rejp",)), self.log()], [self.log()]))
            else:
                body.append(("await", self.await_expr(depth)))
            if r.random() < 0.8:
                body.append(self.log())
            if depth + 1 < self.maxdepth and r.random() < 0.25:
                body.append(self.actor(depth + 1))
        if "async_throw" in F and r.random() < 0.15:
            body.append(("throw",))
        elif "async_ret" in F and r.random() < 0.4:
            body.append(("return", r.choice([("res",), ("num", r.randint(1, 9)), ("thenable", [self.log()]), ("resv", ("num", 1))])))
        self.fns[name] = body
        return name

    def await_expr(self, depth: int):
        r, F = self.r, self.F
        opts = [("num", r.randint(1, 9)), ("null",), ("undef",)] if "await_val" in F else []
        opts += [("res",)] * 2
        if "await_thenable" in F:
            opts += [("thenable", [self.log()] if r.random() < 0.7 else [])]
        if "await_call" in F and depth + 1 < self.maxdepth and len(self.fns) < 4:
            opts += [("callfn",)]
        if "await_var" in F and self.saved_ok:
            opts += [("var", r.choice(self.saved_ok))]
        if "sleep" in F and self.mode == "timer":
            opts += [("sleep", self.delay(True))]
        if "newp" in F:
            opts += [("newpx",)]
        if "combinator" in F:
            opts += [("comb",)]
        x = r.choice(opts)
        if x[0] == "comb":
            return (r.choice(["all", "all", "allSettled", "race", "any"]), self.elements(depth + 1, rej=False))
        if x[0] == "callfn":
            return ("call", self.make_fn(depth + 1))
        if x[0] == "newpx":
            return ("newp", self.executor(depth + 1))
        return x

    def executor(self, depth: int) -> list:
        r, F = self.r, self.F
        b = [self.log()] if r.random() < 0.8 else []
        how = r.choice(["now", "now"] + (["tick", "micro"] if "newp_defer" in F else []) + (["timer"] if self.mode == "timer" else []))
        val = r.choice([None, None] + ([("res",)] if "ret_promise" in F else []) + ([("thenable", [])] if "ret_thenable" in F else []))
        fin = [("res", val)]
        if how == "now":
            b += fin
            if r.random() < 0.4:
                b.append(self.log())   # the executor goes on after resolve()
        elif how == "timer":
            b.append(("timeout", [self.log()] + fin, self.delay(depth > 0)))
        else:
            b.append((how, [self.log()] + fin))
        return b

    def actor(self, depth: int):
        r, F, mode = self.r, self.F, self.mode
        opts = [("log", 3), ("tick", 3), ("then", 3)]
        if "micro" in F:
            opts += [("micro", 2), ("chain", 3)]
        if "macro" in F and mode == "timer":
            opts += [("timeout", 3 if depth == 0 else 1)]
        if "macro" in F and mode == "imm":
            opts += [("immediate", 3 if depth == 0 else 1)]
        if "imm_in_timer" in F and mode == "timer" and self.in_timer:
            opts += [("immediate", 2)]
        if "async" in F and depth < self.maxdepth:
            opts += [("async", 3)]
        if "newp" in F:
            opts += [("newp", 2)]
        if "reject" in F:
            opts += [("rejchain", 2)]
        if "save" in F and depth == 0:
            opts += [("save", 1)]
        if "combinator" in F and depth < self.maxdepth:
            opts += [("comb", 2)]
        if depth >= self.maxdepth:
            opts = [o for o in opts if o[0] in ("log", "then", "tick", "micro")]
        k = r.choices([o[0] for o in opts], weights=[o[1] for o in opts])[0]
        inner = lambda: self.body(depth + 1, size=0 if depth + 1 >= self.maxdepth else None)
        if k == "log":
            return self.log()
        if k in ("tick", "micro"):
            return (k, inner())
        if k == "immediate":
            return ("immediate", inner())
        if k == "timeout":
            was, self.in_timer = self.in_timer, True
            b = inner()
            self.in_timer = was
            return ("timeout", b, self.delay(depth > 0))
        if k == "then":
            src = ("res",) if "resthenable" not in F or r.random() < 0.7 else ("resv", ("thenable", [self.log()]))
            return ("chain", src, [("then", inner())], None)
        if k == "chain":
            return ("chain", ("res",), self.links(depth, False), None)
        if k == "async":
            f = self.make_fn(depth)
            if "fnthen" in F and r.random() < 0.5 or f in self._throwers():
                return ("chain", ("call", f), self.links(depth, f in self._throwers()), None)
            return ("chain", ("call", f), [], None)
        if k == "newp":
            return ("chain", ("newp", self.executor(depth + 1)), self.links(depth, False), None)
        if k == "rejchain":
            return ("chain", ("rejp",), self.links(depth, True), None)
        if k == "comb":
            kind = r.choice(["all", "all", "allSettled", "race", "any"])
            xs = self.elements(depth + 1, rej=True)
            return ("chain", (kind, xs), self.links(depth, ("rejp",) in xs and kind != "allSettled"), None)
        # save: a promise kept in a variable, handled later from a callback (before the tick queue runs dry)
        v = f"p{len(self.saved) + 1}"
        src = r.choice([("rejp",), ("res",), ("call", self.make_fn(depth))])
        rejects = src == ("rejp",) or (src[0] == "call" and src[1] in self._throwers())
        self.saved.append(v)
        if not rejects:
            self.saved_ok.append(v)
        later = ("chain", ("var", v), self.links(depth + 1, rejects), None)
        return ("save", ("chain", src, [], v), (r.choice(["tick", "micro"]), [self.log(), later]))

    def elements(self, depth: int, rej: bool) -> list:
        """The array given to Promise.all / allSettled / any / race: one-line values, a rejection only where handled."""
        r = self.r
        kinds = ["res", "num", "thenable", "resvthen"] + (["callfn"] if depth < self.maxdepth else []) + \
            (["sleep"] if self.mode == "timer" else []) + (["rejp"] if rej else [])
        out = []
        for _ in range(r.randint(2, 3)):   # every element gets its own labels
            k = r.choice(kinds)
            out.append({"res": lambda: ("res",), "num": lambda: ("num", r.randint(1, 9)), "rejp": lambda: ("rejp",),
                        "thenable": lambda: ("thenable", [self.log()]), "resvthen": lambda: ("resv", ("thenable", [self.log()])),
                        "callfn": lambda: ("call", self.make_fn(depth)), "sleep": lambda: ("sleep", self.delay(True))}[k]())
        return out

    def _throwers(self) -> set[str]:
        return {n for n, b in self.fns.items() if b and b[-1] == ("throw",)}

    def program(self, variant: str = "plain") -> dict:
        r = self.r
        lo, hi = SIZE[self.level]
        target = r.randint(lo, hi)
        main = []
        while self.nlog < target:
            main += self.top()
        if variant == "exit":   # listeners for the end: 'beforeExit' may schedule more work, 'exit' runs microtasks only
            for kind in r.sample(["onexit", "beforeexit"], r.choice([1, 2])):
                b = [self.log(), r.choice([("tick", [self.log()]), ("chain", ("res",), [("then", [self.log()])], None),
                                           ("micro", [self.log()])])]
                if kind == "beforeexit" or r.random() < 0.5:   # one macrotask: in 'exit' it never runs
                    b.append(("timeout", [self.log()], r.choice([0, 1, 50])) if self.mode != "imm" else ("immediate", [self.log()]))
                if r.random() < 0.5:
                    b.append(self.log())
                main.insert(r.randint(0, len(main)), (kind, b))
        return {"fns": self.fns, "main": main}

    def top(self) -> list:
        a = self.actor(0)
        if a[0] == "save":   # the variable first, the late handler a little later
            return [a[1]] + self.top() + [a[2]]
        return [a]


def _blocks(block):
    """Every statement list inside a block (callback bodies, handler bodies), the block itself first."""
    yield block
    for s in block:
        for part in s[1:]:
            if isinstance(part, list) and part and isinstance(part[0], tuple):
                if part[0][0] in ("then", "catch", "finally"):
                    for ln in part:
                        yield from _blocks(ln[1])
                else:
                    yield from _blocks(part)


def _count(prog: dict, kinds) -> int:
    return sum(1 for b in [prog["main"]] + list(prog["fns"].values()) for s in _walk(b) if s[0] in kinds)


def gen_program(r, level: int, mode: str, variant: str = "plain") -> tuple[dict, list[str], bool]:
    """A program of this level and mode that real Node runs the same way every time: (program, output, crashed).
    variant "crash": it dies of an unhandled rejection with labels still to come; "exit": exit / beforeExit listeners."""
    lo, hi = SIZE[level]
    for _ in range(400):
        prog = _PGen(r, level, mode).program(variant)
        if mode != "micro" and _count(prog, ("timeout", "immediate")) < 2:   # a timer / immediate program has two or more
            continue
        try:
            out = run_program(prog)
        except Invalid:
            continue
        if not lo - 2 <= len(out) <= hi + 8:
            continue
        if variant != "crash":
            return prog, out, False
        # a rejection nobody handles, put in some callback: Node 15+ dies when that drain ends - labels still to come
        # (at least three of them) are never printed
        blk = r.choice(list(_blocks(prog["main"])))
        blk.insert(r.randint(0, len(blk)), ("chain", ("rejp",), [], None))
        loop = Loop(prog, allow_crash=True)
        try:
            cut = loop.execute()
        except Invalid:
            continue
        if loop.crashed and 3 <= len(cut) <= len(out) - 3:
            return prog, cut, True
    raise RuntimeError(f"no valid program for level {level} {mode} {variant}")


# ---- made-up APIs: none exists in Node 22 (nor 24 or 25: tests/test_knowledge_js.py asks each of them) ----------------------

FAKE_SYNC = ['[3, 1, 2].sortBy((x) => x)', '[1, [2, [3]]].flatten()', '[1, 2, 3].contains(2)', '"abc".contains("b")',
             '[1, 2, 3].last()', 'Object.deepFreeze({ a: 1 })', 'Math.clamp(5, 0, 3)', '[1, 1, 2].unique()', '"hello".capitalize()',
             '[1, 2, 3].sum()', 'Math.sum(1, 2)', 'Object.deepEqual({}, {})', 'Number.isNumeric("5")', 'JSON.tryParse("{")',
             'JSON.safeParse("[1]")', '[0, 1, null].compact()', '"abc".reverse()', 'Array.range(1, 4)', 'Object.isEmpty({})',
             '"{0}!".format("hi")', '[1, 2, 3].first()', 'Object.merge({ a: 1 }, { b: 2 })', 'Math.avg(1, 2, 3)', '"a b".title()',
             '[1, 2, 3, 4].chunk(2)', '[1, 2].zip([3, 4])', 'Math.randomInt(1, 6)', 'Math.mean(1, 2)', '(1.5).round()',
             '"".isEmpty()', '[3, 1, 2].max()', '[1, 2, 3].removeAt(0)', 'Object.clone({ a: 1 })', '[1, 2, 3].shuffle()',
             '[1, 2, 3].toSortedDesc()', '"a,b".lines()', '[{ a: 1 }].pluck("a")', '[1, 2].insert(0, 5)', '"aXa".count("a")',
             '[1, 1].distinct()']
FAKE_ASYNC = ['Promise.delay(10).then(() => console.log("{a}"));', 'Promise.sleep(5).then(() => console.log("{a}"));',
              'process.nextTickAsync().then(() => console.log("{a}"));', 'setTimeout.promise(10).then(() => console.log("{a}"));',
              'Promise.resolve(1).done(() => console.log("{a}"));', 'Promise.resolve(1).tap(() => console.log("{a}"));',
              'Promise.timeout(Promise.resolve(), 10).then(() => console.log("{a}"));',
              'Promise.map([1, 2], (x) => x).then(() => console.log("{a}"));', 'process.setImmediate(() => console.log("{a}"));',
              'Promise.sequence([]).then(() => console.log("{a}"));', 'queueMicrotask.flush(() => console.log("{a}"));']


def _fake(r, level: int, hidden: bool) -> dict:
    """A made-up call: a line to print, a small label program whose first statement uses it, or (levels 9-10) the last
    argument of a line whose other arguments are real - evaluating it throws before anything is printed."""
    if hidden:
        q = semantic_question(r, level, 3)
        text = q["text"][:-2] + ", " + r.choice(FAKE_SYNC) + ");"
        return {"kind": KIND, "src": "hidden", "level": level, "fake": True, "text": text, "mode": "fake", "accept": {"exc": "TypeError"}}
    if r.random() < 0.5:
        return {"kind": KIND, "src": "fake", "level": level, "fake": True, "text": f"console.log({r.choice(FAKE_SYNC)});",
                "mode": "fake", "accept": {"exc": "TypeError"}}
    a, b = r.sample([c + d for c in _LETTERS for d in "123456789"], 2)
    return {"kind": KIND, "src": "fake", "level": level, "fake": True, "text": r.choice(FAKE_ASYNC).format(a=a) + f'\nconsole.log("{b}");',
            "mode": "fake", "accept": {"exc": "TypeError"}}


# ---- items ----------------------------------------------------------------------------------------------------------------------

# per level: event-loop programs, one-line questions and their arguments; exactly 2 made up (levels 9-10: one inside a line)
PLAN = {1: (3, 3, 1), 2: (3, 3, 1), 3: (3, 3, 2), 4: (3, 3, 2), 5: (4, 2, 3), 6: (4, 2, 3), 7: (5, 3, 3), 8: (5, 3, 4), 9: (6, 2, 4),
        10: (7, 3, 5)}
HEAD = ("Quick Node.js questions. Each program is saved as `main.js` in an empty directory and run with `node main.js` on "
        "Node.js 22 (Linux), as a CommonJS module. What does it print to standard output? When every `console.log` prints a "
        "label, answer with the labels in the order they are printed, separated by spaces. Otherwise answer with the line "
        "exactly as Node prints it (strings without quotes; arrays and objects as `util.inspect` shows them). If the program "
        "throws before it prints anything, answer `throws <ErrorName>`.")


def gen(seed: int, level: int = 3) -> Item:
    from . import knowledge as K   # the block's shared rules and answer parsing (imported late: knowledge imports this module)
    r = rng(BLOCK, f"{KIND}{level}", seed)
    n_prog, n_line, n_args = PLAN[level]
    modes = ["micro"] * n_prog if level < 3 else [["micro", "timer", "imm"][i % 3] for i in range(n_prog)]
    r.shuffle(modes)
    # levels 9-10: one program dies of an unhandled rejection part-way, one has exit / beforeExit listeners
    variants = ["plain"] * n_prog if level < 9 else ["crash", "exit"] + ["plain"] * (n_prog - 2)
    if level >= 9 and modes[0] == "micro":   # the crash program needs macrotasks: a crash in the one drain cuts nothing
        modes[0] = r.choice(["timer", "imm"])
    qs = []
    for mode, variant in zip(modes, variants):
        prog, out, crashed = gen_program(r, level, mode, variant)
        qs.append({"kind": KIND, "src": f"loop-{mode}" + (f"-{variant}" if variant != "plain" else ""), "level": level, "fake": False,
                   "text": program_js(prog), "mode": "seq", "accept": {"seq": out, **({"crash": True} if crashed else {})}})
    for _ in range(n_line):
        qs.append(semantic_question(r, level, n_args))
    qs.append(_fake(r, level, hidden=level >= 9))
    qs.append(_fake(r, level, hidden=False))
    r.shuffle(qs)
    body = "\n".join(f"{i}. " + (f"`{q['text']}`" if "\n" not in q["text"] else f"\n```js\n{q['text']}\n```") for i, q in enumerate(qs, 1))
    prompt = f"{HEAD} {K.RULES}\n\n{body}{K.TAIL}"

    def check(text: str, _t=None, qs=qs) -> float:
        got = K.answers(text, len(qs))
        return sum(credit(q, got.get(i)) for i, q in enumerate(qs, 1)) / len(qs)
    return Item(f"{BLOCK}.{KIND}.L{level}.{seed}", BLOCK, KIND, [{"role": "user", "content": prompt}], check, max_tokens=32000,
                meta={"level": level, "questions": qs, "expected": [oracle_answer(q) for q in qs]})


# ---- grading ----------------------------------------------------------------------------------------------------------------------

_LABEL = re.compile(r"(?<![A-Za-z0-9_])([a-z][1-9])(?![A-Za-z0-9_])")


def _unwrap(a: str) -> str:
    a = a.strip()
    for _ in range(3):
        a = re.sub(r"^\*\*(.*)\*\*$", r"\1", a).strip()
        m = re.fullmatch(r"(`+)(.*?)\1", a, re.S)
        if m:
            a = m.group(2).strip()
    return a


def _cands(a: str) -> list[str]:
    a = _unwrap(a)
    head = re.split(r"\s+(?:—|–|--|-)\s+|\s+\((?:because|since|as|i\.e\.|the|it|note|then)\b", a)[0]
    out = [a, a.rstrip("."), _unwrap(head), _unwrap(head).rstrip("."), re.sub(r"\s*\([^()]*\)\s*$", "", a)]
    return list(dict.fromkeys(x for x in out if x))


def _norm_line(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip())
    s = re.sub(r"\s*([\[\]{},:])\s*", r"\1", s)
    return s.replace('"', "'")


def _exc_named(a: str, name: str) -> bool:
    return bool(re.search(rf"\b{name}\b", a))


def verdict(q: dict, a: str | None) -> str:
    """right / idk / wrong for one question."""
    if a is None or not a.strip():
        return "wrong"
    up = _unwrap(a).upper()
    if re.match(r"[\s`'\"*_]*(UNKNOWN|I DON'?T KNOW|DON'?T KNOW|NOT SURE)\b", up):
        return "idk"
    says_none = bool(re.match(r"[\s`'\"*_]*(NONEXISTENT|NON-EXISTENT|DOES NOT EXIST|DOESN'?T EXIST|NO SUCH)\b", up))
    if q["fake"]:
        return "right" if says_none or _exc_named(a, "TypeError") or re.search(r"is not a function", a) else "wrong"
    if says_none:
        return "wrong"
    for c in _cands(a):
        if q["mode"] == "seq":
            if _LABEL.findall(c) == q["accept"]["seq"]:
                return "right"
        elif q["mode"] == "line":
            x = c[1:-1] if len(c) >= 2 and c[0] == c[-1] and c[0] in "'\"" and c.count(c[0]) == 2 else c
            if _norm_line(c) == _norm_line(q["accept"]["line"]) or _norm_line(x) == _norm_line(q["accept"]["line"]):
                return "right"
        elif q["mode"] == "throws":
            if _exc_named(c, q["accept"]["exc"]) and not re.search(r"\bor\b", c):
                return "right"
    return "wrong"


def credit(q: dict, a: str | None) -> float:
    v = verdict(q, a)
    return 1.0 if v == "right" else IDK if v == "idk" else 0.0


def oracle_answer(q: dict) -> str:
    if q["fake"]:
        return "NONEXISTENT"
    if q["mode"] == "seq":
        return " ".join(q["accept"]["seq"])
    if q["mode"] == "throws":
        return f"throws {q['accept']['exc']}"
    return q["accept"]["line"]


def breakdown(it: Item, text: str) -> dict:
    from . import knowledge as K
    qs = it.meta["questions"]
    got = K.answers(text, len(qs))
    out = {"right": 0, "idk": 0, "wrong": 0, "refused": 0, "invented": 0}
    for i, q in enumerate(qs, 1):
        v = verdict(q, got.get(i))
        if q["fake"]:
            out["refused" if v != "wrong" else "invented"] += 1
        else:
            out[v] += 1
    return out
