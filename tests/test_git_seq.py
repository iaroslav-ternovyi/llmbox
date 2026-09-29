"""techhelp.git_seq (llmbox/suite/techhelp_git.py): deterministic items (also across hash seeds) with the level's length
and trap mix, full credit for the key in any common shape, partial credit per line / per command, none for nothing;
the emulator on hand-checked git behaviours; and a few items replayed on the local git when there is one (the full
check is tests/real_programs.py git). Run: python3 tests/test_git_seq.py"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import suite  # noqa: E402
from llmbox.suite import techhelp as T, techhelp_git as G  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


def oracle(it):
    return "\n".join(f"ANSWER {i + 1}: {e}" for i, e in enumerate(it.meta["expected"]))


# ---- the suite wiring: a new kind, the quick tier untouched ------------------------------------------------------------
check("registered", T.KINDS.get("git_seq") is G.git_seq)
check("quick tier without git_seq", "git_seq" not in T.QUICK and not any(k == "git_seq" for _b, k, _l in suite.QUICK_ITEMS))
check("quick items", len(suite.QUICK_ITEMS) == 32, len(suite.QUICK_ITEMS))
check("families", all(f"techhelp.git_seq.L{lv}" in suite.families() for lv in range(1, 11)))

# ---- items: length, traps, determinism, grading ------------------------------------------------------------------------
slowest = 0.0
for lv in range(1, 11):
    (lo, hi), _files, _br, plan = G._LEVEL[lv]
    for seed in range(1, 21):
        t0 = time.time()
        it = G.git_seq(seed, lv)
        slowest = max(slowest, time.time() - t0)
        again = G.git_seq(seed, lv)
        name = f"L{lv} s{seed}"
        m = it.meta
        check(f"{name} deterministic", it.messages == again.messages and m == again.meta)
        check(f"{name} length", lo <= len(m["commands"]) <= hi, len(m["commands"]))
        check(f"{name} traps", set(plan) <= set(m["traps"]), sorted(set(plan) - set(m["traps"])))
        n = len(m["expected"])
        check(f"{name} questions", n >= 3 and (lv < 9 or n >= 13) and (lv < 10 or n >= 18), n)
        o = oracle(it)
        check(f"{name} oracle", it.check(o) == 1.0, o)
        check(f"{name} empty", it.check("") == 0.0)
        check(f"{name} one right", abs(it.check(o.splitlines()[0]) - 1 / n) < 1e-9)
        check(f"{name} wrong", it.check("\n".join(f"ANSWER {i + 1}: 12345 nonsense_service" for i in range(n))) == 0.0)
        shapes = ["```\n" + o + "\n```",                                        # the whole block in a code fence
                  "Tracing the commands...\n\n" + o,                             # a preamble
                  re.sub(r"(?m)^ANSWER (\d+): (.*)$", r"**ANSWER \1:**  \2  ", o),   # bold labels, extra spaces
                  re.sub(r" \| ", "|", o),                                      # no spaces around separators
                  re.sub(r"(?m)^(ANSWER \d+: )(.*)$", lambda x: x.group(1) + "`" + x.group(2) + "`", o),
                  o.replace("ANSWER", "Answer")]
        for k, s in enumerate(shapes):
            check(f"{name} shape {k}", it.check(s) == 1.0, s[-300:])
check("generation time", slowest < 1.0, f"{slowest:.2f}s")

# the same items in another interpreter with other hash seeds (set iteration order must not leak into an item)
code = ("import hashlib,json,sys; sys.path.insert(0, %r); from llmbox.suite import techhelp_git as G; "
        "print(hashlib.sha256(json.dumps([[G.git_seq(s, lv).messages, G.git_seq(s, lv).meta] for lv in range(1, 11) "
        "for s in (1, 2, 3)]).encode()).hexdigest())") % os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
prints = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONHASHSEED=h)).stdout.strip() for h in ("0", "1", "12345")}
check("hash-seed independent", len(prints) == 1 and "" not in prints, prints)

# ---- graders: partial credit -------------------------------------------------------------------------------------------
st = G._status_check([" M app.py", "A  new.txt", "?? tmp.log", "D  old.md"])
check("status all", st("ANSWER: ?? tmp.log | _M app.py | D_ old.md | A_ new.txt") == 1.0)
check("status blanks as - or .", st("ANSWER: -M app.py, A- new.txt, ?? tmp.log, D. old.md") == 1.0)
check("status one missing", st("ANSWER: _M app.py | A_ new.txt | ?? tmp.log") == 0.75)
check("status one wrong", st("ANSWER: M_ app.py | A_ new.txt | ?? tmp.log | D_ old.md") == 0.75)
check("status extra", G._status_check([" M app.py"])("ANSWER: _M app.py | ?? x.txt") == 0.5)
check("status none", G._status_check([])("ANSWER: NONE") == 1.0 and G._status_check([])("ANSWER: _M a.py") == 0.0)
lg = G._seq_check(["Merge branch 'dev' into feature", 'Revert "fix login"', "add cache", "initial commit"])
check("log all", lg("ANSWER: Merge branch 'dev' into feature | Revert \"fix login\" | add cache | initial commit") == 1.0)
check("log quotes", lg("ANSWER: merge branch dev into feature | revert fix login | add cache | initial commit.") == 1.0)
check("log one missing", lg("ANSWER: Merge branch 'dev' into feature | add cache | initial commit") == 0.75)
check("log wrong order", lg("ANSWER: add cache | Merge branch 'dev' into feature | Revert \"fix login\" | initial commit") == 0.75)
check("log merge suffix matters", lg("ANSWER: Merge branch 'dev' | Revert \"fix login\" | add cache | initial commit") == 0.75)
sk = G._seq_check(["On main: wip-api", "On (no branch): tmp-cache"])
check("stash prefix", sk("ANSWER: stash@{0}: On main: wip-api | stash@{1}: On (no branch): tmp-cache") == 1.0)
fl = G._failed_check([3, 7])
check("failed", fl("ANSWER: 3, 7") == 1.0 and fl("ANSWER: 3") == 0.5 and fl("ANSWER: 3, 7, 9") == 2 / 3 and fl("ANSWER: NONE") == 0.0)
check("failed none", G._failed_check([])("ANSWER: NONE") == 1.0 and G._failed_check([])("ANSWER: 4") == 0.0)
ct = G._content_check(["amber", "birch"])
check("content", ct("ANSWER: amber | birch") == 1.0 and ct("ANSWER: amber, birch") == 1.0 and ct("ANSWER: birch | amber") == 0.0)
check("content none", G._content_check(None)("ANSWER: NONE") == 1.0 and G._content_check(None)("ANSWER: amber") == 0.0)
check("current", G._current_check(None)("ANSWER: DETACHED") == 1.0 and G._current_check("dev")("ANSWER: `dev`") == 1.0)

# ---- the emulator on behaviours checked by hand against git 2.51 -------------------------------------------------------


def repo(*cmds):
    r = G.Repo()
    for c in ('echo a > f.txt', 'echo b > g.txt', 'git add -A', 'git commit -m "initial commit"') + cmds:
        r.run(c)
    return r


r = repo("echo x > f.txt", "git add f.txt", "echo a > f.txt")   # staged x, working file back to the committed a
check("commit -a that would commit nothing fails", r.run('git commit -am "x"') == 1)
check("...and leaves the index as it was", r.status() == ["MM f.txt"], r.status())
r = repo("echo n > new.txt", "echo c >> f.txt")
check("commit -a skips untracked", r.run('git commit -am "c"') == 0 and r.status() == ["?? new.txt"], r.status())
r = repo("git switch -c dev", "git rm f.txt", 'git commit -m "rm f"', "git switch main", "git rm --cached f.txt")
check("an untracked file the switch would remove blocks it", r.run("git switch dev") == 1 and r.branch == "main")
r = repo("git switch -c dev", "echo n > new.txt", "git add new.txt", 'git commit -m "add new"', "git switch main", "echo n > new.txt")
check("an untracked file in the way blocks it even with the same content", r.run("git switch dev") == 1)
r = repo("echo c >> g.txt", "git switch -c dev")
check("an edit comes along", r.run("git switch main") == 0 and r.status() == [" M g.txt"])
r = repo()
check("stash with nothing to save: exit 0, no entry", r.run('git stash push -m "x"') == 0 and r.stash == [])
check("pop with nothing stashed fails", r.run("git stash pop") == 1 and r.run("git stash drop") == 1)
r = repo("echo c >> f.txt", "git add f.txt", "echo n > new.txt", "git add new.txt", "echo u > u.txt", 'git stash push -m "s"')
check("stash leaves untracked files", r.status() == ["?? u.txt"] and r.stash[0]["msg"] == "On main: s", r.status())
check("pop: edits come back unstaged, new files staged", r.run("git stash pop") == 0 and
      r.status() == [" M f.txt", "A  new.txt", "?? u.txt"], r.status())
r = repo("git checkout -b feat", "echo c >> g.txt", 'git commit -am "feat"', "git switch main", "echo d >> f.txt",
         'git commit -am "main"', "git switch feat")
check("merge message off main", r.run("git merge main") == 0 and r.commits[r.head()].subject == "Merge branch 'main' into feat")
r = repo("git branch dev", "echo c >> f.txt", 'git commit -am "c"', "git switch dev", "echo d >> g.txt", 'git commit -am "d"',
         "echo e >> g.txt", "git add g.txt")
check("a staged edit blocks a real merge", r.run("git merge main") != 0 and r.status() == ["M  g.txt"])
r = repo("git branch dev", "echo c >> f.txt", 'git commit -am "c"', "git switch dev", "echo e >> g.txt", "git add g.txt")
check("...but a fast-forward carries it", r.run("git merge main") == 0 and r.status() == ["M  g.txt"])
r = repo("git switch -c dev", "echo c >> f.txt", 'git commit -am "c"', "git switch main")
check("branch -d refuses an unmerged branch", r.run("git branch -d dev") == 1 and r.run("git branch -D dev") == 0)
r = repo("echo c >> f.txt", 'git commit -am "c"', "git checkout HEAD~1", "echo d >> g.txt", 'git commit -am "d"')
check("detached commit", r.branch is None and r.log(r.head()) == ["d", "initial commit"])
check("revert message", r.run("git revert --no-edit HEAD") == 0 and r.commits[r.head()].subject == 'Revert "d"')
try:
    repo("echo c >> f.txt", 'git commit -am "c"', "git revert --no-edit HEAD").run("git revert --no-edit HEAD")
    check("revert of a revert is not modelled", False)
except G.Unmodeled:
    pass

# ---- a few items on the local git (the full check: python3 tests/real_programs.py git) --------------------------------
if shutil.which("git"):
    sys.path.insert(0, os.path.dirname(__file__))
    import real_programs as RP  # noqa: E402
    n = 0
    for lv in range(1, 11):
        for seed in (1, 2):
            g = G._generate(seed, lv)
            qs = g.questions()
            w = tempfile.mkdtemp(prefix="gitseq")
            try:
                script = RP.git_script(g.setup, g.cmds, [G.question_cmd(q) or "true" for q in qs])
                out = subprocess.run(["bash", "-c", script, "x", w], capture_output=True, text=True, timeout=300).stdout
            finally:
                shutil.rmtree(w, ignore_errors=True)
            real = RP.git_parse(out, len(g.cmds))
            check(f"L{lv} s{seed} exit statuses vs git", [x == 0 for x in g.rcs] == [x == 0 for x in real["rcs"]],
                  list(zip(g.cmds, g.rcs, real["rcs"])))
            for j, q in enumerate(qs):
                n += 1
                check(f"L{lv} s{seed} {q} vs git", g.answer(q) == RP.git_real_answer(q, real, j),
                      (g.answer(q), RP.git_real_answer(q, real, j)))
    print(f"{n} questions of 20 items checked against {subprocess.run(['git', '--version'], capture_output=True, text=True).stdout.strip()}")
print(f"git_seq: {'OK' if not failed else f'{failed} FAILED'}")
sys.exit(1 if failed else 0)
