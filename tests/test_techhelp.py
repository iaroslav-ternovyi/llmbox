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
    if kind == "sql":   # rows per query, not ANSWER lines: tests/test_real_engines.py
        continue
    for level in range(1, T.MAX_LEVEL + 1):
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
# levels 7-10 (gnu_chmod): directories with setgid / sticky, 3-5 digit and operator-octal modes, copies, several operators
ucases = [dict(f, umask=it.meta["umask"]) for level in range(7, T.MAX_LEVEL + 1) for seed in range(1, 60) for it in [T.chmod_seq(seed, level)]
          for f in it.meta["files"]]
for j, it in enumerate(ucases):
    i = len(cases_) + j
    mk = f"mkdir $D/{i}" if it["is_dir"] else f"touch $D/{i}"
    script.append(f"( set +e; umask {it['umask']}; {mk}; $C {it['start']} $D/{i}; " + "; ".join(
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

# ---- levels 7-8 ------------------------------------------------------------------------------------------------------
import re
import shutil
from datetime import datetime, timedelta, timezone

# chmod: gnu_chmod is the level 1-6 oracle wherever those levels go (it only adds what they never ask) ...
n_same = 0
for level in range(1, 7):
    for seed in range(1, 100):
        it = T.chmod_seq(seed, level)
        for f in it.meta["files"]:
            m = int(f["start"], 8)
            for s_ in f["steps"]:
                m = T.gnu_chmod(m, s_, f["is_dir"], int(it.meta.get("umask", "0022"), 8))
            n_same += 1
            check(f"gnu_chmod = chmod_apply L{level} {f['steps']}", m == int(f["expected"], 8), f"{m:04o} vs {f['expected']}")
print(f"{n_same} level 1-6 chmod sequences: gnu_chmod agrees with their oracle")
# ... and GNU chmod itself decides: here on macOS with Homebrew coreutils (gchmod/gstat), levels 1-8 plus random specs
if shutil.which("gchmod") and shutil.which("gstat"):
    import random
    R = random.Random(7)
    rand = []
    for _ in range(400):   # random specs on directories (files: macOS refuses the sticky bit to non-root, Linux does not)
        spec = ",".join(R.choice(["", "u", "g", "o", "a", "ug", "go"]) + "".join(
            R.choice("=+-") + R.choice(["r", "w", "x", "X", "s", "t", "rwx", "rX", "", "u", "g", "o"]) for _ in range(R.choice([1, 2])))
            for _ in range(R.choice([1, 2])))
        spec = R.choice([spec, spec, R.choice(["755", "0755", "00755", "2750", "=750", "-6000", "+2000", "1777"])])
        rand.append({"is_dir": True, "start": f"{R.choice([0o2775, 0o1777, 0o3770, 0o755, 0o4750]):04o}", "steps": [spec],
                     "umask": R.choice(["022", "027", "077", "002"])})
        m = int(rand[-1]["start"], 8)
        rand[-1]["expected"] = f"{T.gnu_chmod(m, spec, True, int(rand[-1]['umask'], 8)):04o}"
    local = [dict(f, umask=it.meta.get("umask", "0022")) for level in range(1, T.MAX_LEVEL + 1) for seed in range(1, 30)
             for it in [T.chmod_seq(seed, level)] for f in it.meta["files"]] + rand
    lines = ["D=$(mktemp -d)", "cd $D", "chgrp $(id -g) ."]
    for i, f in enumerate(local):
        mk = f"mkdir {i}" if f["is_dir"] else f"touch {i}"
        lines.append(f"( umask {f['umask']}; {mk}; chgrp $(id -g) {i}; gchmod {f['start']} {i}; " + "; ".join(
            f"gchmod -- {shlex.quote(s_)} {i} 2>/dev/null" for s_ in f["steps"]) + f"; echo {i} $(gstat -c %a {i}) )")
    lines.append("cd /; rm -rf $D")
    out = subprocess.run(["bash", "-c", "\n".join(lines)], capture_output=True, text=True, timeout=300).stdout
    got = {int(a): int(b, 8) for a, b in (l.split() for l in out.splitlines() if l.strip())}
    for i, f in enumerate(local):
        check(f"chmod local GNU {i}", got.get(i) == int(f["expected"], 8),
              f"{f['steps']} start {f['start']} dir={f['is_dir']} umask {f['umask']}: ours {f['expected']} real {got.get(i, 0):04o}")
    print(f"{len(got)} of {len(local)} chmod sequences checked against local GNU chmod (gchmod)")

# nginx: rewrite flags, arguments and proxy_pass URIs as the docs describe them
srw = [(r"^/v1/(.*)$", "/api/$1", None)]
nl = [("", "/", {"proxy": ("root", None)}), ("", "/api/", {"proxy": ("api", "/internal/")}), ("", "/app", {"proxy": ("app", "/")}),
      ("^~", "/static/", {"proxy": ("st", None)}), ("~*", r"\.(png|jpe?g)$", {"proxy": ("img", None)}),
      ("", "/old/", {"rw": [(r"^/old/(.*)$", "/api/$1", "last")], "proxy": ("root", None)}),
      ("", "/name/", {"rw": [(r"^/name/([^/]+)$", "/users?name=$1", "break")], "proxy": ("np", "/x/")}),   # the proxy_pass docs' example
      ("", "/s/", {"rw": [(r"^/s/(.*)$", "/t/$1", None), (r"^/t/(.*)\.php$", "/old/$1", None)], "proxy": ("never", None)}),
      ("", "/q/", {"rw": [(r"^/q/(\w+)$", "/api/find?q=$1?", "last")], "proxy": ("root", None)}),
      ("=", "/health", {"proxy": ("h", "/status")})]
for req, want in [("/v1/users?page=2", ("api", "/internal/users?page=2")),   # server rewrite, prefix replaced, args kept
                  ("/api//users", ("api", "/internal/users")),                 # the normalized URI is replaced
                  ("/static//a.css?v=1", ("st", "/static//a.css?v=1")),       # no URI: as the client sent it
                  ("/apps/list", ("app", "/s/list")), ("/app/list", ("app", "//list")),
                  ("/old/logo.PNG", ("img", "/old/logo.PNG")),                 # the regex wins, the rewrite never runs
                  ("/old/users", ("api", "/internal/users")), ("/v1/pic.jpg", ("img", "/api/pic.jpg")),
                  ("/name/bob?x=1", ("np", "/users?name=bob&x=1")),           # break: proxy_pass's URI ignored; args appended
                  ("/s/cart.php", ("api", "/internal/cart")),                  # two rewrites without a flag, then searched again
                  ("/s/cart", ("root", "/t/cart")), ("/q/cats?page=2", ("api", "/internal/find?q=cats")),   # '?' at the end
                  ("/health?full=1", ("h", "/status?full=1"))]:
    check(f"nginx 7 {req}", T.ngx_proxy(srw, nl, req) == want, T.ngx_proxy(srw, nl, req))
chk = T._proxy_check("blue_pool", "/x/y?z=1")
for ans, want in [("blue_pool /x/y?z=1", 1), ("`blue_pool` `/x/y?z=1`", 1), ("http://blue_pool/x/y?z=1", 1), ("blue_pool, URI /x/y?z=1.", 1),
                  ("blue_pool /x/y", 0.5), ("green_pool /x/y?z=1", 0), ("/x/y?z=1", 0)]:
    check(f"proxy check {ans!r}", chk(f"ANSWER: {ans}") == want, chk(f"ANSWER: {ans}"))

# policy routing: suppress_prefixlength, not fwmark, masks, iif, throw / blackhole / unreachable
rt = {"main": [{"net": "0.0.0.0/0", "dev": "wan", "metric": 100}, {"net": "192.168.1.0/24", "dev": "lan0"}, {"net": "172.16.0.0/16", "dev": "eth1", "metric": 50}],
      "100": [{"net": "172.16.5.0/24", "dev": "tun0"}, {"net": "172.16.5.64/26", "type": "throw"}],
      "51820": [{"net": "0.0.0.0/0", "dev": "wg0"}],
      "300": [{"net": "0.0.0.0/0", "dev": "wan", "metric": 10}, {"net": "203.0.113.0/26", "type": "blackhole"}],
      "200": [{"net": "192.168.1.0/24", "type": "unreachable"}],
      "400": [{"net": "10.0.0.0/8", "dev": "a"}, {"net": "10.1.0.0/16", "dev": "b"}, {"net": "10.1.2.0/24", "dev": "c"}]}
rr = [{"prio": 50, "to": "10.0.0.0/8", "table": "400", "suppress": 16}, {"prio": 100, "from": "10.8.0.0/24", "table": "100"},
      {"prio": 110, "mark": 0x100, "mask": 0xf00, "table": "300"}, {"prio": 120, "iif": "wg0", "table": "200"},
      {"prio": 32764, "table": "main", "suppress": 0}, {"prio": 32765, "not": True, "mark": 0xca6c, "table": "51820"},
      {"prio": 32766, "table": "main"}]
for pkt, want in [({"src": "192.168.1.5", "dst": "8.8.8.8", "iif": "lan0"}, "wg0"),           # default suppressed -> not fwmark rule
                  ({"src": "192.168.1.5", "dst": "172.16.9.9", "iif": "lan0"}, "eth1"),       # /16 survives suppress 0
                  ({"src": "81.2.3.4", "dst": "8.8.8.8", "mark": 0xca6c, "iif": "lo"}, "wan"),
                  ({"src": "10.8.0.7", "dst": "172.16.5.70", "iif": "wg0"}, "eth1"),          # throw falls through
                  ({"src": "10.8.0.7", "dst": "172.16.5.10", "iif": "wg0"}, "tun0"),
                  ({"src": "192.168.1.5", "dst": "8.8.8.8", "mark": 0x1ab, "iif": "lan0"}, "wan"),   # 0x1ab & 0xf00 = 0x100
                  ({"src": "192.168.1.5", "dst": "203.0.113.5", "mark": 0x1ab, "iif": "lan0"}, "none"),   # blackhole: no fall-through
                  ({"src": "192.168.1.5", "dst": "8.8.8.8", "mark": 0x2ab, "iif": "lan0"}, "wg0"),
                  ({"src": "10.8.0.7", "dst": "192.168.1.9", "iif": "wg0"}, "none"),          # unreachable: no fall-through
                  ({"src": "192.168.1.5", "dst": "10.1.2.3", "iif": "lan0"}, "c"),            # /24 > 16: kept
                  ({"src": "192.168.1.5", "dst": "10.1.9.9", "iif": "lan0"}, "wg0")]:         # /16 <= 16: suppressed, next rules
    check(f"route7 {pkt}", T.route_lookup7(rr, rt, pkt) == want, T.route_lookup7(rr, rt, pkt))

# logs: read the generated logs back with zoneinfo (not TZ_SEPT / TZ_OCT_*) and re-derive every answer; a syslog line
# in a repeated local hour (levels 9-10: the EU leaves summer time) is placed by the order of the file, as a reader must
try:
    import zoneinfo
    for name, off in T.TZ_SEPT.items():
        for day in range(1, 31):
            u = datetime(2026, 9, day, 12, tzinfo=timezone.utc).astimezone(zoneinfo.ZoneInfo(name)).utcoffset()
            check(f"tz {name} 2026-09-{day}", u == timedelta(minutes=off), str(u))
    for name, (before, after) in T.TZ_OCT_EU.items():
        z = zoneinfo.ZoneInfo(name)
        check(f"tz {name} before the switch", (T.EU_SWITCH - timedelta(seconds=1)).astimezone(z).utcoffset() == timedelta(minutes=before))
        check(f"tz {name} after the switch", T.EU_SWITCH.astimezone(z).utcoffset() == timedelta(minutes=after))
    for name, off in T.TZ_OCT_US.items():
        for day in (24, 25, 26):
            u = datetime(2026, 10, day, 12, tzinfo=timezone.utc).astimezone(zoneinfo.ZoneInfo(name)).utcoffset()
            check(f"tz {name} 2026-10-{day}", u == timedelta(minutes=off), str(u))
except ImportError:
    zoneinfo = None
for level in (7, 8, 9, 10):
    for seed in range(1, 41 if zoneinfo else 1):
        it = T.log_root(seed, level)
        msg = it.messages[0]["content"]
        svc_at = {a: s for s, a in re.findall(r"^- (\w+): ([\d.]+:\d+)$", msg, re.M)}
        events = []   # (utc time, service, upstream it gave up on or None, kind)
        for head, body in re.findall(r"^(box-\w .*?):\n```\n(.*?)\n```", msg, re.M | re.S):
            host = head.split()[0]
            clock = re.search(r"System clock is (\d+) seconds (behind|ahead)", body)   # chronyd, later in the same log
            skew = int(clock.group(1)) * (1 if clock.group(2) == "behind" else -1) if clock else 0
            zone = re.search(r"/etc/timezone is (\S+)\)", head)
            prev = None
            for line in body.splitlines():
                if host == "box-e":
                    boot = datetime.strptime(re.search(r"booted at (\S+)\)", head).group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    mono, text = re.match(r"\[\s*([\d.]+)\] (.*)", line).groups()
                    k = re.search(r"Killed process \d+ \((\w+)\)", text)
                    if k:
                        events.append((boot + timedelta(seconds=float(mono)), k.group(1), None, "root"))
                    continue
                if zone:   # syslog: local time, no offset
                    stamp, svc_, text = re.match(r"(\w{3} [ \d]\d \d\d:\d\d:\d\d) box-\w (\w+)\[\d+\]: (.*)", line).groups()
                    naive = datetime.strptime("2026 " + stamp, "%Y %b %d %H:%M:%S")
                    t = naive.replace(tzinfo=zoneinfo.ZoneInfo(zone.group(1))).astimezone(timezone.utc)
                    if prev and t < prev:   # the second pass through a repeated hour
                        t = naive.replace(tzinfo=zoneinfo.ZoneInfo(zone.group(1)), fold=1).astimezone(timezone.utc)
                    check(f"log_root L{level} s{seed} {host} in order", prev is None or t >= prev, line)
                    prev = t
                else:
                    stamp, svc_, text = re.match(r"(\S+) box-\w (\w+)\[\d+\]: (.*)", line).groups()
                    t = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)
                    t += timedelta(seconds=skew)
                up = re.search(r"(\d+\.\d+\.\d+\.\d+:\d+)", text)
                if "giving up" in text:
                    events.append((t, svc_, svc_at[up.group(1)], "dep"))
                elif text.startswith("ERROR") and "retrying" not in text and "exiting" not in text:
                    events.append((t, svc_, None, "root"))
        roots = sorted(e for e in events if e[3] == "root")
        root_t, root = roots[0][0], roots[0][1]
        chain, deps = {root}, []
        for e in sorted(e for e in events if e[3] == "dep"):
            if e[2] in chain:
                chain.add(e[1])
                deps.append(e)
        nth = {9: 2, 10: 3}.get(level, 1)   # levels 9-10 also ask for the second (and third) consequence
        want = [root] + [d[1] for d in deps[:nth]] + [deps[-1][1], str(round((deps[-1][0] - root_t).total_seconds()))]
        if level >= 8:
            want.append(root_t.strftime("%H:%M:%S"))
        check(f"log_root L{level} s{seed} re-derived", want == it.meta["expected"], f"{want} vs {it.meta['expected']}")
        check(f"log_root L{level} s{seed} gaps", all((b[0] - a[0]).total_seconds() >= 2 for a, b in zip(deps, deps[1:])))
        if level >= 9:   # the root cause before the EU switch, its consequences after it
            check(f"log_root L{level} s{seed} across the switch", root_t < T.EU_SWITCH <= deps[0][0])

# nginx 9-10: variables in proxy_pass, $request_uri / $uri, rewrite and the query string, redirects, captures
srw9 = [(r"^/lookup\?id=(\d+)$", "/api/items/$1", "last"), (r"^/v1/(.*)$", "/api/$1", None)]
nl9 = [("", "/", {"proxy": ("root", None)}), ("", "/api/", {"proxy": ("api", "/internal/")}),
       ("~", r"^/u/(\d+)/(\w+)$", {"proxy": ("users", "/users/$1/$2")}),
       ("", "/files/", {"rw": [(r"^/files/(.*)$", "/f/$1", "break")], "proxy": ("files", "$request_uri")}),
       ("", "/go/", {"rw": [(r"^/go/(.*)$", "/new/$1", "permanent")], "proxy": ("root", None)}),
       ("", "/tmp/", {"rw": [(r"^/tmp/(.*)$", "/scratch/$1?", "redirect")], "proxy": ("root", None)}),
       ("", "/n/", {"proxy": ("n", "$uri")}),
       ("~", r"^/img/(\w+)\.(gif|webp)$", {"rw": [(r"^/img/(\w)\w*\.gif$", "/thumbs/$1.gif", "break")], "proxy": ("cdn", "/cdn/$1")})]
for req, want in [("/u/42/profile?tab=2", ("users", "/users/42/profile")),      # variables: sent as is, no arguments
                  ("/files/a.txt?v=3", ("files", "/files/a.txt?v=3")),         # $request_uri ignores the rewrite
                  ("/lookup?id=7", ("root", "/lookup?id=7")),                  # rewrite never sees the query string
                  ("/go/x?ref=2", ("301", "/new/x?ref=2")), ("/tmp/x?y=1", ("302", "/scratch/x")),   # '?' at the end: no args
                  ("/n//a/b?x=1", ("n", "/n/a/b")), ("/v1/u/5/x", ("api", "/internal/u/5/x")),
                  ("/img/cat.gif", ("cdn", "/cdn/c")), ("/img/cat.webp?s=1", ("cdn", "/cdn/")),   # a failed rewrite empties $1
                  ("/api?x=1", ("301", "/api/?x=1")), ("/files", ("301", "/files/")),   # a proxying "/name/" location
                  ("/go", ("root", "/go"))]:                                            # ... but /go/ only redirects
    check(f"nginx 9 {req}", T.ngx_proxy9(srw9, nl9, req) == want, T.ngx_proxy9(srw9, nl9, req))
for req in ["/v1/users?page=2", "/api//users", "/static//a.css?v=1", "/apps/list", "/old/users", "/name/bob?x=1", "/s/cart.php",
            "/s/cart", "/q/cats?page=2", "/health?full=1"]:   # without variables and redirects, ngx_proxy9 is ngx_proxy
    check(f"nginx 9 = 7 {req}", T.ngx_proxy9(srw, nl, req) == T.ngx_proxy(srw, nl, req))

# policy routing 9-10: OpenVPN's def1 routes are /1, so suppress_prefixlength 0 keeps them; 'not' inverts the whole
# selector; ipproto/dport; the local table; goto; prohibit
rt9 = {"local": [{"net": "192.168.1.1/32", "type": "local"}],
       "main": [{"net": "0.0.0.0/0", "dev": "wan", "metric": 100}, {"net": "0.0.0.0/1", "dev": "tun5"}, {"net": "128.0.0.0/1", "dev": "tun5"},
                {"net": "192.168.1.0/24", "dev": "lan0"}],
       "400": [{"net": "0.0.0.0/0", "dev": "eth5"}], "500": [{"net": "198.51.100.0/25", "dev": "eth3"}],
       "300": [{"net": "0.0.0.0/0", "dev": "wan", "metric": 10}, {"net": "203.0.113.0/26", "type": "blackhole"}],
       "51820": [{"net": "0.0.0.0/0", "dev": "wg0"}]}
rr9 = [{"prio": 0, "table": "local"}, {"prio": 102, "from": "192.168.2.0/24", "goto": 32766},
       {"prio": 105, "from": "192.168.1.0/24", "proto": "tcp", "dport": (443, 443), "table": "400"},
       {"prio": 106, "from": "192.168.3.0/24", "action": "prohibit"}, {"prio": 110, "mark": 0x100, "mask": 0xf00, "table": "300"},
       {"prio": 115, "not": True, "from": "192.168.1.0/24", "mark": 0x10, "table": "500"},
       {"prio": 32764, "table": "main", "suppress": 0}, {"prio": 32765, "not": True, "mark": 0xca6c, "table": "51820"},
       {"prio": 32766, "table": "main"}]
for pkt, want in [({"src": "192.168.1.5", "dst": "8.8.8.8", "proto": "udp", "dport": 53, "iif": "lan0"}, "tun5"),   # /1 beats /0, kept
                  ({"src": "192.168.1.5", "dst": "8.8.8.8", "proto": "tcp", "dport": 443, "iif": "lan0"}, "eth5"),
                  ({"src": "192.168.1.5", "dst": "198.51.100.9", "proto": "tcp", "dport": 80, "mark": 0x10, "iif": "lan0"}, "tun5"),
                  ({"src": "10.8.0.2", "dst": "198.51.100.9", "proto": "tcp", "dport": 80, "iif": "wg0"}, "eth3"),   # not(false)
                  ({"src": "192.168.1.5", "dst": "192.168.1.1", "proto": "tcp", "dport": 22, "iif": "lan0"}, "local"),
                  ({"src": "192.168.2.5", "dst": "203.0.113.5", "proto": "tcp", "dport": 80, "mark": 0x101, "iif": "lan1"}, "tun5"),  # goto skips 110
                  ({"src": "192.168.1.5", "dst": "203.0.113.5", "proto": "tcp", "dport": 80, "mark": 0x101, "iif": "lan0"}, "none"),
                  ({"src": "192.168.3.5", "dst": "8.8.8.8", "proto": "tcp", "dport": 443, "iif": "lan2"}, "none")]:          # prohibit
    check(f"route9 {pkt}", T.route_lookup9(rr9, rt9, pkt) == want, T.route_lookup9(rr9, rt9, pkt))
for pkt in [{"src": "192.168.1.5", "dst": "8.8.8.8", "iif": "lan0"}, {"src": "81.2.3.4", "dst": "8.8.8.8", "mark": 0xca6c, "iif": "lo"},
            {"src": "10.8.0.7", "dst": "172.16.5.70", "iif": "wg0"}, {"src": "192.168.1.5", "dst": "10.1.9.9", "iif": "lan0"},
            {"src": "192.168.1.5", "dst": "203.0.113.5", "mark": 0x1ab, "iif": "lan0"}]:   # route_lookup9 is route_lookup7 there
    check(f"route9 = route7 {pkt}", T.route_lookup9(rr, rt, pkt) == T.route_lookup7(rr, rt, pkt))

# compose: the generated files through the real `docker compose config` when Docker Compose is installed
try:
    have_compose = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=20).returncode == 0
except Exception:
    have_compose = False
if have_compose:
    import json
    import tempfile
    n_c = 0
    for level in (7, 8, 9, 10):
        for seed in range(1, 7):
            it = T.compose_port(seed, level)
            d = tempfile.mkdtemp()
            for name, text in it.meta["files"].items():
                os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
                open(os.path.join(d, name), "w").write(text)
            tport = it.meta["container_port"]
            svcs = it.meta.get("services") or [it.meta["service"]] * len(it.meta["commands"])
            for cmd, svc, want in zip(it.meta["commands"], svcs, it.meta["expected"]):
                parts = shlex.split(cmd)
                env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_") and not k.endswith("_PORT") and k != "DEBUG"}
                while "=" in parts[0]:
                    k, v = parts.pop(0).split("=", 1)
                    env[k] = v
                args = parts[2:]
                i = args.index("up")
                targets = [a_ for a_ in args[i + 1:] if a_ != "-d"]   # `up <x>` starts x and what it depends on
                p = subprocess.run(["docker", "compose"] + args[:i] + ["config", "--format", "json"], cwd=d, env=env,
                                   capture_output=True, text=True, timeout=60)
                conf = json.loads(p.stdout) if p.returncode == 0 else {"services": {}}
                s_ = conf["services"].get(svc)
                started = s_ is not None and (not targets or svc in targets
                                              or any(svc in (conf["services"][t_].get("depends_on") or {}) for t_ in targets))
                got = sorted({str(x["published"]) for x in (s_ or {}).get("ports", []) if started and x["target"] == tport
                              and x["protocol"] == "tcp" and x.get("published") and "-" not in str(x["published"])})
                n_c += 1
                check(f"compose L{level} s{seed} {cmd!r} {svc} vs docker compose config", (", ".join(got) or "NONE") == want,
                      f"compose says {got}, ours {want}; {p.stderr[-200:]}")
            shutil.rmtree(d, ignore_errors=True)
    print(f"{n_c} compose questions checked against `docker compose config`")

print("all passed" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
