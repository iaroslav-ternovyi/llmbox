"""What things are called on the site: blocks and their weights, the four uses, task names, model names and families."""
from __future__ import annotations

import html
import os
import re
from datetime import datetime


BLOCKS = ["agentic", "code", "tools", "techhelp", "knowledge", "explain", "longctx", "writing", "reasoning"]


LABEL = {"agentic": "AGENTIC", "code": "CODE", "tools": "TOOLS", "techhelp": "TECH HELP", "knowledge": "KNOWLEDGE",
         "explain": "EXPLAINING", "longctx": "LONG DOCS", "writing": "WRITING", "reasoning": "REASONING"}


# hover text for each score: what it measures, its weight, example tasks (the suite's real task kinds)
TIPS = {
    "agentic": ("Agentic coding", "Work in a real repository: find and fix the bugs the README contradicts, or keep up with a request that changes mid-session. Hidden tests decide.",
                ["Ledger, inventory, shipping: 4-5 hidden bugs each", "Cart: add discount codes, then change the rule"]),
    "code": ("Code", "One function or module from a written spec. Graded only by hidden unit tests.",
             ["Expression evaluator, LRU cache, CSV parser", "Expert: cron schedule across DST changes"]),
    "tools": ("Tools & automation", "Calling business tools correctly on messy data: a CRM, invoices, payments. Wrong or extra calls cost points.",
              ["Payment reminders only to customers really overdue", "Expert: bulk discounts under an approval policy"]),
    "longctx": ("Long documents", "Exact answers from a long incident log and org chart (~80k tokens in the quick test): five questions per task.",
                ["Final root-cause code after later corrections", "Office of the manager of the engineer on an incident"]),
    "writing": ("Writing", "Text with hard constraints a checker can verify: length, required facts, glossary terms, plural rules.",
                ["Meeting minutes with owners and dates", "UI strings in Russian / Ukrainian with ICU plurals"]),
    "reasoning": ("Reasoning", "Multi-step problems with exact answers, several per problem.",
                  ["Order total with tiered discounts and tax", "Trace a function by hand; schedule jobs"]),
    "techhelp": ("Tech help", "Questions about your own machines and projects - configs, logs, a SQLite database, a git repo, a shell script's effect: every answer computed or checked on the real program.",
                 ["Which host ports does docker compose publish?", "Which files does git status list after these .gitignore files?"]),
    "knowledge": ("Knowledge & \"I don't know\"", "Exact facts developers look up (Python, shell, regular expressions, Node.js event order, error codes); two questions per task ask about things that do not exist.",
                  ["What does this one-liner print?", "Invented flags and functions: saying UNKNOWN scores"]),
    "explain": ("Explaining", "Explain a made-up system in 130-190 words; a fixed reader model must then work out 8 cases from the explanation alone.",
                ["How a CLI decides each setting", "What a month of an API costs"]),
}


def share(b: str) -> float:
    """A block's share of the site's total: the current suite weights over the blocks the ranked suite has."""
    from .. import suite
    return suite.WEIGHTS[b] / sum(suite.WEIGHTS[x] for x in BLOCKS)


for _b in TIPS:   # "Agentic coding · 31% of the total": from the code, not typed by hand
    TIPS[_b] = (f"{TIPS[_b][0]} · {share(_b) * 100:.0f}% of the total",) + TIPS[_b][1:]


def esc(s) -> str:
    return html.escape(str(s))


def _ago(ts: str) -> str:
    try:
        dt = datetime.strptime(ts[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return ts
    s = (datetime.now() - dt).total_seconds()
    return "just now" if s < 90 else f"{s/60:.0f} min ago" if s < 5400 else f"{s/3600:.0f} h ago" if s < 172800 else f"{s/86400:.0f} d ago"


def _quant(fname: str | None) -> str:
    m = re.search(r"(UD-)?(IQ\d_[A-Z]+|Q\d_[A-Z0-9_]+?|PQ\d_\d|BF16|F16)(?=[-.])", fname or "")
    extra = " · MTP" if fname and "MTP" in fname.upper() else ""
    return (m.group(0) if m else (fname or "")[:24]) + extra


# recipe id -> what tells it apart from another measured recipe of the same model file (set by build.set_variants: the
# reasoning effort, e.g. K2-Horizon at high and at medium); the quant alone names every other recipe
VARIANT: dict = {}


def variant(rid: str, fname: str | None, full: bool = False) -> str:
    """The quant, plus what sets this recipe apart when the same file is measured with other settings."""
    q = _quant(fname) if full else _quant(fname).split(" ")[0]
    return f"{q} · {VARIANT[rid]}" if VARIANT.get(rid) else q


def set_variants(host: str, rs: list[dict]) -> None:
    """Recipes that share a model and quant are told apart by their reasoning effort (or thinking on / off)."""
    from .. import recipe as rc
    VARIANT.clear()
    by: dict = {}
    for r in rs:
        by.setdefault((model_name(r), _quant(r.get("file"))), []).append(r["id"])
    for ids in by.values():
        if len(ids) < 2:
            continue
        for rid in ids:
            try:
                kw = (rc.load(host, rid).get("chat") or {}).get("template_kwargs") or {}
            except (OSError, ValueError):
                kw = {}
            VARIANT[rid] = (f"reasoning {kw['reasoning_effort']}" if kw.get("reasoning_effort") else
                            "thinking " + ("on" if kw["enable_thinking"] else "off") if "enable_thinking" in kw else rid)


INSTALL = "# llmbox goes public with this site; until then it runs from its repository"   # then: pip install llmbox


def model_name(r: dict) -> str:
    """The model's own name, from its Hugging Face repo: a recipe id (tiel-al) is llmbox's handle for a model plus its
    settings and means nothing to a visitor."""
    if (r.get("host") or {}).get("id") == "cloud":
        return {"claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-sonnet-5": "Claude Sonnet 5",
                "claude-haiku-4-5": "Claude Haiku 4.5"}.get(r["id"], r["id"])
    n = (r.get("hf_repo") or "").split("/")[-1] or re.sub(r"\.gguf$", "", os.path.basename(r.get("file") or r["id"]))
    n = n.split("_", 1)[1] if "_" in n else n          # bartowski names files org_Model
    while True:
        m = re.sub(r"(?i)[-_](gguf|mtp|i1)(?=$|[-_])", "", n)
        if m == n:
            return n
        n = m


# a model's family is its architecture as the GGUF says (Hugging Face base_model tags stop short: Tiel's chain ends at
# Ornith, itself a Qwen3.6 fine-tune): one colour per family on the chart
FAMILIES = [("qwen35moe", "Qwen MoE 35B-A3B", "#FFB000"), ("qwen3moe", "Qwen MoE", "#FFB000"), ("qwen", "Qwen dense", "#4FC3C7"),
            ("gemma", "Gemma", "#5B8DEF"), ("gpt-oss", "gpt-oss", "#9CCC65"), ("bailing", "Ling", "#B07CF5"),
            ("k2-horizon", "K2 Horizon", "#E8566C"), ("", "other", "#9AA0A6")]


def family(arch: str | None) -> tuple[str, str]:
    """(family name, colour) of a GGUF architecture."""
    a = (arch or "").lower()
    return next((n, c) for k, n, c in FAMILIES if a.startswith(k))


def _kind(repo: str | None) -> str:
    """release / fine-tune / uncensored, from the Hugging Face lineage (cached); release when unknown."""
    if not repo:
        return "release"
    try:
        from .. import candidates as C
        return C.lineage(repo).get("kind") or "release"
    except Exception:
        return "release"


def _name_of(rec: dict) -> str:
    """model_name for a saved run record."""
    m = rec.get("model") or (rec.get("recipe") or {}).get("model") or {}
    return model_name({"id": (rec.get("recipe") or {}).get("id") or "?", "hf_repo": m.get("hf_repo"), "file": m.get("file"), "host": rec.get("host")})


def _size(sh: dict | None, name: str = "") -> str:
    """What the name does not say already: '9B dense', 'MoE, 4B active', '103B MoE, 6B active' (the GGUF's own counts)."""
    if not sh or not sh.get("params") or re.search(r"A\d+(\.\d+)?B", name):   # 35B-A3B says it all
        return ""
    moe = sh.get("moe") and sh.get("active")
    act = f'{sh["active"] / 1e9:.0f}B active' if moe else ""
    if re.search(r"\d[bB](?=$|[-_. ])", name):
        return f"MoE, {act}" if moe else "dense"
    t = sh["params"] / 1e9
    return f"{t:.0f}B MoE, {act}" if moe else f"{t:.0f}B dense"


SHORT = {"agentic": "agentic coding", "code": "code", "tools": "tools", "techhelp": "tech help", "knowledge": "knowledge",
         "explain": "explaining", "longctx": "long docs", "writing": "writing", "reasoning": "reasoning"}


# the nine blocks are what is measured; a visitor chooses by use. Four uses, each the weighted mean of its blocks (the
# suite weights). What the suite does not measure is said next to them: the biggest uses of chatbots (advice, chat) and
# of open-weight models (roleplay) are not in it (docs/fast-test.md: OpenAI's usage data, OpenRouter's open-model tokens).
GROUPS = [("Coding", ["agentic", "code"]), ("Agents & tools", ["tools"]),
          ("Ask & learn", ["techhelp", "knowledge", "explain", "reasoning"]), ("Documents & writing", ["longctx", "writing"])]


NOT_MEASURED = "everyday advice and chat, roleplay and fiction, images and voice"


GROUP_TIPS = {"Coding": "Fixing bugs in real multi-file projects, keeping up with a request that changes turn by turn, and single functions from a spec. Hidden tests decide.",
              "Agents & tools": "Calling business tools correctly on messy data (a CRM, invoices, payments) under a written policy. Wrong or extra calls cost points.",
              "Ask & learn": "Exact answers about your machine and projects (configs, logs, SQL, git, shell), developer facts, saying \"I don't know\", explaining a system so a reader can use it, multi-step problems.",
              "Documents & writing": "Exact answers from a long document with later corrections, and writing under constraints a program checks: minutes, rewrites, translations of UI strings."}


PRESETS = [("All work", {b: round(share(b) * 100) for b in BLOCKS})] + [(n, {b: round(share(b) * 100) for b in bs}) for n, bs in GROUPS]


def _wavg(blocks: dict | None, bs: list[str]) -> float | None:
    """The weighted mean of these blocks (the suite weights) over the ones present."""
    from .. import suite
    xs = [(blocks[b], suite.WEIGHTS[b]) for b in bs if blocks and blocks.get(b) is not None]
    return sum(v * w for v, w in xs) / sum(w for _, w in xs) if xs else None


# what each task kind is, in words (the item id says block.kind.Llevel.seed)
TASK_NAMES = {
    "agentic.inventory": "fix the bugs in an inventory service", "agentic.ledger": "fix the bugs in a ledger",
    "agentic.sheet": "fix the bugs in a spreadsheet engine", "agentic.shipping": "fix the bugs in a shipping calculator",
    "agentic.tmpl": "fix the bugs in a template engine", "agentic.cart": "chat session: a shopping cart whose rules change",
    "agentic.fetch": "chat session: an HTTP fetcher", "agentic.spend": "chat session: a spending tracker", "agentic.todo": "chat session: a to-do app",
    "code.rle": "run-length compression", "code.merge": "merge intervals", "code.levels": "count log levels", "code.base": "number bases",
    "code.topk": "top keys by total", "code.expr": "expression evaluator", "code.lru": "LRU cache with expiry", "code.csv": "CSV parser",
    "code.rooms": "meeting-room booking", "code.cron": "cron schedule across DST",
    "tools.reminders": "payment reminders", "tools.followup": "follow-up emails", "tools.total": "invoice totals", "tools.conditional": "conditional updates",
    "tools.recovery": "recover from a failed call", "tools.dunning": "dunning run by a policy", "tools.reconcile": "bank reconciliation",
    "tools.outreach": "win-back outreach", "tools.bulk_discount": "discounts under an approval policy", "tools.dedupe": "merge duplicate customers",
    "techhelp.compose_port": "docker compose ports", "techhelp.nginx_route": "nginx routing", "techhelp.subnet": "routing table",
    "techhelp.chmod_seq": "chmod chains", "techhelp.log_root": "root cause in logs", "techhelp.sql": "SQLite queries",
    "techhelp.gitignore": ".gitignore rules", "techhelp.git_seq": "long git session", "techhelp.fs_seq": "long shell script",
    "knowledge.python": "Python facts", "knowledge.shell": "shell facts", "knowledge.codes": "error codes and defaults",
    "knowledge.regex": "regular expressions", "knowledge.js": "Node.js event order",
    "explain.config": "explain a build tool's config", "explain.billing": "explain an API's pricing", "explain.access": "explain sharing permissions",
    "longctx.lookup": "look up facts in a long log", "longctx.multihop": "follow links across a long log", "longctx.count": "count in a long log",
    "longctx.latest": "latest value after corrections", "longctx.total": "sum over a long log", "longctx.audit": "fact-check a report against a log",
    "writing.constrained": "text under hard constraints", "writing.translate": "translation", "writing.summarize": "summarize a thread",
    "writing.extract": "extract orders as JSON", "writing.rewrite": "rewrite in a set tone", "writing.minutes": "meeting minutes",
    "writing.i18n": "UI strings with plural rules", "writing.proofread": "proofreading",
    "reasoning.arith": "order pricing", "reasoning.dates": "working-day dates", "reasoning.logic": "logic puzzle",
    "reasoning.code_trace": "trace a program by hand", "reasoning.table": "join and group two tables", "reasoning.schedule": "project schedule",
    "reasoning.budget": "choose projects under a budget"}


def task_name(item_id: str) -> str:
    """'agentic.shipping.L7.3' -> 'fix the bugs in a shipping calculator · level 7'."""
    p = item_id.split(".")
    name = TASK_NAMES.get(".".join(p[:2]), p[1].replace("_", " ") if len(p) > 1 else item_id)
    return f"{name} · level {p[2][1:]}" if len(p) > 2 and p[2][:1] == "L" else name


def _lineage(repo: str | None) -> dict:
    try:
        from .. import candidates as C
        return C.lineage(repo) if repo else {}
    except Exception:
        return {}


_KNAMES = {"sampling.temp": "Temperature", "sampling.top_p": "Top-p", "sampling.top_k": "Top-k", "sampling.min_p": "Min-p",
           "sampling.presence_penalty": "Presence penalty", "sampling.repeat_penalty": "Repeat penalty",
           "chat.template_kwargs.enable_thinking": "Thinking", "chat.template_kwargs.preserve_thinking": "Keep thinking between turns",
           "chat.template_kwargs.reasoning_effort": "Reasoning effort", "placement.kv_type": "KV cache", "placement.ctx": "Context",
           "antiloop.reasoning_budget": "Reasoning reserve", "antiloop.marker_bias": "Loop-marker bias", "speculative.type": "Speculative decoding",
           "speculative.draft_max": "Draft tokens"}


def _human(v) -> str:
    return "default" if v is None else "on" if v is True else "off" if v is False else str(v)


_GRADING = {   # how each block is graded (from the suite modules' own descriptions)
    "agentic": "A sandboxed copy of a real multi-module project with injected bugs or a missing feature; the model works with bash / read / edit tools, "
               "and in {sessions} of {agentic} tasks the requirements change turn by turn, the way people talk to a coding assistant. "
               "Graded by hidden tests copied in only at grading time: doing nothing scores 0.",
    "code": "One function or module from a written spec, in Python or JavaScript. Hidden unit tests from a reference implementation decide.",
    "tools": "Function calls against a simulated CRM with fresh customers and invoices. Graded on the final state of that world: "
             "a wrong or extra email, discount or payment costs points.",
    "techhelp": "Generated configs and logs of a machine (docker compose, nginx, routing tables, chmod chains, journal logs), SQLite queries, "
                ".gitignore files, long git and shell command sequences, with several questions each. Answers come from the real program "
                "(SQLite runs the queries) or from a model of it checked against the real one (nginx, iproute2, git, GNU coreutils, "
                "tests/real_programs.py). Credit per question.",
    "knowledge": "8 short questions per task on Python, shell, regular expressions, Node.js event order and error codes, answers taken from "
                 "running the real thing (CPython, bash, Node 22); two per task "
                 "ask about something that does not exist. Right 1, UNKNOWN 1/3 (made-up: 1), invented 0.",
    "explain": "The model explains a made-up system within a word limit; a fixed reader model (gpt-oss-20b, greedy) then works out 8 "
               "cases from the explanation alone. The score is the share it gets right.",
    "longctx": "Five questions per task on a long incident log (~80k tokens in the quick test), with later corrections that override "
               "earlier facts. Exact answers, credit per question.",
    "writing": "Minutes, rewrites, proofreading, UI strings with plural rules. Every constraint (length, facts, glossary terms) is checked by a program; "
               "the score is the share of constraints met.",
    "reasoning": "Multi-step problems with exact answers, several per problem (subtotal, tax, total; every printed line of a traced "
                 "program). Credit per answer.",
}
