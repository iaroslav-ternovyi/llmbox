"""techhelp.gitignore: which files git sees. A repository tree with .gitignore files at several depths, sometimes
.git/info/exclude and files already tracked; the questions are what `git status --porcelain --untracked-files=all`
lists as untracked, what `git add -A` stages and what `git check-ignore -v <path>` prints.

The answer key is a port of git's own code (dir.c: parse_path_pattern, match_basename, match_pathname,
last_matching_pattern_from_lists, prep_exclude, treat_one_path; wildmatch.c; builtin/check-ignore.c), not of the
gitignore man page, and tests/real_programs.py (`gitignore`) builds every generated tree in a real repository and
compares each answer with real git (2.51 on macOS, 2.49 on Linux), next to random trees with random patterns.

What real git does that a reading of the docs misses: an excluded directory is never entered, so a negation below it
does nothing and an untracked file in it stays hidden even when the directory holds tracked files; `check-ignore -v`
also prints a matching negation (`!pattern`), prints nothing for a tracked path (or a directory holding one) and reports
the parent directory's line for a path inside an excluded directory; a .gitignore inside an excluded directory is never
read; any .gitignore line beats .git/info/exclude; `*.log` matches a directory `old.log/`; `foo/**` matches below foo,
not foo; trailing spaces are trimmed unless escaped (`*.bak\\ ` needs a trailing space in the name).

Levels (questions: untracked listing, `git add -A`, check-ignore): 1-2 name / extension / directory patterns and
anchoring in one .gitignore of a fresh repository; 3 negation, a file named like an ignored directory, `a/*.md`;
4 character classes, a nested .gitignore; 5 `**`, info/exclude, a commit with tracked (some ignored) files, edits and a
deletion; 6 negation under an excluded directory, later lines winning, case, `dir/*` + `!dir/sub/`; 7 escapes, trailing
spaces (shown with `cat -nE`), empty directories, a pattern matching a directory; 8 all of them in three .gitignore
files; 9-10 four or five .gitignore files, a whitelist (`*`, `!*/`, `!*.py`), `.*`, and patterns drawn at random from the
tree (6 / 12) on 50-90 files.
"""
from __future__ import annotations

import re

from .common import Item, final_answer, multi_check, rng

BLOCK = "techhelp"

# ---- wildmatch.c ------------------------------------------------------------------------------------------------------

WM_MATCH, WM_NOMATCH, WM_ABORT_ALL, WM_ABORT_TO_STARSTAR = 0, 1, -1, -2
_GLOB_SPECIAL = set("*?[\\")
_CC = {"alnum": str.isalnum, "alpha": str.isalpha, "blank": lambda c: c in " \t", "cntrl": lambda c: ord(c) < 32 or ord(c) == 127,
       "digit": lambda c: c in "0123456789", "graph": lambda c: 33 <= ord(c) <= 126, "lower": lambda c: "a" <= c <= "z",
       "print": lambda c: 32 <= ord(c) <= 126, "punct": lambda c: 33 <= ord(c) <= 126 and not c.isalnum(),
       "space": lambda c: c in " \t\n\r\f\v", "upper": lambda c: "A" <= c <= "Z", "xdigit": lambda c: c in "0123456789abcdefABCDEF"}


def _dowild(p: str, pi: int, t: str, ti: int, pathname: bool) -> int:
    """git's dowild() over (pattern, index) and (text, index); '' past the end stands for the C string's NUL."""
    P = lambda i: p[i] if i < len(p) else ""
    T = lambda i: t[i] if i < len(t) else ""
    pattern_start = pi
    while P(pi) != "":
        p_ch = P(pi)
        t_ch = T(ti)
        if t_ch == "" and p_ch != "*":
            return WM_ABORT_ALL
        if p_ch == "\\":
            pi += 1
            p_ch = P(pi)
            if t_ch != p_ch:
                return WM_NOMATCH
        elif p_ch == "?":
            if pathname and t_ch == "/":
                return WM_NOMATCH
        elif p_ch == "*":
            pi += 1
            if P(pi) == "*":
                prev_p = pi - 2
                pi += 1
                while P(pi) == "*":
                    pi += 1
                if not pathname:   # without WM_PATHNAME, '*' == '**'
                    match_slash = True
                elif (prev_p < pattern_start or P(prev_p) == "/") and (P(pi) == "" or P(pi) == "/" or (P(pi) == "\\" and P(pi + 1) == "/")):
                    if P(pi) == "/" and _dowild(p, pi + 1, t, ti, pathname) == WM_MATCH:
                        return WM_MATCH
                    match_slash = True
                else:
                    match_slash = not pathname
            else:
                match_slash = not pathname
            if P(pi) == "":
                if not match_slash and "/" in t[ti:]:
                    return WM_NOMATCH
                return WM_MATCH
            elif not match_slash and P(pi) == "/":
                j = t.find("/", ti)
                if j < 0:
                    return WM_NOMATCH
                ti = j
                # the slash is consumed by the loop's increment
                pi += 1
                ti += 1
                continue
            while True:
                if t_ch == "":
                    break
                if P(pi) not in _GLOB_SPECIAL:
                    p_ch = P(pi)
                    while True:
                        t_ch = T(ti)
                        if t_ch == "" or not (match_slash or t_ch != "/"):
                            break
                        if t_ch == p_ch:
                            break
                        ti += 1
                    if t_ch != p_ch:
                        return WM_ABORT_ALL if match_slash else WM_ABORT_TO_STARSTAR
                matched = _dowild(p, pi, t, ti, pathname)
                if matched != WM_NOMATCH:
                    if not match_slash or matched != WM_ABORT_TO_STARSTAR:
                        return matched
                elif not match_slash and t_ch == "/":
                    return WM_ABORT_TO_STARSTAR
                ti += 1
                t_ch = T(ti)
            return WM_ABORT_ALL
        elif p_ch == "[":
            pi += 1
            p_ch = P(pi)
            if p_ch == "^":
                p_ch = "!"
            negated = p_ch == "!"
            if negated:
                pi += 1
                p_ch = P(pi)
            prev_ch = ""
            matched = False
            while True:
                if p_ch == "":
                    return WM_ABORT_ALL
                if p_ch == "\\":
                    pi += 1
                    p_ch = P(pi)
                    if p_ch == "":
                        return WM_ABORT_ALL
                    if t_ch == p_ch:
                        matched = True
                elif p_ch == "-" and prev_ch and P(pi + 1) and P(pi + 1) != "]":
                    pi += 1
                    p_ch = P(pi)
                    if p_ch == "\\":
                        pi += 1
                        p_ch = P(pi)
                        if p_ch == "":
                            return WM_ABORT_ALL
                    if prev_ch <= t_ch <= p_ch:
                        matched = True
                    p_ch = ""
                elif p_ch == "[" and P(pi + 1) == ":":
                    pi += 2
                    s = pi
                    while P(pi) != "" and P(pi) != "]":
                        pi += 1
                    p_ch = P(pi)
                    if p_ch == "":
                        return WM_ABORT_ALL
                    i = pi - s - 1
                    if i < 0 or P(pi - 1) != ":":
                        pi = s - 2
                        p_ch = "["
                        if t_ch == p_ch:
                            matched = True
                        prev_ch = p_ch
                        pi += 1
                        p_ch = P(pi)
                        if p_ch == "]":
                            break
                        continue
                    name = p[s:s + i]
                    if name not in _CC:
                        return WM_ABORT_ALL
                    if t_ch and _CC[name](t_ch):
                        matched = True
                    p_ch = ""
                elif t_ch == p_ch:
                    matched = True
                prev_ch = p_ch
                pi += 1
                p_ch = P(pi)
                if p_ch == "]":
                    break
            if matched == negated or (pathname and t_ch == "/"):
                return WM_NOMATCH
        else:
            if t_ch != p_ch:
                return WM_NOMATCH
        pi += 1
        ti += 1
    return WM_NOMATCH if T(ti) != "" else WM_MATCH


def wildmatch(pattern: str, text: str, pathname: bool) -> bool:
    return _dowild(pattern, 0, text, 0, pathname) == WM_MATCH


# ---- dir.c: patterns ----------------------------------------------------------------------------------------------------

def _simple_length(s: str) -> int:
    for i, c in enumerate(s):
        if c in _GLOB_SPECIAL:
            return i
    return len(s)


def _trim_trailing_spaces(s: str) -> str:
    last_space = None
    i = 0
    while i < len(s):
        c = s[i]
        if c == " ":
            if last_space is None:
                last_space = i
        elif c == "\\":
            i += 1
            if i >= len(s):
                return s
            last_space = None
        else:
            last_space = None
        i += 1
    return s[:last_space] if last_space is not None else s


class Pattern:
    __slots__ = ("pattern", "src", "lineno", "base", "neg", "mustbedir", "nodir", "endswith", "nowild")

    def __init__(self, line: str, src: str, lineno: int, base: str):
        p = line
        self.neg = p.startswith("!")
        if self.neg:
            p = p[1:]
        self.mustbedir = p.endswith("/")
        if self.mustbedir:
            p = p[:-1]
        self.nodir = "/" not in p
        self.nowild = min(_simple_length(line[1:] if self.neg else line), len(p))
        rest = (line[1:] if self.neg else line)
        self.endswith = rest.startswith("*") and _simple_length(rest[1:]) == len(rest[1:])
        self.pattern, self.src, self.lineno, self.base = p, src, lineno, base

    def shown(self) -> str:
        return ("!" if self.neg else "") + self.pattern + ("/" if self.mustbedir else "")

    def matches(self, path: str, is_dir: bool) -> bool:
        """last_matching_pattern_from_list's test for one pattern."""
        if self.mustbedir and not is_dir:
            return False
        basename = path.rsplit("/", 1)[-1]
        pat, prefix = self.pattern, self.nowild
        if self.nodir:   # match_basename
            if prefix == len(pat):
                return pat == basename
            if self.endswith:
                return len(pat) - 1 <= len(basename) and basename.endswith(pat[1:])
            return wildmatch(pat, basename, False)
        # match_pathname
        if pat.startswith("/"):
            pat, prefix = pat[1:], prefix - 1
        base = self.base[:-1] if self.base else ""
        if len(path) < len(base) + 1 or (base and path[len(base)] != "/") or path[:len(base)] != base:
            return False
        name = path[len(base) + 1:] if base else path
        if prefix:
            if prefix > len(name) or pat[:prefix] != name[:prefix]:
                return False
            pat, name = pat[prefix:], name[prefix:]
            if not pat and not name:
                return True
        return wildmatch(pat, name, True)


def parse_ignore(content: str, src: str, base: str) -> list[Pattern]:
    """add_patterns_from_buffer: one pattern per line; blank lines and lines starting with '#' are skipped; trailing
    spaces trimmed unless escaped; line numbers count every line."""
    out = []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines = lines[:-1]
    for n, line in enumerate(lines, 1):
        if line == "" or line.startswith("#"):
            continue
        line = _trim_trailing_spaces(line)
        out.append(Pattern(line, src, n, base))
    return out


# ---- the repository ----------------------------------------------------------------------------------------------------

class Repo:
    """A working tree (files, empty directories), .gitignore contents per directory ('' = top, 'a/b/'), the content of
    .git/info/exclude, and the index (tracked paths; `modified` / `deleted` are tracked paths changed since the commit)."""

    def __init__(self, files, ignores: dict[str, str], exclude: str | None = None, tracked=(), modified=(), deleted=(),
                 empty_dirs=()):
        self.files = set(files) | {d + ".gitignore" for d in ignores}
        self.deleted = set(deleted)
        self.files -= self.deleted
        self.tracked = set(tracked) | self.deleted
        self.modified = set(modified)
        self.ignores = dict(ignores)
        self.exclude = exclude
        self.dirs = {""}
        for f in self.files:
            parts = f.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                self.dirs.add("/".join(parts[:i]) + "/")
        for d in empty_dirs:
            parts = d.rstrip("/").split("/")
            for i in range(1, len(parts) + 1):
                self.dirs.add("/".join(parts[:i]) + "/")
        self._lists = {d: parse_ignore(c, d + ".gitignore", d) for d, c in self.ignores.items()}
        self._excl = parse_ignore(exclude, ".git/info/exclude", "") if exclude else []

    def _from_lists(self, dir_lists: list[list[Pattern]], path: str, is_dir: bool) -> Pattern | None:
        """last_matching_pattern_from_lists: the per-directory files deepest first, then info/exclude; within a file the
        last matching line."""
        for pl in list(reversed(dir_lists)) + [self._excl]:
            for pat in reversed(pl):
                if pat.matches(path, is_dir):
                    return pat
        return None

    def last_match(self, path: str, is_dir: bool) -> Pattern | None:
        """last_matching_pattern: prep_exclude walks the leading directories from the top; a directory that is excluded
        (not by a negative pattern) decides for everything below it, and its own and deeper .gitignore files are
        never read."""
        comps = path.split("/")[:-1]
        lists = [self._lists[""]] if "" in self._lists else []
        for i in range(1, len(comps) + 1):
            d = "/".join(comps[:i])
            pat = self._from_lists(lists, d, True)
            if pat is not None and not pat.neg:
                return pat
            if d + "/" in self._lists:
                lists.append(self._lists[d + "/"])
        return self._from_lists(lists, path, is_dir)

    def excluded(self, path: str, is_dir: bool) -> bool:
        pat = self.last_match(path, is_dir)
        return pat is not None and not pat.neg

    def untracked(self) -> set[str]:
        """read_directory_recursive with --untracked-files=all: an excluded directory is not entered; a tracked file is
        never untracked; directories themselves are never listed."""
        out = set()
        kids: dict[str, list[tuple[str, bool]]] = {}
        for f in self.files:
            kids.setdefault(f.rsplit("/", 1)[0] + "/" if "/" in f else "", []).append((f, False))
        for d in self.dirs:
            if d:
                parent = d[:-1].rsplit("/", 1)[0] + "/" if "/" in d[:-1] else ""
                kids.setdefault(parent, []).append((d[:-1], True))

        def walk(d: str):
            for p, is_dir in kids.get(d, []):
                if is_dir:
                    if not self.excluded(p, True):
                        walk(p + "/")
                elif p not in self.tracked and not self.excluded(p, False):
                    out.add(p)
        walk("")
        return out

    def add_all(self) -> set[str]:
        """`git add -A`, as `git diff --cached --name-only` lists it: new untracked files, modified and deleted tracked
        files (ignore rules do not apply to tracked files)."""
        return self.untracked() | self.modified | self.deleted

    def check_ignore(self, path: str) -> str:
        """`git check-ignore -v <path>`: '<source>:<line>' of the last matching pattern (a negative one too: -v shows
        it), or NONE when nothing matches or the path is tracked (a directory: holds a tracked file - the pathspec
        matches the index)."""
        if self._in_index(path):
            return "NONE"
        pat = self.last_match(path, path + "/" in self.dirs)
        return f"{pat.src}:{pat.lineno}" if pat is not None else "NONE"

    def check_ignore_pattern(self, path: str) -> Pattern | None:
        return None if self._in_index(path) else self.last_match(path, path + "/" in self.dirs)

    def _in_index(self, path: str) -> bool:
        return path in self.tracked or any(t.startswith(path + "/") for t in self.tracked)


# ---- the generator -----------------------------------------------------------------------------------------------------
# A level is a fixed mix of rule modules (a seed changes names, extensions, depths and which directory holds which
# .gitignore); levels 9-10 add patterns drawn at random from the tree, so the rules interact in ways no template plans.
# Every path is unique ignoring case (the real-program check also runs on macOS, whose file system folds case).

NOISE_EXT = ["log", "tmp", "bak", "swp", "out", "cache", "pyc", "orig", "dump", "old"]
BUILD_DIRS = ["build", "dist", "out", "target", "coverage", "bin", "obj", "tmp", "cache", "vendor"]
CODE_DIRS = ["src", "lib", "app", "pkg", "core", "server", "client", "api", "web", "tools", "services", "cmd"]
SUB_DIRS = ["utils", "models", "handlers", "views", "config", "jobs", "db", "cli", "http", "auth", "internal", "common"]
DOC_DIRS = ["docs", "notes", "guides", "manual", "wiki"]
DATA_DIRS = ["data", "fixtures", "samples", "assets", "static", "public", "seeds", "testdata"]
LOG_DIRS = ["logs", "var", "run", "reports", "traces"]
STEMS = ["main", "app", "index", "util", "helpers", "server", "client", "worker", "routes", "schema", "models", "handler",
         "config", "settings", "db", "cli", "api", "auth", "queue", "jobs", "report", "debug", "error", "access", "trace",
         "notes", "todo", "draft", "backup", "sample", "seed", "dump", "setup", "deploy", "build_info", "metrics"]
CODE_EXT = ["js", "ts", "py", "go", "rs", "rb", "java", "c", "h", "sh"]
DOC_EXT = ["md", "txt", "html", "rst"]
SECRET_NAMES = [".env", ".DS_Store", "secrets.json", "local.settings.json", "Thumbs.db", "credentials.yml", ".npmrc", "id_rsa"]
KEEP_STEMS = ["keep", "important", "release", "baseline", "golden", "pinned"]


class _Scn:
    def __init__(self, r):
        self.r = r
        self.files: set[str] = set()
        self.dirs: set[str] = set()          # 'a', 'a/b' (no trailing slash)
        self.low: set[str] = set()           # every file and directory path, lower-cased
        self.empty: list[str] = []
        self.ign: dict[str, list[list[str]]] = {"": []}
        self.excl: list[list[str]] = []
        self.focus: list[str] = []

    def add(self, path: str) -> bool:
        parts = path.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            if d in self.files or (d.lower() in self.low and d not in self.dirs):
                return False
        if path.lower() in self.low:
            return path in self.files
        self.files.add(path)
        self.low.add(path.lower())
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            self.dirs.add(d)
            self.low.add(d.lower())
        return True

    def add_empty(self, d: str) -> bool:
        d = d.rstrip("/")
        if any(x == d.lower() or x.startswith(d.lower() + "/") for x in self.low) or \
                any(d.lower().startswith(f.lower() + "/") for f in self.files):
            return False
        parts = d.split("/")
        for i in range(1, len(parts)):
            p = "/".join(parts[:i])
            if p.lower() in self.low and p not in self.dirs:
                return False
        self.empty.append(d + "/")
        for i in range(1, len(parts) + 1):
            self.dirs.add("/".join(parts[:i]))
            self.low.add("/".join(parts[:i]).lower())
        return True

    def group(self, base: str, lines: list[str]):
        self.ign.setdefault(base, []).append(lines)

    def fname(self, ext: str | None = None) -> str:
        return f"{self.r.choice(STEMS)}.{ext or self.r.choice(CODE_EXT)}"

    def sub(self, base: str, pool=SUB_DIRS) -> str:
        """A directory name under base that is not already a file."""
        for _ in range(50):
            d = self.r.choice(pool)
            if (base + d).lower() not in self.low or (base + d) in self.dirs:
                return d
        return "zz" + str(self.r.randrange(100))


def _m_ext(S: _Scn, B: str, level: int) -> str:
    r = S.r
    ext = r.choice(NOISE_EXT)
    S.group(B, [f"*.{ext}"])
    d1 = S.sub(B, CODE_DIRS)
    d2 = S.sub(B + d1 + "/")
    paths = [B + S.fname(ext), B + d1 + "/" + S.fname(ext), B + d1 + "/" + d2 + "/" + S.fname(ext)]
    near = B + d1 + "/" + r.choice(STEMS) + f".{ext}." + r.choice(["txt", "1", "gz", "json"])   # *.log: not x.log.txt
    for p in paths + [near]:
        S.add(p)
    S.focus += [paths[2], near]
    return ext


def _m_dironly(S: _Scn, B: str, level: int):
    r = S.r
    n = r.choice(BUILD_DIRS)
    S.group(B, [f"{n}/"])
    d1 = S.sub(B, CODE_DIRS)
    deep = B + d1 + "/" + n + "/" + S.fname("txt")
    for p in [B + n + "/" + S.fname(), B + n + "/" + r.choice(SUB_DIRS) + "/" + S.fname(), deep]:
        S.add(p)
    S.focus.append(deep)
    if level >= 3:   # a plain file with the directory's name: `build/` matches directories only
        d2 = S.sub(B, ["scripts", "ci", "hack", "etc"])
        if S.add(B + d2 + "/" + n):
            S.focus.append(B + d2 + "/" + n)


def _m_name(S: _Scn, B: str, level: int):
    r = S.r
    f = r.choice(SECRET_NAMES)
    S.group(B, [f])
    d1 = S.sub(B, CODE_DIRS)
    for p in [B + f, B + d1 + "/" + f, B + f + ".example"]:
        S.add(p)
    S.focus += [B + d1 + "/" + f, B + f + ".example"]


def _m_anchor(S: _Scn, B: str, level: int):
    r = S.r
    d1 = S.sub(B, CODE_DIRS)
    if r.random() < 0.5:
        n = r.choice(["TODO", "config.local.yaml", "scratch.txt", "notes.local.md", "dev.db", "debug.env"])
        S.group(B, [f"/{n}"])
        S.add(B + n)
        S.add(B + d1 + "/" + n)
        S.focus += [B + d1 + "/" + n, B + n]
    else:
        n = r.choice(["scratch", "local", "sandbox", "playground"])
        S.group(B, [r.choice([f"/{n}", f"/{n}/"])])
        deep = B + d1 + "/" + n + "/" + S.fname("txt")
        S.add(B + n + "/" + S.fname())
        S.add(deep)
        S.focus.append(deep)


def _m_midslash(S: _Scn, B: str, level: int):
    r = S.r
    d = r.choice(DOC_DIRS)
    ext = r.choice(DOC_EXT)
    S.group(B, [f"{d}/*.{ext}"])
    sd = r.choice(["api", "old", "v2", "internal", "archive"])
    other = S.sub(B, CODE_DIRS)
    ps = [B + d + "/" + S.fname(ext), B + d + "/" + sd + "/" + S.fname(ext), B + other + "/" + d + "/" + S.fname(ext)]
    for p in ps:
        S.add(p)
    S.focus += ps[1:]


def _m_neg(S: _Scn, B: str, level: int, ext: str | None = None):
    r = S.r
    e = ext or r.choice(NOISE_EXT)
    k = r.choice(KEEP_STEMS)
    S.group(B, ([f"*.{e}"] if ext is None else []) + [f"!{k}.{e}"])
    d1 = S.sub(B, CODE_DIRS)
    for p in [B + f"{k}.{e}", B + d1 + "/" + f"{k}.{e}", B + d1 + "/" + S.fname(e), B + S.fname(e)]:
        S.add(p)
    S.focus.append(B + d1 + "/" + f"{k}.{e}")


def _m_extneg(S: _Scn, B: str, level: int):
    e = _m_ext(S, B, level)
    _m_neg(S, B, level, e if S.r.random() < 0.5 else None)


def _m_charclass(S: _Scn, B: str, level: int):
    r = S.r
    kind = r.choice(["pyc", "q", "digit", "neg"])
    d1 = S.sub(B, CODE_DIRS)
    if kind == "pyc":
        S.group(B, ["*.py[co]"])
        st = r.choice(STEMS)
        ps = [f"{st}.pyc", f"{st}.pyd", f"{st}.pyo", f"{st}.py"]
    elif kind == "q":
        st = r.choice(["report", "export", "snapshot", "chunk"])
        e = r.choice(["csv", "json", "bin"])
        S.group(B, [f"{st}-?.{e}"])
        ps = [f"{st}-1.{e}", f"{st}-10.{e}", f"{st}-.{e}", f"{st}-a.{e}"]
    elif kind == "digit":
        st = r.choice(["part", "page", "shard", "slice"])
        S.group(B, [f"{st}[0-9].txt"])
        ps = [f"{st}1.txt", f"{st}12.txt", f"{st}x.txt", f"{st}7.txt"]
    else:
        st = r.choice(["cache", "tmp", "scratch"])
        S.group(B, [f"{st}_[!a-m]*"])
        ps = [f"{st}_alpha", f"{st}_Beta", f"{st}_zeta", f"{st}_nu.json"]
    for p in ps:
        S.add(B + d1 + "/" + p)
    S.focus += [B + d1 + "/" + ps[1], B + d1 + "/" + ps[2]]


def _m_nested(S: _Scn, B: str, level: int):
    """A .gitignore one directory down: its anchored patterns are relative to it, its negations beat the parent's."""
    r = S.r
    sd = S.sub(B, CODE_DIRS + DATA_DIRS)
    SB = B + sd + "/"
    e = r.choice(["csv", "json", "sql", "yaml"])
    S.group(B, [f"*.{e}"])
    loc = r.choice(["local", "dev", "secret"])
    le = r.choice(NOISE_EXT)
    S.group(SB, [f"/{loc}.{le}"])
    S.group(SB, [f"!{r.choice(['fixtures', 'schema', 'sample', 'seed'])}.{e}"] if r.random() < 0.5 else [f"!*.{e}"])
    inner = r.choice(SUB_DIRS)
    other = S.sub(B, CODE_DIRS)
    ps = [SB + f"{loc}.{le}", SB + inner + f"/{loc}.{le}", B + other + "/" + S.fname(e), B + other + f"/{loc}.{le}"]
    opt = [SB + "fixtures." + e, SB + "schema." + e, SB + "sample." + e, SB + "seed." + e, SB + inner + "/" + S.fname(e)]
    for p in r.sample(opt, 3) + ps:
        S.add(p)
    S.focus += [ps[1], ps[3]] + [p for p in opt if p in S.files][:2]


def _m_doublestar(S: _Scn, B: str, level: int):
    r = S.r
    kind = r.choice(["lead", "mid", "tail"] if level < 8 else ["mid", "tail", "tail"])
    o = S.sub(B, CODE_DIRS)
    if kind == "lead":
        d = r.choice(DATA_DIRS)
        e = r.choice(["json", "bin", "csv"])
        S.group(B, [f"**/{d}/*.{e}"])
        ps = [B + d + "/" + S.fname(e), B + o + "/" + d + "/" + S.fname(e), B + o + "/" + d + "/raw/" + S.fname(e)]
    elif kind == "mid":
        d = r.choice(DOC_DIRS)
        n = r.choice(["draft.md", "wip.md", "private.txt", "scratch.md"])
        S.group(B, [f"{d}/**/{n}"])
        ps = [B + d + "/" + n, B + d + "/" + r.choice(["api", "v1"]) + "/" + n, B + d + "/a/b/" + n, B + o + "/" + d + "/" + n]
    else:
        d = r.choice(["generated", "gen", "output", "artifacts", "snapshots"])
        k = r.choice([".gitkeep", "README.md", "keep.txt"])
        sd = r.choice(["v1", "raw", "tmp", "old"])
        S.group(B, [f"{d}/**", f"!{d}/{k}", f"!{d}/{sd}/{k}"])
        ps = [B + d + "/" + S.fname(), B + d + "/" + k, B + d + "/" + sd + "/" + k, B + d + "/" + sd + "/" + S.fname()]
    for p in ps:
        S.add(p)
    S.focus += ps[1:]


def _m_negparent(S: _Scn, B: str, level: int):
    r = S.r
    ld = r.choice(LOG_DIRS)
    k = r.choice(["keep.txt", ".gitkeep", "README.md", "index.txt"])
    S.group(B, [f"{ld}/", f"!{ld}/{k}"])
    cd = r.choice([x for x in BUILD_DIRS + ["uploads", "media", "storage"] if x != ld])
    k2 = r.choice([".gitkeep", "README", "placeholder.txt"])
    sd = r.choice(["thumbs", "2026", "old", "small"])
    S.group(B, [f"{cd}/*", f"!{cd}/{k2}", f"!{cd}/{sd}/{k2}"])
    ps = [B + ld + "/" + k, B + ld + "/" + S.fname("log"), B + cd + "/" + k2, B + cd + "/" + S.fname(), B + cd + "/" + sd + "/" + k2,
          B + cd + "/" + sd + "/" + S.fname()]
    for p in ps:
        S.add(p)
    S.focus += [ps[0], ps[2], ps[4]]


def _m_order(S: _Scn, B: str, level: int):
    r = S.r
    e = r.choice(NOISE_EXT)
    k = r.choice(KEEP_STEMS)
    S.group(B, [f"!{k}.{e}", f"*.{e}"])   # the negation first: the later line wins
    d1 = S.sub(B, CODE_DIRS)
    for p in [B + f"{k}.{e}", B + d1 + f"/{k}.{e}"]:
        S.add(p)
    S.focus.append(B + f"{k}.{e}")


def _m_case(S: _Scn, B: str, level: int):
    r = S.r
    e = r.choice(["log", "tmp", "bak"])
    n = r.choice(BUILD_DIRS)
    S.group(B, [f"*.{e}"])
    S.group(B, [f"{n}/"])
    d1 = S.sub(B, CODE_DIRS)
    ps = [B + d1 + "/" + r.choice(["ERROR", "Debug", "TRACE"]) + "." + e.upper(), B + d1 + "/" + r.choice(STEMS) + "." + e,
          B + n.capitalize() + "/" + S.fname()]
    for p in ps:
        S.add(p)
    S.focus += [ps[0], ps[2]]


def _m_dirstar(S: _Scn, B: str, level: int):
    r = S.r
    d = r.choice(["modules", "plugins", "packages", "themes", "extensions"])
    keep = r.choice(["core", "shared", "base", "custom"])
    S.group(B, [f"{d}/*", f"!{d}/{keep}/"])
    o = r.choice(["legacy", "contrib", "third", "old"])
    ps = [B + d + "/" + keep + "/" + S.fname(), B + d + "/" + keep + "/sub/" + S.fname(), B + d + "/" + o + "/" + S.fname(),
          B + d + "/" + S.fname("json")]
    for p in ps:
        S.add(p)
    S.focus += ps[1:3]


def _m_exclude(S: _Scn, B: str, level: int):
    """.git/info/exclude has the lowest precedence: its negation cannot undo a .gitignore line, a .gitignore negation
    undoes its line."""
    r = S.r
    e = r.choice(["swp", "iml", "code-workspace", "sublime-project", "vim"])
    S.excl.append([f"*.{e}"])
    d1 = S.sub("", CODE_DIRS)
    p = d1 + "/" + S.fname(e)
    S.add(p)
    S.focus.append(p)
    ge = r.choice(NOISE_EXT)
    k = r.choice(KEEP_STEMS)
    S.group("", [f"*.{ge}"])
    S.excl.append([f"!{k}.{ge}"])
    S.add(k + "." + ge)
    S.focus.append(k + "." + ge)
    n = r.choice(["scratch", "sandbox", "jot"])
    S.excl.append([f"/{n}/"])
    S.group("", [f"!{n}/"])
    S.add(n + "/" + S.fname("md"))


def _m_escape(S: _Scn, B: str, level: int):
    r = S.r
    a, b = r.sample(["draft.md", "notes.md", "todo.txt", "ideas.md", "scratch.txt"], 2)
    c = r.choice(["urgent.txt", "important.md", "wip.txt"])
    S.group(B, [f"\\#{a}", f"#{b}", f"\\!{c}"])
    d1 = S.sub(B, DOC_DIRS)
    ps = [B + "#" + a, B + d1 + "/#" + a, B + "#" + b, B + "!" + c, B + c]
    for p in ps:
        S.add(p)
    S.focus += [ps[1], ps[2], ps[3]]


def _m_space(S: _Scn, B: str, level: int):
    r = S.r
    e1, e2 = r.sample(["tmp", "bak", "swo", "part"], 2)
    S.group(B, [f"*.{e1}" + " " * r.choice([1, 2, 3]), f"*.{e2}\\ "])   # trailing spaces trimmed; an escaped one kept
    d1 = S.sub(B, CODE_DIRS)
    ps = [B + d1 + "/" + S.fname(e1), B + d1 + "/" + S.fname(e2)]
    for p in ps:
        S.add(p)
    S.focus += ps


def _m_empty(S: _Scn, B: str, level: int):
    r = S.r
    S.add_empty(B + r.choice(["uploads", "incoming", "spool"]) + "/" + r.choice(["new", "2026"]))
    S.add_empty(B + r.choice(["empty", "placeholder", "mnt"]))


def _m_dirmatch(S: _Scn, B: str, level: int):
    """A name pattern that matches a directory takes everything in it."""
    r = S.r
    kind = r.choice(["d", "egg", "log"])
    d1 = S.sub(B, CODE_DIRS)
    if kind == "d":
        S.group(B, ["*.d"])
        dd = r.choice(["conf.d", "rc.d", "hooks.d", "sites.d"])
    elif kind == "egg":
        S.group(B, ["*.egg-info"])
        dd = r.choice(["mypkg.egg-info", "tool.egg-info"])
    else:
        S.group(B, ["*.log"])
        dd = r.choice(["old.log", "archive.log", "2025.log"])
    ps = [B + d1 + "/" + dd + "/" + S.fname("conf"), B + d1 + "/" + dd + "/" + S.fname("txt")]
    for p in ps:
        S.add(p)
    S.focus.append(ps[0])


def _m_selfignore(S: _Scn, B: str, level: int):
    r = S.r
    S.group(B, [".*"] + (["!.gitignore"] if r.random() < 0.5 else []))
    d1 = S.sub(B, CODE_DIRS)
    ps = [B + ".cache/" + S.fname(), B + d1 + "/.eslintrc", B + d1 + "/" + S.fname()]
    for p in ps:
        S.add(p)
    S.focus += ps[:2]


def _m_whitelist(S: _Scn, B: str, level: int):
    r = S.r
    e = r.choice(["py", "go", "ts"])
    WB = B + S.sub(B, ["scripts", "ops", "hack", "automation"]) + "/"
    S.group(WB, ["*", "!*/", f"!*.{e}"])
    ps = [WB + S.fname(e), WB + S.fname("sh"), WB + "lib/" + S.fname(e), WB + "lib/" + S.fname("json"), WB + "README.md"]
    for p in ps:
        S.add(p)
    S.focus += ps[1:4]


def _random_patterns(S: _Scn, n: int):
    """Patterns drawn from the tree itself, put into a random .gitignore above them: interactions no template plans."""
    r = S.r
    paths = sorted(S.files)
    bases = sorted(S.ign)
    for _ in range(n):
        p = r.choice(paths)
        base = r.choice([b for b in bases if p.startswith(b)])
        parts = p[len(base):].split("/")
        name = parts[-1]
        ext = name.rsplit(".", 1)[-1] if "." in name.lstrip(".") else None
        opts = [name]
        if ext:
            opts += [f"*.{ext}", f"**/*.{ext}"]
        if len(parts) > 1:
            d = parts[0]
            opts += [f"{d}/", f"/{d}/", f"{d}/*", f"{d}/**", f"**/{parts[-2]}/", f"{d}/**/{name}", "/" + "/".join(parts[:-1]) + "/*"]
        pat = r.choice(opts)
        if r.random() < 0.35:
            pat = "!" + pat
        S.group(base, [pat])


# modules (at the top and in deeper directories with their own .gitignore); questions: U untracked, u untracked under a
# directory, A / a `git add -A` (everything / under a directory), C check-ignore
LEVELS = {
    1: dict(mods=["ext", "dironly", "name"], bases=0, commit=False, q="UCC", size=8),
    2: dict(mods=["ext", "dironly", "name", "anchor"], bases=0, commit=False, q="UCC", size=10),
    3: dict(mods=["extneg", "dironly", "anchor", "midslash", "name"], bases=0, commit=False, q="UCCC", size=12),
    4: dict(mods=["extneg", "dironly", "anchor", "midslash", "charclass", "nested"], bases=0, commit=False, q="uUCC", size=14),
    5: dict(mods=["extneg", "dironly", "anchor", "charclass", "nested", "doublestar", "exclude"], bases=0, commit=True,
            q="UACCC", size=16),
    6: dict(mods=["extneg", "dironly", "midslash", "nested", "doublestar", "exclude", "negparent", "order", "case", "dirstar"],
            bases=1, commit=True, q="uUACC", size=18),
    7: dict(mods=["extneg", "dironly", "anchor", "nested", "doublestar", "exclude", "negparent", "order", "escape", "space",
                  "empty", "dirmatch"], bases=2, commit=True, q="uUACC", size=20),
    8: dict(mods=["extneg", "dironly", "anchor", "midslash", "charclass", "nested", "doublestar", "exclude", "negparent",
                  "order", "case", "dirstar", "escape", "space", "empty", "dirmatch"], bases=2, commit=True, q="uuACCC", size=22),
    9: dict(mods=["extneg", "dironly", "anchor", "midslash", "charclass", "nested", "doublestar", "exclude", "negparent",
                  "order", "case", "dirstar", "escape", "space", "empty", "dirmatch", "selfignore", "whitelist"], bases=3,
            commit=True, q="uuACCC", size=26, rand=6),
    10: dict(mods=["extneg", "dironly", "anchor", "midslash", "charclass", "nested", "doublestar", "exclude", "negparent",
                   "order", "case", "dirstar", "escape", "space", "empty", "dirmatch", "selfignore", "whitelist", "doublestar",
                   "negparent"], bases=4, commit=True, q="UuuaCCC", size=30, rand=12),
}
_MODS = {"ext": _m_ext, "extneg": _m_extneg, "dironly": _m_dironly, "name": _m_name, "anchor": _m_anchor,
         "midslash": _m_midslash, "charclass": _m_charclass, "nested": _m_nested, "doublestar": _m_doublestar,
         "exclude": _m_exclude, "negparent": _m_negparent, "order": _m_order, "case": _m_case, "dirstar": _m_dirstar,
         "escape": _m_escape, "space": _m_space, "empty": _m_empty, "dirmatch": _m_dirmatch, "selfignore": _m_selfignore,
         "whitelist": _m_whitelist}


def _build(seed: int, level: int):
    cfg = LEVELS[level]
    r = rng(BLOCK, f"gitignore{level}", seed)
    S = _Scn(r)
    bases = [""]   # deeper directories with their own .gitignore get some of the modules (patterns relative to them)
    top = r.sample(CODE_DIRS, cfg["bases"] + 1)
    for i in range(cfg["bases"]):
        bases.append((bases[-1] if i >= 2 and r.random() < 0.6 else "") + top[i] + "/")
    for b in bases[1:]:
        S.ign.setdefault(b, [])
        S.add(b + S.fname())
    mods = list(cfg["mods"])
    r.shuffle(mods)
    for i, m in enumerate(mods):
        _MODS[m](S, "" if m == "exclude" else bases[i % len(bases)], level)
    while len(S.files) < cfg["size"] * 2:   # ordinary source files
        b = r.choice(bases)
        d = r.choice(["", S.sub(b, CODE_DIRS) + "/", S.sub(b, CODE_DIRS) + "/" + r.choice(SUB_DIRS) + "/"])
        S.add(b + d + S.fname(r.choice(CODE_EXT + DOC_EXT)))
    if cfg.get("rand"):
        _random_patterns(S, cfg["rand"])
    ignores = {}
    for b, groups in sorted(S.ign.items()):
        if not groups:
            continue
        r.shuffle(groups)
        lines = [r.choice(["# build output", "# generated files", "# local files"])] if level >= 2 and b == "" else []
        for g in groups:
            if level >= 2 and r.random() < 0.25:
                lines.append("")
            lines += g
        ignores[b] = "\n".join(lines) + "\n"
    exclude = None
    if S.excl:
        r.shuffle(S.excl)
        exclude = "# local excludes, not shared\n" + "\n".join(l for g in S.excl for l in g) + "\n"
    files = sorted(S.files)
    tracked, modified, deleted = set(), set(), set()
    if cfg["commit"]:
        gis = {b + ".gitignore" for b in ignores}
        tracked = {g for g in sorted(gis) if r.random() < 0.8}
        probe = Repo(files, ignores, exclude)
        ign = [f for f in files if probe.excluded(f, False)]
        plain = [f for f in files if f not in ign and f not in gis]
        tracked |= set(r.sample(plain, len(plain) // 2))
        tracked |= set(r.sample(ign, min(len(ign), 1 + level // 4)))   # committed before the rule was written
        tr_ign = sorted(f for f in tracked if f in ign)
        modified = set(r.sample(tr_ign, min(1, len(tr_ign))))
        modified |= set(r.sample(sorted(tracked - modified - gis), 1 + (level >= 8)))
        cand = sorted(tracked - modified - gis)
        deleted = set(r.sample(cand, 1)) if cand else set()
    repo = Repo([f for f in files if f not in deleted], ignores, exclude, tracked, modified, deleted, S.empty)
    return S, repo, ignores, exclude


def _subtrees(repo: Repo, want: int, r) -> list[str]:
    """Directories whose untracked listing is worth asking: several files below, some listed and some not."""
    un_all = repo.untracked()
    cands = []
    for d in sorted(repo.dirs):
        if not d:
            continue
        below = [f for f in repo.files if f.startswith(d)]
        un = [f for f in un_all if f.startswith(d)]
        if len(below) >= 4 and un and len(un) < len(below):
            cands.append((d, len(below)))
    cands.sort(key=lambda x: (-x[1], x[0]))
    top = [d for d, _n in cands[:max(want * 3, 4)]]
    r.shuffle(top)
    out = []
    for d in top:   # no subtree inside another one
        if not any(d.startswith(o) or o.startswith(d) for o in out):
            out.append(d)
        if len(out) == want:
            break
    return out


def _ci_kind(repo: Repo, path: str) -> str:
    if repo._in_index(path):
        return "tracked"
    pat = repo.last_match(path, False)
    if pat is None:
        return "none"
    if not pat.matches(path, False):
        return "parent"      # decided by an excluded directory above it
    if pat.neg:
        return "neg"
    if pat.src == ".git/info/exclude":
        return "exclude"
    return "nested" if pat.src != ".gitignore" else "plain"


def _pick_ci(repo: Repo, S: _Scn, n: int, r, level: int) -> list[str]:
    focus = [p for p in dict.fromkeys(S.focus) if p in repo.files or p in repo.deleted]
    if level >= 5:
        focus += sorted(f for f in repo.tracked if f in repo.files and repo.excluded(f, False))
    by: dict[str, list[str]] = {}
    for p in dict.fromkeys(focus):
        by.setdefault(_ci_kind(repo, p), []).append(p)
    kinds = sorted(by)
    r.shuffle(kinds)
    if level >= 6:   # the hard kinds first
        kinds.sort(key=lambda k: k not in ("parent", "neg", "tracked"))
    out = []
    while len(out) < n and any(p not in out for k in kinds for p in by[k]):
        for k in kinds:
            rest = [p for p in by[k] if p not in out]
            if rest and len(out) < n:
                out.append(r.choice(rest))
    return out


def _render_ignore(content: str, cat_e: bool) -> str:
    return "\n".join(f"{i:6d}\t{l}{'$' if cat_e else ''}" for i, l in enumerate(content.split("\n")[:-1], 1))


_NONE = re.compile(r"^\W*(none|nothing|\(none\)|no paths?|no files?|empty|n/a|prints nothing|no output)\W*$", re.I)


def _strip_note(a: str) -> str:
    """The answer without an explanation after it: '(...)' at the end, or after ' - ' / ' -- ' / an em dash."""
    a = re.split(r"\s+(?:—|–|--|-)\s+", a.strip())[0]
    return re.sub(r"\s*\([^()]*\)\s*$", "", a).strip() or a.strip()


def parse_paths(a: str) -> set[str]:
    a = _strip_note(a)
    if _NONE.match(a):
        return set()
    out = set()
    for tok in re.split(r"[,;\s]+", a):
        tok = tok.strip().strip("`'\"*").strip("[]()").strip("`'\"").rstrip(".;:")
        if not tok or tok in ("??", "and") or tok.lower() == "and":
            continue
        out.add(tok[2:] if tok.startswith("./") else tok)
    return out


def _paths_check(expected: set[str]):
    """A set of paths in any order and separator; NONE for the empty set."""
    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        return 1.0 if a is not None and parse_paths(a) == set(expected) else 0.0
    return check


_SRC = re.compile(r"((?:[\w.#!-]+/)*\.gitignore|(?:\.git/)?info/exclude):(\d+)")


def parse_ci(a: str) -> str | None:
    a = _strip_note(a.strip().strip("`*").strip())
    m = _SRC.search(a)
    if m:
        src = m.group(1)
        src = src[2:] if src.startswith("./") else src
        return f"{'.git/info/exclude' if src == 'info/exclude' else src}:{int(m.group(2))}"
    return "NONE" if _NONE.match(a) else None


def _ci_check(expected: str):
    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        return 1.0 if a is not None and parse_ci(a) == expected else 0.0
    return check


def _instr(n: int) -> str:
    return ("\n\nThink it through, then finish with one final line per question, exactly in the form:\n"
            + "\n".join(f"ANSWER {i + 1}: <answer>" for i in range(n)))


def gitignore(seed: int, level: int = 3) -> Item:
    """Which files git sees: the untracked listing, `git add -A`, `git check-ignore -v` on a generated repository."""
    cfg = LEVELS[level]
    S, repo, ignores, exclude = _build(seed, level)
    r = rng(BLOCK, f"gitignore-q{level}", seed)
    untracked, added = repo.untracked(), repo.add_all()
    subs = _subtrees(repo, cfg["q"].count("u") + cfg["q"].count("a"), r)
    cis = _pick_ci(repo, S, cfg["q"].count("C"), r, level)
    qs, exp, checks, qmeta = [], [], [], []
    for q in cfg["q"]:
        if q == "C":
            if not cis:
                continue
            p = cis.pop(0)
            e = repo.check_ignore(p)
            qs.append(f"What does `git check-ignore -v {p}` print?")
            exp.append(e)
            checks.append(_ci_check(e))
            qmeta.append({"q": "check-ignore", "path": p})
            continue
        d = subs.pop(0) if q in "ua" and subs else ""
        if q in "Uu":
            want = {f for f in untracked if f.startswith(d)}
            qs.append(f"Which paths under `{d}` does `git status --porcelain --untracked-files=all` list as untracked?" if d else
                      "Which paths does `git status --porcelain --untracked-files=all` list as untracked (`??`)?")
            qmeta.append({"q": "untracked", "under": d})
        else:
            want = {f for f in added if f.startswith(d)}
            qs.append(f"Which paths under `{d}` does `git add -A` stage (new, modified or deleted files)?" if d else
                      "Which paths does `git add -A` stage (new, modified or deleted files)?")
            qmeta.append({"q": "add", "under": d})
        exp.append(", ".join(sorted(want)) or "NONE")
        checks.append(_paths_check(want))
    cat_e = level >= 7
    parts = [f"A git repository on Linux (ext4; git 2.51 with the default configuration: core.ignoreCase is false and there "
             f"is no global excludes file). Every file in the working tree (`.git/` not shown):\n\n```\n"
             + "\n".join(sorted(repo.files)) + "\n```"]
    if S.empty:
        parts.append("Empty directories: " + ", ".join(f"`{d}`" for d in sorted(S.empty)) + ".")
    how = "`cat -nE` prints them (line numbers; `$` marks the end of each line)" if cat_e else "`cat -n` prints them"
    ig = [f"`{b}.gitignore`:\n```\n{_render_ignore(c, cat_e)}\n```" for b, c in sorted(ignores.items())]
    if exclude:
        ig.append(f"`.git/info/exclude`:\n```\n{_render_ignore(exclude, cat_e)}\n```")
    parts.append(f"The ignore files, as {how}:\n\n" + "\n\n".join(ig))
    if repo.tracked:
        ch = (["edited " + " and ".join(f"`{m}`" for m in sorted(repo.modified))] if repo.modified else []) + \
             (["deleted " + " and ".join(f"`{x}`" for x in sorted(repo.deleted))] if repo.deleted else [])
        parts.append("The last commit contains exactly these files (they are tracked): " + ", ".join(f"`{t}`" for t in sorted(repo.tracked))
                     + (f". Since that commit you have {' and '.join(ch)}" if ch else "") + "; nothing is staged.")
    else:
        parts.append("Nothing has been committed or staged yet (a fresh `git init`).")
    prompt = ("\n\n".join(parts) + "\n\nRun from the top of the repository:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs))
              + "\n\nAnswer a list of paths with the paths relative to the top of the repository, separated by commas, or NONE "
              "if there are none. Answer a check-ignore question with the `<source>:<line number>` its output starts with "
              "(e.g. `app/.gitignore:3`), or NONE if it prints nothing." + _instr(len(qs)))
    meta = {"expected": exp, "level": level, "questions": qmeta,
            "repo": {"files": sorted(repo.files), "empty_dirs": sorted(S.empty), "ignores": ignores, "exclude": exclude,
                     "tracked": sorted(repo.tracked), "modified": sorted(repo.modified), "deleted": sorted(repo.deleted)}}
    return Item(f"{BLOCK}.gitignore.L{level}.{seed}", BLOCK, "gitignore", [{"role": "user", "content": prompt}],
                multi_check(checks), max_tokens=32000, meta=meta)
