"""Levels 9-10 of the tools block (v0.12): deterministic items built in well under a second, full credit for the oracle,
none for doing nothing, partial credit for the typical wrong strategies (obeying the CEO, merging every flagged pair,
ignoring currency and credits, retrying a 429 that went through). Levels 1-8 staying as they were is
tests/test_levels_stable.py. Run: python3 tests/test_levels_910.py"""
import inspect
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import validate as V  # noqa: E402
from llmbox.suite import tools as T  # noqa: E402

failed = 0
HARD = ("reminders", "conditional", "dunning", "reconcile", "bulk_discount", "dedupe")


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


def near(a, b):
    return abs(a - b) < 1e-9


def retry_all(it, name, args):
    """A careless agent: retries every 429, also the ones that say the write went through."""
    for _ in range(20):
        res = it.tool_impl(name, args)
        if not (isinstance(res, dict) and "429" in str(res.get("error", ""))):
            return res


check("MAX_LEVEL", T.MAX_LEVEL == 10)

for kind in HARD:
    gen = T.KINDS[kind]
    # levels 1-8 keep their generator source (the family fingerprint hashes it): the wrapper reports the original's
    check(f"{kind} source kept", inspect.getsource(gen).startswith(f"def {kind}("), inspect.getsource(gen)[:40])
    for lv in (9, 10):
        for seed in (1, 2):
            t0 = time.time()
            it, again = gen(seed, lv), gen(seed, lv)
            took = (time.time() - t0) / 2
            check(f"{kind} L{lv}.{seed} fast", took < 0.5, took)
            check(f"{kind} L{lv}.{seed} deterministic", it.messages == again.messages and it.meta == again.meta)
            check(f"{kind} L{lv}.{seed} id", it.id == f"tools.{kind}.L{lv}.{seed}" and it.meta["max_steps"] == 20 + 16 * lv)
            n = len(it.meta["expected"]["actions"])
            check(f"{kind} L{lv}.{seed} size", 12 <= n <= 80, n)
            check(f"{kind} L{lv}.{seed} empty", it.check("", {}) == 0.0)
            check(f"{kind} L{lv}.{seed} oracle", near(again.check(V.oracle(again), {}), 1.0))

# the 429 on a write says whether it went through; retrying one that did does it twice and costs one action each
it = T.KINDS["reminders"](1, 9)
w = it.tool_impl.__self__
acts = it.meta["expected"]["actions"]
for name, args in acts:
    retry_all(it, name, args)
dup = len(w.emails) + len(w.tasks) - len(acts)
check("blind retry duplicates", dup >= 1, dup)
check("blind retry costs one per duplicate", near(it.check(""), (len(acts) - dup) / len(acts)), (it.check(""), dup, len(acts)))

# a write that went through behind a 429 still shows the CRM's warning (a closed month changes nothing)
it = T.KINDS["conditional"](2, 9)
w = it.tool_impl.__self__
iid = sorted(w.closed)[0]
w.calls, w.n429 = 8, 1          # the next call is rate limited and is one that goes through
res = it.tool_impl("apply_discount", {"invoice_id": iid, "percent": 5})
check("429 keeps the closed-month warning", "closed accounting month" in str(res) and not w.discounts, res)

# dunning: emailing every customer with overdue invoices, main address, no rules and no conversion
it = T.KINDS["dunning"](1, 9)
w = it.tool_impl.__self__
city = re.search(r"every customer in (\w+)", it.messages[1]["content"]).group(1)
for c in w.customers.values():
    over = [i for i in w.unpaid(c["id"]) if c["city"] == city and i["due_date"] < T.TODAY.isoformat()]
    if over:
        w.call("send_email", {"to": c["email"], "subject": "Overdue invoices", "body": " ".join(i["id"] for i in over)
                              + f" total {sum(i['amount'] for i in over):.2f} EUR"})
check("dunning naive email everyone", it.check("", {}) < 0.2, it.check("", {}))

# dunning L10: the actions without the NO ACTION report lose exactly the report's share
it = T.KINDS["dunning"](1, 10)
V.oracle(it)
e = it.meta["expected"]
n_rep = e["reply"].count("NO ACTION")
check("dunning report share", near(it.check("Done.", {}), len(e["actions"]) / (len(e["actions"]) + n_rep)), it.check("Done.", {}))
first = re.search(r"NO ACTION C-\d+: ([\w-]+)", e["reply"]).group(1)
other = next(x for x in T.DUN_REASONS if x != first)
wrong_reason = e["reply"].replace(f": {first}", f": {other}", 1)
check("dunning report wrong reason", near(it.check(wrong_reason, {}), 1 - 1 / (len(e["actions"]) + n_rep)), it.check(wrong_reason, {}))

# conditional: raw amounts (no conversion, no credit, disputed counted) put customers in the wrong bracket
it = T.KINDS["conditional"](1, 9)
w = it.tool_impl.__self__
city = re.search(r"customers in (\w+)", it.messages[1]["content"]).group(1)
for c in w.customers.values():
    un = w.unpaid(c["id"])
    if c["city"] != city or not un:
        continue
    tot = sum(i["amount"] for i in un)
    pct = 8 if tot > 3000 else 5 if tot > 1500 else 3 if tot > 750 else 0
    if pct:
        first = min(un, key=lambda i: (i["due_date"], i["id"]))
        w.call("apply_discount", {"invoice_id": first["id"], "percent": pct + 2 * (c["tier"] == "gold")})
check("conditional raw amounts", it.check("", {}) < 0.5, it.check("", {}))

# bulk discount: doing what the CEO asked (every discount applied directly) - every approval case costs two
it = T.KINDS["bulk_discount"](1, 9)
w = it.tool_impl.__self__
u = it.messages[1]["content"]
pct = {"gold": int(re.search(r"gold-tier customer in \w+ a (\d+)%", u).group(1)), "silver": int(re.search(r"silver-tier customer (\d+)%", u).group(1)),
       "bronze": int(re.search(r"bronze-tier customer (\d+)%", u).group(1))}
city = re.search(r"customers in (\w+):", u).group(1)
for c in w.customers.values():
    if c["city"] == city:
        for i in w.unpaid(c["id"]):
            w.call("apply_discount", {"invoice_id": i["id"], "percent": pct[c["tier"]]})
check("bulk obey the CEO", it.check("NOT DISCOUNTED: none", {}) < 0.1, it.check("NOT DISCOUNTED: none", {}))
it = T.KINDS["bulk_discount"](1, 9)   # the actions right, the report missing: the report's share lost
V.oracle(it)
nd = len(re.findall(r"INV-\d+", it.meta["expected"]["reply"]))
n_act = len(it.meta["expected"]["actions"])
check("bulk report share", near(it.check("done", {}), n_act / (n_act + nd)), it.check("done", {}))

# reconcile: recording what the statement says against the referenced invoice (no balances, no rules)
it = T.KINDS["reconcile"](1, 9)
w = it.tool_impl.__self__
for line in re.findall(r"^(TX-\d+) \| (\S+) \| [^|]+ \| ([\d.,-]+) \| (.*)$", it.messages[1]["content"], re.M):
    ids = re.findall(r"INV-\d+", line[3])
    amt = float(line[2].replace(".", "").replace(",", "."))
    if ids and amt > 0:
        w.call("record_payment", {"invoice_id": ids[0], "amount": amt, "date": line[1]})
check("reconcile naive", 0.0 < it.check("", {}) < 0.6, it.check("", {}))

# dedupe: merging every flagged pair into its lower id - namesakes, twins and swapped birthdays cost two each
it = T.KINDS["dedupe"](1, 9)
w = it.tool_impl.__self__
for a, b in w.pairs:
    p_, d_ = sorted((a, b), key=lambda x: int(x[2:]))
    it.tool_impl("merge_customers", {"primary_id": p_, "duplicate_id": d_})
check("dedupe merge everything", it.check("", {}) == 0.0, it.check("", {}))
it = T.KINDS["dedupe"](1, 10)   # right people, pair by pair (lower id of the pair): the three- and four-record people go wrong
w = it.tool_impl.__self__
units = {a["duplicate_id"]: a["primary_id"] for _n, a in it.meta["expected"]["actions"]}
for a, b in w.pairs:
    if a in units and units[a] in (b, units.get(b)) or b in units and units[b] in (a, units.get(a)):
        p_, d_ = sorted((a, b), key=lambda x: int(x[2:]))
        it.tool_impl("merge_customers", {"primary_id": p_, "duplicate_id": d_})
check("dedupe pairwise", 0.3 < it.check("", {}) < 1.0, it.check("", {}))

# reports: the right actions without the closing report lose exactly the report's share; one wrong value loses one line
for kind, tag in (("conditional", "BALANCE"), ("dedupe", "KEPT APART"), ("dunning", "NO ACTION")):
    for lv in (9, 10):
        it = T.KINDS[kind](3, lv)
        V.oracle(it)
        e = it.meta["expected"]
        n_act, n_rep = len(e["actions"]), e["reply"].count(tag)
        check(f"{kind} L{lv} report lines", n_rep >= 4, n_rep)
        check(f"{kind} L{lv} without report", near(it.check("Done.", {}), n_act / (n_act + n_rep)), it.check("Done.", {}))
        check(f"{kind} L{lv} with report", near(it.check(e["reply"], {}), 1.0))
it = T.KINDS["conditional"](3, 9)
V.oracle(it)
e = it.meta["expected"]
first = re.search(r"BALANCE (C-\d+): (-?[\d.]+)", e["reply"])
off = e["reply"].replace(first.group(0), f"BALANCE {first.group(1)}: {float(first.group(2)) + 0.01:.2f}", 1)
n = len(e["actions"]) + e["reply"].count("BALANCE")
check("conditional balance off by a cent", near(it.check(off, {}), 1 - 1 / n), it.check(off, {}))
prose = f"- **Balance not above 750.00:** {first.group(1)}, C-999 (no discount)\n" + e["reply"]   # a summary line is not a report line
check("conditional prose before the report", near(it.check(prose, {}), 1.0), it.check(prose, {}))
check("conditional last report line wins", near(it.check(off + f"\nBALANCE {first.group(1)}: {first.group(2)}", {}), 1.0))
check("conditional balance in European format", near(it.check(e["reply"].replace(first.group(2), first.group(2).replace(".", ","), 1), {}), 1.0))

print("all passed" if not failed else f"{failed} failed")
sys.exit(1 if failed else 0)
