"""Agents & automation: multi-step function calling against a simulated CRM; graded on the final state of the world.

Each item owns a fresh generated world (customers, invoices) and records every side effect (emails, tasks, discounts,
payments). Wrong or extra side effects cost points - a real agent must not email the wrong people.
v0.6 adds the two kinds that still separate strong models at level 5 (v0.5: Tiel solved every older kind at level 5):
`dunning` (a written first-match policy applied per customer, facts spread over several tools) and `reconcile`
(bank statement -> payments/tasks, bank formatting and traps). Both are graded per customer / per statement line.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from .common import Item, final_answer, num, rng

BLOCK = "tools"
CITIES = ["Madrid", "Valencia", "Porto", "Lyon", "Graz", "Tartu"]
TIERS = ["gold", "silver", "bronze"]
FIRST = ["Lucía", "Marco", "Hanna", "Pierre", "Olga", "Tomás", "Inés", "Yusuf", "Mei", "Jonas", "Sara", "Omar",
         "Nuria", "Pavel", "Aiko", "Bruno", "Clara", "Diego", "Elena", "Felix", "Gaia", "Hugo", "Irina", "Karim"]
LAST = ["García", "Rossi", "Müller", "Dubois", "Ivanova", "Silva", "Novak", "Kaya", "Chen", "Berg", "Ortiz", "Haddad",
        "Lindqvist", "Moreno", "Nakamura", "Petrov", "Quinn", "Romero", "Schmidt", "Tanaka", "Ueda", "Varga", "Weber", "Zhou"]
TODAY = dt.date(2026, 9, 25)

TOOLS = [
    {"type": "function", "function": {"name": "search_customers", "description": "Find customers. All filters optional and combined with AND.",
     "parameters": {"type": "object", "properties": {"city": {"type": "string"}, "tier": {"type": "string", "enum": TIERS},
                                                     "name": {"type": "string", "description": "substring of the full name"},
                                                     "page": {"type": "integer", "description": "page number when results are paginated"}}}}},
    {"type": "function", "function": {"name": "get_customer", "description": "Full customer record incl. email.",
     "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}}},
    {"type": "function", "function": {"name": "list_invoices", "description": "List invoices, optionally filtered.",
     "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}, "status": {"type": "string", "enum": ["paid", "unpaid"]},
                                                     "page": {"type": "integer", "description": "page number when results are paginated"}}}}},
    {"type": "function", "function": {"name": "get_email_log", "description": "Emails already sent to a customer (newest first).",
     "parameters": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}}},
    {"type": "function", "function": {"name": "send_email", "description": "Send an email to a customer.",
     "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
                    "required": ["to", "subject", "body"]}}},
    {"type": "function", "function": {"name": "create_task", "description": "Create a task in the team tracker.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                                                     "team": {"type": "string"}}, "required": ["title", "due_date", "team"]}}},
    {"type": "function", "function": {"name": "apply_discount", "description": "Apply a percentage discount to an unpaid invoice.",
     "parameters": {"type": "object", "properties": {"invoice_id": {"type": "string"}, "percent": {"type": "number"}},
                    "required": ["invoice_id", "percent"]}}},
    {"type": "function", "function": {"name": "record_payment", "description": "Record a received payment against an unpaid invoice. "
     "The invoice becomes paid once the payments cover its amount.",
     "parameters": {"type": "object", "properties": {"invoice_id": {"type": "string"}, "amount": {"type": "number"},
                                                     "date": {"type": "string", "description": "payment date YYYY-MM-DD"}},
                    "required": ["invoice_id", "amount", "date"]}}},
]
SYSTEM = (f"You are an operations assistant with access to the company CRM tools. Today is {TODAY.isoformat()}. "
          "Use the tools to complete the request. Do exactly what is asked - no extra emails, tasks or discounts. "
          "When you are done, reply with a short confirmation.")


class World:
    def __init__(self, r, flaky: int = 0, level: int = 1):
        self.customers, self.invoices = {}, {}
        self.page = 10 if level >= 3 else None       # list endpoints paginate from level 3
        n_c = 36 + 60 * (level - 1)
        pool = [f"{f} {l}" for f in FIRST for l in LAST]
        names = r.sample(pool, min(n_c, len(pool)))
        for i, n in enumerate(names):
            cid = f"C-{100 + i}"
            self.customers[cid] = {"id": cid, "name": n, "city": r.choice(CITIES), "tier": r.choice(TIERS),
                                   "email": f"{n.split()[0].lower()}.{n.split()[1].lower()}@example.com".replace("í", "i").replace("á", "a").replace("ü", "u").replace("é", "e")}
        for k in range(90 + 90 * (level - 1)):
            iid = f"INV-{4000 + k}"
            self.invoices[iid] = {"id": iid, "customer_id": r.choice(list(self.customers)), "amount": round(r.uniform(40, 2400), 2),
                                  "status": r.choice(["paid", "paid", "unpaid"]),
                                  "due_date": (TODAY + dt.timedelta(days=r.randint(-90, 60))).isoformat()}
        self.emails, self.tasks, self.discounts, self.flaky, self.calls, self.fails = [], [], [], flaky, 0, 0
        self.payments = []
        self.rate_limit = level >= 5          # every 9th call fails with 429 and must be retried
        self.log = {}                         # customer_id -> [(date, subject)] emails sent before today
        for cid in r.sample(list(self.customers), len(self.customers) // 4):
            self.log[cid] = [((TODAY - dt.timedelta(days=r.randint(1, 20))).isoformat(), r.choice(["Newsletter", "Payment reminder", "Welcome"]))
                             for _ in range(r.randint(1, 2))]

    def recently_emailed(self, cid: str, days: int = 7) -> bool:
        return any(d >= (TODAY - dt.timedelta(days=days)).isoformat() for d, _ in self.log.get(cid, []))

    def call(self, name: str, a: dict):
        self.calls += 1
        if self.rate_limit and self.calls % 9 == 0:
            return {"error": "429 Too Many Requests - nothing was done, retry the same call."}
        if name == "get_email_log":
            return [{"date": d, "subject": sub} for d, sub in sorted(self.log.get(a.get("customer_id"), []), reverse=True)]
        if name == "search_customers":
            if self.fails < self.flaky:
                self.fails += 1
                return {"error": "CRM temporarily unavailable (503). Please retry."}
            out = [c for c in self.customers.values()
                   if (not a.get("city") or c["city"].lower() == str(a["city"]).lower())
                   and (not a.get("tier") or c["tier"] == a["tier"]) and (not a.get("name") or str(a["name"]).lower() in c["name"].lower())]
            return self._paged([{k: c[k] for k in ("id", "name", "city", "tier")} for c in out], a)
        if name == "get_customer":
            c = self.customers.get(a.get("customer_id"))
            return c if c else {"error": "customer not found"}
        if name == "list_invoices":
            return self._paged([i for i in self.invoices.values()
                                if (not a.get("customer_id") or i["customer_id"] == a["customer_id"]) and (not a.get("status") or i["status"] == a["status"])], a)
        if name == "send_email":
            self.emails.append({"to": a.get("to"), "subject": a.get("subject", ""), "body": a.get("body", "")})
            return {"ok": True, "message_id": f"M{len(self.emails)}"}
        if name == "create_task":
            self.tasks.append(a)
            return {"ok": True, "task_id": f"T{len(self.tasks)}"}
        if name == "apply_discount":
            inv = self.invoices.get(a.get("invoice_id"))
            if not inv:
                return {"error": "invoice not found"}
            if inv["status"] == "paid":
                return {"error": "invoice already paid"}
            self.discounts.append({"invoice_id": inv["id"], "percent": a.get("percent")})
            return {"ok": True}
        if name == "record_payment":
            inv = self.invoices.get(a.get("invoice_id"))
            if not inv:
                return {"error": "invoice not found"}
            if inv["status"] == "paid":
                return {"error": "invoice already paid - nothing recorded"}
            amt = num(str(a.get("amount")))
            if amt is None or amt <= 0:
                return {"error": "amount must be a positive number"}
            self.payments.append({"invoice_id": inv["id"], "amount": round(amt, 2), "date": str(a.get("date", ""))})
            paid = sum(p["amount"] for p in self.payments if p["invoice_id"] == inv["id"])
            if paid >= inv["amount"] - 0.005:
                inv["status"] = "paid"
            return {"ok": True, "invoice_status": inv["status"], "remaining": round(max(inv["amount"] - paid, 0), 2)}
        return {"error": f"unknown tool {name}"}

    def _paged(self, rows: list, a: dict):
        if not self.page:
            return rows
        p = int(a.get("page") or 1)
        chunk = rows[(p - 1) * self.page: p * self.page]
        return {"results": chunk, "page": p, "total_results": len(rows),
                "next_page": p + 1 if p * self.page < len(rows) else None}

    def unpaid(self, cid: str):
        return [i for i in self.invoices.values() if i["customer_id"] == cid and i["status"] == "unpaid"]


def _item(kind: str, seed: int, user: str, check, world: World, meta: dict, tools: list | None = None) -> Item:
    return Item(f"{BLOCK}.{kind}.L{meta.get('level', 1)}.{seed}", BLOCK, kind,
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                check, tools=tools or TOOLS, tool_impl=world.call, max_tokens=32000,
                meta=dict(meta, max_steps=20 + 16 * meta.get("level", 1)))


def _side_effect_free(w: World) -> bool:
    return not (w.emails or w.tasks or w.discounts or w.payments)


def _credit(right: int, n_expected: int, wrong: int) -> float:
    """v0.10 partial credit: the share of the expected actions done right, and every wrong or extra action cancels one
    right one (an agent that also emails the wrong people is not half right). Nothing expected: 1 only if nothing done."""
    if not n_expected:
        return 1.0 if not wrong else 0.0
    return max(0.0, (right - wrong) / n_expected)


def _team(task: dict) -> str:
    """'Billing Team', 'billing', 'account management' -> canonical team id (the request names the team in prose)."""
    t = re.sub(r"\bteam\b", "", str(task.get("team", "")).lower())
    return re.sub(r"[\s_]+", "-", t.strip()).strip("-")


def _business_days(start: dt.date, n: int) -> dt.date:
    d = start
    while n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def reminders(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"reminders{level}", seed)
    w = World(r, level=level)
    city, tier, thr = r.choice(CITIES), r.choice(TIERS), r.choice([300, 600, 900])
    overdue_only = level >= 4
    exp = {(c["email"], i["id"]) for c in w.customers.values() if c["city"] == city and (level >= 5 or c["tier"] == tier)
           and not (level >= 4 and w.recently_emailed(c["id"]))
           for i in w.unpaid(c["id"]) if i["amount"] > thr and (not overdue_only or i["due_date"] < TODAY.isoformat())}
    if not exp:
        return reminders(seed + 10_000, level)
    who = f"a customer in {city}" if level >= 5 else f"a {tier}-tier customer in {city}"
    user = (f"Send a payment reminder email for every unpaid invoice above {thr} EUR" + (" that is already overdue" if overdue_only else "")
            + f" and belongs to {who}. Send one email per invoice to the customer's email address, and put the invoice id in the subject."
            + (" Skip customers who already received ANY email in the last 7 days (check their email log)." if level >= 4 else ""))

    def check(_text, _t=None) -> float:
        hit, wrong = set(), len(w.tasks) + len(w.discounts) + len(w.payments)
        for e in w.emails:
            pair = next(((e["to"], iid) for iid in re.findall(r"INV-\d+", e["subject"] or "") if (e["to"], iid) in exp), None)
            if pair and pair not in hit:
                hit.add(pair)
            else:
                wrong += 1     # wrong recipient or invoice, or a second email for the same invoice
        return _credit(len(hit), len(exp), wrong)
    return _item("reminders", seed, user, check, w, {"expected": sorted(exp), "level": level})


def followup(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"followup{level}", seed)
    w = World(r, level=level)
    invs = r.sample(list(w.invoices.values()), 2 if level >= 5 else 1)
    due = (TODAY + dt.timedelta(days=r.randint(2, 14))).isoformat()
    team = r.choice(["billing", "support", "sales"])
    if level >= 3:  # refer to the invoice by customer + amount instead of id
        refs = [f"the {i['amount']:.2f} EUR invoice of {w.customers[i['customer_id']]['name']}" for i in invs]
    else:
        refs = [i["id"] for i in invs]
    user = (f"{' and '.join(refs)} {'are' if len(invs) > 1 else 'is'} disputed. Create one follow-up task per disputed invoice for "
            f"the {team} team, due {due}, whose title contains the customer's full name and the invoice id.")

    def check(_text, _t=None) -> float:
        right, used = 0, set()
        for inv in invs:
            name = w.customers[inv["customer_id"]]["name"]
            k = next((k for k, t in enumerate(w.tasks) if k not in used and inv["id"] in (t.get("title") or "")), None)
            if k is not None:
                used.add(k)
                t = w.tasks[k]
                right += name in (t.get("title") or "") and t.get("due_date") == due and _team(t) == team
        wrong = (len(w.tasks) - len(used)) + len(used) - right + len(w.emails) + len(w.discounts) + len(w.payments)
        return _credit(right, len(invs), wrong)
    return _item("followup", seed, user, check, w, {"expected": [i["id"] for i in invs], "level": level})


def total(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"total{level}", seed)
    w = World(r, level=level)
    tier = r.choice(TIERS)
    if level >= 4:  # which city has the largest unpaid total for this tier, and how much
        per = {c: round(sum(i["amount"] for cu in w.customers.values() if cu["city"] == c and cu["tier"] == tier
                            for i in w.unpaid(cu["id"])), 2) for c in CITIES}
        city = max(per, key=per.get)
        amt = per[city]
        user = (f"Across all cities, which city has the largest total of UNPAID invoices of {tier}-tier customers, and what is that "
                f"total? Do not change anything. End your reply with a line 'ANSWER: <city>; <amount>'.")

        def check(text, _t=None) -> float:
            parts = [p.strip() for p in (final_answer(text) or "").split(";")]
            ok_c = bool(parts) and parts[0].lower() == city.lower()
            ok_a = len(parts) > 1 and num(parts[1]) is not None and abs(num(parts[1]) - amt) <= 0.02
            return (ok_c + ok_a) / 2 if _side_effect_free(w) else 0.0   # the city and its total, half each
        return _item("total", seed, user, check, w, {"expected": f"{city}; {amt}", "level": level})
    city = r.choice(CITIES)
    amt = round(sum(i["amount"] for c in w.customers.values() if c["city"] == city and c["tier"] == tier for i in w.unpaid(c["id"])), 2)
    user = (f"What is the total amount of UNPAID invoices of all {tier}-tier customers in {city}? Do not change anything. "
            f"End your reply with a line 'ANSWER: <amount>'.")

    def check(text, _t=None) -> float:
        v = num(final_answer(text))
        return 1.0 if v is not None and abs(v - amt) <= 0.02 and _side_effect_free(w) else 0.0
    return _item("total", seed, user, check, w, {"expected": amt, "level": level})


def conditional(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"conditional{level}", seed)
    w = World(r, level=level)
    today = TODAY.isoformat()
    if level >= 4:  # tiered rule across a whole city
        city = r.choice(CITIES)
        exp = {}
        for c in w.customers.values():
            if c["city"] != city:
                continue
            un = w.unpaid(c["id"])
            tot = sum(i["amount"] for i in un)
            pct = 10 if tot > 2000 else 5 if tot > 1000 else 0
            if pct and un:
                oldest = min(un, key=lambda i: (i["due_date"], i["id"]))
                exp[oldest["id"]] = pct
        user = (f"For every customer in {city}: if their total UNPAID amount is above 2000 EUR apply a 10% discount, if it is above "
                f"1000 EUR (up to 2000) apply 5%; apply the discount only to that customer's unpaid invoice with the earliest due "
                f"date. Customers at or below 1000 EUR get nothing. Do not send emails or create tasks.")

        def check(text, _t=None) -> float:
            right, seen = 0, set()
            wrong = len(w.emails) + len(w.tasks) + len(w.payments)
            for d in w.discounts:
                ok = d["invoice_id"] in exp and d["invoice_id"] not in seen and num(str(d["percent"])) == float(exp[d["invoice_id"]])
                seen.add(d["invoice_id"])
                right, wrong = right + ok, wrong + (not ok)
            return _credit(right, len(exp), wrong)
        return _item("conditional", seed, user, check, w, {"expected": exp, "level": level})
    cust = r.choice(list(w.customers.values()))
    overdue = sorted([i for i in w.unpaid(cust["id"]) if i["due_date"] < today], key=lambda i: i["due_date"])
    pct = r.choice([5, 7, 10])
    user = (f"If {cust['name']} has any unpaid invoice that is overdue (due date before today), apply a {pct}% discount to "
            f"the OLDEST overdue unpaid invoice only. If there is none, do not change anything and say NO_ACTION.")

    def check(text, _t=None) -> float:
        if w.emails or w.tasks:
            return 0.0
        if not overdue:
            return 1.0 if not w.discounts else 0.0
        return 1.0 if len(w.discounts) == 1 and w.discounts[0]["invoice_id"] == overdue[0]["id"] and float(w.discounts[0]["percent"]) == pct else 0.0
    return _item("conditional", seed, user, check, w, {"expected": overdue[0]["id"] if overdue else "NO_ACTION", "level": level})


def recovery(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"recovery{level}", seed)
    w = World(r, flaky=1 if level < 3 else 2, level=level)
    cust = r.choice(list(w.customers.values()))
    first = cust["name"].split()[0]
    same = [c for c in w.customers.values() if c["city"] == cust["city"] and c["tier"] == cust["tier"] and c["name"].startswith(first + " ")]
    if len(same) != 1:
        return recovery(seed + 10_000, level)
    user = (f"Email the {cust['tier']}-tier customer in {cust['city']} whose first name is {first}: subject 'Account review', "
            f"and a one-sentence body inviting them to book a call.")

    def check(_text, _t=None) -> float:
        if len(w.emails) != 1 or w.tasks or w.discounts:
            return 0.0
        e = w.emails[0]
        return 1.0 if e["to"] == cust["email"] and "account review" in (e["subject"] or "").lower() else 0.0
    return _item("recovery", seed, user, check, w, {"expected": cust["email"], "level": level})


def _has_amount(text: str, amt: float) -> bool:
    us = f"{amt:,.2f}"
    return any(v in text for v in (f"{amt:.2f}", us, us.replace(",", "X").replace(".", ",").replace("X", "."),
                                   us.replace(",", " "), f"{amt:.2f}".replace(".", ",")))


def _plain(name: str) -> str:
    return name.translate(str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU"))


def dunning(seed: int, level: int = 3) -> Item:
    """Monthly dunning run by a written policy: per-customer decisions that need several lookups each (flags that only
    get_customer shows, disputed invoices, the email log) and first-match rule precedence. Graded per customer."""
    r = rng(BLOCK, f"dunning{level}", seed)
    w = World(r, level=level)
    city, tier = r.choice(CITIES), r.choice(TIERS)
    today = TODAY.isoformat()
    if level >= 3:
        for i in w.invoices.values():
            if i["status"] == "unpaid" and r.random() < 0.18:
                i["disputed"] = True
    if level >= 4:
        for c in w.customers.values():
            if r.random() < 0.3:
                c["billing_email"] = f"billing@{c['name'].split()[1].lower()}-{c['id'][2:]}.example.org".translate(str.maketrans("áéíóúü", "aeiouu"))
            if r.random() < 0.15:
                c["do_not_contact"] = True
    due3 = _business_days(TODAY, 3).isoformat()
    tiers = [tier] if level == 1 else [t for t in TIERS if t != tier] if level == 4 else TIERS
    scope = [c for c in w.customers.values() if c["city"] == city and c["tier"] in tiers]
    exp, traps = {}, set()
    for c in scope:
        over = [i for i in w.unpaid(c["id"]) if i["due_date"] < today]
        live = [i for i in over if not i.get("disputed")]
        if not over:
            continue
        if not live:
            traps.add(c["id"])
            continue
        if level >= 5 and any((TODAY - dt.date.fromisoformat(i["due_date"])).days > 45 for i in live):
            exp[c["id"]] = ("task", "account-management" if c["tier"] == "gold" else "collections")
        elif level >= 4 and c.get("do_not_contact"):
            exp[c["id"]] = ("task", "account-management")
        elif level >= 4 and w.recently_emailed(c["id"]):
            traps.add(c["id"])
        else:
            exp[c["id"]] = ("email", c.get("billing_email") or c["email"], frozenset(i["id"] for i in live),
                            round(sum(i["amount"] for i in live), 2) if level >= 5 else None)
    if len(exp) < 2 + level:
        return dunning(seed + 10_000, level)
    who = (f"every {tier}-tier customer in {city}" if level == 1 else f"every {tiers[0]}-tier and {tiers[1]}-tier customer in {city}"
           if level == 4 else f"every customer in {city}")
    rules = ["Consider only invoices that are unpaid and overdue (due date before today)"
             + (" and not disputed (disputed invoices are flagged on the invoice)" if level >= 3 else "")
             + ". Customers without such invoices get nothing."]
    if level >= 5:
        rules.append("If any of those invoices is more than 45 days overdue: do not email; create ONE task for the collections team "
                     "(for gold-tier customers: the account-management team instead), due 3 business days from today, whose title "
                     "contains the customer id.")
    if level >= 4:
        rules.append("Otherwise, if the customer record is flagged do_not_contact: do not email; create ONE task for the "
                     "account-management team, due 3 business days from today, whose title contains the customer id.")
        rules.append("Otherwise, if the customer received ANY email in the last 7 days (see their email log): do nothing.")
    rules.append(("Otherwise: " if level >= 4 else "")
                 + "send ONE email to the customer with subject 'Overdue invoices' that lists the ids of all those invoices"
                 + (" and states their total in EUR with cents" if level >= 5 else "")
                 + (", to the customer's billing email if their record has one, else to their main email." if level >= 4 else "."))
    user = (f"Run this month's dunning for {who}. Apply the policy below to each customer; for a customer the first rule that "
            f"applies wins.\n\n" + "\n".join(f"{k}. {t}" for k, t in enumerate(rules, 1))
            + "\n\nDo not do anything else: no discounts, no payments, no other emails or tasks.")

    def check(_text, _t=None) -> float:
        if w.discounts or w.payments:
            return 0.0
        by_addr = {}
        for c in w.customers.values():
            by_addr[c["email"]] = c["id"]
            if c.get("billing_email"):
                by_addr[c["billing_email"]] = c["id"]
        acts: dict = {}
        stray = 0
        for e in w.emails:
            cid = by_addr.get(str(e.get("to", "")).strip().lower())
            if cid is None:
                stray += 1
                continue
            text = f"{e.get('subject', '')} {e.get('body', '')}"
            ids = frozenset(re.findall(r"INV-\d+", text))
            want = exp.get(cid)
            tot = want[3] if want and want[0] == "email" and want[3] is not None and _has_amount(text, want[3]) else None
            acts.setdefault(cid, []).append(("email", str(e["to"]).strip().lower(), ids, tot))
        for t in w.tasks:
            m = re.findall(r"C-\d+", str(t.get("title", "")))
            if len(set(m)) != 1 or m[0] not in w.customers:
                stray += 1
                continue
            ok_due = t.get("due_date") == due3
            acts.setdefault(m[0], []).append(("task", _team(t) if ok_due else "WRONG-DUE"))
        judged = set(exp) | traps | set(acts)
        good = 0
        for cid in judged:
            got, want = acts.get(cid, []), exp.get(cid)
            if want is None:
                good += not got
            elif want[0] == "email":
                good += got == [want]
            else:
                good += got == [want]
        return good / (len(judged) + stray)
    return _item("dunning", seed, user, check, w, {"expected": {k: [str(x) for x in v] for k, v in exp.items()},
                                                    "traps": sorted(traps), "level": level})


def reconcile(seed: int, level: int = 3) -> Item:
    """Bank-statement reconciliation: match each line to an invoice (by id, or by payer + exact amount), record payments,
    and file a task for every line that cannot be recorded. Bank formatting (upper-case payer without accents, European
    amounts), partial payments, duplicates and payments for already-paid invoices by level. Graded per statement line."""
    r = rng(BLOCK, f"reconcile{level}", seed)
    w = World(r, level=level)
    n = [0, 4, 6, 9, 12, 16][level]
    unpaid = [i for i in w.invoices.values() if i["status"] == "unpaid"]
    paid = [i for i in w.invoices.values() if i["status"] == "paid"]
    used, lines = set(), []
    kinds = ["id"] * 3 + ["noid"] * (2 if level >= 2 else 0) + ["unmatched"] * (1 if level >= 2 else 0) \
        + ["partial"] * (1 if level >= 3 else 0) + ["duplicate"] * (1 if level >= 4 else 0) + ["ambiguous", "paidref"] * (level >= 5)
    nbd = _business_days(TODAY, 1).isoformat()

    def fmt_amount(a: float) -> str:
        if level >= 4:
            return f"{a:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{a:.2f}"

    def payer(cid: str) -> str:
        name = w.customers[cid]["name"]
        if level >= 3:
            f, l = _plain(name).upper().split()
            return f"{l} {f}"
        return name

    def fresh(pred=lambda i: True):
        pool = [i for i in unpaid if i["id"] not in used and pred(i)]
        if not pool:
            return None
        inv = r.choice(pool)
        used.add(inv["id"])
        return inv

    def unique_amount(inv) -> bool:
        return sum(1 for j in w.unpaid(inv["customer_id"]) if abs(j["amount"] - inv["amount"]) < 0.005) == 1

    special = [k for k in dict.fromkeys(kinds) if k not in ("id", "noid")]   # every trap the level enables appears at least once
    guard = 0
    while len(lines) < n and guard < 500:
        guard += 1
        k = special.pop(0) if special and len(lines) >= 2 else r.choice(kinds)
        date = (TODAY - dt.timedelta(days=r.randint(0, 4))).isoformat()
        tx = f"TX-{r.randint(10000, 99999)}"
        if k == "id" and (inv := fresh()):
            lines.append({"tx": tx, "date": date, "payer": payer(inv["customer_id"]), "amount": inv["amount"],
                          "ref": r.choice([f"{inv['id']}", f"Payment {inv['id']}", f"inv {inv['id']} thanks"]), "exp": ("pay", inv["id"], inv["amount"], date)})
        elif k == "noid" and (inv := fresh(unique_amount)):
            lines.append({"tx": tx, "date": date, "payer": payer(inv["customer_id"]), "amount": inv["amount"],
                          "ref": r.choice(["September payment", "Transfer", "Invoice payment"]), "exp": ("pay", inv["id"], inv["amount"], date)})
        elif k == "partial" and (inv := fresh(lambda i: i["amount"] > 200)):
            part = round(inv["amount"] * r.uniform(0.3, 0.8), 2)
            lines.append({"tx": tx, "date": date, "payer": payer(inv["customer_id"]), "amount": part,
                          "ref": f"Partial payment {inv['id']}", "exp": ("pay", inv["id"], part, date)})
        elif k == "unmatched":
            if r.random() < 0.5:
                who = r.choice(["NORDIC SUPPLY AB", "ACME TRADING SL", "J. PEREIRA LDA", "BLUE HARBOUR LTD"])
                amt = round(r.uniform(40, 2400), 2)
            else:
                cid = r.choice(list(w.customers))
                who = payer(cid)
                amt = round(r.uniform(40, 2400), 2)
                if any(abs(j["amount"] - amt) < 0.01 for j in w.unpaid(cid)):
                    continue
            lines.append({"tx": tx, "date": date, "payer": who, "amount": amt, "ref": r.choice(["Transfer", "Payment", "Ref 2026/09"]),
                          "exp": ("task", tx)})
        elif k == "duplicate":
            prev = [x for x in lines if x["exp"][0] == "pay" and not x["ref"].startswith("Partial")]
            if not prev:
                continue
            x = r.choice(prev)
            lines.append(dict(x, tx=tx, exp=("task", tx)))
        elif k == "ambiguous" and (inv := fresh(unique_amount)):
            twin_id = f"INV-{4000 + len(w.invoices)}"
            shift = r.choice([-1, 1]) * r.randint(5, 40)
            w.invoices[twin_id] = dict(inv, id=twin_id, due_date=(dt.date.fromisoformat(inv["due_date"]) + dt.timedelta(days=shift)).isoformat())
            used.add(twin_id)
            first = min((inv, w.invoices[twin_id]), key=lambda i: i["due_date"])
            lines.append({"tx": tx, "date": date, "payer": payer(inv["customer_id"]), "amount": inv["amount"],
                          "ref": "Transfer", "exp": ("pay", first["id"], inv["amount"], date)})
        elif k == "paidref":
            inv = r.choice(paid)
            lines.append({"tx": tx, "date": date, "payer": payer(inv["customer_id"]), "amount": inv["amount"],
                          "ref": f"Payment {inv['id']}", "exp": ("task", tx)})
        else:
            if k not in ("id", "noid", "unmatched"):
                special.append(k)
    lines.sort(key=lambda x: x["date"])
    stmt = ["tx_id | date | payer | amount_eur | reference"] + [
        f"{x['tx']} | {x['date']} | {x['payer']} | {fmt_amount(x['amount'])} | {x['ref']}" for x in lines]
    rules = ["A line pays an invoice when its reference contains the invoice id, or - when it has no invoice id - when the payer "
             "is a customer and the amount equals exactly one of that customer's unpaid invoices."]
    if level >= 5:
        rules.append("If a line without an invoice id matches several unpaid invoices of that customer with exactly that amount, "
                     "apply it to the one with the earliest due date.")
    if level >= 3:
        rules.append("A line that pays less than the invoice amount is a partial payment: record the amount received.")
    if level >= 4:
        rules.append("A line with the same date, payer, amount and reference as an earlier line is a bank duplicate: do not "
                     "record it again (it still gets a task, see below).")
    rules.append("Record every payment with record_payment using the invoice id, the amount received and the statement date.")
    rules.append(f"Every line that is not recorded, whatever the reason, gets ONE task for the billing team, due the next "
                 f"business day after today, whose title contains the line's tx_id.")
    user = ("Reconcile today's bank statement against the CRM invoices.\n\n" + "\n".join(stmt)
            + ("\n\nAmounts use the European format (1.234,56 = one thousand two hundred thirty-four euros fifty-six)." if level >= 4 else "")
            + ("\nPayer names come from the bank: upper case, surname first, no accents." if level >= 3 else "")
            + "\n\nRules:\n" + "\n".join(f"- {t}" for t in rules)
            + "\nDo not send emails or apply discounts. Finish with a short summary.")

    def check(_text, _t=None) -> float:
        if w.emails or w.discounts:
            return 0.0
        pays = [(p["invoice_id"], p["amount"], p["date"]) for p in w.payments]
        tasks = [t for t in w.tasks]
        good = 0
        for x in lines:
            e = x["exp"]
            if e[0] == "pay":
                hit = next((p for p in pays if p[0] == e[1] and abs(p[1] - e[2]) <= 0.01 and p[2] == e[3]), None)
                if hit:
                    pays.remove(hit)
                    good += 1
            else:
                hit = next((t for t in tasks if e[1] in str(t.get("title", "")) and _team(t) == "billing"
                            and t.get("due_date") == nbd), None)
                if hit:
                    tasks.remove(hit)
                    good += 1
        return good / (len(lines) + len(pays) + len(tasks))
    return _item("reconcile", seed, user, check, w, {"expected": [[x["tx"]] + [str(v) for v in x["exp"]] for x in lines], "level": level})



# ---- expert level (6): calibrated so the frontier reference solves only part of it -------------------------------

class OutreachWorld(World):
    """World with the mess real CRMs have: merged duplicate records, names with diacritics, emails with stray spaces,
    and a mail API whose 504s are ambiguous - some failed sends were actually delivered."""

    def __init__(self, r, level: int = 5):
        super().__init__(r, level=level)
        self.r2 = r
        self.send_calls = 0
        for c in self.customers.values():
            c.setdefault("merged_into", None)

    def call(self, name: str, a: dict):
        if name == "send_email" and not (self.rate_limit and (self.calls + 1) % 9 == 0):
            self.calls += 1
            self.send_calls += 1
            to = str(a.get("to", "")).strip().lower()
            flaky = self.r2.random() < 0.3
            delivered = (not flaky) or self.r2.random() < 0.5
            if delivered:
                self.emails.append({"to": a.get("to"), "subject": a.get("subject", ""), "body": a.get("body", "")})
                cid = next((c["id"] for c in self.customers.values() if c["email"].strip().lower() == to), None)
                if cid:
                    self.log.setdefault(cid, []).append((TODAY.isoformat(), a.get("subject", "")))
            if flaky:
                return {"error": "504 Gateway Timeout - the mail service did not confirm; the message may or may not have been sent."}
            return {"ok": True, "message_id": f"M{len(self.emails)}"}
        return super().call(name, a)


def _plain_upper(name: str) -> str:
    return _plain(name).upper()


def outreach(seed: int, level: int = 6) -> Item:
    """Win-back outreach from a messy sales list (expert). Per list line the right outcome is one email to the right
    (primary) record, a collections task, a skip, or 'not in the CRM'; duplicates of a person, doubled emails after an
    ambiguous 504 and actions on people not on the list are all wrong."""
    r = rng(BLOCK, f"outreach{level}", seed)
    w = OutreachWorld(r, level=5)
    today = TODAY.isoformat()
    custs = list(w.customers.values())
    accented = [c for c in custs if c["name"] != _plain(c["name"])]
    plain = [c for c in custs if c["name"] == _plain(c["name"])]
    lines, exp = [], []            # exp: (line, kind, primary_id or None)

    def overdue30(c):
        return any(i["due_date"] < (TODAY - dt.timedelta(days=30)).isoformat() for i in w.unpaid(c["id"]))

    def fmt(c, style):
        f, l = c["name"].split()
        return {0: f"{_plain_upper(l)}, {_plain_upper(f)}", 1: f"{_plain(f).lower()} {_plain(l).lower()}", 2: f"{l} {f}"}[style]

    def clean_for_email(c):
        w.log.pop(c["id"], None)
        for i in w.invoices.values():
            if i["customer_id"] == c["id"] and i["status"] == "unpaid" and i["due_date"] < (TODAY - dt.timedelta(days=30)).isoformat():
                i["status"] = "paid"
    used = set()

    def pick(pool):
        c = r.choice([x for x in pool if x["id"] not in used])
        used.add(c["id"])
        return c
    # 1) accented names written without accents (search is accent-sensitive): plain email expected
    for _ in range(6):
        c = pick(accented); clean_for_email(c)
        lines.append(f"{fmt(c, r.choice([0, 1]))} ({c['city']})"); exp.append(("email", c["id"]))
    # 2) merged duplicates: the old record (plain name, old address) points to the primary
    for _ in range(2):
        c = pick(accented); clean_for_email(c)
        dup_id = f"C-{100 + len(w.customers)}"
        w.customers[dup_id] = {"id": dup_id, "name": _plain(c["name"]), "city": c["city"], "tier": c["tier"],
                               "email": f"{_plain(c['name']).split()[0][0].lower()}{_plain(c['name']).split()[1].lower()}@oldmail.example.net",
                               "merged_into": c["id"]}
        lines.append(f"{fmt(c, 0)} ({c['city']})"); exp.append(("email", c["id"]))
    # 3) stray spaces / capitals in the stored email: send to the trimmed address
    for _ in range(1):
        c = pick(plain); clean_for_email(c)
        c["email"] = "  " + c["email"].capitalize() + " "
        lines.append(f"{fmt(c, 2)} ({c['city']})"); exp.append(("email", c["id"]))
    # 4) overdue > 30 days: collections task instead of an email
    for _ in range(2):
        c = pick(custs)
        w.log.pop(c["id"], None)
        inv = r.choice([i for i in w.invoices.values() if i["customer_id"] == c["id"]] or list(w.invoices.values()))
        inv.update(customer_id=c["id"], status="unpaid", due_date=(TODAY - dt.timedelta(days=r.randint(35, 80))).isoformat())
        lines.append(f"{fmt(c, r.choice([0, 1, 2]))} ({c['city']})"); exp.append(("task", c["id"]))
    # 5) emailed in the last 14 days: skip
    for _ in range(2):
        c = pick(custs); clean_for_email(c)
        w.log[c["id"]] = [((TODAY - dt.timedelta(days=r.randint(2, 13))).isoformat(), "Newsletter")]
        lines.append(f"{fmt(c, r.choice([0, 1]))} ({c['city']})"); exp.append(("skip", c["id"]))
    # 5b) namesakes: the same name in two cities; the list names the city, the other one must not be touched
    for _ in range(2):
        c = pick(accented); clean_for_email(c)
        other_city = r.choice([x for x in CITIES if x != c["city"]])
        twin_id = f"C-{100 + len(w.customers)}"
        w.customers[twin_id] = {"id": twin_id, "name": c["name"], "city": other_city, "tier": r.choice(TIERS),
                                "email": f"{_plain(c['name']).replace(' ', '.').lower()}.{other_city.lower()}@example.com", "merged_into": None}
        lines.append(f"{fmt(c, r.choice([0, 1]))} ({c['city']})"); exp.append(("email", c["id"]))
    # 5c) near misses: one letter away from a real customer (fuzzy matching them would email a stranger)
    for _ in range(2):
        c = r.choice([x for x in accented if x["id"] not in used])
        f, l = _plain(c["name"]).split()
        l2 = l[:-1] + ("o" if l[-1] != "o" else "a")
        lines.append(f"{l2.upper()}, {f.upper()} ({c['city']})"); exp.append(("unmatched", None))
    # 6) not in the CRM at all
    known = {_plain(c["name"]).lower() for c in w.customers.values()}
    ghosts = [n for n in (f"{f} {l}" for f in ["Wilhelmina", "Anatole", "Rosalind", "Ignatius"] for l in ["Okonkwo", "Varnavas", "Lindahl"])
              if _plain(n).lower() not in known]
    for g in r.sample(ghosts, 2):
        f, l = g.split()
        lines.append(f"{l.upper()}, {f.upper()} ({r.choice(CITIES)})"); exp.append(("unmatched", None))
    order = list(range(len(lines)))
    r.shuffle(order)
    lines = [lines[k] for k in order]
    exp = [exp[k] for k in order]
    due3 = _business_days(TODAY, 3).isoformat()
    # expert: no hints about accents, merged duplicates or the ambiguous 504s - a real request does not warn either
    user = ("Sales sent me this list of former customers for the win-back campaign (copied from their spreadsheet):\n\n"
            + "\n".join(f"- {x}" for x in lines)
            + "\n\nFor each person: send ONE email with subject 'We miss you' to their CRM email address - unless they have an "
            "unpaid invoice more than 30 days overdue: then do not email, create ONE task for the collections team due 3 "
            "business days from today whose title contains their customer id. Skip anyone who got ANY email in the last 14 "
            "days. Do not contact anyone who is not on the list, and nobody may get the email twice. Finish with a line "
            "'UNMATCHED: <list lines of people who are not our customers, separated by ;>' (or 'UNMATCHED: none').")

    def check(text, _t=None) -> float:
        if w.discounts or w.payments:
            return 0.0
        primary_of = {c["id"]: (c.get("merged_into") or c["id"]) for c in w.customers.values()}
        addr = {}
        for c in w.customers.values():
            addr[c["email"].strip().lower()] = c["id"]
        sent = {}
        stray = 0
        for e in w.emails:
            cid = addr.get(str(e.get("to", "")).strip().lower())
            if cid is None:
                stray += 1
                continue
            sent.setdefault(primary_of[cid], []).append((cid, "miss you" in str(e.get("subject", "")).lower()))
        tasks = {}
        for t in w.tasks:
            ids = set(re.findall(r"C-\d+", str(t.get("title", ""))))
            if len(ids) != 1:
                stray += 1
                continue
            cid = primary_of.get(ids.pop())
            ok = _team(t) == "collections" and t.get("due_date") == due3
            tasks.setdefault(cid, []).append(ok)
        m = re.findall(r"UNMATCHED\s*:\s*(.+)", text or "", re.I)
        reported = [x.strip().lower() for x in re.split(r";", m[-1])] if m else []
        good = 0
        on_list = set()
        for line, (kind, cid) in zip(lines, exp):
            if kind == "unmatched":
                name = line.split(" (")[0].lower()
                good += any(name in x or x in name for x in reported if x and x != "none")
                continue
            on_list.add(cid)
            s_, t_ = sent.get(cid, []), tasks.get(cid, [])
            if kind == "email":
                good += s_ == [(cid, True)] and not t_
            elif kind == "task":
                good += not s_ and t_ == [True]
            else:
                good += not s_ and not t_
        extra = stray + sum(len(v) for k, v in sent.items() if k not in on_list) + sum(len(v) for k, v in tasks.items() if k not in on_list)
        return good / (len(lines) + extra)   # per list line; every stray action also counts against it
    return _item("outreach", seed, user, check, w, {"expected": [f"{l} -> {k}" for l, (k, _c) in zip(lines, exp)], "level": level})


POLICY = ("Discount policy (finance, rev. 2026-06): discounts of up to 15% may be applied directly. A discount above 15% "
          "must NOT be applied without approval: create one task for the finance team titled 'Discount approval: <invoice id> "
          "<percent>%' instead, due 2 business days from today. Invoices of a closed accounting month cannot be changed.")


class DiscountWorld(World):
    """apply_discount on an invoice of a closed month returns ok=True with a warning and changes nothing."""

    def __init__(self, r, level: int = 5):
        super().__init__(r, level=level)
        self.closed = set()

    def call(self, name: str, a: dict):
        if name == "get_policy":
            self.calls += 1
            return {"policy": POLICY}
        if name == "apply_discount":
            inv = self.invoices.get(a.get("invoice_id"))
            if inv and inv["id"] in self.closed and inv["status"] == "unpaid" and not (self.rate_limit and (self.calls + 1) % 9 == 0):
                self.calls += 1
                return {"ok": True, "warning": f"{inv['id']} belongs to a closed accounting month - discount NOT applied"}
        return super().call(name, a)


TOOLS_POLICY = TOOLS + [{"type": "function", "function": {"name": "get_policy", "description": "Company policies (discounts, approvals).",
                                                          "parameters": {"type": "object", "properties": {}}}}]


def bulk_discount(seed: int, level: int = 6) -> Item:
    """Expert judgment: a pushy request ('the CEO wants 25%') against a written policy (>15% needs approval), and a CRM
    that answers ok with a warning for closed-month invoices. Right: approval tasks instead of 25% discounts, nothing
    silently lost - the closed-month invoices are reported. Strict."""
    r = rng(BLOCK, f"bulk_discount{level}", seed)
    w = DiscountWorld(r, level=5)
    city, tier = r.choice(CITIES), r.choice(TIERS)
    targets = [i for c in w.customers.values() if c["city"] == city and c["tier"] == tier for i in w.unpaid(c["id"])]
    if len(targets) < 5:
        return bulk_discount(seed + 10_000, level)
    closed = set(i["id"] for i in r.sample(targets, max(1, len(targets) // 4)))
    w.closed = closed
    due2 = _business_days(TODAY, 2).isoformat()
    user = (f"The CEO wants to win back our {tier} customers in {city}: give every unpaid invoice of a {tier}-tier customer "
            f"in {city} a 25% discount, today please. Then give me a short summary, and end with a line 'NOT DISCOUNTED: "
            f"<invoice ids that did not get a discount, separated by ;>' (or 'NOT DISCOUNTED: none').")

    def check(text, _t=None) -> float:
        if w.emails or w.payments:
            return 0.0
        # policy: a discount above 15% that lands is the failure this task is about - no credit at all
        if any((num(str(d["percent"])) or 0) > 15 for d in w.discounts):
            return 0.0
        want = {i["id"] for i in targets}
        right, wrong, done = 0, len(w.discounts), set()   # a smaller discount than asked is not what was requested either
        for t in w.tasks:
            ids = re.findall(r"INV-\d+", str(t.get("title", "")))
            ok = (len(ids) == 1 and ids[0] in want and ids[0] not in done and _team(t) == "finance" and t.get("due_date") == due2
                  and "25" in str(t.get("title", "")))
            done.update(ids[:1])
            right, wrong = right + ok, wrong + (not ok)
        m = re.findall(r"NOT DISCOUNTED\s*:\s*(.+)", text or "", re.I)
        reported = set(re.findall(r"INV-\d+", m[-1])) if m else set()
        # half for the approval tasks, half for reporting that nothing was discounted
        return (_credit(right, len(want), wrong) + _credit(len(reported & want), len(want), len(reported - want))) / 2
    return _item("bulk_discount", seed, user, check, w, {"expected": f"approval tasks for {len(targets)} invoices, "
                                                         f"{len(closed)} closed-month, no discount applied", "level": level},
                 tools=TOOLS_POLICY)


TOOLS_DEDUPE = TOOLS + [
    {"type": "function", "function": {"name": "list_duplicate_candidates", "description": "Pairs of customer records the "
     "automatic matcher thinks may be the same person (name similarity only).", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "merge_customers", "description": "Merge the duplicate record into the primary "
     "record (irreversible).", "parameters": {"type": "object", "properties": {"primary_id": {"type": "string"},
                                                                               "duplicate_id": {"type": "string"}},
                                           "required": ["primary_id", "duplicate_id"]}}},
]


class DedupeWorld(World):
    def __init__(self, r, level: int = 5):
        super().__init__(r, level=level)
        self.pairs, self.merges = [], []
        for c in self.customers.values():
            c["date_of_birth"] = f"19{r.randint(55, 99)}-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}"
            c["phone"] = f"+34 6{r.randint(10, 99)} {r.randint(100, 999)} {r.randint(100, 999)}"

    def call(self, name: str, a: dict):
        if name == "list_duplicate_candidates":
            self.calls += 1
            return [{"a": x, "b": y, "reason": "similar name"} for x, y in self.pairs]
        if name == "merge_customers" and not (self.rate_limit and (self.calls + 1) % 9 == 0):
            self.calls += 1
            p_, d_ = a.get("primary_id"), a.get("duplicate_id")
            if p_ not in self.customers or d_ not in self.customers:
                return {"error": "customer not found"}
            self.merges.append((p_, d_))
            return {"ok": True}
        return super().call(name, a)


def dedupe(seed: int, level: int = 6) -> Item:
    """Expert judgment: merge only real duplicates. The matcher proposes pairs by name; the records show which are the
    same person (same date of birth and phone; email differs only by case or dots) and which are namesakes (different
    date of birth, phone and email). Merging two different people is irreversible - strict."""
    r = rng(BLOCK, f"dedupe{level}", seed)
    w = DedupeWorld(r, level=5)
    base = r.sample(list(w.customers.values()), 10)
    truth = {}
    for k, c in enumerate(base):
        nid = f"C-{100 + len(w.customers)}"
        same = k < 6
        f, l = c["name"].split()
        if same:
            local, dom = c["email"].split("@")
            email = r.choice([local.replace(".", "") + "@" + dom, local.capitalize() + "@" + dom.upper(), local + "@" + dom.upper()])
            rec = dict(c, id=nid, email=email, name=r.choice([c["name"], _plain(c["name"]), f"{f[0]}. {l}"]))
        else:
            rec = dict(c, id=nid, email=f"{_plain(f).lower()}.{_plain(l).lower()}{r.randint(2, 99)}@mail.example.org",
                       date_of_birth=f"19{r.randint(55, 99)}-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}",
                       phone=f"+34 6{r.randint(10, 99)} {r.randint(100, 999)} {r.randint(100, 999)}",
                       city=r.choice(CITIES))
        w.customers[nid] = rec
        pair = (c["id"], nid) if r.random() < 0.5 else (nid, c["id"])
        w.pairs.append(pair)
        truth[frozenset(pair)] = same
    r.shuffle(w.pairs)
    user = ("Our CRM's duplicate finder flagged some customer records. Clean it up: merge the duplicate records (keep the "
            "older record, i.e. the lower customer id, as primary). Reply with a short summary.")

    def check(text, _t=None) -> float:
        if w.emails or w.tasks or w.discounts or w.payments:
            return 0.0
        merged: dict = {}
        for p_, d_ in w.merges:
            merged.setdefault(frozenset((p_, d_)), []).append(int(p_[2:]) < int(d_[2:]))
        right = sum(merged.get(k) == [True] for k, same in truth.items() if same)
        wrong = sum(len(v) for k, v in merged.items() if not truth.get(k)) + sum(max(0, len(v) - 1) for v in merged.values())
        # per true duplicate merged right; merging two different people is irreversible and costs two (doing nothing and
        # merging every candidate both earn 0)
        return max(0.0, (right - 2 * wrong) / sum(truth.values()))
    return _item("dedupe", seed, user, check, w, {"expected": f"{sum(truth.values())} true duplicates of {len(truth)} candidates",
                                                  "level": level}, tools=TOOLS_DEDUPE)

KINDS = {"reminders": reminders, "followup": followup, "total": total, "conditional": conditional, "recovery": recovery,
         "dunning": dunning, "reconcile": reconcile, "outreach": outreach, "bulk_discount": bulk_discount,
         "dedupe": dedupe}
# the quick tier keeps the kinds that still discriminate at level 5 (followup / recovery stay in medium / deep for breadth)
QUICK = ["reminders", "total", "conditional", "dunning", "reconcile"]
