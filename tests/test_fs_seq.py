"""techhelp.fs_seq: the filesystem emulator on the GNU behaviours the traps are built on (each case was run on bash + GNU
coreutils 9.7, 9.4 and 8.32 on 2026-09-29 and gave the same result on all three; tests/real_programs.py fs replays whole
generated items there), and the item contract: deterministic, fast, the key scores 1 in every answer shape, nothing
scores 0, credit per question and per path, the quick tier untouched. Run: python3 tests/test_fs_seq.py"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import suite  # noqa: E402
from llmbox.suite import techhelp as T, techhelp_fs as F  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


def run(cmds):
    """-> (exit statuses as 0/1, the tree listing)"""
    fs = F.FS()
    st = [0 if fs.run(c) else 1 for c in cmds]
    return st, fs.listing(), fs


# ---- the emulator: (name, commands, exit statuses, the tree afterwards)
CASES = [
    ("cp -r into an existing dir", ["mkdir s t", "echo a > s/f", "cp -r s t", "cp -r s u"], [0, 0, 0, 0],
     ["./s/", "./s/f = a", "./t/", "./t/s/", "./t/s/f = a", "./u/", "./u/f = a"]),
    ("mv onto its hard link", ["echo a > a", "ln a b", "mv a b"], [0, 0, 1], ["./a = a", "./b = a"]),
    ("mv onto itself", ["echo a > a", "mv a a", "mv a ./a"], [0, 1, 1], ["./a = a"]),
    ("mv a symlink onto its referent", ["echo a > a", "ln -s a l", "mv l a"], [0, 0, 1], ["./a = a", "./l -> a"]),
    ("mv a file onto a symlink to it", ["echo a > a", "ln -s a l", "mv a l"], [0, 0, 0], ["./l = a"]),
    ("mv link/", ["mkdir d e", "ln -s d l", "mv l/ x", "mv l/ e"], [0, 0, 1, 1], ["./d/", "./e/", "./l -> d"]),
    ("rm link/", ["mkdir d", "echo a > d/f", "ln -s d l", "rm l/"], [0, 0, 0, 1], ["./d/", "./d/f = a", "./l -> d"]),
    ("rm -r link/ empties the target", ["mkdir d d/s", "echo a > d/f", "ln -s d l", "rm -r l/"], [0, 0, 0, 1], ["./d/", "./l -> d"]),
    ("rm -r link removes the link", ["mkdir d", "echo a > d/f", "ln -s d l", "rm -r l"], [0, 0, 0, 0], ["./d/", "./d/f = a"]),
    ("rmdir a symlink", ["mkdir d", "ln -s d l", "rmdir l/", "rmdir l"], [0, 0, 1, 1], ["./d/", "./l -> d"]),
    ("cp to a dangling symlink", ["echo a > a", "ln -s nowhere l", "cp a l", "cp -r a l", "cp -a a l"], [0, 0, 1, 1, 1],
     ["./a = a", "./l -> nowhere"]),
    ("cp writes through a symlink", ["echo a > a", "echo b > b", "ln -s b l", "cp a l"], [0, 0, 0, 0],
     ["./a = a", "./b = a", "./l -> b"]),
    ("cp -r / -R copy a symlink, link/ its directory", ["mkdir d", "echo a > d/f", "ln -s d l", "cp -r l x", "cp -r l/ y", "cp l z", "cp -R l w"],
     [0, 0, 0, 0, 0, 1, 0], ["./d/", "./d/f = a", "./l -> d", "./w -> d", "./x -> d", "./y/", "./y/f = a"]),
    ("cp follows, cp -r / -a do not", ["echo a > a", "ln -s a l", "cp -r l x", "cp l y", "cp -a l z"], [0] * 5,
     ["./a = a", "./l -> a", "./x -> a", "./y = a", "./z -> a"]),
    ("cp -r a symlink replaces a file", ["echo a > a", "echo b > b", "ln -s a l", "cp -r l b"], [0] * 4, ["./a = a", "./b -> a", "./l -> a"]),
    ("cp onto the same file", ["echo a > a", "ln a b", "ln -s a l", "cp a b", "cp a l", "cp l a"], [0, 0, 0, 1, 1, 1],
     ["./a = a", "./b = a", "./l -> a"]),
    ("cp -r a dir onto a file", ["mkdir s", "echo a > t", "cp -r s t"], [0, 0, 1], ["./s/", "./t = a"]),
    ("cp -a keeps hard links, cp -r does not", ["mkdir s", "echo a > s/x", "ln s/x s/y", "cp -a s t", "cp -r s u", "echo b >> t/x", "echo c >> u/x"],
     [0] * 7, ["./s/", "./s/x = a", "./s/y = a", "./t/", "./t/x = a|b", "./t/y = a|b", "./u/", "./u/x = a|c", "./u/y = a"]),
    ("a copy of a read-only file is read-only", ["echo a > a", "chmod 444 a", "cp a b", "echo z >> b", "cp -a a c", "echo z > c"],
     [0, 0, 0, 1, 0, 1], ["./a = a", "./b = a", "./c = a"]),
    ("cp onto a read-only file", ["echo a > a", "echo b > b", "chmod 444 b", "cp a b", "cp -a a b"], [0, 0, 0, 1, 1], ["./a = a", "./b = b"]),
    ("mv into a dir: a non-empty collision", ["mkdir a b b/a", "echo x > a/f", "echo y > b/a/g", "mv a b/"], [0, 0, 0, 1],
     ["./a/", "./a/f = x", "./b/", "./b/a/", "./b/a/g = y"]),
    ("mv into a dir: an empty one is replaced", ["mkdir a b b/a", "echo x > a/f", "mv a b/"], [0, 0, 0], ["./b/", "./b/a/", "./b/a/f = x"]),
    ("mv to a missing dir/", ["echo x > a", "mkdir d", "mv a b/", "mv d e/"], [0, 0, 1, 0], ["./a = x", "./e/"]),
    ("mv into itself", ["mkdir a", "mv a a/b", "mv a a"], [0, 1, 1], ["./a/"]),
    ("ln -s: the target is read from the link's directory", ["mkdir s", "echo a > f", "ln -s f s/l", "ln -s ../f s/m"], [0] * 4,
     ["./f = a", "./s/", "./s/l -> f", "./s/m -> ../f"]),
    ("ln -s into a directory", ["mkdir d", "ln -s ../x d", "ln -s /abs/y d"], [0] * 3, ["./d/", "./d/x -> ../x", "./d/y -> /abs/y"]),
    ("ln -sf into a symlinked dir, -sfn replaces", ["mkdir d", "ln -s d l", "ln -sf zz l", "ln -sfn yy l"], [0] * 4,
     ["./d/", "./d/zz -> zz", "./l -> yy"]),
    ("ln -sf does not replace a directory", ["mkdir d d/zz", "ln -sf zz d"], [0, 1], ["./d/", "./d/zz/"]),
    ("ln -s onto an existing name", ["echo a > f", "ln -s zz f", "ln -s zz nd/x"], [0, 1, 1], ["./f = a"]),
    ("ln hard-links a symlink itself", ["mkdir s", "echo a > f", "ln -s f l", "ln l s/h"], [0] * 4, ["./f = a", "./l -> f", "./s/", "./s/h -> f"]),
    ("ln: no hard link to a directory, no second link", ["mkdir d", "echo a > f", "ln d e", "ln f d", "ln f d", "ln f f"],
     [0, 0, 1, 0, 1, 1], ["./d/", "./d/f = a", "./f = a"]),
    ("echo > a dangling symlink creates the target", ["ln -s t l", "echo a > l", "ln -s nd/t m", "echo a > m"], [0, 0, 0, 1],
     ["./l -> t", "./m -> nd/t", "./t = a"]),
    ("hard links share appends", ["echo a > a", "ln a b", "echo b >> b", "rm a"], [0] * 4, ["./b = a|b"]),
    ("cat missing > f truncates f", ["echo a > b", "mkdir d", "cat nope > b", "echo a > c", "cat d > c", "cat c > d"], [0, 0, 1, 0, 1, 1],
     ["./b = (empty)", "./c = (empty)", "./d/"]),
    ("mkdir -p and existing names", ["mkdir d", "mkdir d", "echo a > f", "mkdir -p f", "mkdir -p d", "mkdir -p f/x", "ln -s nowhere l",
                                     "mkdir l", "mkdir -p l", "ln -s d m", "mkdir -p m/n", "mkdir -p m"],
     [0, 1, 0, 1, 0, 1, 0, 1, 1, 0, 0, 0], ["./d/", "./d/n/", "./f = a", "./l -> nowhere", "./m -> d"]),
    ("a read-only directory", ["mkdir d", "echo a > d/e", "chmod 555 d", "touch d/new", "echo b > d/e", "echo c >> d/e", "rm d/e",
                               "mv d/e f", "touch d/e", "mkdir -p d", "mkdir -p d/x"],
     [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1], ["./d/", "./d/e = b|c"]),
    ("a read-only file", ["echo a > f", "echo z > g", "chmod 444 f", "echo b >> f", "touch f", "chmod 444 g", "mv f g"],
     [0, 0, 0, 1, 0, 0, 0], ["./g = a"]),
    ("a read-only directory cannot change parent", ["mkdir a b", "chmod 555 a", "mv a b/", "mv a c"], [0, 0, 1, 0], ["./b/", "./c/"]),
    ("chmod follows a symlink", ["echo a > f", "ln -s f l", "chmod 444 l", "echo b >> f", "ln -s zz m", "chmod 444 m"],
     [0, 0, 0, 1, 0, 1], ["./f = a", "./l -> f", "./m -> zz"]),
    ("cd through a symlink: .. is physical for the kernel, logical for cd",
     ["mkdir -p a/b", "echo top > x", "echo inner > a/x", "ln -s a/b l", "cd l", "cp ../x got", "ln -s ../x sl", "cd ..", "echo here > h"],
     [0] * 9, ["./a/", "./a/b/", "./a/b/got = inner", "./a/b/sl -> ../x", "./a/x = inner", "./h = here", "./l -> a/b", "./x = top"]),
    ("touch a dangling symlink", ["ln -s t l", "touch l", "ln -s nd/t m", "touch m", "touch f/"], [0, 0, 0, 1, 1],
     ["./l -> t", "./m -> nd/t", "./t = (empty)"]),
    ("rm -f", ["rm -f nope", "rm nope", "rm -rf nope", "mkdir d", "rm -f d"], [0, 1, 0, 0, 1], ["./d/"]),
    ("cp / mv into a symlinked dir", ["mkdir d", "ln -s d l", "echo a > f", "cp f l", "mv f l"], [0] * 5, ["./d/", "./d/f = a", "./l -> d"]),
    ("cp -r merges", ["mkdir -p s/k t/s/k", "echo 1 > s/k/f", "echo 2 > t/s/k/f", "echo 3 > t/s/k/g", "cp -r s t"], [0] * 5,
     ["./s/", "./s/k/", "./s/k/f = 1", "./t/", "./t/s/", "./t/s/k/", "./t/s/k/f = 1", "./t/s/k/g = 3"]),
    ("cp -r of a read-only dir makes a read-only copy", ["mkdir s", "echo a > s/f", "chmod 555 s", "cp -r s t", "touch t/g", "cp -a s u", "touch u/g"],
     [0, 0, 0, 0, 1, 0, 1], ["./s/", "./s/f = a", "./t/", "./t/f = a", "./u/", "./u/f = a"]),
    # levels 7-10
    ("cat into itself or its hard link empties it", ["echo a > f", "cat f > f", "echo b > g", "ln g h", "cat g > h"], [0] * 5,
     ["./f = (empty)", "./g = (empty)", "./h = (empty)"]),
    ("cp -f onto a read-only file makes a new one", ["echo a > a", "echo b > b", "ln b c", "chmod 444 b", "cp -f a b", "cp a c"],
     [0, 0, 0, 0, 0, 1], ["./a = a", "./b = a", "./c = b"]),
    ("rmdir: an empty read-only dir goes, one in a read-only dir stays", ["mkdir -p p/e", "chmod 555 p/e", "rmdir p/e", "mkdir -p q/e",
                                                                           "chmod 555 q", "rmdir q/e"], [0, 0, 0, 0, 0, 1], ["./p/", "./q/", "./q/e/"]),
    ("rmdir -p stops at the first non-empty parent", ["mkdir -p a/b/c", "echo x > a/f", "rmdir -p a/b/c", "mkdir -p z/y/x", "rmdir -p z/y/x"],
     [0, 0, 1, 0, 0], ["./a/", "./a/f = x"]),
    ("cp -rT", ["mkdir -p s t/u", "echo 1 > s/f", "cp -rT s t", "cp -rT s n", "echo z > zf", "cp -rT s zf"], [0, 0, 0, 0, 0, 1],
     ["./n/", "./n/f = 1", "./s/", "./s/f = 1", "./t/", "./t/f = 1", "./t/u/", "./zf = z"]),
    ("mv -T", ["mkdir a b c", "echo x > a/f", "echo y > c/g", "mv -T a b", "mkdir a2", "mv -T a2 c", "echo q > f", "mv -T f c", "mv -T b/f c/g"],
     [0, 0, 0, 0, 0, 1, 0, 1, 0], ["./a2/", "./b/", "./c/", "./c/g = x", "./f = q"]),
    ("cp -r dir/. copies the content", ["mkdir -p s/k t", "echo 1 > s/f", "echo 2 > s/k/g", "cp -r s/. t", "cp -r s/. n"], [0] * 5,
     ["./n/", "./n/f = 1", "./n/k/", "./n/k/g = 2", "./s/", "./s/f = 1", "./s/k/", "./s/k/g = 2", "./t/", "./t/f = 1", "./t/k/", "./t/k/g = 2"]),
    ("rm refuses . and ..", ["mkdir d", "echo x > d/f", "rm -r d/.", "rm -rf d/.", "rm -rf d/.."], [0, 0, 1, 1, 1], ["./d/", "./d/f = x"]),
    ("ln -sr resolves symlinks in both paths", ["mkdir -p a/b c/d", "echo x > a/b/f", "ln -sr a/b/f c/d/l", "ln -s a/b L", "ln -sr L/f c/m",
                                                "ln -sr c/d c/d/up", "ln -sr nope c/n"], [0] * 7,
     ["./L -> a/b", "./a/", "./a/b/", "./a/b/f = x", "./c/", "./c/d/", "./c/d/l -> ../../a/b/f", "./c/d/up -> .", "./c/m -> ../a/b/f",
      "./c/n -> ../nope"]),
    ("globs: cp / rm skip a directory and fail", ["mkdir -p src/sub dst", "echo 1 > src/a.txt", "echo 2 > src/b.log", "echo 3 > src/sub/c",
                                                  "cp src/* dst", "cp src/*.txt src/*.log dst/sub2", "cp *.zz dst", "rm -f *.zz", "rm src/*"],
     [0, 0, 0, 0, 1, 1, 1, 0, 1], ["./dst/", "./dst/a.txt = 1", "./dst/b.log = 2", "./src/", "./src/sub/", "./src/sub/c = 3"]),
    ("several sources", ["echo a > a", "echo b > b", "cp a b nodir", "echo c > c", "mv a b c", "mkdir d", "cp a nope b d", "mv a nope d"],
     [0, 0, 1, 0, 1, 0, 1, 1], ["./b = b", "./c = c", "./d/", "./d/a = a", "./d/b = b"]),
    ("cp -rH / -rL", ["mkdir -p s t", "echo x > t/f", "ln -s ../t s/in", "ln -s t top", "cp -rH top h", "cp -rL s L1", "cp -rH s H1",
                      "ln -s nowhere s/dang", "cp -rL s L2"], [0] * 8 + [1],
     ["./H1/", "./H1/in -> ../t", "./L1/", "./L1/in/", "./L1/in/f = x", "./L2/", "./L2/in/", "./L2/in/f = x", "./h/", "./h/f = x",
      "./s/", "./s/dang -> nowhere", "./s/in -> ../t", "./t/", "./t/f = x", "./top -> t"]),
    ("ln -f", ["echo a > a", "echo b > b", "ln -f a b", "echo c >> b", "ln -f a b"], [0] * 5, ["./a = a|c", "./b = a|c"]),
    ("touch -c, rm -d", ["touch -c nope", "mkdir e", "rm -d e", "mkdir -p n/x", "rm -d n"], [0, 0, 0, 0, 1], ["./n/", "./n/x/"]),
    ("a read-only dir: into / out of / links", ["mkdir d e", "echo x > f", "echo y > d/g", "chmod 555 d", "mv f d", "mv d/g e", "ln f d/h",
                                                "ln d/g e/h", "ln -s f d/s"], [0, 0, 0, 0, 1, 1, 1, 0, 1],
     ["./d/", "./d/g = y", "./e/", "./e/h = y", "./f = x"]),
    ("cp -a onto a hard-linked file makes a new one, cp -r writes into it",
     ["echo a > a", "echo b > b", "ln b c", "chmod 444 b", "cp -a a b", "cp -r a c", "echo d > d", "echo e > e", "ln e f", "cp -r d e"],
     [0, 0, 0, 0, 0, 1, 0, 0, 0, 0], ["./a = a", "./b = a", "./c = b", "./d = d", "./e = d", "./f = d"]),
    ("cp -rT from a symlink onto a dir", ["mkdir d e", "ln -s d l", "cp -r l e", "cp -rT l e"], [0, 0, 0, 1], ["./d/", "./e/", "./e/l -> d", "./l -> d"]),
]
for name, cmds, want_st, want_tree in CASES:
    st, tree, _fs = run(cmds)
    check(f"emulator: {name} (exit statuses)", st == want_st, f"{st} != {want_st}")
    check(f"emulator: {name} (tree)", tree == want_tree, f"{tree} != {want_tree}")

_st, _t, fs = run(["mkdir -p a/b", "echo x > a/b/f", "ln -s a/b l", "ln -s nope d", "ln -s nd/t e", "ln -s l ll"])
for kind, path, want in [("readlink", "l", F.ROOT + "/a/b"), ("readlink", "d", F.ROOT + "/nope"), ("readlink", "e", "ERROR"),
                         ("readlink", "ll/f", F.ROOT + "/a/b/f"), ("readlink", "l/../f", F.ROOT + "/a/f"), ("cat", "ll/f", "x"),
                         ("cat", "l/../b/f", "x"), ("cat", "d", "ERROR"), ("ls", "l", "f"), ("ls", "a", "b"), ("ls", "zz", "ERROR")]:
    got = F._answer(fs, kind, path)
    check(f"question {kind} {path}", got == want, f"{got!r} != {want!r}")

# ---- the items
def oracle(it):
    return "\n".join(f"ANSWER {i + 1}: {e}" for i, e in enumerate(it.meta["expected"]))


def shapes(it):
    """The key written the ways models write it."""
    e = it.meta["expected"]
    tree = e[1].split(" ; ")
    rest = "\n".join(f"ANSWER {i + 3}: {x}" for i, x in enumerate(e[2:]))
    yield "multi-line tree", f"Let me trace it.\n\nANSWER 1: {e[0]}\nANSWER 2:\n" + "\n".join(tree) + "\n" + rest
    yield "fenced tree", f"ANSWER 1: {e[0]}\nANSWER 2:\n```\n" + "\n".join(reversed(tree)) + "\n```\n" + rest
    yield "bullets, no ./", f"ANSWER 1: {e[0]}\nANSWER 2:\n" + "\n".join(f"- {x[2:]}" for x in tree) + "\n" + rest
    yield "absolute paths", f"ANSWER 1: {e[0]}\nANSWER 2:\n" + "\n".join(F.ROOT + x[1:] for x in tree) + "\n" + rest
    yield "one ANSWER 2 per line", f"ANSWER 1: {e[0]}\n" + "\n".join(f"ANSWER 2: {x}" for x in tree) + "\n" + rest
    yield "spacing", f"ANSWER 1:   {e[0]}  \nANSWER 2:\n" + "\n".join("  " + x.replace(" = ", "=").replace("|", " | ") for x in tree) + "\n" + rest
    yield "draft first", "ANSWER 1: 1 2 3\nANSWER 2:\n./zzz/\n\nOn reflection:\n\n" + oracle(it)
    yield "bold headers", "\n".join(f"**ANSWER {i + 1}:** {x}" for i, x in enumerate(e))
    yield "think block", "<think>ANSWER 1: 99</think>\n" + oracle(it)


t0 = time.time()
slow = 0
n = 0
for level in range(1, T.MAX_LEVEL + 1):
    lo, hi = F._LV[level][0]
    for seed in range(1, 31):
        t = time.time()
        a = T.KINDS["fs_seq"](seed, level)
        slow = max(slow, time.time() - t)
        b = T.KINDS["fs_seq"](seed, level)
        n += 1
        m = a.meta
        check(f"L{level} s{seed} deterministic", a.messages == b.messages and m["expected"] == b.meta["expected"])
        check(f"L{level} s{seed} length", lo <= len(m["commands"]) <= hi, len(m["commands"]))
        check(f"L{level} s{seed} oracle", a.check(oracle(a)) == 1.0)
        check(f"L{level} s{seed} empty", a.check("") == 0.0)
        check(f"L{level} s{seed} nonsense", a.check("\n".join(f"ANSWER {i + 1}: 12345 nonsense" for i in range(len(m["kinds"])))) == 0.0)
        check(f"L{level} s{seed} ERROR everywhere", a.check("\n".join(f"ANSWER {i + 1}: ERROR" for i in range(len(m["kinds"])))) <= 1.5 / len(m["kinds"]))
        for name, text in shapes(a):
            check(f"L{level} s{seed} shape: {name}", a.check(text) == 1.0, round(a.check(text), 3))
        # the key replays: the emulator gives the same statuses from the setup and the script alone
        fs_, st = F.replay(m["setup"], m["commands"])
        check(f"L{level} s{seed} replay", (" ".join(str(i + 1) for i, ok in enumerate(st) if not ok) or "NONE") == m["expected"][0]
              and " ; ".join(fs_.listing()) == m["expected"][1])
print(f"{n} items; the slowest took {slow:.2f}s; all in {time.time() - t0:.1f}s")
check("generation well under a second", slow < 0.5, slow)

# credit per question and per path
it = T.KINDS["fs_seq"](4, 9)
e, k = it.meta["expected"], len(it.meta["kinds"])
tree = e[1].split(" ; ")
nt = len(tree)


def with_tree(lines):
    return f"ANSWER 1: {e[0]}\nANSWER 2:\n" + "\n".join(lines) + "\n" + "\n".join(f"ANSWER {i + 3}: {x}" for i, x in enumerate(e[2:]))


def near(a, b):
    return abs(a - b) < 1e-9


check("a missing path costs one share", near(it.check(with_tree(tree[1:])), (k - 1 + (nt - 1) / nt) / k))
check("an extra path costs one share", near(it.check(with_tree(tree + ["./zzz = x"])), (k - 1 + nt / (nt + 1)) / k))
wrong = [x + "|extra" if " = " in x else x for x in tree]
nf = sum(1 for x in tree if " = " in x)
check("a wrong content costs its path", near(it.check(with_tree(wrong)), (k - 1 + (nt - nf) / nt) / k))
check("a directory without its slash is wrong", it.check(with_tree([x.rstrip("/") for x in tree])) < 1.0)
fails = e[0].split()
if len(fails) >= 2:
    got = oracle(it).replace(f"ANSWER 1: {e[0]}", "ANSWER 1: " + " ".join(fails[1:] + ["999"]))
    check("failed commands: set overlap", near(it.check(got), (k - 1 + (len(fails) - 1) / (len(fails) + 1)) / k))
check("NONE when some failed", it.check(oracle(it).replace(f"ANSWER 1: {e[0]}", "ANSWER 1: NONE")) < 1.0)

# the quick tier is unchanged: fs_seq is in medium / deep / ladder only
check("techhelp.QUICK", T.QUICK == ["compose_port", "nginx_route", "subnet", "chmod_seq", "log_root"], T.QUICK)
check("quick items", len(suite.QUICK_ITEMS) == 32 and not any(k == "fs_seq" for _b, k, _l in suite.QUICK_ITEMS))
check("in the ladder", "techhelp.fs_seq.L10" in suite.families())

print("FAILED" if failed else "OK", failed)
sys.exit(1 if failed else 0)
