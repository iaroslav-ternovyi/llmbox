"""Writing, editing, translation, summarization, extraction - verifiable constraints, multilingual, 5 difficulty levels.

Level raises the number of simultaneous constraints and adds traps (changed decisions, corrections, glossaries).
Score = fraction of constraints met.
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


def constrained(seed: int, level: int = 3) -> Item:
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
        return 1.0 if all(bool(f(t)) for f in tests) else 0.0   # strict: every constraint
    return Item(f"{BLOCK}.constrained.L{level}.{seed}", BLOCK, "constrained", [{"role": "user", "content": prompt}], check,
                lang=lang, meta={"level": level})


def translate(seed: int, level: int = 3) -> Item:
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
        return 1.0 if all(tests) else 0.0   # strict: every constraint
    return Item(f"{BLOCK}.translate.L{level}.{seed}", BLOCK, "translate", [{"role": "user", "content": prompt}], check,
                lang=target, meta={"level": level})


def summarize(seed: int, level: int = 3) -> Item:
    """Email thread where decisions change; only the final values count."""
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
        return 1.0 if all(tests) else 0.0   # strict: every constraint
    return Item(f"{BLOCK}.summarize.L{level}.{seed}", BLOCK, "summarize", [{"role": "user", "content": prompt}], check,
                lang=lang, meta={"level": level, "expected": f"{final_day} {final_month} / {budgets[-1]} / {owners[-1]}"})


def extract(seed: int, level: int = 3) -> Item:
    """Several orders in one message, with corrections at higher levels; output a JSON array."""
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
        return 1.0 if all(tests) else 0.0   # strict: every constraint
    return Item(f"{BLOCK}.extract.L{level}.{seed}", BLOCK, "extract", [{"role": "user", "content": prompt}], check,
                meta={"level": level, "expected": orders})


def rewrite(seed: int, level: int = 3) -> Item:
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
        return 1.0 if all(bool(f(t)) for f in tests) else 0.0   # strict: every constraint
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


KINDS = {"constrained": constrained, "translate": translate, "summarize": summarize, "extract": extract, "rewrite": rewrite,
         "minutes": minutes, "i18n": i18n, "proofread": proofread}
# the quick tier keeps the kinds that still discriminate at level 5 plus two classic generation tasks
QUICK = ["constrained", "rewrite", "minutes", "i18n", "proofread"]
