"""Long documents / RAG-style reading at 10 difficulty levels.

Levels 1-5 scale document length (~29k -> ~200k real tokens; the chars/4 estimate under-counts by ~1.2x), hop count, the number of conditions and adds later corrections
("incident reopened" entries that override earlier facts).
v0.11: levels 6-8 (audit: 7-8) keep the document at the level-3 size and make the reading harder: dated org changes, withdrawn
duplicates, revised user counts, facts the document does not contain (NOT STATED) - see "levels 6-8" below.
Levels 9-10 (aimed at the frontier) add corrections of corrections, vendor-conditional updates and retroactive org
corrections, and ask for sets, headcounts, per-director breakdowns, rankings and long audits - see "levels 9-10" below.
"""
from __future__ import annotations

import datetime as dt
import itertools
import re

from .common import Item, final_answer, multi_check, num, rng

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
# v0.10: several questions of one kind per item, credit per question (one 0/1 question per item made this block the
# noisiest per minute; the document is prefilled once either way)
QUESTIONS = 5   # dev5: 3 -> 5 (the document is cached; each question is a few seconds of reading)
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
    qs = []
    for j in range(40):   # distinct questions; the first is the one the single-question item (v0.9) asked
        q = build(rng(BLOCK, f"{kind}{level}" + (f"q{j}" if j else ""), seed), staff, mgr_info, incidents, level)
        if all(q[0] != x[0] for x in qs):
            qs.append(q)
        if len(qs) == QUESTIONS:
            break
    note = "\nLater updates in the log override earlier facts." if level >= 3 else ""
    ask = "Answer each of these questions:\n" + "\n".join(f"{i + 1}. {q[0]}" for i, q in enumerate(qs))
    fin = ("\n\nAnswer from the document only. Finish with one final line per question, exactly in the form:\n"
           + "\n".join(f"ANSWER {i + 1}: <answer>" for i in range(len(qs))))
    return Item(f"{BLOCK}.{kind}.L{level}.{seed}", BLOCK, kind,
                [{"role": "user", "content": doc + "\n\n---\n" + ask + note + fin}], multi_check([q[1] for q in qs]),
                max_tokens=32000, meta={"expected": [q[2] for q in qs], "doc_tokens_est": len(doc) // 4, "level": level,
                                        "questions": len(qs)})


_FILLER = {"the", "office", "based", "in", "director", "incident", "code", "root-cause", "root", "cause", "is", "mr", "ms"}


def _norm_answer(s: str) -> list[str]:
    return [w for w in re.findall(r"[\w-]+", (s or "").lower()) if w not in _FILLER]


def _eq(expected: str):
    """Exact answer, tolerant to filler words: "Cork office" == "Cork", "the director Ana Ruiz" == "Ana Ruiz"."""
    return lambda t, _t=None: 1.0 if _norm_answer(final_answer(t)) == _norm_answer(str(expected)) else 0.0


def lookup(seed: int, level: int = 3) -> Item:
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _x_item("lookup", seed, level)
    if level >= 6:   # v0.11: levels 6-8 keep the document at the level-3 size and ask harder questions
        return _hard_item("lookup", seed, level)
    def build(r, staff, mgr, incidents, level):
        pool = [i for i in incidents if i["updates"] and "corrected" in i["updates"][-1][1]] if level >= 3 else incidents
        inc = r.choice(pool or incidents)
        return f"What is the (final) root-cause code of {inc['id']}?", _eq(inc["code"]), inc["code"]
    return _item("lookup", seed, level, build)


def multihop(seed: int, level: int = 3) -> Item:
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _x_item("multihop", seed, level)
    if level >= 6:   # v0.11: levels 6-8 keep the document at the level-3 size and ask harder questions
        return _hard_item("multihop", seed, level)
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
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _x_item("count", seed, level)
    if level >= 6:   # v0.11: levels 6-8 keep the document at the level-3 size and ask harder questions
        return _hard_item("count", seed, level)
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
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _x_item("latest", seed, level)
    if level >= 6:   # v0.11: levels 6-8 keep the document at the level-3 size and ask harder questions
        return _hard_item("latest", seed, level)
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
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _x_item("total", seed, level)
    if level >= 6:   # v0.11: levels 6-8 keep the document at the level-3 size and ask harder questions
        return _hard_item("total", seed, level)
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
    digits in the user count, a duration off by an hour, a wrong service. Credit per error found, minus false alarms."""
    if level >= 9:
        return _audit_x(seed, level)
    if level >= 7:
        return _audit_hard(seed, level)
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
        got = set(re.findall(r"INC-\d+", final_answer(t) or ""))
        return max(0.0, (len(got & set(exp)) - len(got - set(exp))) / len(exp))   # per error found; a false alarm cancels one
    return Item(f"{BLOCK}.audit.L{level}.{seed}", BLOCK, "audit",
                [{"role": "user", "content": doc + "\n\n---\n" + report + "\n\n---\n" + question + fin}], check, max_tokens=32000,
                meta={"expected": ", ".join(exp), "doc_tokens_est": (len(doc) + len(report)) // 4, "level": level, "wrong": len(exp)})


# ---- levels 6-8 (v0.11) -----------------------------------------------------------------------------------------------
# Levels 1-5 grow the document; levels 1-5 of lookup / multihop / latest topped out (frontier ~1.0, strong local models
# ~0.9). From level 6 the document stays at the level-3 size (<= ~95k real tokens, one prefill on a 12 GB box) and the
# READING gets harder instead:
#   - org changes that take effect on a date: an engineer moves to another manager (and team), a manager relocates to
#     another office or reports to another director - "who was the manager WHEN the incident was opened";
#   - withdrawn duplicates that no statistic may count, and revised user counts (the incident entry keeps the first figure);
#   - facts the document does not contain (a contractor outside the directory, a manager whose office is not stated yet,
#     an incident id or date that is not in the log): the answer is NOT STATED (from level 7);
#   - aggregations picked so that a reader who misses one kind of later update gets a different answer.
# Every level-6..8 item mixes a fixed set of question types (a seed changes only which incidents / values they hit).
TOKENS_HARD = {6: 72_000, 7: 72_000, 8: 72_000, 9: 66_000, 10: 66_000}
NOT_STATED = "NOT STATED"
_T0 = dt.datetime(2026, 1, 1)
_NS = re.compile(r"\bnot[\s_-]+(stated|specified|mentioned|given|listed|recorded|documented|provided|available|found|in\s+the\s+"
                 r"(document|log))\b|\bunknown\b|\bcannot\s+be\s+determined\b|\bno\s+information\b", re.I)
_HARD_FILLER = _FILLER | {"team", "service", "severity"}


def _at(hist: list, when: dt.datetime):
    """The entry of a dated history [(effective, ...), ...] in effect at `when` (entries are sorted by date)."""
    cur = hist[0]
    for h in hist:
        if h[0] <= when:
            cur = h
    return cur


def _world_hard(r, level: int, n_tokens: int | None = None) -> dict:
    staff, mgr_info, incidents = _world(r, n_tokens or TOKENS_HARD[level], corrections=2)
    managers = list(mgr_info)
    dirs = sorted({v["director"] for v in mgr_info.values()})
    used = set(staff) | set(managers) | set(dirs)
    fresh = [f"{f} {l}" for f in FIRST for l in LAST if f"{f} {l}" not in used]
    r.shuffle(fresh)
    if len(dirs) < 3:   # a director who only appears in an org change
        dirs.append(fresh.pop())
    org = []
    # engineers who move to another manager (and sometimes another team) during the year
    hist = {n: [(_T0, s["team"], s["manager"])] for n, s in staff.items()}
    for n in r.sample(list(staff), len(staff) // 3):
        for d in sorted(r.sample(range(30, 290), 2 if level >= 8 and r.random() < 0.4 else 1)):
            when = _T0 + dt.timedelta(days=d)
            _, team, mgr = hist[n][-1]
            new_mgr = r.choice([m for m in managers if m != mgr])
            new_team = r.choice([t for t in SERVICES if t != team]) if r.random() < 0.4 else team
            hist[n].append((when, new_team, new_mgr))
            org.append((when, f"Effective {when:%Y-%m-%d}, {n} " + (f"moves from the {team} team to the {new_team} team and "
                                                                     if new_team != team else "") + f"now reports to {new_mgr}."))
    # managers: two without a stated office at first, three relocations, two new directors
    office = {m: [(_T0, v["office"])] for m, v in mgr_info.items()}
    for m in r.sample(managers, 2):
        office[m] = [(_T0, None)]
    for m in r.sample(managers, 3):
        when = _T0 + dt.timedelta(days=r.randint(30, 290))
        cur = office[m][-1][1]
        new = r.choice([o for o in OFFICES if o != cur])
        office[m].append((when, new))
        org.append((when, f"Effective {when:%Y-%m-%d}, engineering manager {m} " + (f"relocates from the {cur} office to the {new} office."
                                                                                   if cur else f"is based in the {new} office.")))
    director = {m: [(_T0, v["director"])] for m, v in mgr_info.items()}
    for m in r.sample(managers, 2):
        when = _T0 + dt.timedelta(days=r.randint(30, 290))
        new = r.choice([d for d in dirs if d != director[m][-1][1]])
        director[m].append((when, new))
        org.append((when, f"Effective {when:%Y-%m-%d}, engineering manager {m} now reports to director {new}."))
    # contractors: on-call engineers who are not in the directory (their reporting line is not stated anywhere)
    contractors = fresh[:4]
    for inc in r.sample(incidents, max(6, len(incidents) // 60)):
        inc["engineer"] = r.choice(contractors)
    for inc in incidents:
        inc["users0"], inc["dup_of"] = inc["users"], None
    # withdrawn duplicates of an earlier incident on the same service
    by_time = sorted(incidents, key=lambda i: i["opened"])
    wd = r.sample(by_time[30:], max(8, len(incidents) // 50))
    wd_ids = {i["id"] for i in wd}
    for inc in wd:
        earlier = [j for j in by_time if j["service"] == inc["service"] and j["opened"] < inc["opened"] and j["id"] not in wd_ids]
        if not earlier:   # nothing earlier on this service to duplicate (a seed that used to crash here): not withdrawn
            continue
        tgt = earlier[-r.randint(1, min(6, len(earlier)))]
        inc["dup_of"] = tgt["id"]
        when = inc["resolved"] + dt.timedelta(days=r.randint(1, 6), hours=r.randint(0, 12))
        inc["updates"].append((when, f"{inc['id']} was a duplicate of {tgt['id']} and is withdrawn from the log; it does not count "
                                     f"in any statistics."))
    # revised user counts (the incident entry keeps the first figure)
    for inc in r.sample(incidents, len(incidents) // 12):
        new = max(10, int(inc["users"] * r.choice([0.2, 0.4, 0.6, 1.5, 2, 3])) + r.randint(-99, 99))
        last = max([inc["resolved"]] + [w for w, _ in inc["updates"]])
        when = last + dt.timedelta(days=r.randint(1, 10), hours=r.randint(0, 12))
        inc["updates"].append((when, f"Post-mortem update for {inc['id']}: the number of affected users is revised from "
                                     f"{inc['users']:,} to {new:,}."))
        inc["users"] = new
    for inc in incidents:   # the value of each field over time, read back from the update texts
        h = {"sev": [(inc["opened"], inc["sev0"])], "code": [(inc["opened"], inc["code0"])]}
        for when, text in sorted(inc["updates"]):
            m = re.search(r"reclassified from \S+ to (\S+)\.|the root-cause code is corrected from \S+ to (\S+)\.", text)
            if m:
                h["sev" if m.group(1) else "code"].append((when, m.group(1) or m.group(2)))
        assert h["sev"][-1][1] == inc["sev"] and h["code"][-1][1] == inc["code"]
        inc["hist"] = h
    return {"staff": staff, "mgr": mgr_info, "incidents": incidents, "hist": hist, "office": office, "director": director,
            "org": org, "contractors": set(contractors), "by_id": {i["id"]: i for i in incidents}, "cache": {}}


def _render_hard(W: dict) -> str:
    parts = ["# Platform operations handbook - incident log and org directory\n",
             "## Management (as of 2026-01-01; later org changes are recorded in the incident log)\n"]
    for m in W["mgr"]:
        o, d = W["office"][m][0][1], W["director"][m][0][1]
        parts.append(f"- {m} is an engineering manager " + (f"based in the {o} office " if o else "") + f"and reports to director {d}.")
    parts.append("\n## Engineers (as of 2026-01-01)\n")
    for n, s in W["staff"].items():
        parts.append(f"- {n} works on the {s['team']} team and reports to {s['manager']}.")
    parts.append("\n## Incident log\n")
    events = []
    for inc in W["incidents"]:
        o, rs = inc["opened"], inc["resolved"]
        events.append((o, f"### {inc['id']} ({inc['sev0']}, {inc['service']})\nOpened {o:%Y-%m-%d %H:%M} UTC. About {inc['users0']:,} "
                          f"users were affected. The on-call engineer {inc['engineer']} traced it to a {inc['cause']} and applied a fix; "
                          f"the incident was resolved {rs:%Y-%m-%d %H:%M} UTC. Root-cause code: {inc['code0']}. Follow-up actions "
                          f"were filed in the {inc['service']} backlog.\n"))
        for when, text in inc["updates"]:
            events.append((when, f"### Update {when:%Y-%m-%d}\n{text}\n"))
    for when, text in W["org"]:
        events.append((when, f"### Org change {when:%Y-%m-%d}\n{text}\n"))
    parts += [t for _, t in sorted(events, key=lambda e: e[0])]
    return "\n".join(parts)


# reporting lines at a point in time; None = the document does not say (a contractor, a manager without a stated office)
def _mgr_at(W, e, t):
    return _at(W["hist"][e], t)[2] if e in W["hist"] else None


def _team_at(W, e, t):
    return _at(W["hist"][e], t)[1] if e in W["hist"] else None


def _office_at(W, m, t):
    return _at(W["office"][m], t)[1] if m in W["office"] else None


def _dir_at(W, m, t):
    return _at(W["director"][m], t)[1] if m in W["director"] else None


def _clean(W, inc) -> bool:
    """No org change touching this incident's reporting line takes effect on the day it was opened (no same-day ambiguity)."""
    e = inc["engineer"]
    if e not in W["hist"]:
        return True
    days = W["cache"].get(("days", e))
    if days is None:
        days = {h[0].date() for h in W["hist"][e][1:]}
        for m in {h[2] for h in W["hist"][e]}:
            days |= {h[0].date() for h in W["office"][m][1:] + W["director"][m][1:]}
        W["cache"][("days", e)] = days
    return inc["opened"].date() not in days


def _norm_hard(s: str) -> list[str]:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return [w for w in re.findall(r"[\w-]+", s.lower()) if w not in _HARD_FILLER]


def _chk(expected, how: str = "text"):
    if expected == NOT_STATED:
        return lambda t, _t=None: 1.0 if _NS.search(final_answer(t) or "") else 0.0
    if how == "num":
        return lambda t, _t=None: 1.0 if num(final_answer(t)) == expected else 0.0
    if how == "sev":
        return lambda t, _t=None: 1.0 if re.fullmatch(rf"sev\s*-?\s*{expected[-1]}", (final_answer(t) or "").strip(" .").lower()) else 0.0
    return lambda t, _t=None: 1.0 if _norm_hard(final_answer(t)) == _norm_hard(str(expected)) else 0.0


_FIELD = {"code": ("What is the final root-cause code of {ref}?", "code", "text"),
          "sev": ("What is the final severity of {ref}?", "sev", "sev"),
          "users": ("How many users were affected by {ref}, according to the latest figure in the log? Answer with a number.", "users", "num")}


def _changed(i, field: str) -> bool:
    return i[field] != i[field + "0"]


def _field_q(ref: str, inc, field: str):
    q, key, how = _FIELD[field]
    ans = NOT_STATED if inc is None else inc[key]
    return q.format(ref=ref), _chk(ans, how), ans


def _lookup_hard(r, W, typ, level):
    incs = W["incidents"]
    live = [i for i in incs if not i["dup_of"]]
    if typ in _FIELD:        # by id; the value changed after the incident entry (a chain of corrections / a revision)
        i = r.choice([i for i in live if _changed(i, typ)])
        return _field_q(i["id"], i, typ)
    if typ == "absent_id":   # an id with two digits swapped that no incident has
        i = r.choice(incs)
        d = i["id"][4:]
        k = r.randrange(len(d) - 1)
        new = "INC-" + d[:k] + d[k + 1] + d[k] + d[k + 2:]
        if new in W["by_id"] or new == i["id"]:
            return None
        return _field_q(new, None, r.choice(["code", "sev"]))
    if typ == "asof":        # the value on a past day, between two entries: later corrections must be left out
        i = r.choice([i for i in live if len(i["hist"]["sev"]) + len(i["hist"]["code"]) > 2])
        field = r.choice([f for f in ("sev", "code") if len(i["hist"][f]) > 1])
        h = i["hist"][field]
        k = r.randrange(len(h) - 1)
        lo, hi = h[k][0].date(), h[k + 1][0].date()
        days = (hi - lo).days
        if days < 2:
            return None
        day = lo + dt.timedelta(days=r.randint(1, days - 1))
        what = "severity" if field == "sev" else "root-cause code"
        return (f"According to the log, what was the {what} of {i['id']} at the end of {day:%Y-%m-%d} (leave out any update made "
                f"after that day)?"), _chk(h[k][1], "sev" if field == "sev" else "text"), h[k][1]
    if typ == "dup":         # withdrawn -> the incident it duplicated -> its final value
        w = r.choice([i for i in incs if i["dup_of"]])
        field = r.choice(["code", "sev"])
        q, a, e = _field_q("that other incident", W["by_id"][w["dup_of"]], field)
        return f"{w['id']} was withdrawn as a duplicate of another incident. {q}", a, e
    groups = {}
    for i in incs:
        groups.setdefault((i["service"], i["opened"].date()), []).append(i)
    if typ == "attr_absent":  # a day with incidents on other services, none on this one
        day = r.choice(sorted({d for (_s, d), v in groups.items()}))
        svcs = {s for (s, d) in groups if d == day}
        if len(svcs) < 2:
            return None
        svc = r.choice([s for s in SERVICES if s not in svcs])
        return _field_q(f"the incident on the {svc} service that was opened on {day:%Y-%m-%d}", None, r.choice(["code", "sev"]))
    field = typ.split("_")[1]  # attr_code / attr_sev / attr_users: identified by service and opening day, not by id
    pool = [i for i in live if _changed(i, field) and len(groups[(i["service"], i["opened"].date())]) == 1]
    i = r.choice(pool)
    return _field_q(f"the incident on the {i['service']} service that was opened on {i['opened']:%Y-%m-%d}", i, field)


_MH_Q = {"mgr": "Who was the manager of the engineer who handled {id} at the time {id} was opened? Answer with the full name.",
         "office": "In which office was the manager of the engineer who handled {id} based at the time {id} was opened?",
         "dir": "Which director was at the top of the reporting line of the engineer who handled {id} at the time {id} was opened? "
                "Answer with the full name.",
         "team": "On which team did the engineer who handled {id} work at the time {id} was opened?"}


def _mh_answer(W, inc, what):
    e, t = inc["engineer"], inc["opened"]
    m = _mgr_at(W, e, t)
    if m is None:
        return None
    return {"mgr": m, "office": _office_at(W, m, t), "dir": _dir_at(W, m, t), "team": _team_at(W, e, t)}[what]


def _mh_naive(W, inc, what):
    """What a reader who takes the directory at face value (ignoring org changes) answers."""
    e = inc["engineer"]
    if e not in W["staff"]:
        return None
    m = W["staff"][e]["manager"]
    return {"mgr": m, "office": W["office"][m][0][1], "dir": W["director"][m][0][1], "team": W["staff"][e]["team"]}[what]


def _multihop_hard(r, W, typ, level):
    incs = [i for i in W["incidents"] if _clean(W, i)]
    staffed = [i for i in incs if i["engineer"] in W["staff"]]
    if typ == "absent_contractor":
        i = r.choice([i for i in incs if i["engineer"] in W["contractors"]])
        what = r.choice(["mgr", "office", "dir"])
        return _MH_Q[what].format(id=i["id"]), _chk(NOT_STATED), NOT_STATED
    if typ == "absent_office":   # prefer a manager who gets an office later (the log names one, just not for that date)
        pool = [i for i in staffed if _mh_answer(W, i, "office") is None]
        later = [i for i in pool if W["office"][_mgr_at(W, i["engineer"], i["opened"])][-1][1]]
        i = r.choice(later or pool)
        return _MH_Q["office"].format(id=i["id"]), _chk(NOT_STATED), NOT_STATED
    if typ == "dup_office":
        pool = [w for w in W["incidents"] if w["dup_of"] and W["by_id"][w["dup_of"]]["engineer"] in W["staff"]
                and _clean(W, W["by_id"][w["dup_of"]]) and _mh_answer(W, W["by_id"][w["dup_of"]], "office")]
        w = r.choice(pool)
        a = _mh_answer(W, W["by_id"][w["dup_of"]], "office")
        return (f"{w['id']} was withdrawn as a duplicate of another incident. In which office was the manager of the engineer who "
                f"handled that other incident based at the time that other incident was opened?"), _chk(a), a
    if typ == "mgr_t_same":      # opened BEFORE the engineer moved: the directory is still right
        pool = [i for i in staffed if len(W["hist"][i["engineer"]]) > 1 and i["opened"] < W["hist"][i["engineer"]][1][0]]
        i = r.choice(pool)
        a = _mh_answer(W, i, "mgr")
        return _MH_Q["mgr"].format(id=i["id"]), _chk(a), a
    what = typ.split("_")[0]     # mgr_t / office_t / dir_t / team_t: the answer differs from the directory's
    pool = [i for i in staffed if _mh_answer(W, i, what) and _mh_answer(W, i, what) != _mh_naive(W, i, what)]
    i = r.choice(pool)
    a = _mh_answer(W, i, what)
    return _MH_Q[what].format(id=i["id"]), _chk(a), a


# ---- aggregations: every candidate question is scored by how many kinds of later update change its answer --------------

def _org_ok(W, inc, org, naive: bool) -> bool | None:
    if org is None:
        return True
    e, t = inc["engineer"], inc["opened"]
    if e not in W["staff"]:
        return None
    m = W["staff"][e]["manager"] if naive else _mgr_at(W, e, t)
    v = (W["office"][m][0][1] if naive else _office_at(W, m, t)) if org[0] == "office" else \
        (W["director"][m][0][1] if naive else _dir_at(W, m, t))
    return None if v is None else v == org[1]


def _variants(W, sel, org, key=None):
    """(right answer, [answers of readers who miss one kind of update]) over the incidents `sel` (pre-filtered on service,
    cause and months). None when a candidate's org condition cannot be decided from the document or falls on a change day."""
    out, n_right = {}, 0
    for name in ("right", "sev", "wd", "users", "org"):
        if name == "users" and key not in ("users", "sum"):
            continue
        if name == "org" and org is None:
            continue
        rows = []
        for i in sel["items"]:
            sev = i["sev0"] if name == "sev" else i["sev"]
            if sev not in sel["sevs"] or (i["dup_of"] and name != "wd"):
                continue
            ok = _org_ok(W, i, org, name == "org")
            if ok is None or (org and not _clean(W, i)):
                return None
            if ok:
                rows.append(i)
        if name == "right":
            n_right = len(rows)
        users =(lambda i: i["users0"]) if name == "users" else (lambda i: i["users"])
        if key is None:
            out[name] = len(rows)
        elif key == "sum":
            out[name] = sum(users(i) for i in rows) if rows else None
        else:
            vals = sorted(((users(i) if key == "users" else i["resolved"] - i["opened"]), i["id"]) for i in rows)
            if len(vals) < 3 or vals[-1][0] == vals[-2][0]:
                out[name] = None if name == "right" else (vals[-1][1] if vals else None)
            else:
                out[name] = vals[-1][1]
    if out["right"] is None or n_right < 3:
        return None
    work = sum(1 for i in sel["items"] if i["sev"] in sel["sevs"] or i["sev0"] in sel["sevs"])   # incidents a reader must trace
    return out["right"], sum(v != out["right"] for k, v in out.items() if k != "right"), n_right, work


def _agg_pool(W, kind: str, level: int):
    ck = (kind, level)
    if ck in W["cache"]:
        return W["cache"][ck]
    incs = W["incidents"]
    dirs = sorted({d for h in W["director"].values() for _, d in h})
    pool = []
    if kind in ("count", "total"):
        span, cap = {6: 9, 7: 4, 8: 3}[level], {6: 20, 7: 16, 8: 16}[level]
        sev_sets = [("SEV1",), ("SEV2",)] if (kind, level) in (("count", 6), ("count", 7)) else \
            [("SEV1",), ("SEV2",), ("SEV3",)] if kind == "total" and level != 7 else [("SEV1", "SEV2"), ("SEV1", "SEV3"), ("SEV2", "SEV3")]
        orgs = [("dir", d) for d in dirs] if (kind, level) in (("count", 7), ("count", 8), ("total", 8)) else [None]
        for svc in SERVICES:
            on = [i for i in incs if i["service"] == svc]
            for a in range(1, 11):
                for b in range(a + 1, min(11, a + span + 1)):
                    items = [i for i in on if a <= i["opened"].month <= b]
                    for sevs in sev_sets:
                        for org in orgs:
                            v = _variants(W, {"items": items, "sevs": sevs}, org, None if kind == "count" else "sum")
                            if v and v[2] <= 15 and v[3] <= cap:
                                pool.append((v[1], (svc, sevs, a, b, org), v[0]))
    else:   # latest: the incident with the most (final) users / the longest duration among a cause on one or two services
        key = "dur" if level == 6 else "users"
        sev_sets = [None, ("SEV1",), ("SEV2",), ("SEV3",)] if level == 6 else \
            [("SEV1",), ("SEV2",), ("SEV1", "SEV2"), ("SEV2", "SEV3")] if level == 7 else [("SEV1", "SEV2"), ("SEV1", "SEV3"), ("SEV2", "SEV3")]
        svc_sets = [(s,) for s in SERVICES] if level < 8 else [(a, b) for k, a in enumerate(SERVICES) for b in SERVICES[k + 1:]]
        for cause in CAUSES:
            of = [i for i in incs if i["cause"] == cause]
            for svcs in svc_sets:
                items = [i for i in of if i["service"] in svcs]
                for sevs in sev_sets:
                    v = _variants(W, {"items": items, "sevs": sevs or ("SEV1", "SEV2", "SEV3")}, None, key)
                    if v and v[3] <= 20:
                        pool.append((v[1], (cause, svcs, sevs, key), v[0]))
    W["cache"][ck] = pool
    return pool


def _pick(r, pool, need: int):
    for n in range(need, -1, -1):
        sub = [p for p in pool if p[0] >= n]
        if len(sub) >= 12:
            return r.choice(sub)
    return r.choice(pool)


def _org_phrase(org) -> str:
    if org is None:
        return ""
    if org[0] == "dir":
        return f" and handled by an engineer whose manager, at the time the incident was opened, reported to director {org[1]}"
    return f" and handled by an engineer whose manager was based in the {org[1]} office at the time the incident was opened"


def _count_hard(r, W, typ, level):
    _, (svc, sevs, a, b, org), n = _pick(r, _agg_pool(W, "count", level), {6: 1, 7: 2, 8: 2}[level])
    q = (f"How many incidents on the {svc} service with final severity {' or '.join(sevs)} were OPENED between month {a} and "
         f"month {b} of 2026 (inclusive){_org_phrase(org)}? Answer with a number.")
    return q, _chk(n, "num"), n, (svc, a, b)   # one question per service and months window


def _total_hard(r, W, typ, level):
    _, (svc, sevs, a, b, org), n = _pick(r, _agg_pool(W, "total", level), {6: 2, 7: 2, 8: 3}[level])
    q = (f"What is the total number of affected users (latest figures) over all incidents on the {svc} service with final "
         f"severity {' or '.join(sevs)} that were OPENED between month {a} and month {b} of 2026 (inclusive){_org_phrase(org)}? "
         f"Answer with a plain integer.")
    return q, _chk(n, "num"), n, (svc, a, b)


def _latest_hard(r, W, typ, level):
    _, (cause, svcs, sevs, key), best = _pick(r, _agg_pool(W, "latest", level), {6: 1, 7: 1, 8: 2}[level])
    what = "took the longest from opening to resolution" if key == "dur" else "affected the most users (latest figures)"
    sev = f" with final severity {' or '.join(sevs)}" if sevs else ""
    return (f"Among all incidents caused by a {cause} on the {' or '.join(svcs)} service{sev}, which one {what}? Answer with the "
            f"incident id."), _chk(best), best, (cause, svcs)


_HARD = {"lookup": (_lookup_hard, {6: ["code", "sev", "users", "code", "users"], 7: ["code", "asof", "users", "dup", "absent_id"],
                                   8: ["attr_code", "asof", "attr_users", "dup", "attr_absent"]}),
         "multihop": (_multihop_hard, {6: ["mgr_t", "mgr_t", "mgr_t_same", "office_t", "team_t"],
                                       7: ["mgr_t", "office_t", "dir_t", "mgr_t_same", "absent_contractor"],
                                       8: ["dir_t", "office_t", "dup_office", "team_t", "absent_office"]}),
         "count": (_count_hard, None), "total": (_total_hard, None), "latest": (_latest_hard, None)}


def _hard_item(kind: str, seed: int, level: int) -> Item:
    """One document per (level, seed) as below level 6, shared by every question kind (the prompt cache prefills it once)."""
    W = _world_hard(rng(BLOCK, f"doc{level}", seed), level)
    doc = _render_hard(W)
    build, plans = _HARD[kind]
    plan = list(plans[level]) if plans else ["q"] * QUESTIONS
    rng(BLOCK, f"{kind}{level}plan", seed).shuffle(plan)
    qs = []
    for k, typ in enumerate(plan):
        for a in range(60):
            q = build(rng(BLOCK, f"{kind}{level}q{k}.{a}", seed), W, typ, level)
            if q and q[2] is not None and all(q[-1] != x[-1] for x in qs):   # distinct questions (or dedupe keys)
                qs.append(q)
                break
        else:
            raise RuntimeError(f"no question of type {typ} for {kind} L{level} seed {seed}")
    note = ("\nLater updates in the log override earlier facts; org changes take effect on their date; withdrawn incidents do "
            "not count anywhere.")
    if level >= 7:
        note += " If the document does not contain the answer to a question, answer NOT STATED."
    ask = "Answer each of these questions:\n" + "\n".join(f"{i + 1}. {q[0]}" for i, q in enumerate(qs))
    fin = ("\n\nAnswer from the document only. Finish with one final line per question, exactly in the form:\n"
           + "\n".join(f"ANSWER {i + 1}: <answer>" for i in range(len(qs))))
    return Item(f"{BLOCK}.{kind}.L{level}.{seed}", BLOCK, kind,
                [{"role": "user", "content": doc + "\n\n---\n" + ask + note + fin}], multi_check([q[1] for q in qs]),
                max_tokens=32000, meta={"expected": [q[2] for q in qs], "doc_tokens_est": len(doc) // 4, "level": level,
                                        "questions": len(qs), "types": plan})


def _audit_hard(seed: int, level: int) -> Item:
    """Levels 7-8: fact-check a draft report against the level-6+ log. Besides the level-6 errors (stale severity / code,
    swapped digits, an hour off, a wrong service) a line can use the user count before its revision, name the manager from
    the directory although the engineer had moved by then (or name the manager the engineer only moved to later), or report
    a withdrawn incident at all. Correct lines are picked among the incidents that LOOK suspicious (corrected, revised,
    moved engineers). Credit per error found, minus false alarms."""
    r = rng(BLOCK, f"audit{level}", seed)
    W = _world_hard(r, level)
    doc = _render_hard(W)
    hist = W["hist"]
    elig = [i for i in W["incidents"] if i["engineer"] in W["staff"] and _clean(W, i)]
    live = [i for i in elig if not i["dup_of"]]

    def mgr(i):
        return _mgr_at(W, i["engineer"], i["opened"])

    def later_mgr(i):
        return next((h[2] for h in hist[i["engineer"]] if h[0] > i["opened"] and h[2] != mgr(i)), None)
    pools = {"stale_sev": [i for i in live if _changed(i, "sev")], "stale_code": [i for i in live if _changed(i, "code")],
             "stale_users": [i for i in live if _changed(i, "users")],
             "stale_manager": [i for i in live if mgr(i) != W["staff"][i["engineer"]]["manager"]],
             "future_manager": [i for i in live if later_mgr(i)], "withdrawn": [i for i in elig if i["dup_of"]],
             "digits": [i for i in live if len(set(str(i["users"]))) > 1], "duration": live, "service": live}
    kinds = ["stale_sev", "stale_code", "stale_users", "stale_manager", "withdrawn", "digits", "duration", "service"]
    decoys = ["stale_sev", "stale_code", "stale_users", "stale_manager", "future_manager", "stale_users"]
    n_lines = 30
    if level >= 8:
        kinds += ["future_manager", "stale_manager", "stale_code"]
        decoys += ["stale_manager", "stale_sev"]
        n_lines = 36
    used, plan = set(), {}
    for k in kinds:
        i = r.choice([i for i in pools[k] if i["id"] not in used])
        used.add(i["id"])
        plan[i["id"]] = k
    for k in decoys:   # right lines on incidents whose facts changed: a reader who flags every change pays for it
        used.add(r.choice([i for i in pools[k] if i["id"] not in used])["id"])
    rest = r.sample([i for i in live if i["id"] not in used], n_lines - len(used))
    chosen = sorted([W["by_id"][x] for x in used] + rest, key=lambda i: i["opened"])

    def fields(i):
        d = i["resolved"] - i["opened"]
        return {"service": i["service"], "sev": i["sev"], "users": i["users"], "dur": int(d.total_seconds() // 60), "code": i["code"],
                "mgr": mgr(i)}
    lines = []
    for i in chosen:
        f = fields(i)
        k = plan.get(i["id"])
        if k == "stale_sev":
            f["sev"] = i["sev0"]
        elif k == "stale_code":
            f["code"] = i["code0"]
        elif k == "stale_users":
            f["users"] = i["users0"]
        elif k == "stale_manager":
            f["mgr"] = W["staff"][i["engineer"]]["manager"]
        elif k == "future_manager":
            f["mgr"] = later_mgr(i)
        elif k == "digits":
            s_ = str(f["users"])
            p = r.choice([j for j in range(len(s_) - 1) if s_[j] != s_[j + 1]])
            f["users"] = int(s_[:p] + s_[p + 1] + s_[p] + s_[p + 2:])
        elif k == "duration":
            f["dur"] += 60 if f["dur"] <= 60 or r.random() < 0.5 else -60
        elif k == "service":
            f["service"] = r.choice([x for x in SERVICES if x != i["service"]])
        assert (f != fields(i)) == (k is not None and k != "withdrawn")
        lines.append(f"- {i['id']} ({f['service']}, {f['sev']}): about {f['users']:,} users affected, resolved after {f['dur'] // 60}h "
                     f"{f['dur'] % 60:02d}m, root-cause code {f['code']}; handled by {i['engineer']}, whose manager at the time was "
                     f"{f['mgr']}.")
    exp = [i["id"] for i in chosen if i["id"] in plan]
    report = "## Draft reliability report (to be checked)\n\n" + "\n".join(lines)
    question = ("The draft reliability report above was written from the incident log before it. Check EVERY line of the report "
                "against the log: the log is the source of truth, later updates override earlier facts, the manager named must be "
                "the one the engineer reported to when the incident was opened (org changes take effect on their date), and a "
                "withdrawn incident must not appear in the report at all (its line is an error). List the ids of all incidents "
                "whose report line contains at least one error, in report order.")
    fin = "\n\nAnswer from the documents only. Finish with a final line exactly in the form:\nANSWER: <comma-separated incident ids, or none>"

    def check(t, _t=None, exp=tuple(exp)) -> float:
        got = set(re.findall(r"INC-\d+", final_answer(t) or ""))
        return max(0.0, (len(got & set(exp)) - len(got - set(exp))) / len(exp))
    return Item(f"{BLOCK}.audit.L{level}.{seed}", BLOCK, "audit",
                [{"role": "user", "content": doc + "\n\n---\n" + report + "\n\n---\n" + question + fin}], check, max_tokens=32000,
                meta={"expected": ", ".join(exp), "doc_tokens_est": (len(doc) + len(report)) // 4, "level": level, "wrong": len(exp)})


# ---- levels 9-10 (v0.11) ----------------------------------------------------------------------------------------------
# Aimed at the frontier: levels 6-8 were 1.0 for Claude Opus / Sonnet. Same document size (<= ~95k real tokens); the log
# now also has corrections of corrections: an update withdrawn by a later one (L10: a withdrawal withdrawn again), root-
# cause corrections that apply only once a vendor confirms them, mistyped user-count revisions corrected, org changes
# corrected retroactively (a move took effect on another date) or cancelled, and duplicates of incidents that are not in
# this log. Aggregations ask for one number per month over three months (20+ records to trace per question), the top two
# of a ranking, or a whole report to audit - partial credit per number.
_NAME = {"sev": "severity", "code": "root-cause code"}
AUDIT_X_TOKENS = {9: 60_000, 10: 56_000}   # the audit's log is smaller: its long report comes on top


def _world_x(r, level: int, n_tokens: int | None = None) -> dict:
    W = _world_hard(r, level, n_tokens)
    incs = W["incidents"]
    W["hist0"] = {n: list(h) for n, h in W["hist"].items()}   # the org changes as first logged (before any correction)
    for inc in incs:
        users = [(inc["opened"], inc["users0"])]
        for when, text in sorted(inc["updates"]):
            m = re.search(r"affected users is revised from [\d,]+ to ([\d,]+)\.", text)
            if m:
                users.append((when, int(m.group(1).replace(",", ""))))
        inc["tl"] = {"sev": list(inc["hist"]["sev"]), "code": list(inc["hist"]["code"]), "users": users}
        inc["naive"] = {f: v[-1][1] for f, v in inc["tl"].items()}   # a reader who applies every change but no withdrawal / condition
        inc["meta"] = {}

    def after(inc):
        return max([inc["resolved"]] + [w for w, _ in inc["updates"]]) + dt.timedelta(days=r.randint(2, 20), hours=r.randint(1, 12))
    live = [i for i in incs if not i["dup_of"]]
    # 1) the last severity / code change withdrawn as entered in error; at L10 some withdrawals are withdrawn again
    cand = [i for i in live if len(i["tl"]["sev"]) > 1 or len(i["tl"]["code"]) > 1]
    for inc in r.sample(cand, min(len(cand), 30)):
        field = r.choice([f for f in ("sev", "code") if len(inc["tl"][f]) > 1])
        tl = inc["tl"][field]
        (t_last, v_last), v_prev = tl[-1], tl[-2][1]
        if sum(1 for w, _ in tl if w.date() == t_last.date()) > 1:
            continue
        vd = after(inc)
        inc["updates"].append((vd, f"Post-mortem update for {inc['id']}: the {_NAME[field]} change of {t_last:%Y-%m-%d} is withdrawn - "
                                   f"it was entered in error."))
        tl.append((vd, v_prev))
        inc["meta"]["void"] = (field, t_last, vd)
        if level >= 10 and r.random() < 0.45:
            rd = after(inc)
            inc["updates"].append((rd, f"Post-mortem update for {inc['id']}: the withdrawal of {vd:%Y-%m-%d} is itself withdrawn; the "
                                       f"{_NAME[field]} change of {t_last:%Y-%m-%d} stands."))
            tl.append((rd, v_last))
            inc["meta"]["reinstate"] = (field, vd, rd)
    # 2) root-cause corrections that apply only when the vendor confirms them
    for inc in r.sample([i for i in live if "void" not in i["meta"] or i["meta"]["void"][0] != "code"], 24):
        cur = inc["tl"]["code"][-1][1]
        new = f"RC-{r.randint(10, 99)}{r.choice('ABCDEFGH')}"
        if new == cur:
            continue
        cd = after(inc)
        inc["updates"].append((cd, f"Post-mortem update for {inc['id']}: if the vendor confirms a defect in their component, the "
                                   f"root-cause code will be corrected from {cur} to {new}."))
        outcome = r.choice(["yes", "no", "none"])
        cd2 = None
        if outcome != "none":
            cd2 = after(inc)
            inc["updates"].append((cd2, f"Vendor response for {inc['id']}: " + ("the defect is confirmed." if outcome == "yes"
                                                                                 else "no defect was found in their component.")))
            if outcome == "yes":
                inc["tl"]["code"].append((cd2, new))
        inc["meta"]["cond"] = (outcome, cd, cd2, cur, new)
        inc["naive"]["code"] = new   # a reader who applies the announcement
    # 3) user-count revisions that were mistyped (corrected figure) or withdrawn (the original figure stands)
    for inc in r.sample([i for i in live if len(i["tl"]["users"]) > 1], 30):
        tl = inc["tl"]["users"]
        rd, fd = tl[-1][0], after(inc)
        if r.random() < 0.5:
            x = max(10, int(tl[-1][1] * r.choice([0.5, 0.8, 1.25, 1.6])) + r.randint(-40, 40))
            inc["updates"].append((fd, f"Post-mortem update for {inc['id']}: the revised user count of {rd:%Y-%m-%d} was mistyped; "
                                       f"the correct number of affected users is {x:,}."))
        else:
            x = inc["users0"]
            inc["updates"].append((fd, f"Post-mortem update for {inc['id']}: the user-count revision of {rd:%Y-%m-%d} is withdrawn; "
                                       f"the original figure stands."))
        tl.append((fd, x))
        inc["meta"]["fix"] = (rd, fd)
    # 4) duplicates of incidents that are not in this log (the withdrawn one is; the other one is not)
    targets = {i["dup_of"] for i in incs if i["dup_of"]}
    for inc in r.sample([i for i in live if i["id"] not in targets and i["engineer"] in W["staff"]], 7):
        ghost = f"INC-{r.randint(1000, 9999)}"
        if ghost in W["by_id"]:
            continue
        when = inc["resolved"] + dt.timedelta(days=r.randint(1, 6), hours=r.randint(0, 12))
        inc["updates"].append((when, f"{inc['id']} was a duplicate of {ghost}, which is tracked on the partner status page and not in "
                                     f"this log; {inc['id']} is withdrawn and does not count in any statistics."))
        inc["dup_of"], inc["ghost"] = ghost, True
    # 5) org changes corrected retroactively (another effective date) or cancelled
    W["retro"], W["cancelled"], extra = [], [], {}
    moves = [(n, k) for n, h in W["hist"].items() for k in range(1, len(h))]
    for n, k in r.sample(moves, min(len(moves), 16)):
        h = W["hist"][n]
        if any(x[0] == n for x in W["retro"]):
            continue
        eff = h[k][0]
        new = eff + dt.timedelta(days=r.choice([-1, 1]) * r.randint(8, 35))
        lo = h[k - 1][0] if k > 1 else _T0 + dt.timedelta(days=5)
        hi = h[k + 1][0] if k + 1 < len(h) else _T0 + dt.timedelta(days=300)
        if not lo + dt.timedelta(days=3) < new < hi - dt.timedelta(days=3):
            continue
        cw = max(eff, new) + dt.timedelta(days=r.randint(3, 25), hours=r.randint(1, 12))
        W["org"].append((cw, f"Correction to the org change of {eff:%Y-%m-%d}: {n}'s move to {h[k][2]} took effect on "
                             f"{new:%Y-%m-%d}, not {eff:%Y-%m-%d}."))
        h[k] = (new, h[k][1], h[k][2])
        W["retro"].append((n, eff, new))
        extra.setdefault(n, set()).update({eff.date(), new.date()})
    movers = sorted(n for n, h in W["hist"].items() if len(h) > 1 and n not in extra)
    for n in r.sample(movers, min(6, len(movers))):
        h = W["hist"][n]
        (eff, team, mgr), prev = h[-1], h[-2]
        cw = eff + dt.timedelta(days=r.randint(4, 30), hours=r.randint(1, 12))
        W["org"].append((cw, f"The org change of {eff:%Y-%m-%d} for {n} is cancelled: {n} never moved and kept reporting to {prev[2]}"
                             + (f" on the {prev[1]} team." if team != prev[1] else ".")))
        h.pop()
        W["cancelled"].append((n, eff))
        extra.setdefault(n, set()).add(eff.date())
    W["extra_days"] = extra
    for inc in incs:
        for f in ("sev", "code", "users"):
            inc[f] = inc["tl"][f][-1][1]
        inc["wd_when"] = next((w for w, t in inc["updates"] if " was a duplicate of " in t), None)
    return W


def _clean_x(W, inc) -> bool:
    """_clean, plus no logged-then-corrected or cancelled org change of the engineer on the opening day."""
    return _clean(W, inc) and inc["opened"].date() not in W["extra_days"].get(inc["engineer"], ())


def _value_at(inc, field: str, when: dt.datetime):
    return [v for w, v in inc["tl"][field] if w <= when][-1]


def _eod(day) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(23, 59))


def _asof_q(r, inc, field: str, lo: dt.datetime, hi: dt.datetime):
    """A question about the value at the end of a day strictly between two entries (None when they are too close)."""
    days = (hi.date() - lo.date()).days
    if days < 2:
        return None
    day = lo.date() + dt.timedelta(days=r.randint(1, days - 1))
    a = _value_at(inc, field, _eod(day))
    what = {"sev": "severity", "code": "root-cause code", "users": "number of affected users"}[field]
    q = f"According to the log, what was the {what} of {inc['id']} at the end of {day:%Y-%m-%d} (leave out any update made after that day)?"
    return q, [(_chk(a, "sev" if field == "sev" else "num" if field == "users" else "text"), a)], q


def _list_chk(expected: list):
    """A list answer: credit per id found, a wrong id cancels one (listing everything earns nothing)."""
    exp = set(expected)

    def f(t, _t=None):
        got = set(re.findall(r"INC-\d+", final_answer(t) or ""))
        return max(0.0, (len(got & exp) - len(got - exp)) / len(exp))
    return f


def _wd_by(inc, when) -> bool:
    return bool(inc["dup_of"]) and inc["wd_when"] <= when


def _lookup_x(r, W, typ, level):
    """Every incident that matches a condition, scattered over the whole log: the final value (L9) or the value at the end of
    a day (L10) differs from the incident entry. Withdrawn, restored and unconfirmed changes decide membership."""
    incs = W["incidents"]
    field = {"code": "code", "sev": "sev", "users": "users"}[typ]
    what = {"code": "root-cause code", "sev": "severity", "users": "number of affected users"}[field]
    if field == "users":
        m = r.randint(1, 10)
        scope, sel = f"opened in month {m} of 2026 (on any service)", [i for i in incs if i["opened"].month == m]
    else:
        svc = r.choice(SERVICES)
        scope, sel = f"on the {svc} service", [i for i in incs if i["service"] == svc]
    if level >= 10:
        day = _T0.date() + dt.timedelta(days=r.randint(120, 300))
        t = _eod(day)
        exp = [i["id"] for i in sel if i["opened"] <= t and not _wd_by(i, t) and _value_at(i, field, t) != i[field + "0"]]
        naive = [i["id"] for i in sel if i["opened"] <= t and not i["dup_of"] and i["naive"][field] != i[field + "0"]]
        q = (f"At the end of {day:%Y-%m-%d}, which incidents {scope} had a {what} different from the one in their incident entry, as "
             f"recorded up to that day? Leave out incidents opened later, incidents withdrawn by then and every update made after that "
             f"day. List the incident ids.")
    else:
        base = "users0" if field == "users" else field + "0"
        exp = [i["id"] for i in sel if not i["dup_of"] and i[field] != i[base]]
        naive = [i["id"] for i in sel if not i["dup_of"] and i["naive"][field] != i[base]]
        q = (f"Which incidents {scope} end with a {what} different from the one in their incident entry? Withdrawn incidents do not "
             f"count. List the incident ids.")
    if not 3 <= len(exp) <= 12 or set(exp) == set(naive):
        return None
    return q, [(_list_chk(exp), ", ".join(exp))], {q}


def _mh_x(W, inc, what, hist):
    e, t = inc["engineer"], inc["opened"]
    if e not in hist:
        return None
    m = _at(hist[e], t)[2]
    return {"mgr": m, "team": _at(hist[e], t)[1], "office": _office_at(W, m, t), "dir": _dir_at(W, m, t)}[what]


def _headcount(W, hist, day, key: str) -> dict:
    t, c = _eod(day), {}
    for e in W["staff"]:
        m = _at(hist[e], t)[2]
        k = m if key == "mgr" else _dir_at(W, m, t)
        c[k] = c.get(k, 0) + 1
    return c


def _multihop_x(r, W, typ, level):
    """Headcounts on a day after every org correction: how many directory engineers reported to each of three managers
    (L9) / were in each director's organisation (L10) - every engineer's reporting line on that day, 59 timelines."""
    days = sorted({(min(a, b) + dt.timedelta(days=k)).date() for _n, a, b in W["retro"] for k in range(1, max(2, abs((b - a).days)))}
                  | {(e + dt.timedelta(days=k)).date() for _n, e in W["cancelled"] for k in range(1, 30)})
    day = r.choice(days or [_T0.date() + dt.timedelta(days=r.randint(60, 280))])
    key = "mgr" if level < 10 else "dir"
    right, naive = _headcount(W, W["hist"], day, key), _headcount(W, W["hist0"], day, key)
    if key == "mgr":
        diff = [m for m in W["mgr"] if right.get(m, 0) != naive.get(m, 0)]
        if not diff:
            return None
        first = r.choice(diff)
        who = sorted([first] + r.sample([m for m in W["mgr"] if m != first], 2), key=list(W["mgr"]).index)
        q = f"At the end of {day:%Y-%m-%d}, how many engineers from the directory reported to each of these managers: {', '.join(who)}?"
    else:
        who = sorted({d for h in W["director"].values() for _, d in h})
        if all(right.get(d, 0) == naive.get(d, 0) for d in who):
            return None
        q = (f"At the end of {day:%Y-%m-%d}, how many engineers from the directory were in each director's organisation (their manager "
             f"reported to that director on that day): {', '.join(who)}?")
    return q, [(_chk(right.get(x, 0), "num"), right.get(x, 0)) for x in who], {day.month}, who


def _agg_x_pool(W, kind: str, level: int):
    """(traps, params, answers, work) for every question of a kind: traps = how many kinds of misreading change an answer."""
    ck = ("x", kind, level)
    if ck in W["cache"]:
        return W["cache"][ck]
    incs = W["incidents"]
    dirs = sorted({d for h in W["director"].values() for _, d in h})
    pool = []
    if kind in ("count", "total") and level >= 10:   # one month, every service, a breakdown by director
        sevs = ("SEV2", "SEV3") if kind == "count" else ("SEV1", "SEV2")
        for m in range(1, 11):
            items = [i for i in incs if i["opened"].month == m]

            def per_dir(var):
                out = []
                for d in dirs:
                    rows = [i for i in items if (i["sev0"] if var == "sev0" else i["naive"]["sev"] if var == "naive" else i["sev"]) in sevs
                            and (not i["dup_of"] or var == "wd") and _mh_x(W, i, "dir", W["hist0"] if var == "org0" else W["hist"]) == d]
                    out.append(len(rows) if kind == "count" else
                               sum(i["users0"] if var == "users0" else i["naive"]["users"] if var == "unfixed" else i["users"] for i in rows))
                return out
            right = per_dir("right")
            variants = ("sev0", "naive", "wd", "org0") + (("users0", "unfixed") if kind == "total" else ())
            pool.append((sum(1 for v in variants if per_dir(v) != right), (m, sevs, tuple(dirs)), right, len(items)))
    elif kind in ("count", "total"):
        if level < 10:
            svcs = SERVICES
            sev_sets = [("SEV1", "SEV2"), ("SEV2", "SEV3"), ("SEV1", "SEV3")] if kind == "count" else [("SEV1", "SEV2"), ("SEV1", "SEV3")]
        else:
            svcs, sev_sets = [None], [("SEV1",)]
        for svc in svcs:
            on = [i for i in incs if svc is None or i["service"] == svc]
            for a in range(1, 9):
                months = [a, a + 1, a + 2]
                items = [i for i in on if a <= i["opened"].month <= a + 2]
                for sevs in sev_sets:
                    touch = [i for i in items if i["sev"] in sevs or i["sev0"] in sevs or i["naive"]["sev"] in sevs]
                    if len(touch) < (18 if kind == "count" else 14):
                        continue
                    for org in dirs:
                        def total(var, m):
                            rows = []
                            for i in items:
                                if i["opened"].month != m:
                                    continue
                                sev = i["sev0"] if var == "sev0" else i["naive"]["sev"] if var == "naive" else i["sev"]
                                if sev not in sevs or (i["dup_of"] and var != "wd"):
                                    continue
                                if _mh_x(W, i, "dir", W["hist0"] if var == "org0" else W["hist"]) != org:
                                    continue
                                rows.append(i)
                            if kind == "count":
                                return len(rows)
                            return sum(i["users0"] if var == "users0" else i["naive"]["users"] if var == "unfixed" else i["users"] for i in rows)
                        right = [total("right", m) for m in months]
                        if min(right) < 1:
                            continue
                        variants = ("sev0", "naive", "wd", "org0") + (("users0", "unfixed") if kind == "total" else ())
                        traps = sum(1 for v in variants if [total(v, m) for m in months] != right)
                        pool.append((traps, (svc, sevs, a, org), right, len(touch)))
    else:   # latest: the top incidents by affected users (latest figures) among a cause on 3 services (L9) / all services (L10)
        n_top = 2 if level < 10 else 5
        svc_sets = list(itertools.combinations(SERVICES, 3)) if level < 10 else [tuple(SERVICES)]
        for cause in CAUSES:
            of = [i for i in incs if i["cause"] == cause]
            for svcs in svc_sets:
                items = [i for i in of if i["service"] in svcs]
                for sevs in [("SEV1", "SEV2"), ("SEV2", "SEV3")]:
                    def ranked(var):
                        users = (lambda i: i["users0"]) if var == "users0" else (lambda i: i["naive"]["users"]) if var == "unfixed" else (lambda i: i["users"])
                        sev = (lambda i: i["sev0"]) if var == "sev0" else (lambda i: i["naive"]["sev"]) if var == "naive" else (lambda i: i["sev"])
                        return sorted(((users(i), i["id"]) for i in items if (not i["dup_of"] or var == "wd") and sev(i) in sevs), reverse=True)
                    right = ranked("right")
                    if len(right) < 14 or len({u for u, _ in right[:n_top + 1]}) < n_top + 1:
                        continue
                    top = [x[1] for x in right[:n_top]]
                    traps = sum(1 for v in ("users0", "unfixed", "wd", "sev0", "naive") if [x[1] for x in ranked(v)[:n_top]] != top)
                    pool.append((traps, (cause, svcs, sevs), top, len(right)))
    W["cache"][ck] = pool
    return pool


def _months_labels(a):
    return [f"month {m}" for m in (a, a + 1, a + 2)]


def _org_x(org) -> str:
    return (f" and handled by an engineer whose manager, at the time the incident was opened, reported to director {org} (engineers "
            f"who are not in the directory do not count)")


def _breakdown_q(r, W, kind, level):
    _, (m, sevs, dirs), right, _n = _pick(r, _agg_x_pool(W, kind, level), 3)
    what = "how many incidents" if kind == "count" else "what is the total number of affected users (latest figures) over all incidents"
    verb = "were" if kind == "count" else "that were"
    q = (f"In month {m} of 2026: {what} on any service with final severity {' or '.join(sevs)} {verb} OPENED in that month and handled by "
         f"an engineer whose manager, at the time the incident was opened, reported to each of these directors: {', '.join(dirs)}? "
         f"(Engineers who are not in the directory count for none of them.)" + ("" if kind == "count" else " Plain integers."))
    return q, [(_chk(x, "num"), x) for x in right], {m}, list(dirs)


def _count_x(r, W, typ, level):
    if level >= 10:
        return _breakdown_q(r, W, "count", level)
    _, (svc, sevs, a, org), right, _n = _pick(r, _agg_x_pool(W, "count", level), 3)
    where = f"on the {svc} service" if svc else "on any service"
    q = (f"For each of the months {a}, {a + 1} and {a + 2} of 2026: how many incidents {where} with final severity "
         f"{' or '.join(sevs)} were OPENED in that month{_org_x(org)}?")
    return q, [(_chk(x, "num"), x) for x in right], {(svc, m) for m in (a, a + 1, a + 2)}, _months_labels(a)


def _total_x(r, W, typ, level):
    if level >= 10:
        return _breakdown_q(r, W, "total", level)
    _, (svc, sevs, a, org), right, _n = _pick(r, _agg_x_pool(W, "total", level), 3)
    where = f"on the {svc} service" if svc else "on any service"
    q = (f"For each of the months {a}, {a + 1} and {a + 2} of 2026: what is the total number of affected users (latest figures) over "
         f"all incidents {where} with final severity {' or '.join(sevs)} that were OPENED in that month{_org_x(org)}? Plain integers.")
    return q, [(_chk(x, "num"), x) for x in right], {(svc, m) for m in (a, a + 1, a + 2)}, _months_labels(a)


def _latest_x(r, W, typ, level):
    _, (cause, svcs, sevs), top, _n = _pick(r, _agg_x_pool(W, "latest", level), 2)
    where = "on any service" if len(svcs) == len(SERVICES) else f"on the {', '.join(svcs[:-1])} or {svcs[-1]} service"
    n = len(top)
    q = (f"Among all incidents caused by a {cause} {where} with final severity {' or '.join(sevs)}, which {n} affected the most "
         f"users (latest figures)? Incident ids, most first.")
    return q, [(_chk(x), x) for x in top], {cause}, ["the most", "the second most", "the third most", "the fourth", "the fifth"][:n]


_X = {"lookup": (_lookup_x, {9: ["code", "sev", "users"], 10: ["code", "sev", "users"]}),
      "multihop": (_multihop_x, {9: ["q"] * 3, 10: ["q"] * 3}),
      "count": (_count_x, {9: ["q"] * 3, 10: ["q"] * 3}), "total": (_total_x, {9: ["q"] * 3, 10: ["q"] * 3}),
      "latest": (_latest_x, {9: ["q"] * 4, 10: ["q"] * 2})}


_X_NOTE = ("\nHow to read the log: later updates override earlier facts. An update that withdraws an earlier update cancels it (the "
           "value before it applies again), and a withdrawn withdrawal restores the update. A conditional correction applies only "
           "from the day its condition is confirmed. A correction to an org change applies retroactively: the move counts from the "
           "corrected date, and a cancelled move never happened. Org changes take effect on their date. Withdrawn incidents do not "
           "count anywhere. If the document does not contain the answer to a question, answer NOT STATED.")


def _x_item(kind: str, seed: int, level: int) -> Item:
    """Levels 9-10: one document per (level, seed), shared by every question kind; several numbered answers per question
    for the aggregations (credit per answer)."""
    W = _world_x(rng(BLOCK, f"doc{level}", seed), level)
    doc = _render_hard(W)
    build, plans = _X[kind]
    plan = list(plans[level])
    rng(BLOCK, f"{kind}{level}plan", seed).shuffle(plan)
    qs = []
    for k, typ in enumerate(plan):
        for a in range(80):
            q = build(rng(BLOCK, f"{kind}{level}q{k}.{a}", seed), W, typ, level)
            # distinct questions; for the first 40 draws also a distinct key (another service window / cause)
            if q and all(x[1] is not None for x in q[1]) and all(
                    q[0] != x[0] and [e for _, e in q[1]] != [e for _, e in x[1]] and (a >= 40 or not set(q[2]) & set(x[2])) for x in qs):
                qs.append(q)
                break
        else:
            raise RuntimeError(f"no question of type {typ} for {kind} L{level} seed {seed}")
    lines, checks, expected, n = [], [], [], 1
    for i, q in enumerate(qs):
        text = q[0]
        if len(q[1]) > 1:
            labels = q[3]
            text += " Give " + ", ".join(f"ANSWER {n + j} ({lab})" for j, lab in enumerate(labels[:-1])) + \
                f" and ANSWER {n + len(labels) - 1} ({labels[-1]})."
        else:
            text += f" (ANSWER {n})"
        lines.append(f"{i + 1}. {text}")
        for c, e in q[1]:
            checks.append(c)
            expected.append(e)
        n += len(q[1])
    ask = "Answer each of these questions:\n" + "\n".join(lines)
    fin = ("\n\nAnswer from the document only. Finish with one final line per answer, exactly in the form:\n"
           + "\n".join(f"ANSWER {i + 1}: <answer>" for i in range(len(expected))))
    return Item(f"{BLOCK}.{kind}.L{level}.{seed}", BLOCK, kind,
                [{"role": "user", "content": doc + "\n\n---\n" + ask + _X_NOTE + fin}], multi_check(checks),
                max_tokens=32000, meta={"expected": expected, "doc_tokens_est": len(doc) // 4, "level": level,
                                        "questions": len(qs), "types": plan})


def _audit_x(seed: int, level: int) -> Item:
    """Levels 9-10 of audit: 60 / 90 report lines on a level-9+ log (a smaller one: the report is long) with 16 / 22 errors.
    Besides the level-7/8 errors (and a duration off by a few minutes: every duration has to be computed) a
    line can keep a change that was withdrawn, apply a vendor correction that was never confirmed, use a mistyped user
    count, or name the manager of a move that was cancelled or took effect on another date. Right lines are picked among
    incidents whose facts changed in those ways (a reader who flags every change pays for it)."""
    r = rng(BLOCK, f"audit{level}", seed)
    W = _world_x(r, level, AUDIT_X_TOKENS[level])
    doc = _render_hard(W)
    elig = [i for i in W["incidents"] if i["engineer"] in W["staff"] and _clean_x(W, i) and not i.get("ghost")]
    live = [i for i in elig if not i["dup_of"]]
    affected = {n for n, *_ in W["retro"]} | {n for n, _ in W["cancelled"]}

    def mgr(i, hist="hist"):
        return _mh_x(W, i, "mgr", W[hist])
    pools = {"void_kept": [i for i in live if "void" in i["meta"] and "reinstate" not in i["meta"]],
             "cond_applied": [i for i in live if "cond" in i["meta"] and i["meta"]["cond"][0] != "yes"],
             "cond_missed": [i for i in live if "cond" in i["meta"] and i["meta"]["cond"][0] == "yes"],
             "users_unfixed": [i for i in live if "fix" in i["meta"] and i["naive"]["users"] != i["users"]],
             "org_as_logged": [i for i in live if i["engineer"] in affected and mgr(i) != mgr(i, "hist0")],
             "stale_sev": [i for i in live if i["sev0"] != i["sev"]], "stale_code": [i for i in live if i["code0"] != i["code"]],
             "withdrawn": [i for i in elig if i["dup_of"]], "digits": [i for i in live if len(set(str(i["users"]))) > 1],
             "duration": live, "minutes": live, "reinstated": [i for i in live if "reinstate" in i["meta"]]}
    kinds = ["void_kept", "cond_applied", "users_unfixed", "org_as_logged", "stale_sev", "stale_code", "withdrawn", "digits",
             "duration", "void_kept", "users_unfixed", "org_as_logged", "minutes", "minutes", "cond_missed", "reinstated"]
    decoys = ["void_kept", "cond_applied", "cond_missed", "users_unfixed", "org_as_logged", "stale_code", "reinstated", "org_as_logged"]
    n_lines = 60
    if level >= 10:
        kinds += ["minutes", "minutes", "org_as_logged", "cond_applied", "stale_sev", "withdrawn"]
        decoys += ["reinstated", "org_as_logged", "cond_applied", "users_unfixed", "void_kept"]
        n_lines = 90
    used, plan = set(), {}
    for k in kinds:
        c = [i for i in pools[k] if i["id"] not in used]
        if c:
            i = r.choice(c)
            used.add(i["id"])
            plan[i["id"]] = k
    for k in decoys:
        c = [i for i in pools[k] if i["id"] not in used]
        if c:
            used.add(r.choice(c)["id"])
    rest = r.sample([i for i in live if i["id"] not in used], n_lines - len(used))
    chosen = sorted([W["by_id"][x] for x in used] + rest, key=lambda i: i["opened"])

    def fields(i):
        d = i["resolved"] - i["opened"]
        return {"service": i["service"], "sev": i["sev"], "users": i["users"], "dur": int(d.total_seconds() // 60), "code": i["code"],
                "mgr": mgr(i)}
    lines = []
    for i in chosen:
        f = fields(i)
        k = plan.get(i["id"])
        if k == "void_kept":
            fld, t_last, _vd = i["meta"]["void"]
            f[fld] = [v for w, v in i["tl"][fld] if w <= t_last][-1]
        elif k == "reinstated":   # the reader stopped at the withdrawal
            fld, vd, _rd = i["meta"]["reinstate"]
            f[fld] = _value_at(i, fld, vd)
        elif k == "cond_applied":
            f["code"] = i["meta"]["cond"][4]
        elif k == "cond_missed":
            f["code"] = i["meta"]["cond"][3]
        elif k == "users_unfixed":
            f["users"] = i["naive"]["users"]
        elif k == "org_as_logged":
            f["mgr"] = mgr(i, "hist0")
        elif k == "stale_sev":
            f["sev"] = i["sev0"]
        elif k == "stale_code":
            f["code"] = i["code0"]
        elif k == "minutes":
            f["dur"] += r.choice([-1, 1]) * r.randint(1, 9) if f["dur"] > 10 else r.randint(1, 9)
        elif k == "digits":
            s_ = str(f["users"])
            p = r.choice([j for j in range(len(s_) - 1) if s_[j] != s_[j + 1]])
            f["users"] = int(s_[:p] + s_[p + 1] + s_[p] + s_[p + 2:])
        elif k == "duration":
            f["dur"] += 60 if f["dur"] <= 60 or r.random() < 0.5 else -60
        if k and k != "withdrawn" and f == fields(i):   # the stale value happens to equal the final one: no error after all
            plan.pop(i["id"])
        lines.append(f"- {i['id']} ({f['service']}, {f['sev']}): about {f['users']:,} users affected, resolved after {f['dur'] // 60}h "
                     f"{f['dur'] % 60:02d}m, root-cause code {f['code']}; handled by {i['engineer']}, whose manager at the time was "
                     f"{f['mgr']}.")
    exp = [i["id"] for i in chosen if i["id"] in plan]
    report = "## Draft reliability report (to be checked)\n\n" + "\n".join(lines)
    question = ("The draft reliability report above was written from the incident log before it. Check EVERY line of the report "
                "against the log: the log is the source of truth, and a withdrawn incident must not appear in the report at all (its "
                "line is an error). The manager named must be the one the engineer reported to when the incident was opened." + _X_NOTE.replace(
                    " If the document does not contain the answer to a question, answer NOT STATED.", "")
                + "\nList the ids of all incidents whose report line contains at least one error, in report order.")
    fin = "\n\nAnswer from the documents only. Finish with a final line exactly in the form:\nANSWER: <comma-separated incident ids, or none>"

    def check(t, _t=None, exp=tuple(exp)) -> float:
        got = set(re.findall(r"INC-\d+", final_answer(t) or ""))
        return max(0.0, (len(got & set(exp)) - len(got - set(exp))) / len(exp))
    return Item(f"{BLOCK}.audit.L{level}.{seed}", BLOCK, "audit",
                [{"role": "user", "content": doc + "\n\n---\n" + report + "\n\n---\n" + question + fin}], check, max_tokens=32000,
                meta={"expected": ", ".join(exp), "doc_tokens_est": (len(doc) + len(report)) // 4, "level": level, "wrong": len(exp)})


MAX_LEVEL = 10
KINDS = {"lookup": lookup, "multihop": multihop, "count": count, "latest": latest, "total": total, "audit": audit}
