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

print("all passed" if not failed else f"{failed} failed")
sys.exit(1 if failed else 0)
