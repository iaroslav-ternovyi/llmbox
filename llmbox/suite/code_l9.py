"""Code levels 9-10 (v0.11): aimed at the frontier, which passed every hidden test of levels 7-8 in seconds. The specs
here have many interacting rules and the hidden tests probe the edge cases a quick read gets wrong. Tests are layered:
each one exercises a subset of the rules, so a solution that misses one rule still passes the tests that do not need it
(credit = share of tests passed, no 0/1 cliff). On top, scale cases (built when an item is graded, 15x the reference's
time and at least 3 s each): 1,500-deep nesting and 30,000-term expressions, 60,000 cache ops, 20,000 meetings, cron
schedules decades apart - a scan per op or a minute-by-minute walk fails them. Python only: the difficulty is the spec,
not the language. Opus 5.5 (default effort, 2026-09-29) still passed every case of expr, lru, rooms and cron at level 10
with the scale cases announced in the spec; its earlier code without them scored 0.95 / 0.75 / 0.78 / 1.0 on them.

Called from code.py at level >= 9 (levels 1-8 stay as they were). The reference functions compute the hidden tests
and are the oracle's reply.
"""
from __future__ import annotations

import inspect
import json

from .common import Item, rng
from .code import BLOCK, _extract_code, _gen7_expr, _run


# ================================================================== expr: a small language with functions (9) and := (10)

def _ref9_expr(src: str, env: dict, level: int):
    """Statements separated by ';': let, fn definitions and expressions. Python-style comparison chains, lazy ?: && ||,
    signed 32-bit arithmetic, user functions (parameters first, then globals at call time; separate namespaces; arity
    and depth errors); level 10 adds (name := expr) with strict left-to-right evaluation."""
    import re
    toks = re.findall(r"\d+|[A-Za-z_]\w*|:=|&&|\|\||==|!=|<=|>=|\S", src)
    pos = [0]
    cmp_ops = ("<", "<=", ">", ">=", "==", "!=")

    def peek(k=0):
        return toks[pos[0] + k] if pos[0] + k < len(toks) else None

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

    def ternary():
        c = orr()
        if peek() == "?":
            take()
            a = ternary()
            take()                                   # ':'
            return ("?", c, a, ternary())
        return c

    def orr():
        left = andd()
        while peek() == "||":
            take()
            left = ("||", left, andd())
        return left

    def andd():
        left = chain()
        while peek() == "&&":
            take()
            left = ("&&", left, chain())
        return left

    def chain():
        first = add()
        if peek() not in cmp_ops:
            return first
        ops, xs = [], [first]
        while peek() in cmp_ops:
            ops.append(take())
            xs.append(add())
        return ("chain", ops, xs)

    def add():
        left = mul()
        while peek() in ("+", "-"):
            op = take()
            left = (op, left, mul())
        return left

    def mul():
        left = unary()
        while peek() in ("*", "/", "%"):
            op = take()
            left = (op, left, unary())
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
            if level >= 10 and peek(1) == ":=":
                name = take()
                take()                               # ':='
                e = ternary()
                take()                               # ')'
                return ("set", name, e)
            e = ternary()
            take()                                   # ')'
            return e
        if t.isdigit():
            return ("num", int(t))
        if peek() == "(":
            take()
            args = []
            if peek() != ")":
                args.append(ternary())
                while peek() == ",":
                    take()
                    args.append(ternary())
            take()                                   # ')'
            return ("call", t, args)
        return ("var", t)

    stmts = []
    while True:
        if peek() == "let":
            take()
            name = take()
            take()                                   # '='
            stmts.append(("let", name, ternary()))
        elif peek() == "fn":
            take()
            name = take()
            take()                                   # '('
            params = []
            while peek() != ")":
                params.append(take())
                if peek() == ",":
                    take()
            take()                                   # ')'
            take()                                   # '='
            stmts.append(("fn", name, params, ternary()))
        else:
            stmts.append(("expr", ternary()))
        if peek() != ";":
            break
        take()

    class Fail(Exception):
        pass

    wrap = lambda v: (v + 2 ** 31) % 2 ** 32 - 2 ** 31
    glob, funcs, frames = dict(env), {}, []

    def tdiv(a, b):
        q = abs(a) // abs(b)
        return q if (a < 0) == (b < 0) else -q

    def ev(e):
        k = e[0]
        if k == "num":
            return e[1]
        if k == "var":
            if frames and e[1] in frames[-1]:
                return frames[-1][e[1]]
            if e[1] in glob:
                return glob[e[1]]
            raise Fail
        if k == "set":
            v = ev(e[2])
            if frames and e[1] in frames[-1]:
                frames[-1][e[1]] = v
            else:
                glob[e[1]] = v
            return v
        if k == "u-":
            return wrap(-ev(e[1]))
        if k == "u!":
            return int(ev(e[1]) == 0)
        if k == "?":
            return ev(e[2]) if ev(e[1]) != 0 else ev(e[3])
        if k == "&&":
            return int(ev(e[1]) != 0 and ev(e[2]) != 0)
        if k == "||":
            return int(ev(e[1]) != 0 or ev(e[2]) != 0)
        if k == "chain":
            left = ev(e[2][0])
            for op, x in zip(e[1], e[2][1:]):
                right = ev(x)
                if not {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right,
                        "==": left == right, "!=": left != right}[op]:
                    return 0
                left = right
            return 1
        if k == "call":
            name = e[1]
            vals = [ev(a) for a in e[2]]
            if name in ("max", "min", "abs"):
                if not vals or (name == "abs" and len(vals) != 1):
                    raise Fail
                return wrap(max(vals) if name == "max" else min(vals) if name == "min" else abs(vals[0]))
            if name not in funcs or len(vals) != len(funcs[name][0]) or len(frames) >= 50:
                raise Fail
            frames.append(dict(zip(funcs[name][0], vals)))
            try:
                return ev(funcs[name][1])
            finally:
                frames.pop()
        a, b = ev(e[1]), ev(e[2])
        if k in ("/", "%") and b == 0:
            raise Fail
        if k == "+":
            return wrap(a + b)
        if k == "-":
            return wrap(a - b)
        if k == "*":
            return wrap(a * b)
        if k == "/":
            return wrap(tdiv(a, b))
        if k == "%":
            return wrap(a - b * tdiv(a, b))
        return wrap(a ** b)                          # '^' (exponents are never negative)

    try:
        v = None
        for st in stmts:
            if st[0] == "let":
                v = ev(st[2])
                glob[st[1]] = v
            elif st[0] == "fn":
                funcs[st[1]] = (st[2], st[3])
                v = 0
            else:
                v = ev(st[1])
        return v
    except Fail:
        return "error"


def _spec9_expr(r, level: int):
    env = lambda: {"x": r.randint(-5, 9), "y": r.randint(1, 6), "z": r.randint(-3, 3)}
    n_ = lambda lo=2, hi=9: r.randint(lo, hi)
    base = [lambda: (f"let a = {n_()} * x; a - {n_()} ^ 2 + (y > 3 ? a : -a)", env()),
            lambda: (f"2147483647 + {n_(1, 9)} * y", env()),
            lambda: (f"let q = {n_()} / (z - z); {n_()}", env()),
            lambda: (f"0 && 1 / 0 || -{n_()} % {n_()}", env()),
            lambda: (f"max({n_()}, x, y) - min(z, {n_()}) * abs(x - {n_(10, 20)})", env())]
    chain = [lambda: (lambda a, b, c: (f"{a} < {b} <= {c}", env()))(n_(1, 4), n_(4, 6), n_(5, 9)),
             lambda: (f"{n_(1, 3)} < {n_(5, 9)} > {n_(1, 4)}", env()),                     # true although 1 > 4 is false
             lambda: (f"{n_(2, 5)} < {n_(6, 9)} == 1", env()),                              # (a < b) and (b == 1): 0
             lambda: (f"{n_(6, 9)} > {n_(7, 9)} < 1 / 0", env()),                           # stops at the first false
             lambda: (f"{n_(1, 3)} < {n_(5, 9)} < 1 / 0", env()),                           # reaches the division: error
             lambda: (f"(x < y) + (y < {n_()} < {n_(10, 20)}) * 2 + x < y + {n_()} == 1", env()),
             lambda: (f"x != y != x", env()),
             lambda: (f"{n_()} == {n_()} != {n_()} <= {n_()}", env())]
    funcs = [lambda: (f"fn sq(v) = v * v; sq({n_()}) + sq(x)", env()),
             lambda: (f"fn fact(n) = n <= 1 ? 1 : n * fact(n - 1); fact({r.choice([6, 9, 12, 13, 14])})", env()),
             lambda: (f"fn down(n) = n == 0 ? 0 : 1 + down(n - 1); down({r.choice([30, 45, 49, 50, 51, 60])})", env()),
             lambda: (f"fn add(a, b) = a + b; add({n_()}{r.choice(['', ', 1, 2'])})", env()),
             lambda: (f"let k = {n_()}; fn scale(v) = v * k; let k = {n_(10, 20)}; scale({n_()})", env()),
             lambda: (f"let v = {n_(50, 99)}; fn f(v) = v + 1; f({n_()}) + v", env()),
             lambda: (f"let g = {n_()}; fn g(a) = a * 2; g(g) + g", env()),
             lambda: (f"fn early() = late() + 1; let r = early(); fn late() = {n_()}; r", env()),
             lambda: (f"fn early() = late() + 1; fn late() = {n_()}; early() * 2", env()),
             lambda: (f"fn h(a) = a; h + 1", env()),
             lambda: (f"fn z0() = x * {n_()}; z0() + z0()", env()),
             lambda: (f"fn bad() = 1 / 0; (0 && bad()) + (1 || bad()) + (x > 100 ? bad() : {n_()})", env()),
             lambda: (f"fn bad() = 1 / 0; let u = bad(); {n_()}", env()),
             lambda: (f"fn ev(n) = n == 0 ? 1 : od(n - 1); fn od(n) = n == 0 ? 0 : ev(n - 1); ev({n_(5, 20)}) * 10 + od({n_(5, 20)})", env()),
             lambda: (f"fn pick(c, a, b) = c ? a : b; pick(0, 1 / 0, {n_()})", env()),        # arguments are always evaluated
             lambda: (f"fn t(a) = x + a; t({n_()}) + t(y)", env()),
             lambda: (f"abs({n_()}, 1) + 1", env())]
    sets = [lambda: (f"(a := {n_()}) + a * 2", env()),
            lambda: ("x + (x := x * 2) + x", env()),
            lambda: (f"0 && (y := {n_(50, 99)}); 1 || (y := 5); y", env()),
            lambda: (f"{n_(1, 3)} < (z := z + {n_()}) < 100; z", env()),
            lambda: (f"fn inc(v) = (v := v + 1) + (w := {n_(10, 20)}); let w = 0; inc({n_()}) + w", env()),
            lambda: ("let c = 0; fn tick() = (c := c + 1); tick() + tick() * 10 + c", env()),
            lambda: (f"x > 100 ? (x := 0) : (y := x + {n_()}); x * 100 + y", env()),
            lambda: (f"(q := {n_()}) + 1 / 0", env()),
            lambda: (f"(m := {n_()}); let m = m * 3; m", env()),
            lambda: (f"fn two(a, b) = a * 10 + b; two((t := {n_(1, 5)}), (t := t + {n_()})) + t", env()),
            lambda: (f"fn loop(n) = n == 0 ? s : loop(((s := s + n)) - s + n - 1); let s = 0; loop({n_(3, 8)})", env())]
    picks = r.sample(base, 3) + r.sample(chain, 6) + r.sample(funcs, 11) + (r.sample(sets, 8) if level >= 10 else [])
    cases = []
    for f in picks:
        src, env_ = f()
        cases.append([[src, env_], _ref9_expr(src, env_, level)])
    while len(cases) < (24 if level == 9 else 32):    # random programs with a function over the whole grammar
        env_ = env()
        body = _gen7_expr(r, 2, ["a", "b", "x"])
        src = (f"fn f(a, b) = {body}; let t = {_gen7_expr(r, 2, ['x', 'y'])}; "
               f"f({_gen7_expr(r, 1, ['t', 'y'])}, {_gen7_expr(r, 1, ['x', 'z'])}) + t")
        try:
            v = _ref9_expr(src, env_, level)
        except RecursionError:
            continue
        if v != "error":
            cases.append([[src, env_], v])
    rules = [
        "evaluate(program: string, env: object/dict of integer variables) -> integer, or the string \"error\".",
        "A program is one or more statements separated by ';':",
        "- 'let NAME = expression' evaluates the expression, then binds NAME as a global variable (hiding an env variable or an "
        "earlier binding of that name);",
        "- 'fn NAME(p1, p2, ...) = expression' defines (or redefines) a function with zero or more parameters; its value is 0;",
        "- an expression.",
        "The result is the value of the last statement.",
        "Values are signed 32-bit integers: every arithmetic result wraps around in two's complement (2147483647 + 1 = "
        "-2147483648). Literals are at most 2147483647 and exponents are never negative.",
        "Operators from LOWEST to HIGHEST precedence:",
        "  1. c ? a : b   right-associative; only the chosen branch is evaluated",
        "  2. ||          left-associative, result 1 or 0; the right side is evaluated only when the left side is 0",
        "  3. &&          left-associative, result 1 or 0; the right side is evaluated only when the left side is not 0",
        "  4. < <= > >= == !=   all six share one level and CHAIN like Python: a < b <= c means (a < b) && (b <= c), with "
        "b evaluated once; operands are evaluated left to right and the chain stops at the first false comparison (later "
        "operands are not evaluated); result 1 or 0",
        "  5. + -         left-associative",
        "  6. * / %       left-associative; '/' truncates toward zero; '%' is a - b*trunc(a/b)",
        "  7. - !         prefix; !v is 1 if v is 0, else 0",
        "  8. ^           power, right-associative and tighter than a prefix operator before it (-2^2 = -4)",
        "Operands: integer literals, variable names, parenthesized expressions and calls NAME(arg, ...). Any value other "
        "than 0 counts as true.",
        "Calls: the arguments are always evaluated, left to right, before the call. max(...) and min(...) take one or more "
        "arguments and abs(v) exactly one. Any other NAME is a user function looked up when the call happens (so a function "
        "may call itself or a function defined later, as long as that definition has run by then). Inside a function body a "
        "name is its parameter if it has one of that name, otherwise the global variable as it is at the moment of use "
        "(the caller's parameters are NOT visible). Functions and variables are separate namespaces: 'let f = 3' and "
        "'fn f(v) = v' can coexist, and a function name alone is not a variable.",
    ]
    if level >= 10:
        rules.append(
            "Assignment: '(NAME := expression)' - only inside parentheses - evaluates the expression, assigns it and has its "
            "value. It assigns the parameter NAME of the function call currently running if that call has such a parameter "
            "(for the rest of that call), otherwise the global variable NAME (creating it). Evaluation is strictly left to "
            "right everywhere (both operands of an operator, call arguments, chain operands), and assignments in parts that "
            "are not evaluated do not happen.")
    rules.append(
        "Errors - the result of the whole program is the string \"error\" if, in any part that is actually evaluated: a "
        "division or '%' by zero happens; a variable is not defined; a function is not defined (yet); a call has the wrong "
        "number of arguments; or a call would be the 51st nested call (more than 50 user-function calls active at once).")
    rules.append("Do NOT use eval or exec.")
    return "evaluate", "\n".join(rules), cases, {"level": level}


# ================================================================== lru: LFU with aging, pins and TTL; namespaces at 10

def _ref9_lru(ops: list, cap: int, ttl: int, age: int, quotas: dict, level: int) -> list:
    """Evict the lowest frequency (ties: least recently used), never a pinned entry; frequencies halve at every multiple
    of `age`; entries expire after their ttl; level 10: per-namespace quotas ('ns:key') and touch."""
    store, out, clock, prev_t = {}, [], 0, 0

    def victim(keys):
        cands = [k for k in keys if not store[k]["pin"]]
        return min(cands, key=lambda k: (store[k]["f"], store[k]["used"])) if cands else None

    def make_room(keys_of, limit) -> bool:
        while len(keys_of()) >= limit:
            v = victim(keys_of())
            if v is None:
                return False
            del store[v]
        return True

    for op in ops:
        kind = op[0]
        t = op[3] if kind == "put" else op[-1]
        for _m in range(prev_t // age + 1, t // age + 1):   # every multiple of age in (previous time, t]
            for e in store.values():
                e["f"] //= 2
        prev_t = t
        for k in [k for k, e in store.items() if t - e["ts"] >= e["ttl"]]:
            del store[k]
        clock += 1
        if kind == "put":
            k, v, life = op[1], op[2], op[4] if len(op) > 4 else ttl
            if k in store:
                store[k].update(v=v, ts=t, ttl=life, used=clock, f=store[k]["f"] + 1)
                continue
            if level >= 10:
                ns = k.split(":")[0]
                if not make_room(lambda: [x for x in store if x.split(":")[0] == ns], quotas[ns]):
                    continue
            if not make_room(lambda: list(store), cap):
                continue
            store[k] = {"v": v, "f": 1, "ts": t, "ttl": life, "pin": False, "used": clock}
        elif kind in ("get", "peek"):
            e = store.get(op[1])
            out.append(e["v"] if e else -1)
            if e and kind == "get":
                e["f"] += 1
                e["used"] = clock
        elif kind in ("pin", "unpin", "touch"):
            e = store.get(op[1])
            out.append(1 if e else 0)
            if e and kind == "touch":
                e["ts"] = t
            elif e:
                e["pin"] = kind == "pin"
        elif kind == "del":
            out.append(0 if store.pop(op[1], None) is None else 1)
        else:                                        # resize
            cap, n0 = op[1], len(store)
            while len(store) > cap:
                v = victim(list(store))
                if v is None:
                    break
                del store[v]
            out.append(n0 - len(store))
    return out


def _spec9_lru(r, level: int):
    cap, ttl, age = r.randint(3, 4), r.randint(6, 9), r.randint(4, 6)
    quotas = {"img": 2, "api": 3, "db": 2, "bulk": 1_000_000} if level >= 10 else {}
    keys = (["img:a", "img:b", "img:c", "api:a", "api:b", "api:c", "db:a", "db:b"] if level >= 10
            else ["a", "b", "c", "d", "e", "f"])
    layers = [("plain", 3), ("pins", 3), ("aging", 3), ("all", 3)] + ([("ns", 3)] if level >= 10 else [])
    cases = []
    for layer, count in layers:
        for _ in range(count):
            ops, t = [], 0
            kinds = {"plain": ["put", "get", "peek", "del"], "pins": ["put", "get", "peek", "pin", "unpin", "del"],
                     "aging": ["put", "get", "get", "peek"], "all": ["put", "get", "peek", "pin", "unpin", "del", "resize"],
                     "ns": ["put", "get", "pin", "unpin", "touch", "resize"]}[layer]
            if level >= 10 and layer == "all":
                kinds = kinds + ["touch"]
            for _ in range(r.randint(14, 22)):
                t += r.choice([0, 1, 1, 2]) if layer in ("plain", "pins") else r.choice([0, 1, 2, 3])
                if layer in ("plain", "pins"):
                    t = min(t, age - 1)                  # no aging in these layers
                k = r.choice(keys)
                kind = r.choice(kinds + ["put", "get"])
                if kind == "put":
                    op = ["put", k, r.randint(1, 99), t] + ([r.choice([2, 3, 12])] if r.random() < 0.2 else [])
                elif kind == "resize":
                    op = ["resize", r.randint(1, 5), t]
                else:
                    op = [kind, k, t]
                ops.append(op)
            cases.append([[ops], _ref9_lru(ops, cap, ttl, age, quotas, level)])
    desc = (f"run_cache(ops: list) -> list of integers. Simulate a cache holding at most {cap} entries (the capacity), with "
            f"a default time-to-live of {ttl}. Each entry has a value, a use count, a last-use moment, an expiry clock and a "
            "pinned flag. Times are integers and never decrease.\n"
            "Before handling ANY op at time t, in this order:\n"
            f"1. Aging: for every multiple of {age} that lies in (time of the previous op, t] - the previous time is 0 for "
            "the first op - every entry's use count is halved, rounded down (so it happens once per multiple passed).\n"
            "2. Expiry: every entry whose clock started at ts with t - ts >= its ttl is removed (pinned ones too).\n"
            "Eviction rule (whenever an entry must be evicted): among the entries that are NOT pinned, remove the one with "
            "the lowest use count; ties: the least recently used one (last put or get hit, earliest first).\nOps:\n"
            "- [\"put\", key, value, time] or [\"put\", key, value, time, ttl]: if the key is in the cache, replace its value "
            "and ttl, restart its clock, add 1 to its use count and make it the most recently used (nothing is evicted). "
            "Otherwise")
    if level >= 10:
        desc += (" (keys look like 'namespace:name') first, while the key's namespace already holds its quota of entries "
                 f"(quotas: {json.dumps(quotas)}), evict an entry OF THAT NAMESPACE by the eviction rule; then")
    desc += (", while the cache holds at least capacity entries, evict by the eviction rule. If no entry can be evicted "
             "when one is needed, the put is ignored (evictions already done stay done). A new entry has use count 1, is "
             "unpinned, is the most recently used and its clock starts now with the given ttl or the default.\n"
             "- [\"get\", key, time]: the value, or -1. A hit adds 1 to the use count and makes it the most recently used "
             "(the clock is NOT restarted).\n"
             "- [\"peek\", key, time]: the value, or -1; changes nothing.\n"
             "- [\"pin\", key, time] / [\"unpin\", key, time]: set / clear the pinned flag; 1 if the key is in the cache, else 0.\n"
             "- [\"del\", key, time]: remove the key (pinned or not); 1 if it was in the cache, else 0.\n"
             "- [\"resize\", capacity, time]: set the capacity, then evict by the eviction rule while the cache holds more "
             "entries than the capacity and something can be evicted; returns how many were evicted.\n")
    if level >= 10:
        desc += "- [\"touch\", key, time]: restart the key's expiry clock (nothing else changes); 1 if present, else 0.\n"
    desc += "Return the results of all ops except put, in order."
    return "run_cache", desc, cases, {"cap": cap, "ttl": ttl, "age": age, "quotas": quotas, "level": level}


# ================================================================== csv: amounts in the file's locale; dates, comments, ids at 10

def _ref9_csv(text: str, month: str, level: int) -> dict:
    """BOM, delimiter from the header line, RFC 4180 quoting, amounts whose separators follow the delimiter (',' files:
    1,234.56; ';' files: 1.234,56), '-' or parentheses, a currency sign; level 10: dates, comment lines, last id wins,
    one month, the max per region."""
    import datetime as _dt
    import re
    if text.startswith("﻿"):
        text = text[1:]
    first = re.sub(r'"[^"]*"', "", text.split("\n", 1)[0])
    d = ";" if first.count(";") > first.count(",") else ","
    records, i, n = [], 0, len(text)
    while i < n:
        if level >= 10 and text[i] == "#":
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
            while i < n and text[i] not in d + "\r\n":
                field += text[i]
                i += 1
            row.append(field)
            if i < n and text[i] == d:
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
    th, dec = (",", ".") if d == "," else (".", ",")
    num = rf"([0-9]{{1,3}}(?:{re.escape(th)}[0-9]{{3}})+|[0-9]+)(?:{re.escape(dec)}([0-9]{{1,2}}))?"

    def cents(s: str):
        s, neg = s.strip(), False
        if s.startswith("(") and s.endswith(")"):
            s, neg = s[1:-1], True
        elif s.startswith("-"):
            s, neg = s[1:], True
        if s[:1] in ("€", "$"):
            s = s[1:]
        m = re.fullmatch(num, s)
        if not m:
            return None
        v = int(m.group(1).replace(th, "")) * 100 + int((m.group(2) or "").ljust(2, "0"))
        return -v if neg else v

    def date(s: str):
        s = s.strip()
        m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", s)
        if m:
            y, mo, da = map(int, m.groups())
        else:
            m = re.fullmatch(r"([0-9]{2})/([0-9]{2})/([0-9]{4})", s)
            if not m:
                return None
            a, b, y = map(int, m.groups())
            da, mo = (a, b) if d == ";" else (b, a)
        try:
            return _dt.date(y, mo, da)
        except ValueError:
            return None

    kept = []
    for rec in records[1:]:
        if len(rec) != len(head):
            continue
        get = lambda name: rec[head.index(name)]
        region, c = get("region").strip(), cents(get("amount"))
        if not region or c is None:
            continue
        when = date(get("date")) if level >= 10 else None
        if level >= 10 and when is None:
            continue
        kept.append((get("id").strip(), region, c, when))
    if level >= 10:
        last = {rid: k for k, (rid, _g, _c, _w) in enumerate(kept)}
        kept = [x for k, x in enumerate(kept) if last[x[0]] == k and x[3].strftime("%Y-%m") == month]
    out = {}
    for _rid, region, c, _w in kept:
        e = out.setdefault(region, {"cents": 0, "rows": 0})
        e["cents"] += c
        e["rows"] += 1
        if level >= 10:
            e["max"] = max(e.get("max", c), c)
    return out


def _spec9_csv(r, level: int):
    month = f"2026-{r.randint(2, 11):02d}"
    y, mo = map(int, month.split("-"))
    layers = ["comma", "semicolon", "styles", "invalid"] * 3 + (["dates", "dates"] if level >= 10 else [])
    cases = []
    for layer in layers:
        d = ";" if layer == "semicolon" or (layer not in ("comma",) and r.random() < 0.5) else ","
        th, dec = (",", ".") if d == "," else (".", ",")

        def amount() -> str:
            whole, frac = r.randint(0, 2500), r.randint(0, 99)
            fr = r.choice([f"{dec}{frac:02d}", f"{dec}{frac % 10}", ""])
            if layer in ("styles", "invalid", "dates") and whole >= 1000 and r.random() < 0.7:
                w = f"{whole // 1000}{th}{whole % 1000:03d}"
            else:
                w = str(whole)
            s = w + fr
            if layer in ("styles", "invalid", "dates"):
                sign = r.choice(["", "", "-", "()"])
                cur = r.choice(["", "", "€", "$"])
                s = f"({cur}{s})" if sign == "()" else f"{sign}{cur}{s}"
                if r.random() < 0.3:
                    s = f" {s} "
            elif r.random() < 0.3:
                s = "-" + s
            if layer == "invalid" and r.random() < 0.45:
                s = r.choice([f"{r.randint(1, 99)}{dec}{r.randint(100, 999)}", f"{r.randint(1, 9)}{th}{r.randint(10, 99)}{dec}5",
                              f"{r.randint(1, 99)}{dec}", "", "n/a", f"(-{r.randint(1, 9)})", f"€-{r.randint(1, 9)}",
                              f"- {r.randint(1, 9)}", f"+{r.randint(1, 9)}", f"1{dec}2{dec}3", f"{dec}{r.randint(1, 9)}"])
            return s

        def when() -> str:
            if r.random() < 0.15:
                return r.choice([f"{y}-02-30", f"31/04/{y}" if d == ";" else f"04/31/{y}", f"{y}/{mo:02d}/01", "tomorrow"])
            m_ = mo if r.random() < 0.6 else r.choice([mo - 1, mo + 1])
            day = r.randint(1, 28)
            fmt = r.choice(["iso", "slash"])
            if fmt == "iso":
                return f"{y}-{m_:02d}-{day:02d}"
            return f"{day:02d}/{m_:02d}/{y}" if d == ";" else f"{m_:02d}/{day:02d}/{y}"

        def field(s: str) -> str:
            if any(ch in s for ch in (d, '"', "\n", "\r")) or (s and r.random() < 0.15):
                return '"' + s.replace('"', '""') + '"'
            return s

        cols = ["id", "region", "amount", "note"] + (["date"] if level >= 10 else [])
        cols = r.sample(cols, len(cols))
        head = [r.choice([c, c.upper(), c.capitalize(), f" {c}"]) for c in cols]
        nl = r.choice(["\n", "\r\n"])
        lines = [d.join(head)]
        for i in range(r.randint(6, 10)):
            if level >= 10 and r.random() < 0.12:
                lines.append("#" + d.join(["9", "North", "50", "x", f"{y}-{mo:02d}-01"][:len(cols)]))
                continue
            val = {"id": str(r.randint(1, 7)) if level >= 10 else str(i + 1),
                   "region": r.choice(["North", "South", " East", "West ", "North", ""]),
                   "amount": amount(), "note": r.choice(["ok", "a;b", "a,b", 'said "hi"', "two\nlines", ""]),
                   "date": when() if level >= 10 else ""}
            fields = [field(val[c]) for c in cols]
            if r.random() < 0.07:
                fields = fields[:-1]
            lines.append(d.join(fields))
        text = ("﻿" if layer in ("styles", "dates") and r.random() < 0.5 else "") + nl.join(lines) + nl
        cases.append([[text], _ref9_csv(text, month, level)])
    desc = ("summarize(csv_text: string) -> object/dict.\n"
            "File format:\n"
            "- A leading byte-order mark (\\ufeff) is ignored.\n"
            "- The delimiter is ';' if the first line contains more ';' than ',' (not counting characters inside double "
            "quotes), otherwise ','.\n"
            "- Records are separated by \\n or \\r\\n. A field may be enclosed in double quotes; then it can contain the "
            "delimiter, line breaks and doubled quotes (\"\" stands for one \").\n"
            f"- The first record is the header: column names, trimmed and case-insensitive, are "
            f"{'id, region, amount, note and date' if level >= 10 else 'id, region, amount and note'}, in any order.\n")
    if level >= 10:
        desc += ("- A line that starts with '#' outside a quoted field is a comment and is ignored entirely.\n")
    desc += ("Amounts (trimmed of surrounding spaces) follow the file's delimiter: in ',' files the decimal separator is '.' "
             "and the thousands separator ','; in ';' files the decimal separator is ',' and the thousands separator '.'. "
             "Valid form: an optional '-' OR parentheses around the whole amount (negative), then an optional currency sign "
             "'€' or '$', then digits - either plain digits, or 1-3 digits followed by groups of exactly 3 digits each "
             "introduced by the thousands separator - then optionally the decimal separator with 1 or 2 digits. Nothing "
             "else (no spaces inside, no '+'). Examples in a ',' file: valid 12, -3.5, (4.00), $1,234.56, 0.99; invalid "
             "12.345, 1,23.4, 5., .5, +5, (-5), €-5, - 5.\n")
    if level >= 10:
        desc += ("Dates (trimmed): YYYY-MM-DD in any file; also DD/MM/YYYY in ';' files and MM/DD/YYYY in ',' files (two "
                 "digits for day and month); it must be a real calendar date.\n")
    desc += ("Skip a data record if its number of fields differs from the header's (blank lines included), its region is "
             "empty after trimming, or its amount is invalid" + (", or its date is invalid" if level >= 10 else "") + ".\n")
    if level >= 10:
        desc += (f"Among the records that are not skipped, when several have the same id (trimmed), only the LAST one "
                 f"counts; then keep only the records dated in {month} (year-month).\n")
    desc += ("Return, per region (trimmed, case-sensitive), {\"cents\": the sum of the amounts in cents (an integer), "
             "\"rows\": the number of records" + (", \"max\": the largest amount in cents" if level >= 10 else "")
             + "}. Regions without records do not appear.")
    return "summarize", desc, cases, {"month": month, "level": level}


# ================================================================== rooms: priorities that bump; cascades and maintenance at 10

def _ref9_rooms(rooms: list, meetings: list, maint: list, buf: int, big: int, level: int) -> list:
    """Best fit over fixed rooms with cleaning after each meeting; a meeting that finds no room may bump one lower-priority
    meeting, which is then re-placed (level 10: and may bump in turn); level 10 also has maintenance windows."""
    n = len(meetings)
    booked = {k: [] for k in range(len(rooms))}
    where = [-1] * n

    def busy(i):
        return meetings[i][0], meetings[i][1] + (2 * buf if meetings[i][2] > big else buf)

    def clashes(i, k):
        s, e = busy(i)
        return [j for j in booked[k] if s < busy(j)[1] and busy(j)[0] < e]

    def usable(i, k):
        return rooms[k] >= meetings[i][2] and not any(
            m[0] == k and meetings[i][0] < m[2] and m[1] < meetings[i][1] for m in maint)

    def place(i, may_bump):
        ok = [k for k in range(len(rooms)) if usable(i, k) and not clashes(i, k)]
        if ok:
            k = min(ok, key=lambda k: (rooms[k], k))
            booked[k].append(i)
            where[i] = k
            return
        cands = []
        if may_bump:
            for k in range(len(rooms)):
                c = clashes(i, k) if usable(i, k) else []
                if len(c) == 1 and meetings[c[0]][3] < meetings[i][3]:
                    cands.append((meetings[c[0]][3], rooms[k], k, c[0]))
        if not cands:
            where[i] = -1
            return
        _p, _c, k, j = min(cands)
        booked[k].remove(j)
        booked[k].append(i)
        where[i], where[j] = k, -1
        place(j, level >= 10)

    for i in sorted(range(n), key=lambda i: (meetings[i][0], -meetings[i][3], i)):
        place(i, True)
    return where


def _spec9_rooms(r, level: int):
    buf, big = r.choice([15, 30]), r.choice([8, 10, 12])
    layers = ["flat"] * 4 + ["prio"] * 5 + ["tight"] * 3 + (["maint"] * 2 if level >= 10 else [])
    cases = []
    for layer in layers:
        rooms = [r.choice([4, 6, 8, 10, 12, 20]) for _ in range(r.randint(2, 3) if layer == "tight" else r.randint(3, 4))]
        ms = []
        for _ in range(r.randint(7, 12)):
            s_ = r.randint(0, 24 if layer == "tight" else 36) * 15
            ms.append([s_, s_ + r.choice([15, 30, 45, 60, 90, 120]), r.randint(1, 22),
                       1 if layer == "flat" else r.randint(1, 3)])
        maint = []
        if level >= 10 and layer in ("maint", "prio"):
            for _ in range(r.randint(1, 2)):
                s_ = r.randint(0, 36) * 15
                maint.append([r.randrange(len(rooms)), s_, s_ + r.choice([30, 60, 90])])
        args = [rooms, ms, maint] if level >= 10 else [rooms, ms]
        cases.append([args, _ref9_rooms(rooms, ms, maint, buf, big, level)])
    sig = ("assign_rooms(rooms: list of capacities, meetings: list of [start, end, attendees, priority], maintenance: list "
           "of [room, start, end])" if level >= 10 else
           "assign_rooms(rooms: list of capacities, meetings: list of [start, end, attendees, priority])")
    desc = (sig + " -> list with the final room index of each meeting (input order), or -1. Times are minutes; priority "
            "is 1 (low) to 3 (high).\n"
            f"- Room i holds at most rooms[i] people. After each meeting its room needs cleaning: {buf} minutes, or "
            f"{2 * buf} minutes if the meeting had more than {big} attendees. A meeting therefore occupies its room over "
            "[start, end + cleaning).\n"
            "- A room can take a meeting if it is big enough"
            + (", the meeting itself [start, end) does not overlap any maintenance window [start, end) of that room "
               "(cleaning may overlap a window)" if level >= 10 else "")
            + " and the meeting's occupied interval does not overlap the occupied interval of any meeting currently "
            "booked in that room (touching is fine).\n"
            "- Process the meetings in order of start time; ties: higher priority first, then input order.\n"
            "- Placing a meeting: if some rooms can take it, book the one with the smallest capacity (ties: lowest index).\n"
            "- Bumping: if no room can take it, look at every room that is big enough"
            + (" and free of maintenance during the meeting" if level >= 10 else "")
            + " and whose current bookings overlap the meeting's occupied interval in exactly ONE booked meeting that has a "
            "LOWER priority. If there are such rooms, pick the one whose overlapping meeting has the lowest priority (ties: "
            "smaller room capacity, then lower room index), remove that meeting from the room and book the new meeting "
            "there. The removed meeting is then placed again right away by the placing rule"
            + (" - and if no room can take it, it may bump in turn by the same rule (a cascade)" if level >= 10 else
               " only (it cannot bump anything)")
            + "; if it cannot be placed, it ends with -1.\n"
            "- If a meeting can neither be placed nor bump, it gets -1.")
    return "assign_rooms", desc, cases, {"buf": buf, "big": big, "level": level}


# ================================================================== cron: Vixie day rule and DST policy; ISO weeks at 10

def _ref9_cron(expr: str, tz: str, start: str, n: int) -> list:
    """Level-8 cron syntax, with the Vixie day rule (a day field starting with '*' makes the two day fields combine with
    AND), explicit DST handling (jobs with an hour field starting with '*' run in both occurrences of a repeated hour and
    skip a spring-forward gap; other jobs run once, in the first occurrence, and run at the end of a gap for times in it),
    and an optional 6th field of ISO week numbers."""
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

    def weekday_near(y, m, k):
        last = calendar.monthrange(y, m)[1]
        if k > last:
            return None
        d = _dt.date(y, m, k)
        if d.weekday() == 5:
            return d - _dt.timedelta(days=1) if k > 1 else d + _dt.timedelta(days=2)
        if d.weekday() == 6:
            return d + _dt.timedelta(days=1) if k < last else d - _dt.timedelta(days=2)
        return d

    parts = expr.split()
    mi, ho, dom, mo, dow = parts[:5]
    weeks = field(parts[5], 1, 53, {}) if len(parts) > 5 else None
    M, H, MO = sorted(field(mi, 0, 59, {})), sorted(field(ho, 0, 23, {})), field(mo, 1, 12, months)
    star_hour = ho.startswith("*")

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
    last_day = t0.astimezone(z).date() + _dt.timedelta(days=146097)   # 400 Gregorian years
    while day <= last_day:
        if day.month in MO and (weeks is None or day.isocalendar()[1] in weeks):
            dm, dw = dom_ok(day), dow_ok(day)
            if (dm and dw) if (dom.startswith("*") or dow.startswith("*")) else (dm or dw):
                runs = set()
                for h in H:
                    for m in M:
                        loc = _dt.datetime(day.year, day.month, day.day, h, m, tzinfo=z)
                        u0 = loc.astimezone(utc)
                        if u0.astimezone(z).replace(tzinfo=None) != loc.replace(tzinfo=None):   # in a gap
                            if star_hour:
                                continue
                            after = loc.replace(tzinfo=None)
                            while True:
                                after += _dt.timedelta(minutes=1)
                                a = after.replace(tzinfo=z)
                                if a.astimezone(utc).astimezone(z).replace(tzinfo=None) == after:
                                    runs.add(a.astimezone(utc))
                                    break
                            continue
                        runs.add(u0)
                        u1 = loc.replace(fold=1).astimezone(utc)
                        if u1 != u0 and star_hour:     # a repeated local time
                            runs.add(u1)
                for u in sorted(runs):
                    if u > t0:
                        out.append(u.strftime("%Y-%m-%dT%H:%MZ"))
                        if len(out) == n:
                            return out
        day += _dt.timedelta(days=1)
    return out


_CRON9_CASES = [
    ("0 9 */2 * MON", "Europe/Madrid", "2026-03-01T00:00Z"),          # '*/2' starts with '*': odd days AND Mondays
    ("0 9 1-7 * MON", "Europe/Madrid", "2026-03-01T00:00Z"),          # both restricted: days 1-7 OR Mondays
    ("30 2 * * *", "Europe/Madrid", "2026-03-27T12:00Z"),             # fixed hour in the gap: runs at 03:00 CEST
    ("*/20 2 * * *", "Europe/Madrid", "2026-03-28T12:00Z"),           # fixed hour: 02:00, 02:20, 02:40 all collapse to 03:00
    ("15 * * * *", "Europe/Madrid", "2026-03-29T00:00Z"),             # star hour: 02:15 skipped
    ("30 1 * * *", "America/New_York", "2026-10-30T12:00Z"),          # fixed hour, repeated: once
    ("30 * 1 11 *", "America/New_York", "2026-11-01T04:00Z"),         # star hour, repeated: twice
    ("0 */2 * * SUN", "Europe/London", "2026-10-24T20:00Z"),          # star hour on the fall-back Sunday
    ("45 1 * 3 0L", "Europe/London", "2026-03-01T00:00Z"),            # last Sunday of March at 01:45: gap, runs at 02:00
    ("0 12 */10 * 5", "UTC", "2026-01-01T00:00Z"),                    # days 1, 11, 21, 31 that are Fridays
    ("0 12 1,15 * */3", "UTC", "2026-01-01T00:00Z"),                  # '*/3' dow starts with '*': AND
    ("0 3 L * *", "Australia/Sydney", "2026-09-25T00:00Z"),           # 3:00 exists; 4 Oct gap is 2:00-2:59
    ("10 2 4 10 *", "Australia/Sydney", "2026-10-01T00:00Z"),         # 02:10 on 4 Oct does not exist: runs at 03:00
    ("*/30 2 5 4 *", "Australia/Sydney", "2026-04-01T00:00Z"),        # fixed hour 2, repeated on 5 Apr: first only
    ("0 0 13 * FRI", "America/Los_Angeles", "2026-01-01T00:00Z"),     # the 13th OR Fridays
    ("5 0 * * 7#5", "America/Los_Angeles", "2026-01-01T00:00Z"),
]
_CRON10_CASES = [
    ("0 9 * * MON */2", "Europe/Madrid", "2026-12-01T00:00Z"),        # odd ISO weeks: week 53 then week 1 back to back
    ("0 8 * * * 1", "UTC", "2026-12-25T00:00Z"),                      # ISO week 1 of 2027 starts on 4 Jan
    ("0 8 * * * 53", "UTC", "2026-12-20T00:00Z"),                     # 2026 has 53 ISO weeks
    ("30 17 * * FRI 52-53,1", "America/New_York", "2026-12-15T00:00Z"),
    ("0 10 1 * * 1", "UTC", "2026-01-01T00:00Z"),                     # the 1st of a month in ISO week 1
    ("0 12 L * * */4", "Europe/Madrid", "2026-01-01T00:00Z"),
    ("0 2 * * SUN 13", "Europe/Madrid", "2026-03-01T00:00Z"),         # 29 Mar 2026 is in week 13: gap -> 03:00
    ("*/30 1 * * SUN 43", "Europe/London", "2026-10-01T00:00Z"),      # 25 Oct, fixed hour 1 in the repeated hour: first only
    ("0 * 25 10 * 43", "Europe/London", "2026-10-24T00:00Z"),         # star hour on the fall-back day
    ("0 9 1W * * 1-5", "America/New_York", "2026-12-01T00:00Z"),
    ("15 6 */7 * MON 2-10/2", "Asia/Tokyo", "2026-12-20T00:00Z"),
    ("0 0 * * 4 1", "UTC", "2020-12-01T00:00Z"),                      # Thursdays of week 1 across years
]


def _cron9(seed: int, level: int) -> Item:
    r = rng(BLOCK, f"cron{level}", seed)
    cases = r.sample(_CRON9_CASES, 10) if level == 9 else r.sample(_CRON9_CASES, 4) + r.sample(_CRON10_CASES, 8)
    r.shuffle(cases)
    tests = [[[e, tz, st, 5], _ref9_cron(e, tz, st, 5)] for e, tz, st in cases]
    small = [[a, e, 10.0] for a, e in tests]
    desc = ("next_runs(expr: str, tz: str, start: str, n: int) -> list[str]. expr is a cron schedule 'minute hour "
            "day-of-month month day-of-week" + (" [iso-week]" if level >= 10 else "") + "'.\n"
            "Syntax: every field takes numbers, '*', lists 'a,b', ranges 'a-b' and steps '*/s' and 'a-b/s'. Months may be "
            "names JAN-DEC and days of the week SUN-SAT (case-insensitive, also in lists and ranges); day-of-week numbers "
            "are 0-7 with 0 and 7 = Sunday. Special entries (they may appear in lists):\n"
            "- day-of-month 'L' (last day), 'L-k' (k days before the last day; no match if before the 1st), 'LW' (last "
            "Monday-Friday day), 'nW' (the Monday-Friday day nearest to day n of the same month: Saturday -> the Friday "
            "before, Sunday -> the Monday after, never leaving the month: Saturday the 1st -> Monday the 3rd, Sunday the "
            "last day -> the Friday before; no match if the month has no day n)\n"
            "- day-of-week 'dL' (the last such weekday of the month) and 'd#k' (the k-th such weekday; no match if none), d "
            "a number\n")
    if level >= 10:
        desc += ("- the optional 6th field lists ISO 8601 week numbers (1-53, same syntax; '*/2' = weeks 1, 3, 5, ...); a "
                 "day must also lie in one of those ISO weeks (ISO weeks start on Monday; week 1 is the week with the "
                 "year's first Thursday)\n")
    desc += ("Day rule (Vixie cron): if the day-of-month field or the day-of-week field starts with '*' (e.g. '*' or '*/2'), "
             "a day must match BOTH fields; otherwise a day matches when EITHER field matches.\n"
             "Time zone: the schedule is evaluated on the local wall clock of the IANA zone tz (use zoneinfo). "
             "Daylight-saving rules:\n"
             "- A local time that occurs twice (fall-back): if the hour field starts with '*', the job runs at both "
             "occurrences; otherwise only at the first.\n"
             "- Local times that do not exist (spring-forward gap): if the hour field starts with '*', they are skipped. "
             "Otherwise, if any of the day's matching times falls in the gap, the job runs once at the first local minute "
             "after the gap (e.g. 02:30 in a 02:00-03:00 gap runs at 03:00 local), in addition to its other runs; a run "
             "that coincides with another run counts once.\n"
             "Return the next n distinct run times strictly after start ('YYYY-MM-DDTHH:MMZ', UTC), in UTC, ascending, "
             "format 'YYYY-MM-DDTHH:MMZ'.\n" + _SCALE["cron"])
    example = tests[0]
    prompt = (f"Implement this function in Python 3:\n\n{desc}\n\nExample: next_runs({json.dumps(example[0][0])}, "
              f"{json.dumps(example[0][1])}, {json.dumps(example[0][2])}, 5) returns {json.dumps(example[1])}.\n"
              "Use only the standard library. Reply with a single ```python``` code block containing the complete function.")

    def check(text, _t=None, small=small, seed=seed) -> float:   # share of the cases passed, scale cases included
        return _run9("next_runs", _extract_code(text, "python"), small + _big("cron", level, seed, {"level": level}))
    return Item(f"{BLOCK}.cron.L{level}.{seed}", BLOCK, "cron", [{"role": "user", "content": prompt}], check, lang="python",
                meta={"fn": "next_runs", "lang": "python", "tests": len(tests) + 4, "level": level,
                      "expected": [t[1][0] for t in tests if t[1]], "params": {"level": level}, "_small": small,
                      "_seed": seed})


# ================================================================== scale: big inputs with a time limit per test

def _deep(f, *args):
    """f(*args) in a thread with a big stack and a high recursion limit (the recursive references on 1,500-deep input)."""
    import sys
    import threading
    res, old = {}, sys.getrecursionlimit()

    def run():
        try:
            res["v"] = f(*args)
        except BaseException as e:   # noqa: BLE001 - reported to the caller
            res["e"] = e
    sys.setrecursionlimit(1_000_000)
    threading.stack_size(256 * 1024 * 1024)
    try:
        th = threading.Thread(target=run)
        th.start()
        th.join()
    finally:
        threading.stack_size(0)
        sys.setrecursionlimit(old)
    if "e" in res:
        raise res["e"]
    return res["v"]


def _lru_fast(ops: list, cap: int, ttl: int, age: int, level: int) -> list:
    """_ref9_lru for ops without pins, touch or namespace quotas that bind: heaps with lazy entries for eviction (use
    count, last use) and expiry (deadline), rebuilt when aging halves every count."""
    import heapq
    store, out, clock, prev_t = {}, [], 0, 0      # key -> [value, count, clock start, ttl, last use]
    evh, exh = [], []

    def evict_one():
        while evh:
            f, used, k = heapq.heappop(evh)
            e = store.get(k)
            if e is not None and e[1] == f and e[4] == used:
                del store[k]
                return True
        return False
    for op in ops:
        kind = op[0]
        t = op[3] if kind == "put" else op[-1]
        halvings = t // age - prev_t // age
        if halvings > 0:
            for e in store.values():
                e[1] >>= halvings
            evh = [(e[1], e[4], k) for k, e in store.items()]
            heapq.heapify(evh)
        prev_t = t
        while exh and exh[0][0] <= t:
            dl, k, ts = heapq.heappop(exh)
            e = store.get(k)
            if e is not None and e[2] == ts and e[2] + e[3] == dl:
                del store[k]
        clock += 1
        if kind == "put":
            k, v, life = op[1], op[2], op[4] if len(op) > 4 else ttl
            if k in store:
                e = store[k]
                e[0], e[1], e[2], e[3], e[4] = v, e[1] + 1, t, life, clock
            else:
                while len(store) >= cap:
                    if not evict_one():
                        break
                if len(store) >= cap:
                    continue
                e = store[k] = [v, 1, t, life, clock]
            heapq.heappush(evh, (e[1], e[4], k))
            heapq.heappush(exh, (t + life, k, t))
        elif kind in ("get", "peek"):
            e = store.get(op[1])
            out.append(e[0] if e else -1)
            if e and kind == "get":
                e[1] += 1
                e[4] = clock
                heapq.heappush(evh, (e[1], e[4], op[1]))
        elif kind == "del":
            out.append(0 if store.pop(op[1], None) is None else 1)
        else:                                        # resize
            cap, n0 = op[1], len(store)
            while len(store) > cap and evict_one():
                pass
            out.append(n0 - len(store))
    return out


def _rooms_fast(rooms: list, meetings: list, maint: list, buf: int, big: int, level: int) -> list:
    """_ref9_rooms with sorted bookings per room: the bookings overlapping an interval are a contiguous run (bisect)."""
    from bisect import bisect_left, bisect_right
    n, nr = len(meetings), len(rooms)
    bend = [m[1] + (2 * buf if m[2] > big else buf) for m in meetings]
    starts, ends, idxs = [[] for _ in range(nr)], [[] for _ in range(nr)], [[] for _ in range(nr)]
    win = {k: [(a, b) for kk, a, b in maint if kk == k] for k in range(nr)}
    where = [-1] * n

    def clash(i, k):
        j, lo = bisect_left(starts[k], bend[i]), bisect_right(ends[k], meetings[i][0])
        return idxs[k][lo:j] if lo < j else []

    def usable(i, k):
        s, e = meetings[i][0], meetings[i][1]
        return rooms[k] >= meetings[i][2] and not any(s < b and a < e for a, b in win[k])

    def add(i, k):
        p = bisect_left(starts[k], meetings[i][0])
        starts[k].insert(p, meetings[i][0])
        ends[k].insert(p, bend[i])
        idxs[k].insert(p, i)
        where[i] = k

    def remove(j, k):
        p = bisect_left(starts[k], meetings[j][0])
        del starts[k][p], ends[k][p], idxs[k][p]
        where[j] = -1

    def place(i, may_bump):
        ok = [k for k in range(nr) if usable(i, k) and not clash(i, k)]
        if ok:
            add(i, min(ok, key=lambda k: (rooms[k], k)))
            return
        cands = []
        if may_bump:
            for k in range(nr):
                if usable(i, k):
                    c = clash(i, k)
                    if len(c) == 1 and meetings[c[0]][3] < meetings[i][3]:
                        cands.append((meetings[c[0]][3], rooms[k], k, c[0]))
        if not cands:
            where[i] = -1
            return
        _p, _c, k, j = min(cands)
        remove(j, k)
        add(i, k)
        place(j, level >= 10)

    for i in sorted(range(n), key=lambda i: (meetings[i][0], -meetings[i][3], i)):
        place(i, True)
    return where


_BIG_CACHE: dict = {}
_RARE9 = [("0 0 29 2 */7", "UTC", "2026-01-01T00:00Z"), ("0 12 31 2 *", "Europe/Madrid", "2026-01-01T00:00Z"),
          ("30 1 L 2 */7", "Europe/London", "2026-06-01T00:00Z"), ("0 0 1 1 */7", "America/New_York", "2026-01-01T00:00Z"),
          ("0 9 31 */2 */7", "Europe/Madrid", "2026-01-01T00:00Z")]
_RARE10 = [("15 3 1 1 */7 1", "UTC", "2026-01-01T00:00Z"), ("0 0 * * * 53", "UTC", "2027-06-01T00:00Z"),
           ("0 0 29 2 */7", "Asia/Tokyo", "2026-01-01T00:00Z"), ("0 6 29 2 * 9", "UTC", "2026-01-01T00:00Z"),
           ("0 12 1 1 */7 */2", "Europe/Madrid", "2026-01-01T00:00Z")]


def _big(kind: str, level: int, seed: int, p: dict) -> list:
    """[args, expected, time limit] for the scale tests of an item: built when the item is graded (item creation stays
    fast), deterministic, cached. Limit: 15x the reference's own time, at least 3 s."""
    import time
    key = (kind, level, seed)
    if key in _BIG_CACHE:
        return _BIG_CACHE[key]
    r = rng(BLOCK, f"{kind}{level}big", seed)
    jobs = []   # (args, reference call)
    if kind == "expr":
        env = {"x": r.randint(-5, 9), "y": r.randint(1, 6), "z": r.randint(-3, 3)}
        d = r.randint(1200, 1500)
        jobs.append(([("(" * d) + f"x + {r.randint(1, 9)}" + (")" * d), env], None))
        jobs.append(([("-" * r.randint(1200, 1500)) + "7 + (" + ("!" * r.randint(900, 1000)) + "0)", env], None))
        jobs.append(([" + ".join(str(r.randint(1, 9)) for _ in range(30000)) + " * y", env], None))
        jobs.append(([f"fn inc(v) = v + 1; let a0 = {r.randint(1, 9)}; "
                      + "; ".join(f"let a{i} = inc(a{i - 1}) * {r.choice([1, 1, 2])} % 1000" for i in range(1, 2500))
                      + "; a2499", env], None))
        m = r.randint(700, 900)
        jobs.append(([("max(" * m) + "1" + "".join(f", {r.randint(0, 3000)})" for _ in range(m)), env], None))
        jobs.append(([" < ".join(str(i) for i in range(20000)) + " < 1 / (y - y)", env], None))      # a long chain that errs
        jobs.append(([" && ".join(["1"] * 20000) + " || 1 / 0", env], None))
        d = r.randint(1200, 1500)
        jobs.append(([("x > 0 ? y + " * d) + "1" + (" : z" * d), env], None))
        call = lambda a: _deep(_ref9_expr, a[0], a[1], p["level"])
    elif kind == "lru":
        keys = [f"{'bulk:' if level >= 10 else ''}k{i}" for i in range(30000)]
        for slow, put_share, size in ((False, 0.55, 12000), (True, 0.55, 12000), (False, 0.8, 15000), (True, 0.35, 8000),
                                      (False, 0.45, 20000)):
            ops, t = [["resize", size, 1]], 1
            for j in range(60000):
                if slow and j % 200 == 0:
                    t += 1
                x = r.random()
                k = r.choice(keys)
                if x < put_share:
                    ops.append(["put", k, r.randint(1, 999), t] + ([1_000_000] if slow else []))
                elif x < put_share + (1 - put_share) * 0.9:
                    ops.append(["get", k, t])
                elif x < put_share + (1 - put_share) * 0.96:
                    ops.append(["peek", k, t])
                else:
                    ops.append(["del", k, t])
            jobs.append(([ops], None))
        call = lambda a: _lru_fast(a[0], p["cap"], p["ttl"], p["age"], p["level"])
    elif kind == "rooms":
        for n_r, n_m, span in ((25, 20000, 13000), (30, 20000, 9000), (12, 15000, 12000), (20, 18000, 20000)):
            rooms = [r.choice([4, 6, 8, 10, 12, 20]) for _ in range(n_r)]
            ms = []
            for _ in range(n_m):
                s_ = r.randint(0, span) * 15
                ms.append([s_, s_ + r.choice([15, 30, 45, 60, 90, 120]), r.randint(1, 22), r.randint(1, 3)])
            maint = ([[r.randrange(n_r), s_, s_ + 60] for s_ in (r.randint(0, span) * 15 for _ in range(10))]
                     if level >= 10 else [])
            jobs.append(([rooms, ms, maint] if level >= 10 else [rooms, ms], None))
        call = lambda a: _rooms_fast(a[0], a[1], a[2] if len(a) > 2 else [], p["buf"], p["big"], p["level"])
    elif kind == "cron":
        for e, tz, st in r.sample(_RARE9 if level == 9 else _RARE10, 4):
            jobs.append(([e, tz, st, 5], None))
        call = lambda a: _ref9_cron(*a)
    else:
        return []
    out = []
    for args, _ in jobs:
        t0 = time.time()
        exp = call(args)
        out.append([args, exp, round(max(3.0, 15 * (time.time() - t0)), 2)])
    _BIG_CACHE[key] = out
    return out


_H9 = """import json, signal, sys
sys.path.insert(0, '.')
from solution import {fn}


class _Late(BaseException):
    pass


def _alarm(*_a):
    raise _Late()


signal.signal(signal.SIGALRM, _alarm)
for args, exp, lim in json.load(open('tests.json')):
    ok = False
    try:
        signal.setitimer(signal.ITIMER_REAL, lim)
        got = {fn}(*args)
        signal.setitimer(signal.ITIMER_REAL, 0)
        ok = json.dumps(got, sort_keys=True) == json.dumps(exp, sort_keys=True)
    except BaseException:
        signal.setitimer(signal.ITIMER_REAL, 0)
    print('{nonce}' + ('P' if ok else 'F'), flush=True)
"""


def _run9(fn: str, code: str, cases: list) -> float:
    """Share of cases passed; each case [args, expected, seconds] runs under its own time limit, one mark per case, so a
    crash or a hang costs only the cases not yet reported."""
    import os
    import secrets
    import shutil
    import subprocess
    import tempfile
    from .code import _sandbox_cmd
    nonce = secrets.token_hex(8)
    d = os.path.realpath(tempfile.mkdtemp(prefix="llmbox-code9-"))
    try:
        json.dump(cases, open(os.path.join(d, "tests.json"), "w"))
        open(os.path.join(d, "solution.py"), "w").write(code)
        open(os.path.join(d, "harness.py"), "w").write(_H9.format(fn=fn, nonce=nonce))
        cmd = _sandbox_cmd(d, ["python3", "harness.py"])
        if cmd is None:
            raise RuntimeError("no sandbox available (sandbox-exec or bwrap) - refusing to run model code")
        try:
            out = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=sum(c[2] for c in cases) + 30).stdout
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return sum(line == nonce + "P" for line in out.splitlines()) / len(cases)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def cases_of(it: Item) -> list:
    """Every hidden case of a level 9-10 code item: [args, expected, seconds]."""
    return it.meta["_small"] + _big(it.kind, it.meta["level"], it.meta["_seed"], it.meta["params"])


_SCALE = {
    "expr": "Size: a program can be up to 200,000 characters long, with parentheses, prefix operators and calls nested up "
            "to 1,500 levels deep; each call must finish within 3 seconds.",
    "lru": "Size: up to 100,000 ops per call and up to 20,000 entries in the cache at once; each call must finish within 3 "
           "seconds.",
    "rooms": "Size: up to 20,000 meetings and 30 rooms per call; each call must finish within 3 seconds.",
    "cron": "Runs may be decades apart: search at most 400 years after start and return the runs found in that window "
            "(fewer than n, possibly none, if there are not enough); each call must finish within 3 seconds.",
}


# ================================================================== items and the oracle

_SPECS = {"expr": _spec9_expr, "lru": _spec9_lru, "csv": _spec9_csv, "rooms": _spec9_rooms}


def gen(kind: str, seed: int, level: int) -> Item:
    if kind == "cron":
        return _cron9(seed, level)
    r = rng(BLOCK, f"{kind}{level}", seed)
    fn, desc, tests, params = _SPECS[kind](r, level)
    if kind in _SCALE:
        desc += "\n" + _SCALE[kind]
    small = [[a, e, 10.0] for a, e in tests]
    example = next((t for t in tests if t[1] not in ("error", {}, [])), tests[0])
    call = f"{fn}({', '.join(json.dumps(a, ensure_ascii=False) for a in example[0])})"
    prompt = (f"Implement this function in Python 3:\n\n{desc}\n\nExample: {call} returns "
              f"{json.dumps(example[1], ensure_ascii=False)}.\nUse only the standard library. Reply with a single ```python``` "
              "code block containing the complete function.")

    def check(text, _t=None, fn=fn, small=small, seed=seed, params=params) -> float:   # share of the cases passed
        return _run9(fn, _extract_code(text, "python"), small + _big(kind, level, seed, params))
    n_big = {"expr": 8, "lru": 5, "rooms": 4}.get(kind, 0)
    return Item(f"{BLOCK}.{kind}.L{level}.{seed}", BLOCK, kind, [{"role": "user", "content": prompt}], check, lang="python",
                meta={"fn": fn, "lang": "python", "tests": len(tests) + n_big, "level": level, "params": params,
                      "_small": small, "_seed": seed})


def oracle(it: Item) -> str:
    """The reference implementation that computed the hidden tests, as a reply."""
    p = it.meta["params"]
    if it.kind == "cron":
        return f"```python\n{inspect.getsource(_ref9_cron)}\n\nnext_runs = _ref9_cron\n```"
    src = {"expr": [_ref9_expr, _deep], "lru": [_ref9_lru, _lru_fast], "csv": [_ref9_csv],
           "rooms": [_ref9_rooms, _rooms_fast]}[it.kind]
    call = {"expr": f"def evaluate(program, env):\n    return _deep(_ref9_expr, program, env, {p['level']})",
            "lru": f"def run_cache(ops):\n    if len(ops) > 5000:\n        return _lru_fast(ops, {p.get('cap')}, {p.get('ttl')}, "
                   f"{p.get('age')}, {p['level']})\n    return _ref9_lru(ops, {p.get('cap')}, {p.get('ttl')}, {p.get('age')}, "
                   f"{p.get('quotas')!r}, {p['level']})",
            "csv": f"def summarize(csv_text):\n    return _ref9_csv(csv_text, {p.get('month')!r}, {p['level']})",
            "rooms": f"def assign_rooms(rooms, meetings, maintenance=()):\n    f = _rooms_fast if len(meetings) > 1000 else "
                     f"_ref9_rooms\n    return f(rooms, meetings, list(maintenance), {p.get('buf')}, {p.get('big')}, "
                     f"{p['level']})"}[it.kind]
    return "```python\n" + "\n\n".join(inspect.getsource(f) for f in src) + f"\n\n{call}\n```"
