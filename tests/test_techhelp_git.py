"""techhelp.gitignore: deterministic items built fast, right answers in the usual shapes score 1 and near misses 0, the
emulator on rules whose git behaviour is known, and - when git is installed - a sample of generated repositories built for
real. Run: python3 tests/test_techhelp_git.py   (the full check: python3 tests/real_programs.py gitignore [gitignore-linux])"""
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from llmbox import suite  # noqa: E402
from llmbox.suite import techhelp as T, techhelp_git as G  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


check("quick tier unchanged", T.QUICK == ["compose_port", "nginx_route", "subnet", "chmod_seq", "log_root"] and len(suite.QUICK_ITEMS) == 32)
check("registered", T.KINDS["gitignore"] is G.gitignore and suite.max_level("techhelp", "gitignore") == 10)


def answer(lines):
    return "Some reasoning.\n" + "\n".join(f"ANSWER {i + 1}: {a}" for i, a in enumerate(lines))


def shapes(q, e):
    """The oracle answer as models write it."""
    if q["q"] == "check-ignore":
        if e == "NONE":
            return ["NONE", "none", "(nothing)", "NONE (the file is tracked)", "`NONE`", "prints nothing"]
        src, line = e.rsplit(":", 1)
        return [e, f"`{e}`", f"./{e}", f"{e}:*.log\t{q['path']}", f"**{e}**", f"{e} - the negated pattern matches"]
    if e == "NONE":
        return ["NONE", "None.", "(none)", "`NONE`"]
    ps = e.split(", ")
    return [e, ", ".join(reversed(ps)), " ".join(f"`{p}`" for p in ps), "; ".join(f"./{p}" for p in ps),
            ", ".join(f"?? {p}" for p in ps), "[" + ", ".join(ps) + "]", e + "."]


n, slow = 0, 0.0
for level in range(1, 11):
    for seed in range(1, 31):
        t0 = time.time()
        a = G.gitignore(seed, level)
        slow = max(slow, time.time() - t0)
        b = G.gitignore(seed, level)
        n += 1
        tag = f"gitignore L{level} s{seed}"
        exp, qs = a.meta["expected"], a.meta["questions"]
        check(f"{tag} deterministic", a.messages == b.messages and exp == b.meta["expected"])
        check(f"{tag} questions", len(qs) == len(G.LEVELS[level]["q"]), (len(qs), G.LEVELS[level]["q"]))
        for k in range(6):
            ans = [shapes(q, e)[k % len(shapes(q, e))] for q, e in zip(qs, exp)]
            check(f"{tag} shape {k}", a.check(answer(ans)) == 1.0, [(e, x) for e, x in zip(exp, ans)])
        for i, (q, e) in enumerate(zip(qs, exp)):   # near misses score 0 on that question
            one = [x for x in exp]
            if q["q"] == "check-ignore":
                one[i] = "NONE" if e != "NONE" else ".gitignore:1"
                if e != "NONE":
                    src, line = e.rsplit(":", 1)
                    two = list(exp)
                    two[i] = f"{src}:{int(line) + 1}"
                    check(f"{tag} q{i + 1} wrong line", abs(a.check(answer(two)) - (len(exp) - 1) / len(exp)) < 1e-9)
            else:
                ps = [] if e == "NONE" else e.split(", ")
                one[i] = ", ".join(ps[1:]) or "NONE" if ps else "README.md"
                if ps:
                    two = list(exp)
                    two[i] = e + ", " + ps[0] + ".bak"
                    check(f"{tag} q{i + 1} extra path", abs(a.check(answer(two)) - (len(exp) - 1) / len(exp)) < 1e-9)
            check(f"{tag} q{i + 1} missing / wrong", abs(a.check(answer(one)) - (len(exp) - 1) / len(exp)) < 1e-9, one[i])
print(f"{n} items generated, slowest {slow * 1000:.0f} ms")

# ---- the emulator on rules whose git behaviour is known (each was run on git 2.51) -------------------------------------------
R = G.Repo(["build/a.txt", "build/b.txt", "a.log", "keep.log", "logs/k.txt", "logs/x.txt", "sub/k.txt", "docs/x.md", "docs/v/y.md",
            "src/lib/z.c", "src/w.c", "mods/core/m.js", "mods/old/o.js", "#n.md", "!b.txt", "c.tmp", "c.bak", "ERR.LOG"],
           {"": "build/\n*.log\n!keep.log\nlogs/\n!logs/k.txt\ndocs/*.md\nsrc/*\n!src/lib/\nmods/*\n!mods/core/\n\\#n.md\n\\!b.txt\n"
                "*.tmp   \n*.bak\\ \n", "src/lib/": "!*.c\n"},
           "k.txt\n", tracked=["build/a.txt"])
for path, want in [("build/a.txt", "NONE"), ("build/b.txt", ".gitignore:1"), ("keep.log", ".gitignore:3"), ("logs/k.txt", ".gitignore:4"),
                   ("sub/k.txt", ".git/info/exclude:1"), ("docs/x.md", ".gitignore:6"), ("docs/v/y.md", "NONE"),
                   ("src/w.c", ".gitignore:7"), ("src/lib/z.c", "src/lib/.gitignore:1"), ("mods/old/o.js", ".gitignore:9"),
                   ("#n.md", ".gitignore:11"), ("!b.txt", ".gitignore:12"), ("c.tmp", ".gitignore:13"), ("c.bak", "NONE"),
                   ("ERR.LOG", "NONE")]:
    check(f"check-ignore {path}", R.check_ignore(path) == want, R.check_ignore(path))
check("untracked", R.untracked() == {".gitignore", "keep.log", "docs/v/y.md", "src/lib/.gitignore", "src/lib/z.c", "mods/core/m.js",
                                     "c.bak", "ERR.LOG"}, sorted(R.untracked()))
for pat, text, pathname, want in [("a/**/b", "a/b", True, True), ("a/**/b", "a/x/y/b", True, True), ("*.c", "x/y.c", True, False),
                                  ("[!a-c]*", "d1", False, True), ("[^a]", "a", False, False), ("x[[:digit:]]", "x7", False, True),
                                  ("**/x", "x", True, True), ("a/**", "a", True, False), ("?", "/", True, False)]:
    check(f"wildmatch {pat} {text}", G.wildmatch(pat, text, pathname) == want)

# ---- real git, when installed: every level's first seeds built for real ----------------------------------------------------
if shutil.which("git"):
    import real_programs as RP
    import subprocess
    import tempfile
    cases = [c for c in RP._git_cases(3) if c[1] != "random"]
    d, home = tempfile.mkdtemp(), tempfile.mkdtemp()
    env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", LC_ALL="C")
    script = os.path.join(d, "run.sh")
    open(script, "w").write("\n".join(RP._git_script(k, spec, [q["path"] for q in qs if q["q"] == "check-ignore"])
                                      for k, _t, spec, qs, _e in cases) + "\n")
    out = subprocess.run(["bash", script], capture_output=True, text=True, env=env, timeout=600).stdout
    check("real git agrees", RP._git_compare(cases, out, "gitignore sample vs git") == 0)
else:
    print("no git found: the real-git part skipped")

print("all passed" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
