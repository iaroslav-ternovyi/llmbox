"""`llmbox validate`: automatic checks every task kind must pass before it enters a suite.

For each (block, kind, level) and a few seeds:
  determinism  the same seed builds the same item (messages and expected answer)
  oracle       a perfect answer / perfect tool actions / reference solution scores 1.0 (the grader never rejects right work)
  null         an empty answer / no actions scores ~0 (a task cannot be passed by doing nothing)
  format       the right answer in other common shapes (**bold**, trailing period, 'Answer:', `code`) still scores 1.0
  frontier     optional: the frontier reference model's score; anything it fails is flagged for a manual look
Kinds without an automatic oracle (free-form writing, translation) are marked '-' and rely on the frontier check.
Born from 2026-09-26, when ~10 grader bugs were found only by running real models.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil

from . import suite
from .suite import agentic, sessions, tools as T
from .suite.common import Item

ANSWER_KINDS = {("reasoning", k) for k in ("arith", "dates", "logic", "code_trace", "table", "schedule", "budget")} | \
               {("longctx", k) for k in ("lookup", "multihop", "count", "latest", "total", "audit")} | {("tools", "total")}


def _cleanup(it: Item) -> None:
    ws = getattr(it.tool_impl, "__self__", None)
    if ws is not None and hasattr(ws, "cleanup"):
        ws.cleanup()


def _call(w, name: str, a: dict):
    """One tool call as a careful agent makes it: a 429 is retried - unless it says the write went through (levels 9-10)."""
    for _ in range(20):
        res = w.call(name, a)
        if not (isinstance(res, dict) and "429" in str(res.get("error", ""))) or res.get("applied"):
            return res
    return res


# ---- oracles: produce the perfect final text (and perform the perfect actions on the item's world) ------------------

def _answer_oracle(it: Item) -> str:
    exp = it.meta.get("expected")
    if isinstance(exp, list):   # multi-question items (v0.10): one numbered line per question
        return "\n".join(f"ANSWER {i + 1}: {e}" for i, e in enumerate(exp))
    return f"ANSWER: {exp}"


def _tools_oracle(it: Item) -> str | None:
    w = it.tool_impl.__self__
    user = next(m["content"] for m in it.messages if m["role"] == "user")
    e = it.meta.get("expected")
    k = it.kind
    if it.meta.get("level", 0) >= 9 and isinstance(e, dict) and "actions" in e:
        # levels 9-10 (v0.12): the generator lists the actions a perfect agent performs; replay them exactly once each
        # through the world's own tools (rate limits and 429s that went through included) and give its reply
        for name, args in e["actions"]:
            _call(w, name, args)
        return e.get("reply") or "done"
    if k == "total":
        return f"ANSWER: {e}"
    if k == "reminders":
        for email, iid in e:
            _call(w, "send_email", {"to": email, "subject": f"Payment reminder {iid}", "body": "Please pay."})
        return "done"
    if k == "recovery":
        _call(w, "send_email", {"to": e, "subject": "Account review", "body": "Would you like to book a call?"})
        return "done"
    if k == "followup":
        due = re.search(r"due (\d{4}-\d{2}-\d{2})", user).group(1)
        team = re.search(r"for the (\w+) team", user).group(1)
        for iid in e:
            name = w.customers[w.invoices[iid]["customer_id"]]["name"]
            _call(w, "create_task", {"title": f"Dispute {iid} {name}", "due_date": due, "team": team})
        return "done"
    if k == "conditional":
        if isinstance(e, dict):
            for iid, pct in e.items():
                _call(w, "apply_discount", {"invoice_id": iid, "percent": pct})
            return "done"
        if e == "NO_ACTION":
            return "NO_ACTION"
        pct = int(re.search(r"apply a (\d+)% discount", user).group(1))
        _call(w, "apply_discount", {"invoice_id": e, "percent": pct})
        return "done"
    if k == "dunning":
        due3 = T._business_days(T.TODAY, 3).isoformat()
        for cid, v in e.items():
            if v[0] == "email":
                ids = re.findall(r"INV-\d+", v[2])
                body = "Overdue: " + ", ".join(ids) + (f". Total {float(v[3]):.2f} EUR" if v[3] not in (None, "None") else "")
                _call(w, "send_email", {"to": v[1], "subject": "Overdue invoices", "body": body})
            else:
                _call(w, "create_task", {"title": f"Dunning {cid}", "due_date": due3, "team": v[1]})
        return "done"
    if k == "reconcile":
        nbd = T._business_days(T.TODAY, 1).isoformat()
        for row in e:
            if row[1] == "pay":
                _call(w, "record_payment", {"invoice_id": row[2], "amount": float(row[3]), "date": row[4]})
            else:
                _call(w, "create_task", {"title": f"Unrecorded bank line {row[0]}", "due_date": nbd, "team": "billing"})
        return "done"
    if k == "outreach":
        due3 = T._business_days(T.TODAY, 3).isoformat()
        unmatched = []
        for entry in e:
            line, kind = entry.rsplit(" -> ", 1)
            name, city = line.split(" (")[0], line.split("(")[-1].rstrip(")")
            toks = {T._plain(x).lower() for x in re.split(r"[ ,]+", name) if x}
            c = next((c for c in w.customers.values() if not c.get("merged_into") and c["city"] == city
                      and set(T._plain(c["name"]).lower().split()) == toks), None)
            if kind == "unmatched":
                unmatched.append(name)
            elif kind == "email":
                for _ in range(10):
                    res = _call(w, "send_email", {"to": c["email"].strip(), "subject": "We miss you", "body": "Hi"})
                    if res.get("ok") or any(d == T.TODAY.isoformat() for d, _s in w.log.get(c["id"], [])):
                        break
            elif kind == "task":
                _call(w, "create_task", {"title": f"Collections {c['id']}", "due_date": due3, "team": "collections"})
        return "done\nUNMATCHED: " + ("; ".join(unmatched) or "none")
    if k == "bulk_discount":   # v0.11: above 15% in total -> approval task; else the discount (a closed month warns)
        city = re.search(r"customers in (\w+):", user).group(1)
        pct = {t: int(p_) for t, p_ in re.findall(r"(\w+)-tier customer in \w+ a (\d+)% discount", user)}
        due2 = T._business_days(T.TODAY, 2).isoformat()
        nd = []
        for c in w.customers.values():
            if c["city"] != city or c["tier"] not in pct:
                continue
            for inv in w.unpaid(c["id"]):
                p_ = pct[c["tier"]]
                if p_ + inv.get("discount_percent", 0) > 15:
                    _call(w, "create_task", {"title": f"Discount approval: {inv['id']} {p_}%", "due_date": due2, "team": "finance"})
                    nd.append(inv["id"])
                elif "warning" in str(_call(w, "apply_discount", {"invoice_id": inv["id"], "percent": p_})):
                    nd.append(inv["id"])
        return "done\nNOT DISCOUNTED: " + ("; ".join(nd) or "none")
    if k == "dedupe":   # the same person: same date of birth and the same phone digits (formats differ from level 5)
        digits = lambda ph: re.sub(r"\D", "", ph)[-9:]
        for a_, b_ in w.pairs:
            ca, cb = w.customers[a_], w.customers[b_]
            if ca["date_of_birth"] == cb["date_of_birth"] and digits(ca["phone"]) == digits(cb["phone"]):
                p_, d_ = sorted((a_, b_), key=lambda x: int(x[2:]))
                _call(w, "merge_customers", {"primary_id": p_, "duplicate_id": d_})
        return "done"
    return None


def _agentic_oracle(it: Item) -> str | None:
    ws = it.tool_impl.__self__
    ref = os.path.join(ws.src, "_ref")
    if os.path.isdir(ref):                    # implement-from-spec projects and sessions: drop the reference files in
        for root, _d, files in os.walk(ref):
            for f in files:
                src = os.path.join(root, f)
                hits = [os.path.join(r, f) for r, _dd, fs in os.walk(ws.dir) if f in fs and "_hidden" not in r]
                dst = hits[0] if hits else os.path.join(ws.dir, os.path.relpath(src, ref))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(src, dst)
        return "done"
    agentic._materialize(ws.src, ws.dir, set())   # bug-fix projects: the template without injected bugs
    for root, _d, files in os.walk(ws.dir):
        for f in files:
            fp = os.path.join(root, f)
            try:
                t = open(fp).read()
            except UnicodeDecodeError:
                continue
            nt = re.sub(r"(?m)^[ \t]*(?:#|//) FEATURE-(?:BEGIN [\w-]+|END)\n", "", t)
            if nt != t:
                open(fp, "w").write(nt)
    return "done"


def _writing_oracle(it: Item) -> str | None:
    e = it.meta.get("expected")
    if it.kind == "extract":
        return json.dumps(e)
    if it.kind == "minutes":
        return json.dumps([{"topic": t, "owner": o, "due": d} for t, (o, d) in e.items()])
    if it.kind in ("proofread", "i18n") and it.meta["level"] >= 6:   # v0.11: levels 6-8 have their own texts / a reference table
        from .suite import writing as W
        if it.kind == "i18n":
            return W.i18n_reference(it)
        return W._proofread_texts(int(it.id.split(".")[-1]), it.meta["level"])[0]
    if it.kind == "proofread":
        user = it.messages[0]["content"]
        from .suite import writing as W
        r = W.rng(W.BLOCK, f"proofread{it.meta['level']}", int(it.id.split(".")[-1]))
        paras = r.sample(W._PROOF, 1 + (it.meta["level"] + 1) // 2)
        return "\n\n".join(re.sub(r"\{([^|}]*)\|([^}]*)\}", lambda m: m.group(1), p) for p in paras)
    return None   # free-form generation / translation: no automatic oracle


def oracle(it: Item) -> str | None:
    if it.block == "explain":   # the reference explanation written from the rules; the reader model grades it
        from .suite import explain
        return explain.oracle(it)
    if it.block == "knowledge":
        from .suite import knowledge
        return knowledge.oracle(it)
    if it.block == "techhelp":   # several questions per machine (v0.10-dev5): one numbered line each
        return _answer_oracle(it)
    if (it.block, it.kind) in ANSWER_KINDS and it.block != "tools":
        return _answer_oracle(it)
    if it.block == "tools":
        return _tools_oracle(it)
    if it.block == "agentic":
        return _agentic_oracle(it)
    if it.block == "writing":
        return _writing_oracle(it)
    if it.block == "code":   # levels 7-8: the reference implementation that computed the hidden tests (Python or JS port)
        from .suite import code
        return code.oracle(it)
    return None   # code levels 1-6: the expected outputs ARE the reference implementation; checked via null + frontier


def _variants(text: str) -> list[str]:
    """The oracle's answer lines in other common shapes; every 'ANSWER[ n]: x' line is reshaped the same way."""
    if not re.search(r"(?m)^ANSWER(?: \d+)?:\s*.+$", text):
        return []
    shapes = [lambda t, x: f"{t}: **{x}**", lambda t, x: f"{t}: {x}.", lambda t, x: f"{t.title()}: {x}", lambda t, x: f"{t}: `{x}`"]
    out = [re.sub(r"(?m)^(ANSWER(?: \d+)?):\s*(.+)$", lambda m, f=f: f(m.group(1), m.group(2)), text) for f in shapes]
    out[0] = "Some reasoning.\n" + out[0]
    return out


# ---- the run -------------------------------------------------------------------------------------------------------

def kinds_of(tier: str | None) -> list[tuple[str, str, int]]:
    if tier == "quick":
        return list(dict.fromkeys(suite.QUICK_ITEMS))
    out = []
    for b, ks in suite.BLOCKS.items():
        for k in ks:
            lvl = 6 if (b, k) in {("tools", "outreach"), ("tools", "bulk_discount"), ("tools", "dedupe"), ("longctx", "audit"), ("code", "cron")} else 5
            out.append((b, k, lvl))
    return out


def check_kind(b: str, k: str, lvl: int, seeds=(1, 2, 3), frontier: str | None = None) -> dict:
    gen = suite.BLOCKS[b][k]
    lvl = suite._level_for(b, k, lvl)
    res = {"kind": f"{b}.{k}.L{lvl}", "determinism": True, "oracle": [], "null": [], "format": [], "frontier": []}
    for sd in seeds:
        a, bb = gen(sd, lvl), gen(sd, lvl)
        if json.dumps(a.messages, sort_keys=True) != json.dumps(bb.messages, sort_keys=True) or \
                json.dumps(a.meta.get("expected"), default=str) != json.dumps(bb.meta.get("expected"), default=str):
            res["determinism"] = False
        _cleanup(bb)
        txt = oracle(a)
        if txt is not None:
            res["oracle"].append(round(float(a.check(txt, {})), 3))
            if (b, k) in ANSWER_KINDS:
                for v in _variants(txt):
                    fresh = gen(sd, lvl)
                    if b == "tools":
                        oracle(fresh)           # re-do the world actions (none for 'total')
                    res["format"].append(round(float(fresh.check(v, {})), 3))
                    _cleanup(fresh)
        _cleanup(a)
        z = gen(sd, lvl)
        res["null"].append(round(float(z.check("", {})), 3))
        _cleanup(z)
        if frontier:
            from . import bench
            f_ = gen(sd, lvl)
            row = bench.run_item(frontier, "claude-opus-5-5", f_)
            if row.get("pending"):
                bench.grade_deferred(frontier, [f_], [row], progress=lambda *_: None)
            res["frontier"].append(row["score"])
    if b == "explain":   # a model reads the explanation: the reference explanation must work, silence must not
        ok = res["determinism"] and sum(res["oracle"]) / len(res["oracle"]) >= 0.8 and sum(res["null"]) / len(res["null"]) <= 0.35
        res["ok"] = ok
        res["review"] = [s for s in res["frontier"] if s < 0.8]
        return res
    ok = res["determinism"] and all(x >= 0.999 for x in res["oracle"]) and all(x <= 0.25 for x in res["null"]) \
        and all(x >= 0.999 for x in res["format"])
    res["ok"] = ok
    res["review"] = [s for s in res["frontier"] if s < 1.0]
    return res


def run(tier: str | None = "quick", only: list[str] | None = None, seeds=(1, 2, 3), frontier: str | None = None, progress=print) -> list[dict]:
    out = []
    for b, k, lvl in kinds_of(tier):
        if only and not any(f"{b}.{k}".startswith(o) or k == o for o in only):
            continue
        try:
            r = check_kind(b, k, lvl, seeds, frontier)
        except Exception as e:   # a crashing generator or grader is itself a finding
            r = {"kind": f"{b}.{k}.L{lvl}", "ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
        out.append(r)
        fmt = lambda xs: ",".join(f"{x:g}" for x in xs) if xs else "-"
        progress(f"{'OK ' if r['ok'] else 'BAD'} {r['kind']:28s} det={'y' if r.get('determinism') else 'N'} "
                 f"oracle={fmt(r.get('oracle', []))} null={fmt(r.get('null', []))} format={fmt(r.get('format', []))}"
                 + (f" frontier={fmt(r['frontier'])}" if r.get("frontier") else "") + (f"  {r['error']}" if r.get("error") else ""))
    return out
