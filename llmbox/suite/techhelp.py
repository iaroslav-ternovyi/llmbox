"""Tech help: the questions people who run local models ask about their own machines - why a service is not reachable,
which nginx location answers, what a chmod leaves behind, which subnet routes a packet, which service broke first.
The largest everyday category of local-LLM users after coding and agents ("homelab", 18.5% of 10k use cases in local-LLM
communities, docs/usage-research.md). Every answer is exact and computed here from the generated config, so no judge
is needed, and every seed is a fresh machine, so nothing can be memorized. 5 levels per kind.
"""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta, timezone

from .common import Item, final_answer, multi_check, num, rng
from .techhelp_gitseq import git_seq

BLOCK = "techhelp"
CONVENTIONAL = {"postgres": 5432, "db": 5432, "redis": 6379, "cache": 6379, "grafana": 3000, "minio": 9000, "keycloak": 8080,
                "queue": 5672, "search": 9200, "nginx": 80, "proxy": 80, "worker": 8081, "auth": 8080, "api": 8000, "web": 80,
                "app": 3000}
INSTR = "\n\nThink it through, then finish with a final line exactly in the form:\nANSWER: <answer>"
# v0.10-dev5: every item asks several questions about the same machine (config, table, logs), credit per question - one
# 0/1 answer per item made a 6-item block swing a run by 30 points.


def _instr(n: int) -> str:
    return ("\n\nThink it through, then finish with one final line per question, exactly in the form:\n"
            + "\n".join(f"ANSWER {i + 1}: <answer>" for i in range(n)))


def _ask(questions: list[str]) -> str:
    return "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))


def _set_check(expected: set[str]):
    """A set of tokens in any order and separator ('8080, 8443' == '8443 8080'); NONE for the empty set."""
    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        if a is None:
            return 0.0
        toks = [re.sub(r"^\d+\.\d+\.\d+\.\d+:", "", t) for t in re.findall(r"[\w.:/-]+", a.lower())]   # 127.0.0.1:9000 -> 9000
        got = {t for t in toks if t not in {"and", "port", "ports", "on", "host"}}
        want = {e.lower() for e in expected} or {"none"}
        return 1.0 if got == want else 0.0
    return check


def _word_check(expected: str):
    def check(text: str, _t=None) -> float:
        a = (final_answer(text) or "").strip().strip("`'\".").lower()
        return 1.0 if a == expected.lower() or re.sub(r"\s+", "", a) == re.sub(r"\s+", "", expected.lower()) else 0.0
    return check


def _octal_check(want: int):
    def check(text: str, _t=None) -> float:
        a = re.findall(r"[0-7]{3,4}", final_answer(text) or "")
        return 1.0 if a and int(a[-1], 8) == want else 0.0
    return check


def _num_check(expected: float):
    def check(text: str, _t=None) -> float:
        v = num(final_answer(text))
        return 1.0 if v is not None and abs(v - expected) < 1e-9 else 0.0
    return check


# ---- docker compose: where is a service reachable from the host --------------------------------------------------------

def compose_port(seed: int, level: int = 3) -> Item:
    """docker compose port publishing. Short and long syntax, IP bindings, `expose` (not published), a bare container
    port (random host port), ${VAR:-default} with a .env file, a second -f file (ports lists concatenate), port ranges,
    network_mode: host. Level 6: compose_expert; 7-8: compose_78."""
    if level >= 9:
        return compose_910(seed, level)
    if level >= 7:
        return compose_78(seed, level)
    if level >= 6:
        return compose_expert(seed, level)
    r = rng(BLOCK, f"compose{level}", seed)
    targets = r.sample(["web", "api", "grafana", "auth", "search", "app"], 3)
    names = targets + r.sample([n for n in CONVENTIONAL if n not in targets], max(1, level - 1))
    tports = dict(zip(targets, r.sample([80, 3000, 8000, 8080, 5000, 9090], 3)))
    # dev6: the same three publishing modes at a level (which service gets which mode is random)
    triple = {1: ["plain", "ip", "plain"], 2: ["ip", "env", "range"], 3: ["env", "range", "host"], 4: ["env", "expose", "plain"],
              5: ["env", "bare", "range"]}[level]
    r.shuffle(triple)
    modes = dict(zip(targets, triple))
    env_file = {}
    published: dict[str, set[str]] = {t: set() for t in targets}
    # disjoint pools: two services never publish the same host port (compose would refuse to start)
    free = {"plain": [8080, 8081, 8443, 18080, 3001], "ip": [9000, 9443, 8888], "env": [8090, 8181, 8282], "envval": [18090, 28080, 9001]}
    used_host = {str(p) for v in free.values() for p in v} | {"7443", "10080", "8880"}
    used_host |= {str(p) for p in range(8000, 8010)} | {str(p) for p in range(9100, 9110)}   # the targets' candidates
    for k in free:
        r.shuffle(free[k])
    lines = ["services:"]
    extra_files = []

    def add_port(svc_ports: list[str], host: str | None, cont: int, ip: str | None = None, long: bool = False):
        if long:
            svc_ports.append(f"      - target: {cont}\n        published: \"{host}\"" + (f"\n        host_ip: {ip}" if ip else ""))
        elif host is None:
            svc_ports.append(f"      - \"{cont}\"")
        else:
            svc_ports.append(f"      - \"{(ip + ':') if ip else ''}{host}:{cont}\"")

    rbase = [(8000, 1), (9100, 2)]
    for svc in r.sample(names, len(names)):
        lines.append(f"  {svc}:")
        lines.append(f"    image: example/{svc}:latest")
        ports: list[str] = []
        if svc in targets:
            mode, tport, pub = modes[svc], tports[svc], published[svc]
            if mode == "plain":
                hp = free["plain"].pop()
                add_port(ports, str(hp), tport, long=level >= 4 and r.random() < 0.5)
                pub.add(str(hp))
            elif mode == "ip":
                hp = free["ip"].pop()
                add_port(ports, str(hp), tport, ip=r.choice(["127.0.0.1", "0.0.0.0"]))
                pub.add(str(hp))
            elif mode == "env":
                var = f"{svc.upper()}_PORT"
                default = free["env"].pop()
                add_port(ports, f"${{{var}:-{default}}}", tport)
                if level >= 3:   # the .env file overrides the default
                    val = free["envval"].pop()
                    env_file[var] = str(val)
                    pub.add(str(val))
                else:
                    pub.add(str(default))
            elif mode == "range":
                base_h, off = rbase.pop(0)
                base_c, span = tport - off, 4
                ports.append(f"      - \"{base_h}-{base_h + span}:{base_c}-{base_c + span}\"")
                pub.add(str(base_h + off))
            elif mode == "host":
                lines.append("    network_mode: host")
                pub.add(str(tport))
            elif mode == "expose":
                lines.append(f"    expose:\n      - \"{tport}\"")
            elif mode == "bare":
                add_port(ports, None, tport)
        else:   # other services publish their usual ports; some on another host port
            if r.random() < 0.7:
                cport = CONVENTIONAL.get(svc, 8000)
                hport = cport if r.random() < 0.6 else r.choice([cport + 1, cport + 10000 if cport < 50000 else cport - 1000])
                if str(hport) not in used_host:
                    used_host.add(str(hport))
                    add_port(ports, str(hport), cport)
        if ports:
            lines.append("    ports:")
            lines.extend(ports)
        if svc != names[0] and r.random() < 0.4:
            lines.append(f"    depends_on:\n      - {names[0]}")
    main = "\n".join(lines) + "\n"
    files = [("compose.yaml", main)]
    if level >= 4:   # a second -f file adds a port to one published target (ports lists concatenate)
        cand = [t for t in targets if modes[t] not in ("host", "expose")]
        if cand:
            t = r.choice(cand)
            hp2 = r.choice([7443, 10080, 8880])
            files.append(("compose.prod.yaml", f"services:\n  {t}:\n    ports:\n      - \"{hp2}:{tports[t]}\"\n"))
            published[t].add(str(hp2))
            extra_files.append("compose.prod.yaml")
    if env_file:
        files.append((".env", "".join(f"{k}={v}\n" for k, v in env_file.items())))
    cmd = "docker compose " + "".join(f"-f {f} " for f in ["compose.yaml"] + extra_files) + "up -d"
    body = "\n\n".join(f"`{n}`:\n```yaml\n{t}```" if n != ".env" else f"`.env`:\n```\n{t}```" for n, t in files)
    qs = [f"On which host port(s) can I reach container port {tports[t]} of the `{t}` service?" for t in targets]
    prompt = (f"A directory contains these files:\n\n{body}\n\nI run `{cmd}` in it, and connect from the host machine.\n"
              + _ask(qs) + "\nIf a service is not reachable from the host at a fixed port, answer NONE for it; if there are several "
              "ports, list them all." + _instr(3))
    return Item(f"{BLOCK}.compose_port.L{level}.{seed}", BLOCK, "compose_port", [{"role": "user", "content": prompt}],
                multi_check([_set_check(published[t]) for t in targets]), max_tokens=32000,
                meta={"expected": [", ".join(sorted(published[t])) or "NONE" for t in targets], "modes": modes, "level": level})


# ---- nginx: which location answers a request ---------------------------------------------------------------------------

def _nginx_match(locs: list[tuple[str, str, str]], uri: str) -> str:
    """nginx location selection: exact '=' wins; else the longest prefix; if that prefix is '^~' it wins; else the first
    matching regex (in config order, '~' case-sensitive, '~*' not); else the longest prefix."""
    for mod, pat, tgt in locs:
        if mod == "=" and uri == pat:
            return tgt
    prefixes = [(pat, mod, tgt) for mod, pat, tgt in locs if mod in ("", "^~") and uri.startswith(pat)]
    best = max(prefixes, key=lambda x: len(x[0]), default=None)
    if best and best[1] == "^~":
        return best[2]
    for mod, pat, tgt in locs:
        if mod == "~" and re.search(pat, uri):
            return tgt
        if mod == "~*" and re.search(pat, uri, re.I):
            return tgt
    return best[2] if best else "404"


def nginx_route(seed: int, level: int = 3) -> Item:
    if level >= 9:
        return nginx_910(seed, level)
    if level >= 7:
        return nginx_78(seed, level)
    if level >= 6:
        return nginx_expert(seed, level)
    r = rng(BLOCK, f"nginx{level}", seed)
    ups = [f"{c}_pool" for c in r.sample(["blue", "green", "amber", "violet", "teal", "coral", "slate", "olive", "ruby", "indigo"], 9)]   # neutral: the name must not give the answer away
    locs = [("", "/", ups[0]), ("", "/api/", ups[1])]
    if level >= 2:
        locs.append(("", "/api/v2/", ups[2]))
        locs.append(("=", "/health", ups[6]))
    if level >= 3:
        locs.append(("~", r"\.php$", ups[5]))
        locs.append(("", "/static/", ups[3]))
    if level >= 4:
        locs[-1] = ("^~", "/static/", ups[3])
        locs.append(("~*", r"\.(jpg|png|webp)$", ups[4]))
    if level >= 5:
        locs.append(("", "/admin", ups[7]))
        locs.append(("~", r"^/api/v[0-9]+/ws", ups[8]))
    r.shuffle(locs)
    uris = ["/", "/index.html", "/api/users", "/api/v2/orders/7", "/health", "/healthz", "/static/app.js", "/static/logo.PNG",
            "/api/v2/avatar.png", "/blog/post.php", "/static/old/page.php", "/admin", "/administrator/login", "/api/v2/ws/chat",
            "/api/v1/ws", "/media/cat.JPG"]
    # pick a request where a naive reading goes wrong at higher levels
    tricky = [u for u in uris if (level < 3) or _nginx_match(locs, u) != _naive(locs, u)]
    # four requests: from level 3 at least half of them where "the longest prefix wins" reads it wrong
    picked = r.sample(tricky, min(len(tricky), 2 if level >= 3 else 4))
    picked += r.sample([u for u in uris if u not in picked], 4 - len(picked))
    r.shuffle(picked)
    expected = [_nginx_match(locs, u) for u in picked]
    conf = ["server {", "    listen 80;", "    server_name example.lan;"]
    for mod, pat, tgt in locs:
        conf.append(f"    location {mod + ' ' if mod else ''}{pat} {{\n        proxy_pass http://{tgt};\n    }}")
    conf.append("}")
    prompt = (f"Here is an nginx server block:\n\n```nginx\n" + "\n".join(conf) + "\n```\n\n"
              "A client sends these requests for Host example.lan. Which upstream handles each? Answer with the upstream name "
              "(the part after http://).\n" + _ask([f"GET {u}" for u in picked]) + _instr(4))
    return Item(f"{BLOCK}.nginx_route.L{level}.{seed}", BLOCK, "nginx_route", [{"role": "user", "content": prompt}],
                multi_check([_word_check(e) for e in expected]), max_tokens=32000,
                meta={"expected": expected, "uris": picked, "level": level})


def _naive(locs, uri):
    """What 'the longest prefix wins, full stop' would answer: the common misreading."""
    prefixes = [(pat, tgt) for mod, pat, tgt in locs if mod in ("", "^~", "=") and uri.startswith(pat)]
    return max(prefixes, key=lambda x: len(x[0]))[1] if prefixes else "404"


# ---- networking: hosts, broadcast, routing, summarisation ------------------------------------------------------------

def subnet(seed: int, level: int = 3) -> Item:
    if level >= 9:
        return routing_910(seed, level)
    if level >= 7:
        return routing_78(seed, level)
    if level >= 6:
        return routing_expert(seed, level)
    r = rng(BLOCK, f"subnet{level}", seed)
    base = ipaddress.ip_address(f"{r.choice([10, 172, 192])}.{r.randint(0, 31) if level > 1 else 168}.{r.randint(0, 255)}.{r.randint(0, 255)}")
    qs, exp, checks = [], [], []
    if level <= 2:   # three networks: usable hosts (1) or broadcast address (2)
        for _ in range(3):
            b = ipaddress.ip_address(int(base) ^ (r.randint(1, 255) << 8) ^ r.randint(0, 255))
            if level == 1:
                net = ipaddress.ip_network(f"{b}/{r.choice([24, 25, 26, 27, 28])}", strict=False)
                qs.append(f"How many usable host addresses does the network {net} have?")
                exp.append(net.num_addresses - 2)
                checks.append(_num_check(exp[-1]))
            else:
                pfx = r.choice([19, 21, 22, 23, 27, 29])
                net = ipaddress.ip_network(f"{b}/{pfx}", strict=False)
                qs.append(f"What is the broadcast address of the network that contains {b}/{pfx}?")
                exp.append(str(net.broadcast_address))
                checks.append(_word_check(exp[-1]))
        prompt = _ask(qs) + _instr(len(qs))
    else:
        # routing table with overlapping routes; longest prefix wins, ties by the lower metric; four destinations
        n_routes = 3 + level
        routes = [("0.0.0.0/0", "wan0", 100)]
        for _ in range(n_routes):
            p = r.choice([8, 12, 16, 20, 22, 24, 26])
            net = ipaddress.ip_network(f"{base}/{p}", strict=False)
            if r.random() < 0.4:   # the sibling network next to it (does not contain the base address)
                net = ipaddress.ip_network((int(net.network_address) ^ (1 << (32 - p)), p))
            routes.append((str(net), r.choice(["eth0", "eth1", "wg0", "br-lan", "tun0"]), r.choice([10, 20, 50, 100, 200])))
        if level >= 4:   # the same prefix twice with different metrics
            p, dev, m = routes[-1]
            routes.append((p, r.choice(["eth2", "wg1"]), m + r.choice([-5, 5, 30])))
        r.shuffle(routes)
        dsts = [ipaddress.ip_address(int(base) + r.randint(0, 3))]
        for n, _d, _m in r.sample(routes[1:] if len(routes) > 1 else routes, len(routes) - 1):
            net = ipaddress.ip_network(n)
            d = ipaddress.ip_address(int(net.network_address) + r.randint(1, max(1, net.num_addresses - 2)))
            if d not in dsts:
                dsts.append(d)
            if len(dsts) == 4:
                break
        for d in dsts:
            match = [(ipaddress.ip_network(n), dv, m) for n, dv, m in routes if d in ipaddress.ip_network(n)]
            exp.append(max(match, key=lambda x: (x[0].prefixlen, -x[2]))[1])
            qs.append(f"Through which interface does it send a packet to {d}?")
            checks.append(_word_check(exp[-1]))
        table = "\n".join(f"{n:<18s} dev {d:<6s} metric {m}" for n, d, m in routes)
        if level >= 5:   # plus the summarisation question
            nets = [ipaddress.ip_network(f"{base}/{r.choice([24, 25, 26])}", strict=False) for _ in range(3)]
            sup = nets[0]
            while not all(n.subnet_of(sup) for n in nets):
                sup = sup.supernet()
            qs.append(f"What is the smallest single CIDR that covers all of {', '.join(map(str, nets))}?")
            exp.append(str(sup))
            checks.append(_word_check(exp[-1]))
        prompt = (f"A Linux router has this routing table:\n\n```\n{table}\n```\n\nThe kernel picks the most specific route; "
                  "among equally specific ones, the lowest metric.\n" + _ask(qs) + _instr(len(qs)))
    return Item(f"{BLOCK}.subnet.L{level}.{seed}", BLOCK, "subnet", [{"role": "user", "content": prompt}], multi_check(checks),
                max_tokens=32000, meta={"expected": exp, "level": level})


# ---- file permissions: what a chain of chmod leaves ------------------------------------------------------------------

_WHO = {"u": (0o4700, 6), "g": (0o2070, 3), "o": (0o1007, 0)}


def chmod_apply(mode: int, spec: str, is_dir: bool = False, umask: int = 0o022) -> int:
    """GNU symbolic or octal chmod on a mode. With no who letters the umask filters the bits: `+w` / `-w` leave the bits
    the umask covers alone, while `=rw` sets the whole mode to rw minus the umask (the umask bits end up cleared). Checked
    against GNU chmod on the box (tests/test_techhelp.py); GNU also warns and exits 1 when the umask blocked a change."""
    if re.fullmatch(r"[0-7]{3,4}", spec):
        return int(spec, 8)
    for clause in spec.split(","):
        bare = re.fullmatch(r"([+=-])([rwxXst]*)", clause)
        if bare:   # files only (on directories GNU also keeps setuid/setgid unless mentioned)
            op, perms = bare.groups()
            val = 0
            for p in perms:
                val |= {"r": 0o444, "w": 0o222, "x": 0o111, "s": 0o6000, "t": 0o1000}.get(p, 0)
                if p == "X" and (is_dir or mode & 0o111):
                    val |= 0o111
            val &= ~umask
            if op == "+":
                mode |= val
            elif op == "-":
                mode &= ~val
            else:   # '=' sets every class: the umask bits end up cleared, not kept (checked against GNU chmod)
                mode = val
            continue
        m = re.fullmatch(r"([ugoa]+)([+=-])([rwxXst]*)", clause)
        who = "ugo" if "a" in m.group(1) else m.group(1)
        op, perms = m.group(2), m.group(3)
        any_x = bool(mode & 0o111)
        for w in who:
            _mask, shift = _WHO[w]
            bits = 0
            for p in perms:
                if p == "r":
                    bits |= 4 << shift
                elif p == "w":
                    bits |= 2 << shift
                elif p == "x" or (p == "X" and (is_dir or any_x)):
                    bits |= 1 << shift
                elif p == "s" and w in "ug":
                    bits |= 0o4000 if w == "u" else 0o2000
                elif p == "t" and w == "o":
                    bits |= 0o1000
            clear = (7 << shift) | (0o4000 if w == "u" else 0o2000 if w == "g" else 0o1000)
            if op == "+":
                mode |= bits
            elif op == "-":
                mode &= ~bits
            else:   # '=' sets exactly these bits for this class; on a file that includes clearing its special bit
                mode = (mode & ~clear) | bits
    return mode & 0o7777


def chmod_seq(seed: int, level: int = 3) -> Item:
    if level >= 9:
        return chmod_910(seed, level)
    if level >= 7:
        return chmod_78(seed, level)
    if level >= 6:
        return chmod_expert(seed, level)
    r = rng(BLOCK, f"chmod{level}", seed)
    # dev6: every file gets the same mix of operation kinds for the level, in its own order (a random mix made the
    # difficulty of an item depend on the seed)
    ops = {"basic": ["u+x", "g-w", "o-r", "g+w", "a+r", "u-w", "o+x", "ug+rw", "a-x", "go-rwx"],
           "eq": ["o=r", "g=rx", "u=rwx,g=rx,o=", "g=r", "o="], "X": ["a+X", "go+X", "u+X"], "oct": ["740", "750", "640"],
           "special": ["u+s", "g+s", "g-s", "u=rwxs", "u-s"]}   # setuid/setgid on files; no sticky: it means nothing on files
    mix = {1: ["basic", "basic"], 2: ["basic", "basic", "eq"], 3: ["basic", "eq", "X", "basic"],
           4: ["basic", "eq", "X", "basic", "eq"], 5: ["oct", "basic", "eq", "X", "special", "special"]}[level]
    paths, blocks, exp, files = [], [], [], []
    for path, is_dir in [("run.sh", False), ("data/", level == 4), ("notes.txt", False)]:   # three files, one question each
        start = r.choice([0o644, 0o600, 0o640, 0o755, 0o664, 0o700, 0o750]) if not is_dir else r.choice([0o755, 0o750, 0o700, 0o775])
        kinds = list(mix)
        if kinds[0] == "oct":   # an octal mode first (later steps build on it), the rest shuffled
            rest = kinds[1:]
            r.shuffle(rest)
            kinds = ["oct"] + rest
        else:
            r.shuffle(kinds)
        steps = []
        for k in kinds:
            steps.append(r.choice([o for o in ops[k] if o not in steps]))
        mode = start
        for st in steps:
            mode = chmod_apply(mode, st, is_dir)
        what = f"directory `{path}`" if is_dir else f"file `{path}`"
        blocks.append(f"The {what} has mode {start:04o}:\n```\n" + "\n".join(f"chmod {st} {path}" for st in steps) + "\n```")
        paths.append((what, is_dir))
        exp.append(mode)
        files.append({"path": path, "start": f"{start:04o}", "steps": steps, "is_dir": is_dir, "expected": f"{mode:04o}"})
    prompt = ("On Linux (GNU coreutils chmod, run as the owner), these commands run in order, each on its own file:\n\n"
              + "\n\n".join(blocks) + "\n\nWhat is each one's mode afterwards, as four octal digits (e.g. 0755)?\n"
              + _ask([f"{w[0].upper() + w[1:]}" for w, _d in paths]) + _instr(3))
    return Item(f"{BLOCK}.chmod_seq.L{level}.{seed}", BLOCK, "chmod_seq", [{"role": "user", "content": prompt}],
                multi_check([_octal_check(m) for m in exp]), max_tokens=32000,
                meta={"expected": [f"{m:04o}" for m in exp], "files": files, "level": level})


# ---- logs: which service broke first ---------------------------------------------------------------------------------

def log_root(seed: int, level: int = 3) -> Item:
    """A cascade of failures in journal logs; the root cause is the earliest real failure, not the loudest line. Level
    adds noise, a recovered error before the root cause (a retry that succeeded), and a second machine whose logs are
    in another time zone (the order must be read in UTC). Levels 7-8: log_78."""
    if level >= 9:
        return log_910(seed, level)
    if level >= 7:
        return log_78(seed, level)
    r = rng(BLOCK, f"log{level}", seed)
    svcs = ["postgres", "redis", "api", "worker", "nginx", "minio", "keycloak", "grafana"]
    chain = r.sample(svcs, 4)   # root -> dependents
    root, deps = chain[0], chain[1:]
    pid = {s: r.randint(200, 9000) for s in svcs + ["chronyd"]}
    causes = {
        "port": (f"could not bind to 0.0.0.0:{CONVENTIONAL.get(root, 8080)}: Address already in use", "EADDRINUSE"),
        "disk": ("could not write to /var/lib/data: No space left on device", "ENOSPC"),
        "perm": ("open /etc/ssl/private/server.key: permission denied", "EACCES"),
        "oom": ("Out of memory: Killed process (oom-kill)", "OOM"),
    }
    cause = r.choice(list(causes))
    t0 = datetime(2026, 9, r.randint(1, 25), r.randint(0, 22), r.randint(0, 59), tzinfo=timezone.utc)
    ev = []
    t = t0 - timedelta(seconds=r.randint(30, 120))
    if level >= 3:   # an earlier error that recovered - a red herring
        rh = r.choice([s for s in svcs if s not in chain])
        ev.append((t, "box-a", rh, "error", "connection to upstream timed out, retrying in 5s"))
        ev.append((t + timedelta(seconds=6), "box-a", rh, "info", "connected to upstream"))
    ev.append((t0, "box-a", root, "error", causes[cause][0]))
    ev.append((t0 + timedelta(seconds=1), "box-a", root, "error", f"{root}.service: Main process exited, status=1/FAILURE"))
    for i, d in enumerate(deps):
        ev.append((t0 + timedelta(seconds=3 + 4 * i + r.randint(0, 2)), "box-a", d, "error",
                   f"dependency failed: cannot reach {chain[i]} ({r.choice(['connection refused', 'no route to host', '502 Bad Gateway'])})"))
    noise = [("info", "health check ok"), ("info", "config reloaded"), ("warn", "slow request 812ms"), ("info", "rotating logs"),
             ("warn", "high memory usage 87%"), ("info", "checkpoint complete")]
    for _ in range(3 * level):
        s = r.choice(svcs)
        lvl, msg = r.choice(noise)
        ev.append((t0 + timedelta(seconds=r.randint(-200, 60)), "box-a", s, lvl, msg))
    remote_tz = None
    if level >= 4:   # half of the chain logs on a second machine in another time zone
        remote_tz = timezone(timedelta(hours=r.choice([-7, -5, 2, 3, 9])))
        moved = set(deps[-2:])
        ev = [(tt, "box-b" if s in moved else host, s, lvl, msg) for tt, host, s, lvl, msg in ev]
    skew = None
    if level >= 6:   # the first dependent logs on box-c, whose clock is behind: by raw timestamps it looks like the root cause
        skew = timedelta(seconds=r.randint(40, 200) + r.random())
        ev = [(tt, "box-c" if (s == deps[0] and host == "box-a") else host, s, lvl, msg) for tt, host, s, lvl, msg in ev]
        ev.append((t0 + timedelta(seconds=r.randint(240, 400)), "box-c", "chronyd", "warn",
                   f"System clock is {skew.total_seconds():.1f} seconds behind NTP time, stepping the clock forward"))
    ev.sort(key=lambda e: e[0])
    lines = []
    for tt, host, s, lvl, msg in ev:
        shown = tt.astimezone(remote_tz) if (remote_tz and host == "box-b") else tt
        if skew is not None and host == "box-c":
            shown = tt - skew
        stamp = shown.strftime("%Y-%m-%dT%H:%M:%S%z") if level >= 4 else shown.strftime("%b %d %H:%M:%S")
        lines.append(f"{stamp} {host} {s}[{pid[s]}]: {lvl.upper()} {msg}")
    if level >= 4:   # separate log files as they would be collected
        logs = "\n\n".join(f"{hname} (journalctl):\n```\n" + "\n".join(l for l in lines if f" {hname} " in l) + "\n```"
                             for hname in ("box-a", "box-b", "box-c") if any(f" {hname} " in l for l in lines))
    else:
        logs = "```\n" + "\n".join(lines) + "\n```"
    qs = ["Which service is the root cause - the one that failed first and made the others fail?",
          "Which service failed next, as a direct consequence of the root cause?", "Which service failed last?"]
    exp = [root, deps[0], deps[-1]]
    prompt = (f"My stack stopped working. Here are the logs:\n\n{logs}\n\nAnswer each with the service name only.\n" + _ask(qs)
              + _instr(3))
    return Item(f"{BLOCK}.log_root.L{level}.{seed}", BLOCK, "log_root", [{"role": "user", "content": prompt}],
                multi_check([_word_check(e) for e in exp]), max_tokens=32000, meta={"expected": exp, "cause": cause, "level": level})


# ---- level 6: expert traps (headroom above strong local models; frontier models should miss some) -------------------

def compose_expert(seed: int, level: int = 6) -> Item:
    """Compose v2 rules people rarely know by heart: a variable set in the shell beats the same one in .env;
    compose.override.yaml is merged automatically when no -f is given, and its ports list is appended - unless it is
    tagged !override (replaces) or !reset (empties); a service with `profiles:` is not started without its profile."""
    r = rng(BLOCK, f"compose{level}", seed)
    svc = r.choice(["web", "api", "grafana", "auth", "search", "app"])
    others = r.sample([n for n in CONVENTIONAL if n != svc], 3)
    tport = r.choice([80, 3000, 8000, 8080])
    var = f"{svc.upper()}_PORT"
    default, envval, shellval = r.choice([8080, 8090]), r.choice([18080, 8443, 28080]), r.choice([9090, 19090, 7080])
    # dev6: the same rules in every item (a seed changes names, ports and values, not which rules are tested - a random
    # mix of override/reset/profile made one item score 0, 0 and 1 in three runs of one model): a profile, a variable in
    # .env and in the shell, a fixed second port, and compose.override.yaml merged automatically (its ports appended)
    fixed = r.choice([9443, 7443, 10443])
    in_env, profile, mode = True, True, "merge"
    extra = r.choice([7000, 17000, 6443])
    def published(shell_set: bool, enabled: bool) -> set[str]:
        hostport = str(shellval if shell_set else envval if in_env else default)
        base = {hostport} | ({str(fixed)} if fixed else set())
        ports = {"merge": base | {str(extra)}, "override": {str(extra)}, "reset": set()}[mode]
        return ports if (not profile or enabled) else set()
    lines = ["services:", f"  {svc}:", f"    image: example/{svc}:2", "    ports:", f'      - "${{{var}:-{default}}}:{tport}"']
    if fixed:
        lines.append(f'      - "{fixed}:{tport}"')
    if profile:
        lines += ["    profiles:", '      - "debug"']
    for o in others:
        lines += [f"  {o}:", f"    image: example/{o}:2"]
        if r.random() < 0.6:
            lines += ["    ports:", f'      - "{CONVENTIONAL.get(o, 8081)}:{CONVENTIONAL.get(o, 8081)}"']
    over = [f"services:", f"  {svc}:", "    environment:", "      - LOG_LEVEL=debug"]
    over += {"merge": ["    ports:", f'      - "{extra}:{tport}"'], "override": ["    ports: !override", f'      - "{extra}:{tport}"'],
             "reset": ["    ports: !reset []"]}[mode]
    files = [("compose.yaml", "\n".join(lines) + "\n"), ("compose.override.yaml", "\n".join(over) + "\n")]
    envf = (f"{var}={envval}\n" if in_env else "") + "COMPOSE_PROJECT_NAME=stack\n"
    files.append((".env", envf))
    # the same files, three ways to start them: each command exercises other rules (shell beats .env, profiles)
    # without the profile (not started), shell variable + --profile (shell beats .env), COMPOSE_PROFILES (.env value)
    chosen = [(False, None), (True, "flag"), (False, "env")]
    r.shuffle(chosen)
    cmds, want = [], []
    for sh, en in chosen:
        prefix = (f"{var}={shellval} " if sh else "") + ("COMPOSE_PROFILES=debug " if en == "env" else "")
        cmds.append(f"{prefix}docker compose {'--profile debug ' if en == 'flag' else ''}up -d")
        want.append(published(sh, en is not None))
    body = "\n\n".join(f"`{n}`:\n```{'yaml' if n != '.env' else ''}\n{t}```" for n, t in files)
    qs = [f"After `docker compose down`, I run `{c}`: on which host port(s) can I reach container port {tport} of `{svc}`?" for c in cmds]
    prompt = (f"A directory contains these files:\n\n{body}\n\nWith Docker Compose v2.29 in bash, in that directory:\n" + _ask(qs)
              + "\nIf it is not reachable from the host at a fixed port, or not running at all, answer NONE; if there are several "
              "ports, list them all." + _instr(3))
    return Item(f"{BLOCK}.compose_port.L{level}.{seed}", BLOCK, "compose_port", [{"role": "user", "content": prompt}],
                multi_check([_set_check(w) for w in want]), max_tokens=32000,
                meta={"expected": [", ".join(sorted(w)) or "NONE" for w in want], "level": level, "mode": mode,
                      "profile": profile, "commands": cmds})


def _nginx_select(locs: list[tuple], uri: str) -> tuple | None:
    """The location nginx picks (same rules as _nginx_match, returning the whole entry)."""
    for loc in locs:
        if loc[0] == "=" and uri == loc[1]:
            return loc
    prefixes = [loc for loc in locs if loc[0] in ("", "^~") and uri.startswith(loc[1])]
    best = max(prefixes, key=lambda x: len(x[1]), default=None)
    if best and best[0] == "^~":
        return best
    for loc in locs:
        if (loc[0] == "~" and re.search(loc[1], uri)) or (loc[0] == "~*" and re.search(loc[1], uri, re.I)):
            return loc
    return best


def nginx_rewrite(locs: list[tuple], uri: str) -> tuple[str, list[str]]:
    """Follow `rewrite ... last` (the new URI goes through location selection again, at most 10 times)."""
    trail = [uri]
    for _ in range(10):
        loc = _nginx_select(locs, uri)
        if loc is None:
            return "404", trail
        rw = loc[3]
        if rw and re.search(rw[0], uri):
            uri = re.sub(rw[0], rw[1].replace("$", "\\"), uri, count=1)
            trail.append(uri)
            continue
        return loc[2] or "404", trail
    return "500", trail


def nginx_expert(seed: int, level: int = 6) -> Item:
    r = rng(BLOCK, f"nginx{level}", seed)
    ups = [f"{c}_pool" for c in r.sample(["blue", "green", "amber", "violet", "teal", "coral", "slate", "olive", "ruby", "indigo"], 8)]
    locs = [("", "/", ups[0], None), ("", "/api/", ups[1], None), ("^~", "/assets/", ups[2], None), ("~", r"\.php$", ups[3], None),
            ("~*", r"\.(png|jpe?g)$", ups[4], None), ("=", "/", ups[5], None), ("", "/api/v2/", ups[6], None),
            ("", "/old/", None, (r"^/old/(.*)$", r"/api/$1")), ("~", r"^/legacy/(\w+)$", None, (r"^/legacy/(\w+)$", r"/assets/$1.png")),
            ("", "/v2/", None, (r"^/v2/(.*)$", r"/api/v2/$1"))]
    r.shuffle(locs)
    uris = ["/old/users", "/old/v2/orders", "/legacy/logo", "/v2/items.php", "/old/avatar.PNG", "/v2/x", "/legacy/a/b", "/old/",
            "/assets/app.php", "/api/v2/me.jpg"]
    cand = [u for u in uris if len(nginx_rewrite(locs, u)[1]) > 1]
    picked = r.sample(cand, min(len(cand), 3))
    picked += r.sample([u for u in uris if u not in picked], 4 - len(picked))
    r.shuffle(picked)
    expected = [nginx_rewrite(locs, u)[0] for u in picked]
    conf = ["server {", "    listen 80;", "    server_name example.lan;"]
    for mod, pat, tgt, rw in locs:
        inner = f"rewrite {rw[0]} {rw[1]} last;" if rw else f"proxy_pass http://{tgt};"
        conf.append(f"    location {mod + ' ' if mod else ''}{pat} {{\n        {inner}\n    }}")
    conf.append("}")
    prompt = ("Here is an nginx server block:\n\n```nginx\n" + "\n".join(conf) + "\n```\n\n"
              "A client sends these requests for Host example.lan. Which upstream finally handles each? Answer with the upstream "
              "name (the part after http://), or 404 if none does.\n" + _ask([f"GET {u}" for u in picked]) + _instr(4))
    return Item(f"{BLOCK}.nginx_route.L{level}.{seed}", BLOCK, "nginx_route", [{"role": "user", "content": prompt}],
                multi_check([_word_check(e) for e in expected]), max_tokens=32000,
                meta={"expected": expected, "uris": picked, "level": level})


def route_lookup(rules: list[tuple], tables: dict, src, dst) -> str | None:
    """Linux policy routing: rules by priority; a matching rule's table is searched (most specific route, then the
    lowest metric); if the table has no route for the destination, the next rule is tried."""
    for _prio, frm, to, table in sorted(rules):
        if frm and src not in ipaddress.ip_network(frm):
            continue
        if to and dst not in ipaddress.ip_network(to):
            continue
        hits = [(ipaddress.ip_network(n), d, m) for n, d, m in tables.get(table, []) if dst in ipaddress.ip_network(n)]
        if hits:
            return max(hits, key=lambda x: (x[0].prefixlen, -x[2]))[1]
    return None


def routing_expert(seed: int, level: int = 6) -> Item:
    r = rng(BLOCK, f"subnet{level}", seed)
    lan = ipaddress.ip_network(f"192.168.{r.randint(1, 250)}.0/24")
    vpn = ipaddress.ip_network(f"10.{r.randint(1, 250)}.0.0/24")
    far = ipaddress.ip_network(f"172.{r.randint(16, 31)}.{r.randint(0, 250)}.0/24")
    far_sup = far.supernet(new_prefix=16)
    # dev6: one fixed layout (a seed changes addresses and interface names, not the rules tested): the VPN rule comes
    # first and its table only knows the far /24, so VPN traffic elsewhere falls through; the "to far /16" table has
    # both the /16 and a more specific /24
    wan, lan_if, eth, wg, tun_a, tun_b = r.choice(["wan0", "ppp0"]), r.choice(["lan0", "br-lan"]), r.choice(["eth1", "eth2"]), \
        r.choice(["wg0", "wg1"]), r.choice(["tun0", "tun2"]), r.choice(["tun1", "tun3"])
    tables = {"main": [("0.0.0.0/0", wan, 100), (str(lan), lan_if, 0), (str(far_sup), eth, 50)],
              "100": [(str(far), wg, 10)],
              "200": [(str(far_sup), tun_a, 20), (str(far), tun_b, 5)]}
    rules = [(0, None, None, "local"), (90, str(vpn), None, "100"), (100, None, str(far_sup), "200"),
             (32766, None, None, "main"), (32767, None, None, "default")]

    def host_in(net, sub=None):
        while True:
            a = ipaddress.ip_address(int(net.network_address) + r.randint(2, net.num_addresses - 2))
            if sub is None or a not in sub:
                return a
    outside = ipaddress.ip_address(f"{r.choice([8, 1, 9, 185])}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}")
    packets = [(host_in(vpn), host_in(far)),               # VPN rule, its table has the /24
               (host_in(vpn), host_in(far_sup, far)),      # VPN rule, no route there: falls through to the /16 table
               (host_in(vpn), outside),                    # falls through twice, to main
               (host_in(lan), host_in(far))]               # the /16 rule; most specific route in its table
    r.shuffle(packets)
    expected = [route_lookup([x for x in rules if x[3] != "local"], tables, s_, d_) or "none" for s_, d_ in packets]
    rl = "\n".join(f"{p}:\tfrom {frm or 'all'}{' to ' + to if to else ''} lookup {t}" for p, frm, to, t in sorted(rules))
    tl = "\n\n".join(f"$ ip route show table {t}\n" + "\n".join(f"{n} dev {d} metric {m}" if n != "0.0.0.0/0" else f"default dev {d} metric {m}"
                                                                  for n, d, m in routes) for t, routes in tables.items())
    prompt = (f"A Linux router has these policy routing rules and tables:\n\n```\n$ ip rule show\n{rl}\n\n{tl}\n```\n\n"
              "Through which interface does the router send each of these packets?\n"
              + _ask([f"A packet from {s_} for {d_}" for s_, d_ in packets]) + _instr(4))
    return Item(f"{BLOCK}.subnet.L{level}.{seed}", BLOCK, "subnet", [{"role": "user", "content": prompt}],
                multi_check([_word_check(e) for e in expected]), max_tokens=32000,
                meta={"expected": expected, "packets": [f"{a} -> {b}" for a, b in packets], "level": level})


def chmod_expert(seed: int, level: int = 6) -> Item:
    """Symbolic modes without who letters, where the umask decides which bits change (`chmod +w` with umask 022 adds
    write for the owner only; `chmod =r` with umask 027 gives 0440), mixed with explicit ones."""
    r = rng(BLOCK, f"chmod{level}", seed)
    um = r.choice([0o022, 0o027, 0o077, 0o002])
    ops = {"bare": ["+x", "+w", "-w", "+rX", "-rwx"], "bare_eq": ["=r", "=rw", "=rwx"], "who": ["u+x", "o-r", "a+r", "go-w", "g=u"],
           "special": ["+s", "g+s"]}   # dev6: each file: one of each kind, in its own order
    blocks, exp, names, files = [], [], ["run.sh", "deploy.sh", "notes.txt"], []
    for path in names:   # three files under the same umask, one question each
        start = r.choice([0o644, 0o600, 0o664, 0o755, 0o640, 0o666])
        kinds = list(ops)
        r.shuffle(kinds)
        steps = [r.choice(ops[k]) for k in kinds]
        mode = start
        for st in steps:
            # g=u copies the owner's rwx to the group; like any '=' for g it also clears setgid (GNU)
            mode = (mode & ~0o2070) | ((mode & 0o700) >> 3) if st == "g=u" else chmod_apply(mode, st, False, um)
        blocks.append(f"`{path}` has mode {start:04o}:\n```\n" + "\n".join(f"chmod {st} {path}" for st in steps) + "\n```")
        exp.append(mode)
        files.append({"path": path, "start": f"{start:04o}", "steps": steps, "is_dir": False, "expected": f"{mode:04o}"})
    prompt = (f"On Linux (GNU coreutils chmod, run as the owner) the shell's umask is {um:04o}. These commands run in order, each "
              "on its own file:\n\n" + "\n\n".join(blocks) + "\n\nWhat is each file's mode afterwards, as four octal digits "
              "(e.g. 0755)?\n" + _ask([f"`{p}`" for p in names]) + _instr(3))
    return Item(f"{BLOCK}.chmod_seq.L{level}.{seed}", BLOCK, "chmod_seq", [{"role": "user", "content": prompt}],
                multi_check([_octal_check(m) for m in exp]), max_tokens=32000,
                meta={"expected": [f"{m:04o}" for m in exp], "files": files, "umask": f"{um:04o}", "level": level})


# ---- levels 7-8: more interacting rules per question (v0.11) --------------------------------------------------------
# Level 6 saturated: the frontier reference answered every level-6 techhelp item right. Levels 7-8 keep the fixed rule mix
# per level (a seed changes names, numbers, order) and the credit per question, and every question now needs several
# rules at once. Nothing below is reached from levels 1-6: they stay byte-identical (tests/test_levels_stable.py).

def _set_check7(expected: set[str]):
    """_set_check that also takes '8080/tcp' and a 'tcp' word."""
    def check(text: str, _t=None) -> float:
        a = final_answer(text)
        if a is None:
            return 0.0
        toks = [re.sub(r"/tcp$", "", re.sub(r"^\d+\.\d+\.\d+\.\d+:", "", t)) for t in re.findall(r"[\w.:/-]+", a.lower())]
        got = {t for t in toks if t not in {"and", "port", "ports", "on", "host", "tcp", "only"}}
        want = {e.lower() for e in expected} or {"none"}
        return 1.0 if got == want else 0.0
    return check


def _octal_check7(want: int):
    """The last 3-5 digit octal number (2755, 02755 and 0755 are all read as numbers)."""
    def check(text: str, _t=None) -> float:
        a = re.findall(r"(?<![0-9])[0-7]{3,5}(?![0-9])", final_answer(text) or "")
        return 1.0 if a and int(a[-1], 8) == want else 0.0
    return check


def _num_check7(expected: float, tol: float):
    def check(text: str, _t=None) -> float:
        v = num(final_answer(text))
        return 1.0 if v is not None and abs(v - expected) <= tol + 1e-9 else 0.0
    return check


# -- compose: which env file, which compose files, which ports survive -------------------------------------------------

def compose_78(seed: int, level: int) -> Item:
    """Checked with `docker compose config` (v2.40): `env_file:` feeds the container, never ${VAR} interpolation;
    `--env-file` replaces .env entirely (its COMPOSE_FILE too); a variable set in the shell beats .env even when empty,
    and `${VAR:-d}` treats empty as unset while `${VAR-d}` keeps the empty value (the host port becomes a random one);
    compose.override.yaml is merged only when no -f and no COMPOSE_FILE picks the files; `!override` replaces a list;
    identical port mappings merge into one; a /udp mapping is no TCP port; `127.0.0.1::80` is a random port.
    Level 8 adds COMPOSE_FILE in .env and `up <service>` (starts that service and its dependencies only)."""
    r = rng(BLOCK, f"compose{level}", seed)
    svc = r.choice(["web", "api", "grafana", "auth", "search", "app"])
    # other services publish their usual port; none of them on a port the target could get (compose would refuse)
    others = r.sample([n for n in CONVENTIONAL if n != svc and n not in ("nginx", "proxy", "keycloak", "auth")], 3)
    tport = r.choice([80, 3000, 8000, 8080])
    var = f"{svc.upper()}_PORT"
    default, envval, trapval, shellval = r.choice([8080, 8090, 8181, 8282]), r.choice([18080, 8443, 28080]), \
        r.choice([38080, 48080]), r.choice([9090, 19090, 7080])
    fixed, udp, extra, prod = r.choice([9443, 7443, 10443]), r.choice([5353, 6060, 4789]), r.choice([7000, 17000, 6443]), \
        r.choice([8800, 18800, 8888])
    dash = level >= 8   # ${VAR-default}: an empty value stays empty
    ref = f"${{{var}-{default}}}" if dash else f"${{{var}:-{default}}}"
    lines = ["services:", f"  {svc}:", f"    image: example/{svc}:2", f"    env_file: {svc}.env", "    ports:",
             f'      - "{ref}:{tport}"', f'      - "{fixed}:{tport}"', f'      - "{udp}:{tport}/udp"']
    if level >= 8:
        lines.append(f'      - "127.0.0.1::{tport}"')
    taken = set()
    for o in others:
        lines += [f"  {o}:", f"    image: example/{o}:2"]
        if r.random() < 0.6 and CONVENTIONAL[o] not in taken:
            taken.add(CONVENTIONAL[o])
            lines += ["    ports:", f'      - "{CONVENTIONAL[o]}:{CONVENTIONAL[o]}"']
    target = others[0]   # level 8: `up -d <target>` - it does not depend on svc
    over = ["services:", f"  {svc}:", "    ports:", f'      - "{extra}:{tport}"', f'      - "{fixed}:{tport}"']
    prodf = ["services:", f"  {svc}:"] + (["    ports:"] if level >= 8 else ["    ports: !override"]) + [f'      - "{prod}:{tport}"']
    envq = r.choice(['"{v}"', "{v}  # was {d}", "'{v}'"]).format(v=envval, d=default)
    envf = f"COMPOSE_PROJECT_NAME=stack\n{var}={envq}\n" + ("COMPOSE_FILE=compose.yaml:compose.prod.yaml\n" if level >= 8 else "")
    files = [("compose.yaml", "\n".join(lines) + "\n"), ("compose.override.yaml", "\n".join(over) + "\n"),
             ("compose.prod.yaml", "\n".join(prodf) + "\n"), (".env", envf), (f"{svc}.env", f"{var}={trapval}\nLOG_LEVEL=info\n"),
             ("prod.env", f"COMPOSE_PROJECT_NAME=stack\n{var}=\n")]

    def pub(value: str | None, files_: list[str], started: bool = True) -> set[str]:
        """The fixed TCP host ports of container port tport: the variable's port, the fixed one, and each file's own."""
        if not started:
            return set()
        if "compose.prod.yaml" in files_ and level < 8:   # !override: only the prod port is left
            return {str(prod)}
        v = str(default) if value is None or (value == "" and not dash) else value
        out = ({v} if v else set()) | {str(fixed)}
        if "compose.override.yaml" in files_:
            out.add(str(extra))
        if "compose.prod.yaml" in files_:
            out.add(str(prod))
        return out

    base, over_, prod_ = ["compose.yaml"], ["compose.yaml", "compose.override.yaml"], ["compose.yaml", "compose.prod.yaml"]
    if level < 8:
        runs = [("docker compose up -d", pub(str(envval), over_)),
                ("docker compose -f compose.yaml -f compose.prod.yaml up -d", pub(str(envval), prod_)),
                ("docker compose --env-file prod.env up -d", pub("", over_)),
                (f"{var}={shellval} docker compose -f compose.yaml up -d", pub(str(shellval), base))]
    else:
        runs = [("docker compose up -d", pub(str(envval), prod_)),
                ("docker compose --env-file prod.env up -d", pub("", over_)),
                ("docker compose -f compose.yaml up -d", pub(str(envval), base)),
                (f"{var}= docker compose up -d", pub("", prod_)),
                (f"docker compose up -d {target}", pub(str(envval), prod_, started=False))]
    r.shuffle(runs)
    body = "\n\n".join(f"`{n}`:\n```{'yaml' if n.endswith('.yaml') else ''}\n{t}```" for n, t in files)
    qs = [f"After `docker compose down`, I run `{c}`: on which host TCP port(s) can I reach container port {tport} of `{svc}`?"
          for c, _w in runs]
    prompt = (f"A directory contains these files:\n\n{body}\n\nWith Docker Compose v2.40 in bash, in that directory, with no "
              "COMPOSE_* or *_PORT variables in the environment unless a command sets one:\n" + _ask(qs)
              + "\nIf it is not reachable from the host at a fixed TCP port, or not running at all, answer NONE; if there are "
              "several ports, list them all." + _instr(len(qs)))
    return Item(f"{BLOCK}.compose_port.L{level}.{seed}", BLOCK, "compose_port", [{"role": "user", "content": prompt}],
                multi_check([_set_check7(w) for _c, w in runs]), max_tokens=32000,
                meta={"expected": [", ".join(sorted(w)) or "NONE" for _c, w in runs], "level": level,
                      "commands": [c for c, _w in runs], "files": dict(files), "service": svc, "container_port": tport})


# -- nginx: which upstream, and which URI it receives ------------------------------------------------------------------

def _ngx_sub(rx: str, repl: str, uri: str, args: str) -> tuple[str, str]:
    """nginx `rewrite`: the WHOLE URI becomes the replacement ($n = captures). New arguments in it come first and the old
    ones are appended after '&', unless the replacement ends with '?'."""
    m = re.search(rx, uri)
    new = re.sub(r"\$(\d)", lambda g: m.group(int(g.group(1))) or "", repl)
    if "?" not in new:
        return new, args
    path, _, nargs = new.partition("?")
    if new.endswith("?"):
        return path, nargs.rstrip("?")
    return path, nargs + ("&" + args if args else "")


def ngx_proxy(server_rw: list[tuple], locs: list[tuple], request: str) -> tuple[str, str]:
    """(upstream, request URI it receives) per the nginx docs (ngx_http_rewrite_module, proxy_pass):
    - server-level rewrites run once, in order, before the location search (`last`/`break` stop them);
    - location search as in _nginx_select, on the normalized URI (merge_slashes on);
    - location rewrites in order: `last` searches the locations again with the new URI, `break` stays in this location,
      no flag goes on with the next rewrite and, if the URI changed, searches again after the last one (max 10 rounds);
    - proxy_pass with a URI replaces the part of the URI the (prefix or exact) location matched - after `break` the URI
      is ignored and the whole changed URI is sent; without a URI the request goes as the client sent it, or as the
      normalized changed URI if a rewrite changed it; the arguments follow after '?'."""
    raw = request
    path, _, args = raw.partition("?")
    uri = re.sub(r"/{2,}", "/", path)
    changed = False
    for rx, repl, flag in server_rw:
        if re.search(rx, uri):
            uri, args = _ngx_sub(rx, repl, uri, args)
            changed = True
            if flag in ("last", "break"):
                break
    for _ in range(10):
        loc = _nginx_select(locs, uri)
        if loc is None:
            return "404", ""
        body = loc[2]
        again = broke = False
        for rx, repl, flag in body.get("rw", []):
            if re.search(rx, uri):
                uri, args = _ngx_sub(rx, repl, uri, args)
                changed = True
                if flag == "break":
                    broke = True
                    break
                again = True
                if flag == "last":
                    break
        if again:
            continue
        up, puri = body["proxy"]
        q = ("?" + args) if args else ""
        if puri is None:
            return up, (raw if not changed else uri + q)
        if broke:
            return up, uri + q
        return up, puri + uri[len(loc[1]):] + q
    return "500", ""


def _proxy_check(up: str, path: str):
    """'<upstream> <URI>' (also 'http://<upstream><URI>'): the upstream alone earns half."""
    def check(text: str, _t=None) -> float:
        a = (final_answer(text) or "").replace("`", " ").replace("*", " ")
        a = re.sub(r"https?://([\w.-]+)(?=/)", r"\1 ", a)
        toks = [t.strip(".,;:()'\"") for t in a.split()]
        got_up = next((t for t in toks if re.fullmatch(r"[\w-]+", t) and t.lower() not in ("upstream", "uri", "path", "to")), None)
        got_path = next((t for t in toks if t.startswith("/")), None)
        if got_up != up:
            return 0.0
        return 1.0 if got_path == path else 0.5
    return check


def nginx_78(seed: int, level: int) -> Item:
    r = rng(BLOCK, f"nginx{level}", seed)
    u = [f"{c}_pool" for c in r.sample(["blue", "green", "amber", "violet", "teal", "coral", "slate", "olive", "ruby", "indigo"], 9)]
    api, app, static, old, legacy, v1 = r.choice(["api", "svc"]), r.choice(["app", "shop", "portal"]), \
        r.choice(["static", "assets"]), r.choice(["old", "archive"]), r.choice(["legacy", "classic"]), r.choice(["v1", "beta"])
    shop, store = r.choice([("cart", "store"), ("buy", "market")])
    internal, base, v2 = r.choice(["internal", "rest"]), r.choice(["base", "srv"]), r.choice(["v2", "next"])
    word, word2, img, n = r.choice(["users", "orders", "items"]), r.choice(["report", "invoice", "profile"]), \
        r.choice(["logo", "banner", "avatar"]), r.randint(2, 9)
    tail = r.choice(["s", "x", "data"])
    server_rw = [(rf"^/{v1}/(.*)$", f"/{api}/$1", None)]
    if level >= 8:
        server_rw.append((r"^/search/(\w+)$", f"/{api}/find?q=$1", "last"))
    img_rx = ("~*", r"\.(png|jpe?g)$", {"proxy": (u[4], None)})
    locs = [("", "/", {"proxy": (u[0], None)}),
            ("", f"/{api}/", {"proxy": (u[1], f"/{internal}/")}),
            ("", f"/{app}", {"proxy": (u[2], "/")}),
            ("^~", f"/{static}/", {"proxy": (u[3], None)}),
            img_rx,
            ("", f"/{old}/", {"rw": [(rf"^/{old}/(.*)$", f"/{api}/$1", "last")], "proxy": (u[0], None)}),
            ("", f"/{legacy}/", {"rw": [(rf"^/{legacy}/(.*)$", f"/{v2}/$1", "break")], "proxy": (u[5], f"/{base}/")}),
            ("=", "/health", {"proxy": (u[6], "/status")})]
    if level >= 8:
        locs += [("", f"/{shop}/", {"rw": [(rf"^/{shop}/(.*)$", f"/{store}/$1", None), (rf"^/{store}/(.*)\.php$", f"/{legacy}/$1", None)],
                                    "proxy": (u[7], None)}),
                 ("~", rf"^/{api}/.*\.(json|jpg)$", {"proxy": (u[8], None)})]
    r.shuffle(locs)
    ext = r.choice(["PNG", "JPG", "Png"])
    # the same request shapes at a level, each through two or three rules
    if level < 8:
        reqs = [f"/{v1}/{word}?page={n}",          # server rewrite, then proxy_pass with a URI; the arguments follow
                f"/{old}/{img}.{ext}",              # a regex beats the longest prefix (no ^~), so the rewrite never runs
                f"/{legacy}/{word2}?id={n}",        # rewrite ... break: proxy_pass's URI is ignored
                f"/{app}{tail}/{word}",             # 'location /app' also matches /apps...; 'proxy_pass http://x/' cuts 4 chars
                f"/{v1}/{img}.jpg"]                 # server rewrite into a regex location: the changed URI is sent
    else:
        reqs = [f"/search/{word}?page={n}",         # server rewrite with new arguments: the old ones are appended
                f"/{shop}/{word2}.php",             # two rewrites without a flag, then the locations are searched again
                f"/{shop}/{word2}",                 # the first rewrite matches, the second not: search again, not this proxy_pass
                f"/{static}//{img}.css",            # ^~ and proxy_pass without a URI: sent as the client sent it
                f"/{api}/{img}.jpg",                # two regex locations match: the first in the file wins
                f"/{legacy}/{word2}?id={n}"]
    r.shuffle(reqs)
    expected = [ngx_proxy(server_rw, locs, q) for q in reqs]
    conf = ["server {", "    listen 80;", "    server_name example.lan;"]
    for rx, repl, flag in server_rw:
        conf.append(f"    rewrite {rx} {repl}{' ' + flag if flag else ''};")
    for mod, pat, body in locs:
        inner = [f"        rewrite {rx} {repl}{' ' + flag if flag else ''};" for rx, repl, flag in body.get("rw", [])]
        up, puri = body["proxy"]
        inner.append(f"        proxy_pass http://{up}{puri or ''};")
        conf.append(f"    location {mod + ' ' if mod else ''}{pat} {{\n" + "\n".join(inner) + "\n    }")
    conf.append("}")
    prompt = ("Here is an nginx server block (nginx 1.26, default settings otherwise):\n\n```nginx\n" + "\n".join(conf)
              + "\n```\n\nA client sends these requests for Host example.lan, exactly as written. For each, which upstream "
              "finally handles it, and which URI (path and query string) is in the request line nginx sends to it? Answer "
              "as `<upstream> <URI>`, e.g. `blue_pool /x/y?z=1`.\n" + _ask([f"GET {q}" for q in reqs]) + _instr(len(reqs)))
    return Item(f"{BLOCK}.nginx_route.L{level}.{seed}", BLOCK, "nginx_route", [{"role": "user", "content": prompt}],
                multi_check([_proxy_check(up, p) for up, p in expected]), max_tokens=32000,
                meta={"expected": [f"{up} {p}" for up, p in expected], "uris": reqs, "level": level})


# -- policy routing as wg-quick sets it up: suppress_prefixlength, not fwmark, throw / blackhole / unreachable ----------

def route_lookup7(rules: list[dict], tables: dict, pkt: dict) -> str:
    """Linux fib_rules_lookup: rules by priority; selectors from/to/fwmark[/mask]/iif all must match, `not` inverts the
    whole match. A matching rule looks its table up: the most specific route, then the lowest metric. No route there or a
    `throw` route: the next rule. A `blackhole`/`unreachable`/`prohibit` route or rule: dropped (no further rules).
    `suppress_prefixlength N`: a found route with prefix length <= N is ignored and the next rule is tried."""
    for rule in sorted(rules, key=lambda x: x["prio"]):
        ok = ((not rule.get("from") or ipaddress.ip_address(pkt["src"]) in ipaddress.ip_network(rule["from"]))
              and (not rule.get("to") or ipaddress.ip_address(pkt["dst"]) in ipaddress.ip_network(rule["to"]))
              and (rule.get("mark") is None or ((rule["mark"] ^ pkt.get("mark", 0)) & rule.get("mask", 0xffffffff)) == 0)
              and (not rule.get("iif") or rule["iif"] == pkt.get("iif")))
        if rule.get("not"):
            ok = not ok
        if not ok:
            continue
        if rule.get("action"):
            return "none"
        hits = [rt for rt in tables.get(rule["table"], []) if ipaddress.ip_address(pkt["dst"]) in ipaddress.ip_network(rt["net"])]
        if not hits:
            continue
        plen = max(ipaddress.ip_network(rt["net"]).prefixlen for rt in hits)
        best = min((rt for rt in hits if ipaddress.ip_network(rt["net"]).prefixlen == plen), key=lambda rt: rt.get("metric", 0))
        if best.get("type") == "throw":
            continue
        if best.get("type") in ("blackhole", "unreachable", "prohibit"):
            return "none"
        if rule.get("suppress") is not None and plen <= rule["suppress"]:
            continue
        return best["dev"]
    return "none"


def _rule_line(rule: dict) -> str:
    s = f"{rule['prio']}:\t" + ("not " if rule.get("not") else "") + f"from {rule.get('from') or 'all'}"
    if rule.get("to"):
        s += f" to {rule['to']}"
    if rule.get("mark") is not None:
        s += f" fwmark {rule['mark']:#x}" + (f"/{rule['mask']:#x}" if rule.get("mask") is not None else "")
    if rule.get("iif"):
        s += f" iif {rule['iif']}"
    s += f" {rule['action']}" if rule.get("action") else f" lookup {rule['table']}"
    if rule.get("suppress") is not None:
        s += f" suppress_prefixlength {rule['suppress']}"
    return s


def _route_line(rt: dict) -> str:
    net = "default" if rt["net"] == "0.0.0.0/0" else rt["net"]
    if rt.get("type"):
        return f"{rt['type']} {net}"
    return f"{net} dev {rt['dev']}" + (f" metric {rt['metric']}" if rt.get("metric") else " scope link")


def routing_78(seed: int, level: int) -> Item:
    r = rng(BLOCK, f"subnet{level}", seed)
    lan = ipaddress.ip_network(f"192.168.{r.randint(1, 250)}.0/24")
    vpn = ipaddress.ip_network(f"10.{r.randint(1, 250)}.{r.randint(0, 250)}.0/24")
    far = ipaddress.ip_network(f"172.{r.randint(16, 31)}.{r.randint(0, 250)}.0/24")
    far_sup = far.supernet(new_prefix=16)
    carve = list(far.subnets(new_prefix=26))[r.randint(0, 3)]
    wan, lan_if, eth, wg, tun = r.choice(["wan0", "ppp0"]), r.choice(["lan0", "br-lan"]), r.choice(["eth1", "eth2"]), \
        r.choice(["wg0", "wg1"]), r.choice(["tun0", "tun1"])
    wgmark = 0xca6c   # wg-quick's default: 51820
    tables = {"main": [{"net": "0.0.0.0/0", "dev": wan, "metric": 100}, {"net": str(lan), "dev": lan_if, "metric": 0},
                       {"net": str(far_sup), "dev": eth, "metric": 50}],
              "100": [{"net": str(far), "dev": tun, "metric": 10}, {"net": str(carve), "type": "throw"}],
              "51820": [{"net": "0.0.0.0/0", "dev": wg}]}
    rules = [{"prio": 0, "table": "local"}, {"prio": 100, "from": str(vpn), "table": "100"},
             {"prio": 32764, "table": "main", "suppress": 0}, {"prio": 32765, "not": True, "mark": wgmark, "table": "51820"},
             {"prio": 32766, "table": "main"}, {"prio": 32767, "table": "default"}]
    blk = ipaddress.ip_network(f"203.0.113.{r.choice([0, 64, 128])}/26")
    if level >= 8:   # a mark class that bypasses the tunnel (with a blocked range) and a table that walls VPN peers off the LAN
        tables["300"] = [{"net": "0.0.0.0/0", "dev": wan, "metric": 10}, {"net": str(blk), "type": "blackhole"}]
        tables["200"] = [{"net": str(lan), "type": "unreachable"}]
        rules += [{"prio": 110, "mark": 0x100, "mask": 0xf00, "table": "300"}, {"prio": 120, "iif": wg, "table": "200"}]
    public = lambda: f"{r.choice([8, 1, 9, 185, 151])}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}"

    def host_in(net, avoid=None):
        while True:
            a = ipaddress.ip_address(int(net.network_address) + r.randint(2, net.num_addresses - 2))
            if avoid is None or a not in avoid:
                return str(a)
    router_wan = f"{r.choice([81, 88, 95])}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}"
    lanh, vpnh = host_in(lan), host_in(vpn)
    pk = {"lan_out": {"src": lanh, "dst": public(), "iif": lan_if},                       # main's default suppressed -> the tunnel
          "lan_far": {"src": host_in(lan), "dst": host_in(far_sup, far), "iif": lan_if},  # a /16 is not suppressed by 0
          "wg_own": {"src": router_wan, "dst": public(), "mark": wgmark, "iif": "lo"},    # the tunnel's own packets: main
          "vpn_carve": {"src": vpnh, "dst": host_in(carve), "iif": wg},                  # throw: falls through to main's /16
          "vpn_far": {"src": host_in(vpn), "dst": host_in(far, carve), "iif": wg}}       # table 100
    if level >= 8:
        pk.update({"mark_out": {"src": host_in(lan), "dst": public(), "mark": r.choice([0x100, 0x1a5, 0x1c0, 0x13f]), "iif": lan_if},
                   "mark_blk": {"src": host_in(lan), "dst": host_in(blk), "mark": r.choice([0x101, 0x1f0, 0x180]), "iif": lan_if},
                   "mark_miss": {"src": host_in(lan), "dst": public(), "mark": r.choice([0x200, 0x2a5, 0x010, 0xf00]), "iif": lan_if},
                   "peer_lan": {"src": vpnh, "dst": host_in(lan), "iif": wg}})           # table 100: no route; 200: unreachable
    names = ["lan_out", "lan_far", "wg_own", "vpn_carve", "vpn_far"] if level < 8 else \
        ["mark_out", "mark_blk", "mark_miss", "wg_own", "vpn_carve", "peer_lan"]
    r.shuffle(names)
    pkts = [pk[n] for n in names]
    expected = [route_lookup7([x for x in rules if x.get("table") != "local"], tables, p) for p in pkts]
    rl = "\n".join(_rule_line(x) for x in sorted(rules, key=lambda x: x["prio"]))
    tl = "\n\n".join(f"$ ip route show table {t}\n" + "\n".join(_route_line(rt) for rt in rts) for t, rts in tables.items())

    def desc(p):
        where = "sent by the router itself" if p["iif"] == "lo" else f"arriving on {p['iif']}"
        return f"A packet from {p['src']} to {p['dst']}, {where}, " + (f"firewall mark {p['mark']:#x}" if p.get("mark") else "no firewall mark")
    prompt = (f"A Linux router (kernel 6.x) has these policy routing rules and tables:\n\n```\n$ ip rule show\n{rl}\n\n{tl}\n```\n\n"
              "None of the destinations below is one of the router's own addresses. Through which interface does the router "
              "send each of these packets? If it drops the packet or has no route for it, answer NONE.\n"
              + _ask([desc(p) for p in pkts]) + _instr(len(pkts)))
    return Item(f"{BLOCK}.subnet.L{level}.{seed}", BLOCK, "subnet", [{"role": "user", "content": prompt}],
                multi_check([_word_check(e) for e in expected]), max_tokens=32000,
                meta={"expected": expected, "packets": [dict(p, name=n) for n, p in zip(names, pkts)], "level": level})


# -- chmod: GNU mode_compile / mode_adjust ported, directories keep setuid/setgid ------------------------------------------

_ALLM = 0o7777


def gnu_chmod(mode: int, spec: str, is_dir: bool = False, umask: int = 0o022) -> int:
    """GNU chmod (gnulib modechange.c, mode_compile + mode_adjust) for levels 7-8, checked against GNU chmod 9 on random
    specs (tests/test_techhelp.py). Beyond chmod_apply: a directory keeps setuid/setgid unless the clause names them
    (`g=rx`, `755` and even `0755` keep setgid; `00755`, `=755`, `g-s` clear it); `o=...` clears the sticky bit;
    `g=u` / `o=g` copy bits; several operators in one clause (`g=u-w`); operator + octal (`=750`, `-6000`); X looks at
    the mode as the clauses before it in the same command left it."""
    new = mode & _ALLM
    for op, flag, affected, value, mentioned in _gnu_compile(spec):
        omit = (0o6000 if is_dir else 0) & ~mentioned
        if flag == "copy":
            value &= new
            value |= (0o444 if value & 0o444 else 0) | (0o222 if value & 0o222 else 0) | (0o111 if value & 0o111 else 0)
        elif flag == "X" and ((new & 0o111) or is_dir):
            value |= 0o111
        value &= (affected if affected else ~umask & _ALLM) & ~omit
        if op == "=":
            new = (new & (((~affected & _ALLM) if affected else 0) | omit)) | value
        elif op == "+":
            new |= value
        else:
            new &= ~value
    return new & _ALLM


def _gnu_compile(spec: str) -> list[tuple]:
    if re.fullmatch(r"[0-7]+", spec):
        m = int(spec, 8)
        return [("=", "plain", _ALLM, m, ((m & 0o6000) | 0o1777) if len(spec) < 5 else _ALLM)]
    out = []
    for clause in spec.split(","):
        m = re.fullmatch(r"([ugoa]*)((?:[-+=](?:[0-7]+|[ugo]|[rwxXst]*))+)", clause)
        if not m:
            raise ValueError(f"invalid mode: {spec!r}")
        affected = 0
        for w in m.group(1):
            affected |= {"u": 0o4700, "g": 0o2070, "o": 0o1007, "a": _ALLM}[w]
        for op, arg in re.findall(r"([-+=])([0-7]+|[ugo]|[rwxXst]*)", m.group(2)):
            mentioned, flag = 0, "copy"
            if arg[:1].isdigit():
                affected = mentioned = _ALLM
                value, flag = int(arg, 8), "plain"
            elif arg in ("u", "g", "o"):
                value = {"u": 0o700, "g": 0o070, "o": 0o007}[arg]
            else:
                value, flag = 0, "plain"
                for p in arg:
                    value |= {"r": 0o444, "w": 0o222, "x": 0o111, "s": 0o6000, "t": 0o1000}.get(p, 0)
                    if p == "X":
                        flag = "X"
            out.append((op, flag, affected, value, mentioned or ((affected & value) if affected else value)))
    return out


def chmod_78(seed: int, level: int) -> Item:
    r = rng(BLOCK, f"chmod{level}", seed)
    um = r.choice([0o022, 0o027, 0o077, 0o002])
    ops = {"oct3": ["755", "770", "750", "775"], "oct4": ["0770", "0775", "0755", "0750"],
           "oct5": ["00775", "02750", "=770", "=2750", "00750"], "fileoct": ["0750", "2755", "4750", "0644", "0754"],
           "who_eq": ["g=rx", "g=rwx", "u=rwx,g=rx,o=", "go=rx"], "who_o": ["o=rx", "o=rwx", "o=", "o=r"],
           "bare_d": ["+w", "-w", "=rwx", "+rX", "=rx", "-x"], "bare_f": ["+w", "-w", "+x", "+rX", "-x", "-rwx"],
           "X": ["go+X", "o+X", "a+X"], "special": ["u+s", "g+s", "ug+s"], "special_d": ["g+s", "u+s", "+s"],
           "copy": ["g=u", "o=g", "go=u"], "t": ["+t"], "minus_sp": ["-6000", "g-s"],
           "multi": ["g=u-w", "u=rwX,go=u-w", "a=rX,u+w", "o=g-x"], "xsame": ["u+x,go+X", "a-x,u+X", "go-x,a+X"]}
    # (path, is a directory, start modes, the first step, the other steps in a random order): a numeric mode first, so
    # every later step still shows in the result (a numeric mode or a bare '=' on a file as the last step would erase them)
    objs = {7: [("shared/", True, [0o2775, 0o2770, 0o2750], "oct3", ["who_eq", "bare_d", "X"]),
                ("deploy.sh", False, [0o755, 0o750, 0o775, 0o700], "fileoct", ["special", "copy", "bare_f"]),
                ("tmp/", True, [0o1777, 0o1775, 0o3777, 0o3770], "oct4", ["who_o", "t", "minus_sp"])],
            8: [("shared/", True, [0o2775, 0o2770, 0o2750], "oct3", ["who_eq", "bare_d", "multi", "X"]),
                ("run.sh", False, [0o755, 0o750, 0o775, 0o700], "fileoct", ["special", "copy", "bare_f", "xsame"]),
                ("tmp/", True, [0o1777, 0o1775, 0o3777, 0o3770], "oct4", ["who_o", "t", "minus_sp", "bare_d"]),
                ("cache/", True, [0o2775, 0o3775, 0o2750], "oct5", ["special_d", "multi", "who_eq", "bare_d"])]}[level]
    blocks, exp, files = [], [], []
    for path, is_dir, starts, first, rest in objs:
        start = r.choice(starts)
        rest = list(rest)
        r.shuffle(rest)
        kinds = [first] + rest
        steps = []
        for k in kinds:
            steps.append(r.choice([o for o in ops[k] if o not in steps]))
        mode = start
        for st in steps:
            mode = gnu_chmod(mode, st, is_dir, um)
        what = f"directory `{path}`" if is_dir else f"file `{path}`"
        blocks.append(f"The {what} has mode {start:04o}:\n```\n" + "\n".join(f"chmod {st} {path}" for st in steps) + "\n```")
        exp.append(mode)
        files.append({"path": path, "start": f"{start:04o}", "steps": steps, "is_dir": is_dir, "expected": f"{mode:04o}"})
    prompt = (f"On Linux, as the owner (who is also in each one's group), with GNU coreutils 9 chmod, the shell's umask is "
              f"{um:04o}. These commands run in order, each on its own path:\n\n" + "\n\n".join(blocks)
              + "\n\nWhat is each one's mode afterwards, as four octal digits (e.g. 2755)? Ignore chmod's warnings.\n"
              + _ask([f"`{f['path']}`" for f in files]) + _instr(len(files)))
    return Item(f"{BLOCK}.chmod_seq.L{level}.{seed}", BLOCK, "chmod_seq", [{"role": "user", "content": prompt}],
                multi_check([_octal_check7(m) for m in exp]), max_tokens=32000,
                meta={"expected": [f"{m:04o}" for m in exp], "files": files, "umask": f"{um:04o}", "level": level})


# -- logs: one incident seen through five different clocks ---------------------------------------------------------------

# UTC offsets in minutes during September 2026 (checked against zoneinfo in tests/test_techhelp.py): London is on BST,
# Sydney not yet on daylight time (from 4 October), Phoenix never is, Kolkata and Kathmandu are off by 30 / 45 minutes
TZ_SEPT = {"Europe/London": 60, "America/New_York": -240, "Australia/Sydney": 600, "Asia/Kolkata": 330,
           "America/Phoenix": -420, "Asia/Kathmandu": 345}


def log_78(seed: int, level: int) -> Item:
    """The root cause's dependents fail within seconds of each other on hosts whose clocks read differently: UTC, a
    numeric offset, a clock that chronyd later finds behind, syslog in a named time zone without an offset, and (level
    8) dmesg seconds since boot. They name their upstream by address only (an inventory maps it); level 8 adds a failure
    one step further down, an unrelated late failure, and the root cause's UTC time."""
    r = rng(BLOCK, f"log{level}", seed)
    svcs = ["postgres", "redis", "api", "worker", "nginx", "minio", "keycloak", "grafana", "queue", "search"]
    pick = r.sample(svcs, 8)
    root, deps, herring, unrel = pick[0], pick[1:5], pick[5], pick[6]
    tzname = r.choice(sorted(TZ_SEPT))
    tz_d = timezone(timedelta(minutes=TZ_SEPT[tzname]))
    tz_b = timezone(timedelta(hours=r.choice([-7, -5, 2, 3, 9])))
    skew = r.randint(45, 200)
    t0 = datetime(2026, 9, r.randint(1, 25), r.randint(0, 23), r.randint(0, 59), r.randint(0, 59), tzinfo=timezone.utc)
    boot = t0 - timedelta(seconds=r.randint(20000, 400000))
    frac = r.randint(50, 449) / 1000   # dmesg: the kill happened frac s after t0 (floor and round agree)
    port = {s: CONVENTIONAL[s] for s in svcs}
    # level 7: root on box-d (named zone); dependents on box-b, box-a, box-c in true order. Level 8: root on box-e (dmesg),
    # the fan-out on box-d, box-a, box-c, and one more on box-b that depended on the second one
    if level < 8:
        hosts = {root: "box-d", deps[0]: "box-b", deps[1]: "box-a", deps[2]: "box-c"}
        fail = [deps[0], deps[1], deps[2]]
    else:
        hosts = {root: "box-e", deps[0]: "box-d", deps[1]: "box-a", deps[2]: "box-c", deps[3]: "box-b"}
        fail = [deps[0], deps[1], deps[2], deps[3]]
    hosts.setdefault(herring, "box-a")
    hosts.setdefault(unrel, "box-a")
    for s in svcs:
        hosts.setdefault(s, r.choice(["box-a", "box-b", "box-c"]))
    ip = {h: f"10.0.{i + 1}.{r.randint(10, 60)}" for i, h in enumerate(["box-a", "box-b", "box-c", "box-d", "box-e"])}
    addr = {s: f"{ip[hosts[s]]}:{port[s]}" for s in svcs}
    pid = {s: r.randint(200, 9000) for s in svcs + ["chronyd"]}
    ev = []   # (true time, host, service, level, message)
    gaps = [r.randint(2, 6) for _ in fail]
    times, t = [], t0 + timedelta(seconds=r.randint(1, 3))
    for g in gaps:
        t += timedelta(seconds=g)
        times.append(t)
    causes = {"disk": "could not write to /var/lib/data: No space left on device", "perm": "open /etc/ssl/private/server.key: permission denied",
              "port": f"could not bind to 0.0.0.0:{port[root]}: Address already in use"}
    if level < 8:
        ev.append((t0, "box-d", root, "error", causes[r.choice(sorted(causes))]))
        ev.append((t0 + timedelta(seconds=1), "box-d", root, "error", f"{root}.service: Main process exited, status=1/FAILURE"))
    else:
        rss = r.randint(3_000_000, 9_000_000)
        ev.append((t0, "box-e", root, "kernel", f"Out of memory: Killed process {pid[root]} ({root}) total-vm:{rss + r.randint(10**5, 10**6)}kB, "
                                                  f"anon-rss:{rss}kB, file-rss:0kB, shmem-rss:0kB, UID:{r.randint(100, 999)} pgtables:{r.randint(8000, 20000)}kB oom_score_adj:0"))
    phr = ["dial tcp {a}: connect: connection refused", "upstream {a} unreachable: no route to host",
           "request to {a} failed: connection reset by peer", "health check of {a} failed: timeout after 2s"]
    for i, (d, tt) in enumerate(zip(fail, times)):
        up = deps[1] if (level >= 8 and i == 3) else root
        ev.append((tt, hosts[d], d, "error", r.choice(phr).format(a=addr[up]) + ", giving up"))
    rh_t = t0 - timedelta(seconds=r.randint(60, 150))   # an earlier error that recovered
    ev.append((rh_t, hosts[herring], herring, "error", f"connection to {addr[r.choice([s for s in svcs if s not in (root, herring)])]} timed out, retrying in 5s"))
    ev.append((rh_t + timedelta(seconds=6), hosts[herring], herring, "info", "connected, resuming"))
    if level >= 8:   # an unrelated failure after everything else
        ev.append((times[-1] + timedelta(seconds=r.randint(4, 9)), hosts[unrel], unrel, "error",
                   f"failed to rotate /var/log/{unrel}/{unrel}.log: Disk quota exceeded, exiting"))
    noise = [("info", "health check ok"), ("info", "config reloaded"), ("warn", "slow request 812ms"), ("info", "rotating logs"),
             ("warn", "high memory usage 87%"), ("info", "checkpoint complete")]
    alive = [s for s in svcs if s not in (root, *fail, unrel)]
    for _ in range(14 if level < 8 else 16):
        s = r.choice(alive)
        lv, msg = r.choice(noise)
        ev.append((t0 + timedelta(seconds=r.randint(-240, 30)), hosts[s], s, lv, msg))
    s = next((x for x in alive if hosts[x] == "box-b"), None)
    if s:   # box-b logs something before the incident too
        ev.append((t0 - timedelta(seconds=r.randint(5, 200)), "box-b", s, "info", "health check ok"))
    ev.append((times[-1] + timedelta(seconds=r.randint(200, 400)), "box-c", "chronyd", "warn",
               f"System clock is {skew} seconds behind NTP time, stepping the clock forward"))
    if level >= 8:
        for _ in range(3):
            ev.append((t0 + timedelta(seconds=r.randint(-300, -2)), "box-e", "kernel", "kernel",
                       r.choice(["eth0: Link is Up - 10Gbps/Full", "audit: type=1400 apparmor=\"STATUS\"",
                                 "EXT4-fs (nvme0n1p2): mounted filesystem", "systemd-journald[412]: Time spent on flushing"])))
    ev.sort(key=lambda e: e[0])
    logs = {}
    for tt, host, s, lv, msg in ev:
        if host == "box-e":
            if lv != "kernel":
                continue
            mono = (tt - boot).total_seconds() + (frac if s == root else r.randint(0, 999999) / 1e6)
            logs.setdefault(host, []).append(f"[{mono:12.6f}] {msg}")
            continue
        if host == "box-d":   # traditional syslog: local time, the day padded with a space, no year or offset
            lt = tt.astimezone(tz_d)
            logs.setdefault(host, []).append(f"{lt:%b} {lt.day:2d} {lt:%H:%M:%S} box-d {s}[{pid[s]}]: {lv.upper()} {msg}")
            continue
        shown = tt.astimezone(tz_b) if host == "box-b" else (tt - timedelta(seconds=skew) if host == "box-c" else tt)
        logs.setdefault(host, []).append(f"{shown.strftime('%Y-%m-%dT%H:%M:%S%z')} {host} {s}[{pid[s]}]: {lv.upper()} {msg}")
    heads = {"box-a": "box-a (journalctl -o short-iso)", "box-b": "box-b (journalctl -o short-iso)",
             "box-c": "box-c (journalctl -o short-iso)", "box-d": f"box-d (/var/log/syslog, local time; /etc/timezone is {tzname})",
             "box-e": f"box-e (dmesg; the kernel booted at {boot.strftime('%Y-%m-%dT%H:%M:%SZ')})"}
    blocks = "\n\n".join(f"{heads[h]}:\n```\n" + "\n".join(logs[h]) + "\n```" for h in sorted(logs))
    inv = "\n".join(f"- {s}: {addr[s]}" for s in sorted(svcs, key=lambda x: addr[x]))
    last = fail[-1]
    elapsed = (times[-1] - t0).total_seconds() - (frac if level >= 8 else 0)
    qs = ["Which service is the root cause - the one that failed first and made the others fail?",
          "Which service failed next, as the first consequence of the root cause?",
          "Which service was the last to fail as a consequence of the root cause?",
          "How many seconds passed between the root cause failing and that last consequence (to the nearest second)?"]
    exp = [root, fail[0], last, str(round(elapsed))]
    checks = [_word_check(root), _word_check(fail[0]), _word_check(last), _num_check7(round(elapsed), 1)]
    if level >= 8:
        qs.append("At what UTC time did the root cause fail? Answer as HH:MM:SS.")
        exp.append(t0.strftime("%H:%M:%S"))
        checks.append(_word_check(exp[-1]))
    prompt = (f"My stack stopped working. Each machine logs with its own clock and format. Services and the addresses they "
              f"listen on:\n{inv}\n\nThe logs:\n\n{blocks}\n\nAnswer the service questions with the service name only.\n"
              + _ask(qs) + _instr(len(qs)))
    return Item(f"{BLOCK}.log_root.L{level}.{seed}", BLOCK, "log_root", [{"role": "user", "content": prompt}],
                multi_check(checks), max_tokens=32000,
                meta={"expected": exp, "level": level, "tz": tzname, "skew": skew, "hosts": {s: hosts[s] for s in [root, *fail]}})


# ---- levels 9-10: aimed at the frontier (v0.11) ----------------------------------------------------------------------
# Measured on Claude Opus / Sonnet (v0.11-dev2): compose L7-L8 all right; chmod L7 1 of 3 - both missed exactly the GNU
# special-bit rules. Levels 9-10 add rare corners of the real tools (each checked against the tool where it runs here),
# decoys that look like a familiar rule, longer sequences and more questions per item (finer partial credit). Nothing
# below is reached from levels 1-8.

# -- compose: YAML merge keys vs extends, ${} inside .env, which .env, include + env_file, :+, profiles -------------------

def compose_910(seed: int, level: int) -> Item:
    """Checked with `docker compose config` (v2.40). Level 9 (10 questions): a YAML merge key (`<<: *app`) is shallow - a
    service's own `ports:` replaces the anchor's list - and with `<<: [*a, *b]` the first anchor wins; `extends` + `!override`
    replaces; `.env` may reference variables defined above it and a shell variable wins even there; a later `--env-file`
    sees the variables of an earlier one, not the other way round; `-f sub/compose.yaml` reads `sub/.env`, never the .env
    of the directory you are in; `--env-file` replaces .env; `include` with its own env_file (the main .env still wins);
    `${DEBUG:+x}` for any non-empty value, even 0; `--profile` replaces COMPOSE_PROFILES from .env; `$BASE_PORT1` is the
    variable BASE_PORT1. Level 10 (13): COMPOSE_ENV_FILES works from the shell but not from .env; `${VAR?msg}` accepts an
    empty value (a random port, no error)."""
    r = rng(BLOCK, f"compose{level}", seed)
    tp = r.choice([80, 3000, 8080])
    B, B0, S, P = r.choice([7070, 7171, 7272]), r.choice([7000, 7100]), r.choice([6060, 6161]), r.choice([5050, 5151])
    WD, W2, SD, DBG = r.choice([8100, 8110]), r.choice([8002, 8012, 8022]), r.choice([8888, 8898]), r.choice([9229, 9339])
    M1, MD, A1, A2, AD = r.choice([9100, 9110]), r.choice([9300, 9310]), r.choice([9093, 9094]), r.choice([9193, 9194]), r.choice([9400, 9410])
    OD, Q, CD, CE = r.choice([8500, 8510]), r.choice([4040, 4141]), r.choice([6379, 6380]), r.choice([16379, 26379])
    ten = level >= 10
    main = ["include:", "  - path: metrics/compose.yaml", "    env_file: metrics/metrics.env",
            "x-app: &app", "  image: example/app:3", "  restart: unless-stopped", "  ports:", f'    - "${{BASE_PORT:-{B0}}}:{tp}"',
            "x-ops: &ops", "  image: example/ops:1", "  ports:", f'    - "${{OPS_PORT:-{OD}}}:{tp}"',
            "services:", "  web:", "    <<: *app", "    ports:", f'      - "${{WEB_PORT}}:{tp}"', f'      - "${{DEBUG:+{DBG}}}:{tp}"',
            "  api:", "    <<: *app", '    profiles: ["api"]',
            "  admin:", "    <<: [*ops, *app]",
            "  worker:", "    extends:", "      file: common.yaml", "      service: base", "    ports: !override", f'      - "{W2}:{tp}"']
    if ten:
        main += ["  cache:", "    image: example/cache:1", "    ports:", f'      - "${{CACHE_PORT:-{CD}}}:{tp}"']
    files = [("compose.yaml", main),
             ("common.yaml", ["services:", "  base:", "    image: example/app:3", "    ports:", f'      - "${{WORKER_PORT:-{WD}}}:{tp}"']),
             (".env", ["COMPOSE_PROJECT_NAME=stack", "COMPOSE_PROFILES=api"] + (["COMPOSE_ENV_FILES=extra.env"] if ten else [])
              + [f"BASE_PORT={B}", "WEB_PORT=1${BASE_PORT}", f"ALERT_PORT={A2}"] + (["TOKEN_PORT="] if ten else [])),
             ("prod.env", ["COMPOSE_PROJECT_NAME=stack", f"BASE_PORT={P}", "WEB_PORT=$BASE_PORT1"]),
             ("base.env", [f"BASE_PORT={Q}"]),
             ("web.env", ["WEB_PORT=${BASE_PORT:-4}4"]),
             ("sub/compose.yaml", ["services:", "  web:", "    image: example/app:3", "    ports:", f'      - "${{WEB_PORT:-{SD}}}:{tp}"']),
             ("metrics/compose.yaml", ["services:", "  metrics:", "    image: example/metrics:1", "    ports:",
                                       f'      - "${{METRICS_PORT:-{MD}}}:{tp}"', f'      - "${{ALERT_PORT:-{AD}}}:{tp}"']),
             ("metrics/metrics.env", [f"METRICS_PORT={M1}", f"ALERT_PORT={A1}"])]
    if ten:
        files += [("extra.env", [f"CACHE_PORT={CE}"]),
                  ("compose.token.yaml", ["services:", "  token:", "    image: example/token:1", "    ports:",
                                          f'      - "${{TOKEN_PORT?set TOKEN_PORT}}:{tp}"'])]
    one = lambda *ps: {str(p) for p in ps}
    qa = [("docker compose up -d", "web", one(f"1{B}")), ("DEBUG=0 docker compose up -d", "web", one(f"1{B}", DBG)),
          ("docker compose up -d", "api", one(B)), ("docker compose --profile ops up -d", "api", set()),
          ("docker compose up -d", "metrics", one(M1, A2)), ("docker compose up -d", "worker", one(W2)),
          ("docker compose -f sub/compose.yaml up -d", "web", one(SD)), ("docker compose --env-file prod.env up -d", "web", set()),
          ("docker compose up -d", "admin", one(OD)),                                        # <<: [*ops, *app]: ops' ports
          ("docker compose --env-file web.env --env-file base.env up -d", "web", one(44))]   # web.env cannot see base.env
    if ten:
        qa += [("docker compose up -d", "cache", one(CD)),                                   # COMPOSE_ENV_FILES in .env: ignored
               ("COMPOSE_ENV_FILES=extra.env docker compose up -d", "cache", one(CE)),
               ("docker compose -f compose.yaml -f compose.token.yaml up -d", "web", one(f"1{B}"))]   # empty is fine for ?
    r.shuffle(qa)
    body = "\n\n".join(f"`{n}`:\n```{'yaml' if n.endswith('.yaml') else ''}\n" + "\n".join(t) + "\n```" for n, t in files)
    qs = [f"After `docker compose down`, I run `{c}`: on which host TCP port(s) can I reach container port {tp} of `{s}`?" for c, s, _w in qa]
    prompt = (f"A directory contains these files (paths relative to it):\n\n{body}\n\nWith Docker Compose v2.40 in bash, in that "
              "directory, with no COMPOSE_*, DEBUG or *_PORT variables in the environment unless a command sets one:\n" + _ask(qs)
              + "\nIf it is not reachable from the host at a fixed TCP port, or not running at all (also when Compose refuses to "
              "start), answer NONE; if there are several ports, list them all." + _instr(len(qs)))
    return Item(f"{BLOCK}.compose_port.L{level}.{seed}", BLOCK, "compose_port", [{"role": "user", "content": prompt}],
                multi_check([_set_check7(w) for _c, _s, w in qa]), max_tokens=32000,
                meta={"expected": [", ".join(sorted(w)) or "NONE" for _c, _s, w in qa], "level": level,
                      "commands": [c for c, _s, _w in qa], "services": [s for _c, s, _w in qa], "container_port": tp,
                      "files": {n: "\n".join(t) + "\n" for n, t in files}})


# -- nginx: variables in proxy_pass, $request_uri / $uri, rewrite never sees the query, redirects, captures --------------

def ngx_proxy9(server_rw: list[tuple], locs: list[tuple], request: str) -> tuple[str, str]:
    """ngx_proxy plus (nginx docs): a `proxy_pass` URI with variables is evaluated and sent as is - the request's
    arguments are not added; $request_uri is the original request line URI with its arguments, $uri the current
    normalized URI without them; $1..$9 come from the location's regex, but every `rewrite` evaluated after it replaces
    them with its own groups - empty when it does not match or has none (checked on nginx 1.31: tests/test_techhelp.py);
    `rewrite ... permanent|redirect` answers the client (301 / 302) with the new URI and the arguments; a request for a
    prefix location's name without its trailing slash, when that location proxies, gets a 301 to the name with the slash
    (and the arguments) - unless an exact location matches (docs: location)."""
    raw = request
    path, _, args = raw.partition("?")
    uri = re.sub(r"/{2,}", "/", path)
    changed, caps = False, ()
    proxies = lambda body: not any(f in ("permanent", "redirect") for _rx, _rp, f in body.get("rw", []))

    def sub(rx, repl):
        nonlocal uri, args, caps
        caps = re.search(rx, uri).groups()
        uri, args = _ngx_sub(rx, repl, uri, args)
    for rx, repl, flag in server_rw:
        caps = ()   # an evaluated rewrite regex resets the captures, matching or not
        if re.search(rx, uri):
            sub(rx, repl)
            changed = True
            if flag in ("permanent", "redirect"):
                return ("301" if flag == "permanent" else "302"), uri + (("?" + args) if args else "")
            if flag in ("last", "break"):
                break
    for _ in range(10):
        if not any(lc[0] == "=" and lc[1] == uri for lc in locs) and any(
                lc[0] in ("", "^~") and lc[1] == uri + "/" and proxies(lc[2]) for lc in locs):
            return "301", uri + "/" + (("?" + args) if args else "")
        loc = _nginx_select(locs, uri)
        if loc is None:
            return "404", ""
        if loc[0] in ("~", "~*"):
            caps = re.search(loc[1], uri, re.I if loc[0] == "~*" else 0).groups() or caps
        body = loc[2]
        again = broke = False
        for rx, repl, flag in body.get("rw", []):
            caps = ()   # an evaluated rewrite regex resets the captures, matching or not
            if re.search(rx, uri):
                sub(rx, repl)
                changed = True
                if flag in ("permanent", "redirect"):
                    return ("301" if flag == "permanent" else "302"), uri + (("?" + args) if args else "")
                if flag == "break":
                    broke = True
                    break
                again = True
                if flag == "last":
                    break
        if again:
            continue
        up, puri = body["proxy"]
        q = ("?" + args) if args else ""
        if puri and "$" in puri:   # variables: the evaluated URI replaces the request URI, arguments included or not
            val = puri.replace("$request_uri", raw).replace("$uri", uri).replace("$args", args)
            return up, re.sub(r"\$(\d)", lambda g: caps[int(g.group(1)) - 1] if int(g.group(1)) <= len(caps) else "", val)
        if puri is None:
            return up, (raw if not changed else uri + q)
        if broke:
            return up, uri + q
        return up, puri + uri[len(loc[1]):] + q
    return "500", ""


def nginx_910(seed: int, level: int) -> Item:
    r = rng(BLOCK, f"nginx{level}", seed)
    u = [f"{c}_pool" for c in r.sample(["blue", "green", "amber", "violet", "teal", "coral", "slate", "olive", "ruby", "indigo",
                                        "jade", "rust", "sand", "plum"], 13)]
    api, app, static, legacy, v1 = r.choice(["api", "svc"]), r.choice(["app", "portal"]), r.choice(["static", "assets"]), \
        r.choice(["legacy", "classic"]), r.choice(["v1", "beta"])
    shop, store = r.choice([("cart", "store"), ("buy", "market")])
    internal, base, v2, target = r.choice(["internal", "rest"]), r.choice(["base", "srv"]), r.choice(["v2", "next"]), r.choice(["new", "docs"])
    word, word2, img, name = r.choice(["users", "orders", "items"]), r.choice(["report", "invoice", "profile"]), \
        r.choice(["logo", "banner", "avatar"]), r.choice(["cat", "dog", "fox", "owl"])
    n, k, tail = r.randint(11, 99), r.randint(2, 9), r.choice(["s", "x", "data"])
    server_rw = [(rf"^/{v1}/(.*)$", f"/{api}/$1", None), (r"^/search/(\w+)$", f"/{api}/find?q=$1", "last"),
                 (r"^/lookup\?id=(\d+)$", f"/{api}/items/$1", "last")]   # never matches: rewrite sees the URI without arguments
    locs = [("", "/", {"proxy": (u[0], None)}),
            ("", f"/{api}/", {"proxy": (u[1], f"/{internal}/")}),
            ("", f"/{app}", {"proxy": (u[2], "/")}),
            ("^~", f"/{static}/", {"proxy": (u[3], None)}),
            ("~*", r"\.(png|jpe?g)$", {"proxy": (u[4], None)}),
            ("", f"/{legacy}/", {"rw": [(rf"^/{legacy}/(.*)$", f"/{v2}/$1", "break")], "proxy": (u[5], f"/{base}/")}),
            ("", f"/{shop}/", {"rw": [(rf"^/{shop}/(.*)$", f"/{store}/$1", None), (rf"^/{store}/(.*)\.php$", f"/{legacy}/$1", None)],
                               "proxy": (u[7], None)}),
            ("~", r"^/u/(\d+)/(\w+)$", {"proxy": (u[8], "/users/$1/$2")}),
            ("", "/files/", {"rw": [(r"^/files/(.*)$", "/f/$1", "break")], "proxy": (u[9], "$request_uri")}),
            ("", "/go/", {"rw": [(r"^/go/(.*)$", f"/{target}/$1", "permanent")], "proxy": (u[0], None)})]
    locs += [("", "/n/", {"proxy": (u[10], "$uri")}),
             ("~", r"^/img/(\w+)\.(gif|webp)$", {"rw": [(r"^/img/(\w)\w*\.gif$", "/thumbs/$1.gif", "break")],
                                                 "proxy": (u[11], "/cdn/$1")})]
    if level >= 10:
        locs.append(("", "/tmp/", {"rw": [(r"^/tmp/(.*)$", "/scratch/$1?", "redirect")], "proxy": (u[0], None)}))
    r.shuffle(locs)
    reqs = [f"/u/{n}/{word}?tab={k}",               # variables in proxy_pass: sent as is, the arguments are dropped
            f"/files/{word2}.txt?v={k}",            # $request_uri: the original URI, whatever rewrite ... break did
            f"/img/{name}.gif",                     # the rewrite's capture replaces the location's $1
            f"/img/{name}.webp?s={k}",              # the rewrite does not match, yet it empties $1; arguments dropped
            f"/n//{word}/{word2}?x={k}",            # $uri: normalized, without arguments
            f"/{v1}/u/{n}/{word}",                  # server rewrite first: the ^/u/ regex no longer matches
            f"/go/{word}?ref={k}",                  # rewrite ... permanent: a 301 with the arguments appended
            f"/search/{word}?page={k}"]             # a server rewrite with new arguments: the old ones follow
    if level >= 10:   # (Claude Opus answered the level-9 set right)
        reqs += [f"/{api}?x={k}",                   # the proxying prefix location's name without the slash: 301 to it
                 f"/tmp/{word2}?y={k}",             # redirect to a replacement ending in '?': the arguments are dropped
                 f"/lookup?id={n}",                 # the server rewrite's regex would need the arguments: it never matches
                 f"/{shop}/{word2}.php"]            # two rewrites without a flag, then the locations are searched again
    r.shuffle(reqs)
    expected = [ngx_proxy9(server_rw, locs, q) for q in reqs]
    conf = ["server {", "    listen 80;", "    server_name example.lan;"]
    for rx, repl, flag in server_rw:
        conf.append(f"    rewrite {rx} {repl}{' ' + flag if flag else ''};")
    for mod, pat, body in locs:
        inner = [f"        rewrite {rx} {repl}{' ' + flag if flag else ''};" for rx, repl, flag in body.get("rw", [])]
        up, puri = body["proxy"]
        if not any(f in ("permanent", "redirect") for _rx, _rp, f in body.get("rw", [])):
            inner.append(f"        proxy_pass http://{up}{puri or ''};")
        conf.append(f"    location {mod + ' ' if mod else ''}{pat} {{\n" + "\n".join(inner) + "\n    }")
    conf.append("}")
    prompt = ("Here is an nginx server block (nginx 1.26, default settings otherwise; every name after http:// is an "
              "upstream{} group):\n\n```nginx\n" + "\n".join(conf) + "\n```\n\nA client sends these requests for Host "
              "example.lan, exactly as written. For each, which upstream finally handles it, and which URI (path and query "
              "string) is in the request line nginx sends to it? Answer as `<upstream> <URI>`, e.g. `blue_pool /x/y?z=1`. If "
              "nginx itself answers with a redirect, answer `<status> <path and query of the Location>`, e.g. `302 /a?b=1`.\n"
              + _ask([f"GET {q}" for q in reqs]) + _instr(len(reqs)))
    return Item(f"{BLOCK}.nginx_route.L{level}.{seed}", BLOCK, "nginx_route", [{"role": "user", "content": prompt}],
                multi_check([_proxy_check(up, p) for up, p in expected]), max_tokens=32000,
                meta={"expected": [f"{up} {p}" for up, p in expected], "uris": reqs, "level": level})


# -- policy routing: OpenVPN's def1 routes vs suppress_prefixlength, 'not' over two selectors, ports, local, goto --------

def route_lookup9(rules: list[dict], tables: dict, pkt: dict) -> str:
    """route_lookup7 plus: `ipproto`/`dport` selectors, the local table (answer 'local'), `goto N` (jump to the rule with
    preference N and go on from there), and `prohibit`/`blackhole`/`unreachable` rule actions (dropped)."""
    order = sorted(rules, key=lambda x: x["prio"])
    i = 0
    while i < len(order):
        rule = order[i]
        i += 1
        ok = ((not rule.get("from") or ipaddress.ip_address(pkt["src"]) in ipaddress.ip_network(rule["from"]))
              and (not rule.get("to") or ipaddress.ip_address(pkt["dst"]) in ipaddress.ip_network(rule["to"]))
              and (rule.get("mark") is None or ((rule["mark"] ^ pkt.get("mark", 0)) & rule.get("mask", 0xffffffff)) == 0)
              and (not rule.get("iif") or rule["iif"] == pkt.get("iif"))
              and (not rule.get("proto") or rule["proto"] == pkt.get("proto"))
              and (not rule.get("dport") or rule["dport"][0] <= pkt.get("dport", -1) <= rule["dport"][1]))
        if rule.get("not"):
            ok = not ok
        if not ok:
            continue
        if rule.get("goto") is not None:
            i = next(j for j, x in enumerate(order) if x["prio"] == rule["goto"])
            continue
        if rule.get("action"):
            return "none"
        hits = [rt for rt in tables.get(rule["table"], []) if ipaddress.ip_address(pkt["dst"]) in ipaddress.ip_network(rt["net"])]
        if not hits:
            continue
        plen = max(ipaddress.ip_network(rt["net"]).prefixlen for rt in hits)
        best = min((rt for rt in hits if ipaddress.ip_network(rt["net"]).prefixlen == plen), key=lambda rt: rt.get("metric", 0))
        if best.get("type") == "throw":
            continue
        if best.get("type") in ("blackhole", "unreachable", "prohibit"):
            return "none"
        if rule.get("suppress") is not None and plen <= rule["suppress"]:
            continue
        return "local" if best.get("type") == "local" else best["dev"]
    return "none"


def _rule_line9(rule: dict) -> str:
    s = f"{rule['prio']}:\t" + ("not " if rule.get("not") else "") + f"from {rule.get('from') or 'all'}"
    if rule.get("to"):
        s += f" to {rule['to']}"
    if rule.get("mark") is not None:
        s += f" fwmark {rule['mark']:#x}" + (f"/{rule['mask']:#x}" if rule.get("mask") is not None else "")
    if rule.get("iif"):
        s += f" iif {rule['iif']}"
    if rule.get("proto"):
        s += f" ipproto {rule['proto']}"
    if rule.get("dport"):
        lo, hi = rule["dport"]
        s += f" dport {lo}" + (f"-{hi}" if hi != lo else "")
    if rule.get("goto") is not None:
        return s + f" goto {rule['goto']}"
    s += f" {rule['action']}" if rule.get("action") else f" lookup {rule['table']}"
    if rule.get("suppress") is not None:
        s += f" suppress_prefixlength {rule['suppress']}"
    return s


def routing_910(seed: int, level: int) -> Item:
    r = rng(BLOCK, f"subnet{level}", seed)
    x = r.randint(1, 200)
    lan, guest, iot = (ipaddress.ip_network(f"192.168.{x + i}.0/24") for i in range(3))
    vpn = ipaddress.ip_network(f"10.{r.randint(1, 250)}.{r.randint(0, 250)}.0/24")
    far = ipaddress.ip_network(f"172.{r.randint(16, 31)}.{r.randint(0, 250)}.0/24")
    far_sup = far.supernet(new_prefix=16)
    carve = list(far.subnets(new_prefix=26))[r.randint(0, 3)]
    part = ipaddress.ip_network(f"198.51.100.{r.choice([0, 128])}/25")
    blk = ipaddress.ip_network(f"203.0.113.{r.choice([0, 64, 128])}/26")
    wan, lan_if, eth, wg, tun, ovpn, fib, part_if = r.choice(["wan0", "ppp0"]), r.choice(["lan0", "br-lan"]), \
        r.choice(["eth1", "eth2"]), r.choice(["wg0", "wg1"]), r.choice(["tun0", "tun1"]), r.choice(["tun5", "tun8"]), \
        r.choice(["eth5", "eth6"]), r.choice(["eth3", "eth4"])
    gw_lan = str(lan.network_address + 1)
    wgmark, mk = 0xca6c, r.choice([0x10, 0x20, 0x40])
    tables = {"local": [{"net": f"{gw_lan}/32", "type": "local"}],
              "main": [{"net": "0.0.0.0/0", "dev": wan, "metric": 100}, {"net": "0.0.0.0/1", "dev": ovpn},
                       {"net": "128.0.0.0/1", "dev": ovpn}, {"net": str(lan), "dev": lan_if},
                       {"net": str(far_sup), "dev": eth, "metric": 50}],
              "100": [{"net": str(far), "dev": tun, "metric": 10}, {"net": str(carve), "type": "throw"}],
              "51820": [{"net": "0.0.0.0/0", "dev": wg}],
              "300": [{"net": "0.0.0.0/0", "dev": wan, "metric": 10}, {"net": str(blk), "type": "blackhole"}],
              "200": [{"net": str(lan), "type": "unreachable"}],
              "400": [{"net": "0.0.0.0/0", "dev": fib}],
              "500": [{"net": str(part), "dev": part_if}]}
    rules = [{"prio": 0, "table": "local"}, {"prio": 100, "from": str(vpn), "table": "100"},
             {"prio": 105, "from": str(lan), "proto": "tcp", "dport": (443, 443), "table": "400"},
             {"prio": 110, "mark": 0x100, "mask": 0xf00, "table": "300"},
             {"prio": 115, "not": True, "from": str(lan), "mark": mk, "table": "500"},
             {"prio": 120, "iif": wg, "table": "200"},
             {"prio": 32764, "table": "main", "suppress": 0}, {"prio": 32765, "not": True, "mark": wgmark, "table": "51820"},
             {"prio": 32766, "table": "main"}, {"prio": 32767, "table": "default"}]
    if level >= 10:   # the guest network jumps straight to main; the IoT network may not leave except HTTPS for updates
        rules += [{"prio": 102, "from": str(guest), "goto": 32766}, {"prio": 104, "from": str(iot), "proto": "tcp", "dport": (8883, 8883), "table": "400"},
                  {"prio": 106, "from": str(iot), "action": "prohibit"}]
        tables["main"].append({"net": str(guest), "dev": "lan1"})
        tables["main"].append({"net": str(iot), "dev": "lan2"})

    def host(net, avoid=None):
        while True:
            a = ipaddress.ip_address(int(net.network_address) + r.randint(2, net.num_addresses - 2))
            if avoid is None or a not in avoid:
                return str(a)
    public = lambda: f"{r.choice([8, 1, 9, 185, 151])}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}"
    router_wan = f"{r.choice([81, 88, 95])}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}"
    pk = {"lan_udp": {"src": host(lan), "dst": public(), "proto": "udp", "dport": r.choice([53, 123, 51820]), "iif": lan_if},
          "lan_https": {"src": host(lan), "dst": public(), "proto": "tcp", "dport": 443, "iif": lan_if},
          "lan_part_mk": {"src": host(lan), "dst": host(part), "proto": "tcp", "dport": 80, "mark": mk, "iif": lan_if},
          "vpn_part": {"src": host(vpn), "dst": host(part), "proto": "tcp", "dport": 22, "iif": wg},
          "to_router": {"src": host(lan), "dst": gw_lan, "proto": "tcp", "dport": 22, "iif": lan_if},
          "wg_own": {"src": router_wan, "dst": public(), "proto": "udp", "dport": 51820, "mark": wgmark, "iif": "lo"},
          "vpn_carve": {"src": host(vpn), "dst": host(carve), "proto": "tcp", "dport": 5432, "iif": wg},
          "guest_blk": {"src": host(guest), "dst": host(blk), "proto": "tcp", "dport": 80, "mark": r.choice([0x101, 0x1f0]), "iif": "lan1"},
          "iot_mqtt": {"src": host(iot), "dst": public(), "proto": "tcp", "dport": 8883, "iif": "lan2"},
          "iot_https": {"src": host(iot), "dst": public(), "proto": "tcp", "dport": 443, "iif": "lan2"}}
    names = ["lan_udp", "lan_https", "lan_part_mk", "vpn_part", "to_router", "wg_own", "vpn_carve"] if level < 10 else \
        ["lan_udp", "lan_part_mk", "vpn_part", "to_router", "wg_own", "guest_blk", "iot_mqtt", "iot_https"]
    r.shuffle(names)
    pkts = [pk[nm] for nm in names]
    expected = [route_lookup9(rules, tables, p) for p in pkts]
    rl = "\n".join(_rule_line9(x) for x in sorted(rules, key=lambda x: x["prio"]))

    def rline(t, rt):
        if rt.get("type") == "local":
            a = rt["net"].split("/")[0]
            return f"local {a} dev {lan_if} proto kernel scope host src {a}"
        return _route_line(rt)
    tl = "\n\n".join(f"$ ip route show table {t}\n" + "\n".join(rline(t, rt) for rt in rts) for t, rts in tables.items())

    def desc(p):
        where = "sent by the router itself" if p["iif"] == "lo" else f"arriving on {p['iif']}"
        return (f"A {p['proto'].upper()} packet from {p['src']} to {p['dst']} port {p['dport']}, {where}, "
                + (f"firewall mark {p['mark']:#x}" if p.get("mark") else "no firewall mark"))
    prompt = (f"A Linux router (kernel 6.x) has these policy routing rules and tables:\n\n```\n$ ip rule show\n{rl}\n\n{tl}\n```\n\n"
              "Through which interface does the router send each of these packets? If it delivers the packet to itself, "
              "answer LOCAL; if it drops it or has no route for it, answer NONE.\n" + _ask([desc(p) for p in pkts]) + _instr(len(pkts)))
    return Item(f"{BLOCK}.subnet.L{level}.{seed}", BLOCK, "subnet", [{"role": "user", "content": prompt}],
                multi_check([_word_check(e) for e in expected]), max_tokens=32000,
                meta={"expected": expected, "packets": [dict(p, name=nm) for nm, p in zip(names, pkts)], "level": level})


# -- chmod: six objects, longer, the directory special-bit rules mixed with long ordinary sequences ----------------------

_CHMOD9_OPS = {"fileoct": ["0750", "2755", "4750", "0644", "0754", "6755"], "oct3": ["755", "770", "750", "775"],
               "oct4": ["0770", "0775", "0755", "0750"], "oct4d": ["0775", "0770", "2770", "0750"],
               "oct5": ["00775", "02750", "=770", "=2750", "00750"],
               "who_eq": ["g=rx", "g=rwx", "u=rwx,g=rx,o=", "go=rx"], "who_f": ["u=rwx,g=rx,o=", "g=rw", "o=r", "ug=rw"],
               "who_o": ["o=rx", "o=rwx", "o=", "o=r"], "bare_d": ["+w", "-w", "=rwx", "+rX", "=rx", "-x"],
               "bare_f": ["+w", "-w", "+x", "+rX", "-x", "-rwx", "+r"], "bare_copy": ["=u", "=g", "+u", "-g"],
               "X": ["go+X", "o+X", "a+X"], "Xd": ["a-x,g+X", "go-x,o+X", "a-x,u+X"], "xsame": ["u+x,go+X", "a-x,u+X", "go-x,a+X"],
               "special": ["u+s", "g+s", "ug+s"], "special_d": ["g+s", "u+s", "+s", "-s"], "copy": ["g=u", "o=g", "go=u", "u=g"],
               "t": ["+t", "o+t"], "minus_sp": ["-6000", "g-s", "u-s"],
               "multi": ["g=u-w", "u=rwX,go=u-w", "a=rX,u+w", "o=g-x", "ug=rwx,o=g-w"]}


def chmod_910(seed: int, level: int) -> Item:
    """gnu_chmod on six (9) or eight (10) paths of 8-9 steps: long ordinary sequences under a umask (one slip carries to
    the end) next to the rules the frontier missed at level 7 - a directory keeps setuid/setgid through `775`, `0775`,
    `g=rx` and `=u` (not through `00775`, `=770` or `-s`), and `o=` clears the sticky bit. (Claude Opus scored 1.0 and
    0.67 on two six-path items; level 10 has two paths and one step per path more.)"""
    r = rng(BLOCK, f"chmod{level}", seed)
    um = r.choice([0o022, 0o027, 0o077, 0o002])
    six = [("build.sh", False, [0o755, 0o750, 0o775, 0o700], "fileoct", ["bare_f", "bare_f", "copy", "who_f", "xsame", "special", "bare_copy"]),
           ("shared/", True, [0o2775, 0o2770, 0o2750, 0o6770], "oct3", ["who_eq", "bare_d", "X", "multi", "oct4d", "special_d", "copy"]),
           ("tmp/", True, [0o1777, 0o1775, 0o3777, 0o3770], "oct4", ["who_o", "t", "minus_sp", "bare_d", "Xd", "multi", "oct3"]),
           ("notes.txt", False, [0o644, 0o664, 0o640, 0o600], "fileoct", ["bare_f", "bare_copy", "multi", "copy", "special", "who_f", "bare_f"]),
           ("cache/", True, [0o2775, 0o3775, 0o2750, 0o6775], "oct5", ["special_d", "multi", "who_eq", "bare_d", "Xd", "bare_copy", "t"]),
           ("srv/", True, [0o6775, 0o7775, 0o2755], "oct4d", ["copy", "who_o", "multi", "minus_sp", "bare_copy", "X", "oct3"])]
    if level < 10:
        objs = six
    else:
        more = {"build.sh": "multi", "shared/": "t", "tmp/": "bare_copy", "notes.txt": "xsame", "cache/": "copy", "srv/": "bare_d"}
        objs = [(p_, d_, st_, f_, rs_ + [more[p_]]) for p_, d_, st_, f_, rs_ in six] + [
            ("deploy/", True, [0o2775, 0o2750, 0o6775], "oct5", ["bare_copy", "copy", "special_d", "who_o", "Xd", "multi", "oct4d", "t"]),
            ("run.sh", False, [0o755, 0o700, 0o750], "fileoct", ["xsame", "bare_copy", "special", "copy", "who_f", "bare_f", "multi", "bare_f"])]
    blocks, exp, files = [], [], []
    for path, is_dir, starts, first, rest in objs:
        start = r.choice(starts)
        rest = list(rest)
        r.shuffle(rest)
        steps = []
        for kd in [first] + rest:
            steps.append(r.choice([o for o in _CHMOD9_OPS[kd] if o not in steps]))
        mode = start
        for st in steps:
            mode = gnu_chmod(mode, st, is_dir, um)
        what = f"directory `{path}`" if is_dir else f"file `{path}`"
        blocks.append(f"The {what} has mode {start:04o}:\n```\n" + "\n".join(f"chmod {st} {path}" for st in steps) + "\n```")
        exp.append(mode)
        files.append({"path": path, "start": f"{start:04o}", "steps": steps, "is_dir": is_dir, "expected": f"{mode:04o}"})
    prompt = (f"On Linux, as the owner (who is also in each one's group), with GNU coreutils 9 chmod, the shell's umask is "
              f"{um:04o}. These commands run in order, each on its own path:\n\n" + "\n\n".join(blocks)
              + "\n\nWhat is each one's mode afterwards, as four octal digits (e.g. 2755)? Ignore chmod's warnings.\n"
              + _ask([f"`{f['path']}`" for f in files]) + _instr(len(files)))
    return Item(f"{BLOCK}.chmod_seq.L{level}.{seed}", BLOCK, "chmod_seq", [{"role": "user", "content": prompt}],
                multi_check([_octal_check7(m) for m in exp]), max_tokens=32000,
                meta={"expected": [f"{m:04o}" for m in exp], "files": files, "umask": f"{um:04o}", "level": level})


# -- logs: an incident across the night the EU leaves summer time --------------------------------------------------------

EU_SWITCH = datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)   # the last Sunday of October: 01:00 UTC
# UTC offsets in minutes before / after EU_SWITCH (the US keeps summer time until 1 November); checked with zoneinfo
TZ_OCT_EU = {"Europe/London": (60, 0), "Europe/Madrid": (120, 60), "Europe/Berlin": (120, 60), "Europe/Helsinki": (180, 120)}
TZ_OCT_US = {"America/New_York": -240, "America/Chicago": -300, "America/Los_Angeles": -420}


def log_910(seed: int, level: int) -> Item:
    """log_78's clocks on 25 October 2026 around 01:00 UTC. The root cause is an OOM kill in dmesg on box-e (seconds since
    boot) just before the EU leaves summer time; its first consequence logs on box-d in a European zone as local time,
    after the clocks went back an hour (only the order in the file and the new offset tell); a US host logs local time
    too - still on summer time; box-c's clock is behind (chronyd says so later); one consequence depends on another.
    Level 10 (Claude Opus answered level 9 right): box-f's clock is ahead - chronyd steps it back - and hosts a sixth
    consequence's upstream; the third consequence is asked too."""
    r = rng(BLOCK, f"log{level}", seed)
    ten = level >= 10
    svcs = ["postgres", "redis", "api", "worker", "nginx", "minio", "keycloak", "grafana", "queue", "search"]
    pick = r.sample(svcs, 10 if ten else 9)
    nd = 6 if ten else 5
    root, deps, herring, unrel, calm = pick[0], pick[1:1 + nd], pick[1 + nd], pick[2 + nd], pick[3 + nd]
    eu = r.choice(sorted(TZ_OCT_EU))
    us = r.choice(sorted(TZ_OCT_US))
    skew = r.randint(45, 200)
    ahead = r.randint(45, 200) if ten else 0
    t0 = EU_SWITCH - timedelta(seconds=r.randint(3, 9))
    boot = t0 - timedelta(seconds=r.randint(20000, 400000))
    frac = r.randint(50, 449) / 1000
    # consequences in true order: box-d (after the switch), box-b (US), box-c (behind), [box-f (ahead)], box-a (it needed
    # the second one), box-d again [(it needed the one on box-f)]
    if not ten:
        hosts = {root: "box-e", deps[0]: "box-d", deps[1]: "box-b", deps[2]: "box-c", deps[3]: "box-a", deps[4]: "box-d"}
        fail, ups = deps, [root, root, root, root, deps[1]]
    else:
        hosts = {root: "box-e", deps[0]: "box-d", deps[1]: "box-b", deps[2]: "box-c", deps[3]: "box-f", deps[4]: "box-a", deps[5]: "box-d"}
        fail, ups = deps, [root, root, root, root, deps[1], deps[3]]
    hosts.setdefault(herring, "box-a")
    hosts.setdefault(unrel, "box-c")
    hosts.setdefault(calm, "box-d")   # box-d has a healthy service too: it logs on both sides of the switch
    for s in svcs:
        hosts.setdefault(s, r.choice(["box-a", "box-b", "box-c", "box-d"]))
    ip = {h: f"10.0.{i + 1}.{r.randint(10, 60)}" for i, h in enumerate(["box-a", "box-b", "box-c", "box-d", "box-e", "box-f"])}
    addr = {s: f"{ip[hosts[s]]}:{CONVENTIONAL[s]}" for s in svcs}
    pid = {s: r.randint(200, 9000) for s in svcs + ["chronyd"]}
    times, t = [], max(t0 + timedelta(seconds=1), EU_SWITCH) + timedelta(seconds=r.randint(1, 3))
    for _ in fail:
        times.append(t)
        t += timedelta(seconds=r.randint(2, 6))
    rss = r.randint(3_000_000, 9_000_000)
    ev = [(t0, "box-e", root, "kernel", f"Out of memory: Killed process {pid[root]} ({root}) total-vm:{rss + r.randint(10**5, 10**6)}kB, "
                                         f"anon-rss:{rss}kB, file-rss:0kB, shmem-rss:0kB, UID:{r.randint(100, 999)} pgtables:{r.randint(8000, 20000)}kB oom_score_adj:0")]
    phr = ["dial tcp {a}: connect: connection refused", "upstream {a} unreachable: no route to host",
           "request to {a} failed: connection reset by peer", "health check of {a} failed: timeout after 2s"]
    for d, tt, up in zip(fail, times, ups):
        ev.append((tt, hosts[d], d, "error", r.choice(phr).format(a=addr[up]) + ", giving up"))
    rh_t = t0 - timedelta(seconds=r.randint(60, 150))
    ev.append((rh_t, hosts[herring], herring, "error", f"connection to {addr[r.choice([s for s in svcs if s not in (root, herring)])]} timed out, retrying in 5s"))
    ev.append((rh_t + timedelta(seconds=6), hosts[herring], herring, "info", "connected, resuming"))
    ev.append((times[-1] + timedelta(seconds=r.randint(4, 9)), hosts[unrel], unrel, "error",
               f"failed to rotate /var/log/{unrel}/{unrel}.log: Disk quota exceeded, exiting"))
    noise = [("info", "health check ok"), ("info", "config reloaded"), ("warn", "slow request 812ms"), ("info", "rotating logs"),
             ("warn", "high memory usage 87%"), ("info", "checkpoint complete")]
    alive = [s for s in svcs if s not in (root, *fail, unrel)]
    for _ in range(22 if ten else 20):
        s = r.choice(alive)
        lv, msg = r.choice(noise)
        ev.append((t0 + timedelta(seconds=r.randint(-300, 40)), hosts[s], s, lv, msg))
    for dt_ in (-r.randint(60, 200), r.randint(12, 60)):   # box-d logs on both sides of the switch
        ev.append((EU_SWITCH + timedelta(seconds=dt_), "box-d", calm, "info", "health check ok"))
    ev.append((times[-1] + timedelta(seconds=r.randint(200, 400)), "box-c", "chronyd", "warn",
               f"System clock is {skew} seconds behind NTP time, stepping the clock forward"))
    if ten:
        ev.append((times[-1] + timedelta(seconds=r.randint(200, 400)), "box-f", "chronyd", "warn",
                   f"System clock is {ahead} seconds ahead of NTP time, stepping the clock back"))
    for _ in range(3):
        ev.append((t0 + timedelta(seconds=r.randint(-300, -2)), "box-e", "kernel", "kernel",
                   r.choice(["eth0: Link is Up - 10Gbps/Full", "audit: type=1400 apparmor=\"STATUS\"",
                             "EXT4-fs (nvme0n1p2): mounted filesystem", "systemd-journald[412]: Time spent on flushing"])))
    ev.sort(key=lambda e: e[0])
    tz_b = TZ_OCT_US[us]
    logs = {}
    for tt, host, s, lv, msg in ev:
        if host == "box-e":
            if lv == "kernel":
                mono = (tt - boot).total_seconds() + (frac if s == root else r.randint(0, 999999) / 1e6)
                logs.setdefault(host, []).append(f"[{mono:12.6f}] {msg}")
            continue
        if host in ("box-d", "box-b"):   # traditional syslog: local time, no year, no offset
            off = (TZ_OCT_EU[eu][0] if tt < EU_SWITCH else TZ_OCT_EU[eu][1]) if host == "box-d" else tz_b
            lt = tt + timedelta(minutes=off)
            logs.setdefault(host, []).append(f"{lt:%b} {lt.day:2d} {lt:%H:%M:%S} {host} {s}[{pid[s]}]: {lv.upper()} {msg}")
            continue
        shown = tt - timedelta(seconds=skew) if host == "box-c" else tt + timedelta(seconds=ahead) if host == "box-f" else tt
        logs.setdefault(host, []).append(f"{shown.strftime('%Y-%m-%dT%H:%M:%S%z')} {host} {s}[{pid[s]}]: {lv.upper()} {msg}")
    heads = {"box-a": "box-a (journalctl -o short-iso)", "box-b": f"box-b (/var/log/syslog, local time; /etc/timezone is {us})",
             "box-c": "box-c (journalctl -o short-iso)", "box-d": f"box-d (/var/log/syslog, local time; /etc/timezone is {eu})",
             "box-e": f"box-e (dmesg; the kernel booted at {boot.strftime('%Y-%m-%dT%H:%M:%SZ')})",
             "box-f": "box-f (journalctl -o short-iso)"}
    blocks = "\n\n".join(f"{heads[h]}:\n```\n" + "\n".join(logs[h]) + "\n```" for h in sorted(logs))
    inv = "\n".join(f"- {s}: {addr[s]}" for s in sorted(svcs, key=lambda x: addr[x]))
    root_t = t0 + timedelta(seconds=frac)
    elapsed = round((times[-1] - root_t).total_seconds())
    qs = ["Which service is the root cause - the one that failed first and made the others fail?",
          "Which service failed next, as the first consequence of the root cause?",
          "Which service was the second consequence to fail?"] + (["Which service was the third consequence to fail?"] if ten else [])
    exp = [root] + fail[:3 if ten else 2]
    checks = [_word_check(e) for e in exp]
    qs += ["Which service was the last to fail as a consequence of the root cause?",
           "How many seconds passed between the root cause failing and that last consequence (to the nearest second)?",
           "At what UTC time did the root cause fail? Answer as HH:MM:SS."]
    exp += [fail[-1], str(elapsed), t0.strftime("%H:%M:%S")]
    checks += [_word_check(fail[-1]), _num_check7(elapsed, 1), _word_check(exp[-1])]
    prompt = (f"My stack stopped working in the night of 24 to 25 October 2026. Each machine logs with its own clock and "
              f"format. Services and the addresses they listen on:\n{inv}\n\nThe logs:\n\n{blocks}\n\nAnswer the service "
              "questions with the service name only.\n" + _ask(qs) + _instr(len(qs)))
    return Item(f"{BLOCK}.log_root.L{level}.{seed}", BLOCK, "log_root", [{"role": "user", "content": prompt}],
                multi_check(checks), max_tokens=32000,
                meta={"expected": exp, "level": level, "tz": [eu, us], "skew": skew, "ahead": ahead,
                      "hosts": {s: hosts[s] for s in [root, *fail]}})


KINDS = {"compose_port": compose_port, "nginx_route": nginx_route, "subnet": subnet, "chmod_seq": chmod_seq, "log_root": log_root}
KINDS["git_seq"] = git_seq   # techhelp_gitseq.py (v0.11): long exact git state, checked against real git
QUICK = ["compose_port", "nginx_route", "subnet", "chmod_seq", "log_root"]
MAX_LEVEL = 10
