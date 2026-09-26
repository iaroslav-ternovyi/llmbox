"""Long documents / RAG-style reading at 5 difficulty levels.

Level scales document length (~29k -> ~200k real tokens; the chars/4 estimate under-counts by ~1.2x), hop count, the number of conditions and adds later corrections
("incident reopened" entries that override earlier facts).
"""
from __future__ import annotations

import datetime as dt
import re

from .common import Item, final_answer, num, rng

BLOCK = "longctx"
SERVICES = ["billing", "search", "checkout", "auth", "notifications", "inventory", "payments", "reporting"]
CAUSES = ["expired TLS certificate", "database connection pool exhaustion", "bad feature-flag rollout", "DNS misconfiguration",
          "memory leak in the worker", "third-party API rate limit", "disk full on log volume", "clock skew between nodes"]
OFFICES = ["Lisbon", "Warsaw", "Montreal", "Nairobi", "Seoul", "Tallinn", "Bogotá", "Cork"]
FIRST = ["Ana", "Bruno", "Chiara", "Daniel", "Eva", "Fatima", "Gustavo", "Hana", "Igor", "Julia", "Kenji", "Lena",
         "Mateo", "Nadia", "Oscar", "Paula", "Rafael", "Sofia", "Tariq", "Uma", "Viktor", "Wen", "Ximena", "Yara"]
LAST = ["Alvarez", "Bauer", "Costa", "Dimitrov", "Eriksen", "Farah", "Gomez", "Hoffmann", "Ito", "Jovanovic", "Klein",
        "Lopes", "Moreau", "Nowak", "Okafor", "Petrov"]
INSTR = "\n\nAnswer from the document only. Finish with a final line exactly in the form:\nANSWER: <answer>"
# level 5 stays ~200k real tokens so a 262k-context model keeps ~60k for reasoning (190k here measured 231k with a Qwen tokenizer)
TOKENS = {1: 24_000, 2: 48_000, 3: 80_000, 4: 130_000, 5: 165_000}


def _world(r, n_tokens: int, corrections: bool):
    names = r.sample([f"{f} {l}" for f in FIRST for l in LAST], 70)
    directors = names[:3]
    managers = names[3:11]
    mgr_info = {m: {"director": r.choice(directors), "office": r.choice(OFFICES)} for m in managers}
    staff = {n: {"manager": r.choice(managers), "team": r.choice(SERVICES)} for n in names[11:]}
    incidents = []
    start = dt.datetime(2026, 1, 3, 8, 0)
    n_inc = max(20, n_tokens // 100)
    ids = r.sample(range(1000, 9999), n_inc)
    for i in range(n_inc):
        opened = start + dt.timedelta(hours=r.randint(0, 24 * 300))
        dur = dt.timedelta(minutes=r.randint(12, 60 * 30))
        incidents.append({"id": f"INC-{ids[i]}", "service": r.choice(SERVICES), "sev": r.choice(["SEV1", "SEV2", "SEV2", "SEV3", "SEV3", "SEV3"]),
                          "cause": r.choice(CAUSES), "engineer": r.choice(list(staff)), "opened": opened, "resolved": opened + dur,
                          "users": r.randint(10, 90_000), "code": f"RC-{r.randint(10, 99)}{r.choice('ABCDEFGH')}", "updates": []})
    for inc in incidents:
        inc["sev0"], inc["code0"] = inc["sev"], inc["code"]
    if corrections:  # later post-mortems change severity / root-cause code; from level 4 some incidents get a chain of them
        for inc in r.sample(incidents, max(3, n_inc // 10)):
            when = inc["resolved"]
            for _ in range(1 if corrections == 1 else r.randint(1, 3)):
                when = when + dt.timedelta(days=r.randint(2, 20))
                if r.random() < 0.5:
                    new = r.choice([s for s in ["SEV1", "SEV2", "SEV3"] if s != inc["sev"]])
                    inc["updates"].append((when, f"Post-mortem update for {inc['id']}: severity reclassified from {inc['sev']} to {new}."))
                    inc["sev"] = new
                else:
                    new = f"RC-{r.randint(10, 99)}{r.choice('ABCDEFGH')}"
                    inc["updates"].append((when, f"Post-mortem update for {inc['id']}: the root-cause code is corrected from {inc['code']} to {new}."))
                    inc["code"] = new
    return staff, mgr_info, incidents


def _render(staff, mgr_info, incidents) -> str:
    parts = ["# Platform operations handbook - incident log and org directory\n", "## Management\n"]
    for m, v in mgr_info.items():
        parts.append(f"- {m} is an engineering manager based in the {v['office']} office and reports to director {v['director']}.")
    parts.append("\n## Engineers\n")
    for n, s in staff.items():
        parts.append(f"- {n} works on the {s['team']} team and reports to {s['manager']}.")
    parts.append("\n## Incident log\n")
    events = []
    for inc in incidents:
        o, rs = inc["opened"], inc["resolved"]
        first_sev, first_code = inc["sev0"], inc["code0"]
        events.append((o, f"### {inc['id']} ({first_sev}, {inc['service']})\nOpened {o:%Y-%m-%d %H:%M} UTC. About {inc['users']:,} users "
                          f"were affected. The on-call engineer {inc['engineer']} traced it to a {inc['cause']} and applied a fix; the "
                          f"incident was resolved {rs:%Y-%m-%d %H:%M} UTC. Root-cause code: {first_code}. Follow-up actions were "
                          f"filed in the {inc['service']} backlog.\n"))
        for when, text in inc["updates"]:
            events.append((when, f"### Update {when:%Y-%m-%d}\n{text}\n"))
    parts += [t for _, t in sorted(events, key=lambda e: e[0])]
    return "\n".join(parts)


def _item(kind: str, seed: int, level: int, build) -> Item:
    # v0.8: ONE document per (level, seed), shared by every question kind - how documents are really used (several
    # questions about the same file) and the server's prompt cache prefills it once instead of once per question.
    wr = rng(BLOCK, f"doc{level}", seed)
    staff, mgr_info, incidents = _world(wr, TOKENS[level], corrections=0 if level < 3 else 1 if level == 3 else 2)
    doc = _render(staff, mgr_info, incidents)
    r = rng(BLOCK, f"{kind}{level}", seed)
    question, check, expected = build(r, staff, mgr_info, incidents, level)
    note = "\nLater updates in the log override earlier facts." if level >= 3 else ""
    return Item(f"{BLOCK}.{kind}.L{level}.{seed}", BLOCK, kind,
                [{"role": "user", "content": doc + "\n\n---\n" + question + note + INSTR}], check, max_tokens=32000,
                meta={"expected": expected, "doc_tokens_est": len(doc) // 4, "level": level})


_FILLER = {"the", "office", "based", "in", "director", "incident", "code", "root-cause", "root", "cause", "is", "mr", "ms"}


def _norm_answer(s: str) -> list[str]:
    return [w for w in re.findall(r"[\w-]+", (s or "").lower()) if w not in _FILLER]


def _eq(expected: str):
    """Exact answer, tolerant to filler words: "Cork office" == "Cork", "the director Ana Ruiz" == "Ana Ruiz"."""
    return lambda t, _t=None: 1.0 if _norm_answer(final_answer(t)) == _norm_answer(str(expected)) else 0.0


def lookup(seed: int, level: int = 3) -> Item:
    def build(r, staff, mgr, incidents, level):
        pool = [i for i in incidents if i["updates"] and "corrected" in i["updates"][-1][1]] if level >= 3 else incidents
        inc = r.choice(pool or incidents)
        return f"What is the (final) root-cause code of {inc['id']}?", _eq(inc["code"]), inc["code"]
    return _item("lookup", seed, level, build)


def multihop(seed: int, level: int = 3) -> Item:
    def build(r, staff, mgr, incidents, level):
        inc = r.choice(incidents)
        m = staff[inc["engineer"]]["manager"]
        if level <= 2:
            return f"Who is the manager of the engineer who handled {inc['id']}? Answer with the full name.", _eq(m), m
        if level == 3:
            office = mgr[m]["office"]
            return f"In which office is the manager of the engineer who handled {inc['id']} based?", _eq(office), office
        d = mgr[m]["director"]
        return (f"Which director is at the top of the reporting line of the engineer who handled {inc['id']}? Answer with the full name.",
                _eq(d), d)
    return _item("multihop", seed, level, build)


def count(seed: int, level: int = 3) -> Item:
    def build(r, staff, mgr, incidents, level):
        for _ in range(50):   # re-draw degenerate questions (v0.5 asked about an office no manager was in -> answer 0)
            svc = r.choice(SERVICES)
            sev = r.choice(["SEV1", "SEV2"])
            months = sorted(r.sample(range(1, 11), 2))
            cond = lambda i, svc=svc, sev=sev, months=months: i["service"] == svc and i["sev"] == sev and months[0] <= i["opened"].month <= months[1]
            extra = ""
            if level >= 4:
                office = r.choice(sorted({v["office"] for v in mgr.values()}))
                cond0 = cond
                cond = lambda i, cond0=cond0, office=office: cond0(i) and mgr[staff[i["engineer"]]["manager"]]["office"] == office
                extra = f" and were handled by an engineer whose manager is based in {office}"
            n = sum(1 for i in incidents if cond(i))
            if n >= 3:
                break
        q = (f"How many incidents on the {svc} service have (final) severity {sev}, were OPENED between month {months[0]} and "
             f"month {months[1]} of 2026 (inclusive){extra}? Answer with a number.")
        return q, (lambda t, _t=None, n=n: 1.0 if num(final_answer(t)) == n else 0.0), n
    return _item("count", seed, level, build)


def latest(seed: int, level: int = 3) -> Item:
    def build(r, staff, mgr, incidents, level):
        cause = r.choice(CAUSES)
        sel = [i for i in incidents if i["cause"] == cause]
        if level >= 4:
            svc = r.choice(SERVICES)
            sel2 = [i for i in sel if i["service"] == svc]
            if sel2:
                sel, cause = sel2, f"{cause} on the {svc} service"
        key = (lambda i: i["resolved"] - i["opened"]) if level >= 5 else (lambda i: i["resolved"])
        best = max(sel, key=key)
        what = "took the longest from opening to resolution" if level >= 5 else "was RESOLVED last"
        return (f"Among all incidents caused by a {cause}, which one {what}? Answer with the incident id.", _eq(best["id"]), best["id"])
    return _item("latest", seed, level, build)


def total(seed: int, level: int = 3) -> Item:
    """Sum of affected users over many matching incidents - needs every match, not just finding one."""
    def build(r, staff, mgr, incidents, level):
        for _ in range(50):
            svc = r.choice(SERVICES)
            sevs = r.sample(["SEV1", "SEV2", "SEV3"], 2 if level >= 4 else 1)
            months = sorted(r.sample(range(1, 11), 2))
            sel = [i for i in incidents if i["service"] == svc and i["sev"] in sevs and months[0] <= i["opened"].month <= months[1]]
            if len(sel) >= 3:
                break
        n = sum(i["users"] for i in sel)
        q = (f"What is the total number of affected users over all incidents on the {svc} service with (final) severity "
             f"{' or '.join(sevs)} that were OPENED between month {months[0]} and month {months[1]} of 2026 (inclusive)? "
             f"Answer with a plain integer.")
        return q, (lambda t, _t=None, n=n: 1.0 if num(final_answer(t)) == n else 0.0), n
    return _item("total", seed, level, build)


def audit(seed: int, level: int = 6) -> Item:
    """Expert: fact-check a draft monthly report (40 lines) against the incident log - the log is the source of truth and
    later updates override earlier facts. Errors are subtle: a pre-correction severity or root-cause code, two swapped
    digits in the user count, a duration off by an hour, a wrong service. Strict: the exact set of wrong lines."""
    r = rng(BLOCK, f"audit{level}", seed)
    staff, mgr_info, incidents = _world(r, TOKENS[4], corrections=2)
    doc = _render(staff, mgr_info, incidents)
    corrected = [i for i in incidents if i["updates"]]
    rest = [i for i in incidents if not i["updates"]]
    chosen = r.sample(corrected, min(14, len(corrected))) + r.sample(rest, 40 - min(14, len(corrected)))
    chosen.sort(key=lambda i: i["opened"])

    def fields(i):
        d = i["resolved"] - i["opened"]
        return {"service": i["service"], "sev": i["sev"], "users": i["users"], "dur": int(d.total_seconds() // 60), "code": i["code"]}

    def line(i, f):
        return (f"- {i['id']} ({f['service']}, {f['sev']}): about {f['users']:,} users affected, resolved after "
                f"{f['dur'] // 60}h {f['dur'] % 60:02d}m, root-cause code {f['code']}.")
    wrong = set()
    kinds = ["stale_sev", "stale_code", "digits", "duration", "service", "stale_sev", "stale_code", "digits"]
    targets = r.sample(chosen, len(kinds))
    lines = []
    plan = dict(zip((t["id"] for t in targets), kinds))
    for i in chosen:
        f = fields(i)
        k = plan.get(i["id"])
        if k == "stale_sev" and i["sev0"] != i["sev"]:
            f["sev"] = i["sev0"]
        elif k == "stale_code" and i["code0"] != i["code"]:
            f["code"] = i["code0"]
        elif k in ("stale_sev", "stale_code", "digits"):
            s_ = str(f["users"])
            if len(s_) >= 2 and len(set(s_)) > 1:
                p = r.choice([j for j in range(len(s_) - 1) if s_[j] != s_[j + 1]] or [0])
                s_ = s_[:p] + s_[p + 1] + s_[p] + s_[p + 2:]
                f["users"] = int(s_)
        elif k == "duration":
            f["dur"] = max(1, f["dur"] + r.choice([-60, 60]))
        elif k == "service":
            f["service"] = r.choice([x for x in SERVICES if x != i["service"]])
        if f != fields(i):
            wrong.add(i["id"])
        lines.append(line(i, f))
    exp = [i["id"] for i in chosen if i["id"] in wrong]
    report = "## Draft monthly reliability report (to be checked)\n\n" + "\n".join(lines)
    question = ("The draft reliability report below was written from the incident log above. Check EVERY line of the report "
                "against the log (the log is the source of truth, and later updates in the log override earlier facts). "
                "List the ids of all incidents whose report line contains at least one error, in report order.")
    fin = "\n\nAnswer from the documents only. Finish with a final line exactly in the form:\nANSWER: <comma-separated incident ids, or none>"

    def check(t, _t=None, exp=tuple(exp)) -> float:
        got = re.findall(r"INC-\d+", final_answer(t) or "")
        return 1.0 if set(got) == set(exp) and len(got) == len(set(got)) else 0.0
    return Item(f"{BLOCK}.audit.L{level}.{seed}", BLOCK, "audit",
                [{"role": "user", "content": doc + "\n\n---\n" + report + "\n\n---\n" + question + fin}], check, max_tokens=32000,
                meta={"expected": ", ".join(exp), "doc_tokens_est": (len(doc) + len(report)) // 4, "level": level, "wrong": len(exp)})


KINDS = {"lookup": lookup, "multihop": multihop, "count": count, "latest": latest, "total": total, "audit": audit}
