"""Tech-help generators: deterministic, the oracle answer scores 1, wrong answers 0, and chmod agrees with the real
chmod binary. Run: python3 tests/test_techhelp.py"""
import os
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox.suite import techhelp as T  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


def oracle(it):
    """One numbered line per question (v0.10-dev5: several questions per machine)."""
    return "\n".join(f"ANSWER {i + 1}: {e}" for i, e in enumerate(it.meta["expected"]))


n = 0
for kind, gen in T.KINDS.items():
    for level in range(1, 7):
        for seed in range(1, 41):
            a, b = gen(seed, level), gen(seed, level)
            n += 1
            check(f"{kind} L{level} s{seed} deterministic", a.messages == b.messages and a.meta["expected"] == b.meta["expected"])
            check(f"{kind} L{level} s{seed} oracle", a.check(oracle(a)) == 1.0, oracle(a))
            n_q = len(a.meta["expected"])
            check(f"{kind} L{level} s{seed} wrong", a.check("\n".join(f"ANSWER {i + 1}: 12345 nonsense_service" for i in range(n_q))) == 0.0)
            check(f"{kind} L{level} s{seed} one right", abs(a.check(oracle(a).splitlines()[0]) - 1 / n_q) < 1e-9)
print(f"{n} items generated")

# chmod against GNU coreutils on the Linux box (the answers are Linux answers); falls back to local chmod without a host
import shlex
cases_ = []
for level in range(1, 6):
    for seed in range(1, 60):
        cases_ += [f for f in T.chmod_seq(seed, level).meta["files"] if "g=u" not in f["steps"]]
script = ["set -e", "D=$(mktemp -d)", "C=$(command -v gnuchmod || echo chmod)"]   # Ubuntu 25.10+: chmod is uutils, GNU is gnuchmod
for i, it in enumerate(cases_):
    mk = f"mkdir $D/{i}" if it["is_dir"] else f"touch $D/{i}"
    script.append(f"{mk}; $C {it['start']} $D/{i}; " + "; ".join(f"$C {shlex.quote(s)} $D/{i}" for s in it["steps"])
                  + f"; echo {i} $(stat -c %a $D/{i})")
# level 6: clauses without who letters under a umask (umask set per case in a subshell)
ucases = [dict(f, umask=it.meta["umask"]) for seed in range(1, 60) for it in [T.chmod_seq(seed, 6)] for f in it.meta["files"]]
for j, it in enumerate(ucases):
    i = len(cases_) + j
    # GNU chmod warns and exits 1 when the umask blocked part of a clause ("new permissions are ..., not ..."): expected
    script.append(f"( set +e; umask {it['umask']}; touch $D/{i}; $C {it['start']} $D/{i}; " + "; ".join(
        f"$C {shlex.quote(s)} $D/{i} 2>/dev/null" for s in it["steps"]) + f"; echo {i} $(stat -c %a $D/{i}) )")
cases_ = cases_ + ucases
script.append("rm -rf $D; echo chmod-binary $C")
try:
    out = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "user@gpu-box", "bash -s"], input="\n".join(script),
                         capture_output=True, text=True, timeout=120).stdout
    where = "GNU chmod on the Linux box"
except Exception:
    out, where = "", "no host"
real = {int(a): int(b, 8) for a, b in (l.split() for l in out.splitlines() if l.strip() and not l.startswith("chmod-binary"))}
where += "".join(f" ({l.split()[1]})" for l in out.splitlines() if l.startswith("chmod-binary"))
for i, it in enumerate(cases_):
    if i in real:
        check(f"chmod case {i} vs GNU", real[i] == int(it["expected"], 8), f"{it['steps']} start {it['start']} dir={it['is_dir']}: ours {it['expected']} real {real[i]:04o}")
print(f"{len(real)} of {len(cases_)} chmod sequences checked against {where}")

# nginx: the documented selection order
locs = [("", "/", "a"), ("", "/api/", "b"), ("=", "/health", "h"), ("~", r"\.php$", "p"), ("^~", "/static/", "s"), ("~*", r"\.(jpg|png)$", "i")]
for uri, want in [("/health", "h"), ("/healthz", "a"), ("/api/x.php", "p"), ("/static/x.php", "s"), ("/api/logo.PNG", "i"), ("/api/users", "b")]:
    check(f"nginx {uri}", T._nginx_match(locs, uri) == want, T._nginx_match(locs, uri))

print("all passed" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
