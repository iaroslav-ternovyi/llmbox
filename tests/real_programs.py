"""The answers llmbox computes with a model of a program, checked against the program itself (needs Docker; pulls
nginx:alpine and debian:stable; the routing check runs a privileged container). Not a test_*.py: slow and networked.

  nginx   techhelp.nginx_route: each generated server block on real nginx; every upstream group is an echo server that
          answers "<name> <$request_uri>", requests go out byte-exact (HTTP/1.0 through nc) - upstream and URI observed
  route   techhelp.subnet (levels 6-10): the `ip rule` / `ip route` lines of the prompt applied in a network namespace of
          a real kernel (dummy interfaces, forwarding on, rp_filter off), answers from `ip route get`
  shell   knowledge.shell (levels 7-10 and the part type levels 9-10 leave out) in bash 5 with GNU coreutils, gawk, jq

Run: python3 tests/real_programs.py [nginx|route|shell ...] [--seeds N]
2026-09-29, --seeds 25: 668 / 495 / 1296 checked, 0 wrong - after the fix it found (a rewrite empties $1 even when its
regex does not match); nginx 1.31.6, kernel 6.17 + iproute2 6.15, bash 5.2.37 / coreutils 9.7 / gawk 5.2.1 / jq 1.7.

"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox.suite import knowledge as K, techhelp as T  # noqa: E402


def docker(args, timeout=3600):
    return subprocess.run(["docker", "run", "--rm"] + args, capture_output=True, text=True, timeout=timeout)


def report(name, by):
    bad = sum(b for _c, b in by.values())
    print(f"{name}: " + json.dumps({k: {"checked": c, "disagree": b} for k, (c, b) in sorted(by.items())}))
    print(f"{name}: {sum(c for c, _b in by.values())} checked, {bad} disagree")
    return bad


def nginx(n):
    specs = [(lv, s) for lv in (9, 10) for s in range(1, n + 1)] + [(lv, s) for lv in (6, 7, 8) for s in range(1, 9)] + \
        [(lv, s) for lv in (3, 4, 5) for s in range(1, 5)]
    items, pools = [], set()
    for i, (lv, sd) in enumerate(specs):
        it = T.nginx_route(sd, lv)
        conf = re.search(r"```nginx\n(.*?)\n```", it.messages[0]["content"], re.S).group(1)
        conf = conf.replace("    listen 80;", f"    listen 127.0.0.1:{20000 + i};")
        pools |= set(re.findall(r"http://([a-z]+_pool)", conf))
        items.append((lv, sd, 20000 + i, conf, it.meta["uris"], it.meta["expected"]))
    ups = [f"upstream {p} {{ server 127.0.0.1:{30000 + j}; }}\nserver {{ listen 127.0.0.1:{30000 + j}; "
           f"location / {{ return 200 \"{p} $request_uri\"; }} }}" for j, p in enumerate(sorted(pools))]
    conf = ("worker_processes 1;\nerror_log /dev/stderr error;\nevents { worker_connections 4096; }\nhttp {\n access_log off;\n"
            " default_type text/plain;\n" + "\n".join(ups) + "\n" + "\n".join(x[3] for x in items) + "\n}\n")
    reqs = [(x[2], u) for x in items for u in x[4]]
    sh = ["nginx -c /w/nginx.conf", "sleep 1", "mkdir -p /tmp/r"]
    for k, (port, u) in enumerate(reqs):   # stdin stays open a moment: busybox nc hangs up at EOF, before the upstream answers
        sh.append(f"( (printf 'GET {u} HTTP/1.0\\r\\nHost: example.lan\\r\\n\\r\\n'; sleep 1) | nc -w 3 127.0.0.1 {port} > /tmp/r/{k} 2>&1 || true ) &")
        if k % 40 == 39:
            sh.append("wait")
    sh += ["wait"] + [f"printf '=== {k}\\n'; cat /tmp/r/{k}; printf '\\n'" for k in range(len(reqs))]
    d = tempfile.mkdtemp()
    open(os.path.join(d, "nginx.conf"), "w").write(conf)
    open(os.path.join(d, "run.sh"), "w").write("\n".join(sh) + "\n")
    out = docker(["-v", f"{d}:/w", "nginx:alpine", "sh", "/w/run.sh"]).stdout
    parts = re.split(r"^=== (\d+)\n", out, flags=re.M)
    resp = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}

    def seen(r):
        head, _, body = r.replace("\r\n", "\n").partition("\n\n")
        m = re.match(r"HTTP/1\.\d (\d{3})", head)
        if not m:
            return "NO RESPONSE"
        if m.group(1) in ("301", "302"):
            return m.group(1) + " " + re.sub(r"^https?://[^/]+", "", re.search(r"^Location: (\S+)", head, re.M | re.I).group(1))
        return body.strip() if m.group(1) == "200" else m.group(1)
    by, k = {}, 0
    for lv, sd, _port, _conf, uris, exp in items:
        for u, e in zip(uris, exp):
            got = seen(resp.get(k, ""))
            k += 1
            ok = got == e if lv >= 7 else got.split(" ")[0] == e   # up to level 6 only the upstream is asked
            by.setdefault(f"L{lv}", [0, 0])
            by[f"L{lv}"][0] += 1
            if not ok:
                by[f"L{lv}"][1] += 1
                print(f"  nginx L{lv} s{sd} GET {u}: ours {e!r}, nginx {got!r}")
    return report("nginx_route vs nginx", by)


def route(n):
    specs = [(lv, s) for lv in (9, 10) for s in range(1, n + 1)] + [(lv, s) for lv in (6, 7, 8) for s in range(1, 9)]
    defaults = {("0", "from all lookup local"), ("32766", "from all lookup main"), ("32767", "from all lookup default")}
    sh, queries = [], []
    for i, (lv, sd) in enumerate(specs):
        it = T.subnet(sd, lv)
        block = re.search(r"```\n(.*?)\n```", it.messages[0]["content"], re.S).group(1)
        rules, routes, table = [], [], None
        for line in block.splitlines():
            m = re.match(r"\$ ip route show table (\S+)", line)
            if line.startswith("$ ip rule show") or m:
                table = m.group(1) if m else "RULES"
            elif line.strip() and table == "RULES":
                pref, rest = line.split(":\t", 1)
                if (pref, rest) not in defaults:
                    rules.append((pref, rest))
            elif line.strip():
                routes.append((table, line))
        pkts = it.meta["packets"] if lv >= 7 else [{"src": a.split(" -> ")[0], "dst": a.split(" -> ")[1], "iif": "in0"}
                                                   for a in it.meta["packets"]]
        devs = (set(re.findall(r"\bdev (\S+)", block)) | set(re.findall(r"\biif (\S+)", block)) | {p["iif"] for p in pkts}
                | {"in0", "wan0"}) - {"lo"}
        ns = f"n{i}"
        sh += [f"ip netns add {ns}", f"ip -n {ns} link set lo up",
               f"ip netns exec {ns} sh -c 'echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter; echo 0 > /proc/sys/net/ipv4/conf/default/rp_filter'"]
        sh += [f"ip -n {ns} link add {shlex.quote(x)} type dummy && ip -n {ns} link set {shlex.quote(x)} up" for x in sorted(devs)]
        sh.append(f"ip netns exec {ns} sh -c 'echo 1 > /proc/sys/net/ipv4/ip_forward'")
        sh += [f"ip -n {ns} addr add {m.group(1)}/32 dev {m.group(2)}" for _t, l in routes for m in [re.match(r"local (\S+) dev (\S+)", l)] if m]
        wan = next((x for x in ("wan0", "ppp0") if x in devs), "wan0")
        sh += [f"ip -n {ns} addr add {p['src']}/32 dev {wan} 2>/dev/null || true" for p in pkts if p["iif"] == "lo"]   # its own address
        sh += [f"ip -n {ns} rule add pref {pf} {rest} || echo SETUP-ERROR" for pf, rest in rules]
        sh += [f"ip -n {ns} route add table {t} {l} || echo SETUP-ERROR" for t, l in routes if not l.startswith("local ")]
        for j, (p, e) in enumerate(zip(pkts, it.meta["expected"])):
            opt = f" from {p['src']}" + ("" if p["iif"] == "lo" else f" iif {p['iif']}")
            opt += f" mark {p['mark']:#x}" if p.get("mark") else ""
            opt += f" ipproto {({'tcp': 6, 'udp': 17})[p['proto']]} dport {p['dport']}" if p.get("proto") else ""
            queries.append((i, j, e, lv, sd, p))
            sh.append(f"printf '=== {i} {j}\\n'; ip -n {ns} -o route get {p['dst']}{opt} 2>&1 | head -1")
        sh.append(f"ip netns del {ns}")
    d = tempfile.mkdtemp()
    open(os.path.join(d, "run.sh"), "w").write("\n".join(sh) + "\n")
    out = docker(["--privileged", "-v", f"{d}:/w", "debian:stable", "sh", "-c",
                  "apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq iproute2 netbase >/dev/null 2>&1 && bash /w/run.sh"]).stdout
    if "SETUP-ERROR" in out:
        print("  route: SETUP-ERROR - a rule or route of the prompt was not accepted")
    got = {(int(m.group(1)), int(m.group(2))): m.group(3) for m in re.finditer(r"^=== (\d+) (\d+)\n(.*)$", out, re.M)}

    def answer(l):
        if l.startswith("local "):
            return "local"
        if l.startswith(("unreachable", "blackhole", "prohibit", "RTNETLINK")) or "Error" in l:
            return "none"
        m = re.search(r"\bdev (\S+)", l)
        return m.group(1) if m else "?"
    by = {}
    for i, j, e, lv, sd, p in queries:
        a = answer(got.get((i, j), ""))
        by.setdefault(f"L{lv}", [0, 0])
        by[f"L{lv}"][0] += 1
        if a != e:
            by[f"L{lv}"][1] += 1
            print(f"  subnet L{lv} s{sd} {p}: ours {e!r}, kernel {a!r} [{got.get((i, j), '')[:120]}]")
    return report("subnet vs ip route get", by)


def shell(n):
    cases = {}
    for level in (9, 10, 7, 8):
        for seed in range(1, n + 1):
            for q in K.KINDS["shell"](seed, level).meta["questions"]:
                cases.setdefault(q["text"], (q, f"L{level}" if level >= 9 else "L7-8"))
    bank = {q["text"]: q for q in K.bank()["questions"] if q["kind"] == "shell"}
    odd = [t for t, q in bank.items() if "\"'" in t and not q["fake"] and q["level"] >= 5 and not K._COMBO_BAD_SH.search(t)]
    for a in odd:   # the part type levels 9-10 leave out, next to every other part
        for b in [p["text"] for p in K._combo_pool910("shell")[1]]:
            for x, y in ((a, b), (b, a)):
                q = K._combine("shell", [bank[x], bank[y]], 9)
                cases.setdefault(q["text"], (q, "left-out part type"))
    texts = list(cases)
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "c"))
    os.makedirs(os.path.join(d, "o"))
    for i, t in enumerate(texts):
        open(os.path.join(d, "c", str(i)), "w").write(t)
    run = ["export LC_ALL=C.UTF-8 LANG=C.UTF-8", "update-alternatives --set awk /usr/bin/gawk >/dev/null",
           f"for i in $(seq 0 {len(texts) - 1}); do for k in 1 2; do w=$(mktemp -d); export HOME=$(mktemp -d); "
           "(cd $w && timeout 20 bash --norc --noprofile -c \"$(cat /w/c/$i)\" > /w/o/$i.$k.out 2> /w/o/$i.$k.err < /dev/null; "
           "echo $? > /w/o/$i.$k.rc); done; done"]
    open(os.path.join(d, "run.sh"), "w").write("\n".join(run) + "\n")
    docker(["-v", f"{d}:/w", "debian:stable", "sh", "-c",
            "apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq jq gawk >/dev/null 2>&1 && bash /w/run.sh"])
    rd = lambda i, k, ext: open(os.path.join(d, "o", f"{i}.{k}.{ext}"), encoding="utf-8", errors="replace").read()
    by = {}
    for i, t in enumerate(texts):
        q, tag = cases[t]
        out, err, rc = rd(i, 1, "out"), rd(i, 1, "err"), int(rd(i, 1, "rc").strip() or 99)
        if q["fake"]:
            ok = rc != 0 and not out.strip() and bool(re.search(r"unrecognized|invalid|unknown|not found|not defined|illegal|error", err, re.I))
        else:
            ok = out == rd(i, 2, "out") and rc == 0 and out.count("\n") == 1 and K.verdict(q, out.strip()) == "right"
        key = f"{tag} {'combination' if q.get('src') == 'combo' else 'single'}{' made-up' if q['fake'] else ''}"
        by.setdefault(key, [0, 0])
        by[key][0] += 1
        if not ok:
            by[key][1] += 1
            print(f"  shell [{key}] {t}: ours {K.oracle_answer(q)!r}, bash: rc={rc} {out!r}")
    return report("knowledge.shell vs bash 5 / GNU", by)


if __name__ == "__main__":
    args = sys.argv[1:]
    n = int(args[args.index("--seeds") + 1]) if "--seeds" in args else 25
    todo = [a for a in args if a in ("nginx", "route", "shell")] or ["nginx", "route", "shell"]
    bad = sum({"nginx": nginx, "route": route, "shell": shell}[t](n) for t in todo)
    sys.exit(1 if bad else 0)
