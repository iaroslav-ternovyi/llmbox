"""Knowledge and "I don't know": the things people who run local models look up - what a Python call returns, what a
shell pipeline prints, what a jq filter gives, which signal an exit status 137 means, which port a service listens on -
mixed with questions about things that do not exist (a stdlib function, a grep option, a jq builtin, an errno, an HTTP
status, a product). Advice and information seeking is the largest use of assistants (docs/usage-research.md), and for
a local model the costly failure is not ignorance but a confident wrong answer the user then acts on.

No judge and nothing written from memory: every answer in knowledge_bank.json was produced by running the real thing
when the bank was built (`python3 -m llmbox.suite.knowledge build`): CPython 3.12 and 3.14 (kept only when both agree),
bash 5.3 with GNU coreutils/grep/sed/gawk, jq 1.7.1, the Linux errno/signal tables and the exit statuses of real
commands. Things that do not exist are checked to really fail. Tool default ports that no system file records are the
one exception (PORTS_DOCS: documented defaults), and made-up products have no answer at all.

An item is 8 short questions of one kind and level; 2 of them ask about something that does not exist (1-3 before dev6); the prompt never says how many, so the
format gives nothing away. Per question: right 1, UNKNOWN 1/3 (honest, but no help), wrong or invented 0. A model that
answers UNKNOWN to everything scores 1/3; one that guesses at 25% and invents the rest scores less.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
import re
import subprocess
import sys

from .common import Item, rng, strip_think

BLOCK = "knowledge"
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "knowledge_src.txt")
BANK = os.path.join(HERE, "knowledge_bank.json")
PER_ITEM = 8
IDK = 1 / 3
HOST = "user@gpu-box"

RULES = ("I'll act on your answers, so don't guess. If you are not sure, answer UNKNOWN. If the function, option, command, "
         "code or product asked about does not exist, answer NONEXISTENT.")
HEAD = {
    "python": ("Quick Python questions. Each snippet runs on CPython 3.12 or newer, with the standard-library modules it uses "
               "already imported. What does its last line evaluate to? Answer with the value's repr as the Python REPL "
               "shows it, or `raises <ExceptionName>` if it raises."),
    "shell": ("Quick shell questions. Each command runs with `bash -c` on Linux: bash 5, GNU coreutils, grep, sed and "
              "findutils, gawk as `awk`, jq 1.7; locale C.UTF-8; the current directory is empty. What does it print to "
              "standard output? Every command that works prints exactly one line."),
    "codes": "Quick questions about codes and defaults you meet when running services on Linux (x86-64).",
}
TAIL = "\n\nFinish with your answers, one line per question, exactly in this form:\nANSWERS\n1. <answer>\n2. <answer>\n..."


# ---- the bank ----------------------------------------------------------------------------------------------------------

_bank: dict | None = None


def bank() -> dict:
    global _bank
    if _bank is None:
        _bank = json.load(open(BANK))
    return _bank


def _pool(kind: str, level: int, fake: bool) -> list[dict]:
    qs = bank()["questions"]
    if fake:   # few fakes per level: the neighbours' fakes are just as plausible
        d, pool = 1, []
        while len(pool) < 4 and d < 5:
            pool = [q for q in qs if q["kind"] == kind and q["fake"] and abs(q["level"] - level) <= d]
            d += 1
        return pool
    pool = [q for q in qs if q["kind"] == kind and not q["fake"] and q["level"] == level]
    d = 1
    while len(pool) < PER_ITEM and d < 5:   # a thin level borrows from its neighbours
        pool += [q for q in qs if q["kind"] == kind and not q["fake"] and abs(q["level"] - level) == d]
        d += 1
    return pool


def _show(q: dict) -> str:
    t = q["text"]
    if q["kind"] == "codes":
        return t
    if "\n" in t:
        return f"\n```{'python' if q['kind'] == 'python' else 'bash'}\n{t}\n```"
    return f"`{t}`"


def _gen(kind: str):
    def gen(seed: int, level: int = 3) -> Item:
        r = rng(BLOCK, f"{kind}{level}", seed)
        n_fake = 2   # dev6: fixed (1-3 at random before: the share of made-up questions changed the difficulty by seed)
        qs = r.sample(_pool(kind, level, False), PER_ITEM - n_fake) + r.sample(_pool(kind, level, True), n_fake)
        r.shuffle(qs)
        body = "\n".join(f"{i}. {_show(q)}" for i, q in enumerate(qs, 1))
        prompt = f"{HEAD[kind]} {RULES}\n\n{body}{TAIL}"

        def check(text: str, _t=None, qs=qs) -> float:
            got = answers(text, len(qs))
            return sum(credit(q, got.get(i)) for i, q in enumerate(qs, 1)) / len(qs)
        return Item(f"{BLOCK}.{kind}.L{level}.{seed}", BLOCK, kind, [{"role": "user", "content": prompt}], check,
                    max_tokens=32000, meta={"level": level, "questions": qs, "expected": [oracle_answer(q) for q in qs]})
    return gen


# ---- grading -----------------------------------------------------------------------------------------------------------

_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?\s*(\d{1,2})\s*[.):]\s*(?:\*\*)?\s*(.*?)\s*$")


def answers(text: str, n: int) -> dict[int, str]:
    """Numbered answer lines after the last ANSWERS header (or anywhere, the last line per number wins)."""
    t = strip_think(text or "")
    heads = list(re.finditer(r"(?im)^[#*\s]*answers\W*$", t))
    if heads:
        t = t[heads[-1].end():]
    out: dict[int, str] = {}
    for line in t.splitlines():
        m = _LINE.match(line)
        if m and 1 <= int(m.group(1)) <= n:
            out[int(m.group(1))] = m.group(2)
    return out


def _unwrap(a: str) -> str:
    a = a.strip()
    for _ in range(3):
        a = re.sub(r"^\*\*(.*)\*\*$", r"\1", a).strip()
        m = re.fullmatch(r"(`+)(.*?)\1", a, re.S)
        if m:
            a = m.group(2).strip()
    return a


def _cands(a: str) -> list[str]:
    """The answer and its likely trimmed forms: an explanation after a dash, a trailing period."""
    a = _unwrap(a)
    out = [a, a.rstrip(".")]
    head = re.split(r"\s+(?:—|–|--)\s+|\s+\((?:because|since|as|i\.e\.|the|it|note)\b", a)[0]
    out += [_unwrap(head), _unwrap(head).rstrip(".")]
    return list(dict.fromkeys(x for x in out if x))


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _canon(v):
    """Type-strict structural form: 1, 1.0 and True differ; dict and set order do not matter."""
    if isinstance(v, dict):
        return ("dict", sorted((repr(_canon(k)), repr(_canon(x))) for k, x in v.items()))
    if isinstance(v, (set, frozenset)):
        return (type(v).__name__, sorted(repr(_canon(x)) for x in v))
    if isinstance(v, (list, tuple)):
        return (type(v).__name__, [_canon(x) for x in v])
    return (type(v).__name__, v)


def _exc_name(a: str) -> str | None:
    m = re.search(r"\braise[sd]?\s+(?:an?\s+)?`?([\w.]+)", a, re.I) or \
        re.fullmatch(r"`?([\w.]*(?:Error|Exception|Exit|Interrupt|Warning))`?(?:\s*[:(].*)?", a.strip(), re.S)
    return m.group(1).split(".")[-1] if m else None


def _ints(a: str) -> list[int]:
    return [int(x) for x in re.findall(r"(?<![\w.-])-?\d+(?![\w.])", a)]


def _match(q: dict, a: str) -> bool:
    acc, mode = q["accept"], q["mode"]
    for c in _cands(a):
        if mode == "py":
            if acc.get("exc"):
                if _exc_name(c) in acc["exc"]:
                    return True
                continue
            if _exc_name(c):
                continue
            if acc.get("canon") is not None:
                try:
                    if repr(_canon(ast.literal_eval(c))) == acc["canon"]:
                        return True
                except Exception:
                    pass
            if _ws(c) in (_ws(acc["repr"]), _ws(acc.get("str") or "\0")):
                return True
            if len(c) >= 2 and c[0] == c[-1] and c[0] in "'\"" and _ws(c[1:-1]) == _ws(acc.get("str") or "\0") and acc.get("type") == "str":
                return True
        elif mode == "text":
            # the output written with escapes ("1\tx") or a description after it ("... (five spaces, a TAB)") is still it
            c0 = re.sub(r"\s*\([^()]*\)\s*$", "", c).strip()
            for x in (c, c0):
                x2 = x[1:-1] if len(x) >= 2 and x[0] == x[-1] and x[0] in "'\"" else x
                for y in (x, x2, x2.replace("\\t", "\t").replace("\\n", "\n")):
                    if _ws(y) in acc["texts"]:
                        return True
        elif mode == "int":
            ns = _ints(c)
            if ns and ns[0] == acc["int"] and not (len(set(ns)) > 1 and re.search(r"\bor\b", c)):
                return True
        elif mode == "name":
            toks = re.findall(r"\b(?:SIG[A-Z0-9]+|E[A-Z0-9]{2,})\b", c)
            if toks:
                if all(t in acc["names"] for t in toks):
                    return True
            elif c.strip().upper() in acc["names"] or (acc.get("int") is not None and _ints(c)[:1] == [acc["int"]]):
                return True
        elif mode == "phrase":
            norm = re.sub(r"[^a-z]", "", re.sub(r"^\s*\d{3}\s*", "", c.lower()))
            if norm in acc["phrases"]:
                return True
    return False


_ERRWORDS = re.compile(r"error|unrecognized|unrecogni[sz]ed|invalid option|unknown option|illegal option|not found|"
                       r"not defined|no such|does not exist|doesn't exist", re.I)


def verdict(q: dict, a: str | None) -> str:
    """right / idk / wrong for one question."""
    if a is None or not a.strip():
        return "wrong"
    up = _unwrap(a).upper()
    if re.match(r"[\s`'\"*_]*(UNKNOWN|I DON'?T KNOW|DON'?T KNOW|NOT SURE)\b", up):
        return "idk"
    says_none = bool(re.match(r"[\s`'\"*_]*(NONEXISTENT|NON-EXISTENT|DOES NOT EXIST|DOESN'T EXIST|NO SUCH)\b", up))   # not '*.nonexistent'
    if q["fake"]:
        if says_none:
            return "right"
        if q["kind"] == "python" and _exc_name(_unwrap(a)) in q["accept"].get("exc", []):
            return "right"
        if q["kind"] == "shell" and _ERRWORDS.search(a):
            return "right"
        return "wrong"
    if says_none:
        return "wrong"
    return "right" if _match(q, a) else "wrong"


def credit(q: dict, a: str | None) -> float:
    v = verdict(q, a)
    return 1.0 if v == "right" else q.get("idk", IDK) if v == "idk" else 0.0


def breakdown(it: Item, text: str) -> dict:
    """How the item was answered: known facts right / UNKNOWN / wrong, and made-up things refused / invented."""
    qs = it.meta["questions"]
    got = answers(text, len(qs))
    out = {"right": 0, "idk": 0, "wrong": 0, "refused": 0, "invented": 0}
    for i, q in enumerate(qs, 1):
        v = verdict(q, got.get(i))
        if q["fake"]:
            out["refused" if v != "wrong" else "invented"] += 1
        else:
            out[v] += 1
    return out


def oracle_answer(q: dict) -> str:
    if q["fake"]:
        return "NONEXISTENT"
    acc = q["accept"]
    if q["mode"] == "py":
        return f"raises {acc['exc'][0]}" if acc.get("exc") else acc["repr"]
    if q["mode"] == "text":
        return acc["texts"][0]
    if q["mode"] == "int":
        return str(acc["int"])
    if q["mode"] == "name":
        return acc["names"][0]
    return acc["show"]


def oracle(it: Item) -> str:
    return "ANSWERS\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(it.meta["expected"], 1))


KINDS = {"python": _gen("python"), "shell": _gen("shell"), "codes": _gen("codes")}
MAX_LEVEL = 6   # 6 = expert: implementation-specific behaviour even frontier models get wrong (headroom above them)
QUICK = list(KINDS)


# ---- building the bank: run every question on the real thing ----------------------------------------------------------

PY_MODULES = ["argparse", "base64", "bisect", "calendar", "collections", "contextlib", "copy", "csv", "dataclasses",
              "datetime", "decimal", "email.utils", "enum", "fnmatch", "fractions", "functools", "glob", "graphlib",
              "hashlib", "heapq", "html", "ipaddress", "itertools", "json", "logging", "math", "operator", "os",
              "pathlib", "random", "re", "secrets", "shlex", "shutil", "statistics", "string", "struct", "subprocess",
              "sys", "textwrap", "time", "tomllib", "typing", "unicodedata", "urllib.parse", "uuid", "zlib", "zoneinfo"]

_PY_RUNNER = r'''
import ast, contextlib, importlib, io, json, signal, sys, warnings
warnings.simplefilter("ignore")
mods, codes = json.load(sys.stdin)
base = {}
for m in mods:
    importlib.import_module(m)
    base[m.split(".")[0]] = importlib.import_module(m.split(".")[0])
import enum
def plain(v):   # the same value without enum wrappers: (calendar.THURSDAY, 29) == (3, 29)
    if isinstance(v, enum.Enum) and isinstance(v, (int, str)):
        return int(v) if isinstance(v, int) else str(v.value)
    if isinstance(v, (list, tuple)):
        return type(v)(plain(x) for x in v)
    return v
def run(code):
    ns = dict(base)
    signal.alarm(5)
    try:
        tree = ast.parse(code)
        last = tree.body[-1]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if isinstance(last, ast.Expr):
                exec(compile(ast.Module(tree.body[:-1], []), "<q>", "exec"), ns)
                v = eval(compile(ast.Expression(last.value), "<q>", "eval"), ns)
                return {"repr": repr(v), "str": str(v), "type": type(v).__name__, "plain": repr(plain(v))}
            exec(compile(tree, "<q>", "exec"), ns)
            return {"noexpr": True}
    except BaseException as e:
        return {"exc": [c.__name__ for c in type(e).__mro__ if c.__name__ not in ("object", "BaseException", "Exception")],
                "msg": str(e)[:200]}
    finally:
        signal.alarm(0)
print(json.dumps([run(c) for c in codes]))
'''

_SH_RUNNER = r'''
import json, os, subprocess, sys, tempfile
cmds, gnu_names = json.load(sys.stdin)
home = tempfile.mkdtemp()
env = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8", HOME=home)
if gnu_names:   # the box has uutils coreutils as the default; the GNU ones are installed as gnu<name>
    gnu = tempfile.mkdtemp()
    for n in gnu_names:
        if os.path.exists("/usr/bin/gnu" + n):
            os.symlink("/usr/bin/gnu" + n, os.path.join(gnu, n))
    env["PATH"] = gnu + ":" + env["PATH"]
out = []
for c in cmds:
    runs = []
    for _ in range(2):
        d = tempfile.mkdtemp()
        try:
            p = subprocess.run(["bash", "--norc", "--noprofile", "-c", c], cwd=d, env=env, capture_output=True, timeout=20)
            runs.append({"out": p.stdout.decode("utf-8", "replace"), "err": p.stderr.decode("utf-8", "replace")[:600], "rc": p.returncode})
        except subprocess.TimeoutExpired:
            runs.append({"timeout": True})
    out.append(runs)
print(json.dumps(out))
'''

GNU_NAMES = ("[ arch b2sum base32 base64 basename basenc cat chcon chgrp chmod chown cksum comm cp csplit cut date dd df dir "
             "dircolors dirname du echo env expand expr factor false fmt fold groups head hostid id install join link ln "
             "logname ls md5sum mkdir mkfifo mknod mktemp mv nice nl nohup nproc numfmt od paste pathchk pinky pr "
             "printenv printf ptx pwd readlink realpath rm rmdir runcon seq sha1sum sha224sum sha256sum sha384sum "
             "sha512sum shred shuf sleep sort split stat stdbuf stty sum sync tac tail tee test timeout touch tr true "
             "truncate tsort tty uname unexpand uniq unlink users vdir wc who whoami yes").split()


def _remote(script: str, payload, host: str | None, python: str = "python3", timeout: int = 900):
    cmd = ["ssh", "-o", "ConnectTimeout=10", host, f"{python} -c {_shq(script)}"] if host else [python, "-c", script]
    p = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"{'ssh ' + host if host else python}: {p.stderr[-800:]}")
    return json.loads(p.stdout)


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def parse_src(path: str = SRC) -> list[dict]:
    out, sec = [], None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        m = re.match(r"^## (python|shell|jq)( fake)? (\d)$", line)
        if m:
            sec = (m.group(1), bool(m.group(2)), int(m.group(3)))
            continue
        if not line.strip() or line.startswith("#") or sec is None:
            continue
        src, fake, level = sec
        out.append({"src": src, "fake": fake, "level": level, "text": line.replace("⏎", "\n")})
    return out


_FAKE_TYPEERR = re.compile(r"keyword|argument", re.I)


def _build_python(entries: list[dict], host: str, log) -> list[dict]:
    codes = [e["text"] for e in entries]
    runs = {"3.12 seed 0": _remote(_PY_RUNNER, [PY_MODULES, codes], None, "python3"),
            "box": _remote(_PY_RUNNER, [PY_MODULES, codes], host)}
    os.environ["PYTHONHASHSEED"] = "1"
    try:
        runs["3.12 seed 1"] = _remote(_PY_RUNNER, [PY_MODULES, codes], None, "python3")
    finally:
        del os.environ["PYTHONHASHSEED"]
    out = []
    for i, e in enumerate(entries):
        rs = [runs[k][i] for k in runs]
        a = rs[0]
        why = None
        if e["fake"]:
            ok = all(r.get("exc") and (r["exc"][0] in ("AttributeError", "NameError")
                                       or (r["exc"][0] == "TypeError" and _FAKE_TYPEERR.search(r["msg"]))) for r in rs)
            why = None if ok else f"does not fail as a missing name/option: {[r.get('exc', r.get('repr')) for r in rs]}"
            acc = {"exc": sorted({n for r in rs for n in r.get("exc", [])})}
        else:
            if any(r.get("noexpr") for r in rs):
                why = "the last line is not an expression and nothing raised"
            elif any(r.get("exc") for r in rs):
                if not all(r.get("exc") and r["exc"][0] == a.get("exc", [None])[0] for r in rs):
                    why = f"versions disagree: {[r.get('exc', r.get('repr')) for r in rs]}"
                elif a["exc"][0] == "AttributeError" or (a["exc"][0] == "NameError" and "\n" not in e["text"]):
                    # a one-liner raising these is a typo; a multi-line snippet may raise NameError on purpose (scoping)
                    why = "raises AttributeError/NameError: a real question must use things that exist"
                acc = {"exc": a.get("exc")}
            elif len({r["repr"] for r in rs}) > 1:
                why = f"versions or hash seeds disagree: {[r['repr'] for r in rs]}"
            elif len(a["repr"]) > 80:
                why = f"answer too long ({len(a['repr'])} chars)"
            else:
                canon = None
                for form in (a["repr"], a["plain"]):   # the repr, or failing that the value without enum wrappers
                    try:
                        canon = repr(_canon(ast.literal_eval(form)))
                        break
                    except Exception:
                        pass
                acc = {"repr": a["repr"], "str": a["str"], "type": a["type"], "canon": canon}
        if why:
            log(f"  drop python L{e['level']} {'fake ' if e['fake'] else ''}{e['text']!r}: {why}")
            continue
        out.append({"kind": "python", "src": "python", "level": e["level"], "fake": e["fake"], "text": e["text"],
                    "mode": "py", "accept": acc})
    return out


def _build_shell(entries: list[dict], host: str, log) -> list[dict]:
    box = [e for e in entries if e["src"] == "shell"]
    loc = [e for e in entries if e["src"] == "jq"]   # jq is not on the box; jq 1.7.1 here
    res = {}
    if box:
        for e, r in zip(box, _remote(_SH_RUNNER, [[e["text"] for e in box], GNU_NAMES], host)):
            res[id(e)] = r
    if loc:
        for e, r in zip(loc, _remote(_SH_RUNNER, [[e["text"] for e in loc], []], None)):
            res[id(e)] = r
    out = []
    for e in box + loc:
        rs = res[id(e)]
        why = None
        if any(r.get("timeout") for r in rs):
            why = "timed out"
        elif e["fake"]:
            ok = all(r["rc"] != 0 and not r["out"].strip() and re.search(r"unrecognized|invalid|unknown|not found|not defined|illegal|error", r["err"], re.I) for r in rs)
            why = None if ok else f"does not fail: rc={rs[0]['rc']} out={rs[0]['out']!r} err={rs[0]['err'][-120:]!r}"
        else:
            o = rs[0]["out"]
            if rs[0]["out"] != rs[1]["out"]:
                why = f"not deterministic: {rs[0]['out']!r} vs {rs[1]['out']!r}"
            elif rs[0]["rc"] != 0:
                why = f"exit {rs[0]['rc']}: {rs[0]['err'][-120:]!r}"
            elif o.count("\n") != 1 or not o.endswith("\n") or not o.strip():
                why = f"not exactly one line: {o!r}"
            elif len(o) > 80:
                why = "output too long"
        if why:
            log(f"  drop {e['src']} L{e['level']} {'fake ' if e['fake'] else ''}{e['text']!r}: {why}")
            continue
        acc = {} if e["fake"] else {"texts": [_ws(rs[0]["out"])]}
        out.append({"kind": "shell", "src": e["src"], "level": e["level"], "fake": e["fake"], "text": e["text"],
                    "mode": "text", "accept": acc})
    return out


# ---- codes: HTTP statuses, signals, errno, exit statuses, ports --------------------------------------------------------

HTTP_REAL = {1: [200, 404, 500], 2: [201, 301, 302, 400, 401, 403, 502, 503], 3: [204, 304, 307, 308, 405, 409, 422, 429, 504],
             4: [100, 101, 202, 206, 406, 410, 412, 413, 415, 418, 451], 5: [103, 207, 208, 226, 417, 421, 423, 424, 425, 426, 428,
                                                                            431, 505, 506, 507, 508, 510, 511]}
# unassigned by IANA and not a well-known vendor code (419 Laravel, 420 Twitter, 430 Shopify, 440/449/450 IIS, 444/494-499
# nginx, 460/463 AWS, 509 cPanel, 520-530 Cloudflare, 598/599 proxies are all avoided)
HTTP_FAKE = {3: [437, 438, 446], 4: [447, 454, 456, 457, 461], 5: [468, 471, 476, 512, 513, 515, 517, 533, 309, 312]}
HTTP_ALIASES = {413: ["Payload Too Large", "Request Entity Too Large"], 414: ["Request-URI Too Long"],
                416: ["Requested Range Not Satisfiable"], 422: ["Unprocessable Entity", "Unprocessable Content"],
                418: ["I'm a teapot", "I am a teapot"]}
SIG_REAL = {1: ["SIGKILL", "SIGINT", "SIGTERM"], 2: ["SIGHUP", "SIGSEGV"], 3: ["SIGQUIT", "SIGABRT", "SIGPIPE", "SIGALRM", "SIGUSR1", "SIGSTOP"],
            4: ["SIGUSR2", "SIGCHLD", "SIGCONT", "SIGTSTP", "SIGBUS", "SIGFPE"],
            5: ["SIGWINCH", "SIGXCPU", "SIGSYS", "SIGTTIN", "SIGURG", "SIGIO", "SIGPWR", "SIGVTALRM", "SIGPROF", "SIGXFSZ", "SIGTRAP", "SIGILL"]}
SIG_FAKE = {2: ["SIGRELOAD", "SIGSHUTDOWN"], 3: ["SIGRESTART", "SIGTIMEOUT"], 4: ["SIGOOM", "SIGDRAIN"], 5: ["SIGMEM", "SIGNET", "SIGDISK"]}
EXIT_SIG = {2: [137, 130], 3: [143, 139], 4: [134, 141, 129], 5: [135, 136, 159, 152]}
ERRNO_NUM = {2: ["ENOENT", "EACCES", "EPERM"], 3: ["EEXIST", "EINVAL", "ENOSPC", "EAGAIN", "EINTR", "EBADF", "ENOMEM", "EPIPE"],
             4: ["ECONNREFUSED", "ETIMEDOUT", "EADDRINUSE", "ECONNRESET", "EMFILE", "ENOTDIR", "EISDIR", "EROFS", "EXDEV"],
             5: ["ENOTEMPTY", "ENOSYS", "ELOOP", "ENAMETOOLONG", "EHOSTUNREACH", "ENETUNREACH", "ETXTBSY", "ENOEXEC", "EDQUOT",
                 "ENFILE", "ESPIPE", "EMLINK", "ENOTSOCK", "EADDRNOTAVAIL", "ECHILD", "E2BIG"]}
ERRNO_FAKE = {3: ["ENOTALLOWED", "ENOCONN"], 4: ["ETOOBIG", "ENOTREADY", "EFILELOCKED", "ECONNTIMEOUT"],
              5: ["ENOPERM", "EDISKFULL", "EOUTOFMEM", "ENOPORT", "EBADPATH"]}
ERRNO_MSG = {2: ["ENOENT", "EACCES"], 3: ["EEXIST", "ENOSPC", "ECONNREFUSED", "EMFILE", "EPIPE"],
             4: ["EADDRINUSE", "ECONNRESET", "EROFS", "EPERM", "ENOTEMPTY", "EAGAIN", "ETIMEDOUT"],
             5: ["EXDEV", "ETXTBSY", "ENOEXEC", "ELOOP", "EDQUOT", "ENOSYS", "EHOSTUNREACH", "EADDRNOTAVAIL", "E2BIG", "ENOTTY",
                 "ESTALE", "ENOTCONN", "EBADF", "EINTR"]}
# (level, question, command whose exit status is the answer; run on the box with GNU coreutils first in PATH)
EXITS = [
    (1, "What exit status means success for a Unix command?", "true"),
    (2, "What exit status does bash return when a command is not found?", "bash -c 'nonexistent_cmd_xyz' 2>/dev/null"),
    (2, "What exit status does `grep` return when no line matches?", "echo a | grep -q b"),
    (2, "What exit status does a Python script have when it ends with an uncaught exception?", "python3 -c 'raise ValueError' 2>/dev/null"),
    (2, "What exit status does `[ -f missing.txt ]` return when the file does not exist?", "[ -f missing.txt ]"),
    (3, "What exit status does bash return when the command's file exists but is not executable?",
        "f=$(mktemp); bash -c \"$f\" 2>/dev/null; r=$?; rm -f \"$f\"; exit $r"),
    (3, "What exit status does `grep` return when the file it should read does not exist?", "grep x /nonexistent 2>/dev/null"),
    (3, "What exit status does `diff` return when the two files differ?", "diff <(echo a) <(echo b) >/dev/null"),
    (3, "What exit status does bash report for a program killed with Ctrl+C?", "sh -c 'kill -INT $$'"),
    (4, "What exit status does GNU `timeout` return when the command runs out of time?", "timeout 0.2 sleep 2"),
    (4, "What exit status does `curl` return when it cannot resolve the host name?", "curl -s http://nonexistent-host.invalid/ >/dev/null"),
    (4, "What exit status does `curl` return when the connection is refused?", "curl -s http://127.0.0.1:1/ >/dev/null"),
    (4, "What exit status does `ssh` return when it cannot connect to the host?",
        "ssh -o BatchMode=yes -o ConnectTimeout=3 nobody@nonexistent-host.invalid true 2>/dev/null"),
    (4, "What exit status does `git diff --exit-code` return when there are unstaged changes?",
        "d=$(mktemp -d); cd \"$d\" && git init -q && echo a > f && git add f && git -c user.email=a@b -c user.name=a commit -qm i "
        "&& echo b > f && git diff --exit-code >/dev/null; r=$?; rm -rf \"$d\"; exit $r"),
    (5, "What exit status does `curl --fail` return when the server answers HTTP 404?",
        "python3 -m http.server 18765 --bind 127.0.0.1 >/dev/null 2>&1 & p=$!; sleep 1; curl -sf http://127.0.0.1:18765/nope >/dev/null; "
        "r=$?; kill $p; exit $r"),
    (5, "What exit status does `curl` return when `--max-time` runs out?", "curl -s -m 1 http://10.255.255.1/ >/dev/null"),
    (5, "What exit status does `python3 -c 'raise SystemExit(\"bye\")'` have?", "python3 -c 'raise SystemExit(\"bye\")' 2>/dev/null"),
    (5, "What exit status does bash return after `exit 300`?", "bash -c 'exit 300'"),
    (5, "What exit status does `kill -0` return for a PID that does not exist?", "kill -0 999999 2>/dev/null"),
    (5, "What exit status does GNU `ls` return when a file given to it does not exist?", "ls /nonexistent 2>/dev/null"),
    (5, "What exit status does `timeout -s KILL 0.2 sleep 2` return?", "timeout -s KILL 0.2 sleep 2"),
]
# (level, service, port, /etc/services name to check it against or None, protocol)
PORTS = [
    (1, "SSH", 22, "ssh", "tcp"), (1, "HTTPS", 443, "https", "tcp"), (1, "plain HTTP", 80, "http", "tcp"),
    (2, "PostgreSQL", 5432, "postgresql", "tcp"), (2, "MySQL", 3306, "mysql", "tcp"), (2, "Redis", 6379, "redis", "tcp"),
    (2, "DNS", 53, "domain", "udp"), (2, "SMTP (server to server)", 25, "smtp", "tcp"),
    (3, "MongoDB", 27017, None, "tcp"), (3, "the Ollama API", 11434, None, "tcp"), (3, "Windows Remote Desktop (RDP)", 3389, "ms-wbt-server", "tcp"),
    (3, "Prometheus", 9090, None, "tcp"), (3, "Grafana", 3000, None, "tcp"), (3, "Elasticsearch (HTTP)", 9200, None, "tcp"),
    (3, "Kafka brokers", 9092, None, "tcp"), (3, "IMAP over TLS (IMAPS)", 993, "imaps", "tcp"),
    (3, "SMTP mail submission with STARTTLS", 587, "submission", "tcp"), (3, "Jupyter Notebook/Lab", 8888, None, "tcp"),
    (3, "the Vite dev server", 5173, None, "tcp"),
    (4, "Memcached", 11211, None, "tcp"), (4, "RabbitMQ (AMQP)", 5672, "amqp", "tcp"), (4, "the RabbitMQ management UI", 15672, None, "tcp"),
    (4, "MQTT without TLS", 1883, None, "tcp"), (4, "the Kubernetes API server", 6443, None, "tcp"), (4, "etcd client requests", 2379, None, "tcp"),
    (4, "Prometheus node_exporter", 9100, None, "tcp"), (4, "Home Assistant", 8123, None, "tcp"), (4, "a Gradio app", 7860, None, "tcp"),
    (4, "a Streamlit app", 8501, None, "tcp"), (4, "ComfyUI", 8188, None, "tcp"), (4, "the LM Studio local server", 1234, None, "tcp"),
    (4, "WireGuard", 51820, None, "udp"), (4, "SMB file sharing", 445, "microsoft-ds", "tcp"), (4, "NFS", 2049, "nfs", "tcp"),
    (4, "VNC (display :0)", 5900, None, "tcp"), (4, "the rsync daemon", 873, "rsync", "tcp"), (4, "LDAP over TLS (LDAPS)", 636, "ldaps", "tcp"),
    (4, "the Syncthing web GUI", 8384, None, "tcp"), (4, "Plex Media Server", 32400, None, "tcp"), (4, "Jellyfin (HTTP)", 8096, None, "tcp"),
    (4, "Kibana", 5601, None, "tcp"), (4, "Microsoft SQL Server", 1433, "ms-sql-s", "tcp"), (4, "the Oracle Database listener", 1521, None, "tcp"),
    (5, "Qdrant (REST API)", 6333, None, "tcp"), (5, "Milvus", 19530, None, "tcp"), (5, "Meilisearch", 7700, None, "tcp"),
    (5, "InfluxDB 2 (HTTP API)", 8086, None, "tcp"), (5, "CouchDB", 5984, None, "tcp"), (5, "Cassandra (CQL)", 9042, None, "tcp"),
    (5, "Neo4j Bolt", 7687, None, "tcp"), (5, "the Consul HTTP API", 8500, None, "tcp"), (5, "HashiCorp Vault", 8200, None, "tcp"),
    (5, "Nomad (HTTP API)", 4646, None, "tcp"), (5, "Grafana Loki", 3100, None, "tcp"), (5, "the Jaeger UI", 16686, None, "tcp"),
    (5, "OTLP over gRPC (OpenTelemetry)", 4317, None, "tcp"), (5, "OTLP over HTTP (OpenTelemetry)", 4318, None, "tcp"),
    (5, "Zipkin", 9411, None, "tcp"), (5, "Prometheus Alertmanager", 9093, None, "tcp"), (5, "n8n", 5678, None, "tcp"),
    (5, "Node-RED", 1880, None, "tcp"), (5, "Mattermost", 8065, None, "tcp"), (5, "Tailscale's WireGuard traffic", 41641, None, "udp"),
    (5, "the kubelet API", 10250, None, "tcp"), (5, "the git:// daemon", 9418, "git", "tcp"), (5, "ZooKeeper clients", 2181, None, "tcp"),
    (5, "the Tor SOCKS proxy", 9050, None, "tcp"), (5, "the Squid proxy", 3128, None, "tcp"), (5, "KoboldCpp", 5001, None, "tcp"),
    (5, "SGLang's server", 30000, None, "tcp"), (5, "the LiteLLM proxy", 4000, None, "tcp"), (5, "the Jan local API server", 1337, None, "tcp"),
    (5, "Immich (web and API)", 2283, None, "tcp"), (5, "Uptime Kuma", 3001, None, "tcp"), (5, "the Nginx Proxy Manager admin UI", 81, None, "tcp"),
    (5, "mDNS", 5353, "mdns", "udp"), (5, "SSDP (UPnP discovery)", 1900, None, "udp"), (5, "IPP / CUPS printing", 631, "ipp", "tcp"),
    (5, "Kerberos", 88, "kerberos", "udp"), (5, "the Chroma vector DB server", 8000, None, "tcp"),
]
# made-up products: nobody can know their port, so UNKNOWN is as right as NONEXISTENT
PORTS_FAKE = [(3, "the Brindleport cache server"), (3, "the Quillmesh vector database"), (4, "the Tarnwick job queue"),
              (4, "the Voxmere inference server"), (4, "the Glintlake metrics agent"), (5, "the Hollowfen message broker"),
              (5, "the Sablecrest search engine"), (5, "the Mirewood reverse proxy")]
PORTS_DOCS = "documented defaults of each project (2026); the IANA ones are checked against /etc/services on the box"

_CODES_RUNNER = r'''
import errno, http, json, os, signal, socket, sys
checks = json.load(sys.stdin)
names = {n: getattr(errno, n) for n in dir(errno) if n.startswith("E") and isinstance(getattr(errno, n), int)}
sigs = {n: int(getattr(signal, n)) for n in dir(signal) if n.startswith("SIG") and not n.startswith("SIG_") and isinstance(getattr(signal, n), int)}
st = {}
for n, m in http.HTTPStatus.__members__.items():
    st.setdefault(m.value, {"phrase": m.phrase, "names": []})["names"].append(n)
svc = {}
for name, proto in checks:
    try:
        svc[name + "/" + proto] = socket.getservbyname(name, proto)
    except OSError:
        svc[name + "/" + proto] = None
print(json.dumps({"errno": names, "strerror": {v: os.strerror(v) for v in set(names.values())}, "signals": sigs,
                  "http": st, "services": svc, "platform": sys.platform, "python": sys.version.split()[0]}))
'''


def _build_codes(host: str, log) -> list[dict]:
    checks = [[s, p] for *_x, s, p in PORTS if s]
    box = _remote(_CODES_RUNNER, checks, host)
    mac = _remote(_CODES_RUNNER, [], None)
    assert box["platform"] == "linux", box["platform"]
    out = []

    def add(level, text, mode, accept, fake=False, idk=IDK, src="codes"):
        out.append({"kind": "codes", "src": src, "level": level, "fake": fake, "text": text, "mode": mode, "accept": accept, "idk": idk})

    phrases = {}
    for tab in (box["http"], mac["http"]):   # 3.12 and 3.14 phrases and member names (3.13 renamed 413/416/422)
        for v, d in tab.items():
            s = phrases.setdefault(int(v), set())
            s.add(d["phrase"])
            s.update(n.replace("_", " ").title() for n in d["names"])
    for code, al in HTTP_ALIASES.items():
        phrases[code].update(al)

    def norm(p):
        return re.sub(r"[^a-z]", "", p.lower())
    for level, codes in HTTP_REAL.items():
        for c in codes:
            ph = sorted(phrases[c])
            add(level, f"What is the standard reason phrase of HTTP status {c}?", "phrase",
                {"phrases": sorted({norm(p) for p in ph}), "show": box["http"][str(c)]["phrase"]}, src="http")
            add(level, f"Which HTTP status code has the reason phrase \"{box['http'][str(c)]['phrase']}\"?", "int", {"int": c}, src="http")
    for level, codes in HTTP_FAKE.items():
        for c in codes:
            assert c not in phrases, c
            add(level, f"What is the standard reason phrase of HTTP status {c}?", "phrase", {}, fake=True, src="http")
    by_num = {}
    for n, v in box["signals"].items():
        by_num.setdefault(v, set()).update({n, n[3:]})
    for level, ns in SIG_REAL.items():
        for n in ns:
            add(level, f"What is the number of signal {n} on x86-64 Linux?", "int", {"int": box["signals"][n]}, src="signal")
    for level, ns in SIG_FAKE.items():
        for n in ns:
            assert n not in box["signals"] and n not in mac["signals"], n
            add(level, f"What is the number of signal {n} on x86-64 Linux?", "int", {}, fake=True, src="signal")
    for level, codes in EXIT_SIG.items():
        for c in codes:
            add(level, f"A process exited with status {c}, as bash or Docker report it. Which signal killed it?", "name",
                {"names": sorted(by_num[c - 128], key=lambda x: (not x.startswith("SIG"), x)), "int": c - 128}, src="signal")
    enames = {}
    for n, v in box["errno"].items():
        enames.setdefault(v, set()).add(n)
    for level, ns in ERRNO_NUM.items():
        for n in ns:
            add(level, f"What is the errno number of {n} on Linux?", "int", {"int": box["errno"][n]}, src="errno")
    for level, ns in ERRNO_FAKE.items():
        for n in ns:
            assert n not in box["errno"] and n not in mac["errno"], n
            add(level, f"What is the errno number of {n} on Linux?", "int", {}, fake=True, src="errno")
    for level, ns in ERRNO_MSG.items():
        for n in ns:
            v = box["errno"][n]
            add(level, f"Which errno name goes with the error message \"{box['strerror'][str(v)]}\" on Linux?", "name",
                {"names": sorted(enames[v], key=lambda x: (x != n, x))}, src="errno")
    # the status as the calling shell reports it (a process killed by SIGINT is 130 there, not -2)
    res = _remote(_SH_RUNNER, [[f"(\n{c}\n)\necho __rc=$?" for *_x, c in EXITS], GNU_NAMES], host)
    for (level, q, _c), rs in zip(EXITS, res):
        rcs = [re.findall(r"__rc=(\d+)", r.get("out", "")) for r in rs]
        if any(r.get("timeout") for r in rs) or not all(rcs) or rcs[0] != rcs[1] or re.search(r"command not found", rs[0]["err"]):
            log(f"  drop exit status {q!r}: {rs}")
            continue
        add(level, q, "int", {"int": int(rcs[0][-1])}, src="exit")
    for level, name, port, svc, proto in PORTS:
        if svc:
            have = box["services"].get(f"{svc}/{proto}")
            if have != port:
                log(f"  drop port {name}: /etc/services says {have}, the list says {port}")
                continue
        add(level, f"What is the default {'UDP' if proto == 'udp' else 'TCP'} port of {name}?", "int", {"int": port}, src="port")
    for level, name in PORTS_FAKE:
        add(level, f"What is the default TCP port of {name}?", "int", {}, fake=True, idk=1.0, src="port")
    return out


def _write(b: dict, path: str) -> None:
    """One question per line, so a rebuild shows up as a readable diff."""
    head = {k: v for k, v in b.items() if k != "questions"}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(head, ensure_ascii=False)[:-1] + ', "questions": [\n')
        f.write(",\n".join(json.dumps(q, ensure_ascii=False) for q in b["questions"]))
        f.write("\n]}\n")


def build(host: str = HOST, out_path: str = BANK, log=print) -> dict:
    entries = parse_src()
    log(f"{len(entries)} source questions; running them ...")
    qs = _build_python([e for e in entries if e["src"] == "python"], host, log)
    qs += _build_shell([e for e in entries if e["src"] in ("shell", "jq")], host, log)
    qs += _build_codes(host, log)
    envs = {"python": [subprocess.run(["python3", "--version"], capture_output=True, text=True).stdout.split()[-1],
                       subprocess.run(["ssh", host, "python3 --version"], capture_output=True, text=True).stdout.split()[-1]],
            "bash": subprocess.run(["ssh", host, "bash --version | head -1"], capture_output=True, text=True).stdout.strip(),
            "jq": subprocess.run(["jq", "--version"], capture_output=True, text=True).stdout.strip(),
            "ports": PORTS_DOCS}
    b = {"built": dt.date.today().isoformat(), "env": envs, "questions": qs}
    _write(b, out_path)
    counts = {}
    for q in qs:
        k = (q["kind"], q["level"], q["fake"])
        counts[k] = counts.get(k, 0) + 1
    for kind in ("python", "shell", "codes"):
        log(f"{kind:7s} " + "  ".join(f"L{l}: {counts.get((kind, l, False), 0)}+{counts.get((kind, l, True), 0)} fake" for l in range(1, 7)))
    log(f"{len(qs)} questions -> {out_path}")
    return b


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        build()
    else:
        print("usage: python3 -m llmbox.suite.knowledge build")
