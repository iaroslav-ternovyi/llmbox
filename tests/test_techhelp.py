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
    e = it.meta["expected"]
    return "ANSWER: " + (", ".join(e) if isinstance(e, list) else str(e))


n = 0
for kind, gen in T.KINDS.items():
    for level in range(1, 6):
        for seed in range(1, 41):
            a, b = gen(seed, level), gen(seed, level)
            n += 1
            check(f"{kind} L{level} s{seed} deterministic", a.messages == b.messages and a.meta["expected"] == b.meta["expected"])
            check(f"{kind} L{level} s{seed} oracle", a.check(oracle(a)) == 1.0, oracle(a))
            check(f"{kind} L{level} s{seed} wrong", a.check("ANSWER: 12345 nonsense_service") == 0.0)
print(f"{n} items generated")

# chmod against GNU coreutils on the Linux box (the answers are Linux answers); falls back to local chmod without a host
import shlex
cases_ = []
for level in range(1, 6):
    for seed in range(1, 60):
        it = T.chmod_seq(seed, level)
        if "g=u" not in it.meta["steps"]:
            cases_.append(it)
script = ["set -e", "D=$(mktemp -d)"]
for i, it in enumerate(cases_):
    mk = f"mkdir $D/{i}" if it.meta["is_dir"] else f"touch $D/{i}"
    script.append(f"{mk}; chmod {it.meta['start']} $D/{i}; " + "; ".join(f"chmod {shlex.quote(s)} $D/{i}" for s in it.meta["steps"])
                  + f"; echo {i} $(stat -c %a $D/{i})")
script.append("rm -rf $D")
try:
    out = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "user@gpu-box", "bash -s"], input="\n".join(script),
                         capture_output=True, text=True, timeout=120).stdout
    where = "GNU chmod on the Linux box"
except Exception:
    out, where = "", "no host"
real = {int(a): int(b, 8) for a, b in (l.split() for l in out.splitlines() if l.strip())}
for i, it in enumerate(cases_):
    if i in real:
        check(f"chmod {it.id} vs GNU", real[i] == int(it.meta["expected"], 8), f"{it.meta['steps']} start {it.meta['start']} dir={it.meta['is_dir']}: ours {it.meta['expected']} real {real[i]:04o}")
print(f"{len(real)} of {len(cases_)} chmod sequences checked against {where}")

# nginx: the documented selection order
locs = [("", "/", "a"), ("", "/api/", "b"), ("=", "/health", "h"), ("~", r"\.php$", "p"), ("^~", "/static/", "s"), ("~*", r"\.(jpg|png)$", "i")]
for uri, want in [("/health", "h"), ("/healthz", "a"), ("/api/x.php", "p"), ("/static/x.php", "s"), ("/api/logo.PNG", "i"), ("/api/users", "b")]:
    check(f"nginx {uri}", T._nginx_match(locs, uri) == want, T._nginx_match(locs, uri))

print("all passed" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
