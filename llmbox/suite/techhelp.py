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
    network_mode: host. Level 6: compose_expert."""
    if level >= 6:
        return compose_expert(seed, level)
    r = rng(BLOCK, f"compose{level}", seed)
    targets = r.sample(["web", "api", "grafana", "auth", "search", "app"], 3)
    names = targets + r.sample([n for n in CONVENTIONAL if n not in targets], max(1, level - 1))
    tports = dict(zip(targets, r.sample([80, 3000, 8000, 8080, 5000, 9090], 3)))
    allowed = ["plain", "ip", "env", "range", "host", "expose", "bare"][: min(7, 2 + level)]
    modes, bases = {}, [(8000, 1), (9100, 2)]
    for t in targets:   # different modes where the level has enough of them; one network_mode: host at most
        pool = [m for m in allowed if m not in modes.values() and not (m == "range" and not bases)] or \
               [m for m in allowed if m not in ("host", "range")]
        modes[t] = r.choice(pool)
        if modes[t] == "range":
            bases.pop(0)
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
                if level >= 3 and r.random() < 0.7:
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
                multi_check([_set_check(published[t]) for t in targets]), max_tokens=16000,
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
                multi_check([_word_check(e) for e in expected]), max_tokens=16000,
                meta={"expected": expected, "uris": picked, "level": level})


def _naive(locs, uri):
    """What 'the longest prefix wins, full stop' would answer: the common misreading."""
    prefixes = [(pat, tgt) for mod, pat, tgt in locs if mod in ("", "^~", "=") and uri.startswith(pat)]
    return max(prefixes, key=lambda x: len(x[0]))[1] if prefixes else "404"


# ---- networking: hosts, broadcast, routing, summarisation ------------------------------------------------------------

def subnet(seed: int, level: int = 3) -> Item:
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
                max_tokens=16000, meta={"expected": exp, "level": level})


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
    if level >= 6:
        return chmod_expert(seed, level)
    r = rng(BLOCK, f"chmod{level}", seed)
    pool = ["u+x", "g-w", "o-r", "g+w", "o=r", "a+r", "u-w", "go-rwx", "g=rx", "o+x", "ug+rw", "a-x"]
    if level >= 3:
        pool += ["740", "u=rwx,g=rx,o=", "a+X", "go+X"]
    if level >= 5:
        pool += ["u+s", "g+s", "g-s", "u=rwxs", "u-s"]   # setuid/setgid on files; no sticky: it means nothing on files
    paths, blocks, exp, files = [], [], [], []
    for path, is_dir in [("run.sh", False), ("data/", level == 4), ("notes.txt", False)]:   # three files, one question each
        start = r.choice([0o644, 0o600, 0o640, 0o755, 0o664, 0o700, 0o750]) if not is_dir else r.choice([0o755, 0o750, 0o700, 0o775])
        steps = [r.choice(pool) for _ in range(1 + level)]
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
                multi_check([_octal_check(m) for m in exp]), max_tokens=16000,
                meta={"expected": [f"{m:04o}" for m in exp], "files": files, "level": level})


# ---- logs: which service broke first ---------------------------------------------------------------------------------

def log_root(seed: int, level: int = 3) -> Item:
    """A cascade of failures in journal logs; the root cause is the earliest real failure, not the loudest line. Level
    adds noise, a recovered error before the root cause (a retry that succeeded), and a second machine whose logs are
    in another time zone (the order must be read in UTC)."""
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
                multi_check([_word_check(e) for e in exp]), max_tokens=16000, meta={"expected": exp, "cause": cause, "level": level})


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
    fixed = r.choice([9443, 7443, 10443]) if r.random() < 0.6 else None
    in_env = r.random() < 0.75
    profile = r.random() < 0.5
    mode = r.choice(["merge", "override", "reset", "merge"])
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
    variants = [(False, None), (True, None), (False, "flag"), (False, "env"), (True, "flag")]
    chosen = r.sample(variants, 3)
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
                multi_check([_set_check(w) for w in want]), max_tokens=16000,
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
                multi_check([_word_check(e) for e in expected]), max_tokens=16000,
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
    tables = {"main": [("0.0.0.0/0", "wan0", 100), (str(lan), "lan0", 0), (str(far_sup), "eth1", 50)],
              "100": [(str(far), "wg0", 10)] + ([("0.0.0.0/0", "wg0", 10)] if r.random() < 0.5 else []),
              "200": [(str(far_sup), "tun0", 20), (str(far), "tun1", 5)]}
    if r.random() < 0.5:
        tables["main"].append((str(far), "eth2", 30))
    rules = [(0, None, None, "local"), (r.choice([90, 110]), str(vpn), None, "100"), (100, None, str(far_sup), "200"),
             (32766, None, None, "main"), (32767, None, None, "default")]
    packets = []
    while len(packets) < 4:   # four packets: from the VPN or the LAN, to the far /24, the rest of its /16, or elsewhere
        src = ipaddress.ip_address(int(r.choice([vpn, lan]).network_address) + r.randint(2, 200))
        kind = r.choice(["far", "sup", "far", "out"])
        if kind == "out":
            dst = ipaddress.ip_address(f"{r.choice([8, 1, 9, 185])}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}")
        else:
            dst = ipaddress.ip_address(int(far.network_address) + r.randint(2, 200) * (1 if kind == "far" else 256))
            if dst not in far_sup:
                dst = ipaddress.ip_address(int(far.network_address) + 7)
        if (src, dst) not in packets:
            packets.append((src, dst))
    expected = [route_lookup([x for x in rules if x[3] != "local"], tables, s_, d_) or "none" for s_, d_ in packets]
    rl = "\n".join(f"{p}:\tfrom {frm or 'all'}{' to ' + to if to else ''} lookup {t}" for p, frm, to, t in sorted(rules))
    tl = "\n\n".join(f"$ ip route show table {t}\n" + "\n".join(f"{n} dev {d} metric {m}" if n != "0.0.0.0/0" else f"default dev {d} metric {m}"
                                                                  for n, d, m in routes) for t, routes in tables.items())
    prompt = (f"A Linux router has these policy routing rules and tables:\n\n```\n$ ip rule show\n{rl}\n\n{tl}\n```\n\n"
              "Through which interface does the router send each of these packets?\n"
              + _ask([f"A packet from {s_} for {d_}" for s_, d_ in packets]) + _instr(4))
    return Item(f"{BLOCK}.subnet.L{level}.{seed}", BLOCK, "subnet", [{"role": "user", "content": prompt}],
                multi_check([_word_check(e) for e in expected]), max_tokens=16000,
                meta={"expected": expected, "packets": [f"{a} -> {b}" for a, b in packets], "level": level})


def chmod_expert(seed: int, level: int = 6) -> Item:
    """Symbolic modes without who letters, where the umask decides which bits change (`chmod +w` with umask 022 adds
    write for the owner only; `chmod =r` with umask 027 gives 0440), mixed with explicit ones."""
    r = rng(BLOCK, f"chmod{level}", seed)
    um = r.choice([0o022, 0o027, 0o077, 0o002])
    pool = ["+x", "+w", "-w", "=r", "=rw", "+rX", "-rwx", "u+x", "g=u", "o-r", "a+r", "+s", "g+s", "=rwx", "go-w"]
    blocks, exp, names, files = [], [], ["run.sh", "deploy.sh", "notes.txt"], []
    for path in names:   # three files under the same umask, one question each
        start = r.choice([0o644, 0o600, 0o664, 0o755, 0o640, 0o666])
        steps = [r.choice(pool) for _ in range(4)]
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
                multi_check([_octal_check(m) for m in exp]), max_tokens=16000,
                meta={"expected": [f"{m:04o}" for m in exp], "files": files, "umask": f"{um:04o}", "level": level})


KINDS = {"compose_port": compose_port, "nginx_route": nginx_route, "subnet": subnet, "chmod_seq": chmod_seq, "log_root": log_root}
QUICK = list(KINDS)
MAX_LEVEL = 6
