"""v0.10 partial credit in longctx, reasoning, code and tools: numbered answers, credit per question / test / action, and
wrong actions that cancel right ones. Run: python3 tests/test_partial.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox.suite import code as C, longctx as L, reasoning as R, tools as T  # noqa: E402
from llmbox.suite.common import numbered_answers  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    if not ok:
        print(f"FAIL {name}  {detail}")


def near(a, b):
    return abs(a - b) < 1e-9


# numbered answers: last line per number wins, markup is stripped, plain ANSWER lines count in order
check("numbered", numbered_answers("ANSWER 1: x\nANSWER 2: **y**\nANSWER 1: z", 2) == ["z", "y"])
check("plain in order", numbered_answers("ANSWER: a\nANSWER: b", 2) == ["a", "b"])
check("missing", numbered_answers("ANSWER 2: b", 3) == [None, "b", None])

# longctx / reasoning: the share of questions right, in any answer order the numbers say
for mod, kind, level in [(L, "lookup", 3), (L, "multihop", 3), (L, "latest", 3), (R, "arith", 5), (R, "code_trace", 5),
                         (R, "logic", 4), (R, "schedule", 4)]:
    it = mod.KINDS[kind](7, level)
    exp = it.meta["expected"]
    n = len(exp)
    lines = [f"ANSWER {i + 1}: {e}" for i, e in enumerate(exp)]
    check(f"{kind} oracle", near(it.check("\n".join(reversed(lines))), 1.0))
    check(f"{kind} one right", near(it.check(lines[0]), 1 / n))
    check(f"{kind} empty", it.check("") == 0.0)

# longctx audit: each error found is a point, a false alarm cancels one, listing everything earns nothing
it = L.audit(1, 6)
wrong = [x.strip() for x in it.meta["expected"].split(",")]
ids = sorted(set(__import__("re").findall(r"INC-\d+", it.messages[0]["content"].split("Draft monthly")[1])))
check("audit all right", near(it.check("ANSWER: " + ", ".join(wrong)), 1.0))
check("audit half", near(it.check("ANSWER: " + ", ".join(wrong[: len(wrong) // 2])), (len(wrong) // 2) / len(wrong)))
check("audit everything", it.check("ANSWER: " + ", ".join(ids)) == 0.0)

# code: the share of hidden tests passed
it = C.KINDS["rle"](1, 1)
if it.lang == "python":
    body = "def %s(*a):\n    return None\n" % it.meta["fn"]
    check("code all wrong", it.check(f"```python\n{body}```") == 0.0)

# tools: reminders - one right email plus one to a wrong address: they cancel out
it = T.reminders(1, 4)
exp = it.meta["expected"]
w = it.tool_impl.__self__
it.tool_impl("send_email", {"to": exp[0][0], "subject": f"Reminder {exp[0][1]}", "body": "."})
check("reminders one of n", near(it.check(""), 1 / len(exp)), it.check(""))
it.tool_impl("send_email", {"to": "stranger@example.com", "subject": f"Reminder {exp[0][1]}", "body": "."})
check("reminders wrong cancels", it.check("") == 0.0, it.check(""))

# tools: dedupe - a namesake merged costs the pair and cancels one right decision
it = T.dedupe(1, 6)
w = it.tool_impl.__self__
namesake = [p for p in w.pairs if w.customers[p[0]]["date_of_birth"] != w.customers[p[1]]["date_of_birth"]]
same = [p for p in w.pairs if p not in namesake]
check("dedupe nothing merged", it.check("") == 0.0, it.check(""))
for p in same:
    a, b = sorted(p, key=lambda c: int(c[2:]))
    it.tool_impl("merge_customers", {"primary_id": a, "duplicate_id": b})
check("dedupe all right", near(it.check(""), 1.0), it.check(""))
a, b = sorted(namesake[0], key=lambda c: int(c[2:]))
it.tool_impl("merge_customers", {"primary_id": a, "duplicate_id": b})
check("dedupe wrong merge costs two", near(it.check(""), (len(same) - 2) / len(same)), it.check(""))
it2 = T.dedupe(1, 6)
for p in it2.tool_impl.__self__.pairs:
    a, b = sorted(p, key=lambda c: int(c[2:]))
    it2.tool_impl("merge_customers", {"primary_id": a, "duplicate_id": b})
check("dedupe merge everything", it2.check("") == 0.0, it2.check(""))

# tools: bulk discount - any 25% discount that lands is the failure: no credit
it = T.bulk_discount(1, 6)
w = it.tool_impl.__self__
unpaid = [i for i, v in w.invoices.items() if v["status"] == "unpaid" and i not in w.closed]
it.tool_impl("apply_discount", {"invoice_id": unpaid[0], "percent": 25})
check("bulk 25% applied", it.check("NOT DISCOUNTED: none") == 0.0)

# writing: the share of constraints (6 rules at level 3), and only for a real attempt (an empty reply meets every "do not ..." rule)
from llmbox.suite import writing as W  # noqa: E402
it = W.rewrite(1, 3)
import re as _re
nums = _re.findall(r"like (\d+) days behind\. we'll give you (\d+)%", it.messages[0]["content"])[0]
good = (f"Dear customer, the delivery is delayed by {nums[0]} days because the supplier's truck broke down. We offer a "
        f"{nums[1]}% discount on your next order. We are taking steps so that this does not happen again.")
check("rewrite all rules", near(it.check(good), 1.0), it.check(good))
check("rewrite one rule broken", near(it.check(good.replace("again.", "again!")), 5 / 6), it.check(good.replace("again.", "again!")))
check("rewrite empty", it.check("") == 0.0)
check("rewrite without the facts", it.check("Dear customer, we are sorry for the delay and we will fix it soon.") == 0.0)

# v0.11 levels 6-8 of longctx: the document stays at the level-3 size, credit per question, NOT STATED is an answer
from llmbox import validate as V  # noqa: E402
for kind in ("lookup", "multihop", "count", "latest", "total"):
    for level in (6, 7, 8):
        it = L.KINDS[kind](3, level)
        exp = it.meta["expected"]
        lines = [f"ANSWER {i + 1}: {e}" for i, e in enumerate(exp)]
        check(f"{kind} L{level} oracle", near(it.check("\n".join(lines)), 1.0))
        check(f"{kind} L{level} one right", near(it.check(lines[0]), 1 / len(exp)))
        check(f"{kind} L{level} empty", it.check("") == 0.0)
        check(f"{kind} L{level} size", it.meta["doc_tokens_est"] <= 68_000, it.meta["doc_tokens_est"])   # level 3: ~66k (~95k real)
for kind in ("lookup", "multihop"):
    it = L.KINDS[kind](3, 7)
    exp = it.meta["expected"]
    ns = exp.index(L.NOT_STATED)
    real = next(i for i, e in enumerate(exp) if e != L.NOT_STATED)
    check(f"{kind} not stated", near(it.check(f"ANSWER {ns + 1}: The document does not say (not stated)."), 1 / len(exp)))
    check(f"{kind} guess instead of not stated", it.check(f"ANSWER {ns + 1}: {exp[real]}") == 0.0)
    check(f"{kind} not stated instead of a value", it.check(f"ANSWER {real + 1}: NOT STATED") == 0.0)
for level in (7, 8):   # audit: stale values, moved engineers, withdrawn incidents; a false alarm cancels a hit
    it = L.audit(2, level)
    wrong = [x.strip() for x in it.meta["expected"].split(",")]
    ids = sorted(set(__import__("re").findall(r"INC-\d+", it.messages[0]["content"].split("Draft reliability report")[1])))
    extra = next(i for i in ids if i not in wrong)
    check(f"audit L{level} all right", near(it.check("ANSWER: " + ", ".join(wrong)), 1.0))
    check(f"audit L{level} one false alarm", near(it.check("ANSWER: " + ", ".join(wrong + [extra])), (len(wrong) - 1) / len(wrong)))
    check(f"audit L{level} everything", it.check("ANSWER: " + ", ".join(ids)) == 0.0)
    check(f"audit L{level} empty", it.check("") == 0.0)

# v0.11 levels 6-8 of writing: the oracle (where there is one) earns 1.0, a real answer that breaks one rule loses one share
for kind, levels in (("extract", (7, 8)), ("minutes", (6, 7, 8)), ("i18n", (6, 7, 8)), ("proofread", (6, 7, 8))):
    for level in levels:
        it = W.KINDS[kind](4, level)
        check(f"{kind} L{level} oracle", near(it.check(V.oracle(it)), 1.0), it.check(V.oracle(it)))
        check(f"{kind} L{level} empty", it.check("") == 0.0)
it = W.extract(1, 8)   # one line wrong in one field: that line and nothing else
import json as _json  # noqa: E402
rows = [dict(o) for o in it.meta["expected"]]
rows[0]["quantity"] += 1
check("extract L8 one line wrong", near(it.check(_json.dumps(rows)), (len(rows) - 1 + 1) / (len(rows) + 1)))
it = W.i18n(3, 8)      # a nested plural that lacks the Ukrainian few / many categories costs its key
ref = _json.loads(V.oracle(it))
ref["added"] = ref["added"].replace(" few {Вона додала # файли} many {Вона додала # файлів}", "")
check("i18n L8 nested plural", near(it.check(_json.dumps(ref, ensure_ascii=False)), (len(ref) - 1) / len(ref)))
it = W.proofread(2, 7)  # "correcting" a quotation (quoted verbatim) is an unrequested edit
clean = V.oracle(it)
check("proofread L7 quote fixed", near(it.check(clean.replace("paitence", "patience")), 1 - 1 / it.meta["errors"]))
good = """NOVA KEEPS YOUR PASSWORDS SAFE

Passwords are hard to remember, so let Nova help. Nova stores every login in one encrypted vault.

Launching in 2026, Nova works on phones and laptops. It fills in forms for you. Your data never leaves your devices unencrypted.

Trust Nova to watch the web for leaked passwords and warn you. Our support team answers questions 24/7.

Setting up takes one minute. Nova can import your old logins. Scan the QR code to try Nova today?"""
it = W.constrained(3, 8)   # conflicts resolved by order: the strings with digits stay, "Nova" in every paragraph, a final "?"
check("constrained L8 all rules", near(it.check(good), 1.0), it.check(good))
check("constrained L8 one rule broken", near(it.check(good.replace("Launching in 2026, Nova works", "Launching in 2026, it works")),
                                              15 / 16))
check("constrained L8 empty", it.check("") == 0.0)
good = """Dear Prof. García,
We regret to inform you that order 87913 cannot be shipped this week because a delivery from our supplier is four days late. \
The missing parts will reach us this Friday, and we will ship your order on 28 October 2026. As compensation, we will grant you a \
26% discount on your next order. We will also refund EUR 22.00 for the express shipping you paid. For your records, the internal \
reference is ZX-7806."""
it = W.rewrite(1, 8)       # the shipping day skips the Monday holiday; numbers below ten in words wins over digits
check("rewrite L8 all rules", near(it.check(good), 1.0), it.check(good))
check("rewrite L8 delay in digits", near(it.check(good.replace("four days", "4 days")), 13 / 14))
check("rewrite L8 empty", it.check("") == 0.0)

# v0.11 levels 9-10 (aimed at the frontier): several answers per question, credit per answer, lists graded per id
check("MAX_LEVEL 10", L.MAX_LEVEL == 10 and W.MAX_LEVEL == 10)
for kind in ("lookup", "multihop", "count", "latest", "total"):
    for level in (9, 10):
        it, again = L.KINDS[kind](2, level), L.KINDS[kind](2, level)
        exp = it.meta["expected"]
        lines = [f"ANSWER {i + 1}: {e}" for i, e in enumerate(exp)]
        check(f"{kind} L{level} deterministic", it.messages == again.messages and it.meta == again.meta)
        check(f"{kind} L{level} oracle", near(it.check("\n".join(reversed(lines))), 1.0))
        check(f"{kind} L{level} one right", near(it.check(lines[0]), 1 / len(exp)))
        check(f"{kind} L{level} empty", it.check("") == 0.0)
        check(f"{kind} L{level} NOT STATED everywhere", it.check("\n".join(f"ANSWER {i + 1}: NOT STATED" for i in range(len(exp)))) == 0.0)
        check(f"{kind} L{level} size", it.meta["doc_tokens_est"] <= 68_000, it.meta["doc_tokens_est"])
it = L.lookup(2, 9)   # a list answer: half the ids earn half of it, a wrong id cancels one
ids = it.meta["expected"][0].split(", ")
half = ids[: len(ids) // 2]
other = next(x for x in sorted(set(__import__("re").findall(r"INC-\d+", it.messages[0]["content"]))) if x not in ids)
n = len(it.meta["expected"])
check("lookup L9 half a list", near(it.check("ANSWER 1: " + ", ".join(half)), len(half) / len(ids) / n))
check("lookup L9 false alarm", near(it.check("ANSWER 1: " + ", ".join(half + [other])), (len(half) - 1) / len(ids) / n))
for level in (9, 10):
    it = L.audit(2, level)
    wrong = [x.strip() for x in it.meta["expected"].split(",")]
    ids = sorted(set(__import__("re").findall(r"INC-\d+", it.messages[0]["content"].split("Draft reliability report")[1])))
    extra = next(i for i in ids if i not in wrong)
    check(f"audit L{level} all right", near(it.check("ANSWER: " + ", ".join(wrong)), 1.0))
    check(f"audit L{level} one false alarm", near(it.check("ANSWER: " + ", ".join(wrong + [extra])), (len(wrong) - 1) / len(wrong)))
    check(f"audit L{level} everything", it.check("ANSWER: " + ", ".join(ids)) == 0.0)
    check(f"audit L{level} size", it.meta["doc_tokens_est"] <= 68_000, it.meta["doc_tokens_est"])
for kind in ("extract", "minutes", "i18n", "proofread"):
    for level in (9, 10):
        it = W.KINDS[kind](3, level)
        check(f"{kind} L{level} oracle", near(it.check(V.oracle(it)), 1.0), it.check(V.oracle(it)))
        check(f"{kind} L{level} empty", it.check("") == 0.0)
it = W.i18n(3, 10)     # every plain string has a screen limit: one character over it costs that key
ref = _json.loads(V.oracle(it))
k = sorted(it.meta["limits"])[0]
ref[k] = ref[k] + "!" * (it.meta["limits"][k] - len(ref[k]) + 1)
check("i18n L10 over the limit", near(it.check(_json.dumps(ref, ensure_ascii=False)), (len(ref) - 1) / len(ref)))
it = W.proofread(3, 10)   # an unrequested edit costs as much as a missed error
clean = V.oracle(it)
check("proofread L10 one edit", near(it.check(clean.replace("the", "teh", 1)), 1 - 1 / it.meta["errors"]))
# the kinds without an oracle: answers that meet every rule (written by the frontier reference, checked here) score 1.0,
# and breaking one rule costs one share
fixtures = _json.load(open(os.path.join(os.path.dirname(__file__), "writing_answers_910.json")))
for item_id, answer in fixtures.items():
    _, kind, lv, sd = item_id.split(".")
    it = W.KINDS[kind](int(sd), int(lv[1:]))
    check(f"{item_id} answer", near(it.check(answer), 1.0), it.check(answer))
    check(f"{item_id} empty", it.check("") == 0.0)
    lines = answer.split("\n")   # one word less in the first sentence / bullet
    i = next(k for k, l in enumerate(lines) if l.startswith("- ")) if kind in ("summarize", "translate") else 1 if kind == "rewrite" else 2
    toks = lines[i].split(" ")
    del toks[-2]
    broken = "\n".join(lines[:i] + [" ".join(toks)] + lines[i + 1:])
    check(f"{item_id} broken", 0 < it.check(broken) < 1.0, it.check(broken))

print("all passed" if not failed else f"{failed} failed")
sys.exit(1 if failed else 0)
