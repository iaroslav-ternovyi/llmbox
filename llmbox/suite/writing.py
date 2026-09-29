"""Writing, editing, translation, summarization, extraction - verifiable constraints, multilingual, 10 difficulty levels.

Level raises the number of simultaneous constraints and adds traps (changed decisions, corrections, glossaries).
Score = fraction of constraints met.
v0.11: levels 7-8 (minutes, i18n, proofread: 6-8) - interacting rules, contradicting rule pairs with one stated resolution,
derived facts, authority rules, values that must not appear; see "levels 7-8" below. Levels 9-10 (aimed at the frontier):
exact word / letter counts per sentence or bullet, lipograms, initials, longer threads and edit chains; see "levels 9-10".
"""
from __future__ import annotations

import json
import re

from .common import Item, detect_lang, rng, strip_think, words

BLOCK = "writing"
LANGS = {"en": "English", "es": "Spanish", "de": "German", "fr": "French", "ru": "Russian", "it": "Italian", "pt": "Portuguese"}
TOPICS = ["a reusable water bottle", "a city bike-sharing service", "a small bakery opening", "a password manager app",
          "a weekend hiking trip", "a home coffee grinder", "an online language course", "a solar phone charger"]
FIRST = ["Lucía", "Marco", "Hanna", "Pierre", "Olga", "Tomás", "Inés", "Yusuf", "Mei", "Jonas"]
LAST = ["García", "Rossi", "Müller", "Dubois", "Ivanova", "Silva", "Novak", "Kaya", "Chen", "Berg"]
COMPANIES = ["Northwind", "Bluepeak", "Altavia", "Quantum Loom", "Verdant Labs", "Orbital Foods"]
BANNED = {"en": "very", "es": "muy", "de": "sehr", "fr": "très", "ru": "очень", "it": "molto", "pt": "muito"}


def _paragraphs(t: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", t.strip()) if p.strip()]


def _sentences(p: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+", p.strip()) if s.strip()]


def _has_num(t: str, n) -> bool:
    s = str(n)
    variants = {s, s.replace(".", ","), f"{n:,}" if isinstance(n, int) else s, f"{n:,}".replace(",", ".") if isinstance(n, int) else s,
                f"{n:,}".replace(",", " ") if isinstance(n, int) else s, f"{n:,}".replace(",", " ") if isinstance(n, int) else s}
    return any(v in t for v in variants)


def _share(results: list, gate: bool) -> float:
    """v0.10: the share of the constraints met (was all-or-nothing). Only a real attempt earns it: an empty or off-topic
    reply meets every "do not ..." rule, so the gate (right language, enough text, the facts kept) comes first."""
    return sum(bool(x) for x in results) / len(results) if gate and results else 0.0


def constrained(seed: int, level: int = 3) -> Item:
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _constrained_x(seed, level)
    if level >= 7:   # v0.11: levels 7-8, below
        return _constrained_hard(seed, level)
    r = rng(BLOCK, f"constrained{level}", seed)
    lang = r.choice(list(LANGS))
    topic = r.choice(TOPICS)
    n_par = r.randint(2, 4)
    lo = r.choice([90, 110, 130]); hi = lo + (60 - 8 * level)
    kws = r.sample(["2026", "48", "Madrid", "Nova", "eco", "24/7", "QR", "3x"], 1 + (level + 1) // 2)
    rules = [f"exactly {n_par} paragraphs separated by one blank line", f"between {lo} and {hi} words in total",
             "include the exact strings " + ", ".join(f'"{k}"' for k in kws)]
    tests = [lambda t: len(_paragraphs(t)) == n_par, lambda t: lo <= len(words(t)) <= hi, lambda t: all(k in t for k in kws)]
    if level >= 2:
        banned = BANNED[lang]
        rules.append(f'never use the word "{banned}"')
        tests.append(lambda t: re.search(rf"(?<!\w){re.escape(banned)}(?!\w)", t, re.I) is None)
        ask_q = r.random() < 0.5
        rules.append("the text must end with a question mark" if ask_q else "do not use any question marks")
        tests.append((lambda t: t.rstrip().endswith("?")) if ask_q else (lambda t: "?" not in t))
    if level >= 3:
        k = r.randint(2, 3)
        rules.append(f"the last paragraph has exactly {k} sentences")
        tests.append(lambda t: bool(_paragraphs(t)) and len(_sentences(_paragraphs(t)[-1])) == k)
    if level >= 4:
        rules.append("start with a title line written entirely in UPPERCASE (the title counts as the first paragraph)")
        tests.append(lambda t: bool(_paragraphs(t)) and _paragraphs(t)[0].splitlines()[0].strip() == _paragraphs(t)[0].splitlines()[0].strip().upper()
                     and any(c.isalpha() for c in _paragraphs(t)[0].splitlines()[0]))
    if level >= 5:
        letter = r.choice("BCDMPST")
        rules.append(f"every paragraph after the title starts with a word beginning with the letter {letter}")
        tests.append(lambda t: len(_paragraphs(t)) > 1 and all(p.strip()[:1].upper() == letter for p in _paragraphs(t)[1:]))
    tests.append(lambda t: detect_lang(t) == lang)
    prompt = (f"Write a short promotional text about {topic} in {LANGS[lang]}.\nRules:\n" + "\n".join(f"- {x}" for x in rules)
              + "\nOutput only the text.")

    def check(text: str, _t=None) -> float:
        t = strip_think(text)
        return _share([f(t) for f in tests], detect_lang(t) == lang and len(words(t)) >= lo // 2)
    return Item(f"{BLOCK}.constrained.L{level}.{seed}", BLOCK, "constrained", [{"role": "user", "content": prompt}], check,
                lang=lang, meta={"level": level})


def translate(seed: int, level: int = 3) -> Item:
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _translate_x(seed, level)
    if level >= 7:   # v0.11: levels 7-8, below
        return _translate_hard(seed, level)
    r = rng(BLOCK, f"translate{level}", seed)
    target = r.choice([k for k in LANGS if k != "en"])
    name = f"{r.choice(FIRST)} {r.choice(LAST)}"
    comp = r.choice(COMPANIES)
    nums = [r.randint(12, 95), r.randint(1200, 9800), round(r.uniform(1.5, 9.9), 1), r.randint(2, 9), r.randint(100, 999)]
    sents = [f"{name}, the operations lead at {comp}, confirmed that the new warehouse will open on 14 March with {nums[0]} employees.",
             f"The company expects to ship {nums[1]} parcels per day during the first quarter.",
             f"Delivery times should drop by {nums[2]} percent compared with last year.",
             f"Customers can track every order in the app, and the support team will answer questions seven days a week.",
             f"A pilot with {nums[3]} partner shops starts next month; each shop gets a starter kit worth {nums[4]} euros.",
             "If the pilot works, the programme will expand to three more regions before the end of the year."]
    src_lines = sents[:2 + level]
    glossary = ""
    if level >= 3:
        src = "\n".join(f"- {s}" for s in src_lines)
        fmt = "Keep the Markdown bullet list: one bullet per sentence, same order."
    else:
        src = " ".join(src_lines)
        fmt = ""
    gl_word = None
    if level >= 4:
        gl_word = {"es": "almacén", "de": "Lager", "fr": "entrepôt", "ru": "склад", "it": "magazzino", "pt": "armazém"}[target]
        glossary = f' Glossary: translate "warehouse" as "{gl_word}".'
    used = [n for i, n in enumerate(nums) if any(str(n) in s for s in src_lines)]
    prompt = (f"Translate the text into {LANGS[target]}. Keep names, numbers and the company name unchanged. {fmt}{glossary} "
              f"Output only the translation.\n\n{src}")

    def check(text: str, _t=None) -> float:
        t = strip_think(text)
        tests = [detect_lang(t) == target, comp in t, name.split()[1] in t, 0.6 <= len(t) / len(src) <= 1.9]
        tests += [_has_num(t, n) for n in used]
        if level >= 3:
            tests.append(len([l for l in t.splitlines() if l.strip().startswith(("- ", "* "))]) == len(src_lines))
        if gl_word:
            tests.append(gl_word.lower() in t.lower())
        return _share(tests, detect_lang(t) == target and 0.3 <= len(t) / len(src) <= 3)
    return Item(f"{BLOCK}.translate.L{level}.{seed}", BLOCK, "translate", [{"role": "user", "content": prompt}], check,
                lang=target, meta={"level": level})


def summarize(seed: int, level: int = 3) -> Item:
    """Email thread where decisions change; only the final values count."""
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _summarize_x(seed, level)
    if level >= 7:   # v0.11: levels 7-8, below
        return _summarize_hard(seed, level)
    r = rng(BLOCK, f"summarize{level}", seed)
    lang = r.choice(["en", "en", "es", "de", "ru", "fr"])
    ppl = r.sample([f"{f} {l}" for f in FIRST for l in LAST], 5)
    months = ["April", "May", "June", "October", "November"]
    dl = [(r.randint(3, 27), r.choice(months)) for _ in range(1 + min(level, 3))]
    budgets = [r.choice([12000, 18500, 24000, 31000, 36500]) for _ in range(1 + (level >= 3))]
    owners = [r.choice(ppl) for _ in range(1 + (level >= 4))]
    msgs = [f"From: {ppl[0]}\nHi all, the client moved the launch of the Atlas dashboard. We need to agree on scope, budget and who owns the rollout.",
            f"From: {ppl[1]}\nFinance approved up to {budgets[0]} EUR for the extra work. Proposed deadline: {dl[0][0]} {dl[0][1]}.",
            f"From: {ppl[2]}\nDesign is done except the export screen. I suggest we drop the PDF export from the first release.",
            f"From: {ppl[3]}\nAgreed on dropping PDF export. {owners[0]} has the most context and should own the rollout."]
    for i, d in enumerate(dl[1:], 1):
        msgs.append(f"From: {ppl[(i + 3) % 5]}\nUpdate: the client needs more time, so the deadline moves to {d[0]} {d[1]}.")
    if len(budgets) > 1:
        msgs.append(f"From: {ppl[1]}\nCorrection from finance: the approved budget is {budgets[1]} EUR, not {budgets[0]}.")
    if len(owners) > 1:
        msgs.append(f"From: {ppl[0]}\n{owners[0]} is on leave, so {owners[1]} takes over the rollout.")
    msgs.append(f"From: {ppl[0]}\nThanks all - please confirm the final plan by Friday.")
    final_day, final_month = dl[-1]
    n_b = r.randint(3, 5)
    prompt = (f"Summarize this email thread in {LANGS[lang]} as exactly {n_b} bullet points starting with \"- \". "
              f"Each bullet has at most 20 words. State the FINAL deadline, the FINAL budget and who FINALLY owns the rollout "
              f"(later messages override earlier ones).\n\n" + "\n\n".join(msgs))

    def check(text: str, _t=None) -> float:
        t = strip_think(text)
        bullets = [l for l in t.splitlines() if l.strip().startswith(("- ", "* ", "• "))]
        tests = [len(bullets) == n_b, bool(bullets) and all(len(words(b)) <= 21 for b in bullets),
                 re.search(rf"\b{final_day}\b", t) is not None, _has_num(t, budgets[-1]), owners[-1].split()[1] in t,
                 detect_lang(t) == lang]
        return _share(tests, bool(bullets) and detect_lang(t) == lang)
    return Item(f"{BLOCK}.summarize.L{level}.{seed}", BLOCK, "summarize", [{"role": "user", "content": prompt}], check,
                lang=lang, meta={"level": level, "expected": f"{final_day} {final_month} / {budgets[-1]} / {owners[-1]}"})


def extract(seed: int, level: int = 3) -> Item:
    """Several orders in one message, with corrections at higher levels; output a JSON array."""
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _extract_x(seed, level)
    if level >= 7:   # v0.11: levels 7-8, below
        return _extract_hard(seed, level)
    r = rng(BLOCK, f"extract{level}", seed)
    name = f"{r.choice(FIRST)} {r.choice(LAST)}"
    city = r.choice(["Valencia", "Porto", "Lyon", "Graz", "Tartu", "Bilbao"])
    n_orders = 1 + (level + 1) // 2
    orders = []
    for _ in range(n_orders):
        orders.append({"sku": f"{r.choice('ABCDEFGH')}{r.randint(100, 999)}-{r.choice('XYZ')}", "quantity": r.randint(2, 40),
                       "unit_price": round(r.uniform(3, 250), 2), "express": r.random() < 0.5})
    parts = [f"Hello, this is {name} from our {city} office."]
    for o in orders:
        parts.append(f"Please send {o['quantity']} units of {o['sku']} at the agreed {o['unit_price']} per unit"
                     + (", express shipping." if o["express"] else ", standard shipping is fine."))
    if level >= 3:
        o = orders[0]
        new_q = o["quantity"] + r.randint(1, 5)
        parts.append(f"Oh wait - for {o['sku']} make it {new_q} units instead of {o['quantity']}.")
        o["quantity"] = new_q
    if level >= 5 and n_orders > 1:
        dropped = orders.pop()
        parts.append(f"And cancel the {dropped['sku']} line completely, we found stock.")
    parts.append("Thanks!")
    schema = '[{"sku": string, "quantity": integer, "unit_price": number, "express": boolean}]'
    prompt = (f"Extract the FINAL order lines from this message as a JSON array matching {schema}, in the order they were "
              f"first mentioned. Output only the JSON array.\n\nMessage: " + " ".join(parts))

    def check(t: str, _t=None) -> float:
        s = strip_think(t)
        m = re.search(r"\[.*\]", s, re.S)
        try:
            arr = json.loads(m.group(0)) if m else None
        except ValueError:
            return 0.0
        if not isinstance(arr, list):
            return 0.0
        tests = [len(arr) == len(orders)]
        for i, o in enumerate(orders):
            g = arr[i] if i < len(arr) and isinstance(arr[i], dict) else {}
            tests += [g.get("sku") == o["sku"], g.get("quantity") == o["quantity"],
                      isinstance(g.get("unit_price"), (int, float)) and abs(g["unit_price"] - o["unit_price"]) < 0.005,
                      g.get("express") is o["express"]]
        return _share(tests, True)   # per field of every order line, plus the number of lines
    return Item(f"{BLOCK}.extract.L{level}.{seed}", BLOCK, "extract", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "expected": orders})


def rewrite(seed: int, level: int = 3) -> Item:
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _rewrite_x(seed, level)
    if level >= 7:   # v0.11: levels 7-8, below
        return _rewrite_hard(seed, level)
    r = rng(BLOCK, f"rewrite{level}", seed)
    n1, n2 = r.randint(3, 19), r.randint(20, 90)
    src = (f"hey!! so we can't ship the thing this week, sorry :( the supplier's truck broke down and we're like {n1} days "
           f"behind. we'll give you {n2}% off the next order, that's the best we can do. don't worry, it won't happen again!!")
    limit = 70 - 5 * level
    rules = ["formal, polite business tone", "keep both numbers", "no contractions (like can't, we'll)", "no exclamation marks",
             "no emoticons", f"at most {limit} words"]
    tests = [lambda t: str(n1) in t and str(n2) in t,
             lambda t: re.search(r"\b(\w+n't|\w+'(ll|re|ve|d|m)|(it|that|let|what|here|there|he|she|who)'s)\b", t.replace("’", "'"), re.I) is None,
             lambda t: "!" not in t, lambda t: ":(" not in t and ":)" not in t, lambda t: len(words(t)) <= limit]
    if level >= 3:
        rules.append("exactly 3 sentences")
        tests.append(lambda t: len(_sentences(t)) == 3)
    if level >= 4:
        rules.append('begin with "Dear customer,"')
        tests.append(lambda t: t.strip().startswith("Dear customer,"))
    if level >= 5:
        rules.append('do not use the words "sorry" or "apologize"')
        tests.append(lambda t: re.search(r"\b(sorry|apologi[sz]e)\b", t, re.I) is None)
    prompt = "Rewrite this customer message. Rules: " + "; ".join(rules) + f". Output only the rewritten text.\n\n{src}"

    def check(text: str, _t=None) -> float:
        t = strip_think(text)
        return _share([f(t) for f in tests], str(n1) in t and str(n2) in t and len(words(t)) >= 10)
    return Item(f"{BLOCK}.rewrite.L{level}.{seed}", BLOCK, "rewrite", [{"role": "user", "content": prompt}], check,
                meta={"level": level})



# ---- v0.6: kinds that still separate strong models at level 5 (v0.5: Tiel solved every older kind at level 5) ----

import datetime as _dt
import difflib

_MEET_PEOPLE = [("Marta Llorente", "product manager"), ("Leo Brandt", "backend lead"), ("Aisha Karimi", "designer"),
                ("Tomasz Wolski", "QA lead"), ("Chloé Martin", "legal counsel"), ("Ravi Menon", "data analyst"),
                ("Greta Holm", "marketing manager"), ("Pablo Ruiz", "mobile lead")]
_MEET_TOPICS = ["vendor contract", "SSO migration", "pricing page", "load testing", "privacy review", "onboarding emails",
                "analytics dashboard", "hiring plan", "API rate limits", "mobile release", "customer survey", "backup policy",
                "accessibility audit", "partner webinar"]
_FILLER = ["Can everyone hear me? The audio keeps cutting out.", "Quick reminder that the coffee machine on the third floor is fixed.",
           "Sorry, I was on mute.", "Let's keep this short, we have another call at eleven.", "I'll share my screen in a second.",
           "Did anyone see the client's email this morning?", "We can discuss the offsite later.", "Noted, thanks."]


def _due_phrases(day: _dt.date, level: int) -> list[tuple[str, _dt.date]]:
    fri = day + _dt.timedelta(days=(4 - day.weekday()) % 7)
    mon = day + _dt.timedelta(days=(7 - day.weekday()) % 7 or 7)
    nxt = (day.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    eom = nxt - _dt.timedelta(days=1)
    lwd = eom - _dt.timedelta(days=max(0, eom.weekday() - 4))
    out = [("this Friday", fri), ("Friday next week", fri + _dt.timedelta(days=7)), ("the coming Monday", mon),
           ("two weeks from today", day + _dt.timedelta(days=14)), (f"{nxt:%B} {nxt.day + 9}", nxt + _dt.timedelta(days=9))]
    if level >= 3:
        out += [("the end of the month", eom), ("the last working day of this month", lwd)]
    if level >= 5:
        rel = nxt + _dt.timedelta(days=14)
        while rel.weekday() > 4:
            rel += _dt.timedelta(days=1)
        d, k = rel, 3
        while k:
            d -= _dt.timedelta(days=1)
            if d.weekday() < 5:
                k -= 1
        out.append((f"three working days before the release on {rel:%B} {rel.day}", d))
    return out


def minutes(seed: int, level: int = 3) -> Item:
    """Meeting transcript -> action items (topic, owner, due date). Owners change, deadlines move and are relative,
    items get dropped (and revived at level 5), people are named by first name or role. Graded per action item."""
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _minutes_x(seed, level)
    if level >= 6:   # v0.11: levels 6-8 (this kind stopped at level 5), below
        return _minutes_hard(seed, level)
    r = rng(BLOCK, f"minutes{level}", seed)
    day = _dt.date(2026, 10, 1) + _dt.timedelta(days=r.randint(0, 60))
    while day.weekday() not in (1, 2):
        day += _dt.timedelta(days=1)
    people = r.sample(_MEET_PEOPLE, 4 + (level >= 3) + (level >= 5))
    first = {p[0]: p[0].split()[0] for p in people}
    topics = r.sample(_MEET_TOPICS, [0, 3, 4, 6, 8, 10][level])
    dues = _due_phrases(day, level)
    chair = people[0][0]

    def ref(person: str) -> str:
        role = dict(people)[person]
        if person == chair:
            return "I"
        return f"our {role}" if level >= 4 and r.random() < 0.4 else first[person]

    state, lines = {}, [f"Attendees: " + ", ".join(f"{n} ({ro})" for n, ro in people), f"Date: {day:%A %Y-%m-%d}", ""]
    for t in topics:
        owner = r.choice(people)[0]
        phrase, due = r.choice(dues)
        state[t] = {"owner": owner, "due": due, "active": True}
        who = ref(owner)
        lines.append(f"{first[chair]}: Next item, the {t}. " + ("I will own it myself" if who == "I" else f"{who[0].upper() + who[1:]} will own it")
                     + f", due {phrase}.")
        if r.random() < 0.3:
            lines.append(f"{first[r.choice(people)[0]]}: {r.choice(_FILLER)}")
    n_changes = [0, 0, 1, 3, 5, 8][level]
    made = 0
    for _ in range(200):
        if made >= n_changes:
            break
        made += 1
        t = r.choice(topics)
        s = state[t]
        kind = r.choice(["owner", "due", "drop"] + (["decline"] if level >= 4 else []) + (["revive"] if level >= 5 else []))
        if kind == "owner" and s["active"]:
            new = r.choice([p[0] for p in people if p[0] != s["owner"]])
            lines.append(f"{first[s['owner']]}: I'm swamped this month - could someone else take the {t}?")
            lines.append(f"{first[new]}: I can take the {t}, same deadline.")
            s["owner"] = new
        elif kind == "due" and s["active"]:
            phrase, due = r.choice([d for d in dues if d[1] != s["due"]])
            lines.append(f"{first[r.choice(people)[0]]}: For the {t}, let's move the deadline to {phrase}.")
            lines.append(f"{first[chair]}: Agreed, {phrase} for the {t}.")
            s["due"] = due
        elif kind == "decline" and s["active"]:
            other = r.choice([p[0] for p in people if p[0] not in (s["owner"], chair)])
            lines.append(f"{first[other]}: I could also take the {t} if that helps.")
            lines.append(f"{first[chair]}: Thanks, but let's keep the {t} with " + ("me" if s["owner"] == chair else first[s["owner"]]) + ".")
        elif kind == "drop" and s["active"]:
            lines.append(f"{first[chair]}: Let's drop the {t} for now, it is not a priority this quarter.")
            s["active"] = False
        elif kind == "revive" and not s["active"]:
            phrase, due = r.choice(dues)
            lines.append(f"{first[chair]}: On second thought, the {t} is back on - same owner as before, due {phrase}.")
            s["active"], s["due"] = True, due
        else:
            made -= 1
            continue
        if r.random() < 0.4:
            lines.append(f"{first[r.choice(people)[0]]}: {r.choice(_FILLER)}")
    lines.append(f"{first[chair]}: That's all, thanks everyone.")
    exp = {t: (s["owner"], s["due"].isoformat()) for t, s in state.items() if s["active"]}
    prompt = ("Here is the transcript of today's meeting. Extract the action items that are still active at the end of the "
              "meeting as a JSON array of objects {\"topic\": string, \"owner\": string, \"due\": \"YYYY-MM-DD\"}. Use the topic "
              "names as they appear in the transcript, the owner's FULL name from the attendee list, and resolve relative dates "
              "against the meeting date. Output only the JSON array.\n\n" + "\n".join(lines))

    def check(text: str, _t=None) -> float:
        s = strip_think(text)
        m = re.search(r"\[.*\]", s, re.S)
        try:
            arr = json.loads(m.group(0)) if m else None
        except ValueError:
            return 0.0
        if not isinstance(arr, list):
            return 0.0
        good, extra, seen = 0, 0, set()
        for o in arr:
            if not isinstance(o, dict):
                extra += 1
                continue
            t = str(o.get("topic", "")).strip().lower().removeprefix("the ")
            key = next((k for k in exp if k.lower() == t), None)
            if key is None or key in seen:
                extra += 1
                continue
            seen.add(key)
            good += str(o.get("owner", "")).strip() == exp[key][0] and str(o.get("due", "")).strip() == exp[key][1]
        return good / (len(exp) + extra) if exp else float(not arr)
    return Item(f"{BLOCK}.minutes.L{level}.{seed}", BLOCK, "minutes", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "expected": exp})


_UI = [  # (key, English source, features) - features gate the level a string can appear at
    ("welcome", "Welcome back, {name}!", 1), ("cta", "Start your free trial", 1), ("saved", "Settings saved.", 1),
    ("session", "Your session expired. Please sign in again.", 1), ("export", "Export as CSV", 1),
    ("password", "Password must be at least {min} characters long.", 1), ("quota", "Only {percent}% of your storage is left.", 1),
    ("storage", "You are using {used} of {total} GB.", 2), ("sync", "Syncing {done} of {total} files…", 2),
    ("brand", "Nova Cloud keeps your files safe.", 2), ("last_seen", "Last seen %s ago", 3),
    ("upload_error", "Upload failed: %1$s (error code %2$d).", 3),
    ("invite", "<b>{inviter}</b> invited you to join the workspace <i>{workspace}</i>.", 3),
    ("policy", "Read our <a href=\"{url}\">privacy policy</a> before sharing files.", 3),
    ("comment", "{name} commented on <b>{file}</b>.", 3), ("ws_limit", "This workspace has reached its member limit.", 4),
    ("files", "{count, plural, one {# file} other {# files}}", 4),
    ("trial", "Your free trial ends in {days, plural, one {# day} other {# days}}.", 4),
    ("members", "{count, plural, =0 {No members yet} one {# member} other {# members}}", 5),
    ("shared", "{gender, select, female {She} male {He} other {They}} shared a folder with you.", 5),
    ("delete", "Delete {count, plural, one {this file} other {these # files}}? Nova Cloud cannot undo this.", 5),
]
_WS = {"es": "espacio de trabajo", "de": "Arbeitsbereich", "fr": "espace de travail", "it": "area di lavoro",
       "pt": "espaço de trabalho", "ru": "рабочее пространство", "uk": "робочий простір"}
_I18N_LANGS = dict(LANGS, uk="Ukrainian")
# glossary check tolerant to inflection (ru/uk decline the noun: "рабочему пространству")
_WS_RE = {"es": r"espacios? de trabajo", "de": r"arbeitsbereich", "fr": r"espaces? de travail", "it": r"aree? di lavoro|area di lavoro",
          "pt": r"espaços? de trabalho", "ru": r"рабоч\w*\s+пространств\w*", "uk": r"робоч\w*\s+прост\w*"}


def _icu(s: str, i: int = 0):
    """Tiny ICU MessageFormat reader: [(arg, type, {selector: nodes} | None) | ('#',)], stops at an unmatched '}'."""
    nodes = []
    while i < len(s):
        c = s[i]
        if c == "{":
            m = re.match(r"\{\s*(\w+)\s*(?:,\s*(\w+)\s*,?)?", s[i:])
            if not m:
                raise ValueError("bad argument")
            name, typ = m.group(1), m.group(2)
            j = i + m.end()
            if typ in ("plural", "select", "selectordinal"):
                br = {}
                while True:
                    m2 = re.match(r"\s*(=?\w+)\s*\{", s[j:])
                    if not m2:
                        break
                    sub, j = _icu(s, j + m2.end())
                    br[m2.group(1)] = sub
                    j += 1
                m3 = re.match(r"\s*\}", s[j:])
                if not m3:
                    raise ValueError("unclosed argument")
                nodes.append((name, typ, br))
                i = j + m3.end()
            else:
                k = s.find("}", j)
                if k < 0:
                    raise ValueError("unclosed argument")
                nodes.append((name, typ, None))
                i = k + 1
        elif c == "}":
            return nodes, i
        else:
            if c == "#":
                nodes.append(("#",))
            i += 1
    return nodes, i


def _sig(nodes) -> list:
    return sorted(str(n[:2]) if len(n) > 1 else "#" for n in nodes)


def _icu_ok(src: str, dst: str, lang: str) -> bool:
    try:
        a, ia = _icu(src)
        b, ib = _icu(dst)
    except (ValueError, IndexError):
        return False
    if ib != len(dst):
        return False

    def same(x, y, in_plural=False) -> bool:
        if _sig([n for n in x if n != ("#",)]) != _sig([n for n in y if n != ("#",)]):
            return False
        if in_plural and ("#",) in x and ("#",) not in y:   # the count may be added (ru "one" also covers 21), never dropped
            return False
        bx = {n[0]: n for n in x if len(n) == 3 and n[2] is not None}
        by = {n[0]: n for n in y if len(n) == 3 and n[2] is not None}
        for k, nx in bx.items():
            ny = by.get(k)
            if not ny:
                return False
            sx, sy = nx[2], ny[2]
            need = set(sx)
            if nx[1] == "plural" and lang in ("ru", "uk"):
                need |= {"one", "few", "many", "other"}
            if not need <= set(sy) or (nx[1] == "select" and set(sy) != set(sx)):
                return False
            for sel in sy:
                ref = sx.get(sel, sx.get("other"))
                # explicit =N branches name their number themselves; category branches must keep the count
                if ref is None or not same(ref, sy[sel], nx[1] == "plural" and not sel.startswith("=")):
                    return False
        return True
    return same(a, b)


def i18n(seed: int, level: int = 3) -> Item:
    """App localization: translate a JSON string table keeping placeholders, printf tokens, HTML tags and ICU plural /
    select structure (Russian plurals need one/few/many/other), with a glossary term that is also a placeholder name
    and a brand that must not be translated. Graded per key."""
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _i18n_x(seed, level)
    if level >= 6:   # v0.11: levels 6-8 (this kind stopped at level 5), below
        return _i18n_hard(seed, level)
    r = rng(BLOCK, f"i18n{level}", seed)
    lang = r.choice(["ru", "uk"]) if level >= 5 else r.choice(["es", "de", "fr", "it", "pt", "ru"])   # ru/uk plurals: one/few/many/other
    pool = [u for u in _UI if u[2] <= level]
    n = min(len(pool), [0, 5, 7, 10, 13, 16][level])
    must = [u for u in pool if u[2] == level]
    rest = [u for u in pool if u[2] != level]
    chosen = must[:n] + r.sample(rest, n - len(must[:n]))
    r.shuffle(chosen)
    src = {k: v for k, v, _ in chosen}
    notes = ["Keep every placeholder ({name}, %s, %1$s ...) and HTML tag exactly as in the source.",
             "Keep the brand name \"Nova Cloud\" untranslated."]
    if level >= 4:
        notes.append("Keep ICU MessageFormat syntax valid: translate only the text inside plural/select branches, keep the "
                     "argument names and select keywords, and use the plural categories the target language needs.")
        notes.append(f"Glossary: translate the word \"workspace\" as \"{_WS[lang]}\" (placeholder names stay unchanged).")
    prompt = (f"Translate the values of this app string table from English into {_I18N_LANGS[lang]}. Output only the JSON object "
              f"with the same keys.\n" + "\n".join(f"- {x}" for x in notes) + "\n\n" + json.dumps(src, ensure_ascii=False, indent=1))

    def check(text: str, _t=None) -> float:
        s = strip_think(text)
        m = re.search(r"\{.*\}", s, re.S)
        try:
            out = json.loads(m.group(0)) if m else None
        except ValueError:
            return 0.0
        if not isinstance(out, dict) or set(out) != set(src):
            return 0.0
        vals = [str(v) for v in out.values()]
        if detect_lang(" ".join(re.sub(r"\{[^{}]*\}", " ", v) for v in vals)) != ("ru" if lang == "uk" else lang):
            return 0.0
        good = 0
        for k, sv in src.items():
            v = out.get(k)
            if not isinstance(v, str):
                continue
            ok = sorted(re.findall(r"%(?:\d+\$)?[sd]", sv)) == sorted(re.findall(r"%(?:\d+\$)?[sd]", v))
            ok &= sorted(re.findall(r"</?\w+[^>]*>", sv)) == sorted(re.findall(r"</?\w+[^>]*>", v))
            ok &= _icu_ok(sv, v, lang)
            ok &= ("Nova Cloud" in v) == ("Nova Cloud" in sv)
            plain = re.sub(r"\{[^{}]*\}|<[^>]+>|%\S+", "", sv)
            if len(re.findall(r"[A-Za-z]{3,}", plain)) >= 2 and "Nova Cloud" not in sv:
                ok &= v.strip() != sv.strip()
            if level >= 4 and re.search(r"(?<!\{)\bworkspace\b(?!\})", sv):
                ok &= re.search(_WS_RE[lang], v.lower()) is not None
            good += bool(ok)
        return good / len(src)
    return Item(f"{BLOCK}.i18n.L{level}.{seed}", BLOCK, "i18n", [{"role": "user", "content": prompt}], check, lang=lang,
                meta={"level": level, "keys": list(src)})


# clean American-English paragraphs; {right|wrong} marks where an error can be injected (the right form is the original)
_PROOF = [
    "We are pleased to announce that the office move will be completed by the end of {the|teh} month. Each team will {receive|recieve} "
    "a floor plan and a list of {necessary|neccessary} equipment. Please {separate|seperate} personal items from company property and "
    "label every box with your name and department. {The|the} movers will arrive at eight in the morning, so make sure {your|you're} desk "
    "is cleared the evening before. If you have any questions, contact the facilities {committee|commitee}, {which|wich} meets every Tuesday.",
    "Our support team handled more tickets this quarter {than|then} in any quarter before. Most requests were about billing, and the "
    "average reply time {was|were} under two hours. The new help center {has|have} already reduced the number of {repeated|repeted} "
    "questions. We {definitely|definately} want to keep this momentum, {but|but but} we also need to hire two more agents before the "
    "holiday season. {It|it} would be a mistake to {lose|loose} the trust we have built with our customers.",
    "The city council approved the new cycling plan on Thursday. The plan adds forty kilometers of protected lanes and {its|it's} first "
    "phase starts in {February|Febuary}. {Residents|Residants} can comment on the proposed routes until the end of March. Several shop "
    "owners worry that the lanes will {affect|effect} deliveries, so the council {has|have} promised a loading zone on every block. "
    "{Officials|Oficials} expect the work to continue {until|untill} the summer of next year, weather permitting.",
    "Before you install the update, make a backup of your {data|date} and close all other programs. The installer checks your system "
    "and warns you if there {is|are} not enough free space. The whole process {usually|usualy} takes about ten minutes. Do not turn off "
    "the computer while {the|the the} progress bar is visible. After the restart, sign in with the same account {as|than} before, and your "
    "settings will be restored {automatically|automaticaly}.",
    "Thank you for choosing our cooking class. Please arrive ten minutes early so we can start on time. All {ingredients|ingrediants} and "
    "tools are provided, but you may bring your own knife if you prefer. {There|Their} is free parking behind the building. If you have "
    "an allergy, tell us at least two days in {advance|advanse} so we can adjust the menu. We {believe|beleive} that cooking together is "
    "the best way to {learn|learns}, and we look forward to seeing you {tomorrow|tommorow}.",
]


def proofread(seed: int, level: int = 3) -> Item:
    """Fix the injected spelling/grammar errors and change NOTHING else. Graded on the word sequence against the clean
    original: every unfixed error and every unrequested word edit costs one; score = 1 - differing words / errors."""
    if level >= 9:   # v0.11: levels 9-10, aimed at the frontier
        return _proofread_x(seed, level)
    if level >= 6:   # v0.11: levels 6-8 (this kind stopped at level 5), below
        return _proofread_hard(seed, level)
    r = rng(BLOCK, f"proofread{level}", seed)
    paras = r.sample(_PROOF, 1 + (level + 1) // 2)
    slots = [(pi, m) for pi, p in enumerate(paras) for m in re.finditer(r"\{([^|}]*)\|([^}]*)\}", p)]
    n_err = min(len(slots), [0, 3, 5, 7, 10, 13][level])
    bad = set((pi, m.start()) for pi, m in r.sample(slots, n_err))

    def render(pi: int, p: str, inject: bool) -> str:
        return re.sub(r"\{([^|}]*)\|([^}]*)\}", lambda m: m.group(2) if inject and (pi, m.start()) in bad else m.group(1), p)
    clean = "\n\n".join(render(i, p, False) for i, p in enumerate(paras))
    dirty = "\n\n".join(render(i, p, True) for i, p in enumerate(paras))
    prompt = ("Proofread this text. Fix only spelling, grammar and capitalization errors; do not rephrase, reorder or change "
              "anything else, and keep the paragraphs. Output only the corrected text.\n\n" + dirty)

    def toks(t: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9']+", (t or "").replace("’", "'"))

    def check(text: str, _t=None) -> float:
        a, b = toks(clean), toks(strip_think(text))
        diff = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
                   if op != "equal")
        return max(0.0, 1 - diff / n_err)
    return Item(f"{BLOCK}.proofread.L{level}.{seed}", BLOCK, "proofread", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "errors": n_err})


# ---- v0.11: levels 7-8 (and 6 for the kinds that stopped at 5) ------------------------------------------------------------
# Levels 1-6 topped out (frontier ~1.0, strong local models ~0.9). The hard levels do not add length; they add rules that
# INTERACT: pairs of rules that contradict each other with one stated resolution (the later rule wins, only as far as
# the conflict goes), facts that must be derived (a date two working days after a Friday, a refund in a fixed format,
# a price 10% lower), authority rules (only the lead can move the deadline), values that must NOT appear (superseded or
# rejected ones), and in-text instructions. Every rule is still one checkable constraint; the score stays the share met.

_CONFLICT = ("Some of these rules contradict each other. When two rules conflict, the rule that comes LATER in the list wins, "
             "and only as far as the conflict goes: follow every other rule, and every other part of a rule, fully.")
_LETTERS = {"en": "BCDFHLMPRSTW", "es": "BCDLMPST", "de": "BDFGHKLMSTW", "fr": "BCDLMPST", "it": "BCDFLMPST", "pt": "BCDFLMPST",
            "ru": "БВДКМНПСТ"}


def _count_str(t: str, k: str) -> int:
    return len(re.findall(rf"(?<![\w/]){re.escape(k)}(?![\w/])", t))


def _insert_pair(r, rules: list, first, second, anchor: int | None = None) -> bool:
    """Insert two conflicting rules at random places in `rules`, in random order; True when `second` ends up later.
    With `anchor`, `first` conflicts with the rule already at that index: it goes before or after it."""
    later = r.random() < 0.5
    if anchor is not None:
        pos = r.randint(anchor + 1, len(rules)) if later else r.randint(0, anchor)
        rules.insert(pos, first)
        return later
    a, b = sorted(r.sample(range(len(rules) + 2), 2))
    x, y = (first, second) if later else (second, first)
    rules.insert(a, x)
    rules.insert(b, y)
    return later


def _constrained_hard(seed: int, level: int) -> Item:
    """Promotional text under 12 (L7) / 15 (L8) interacting rules: exact paragraph and per-paragraph sentence counts, a
    narrow word window, strings exactly once, paragraph initials, a sentence length cap, and pairs of contradicting rules
    whose order (random per seed) decides the resolution: no digits vs required strings with digits, a final question
    mark vs periods everywhere, and (L8) a name in every paragraph vs not in the second one, plus an exact sentence length."""
    r = rng(BLOCK, f"constrained{level}", seed)
    lang = r.choice(list(LANGS))
    topic = r.choice(TOPICS)
    n = r.choice([3, 4])
    sents = [r.choice([2, 3]) for _ in range(n)]
    cap, width = (18, 20) if level == 7 else (15, 14)
    lo = 5 * round(sum(sents) * r.choice([8, 9, 10]) / 5)
    hi = lo + width
    letters = r.sample(_LETTERS[lang], n)
    kws = r.sample(["QR", "Madrid"] + (["Nova"] if level == 7 else []), 1) + r.sample(["48", "2026", "24/7", "3x"], 2)
    dig = kws[1:]   # the strings with digits
    banned = BANNED[lang]
    first_len = r.randint(7, 11)

    def body(t):
        return _paragraphs(t)[1:]

    def body_sents(t):
        return [s for p in body(t) for s in _sentences(p)]

    def title_ok(t):
        ps = _paragraphs(t)
        return bool(ps) and len(ps[0].splitlines()) == 1 and ps[0] == ps[0].upper() and any(c.isalpha() for c in ps[0])

    # the tests read the resolution of each conflict (known once the pair is placed) when they run
    def kw_ok(t):
        return all(_count_str(t, k) == (0 if no_digits_wins and k in dig else 1) for k in kws)

    def no_digit_ok(t):
        rest = t
        for k in ([] if no_digits_wins else dig):
            rest = re.sub(rf"(?<![\w/]){re.escape(k)}(?![\w/])", " ", rest)
        return re.search(r"\d", rest) is None

    def periods_ok(t):
        ss = body_sents(t)
        return len(ss) >= 2 and all(s.rstrip().endswith(".") for s in ss[:-1])

    def last_ok(t):
        ss = body_sents(t)
        return bool(ss) and ss[-1].rstrip().endswith("?" if q_wins else ".")
    rules = [("start with a title line written entirely in UPPERCASE; the title is a paragraph of its own (a single line)", title_ok),
             (f"after the title, exactly {n} paragraphs, separated by one blank line", lambda t: len(body(t)) == n),
             ("the paragraphs after the title have exactly " + ", ".join(str(x) for x in sents) + " sentences, in this order",
              lambda t: [len(_sentences(p)) for p in body(t)] == sents),
             (f"between {lo} and {hi} words in total, the title included", lambda t: lo <= len(words(t)) <= hi),
             ("include each of these exact strings exactly once: " + ", ".join(f'"{k}"' for k in kws), kw_ok),
             (f'the string "{kws[0]}" appears in the last paragraph', lambda t: bool(body(t)) and _count_str(body(t)[-1], kws[0]) >= 1),
             (f'never use the word "{banned}"', lambda t: re.search(rf"(?<!\w){re.escape(banned)}(?!\w)", t, re.I) is None),
             ("the paragraphs after the title start with words beginning with the letters " + ", ".join(letters) + ", in this order",
              lambda t: [p.strip()[:1].upper() for p in body(t)] == letters),
             (f"no sentence is longer than {cap} words", lambda t: bool(body_sents(t)) and all(len(words(s)) <= cap for s in body_sents(t)))]
    no_digits_wins = _insert_pair(r, rules, ("do not use any digits anywhere in the text", no_digit_ok), None, anchor=4)
    q_wins = _insert_pair(r, rules, ("end every sentence after the title with a period", periods_ok),
                          ("the text ends with a question mark", last_ok))
    if level >= 8:
        not2_wins = _insert_pair(
            r, rules, ('mention the name "Nova" in every paragraph after the title',
                       lambda t: len(body(t)) >= 2 and all(_count_str(p, "Nova") >= 1 for k, p in enumerate(body(t)) if k != 1)),
            ('the second paragraph after the title must not contain the name "Nova"',
             lambda t: len(body(t)) >= 2 and (_count_str(body(t)[1], "Nova") == 0) == not2_wins))
        rules.insert(r.randint(0, len(rules)), (f"the first sentence after the title has exactly {first_len} words",
                                                lambda t: bool(body_sents(t)) and len(words(body_sents(t)[0])) == first_len))
    tests = [f for _, f in rules] + [lambda t: detect_lang(t) == lang]
    prompt = (f"Write a short promotional text about {topic} in {LANGS[lang]}.\n{_CONFLICT}\nRules:\n"
              + "\n".join(f"{i + 1}. {x}" for i, (x, _f) in enumerate(rules)) + "\nOutput only the text.")

    def check(text: str, _t=None) -> float:
        t = strip_think(text)
        return _share([f(t) for f in tests], detect_lang(t) == lang and len(words(t)) >= lo // 2)
    return Item(f"{BLOCK}.constrained.L{level}.{seed}", BLOCK, "constrained", [{"role": "user", "content": prompt}], check,
                lang=lang, meta={"level": level, "rules": len(tests), "no_digits_wins": no_digits_wins, "question_wins": q_wins})


_NUM_WORDS = {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def _rewrite_hard(seed: int, level: int) -> Item:
    """Rewrite an informal support message as a formal one with derived facts: the salutation from the customer line, the
    refund as 'EUR x.xx', the shipping day as an absolute date (two working days after this Friday; at L8 the Monday is a
    public holiday), and contradicting rule pairs in random order (one check each): keep the reference numbers vs never
    mention internal ticket numbers; (L8) name the supplier vs name no other company, the delay in digits vs numbers below
    ten in words."""
    r = rng(BLOCK, f"rewrite{level}", seed)
    holiday = None
    for _ in range(100):
        sent = _dt.date(2026, 9, 1) + _dt.timedelta(days=r.randint(0, 110))
        if sent.weekday() > 3:
            continue
        friday = sent + _dt.timedelta(days=4 - sent.weekday())
        holiday = friday + _dt.timedelta(days=3) if level >= 8 else None
        ship, k = friday, 2
        while k:
            ship += _dt.timedelta(days=1)
            if ship.weekday() < 5 and ship != holiday:
                k -= 1
        if level < 8 or ship.day >= 10:   # at L8 the only number below ten is the delay (see the digits / words pair)
            break
    first = r.choice(FIRST)
    female = first in ("Lucía", "Hanna", "Olga", "Inés", "Mei")
    title = r.choice(["Dr.", "Prof.", "Ms." if female else "Mr."])
    last = r.choice([x for x in LAST if female or x != "Ivanova"])
    order, ticket = r.randint(10000, 99999), f"{r.choice(['ZX', 'QT', 'HD'])}-{r.randint(1000, 9999)}"
    n1 = r.randint(3, 9) if level >= 8 else r.randint(3, 19)
    n2 = r.randint(10, 30)
    amount = r.choice([12.5, 14, 18.5, 22, 24.9, 31, 16.4])
    supplier = r.choice(["Kestrel Parts", "Marlow Logistics", "Vantor Components", "Ibex Supply"])
    src = (f"Customer: {title} {first} {last}\nSent: {sent:%A}, {sent.day} {sent:%B} {sent.year}\n"
           + (f"Note: Monday {holiday.day} {holiday:%B} is a public holiday here, nobody works that day.\n" if holiday else "")
           + f"\nMessage:\nhey {first}!! so we can't ship order #{order} this week, sorry :( our supplier {supplier} messed up and "
             f"we're like {n1} days behind. the parts get to us this Friday and we'll ship two working days after that. we'll give "
             f"you {n2}% off your next order and refund {amount:g} bucks for the express shipping you paid. (internal ref {ticket}, "
             f"don't worry about it) it won't happen again!!")
    n_sent = r.choice([4, 5])
    limit = 95 if level == 7 else 85
    salute = f"Dear {title} {last},"
    ship_s = f"{ship.day} {ship:%B} {ship.year}"
    refund = f"EUR {amount:.2f}"
    ticket_digits = ticket.split("-")[1]

    def bod(t):
        return "\n".join(t.strip().splitlines()[1:]).strip()
    rules = [("a formal, polite business tone", None),
             (f'the first line is exactly "{salute}" (title and last name from the Customer line); the rest is the body',
              lambda t: (t.strip().splitlines() or [""])[0].strip() == salute),
             (f"the body has exactly {n_sent} sentences", lambda t: len(_sentences(bod(t))) == n_sent),
             (f"the body has at most {limit} words", lambda t: len(words(bod(t))) <= limit),
             ("keep the order number and the discount percentage",
              lambda t: str(order) in t and re.search(rf"\b{n2}\s?(%|percent\b|per cent\b)", t) is not None),
             ('state the refund exactly in the form "EUR 0.00" (currency code, a space, two decimals)', lambda t: refund in t),
             ('replace the relative shipping day with the absolute date in the form "14 October 2026"', lambda t: ship_s in t),
             ("no contractions (like can't, we'll)", lambda t: re.search(r"\b(\w+n't|\w+'(ll|re|ve|d|m)|(it|that|let|what|here|there|he|she|who)'s)\b",
                                                                         t.replace("’", "'"), re.I) is None),
             ("no exclamation marks", lambda t: "!" not in t), ("no emoticons", lambda t: ":(" not in t and ":)" not in t),
             ('do not use the words "sorry", "apologize", "unfortunately", "problem" or "issue"',
              lambda t: re.search(r"\b(sorry|apologi[sz]e\w*|unfortunately|problems?|issues?)\b", t, re.I) is None),
             ("end with the last sentence of the body: no closing formula, no signature", lambda t: t.strip().endswith("."))]
    # conflicting pairs: the first rule of a pair carries the one check of how the conflict was resolved
    no_ticket_wins = _insert_pair(r, rules, ("keep every reference number from the original message (the order number and the "
                                             "internal ticket number)", lambda t: (ticket_digits in t) != no_ticket_wins),
                                  ("never mention internal ticket or reference numbers", None))
    if level >= 8:
        no_company_wins = _insert_pair(r, rules, (f"name the supplier ({supplier}) as the cause of the delay",
                                                  lambda t: (supplier.split()[0] in t) != no_company_wins),
                                       ("do not name any company other than ours", None))

        def delay_form(t):
            as_word = re.search(rf"\b{_NUM_WORDS[n1]}\b", bod(t), re.I) is not None
            as_digit = re.search(rf"(?<![\w.,]){n1}(?![\w.,%])", bod(t)) is not None
            return (as_word and not as_digit) if words_win else (as_digit and not as_word)
        words_win = _insert_pair(r, rules, ("write the length of the delay in digits", delay_form),
                                 ("write every number below ten in words", None))
    tests = [f for _, f in rules if f is not None]
    prompt = ("Rewrite this customer message for the customer named in it.\n" + _CONFLICT + "\nRules:\n"
              + "\n".join(f"{i + 1}. {x}" for i, (x, _f) in enumerate(rules)) + "\nOutput only the rewritten text.\n\n" + src)

    def check(text: str, _t=None) -> float:
        t = strip_think(text)
        return _share([f(t) for f in tests], bool(t) and str(order) in t and len(words(t)) >= 20)
    return Item(f"{BLOCK}.rewrite.L{level}.{seed}", BLOCK, "rewrite", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "rules": len(tests), "ship": ship_s, "refund": refund, "no_ticket_wins": no_ticket_wins})


_MONTH_RE = {"en": ["octob", "novemb", "decemb"], "es": ["octubr", "noviembr", "diciembr"], "de": ["oktob", "novemb", "dezemb"],
             "fr": ["octobr", "novembr", "d[ée]cembr"], "ru": ["октябр", "ноябр", "декабр"], "it": ["ottobr", "novembr", "dicembr"],
             "pt": ["outubr", "novembr", "dezembr"]}
_MONTHS3 = ["October", "November", "December"]


def _summarize_hard(seed: int, level: int) -> Item:
    """An email thread with authority rules: only the lead moves the deadline or the owner, only finance sets the budget.
    Unauthorized proposals and offers change nothing (L8: until the lead accepts one, by name), a conditional budget
    depends on the weekday the client signed, the lead reverts or moves the deadline; L8 adds a budget cut to compute.
    The summary must state the final values and must NOT contain superseded or rejected dates / amounts or anyone's name
    other than the final owner."""
    r = rng(BLOCK, f"summarize{level}", seed)
    lang = r.choice(list(LANGS))
    ppl = [f"{f} {l}" for f, l in zip(r.sample(FIRST, 6), r.sample(LAST, 6))]
    lead, fin, others = ppl[0], ppl[1], ppl[2:]
    fn = {p: p.split()[0] for p in ppl}
    roles = dict(zip(others, r.sample(["designer", "backend developer", "QA engineer", "account manager", "data analyst"], 4)))
    roles.update({lead: "project lead", fin: "finance"})
    # six deadlines with distinct day numbers, in story order: the kick-off date first, moves for "more time" later than it,
    # and the last unaccepted push (L8) later than the accepted proposal
    pool = sorted(((d, r.choice(_MONTHS3)) for d in r.sample([d for d in range(3, 28) if d not in (10, 11, 12)], 6)),
                  key=lambda x: (_MONTHS3.index(x[1]), x[0]))
    rest = pool[1:]
    r.shuffle(rest)
    d4, d5 = sorted(rest[3:5], key=lambda x: (_MONTHS3.index(x[1]), x[0]))
    dates = [pool[0], rest[0], rest[1], rest[2], d4, d5]
    ds = [f"{d} {m}" for d, m in dates]
    b0, b1 = sorted(r.sample([41500, 46000, 52500, 57000, 63500, 68000, 74500, 79000], 2))
    owner0, owner1, volunteer, proposer = r.sample(others, 4)
    msgs = []

    def say(p, text):
        msgs.append(f"From: {p} ({roles[p]})\n{text}")
    say(lead, f"Kick-off for the Atlas dashboard: the deadline is {ds[0]}, and {owner0} owns the rollout.")
    say(fin, f"Finance approves a budget of {b0:,} EUR for this phase.")
    say(proposer, f"Could we move the deadline to {ds[1]}? My team would appreciate the extra days.")
    say(lead, f"The client needs more time, so the deadline moves to {ds[2]}.")
    say(fin, f"If the client signs the change request by Thursday, the budget rises to {b1:,} EUR.")
    say(volunteer, f"I can take over the rollout from {fn[owner0]} if that helps.")
    in_time = r.random() < 0.5
    say(fin, f"The client signed the change request on {'Wednesday' if in_time else 'Friday'}.")
    budget = b1 if in_time else b0
    if r.random() < 0.5:
        say(lead, f"{fn[owner0]} is on leave next month, so {owner1} takes over the rollout.")
        owner = owner1
    else:
        say(lead, f"Thanks {fn[volunteer]}, but the rollout stays with {fn[owner0]}.")
        owner = owner0
    if r.random() < 0.5:
        say(lead, f"Correction: forget the move to {ds[2]}; the deadline stays {ds[0]}.")
        deadline = dates[0]
    else:
        say(lead, f"One more change from the client: the deadline is now {ds[3]}.")
        deadline = dates[3]
    used_days = [dates[k][0] for k in range(4)]
    if level >= 8:
        second = owner1 if owner == owner0 else owner0   # someone who has not proposed a date before
        say(second, f"What about {ds[4]} as the deadline? Then QA gets a full week.")
        say(lead, f"{fn[second]}'s suggestion works for me, let's do that.")
        deadline = dates[4]
        say(volunteer, f"Could we even push it to {ds[5]}?")
        say(fin, "Because of the spending freeze, the budget is reduced by 5,000 EUR.")
        budget -= 5000
        say(lead, f"On second thought, {fn[volunteer]} takes the rollout after all.")
        owner = volunteer
        used_days += [dates[4][0], dates[5][0]]
    say(lead, "Thanks all - please confirm the final plan by Friday.")
    n_b = r.choice([3, 4]) if level >= 8 else r.randint(3, 5)
    w_max = 14 if level >= 8 else 16
    stale_days = [d for d in used_days if d != deadline[0]]
    stale_budgets = [b for b in (b0, b1) if b != budget]
    prompt = (f"Summarize this email thread in {LANGS[lang]} as exactly {n_b} bullet points starting with \"- \".\n"
              f"Authority: only {lead} (project lead) can set or change the deadline and the rollout owner; only {fin} (finance) "
              f"can set or change the budget. Anything proposed or offered by someone else changes nothing unless the person with "
              f"that authority accepts it.\nRules:\n- each bullet has at most {w_max} words\n"
              f"- state the FINAL deadline, the FINAL budget and who FINALLY owns the rollout\n"
              f"- mention only final values: no superseded, rejected or unaccepted dates or amounts\n"
              f"- do not name any person other than the final rollout owner; keep that name in its original spelling\n"
              + ("- bullet 1 gives the deadline, bullet 2 the budget, bullet 3 the rollout owner\n" if level >= 8 else "")
              + "\n" + "\n\n".join(msgs))
    surname = {p: p.split()[1] for p in ppl}

    def has_date(t):
        return re.search(rf"(?<!\d){deadline[0]}(?!\d)", t) is not None and (
            re.search(_MONTH_RE[lang][_MONTHS3.index(deadline[1])], t, re.I) is not None
            or re.search(rf"(?<!\d)0?{deadline[0]}[./-]{10 + _MONTHS3.index(deadline[1])}(?!\d)|(?<!\d){10 + _MONTHS3.index(deadline[1])}[./-]0?{deadline[0]}(?!\d)", t) is not None)

    def check(text: str, _t=None) -> float:
        t = strip_think(text).replace(" ", " ").replace("\xa0", " ")
        bullets = [l.strip()[2:] for l in t.splitlines() if l.strip().startswith(("- ", "* ", "• "))]
        tests = [len(bullets) == n_b, bool(bullets) and all(len(words(b)) <= w_max for b in bullets), has_date(t),
                 _has_num(t, budget), re.search(rf"\b{re.escape(surname[owner])}\b", t) is not None,
                 all(re.search(rf"(?<![\d.,]){d}(?!\d|[.,]\d)", t) is None for d in stale_days),
                 not any(_has_num(t, b) for b in stale_budgets),
                 not any(re.search(rf"\b{re.escape(surname[p])}\b", t) for p in ppl if p != owner), detect_lang(t) == lang]
        if level >= 8:
            tests.append(len(bullets) >= 3 and has_date(bullets[0]) and _has_num(bullets[1], budget) and surname[owner] in bullets[2])
        return _share(tests, bool(bullets) and detect_lang(t) == lang)
    return Item(f"{BLOCK}.summarize.L{level}.{seed}", BLOCK, "summarize", [{"role": "user", "content": prompt}], check, lang=lang,
                meta={"level": level, "expected": f"{deadline[0]} {deadline[1]} / {budget} / {owner}"})


def _extract_hard(seed: int, level: int) -> Item:
    """Order lines with edits that interact: a quantity in dozens (L8 also in boxes), a price 10% lower than written, a
    mistyped SKU corrected, a line cancelled (L8: and put back with a new quantity, keeping its first-mention place), a
    price in European notation (L8), 'everything express except ...' as the last word. Credit per order line that is right
    in every field, plus one for the order of the lines; extra lines cost."""
    r = rng(BLOCK, f"extract{level}", seed)
    name, city = f"{r.choice(FIRST)} {r.choice(LAST)}", r.choice(["Valencia", "Porto", "Lyon", "Graz", "Tartu", "Bilbao"])
    n = 5 if level == 7 else 6
    skus = set()
    lines = []
    while len(lines) < n:
        sku = f"{r.choice('ABCDEFGH')}{r.randint(100, 999)}-{r.choice('XYZ')}"
        if sku[:4] in {s[:4] for s in skus}:
            continue
        skus.add(sku)
        lines.append({"sku": sku, "quantity": r.randint(3, 40), "unit_price": r.randint(30, 2500) * 10 / 100, "express": r.random() < 0.5})
    a_more, b_cheaper, c_cancel, d_typo, e_standard = r.sample(range(n), 5)
    dozen = r.choice([k for k in range(n) if k != a_more])
    boxes = r.choice([k for k in range(n) if k not in (a_more, dozen)]) if level >= 8 else None
    euro = r.choice([k for k in range(n) if k != b_cheaper]) if level >= 8 else None
    lines[dozen]["quantity"] = r.choice([12, 24, 36, 6])
    if boxes is not None:
        lines[boxes]["quantity"] = 12 * r.randint(2, 5)
    typo = lines[d_typo]["sku"][:-1] + r.choice([x for x in "XYZ" if x != lines[d_typo]["sku"][-1]])
    words_q = {6: "half a dozen", 12: "a dozen", 24: "two dozen", 36: "three dozen"}
    parts = [f"Hello, this is {name} from our {city} office. Here is our order:"]
    for k, o in enumerate(lines):
        qty = words_q[o["quantity"]] if k == dozen else f"{o['quantity'] // 12} boxes of 12" if k == boxes else str(o["quantity"])
        price = (f"{int(o['unit_price']):,}".replace(",", ".") + f",{round(o['unit_price'] * 100) % 100:02d} EUR") if k == euro \
            else f"{o['unit_price']:.2f}"
        sku = typo if k == d_typo else o["sku"]
        parts.append(f"- {qty} units of {sku} at {price} per unit, " + ("express shipping." if o["express"] else "standard shipping."))
    more = r.randint(2, 9)
    edits = [f"For {lines[a_more]['sku']}, add {more} more units.",
             f"For {lines[b_cheaper]['sku']} the agreed price is 10% lower than what I wrote.",
             f"Please cancel the {lines[c_cancel]['sku']} line completely.",
             f"I mistyped a code: {typo} should be {lines[d_typo]['sku']}."]
    r.shuffle(edits)
    lines[a_more]["quantity"] += more
    lines[b_cheaper]["unit_price"] = round(lines[b_cheaper]["unit_price"] * 0.9, 2)
    final = [dict(o) for k, o in enumerate(lines) if k != c_cancel]
    if level >= 8:
        back = r.randint(2, 15)
        edits.append(f"Actually, put the {lines[c_cancel]['sku']} line back after all, but only {back} units.")
        lines[c_cancel]["quantity"] = back
        final = [dict(o) for o in lines]
    edits.append(f"And please make everything express except the {lines[e_standard]['sku']} line, which stays standard.")
    for o in final:
        o["express"] = o["sku"] != lines[e_standard]["sku"]
    msg = "\n".join(parts) + "\n\n" + " ".join(["A few changes:"] + edits + ["Thanks!"])
    schema = '[{"sku": string, "quantity": integer (units), "unit_price": number (EUR), "express": boolean}]'
    prompt = (f"Extract the FINAL order lines from this message as a JSON array matching {schema}, one object per line, in the "
              f"order the lines were first mentioned. Output only the JSON array.\n\nMessage:\n{msg}")

    def check(t: str, _t=None) -> float:
        s = strip_think(t)
        m = re.search(r"\[.*\]", s, re.S)
        try:
            arr = json.loads(m.group(0)) if m else None
        except ValueError:
            return 0.0
        if not isinstance(arr, list) or not arr:
            return 0.0
        exp = {o["sku"]: o for o in final}
        good, extra, seen, order = 0, 0, set(), []
        for g in arr:
            sku = g.get("sku") if isinstance(g, dict) else None
            if sku not in exp or sku in seen:
                extra += 1
                continue
            seen.add(sku)
            order.append(sku)
            o = exp[sku]
            good += (g.get("quantity") == o["quantity"] and not isinstance(g.get("quantity"), bool)
                     and isinstance(g.get("unit_price"), (int, float)) and abs(g["unit_price"] - o["unit_price"]) < 0.005
                     and g.get("express") is o["express"])
        in_order = order == [o["sku"] for o in final if o["sku"] in seen] and len(order) >= 2
        return (good + in_order) / (len(final) + 1 + extra)
    return Item(f"{BLOCK}.extract.L{level}.{seed}", BLOCK, "extract", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "expected": final})


_GLOSS = {"warehouse": {"es": ("almacén", r"almac[eé]n"), "de": ("Lager", r"lager"), "fr": ("entrepôt", r"entrep[oô]t"),
                        "ru": ("склад", r"склад"), "it": ("magazzino", r"magazzin"), "pt": ("armazém", r"armaz[eé]")},
          "parcel": {"es": ("paquete", r"paquete"), "de": ("Paket", r"paket"), "fr": ("colis", r"colis"), "ru": ("посылка", r"посылк"),
                     "it": ("pacco", r"pacc[oh]"), "pt": ("encomenda", r"encomenda")}}


def _translate_hard(seed: int, level: int) -> Item:
    """Translate a bullet list that carries instructions for the translator: NOTE lines that drop a bullet and replace an
    outdated number (L8: also convert miles to kilometres), a decimal comma, a two-term glossary, an app name and people
    kept as they are. The NOTE lines themselves must not appear in the output."""
    r = rng(BLOCK, f"translate{level}", seed)
    target = r.choice([k for k in LANGS if k != "en"])
    name, comp = f"{r.choice(FIRST)} {r.choice(LAST)}", r.choice(COMPANIES)
    n0, n1, n3, n4 = r.randint(12, 95), r.randint(1200, 9800), r.randint(2, 9), r.randint(100, 999)
    dec = r.randint(15, 95) / 10
    while dec == int(dec):
        dec = r.randint(15, 95) / 10
    new1 = n1 + r.choice([-1, 1]) * r.randint(300, 900)
    miles = r.choice([m for m in (10, 15, 20, 25, 30, 35, 40) if m not in (n0, n4) and round(m * 1.6) not in (n0, n4)])
    km = round(miles * 1.6)
    sents = [(f"{name}, the operations lead at {comp}, confirmed that the new warehouse will open on 14 March with {n0} employees.", None),
             (f"The company expects to ship {n1} parcels per day from the new warehouse during the first quarter.",
              f"NOTE: the number of parcels in the next bullet is outdated; use {new1} instead."),
             (f"Delivery times should drop by {dec} percent compared with last year.", None),
             ('Customers can track every parcel in the "Nova Track" app, and the support team answers questions seven days a week.', None),
             (f"A pilot with {n3} partner shops starts next month; each shop gets a starter kit worth {n4} euros.",
              "NOTE: leave out the next bullet, the pilot is not public yet."),
             (f"The warehouse is {miles} miles from the city centre, so most parcels arrive the next morning.",
              "NOTE: give the distance in the next bullet in kilometres (1 mile = 1.6 km), rounded to a whole number."),
             ("If the pilot works, the programme will expand to three more regions before the end of the year.", None)]
    if level < 8:
        sents[5] = (sents[5][0], None)
    src_lines = []
    for s, note in sents:
        if note:
            src_lines.append(note)
        src_lines.append(f"- {s}")
    src = "\n".join(src_lines)
    plain_len = sum(len(s) for s, _ in sents)
    g1, g2 = _GLOSS["warehouse"][target], _GLOSS["parcel"][target]
    prompt = (f"Translate the text into {LANGS[target]}.\n"
              f"- Keep names, the company name and the app name \"Nova Track\" unchanged, and numbers too unless a NOTE says otherwise.\n"
              f"- Keep the Markdown bullet list: one bullet per translated sentence, in the same order.\n"
              f"- Lines starting with NOTE: are instructions for you: follow them, and do not translate or output them.\n"
              f"- Write decimal numbers with the decimal comma of {LANGS[target]} (for example 2,5).\n"
              f"- Glossary: translate \"warehouse\" as \"{g1[0]}\" and \"parcel\" as \"{g2[0]}\" (inflected as the grammar needs).\n"
              f"Output only the translation.\n\n{src}")
    dec_s = f"{dec}"

    def has(t, n_):
        return re.search(rf"(?<![\d.,]){re.escape(str(n_))}(?![\d]|[.,]\d)", t) is not None

    def check(text: str, _t=None) -> float:
        t = strip_think(text)
        low = t.lower()
        bullets = [l for l in t.splitlines() if l.strip().startswith(("- ", "* "))]
        tests = [detect_lang(t) == target, comp in t, name.split()[1] in t, "Nova Track" in t, has(t, n0) and (level >= 8 or has(t, miles)),
                 dec_s.replace(".", ",") in t and dec_s not in t, re.search(g1[1], low) is not None, re.search(g2[1], low) is not None,
                 not has(t, n3) and not has(t, n4), _has_num(t, new1) and not _has_num(t, n1),
                 len(bullets) == len(sents) - 1, all(l.strip().startswith(("- ", "* ")) for l in t.splitlines() if l.strip())]
        if level >= 8:
            tests.append(has(t, km) and not has(t, miles))
        return _share(tests, detect_lang(t) == target and 0.3 <= len(t) / plain_len <= 3)
    return Item(f"{BLOCK}.translate.L{level}.{seed}", BLOCK, "translate", [{"role": "user", "content": prompt}], check,
                lang=target, meta={"level": level})


def _workdays_after(d: _dt.date, n: int) -> _dt.date:
    while n:
        d += _dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _minutes_hard(seed: int, level: int) -> Item:
    """Levels 6-8 of minutes: an authority rule (only the chair drops or revives an item; a suggestion by someone else
    changes nothing unless the chair agrees), deadlines pushed back by a week or set to the same day as another item, owners
    who swap items (L7+), a deadline that depends on whether legal signs off (L7+), a deadline two working days after
    another item's (L8), on top of the level-5 owner changes, moved / relative deadlines, declined offers and revivals."""
    r = rng(BLOCK, f"minutes{level}", seed)
    day = _dt.date(2026, 10, 1) + _dt.timedelta(days=r.randint(0, 60))
    while day.weekday() not in (1, 2):
        day += _dt.timedelta(days=1)
    people = r.sample(_MEET_PEOPLE, 6)
    first = {p[0]: p[0].split()[0] for p in people}
    role = dict(people)
    names = [p[0] for p in people]
    topics = r.sample(_MEET_TOPICS, {6: 10, 7: 11, 8: 12}[level])
    dues = _due_phrases(day, 5)
    chair = names[0]
    legal = next((n for n in names if role[n] == "legal counsel"), None)

    def ref(person: str) -> str:
        return "I" if person == chair else f"our {role[person]}" if r.random() < 0.4 else first[person]

    def cap(x: str) -> str:
        return x[0].upper() + x[1:]
    fill = [f for f in _FILLER if f != "Noted, thanks."]   # a chair's "noted" after a suggestion would read as agreement
    state, frozen = {}, set()
    lines = [f"Attendees: " + ", ".join(f"{n} ({ro})" for n, ro in people), f"Date: {day:%A %Y-%m-%d}", ""]
    for t in topics:
        owner = r.choice(names)
        phrase, due = r.choice(dues)
        state[t] = {"owner": owner, "due": due, "active": True}
        who = ref(owner)
        lines.append(f"{first[chair]}: Next item, the {t}. " + ("I will own it myself" if who == "I" else f"{cap(who)} will own it")
                     + f", due {phrase}.")
        if r.random() < 0.3:
            lines.append(f"{first[r.choice(names)]}: {r.choice(fill)}")
    kinds = ["owner", "due", "drop", "decline", "revive", "push", "same_as", "suggest_drop", "suggest_drop_ok"]
    if level >= 7:
        kinds += ["swap", "cond"]
    if level >= 8:
        kinds += ["after", "suggest_revive"]
    # every level-6+ item uses each change kind at least once (a fixed mix; a seed changes topics, people and dates)
    plan = kinds + [r.choice(kinds) for _ in range({6: 2, 7: 2, 8: 2}[level])]
    r.shuffle(plan)
    for k in ("revive", "suggest_revive"):   # something has to be dropped first
        while k in plan and plan.index(k) < min((plan.index(d) for d in ("drop", "suggest_drop_ok") if d in plan), default=0):
            plan.remove(k)
            plan.append(k)
    for kind in plan:
        for _ in range(60):
            t = r.choice(topics)
            s = state[t]
            others = [p for p in names if p != chair]
            if kind == "owner" and s["active"]:
                new = r.choice([p for p in names if p != s["owner"]])
                lines.append(f"{first[s['owner']]}: I'm swamped this month - could someone else take the {t}?")
                lines.append(f"{first[new]}: I can take the {t}, same deadline.")
                s["owner"] = new
            elif kind == "due" and s["active"] and t not in frozen:
                phrase, due = r.choice([d for d in dues if d[1] != s["due"]])
                lines.append(f"{first[r.choice(names)]}: For the {t}, let's move the deadline to {phrase}.")
                lines.append(f"{first[chair]}: Agreed, {phrase} for the {t}.")
                s["due"] = due
            elif kind == "push" and s["active"] and t not in frozen:
                asker = s["owner"] if s["owner"] != chair else r.choice(others)
                lines.append(f"{first[asker]}: The {t} needs more time - can we push it back by one week?")
                lines.append(f"{first[chair]}: Fine, one more week for the {t}.")
                s["due"] += _dt.timedelta(days=7)
            elif kind in ("same_as", "after") and s["active"] and t not in frozen:
                t2 = r.choice([x for x in topics if x != t and state[x]["active"]])
                frozen.add(t2)   # its deadline does not move after this, so the reference stays unambiguous
                if kind == "same_as":
                    lines.append(f"{first[chair]}: Let's make the {t} due the same day as the {t2}.")
                    s["due"] = state[t2]["due"]
                else:
                    lines.append(f"{first[chair]}: The {t} can only start once the {t2} is done, so the {t} is due two working "
                                 f"days after the {t2} deadline.")
                    s["due"] = _workdays_after(state[t2]["due"], 2)
                frozen.add(t)
            elif kind == "decline" and s["active"]:
                other = r.choice([p for p in names if p not in (s["owner"], chair)])
                lines.append(f"{first[other]}: I could also take the {t} if that helps.")
                lines.append(f"{first[chair]}: Thanks, but let's keep the {t} with " + ("me" if s["owner"] == chair else first[s["owner"]]) + ".")
            elif kind == "drop" and s["active"] and t not in frozen:
                lines.append(f"{first[chair]}: Let's drop the {t} for now, it is not a priority this quarter.")
                s["active"] = False
            elif kind == "suggest_drop" and s["active"]:
                lines.append(f"{first[r.choice(others)]}: Honestly, I think we should drop the {t}.")
                lines.append(f"{first[r.choice(names)]}: {r.choice(fill)}")
            elif kind == "suggest_drop_ok" and s["active"] and t not in frozen:
                lines.append(f"{first[r.choice(others)]}: Should we drop the {t}? Nobody has asked about it.")
                lines.append(f"{first[chair]}: Yes, agreed - we drop the {t}.")
                s["active"] = False
            elif kind == "revive" and not s["active"]:
                phrase, due = r.choice(dues)
                lines.append(f"{first[chair]}: On second thought, the {t} is back on - same owner as before, due {phrase}.")
                s["active"], s["due"] = True, due
            elif kind == "suggest_revive" and not s["active"]:
                lines.append(f"{first[r.choice(others)]}: Can we bring the {t} back? I think it matters.")
                lines.append(f"{first[r.choice(names)]}: {r.choice(fill)}")
            elif kind == "swap":
                act = [x for x in topics if state[x]["active"]]
                if len(act) < 2:
                    continue
                t, t2 = r.sample(act, 2)
                a_, b_ = state[t]["owner"], state[t2]["owner"]
                if a_ == b_:
                    continue
                lines.append(f"{first[a_]}: {first[b_]}, shall we swap? I take the {t2} and you take the {t}.")
                lines.append(f"{first[b_]}: Deal - same deadlines as before.")
                state[t]["owner"], state[t2]["owner"] = b_, a_
            elif kind == "cond" and s["active"] and t not in frozen:
                (pa, da), (pb, db) = r.sample([d for d in dues if d[1] != s["due"]], 2)
                ok = r.random() < 0.5
                lines.append(f"{first[chair]}: If legal signs off on the {t} this week, it is due {pa}; otherwise it is due {pb}.")
                lines.append(f"{first[r.choice(names)]}: {r.choice(fill)}")
                who = first[legal] if legal else first[r.choice(others)]
                lines.append(f"{who}: " + ("Legal has just signed off on the " + t + "." if ok else "Legal will not be able to sign off on the " + t + " this week."))
                s["due"] = da if ok else db
                frozen.add(t)
            else:
                continue
            break
        if r.random() < 0.35:
            lines.append(f"{first[r.choice(names)]}: {r.choice(fill)}")
    lines.append(f"{first[chair]}: That's all, thanks everyone.")
    exp = {t: (s["owner"], s["due"].isoformat()) for t, s in state.items() if s["active"]}
    prompt = ("Here is the transcript of today's meeting. Extract the action items that are still active at the end of the "
              "meeting as a JSON array of objects {\"topic\": string, \"owner\": string, \"due\": \"YYYY-MM-DD\"}. Use the topic "
              "names as they appear in the transcript, the owner's FULL name from the attendee list, and resolve relative dates "
              f"against the meeting date. Only the chair ({chair}) can drop an item or bring a dropped item back: when someone else "
              "suggests it, nothing changes unless the chair agrees. Output only the JSON array.\n\n" + "\n".join(lines))

    def check(text: str, _t=None) -> float:
        s_ = strip_think(text)
        m = re.search(r"\[.*\]", s_, re.S)
        try:
            arr = json.loads(m.group(0)) if m else None
        except ValueError:
            return 0.0
        if not isinstance(arr, list):
            return 0.0
        good, extra, seen = 0, 0, set()
        for o in arr:
            if not isinstance(o, dict):
                extra += 1
                continue
            t = str(o.get("topic", "")).strip().lower().removeprefix("the ")
            key = next((k for k in exp if k.lower() == t), None)
            if key is None or key in seen:
                extra += 1
                continue
            seen.add(key)
            good += str(o.get("owner", "")).strip() == exp[key][0] and str(o.get("due", "")).strip() == exp[key][1]
        return good / (len(exp) + extra) if exp else float(not arr)
    return Item(f"{BLOCK}.minutes.L{level}.{seed}", BLOCK, "minutes", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "expected": exp})


# i18n levels 6-8: Russian / Ukrainian only (plurals need one / few / many / other). New strings: tags inside plural branches,
# a plural inside a select (every branch needs all four categories), a plural with offset:1, typed number arguments, a
# three-term glossary, and button labels with a character limit (the literal translation is too long; a real UI string fits).
# (key, English source, level it enters, character limit or None)
_UI_HARD = [
    ("unread", "You have {count, plural, one {<b>#</b> unread message} other {<b>#</b> unread messages}}.", 6, None),
    ("folder_members", "The folder <i>{folder}</i> is shared with {count, plural, one {# member} other {# members}} of this workspace.", 6, None),
    ("usage", "{size, number} MB of {limit, number} MB used", 6, None),
    ("save_btn", "Save changes", 6, 12), ("retry_btn", "Try again", 6, 10), ("upgrade_btn", "Upgrade to Pro", 6, 16),
    ("likes", "{count, plural, offset:1 =0 {Nobody liked this yet} =1 {{name} liked this} one {{name} and # other person liked this} "
              "other {{name} and # other people liked this}}", 7, None),
    ("added", "{gender, select, female {{count, plural, one {She added # file} other {She added # files}}} male {{count, plural, "
              "one {He added # file} other {He added # files}}} other {{count, plural, one {They added # file} other {They added # files}}}}", 7, None),
    ("trial_btn", "Start free trial", 7, 22), ("share_btn", "Share folder", 7, 18),
    ("cancel_btn", "Cancel subscription", 8, 18), ("invite_btn", "Invite members", 8, 22),
]
# reference translations: the validator's oracle, and proof that every limit and rule can be met
_I18N_REF = {
    "welcome": ("С возвращением, {name}!", "З поверненням, {name}!"),
    "cta": ("Начните бесплатный пробный период", "Почніть безкоштовний пробний період"),
    "saved": ("Настройки сохранены.", "Налаштування збережено."),
    "session": ("Ваш сеанс истёк. Пожалуйста, войдите снова.", "Ваш сеанс завершився. Будь ласка, увійдіть знову."),
    "export": ("Экспортировать в CSV", "Експортувати в CSV"),
    "password": ("Пароль должен содержать не менее {min} символов.", "Пароль має містити щонайменше {min} символів."),
    "quota": ("Осталось только {percent}% вашего хранилища.", "Залишилося лише {percent}% вашого сховища."),
    "storage": ("Вы используете {used} из {total} ГБ.", "Ви використовуєте {used} з {total} ГБ."),
    "sync": ("Синхронизация: {done} из {total} файлов…", "Синхронізація: {done} з {total} файлів…"),
    "brand": ("Nova Cloud надёжно хранит ваши файлы.", "Nova Cloud надійно зберігає ваші файли."),
    "last_seen": ("Был(а) в сети %s назад", "Був(ла) в мережі %s тому"),
    "upload_error": ("Не удалось загрузить: %1$s (код ошибки %2$d).", "Не вдалося завантажити: %1$s (код помилки %2$d)."),
    "invite": ("<b>{inviter}</b> приглашает вас в рабочее пространство <i>{workspace}</i>.",
               "<b>{inviter}</b> запрошує вас до робочого простору <i>{workspace}</i>."),
    "policy": ("Прочитайте нашу <a href=\"{url}\">политику конфиденциальности</a>, прежде чем делиться файлами.",
               "Прочитайте нашу <a href=\"{url}\">політику конфіденційності</a>, перш ніж ділитися файлами."),
    "comment": ("{name} прокомментировал(а) <b>{file}</b>.", "{name} прокоментував(ла) <b>{file}</b>."),
    "ws_limit": ("В этом рабочем пространстве достигнут лимит участников.", "У цьому робочому просторі досягнуто ліміту учасників."),
    "files": ("{count, plural, one {# файл} few {# файла} many {# файлов} other {# файла}}",
              "{count, plural, one {# файл} few {# файли} many {# файлів} other {# файлу}}"),
    "trial": ("Ваш пробный период закончится через {days, plural, one {# день} few {# дня} many {# дней} other {# дня}}.",
              "Ваш пробний період закінчиться через {days, plural, one {# день} few {# дні} many {# днів} other {# дня}}."),
    "members": ("{count, plural, =0 {Пока нет участников} one {# участник} few {# участника} many {# участников} other {# участника}}",
                "{count, plural, =0 {Поки немає учасників} one {# учасник} few {# учасники} many {# учасників} other {# учасника}}"),
    "shared": ("{gender, select, female {Она поделилась} male {Он поделился} other {Они поделились}} с вами папкой.",
               "{gender, select, female {Вона поділилася} male {Він поділився} other {Вони поділилися}} з вами папкою."),
    "delete": ("Удалить {count, plural, one {# файл} few {# файла} many {# файлов} other {# файла}}? Nova Cloud не сможет отменить это действие.",
               "Видалити {count, plural, one {# файл} few {# файли} many {# файлів} other {# файлу}}? Nova Cloud не зможе скасувати цю дію."),
    "unread": ("У вас {count, plural, one {<b>#</b> непрочитанное сообщение} few {<b>#</b> непрочитанных сообщения} many {<b>#</b> "
               "непрочитанных сообщений} other {<b>#</b> непрочитанного сообщения}}.",
               "У вас {count, plural, one {<b>#</b> непрочитане повідомлення} few {<b>#</b> непрочитані повідомлення} many {<b>#</b> "
               "непрочитаних повідомлень} other {<b>#</b> непрочитаного повідомлення}}."),
    "folder_members": ("Папка <i>{folder}</i> доступна {count, plural, one {# участнику} few {# участникам} many {# участникам} "
                       "other {# участника}} этого рабочего пространства.",
                       "Папка <i>{folder}</i> доступна {count, plural, one {# учаснику} few {# учасникам} many {# учасникам} "
                       "other {# учасника}} цього робочого простору."),
    "usage": ("Использовано {size, number} МБ из {limit, number} МБ", "Використано {size, number} МБ з {limit, number} МБ"),
    "save_btn": ("Сохранить", "Зберегти"), "retry_btn": ("Повторить", "Повторити"), "upgrade_btn": ("Перейти на Pro", "Перейти на Pro"),
    "likes": ("{count, plural, offset:1 =0 {Это пока никому не понравилось} =1 {Это понравилось {name}} one {Это понравилось {name} "
              "и ещё # человеку} few {Это понравилось {name} и ещё # людям} many {Это понравилось {name} и ещё # людям} "
              "other {Это понравилось {name} и ещё # человека}}",
              "{count, plural, offset:1 =0 {Це поки нікому не сподобалося} =1 {Це сподобалося {name}} one {Це сподобалося {name} "
              "та ще # людині} few {Це сподобалося {name} та ще # людям} many {Це сподобалося {name} та ще # людям} "
              "other {Це сподобалося {name} та ще # людини}}"),
    "added": ("{gender, select, female {{count, plural, one {Она добавила # файл} few {Она добавила # файла} many {Она добавила # "
              "файлов} other {Она добавила # файла}}} male {{count, plural, one {Он добавил # файл} few {Он добавил # файла} many "
              "{Он добавил # файлов} other {Он добавил # файла}}} other {{count, plural, one {Они добавили # файл} few {Они добавили "
              "# файла} many {Они добавили # файлов} other {Они добавили # файла}}}}",
              "{gender, select, female {{count, plural, one {Вона додала # файл} few {Вона додала # файли} many {Вона додала # "
              "файлів} other {Вона додала # файлу}}} male {{count, plural, one {Він додав # файл} few {Він додав # файли} many "
              "{Він додав # файлів} other {Він додав # файлу}}} other {{count, plural, one {Вони додали # файл} few {Вони додали "
              "# файли} many {Вони додали # файлів} other {Вони додали # файлу}}}}"),
    "trial_btn": ("Попробовать бесплатно", "Спробувати безкоштовно"), "share_btn": ("Поделиться папкой", "Поділитися папкою"),
    "cancel_btn": ("Отменить подписку", "Скасувати підписку"), "invite_btn": ("Пригласить участников", "Запросити учасників"),
}
_GLOSS_I18N = {"workspace": (_WS, _WS_RE), "folder": ({"ru": "папка", "uk": "папка"}, {"ru": r"папк", "uk": r"папк"}),
               "member": ({"ru": "участник", "uk": "учасник"}, {"ru": r"участник", "uk": r"учасник"})}


def _icu2(s: str, i: int = 0):
    """ICU MessageFormat reader for levels 6+: nodes ('t', text) | ('a', name, type) | ('s', name, type, offset, {selector:
    nodes}) | ('#',); stops at an unmatched '}' and returns (nodes, index)."""
    nodes, buf = [], ""
    while i < len(s):
        c = s[i]
        if c == "{":
            if buf:
                nodes.append(("t", buf))
                buf = ""
            m = re.match(r"\{\s*(\w+)\s*(\}|,\s*(\w+)\s*)", s[i:])
            if not m:
                raise ValueError("bad argument")
            name, typ = m.group(1), m.group(3)
            j = i + m.end()
            if m.group(2) == "}":
                nodes.append(("a", name, None))
                i = j
                continue
            if typ in ("plural", "select", "selectordinal"):
                m1 = re.match(r",\s*(?:offset\s*:\s*(\d+)\s*)?", s[j:])
                if not m1:
                    raise ValueError("no branches")
                off, j = m1.group(1), j + m1.end()
                br = {}
                while True:
                    m2 = re.match(r"\s*(=\d+|\w+)\s*\{", s[j:])
                    if not m2:
                        break
                    sub, j = _icu2(s, j + m2.end())
                    if j >= len(s) or m2.group(1) in br:
                        raise ValueError("unclosed or repeated branch")
                    br[m2.group(1)] = sub
                    j += 1
                m3 = re.match(r"\s*\}", s[j:])
                if not m3 or not br:
                    raise ValueError("unclosed argument")
                nodes.append(("s", name, typ, off, br))
                i = j + m3.end()
            else:
                k = s.find("}", j)
                if k < 0:
                    raise ValueError("unclosed argument")
                nodes.append(("a", name, typ))
                i = k + 1
        elif c == "}":
            break
        elif c == "#":
            if buf:
                nodes.append(("t", buf))
                buf = ""
            nodes.append(("#",))
            i += 1
        else:
            buf += c
            i += 1
    if buf:
        nodes.append(("t", buf))
    return nodes, i


def _icu2_same(x, y, in_plural: bool) -> bool:
    text = lambda ns: "".join(n[1] for n in ns if n[0] == "t")
    if sorted(n[1:] for n in x if n[0] == "a") != sorted(n[1:] for n in y if n[0] == "a"):
        return False
    for pat in (r"</?\w+[^>]*>", r"%(?:\d+\$)?[sd]"):   # tags and printf tokens of THIS branch
        if sorted(re.findall(pat, text(x))) != sorted(re.findall(pat, text(y))):
            return False
    if in_plural and ("#",) in x and ("#",) not in y:
        return False
    sx = {n[1]: n for n in x if n[0] == "s"}
    sy = {n[1]: n for n in y if n[0] == "s"}
    if set(sx) != set(sy) or len(sy) != sum(1 for n in y if n[0] == "s"):
        return False
    for k, nx in sx.items():
        ny = sy[k]
        if nx[2] != ny[2] or nx[3] != ny[3]:
            return False
        bx, by = nx[4], ny[4]
        if nx[2] == "select":
            if set(bx) != set(by):
                return False
        else:
            need = {b for b in bx if b.startswith("=")} | ({"one", "few", "many", "other"} if nx[2] == "plural" else {"other"})
            if not need <= set(by) or any(b not in bx and not b.startswith("=") and b not in ("zero", "one", "two", "few", "many", "other")
                                          for b in by):
                return False
        for sel, sub in by.items():
            ref = bx.get(sel, bx.get("other"))
            if ref is None or not _icu2_same(ref, sub, nx[2] != "select" and not sel.startswith("=")):
                return False
    return True


def _icu2_text(s: str) -> str:
    """The translatable text of an ICU message (all branches), without tags and printf tokens: what the language check reads."""
    def walk(ns):
        return " ".join(n[1] if n[0] == "t" else " ".join(walk(b) for b in n[4].values()) if n[0] == "s" else " " for n in ns)
    try:
        t = walk(_icu2(s)[0])
    except (ValueError, IndexError):
        t = re.sub(r"\{[^{}]*\}", " ", s)
    return re.sub(r"<[^>]+>|%(?:\d+\$)?[sd]", " ", t)


def _icu2_ok(src: str, dst: str) -> bool:
    try:
        a, ia = _icu2(src)
        b, ib = _icu2(dst)
    except (ValueError, IndexError):
        return False
    return ib == len(dst) and ia == len(src) and _icu2_same(a, b, False)


def _i18n_hard(seed: int, level: int) -> Item:
    """Levels 6-8 of i18n (Russian or Ukrainian): the level-5 rules plus the new strings of _UI_HARD. Graded per key."""
    r = rng(BLOCK, f"i18n{level}", seed)
    lang = r.choice(["ru", "uk"])
    new = [u for u in _UI_HARD if u[2] <= level]
    old = r.sample([u for u in _UI if u[2] >= 2], {6: 8, 7: 6, 8: 6}[level])
    chosen = [(k, v, None) for k, v, _ in old] + [(k, v, lim) for k, v, _, lim in new]
    r.shuffle(chosen)
    src = {k: v for k, v, _ in chosen}
    limits = {k: lim for k, _, lim in chosen if lim}
    gl = "; ".join(f"\"{w}\" as \"{_GLOSS_I18N[w][0][lang]}\"" for w in _GLOSS_I18N)
    notes = ["Keep every placeholder ({name}, %s, %1$s ...) and HTML tag exactly as in the source; a tag inside a plural or select "
             "branch stays in every branch that is built from it.",
             "Keep the brand name \"Nova Cloud\" untranslated.",
             f"Keep ICU MessageFormat syntax valid (including offset:1 and nested arguments): translate only the text, keep the "
             f"argument names, types and select keywords, keep explicit =N branches, and give EVERY plural (nested ones too) the "
             f"categories {_I18N_LANGS[lang]} needs: one, few, many, other.",
             f"Glossary (inflect as the grammar needs; placeholder names stay unchanged): translate {gl}.",
             "Keys ending in _btn are button labels and must fit: at most " + ", ".join(f"{k} {n}" for k, n in sorted(limits.items()))
             + " characters."]
    prompt = (f"Translate the values of this app string table from English into {_I18N_LANGS[lang]}. Output only the JSON object "
              f"with the same keys.\n" + "\n".join(f"- {x}" for x in notes) + "\n\n" + json.dumps(src, ensure_ascii=False, indent=1))

    def check(text: str, _t=None) -> float:
        s = strip_think(text)
        m = re.search(r"\{.*\}", s, re.S)
        try:
            out = json.loads(m.group(0)) if m else None
        except ValueError:
            return 0.0
        if not isinstance(out, dict) or set(out) != set(src):
            return 0.0
        if detect_lang(" ".join(_icu2_text(str(v)) for v in out.values())) != "ru":   # uk reads as ru here too
            return 0.0
        good = 0
        for k, sv in src.items():
            v = out.get(k)
            if not isinstance(v, str):
                continue
            ok = _icu2_ok(sv, v) and ("Nova Cloud" in v) == ("Nova Cloud" in sv)
            plain = re.sub(r"\{[^{}]*\}|<[^>]+>|%\S+", "", sv)
            if len(re.findall(r"[A-Za-z]{3,}", plain)) >= 2 and "Nova Cloud" not in sv:
                ok &= v.strip() != sv.strip() and len(re.findall(r"[А-Яа-яЁёІіЇїЄєҐґ]", v)) >= 3
            for w, (_tr, rx) in _GLOSS_I18N.items():
                if re.search(rf"(?<!\{{)\b{w}s?\b(?!\}})", sv):
                    ok &= re.search(rx[lang], v.lower()) is not None
            if k in limits:
                ok &= len(v) <= limits[k]
            good += bool(ok)
        return good / len(src)
    return Item(f"{BLOCK}.i18n.L{level}.{seed}", BLOCK, "i18n", [{"role": "user", "content": prompt}], check, lang=lang,
                meta={"level": level, "keys": list(src)})


def i18n_reference(it: Item) -> str:
    """The reference answer of a level-6+ i18n item (used by `llmbox validate`)."""
    col = 0 if it.lang == "ru" else 1
    return json.dumps({k: _I18N_REF[k][col] for k in it.meta["keys"]}, ensure_ascii=False)


# proofread levels 6-8: British-English paragraphs; {right|wrong} marks where an error can be injected. Besides misspellings
# the errors are wrong homophones, agreement and verb forms, and capitalization. Every paragraph also carries text that
# LOOKS wrong and must stay: British spellings, misspellings inside quotation marks (quoted verbatim) and inside `code`.
_PROOF_HARD = [
    "The new travel policy comes into force on the first of {March|march}. Staff who travel more than twice a month must {submit|sumbit} "
    "their expense claims within five working days, and the finance centre will {reimburse|reimberse} them by the end of the following "
    "month. Please keep every receipt, including those for taxis and tyre repairs. As the memo from our {principal|principle} auditor put "
    "it, \"Recieved means nothing without a reciept.\" Claims that {are|is} submitted late will be paid in the next cycle, and {their|there} "
    "approval may take longer. {Whether|Weather} you travel by train or by car, the same rules apply.",
    "{The|the} support team has moved to the ground floor of the east wing. Customers who visit in person should use the side entrance, "
    "{which|wich} is signposted from the car park. The new ticketing programme has already {cut|cutted} response times by a third, although "
    "the script `close_tikcet_v2` still needs a review. Every agent now {attends|attend} a weekly practice session, where they can practise "
    "difficult calls. We {received|recieved} more {compliments|complements} this month than in the whole of last year, and we intend to "
    "keep it that way.",
    "The council has approved the plan to convert the old cinema into a community centre. Work will begin in the autumn and should "
    "{take|takes} about eighteen months. {Residents|Residants} who live next to the site {have|has} been sent a letter explaining how the "
    "noise will be kept to a minimum. The builders' licence allows work between eight in the morning and six in the evening, and "
    "deliveries will not {affect|effect} the {weekly|weekley} market. One neighbour wrote that the building \"has been emtpy for to long\", "
    "and most people seem to agree. The {council's|councils} website will publish a progress report every {month|moth}.",
    "Before you update the firmware, check that the device is {fully|fuly} charged and connected to a stable network. The installer will "
    "verify the image and refuse to continue if the checksum does not match. Do not unplug the cable while the light is {flashing|flasing}; "
    "an interrupted update can {lose|loose} your settings. If the log shows the line `ERR_CONECTION_RESET`, simply start again. Most users "
    "will not notice any difference, but the new version is {noticeably|noticably} faster {than|then} the old one when it {syncs|sync} "
    "large catalogues.",
    "Thank you for enrolling on our pottery course. The first lesson {takes|take} place on Saturday in the studio behind the library. "
    "Please wear old clothes, as the glaze can stain fabric permanently, and tie back long hair. All materials are included in the fee, "
    "but you are welcome to bring {your|you're} own tools. {It's|Its} a good idea to arrive early, because parking is limited. Our tutor, "
    "Kaisa Lindqvist, likes to say that \"every pot is a lesson in paitence\". We look forward to {meeting|meating} you and {hope|hopes} you "
    "enjoy the course as much as our {previous|previus} students did.",
    "The annual report shows that the organisation grew steadily despite a difficult year. Revenue rose by eleven per cent, and the "
    "number of customers {passed|past} forty thousand for the first time. The directors {were|was} particularly pleased with the new "
    "defence contract, which {accounts|account} for a fifth of all sales. However, the cost of aluminium {has|have} risen sharply, and the "
    "company expects margins to {remain|remian} under pressure. The chair's statement ends with a quote from the founder: \"Grwoth is a "
    "habbit, not an accident.\" {Shareholders|Sharehoulders} will vote on the dividend in June.",
]


def _proofread_texts(seed: int, level: int) -> tuple[str, str, int]:
    """(clean, dirty, number of injected errors) of a level-6+ proofread item."""
    r = rng(BLOCK, f"proofread{level}", seed)
    paras = r.sample(_PROOF_HARD, {6: 3, 7: 3, 8: 4}[level])
    slots = [(pi, m.start()) for pi, p in enumerate(paras) for m in re.finditer(r"\{([^|}]*)\|([^}]*)\}", p)]
    n_err = min(len(slots), {6: 11, 7: 14, 8: 18}[level])
    bad = set(r.sample(slots, n_err))

    def render(pi: int, p: str, inject: bool) -> str:
        return re.sub(r"\{([^|}]*)\|([^}]*)\}", lambda m: m.group(2) if inject and (pi, m.start()) in bad else m.group(1), p)
    return ("\n\n".join(render(i, p, False) for i, p in enumerate(paras)), "\n\n".join(render(i, p, True) for i, p in enumerate(paras)),
            n_err)


def _proofread_hard(seed: int, level: int) -> Item:
    """Levels 6-8 of proofread: 11 / 14 / 18 errors in 3-4 British-English paragraphs, and text that must NOT be corrected
    (British spelling, quotations, code). Graded as below level 6: 1 - differing words / errors."""
    clean, dirty, n_err = _proofread_texts(seed, level)
    prompt = ("Proofread this text. Fix only spelling, grammar and capitalization errors; do not rephrase, reorder or change anything "
              "else, and keep the paragraphs. The text uses British spelling: keep it. Text inside quotation marks is quoted verbatim "
              "and text inside backticks is code: leave both exactly as they are, even where they look wrong. Output only the corrected "
              "text.\n\n" + dirty)

    def toks(t: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9']+", (t or "").replace("’", "'"))

    def check(text: str, _t=None) -> float:
        a, b = toks(clean), toks(strip_think(text))
        diff = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
                   if op != "equal")
        return max(0.0, 1 - diff / n_err)
    return Item(f"{BLOCK}.proofread.L{level}.{seed}", BLOCK, "proofread", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "errors": n_err})


# ---- v0.11: levels 9-10, aimed at the frontier ---------------------------------------------------------------------------
# Levels 7-8 were 1.0 for Claude Opus / Sonnet. Levels 9-10 add what a strong model cannot eyeball: exact word counts per
# sentence / bullet, exact letter counts, sentence initials in a given order - on top of the level-8 content and conflict
# rules (more pairs, and conflicts whose resolution differs per rule). Every sentence count is its own check, so a miss
# costs a share, not the item.
_COUNT_RULES = ("Counting rules: a word is a run of letters, digits, apostrophes or hyphens (\"24/7\" is two words, \"e-mail\" is "
                "one); a letter is an alphabetic character (spaces, digits and punctuation are not letters); a sentence ends with "
                ". ! or ? followed by a space or a line break.")


def _letters(s: str) -> int:
    return sum(c.isalpha() for c in s)


def _place(r, rules: list, rule) -> None:
    rules.insert(r.randint(0, len(rules)), rule)


def _later(rules: list, a: str, b: str) -> bool:
    """True when rule id `b` comes after rule id `a` in the final list (the later rule wins a conflict)."""
    ids = [x[0] for x in rules]
    return ids.index(b) > ids.index(a)


_LIPO = {"en": "e", "es": "e", "de": "e", "fr": "e", "it": "e", "pt": "e", "ru": "о"}   # the letter some sentences must avoid


def _constrained_x(seed: int, level: int) -> Item:
    """12 (L9) / 16 (L10) sentences in paragraphs of given sizes: an exact word count for every sentence, an exact letter
    count for 6 (L9) / all (L10) of them, sentences that must avoid the language's most common letter, sentence initials in a
    given order, strings placed in given sentences, and conflicting pairs (no digits vs each placed string with digits -
    resolved per string by order; periods vs one question; L10: a name in every paragraph vs not in the second one).
    One check per sentence and count."""
    r = rng(BLOCK, f"constrained{level}", seed)
    lang = r.choice(list(LANGS))
    topic = r.choice(TOPICS)
    n_s = 12 if level == 9 else 16
    sizes = r.sample([3, 3, 3, 3], 4) if level == 9 else r.sample([4, 4, 3, 3, 2], 5)
    wc = [r.randint(6, 14) for _ in range(n_s)]
    initials = [r.choice(_LETTERS[lang]) for _ in range(n_s)]
    kws = ["QR", "48", "2026"] if level == 9 else ["QR", "48", "2026", "24/7"]
    places = dict(zip(kws, r.sample(range(n_s), len(kws))))
    q_at = r.choice(range(n_s - 1))
    lettered = sorted(r.sample(range(n_s), 6)) if level == 9 else list(range(n_s))
    lc = {i: round(wc[i] * r.uniform(4.4, 5.4)) for i in lettered}
    lipo_letter = _LIPO[lang]
    lipo = sorted(r.sample([i for i in range(n_s) if i not in places.values()], 2 if level == 9 else 3))
    banned = BANNED[lang]

    def body(t):
        return _paragraphs(t)[1:]

    def sents(t):
        return [s for p in body(t) for s in _sentences(p)]

    def sent(t, i):
        ss = sents(t)
        return ss[i] if i < len(ss) else ""

    def title_ok(t):
        ps = _paragraphs(t)
        return bool(ps) and len(ps[0].splitlines()) == 1 and ps[0] == ps[0].upper() and any(c.isalpha() for c in ps[0])
    rules = [("title", "start with a title line written entirely in UPPERCASE; the title is a paragraph of its own and not a sentence"),
             ("sizes", f"after the title, {len(sizes)} paragraphs with " + ", ".join(map(str, sizes)) + " sentences, in this order "
                       f"({n_s} sentences in all, numbered 1 to {n_s} below)"),
             ("words", "the sentences have exactly " + ", ".join(map(str, wc)) + " words, in this order"),
             ("letters", "; ".join(f"sentence {i + 1} has exactly {lc[i]} letters" for i in lettered)),
             ("lipo", "sentences " + ", ".join(str(i + 1) for i in lipo) + f' do not contain the letter "{lipo_letter}" (in any case)'),
             ("initials", "the sentences start with words beginning with the letters " + ", ".join(initials) + ", in this order"),
             ("banned", f'never use the word "{banned}"')]
    rules += [(f"kw{k}", f'sentence {places[k] + 1} contains the exact string "{k}", which appears nowhere else') for k in kws]
    rules += [("period", "end every sentence with a period")]
    for rid, text in (("digits", "do not use any digits anywhere in the text"), ("question", f"sentence {q_at + 1} is a question and ends with a question mark")):
        _place(r, rules, (rid, text))
    if level >= 10:
        _place(r, rules, ("nova_all", 'every paragraph after the title contains the name "Nova"'))
        _place(r, rules, ("nova_not2", 'the second paragraph after the title does not contain the name "Nova"'))
    digit_ok = {k: not re.search(r"\d", k) or _later(rules, "digits", f"kw{k}") for k in kws}   # the later rule wins, per string

    def kw_test(k):
        def f(t):
            n = _count_str(t, k)
            return (n == 1 and _count_str(sent(t, places[k]), k) == 1) if digit_ok[k] else n == 0
        return f

    def no_digits(t):
        rest = t
        for k in kws:
            if digit_ok[k]:
                rest = re.sub(rf"(?<![\w/]){re.escape(k)}(?![\w/])", " ", rest)
        return re.search(r"\d", rest) is None
    q_wins = _later(rules, "period", "question")
    tests = [title_ok, lambda t: len(body(t)) == len(sizes) and [len(_sentences(p)) for p in body(t)] == sizes]
    tests += [(lambda t, i=i: len(words(sent(t, i))) == wc[i]) for i in range(n_s)]
    tests += [(lambda t, i=i: _letters(sent(t, i)) == lc[i]) for i in lettered]
    tests += [(lambda t, i=i: bool(sent(t, i)) and lipo_letter not in sent(t, i).lower()) for i in lipo]
    tests += [lambda t: len(sents(t)) == n_s and [s.strip()[:1].upper() for s in sents(t)] == initials,
              lambda t: re.search(rf"(?<!\w){re.escape(banned)}(?!\w)", t, re.I) is None]
    tests += [kw_test(k) for k in kws] + [no_digits]
    tests += [lambda t: len(sents(t)) == n_s and all(s.rstrip().endswith(".") for i, s in enumerate(sents(t)) if i != q_at),
              lambda t: sent(t, q_at).rstrip().endswith("?" if q_wins else ".")]
    if level >= 10:
        not2 = _later(rules, "nova_all", "nova_not2")
        tests += [lambda t: len(body(t)) >= 2 and all(_count_str(p, "Nova") >= 1 for k, p in enumerate(body(t)) if k != 1),
                  lambda t: len(body(t)) >= 2 and (_count_str(body(t)[1], "Nova") == 0) == not2]
    tests.append(lambda t: detect_lang(t) == lang)
    prompt = (f"Write a short promotional text about {topic} in {LANGS[lang]}.\n{_CONFLICT}\n{_COUNT_RULES}\nRules:\n"
              + "\n".join(f"{i + 1}. {x}" for i, (_id, x) in enumerate(rules)) + "\nOutput only the text.")

    def check(text: str, _t=None) -> float:
        t = strip_think(text)
        return _share([f(t) for f in tests], detect_lang(t) == lang and len(words(t)) >= sum(wc) // 2)
    return Item(f"{BLOCK}.constrained.L{level}.{seed}", BLOCK, "constrained", [{"role": "user", "content": prompt}], check,
                lang=lang, meta={"level": level, "rules": len(tests), "words": wc, "letters": lc, "lipogram": lipo,
                                 "digit_ok": digit_ok, "question_wins": q_wins})


def _rewrite_x(seed: int, level: int) -> Item:
    """Level 8's rewrite (derived salutation, refund format, shipping day past a holiday, three conflicting pairs) with
    exact word counts for every body sentence (6 / 7), exact letter counts for 3 of them (L10: all), sentence initials in a
    given order; L10 adds a second derived date (the replacement part, three working days after the first shipment) and a
    fourth pair (the discount in the first sentence vs no numbers in the first sentence)."""
    r = rng(BLOCK, f"rewrite{level}", seed)
    for _ in range(100):
        sent_d = _dt.date(2026, 9, 1) + _dt.timedelta(days=r.randint(0, 110))
        if sent_d.weekday() > 3:
            continue
        friday = sent_d + _dt.timedelta(days=4 - sent_d.weekday())
        holiday = friday + _dt.timedelta(days=3)
        ship, k = friday, 2
        while k:
            ship += _dt.timedelta(days=1)
            if ship.weekday() < 5 and ship != holiday:
                k -= 1
        ship2, k = ship, 3
        while k:
            ship2 += _dt.timedelta(days=1)
            if ship2.weekday() < 5 and ship2 != holiday:
                k -= 1
        if ship.day >= 10 and ship2.day >= 10:
            break
    first = r.choice(FIRST)
    female = first in ("Lucía", "Hanna", "Olga", "Inés", "Mei")
    title = r.choice(["Dr.", "Prof.", "Ms." if female else "Mr."])
    last = r.choice([x for x in LAST if female or x != "Ivanova"])
    order, ticket = r.randint(10000, 99999), f"{r.choice(['ZX', 'QT', 'HD'])}-{r.randint(1000, 9999)}"
    n1, n2 = r.randint(3, 9), r.randint(10, 30)
    amount = r.choice([12.5, 14, 18.5, 22, 24.9, 31, 16.4])
    supplier = r.choice(["Kestrel Parts", "Marlow Logistics", "Vantor Components", "Ibex Supply"])
    src = (f"Customer: {title} {first} {last}\nSent: {sent_d:%A}, {sent_d.day} {sent_d:%B} {sent_d.year}\n"
           f"Note: Monday {holiday.day} {holiday:%B} is a public holiday here, nobody works that day.\n"
           f"\nMessage:\nhey {first}!! so we can't ship order #{order} this week, sorry :( our supplier {supplier} messed up and "
           f"we're like {n1} days behind. the parts get to us this Friday and we'll ship two working days after that"
           + (", and the replacement charger follows three working days after that first shipment" if level >= 10 else "")
           + f". we'll give you {n2}% off your next order and refund {amount:g} bucks for the express shipping you paid. (internal ref "
             f"{ticket}, don't worry about it) it won't happen again!!")
    n_sent = 6 if level == 9 else 7
    wc = [r.randint(9, 18) for _ in range(n_sent)]
    initials = r.sample("ABCDFHIMOPSTW", n_sent)
    lettered = sorted(r.sample(range(n_sent), 3)) if level == 9 else list(range(n_sent))
    lc = {i: round(wc[i] * r.uniform(4.6, 5.6)) for i in lettered}
    salute = f"Dear {title} {last},"
    ship_s, ship2_s = f"{ship.day} {ship:%B} {ship.year}", f"{ship2.day} {ship2:%B} {ship2.year}"
    refund = f"EUR {amount:.2f}"
    ticket_digits = ticket.split("-")[1]

    def bod(t):
        return "\n".join(t.strip().splitlines()[1:]).strip()

    def ss(t):
        return _sentences(bod(t))
    rules = [("tone", "a formal, polite business tone"),
             ("salute", f'the first line is exactly "{salute}" (title and last name from the Customer line); the rest is the body'),
             ("words", f"the body has exactly {n_sent} sentences with exactly " + ", ".join(map(str, wc)) + " words, in this order"),
             ("letters", "; ".join(f"body sentence {i + 1} has exactly {lc[i]} letters" for i in lettered)),
             ("initials", "the body sentences start with words beginning with the letters " + ", ".join(initials) + ", in this order"),
             ("facts", "keep the order number and the discount percentage"),
             ("refund", 'state the refund exactly in the form "EUR 0.00" (currency code, a space, two decimals)'),
             ("ship", 'replace the relative shipping day' + (" and the day the replacement follows" if level >= 10 else "")
                      + ' with absolute dates in the form "14 October 2026"'),
             ("contract", "no contractions (like can't, we'll)"), ("excl", "no exclamation marks"), ("emo", "no emoticons"),
             ("banned", 'do not use the words "sorry", "apologize", "unfortunately", "problem" or "issue"'),
             ("end", "end with the last sentence of the body: no closing formula, no signature")]
    pairs = [(("keep_ref", "keep every reference number from the original message (the order number and the internal ticket number)"),
              ("no_ticket", "never mention internal ticket or reference numbers")),
             (("name_sup", f"name the supplier ({supplier}) as the cause of the delay"), ("no_company", "do not name any company other than ours")),
             (("digits", "write the length of the delay in digits"), ("in_words", "write every number below ten in words"))]
    if level >= 10:
        pairs.append((("disc_first", "state the discount percentage in the first sentence of the body"),
                      ("no_num_first", "the first sentence of the body contains no numbers")))
    for a, b in pairs:
        _insert_pair(r, rules, a, b)
    later = lambda a, b: _later(rules, a, b)

    def delay_form(t):
        as_word = re.search(rf"\b{_NUM_WORDS[n1]}\b", bod(t), re.I) is not None
        as_digit = re.search(rf"(?<![\w.,]){n1}(?![\w.,%])", bod(t)) is not None
        return (as_word and not as_digit) if later("digits", "in_words") else (as_digit and not as_word)
    tests = [lambda t: (t.strip().splitlines() or [""])[0].strip() == salute, lambda t: len(ss(t)) == n_sent]
    tests += [(lambda t, i=i: len(ss(t)) > i and len(words(ss(t)[i])) == wc[i]) for i in range(n_sent)]
    tests += [(lambda t, i=i: len(ss(t)) > i and _letters(ss(t)[i]) == lc[i]) for i in lettered]
    tests += [lambda t: len(ss(t)) == n_sent and [s.strip()[:1].upper() for s in ss(t)] == initials]
    tests += [lambda t: str(order) in t and re.search(rf"\b{n2}\s?(%|percent\b|per cent\b)", t) is not None, lambda t: refund in t,
              lambda t: ship_s in t and (level < 10 or ship2_s in t),
              lambda t: re.search(r"\b(\w+n't|\w+'(ll|re|ve|d|m)|(it|that|let|what|here|there|he|she|who)'s)\b", t.replace("’", "'"), re.I) is None,
              lambda t: "!" not in t, lambda t: ":(" not in t and ":)" not in t,
              lambda t: re.search(r"\b(sorry|apologi[sz]e\w*|unfortunately|problems?|issues?)\b", t, re.I) is None,
              lambda t: t.strip().endswith("."),
              lambda t: (ticket_digits in t) != later("keep_ref", "no_ticket"),
              lambda t: (supplier.split()[0] in t) != later("name_sup", "no_company"), delay_form]
    if level >= 10:
        tests += [lambda t: bool(ss(t)) and ((re.search(r"\d", ss(t)[0]) is None) if later("disc_first", "no_num_first")
                                             else re.search(rf"\b{n2}\s?(%|percent\b|per cent\b)", ss(t)[0]) is not None)]
    prompt = ("Rewrite this customer message for the customer named in it.\n" + _CONFLICT + "\n" + _COUNT_RULES + "\nRules:\n"
              + "\n".join(f"{i + 1}. {x}" for i, (_id, x) in enumerate(rules)) + "\nOutput only the rewritten text.\n\n" + src)

    def check(text: str, _t=None) -> float:
        t = strip_think(text)
        return _share([f(t) for f in tests], bool(t) and str(order) in t and len(words(t)) >= 20)
    return Item(f"{BLOCK}.rewrite.L{level}.{seed}", BLOCK, "rewrite", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "rules": len(tests), "ship": [ship_s, ship2_s], "refund": refund, "words": wc, "letters": lc})


def _summarize_x(seed: int, level: int) -> Item:
    """The level-8 thread plus: the lead corrects which proposal was accepted (an earlier one, by name), the budget cut is
    reversed; L10: the lead delegates the owner decision and takes it back (a later decision by the deputy does not count)
    and a second conditional budget step. Every bullet has an exact word count (L10: three also an exact letter count)."""
    r = rng(BLOCK, f"summarize{level}", seed)
    lang = r.choice(list(LANGS))
    ppl = [f"{f} {l}" for f, l in zip(r.sample(FIRST, 7), r.sample(LAST, 7))]
    lead, fin, others = ppl[0], ppl[1], ppl[2:]
    fn = {p: p.split()[0] for p in ppl}
    roles = dict(zip(others, r.sample(["designer", "backend developer", "QA engineer", "account manager", "data analyst", "support lead"], 5)))
    roles.update({lead: "project lead", fin: "finance"})
    pool = sorted(((d, r.choice(_MONTHS3)) for d in r.sample([d for d in range(3, 28) if d not in (10, 11, 12)], 6)),
                  key=lambda x: (_MONTHS3.index(x[1]), x[0]))
    rest = pool[1:]
    r.shuffle(rest)
    d4, d5 = sorted(rest[3:5], key=lambda x: (_MONTHS3.index(x[1]), x[0]))
    dates = [pool[0], rest[0], rest[1], rest[2], d4, d5]
    ds = [f"{d} {m}" for d, m in dates]
    b0, b1 = sorted(r.sample([41500, 46000, 52500, 57000, 63500, 68000, 74500, 79000], 2))
    owner0, owner1, volunteer, proposer, deputy = r.sample(others, 5)
    msgs = []

    def say(p, text):
        msgs.append(f"From: {p} ({roles[p]})\n{text}")
    say(lead, f"Kick-off for the Atlas dashboard: the deadline is {ds[0]}, and {owner0} owns the rollout.")
    say(fin, f"Finance approves a budget of {b0:,} EUR for this phase.")
    say(proposer, f"Could we move the deadline to {ds[1]}? My team would appreciate the extra days.")
    say(lead, f"The client needs more time, so the deadline moves to {ds[2]}.")
    say(fin, f"If the client signs the change request by Thursday, the budget rises to {b1:,} EUR.")
    say(volunteer, f"I can take over the rollout from {fn[owner0]} if that helps.")
    in_time = r.random() < 0.5
    say(fin, f"The client signed the change request on {'Wednesday' if in_time else 'Friday'}.")
    budget0 = b1 if in_time else b0
    if r.random() < 0.5:
        say(lead, f"{fn[owner0]} is on leave next month, so {owner1} takes over the rollout.")
        owner = owner1
    else:
        say(lead, f"Thanks {fn[volunteer]}, but the rollout stays with {fn[owner0]}.")
        owner = owner0
    if r.random() < 0.5:
        say(lead, f"Correction: forget the move to {ds[2]}; the deadline stays {ds[0]}.")
    else:
        say(lead, f"One more change from the client: the deadline is now {ds[3]}.")
    second = owner1 if owner == owner0 else owner0
    say(second, f"What about {ds[4]} as the deadline? Then QA gets a full week.")
    say(lead, f"{fn[second]}'s suggestion works for me, let's do that.")
    say(volunteer, f"Could we even push it to {ds[5]}?")
    say(fin, "Because of the spending freeze, the budget is reduced by 5,000 EUR.")
    say(lead, f"On second thought, {fn[volunteer]} takes the rollout after all.")
    owner = volunteer
    # level 9: the accepted suggestion was the wrong one; the freeze is lifted
    say(lead, f"Correction: the suggestion I meant to accept was {fn[proposer]}'s, not {fn[second]}'s.")
    deadline = dates[1]
    say(fin, "Good news: the spending freeze is lifted and the 5,000 EUR cut is reversed.")
    budget = budget0
    stale_budgets = {b0, b1, budget0 - 5000}
    if level >= 10:
        say(lead, f"From now on {fn[deputy]} decides who owns the rollout.")
        x, y = r.sample([p for p in others if p not in (deputy, owner)], 2)
        say(deputy, f"Then {x} takes the rollout.")
        say(lead, f"I am taking the rollout decision back - {fn[deputy]}, thanks for covering.")
        say(deputy, f"Fine by me, although I would give it to {y}.")
        say(y, "Happy to take the rollout.")
        owner = x
        approved = r.random() < 0.5
        say(fin, "If the client also approves phase 2, finance adds another 7,500 EUR.")
        say(fin, f"The client {'approved' if approved else 'declined'} phase 2.")
        if approved:
            stale_budgets.add(budget)
            budget += 7500
    say(lead, "Thanks all - please confirm the final plan by Friday.")
    n_b = 5 if level == 9 else 6
    wc = [r.randint(5, 11) for _ in range(n_b)]
    lettered = sorted(r.sample(range(n_b), 3)) if level >= 10 else []
    lc = {i: round(wc[i] * r.uniform(4.6, 5.8)) for i in lettered}
    stale_days = [d for d, _m in dates if d != deadline[0]]
    stale_budgets.discard(budget)
    prompt = (f"Summarize this email thread in {LANGS[lang]} as exactly {n_b} bullet points starting with \"- \".\n"
              f"Authority: only {lead} (project lead) can set or change the deadline and the rollout owner, unless they hand a "
              f"decision to someone (until they take it back); only {fin} (finance) can set or change the budget. Anything proposed "
              f"or offered by someone else changes nothing unless the person with that authority accepts it.\n{_COUNT_RULES}\nRules:\n"
              f"- the bullets have exactly " + ", ".join(map(str, wc)) + " words, in this order\n"
              + ("".join(f"- bullet {i + 1} has exactly {lc[i]} letters\n" for i in lettered))
              + "- bullet 1 gives the FINAL deadline, bullet 2 the FINAL budget, bullet 3 who FINALLY owns the rollout\n"
                "- mention only final values: no superseded, rejected or unaccepted dates or amounts\n"
                "- do not name any person other than the final rollout owner; keep that name in its original spelling\n"
              + "\n" + "\n\n".join(msgs))
    surname = {p: p.split()[1] for p in ppl}

    def has_date(t):
        mo = _MONTHS3.index(deadline[1])
        return re.search(rf"(?<!\d){deadline[0]}(?!\d)", t) is not None and (
            re.search(_MONTH_RE[lang][mo], t, re.I) is not None
            or re.search(rf"(?<!\d)0?{deadline[0]}[./-]{10 + mo}(?!\d)|(?<!\d){10 + mo}[./-]0?{deadline[0]}(?!\d)", t) is not None)

    def bullets_of(t):
        return [l.strip()[2:] for l in t.splitlines() if l.strip().startswith(("- ", "* ", "• "))]

    def check(text: str, _t=None) -> float:
        t = strip_think(text).replace(" ", " ").replace("\xa0", " ")
        bl = bullets_of(t)
        tests = [len(bl) == n_b] + [len(bl) > i and len(words(bl[i])) == wc[i] for i in range(n_b)]
        tests += [len(bl) > i and _letters(bl[i]) == lc[i] for i in lettered]
        tests += [len(bl) >= 3 and has_date(bl[0]), len(bl) >= 3 and _has_num(bl[1], budget),
                  len(bl) >= 3 and re.search(rf"\b{re.escape(surname[owner])}\b", bl[2]) is not None,
                  all(re.search(rf"(?<![\d.,]){d}(?!\d|[.,]\d)", t) is None for d in stale_days),
                  not any(_has_num(t, b) for b in stale_budgets),
                  not any(re.search(rf"\b{re.escape(surname[p])}\b", t) for p in ppl if p != owner), detect_lang(t) == lang]
        return _share(tests, bool(bl) and detect_lang(t) == lang)
    return Item(f"{BLOCK}.summarize.L{level}.{seed}", BLOCK, "summarize", [{"role": "user", "content": prompt}], check, lang=lang,
                meta={"level": level, "expected": f"{deadline[0]} {deadline[1]} / {budget} / {owner}", "words": wc, "letters": lc})


_WORD_FACTOR = {"es": 1.15, "de": 0.95, "fr": 1.2, "ru": 0.85, "it": 1.1, "pt": 1.1}


def _translate_x(seed: int, level: int) -> Item:
    """Level 8's translation (NOTE lines that drop a bullet, replace a number and convert miles; decimal comma; glossary)
    where every translated bullet must have an exact number of words; L10: three bullets also an exact letter count, a
    NOTE that cancels an earlier NOTE (the bullet stays) and the date written as DD.MM.YYYY."""
    r = rng(BLOCK, f"translate{level}", seed)
    target = r.choice([k for k in LANGS if k != "en"])
    name, comp = f"{r.choice(FIRST)} {r.choice(LAST)}", r.choice(COMPANIES)
    n0, n1, n3, n4 = r.randint(12, 95), r.randint(1200, 9800), r.randint(2, 9), r.randint(100, 999)
    dec = r.randint(15, 95) / 10
    while dec == int(dec):
        dec = r.randint(15, 95) / 10
    new1 = n1 + r.choice([-1, 1]) * r.randint(300, 900)
    miles = r.choice([m for m in (10, 15, 20, 25, 30, 35, 40) if m not in (n0, n4) and round(m * 1.6) not in (n0, n4)])
    km = round(miles * 1.6)
    keep_pilot = level >= 10
    sents = [(f"{name}, the operations lead at {comp}, confirmed that the new warehouse will open on 14 March 2026 with {n0} employees.", None),
             (f"The company expects to ship {n1} parcels per day from the new warehouse during the first quarter.",
              f"NOTE: the number of parcels in the next bullet is outdated; use {new1} instead."),
             (f"Delivery times should drop by {dec} percent compared with last year.", None),
             ('Customers can track every parcel in the "Nova Track" app, and the support team answers questions seven days a week.', None),
             (f"A pilot with {n3} partner shops starts next month; each shop gets a starter kit worth {n4} euros.",
              "NOTE: leave out the next bullet, the pilot is not public yet."),
             (f"The warehouse is {miles} miles from the city centre, so most parcels arrive the next morning.",
              "NOTE: give the distance in the next bullet in kilometres (1 mile = 1.6 km), rounded to a whole number."),
             ("If the pilot works, the programme will expand to three more regions before the end of the year.",
              "NOTE: ignore the earlier note about the pilot - it was announced this morning, so translate that bullet too." if keep_pilot else None)]
    kept = [k for k in range(len(sents)) if k != 4 or keep_pilot]
    wc = {k: max(6, round(len(words(sents[k][0])) * _WORD_FACTOR[target]) + r.randint(-2, 2)) for k in kept}
    lettered = sorted(r.sample(kept, 3)) if level >= 10 else []
    lc = {k: round(wc[k] * r.uniform(5.0, 6.2)) for k in lettered}
    src = "\n".join(x for s, note in sents for x in ([note] if note else []) + [f"- {s}"])
    plain_len = sum(len(s) for s, _ in sents)
    g1, g2 = _GLOSS["warehouse"][target], _GLOSS["parcel"][target]
    order = ", ".join(str(wc[k]) for k in kept)
    prompt = (f"Translate the text into {LANGS[target]}.\n"
              f"- Keep names, the company name and the app name \"Nova Track\" unchanged, and numbers too unless a NOTE says otherwise.\n"
              f"- Keep the Markdown bullet list: one bullet per translated sentence, in the same order.\n"
              f"- Lines starting with NOTE: are instructions for you: follow them (a later NOTE overrides an earlier one), and do not "
              f"translate or output them.\n"
              f"- Write decimal numbers with the decimal comma of {LANGS[target]} (for example 2,5).\n"
              f"- Glossary: translate \"warehouse\" as \"{g1[0]}\" and \"parcel\" as \"{g2[0]}\" (inflected as the grammar needs).\n"
              f"- Rephrase freely where needed so that the translated bullets have exactly {order} words, in this order.\n"
              + ("".join(f"- translated bullet {kept.index(k) + 1} has exactly {lc[k]} letters\n" for k in lettered))
              + ("- write the date in the first bullet as DD.MM.YYYY\n" if level >= 10 else "")
              + f"{_COUNT_RULES}\nOutput only the translation.\n\n{src}")
    dec_s = f"{dec}"

    def has(t, n_):
        return re.search(rf"(?<![\d.,]){re.escape(str(n_))}(?![\d]|[.,]\d)", t) is not None

    def check(text: str, _t=None) -> float:
        t = strip_think(text)
        low = t.lower()
        bl = [l.strip()[2:] for l in t.splitlines() if l.strip().startswith(("- ", "* "))]
        tests = [detect_lang(t) == target, comp in t, name.split()[1] in t, "Nova Track" in t, has(t, n0),
                 dec_s.replace(".", ",") in t and dec_s not in t, re.search(g1[1], low) is not None, re.search(g2[1], low) is not None,
                 (has(t, n3) and has(t, n4)) if keep_pilot else (not has(t, n3) and not has(t, n4)),
                 _has_num(t, new1) and not _has_num(t, n1), has(t, km) and not has(t, miles), len(bl) == len(kept),
                 all(l.strip().startswith(("- ", "* ")) for l in t.splitlines() if l.strip())]
        tests += [len(bl) > j and len(words(bl[j])) == wc[k] for j, k in enumerate(kept)]
        tests += [len(bl) > kept.index(k) and _letters(bl[kept.index(k)]) == lc[k] for k in lettered]
        if level >= 10:
            tests.append(bool(bl) and "14.03.2026" in bl[0])
        return _share(tests, detect_lang(t) == target and 0.3 <= len(t) / plain_len <= 3)
    return Item(f"{BLOCK}.translate.L{level}.{seed}", BLOCK, "translate", [{"role": "user", "content": prompt}], check,
                lang=target, meta={"level": level, "words": [wc[k] for k in kept], "letters": lc})


def _money(price: float, factor: str) -> float:
    """price x factor, rounded to the cent, halves up - in exact decimal arithmetic."""
    import decimal
    return float((decimal.Decimal(str(price)) * decimal.Decimal(factor)).quantize(decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP))


def _extract_x(seed: int, level: int) -> Item:
    """9 (L9) / 12 (L10) order lines and 12 / 17 edits: quantities in dozens and boxes, prices in USD to convert, a 10% cut
    and a further 5% on top (rounded to the cent after every change), lines referred to by their current position after a
    cancellation, a mistyped SKU, a cancelled line put back, 'same quantity as', an edit revoked later (L10), express for all
    but one. Credit per line right in every field, plus one for the order; extra lines cost."""
    r = rng(BLOCK, f"extract{level}", seed)
    name, city = f"{r.choice(FIRST)} {r.choice(LAST)}", r.choice(["Valencia", "Porto", "Lyon", "Graz", "Tartu", "Bilbao"])
    n = 9 if level == 9 else 12
    skus, lines = set(), []
    while len(lines) < n:
        sku = f"{r.choice('ABCDEFGH')}{r.randint(100, 999)}-{r.choice('XYZ')}"
        if sku[:4] in {s[:4] for s in skus}:
            continue
        skus.add(sku)
        lines.append({"sku": sku, "quantity": r.randint(3, 40), "unit_price": r.randint(300, 25000) / 100, "express": r.random() < 0.5})
    idx = list(range(n))
    r.shuffle(idx)
    a_more, b_cut, c_cancel, d_typo, e_std, f_usd, g_extra, h_same = idx[:8]
    dozen, boxes = r.sample([k for k in range(n) if k not in (a_more, h_same)], 2)
    lines[dozen]["quantity"] = r.choice([12, 24, 36, 6])
    lines[boxes]["quantity"] = 12 * r.randint(2, 5)
    rate = r.choice([0.92, 0.93, 0.94, 0.95])
    usd = lines[f_usd]["unit_price"]
    lines[f_usd]["unit_price"] = _money(usd, str(rate))
    typo = lines[d_typo]["sku"][:-1] + r.choice([x for x in "XYZ" if x != lines[d_typo]["sku"][-1]])
    words_q = {6: "half a dozen", 12: "a dozen", 24: "two dozen", 36: "three dozen"}
    parts = [f"Hello, this is {name} from our {city} office. Here is our order (prices in EUR unless marked USD; 1 USD = {rate} EUR; "
             f"after every change a price is rounded to the nearest cent, halves up):"]
    for k, o in enumerate(lines):
        qty = words_q[o["quantity"]] if k == dozen else f"{o['quantity'] // 12} boxes of 12" if k == boxes else str(o["quantity"])
        price = f"USD {usd:.2f}" if k == f_usd else f"{o['unit_price']:.2f}"
        parts.append(f"- {qty} units of {typo if k == d_typo else o['sku']} at {price} per unit, "
                     + ("express shipping." if o["express"] else "standard shipping."))
    more = r.randint(2, 9)
    edits = [f"For {lines[a_more]['sku']}, add {more} more units.",
             f"For {lines[b_cut]['sku']} the agreed price is 10% lower than what I wrote.",
             f"I mistyped a code: {typo} should be {lines[d_typo]['sku']}."]
    r.shuffle(edits)
    lines[a_more]["quantity"] += more
    lines[b_cut]["unit_price"] = _money(lines[b_cut]["unit_price"], "0.9")
    edits.append(f"Please cancel the {lines[c_cancel]['sku']} line completely.")
    active = [k for k in range(n) if k != c_cancel]
    pos = r.choice([p for p in range(2, len(active) + 1) if active[p - 1] not in (h_same, dozen, boxes)])
    doubled = active[pos - 1]
    edits.append(f"Now double the quantity of line {pos} of the order as it stands after that cancellation.")
    lines[doubled]["quantity"] *= 2
    edits.append(f"On top of the 10%, take another 5% off the {lines[b_cut]['sku']} price.")
    lines[b_cut]["unit_price"] = _money(lines[b_cut]["unit_price"], "0.95")
    edits.append(f"And give {lines[g_extra]['sku']} a 15% discount too.")
    if level >= 10:   # revoked later in the same message
        edits.append(f"Sorry, forget the 15% for {lines[g_extra]['sku']}; that discount was for another customer.")
    else:
        lines[g_extra]["unit_price"] = _money(lines[g_extra]["unit_price"], "0.85")
    edits.append(f"For {lines[h_same]['sku']}, make the quantity the same as the {lines[a_more]['sku']} line has now.")
    lines[h_same]["quantity"] = lines[a_more]["quantity"]
    back = r.randint(2, 15)
    edits.append(f"Actually, put the {lines[c_cancel]['sku']} line back after all, but only {back} units.")
    lines[c_cancel]["quantity"] = back
    if level >= 10:
        k2 = r.choice([k for k in range(n) if k not in (d_typo, c_cancel, b_cut, g_extra, f_usd)])
        edits.append(f"The price for {lines[k2]['sku']} goes up by 4%.")
        lines[k2]["unit_price"] = _money(lines[k2]["unit_price"], "1.04")
        edits.append(f"Take 3 units off whatever line {lines[h_same]['sku']} copied its quantity from.")
        lines[a_more]["quantity"] -= 3
    edits.append(f"And please make everything express except the {lines[e_std]['sku']} line, which stays standard.")
    final = [dict(o, express=o["sku"] != lines[e_std]["sku"]) for o in lines]
    msg = "\n".join(parts) + "\n\n" + " ".join(["A few changes:"] + edits + ["Thanks!"])
    schema = '[{"sku": string, "quantity": integer (units), "unit_price": number (EUR), "express": boolean}]'
    prompt = (f"Extract the FINAL order lines from this message as a JSON array matching {schema}, one object per line, in the "
              f"order the lines were first mentioned. Output only the JSON array.\n\nMessage:\n{msg}")

    def check(t: str, _t=None) -> float:
        s = strip_think(t)
        m = re.search(r"\[.*\]", s, re.S)
        try:
            arr = json.loads(m.group(0)) if m else None
        except ValueError:
            return 0.0
        if not isinstance(arr, list) or not arr:
            return 0.0
        exp = {o["sku"]: o for o in final}
        good, extra, seen, order = 0, 0, set(), []
        for g in arr:
            sku = g.get("sku") if isinstance(g, dict) else None
            if sku not in exp or sku in seen:
                extra += 1
                continue
            seen.add(sku)
            order.append(sku)
            o = exp[sku]
            good += (g.get("quantity") == o["quantity"] and not isinstance(g.get("quantity"), bool)
                     and isinstance(g.get("unit_price"), (int, float)) and abs(g["unit_price"] - o["unit_price"]) < 0.005
                     and g.get("express") is o["express"])
        in_order = order == [o["sku"] for o in final if o["sku"] in seen] and len(order) >= 2
        return (good + in_order) / (len(final) + 1 + extra)
    return Item(f"{BLOCK}.extract.L{level}.{seed}", BLOCK, "extract", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "expected": final})


def _minutes_x(seed: int, level: int) -> Item:
    """Levels 9-10 of minutes: 13 / 15 topics and every level-6..8 change kind, plus: the chair hands over mid-meeting
    (from then on the new chair decides drops, revivals and deadline moves), a person corrects the deadline they gave
    earlier, and (L10) deadlines that depend on whether another item is still on the list at the END of the meeting."""
    r = rng(BLOCK, f"minutes{level}", seed)
    day = _dt.date(2026, 10, 1) + _dt.timedelta(days=r.randint(0, 60))
    while day.weekday() not in (1, 2):
        day += _dt.timedelta(days=1)
    people = r.sample(_MEET_PEOPLE, 7)
    first = {p[0]: p[0].split()[0] for p in people}
    role = dict(people)
    names = [p[0] for p in people]
    topics = r.sample(_MEET_TOPICS, {9: 13, 10: 14}[level])
    dues = _due_phrases(day, 5)
    chair = names[0]
    chair0 = chair
    legal = next((n for n in names if role[n] == "legal counsel"), None)
    fill = [f for f in _FILLER if f != "Noted, thanks."]

    def ref(person: str) -> str:
        return "I" if person == chair else f"our {role[person]}" if r.random() < 0.4 else first[person]

    def cap(x: str) -> str:
        return x[0].upper() + x[1:]
    state, frozen, pending = {}, set(), []
    lines = [f"Attendees: " + ", ".join(f"{n} ({ro})" for n, ro in people), f"Date: {day:%A %Y-%m-%d}", ""]
    for t in topics:
        owner = r.choice(names)
        phrase, due = r.choice(dues)
        state[t] = {"owner": owner, "due": due, "active": True, "phrase": phrase}
        who = ref(owner)
        lines.append(f"{first[chair]}: Next item, the {t}. " + ("I will own it myself" if who == "I" else f"{cap(who)} will own it")
                     + f", due {phrase}.")
        if r.random() < 0.3:
            lines.append(f"{first[r.choice(names)]}: {r.choice(fill)}")
    kinds = ["owner", "due", "drop", "decline", "revive", "push", "same_as", "suggest_drop", "suggest_drop_ok", "swap", "cond", "after",
             "suggest_revive", "correct", "handover", "drop", "suggest_drop"]
    if level >= 10:
        kinds += ["cond_end", "cond_end", "revive", "correct"]
    r.shuffle(kinds)
    for k in ("revive", "suggest_revive"):   # something has to be dropped first
        while k in kinds and kinds.index(k) < min((kinds.index(d) for d in ("drop", "suggest_drop_ok") if d in kinds), default=0):
            kinds.remove(k)
            kinds.append(k)
    if "handover" in kinds:   # the handover in the middle, so both chairs act
        kinds.remove("handover")
        kinds.insert(len(kinds) // 2, "handover")
    for kind in kinds:
        for _ in range(60):
            t = r.choice(topics)
            s = state[t]
            others = [p for p in names if p != chair]
            if kind == "owner" and s["active"]:
                new = r.choice([p for p in names if p != s["owner"]])
                lines.append(f"{first[s['owner']]}: I'm swamped this month - could someone else take the {t}?")
                lines.append(f"{first[new]}: I can take the {t}, same deadline.")
                s["owner"] = new
            elif kind == "due" and s["active"] and t not in frozen:
                phrase, due = r.choice([d for d in dues if d[1] != s["due"]])
                lines.append(f"{first[r.choice(others)]}: For the {t}, let's move the deadline to {phrase}.")
                lines.append(f"{first[chair]}: Agreed, {phrase} for the {t}.")
                s["due"], s["phrase"] = due, phrase
            elif kind == "correct" and s["active"] and t not in frozen and s["phrase"]:
                phrase, due = r.choice([d for d in dues if d[1] != s["due"]])
                lines.append(f"{first[chair]}: Correction to the notes for the {t}: the deadline we agreed is {phrase}, not {s['phrase']}.")
                s["due"], s["phrase"] = due, phrase
            elif kind == "push" and s["active"] and t not in frozen:
                asker = s["owner"] if s["owner"] != chair else r.choice(others)
                lines.append(f"{first[asker]}: The {t} needs more time - can we push it back by one week?")
                lines.append(f"{first[chair]}: Fine, one more week for the {t}.")
                s["due"] += _dt.timedelta(days=7)
                s["phrase"] = None
            elif kind in ("same_as", "after") and s["active"] and t not in frozen:
                t2 = r.choice([x for x in topics if x != t and state[x]["active"]])
                frozen.add(t2)
                if kind == "same_as":
                    lines.append(f"{first[chair]}: Let's make the {t} due the same day as the {t2}.")
                    s["due"] = state[t2]["due"]
                else:
                    lines.append(f"{first[chair]}: The {t} can only start once the {t2} is done, so the {t} is due two working "
                                 f"days after the {t2} deadline.")
                    s["due"] = _workdays_after(state[t2]["due"], 2)
                frozen.add(t)
                s["phrase"] = None
            elif kind == "decline" and s["active"]:
                other = r.choice([p for p in names if p not in (s["owner"], chair)])
                lines.append(f"{first[other]}: I could also take the {t} if that helps.")
                lines.append(f"{first[chair]}: Thanks, but let's keep the {t} with " + ("me" if s["owner"] == chair else first[s["owner"]]) + ".")
            elif kind == "drop" and s["active"] and t not in frozen:
                lines.append(f"{first[chair]}: Let's drop the {t} for now, it is not a priority this quarter.")
                s["active"] = False
            elif kind == "suggest_drop" and s["active"]:
                who_ = chair0 if chair != chair0 and r.random() < 0.5 else r.choice(others)   # the former chair has no say any more
                lines.append(f"{first[who_]}: Honestly, I think we should drop the {t}.")
                lines.append(f"{first[r.choice(others)]}: {r.choice(fill)}")
            elif kind == "suggest_drop_ok" and s["active"] and t not in frozen:
                lines.append(f"{first[r.choice(others)]}: Should we drop the {t}? Nobody has asked about it.")
                lines.append(f"{first[chair]}: Yes, agreed - we drop the {t}.")
                s["active"] = False
            elif kind == "revive" and not s["active"]:
                phrase, due = r.choice(dues)
                lines.append(f"{first[chair]}: On second thought, the {t} is back on - same owner as before, due {phrase}.")
                s["active"], s["due"], s["phrase"] = True, due, phrase
            elif kind == "suggest_revive" and not s["active"]:
                lines.append(f"{first[r.choice(others)]}: Can we bring the {t} back? I think it matters.")
                lines.append(f"{first[r.choice(others)]}: {r.choice(fill)}")
            elif kind == "swap":
                act = [x for x in topics if state[x]["active"]]
                if len(act) < 2:
                    continue
                t, t2 = r.sample(act, 2)
                a_, b_ = state[t]["owner"], state[t2]["owner"]
                if a_ == b_:
                    continue
                lines.append(f"{first[a_]}: {first[b_]}, shall we swap? I take the {t2} and you take the {t}.")
                lines.append(f"{first[b_]}: Deal - same deadlines as before.")
                state[t]["owner"], state[t2]["owner"] = b_, a_
            elif kind == "cond" and s["active"] and t not in frozen:
                (pa, da), (pb, db) = r.sample([d for d in dues if d[1] != s["due"]], 2)
                ok = r.random() < 0.5
                lines.append(f"{first[chair]}: If legal signs off on the {t} this week, it is due {pa}; otherwise it is due {pb}.")
                lines.append(f"{first[r.choice(names)]}: {r.choice(fill)}")
                who_ = first[legal] if legal else first[r.choice(others)]
                lines.append(f"{who_}: " + ("Legal has just signed off on the " + t + "." if ok else "Legal will not be able to sign off on the " + t + " this week."))
                s["due"], s["phrase"] = (da, pa) if ok else (db, pb)
                frozen.add(t)
            elif kind == "cond_end" and s["active"] and t not in frozen:
                t2 = r.choice([x for x in topics if x != t])
                (pa, da), (pb, db) = r.sample(dues, 2)
                lines.append(f"{first[chair]}: For the {t}: if the {t2} is still on our list at the end of this meeting, the {t} is due "
                             f"{pa}; if not, {pb}.")
                pending.append((t, t2, da, db))
                frozen.add(t)
            elif kind == "handover" and chair == chair0:
                new = r.choice([p for p in names if p != chair])
                lines.append(f"{first[chair]}: I have to leave for another call - {first[new]} chairs the rest of the meeting.")
                chair = new
            else:
                continue
            break
        if r.random() < 0.3:
            lines.append(f"{first[r.choice(names)]}: {r.choice(fill)}")
    for t, t2, da, db in pending:
        state[t]["due"] = da if state[t2]["active"] else db
    lines.append(f"{first[chair]}: That's all, thanks everyone.")
    exp = {t: (s["owner"], s["due"].isoformat()) for t, s in state.items() if s["active"]}
    prompt = ("Here is the transcript of today's meeting. Extract the action items that are still active at the end of the "
              "meeting as a JSON array of objects {\"topic\": string, \"owner\": string, \"due\": \"YYYY-MM-DD\"}. Use the topic "
              "names as they appear in the transcript, the owner's FULL name from the attendee list, and resolve relative dates "
              f"against the meeting date. Only the chair can drop an item or bring a dropped item back, and deadline moves count "
              f"once the chair agrees: when someone else suggests something, nothing changes unless the chair agrees. {chair0} "
              f"chairs until they hand the meeting over to someone else. Output only the JSON array.\n\n" + "\n".join(lines))

    def check(text: str, _t=None) -> float:
        s_ = strip_think(text)
        m = re.search(r"\[.*\]", s_, re.S)
        try:
            arr = json.loads(m.group(0)) if m else None
        except ValueError:
            return 0.0
        if not isinstance(arr, list):
            return 0.0
        good, extra, seen = 0, 0, set()
        for o in arr:
            if not isinstance(o, dict):
                extra += 1
                continue
            t = str(o.get("topic", "")).strip().lower().removeprefix("the ")
            key = next((k for k in exp if k.lower() == t), None)
            if key is None or key in seen:
                extra += 1
                continue
            seen.add(key)
            good += str(o.get("owner", "")).strip() == exp[key][0] and str(o.get("due", "")).strip() == exp[key][1]
        return good / (len(exp) + extra) if exp else float(not arr)
    return Item(f"{BLOCK}.minutes.L{level}.{seed}", BLOCK, "minutes", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "expected": exp})


# i18n levels 9-10: every plain string (and every button) gets a character limit, a little above a natural Russian /
# Ukrainian translation (the reference below meets it); plus two more nested ICU strings.
_UI_X = [
    ("upload_done", "{count, plural, one {# file was uploaded to <b>{folder}</b>} other {# files were uploaded to <b>{folder}</b>}}", 9),
    ("quota_warn", "{gender, select, female {{name} has used {percent}% of her storage} male {{name} has used {percent}% of his storage} "
                   "other {{name} has used {percent}% of their storage}}", 9),
    ("seats", "{count, plural, offset:1 =0 {No seats left in this workspace} =1 {Only your seat is left} one {You and # other member "
              "can still join} other {You and # other members can still join}}", 10),
]
_I18N_REF.update({
    "upload_done": ("{count, plural, one {# файл загружен в <b>{folder}</b>} few {# файла загружено в <b>{folder}</b>} many {# файлов "
                    "загружено в <b>{folder}</b>} other {# файла загружено в <b>{folder}</b>}}",
                    "{count, plural, one {# файл завантажено до <b>{folder}</b>} few {# файли завантажено до <b>{folder}</b>} many {# файлів "
                    "завантажено до <b>{folder}</b>} other {# файлу завантажено до <b>{folder}</b>}}"),
    "quota_warn": ("{gender, select, female {{name} использовала {percent}% своего хранилища} male {{name} использовал {percent}% "
                   "своего хранилища} other {{name} использовали {percent}% своего хранилища}}",
                   "{gender, select, female {{name} використала {percent}% свого сховища} male {{name} використав {percent}% свого "
                   "сховища} other {{name} використали {percent}% свого сховища}}"),
    "seats": ("{count, plural, offset:1 =0 {В этом рабочем пространстве не осталось мест} =1 {Осталось только ваше место} one {Вы и "
              "ещё # участник ещё можете присоединиться} few {Вы и ещё # участника ещё можете присоединиться} many {Вы и ещё # "
              "участников ещё можете присоединиться} other {Вы и ещё # участника ещё можете присоединиться}}",
              "{count, plural, offset:1 =0 {У цьому робочому просторі не залишилося місць} =1 {Залишилося лише ваше місце} one {Ви та "
              "ще # учасник ще можете приєднатися} few {Ви та ще # учасники ще можете приєднатися} many {Ви та ще # учасників ще "
              "можете приєднатися} other {Ви та ще # учасника ще можете приєднатися}}"),
})


def _i18n_x(seed: int, level: int) -> Item:
    """Levels 9-10 of i18n: all level-8 strings (18 / 21 keys with the new ones) and a character limit for EVERY string
    without ICU branches: at most the length of a natural translation plus 1-3 characters (the reference table meets every
    limit). The level-6+ checks per key, plus the limit."""
    r = rng(BLOCK, f"i18n{level}", seed)
    lang = r.choice(["ru", "uk"])
    col = 0 if lang == "ru" else 1
    new = [(k, v, lim) for k, v, _, lim in _UI_HARD] + [(k, v, None) for k, v, lv in _UI_X if lv <= level]
    old = r.sample([u for u in _UI if u[2] >= 2], {9: 4, 10: 6}[level])
    chosen = [(k, v, None) for k, v, _ in old] + new
    r.shuffle(chosen)
    src = {k: v for k, v, _ in chosen}
    limits = {}
    for k, v, lim in chosen:
        if "{" not in v or not re.search(r",\s*(plural|select)", v):
            limits[k] = min(lim, len(_I18N_REF[k][col]) + 2) if lim else len(_I18N_REF[k][col]) + r.randint(1, 3)
    gl = "; ".join(f"\"{w}\" as \"{_GLOSS_I18N[w][0][lang]}\"" for w in _GLOSS_I18N)
    notes = ["Keep every placeholder ({name}, %s, %1$s ...) and HTML tag exactly as in the source; a tag inside a plural or select "
             "branch stays in every branch that is built from it.",
             "Keep the brand name \"Nova Cloud\" untranslated.",
             f"Keep ICU MessageFormat syntax valid (including offset:1 and nested arguments): translate only the text, keep the "
             f"argument names, types and select keywords, keep explicit =N branches, and give EVERY plural (nested ones too) the "
             f"categories {_I18N_LANGS[lang]} needs: one, few, many, other.",
             f"Glossary (inflect as the grammar needs; placeholder names stay unchanged): translate {gl}.",
             "Every value without plural or select branches must fit its screen: at most " + ", ".join(f"{k} {n}" for k, n in sorted(limits.items()))
             + " characters (placeholders and tags count as written)."]
    prompt = (f"Translate the values of this app string table from English into {_I18N_LANGS[lang]}. Output only the JSON object "
              f"with the same keys.\n" + "\n".join(f"- {x}" for x in notes) + "\n\n" + json.dumps(src, ensure_ascii=False, indent=1))

    def check(text: str, _t=None) -> float:
        s = strip_think(text)
        m = re.search(r"\{.*\}", s, re.S)
        try:
            out = json.loads(m.group(0)) if m else None
        except ValueError:
            return 0.0
        if not isinstance(out, dict) or set(out) != set(src):
            return 0.0
        if detect_lang(" ".join(_icu2_text(str(v)) for v in out.values())) != "ru":
            return 0.0
        good = 0
        for k, sv in src.items():
            v = out.get(k)
            if not isinstance(v, str):
                continue
            ok = _icu2_ok(sv, v) and ("Nova Cloud" in v) == ("Nova Cloud" in sv)
            plain = re.sub(r"\{[^{}]*\}|<[^>]+>|%\S+", "", sv)
            if len(re.findall(r"[A-Za-z]{3,}", plain)) >= 2 and "Nova Cloud" not in sv:
                ok &= v.strip() != sv.strip() and len(re.findall(r"[А-Яа-яЁёІіЇїЄєҐґ]", v)) >= 3
            for w, (_tr, rx) in _GLOSS_I18N.items():
                if re.search(rf"(?<!\{{)\b{w}s?\b(?!\}})", sv):
                    ok &= re.search(rx[lang], v.lower()) is not None
            if k in limits:
                ok &= len(v) <= limits[k]
            good += bool(ok)
        return good / len(src)
    return Item(f"{BLOCK}.i18n.L{level}.{seed}", BLOCK, "i18n", [{"role": "user", "content": prompt}], check, lang=lang,
                meta={"level": level, "keys": list(src), "limits": limits})


# proofread levels 9-10: British noun / verb pairs (licence / license, practice / practise, advice / advise) as errors AND
# as right words that look American, errors that need the whole sentence (agreement over a long subject, its / it's in a
# quotation-free clause), and the level-6+ traps (quotations and code stay as written).
_PROOF_X = [
    "The council will {license|licence} two new market stalls this spring. Each trader needs a food hygiene {licence|license} and must "
    "renew it every year. Traders who {practise|practice} street cooking should also {advise|advice} the council of their opening hours. "
    "The {advice|advise} from last year's inspection {was|were} simple: keep a thermometer on every stall. The list of approved "
    "suppliers {is|are} published on the council's website, and several local farms {are|is} on it. A sign by the old fountain "
    "still reads \"No trading after dusk, by order of the counsel\", and nobody has corrected it.",
    "Our tennis club has moved its evening {practice|practise} to Thursdays. Members who wish to {practise|practice} on other days can "
    "book a court online, although the booking page {doesn't|don't} work well on older phones. The head coach, together with her two "
    "assistants, {has|have} agreed that juniors play first. Each of the new balls {costs|cost} more than last season's, so please return "
    "them. The script `resrve_court.py` sends the confirmation emails; its author says it will be {fixed|fixt} by the end of the month.",
    "If you are unsure whether a {licence|license} is needed, ask for {advice|advise} before you start the work. We {advise|advice} "
    "every tenant to keep copies of all letters. The landlord, not the tenants, {is|are} responsible for the gas safety certificate. "
    "One of the flats on the second storey {needs|need} a new boiler, and {its|it's} warranty has already expired. The notice in the "
    "hallway says \"Please do not leave you're bikes here\"; the committee has asked us to leave it as it is.",
    "Every Friday the clinic runs a drop-in session for parents. The paediatric nurse, who {has|have} worked here for twenty years, "
    "answers questions about sleep and feeding. Parents {who|whom} cannot attend can phone the practice between two and four. The "
    "doctors {advise|advice} that children under two should not be given honey. Neither the nurse nor the doctors {are|is} available on "
    "bank holidays. The waiting room has been {repainted|repaynted} in a calm shade of grey.",
    "The removal company packed forty boxes and loaded them onto the lorry before nine. Two of the boxes {were|was} marked fragile, "
    "but one of them {was|were} still dropped on the pavement. The driver, together with his assistant, {has|have} apologised in "
    "writing. We have been {advised|adviced} to claim for the broken lamp within fourteen days. The inventory, including the photos "
    "we took, {is|are} attached to this email. The label on the damaged box says `FRAGIEL - THIS WAY UP`, exactly as the company printed it.",
]


def _proofread_x(seed: int, level: int) -> Item:
    """Levels 9-10 of proofread: 4 / 5 paragraphs from the level-6+ bank and the level-9+ bank (at least 2 / 3 of the new
    ones), 22 / 28 injected errors. Graded as below: 1 - differing words / errors."""
    clean, dirty, n_err = _proofread_x_texts(seed, level)
    prompt = ("Proofread this text. Fix only spelling, grammar and capitalization errors; do not rephrase, reorder or change anything "
              "else, and keep the paragraphs. The text uses British spelling (licence and practice are nouns, license and practise "
              "are verbs): keep it. Text inside quotation marks is quoted verbatim and text inside backticks is code: leave both "
              "exactly as they are, even where they look wrong. Output only the corrected text.\n\n" + dirty)

    def toks(t: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9']+", (t or "").replace("’", "'"))

    def check(text: str, _t=None) -> float:
        a, b = toks(clean), toks(strip_think(text))
        diff = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
                   if op != "equal")
        return max(0.0, 1 - diff / n_err)
    return Item(f"{BLOCK}.proofread.L{level}.{seed}", BLOCK, "proofread", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "errors": n_err})


def _proofread_x_texts(seed: int, level: int) -> tuple[str, str, int]:
    r = rng(BLOCK, f"proofread{level}", seed)
    n_new = 2 if level == 9 else 3
    paras = r.sample(_PROOF_X, n_new) + r.sample(_PROOF_HARD, (4 if level == 9 else 5) - n_new)
    r.shuffle(paras)
    slots = [(pi, m.start()) for pi, p in enumerate(paras) for m in re.finditer(r"\{([^|}]*)\|([^}]*)\}", p)]
    n_err = min(len(slots), {9: 22, 10: 28}[level])
    bad = set(r.sample(slots, n_err))

    def render(pi: int, p: str, inject: bool) -> str:
        return re.sub(r"\{([^|}]*)\|([^}]*)\}", lambda m: m.group(2) if inject and (pi, m.start()) in bad else m.group(1), p)
    return ("\n\n".join(render(i, p, False) for i, p in enumerate(paras)), "\n\n".join(render(i, p, True) for i, p in enumerate(paras)),
            n_err)


MAX_LEVEL = 10
KINDS = {"constrained": constrained, "translate": translate, "summarize": summarize, "extract": extract, "rewrite": rewrite,
         "minutes": minutes, "i18n": i18n, "proofread": proofread}
# the quick tier keeps the kinds that still discriminate at level 5 plus two classic generation tasks
QUICK = ["constrained", "rewrite", "minutes", "i18n", "proofread"]
