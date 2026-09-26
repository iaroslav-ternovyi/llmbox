"""Reference implementation of README.md (NOT shipped to the agent; used to validate the hidden tests)."""
import re
from decimal import Decimal, ROUND_HALF_UP

ERRORS = ("#DIV/0!", "#REF!", "#VALUE!", "#NAME?", "#CYCLE!")
CELL = re.compile(r"^([A-Z])([1-9][0-9]?)$")


class Err(Exception):
    def __init__(self, code):
        self.code = code


def is_err(v):
    return isinstance(v, str) and v in ERRORS and False  # plain strings are never errors inside the engine


class E:
    """error value wrapper inside evaluation"""
    def __init__(self, code):
        self.code = code


def norm(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(norm(v))
    return v


def num(v):
    if v is None:
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    raise Err("#VALUE!")


TOK = re.compile(r'\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)|("[^"]*")|([A-Za-z_][A-Za-z0-9_]*)|(<=|>=|<>|[-+*/^&=<>(),:]))')


def tokenize(src):
    pos, out = 0, []
    src = src.rstrip()
    while pos < len(src):
        m = TOK.match(src, pos)
        if not m or m.end() == pos:
            raise Err("#VALUE!")
        n, s, ident, op = m.groups()
        if n is not None:
            out.append(("num", float(n)))
        elif s is not None:
            out.append(("str", s[1:-1]))
        elif ident is not None:
            out.append(("id", ident.upper()))
        else:
            out.append(("op", op))
        pos = m.end()
    return out


class Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else ("eof", None)

    def take(self, kind=None, val=None):
        tk = self.peek()
        if tk[0] == "eof" or (kind and tk[0] != kind) or (val and tk[1] != val):
            raise Err("#VALUE!")
        self.i += 1
        return tk

    def parse(self):
        e = self.comparison()
        if self.peek()[0] != "eof":
            raise Err("#VALUE!")
        return e

    def comparison(self):
        e = self.concat()
        while self.peek()[0] == "op" and self.peek()[1] in ("=", "<>", "<", ">", "<=", ">="):
            op = self.take()[1]
            e = ("cmp", op, e, self.concat())
        return e

    def concat(self):
        e = self.additive()
        while self.peek() == ("op", "&"):
            self.take()
            e = ("cat", e, self.additive())
        return e

    def additive(self):
        e = self.term()
        while self.peek()[0] == "op" and self.peek()[1] in "+-":
            op = self.take()[1]
            e = ("bin", op, e, self.term())
        return e

    def term(self):
        e = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in "*/":
            op = self.take()[1]
            e = ("bin", op, e, self.unary())
        return e

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return ("neg", self.unary())
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.power()

    def power(self):
        base = self.primary()
        if self.peek() == ("op", "^"):
            self.take()
            return ("bin", "^", base, self.unary_pow())
        return base

    def unary_pow(self):  # exponent may carry a sign; right-associative
        if self.peek() == ("op", "-"):
            self.take()
            return ("neg", self.unary_pow())
        return self.power()

    def primary(self):
        tk = self.take()
        if tk[0] == "num":
            return ("num", tk[1])
        if tk[0] == "str":
            return ("str", tk[1])
        if tk[0] == "op" and tk[1] == "(":
            e = self.comparison()
            self.take("op", ")")
            return e
        if tk[0] == "id":
            name = tk[1]
            if self.peek() == ("op", "("):
                self.take()
                args = []
                if self.peek() != ("op", ")"):
                    while True:
                        args.append(self.arg())
                        if self.peek() == ("op", ","):
                            self.take()
                            continue
                        break
                self.take("op", ")")
                return ("fn", name, args)
            if name in ("TRUE", "FALSE"):
                return ("bool", name == "TRUE")
            return ("ref", name)
        raise Err("#VALUE!")

    def arg(self):
        if self.peek()[0] == "id" and self.i + 1 < len(self.t) and self.t[self.i + 1] == ("op", ":"):
            a = self.take()[1]
            self.take()
            b = self.take("id")[1]
            return ("range", a, b)
        return self.comparison()


def refs_of(node, out):
    if node[0] == "ref":
        out.add(node[1])
    elif node[0] == "range":
        for c in expand(node[1], node[2]) or []:
            out.add(c)
    elif node[0] in ("bin", "cmp"):
        refs_of(node[2], out); refs_of(node[3], out)
    elif node[0] == "cat":
        refs_of(node[1], out); refs_of(node[2], out)
    elif node[0] == "neg":
        refs_of(node[1], out)
    elif node[0] == "fn":
        for a in node[2]:
            refs_of(a, out)
    return out


def expand(a, b):
    ma, mb = CELL.match(a), CELL.match(b)
    if not ma or not mb:
        return None
    c1, c2 = sorted([ma.group(1), mb.group(1)])
    r1, r2 = sorted([int(ma.group(2)), int(mb.group(2))])
    return [f"{chr(c)}{r}" for r in range(r1, r2 + 1) for c in range(ord(c1), ord(c2) + 1)]


class Sheet:
    def __init__(self):
        self.raw = {}

    def set(self, cell, raw):
        cell = cell.upper()
        if raw == "":
            self.raw.pop(cell, None)
        else:
            self.raw[cell] = raw

    def _ast(self, cell):
        raw = self.raw.get(cell)
        if raw is None or not raw.startswith("="):
            return None
        try:
            return Parser(tokenize(raw[1:])).parse()
        except Err:
            return ("bad",)

    def _cycle_cells(self):
        graph = {}
        for c in self.raw:
            ast = self._ast(c)
            graph[c] = refs_of(ast, set()) if ast and ast[0] != "bad" else set()
        index, low, onstack, stack, sccs, counter = {}, {}, set(), [], [], [0]

        def strong(v):
            index[v] = low[v] = counter[0]; counter[0] += 1
            stack.append(v); onstack.add(v)
            for w in graph.get(v, ()):
                if w not in graph:
                    continue
                if w not in index:
                    strong(w); low[v] = min(low[v], low[w])
                elif w in onstack:
                    low[v] = min(low[v], index[w])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop(); onstack.discard(w); comp.append(w)
                    if w == v:
                        break
                sccs.append(comp)
        for v in graph:
            if v not in index:
                strong(v)
        cyc = set()
        for comp in sccs:
            if len(comp) > 1 or comp[0] in graph.get(comp[0], ()):
                cyc.update(comp)
        return cyc

    def get(self, cell):
        cell = cell.upper()
        self._cyc = self._cycle_cells()
        self._memo = {}
        v = self._value(cell)
        return v.code if isinstance(v, E) else norm(v)

    def _value(self, cell):
        if not CELL.match(cell):
            return E("#REF!")
        if cell in self._memo:
            return self._memo[cell]
        if cell in self._cyc:
            return E("#CYCLE!")
        raw = self.raw.get(cell)
        if raw is None:
            v = None
        elif not raw.startswith("="):
            try:
                v = float(raw)
                v = int(v) if v.is_integer() and "e" not in raw.lower() and "." not in raw else v
            except ValueError:
                v = raw
        else:
            ast = self._ast(cell)
            v = E("#VALUE!") if ast[0] == "bad" else self._eval(ast)
        self._memo[cell] = v
        return v

    def _eval(self, n):
        k = n[0]
        if k == "num":
            return n[1]
        if k == "str":
            return n[1]
        if k == "bool":
            return n[1]
        if k == "ref":
            return self._value(n[1])
        if k == "range":
            return E("#VALUE!")
        if k == "neg":
            v = self._eval(n[1])
            if isinstance(v, E):
                return v
            try:
                return -num(v)
            except Err as e:
                return E(e.code)
        if k == "bin":
            a = self._eval(n[2])
            if isinstance(a, E):
                return a
            b = self._eval(n[3])
            if isinstance(b, E):
                return b
            try:
                x, y = num(a), num(b)
            except Err as e:
                return E(e.code)
            op = n[1]
            if op == "+": return x + y
            if op == "-": return x - y
            if op == "*": return x * y
            if op == "/":
                return E("#DIV/0!") if y == 0 else x / y
            try:
                r = float(x) ** float(y)
            except (ZeroDivisionError, OverflowError):
                return E("#DIV/0!")
            return E("#VALUE!") if isinstance(r, complex) else r
        if k == "cat":
            a = self._eval(n[1])
            if isinstance(a, E):
                return a
            b = self._eval(n[2])
            if isinstance(b, E):
                return b
            return text(a) + text(b)
        if k == "cmp":
            a = self._eval(n[2])
            if isinstance(a, E):
                return a
            b = self._eval(n[3])
            if isinstance(b, E):
                return b
            if isinstance(a, str) and isinstance(b, str):
                x, y = a, b
            else:
                try:
                    x, y = num(a), num(b)
                except Err as e:
                    return E(e.code)
            op = n[1]
            return {"=": x == y, "<>": x != y, "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]
        if k == "fn":
            return self._fn(n[1], n[2])
        return E("#VALUE!")

    def _values(self, args):
        """flatten arguments; ranges -> (value, from_range=True)"""
        out = []
        for a in args:
            if a[0] == "range":
                cells = expand(a[1], a[2])
                if cells is None:
                    return E("#REF!")
                for c in cells:
                    out.append((self._value(c), True))
            else:
                out.append((self._eval(a), False))
        return out

    def _fn(self, name, args):
        if name == "IF":
            if len(args) != 3:
                return E("#VALUE!")
            c = self._eval(args[0])
            if isinstance(c, E):
                return c
            if isinstance(c, str):
                return E("#VALUE!")
            return self._eval(args[1] if num(c) else args[2])
        if name not in ("SUM", "MIN", "MAX", "AVERAGE", "COUNT", "ROUND", "LEN", "CONCAT"):
            return E("#NAME?")
        vals = self._values(args)
        if isinstance(vals, E):
            return vals
        for v, _ in vals:
            if isinstance(v, E):
                return v
        if name in ("SUM", "MIN", "MAX", "AVERAGE"):
            nums = []
            for v, from_range in vals:
                if isinstance(v, bool):
                    if not from_range:
                        nums.append(1 if v else 0)
                elif isinstance(v, (int, float)):
                    nums.append(v)
                elif v is None or from_range:
                    continue
                else:
                    return E("#VALUE!")
            if name == "SUM": return sum(nums)
            if name == "MIN": return min(nums) if nums else 0
            if name == "MAX": return max(nums) if nums else 0
            return E("#DIV/0!") if not nums else sum(nums) / len(nums)
        if name == "COUNT":
            return sum(1 for v, _ in vals if isinstance(v, (int, float)) and not isinstance(v, bool))
        if name == "ROUND":
            if len(vals) != 2:
                return E("#VALUE!")
            try:
                x, d = num(vals[0][0]), int(num(vals[1][0]))
            except Err as e:
                return E(e.code)
            q = Decimal(1).scaleb(-d)
            return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))
        if name == "LEN":
            return len(text(vals[0][0])) if vals else E("#VALUE!")
        return "".join(text(v) for v, _ in vals)
