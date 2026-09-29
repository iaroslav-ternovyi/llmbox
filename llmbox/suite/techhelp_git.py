"""techhelp.git_seq: long exact git state. A starting repository, then a numbered sequence of git commands and file edits;
the questions need exact tracking of HEAD, the branches, the index, the working tree and the stash (final
`git status --porcelain`, `git log --format=%s <branch>`, a file in the working tree / index / a commit, the stash list,
which commands failed). Claude Opus solves templated rules and documented quirks at 1.0 and lost points only on long
exact state (chmod, 6 paths x 8 steps: 0.67), so this kind is long state with real program behaviour.

The answers come from `Repo`, an emulator of the bounded command set below: no subprocess, deterministic, a few ms per
item. It is checked against real git (tests/real_programs.py git: every generated command replayed in a temp repo with
fixed dates, every exit status and every question compared). What the emulator cannot model exactly (merge conflicts,
criss-cross merge bases, rename detection in a merge, empty cherry-picks and reverts) raises Unmodeled, and the
generator never emits such a command - so every item stays inside behaviour the emulator gets exactly right, and that
is stable across git 2.30+: status runs with --no-renames, stashes carry a message (no hashes anywhere), commits are
made one minute apart so `git log` lists them newest first by commit time.

Commands: echo W > f, echo W >> f, rm f; git add f | -A | -u; commit -m | -am | --amend -m; rm [--cached] f; mv a b;
branch b | -d b | -D b; switch b | -c b; checkout b | -b b | <rev> (detached); reset --soft | --mixed | --hard <rev>;
restore [--staged] f; stash push [-u] -m m; stash pop | drop; merge [--no-ff] b; cherry-pick <rev>;
revert --no-edit <rev>. <rev> is HEAD, a branch, or either with ~N.
"""
from __future__ import annotations

import re
import shlex

from .common import Item, final_answer, multi_check, num, rng

BLOCK = "techhelp"
GRADING = 1          # bump when a grader of this kind changes (llmbox/famfp.py pools answers per family and grading)


class Unmodeled(Exception):
    """A situation the emulator does not model exactly: the generator must not emit the command that leads to it."""


class Commit:
    __slots__ = ("tree", "parents", "subject")

    def __init__(self, tree: dict, parents: tuple, subject: str):
        self.tree, self.parents, self.subject = tree, parents, subject


def _merge3(base: dict, ours: dict, theirs: dict) -> dict:
    """A path-level three-way merge that is exact for git (ort or recursive) when it succeeds: at most one side changed a
    path, or both made the same modification of a path the base has. Everything else - a conflict, or the same file
    added or deleted on both sides, where rename detection could pair paths - raises Unmodeled."""
    out = {}
    for p in set(base) | set(ours) | set(theirs):
        b, o, t = base.get(p), ours.get(p), theirs.get(p)
        if o == t:
            if o != b and (o is None or b is None):
                raise Unmodeled(f"both sides added or deleted {p}")
            r = o
        elif o == b:
            r = t
        elif t == b:
            r = o
        else:
            raise Unmodeled(f"conflict in {p}")
        if r is not None:
            out[p] = r
    return out


class Repo:
    """One repository: commits (creation order = commit time), branches, HEAD, index, working tree, stash.
    Files are flat names with text content; a tree is a dict path -> content."""

    def __init__(self):
        self.commits: list[Commit] = []
        self.branches: dict[str, int] = {}
        self.branch: str | None = "main"      # the checked-out branch; None = detached HEAD
        self.detached: int | None = None      # the commit HEAD points at when detached
        self.index: dict[str, str] = {}
        self.wt: dict[str, str] = {}
        self.stash: list[dict] = []           # [0] = stash@{0}
        self._anc: dict[int, frozenset] = {}  # ancestor sets (commits never change: shared between clones)

    def clone(self) -> "Repo":
        c = Repo.__new__(Repo)
        c.commits, c.branches, c.branch, c.detached = list(self.commits), dict(self.branches), self.branch, self.detached
        c.index, c.wt, c.stash, c._anc = dict(self.index), dict(self.wt), list(self.stash), self._anc
        return c

    # ---- refs and history ----------------------------------------------------------------------------------------

    def head(self) -> int | None:
        return self.branches.get(self.branch) if self.branch is not None else self.detached

    def head_tree(self) -> dict:
        h = self.head()
        return self.commits[h].tree if h is not None else {}

    def _set_head(self, c: int) -> None:
        if self.branch is not None:
            self.branches[self.branch] = c
        else:
            self.detached = c

    def resolve(self, rev: str) -> int | None:
        m = re.fullmatch(r"([\w./-]+?)(?:~(\d+))?", rev)
        if not m:
            return None
        c = self.head() if m.group(1) == "HEAD" else self.branches.get(m.group(1))
        for _ in range(int(m.group(2) or 0)):
            if c is None or not self.commits[c].parents:
                return None
            c = self.commits[c].parents[0]
        return c

    def ancestors(self, c: int) -> frozenset:
        """c and every commit reachable from it."""
        if c not in self._anc:
            seen, todo = set(), [c]
            while todo:
                x = todo.pop()
                if x not in seen:
                    seen.add(x)
                    todo.extend(self.commits[x].parents)
            self._anc[c] = frozenset(seen)
        return self._anc[c]

    def merge_bases(self, a: int, b: int) -> set:
        common = self.ancestors(a) & self.ancestors(b)
        below = set()
        for d in common:
            below |= self.ancestors(d) - {d}
        return common - below

    def log(self, c: int) -> list[str]:
        """`git log --format=%s`: every reachable commit, newest first (commits are made one minute apart, children
        always after their parents, so git's date-ordered walk is exactly creation order)."""
        return [self.commits[x].subject for x in sorted(self.ancestors(c), reverse=True)]

    def _new_commit(self, tree: dict, parents: tuple, subject: str) -> int:
        self.commits.append(Commit(dict(tree), tuple(parents), subject))
        return len(self.commits) - 1

    # ---- reading the state ---------------------------------------------------------------------------------------

    def status(self) -> list[str]:
        """`git status --porcelain --untracked-files=all --no-renames`: changes of tracked paths sorted by path, then
        untracked files sorted."""
        H, I, W = self.head_tree(), self.index, self.wt
        out = []
        for p in sorted(set(H) | set(I)):
            h, i = H.get(p), I.get(p)
            if i is None:
                x, y = "D", " "
            else:
                x = " " if h == i else ("A" if h is None else "M")
                w = W.get(p)
                y = " " if w == i else ("D" if w is None else "M")
            if (x, y) != (" ", " "):
                out.append(f"{x}{y} {p}")
        return out + [f"?? {p}" for p in sorted(p for p in W if p not in I)]

    def dirty_tracked(self) -> bool:
        return self.index != self.head_tree() or any(self.wt.get(p) != v for p, v in self.index.items())

    # ---- two-way merge (checkout, fast-forward, applying a merge result) -----------------------------------------

    def _twoway(self, H: dict, M: dict) -> tuple[dict, dict] | None:
        """unpack_trees' twoway_merge from tree H to tree M with the current index and working tree (git read-tree's
        two-tree table): the new (index, worktree), or None when git refuses. 'clean' = the working file matches the
        index entry or is missing (verify_uptodate lets a missing file through); an untracked file where M needs a
        file, or where H has one that M drops, is in the way (verify_absent)."""
        I, W = self.index, self.wt
        ni, nw = dict(I), dict(W)
        for p in set(I) | set(H) | set(M):
            i, h, m = I.get(p), H.get(p), M.get(p)
            if i is not None:
                clean = p not in W or W[p] == i
                if (h is None and (m is None or i == m)) or (h is not None and m is not None and (h == m or i == m)):
                    continue                                  # keep the index entry and the working file
                if h is not None and m is None and i == h:
                    if not clean:
                        return None
                    del ni[p]
                    nw.pop(p, None)
                elif h is not None and m is not None and i == h:
                    if not clean:
                        return None
                    ni[p] = nw[p] = m
                else:
                    return None
            elif m is not None:
                if h is not None:                              # deletion staged
                    if h != m:
                        return None
                elif p in W:
                    return None                                # untracked file would be overwritten
                else:
                    ni[p] = nw[p] = m
            elif p in W:
                return None                                    # untracked file would be removed
        return ni, nw

    # ---- commands ------------------------------------------------------------------------------------------------

    def run(self, cmd: str) -> int:
        """Run one command; its exit status (0 = success). A failing command changes nothing."""
        a = shlex.split(cmd)
        if a[0] == "echo":
            word, op, f = a[1], a[2], a[3]
            self.wt[f] = (self.wt.get(f, "") if op == ">>" else "") + word + "\n"
            return 0
        if a[0] == "rm":
            if a[1] not in self.wt:
                return 1
            del self.wt[a[1]]
            return 0
        assert a[0] == "git", cmd
        sub, args = a[1], a[2:]
        fn = getattr(self, "_git_" + sub.replace("-", "_"), None)
        if fn is None:
            raise Unmodeled(cmd)
        return fn(args)

    def _git_add(self, args):
        if args == ["-A"]:
            self.index = dict(self.wt)
        elif args == ["-u"]:
            self.index = self._add_u(self.index)
        else:
            (p,) = args
            if p in self.wt:
                self.index[p] = self.wt[p]
            elif p in self.index:
                del self.index[p]                                 # git 2.x stages the removal
            else:
                return 128                                        # pathspec did not match any files
        return 0

    def _add_u(self, idx: dict) -> dict:
        return {p: self.wt[p] for p in idx if p in self.wt}

    def _git_commit(self, args):
        amend = "--amend" in args
        all_ = "-am" in args or "-a" in args
        msg = args[args.index("-am" if "-am" in args else "-m") + 1]
        idx = self._add_u(self.index) if all_ else dict(self.index)
        h = self.head()
        if amend:
            if h is None:
                return 128
            parents = self.commits[h].parents
            if len(parents) <= 1:   # amending must not leave an empty commit (a merge may)
                base = self.commits[parents[0]].tree if parents else None
                if (idx == base) if base is not None else not idx:
                    return 1
        else:
            parents = (h,) if h is not None else ()
            if idx == self.head_tree():
                return 1                                           # nothing to commit (-a's staging is rolled back)
        c = self._new_commit(idx, parents, msg)
        self.index = idx
        if h is None:
            self.branches[self.branch] = c
        else:
            self._set_head(c)
        return 0

    def _git_rm(self, args):
        cached = "--cached" in args
        (p,) = [x for x in args if x != "--cached"]
        if p not in self.index:
            return 128
        if p in self.wt:
            H = self.head_tree()
            local = self.wt[p] != self.index[p]
            staged = H.get(p) != self.index[p]
            if (local and staged) or (not cached and (local or staged)):
                return 1
        del self.index[p]
        if not cached:
            self.wt.pop(p, None)
        return 0

    def _git_mv(self, args):
        src, dst = args
        if src not in self.wt or src not in self.index or dst in self.wt:
            return 128
        if dst in self.index:
            raise Unmodeled("mv onto a path in the index")
        self.index[dst] = self.index.pop(src)
        self.wt[dst] = self.wt.pop(src)
        return 0

    def _git_branch(self, args):
        if args[0] in ("-d", "-D"):
            b = args[1]
            if b not in self.branches or b == self.branch:
                return 1
            if args[0] == "-d" and self.branches[b] not in self.ancestors(self.head()):
                return 1                                           # not fully merged (into HEAD: no upstreams)
            del self.branches[b]
            return 0
        (b,) = args
        if b in self.branches:
            return 128
        self.branches[b] = self.head()
        return 0

    def _switch_to(self, b: str) -> int:
        if b == self.branch:
            return 0                                               # Already on 'b'
        res = self._twoway(self.head_tree(), self.commits[self.branches[b]].tree)
        if res is None:
            return 1
        self.index, self.wt = res
        self.branch, self.detached = b, None
        return 0

    def _create_and_switch(self, b: str) -> int:
        if b in self.branches:
            return 128
        self.branches[b] = self.head()
        self.branch, self.detached = b, None
        return 0

    def _git_switch(self, args):
        if args[0] == "-c":
            return self._create_and_switch(args[1])
        (b,) = args
        if b not in self.branches:
            return 128                                             # invalid reference
        return self._switch_to(b)

    def _git_checkout(self, args):
        if args[0] == "-b":
            return self._create_and_switch(args[1])
        (rev,) = args
        if rev in self.branches:
            return self._switch_to(rev)
        if rev == "HEAD":
            return 0                                               # stays on the branch (HEAD~0 would detach)
        c = self.resolve(rev)
        if c is None:
            return 1                                               # pathspec did not match
        res = self._twoway(self.head_tree(), self.commits[c].tree)
        if res is None:
            return 1
        self.index, self.wt = res
        self.branch, self.detached = None, c
        return 0

    def _git_reset(self, args):
        mode = "--mixed"
        if args and args[0] in ("--soft", "--mixed", "--hard"):
            mode, args = args[0], args[1:]
        c = self.resolve(args[0] if args else "HEAD")
        if c is None:
            return 128
        T = self.commits[c].tree
        if mode == "--hard":
            for p in set(self.index) | set(T):
                if p in T:
                    self.wt[p] = T[p]
                else:
                    self.wt.pop(p, None)
        if mode != "--soft":
            self.index = dict(T)
        self._set_head(c)
        return 0

    def _git_restore(self, args):
        staged = "--staged" in args
        (p,) = [x for x in args if x != "--staged"]
        if staged:
            H = self.head_tree()
            if p not in self.index and p not in H:
                return 1
            if p in H:
                self.index[p] = H[p]
            else:
                del self.index[p]
        else:
            if p not in self.index:
                return 1
            self.wt[p] = self.index[p]
        return 0

    def _git_stash(self, args):
        if args[0] == "push":
            return self._stash_push("-u" in args, args[args.index("-m") + 1])
        if args == ["drop"]:
            if not self.stash:
                return 1
            self.stash.pop(0)
            return 0
        if args == ["pop"]:
            return self._stash_pop()
        raise Unmodeled("stash " + " ".join(args))

    def _stash_push(self, untracked: bool, msg: str) -> int:
        H, I, W = self.head_tree(), self.index, self.wt
        ut = sorted(p for p in W if p not in I)
        if not self.dirty_tracked() and not (untracked and ut):
            return 0                                               # No local changes to save (no entry, exit 0)
        # the worktree tree: the index with every path diff-index (HEAD vs worktree) reports taken from the worktree;
        # a file added to the index and missing from the worktree is not reported, so it keeps its index version
        w = dict(I)
        for p in set(H) | set(I):
            if p in I and p not in H and p not in W:
                continue
            if p in W:
                w[p] = W[p]
            else:
                w.pop(p, None)
        self.stash.insert(0, {"base": self.head(), "w": w, "u": {p: W[p] for p in ut} if untracked and ut else None,
                              "msg": f"On {self.branch or '(no branch)'}: {msg}"})
        if untracked:
            for p in ut:
                del self.wt[p]                                     # git clean
        for p in set(I) | set(H):                                  # git reset --hard
            if p in H:
                self.wt[p] = H[p]
            else:
                self.wt.pop(p, None)
        self.index = dict(H)
        return 0

    def _stash_pop(self) -> int:
        if not self.stash:
            return 1
        e = self.stash[0]
        C = dict(self.index)                                       # the index written as a tree
        R = _merge3(self.commits[e["base"]].tree, C, e["w"])
        if e["u"] and any(p in self.wt or p in R or p in C for p in e["u"]):
            raise Unmodeled("untracked file of the stash in the way")
        res = self._twoway(C, R)
        if res is None:
            if e["u"]:
                raise Unmodeled("a failing pop restores the untracked files anyway (git 2.3x+)")
            return 1                                               # local changes would be overwritten; entry kept
        ni, nw = res
        # without --index the index goes back to what it was, except paths new in the result stay staged
        self.index = {**C, **{p: v for p, v in R.items() if p not in C}}
        self.wt = nw
        if e["u"]:
            self.wt.update(e["u"])
        self.stash.pop(0)
        return 0

    def _git_merge(self, args):
        no_ff = "--no-ff" in args
        (b,) = [x for x in args if x != "--no-ff"]
        if b not in self.branches:
            return 1
        h, m = self.head(), self.branches[b]
        if m in self.ancestors(h):
            return 0                                               # Already up to date
        if h in self.ancestors(m) and not no_ff:
            res = self._twoway(self.head_tree(), self.commits[m].tree)
            if res is None:
                return 1
            self.index, self.wt = res
            self._set_head(m)
            return 0
        bases = self.merge_bases(h, m)
        if len(bases) != 1:
            raise Unmodeled("criss-cross merge")
        R = _merge3(self.commits[next(iter(bases))].tree, self.head_tree(), self.commits[m].tree)
        if self.index != self.head_tree():
            return 2                                               # index must match HEAD for a real merge
        res = self._twoway(self.head_tree(), R)
        if res is None:
            return 2
        self.index, self.wt = res
        into = "" if self.branch in ("main", "master") else f" into {self.branch or 'HEAD'}"
        self._set_head(self._new_commit(R, (h, m), f"Merge branch '{b}'{into}"))
        return 0

    def _pick(self, rev: str, revert: bool) -> int:
        c = self.resolve(rev)
        if c is None:
            return 128
        cm = self.commits[c]
        if len(cm.parents) != 1:
            raise Unmodeled("pick of a merge or root commit")
        if revert and re.match(r"(Revert|Reapply) ", cm.subject):
            raise Unmodeled("revert of a revert (message differs between git versions)")
        parent = self.commits[cm.parents[0]].tree
        H = self.head_tree()
        R = _merge3(cm.tree, H, parent) if revert else _merge3(parent, H, cm.tree)
        if R == H:
            raise Unmodeled("empty pick")
        if self.index != H:
            return 128                                             # your local changes would be overwritten
        res = self._twoway(H, R)
        if res is None:
            return 128
        self.index, self.wt = res
        self._set_head(self._new_commit(R, (self.head(),), f'Revert "{cm.subject}"' if revert else cm.subject))
        return 0

    def _git_cherry_pick(self, args):
        (rev,) = args
        return self._pick(rev, False)

    def _git_revert(self, args):
        (rev,) = [x for x in args if x != "--no-edit"]
        return self._pick(rev, True)


# ---- the generator -----------------------------------------------------------------------------------------------------

_WORDS = """amber azure basil birch bison cedar cobalt coral crane cumin daisy dingo ebony eagle ember fennel ferret fig
gecko ginger granite hazel heron hickory husky ibis indigo iris ivory jackal jade jasmine juniper kelp kiwi koala lagoon
larch lemon lemur lilac linen llama lotus lynx mango maple marble marten meadow melon mint moose moss nectar newt nutmeg
oak ocean ochre olive onyx opal orchid otter panda papaya pearl pepper pine plum poppy puma quail quartz quince raven reef
robin rose ruby saffron sage salmon sand sepia shale sloth spruce stoat storm swan tapir teal thyme tiger topaz tulip umber
vanilla velvet viper walnut walrus willow wombat yak yarrow yew zebra zinc acorn alder anise aspen bamboo beryl canyon
clover comet dune falcon fjord flint frost garnet glacier harbor heath hollow island jasper kestrel lichen lupin marsh
mesa nimbus orbit pebble prairie quill ridge river sable sierra slate sorrel summit thistle tundra valley vapor wren
zephyr""".split()
_BASE_FILES = ["app.py", "readme.md", "config.yml", "notes.txt", "main.go", "style.css", "index.html", "setup.cfg",
               "data.csv", "util.js", "lib.rs", "todo.txt"]
_NEW_FILES = [s + e for s in ("draft", "scratch", "plan", "api", "db", "cache", "hello", "extra", "debug", "run", "env",
                              "report", "ideas", "bench", "demo", "sample", "schema", "routes", "models", "views", "helpers",
                              "tasks", "old", "tmp", "backup", "spec", "test", "changes")
              for e in (".txt", ".md", ".py", ".sh", ".log", ".json")]
_BRANCHES = ["dev", "feature", "hotfix", "docs", "experiment", "release", "cleanup", "spike", "refactor", "ui"]
_VERBS = ["add", "fix", "update", "remove", "tweak", "rename", "document", "refactor", "test", "bump", "clean up", "polish",
          "simplify", "split", "move", "speed up"]
_NOUNS = ["parser", "login", "cache", "readme", "config", "styles", "logging", "api", "footer", "header", "search", "sidebar",
          "build", "docs", "errors", "routes", "models", "schema", "timeouts", "retries", "metrics", "tests", "deps",
          "version", "labels", "menu", "export", "import", "upload", "auth"]
_SUBJECTS = [f"{v} {n}" for v in _VERBS for n in _NOUNS]
_STASH = [f"{a}-{n}" for a in ("wip", "try", "tmp", "half") for n in _NOUNS]

# per level: number of commands (min, max), files in the first commit, branches besides main the sequence may create,
# and the fixed mix of traps (a seed changes names, files, words, order and small variants - not which rules are tested)
_L7 = ["detached", "amend", "cherry_pick", "revert", "checkout_refused", "stash_pop", "merge_true", "reset_hard",
       "commit_nothing"]
_L8 = _L7 + ["rm_refused", "reset_soft", "mv", "branch_d", "restore_fail"]
_L9 = _L8 + ["pick_refused", "merge_refused", "stash_untracked", "commit_a", "rm_cached", "merge_ff", "switch_missing"]
_L10 = _L9 + ["stash_empty", "add_deleted", "echo_back", "merge_noff", "reset_mixed"]
_LEVEL = {
    1: ((5, 7), 3, 0, ["commit_a"]),
    2: ((6, 9), 3, 0, ["reset_mixed_full"]),
    3: ((8, 10), 3, 0, ["rm_cached", "commit_nothing"]),
    4: ((12, 16), 4, 1, ["switch_carry", "stash_pop", "reset_soft", "stash_empty"]),
    5: ((16, 21), 4, 1, ["checkout_refused", "stash_untracked", "reset_hard", "merge_ff", "commit_a"]),
    6: ((20, 25), 4, 2, ["branch_d", "merge_true", "restore", "stash_empty", "reset_mixed", "add_deleted", "mv"]),
    7: ((30, 40), 5, 2, _L7),
    8: ((40, 50), 5, 2, _L8),
    9: ((65, 80), 5, 2, _L9),
    10: ((86, 100), 6, 3, _L10),
}
# commands a trap emits on average with its setup (measured): filler goes in only while the planned traps still fit
_COSTS = {"merge_refused": 6.5, "stash_pop": 6.5, "detached": 5.1, "merge_true": 4.3, "cherry_pick": 4.0,
          "stash_untracked": 3.8, "merge_ff": 3.8, "merge_noff": 3.4, "restore": 3.3, "reset_hard": 3.0,
          "commit_a": 3.0, "rm_refused": 2.9, "checkout_refused": 2.6, "switch_carry": 2.5, "echo_back": 2.5, "amend": 2.3,
          "stash_empty": 2.3, "commit_nothing": 2.2, "add_deleted": 2.2, "pick_refused": 2.2, "rm_cached": 2.1,
          "reset_mixed": 2.0, "branch_d": 1.8, "revert": 1.7, "restore_fail": 1.6, "reset_soft": 1.6, "mv": 1.0,
          "switch_missing": 1.0, "reset_mixed_full": 5.0}


class _Gen:
    """Builds one item: a setup, then numbered commands chosen on the emulated state (every command is run on a clone
    first; a command whose outcome the emulator cannot model exactly is never emitted)."""

    def __init__(self, r, level: int):
        self.r, self.level = r, level
        self.repo = Repo()
        self.setup: list[str] = ["git init"]
        self.cmds: list[str] = []
        self.rcs: list[int] = []
        self.words = r.sample(_WORDS, len(_WORDS))
        self.subjects = r.sample(_SUBJECTS, len(_SUBJECTS))
        self.fresh = r.sample(_NEW_FILES, len(_NEW_FILES))
        self.stash_msgs = r.sample(_STASH, len(_STASH))
        (lo, hi), self.n_files, n_br, self.plan = _LEVEL[level]
        self.target = r.randint(lo, hi)
        self.hi = hi
        self.pool = r.sample(_BRANCHES, n_br + {9: 1, 10: 0}.get(level, 2 if level >= 4 else 0))   # new names + spares
        self.owned: dict[str, set] = {"main": set()}     # files a branch mostly works on (keeps merges clean)
        self.deleted: list[str] = []
        self.done: list[str] = []                        # traps that happened, in order
        self.costs: list[tuple] = []                     # (trap, commands it emitted): for tuning the level table

    # ---- primitives --------------------------------------------------------------------------------------------------

    def word(self) -> str:
        return self.words.pop()

    def subject(self) -> str:
        return self.subjects.pop()

    def peek(self, cmd: str) -> int | None:
        s = self.repo.clone()
        try:
            return s.run(cmd)
        except Unmodeled:
            return None

    def do(self, cmd: str, want: bool | None = None) -> bool:
        """Emit cmd if the emulator models it (and, with want, only if it succeeds / fails as wanted)."""
        s = self.repo.clone()
        try:
            rc = s.run(cmd)
        except Unmodeled:
            return False
        if want is not None and (rc == 0) != want:
            return False
        self.repo = s
        self.cmds.append(cmd)
        self.rcs.append(rc)
        return True

    def room(self) -> int:
        return self.hi - len(self.cmds)

    def tracked_wt(self, avoid=()) -> list[str]:
        return [p for p in sorted(self.repo.index) if p in self.repo.wt and p not in avoid]

    def untracked(self) -> list[str]:
        return sorted(p for p in self.repo.wt if p not in self.repo.index)

    def others(self) -> list[str]:
        return [b for b in sorted(self.repo.branches) if b != self.repo.branch]

    def tree(self, b: str) -> dict:
        return self.repo.commits[self.repo.branches[b]].tree

    def edit(self, p: str | None = None, avoid=(), op: str | None = None) -> str | None:
        if p is None:
            mine = self.owned.get(self.repo.branch or "", set())
            c = self.tracked_wt(avoid)
            pref = [x for x in c if x in mine]
            if not c:
                return None
            p = self.r.choice(pref if pref and self.r.random() < 0.7 else c)
        op = op or (">>" if self.r.random() < 0.6 else ">")
        self.do(f"echo {self.word()} {op} {p}")
        return p

    def new_file(self) -> str:
        p = self.fresh.pop()
        self.do(f"echo {self.word()} > {p}")
        return p

    def commit_msg(self, flag: str = "-m", want: bool | None = True) -> bool:
        return self.do(f'git commit {flag} "{self.subject()}"', want)

    def work_commit(self, avoid=()) -> bool:
        """Ordinary work: edit one or two files (sometimes a new one) and commit them."""
        c = self.tracked_wt(avoid)
        ps = []
        if not c or self.r.random() < 0.2:
            n = self.new_file()
            self.owned.setdefault(self.repo.branch or "", set()).add(n)
            ps.append(n)
        else:
            for _ in range(self.r.choice([1, 1, 2])):
                p = self.edit(avoid=set(avoid) | set(ps))
                if p:
                    ps.append(p)
        if self.r.random() < 0.5 and all(p in self.repo.index for p in ps):
            return self.commit_msg("-am")
        if len(ps) > 1 and self.r.random() < 0.5:
            self.do("git add -A")
        else:
            for p in ps:
                self.do(f"git add {p}")
        return self.commit_msg()

    def settle(self) -> None:
        """Commit pending tracked work so what follows starts from a clean index and working tree."""
        if not self.repo.dirty_tracked():
            return
        if self.r.random() < 0.5 or any(p not in self.repo.wt for p in self.repo.index):
            self.do("git add -u")
            self.commit_msg()
        else:
            self.commit_msg("-am")

    def branch_name(self) -> str | None:
        free = [b for b in self.pool if b not in self.repo.branches]
        return free[0] if free else None

    def make_side(self, commits: int = 1, back: bool = True) -> str | None:
        """Start a branch here, commit on it, and (back) return: the branch is then ahead of HEAD. With every branch
        name in use, commit on another branch instead (it then has work HEAD lacks)."""
        name, orig = self.branch_name(), self.repo.branch
        if orig is None:
            return None
        self.settle()
        if name is None:
            o = [b for b in self.others() if b != "main" and self.peek(f"git switch {b}") == 0]
            if not o:
                return None
            name = self.r.choice(o)
            self.do(self.r.choice([f"git switch {name}", f"git checkout {name}"]), want=True)
        elif not self.do(self.r.choice([f"git switch -c {name}", f"git checkout -b {name}"]), want=True):
            return None
        self.owned.setdefault(name, set())
        others_own = set().union(*[v for k, v in self.owned.items() if k != name])
        for _ in range(commits):
            self.work_commit(avoid=others_own if len(self.tracked_wt(others_own)) >= 1 else ())
        if back and not self.do(self.r.choice([f"git switch {orig}", f"git checkout {orig}"]), want=True):
            self.settle()
            self.do(f"git switch {orig}", want=True)
        return name

    def touched(self, b: str) -> set:
        """Paths a branch changed since it forked from HEAD."""
        base = self.repo.merge_bases(self.repo.head(), self.repo.branches[b])
        if len(base) != 1:
            return set(self.tree(b))
        B, T = self.repo.commits[next(iter(base))].tree, self.tree(b)
        return {p for p in set(B) | set(T) if B.get(p) != T.get(p)}

    def ahead(self) -> list[str]:
        h = self.repo.head()
        return [b for b in self.others() if self.repo.branches[b] != h and h in self.repo.ancestors(self.repo.branches[b])]

    def diverged(self) -> list[str]:
        h = self.repo.head()
        out = []
        for b in self.others():
            m = self.repo.branches[b]
            if m not in self.repo.ancestors(h) and h not in self.repo.ancestors(m):
                out.append(b)
        return out

    # ---- traps -------------------------------------------------------------------------------------------------------

    def m_commit_a(self) -> bool:
        """commit -a commits tracked edits only: a new file stays untracked."""
        self.new_file()
        return self.edit() is not None and self.commit_msg("-am")

    def m_reset_mixed_full(self) -> bool:
        """A commit with a new file, undone by reset (mixed): its edits unstaged, the new file untracked."""
        n = self.new_file()
        self.do(f"git add {n}")
        self.edit(avoid={n})
        if not self.commit_msg("-am"):
            return False
        return self.do(f"git reset {self.r.choice(['', '--mixed '])}HEAD~1", want=True)

    def m_reset_mixed(self) -> bool:
        if self.r.random() < 0.5 and self.repo.commits[self.repo.head()].parents:
            return self.do(f"git reset {self.r.choice(['', '--mixed '])}HEAD~1", want=True)
        p = self.edit()
        if p:
            self.do(f"git add {p}")
        return self.do(self.r.choice(["git reset", "git reset --mixed HEAD"]), want=True)

    def m_reset_soft(self) -> bool:
        if not self.repo.commits[self.repo.head()].parents:
            self.work_commit()
        n = 2 if self.level >= 7 and self.repo.resolve("HEAD~2") is not None and self.r.random() < 0.4 else 1
        if not self.do(f"git reset --soft HEAD~{n}", want=True):
            return False
        if self.r.random() < 0.6:
            self.commit_msg()
        return True

    def m_reset_hard(self) -> bool:
        v = self.r.random()
        if v < 0.4:          # a staged new file is deleted, an untracked one survives
            a = self.new_file()
            self.do(f"git add {a}")
            self.new_file()
            self.edit(avoid={a})
            return self.do(self.r.choice(["git reset --hard", "git reset --hard HEAD"]), want=True)
        if v < 0.75 and self.repo.commits[self.repo.head()].parents:
            self.edit()
            return self.do("git reset --hard HEAD~1", want=True)
        o = self.others()
        if o:
            return self.do(f"git reset --hard {self.r.choice(o)}", want=True)
        return self.do("git reset --hard HEAD", want=True)

    def m_rm_cached(self) -> bool:
        H = self.repo.head_tree()
        c = [p for p in self.tracked_wt() if p in H and self.peek(f"git rm --cached {p}") == 0]
        if not c:
            return False
        p = self.r.choice(c)
        self.do(f"git rm --cached {p}")
        if self.r.random() < 0.6:
            self.commit_msg()
        if self.r.random() < 0.4:
            self.edit(p)
        return True

    def m_rm_refused(self) -> bool:
        """git rm refuses a file with staged changes or local edits (and --cached one with both)."""
        c = self.tracked_wt()
        if not c:
            return False
        p = self.r.choice(c)
        v = self.r.random()
        self.edit(p, op=">>")
        if v < 0.35:
            self.do(f"git add {p}")
            return self.do(f"git rm {p}", want=False)
        if v < 0.65:
            return self.do(f"git rm {p}", want=False)
        self.do(f"git add {p}")
        self.edit(p, op=">>")
        return self.do(f"git rm --cached {p}", want=False)

    def m_mv(self) -> bool:
        c = self.tracked_wt()
        if not c:
            return False
        p = self.r.choice(c)
        n = self.fresh.pop()
        for s in self.owned.values():
            if p in s:
                s.add(n)
        return self.do(f"git mv {p} {n}", want=True)

    def m_restore(self) -> bool:
        v = self.r.random()
        if v < 0.35:         # the working file goes back to the staged version, not to HEAD's
            p = self.edit(op=">>")
            if not p:
                return False
            self.do(f"git add {p}")
            self.edit(p)
            return self.do(f"git restore {p}", want=True)
        if v < 0.7:
            p = self.edit()
            if not p:
                return False
            self.do(f"git add {p}")
            return self.do(f"git restore --staged {p}", want=True)
        n = self.new_file()
        self.do(f"git add {n}")
        return self.do(f"git restore --staged {n}", want=True)

    def m_restore_fail(self) -> bool:
        u = self.untracked()
        n = self.r.choice(u) if u and self.r.random() < 0.5 else self.new_file()
        return self.do(self.r.choice([f"git restore {n}", f"git restore --staged {n}"]), want=False)

    def stash_push(self, u: bool = False) -> bool:
        before = len(self.repo.stash)
        self.do(f'git stash push {"-u " if u else ""}-m "{self.stash_msgs.pop()}"')
        return len(self.repo.stash) > before

    def m_stash_pop(self) -> bool:
        """Stashed staged and unstaged edits come back unstaged (pop without --index), after other work in between."""
        p1 = self.edit()
        if not p1:
            return False
        self.do(f"git add {p1}")
        touched = {p1}
        if self.r.random() < 0.6:
            p2 = self.edit(avoid=touched)
            if p2:
                touched.add(p2)
        if not self.stash_push():
            return False
        v = self.r.random()
        o = [b for b in self.others() if all(self.tree(b).get(p) == self.repo.head_tree().get(p) for p in touched)]
        if v < 0.4 and o:
            self.do(f"git switch {self.r.choice(o)}", want=True)
        elif v < 0.75:
            self.work_commit(avoid=touched)
        return self.do("git stash pop", want=True)

    def m_stash_untracked(self) -> bool:
        """A stash without -u leaves untracked files in place; with -u it takes them too."""
        self.new_file()
        self.edit()
        u = self.r.random() < 0.5
        if not self.stash_push(u):
            return False
        if self.r.random() < 0.4:
            self.edit()
            self.do("git stash pop", want=None)
        return True

    def m_stash_empty(self) -> bool:
        """No local changes to save: no entry, exit 0 - the pop that follows takes an older entry, or fails."""
        if not self.repo.stash and self.r.random() < 0.5:
            return self.do(self.r.choice(["git stash pop", "git stash drop"]), want=False)
        self.settle()
        self.do(f'git stash push -m "{self.stash_msgs.pop()}"')
        if self.r.random() < 0.3:
            self.do("git stash drop")
        self.do("git stash pop")
        return True

    def m_switch_carry(self) -> bool:
        """An edit to a file the other branch has the same way comes along to the other branch."""
        H = self.repo.head_tree()
        cands = [(b, p) for b in self.others() for p in self.tracked_wt() if p in H and H.get(p) == self.tree(b).get(p)
                 and self.repo.index.get(p) == H.get(p)]
        if not cands:
            name = self.branch_name()
            if name is None or not self.do(f"git branch {name}", want=True):
                return False
            cands = [(name, p) for p in self.tracked_wt() if self.repo.index.get(p) == H.get(p)]
        if not cands:
            return False
        b, p = self.r.choice(cands)
        self.edit(p)
        if self.peek(f"git switch {b}") != 0:
            self.settle()
        return self.do(self.r.choice([f"git switch {b}", f"git checkout {b}"]), want=True)

    def m_checkout_refused(self) -> bool:
        """An unstaged edit to a file that differs on the other branch: git refuses to switch (nothing changes)."""
        H = self.repo.head_tree()
        cands = [(b, p) for b in self.others() for p in self.tracked_wt() if p in H and self.tree(b).get(p) != H.get(p)]
        if not cands and self.repo.branch is not None:
            b = self.make_side()
            if b and self.repo.branch != b:
                cands = [(b, p) for p in self.tracked_wt() if p in H and self.tree(b).get(p) != H.get(p)]
        if not cands:
            return False
        b, p = self.r.choice(cands)
        self.edit(p)
        return self.do(self.r.choice([f"git switch {b}", f"git checkout {b}"]), want=False)

    def m_merge_ff(self) -> bool:
        a = self.ahead()
        b = self.r.choice(a) if a else self.make_side(commits=self.r.choice([1, 2]))
        if b and self.repo.branch != b:
            return self.do(f"git merge {b}", want=True)
        h, cur = self.repo.head(), self.repo.branch      # or catch a branch that is behind up with this one
        behind = [x for x in self.others() if self.repo.branches[x] != h and self.repo.branches[x] in self.repo.ancestors(h)]
        if cur is None or not behind:
            return False
        self.settle()
        x = self.r.choice(behind)
        return self.do(f"git switch {x}", want=True) and self.do(f"git merge {cur}", want=True)

    def m_merge_noff(self) -> bool:
        a = self.ahead()
        b = self.r.choice(a) if a else self.make_side()
        if not b:
            return False
        self.settle()
        return self.do(f"git merge --no-ff {b}", want=True)

    def m_merge_true(self) -> bool:
        """Diverged branches: a merge commit "Merge branch 'b'" (plus " into <branch>" off main, " into HEAD" detached)."""
        self.settle()
        d = [b for b in self.diverged() if self.peek(f"git merge {b}") == 0]
        if not d:
            b = self.make_side()
            if not b or self.repo.branch == b:
                return False
            self.work_commit(avoid=self.touched(b))
            d = [b] if self.peek(f"git merge {b}") == 0 else []
        return bool(d) and self.do(f"git merge {self.r.choice(d)}", want=True)

    def m_merge_refused(self) -> bool:
        """A staged edit blocks a real merge (a fast-forward would have carried it)."""
        self.settle()
        d = [b for b in self.diverged() if self.peek(f"git merge {b}") == 0]
        if not d:
            b = self.make_side()
            if not b or self.repo.branch == b:
                return False
            self.work_commit(avoid=self.touched(b))
            d = [b]
        b = self.r.choice(d)
        p = self.edit(avoid=self.touched(b))
        if not p:
            return False
        self.do(f"git add {p}")
        return self.do(f"git merge {b}", want=False)

    def pick_cands(self, revert: bool = False) -> list[str]:
        h = self.repo.head()
        if revert:
            return [x for x in ("HEAD", "HEAD~1", "HEAD~2") if self.peek(f"git revert --no-edit {x}") == 0]
        out = []
        for b in self.others():
            for rev in (b, f"{b}~1"):
                c = self.repo.resolve(rev)
                if c is not None and c not in self.repo.ancestors(h) and self.peek(f"git cherry-pick {rev}") == 0:
                    out.append(rev)
        return out

    def m_cherry_pick(self) -> bool:
        self.settle()
        c = self.pick_cands()
        if not c and self.repo.branch is not None:
            b = self.make_side()
            if b and self.repo.branch != b:
                self.work_commit(avoid=self.touched(b))
            c = self.pick_cands()
        return bool(c) and self.do(f"git cherry-pick {self.r.choice(c)}", want=True)

    def m_pick_refused(self) -> bool:
        self.settle()
        c = self.pick_cands()
        if not c and self.repo.branch is not None:
            b = self.make_side()
            if b and self.repo.branch != b:
                self.work_commit(avoid=self.touched(b))
            c = self.pick_cands()
        if not c:
            return False
        p = self.edit()
        self.do(f"git add {p}")
        return self.do(f"git cherry-pick {self.r.choice(c)}", want=False)

    def m_revert(self) -> bool:
        self.settle()
        c = self.pick_cands(revert=True)
        return bool(c) and self.do(f"git revert --no-edit {self.r.choice(c)}", want=True)

    def m_detached(self) -> bool:
        """Check out an older commit, commit on the detached HEAD, then leave it: the commit stays only if a branch
        was made for it."""
        self.settle()
        revs = [x for x in ["HEAD~1", "HEAD~2"] + [f"{b}~1" for b in self.others()] + self.others()[:0]
                if self.repo.resolve(x) is not None and self.peek(f"git checkout {x}") == 0]
        if not revs:
            return False
        orig = self.repo.branch
        self.do(f"git checkout {self.r.choice(revs)}", want=True)
        for _ in range(self.r.choice([1, 1, 2])):
            self.edit()
            self.commit_msg("-am")
        v = self.r.random()
        name = self.branch_name()
        if v < 0.15 and self.level >= 8:
            return True                                  # stays detached for a while
        if v < 0.55 or name is None:
            back = orig or self.r.choice(sorted(self.repo.branches))
            return self.do(f"git switch {back}", want=True) or self.do(f"git checkout {back}", want=True)
        if v < 0.8:
            self.owned[name] = set()
            return self.do(f"git switch -c {name}", want=True)
        self.do(f"git branch {name}", want=True)
        self.owned[name] = set()
        back = orig or "main"
        return self.do(f"git switch {back}", want=True)

    def m_amend(self) -> bool:
        if self.r.random() < 0.6:
            p = self.edit()
            if p:
                self.do(f"git add {p}")
        return self.do(f'git commit --amend -m "{self.subject()}"', want=True)

    def m_commit_nothing(self) -> bool:
        """git commit with nothing staged fails: only unstaged edits (-m), or only an untracked file (-am)."""
        if self.repo.index != self.repo.head_tree():
            self.commit_msg()
        if self.r.random() < 0.5 or self.repo.dirty_tracked():
            self.edit()
            return self.commit_msg("-m", want=False)
        self.new_file()
        return self.commit_msg("-am", want=False)

    def m_add_deleted(self) -> bool:
        """`git add` of a deleted tracked file stages the deletion (-u too); of a never-tracked one it fails."""
        v = self.r.random()
        if v < 0.2:
            n = self.new_file()
            self.do(f"rm {n}")
            return self.do(f"git add {n}", want=False)
        H = self.repo.head_tree()
        c = [p for p in self.tracked_wt() if p in H]
        if not c:
            return False
        p = self.r.choice(c)
        self.do(f"rm {p}")
        return self.do(f"git add {p}" if v < 0.6 else "git add -u", want=True)

    def m_switch_missing(self) -> bool:
        gone = [b for b in self.deleted if b not in self.repo.branches]
        if gone and self.r.random() < 0.6:
            return self.do(f"git switch {self.r.choice(gone)}", want=False)
        o = sorted(self.repo.branches)
        return self.do(self.r.choice([f"git switch -c {self.r.choice(o)}", f"git checkout -b {self.r.choice(o)}"]), want=False)

    def m_branch_d(self) -> bool:
        """-d refuses a branch not merged into HEAD (not into main); -D deletes it anyway."""
        h = self.repo.head()
        o = [b for b in self.others() if b != "main"]
        unmerged = [b for b in o if self.repo.branches[b] not in self.repo.ancestors(h)]
        merged = [b for b in o if self.repo.branches[b] in self.repo.ancestors(h)]
        if not unmerged and not merged:
            b = self.make_side()
            if not b:
                return False
            unmerged = [b] if self.repo.branch != b else []
        if unmerged and (self.r.random() < 0.65 or not merged):
            b = self.r.choice(unmerged)
            self.do(f"git branch -d {b}", want=False)
            if self.r.random() < 0.5:
                self.do(f"git branch -D {b}", want=True)
                self.deleted.append(b)
            return True
        if merged:
            b = self.r.choice(merged)
            self.deleted.append(b)
            return self.do(f"git branch -d {b}", want=True)
        return False

    def m_echo_back(self) -> bool:
        """Writing a file's committed content back: the file is unmodified again."""
        H = self.repo.head_tree()
        c = [p for p in self.tracked_wt() if H.get(p) and H[p].count("\n") == 1 and self.repo.wt[p] == H[p]
             and self.repo.index.get(p) == H[p]]
        if not c:
            return False
        p = self.r.choice(c)
        self.edit(p)
        if self.r.random() < 0.5:
            self.edit()
        return self.do(f"echo {H[p].strip()} > {p}")

    # ---- filler and the whole sequence -------------------------------------------------------------------------------

    def filler(self) -> None:
        v = self.r.random()
        if self.level <= 3:
            if v < 0.5:
                self.edit()
            elif v < 0.8:
                self.work_commit()
            else:
                self.new_file()
            return
        name = self.branch_name()
        if v < 0.45:
            self.work_commit()
        elif v < 0.6:
            self.edit()
        elif v < 0.67:
            self.new_file()
        elif v < 0.75:
            dirty = [p for p in sorted(self.repo.wt) if self.repo.wt[p] != self.repo.index.get(p)]
            if dirty:
                self.do(f"git add {self.r.choice(dirty)}")
        elif v < 0.88 and self.others():
            b = self.r.choice(self.others())
            if self.peek(f"git switch {b}") == 0:
                self.do(self.r.choice([f"git switch {b}", f"git checkout {b}"]))
        elif name and self.repo.branch is not None:
            self.make_side(back=self.r.random() < 0.5)

    def build(self) -> None:
        r = self.r
        files = r.sample(_BASE_FILES, self.n_files)
        for f in files:
            self._setup(f"echo {self.word()} > {f}")
        for f in r.sample(files, len(files) // 2):
            self._setup(f"echo {self.word()} >> {f}")
        self._setup("git add -A")
        self._setup('git commit -m "initial commit"')
        if self.level >= 4 or r.random() < 0.5:
            self._setup(f"echo {self.word()} >> {r.choice(files)}")
            self._setup(f'git commit -am "{self.subject()}"')
        self.owned["main"] = set(files[: len(files) // 2])
        if self.level >= 9:                              # the branches first: 3-4 lines of work to merge and pick from
            spare = [f for f in files if f not in self.owned["main"]]
            for i in range(2 if self.level == 9 else 3):
                self.owned[self.pool[i]] = {spare[i % len(spare)]}
                self.make_side(commits=r.choice([1, 2]), back=True)
        plan = list(self.plan)
        r.shuffle(plan)
        for k, m in enumerate(plan):
            spare = self.target - len(self.cmds) - sum(_COSTS[x] for x in plan[k:])   # filler only while the traps fit
            if spare > 0 and r.random() < min(0.9, spare / (len(plan) - k) / 3):
                self.filler()
            n0 = len(self.cmds)
            if getattr(self, "m_" + m)():
                self.done.append(m)
            self.costs.append((m, len(self.cmds) - n0))
        guard = 0
        while len(self.cmds) < self.target and guard < 50:
            guard += 1
            self.filler()
        if not self.repo.dirty_tracked() and len(self.cmds) < self.hi:
            self.edit()

    def _setup(self, cmd: str) -> None:
        assert self.repo.run(cmd) == 0, cmd
        self.setup.append(cmd)

    # ---- questions ---------------------------------------------------------------------------------------------------

    def questions(self) -> list[tuple]:
        R, r, L = self.repo, self.r, self.level
        qs: list[tuple] = [("status", None)]
        branches = sorted(R.branches)
        nlog = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 2, 8: 3, 9: 4, 10: 5}[L]
        order = ([R.branch] if R.branch else []) + [b for b in r.sample(branches, len(branches)) if b != R.branch]
        qs += [("log", b) for b in order[:nlog]]
        qs += self.content_questions({1: 1, 2: 1, 3: 2, 4: 2, 5: 2, 6: 2, 7: 3, 8: 3, 9: 4, 10: 4}[L])
        if L >= 3:
            qs.append(("failed", None))
        if any(c.startswith("git stash") for c in self.cmds):
            qs.append(("stash", None))
        if L >= 5:
            qs.append(("current", None))
        if L >= 9 or (L >= 7 and R.branch is None):
            qs.append(("head", None))
        if L >= 9:
            merged = [b for b in branches if any(len(R.commits[c].parents) > 1 for c in R.ancestors(R.branches[b]))]
            qs.append(("count", r.choice(merged or branches)))
        if L >= 9 or (L >= 6 and self.deleted):
            qs.append(("branches", None))
        return qs

    def content_questions(self, k: int) -> list[tuple]:
        """Files where the working tree, the index and the branches disagree are asked more often; NONE answers (the
        file does not exist there) included."""
        R, r = self.repo, self.r
        H, I, W = R.head_tree(), R.index, R.wt
        trees = {b: self.tree(b) for b in sorted(R.branches)}
        paths = sorted(set(H) | set(I) | set(W) | {p for t in trees.values() for p in t})
        cands: dict[str, list] = {"cat": [], "index": [], "show": []}
        for p in paths:
            w, i, h = W.get(p), I.get(p), H.get(p)
            cands["cat"].append((4 if w != i else 1, ("cat", p)))
            cands["index"].append((4 if i != h or i != w else 0.5, ("index", p)))
            for b, t in trees.items():
                diff = t.get(p) != h or any(o.get(p) != t.get(p) for o in trees.values())
                cands["show"].append((3 if diff else 0.3, ("show", f"{b}:{p}")))
        kinds = (["cat", "index", "show"] * 2)[:k]
        r.shuffle(kinds)
        out = []
        for kd in kinds:
            c = [x for x in cands[kd] if x[1] not in out]
            if c:
                out.append(r.choices([q for _w, q in c], weights=[w for w, _q in c])[0])
        return out

    def answer(self, q: tuple):
        """The exact output for a question: a list of lines (None: the command fails - the file is not there)."""
        R = self.repo
        kind, arg = q
        if kind == "status":
            return R.status()
        if kind == "log":
            return R.log(R.branches[arg])
        if kind == "cat":
            return _lines(R.wt.get(arg))
        if kind == "index":
            return _lines(R.index.get(arg))
        if kind == "show":
            b, p = arg.split(":")
            return _lines(self.tree(b).get(p))
        if kind == "failed":
            return [i for i, rc in enumerate(self.rcs, 1) if rc != 0]
        if kind == "stash":
            return [e["msg"] for e in R.stash]
        if kind == "current":
            return R.branch
        if kind == "head":
            return R.commits[R.head()].subject
        if kind == "count":
            return len(R.ancestors(R.branches[arg]))
        if kind == "branches":
            return sorted(R.branches)
        raise ValueError(kind)


def _lines(content: str | None) -> list[str] | None:
    return None if content is None else content.splitlines()


# ---- grading -----------------------------------------------------------------------------------------------------------

_NONE = {"none", "nothing", "empty", "(empty)", "(nothing)", "no output", "(no output)", "n/a"}


def _is_none(a: str) -> bool:
    return a.strip().strip("`*.").strip().lower() in _NONE


def _norm(s: str) -> str:
    """One subject / stash line: case, quotes, spacing and a trailing period do not matter."""
    s = re.sub(r"^\s*stash@\{\d+\}:\s*", "", s)
    s = re.sub("[\"'`“”‘’]", "", s.strip().strip("*"))
    return re.sub(r"\s+", " ", s).strip().rstrip(".").strip().lower()


def _lcs(a: list, b: list) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def _seq_check(expected: list[str]):
    """Ordered lines (git log subjects, stash entries) joined with '|': the longest common subsequence over the longer
    of the two lists - a missing or extra line costs its share, not everything after it."""
    exp = [_norm(x) for x in expected]

    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        if a is None:
            return 0.0
        if not exp:
            return 1.0 if _is_none(a) else 0.0
        got = [_norm(x) for x in a.strip().rstrip(".").split("|") if x.strip()]
        return _lcs(exp, got) / max(len(exp), len(got)) if got else 0.0
    return check


def _status_key(line: str) -> str:
    return line[:2].replace(" ", "_") + " " + line[3:]


def _status_check(expected: list[str]):
    """`XY path` entries in any order (a blank column as _ - . or a space inside the entry), credit per line."""
    exp = {_status_key(x) for x in expected}

    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        if a is None:
            return 0.0
        a = a.strip().rstrip(".")
        if not exp:
            return 1.0 if _is_none(a) else 0.0
        entries = [e.strip().strip("`").strip() for e in re.split(r"[|,;]", a) if e.strip().strip("`").strip()]
        got = set()
        for e in entries:
            m = re.fullmatch(r"([ _.\-MADRCU?!]{2})\s+(\S+)", e)
            if m:
                got.add(re.sub(r"[ .\-]", "_", m.group(1)) + " " + m.group(2).rstrip("."))
        return len(got & exp) / max(len(entries), len(exp)) if entries else 0.0
    return check


def _content_check(expected: list[str] | None):
    """A file's lines (single words) in order, any separators; NONE when the file does not exist there."""
    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        if a is None:
            return 0.0
        toks = re.findall(r"[a-z0-9]+", a.lower())
        if expected is None:
            return 1.0 if toks and "none" in toks and not set(toks) & _WORDSET else 0.0
        return 1.0 if toks == expected else 0.0
    return check


_WORDSET = set(_WORDS)


def _failed_check(expected: list[int]):
    """The numbers of the failed commands: credit = |right| / |union| (NONE when none failed)."""
    want = set(expected)

    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        if a is None:
            return 0.0
        got = {int(x) for x in re.findall(r"\d+", a)}
        if not want:
            return 1.0 if not got and _is_none(a) else 0.0
        return len(got & want) / len(got | want)
    return check


def _word_check(expected: str):
    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        return 1.0 if a is not None and _norm(a) == _norm(expected) else 0.0
    return check


def _current_check(expected: str | None):
    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        if a is None:
            return 0.0
        a = a.strip().strip("`'\".*").strip().lower()
        if expected is None:
            return 1.0 if "detached" in a or _is_none(a) else 0.0
        return 1.0 if a == expected.lower() else 0.0
    return check


def _count_check(expected: int):
    def check(text: str, _t=None) -> float:
        v = num(final_answer(text))
        return 1.0 if v is not None and v == expected else 0.0
    return check


def _names_check(expected: list[str]):
    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        if a is None:
            return 0.0
        return 1.0 if set(re.findall(r"[\w./-]+", a.lower().rstrip("."))) == set(expected) else 0.0
    return check


def _grader(q: tuple, ans):
    kind = q[0]
    if kind == "status":
        return _status_check(ans), " | ".join(_status_key(x) for x in ans) or "NONE"
    if kind in ("log", "stash"):
        return _seq_check(ans), " | ".join(ans) or "NONE"
    if kind in ("cat", "index", "show"):
        return _content_check(ans), "NONE" if ans is None else " | ".join(ans)
    if kind == "failed":
        return _failed_check(ans), ", ".join(map(str, ans)) or "NONE"
    if kind == "current":
        return _current_check(ans), ans or "DETACHED"
    if kind == "head":
        return _word_check(ans), ans
    if kind == "count":
        return _count_check(ans), str(ans)
    if kind == "branches":
        return _names_check(ans), ", ".join(ans)
    raise ValueError(kind)


def _question(q: tuple) -> str:
    kind, arg = q
    return {"status": "What does `git status --porcelain --untracked-files=all --no-renames` print?",
            "log": f"What does `git log --format=%s {arg}` print?",
            "cat": f"What does `cat {arg}` print?",
            "index": f"What does `git show :{arg}` print (the version staged in the index)?",
            "show": f"What does `git show {arg}` print?",
            "failed": "Which of the numbered commands failed (exited with a non-zero status)?",
            "stash": "What does `git stash list --format=%gs` print?",
            "current": "What does `git branch --show-current` print?",
            "head": "What does `git log -1 --format=%s` print?",
            "count": f"What does `git rev-list --count {arg}` print?",
            "branches": "Which branches exist (`git for-each-ref --format='%(refname:short)' refs/heads`)?"}[kind]


# ---- the task kind -----------------------------------------------------------------------------------------------------

def _generate(seed: int, level: int, tries: int = 60) -> _Gen:
    """The first attempt (seeded, so deterministic) whose sequence has the level's length and every planned trap - the
    same rule mix at every seed; failing that, the attempt that came closest."""
    (lo, hi), plan = _LEVEL[level][0], _LEVEL[level][3]
    best, best_key = None, None
    for t in range(tries):
        g = _Gen(rng(BLOCK, f"git{level}" + (f"/{t}" if t else ""), seed), level)
        g.build()
        missing = sum(max(0, plan.count(m) - g.done.count(m)) for m in set(plan))
        n = len(g.cmds)
        key = (missing, max(0, lo - n, n - hi))
        if key == (0, 0):
            g.attempt = t
            return g
        if best_key is None or key < best_key:
            best, best_key = g, key
    best.attempt = -1
    return best

def git_seq(seed: int, level: int = 3) -> Item:
    """A git session replayed exactly: a small repository, then 5-100 numbered commands (edits, staging, commits,
    branches, switches that carry or refuse local changes, stash, resets, merges, detached HEAD, amend, cherry-pick,
    revert), and questions about the final state. Level 1: `commit -a` leaves a new file untracked; 2: a mixed reset
    unstages; 3: `rm --cached` and a commit with nothing staged; 4-6: 12-25 commands with branches, stash and the reset
    modes; 7-8: 30-50 with detached HEAD, amend, cherry-pick, revert and refused checkouts; 9-10: 60-100 over 3-4
    branches with 14-15 questions (the volume of exact state is the difficulty). Credit per question, per line for
    status / log / stash, per command for the failures."""
    g = _generate(seed, level)
    qs = g.questions()
    answers = [g.answer(q) for q in qs]
    graded = [_grader(q, a) for q, a in zip(qs, answers)]
    width = len(str(len(g.cmds)))
    prompt = (
        "A git repository was created like this - a recent git (2.4x), default configuration, `init.defaultBranch=main`, "
        "no remotes, hooks, aliases or ignore files:\n\n```sh\n" + "\n".join(g.setup) + "\n```\n\n"
        "Then these numbered commands ran in its top directory, in order, one minute apart (so `git log` lists commits "
        "newest first in the order they were made). A command that fails prints an error and changes nothing; "
        "`git merge` and `git revert --no-edit` keep git's default commit messages.\n\n```\n"
        + "\n".join(f"{i:>{width}}  {c}" for i, c in enumerate(g.cmds, 1)) + "\n```\n\n"
        "Questions about the repository after the last command:\n"
        + "\n".join(f"{i + 1}. {_question(q)}" for i, q in enumerate(qs)) + "\n\n"
        "Answer formats: output lines joined with ` | `, in the order the command prints them (a file's lines; `git log` "
        "subjects newest first; stash entries top first); NONE if a command prints nothing, or fails because the file "
        "does not exist there. Write each `git status` line as `XY path` with a blank status column as `_` (e.g. "
        "`_M app.py`, `A_ new.txt`, `?? tmp.log`), in any order. Failed commands: their numbers separated by commas, or "
        "NONE. Branch names: separated by commas. DETACHED if `git branch --show-current` prints nothing because HEAD "
        "is detached." + _instr(len(qs)))
    return Item(f"{BLOCK}.git_seq.L{level}.{seed}", BLOCK, "git_seq", [{"role": "user", "content": prompt}],
                multi_check([c for c, _e in graded]), max_tokens=32000,
                meta={"expected": [e for _c, e in graded], "questions": [list(q) for q in qs], "setup": g.setup,
                      "commands": g.cmds, "rcs": g.rcs, "traps": g.done, "level": level})


def _instr(n: int) -> str:
    return ("\n\nThink it through, then finish with one final line per question, exactly in the form:\n"
            + "\n".join(f"ANSWER {i + 1}: <answer>" for i in range(n)))

