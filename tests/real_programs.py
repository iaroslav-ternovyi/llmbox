"""The answers llmbox computes with a model of a program, checked against the program itself (needs Docker; pulls
nginx:alpine and debian:stable; the routing check runs a privileged container). Not a test_*.py: slow and networked.

  nginx   techhelp.nginx_route: each generated server block on real nginx; every upstream group is an echo server that
          answers "<name> <$request_uri>", requests go out byte-exact (HTTP/1.0 through nc) - upstream and URI observed
  route   techhelp.subnet (levels 6-10): the `ip rule` / `ip route` lines of the prompt applied in a network namespace of
          a real kernel (dummy interfaces, forwarding on, rp_filter off), answers from `ip route get`
  shell   knowledge.shell (levels 7-10 and the part type levels 9-10 leave out) in bash 5 with GNU coreutils, gawk, jq
  git     techhelp.git_seq (levels 1-10): every item replayed on the local git (no Docker; --image alpine:3.13 runs it
          on that image's git 2.30 instead) in a temp repo with fixed names, dates one minute apart and no user config;
          each command's exit status, each question's own command and the whole final state compared with the emulator.
          2026-09-29: --seeds 320 on git 2.51.2: 3200 items, 132332 commands, 28499 questions; --seeds 40 on git 2.30.6
          (alpine:3.13): 400 items, 16550 commands, 3561 questions - 0 disagreements (the fuzzing before it found two:
          `git checkout HEAD` stays on the branch; a failing pop of a -u stash restores its untracked files anyway)

  gitignore        techhelp.gitignore (levels 1-10) and 2n random trees with random patterns: each repository built with
                   this machine's git (files, ignore files, .git/info/exclude, a commit, edits and deletions after it), then
                   `git status --porcelain -uall`, `git check-ignore -v <path>` and `git add -A` + `git diff --cached`
  gitignore-linux  the same in alpine:3.22 (Linux, a case-sensitive file system)
  js               knowledge.js (levels 1-10): every program and line on Node 22, 5 runs each, 8 in parallel - output that
                   ever differs between runs counts as a disagreement (the generator must not produce races)
  js-linux         the same in the node:22 image;  js-node24: on Node 24 (2 runs), to see what changed since 22
  fs               techhelp.fs_seq (levels 1-10): every item replayed by bash in a fresh /home/dev/proj as uid 1234 (umask
                   022, LC_ALL=C, stdin /dev/null) on coreutils 9.7 / 9.4 / 8.32 (debian:stable, ubuntu:24.04 / 22.04);
                   exit statuses, the tree (types, symlink texts, contents), modes, hard-link groups, $PWD and every
                   question's answer compared with the emulator. 2026-09-29, --seeds 320: 3200 items, 140673 commands,
                   17599 questions, 0 disagreements on each of coreutils 9.7 / 9.4 / 8.32 (bash 5.2.37 / 5.2.21 / 5.1.16)
                   - after fixes for what it found: `cp -a` onto a file with other hard links replaces it (`cp -r`
                   writes into it), `cp -a` keeps hard links between names of one symlink, a `cp -rL` failure one
                   level down fails the whole command. A Docker that goes away mid-run shows as "[run] no output".

Run: python3 tests/real_programs.py [nginx|route|shell|git|gitignore|gitignore-linux|js|js-linux|js-node24|fs ...] [--seeds N] [--image IMAGE]
2026-09-29, --seeds 25: 668 / 495 / 1296 checked, 0 wrong - after the fix it found (a rewrite empties $1 even when its
regex does not match); nginx 1.31.6, kernel 6.17 + iproute2 6.15, bash 5.2.37 / coreutils 9.7 / gawk 5.2.1 / jq 1.7.
2026-09-29, gitignore --seeds 210: 2520 repositories, 19972 answers, 0 wrong on git 2.51.2 (macOS) and 2.49.1 (Alpine)
- after fixes for what it found (check-ignore prints nothing for a directory holding a tracked file; the staged list needs
--no-renames when files share content). js --seeds 50: 4000 questions x 5 runs on Node 22.22.3, 0 wrong, 0 varied (the
first version had 2 programs whose output varied under load: a timer set later with a shorter delay; such orders are no
longer generated); node:22 (22.23.3) --seeds 10: 834, Node 24.14.1 --seeds 15: 1235, 0 wrong.
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


FS_IMAGES = ("debian:stable", "ubuntu:24.04", "ubuntu:22.04")   # coreutils 9.7 / 9.4 / 8.32, bash 5.2 / 5.2 / 5.1


def _fs_item_script(it) -> str:
    """One techhelp.fs_seq item as bash: a fresh /home/dev/proj, the starting tree, each numbered command with its exit
    status, the shell's $PWD, the questions asked from the project directory, then every entry (type, mode, inode,
    symlink target) and every regular file's bytes."""
    from llmbox.suite import techhelp_fs as FSM
    m = it.meta
    sh = ["cd /home/dev || exit 1", "chmod -R u+rwX proj 2>/dev/null; rm -rf proj; mkdir proj && cd proj || exit 1"]
    sh += [f"{{ {c} ; }} >/dev/null 2>&1 || echo '@@SETUPFAIL {i}'" for i, c in enumerate(m["setup"])]
    sh += [f"{{ {c} ; }} >/dev/null 2>&1; echo \"@@RC {i + 1} $?\"" for i, c in enumerate(m["commands"])]
    sh += ['echo "@@PWD $PWD"', f"cd {FSM.ROOT} || exit 1"]
    run = {"cat": "cat", "readlink": "readlink -f", "ls": "LC_ALL=C ls"}
    for j, (k, p) in enumerate(zip(m["kinds"][2:], m["paths"])):
        if k == "pwd":
            continue
        sh.append(f"{{ {run[k]} {p} ; }} > /tmp/q 2>/dev/null; echo \"@@Q {j} $? $(base64 -w0 /tmp/q)\"")
    sh += ["find . -mindepth 1 -printf '@@E %y %m %i %p\\t%l\\n'", "chmod -R u+rX . 2>/dev/null",
           "find . -mindepth 1 -type f -print | while IFS= read -r p; do echo \"@@F $p $(base64 -w0 < \"$p\")\"; done"]
    return "\n".join(sh) + "\n"


def _fs_real(image: str, scripts: list[str], workers: int = 6) -> list[str]:
    """Run the item scripts in `image` as uid 1234 (umask 022, stdin /dev/null, a tmpfs home), `workers` containers at
    a time; one output text per script."""
    import base64  # noqa: F401  (used by the caller)
    chunks = [list(range(k, len(scripts), workers)) for k in range(workers)]
    procs = []
    for chunk in chunks:
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "items"))
        for i in chunk:
            open(os.path.join(d, "items", f"{i}.sh"), "w").write(scripts[i])
        open(os.path.join(d, "drv.sh"), "w").write(
            "umask 022\nexport LC_ALL=C\n" + "".join(f"echo '@@ITEM {i}'; bash --norc --noprofile /w/items/{i}.sh < /dev/null\n" for i in chunk))
        open(os.path.join(d, "run.sh"), "w").write(
            "mkdir -p /home/dev && chown 1234:1234 /home/dev && cd / && "
            "exec setpriv --reuid=1234 --regid=1234 --clear-groups env HOME=/home/dev bash /w/drv.sh\n")
        procs.append(subprocess.Popen(["docker", "run", "--rm", "--tmpfs", "/home/dev:rw,exec", "-v", f"{d}:/w", image,
                                       "sh", "/w/run.sh"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True))
    out = [""] * len(scripts)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(len(procs)) as ex:    # drained together: a full pipe must not stall another container
        texts = list(ex.map(lambda p: p.communicate(timeout=7200)[0], procs))
    for text in texts:
        for part in re.split(r"^@@ITEM ", text, flags=re.M)[1:]:
            i, _, body = part.partition("\n")
            out[int(i)] = body
    return out


def _fs_parse(text: str) -> dict:
    import base64
    r = {"rc": {}, "q": {}, "ent": {}, "data": {}, "setupfail": [], "pwd": None}
    for line in text.splitlines():
        if line.startswith("@@RC "):
            _, i, rc = line.split()
            r["rc"][int(i)] = int(rc)
        elif line.startswith("@@Q "):
            parts = line.split(" ", 3)
            r["q"][int(parts[1])] = (int(parts[2]), base64.b64decode(parts[3] if len(parts) > 3 else "").decode())
        elif line.startswith("@@E "):
            head, _, tgt = line[4:].partition("\t")
            y, mode, ino, path = head.split(" ", 3)
            r["ent"][path] = (y, int(mode, 8), int(ino), tgt)
        elif line.startswith("@@F "):
            path, _, b = line[4:].rpartition(" ")
            r["data"][path] = base64.b64decode(b).decode()
        elif line.startswith("@@SETUPFAIL"):
            r["setupfail"].append(line)
        elif line.startswith("@@PWD "):
            r["pwd"] = line[6:]
    return r


def _fs_compare(it, real: dict) -> list[tuple[str, str]]:
    """Where the emulator (the answer key) and the real run disagree: [(category, detail)]."""
    from llmbox.suite import techhelp_fs as FSM
    m = it.meta
    fs, st = FSM.replay(m["setup"], m["commands"])
    bad = []
    if real["setupfail"]:
        return [("setup", " ".join(real["setupfail"]))]
    for i, ok in enumerate(st):
        rc = real["rc"].get(i + 1)
        if rc is None or (rc == 0) != ok:
            bad.append(("command", f"{i + 1}. {m['commands'][i]}: emulator {'ok' if ok else 'fails'}, real rc={rc}"))
    failed = " ".join(str(i) for i in sorted(real["rc"]) if real["rc"][i]) or "NONE"
    if failed != m["expected"][0]:
        bad.append(("failed", f"key {m['expected'][0]!r}, real {failed!r}"))
    lines, modes, inos = [], {}, {}
    for path, (y, mode, ino, tgt) in sorted(real["ent"].items()):
        if y == "d":
            lines.append(path + "/")
        elif y == "l":
            lines.append(f"{path} -> {tgt}")
        else:
            data = real["data"].get(path, "")
            try:
                lines.append(f"{path} = {FSM.content(data)}")
            except FSM.Unsupported:
                lines.append(f"{path} = {data!r}")
        if y != "l":
            modes[path] = mode
        inos.setdefault(ino, []).append(path)
    emu = fs.listing()
    if sorted(lines) != sorted(emu) or " ; ".join(emu) != m["expected"][1]:
        miss, extra = sorted(set(emu) - set(lines)), sorted(set(lines) - set(emu))
        bad.append(("tree", f"emulator only {miss[:6]}, real only {extra[:6]}"))
    em_modes, em_ino = {}, {}

    def rec(d, pre):
        for name in sorted(d.ents):
            n = d.ents[name]
            p = pre + name
            if n.kind != "l":
                em_modes[p] = n.mode & 0o7777
            em_ino.setdefault(n.ino, []).append(p)
            if n.kind == "d":
                rec(n, p + "/")
    rec(fs.top, "./")
    for p in sorted(set(modes) & set(em_modes)):
        if modes[p] != em_modes[p]:
            bad.append(("mode", f"{p}: emulator {em_modes[p]:o}, real {modes[p]:o}"))
    if sorted(map(sorted, inos.values())) != sorted(map(sorted, em_ino.values())):
        bad.append(("hard links", f"{[g for g in inos.values() if len(g) > 1]} vs {[g for g in em_ino.values() if len(g) > 1]}"))
    if real["pwd"] != fs.pwd:
        bad.append(("pwd", f"emulator {fs.pwd}, real {real['pwd']}"))
    for j, (k, p) in enumerate(zip(m["kinds"][2:], m["paths"])):
        rc, out = real["q"].get(j, (None, ""))
        if k == "pwd":
            got = real["pwd"]
        elif rc != 0:
            got = "ERROR"
        elif k == "cat":
            try:
                got = FSM.content(out)
            except FSM.Unsupported:
                got = repr(out)
        elif k == "readlink":
            got = out.strip()
        else:
            got = " ".join(sorted(out.split())) or "(empty)"
        if got != m["expected"][2 + j]:
            bad.append(("question", f"{k} {p}: key {m['expected'][2 + j]!r}, real {got!r}"))
    return bad


def fs(n):
    """techhelp.fs_seq: every level's items replayed by bash with GNU coreutils in a fresh directory, as an ordinary
    user, on coreutils 9.7 (debian:stable), 9.4 and 8.32; exit statuses, the tree (types, symlink targets, contents),
    modes, hard-link groups, $PWD and the question answers compared with the emulator's."""
    from llmbox.suite import techhelp as T
    items = [T.KINDS["fs_seq"](s, lv) for lv in range(1, T.MAX_LEVEL + 1) for s in range(1, n + 1)]
    scripts = [_fs_item_script(it) for it in items]
    n_q = sum(len(it.meta["kinds"]) for it in items)
    n_c = sum(len(it.meta["commands"]) for it in items)
    total = 0
    for image in FS_IMAGES:
        outs = _fs_real(image, scripts)
        by = {}
        shown = 0
        for it, out in zip(items, outs):
            lv = f"L{it.meta['level']}"
            by.setdefault(lv, [0, 0])
            by[lv][0] += 1
            diff = _fs_compare(it, _fs_parse(out)) if out else [("run", "no output")]
            if diff:
                by[lv][1] += 1
                for cat, det in diff[:3]:
                    if shown < 40:
                        print(f"  {image} {it.id} [{cat}] {det}")
                        shown += 1
        print(f"{image}: {len(items)} items, {n_c} commands, {n_q} questions")
        total += report(f"fs_seq vs {image}", by)
    return total


# ---- techhelp.gitignore: each generated repository built for real ----------------------------------------------------

def _git_script(key, spec, cis):
    """bash that builds one repository (files, empty directories, ignore files, a commit of the tracked files, edits and
    deletions after it, .git/info/exclude) and prints the untracked listing, `git check-ignore -v` of each path and what
    `git add -A` staged. Every file's content is its own path, and the staged list is taken without rename detection."""
    q = shlex.quote
    files = set(spec["files"]) | set(spec["deleted"]) | {b + ".gitignore" for b in spec["ignores"]}
    sh = ['d=$(mktemp -d); cd "$d" && git init -q && git config core.ignorecase false']
    dirs = sorted({f.rsplit("/", 1)[0] for f in files if "/" in f} | {e.rstrip("/") for e in spec["empty_dirs"]})
    if dirs:
        sh.append("mkdir -p -- " + " ".join(q(x) for x in dirs))
    for f in sorted(files):
        base = f[:-len(".gitignore")] if f.endswith(".gitignore") else None
        body = spec["ignores"][base] if base is not None and base in spec["ignores"] else f + "\n"
        sh.append(f"printf %s {q(body)} > {q(f)}")
    if spec["tracked"]:
        sh.append("git add -f -- " + " ".join(q(t) for t in spec["tracked"]) + " && git commit -qm init")
    if spec.get("exclude") is not None:
        sh.append(f"printf %s {q(spec['exclude'])} > .git/info/exclude")
    sh += [f"printf 'edit\\n' >> {q(m)}" for m in spec["modified"]] + [f"rm -f -- {q(x)}" for x in spec["deleted"]]
    sh.append(f"echo '=== {key} U'; git status --porcelain -z --untracked-files=all | tr '\\0' '\\n'")
    for k, p in enumerate(cis):
        sh.append(f"echo '=== {key} C{k}'; git check-ignore -v -- {q(p)}")
    sh.append(f"git add -A; echo '=== {key} A'; git diff --cached --no-renames --name-only -z | tr '\\0' '\\n'")
    sh.append('cd /; rm -rf "$d"')
    return "\n".join(sh)


def _git_answers(out):
    """{(key, part): lines} from the script output."""
    got, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"^=== (\S+) (\S+)$", line)
        if m:
            cur = (m.group(1), m.group(2))
            got[cur] = []
        elif cur is not None and line:
            got[cur].append(line)
    return got


def _git_cases(n):
    """(key, level tag, repo spec, questions, expected answers): the generated items of every level, then random trees
    with random patterns (tests every pattern form the generator can write, in combinations it never plans)."""
    from llmbox.suite import techhelp_git as G
    cases = []
    for lv in range(1, 11):
        for sd in range(1, n + 1):
            it = T.KINDS["gitignore"](sd, lv)
            cases.append((f"L{lv}s{sd}", f"L{lv}", it.meta["repo"], it.meta["questions"], it.meta["expected"]))
    import random
    for sd in range(n * 2):
        spec = _fuzz_tree(random.Random(f"fuzz/{sd}"))
        repo = G.Repo(spec["files"], spec["ignores"], spec["exclude"], spec["tracked"], spec["modified"], spec["deleted"],
                      spec["empty_dirs"])
        paths = sorted(repo.files | repo.deleted) + sorted(d[:-1] for d in repo.dirs if d)
        qs = [{"q": "untracked", "under": ""}, {"q": "add", "under": ""}] + [{"q": "check-ignore", "path": p} for p in paths]
        exp = [", ".join(sorted(repo.untracked())) or "NONE", ", ".join(sorted(repo.add_all())) or "NONE"] + \
              [repo.check_ignore(p) for p in paths]
        cases.append((f"F{sd}", "random", spec, qs, exp))
    return cases


def _fuzz_tree(r):
    DIRS = ["a", "b", "build", "logs", "src", "x", "a.d", "tmp", "Out"]
    FILES = ["f.txt", "a.log", "b.LOG", "keep.log", "x", "build", "#n.md", "!b.txt", "n.md", "c1.txt", "c22.txt", "a.bak",
             "a.tmp", ".env", "q.py", "q.pyc", "KEEP.txt", "a", "logs", "t.o", "out"]
    ATOMS = ["*.log", "a", "b/", "/build", "build/", "logs/", "logs/*", "src/*", "x", "/x", "a/**", "**/b", "a/**/x", "**/logs/",
             "*.d", "c?.txt", "c[0-9].txt", "c[!0-9]*.txt", "c[^1]*.txt", "c[[:digit:]]*", "\\#n.md", "#n.md", "\\!b.txt",
             "!b.txt", "*.tmp   ", "*.bak\\ ", "*", "*/", "**", "/*", "a/*.log", "**/*.txt", "tmp/", "BUILD/", "keep.log",
             "*.LOG", "t.[oa]", ".*", ".gitignore", "a/x", "src/**/f.txt", "**/a.d/", "a*", "*b*", "logs", "/a/b/", "b/x",
             "b/**/", "**/", "[a-c].log", "*.[!l]*"]
    dirs = set()
    for _ in range(r.randint(1, 6)):
        parts = [r.choice(DIRS) for _ in range(r.randint(1, 3))]
        dirs |= {"/".join(parts[:i]) for i in range(1, len(parts) + 1)}
    low, files = {d.lower() for d in dirs}, {}
    for _ in range(r.randint(4, 18)):
        f = r.choice([""] + sorted(x + "/" for x in dirs)) + r.choice(FILES)
        if f.lower() not in low and f.lower() not in files:
            files[f.lower()] = f
    files = set(files.values())
    ignores = {}
    for d in [""] + sorted({f.rsplit("/", 1)[0] + "/" for f in files if "/" in f}):
        if d and r.random() < 0.55:
            continue
        lines = []
        for _ in range(r.randint(1, 7)):
            a = r.choice(ATOMS)
            lines += [""] * (r.random() < 0.08) + ["# note"] * (r.random() < 0.05)
            lines.append("!" + a if r.random() < 0.25 and not a.startswith(("!", "#", "\\")) else a)
        ignores[d] = "\n".join(lines) + "\n"
    exclude = "\n".join(r.choice(ATOMS) if r.random() > 0.3 else "!" + r.choice(ATOMS[:20]) for _ in range(r.randint(1, 3))) + "\n" \
        if r.random() < 0.4 else None
    fl = sorted(files)
    tracked = [f for f in fl if r.random() < 0.25]
    modified = [f for f in tracked if r.random() < 0.5]
    deleted = [f for f in tracked if f not in modified and r.random() < 0.3]
    tracked = sorted(set(tracked) | {d + ".gitignore" for d in ignores if r.random() < 0.3})
    alll = {f.lower() for f in files}
    empty = [e for e in (r.choice(DIRS) + "/" + r.choice(DIRS) + "/" for _ in range(r.randint(0, 2)))
             if not any(f.startswith(e.lower()) or e.lower().startswith(f + "/") for f in alll | low)]
    return {"files": sorted(files - set(deleted)), "ignores": ignores, "exclude": exclude, "tracked": tracked,
            "modified": modified, "deleted": deleted, "empty_dirs": empty}


def _git_compare(cases, out, name):
    got = _git_answers(out)
    by = {}
    for key, tag, spec, qs, exp in cases:
        cis = [q["path"] for q in qs if q["q"] == "check-ignore"]
        k = 0
        for q, e in zip(qs, exp):
            if q["q"] == "check-ignore":
                lines = got.get((key, f"C{k}"), [])
                k += 1
                m = re.match(r"^(.*?):(\d+):.*\t", lines[0]) if lines else None
                real = f"{m.group(1)}:{m.group(2)}" if m else "NONE"
                ok = real == e
            else:
                part = "U" if q["q"] == "untracked" else "A"
                lines = got.get((key, part))
                if lines is None:
                    real, ok = "MISSING", False
                else:
                    paths = {l[3:] if part == "U" and l.startswith("?? ") else l for l in lines
                             if part == "A" or l.startswith("?? ")}
                    paths = {p for p in paths if p.startswith(q["under"])}
                    real = ", ".join(sorted(paths)) or "NONE"
                    ok = real == e
            tag_q = f"{tag} {q['q']}"
            by.setdefault(tag_q, [0, 0])
            by[tag_q][0] += 1
            if not ok:
                by[tag_q][1] += 1
                print(f"  {name} {key} {q}: ours {e!r}, git {real!r}")
        assert len(cis) == k
    return report(name, by)


def gitignore(n):
    """Every generated item of levels 1-10 (n seeds each) and 2n random trees, on this machine's git (macOS: the file
    system folds case, so paths are unique ignoring case and core.ignoreCase is set to false as on Linux)."""
    cases = _git_cases(n)
    chunks = [cases[i::8] for i in range(8)]
    d = tempfile.mkdtemp()
    home = tempfile.mkdtemp()
    env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", LC_ALL="C")
    from concurrent.futures import ThreadPoolExecutor

    def run(i):
        path = os.path.join(d, f"{i}.sh")
        open(path, "w").write("\n".join(_git_script(key, spec, [q["path"] for q in qs if q["q"] == "check-ignore"])
                                        for key, _t, spec, qs, _e in chunks[i]) + "\n")
        return subprocess.run(["bash", path], capture_output=True, text=True, env=env, timeout=3600).stdout
    with ThreadPoolExecutor(8) as ex:
        out = "\n".join(ex.map(run, range(8)))
    ver = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
    print(f"gitignore: {ver}, {len(cases)} repositories")
    return _git_compare(cases, out, "gitignore vs git")


def gitignore_linux(n):
    """The same on Linux (Alpine's git in a container, a case-sensitive file system)."""
    cases = _git_cases(n)
    d = tempfile.mkdtemp()
    for i in range(8):
        open(os.path.join(d, f"{i}.sh"), "w").write("\n".join(
            _git_script(key, spec, [q["path"] for q in qs if q["q"] == "check-ignore"]) for key, _t, spec, qs, _e in cases[i::8]) + "\n")
    run = ("apk add -q git bash >/dev/null 2>&1; git --version > /w/version; export HOME=/tmp/h XDG_CONFIG_HOME=/tmp/h "
           "GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t "
           "GIT_COMMITTER_EMAIL=t@t LC_ALL=C; mkdir -p /tmp/h; for i in 0 1 2 3 4 5 6 7; do bash /w/$i.sh > /w/$i.out 2>/dev/null & "
           "done; wait")
    docker(["-v", f"{d}:/w", "alpine:3.22", "sh", "-c", run])
    out = "\n".join(open(os.path.join(d, f"{i}.out")).read() for i in range(8) if os.path.exists(os.path.join(d, f"{i}.out")))
    print(f"gitignore-linux: {open(os.path.join(d, 'version')).read().strip()}, {len(cases)} repositories")
    return _git_compare(cases, out, "gitignore vs git on Linux")


# ---- knowledge.js: every generated program on real Node, several times --------------------------------------------------

NODE22 = os.path.expanduser("~/.nvm/versions/node/v22.22.3/bin/node")


def _js_cases(n):
    from llmbox.suite import knowledge as KN
    cases = {}
    for lv in range(1, 11):
        for sd in range(1, n + 1):
            for q in KN.KINDS["js"](sd, lv).meta["questions"]:
                cases.setdefault(q["text"], (q, f"L{lv}"))
    return [(t, q, tag) for t, (q, tag) in cases.items()]


def _js_judge(q, runs):
    """(agrees, varied, what node printed) for one question and its runs [(rc, stdout, stderr)]."""
    rc, out, err = runs[0]
    varied = len({(x[0], x[1]) for x in runs}) > 1
    if q["fake"]:
        ok = rc != 0 and out == "" and "TypeError" in err and "is not a function" in err
    elif q["mode"] == "seq":   # a program that dies of an unhandled rejection: exit code 1, the rejection on stderr
        ok = (rc != 0 and "Error: x" in err if q["accept"].get("crash") else rc == 0) and \
            out.split() == q["accept"]["seq"] and out.count("\n") == len(q["accept"]["seq"])
    elif q["mode"] == "line":
        ok = rc == 0 and out == q["accept"]["line"] + "\n"
    else:
        ok = rc != 0 and out == "" and f"{q['accept']['exc']}:" in err
    return ok and not varied, varied, (out if out else err.strip().splitlines()[-1:] if err else "")


def _js_report(name, cases, results):
    by, varied_n = {}, 0
    for (t, q, tag), runs in zip(cases, results):
        ok, varied, seen = _js_judge(q, runs)
        key = f"{tag} {'made-up' if q['fake'] else q['mode']}"
        by.setdefault(key, [0, 0])
        by[key][0] += 1
        varied_n += varied
        if not ok:
            by[key][1] += 1
            print(f"  {name} [{key}]{' VARIED' if varied else ''}\n{t}\n    ours: {q['accept']!r}\n    node: {seen!r}")
    print(f"{name}: output varied between runs for {varied_n} programs")
    return report(name, by)


def js(n, node=NODE22, runs=5):
    """Every question of levels 1-10 (n seeds each), run `runs` times with this Node (in parallel: timing noise is part
    of the test)."""
    cases = _js_cases(n)
    d = tempfile.mkdtemp()
    for i, (t, _q, _tag) in enumerate(cases):
        open(os.path.join(d, f"{i}.js"), "w").write(t + "\n")
    from concurrent.futures import ThreadPoolExecutor

    def one(i):
        out = []
        for _ in range(runs):
            p = subprocess.run([node, os.path.join(d, f"{i}.js")], capture_output=True, text=True, timeout=60, cwd=d)
            out.append((p.returncode, p.stdout, p.stderr))
        return out
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(one, range(len(cases))))
    ver = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    progs = sum(1 for _t, q, _g in cases if q["mode"] == "seq")
    print(f"js: node {ver}, {len(cases)} questions ({progs} event-loop programs), {runs} runs each")
    return _js_report(f"knowledge.js vs node {ver}", cases, results)


def js_linux(n, runs=5):
    """The same in the node:22 image (Linux)."""
    cases = _js_cases(n)
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "o"))
    for i, (t, _q, _tag) in enumerate(cases):
        open(os.path.join(d, f"{i}.js"), "w").write(t + "\n")
    sh = (f"node --version > /w/version; cd /w; for i in $(seq 0 {len(cases) - 1}); do for k in $(seq 1 {runs}); do "
          "node $i.js > o/$i.$k.out 2> o/$i.$k.err; echo $? > o/$i.$k.rc; done & "
          "if [ $((i % 8)) -eq 7 ]; then wait; fi; done; wait")
    docker(["-v", f"{d}:/w", "node:22", "bash", "-c", sh])
    rd = lambda i, k, e: open(os.path.join(d, "o", f"{i}.{k}.{e}"), errors="replace").read()
    results = [[(int(rd(i, k, "rc").strip() or 99), rd(i, k, "out"), rd(i, k, "err")) for k in range(1, runs + 1)]
               for i in range(len(cases))]
    ver = open(os.path.join(d, "version")).read().strip()
    print(f"js-linux: node {ver} (node:22 image), {len(cases)} questions, {runs} runs each")
    return _js_report(f"knowledge.js vs node {ver} on Linux", cases, results)


# ---- techhelp.git_seq: each generated command sequence replayed on real git ---------------------------------------

GIT_T0 = 1767225600   # 2026-01-01; every command one minute after the previous one (git log order = creation order)


def git_script(setup, cmds, probes=()):
    """bash script (argument: an empty work dir) that replays an item's setup and numbered commands on real git with a
    clean environment and fixed identities and dates, runs the commands the questions ask about (probes), then dumps
    the whole state."""
    sh = ['W="$1"', 'mkdir -p "$W/home" "$W/repo" && cd "$W/repo" || exit 3',
          'export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null HOME="$W/home" XDG_CONFIG_HOME="$W/home" LC_ALL=C LANG=C '
          'GIT_AUTHOR_NAME=dev GIT_AUTHOR_EMAIL=dev@example.com GIT_COMMITTER_NAME=dev GIT_COMMITTER_EMAIL=dev@example.com '
          'GIT_EDITOR=true GIT_MERGE_AUTOEDIT=no GIT_PAGER=cat PAGER=cat GIT_TERMINAL_PROMPT=0']
    t = GIT_T0
    for c in setup:
        t += 60
        c = "git -c init.defaultBranch=main init -q ." if c == "git init" else c
        sh.append(f"export GIT_AUTHOR_DATE='{t} +0000' GIT_COMMITTER_DATE='{t} +0000'; {{ {c}; }} >/dev/null 2>&1 || echo @@setupfail")
    for i, c in enumerate(cmds, 1):
        t += 60
        sh.append(f"export GIT_AUTHOR_DATE='{t} +0000' GIT_COMMITTER_DATE='{t} +0000'; {{ {c}; }} >/dev/null 2>&1 </dev/null; "
                  f"echo \"@@rc {i} $?\"")
    for k, c in enumerate(probes):
        sh.append(f"echo '@@q {k}'; {c} 2>/dev/null; echo \"@@qrc {k} $?\"")
    sh += ["echo @@version; git --version",
           "echo @@status; git status --porcelain --untracked-files=all --no-renames",
           "echo @@branch; git branch --show-current",
           "echo @@head; git log -1 --format=%s",
           "echo @@stash; git stash list --format=%gs",
           "for b in $(git for-each-ref --format='%(refname:short)' refs/heads/); do echo \"@@log $b\"; git log --format=%s \"$b\"; "
           "echo \"@@count $b\"; git rev-list --count \"$b\"; done",
           "for f in *; do [ -f \"$f\" ] && { echo \"@@wt $f\"; cat \"$f\"; }; done",
           "for f in $(git ls-files); do echo \"@@idx $f\"; git show \":$f\"; done",
           "for b in $(git for-each-ref --format='%(refname:short)' refs/heads/); do for f in $(git ls-tree --name-only \"$b\"); do "
           "echo \"@@tree $b:$f\"; git show \"$b:$f\"; done; done"]
    return "\n".join(sh) + "\n"


def git_parse(out, n_cmds):
    st = {"rcs": {}, "status": [], "branch": [], "head": [], "stash": [], "version": [], "log": {}, "count": {}, "wt": {},
          "idx": {}, "tree": {}, "q": {}, "qrc": {}}
    sec = None
    for line in out.splitlines():
        if line.startswith("@@rc "):
            _, i, rc = line.split()
            st["rcs"][int(i)] = int(rc)
        elif line.startswith("@@qrc "):
            _, i, rc = line.split()
            st["qrc"][int(i)] = int(rc)
            sec = None
        elif line.startswith("@@"):
            k, _, arg = line[2:].partition(" ")
            if arg:
                st[k][arg] = []
                sec = st[k][arg]
            else:
                sec = st[k]
        elif sec is not None:
            sec.append(line)
    return {"rcs": [st["rcs"].get(i) for i in range(1, n_cmds + 1)], "status": st["status"],
            "branch": (st["branch"] or [""])[0] or None, "head": (st["head"] or [None])[0], "stash": st["stash"],
            "logs": st["log"], "counts": {b: int(v[0]) for b, v in st["count"].items()}, "wt": st["wt"], "index": st["idx"],
            "trees": st["tree"], "version": " ".join(st["version"]),
            "q": {int(k): v for k, v in st["q"].items()}, "qrc": st["qrc"]}


def git_real_answer(q, real, k):
    """What git printed for question k (its probe): lines, None when the command failed (the file is not there)."""
    kind = q[0]
    if kind == "failed":
        return [i for i, rc in enumerate(real["rcs"], 1) if rc != 0]
    out, rc = real["q"].get(k), real["qrc"].get(k)
    if out is None or rc is None:
        return "NO PROBE OUTPUT"
    if kind in ("log", "cat", "index", "show"):
        return out if rc == 0 else None
    if kind == "current":
        return out[0] if out and out[0] else None
    if kind == "head":
        return out[0] if out else None
    if kind == "count":
        return int(out[0]) if rc == 0 and out else None
    return out                                           # status, stash, branches


def git(n, image=None):
    """techhelp.git_seq: every item of levels 1-10 x n seeds replayed on real git (the local one, or with --image in a
    Docker container, e.g. debian:bullseye for git 2.30): each command's exit status (success or failure), every
    question's answer, and the whole final state (status, HEAD, every branch's log and files, index, working tree,
    stash) compared with the emulator."""
    from concurrent.futures import ThreadPoolExecutor
    from llmbox.suite import techhelp_gitseq as TG
    specs = [(lv, s) for lv in range(1, 11) for s in range(1, n + 1)]
    gens = {}
    for lv, s in specs:
        g = TG._generate(s, lv)
        gens[(lv, s)] = (g, g.questions())
    d = tempfile.mkdtemp(prefix="gitseq")
    os.makedirs(os.path.join(d, "s"))
    os.makedirs(os.path.join(d, "o"))
    for k, (lv, s) in enumerate(specs):
        g, qs = gens[(lv, s)]
        probes = [TG.question_cmd(q) or "true" for q in qs]
        open(os.path.join(d, "s", f"{k}.sh"), "w").write(git_script(g.setup, g.cmds, probes))
    if image:
        run = (f"cd /w && ls s | sed 's/.sh$//' | xargs -P 8 -I{{}} sh -c 'bash s/{{}}.sh /tmp/r{{}} > o/{{}}.out 2>&1; rm -rf /tmp/r{{}}'")
        p = docker(["-v", f"{d}:/w", image, "sh", "-c", "if command -v apk >/dev/null; then apk add -q --no-cache git bash >/dev/null; "
                    "else apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null; fi; " + run])
        if not os.listdir(os.path.join(d, "o")):
            raise RuntimeError(f"no output from {image}: {p.stderr[-500:]}")
    else:
        def one(k):
            w = tempfile.mkdtemp(prefix="gitrepo")
            out = subprocess.run(["bash", os.path.join(d, "s", f"{k}.sh"), w], capture_output=True, text=True, timeout=600).stdout
            open(os.path.join(d, "o", f"{k}.out"), "w").write(out)
            subprocess.run(["rm", "-rf", w])
        with ThreadPoolExecutor(10) as ex:
            list(ex.map(one, range(len(specs))))
    by, version, shown = {}, set(), 0
    for k, (lv, s) in enumerate(specs):
        g, qs = gens[(lv, s)]
        out = open(os.path.join(d, "o", f"{k}.out")).read()
        real = git_parse(out, len(g.cmds))
        version.add(real["version"])
        row = by.setdefault(f"L{lv}", {"items": 0, "commands": 0, "questions": 0, "bad_rc": 0, "bad_q": 0, "bad_state": 0})
        row["items"] += 1
        row["commands"] += len(g.cmds)
        problems = ["setup failed"] if "@@setupfail" in out else []
        for i, (a, b) in enumerate(zip(g.rcs, real["rcs"]), 1):
            if b is None or (a == 0) != (b == 0):
                row["bad_rc"] += 1
                problems.append(f"exit status of {i} `{g.cmds[i - 1]}`: ours {a}, git {b}")
        lines = lambda v: None if v is None else v.splitlines()   # noqa: E731
        for j, q in enumerate(qs):
            row["questions"] += 1
            ours, theirs = g.answer(q), git_real_answer(q, real, j)
            if ours != theirs:
                row["bad_q"] += 1
                problems.append(f"question {q}: ours {ours!r}, git {theirs!r}")
        R = g.repo
        emu = {"status": R.status(), "branch": R.branch, "head": R.commits[R.head()].subject,
               "stash": [e["msg"] for e in R.stash], "logs": {b: R.log(c) for b, c in R.branches.items()},
               "wt": {p: lines(v) for p, v in R.wt.items()}, "index": {p: lines(v) for p, v in R.index.items()},
               "trees": {f"{b}:{p}": lines(v) for b, c in R.branches.items() for p, v in R.commits[c].tree.items()}}
        for key, v in emu.items():
            if v != real[key]:
                row["bad_state"] += 1
                problems.append(f"state {key}: ours {v!r}, git {real[key]!r}")
        if problems and shown < 5:
            shown += 1
            print(f"  git_seq L{lv} s{s}:")
            for i, c in enumerate(g.cmds, 1):
                print(f"    {i:3d} [{g.rcs[i - 1]}] {c}")
            for p in problems[:8]:
                print(f"    ! {p[:400]}")
    subprocess.run(["rm", "-rf", d])
    print("git_seq vs " + "; ".join(sorted(version)) + ": " + json.dumps(by))
    bad = sum(r["bad_rc"] + r["bad_q"] + r["bad_state"] for r in by.values())
    print(f"git_seq vs real git: {sum(r['items'] for r in by.values())} items, {sum(r['commands'] for r in by.values())} "
          f"commands, {sum(r['questions'] for r in by.values())} questions checked; {sum(r['bad_rc'] for r in by.values())} "
          f"exit statuses, {sum(r['bad_q'] for r in by.values())} answers, {sum(r['bad_state'] for r in by.values())} state "
          f"parts disagree")
    return bad


if __name__ == "__main__":
    args = sys.argv[1:]
    n = int(args[args.index("--seeds") + 1]) if "--seeds" in args else 25
    image = args[args.index("--image") + 1] if "--image" in args else None
    modes = {"nginx": nginx, "route": route, "shell": shell, "git": lambda k: git(k, image), "gitignore": gitignore,
             "gitignore-linux": gitignore_linux, "js": js, "js-linux": js_linux,
             "js-node24": lambda k: js(k, os.path.expanduser("~/.nvm/versions/node/v24.14.1/bin/node"), 2), "fs": fs}
    todo = [a for a in args if a in modes] or ["nginx", "route", "shell"]
    bad = sum(modes[t](n) for t in todo)
    sys.exit(1 if bad else 0)
