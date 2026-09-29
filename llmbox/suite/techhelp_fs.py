"""techhelp.fs_seq: what a shell script leaves behind in a directory tree.

A starting tree (directories, small text files, symlinks, a hard link) and a numbered script of everyday commands - mkdir
(-p), touch, echo > / >>, cat > , cp (-r / -a), mv, rm (-r / -f), rmdir, ln (-s / -sf / -sfn), cd, chmod - run by bash
with GNU coreutils as an ordinary user. The questions: which commands failed, the whole tree afterwards (find . | sort
with a type marker, symlink targets and file contents) and what a few paths hold or resolve to. Built on real behaviour
that is easy to get wrong over a long script: `cp -r src dst` into an existing dst, `mv` into / over / onto itself, a
relative `ln -s` target read from the link's directory, writing through a dangling symlink, hard links sharing appends,
`rm -r link` removing only the link and `rm -r link/` emptying the target, `cp` following symlinks while `cp -r`/`-a`
copy them, `ln -sf` into a symlinked directory, `..` after `cd` through a symlink, a read-only directory or file.

The answers come from an emulator of a small in-memory filesystem (no subprocess, deterministic, milliseconds per item),
checked against bash + GNU coreutils 8.32 / 9.4 / 9.7 in Docker by tests/real_programs.py (mode fs). Anything the
emulator does not model exactly, or where those versions differ, raises Unsupported and the generator never goes there.
Levels: 1-3 one trap in 5-10 commands, 4-6 15-25 commands, 7-8 30-50 with symlinks and hard links interacting, 9-10
60-100: the difficulty is the volume of exact state plus interacting traps. Credit per question; the tree per path.
"""
from __future__ import annotations

import re

from .common import Item, rng, strip_think

GRADING = 1
BLOCK = "techhelp"
ROOT = "/home/dev/proj"
UMASK = 0o022
MAXLINK = 40


class Unsupported(Exception):
    """A state the emulator does not model exactly, or one where coreutils versions differ: generation avoids it."""


class FSErr(Exception):
    """A failing system call (the errno name)."""


class Node:
    __slots__ = ("kind", "ino", "mode", "data", "target", "ents", "parent")

    def __init__(self, kind: str, ino: int, mode: int, data: str = "", target: str = ""):
        self.kind, self.ino, self.mode, self.data, self.target = kind, ino, mode, data, target
        self.ents: dict | None = {} if kind == "d" else None
        self.parent: Node | None = None


def _base(path: str) -> str:
    b = path.rstrip("/").split("/")[-1]
    if b in ("", ".", ".."):
        raise Unsupported(f"basename of {path!r}")
    return b


def _join(a: str, b: str) -> str:
    return b if a in ("", ".") else a.rstrip("/") + "/" + b


def content(data: str) -> str:
    """A file's content in the answer notation: its lines joined with '|', or (empty)."""
    if not data:
        return "(empty)"
    if not data.endswith("\n"):
        raise Unsupported("a partial last line")
    return "|".join(data[:-1].split("\n"))


class FS:
    """A tiny POSIX filesystem: directories, regular files (text), symlinks, hard links (shared nodes) and owner
    permission bits, with the kernel's path walk and the GNU tools' decisions on top. Every change goes through _put /
    _del / _set, which log it, so a command that turns out Unsupported is rolled back."""

    def __init__(self):
        self.n = 0
        self.log: list = []
        self.root = self._new("d", 0o755)
        self.root.parent = self.root
        cur = self.root
        for part in ROOT.strip("/").split("/"):
            d = self._new("d", 0o755)
            d.parent = cur
            cur.ents[part] = d
            cur = d
        self.top = cur
        self.cwd, self.pwd = cur, ROOT

    # ---- nodes and the undo log
    def _new(self, kind: str, mode: int, data: str = "", target: str = "") -> Node:
        self.n += 1
        return Node(kind, self.n, mode, data, target)

    def _put(self, d: Node, name: str, node: Node) -> None:
        self.log.append(("ent", d, name, d.ents.get(name), node, node.parent))
        d.ents[name] = node
        if node.kind == "d":
            node.parent = d

    def _del(self, d: Node, name: str) -> None:
        self.log.append(("ent", d, name, d.ents[name], None, None))
        del d.ents[name]

    def _set(self, node: Node, attr: str, val) -> None:
        self.log.append(("attr", node, attr, getattr(node, attr)))
        setattr(node, attr, val)

    def _setcwd(self, node: Node, pwd: str) -> None:
        self.log.append(("cwd", self.cwd, self.pwd))
        self.cwd, self.pwd = node, pwd

    def rollback(self, mark: int) -> None:
        while len(self.log) > mark:
            e = self.log.pop()
            if e[0] == "ent":
                _, d, name, old, new, oldparent = e
                if old is None:
                    d.ents.pop(name, None)
                else:
                    d.ents[name] = old
                if new is not None and new.kind == "d":
                    new.parent = oldparent
            elif e[0] == "attr":
                setattr(e[1], e[2], e[3])
            else:
                self.cwd, self.pwd = e[1], e[2]

    # ---- the kernel's path walk
    def walk(self, path: str, follow: bool = True, trail: bool = True, base: Node | None = None, depth: int = 0):
        """-> (parent dir, last name, node or None, trailing slash). Symlinks in the middle are always followed; the
        last one when `follow`, or when the path ends in '/' and `trail` (stat, open; mkdir / rmdir / rename / unlink /
        link / symlink do not follow it). node None: the last component is missing, its directory exists."""
        if not path:
            raise FSErr("ENOENT")
        cur = self.root if path[0] == "/" else (self.cwd if base is None else base)
        comps = [c for c in path.split("/") if c]
        slash = path.endswith("/")
        if not comps:
            return cur, ".", cur, True
        last = len(comps) - 1
        for i, c in enumerate(comps):
            if cur.kind != "d":
                raise FSErr("ENOTDIR")
            if not cur.mode & 0o100:
                raise FSErr("EACCES")
            nxt = cur if c == "." else cur.parent if c == ".." else cur.ents.get(c)
            if i < last:
                if nxt is not None and nxt.kind == "l":
                    if depth >= MAXLINK:
                        raise Unsupported("symlink loop")
                    nxt = self.walk(nxt.target, True, True, cur, depth + 1)[2]
                if nxt is None:
                    raise FSErr("ENOENT")
                cur = nxt
                continue
            if nxt is not None and nxt.kind == "l" and (follow or (slash and trail)):
                if depth >= MAXLINK:
                    raise Unsupported("symlink loop")
                p, n, t, s2 = self.walk(nxt.target, True, True, cur, depth + 1)
                if slash and t is not None and t.kind != "d":
                    raise FSErr("ENOTDIR")
                return p, n, t, slash or s2
            if slash and nxt is not None and nxt.kind == "f":
                raise FSErr("ENOTDIR")
            return cur, c, nxt, slash

    def stat(self, path: str) -> Node:
        node = self.walk(path, True)[2]
        if node is None:
            raise FSErr("ENOENT")
        return node

    def lstat(self, path: str) -> Node:
        node = self.walk(path, False, True)[2]
        if node is None:
            raise FSErr("ENOENT")
        return node

    def inside(self, d: Node) -> None:
        x = d
        while x is not self.top:
            if x is self.root:
                raise Unsupported("outside the project directory")
            x = x.parent

    def nlink(self, node: Node) -> int:
        n, stack = 0, [self.root]
        while stack:
            d = stack.pop()
            for ch in d.ents.values():
                n += ch is node
                if ch.kind == "d":
                    stack.append(ch)
        return n

    def validate(self) -> None:
        """The shell's logical $PWD must still lead to its working directory, inside the project."""
        if not (self.pwd == ROOT or self.pwd.startswith(ROOT + "/")):
            raise Unsupported("left the project directory")
        try:
            node = self.stat(self.pwd)
        except FSErr:
            raise Unsupported("stale $PWD") from None
        if node is not self.cwd:
            raise Unsupported("stale $PWD")

    # ---- a command line
    def glob(self, pat: str) -> list[str]:
        """bash pathname expansion of a '*' in the last component (LC_ALL=C order); no match leaves the word as it is."""
        d, _, base = pat.rpartition("/")
        if "*" in d or "?" in base or "[" in base:
            raise Unsupported(f"glob {pat}")
        try:
            node = self.stat(d) if d else self.cwd
        except FSErr:
            return [pat]
        if node.kind != "d":
            return [pat]
        rx = re.compile(".*".join(re.escape(x) for x in base.split("*")))
        names = sorted(n for n in node.ents if rx.fullmatch(n) and not n.startswith("."))
        return [(d + "/" if d else "") + n for n in names] or [pat]

    def run(self, cmd: str) -> bool:
        """Run one command; True if it exits 0."""
        t = cmd.split()
        c = t[0]
        if c == "echo":                       # echo WORD > PATH  |  echo WORD >> PATH
            return self.echo(t[1], t[3], t[2] == ">>")
        if c == "cat":                        # cat SRC > DST  |  cat SRC >> DST
            return self.cat(t[1], t[3], t[2] == ">>")
        opts = "".join(x[1:] for x in t[1:] if x.startswith("-"))
        args = []
        for x in t[1:]:
            if not x.startswith("-"):
                args += self.glob(x) if "*" in x and c != "cd" else [x]
        if c == "cd":
            return self.cd(args[0])
        if c == "mkdir":
            return self.mkdir(args, "p" in opts)
        if c == "touch":
            return self.touch(args, "c" in opts)
        if c == "rm":
            return self.rm(args, "r" in opts or "R" in opts, "f" in opts, "d" in opts)
        if c == "rmdir":
            return self.rmdir(args, "p" in opts)
        if c == "cp":
            return self.cp(opts, args)
        if c == "mv":
            return self.mv(opts, args)
        if c == "ln":
            if "s" in opts:
                return self.ln_s(args[0], args[1], "f" in opts, "n" in opts, "r" in opts)
            return self.ln(args[0], args[1], "f" in opts)
        if c == "chmod":
            return self.chmod(args[0], args[1:])
        raise ValueError(cmd)

    # ---- bash redirections
    def open_w(self, path: str, append: bool) -> Node:
        """open(O_WRONLY|O_CREAT|(O_APPEND or O_TRUNC)): follows symlinks, a dangling one creates its target."""
        p, n, node, slash = self.walk(path, True)
        if node is None:
            if slash:
                raise FSErr("EISDIR")
            if n in (".", ".."):
                raise Unsupported(path)
            self.inside(p)
            if not p.mode & 0o200:
                raise FSErr("EACCES")
            node = self._new("f", 0o666 & ~UMASK)
            self._put(p, n, node)
            return node
        if node.kind == "d":
            raise FSErr("EISDIR")
        if not node.mode & 0o200:
            raise FSErr("EACCES")
        if not append and node.data:
            self._set(node, "data", "")
        return node

    def echo(self, word: str, path: str, append: bool) -> bool:
        try:
            f = self.open_w(path, append)
        except FSErr:
            return False
        self._set(f, "data", f.data + word + "\n")
        return True

    def cat(self, src: str, dst: str, append: bool) -> bool:
        """`cat SRC > DST`: bash opens (truncates) DST first, then cat reads SRC."""
        try:
            out = self.open_w(dst, append)
        except FSErr:
            return False
        try:
            s = self.stat(src)
        except FSErr:
            return False
        if s is out:      # bash emptied it before cat started: cat reads nothing and succeeds
            if append:
                raise Unsupported("cat a file onto its own end")
            return True
        if s.kind == "d" or not s.mode & 0o400:
            return False
        if s.data:
            self._set(out, "data", out.data + s.data)
        return True

    # ---- coreutils
    def touch(self, args: list[str], nocreate: bool = False) -> bool:
        ok = True
        for a in args:
            try:
                p, n, node, slash = self.walk(a, True)
            except FSErr:
                ok = ok and nocreate
                continue
            if node is not None or nocreate:
                continue
            if n in (".", ".."):
                raise Unsupported(a)
            self.inside(p)
            if slash or not p.mode & 0o200:
                ok = False
                continue
            self._put(p, n, self._new("f", 0o666 & ~UMASK))
        return ok

    def _mkdir1(self, path: str) -> None:
        p, n, node, _s = self.walk(path, False, False)
        if n in (".", "..") or node is not None:
            raise FSErr("EEXIST")
        self.inside(p)
        if not p.mode & 0o200:
            raise FSErr("EACCES")
        self._put(p, n, self._new("d", 0o777 & ~UMASK))

    def mkdir(self, args: list[str], parents: bool) -> bool:
        ok = True
        for a in args:
            try:
                if not parents:
                    self._mkdir1(a)
                    continue
                comps = [c for c in a.split("/") if c]
                if any(c in (".", "..") for c in comps):
                    raise Unsupported("mkdir -p with . or ..")
                for k in range(1, len(comps) + 1):
                    sub = ("/" if a.startswith("/") else "") + "/".join(comps[:k])
                    try:
                        self._mkdir1(sub)
                    except FSErr as e:   # an existing directory (or a symlink to one) is fine
                        try:
                            t = self.stat(sub)
                        except FSErr:
                            raise e from None
                        if t.kind != "d":
                            raise e from None
            except FSErr:
                ok = False
        return ok

    def _rmdir1(self, a: str) -> bool:
        try:
            p, n, node, _s = self.walk(a, False, False)
        except FSErr:
            return False
        if n in (".", ".."):
            raise Unsupported(a)
        if node is None or node.kind != "d" or node.ents or not p.mode & 0o200:
            return False
        self.inside(p)
        self._del(p, n)
        return True

    def rmdir(self, args: list[str], parents: bool = False) -> bool:
        """rmdir; -p then removes each parent named in the path, stopping at the first that fails."""
        ok = True
        for a in args:
            comps = [c for c in a.split("/") if c]
            if parents and (a.startswith("/") or any(c in (".", "..") for c in comps)):
                raise Unsupported("rmdir -p with . / .. / an absolute path")
            if not self._rmdir1(a):
                ok = False
                continue
            while parents and len(comps) > 1:
                comps.pop()
                pre = "/".join(comps)
                ln_ = self.walk(pre, False, False)[2]
                if ln_ is not None and ln_.kind == "l":
                    raise Unsupported("rmdir -p through a symlink")
                if not self._rmdir1(pre):
                    ok = False
                    break
        return ok

    def _rm_tree(self, p: Node, n: str) -> None:
        node = p.ents[n]
        if node.kind == "d":
            if node.ents and not node.mode & 0o200:
                raise Unsupported("rm -r in a read-only directory")
            for c in sorted(node.ents):
                self._rm_tree(node, c)
        if not p.mode & 0o200:
            raise Unsupported("rm -r in a read-only directory")
        self._del(p, n)

    def rm(self, args: list[str], rec: bool, force: bool, dirs: bool = False) -> bool:
        ok = True
        for a in args:
            if a.rstrip("/").split("/")[-1] in (".", ".."):   # "refusing to remove '.' or '..'"
                ok = False
                continue
            try:
                p, n, node, _s = self.walk(a, False, True)   # lstat: a trailing slash follows a symlink
            except FSErr as e:
                if not (force and e.args[0] in ("ENOENT", "ENOTDIR")):
                    ok = False
                continue
            if node is None:
                ok = ok and force
                continue
            if n in (".", ".."):
                raise Unsupported(a)
            self.inside(p)
            if node.kind != "d":
                if not p.mode & 0o200:
                    ok = False
                    continue
                self._del(p, n)
                continue
            if not rec:
                if dirs and a.endswith("/") and self.walk(a, False, False)[2].kind == "l":
                    raise Unsupported("rm -d link/")
                if dirs and not node.ents and p.mode & 0o200:   # rm -d: an empty directory
                    self._del(p, n)
                else:
                    ok = False
                continue
            ln_ = self.walk(a, False, False)[2]
            if ln_ is not None and ln_.kind == "l":     # rm -r link/: empties the target, then fails on the link
                if node.ents and not node.mode & 0o200:
                    raise Unsupported("rm -r in a read-only directory")
                for c in sorted(node.ents):
                    self._rm_tree(node, c)
                ok = False
                continue
            self._rm_tree(p, n)
        return ok

    def ln(self, target: str, link: str, force: bool = False) -> bool:
        """ln TARGET LINK (hard): a symlink TARGET is linked itself (-P, the default); -f replaces LINK."""
        try:
            into = self.stat(link).kind == "d"
        except FSErr:
            into = False
        if link.endswith("/") and not into:
            raise Unsupported("ln to a missing dir/")
        dst = _join(link, _base(target)) if into else link
        try:
            _p, _n, src, _s = self.walk(target, False, True)
            dp, dn, d, _s2 = self.walk(dst, False, False)
        except FSErr:
            return False
        if src is None or src.kind == "d":
            return False
        if dn in (".", ".."):
            raise Unsupported(dst)
        self.inside(dp)
        if d is not None:
            if not force or d.kind == "d":
                return False
            if d is src:     # -f onto another name of the same file: "are the same file" only for one name
                if self.nlink(src) == 1:
                    return False
                if self.walk(target, False, False)[:2] == (dp, dn):
                    return False
                return True
            if not dp.mode & 0o200:
                return False
            self._del(dp, dn)
        elif not dp.mode & 0o200:
            return False
        self._put(dp, dn, src)
        return True

    def canon(self, path: str) -> list[str]:
        """realpath -m (canonicalize, every component may be missing): the absolute path's components."""
        rname = [] if path.startswith("/") else [c for c in self.cwd_path().split("/") if c]
        rest = [c for c in path.split("/") if c]
        links = 0
        while rest:
            c = rest.pop(0)
            if c == ".":
                continue
            if c == "..":
                if rname:
                    rname.pop()
                continue
            node = self.root
            for x in rname:
                node = node.ents.get(x) if node is not None and node.kind == "d" else None
            nxt = node.ents.get(c) if node is not None and node.kind == "d" else None
            if nxt is not None and nxt.kind == "l":
                links += 1
                if links > MAXLINK:
                    raise Unsupported("symlink loop")
                if nxt.target.startswith("/"):
                    rname = []
                rest = [x for x in nxt.target.split("/") if x] + rest
                continue
            if nxt is not None and nxt.kind == "f" and rest:
                raise Unsupported("realpath -m through a file")
            rname.append(c)
        return rname

    def cwd_path(self) -> str:
        """The shell's working directory as an absolute physical path."""
        parts, x = [], self.cwd
        while x is not self.root:
            par = x.parent
            parts.append(next(n for n, c in par.ents.items() if c is x))
            x = par
        return "/" + "/".join(reversed(parts))

    def ln_s(self, target: str, link: str, force: bool, nodere: bool, rel: bool = False) -> bool:
        into = False
        try:
            if nodere:      # -n: a symlink to a directory is replaced, not entered
                ln_ = self.walk(link, False, False)[2]
                into = ln_ is not None and ln_.kind == "d"
            else:
                into = self.stat(link).kind == "d"
        except FSErr:
            pass
        if link.endswith("/") and not into:
            raise Unsupported("ln -s to a missing dir/")
        dst = _join(link, _base(target)) if into else link
        try:
            dp, dn, d, _s = self.walk(dst, False, False)
        except FSErr:
            return False
        if dn in (".", ".."):
            raise Unsupported(dst)
        self.inside(dp)
        if rel:   # -r: the target (from the working directory) relative to the link's directory, both canonicalized
            if force:
                raise Unsupported("ln -srf")
            frm = self.canon(target)
            ldir = self.canon(dst.rsplit("/", 1)[0] if "/" in dst.rstrip("/") else ".")
            k = 0
            while k < min(len(frm), len(ldir)) and frm[k] == ldir[k]:
                k += 1
            target = "/".join([".."] * (len(ldir) - k) + frm[k:]) or "."
        if d is not None:
            if not force or d.kind == "d":
                return False
            if d.kind == "f":     # ln's "are the same file" check (target resolved from the working directory)
                try:
                    if self.stat(target) is d:
                        raise Unsupported("ln -sf onto the file it names")
                except FSErr:
                    pass
            if not dp.mode & 0o200:
                return False
            self._del(dp, dn)
        elif not dp.mode & 0o200:
            return False
        self._put(dp, dn, self._new("l", 0o777, target=target))
        return True

    def _targets(self, srcs: list[str], dst: str, notarget: bool, is_cp: bool = True) -> list[tuple[str, str]] | None:
        """(source, destination path) per source of cp / mv: into DST when it is a directory (following a symlink),
        DST itself for one source otherwise, with -T, or for 'dir/.'; None when several sources have no directory."""
        if notarget and len(srcs) > 1:
            raise Unsupported("-T with several sources")
        try:
            into = self.stat(dst).kind == "d"
        except FSErr:
            into = False
        if len(srcs) > 1 and not into:
            return None                              # "target 'x' is not a directory": nothing is done
        if is_cp and dst.endswith("/") and not into and not notarget:
            raise Unsupported("cp to a missing dir/")
        out, seen = [], set()
        for s in srcs:
            dot = s == "." or s.endswith("/.")      # 'cp -r dir/. dst': the directory's content into dst itself
            t = dst if notarget or dot or not into else _join(dst, _base(s))
            key = t.rstrip("/")
            if key in seen:
                raise Unsupported("two sources onto one name")
            seen.add(key)
            out.append((s, t))
        return out

    def cp(self, opts: str, args: list[str]) -> bool:
        rec = bool(set(opts) & set("rRa"))
        arch, force, deref_all = "a" in opts, "f" in opts, "L" in opts
        follow_top = not rec or deref_all or "H" in opts   # cp follows a symlink SRC; -r / -a copy it, -H / -L follow it
        pairs = self._targets(args[:-1], args[-1], "T" in opts)
        if pairs is None:
            return False
        self._hl: dict = {}
        self._stack: list = []
        ok = True
        for src, target in pairs:
            try:
                s = self.walk(src, follow_top, True)[2]
            except FSErr:
                ok = False
                continue
            if s is None or (s.kind == "d" and not rec):
                ok = False                           # missing, or "-r not specified; omitting directory"
                continue
            ok = self._copy(s, src, target, arch, True, force, deref_all) and ok
        return ok

    def _copy(self, s: Node, spath: str, dst: str, arch: bool, top: bool, force: bool = False, deref: bool = False) -> bool:
        def fail() -> bool:
            if top:
                return False
            raise Unsupported("a recursive copy that fails part-way")
        try:
            dp, dn, d, _s = self.walk(dst, False, False)
        except FSErr:
            return fail()
        if dn in (".", ".."):
            if not (s.kind == "d" and d is not None and d.kind == "d"):
                raise Unsupported(dst)
            dp, dn = d.parent, next(n for n, c in d.parent.ents.items() if c is d)
        self.inside(dp)
        if d is s:
            if s.kind == "d":
                raise Unsupported("cp -r a directory onto itself")
            return fail()                            # "are the same file"
        if s.kind == "l":                            # -r / -a: the symlink itself, its text unchanged
            if d is not None:
                if d.kind == "d":
                    return fail()
                try:
                    if self.stat(spath) is d or (d.kind == "l" and self.stat(dst) is self.stat(spath)):
                        raise Unsupported("cp -r a symlink onto the file it points to")
                except FSErr:
                    pass
                if not dp.mode & 0o200:
                    return fail()
                self._del(dp, dn)
            elif not dp.mode & 0o200:
                return fail()
            self._put(dp, dn, self._new("l", 0o777, target=s.target))
            return True
        if s.kind == "f":
            if not s.mode & 0o400:
                return fail()
            via_link = d is not None and d.kind == "l"
            if via_link:                             # writes through a symlink; not through a dangling one
                try:
                    t = self.walk(dst, True)[2]
                except FSErr:
                    return fail()
                if t is None:
                    return fail()
                d = t
            if d is not None:
                if d.kind == "d" or d is s:
                    return fail()
                if arch and self.nlink(s) > 1:
                    raise Unsupported("cp -a of a hard-linked file onto an existing file")
                if arch and self.nlink(d) > 1:       # -a (--preserve=links) unlinks a destination with other names first
                    if via_link:
                        raise Unsupported("cp -a through a symlink onto a hard-linked file")
                    if not dp.mode & 0o200:
                        return fail()
                    self._del(dp, dn)
                    node = self._new("f", s.mode, data=s.data)
                    self._put(dp, dn, node)
                    self._hl[s.ino] = node
                    return True
                if not d.mode & 0o200:
                    if not force or not top:
                        return fail()
                    if via_link or arch:
                        raise Unsupported("cp -f through a symlink / with -a")
                    if not dp.mode & 0o200:
                        return False
                    self._del(dp, dn)            # -f: the unwritable file is unlinked and a new one made
                    self._put(dp, dn, self._new("f", s.mode & ~UMASK, data=s.data))
                    return True
                if d.data != s.data:
                    self._set(d, "data", s.data)
                if arch and d.mode != s.mode:
                    self._set(d, "mode", s.mode)
                return True
            if not dp.mode & 0o200:
                return fail()
            if arch and s.ino in self._hl:
                self._put(dp, dn, self._hl[s.ino])
                return True
            node = self._new("f", s.mode if arch else s.mode & ~UMASK, data=s.data)
            self._put(dp, dn, node)
            if arch:
                self._hl[s.ino] = node
            return True
        x = dp                                       # a directory: never into itself
        while True:
            if x is s:
                raise Unsupported("copy a directory into itself")
            if x is x.parent:
                break
            x = x.parent
        if d is not None and d.kind == "l":
            raise Unsupported("cp -r a directory onto a symlink")
        if d is not None and d.kind != "d":
            return fail()
        if d is None:
            if not dp.mode & 0o200:
                return fail()
            d = self._new("d", 0o700)
            self._put(dp, dn, d)
            final = s.mode if arch else s.mode & ~UMASK
        else:
            if not d.mode & 0o200:
                raise Unsupported("cp -r into a read-only directory")
            final = s.mode if arch else d.mode
        if any(x is s for x in self._stack):
            raise Unsupported("a directory cycle (cp -L through a symlink to an ancestor)")
        self._stack.append(s)
        ok = True
        for name in sorted(s.ents):
            c, cpath = s.ents[name], spath.rstrip("/") + "/" + name
            if deref and c.kind == "l":              # -L: what the symlink points to; a dangling one is an error
                try:
                    c = self.stat(cpath)
                except FSErr:
                    ok = False
                    continue
            ok = self._copy(c, cpath, dst.rstrip("/") + "/" + name, arch, False, False, deref) and ok
        self._stack.pop()
        if d.mode != final:
            self._set(d, "mode", final)
        return ok

    def mv(self, opts: str, args: list[str]) -> bool:
        pairs = self._targets(args[:-1], args[-1], "T" in opts, is_cp=False)
        if pairs is None:
            return False
        ok = True
        for src, target in pairs:
            ok = self._mv1(src, target) and ok
        return ok

    def _mv1(self, src: str, target: str) -> bool:
        try:
            sp, sn, s, _s = self.walk(src, False, False)
        except FSErr:
            return False
        if s is None:
            return False
        if sn in (".", ".."):
            raise Unsupported(src)
        if src.endswith("/") and s.kind != "d":      # 'mv link/ x': Not a directory
            return False
        try:
            dp, dn, d, dslash = self.walk(target, False, False)
        except FSErr:
            return False
        if dn in (".", ".."):
            raise Unsupported(target)
        self.inside(sp)
        self.inside(dp)
        if d is not None:                            # "are the same file"
            if d is s:
                return False
            if d.kind != "l" and s.kind == "l":
                try:
                    t = self.stat(src)
                except FSErr:
                    t = None
                if t is d:
                    if self.nlink(d) > 1:
                        raise Unsupported("mv a symlink onto its hard-linked referent")
                    return False
        if dslash and s.kind != "d":
            return False
        if s.kind == "d":
            x = dp
            while True:
                if x is s:
                    return False                     # into a subdirectory of itself
                if x is x.parent:
                    break
                x = x.parent
            if d is not None and (d.kind != "d" or d.ents):
                return False
        elif d is not None and d.kind == "d":
            return False
        if not sp.mode & 0o200 or not dp.mode & 0o200:
            return False
        if s.kind == "d" and sp is not dp and not s.mode & 0o200:
            return False                             # a directory moving to a new parent must be writable (its '..')
        if d is not None:
            self._del(dp, dn)
        self._del(sp, sn)
        self._put(dp, dn, s)
        return True

    def chmod(self, mode: str, args: list[str]) -> bool:
        ok = True
        for a in args:
            try:
                node = self.stat(a)
            except FSErr:
                ok = False
                continue
            if re.fullmatch(r"[0-7]{3}", mode):
                m = int(mode, 8)
            elif mode == "a-w":
                m = node.mode & ~0o222
            elif mode == "u+w":
                m = node.mode | 0o200
            else:
                raise Unsupported(mode)
            if node.kind == "d" and (m & 0o500) != 0o500:
                raise Unsupported("a directory the owner cannot list")
            if m != node.mode:
                self._set(node, "mode", m)
        return ok

    def cd(self, path: str) -> bool:
        """bash's logical cd: $PWD/path with '.' and 'x/..' removed as text, then chdir to that."""
        parts = [] if path.startswith("/") else [c for c in self.pwd.split("/") if c]
        for c in path.split("/"):
            if c in ("", "."):
                continue
            if c == "..":
                if parts:
                    try:
                        if self.stat("/" + "/".join(parts)).kind != "d":
                            raise Unsupported("cd .. past a non-directory")
                    except FSErr:
                        raise Unsupported("cd .. past a missing directory") from None
                    parts.pop()
                continue
            parts.append(c)
        tdir = "/" + "/".join(parts)
        try:
            node = self.stat(tdir)
        except FSErr:
            node = None
        if node is None or node.kind != "d":
            try:
                if self.stat(path).kind == "d":
                    raise Unsupported("cd falls back to the physical path")
            except FSErr:
                pass
            return False
        self._setcwd(node, tdir)
        return True

    # ---- after the script: the tree and the questions
    def listing(self) -> list[str]:
        out = []

        def rec(d: Node, pre: str) -> None:
            for name in sorted(d.ents):
                n = d.ents[name]
                p = pre + name
                if n.kind == "d":
                    out.append(p + "/")
                    rec(n, p + "/")
                elif n.kind == "l":
                    out.append(f"{p} -> {n.target}")
                else:
                    out.append(f"{p} = {content(n.data)}")
        rec(self.top, "./")
        return out

    def _at_top(self, fn, path: str):
        cwd, self.cwd = self.cwd, self.top
        try:
            return fn(path)
        finally:
            self.cwd = cwd

    def q_cat(self, path: str) -> str:
        def f(p):
            try:
                n = self.stat(p)
            except FSErr:
                return "ERROR"
            return content(n.data) if n.kind == "f" and n.mode & 0o400 else "ERROR"
        return self._at_top(f, path)

    def q_ls(self, path: str) -> str:
        def f(p):
            try:
                n = self.stat(p)
            except FSErr:
                return "ERROR"
            if n.kind != "d":
                raise Unsupported("ls of a file")
            return " ".join(sorted(n.ents)) or "(empty)"
        return self._at_top(f, path)

    def q_readlink(self, path: str) -> str:
        """readlink -f: every symlink followed; every component but the last must exist."""
        rname = [] if path.startswith("/") else [c for c in ROOT.split("/") if c]
        rest = [c for c in path.split("/") if c]
        links = 0
        while rest:
            c = rest.pop(0)
            if c == ".":
                continue
            if c == "..":
                if rname:
                    rname.pop()
                continue
            node = self.root
            for x in rname:
                node = node.ents[x]
            if node.kind != "d":
                return "ERROR"
            nxt = node.ents.get(c)
            if nxt is None:
                if any(x not in (".",) for x in rest):
                    return "ERROR"
                rname.append(c)
                break
            if nxt.kind == "l":
                links += 1
                if links > MAXLINK:
                    return "ERROR"
                if nxt.target.startswith("/"):
                    rname = []
                rest = [x for x in nxt.target.split("/") if x] + rest
                continue
            if rest and nxt.kind != "d":
                return "ERROR"
            rname.append(c)
        return "/" + "/".join(rname)


def replay(setup: list[str], cmds: list[str]) -> tuple[FS, list[bool]]:
    """The emulator's run of a generated item: the starting tree, then the numbered commands."""
    fs = FS()
    for c in setup:
        if not fs.run(c):
            raise AssertionError(f"setup command failed: {c}")
    st = []
    for c in cmds:
        st.append(fs.run(c))
        fs.validate()
    fs.log.clear()
    return fs, st


# ---- the generator ----------------------------------------------------------------------------------------------------

_DIRS = ["src", "docs", "conf", "data", "logs", "lib", "assets", "notes", "archive", "cache", "build", "tests", "tools",
         "vendor", "site", "media", "bin", "etc", "old", "tmp", "backup", "shared", "pub", "drafts", "out", "inbox", "www",
         "jobs", "keys", "db", "img", "css", "api", "app", "srv", "spool", "stage", "prod", "misc", "attic"]
_FILES = ["readme", "notes.txt", "todo", "app.conf", "main.c", "util.py", "index.html", "run.sh", "data.csv", "log.1",
          "list", "config.ini", "version", "hosts", "motd", "report.md", "plan.txt", "keys.txt", "a.txt", "b.txt", "c.txt",
          "cron", "env", "setup.cfg", "draft.md", "old.log", "new.log", "cache.db", "id", "style.css", "app.js", "users",
          "groups", "stats", "summary", "changes", "license", "authors", "manifest", "state"]
_LINKS = ["current", "latest", "cfg", "link", "ptr", "alias", "here", "there", "prev", "next", "stable", "live", "active",
          "last", "cur", "ref", "mirror", "short", "front", "home"]
_WORDS = """amber basil cedar delta ember fjord gamma hazel iris jade kiwi lemon maple nova olive pearl quartz raven sage
tulip umber violet willow xenon yarrow zinc acorn birch coral dune elm fern grape heron ivy juniper kelp lotus moss nectar
onyx plum quince reed slate thyme urchin vale wren yew zest apple berry clove daisy eagle flint garnet honey indigo jasper
koala lilac mango nutmeg otter poppy quail ruby saffron topaz vanilla walnut zebra anchor bison cobalt dingo egret falcon
gecko hyena ibis jackal kestrel lynx marten newt osprey panda robin salmon tapir viper walrus badger cricket dolphin ferret
gopher hornet iguana krill lemur magpie narwhal ocelot pelican rook stork toucan vulture wombat alder aspen beech cypress
hemlock larch linden myrtle oak pine poplar rowan spruce sumac teak yucca""".split()

_LV = {   # commands, trap operations, questions about paths, starting tree (dirs, files, symlinks, hard links), tree cap
    1: ((5, 7), 1, 1, (3, 4, 0, 0), 24), 2: ((6, 8), 1, 1, (3, 4, 1, 0), 24), 3: ((8, 10), 1, 2, (4, 5, 1, 0), 28),
    4: ((15, 17), 4, 2, (4, 6, 1, 0), 36), 5: ((18, 21), 5, 2, (5, 6, 1, 0), 40), 6: ((22, 25), 6, 3, (5, 7, 2, 1), 44),
    7: ((30, 38), 12, 3, (6, 8, 2, 1), 52), 8: ((40, 50), 16, 4, (7, 9, 3, 1), 58), 9: ((85, 100), 40, 7, (10, 13, 4, 2), 90),
    10: ((170, 200), 80, 9, (12, 16, 5, 2), 130)}
_P1 = ["cp_r_into", "mv_into", "mkdir_exists", "rmdir"]
_P2 = ["mv_over", "ln_s_rel", "write_link", "rm_r_link"]
_P3 = ["hardlink", "cp_follow", "cat_missing", "ln_s_into", "cp_r_link", "ln_sf_dir"]
_P4 = _P1 + _P2 + ["mv_collide", "cp_r_new"]
_P5 = _P4 + ["cp_follow", "cat_missing", "ln_s_into", "ln_s_exists", "touch_link", "cp_dangling"]
_P6 = _P5 + ["hardlink", "cp_r_link", "cd", "mv_self", "cp_same"]
_P7 = _P6 + ["ln_sf_dir", "ln_hard_symlink", "cp_r_tree", "cp_a_tree", "mv_link", "cp_through", "rm_link_slash",
             "glob_cp", "glob_rm", "multi", "cat_self", "ln_f", "cd_session"]
_P9 = _P7 + ["chmod_dir", "chmod_file", "ln_sfn", "mv_link_slash", "rmdir_link", "glob_mv", "glob_rmdir", "glob_chmod",
             "cp_f", "ln_sr", "rmdir_p", "cp_T", "mv_T", "rm_dot", "cp_L", "chmod_dir_mv", "rmdir_ro", "cd_session"]
_POOLS = {1: _P1, 2: _P2, 3: _P3, 4: _P4, 5: _P5, 6: _P6, 7: _P7, 8: _P7, 9: _P9, 10: _P9}
_FILLER = {"mkdir": 1.0, "write": 2.0, "append": 2.0, "overwrite": 1.0, "touch": 0.5, "cp": 1.5, "mv": 1.0, "rm": 0.7}


class _Gen:
    def __init__(self, r, level: int):
        self.r, self.level = r, level
        self.fs = FS()
        self.setup: list[str] = []
        self.cmds: list[str] = []
        self.status: list[bool] = []
        self.focus: list[str] = []
        self.words = list(_WORDS)
        r.shuffle(self.words)
        self.used: set[str] = set()
        self.hard_note = ""
        self.cap = _LV[level][4]
        self.limit = _LV[level][0][1]     # the level's longest script
        self.touch: dict[int, int] = {}   # inode -> how many commands changed it (levels 9-10 ask about the busiest)

    # ---- plumbing
    def word(self) -> str:
        return self.words.pop() if self.words else f"w{self.r.randint(100, 999)}"

    def pick(self, xs):
        return self.r.choice(xs) if xs else None

    def here(self) -> list[str]:
        """The shell's physical directory, as components below the top."""
        return [c for c in self.fs.cwd_path()[len(ROOT):].split("/") if c]

    def R(self, p: str) -> str:
        """A path given from the top, written relative to the shell's physical directory (the kernel resolves '..'
        physically); a trailing '/', '/.' or glob component stays as it is."""
        cwd = self.here()
        if not cwd or p.startswith("/"):
            return p
        trail = "/" if p.endswith("/") and p.strip("/") else ""
        parts = [c for c in p.split("/") if c]
        if parts and parts[0] == ".":
            parts.pop(0)
        tail = [parts.pop()] if parts and (parts[-1] == "." or "*" in parts[-1]) else []
        k = 0
        while k < min(len(cwd), len(parts)) and cwd[k] == parts[k]:
            k += 1
        return "/".join([".."] * (len(cwd) - k) + parts[k:] + tail) + trail or "."

    def render(self, cmd: str) -> str:
        """The command with its paths relative to the shell's directory (symlink texts and cd arguments as they are)."""
        if not self.here():
            return cmd
        t = cmd.split()
        name = t[0]
        if name == "cd":
            return cmd
        if name in ("echo", "cat"):
            if name == "cat":
                t[1] = self.R(t[1])
            t[3] = self.R(t[3])
            return " ".join(t)
        sym = name == "ln" and any(x.startswith("-") and "s" in x and "r" not in x for x in t[1:])
        out, k = [name], 0
        for x in t[1:]:
            if x.startswith("-"):
                out.append(x)
                continue
            out.append(x if k == 0 and (name == "chmod" or sym) else self.R(x))
            k += 1
        return " ".join(out)

    def do(self, cmd: str, ok: bool | None = None, raw: bool = False) -> bool:
        """Run one command on the emulator and keep it, unless it is Unsupported (or its exit status is not `ok`).
        Paths are given from the top and written relative to the shell's directory (raw: exactly as given)."""
        fs = self.fs
        if len(self.cmds) >= self.limit:
            return False
        if not raw:
            cmd = self.render(cmd)
        mark = len(fs.log)
        try:
            res = fs.run(cmd)
            fs.validate()
            if ok is not None and res != ok:
                raise Unsupported("not the wanted outcome")
        except Unsupported:
            fs.rollback(mark)
            return False
        for e in {id(x[1] if x[0] == "attr" else x[4]): x for x in fs.log[mark:] if x[0] == "attr" or (x[0] == "ent" and x[4])}.values():
            node = e[1] if e[0] == "attr" else e[4]
            self.touch[node.ino] = self.touch.get(node.ino, 0) + 1
        self.cmds.append(cmd)
        self.status.append(res)
        return True

    def seq(self, cmds: list[tuple]) -> bool:
        """Several commands kept together or not at all: (command, wanted status[, raw])."""
        mark, n = len(self.fs.log), len(self.cmds)
        for c in cmds:
            if not self.do(*c):
                self.fs.rollback(mark)
                del self.cmds[n:], self.status[n:]
                return False
        return True

    def ents(self) -> list[tuple[str, Node]]:
        out = []

        def rec(d: Node, pre: str) -> None:
            for name in sorted(d.ents):
                n = d.ents[name]
                out.append((pre + name, n))
                if n.kind == "d":
                    rec(n, pre + name + "/")
        rec(self.fs.top, "")
        return out

    def res(self, p: str) -> Node | None:
        """What a path from the top resolves to (following symlinks)."""
        if not p.strip("/"):
            return self.fs.top
        try:
            return self.fs.walk(p, True, True, base=self.fs.top)[2]
        except (FSErr, Unsupported):
            return None

    def lres(self, p: str) -> Node | None:
        try:
            return self.fs.walk(p, False, False, base=self.fs.top)[2]
        except (FSErr, Unsupported):
            return None

    def files(self) -> list[str]:
        return [p for p, n in self.ents() if n.kind == "f"]

    def dirs(self, writable: bool = False) -> list[str]:
        return [p for p, n in self.ents() if n.kind == "d" and (not writable or n.mode & 0o200)]

    def wdirs(self) -> list[str]:
        return [""] + self.dirs(writable=True)

    def links(self, to: str | None = None) -> list[str]:
        """Symlinks; to='f' / 'd' those that resolve to a file / directory, 'x' the dangling ones."""
        out = []
        for p, n in self.ents():
            if n.kind != "l":
                continue
            t = self.res(p)
            k = "x" if t is None else t.kind
            if to is None or to == k:
                out.append(p)
        return out

    def size(self, p: str = "") -> int:
        return sum(1 for q, _n in self.ents() if not p or q.startswith(p + "/"))

    def fresh(self, kind: str, par: str | None, reuse: float = 0.0) -> str:
        pool = _DIRS if kind == "d" else _FILES if kind == "f" else _LINKS
        taken: set = set()
        if par is not None:
            d = self.res(par) if par else self.fs.top
            taken = set(d.ents) if d is not None and d.kind == "d" else set()
        cands = [x for x in pool if x not in taken and x not in self.used]
        if reuse and self.r.random() < reuse:
            cands = [x for x in pool if x not in taken and x in self.used] or cands
        if not cands:
            cands = [f"{x}{k}" for k in (2, 3, 4) for x in pool if f"{x}{k}" not in taken and f"{x}{k}" not in self.used]
        name = self.r.choice(cands)
        self.used.add(name)
        return name

    @staticmethod
    def up(frm: str, to: str) -> str:
        """The relative symlink text that leads from directory `frm` to path `to` (both from the top)."""
        return "../" * len([c for c in frm.split("/") if c]) + to

    @staticmethod
    def dirof(p: str) -> str:
        return p.rsplit("/", 1)[0] if "/" in p else ""

    # ---- the starting tree
    def build(self) -> None:
        nd, nf, nl, nh = _LV[self.level][3]
        r = self.r
        dirs: list[str] = []
        for i in range(nd):
            par = "" if i < 2 or r.random() < 0.4 else r.choice([d for d in dirs if d.count("/") < 2])
            dirs.append(_join(par, self.fresh("d", par)))
        cmds = [f"mkdir -p {d}" for d in dirs if not any(e.startswith(d + "/") for e in dirs)]
        for c in cmds:
            assert self.fs.run(c), c
        files: list[str] = []
        for i in range(nf):
            par = r.choice([""] + dirs) if i else ""
            f = _join(par, self.fresh("f", par, reuse=0.25))
            files.append(f)
            cmds.append(f"echo {self.word()} > {f}")
            assert self.fs.run(cmds[-1]), cmds[-1]
            for _ in range(r.choice([1, 1, 2]) if self.level >= 9 and r.random() < 0.6 else 1 if r.random() < 0.3 else 0):
                cmds.append(f"echo {self.word()} >> {f}")
                assert self.fs.run(cmds[-1]), cmds[-1]
        self.setup += cmds
        for i in range(nl):   # a symlink to a directory, one to a file; relative and correct at the start
            if i % 2 == 0:
                t = r.choice([d for d in dirs if "/" in d] or dirs)
                where = r.choice(["", ""] + [d for d in dirs if "/" not in d and not t.startswith(d)])
            else:
                t = r.choice(files)
                where = r.choice([""] + [d for d in dirs if d != self.dirof(t)])
            name = _join(where, self.fresh("l", where))
            text = ROOT + "/" + t if self.level >= 7 and i == 2 else self.up(where, t)
            c = f"ln -s {text} {name}"
            assert self.fs.run(c), c
            self.setup.append(c)
        for _ in range(nh):
            f = r.choice(files)
            par = r.choice([d for d in dirs if d != self.dirof(f)] or [""])
            h = _join(par, self.fresh("f", par))
            c = f"ln {f} {h}"
            assert self.fs.run(c), c
            self.setup.append(c)
            self.hard_note += f"`./{f}` and `./{h}` are hard links to the same file.\n"
        self.fs.log.clear()

    # ---- filler: ordinary work between the traps
    def f_mkdir(self) -> bool:
        par = self.pick(self.wdirs())
        if self.r.random() < 0.3:
            chain = [self.fresh("d", par)] + [self.fresh("d", None) for _ in range(self.r.randint(1, 2))]
            return self.do(f"mkdir -p {_join(par, '/'.join(chain))}", True)
        return self.do(f"mkdir {_join(par, self.fresh('d', par))}", True)

    def f_write(self) -> bool:
        par = self.pick(self.wdirs())
        return self.do(f"echo {self.word()} > {_join(par, self.fresh('f', par, 0.2))}", True)

    def f_append(self) -> bool:
        t = self.pick(self.files() + self.links("f"))
        return t is not None and self.do(f"echo {self.word()} >> {t}")

    def f_overwrite(self) -> bool:
        t = self.pick(self.files())
        return t is not None and self.do(f"echo {self.word()} > {t}")

    def f_touch(self) -> bool:
        par = self.pick(self.wdirs())
        return self.do(f"touch {_join(par, self.fresh('f', par))}", True)

    def f_cp(self) -> bool:
        s = self.pick(self.files())
        if s is None:
            return False
        if self.r.random() < 0.4:
            d = self.pick([x for x in self.dirs(True) if x != self.dirof(s) and self.lres(_join(x, _base(s))) is None])
            if d:
                return self.do(f"cp {s} {d}", True)
        par = self.pick(self.wdirs())
        return self.do(f"cp {s} {_join(par, self.fresh('f', par, 0.2))}", True)

    def f_mv(self) -> bool:
        s = self.pick(self.files())
        if s is None:
            return False
        par = self.pick(self.wdirs())
        return self.do(f"mv {s} {_join(par, self.fresh('f', par, 0.2))}", True)

    def f_rm(self) -> bool:
        t = self.pick(self.files() + self.links())
        return t is not None and self.do(f"rm {t}", True)

    def f_prune(self) -> bool:
        d = self.pick([d for d in self.dirs() if 2 <= self.size(d) <= 12])
        return d is not None and self.do(f"rm -r {d}", True)

    # ---- traps
    def t_cp_r_into(self) -> bool:
        ds = self.dirs()
        c = [(a, b) for a in ds for b in self.dirs(True) if a != b and not b.startswith(a + "/") and not a.startswith(b + "/")
             and 1 <= self.size(a) <= 6 and self.lres(_join(b, _base(a))) is None]
        if not c:
            return False
        a, b = self.r.choice(c)
        kids = [q for q, _n in self.ents() if q.startswith(a + "/") and q.count("/") == a.count("/") + 1]
        self.focus += [_join(b, _base(a))] + ([_join(b, _base(self.r.choice(kids)))] if kids else [])
        return self.do(f"cp -r {a} {b}", True)

    def t_cp_r_new(self) -> bool:
        a = self.pick([d for d in self.dirs() if 1 <= self.size(d) <= 6])
        if a is None:
            return False
        par = self.pick([x for x in self.wdirs() if not (x + "/").startswith(a + "/")])
        new = _join(par, self.fresh("d", par))
        self.focus.append(new)
        return self.do(f"cp -r {a} {new}", True)

    def t_mv_into(self) -> bool:
        ds = self.dirs(True) + self.links("d")
        c = [(x, d) for x in self.files() + self.dirs() for d in ds if x != d and not d.startswith(x + "/")
             and self.dirof(x) != d and self.lres(_join(d, _base(x))) is None]
        if not c:
            return False
        x, d = self.r.choice(c)
        self.focus += [_join(d, _base(x)), x]
        return self.do(f"mv {x} {d}")

    def t_mv_over(self) -> bool:
        fs_ = self.files()
        c = [(a, b) for a in fs_ for b in fs_ if a != b and self.res(a) is not self.res(b)]
        if not c:
            return False
        a, b = self.r.choice(c)
        self.focus += [b, a]
        return self.do(f"mv {a} {b}", True)

    def t_mv_collide(self) -> bool:
        ds = self.dirs()
        c = []
        for a in ds:
            for b in self.dirs(True):
                if a != b and not b.startswith(a + "/"):
                    n = self.lres(_join(b, _base(a)))
                    if n is not None and n.kind == "d" and n is not self.res(a):
                        c.append((a, b))
        if c:
            a, b = self.r.choice(c)
            self.focus += [_join(b, _base(a)), a]
            return self.do(f"mv {a} {b}/")
        c = [(a, b) for a in ds for b in self.dirs(True) if a != b and not b.startswith(a + "/") and not a.startswith(b + "/")
             and 1 <= self.size(a) <= 5 and self.lres(_join(b, _base(a))) is None]
        if not c:
            return False
        a, b = self.r.choice(c)
        self.focus += [_join(b, _base(a)), a]
        return self.seq([(f"cp -r {a} {b}", True), (f"echo {self.word()} > {_join(b, _base(a))}/{self.fresh('f', None)}", True),
                         (f"mv {a} {b}/", False)])

    def t_mkdir_exists(self) -> bool:
        v = self.r.randrange(3)
        if v == 0:
            d = self.pick(self.dirs())
            if d:
                self.focus.append(d)
                return self.do(f"mkdir {d}", False)
        if v == 1:
            f = self.pick(self.files())
            if f:
                self.focus.append(f)
                return self.do(f"mkdir -p {f}/{self.fresh('d', None)}", False)
        par = self.pick(self.wdirs())
        a, b = self.fresh("d", par), self.fresh("d", None)
        self.focus.append(_join(par, a))
        return self.do(f"mkdir {_join(par, a)}/{b}", False)

    def t_rmdir(self) -> bool:
        ne = [d for d in self.dirs() if self.size(d) > 0]
        if ne and self.r.random() < 0.3:
            d = self.r.choice(ne)
            self.focus.append(d)
            return self.do(f"rm {d}", False)
        if ne and self.r.random() < 0.7:
            d = self.r.choice(ne)
            self.focus.append(d)
            return self.do(f"rmdir {d}", False)
        d = self.pick([d for d in self.dirs() if self.size(d) == 0])
        if d is None:
            return False
        self.focus.append(d)
        return self.do(f"rmdir {d}", True)

    def t_ln_s_rel(self) -> bool:
        f = self.pick(self.files())
        if f is None:
            return False
        y = self.pick([d for d in self.dirs(True) if d != self.dirof(f)])
        if y is None:
            return False
        name = _join(y, self.fresh("l", y))
        text = f if self.r.random() < 0.7 else self.up(y, f)
        self.focus.append(name)
        return self.do(f"ln -s {text} {name}", True)

    def t_ln_s_into(self) -> bool:
        d = self.pick(self.dirs(True))
        if d is None:
            return False
        t = self.pick([x for x in self.files() + self.dirs() if not (x + "/").startswith(d + "/") and not d.startswith(x + "/")])
        if t is None:
            return False
        text = self.up(d, t) if self.r.random() < 0.5 else t
        if self.lres(_join(d, _base(text))) is not None:
            return False
        self.focus.append(_join(d, _base(text)))
        return self.do(f"ln -s {text} {d}", True)

    def t_ln_s_exists(self) -> bool:
        t = self.pick(self.files() + self.links())
        s = self.pick(self.files())
        if t is None or s is None:
            return False
        self.focus.append(t)
        return self.do(f"ln -s {s} {t}", False)

    def _dirlink(self) -> tuple[str | None, list]:
        """A symlink to a directory, or the command that makes one."""
        L = self.pick(self.links("d"))
        if L:
            return L, []
        d = self.pick([x for x in self.dirs(True) if "/" in x] or self.dirs(True))
        if d is None:
            return None, []
        name = self.fresh("l", "")
        return name, [(f"ln -s {d} {name}", True)]

    def t_ln_sf_dir(self) -> bool:
        L, pre = self._dirlink()
        t = self.pick(self.files() + self.dirs())
        if L is None or t is None:
            return False
        self.focus += [L, f"{L}/{_base(t)}"]
        return self.seq(pre + [(f"ln -sf {t} {L}", None)])

    def t_ln_sfn(self) -> bool:
        L, pre = self._dirlink()
        t = self.pick(self.dirs() + self.files())
        if L is None or t is None:
            return False
        self.focus.append(L)
        return self.seq(pre + [(f"ln -sfn {self.up(self.dirof(L), t)} {L}", True)])

    def t_write_link(self) -> bool:
        if self.r.random() < 0.5:
            L = self.pick(self.links("f"))
            if L:
                self.focus.append(L)
                return self.do(f"echo {self.word()} >> {L}")
        L = self.pick(self.links("x"))
        pre = []
        if L is None:
            par = self.pick(self.wdirs())
            L = _join(par, self.fresh("l", par))
            pre = [(f"ln -s {self.fresh('f', None, 0.3)} {L}", True)]
        self.focus.append(L)
        return self.seq(pre + [(f"echo {self.word()} > {L}", None)])

    def t_touch_link(self) -> bool:
        L = self.pick(self.links("x"))
        pre = []
        if L is None:
            par = self.pick(self.wdirs())
            L = _join(par, self.fresh("l", par))
            pre = [(f"ln -s {self.fresh('f', None)} {L}", True)]
        self.focus.append(L)
        return self.seq(pre + [(f"touch {L}", None)])

    def t_rm_r_link(self) -> bool:
        L, pre = self._dirlink()
        if L is None:
            return False
        tgt = pre[0][0].split()[2] if pre else self.fs.q_readlink(L)[len(ROOT) + 1:]
        self.focus += [L, tgt]
        return self.seq(pre + [(f"rm -r {L}", True)])

    def t_rm_link_slash(self) -> bool:
        L, pre = self._dirlink()
        if L is None:
            return False
        tgt = pre[0][0].split()[2] if pre else self.fs.q_readlink(L)[len(ROOT) + 1:]
        self.focus += [L, tgt]
        return self.seq(pre + [(f"rm {'-r ' if self.r.random() < 0.5 else ''}{L}/", False)])

    def t_rmdir_link(self) -> bool:
        L, pre = self._dirlink()
        if L is None:
            return False
        self.focus.append(L)
        return self.seq(pre + [(f"rmdir {L}", False)])

    def t_mv_link_slash(self) -> bool:
        L, pre = self._dirlink()
        if L is None:
            return False
        par = self.pick(self.wdirs())
        new = _join(par, self.fresh("d", par))
        self.focus += [L, new]
        return self.seq(pre + [(f"mv {L}/ {new}", False)])

    def t_cp_follow(self) -> bool:
        L = self.pick(self.links("f"))
        pre = []
        if L is None:
            f = self.pick(self.files())
            if f is None:
                return False
            L = self.fresh("l", "")
            pre = [(f"ln -s {f} {L}", True)]
        par = self.pick(self.wdirs())
        new = _join(par, self.fresh("f", par))
        self.focus.append(new)
        return self.seq(pre + [(f"cp {L} {new}", True)])

    def t_cp_r_link(self) -> bool:
        L = self.pick(self.links())
        if L is None:
            return False
        par = self.pick([x for x in self.wdirs() if x != self.dirof(L)] or [""])
        if self.r.random() < 0.5 or not par:
            new = _join(par, self.fresh("l", par))
            self.focus.append(new)
            return self.do(f"cp -{self.r.choice(['r', 'a', 'R'])} {L} {new}", True)
        if self.lres(_join(par, _base(L))) is not None:
            return False
        self.focus.append(_join(par, _base(L)))
        return self.do(f"cp -r {L} {par}", True)

    def t_cp_r_tree(self) -> bool:
        lk = self.links()
        a = self.pick([d for d in self.dirs() if 1 <= self.size(d) <= 7 and any(q.startswith(d + "/") for q in lk)])
        if a is None:
            return False
        par = self.pick([x for x in self.wdirs() if not (x + "/").startswith(a + "/")])
        new = _join(par, self.fresh("d", par))
        self.focus += [new + q[len(a):] for q in lk if q.startswith(a + "/")]
        return self.do(f"cp {self.r.choice(['-r', '-R', '-a'])} {a} {new}", True)

    def t_cp_a_tree(self) -> bool:
        a = self.pick([d for d in self.dirs() if 1 <= self.size(d) <= 7])
        if a is None:
            return False
        ins = [q for q in self.files() if q.startswith(a + "/")]
        pre, post = [], []
        par = self.pick([x for x in self.wdirs() if not (x + "/").startswith(a + "/")])
        new = _join(par, self.fresh("d", par))
        if ins and self.r.random() < 0.7:
            f = self.r.choice(ins)
            h = f"{a}/{self.fresh('f', a)}"
            pre = [(f"ln {f} {h}", True)]
            post = [(f"echo {self.word()} >> {new}{h[len(a):]}", True)]
            self.focus += [new + f[len(a):], new + h[len(a):]]
        return self.seq(pre + [(f"cp {self.r.choice(['-a', '-a', '-r'])} {a} {new}", True)] + post)

    def t_cp_dangling(self) -> bool:
        L = self.pick(self.links("x"))
        pre = []
        if L is None:
            par = self.pick(self.wdirs())
            L = _join(par, self.fresh("l", par))
            pre = [(f"ln -s {self.fresh('f', None)} {L}", True)]
        f = self.pick(self.files())
        if f is None:
            return False
        self.focus.append(L)
        return self.seq(pre + [(f"cp {f} {L}", False)])

    def _hard_groups(self) -> list[list[str]]:
        groups: dict = {}
        for p, n in self.ents():
            if n.kind == "f":
                groups.setdefault(n.ino, []).append(p)
        return [g for g in groups.values() if len(g) > 1]

    def t_cp_same(self) -> bool:
        c = [(a, b) for g in self._hard_groups() for a in g for b in g if a != b]
        c += [(L, t) for L in self.links("f") for t in self.files() if self.res(L) is self.res(t)]
        if not c:
            return False
        a, b = self.r.choice(c)
        self.focus.append(b)
        return self.do(f"cp {a} {b}", False)

    def t_cp_through(self) -> bool:
        L = self.pick(self.links("f"))
        if L is None:
            return False
        f = self.pick([x for x in self.files() if self.res(x) is not self.res(L)])
        if f is None:
            return False
        self.focus.append(L)
        return self.do(f"cp {f} {L}")

    def t_hardlink(self) -> bool:
        f = self.pick(self.files())
        if f is None:
            return False
        par = self.pick([x for x in self.wdirs() if x != self.dirof(f)] or [""])
        h = _join(par, self.fresh("f", par, 0.3))
        steps = [(f"ln {f} {h}", True), (f"echo {self.word()} >> {self.r.choice([f, h])}", None)]
        v = self.r.randrange(3)
        if v == 0:
            steps.append((f"rm {f}", True))
        elif v == 1:
            g = self.pick([x for x in self.files() if x not in (f, h)])
            if g:
                steps.append((f"mv {g} {f}", True))
        self.focus += [h, f]
        return self.seq(steps)

    def t_ln_hard_symlink(self) -> bool:
        L = self.pick([x for x in self.links() if not self.lres(x).target.startswith("/")])
        if L is None:
            return False
        par = self.pick([x for x in self.wdirs() if x != self.dirof(L)])
        if par is None:
            return False
        h = _join(par, self.fresh("l", par))
        self.focus.append(h)
        return self.do(f"ln {L} {h}", True)

    def t_cat_missing(self) -> bool:
        b = self.pick([x for x in self.files() if self.res(x).data])
        if b is None:
            return False
        miss = self.pick([x for x in self.focus if self.lres(x) is None and self.res(self.dirof(x) or ".") is not None])
        if miss is None or self.r.random() < 0.3:
            miss = _join(self.pick(self.dirs() + [""]), self.fresh("f", None))
        self.focus.append(b)
        return self.do(f"cat {miss} > {b}", False)

    def t_mv_self(self) -> bool:
        c = [f"mv {a} {b}" for g in self._hard_groups() for a in g for b in g if a != b]
        f = self.pick(self.files())
        if f:
            c += [f"mv {f} ./{f}", f"mv {f} {f}"]
        c += [f"mv {L} {t}" for L in self.links("f") for t in self.files() if self.res(L) is self.res(t)]
        cmd = self.pick(c)
        if cmd is None:
            return False
        self.focus.append(cmd.split()[1])
        return self.do(cmd, False)

    def t_mv_link(self) -> bool:
        L = self.pick([x for x in self.links() if not self.lres(x).target.startswith("/")])
        if L is None:
            return False
        d = self.pick([x for x in self.dirs(True) if x != self.dirof(L) and self.lres(_join(x, _base(L))) is None])
        if d is None:
            return False
        self.focus.append(_join(d, _base(L)))
        return self.do(f"mv {L} {d}", True)

    def t_cd(self) -> bool:
        """cd into a symlinked directory, work relative to it (the kernel resolves '..' physically), cd .. back."""
        c = [L for L in self.links("d") if "/" not in L and self.fs.q_readlink(L).count("/") > ROOT.count("/") + 1]
        pre = []
        if c:
            L = self.r.choice(c)
            phys = self.fs.q_readlink(L)[len(ROOT) + 1:]
        else:
            phys = self.pick([x for x in self.dirs(True) if "/" in x])
            if phys is None:
                return False
            L = self.fresh("l", "")
            pre = [(f"ln -s {phys} {L}", True)]
        up = self.dirof(phys)             # the physical parent of the directory
        new = self.fresh("f", None)
        src = self.pick([x[len(up) + 1:] for x in self.files() if x.startswith(up + "/") and x.count("/") == up.count("/") + 1])
        v = self.r.randrange(3)
        if v == 0 and src:
            body = [(f"cp ../{src} {new}", True)]
            self.focus.append(f"{phys}/{new}")
        elif v == 1:
            body = [(f"ln -s ../{src or self.fresh('f', None)} {new}", True)]
            self.focus.append(f"{phys}/{new}")
        else:
            body = [(f"echo {self.word()} > ../{new}", True)]
            self.focus.append(f"{up}/{new}")
        if self.r.random() < 0.5:
            body.append((f"echo {self.word()} > {self.fresh('f', phys)}", True))
        if self.here():
            return False
        return self.seq(pre + [(f"cd {L}", True, True)] + [c + (True,) for c in body] + [("cd ..", True, True)])

    def t_chmod_dir(self) -> bool:
        fl = self.files()
        d = self.pick([x for x in self.dirs(True) if any(q.startswith(x + "/") for q in fl)])
        if d is None:
            return False
        f = self.r.choice([q for q in fl if q.startswith(d + "/")])
        probes = [(f"touch {d}/{self.fresh('f', d)}", False), (f"echo {self.word()} >> {f}", None), (f"rm {f}", False),
                  (f"mv {f} {self.fresh('f', '')}", False), (f"mkdir -p {d}", True)]
        self.r.shuffle(probes)
        self.focus += [f, d]
        return self.seq([(f"chmod {self.r.choice(['555', 'a-w'])} {d}", True)] + probes[:2])

    def t_chmod_file(self) -> bool:
        f = self.pick(self.files())
        if f is None:
            return False
        par = self.pick(self.wdirs())
        new = _join(par, self.fresh("f", par))
        steps = [(f"chmod {self.r.choice(['444', 'a-w'])} {f}", True), (f"echo {self.word()} >> {f}", False)]
        v = self.r.randrange(3)
        if v == 0:
            steps += [(f"cp {f} {new}", True), (f"echo {self.word()} > {new}", False)]
            self.focus.append(new)
        elif v == 1:
            g = self.pick([x for x in self.files() if x != f and self.res(x) is not self.res(f)])
            if g:
                steps += [(f"mv {g} {f}", True), (f"echo {self.word()} >> {f}", True)]
        else:
            steps += [(f"cp -a {f} {new}", True), (f"chmod u+w {f}", True)]
            self.focus.append(new)
        self.focus.append(f)
        return self.seq(steps)

    # ---- levels 7-10: globs, several sources, less common options, sessions in a symlinked directory
    def gl(self, pat: str) -> list[str]:
        """A glob's expansion from the top."""
        cwd, self.fs.cwd = self.fs.cwd, self.fs.top
        try:
            return self.fs.glob(pat)
        finally:
            self.fs.cwd = cwd

    def _dirs_with(self, lo: int, hi: int, sub: bool | None = None) -> list[str]:
        """Directories with lo..hi entries (sub: with / without a subdirectory among them)."""
        out = []
        for d in self.dirs():
            ents = self.res(d).ents
            if lo <= len(ents) <= hi and (sub is None or sub == any(c.kind == "d" for c in ents.values())):
                out.append(d)
        return out

    def _pattern(self, d: str) -> str:
        exts = sorted({n.rsplit(".", 1)[1] for n in self.res(d).ents if "." in n})
        return f"{d}/*.{self.r.choice(exts)}" if exts and self.r.random() < 0.4 else f"{d}/*"

    def _other_dir(self, d: str) -> str | None:
        return self.pick([x for x in self.dirs(True) if x != d and not (x + "/").startswith(d + "/") and not d.startswith(x + "/")])

    def t_glob_cp(self) -> bool:
        d = self.pick(self._dirs_with(2, 6))
        e = d and self._other_dir(d)
        if not e:
            return False
        pat = self._pattern(d)
        self.focus += [f"{e}/{x.split('/')[-1]}" for x in self.gl(pat)[:2]]
        return self.do(f"cp {self.r.choice(['', '', '-r '])}{pat} {e}")

    def t_glob_rm(self) -> bool:
        d = self.pick(self._dirs_with(2, 6))
        if d is None:
            return False
        pat = self._pattern(d)
        self.focus.append(d)
        return self.do(f"rm {self.r.choice(['', '', '-f ', '-r '])}{pat}")

    def t_glob_mv(self) -> bool:
        d = self.pick(self._dirs_with(2, 6))
        e = d and self._other_dir(d)
        if not e:
            return False
        pat = self._pattern(d)
        self.focus += [e, d]
        return self.do(f"mv {pat} {e}")

    def t_glob_rmdir(self) -> bool:
        d = self.pick(self._dirs_with(2, 6, sub=True))
        if d is None:
            return False
        self.focus.append(d)
        return self.do(f"rmdir {d}/*")

    def t_glob_chmod(self) -> bool:
        d = self.pick(self._dirs_with(2, 5))
        if d is None:
            return False
        pat = self._pattern(d)
        fl = [x for x in self.gl(pat) if (n := self.res(x)) is not None and n.kind == "f"]
        steps = [(f"chmod a-w {pat}", None)]
        if fl:
            f = self.r.choice(fl)
            steps.append((f"echo {self.word()} >> {f}", None))
            self.focus.append(f)
        return self.seq(steps)

    def t_multi(self) -> bool:
        fl = self.files()
        if len(fl) < 2:
            return False
        a, b = self.r.sample(fl, 2)
        d = self.pick([x for x in self.dirs(True) if self.lres(_join(x, _base(a))) is None and self.lres(_join(x, _base(b))) is None
                       and _base(a) != _base(b)])
        if d is None:
            return False
        miss = self.pick([x for x in self.focus if self.lres(x) is None]) or _join(self.dirof(a), self.fresh("f", None))
        v = self.r.randrange(4)
        self.focus += [_join(d, _base(a)), _join(d, _base(b))]
        if v == 0:
            return self.do(f"cp {a} {miss} {b} {d}")
        if v == 1:
            return self.do(f"mv {a} {miss} {b} {d}")
        if v == 2:
            return self.do(f"{self.r.choice(['cp', 'mv'])} {a} {b} {self.r.choice(fl)}")   # the last one is not a directory
        s = self.pick([x for x in self.dirs() if 1 <= self.size(x) <= 5 and not (d + "/").startswith(x + "/")])
        return s is not None and self.do(f"cp {self.r.choice(['', '-r '])}{s} {a} {d}")

    def _pair(self) -> tuple[list, str, str] | None:
        """Two names of one file: an existing hard link pair, or the command that makes one."""
        c = [(a, b) for g in self._hard_groups() for a in g for b in g if a != b]
        if c:
            a, b = self.r.choice(c)
            return [], a, b
        f = self.pick(self.files())
        if f is None:
            return None
        par = self.pick([x for x in self.wdirs() if x != self.dirof(f)] or [""])
        h = _join(par, self.fresh("f", par, 0.3))
        return [(f"ln {f} {h}", True)], f, h

    def t_cp_f(self) -> bool:
        pr = self._pair()
        g = self.pick(self.files())
        if pr is None or g is None:
            return False
        pre, a, b = pr
        if self.res(g) is self.res(a):
            return False
        self.focus += [a, b]
        return self.seq(pre + [(f"chmod {self.r.choice(['444', 'a-w'])} {b}", True), (f"cp -f {g} {b}", True),
                               (f"echo {self.word()} >> {a}", None)])

    def t_cat_self(self) -> bool:
        pr = self._pair()
        if pr is None:
            return False
        pre, a, b = pr
        self.focus += [a, b]
        if self.r.random() < 0.3:
            return self.seq(pre + [(f"cat {a} > {a}", True)])
        return self.seq(pre + [(f"cat {a} > {b}", True)])

    def t_ln_f(self) -> bool:
        fl = self.files()
        if len(fl) < 2:
            return False
        a, b = self.r.sample(fl, 2)
        self.focus += [a, b]
        return self.seq([(f"ln -f {a} {b}", None), (f"echo {self.word()} >> {b}", None)])

    def t_ln_sr(self) -> bool:
        cand = [f"{L}/{n}" for L in self.links("d") for n in sorted(self.res(L).ents)] + self.files()
        t = self.pick(cand)
        d = self.pick([x for x in self.dirs(True) if "/" in x] or self.dirs(True))
        if t is None or d is None:
            return False
        if self.r.random() < 0.5:
            if self.lres(_join(d, _base(t))) is not None:
                return False
            self.focus.append(_join(d, _base(t)))
            return self.do(f"ln -sr {t} {d}", True)
        name = _join(d, self.fresh("l", d))
        self.focus.append(name)
        return self.do(f"ln -sr {t} {name}", True)

    def t_rmdir_p(self) -> bool:
        base = self.pick([x for x in self.dirs(True) if "/" not in x])
        if base is None:
            return False
        a, b = self.fresh("d", None), self.fresh("d", None)
        steps = [(f"mkdir -p {base}/{a}/{b}", True)]
        if self.r.random() < 0.4:
            steps.append((f"echo {self.word()} > {base}/{a}/{self.fresh('f', None)}", True))
        steps.append((f"rmdir -p {base}/{a}/{b}", None))
        self.focus += [f"{base}/{a}", base]
        return self.seq(steps)

    def t_cp_T(self) -> bool:
        d = self.pick([x for x in self.dirs() if 1 <= self.size(x) <= 6])
        e = d and self._other_dir(d)
        if not e:
            return False
        kids = sorted(self.res(d).ents)
        self.focus += [f"{e}/{k}" for k in kids[:2]]
        form = self.r.choice([f"cp -rT {d} {e}", f"cp -r {d}/. {e}", f"cp -a {d}/. {e}"])
        return self.do(form, True)

    def t_mv_T(self) -> bool:
        d = self.pick([x for x in self.dirs(True) if 1 <= self.size(x) <= 6])
        e = self.pick([x for x in self.dirs(True) if x != d and d and not x.startswith(d + "/") and not d.startswith(x + "/")])
        if not d or not e:
            return False
        self.focus += [e, d]
        return self.do(f"mv -T {d} {e}")

    def t_rm_dot(self) -> bool:
        d = self.pick([x for x in self.dirs(True) if self.size(x) >= 1])
        if d is None:
            return False
        self.focus.append(d)
        return self.do(f"rm {self.r.choice(['-r', '-rf'])} {d}/.", False)

    def t_cp_L(self) -> bool:
        lk = self.links()
        c = [d for d in self.dirs() if 1 <= self.size(d) <= 7 and any(q.startswith(d + "/") for q in lk)]
        if c and self.r.random() < 0.6:
            a = self.r.choice(c)
            par = self.pick([x for x in self.wdirs() if not (x + "/").startswith(a + "/")])
            new = _join(par, self.fresh("d", par))
            self.focus += [new + q[len(a):] for q in lk if q.startswith(a + "/")]
            return self.do(f"cp -rL {a} {new}")
        L = self.pick([x for x in self.links("d") if self.size(self.fs.q_readlink(x)[len(ROOT) + 1:]) <= 6])
        if L is None:
            return False
        par = self.pick([x for x in self.wdirs() if x != self.dirof(L)] or [""])
        new = _join(par, self.fresh("d", par))
        self.focus.append(new)
        return self.do(f"cp -{self.r.choice(['rH', 'rL'])} {L} {new}", True)

    def t_chmod_dir_mv(self) -> bool:
        d = self.pick([x for x in self.dirs(True) if self.size(x) <= 4 and "/" in x])
        e = d and self._other_dir(d)
        if not e:
            return False
        new = _join(self.dirof(d), self.fresh("d", self.dirof(d)))
        self.focus += [new, d, f"{e}/{_base(d)}"]
        return self.seq([(f"chmod 555 {d}", True), (f"mv {d} {e}", False), (f"mv {d} {new}", True)])

    def t_rmdir_ro(self) -> bool:
        d = self.pick([x for x in self.dirs(True) if self.size(x) == 0])
        pre = []
        if d is None:
            par = self.pick(self.wdirs())
            d = _join(par, self.fresh("d", par))
            pre = [(f"mkdir {d}", True)]
        self.focus.append(d)
        return self.seq(pre + [(f"chmod 555 {d}", True), (f"rmdir {d}", True)])

    _SESSION = ["write", "write", "append", "append", "cp", "mv", "overwrite", "mkdir", "touch", "rm", "t_ln_s_rel",
                "t_glob_cp", "t_cp_follow", "t_hardlink", "t_cat_missing", "t_cp_r_into", "t_mv_into", "t_cp_T", "t_multi"]

    def t_cd_session(self, stay: bool = False) -> bool:
        """cd into a symlinked directory and work there for a while: every relative path resolves physically (its '..'
        is the real parent), while `cd ..` and `cd ../x` are logical; back to the top with `cd ..` (or stay: the end)."""
        if self.here():
            return False
        c = [L for L in self.links("d") if "/" not in L and self.fs.q_readlink(L).count("/") > ROOT.count("/") + 1]
        mark, n = len(self.fs.log), len(self.cmds)
        if c:
            L = self.r.choice(c)
        else:
            phys = self.pick([x for x in self.dirs(True) if "/" in x])
            if phys is None:
                return False
            L = self.fresh("l", "")
            if not self.do(f"ln -s {phys} {L}", True, True):
                return False
        ok = self.do(f"cd {L}", True, True)
        hop = False
        for _ in range(self.r.randint(3, 7) if ok else 0):
            if not hop and self.r.random() < 0.25:
                x = self.pick([p for p, nd in self.ents() if "/" not in p and nd.kind == "d"])
                if x and self.do(f"cd ../{x}", True, True):
                    hop = True
                    continue
            op = self.r.choice(self._SESSION)
            getattr(self, op if op.startswith("t_") else "f_" + op)()
        if ok and not stay:
            ok = self.do("cd ..", True, True)
        if not ok:
            self.fs.rollback(mark)
            del self.cmds[n:], self.status[n:]
        return ok

    # ---- the script
    def generate(self) -> None:
        (lo, hi), n_traps, _nq, _t, _cap = _LV[self.level]
        r = self.r
        target = r.randint(lo, hi)
        pool = _POOLS[self.level]
        if n_traps >= len(pool):
            traps: list[str] = []
            while len(traps) < n_traps:
                p = list(pool)
                r.shuffle(p)
                traps += p
            traps = traps[:n_traps]
        else:
            traps = r.sample(pool, n_traps)
        # where the traps go: spread over the script (single-trap levels: after at least two ordinary commands)
        at = sorted(min(target - 1, int((k + r.uniform(0.2, 0.8)) * target / n_traps)) for k in range(n_traps))
        if self.level <= 3:
            at = [r.randint(2, max(2, target - 3))]
        fill = list(_FILLER)
        w = [_FILLER[k] for k in fill]
        stay = self.level >= 9 and r.random() < 0.5     # the script ends inside a symlinked directory: ask for pwd
        if stay:
            target -= 6
        tries, blocked = 0, -1
        while len(self.cmds) < target and tries < 3000:
            tries += 1
            if traps and len(self.cmds) >= at[0] and blocked != len(self.cmds):
                placed = next((k for k in range(len(traps)) if getattr(self, "t_" + traps[k])()), None)
                if placed is not None:
                    traps.pop(placed)
                    at.pop(0)
                    continue
                blocked = len(self.cmds)     # none is possible here and now: an ordinary command first
            if self.size() > self.cap and r.random() < 0.6:
                if not self.f_prune():
                    self.f_rm()
                continue
            getattr(self, "f_" + r.choices(fill, w)[0])()
        if stay:
            self.t_cd_session(stay=True)
        while len(self.cmds) < lo and tries < 4000:
            tries += 1
            getattr(self, "f_" + r.choices(fill, w)[0])()
        self.traps_left = traps


# ---- questions and grading -----------------------------------------------------------------------------------------------

def _pick_questions(g: _Gen, n: int) -> list[tuple[str, str]]:
    r = g.r
    seen: set = set()
    cands = []
    for p in g.focus:
        p = p.strip("/")
        if not p or p in seen or p.split("/")[0] in ("..", ".") or "/../" in p:
            continue
        seen.add(p)
        ln_, t = g.lres(p), g.res(p)
        if ln_ is not None and ln_.kind == "l":
            kinds = ["readlink"] if t is None else ["readlink", "ls"] if t.kind == "d" else ["readlink", "cat"]
        elif t is None:
            kinds = ["cat"]
        elif t.kind == "d":
            kinds = ["ls"] if len(t.ents) <= 8 else []
        else:
            kinds = ["cat"]
        cands += [(k, p) for k in kinds]
    r.shuffle(cands)
    if g.level >= 9:   # the paths whose answer took the most commands or symlinks to get right come first
        for p, nd in g.ents():
            if p not in seen and ((nd.kind == "f" and g.touch.get(nd.ino, 0) >= 3) or nd.kind == "l"):
                seen.add(p)
                t = g.res(p)
                cands.append(("readlink", p) if nd.kind == "l" and (t is None or t.kind == "d" or r.random() < 0.5)
                             else ("cat", p) if t is not None and t.kind == "f" else ("readlink", p))
        r.shuffle(cands)
        cands.sort(key=lambda kp: -_hardness(g, *kp))
    out: list = []
    used: set = set()
    errors = [0 if r.random() < 0.4 else 1]

    def take(k: str, p: str) -> bool:
        """At most one question answers ERROR, in 40% of the items (it must not pay to guess it)."""
        if p in used or len(out) >= n:
            return False
        if _answer(g.fs, k, p) == "ERROR":
            if errors[0]:
                return False
            errors[0] += 1
        out.append((k, p))
        used.add(p)
        return True
    for want in ["readlink", "cat", "ls", "readlink", "cat", "cat", "readlink", "ls", "cat"]:
        for k, p in cands:
            if k == want and take(k, p):
                break
    for k, p in cands:
        take(k, p)
    for p in sorted(g.files(), key=lambda _x: r.random()):   # few traps touched paths (low levels): ordinary files
        take("cat", p)
    r.shuffle(out)
    return out


def _hardness(g: _Gen, kind: str, p: str) -> int:
    """How much of the script a question's answer depends on: the changes to the file it reaches, the symlinks on the
    way, the lines to reproduce."""
    parts, links = p.split("/"), 0
    for i in range(1, len(parts) + 1):
        n = g.lres("/".join(parts[:i]))
        links += n is not None and n.kind == "l"
    t = g.res(p)
    h = 2 * links
    if t is not None and t.kind == "f":
        h += g.touch.get(t.ino, 0) + t.data.count("\n") // 2
    if t is not None and t.kind == "d":
        h += sum(g.touch.get(c.ino, 0) > 0 for c in t.ents.values())
    return h


def _answer(fs: FS, kind: str, path: str) -> str:
    if kind == "pwd":
        return fs.pwd
    return {"cat": fs.q_cat, "ls": fs.q_ls, "readlink": fs.q_readlink}[kind](path)


_HDR = re.compile(r"^[ \t>*_#`-]*ANSWER\s*#?\s*(\d+)\s*[:：.)][*_`]*[ \t]*", re.I | re.M)


def _blocks(text: str, n: int) -> list[str | None]:
    """The text after each 'ANSWER k:' header up to the next header; a run of headers with the same number (a tree
    written as one 'ANSWER 2:' per line) is joined; a later run replaces an earlier one (drafts in the reasoning)."""
    text = strip_think(text or "")
    ms = list(_HDR.finditer(text))
    got: dict[int, str] = {}
    prev = None
    for i, m in enumerate(ms):
        k = int(m.group(1))
        blk = text[m.end():ms[i + 1].start() if i + 1 < len(ms) else len(text)]
        got[k] = got[k] + "\n" + blk if k == prev else blk
        prev = k
    return [got.get(i + 1) for i in range(n)]


def _lines(blk: str) -> list[str]:
    return [x for x in (ln.strip() for ln in blk.splitlines()) if x and not x.startswith("```")]


def _clean(v: str) -> str:
    v = re.sub(r"^[`*_\"']+|[`*_\"']+$", "", v.strip()).strip()
    if v.endswith(".") and not v.endswith(".."):
        v = v[:-1].rstrip()
    return re.sub(r"^[`*_\"']+|[`*_\"']+$", "", v).strip()


def _norm_content(v: str) -> str:
    v = re.sub(r"\s*\|\s*", "|", _clean(v)).lower()
    return "(empty)" if v in ("(empty)", "empty", "<empty>", "(empty file)") else v


def _entry(line: str) -> tuple[str, str] | None:
    s = _clean(re.sub(r"^(?:[-*+•]\s+|\d+[.)]\s+)", "", line.strip()))
    if not s or s in (".", "./"):
        return None
    m = re.match(r"(.*?)\s*->\s*(.*)$", s)
    if m:
        path, rest = m.group(1), "l " + _clean(m.group(2))
    else:
        m = re.match(r"(.*?)\s*=\s*(.*)$", s)
        if m:
            path, rest = m.group(1), "f " + _norm_content(m.group(2))
        else:
            path, rest = s, "d" if s.endswith("/") else "?"
    path = _clean(path)
    if path.startswith(ROOT):
        path = "." + path[len(ROOT):]
    if not path.startswith("./"):
        path = "./" + path.lstrip("/")
    return path.rstrip("/"), rest


def _tree_score(expected: str, blk: str) -> float:
    exp = dict(e for e in (_entry(x) for x in expected.split(" ; ")) if e)
    got: dict[str, set] = {}
    for part in (p for line in _lines(blk) for p in line.split(";")):
        e = _entry(part)
        if e:
            got.setdefault(e[0], set()).add(e[1])
    right = sum(1 for p, v in exp.items() if got.get(p) == {v})
    extra = sum(1 for p in got if p not in exp)
    return right / (len(exp) + extra) if exp or extra else 1.0


def _grade(kind: str, exp: str, blk: str | None) -> float:
    if blk is None:
        return 0.0
    if kind == "tree":
        return _tree_score(exp, blk)
    ls_ = _lines(blk)
    if not ls_:
        return 0.0
    a = _clean(ls_[0])
    if kind == "failed":
        want = set(map(int, exp.split())) if exp != "NONE" else set()
        got = set(map(int, re.findall(r"\d+", a)))
        if not want:
            return 1.0 if not got and re.search(r"\bnone\b|\bno\b|^-$", a, re.I) else 0.0
        return len(want & got) / len(want | got)
    if exp == "ERROR":
        return 1.0 if re.match(r"error\b", a, re.I) else 0.0
    if kind == "cat":
        return 1.0 if _norm_content(a) == exp.lower() else 0.0
    if kind in ("readlink", "pwd"):
        return 1.0 if a.rstrip("/") == exp else 0.0
    if exp == "(empty)":
        return 1.0 if _norm_content(a) == "(empty)" else 0.0
    return 1.0 if set(re.split(r"[\s,]+", a.strip().rstrip("/"))) == set(exp.split()) else 0.0


def _checker(kinds: list[str], expected: list[str]):
    def check(text: str, _t=None) -> float:
        blocks = _blocks(text, len(kinds))
        return sum(_grade(k, e, b) for k, e, b in zip(kinds, expected, blocks)) / len(kinds)
    return check


_NOTATION = ("(Notation, as in `find . | sort`: `./path/` is a directory; `./path = <content>` a regular file, its lines "
             "joined with `|` - `./f = one|two` is a two-line file, `./f = (empty)` an empty one; `./path -> <target>` a "
             "symbolic link and the target text it stores.)")


def fs_seq(seed: int, level: int = 3) -> Item:
    """techhelp.fs_seq: see the module docstring."""
    r = rng(BLOCK, f"fs_seq{level}", seed)
    g = _Gen(r, level)
    g.build()
    start = g.fs.listing()
    g.generate()
    fs = g.fs
    failed = [i + 1 for i, ok in enumerate(g.status) if not ok]
    pq = _pick_questions(g, _LV[level][2])
    if fs.pwd != ROOT:     # the script ended inside a (symlinked) directory
        pq.append(("pwd", ""))
    kinds = ["failed", "tree"] + [k for k, _p in pq]
    exp = [" ".join(map(str, failed)) or "NONE", " ; ".join(fs.listing())] + [_answer(fs, k, p) for k, p in pq]
    qtext = ["Which of the numbered commands failed (exited with a non-zero status)? Give their numbers, or NONE.",
             f"What does {ROOT} contain after the script? List every path, one per line - what `find . | sort` prints "
             "there, without `.` itself - in the notation of the starting tree."]
    for k, p in pq:
        if k == "cat":
            qtext.append(f"Afterwards, in {ROOT}: what does `cat {p}` print? Answer in the content notation (lines joined "
                         "with `|`, or (empty)), or ERROR if it fails.")
        elif k == "readlink":
            qtext.append(f"Afterwards, in {ROOT}: what does `readlink -f {p}` print? Answer with the path, or ERROR if it "
                         "fails.")
        elif k == "ls":
            qtext.append(f"Afterwards, in {ROOT}: which names does `ls {p}` list? Answer with the names separated by "
                         "spaces (any order), (empty) for an empty directory, or ERROR if it fails.")
        else:
            qtext.append("What would `pwd` print at the end of the script, in the script's shell?")
    script = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(g.cmds))
    form = "\n".join(["ANSWER 1: <numbers, or NONE>", "ANSWER 2:", "<one line per path>"]
                     + [f"ANSWER {i}: <answer>" for i in range(3, len(kinds) + 1)])
    tree = "\n".join(start)
    prompt = (f"I run a script with bash on Linux with GNU coreutils 9.x and LC_ALL=C, as an ordinary user (not root) who owns every "
              f"file, with umask 022. The script is not interactive (standard input is not a terminal, so nothing asks "
              f"for confirmation) and it does not stop when a command fails. It starts in {ROOT}, which contains:\n\n"
              f"```\n{tree}\n```\n{_NOTATION}" + (f"\n{g.hard_note.strip()}" if g.hard_note else "")
              + f"\n\nThe script:\n```\n{script}\n```\n\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qtext))
              + "\n\nThink it through, then finish with the answers in exactly this form (the tree one path per line "
              f"after `ANSWER 2:`):\n{form}")
    return Item(f"{BLOCK}.fs_seq.L{level}.{seed}", BLOCK, "fs_seq", [{"role": "user", "content": prompt}],
                _checker(kinds, exp), max_tokens=32000,
                meta={"expected": exp, "kinds": kinds, "paths": [p for _k, p in pq], "setup": g.setup, "commands": g.cmds,
                      "level": level})
