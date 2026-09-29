"""`llmbox site`: the public static site, generated from saved results (design "Oscilloscope", llmbox/site_assets/osc.css).

Pages are built only from records: suite results, the job queue (what is being measured now) and speed probes. Nothing
on a page is typed by hand, so the site cannot drift from the data. Structure follows what users of benchmark sites
value: UserBenchmark (ranked tiles, your box among the same hardware), Artificial Analysis (quality x speed with a
Pareto line), LocalScore (time to first token), LMArena (ties when a difference is inside its margin of error).
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sqlite3
import statistics
import time
from datetime import datetime

from . import report
from . import suite as _suite
from .hosts import HOME

ASSETS = os.path.join(os.path.dirname(__file__), "site_assets")
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
    from . import suite
    return suite.WEIGHTS[b] / sum(suite.WEIGHTS[x] for x in BLOCKS)


for _b in TIPS:   # "Agentic coding · 31% of the total": from the code, not typed by hand
    TIPS[_b] = (f"{TIPS[_b][0]} · {share(_b) * 100:.0f}% of the total",) + TIPS[_b][1:]

# ranking presets: block weights (the current suite weights first); the page recomputes the total and the order


def esc(s) -> str:
    return html.escape(str(s))


def _tile(v: float | None, small: str = "", big: bool = False) -> str:
    if v is None:
        return '<span class="tile na">—</span>'
    c = "hi" if v >= 85 else ("mid" if v >= 50 else "lo")
    return f'<span class="tile {c}{" big" if big else ""}">{v:.0f}{"%" if big else ""}{f"<small>{esc(small)}</small>" if small else ""}</span>'


def _tip(block: str, open_: bool = False) -> str:
    title, text, ex = TIPS[block]
    lis = "".join(f"<li>{esc(e)}</li>" for e in ex)
    return f'<span class="tip{" open" if open_ else ""}">{LABEL[block]}<span class="pop"><b>{esc(title)}</b>{esc(text)}<ul>{lis}</ul></span></span>'


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
        from . import candidates as C
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


def _se(r: dict, up: bool) -> float:
    """Standard error of a score from its 95% interval, on the side facing the other model (intervals are skewed)."""
    lo, hi = r["ci"]
    return max(0.3, ((hi - r["capability"]) if up else (r["capability"] - lo)) / 1.96)


def surely_better(a: dict, b: dict) -> bool:
    """a is measurably better than b: the difference is outside its own 95% margin. Stricter than 'the two intervals do
    not overlap' is loose: overlapping intervals can still hold a real difference."""
    d = a["capability"] - b["capability"]
    return d > 1.96 * (_se(a, False) ** 2 + _se(b, True) ** 2) ** 0.5


def rank_ranges(rs: list[dict]) -> dict:
    """{id: (place, first tied place, last tied place, group)}: the place by score, the places a model is not
    measurably apart from, and groups cut where the next model is measurably worse than the top of the current group."""
    order = sorted(rs, key=lambda r: -(r["capability"] or 0))
    out, group, head = {}, 0, None
    for i, r in enumerate(order):
        if head is not None and surely_better(head, r):
            group, head = group + 1, r
        head = head or r
        tied = [j for j, o in enumerate(order) if o is r or not (surely_better(o, r) or surely_better(r, o))]
        out[r["id"]] = (i + 1, min(tied) + 1, max(tied) + 1, group)
    return out


SHORT = {"agentic": "agentic coding", "code": "code", "tools": "tools", "techhelp": "tech help", "knowledge": "knowledge",
         "explain": "explaining", "longctx": "long docs", "writing": "writing", "reasoning": "reasoning"}


def _marker(col: str, kind: str, s: int = 14) -> str:
    """The chart's marker: filled = the maker's release, ring = a fine-tune, diamond = an uncensored remix."""
    c, r = s / 2, s / 2 - 2
    mk = (f'<path d="M{c} {c - r - 1}L{c + r + 1} {c}L{c} {c + r + 1}L{c - r - 1} {c}Z" fill="none" stroke="{col}" stroke-width="2"/>' if kind == "uncensored"
          else f'<circle cx="{c}" cy="{c}" r="{r}" fill="{"none" if kind == "fine-tune" else col}" stroke="{col}" stroke-width="2"/>')
    return f'<svg class="mk" width="{s}" height="{s}" aria-hidden="true">{mk}</svg>'


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f}%"


def _spd(sp: dict) -> str:
    tps, deep = sp.get("decode_tps"), report._deep(sp)
    return f"<b>{tps:.0f}</b><small>{'' if deep == '-' else f'{float(deep):.0f} long'}</small>" if tps else "—"


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
    from . import suite
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
        from . import candidates as C
        return C.lineage(repo) if repo else {}
    except Exception:
        return {}


def _stand_words(blocks: dict, med: dict, gap: float = 6) -> tuple[list, list, bool]:
    """(blocks clearly above the typical local model, blocks clearly below, behind everywhere) - two of each at most."""
    d = {b: blocks[b] - med[b] for b in BLOCKS if blocks.get(b) is not None and b in med}
    up = [b for b, v in sorted(d.items(), key=lambda x: -x[1]) if v >= gap][:2]
    dn = [b for b, v in sorted(d.items(), key=lambda x: x[1]) if v <= -gap][:2]
    return up, dn, bool(d) and all(v <= -gap for v in d.values())


def _stands_out(blocks: dict, med: dict) -> str:
    """The blocks where a model is clearly above or below the typical local model (the median), two of each at most."""
    up, dn, behind = _stand_words(blocks, med)
    if behind:
        return '<span class="dn">▼ behind on every block</span>'
    out = ([f'<span class="up">▲ {" · ".join(SHORT[b] for b in up)}</span>'] if up else []) + ([f'<span class="dn">▼ {" · ".join(SHORT[b] for b in dn)}</span>'] if dn else [])
    return "".join(out) or '<span class="ev">even, close to typical</span>'


def _stands_sentence(blocks: dict, med: dict) -> str:
    up, dn, behind = _stand_words(blocks, med)
    if behind:
        return "Behind the typical local model here on every block."
    j = lambda bs: " and ".join(SHORT[b] for b in bs)
    parts = ([f"stronger at <b class='up'>{j(up)}</b>"] if up else []) + ([f"weaker at <b class='dn'>{j(dn)}</b>"] if dn else [])
    return ("Compared with the typical local model here: " + "; ".join(parts) + ".") if parts else "Close to the typical local model here on every block."


def _bar(v: float, col: str, med: float | None = None, ref: float | None = None, big: bool = False) -> str:
    return (f"<span class='bt{' big' if big else ''}'><i style='width:{v:.0f}%;background:{col}'></i>"
            + (f"<u style='left:{med:.0f}%' title='typical local model: {med:.0f}'></u>" if med is not None else "")
            + (f"<s style='left:{ref:.0f}%' title='Claude Opus 5.5: {ref:.0f}'></s>" if ref is not None else "") + "</span>")


def _groups_html(blocks: dict, med: dict, col: str, ref: dict | None = None, lost: dict | None = None) -> str:
    """The four uses, each a bar with its blocks under it (a lone block is the use itself). With `lost`, a block opens the
    tasks the model lost there and why."""
    out = []
    for name, bs in GROUPS:
        g = _wavg(blocks, bs)
        if g is None:
            continue
        rows = []
        for b in bs if len(bs) > 1 else []:
            v = blocks.get(b)
            if v is None:
                continue
            row = f"<span class='bn'>{SHORT[b]}</span>{_bar(v, col, med.get(b), (ref or {}).get(b))}<b>{v:.0f}</b>"
            ls = (lost or {}).get(b)
            rows.append(f"<details class='bb'><summary>{row}</summary><ul>{''.join(f'<li>{x}</li>' for x in ls)}</ul></details>" if ls else f"<div class='bb'>{row}</div>")
        one = (lost or {}).get(bs[0]) if len(bs) == 1 else None
        head = f"<span class='gn tip'>{esc(name)}<span class='pop'><b>{esc(name)}</b>{esc(GROUP_TIPS[name])}</span></span><b class='gv'>{g:.0f}</b>{_bar(g, col, _wavg(med, bs), _wavg(ref, bs) if ref else None, big=True)}"
        out.append(f"<div class='grp'>"
                   + (f"<details class='gh'><summary>{head}</summary><ul>{''.join(f'<li>{x}</li>' for x in one)}</ul></details>" if one else f"<div class='gh'>{head}</div>")
                   + "".join(rows) + "</div>")
    return f"<div class='groups'>{''.join(out)}</div>"


def _groups_legend(ref: bool = False) -> str:
    return (f"<p class='q glg'>Out of 100 · <u></u> typical local model here" + (" · <s></s> Claude Opus 5.5" if ref else "")
            + f" · not measured: {NOT_MEASURED}</p>")


def _profile(r: dict, med: dict, col: str, rank: tuple) -> str:
    """Under a ranking line: the four uses with their blocks, the typical local model marked."""
    rid = r["id"]
    pl, lo, hi, _ = rank
    k = (r.get("vs_ref") or 0) / r["capability"] if r["capability"] else 0
    return (f"<div class='pf'>{_groups_html(r['blocks'], med, col)}{_groups_legend()}<div class='pfl'>"
            + (f"<span>Overall {r['vs_ref']:.0f}% of Claude Opus 5.5 · 95% range {r['ci'][0] * k:.0f}–{min(100, r['ci'][1] * k):.0f} · "
               f"place {pl}{f', tied with places {lo}–{hi}' if lo != hi else ''}</span>" if k else "")
            + f"<a href='recipe-{esc(rid)}.html'>File, settings and every task →</a><a href='hardware-{esc(rid)}.html'>Speed on other boxes →</a></div></div>")


def pareto(points: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Non-dominated points in (speed, quality), sorted by speed."""
    front = [p for p in points if not any(q[0] >= p[0] and q[1] >= p[1] and (q[0] > p[0] or q[1] > p[1]) for q in points)]
    return sorted(front)


def queue_state() -> list[dict]:
    """Jobs being measured or waiting (running first), with progress from the job log."""
    db = os.path.join(HOME, "queue.db")
    if not os.path.exists(db):
        return []
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    out = []
    for j in c.execute("SELECT * FROM jobs WHERE status IN ('running','queued','interrupted') ORDER BY status='running' DESC, priority DESC, id"):
        done = total = 0
        try:
            m = re.findall(r"\[\s*(\d+)/(\d+)\]", open(j["log"]).read())
            if m:
                done, total = int(m[-1][0]), int(m[-1][1])
        except (OSError, TypeError):
            pass
        out.append({"model": j["model"], "status": j["status"], "done": done, "total": total})
    return out


def _scatter(local: list[dict]) -> str:
    """Quality (% of frontier) x decode speed; the page's JS redraws it for the visitor's box (same drawing as SCATTER_JS)."""
    pts = [{"id": r["id"], "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "t2": r["speed"].get("decode_tps")} for r in local]
    return f'<noscript>{len(pts)} models; enable JavaScript for the chart</noscript>'


# common GPUs: VRAM (MiB) and memory bandwidth (GB/s), for the "your box" picker
GPUS = [("RTX 3060 12 GB", 12288, 360), ("RTX 3090 24 GB", 24576, 936), ("RTX 4060 Ti 16 GB", 16380, 288),
        ("RTX 4070 12 GB", 12282, 504), ("RTX 4070 Ti Super 16 GB", 16376, 672), ("RTX 4080 16 GB", 16376, 717),
        ("RTX 4090 24 GB", 24564, 1008), ("RTX 5060 Ti 16 GB", 16311, 448), ("RTX 5070 12 GB", 12227, 672),
        ("RTX 5070 Ti 16 GB", 16303, 896), ("RTX 5080 16 GB", 16303, 960), ("RTX 5090 32 GB", 32607, 1792)]
# Apple Silicon: unified memory, so no VRAM/RAM split - (name, 0, memory bandwidth GB/s, "mac", largest memory GB).
# Bandwidth: Apple's specs (M5 Pro 307, M5 Max 460 / 614: apple.com newsroom 2026-03). Nothing is measured on a Mac here.
MACS = [("Mac M1", 0, 68, "mac", 16), ("Mac M1 Pro", 0, 200, "mac", 32), ("Mac M1 Max", 0, 400, "mac", 64), ("Mac M1 Ultra", 0, 800, "mac", 128),
        ("Mac M2", 0, 100, "mac", 24), ("Mac M2 Pro", 0, 200, "mac", 32), ("Mac M2 Max", 0, 400, "mac", 96), ("Mac M2 Ultra", 0, 800, "mac", 192),
        ("Mac M3", 0, 100, "mac", 24), ("Mac M3 Pro", 0, 150, "mac", 36), ("Mac M3 Max 30-core GPU", 0, 300, "mac", 96),
        ("Mac M3 Max 40-core GPU", 0, 400, "mac", 128), ("Mac M3 Ultra", 0, 819, "mac", 512),
        ("Mac M4", 0, 120, "mac", 32), ("Mac M4 Pro", 0, 273, "mac", 64), ("Mac M4 Max 32-core GPU", 0, 410, "mac", 36),
        ("Mac M4 Max 40-core GPU", 0, 546, "mac", 128), ("Mac M5", 0, 153, "mac", 32), ("Mac M5 Pro", 0, 307, "mac", 64),
        ("Mac M5 Max 32-core GPU", 0, 460, "mac", 128), ("Mac M5 Max 40-core GPU", 0, 614, "mac", 128)]
GPUS = GPUS + MACS
RAM_KINDS = [("DDR4-3200", 40), ("DDR5-5600", 60), ("DDR5-6400", 75), ("DDR5-8000", 88)]


def shape_data(local: list[dict], host: str = "box") -> dict:
    """Per recipe: the GGUF shape numbers estimate.plan() uses, plus the calibration measured / predicted on the
    reference box (llmbox.fit, the same numbers `llmbox fit` prints)."""
    from . import fit as F, hosts, recipe as rc
    prof = hosts.load(host)
    ref_hw = hosts.spec(prof)
    out = {}
    for r in local:
        try:
            rec = rc.load(host, r["id"])
        except (OSError, ValueError):
            continue
        sh = F.shape_for(rec, host=hosts.host_of(prof))
        cal = F.calibration(rec, sh, host)
        kv, ctx = rec["placement"]["kv_type"], rec["placement"]["ctx"] or sh.context_length
        out[r["id"]] = {"moe": sh.is_moe, "nonexp": sh.nonexpert_bytes, "exp": sh.expert_bytes, "embed": sh.embed_bytes,
                        "layers": sh.n_layers, "nExp": sh.n_expert, "nUsed": sh.n_expert_used, "rec": sh.recurrent_state_bytes + sh.kv_swa_bytes(kv),
                        "cpuEff": sh.expert_cpu_eff, "kvB": sh.kv_bytes_per_token(kv), "ctx": ctx, "k2": round(cal.k2, 4), "kd": round(cal.kd, 4),
                        "deepK": cal.deep_k, "size": round((sh.total_bytes or 0) / 1e9, 1),
                        "arch": sh.arch, "params": int(sh.total_params * (1 - (sh.mtp_bytes or 0) / sh.total_bytes)) if sh.total_bytes else 0,
                        "active": sh.active_params}
    return {"recipes": out, "ref": {"gpu": prof["hw"]["gpus"][0]["name"].replace("NVIDIA GeForce ", "") if prof["hw"]["gpus"] else "",
                                    "vram": ref_hw.vram_mib, "ram": ref_hw.ram_mib, "rambw": ref_hw.ram_bw_gbs, "vrambw": ref_hw.vram_bw_gbs},
            "gpus": GPUS, "ramKinds": RAM_KINDS}


def home(out_dir: str, host: str = "box", suite_version: str | None = None, tier: str = "quick") -> str:
    from . import suite as _s
    suite_version = suite_version or _s.VERSION
    rs = report.rows(host, suite_version=suite_version, tier=tier)
    ref = next((r for r in rs if r["host"].get("id") == "cloud"), None)
    local = [r for r in rs if r["host"].get("id") != "cloud" and not r.get("partial")]
    clouds = [r for r in rs if r["host"].get("id") == "cloud" and not r.get("partial")]
    hw = next((r["host"] for r in local), {})
    ref_box = f'{hw.get("gpu", "").replace("NVIDIA GeForce ", "")} + {hw.get("ram_gib", "?")} GB RAM'
    ranks = rank_ranges(local)
    q = [j for j in queue_state() if j["model"] not in {r["id"] for r in local}]
    sd = shape_data(local, host)
    names0 = {r["id"]: model_name(r) for r in local}
    # one model in two quants (Tiel Q4 and Q6): the chart legend and the picks say which is which
    dup = {n for n in names0.values() if list(names0.values()).count(n) > 1}
    labels = {rid: n + (f" · {_quant(next(r['file'] for r in local if r['id'] == rid)).split(' ')[0]}" if n in dup else "") for rid, n in names0.items()}

    def pop(label: str, title: str, text: str) -> str:
        return f'<span class="tip">{label}<span class="pop"><b>{esc(title)}</b>{esc(text)}</span></span>'
    # what llmbox's settings are worth: the biggest measured gains over stock llama.cpp, same model, same box
    opts = {k: v for k, v in optimize_records(host).items() if k in {r["id"] for r in local}}
    gain = lambda o: o["summary"]["llmbox"]["decode"] / o["summary"]["stock"]["decode"] - 1
    top = sorted(opts.items(), key=lambda kv: -gain(kv[1]))[:4]
    optpanel = ("" if not top or gain(top[0][1]) < 0.15 else
                '<section class="panel feed optp"><div class="lbl">What the settings are worth</div><p class="q nf0">Same model, same PC: stock llama.cpp → llmbox settings.</p><ul>'
                + "".join(f'<li><a href="recipe-{esc(rid)}.html">{esc(names0[rid])}</a> <span class="fv">{o["summary"]["stock"]["decode"]:.0f} → {o["summary"]["llmbox"]["decode"]:.0f} tok/s</span>'
                          f'<em>{gain(o) * 100:+.0f}%</em></li>' for rid, o in top)
                + '</ul><p class="q nf"><a href="method.html#settings">all models →</a></p></section>')
    # the ranking: one line per model (place, name, score with its range, speed, fit, what stands out); the nine block
    # scores open under the line. Colour and marker as on the chart.
    med = {b: statistics.median(v) for b in BLOCKS if (v := [r["blocks"][b] for r in local if r["blocks"].get(b) is not None])}
    head = ("<tr><th class='rk'>#</th><th class='l'>MODEL</th><th class='sch' data-sort='score'><div class='fp'><div class='trk axis'></div><span class='num'>"
            + pop("SCORE ↕", "Score · % of Claude Opus 5.5", "How close the model gets to Claude Opus 5.5 on the same tasks (Opus = 100%). "
                  "The dot is the score, the line its 95% range: models whose lines overlap are not measurably apart yet. Click to sort.")
            + "</span></div></th><th class='r' data-sort='speed'>" + pop("TOK/S ↕", "Speed on your box", "Tokens per second while writing the answer, "
            "in a short chat (big number) and with a long document in context (small). Measured on the reference PC, predicted for the box you pick. Click to sort.")
            + "</th><th class='r'>" + pop("FITS", "Does it fit?", "Whether the model and its context fit in the graphics card plus RAM of the box you pick, "
            "and the largest context that does.") + "</th><th class='l'>" + pop("STANDS OUT", "Stands out", "Blocks where the model scores at least "
            "6 points above (▲) or below (▼) the typical (median) local model here. Click a row for all nine.") + "</th><th></th></tr>")
    body = []
    for r in local:
        rid, nm = r["id"], model_name(r)
        pl, lo, hi, grp = ranks[rid]
        tip = f"not measurably apart from places {lo}–{hi}" if lo != hi else "measurably apart from every other model"
        col, kind = family((sd["recipes"].get(rid) or {}).get("arch"))[1], _kind(r.get("hf_repo"))
        sub = " · ".join(x for x in (_quant(r["file"]), _size(sd["recipes"].get(rid), nm), kind if kind != "release" else "") if x)
        body.append(f"<tr class='mr' data-rid='{esc(rid)}' data-g='{grp}'><td class='rk' title='{tip}'>{pl}</td>"
                    f"<td class='l mod'><div class='mw'>{_marker(col, kind)}<a class='m' href='recipe-{esc(rid)}.html'>{esc(nm)}</a><span class='qt'>{esc(sub)}</span></div></td>"
                    f"<td class='sco'>{_pct(r.get('vs_ref'))}</td><td class='spd r'>{_spd(r['speed'])}</td><td class='fit r'>—</td>"
                    f"<td class='l so'>{_stands_out(r['blocks'], med)}</td>"
                    f"<td class='act'><label class='pick2' title='tick two to compare'><input type='checkbox' value='{esc(rid)}' aria-label='compare {esc(nm)}'></label>"
                    f"<button class='exp' aria-expanded='false' aria-label='all block scores of {esc(nm)}'>▾</button></td></tr>")
        body.append(f"<tr class='prof' data-for='{esc(rid)}' hidden><td colspan='7'>{_profile(r, med, col, ranks[rid])}</td></tr>")
    # cloud models: reference lines in the order (same tasks, same scale), not places in a ranking of what runs on a box
    for r in clouds:
        note = "cloud · the 100% mark" if ref and r["id"] == ref["id"] else "cloud · for comparison"
        body.append(f"<tr class='cloud' data-rid='{esc(r['id'])}'><td class='rk'>☁</td><td class='l mod'><div class='mw'><span></span><span class='m'>{esc(model_name(r))}</span><span class='qt'>{note}</span></div></td>"
                    f"<td class='sco'>{_pct(r.get('vs_ref'))}</td><td class='spd r'></td><td class='fit'></td><td class='so'></td><td class='act'></td></tr>")
    qline = ""
    run = next((j for j in q if j["status"] == "running"), None)
    nxt = [j["model"] for j in q if j is not run]
    if run or nxt:
        w = 100 * run["done"] / run["total"] if run and run["total"] else 0
        qline = ("<div class='queue'>" + (f"<span class='live'>●</span> Measuring <b>{esc(run['model'])}</b><span class='prog'><i style='width:{w:.0f}%'></i></span>"
                                          + (f"<span class='q'>{run['done']} of {run['total']} tasks</span>" if run["total"] else "<span class='q'>starting</span>") if run else "")
                 + (f"<span class='q nx'>Next: {esc(', '.join(nxt))}</span>" if nxt else "") + "</div>")

    feed, names = [], {r["id"]: model_name(r) for r in local + clouds}
    for j in q:
        if j["status"] == "running":
            feed.append(f"<li><span class='live'>●</span> <b>{esc(j['model'])}</b> <span class='q'>{f"measuring {j['done']}/{j['total']}" if j['total'] else "starting"}</span><span class='when'>now</span></li>")
    for rec in sorted(report.results.load_all(host) + report.results.load_all("cloud"), key=lambda x: x.get("created", ""), reverse=True):
        su, s = rec.get("suite", {}), rec.get("summary", {})
        if rec.get("kind") != "suite" or report.version_of(su) != suite_version or report.scale(su.get("tier")) != report.scale(tier) \
                or su.get("blocks"):
            continue   # only results comparable with the ranking above
        rid = (rec.get("recipe") or {}).get("id", "?")
        cloud = (rec.get("host") or {}).get("id") == "cloud"
        tps = (s.get("speed") or {}).get("decode_tps")
        nm = names.get(rid, rid)
        name = f"<a href='recipe-{esc(rid)}.html'>{esc(nm)}</a>" if rid in {r["id"] for r in local} else f"<b>{esc(nm)}</b>"
        feed.append(f"<li>{name} <span class='fv' title='the score of this one run; the ranking pools every run of the model'>"
                    f"{s.get('capability', 0):.1f} this run{f' · {tps:.0f} tok/s' if tps else ''}</span>"
                    f"<span class='when'>{'cloud' if cloud else esc((rec.get('host') or {}).get('gpu', '?').replace('NVIDIA GeForce ', ''))} · {_ago(rec.get('created', ''))}</span></li>")
        if len(feed) >= 6:
            break

    presets = "".join(f'<button class="{"on" if i == 0 else ""}" data-p="{i}" title="{esc(" · ".join(f"{LABEL[b].lower()} {v}" for b, v in w.items()))}">{esc(n)}</button>' for i, (n, w) in enumerate(PRESETS))
    data = dict(sd, families=[[n, c] for _k, n, c in FAMILIES], presets=[w for _, w in PRESETS], refBlocks=(ref or {}).get("blocks") or {},
                points=[{"id": r["id"], "name": labels[r["id"]], "model": names0[r["id"]], "quant": _quant(r["file"]).split(" ")[0],
                         "fam": family((sd["recipes"].get(r["id"]) or {}).get("arch"))[0], "kind": _kind(r.get("hf_repo")), "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "blocks": r["blocks"], "t2": r["speed"].get("decode_tps"),
                         "td": float(report._deep(r["speed"])) if report._deep(r["speed"]) != "-" else None, "rank": list(ranks[r["id"]])} for r in local]
                + [{"id": r["id"], "name": model_name(r), "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "blocks": r["blocks"], "t2": None, "td": None,
                    "rank": None, "cloud": True} for r in clouds])
    compare_tab = "compare.html"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>llmbox · What should I run on my box?</title><link rel="stylesheet" href="osc.css"><style>{_HOME_CSS}</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>
<div class="wrap">
<header class="plate"><a class="brand glow" href="index.html">LLMBOX<small>LOCAL LLM BENCHMARK</small></a>
 <nav class="tabs"><a class="on" href="index.html">MODELS</a><a href="new.html">NEW</a><a href="{esc(compare_tab)}">COMPARE</a><a href="method.html">METHOD</a></nav></header>
<h1 class="q1">What should I run on my box?</h1>
<p class="lede">AI models you can run on your own computer, graded on real work (coding, tools, documents, writing) and timed on a real PC.
Pick your graphics card or Mac: the table shows what fits, how fast it answers and how close it gets to Claude. Every model page has the file to download and settings to copy.</p>
{NEWBIE}
<section class="boxbar"><span class="sc">Your box</span>
 <select id="gpu" aria-label="GPU or Mac"><option value="">the reference PC ({esc(ref_box)})</option></select>
 <span class="bl">RAM</span><select id="ram" aria-label="System RAM or a Mac's unified memory"><option value="8">8 GB</option><option value="16">16 GB</option><option value="24">24 GB</option><option value="32">32 GB</option><option value="36">36 GB</option><option value="48">48 GB</option><option value="64" selected>64 GB</option><option value="96">96 GB</option><option value="128">128 GB</option><option value="192">192 GB</option><option value="256">256 GB</option><option value="512">512 GB</option></select>
 <span class="bl" id="bwl">speed</span><select id="bw" aria-label="RAM speed"></select>
 <input id="bwn" placeholder="GB/s" size="5" aria-label="measured RAM read speed, GB/s" title="your measured RAM read speed (llmbox host add)">
 <span id="boxnote" class="q">speeds measured on this box</span></section>
<section class="panel chart hero"><h2 class="ch2">Smarter or faster: what runs best on your box</h2>
 <div id="scatter">{_scatter(local)}</div>
 <p class="cap">Each point is a model with the settings it was measured with. Higher = closer to Claude Opus 5.5 on the same tasks;
 further right = faster on the box you picked above. Bright points are the best trade-offs: no other model is both smarter and faster.
 Point at a model for its range and speed, click it for its page. <a href="method.html">How scores work</a></p></section>
<section class="panel rankp"><div class="lbl">Ranking <span class="faint">· suite v{esc(suite_version)}{" · preliminary: runs of this version are still coming in" if "-dev" in suite_version else ""}</span></div>
 <div class="rhead"><div class="seg" role="group" aria-label="rank by"><span class="sc">Rank by</span>{presets}</div>
  <div class="cmp"><span class="q" id="cmpn">tick two models to compare</span><a class="btn" id="cmpgo" aria-disabled="true">COMPARE</a></div></div>
 <div class="tw"><table class="rank"><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>
 <p class="rnote">Places by score. A dashed line between rows: every model above it is measurably better than the ones below; inside a group the order is not settled yet.
 Click a row for its nine block scores.</p>{qline}</section>
<div class="below">{optpanel}<section class="panel feed"><div class="lbl">Latest results</div><ul>{''.join(feed)}</ul></section>
 {_news_panel({r["id"] for r in local})}</div>
<footer><span>Every number comes from a saved run. The score does not depend on the box; speed does. <a href="method.html">How scores work →</a></span><span>generated {time.strftime('%b %d, %Y %H:%M')}</span></footer>
</div>
<script>const DATA = {json.dumps(data)};
{PLAN_JS}{_JS}</script></body></html>"""
    os.makedirs(out_dir, exist_ok=True)
    shutil.copy(os.path.join(ASSETS, "osc.css"), os.path.join(out_dir, "osc.css"))
    path = os.path.join(out_dir, "index.html")
    with open(path, "w") as f:
        f.write(page)
    return path


_NEWS_LABEL = {"new_model": "NEW MODEL", "new_files": "NEW FILES", "repo_update": "UPDATED", "runtime": "RUNTIME", "pr": "LLAMA.CPP"}


def _news_panel(ranked: set) -> str:
    """What's new out there (llmbox/watch.py, a daily look): new models, new files of measured ones, runtime releases."""
    from . import watch as W
    evs = W.events(8)
    try:
        checked = json.load(open(W.STATE)).get("checked", "")
    except (OSError, ValueError):
        checked = ""
    if not evs and not checked:
        return ""
    items = []
    for e in evs:
        rids = [r for r in e.get("rids") or [] if r in ranked]
        items.append(f'<li><span class="nt {esc(e["type"])}">{_NEWS_LABEL.get(e["type"], e["type"].upper())}</span> '
                     + (f'<a href="{esc(e["url"])}" rel="noopener">{esc(e["title"])}</a>' if e.get("url") else f"<b>{esc(e['title'])}</b>")
                     + (f'<span class="nd">{esc(e["detail"])}</span>' if e.get("detail") else "")
                     + "".join(f'<a class="nr" href="recipe-{esc(r)}.html">our results →</a>' for r in rids[:1])
                     + f'<span class="when">{_ago(e["at"])}</span></li>')
    foot = f'<p class="q nf">Checked daily for new models, new files of the models above and runtime releases{f" · last look {_ago(checked)}" if checked else ""}.</p>'
    return (f'<section class="panel feed news"><div class="lbl">What\'s new</div>'
            + (f"<ul>{''.join(items)}</ul>" if items else '<p class="q nf">Nothing new since the first look.</p>') + foot + "</section>")


# for visitors new to local models: how to read the page and the words it uses; closed by default
NEWBIE = """<details class="newbie"><summary>New to local models? How to use this page and what the words mean</summary>
<div class="nb"><ol class="steps">
<li><b>Pick your box.</b> Choose your graphics card (or your Mac) and how much RAM it has. Speeds and the FITS column change to your box.</li>
<li><b>Choose between smarter and faster.</b> The score says how close a model gets to Claude on the same tasks; tok/s says how fast it writes.
&ldquo;Rank by&rdquo; re-sorts for coding, documents or writing.</li>
<li><b>Open the model.</b> Its page has the file to download and the settings it was measured with, ready to copy into llama.cpp,
LM Studio or Ollama, on Linux, Windows or macOS.</li></ol>
<dl class="gl">
<dt>tok/s</dt><dd>Tokens per second, how fast the answer appears. A token is about &frac34; of a word. 20 reads comfortably; a coding agent feels quick from about 50.</dd>
<dt>Context</dt><dd>How much text the model keeps in view at once: the chat, your files, a document. 256k tokens is roughly a 500-page book.
A bigger context needs more memory, and answers get slower as it fills up (the &ldquo;long&rdquo; speed).</dd>
<dt>Quant (Q4_K_M, UD-Q4_K_XL, IQ3_XXS)</dt><dd>The model&rsquo;s numbers stored in fewer bits so it fits in memory. 4-bit (Q4) is the usual choice:
about a quarter of the original size for a small loss. Q3 and Q2 fit smaller boxes and lose more; Q6 and Q8 lose almost nothing.</dd>
<dt>MoE, &ldquo;35B-A3B&rdquo;</dt><dd>Mixture of experts: 35 billion parameters in total, but only 3 billion work on each token. It needs memory for all of them
and runs about as fast as a 3B model, which is why it still runs well when part of it sits in ordinary RAM. A dense model uses all of its parameters for every token.</dd>
<dt>VRAM and RAM</dt><dd>The graphics card&rsquo;s memory is fast; what does not fit there runs from system RAM, several times slower.
On a Mac both are the same unified memory.</dd>
<dt>MTP</dt><dd>Multi-token prediction: the model drafts the next few tokens at once and checks them, so it writes faster with the same answers.</dd>
<dt>Thinking</dt><dd>The model reasons before it answers: better answers, more waiting.</dd>
<dt>% of frontier</dt><dd>The score relative to Claude Opus 5.5, a leading cloud model, on the same tasks and graders (Opus = 100%).</dd>
</dl></div></details>"""


_HOME_CSS = """
.q1{font:600 34px/1.1 "IBM Plex Sans Condensed";margin:26px 0 14px;letter-spacing:.01em}
.lede{font-size:14px;line-height:1.65;color:var(--soft);max-width:92ch;margin:-4px 0 10px}
.newbie{border:1px solid var(--line2);margin:0 0 14px;font-size:13px}.newbie summary{cursor:pointer;padding:9px 14px;color:var(--amber);list-style:none}
.newbie summary::-webkit-details-marker{display:none}.newbie summary::before{content:"+ ";color:var(--muted)}.newbie[open] summary::before{content:"− "}
.newbie .nb{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);gap:10px 34px;padding:4px 18px 16px}
.newbie .steps{padding-left:18px;line-height:1.6;color:var(--soft)}.newbie .steps li{margin:0 0 8px}.newbie b{color:var(--ink);font-weight:500}
.newbie .gl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:7px 14px;line-height:1.55;margin:0}
.newbie dt{color:var(--amber);font-size:12px;padding-top:1px;max-width:150px}.newbie dd{margin:0;color:var(--soft)}
@media (max-width:760px){.newbie .nb{grid-template-columns:1fr}.newbie .gl{grid-template-columns:1fr}.newbie dd{margin-bottom:6px}}
.boxbar .bl{font-size:12px;color:var(--muted);margin-left:6px}
.side{display:flex;flex-direction:column;gap:22px;min-width:0}
.hero{padding:22px 22px 14px;margin:0 0 22px}.hero .ch2{font:600 22px "IBM Plex Sans Condensed";margin:0 0 12px;color:var(--ink)}
.hero .cap{font-size:12.5px;line-height:1.6;color:var(--muted);max-width:110ch;margin:8px 4px 2px}
#scatter{position:relative}.scatter2{width:100%;height:auto;display:block}
.scatter2 .gl{stroke:#1d1e19}.scatter2 .axl{stroke:#3a3b33}.scatter2 .ax{fill:#6c695f;font-size:12px}.scatter2 .axt{fill:#8b877b;font-size:12px}
.scatter2 .ref{stroke:#56544b;stroke-dasharray:6 5}.scatter2 .refl{fill:#8b877b;font-size:12px}.scatter2 .refv{fill:#c9c4b5}
.scatter2 .lb{fill:#7d7a70;font-size:12.5px;cursor:pointer}.scatter2 .lb.on{fill:#ece7da}.scatter2 .pt{cursor:pointer}
#scatter.hov .pt,#scatter.hov .lb{opacity:.18}#scatter.hov .on2{opacity:1!important}
.clg{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12.5px;color:var(--soft);margin:0 0 6px 4px}.clg span{display:inline-flex;align-items:center;gap:6px}
.clg i{width:10px;height:10px;border-radius:50%;display:inline-block}.clg .k{color:var(--muted)}
.ctip{position:absolute;z-index:5;width:270px;background:#15160f;border:1px solid var(--amber-dim);padding:10px 12px;font-size:12px;line-height:1.55;pointer-events:none}
.ctip b{display:block;color:var(--ink);font-weight:500;margin-bottom:2px}.ctip span{display:block;color:var(--muted)}.ctip em{font-style:normal;color:var(--amber)}

.news .nt{display:inline-block;font-size:10px;letter-spacing:.08em;padding:1px 5px;margin-right:4px;border:1px solid var(--amber-dim);color:var(--amber)}
.news .nt.repo_update,.news .nt.runtime,.news .nt.pr{border-color:var(--line);color:var(--muted)}
.news .nd{display:block;font-size:12px;color:var(--faint);margin-top:3px;overflow-wrap:anywhere}.news .nr{font-size:12px;margin-left:8px}
.news .nf{font-size:11.5px;padding:0 18px 14px;margin:0}
.boxbar{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:12px 16px;border:1px solid var(--line);background:var(--panel)}
.boxbar .sc{margin-right:4px}
.boxbar select,.boxbar input{background:#0b0c09;color:var(--ink);border:1px solid var(--line);padding:6px 8px;font:13px "IBM Plex Mono"}
.boxbar select:focus,.boxbar input:focus{border-color:var(--amber);outline:none}.boxbar select:disabled,.boxbar input:disabled{opacity:.35}
.boxbar #boxnote{margin-left:auto}
.rhead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;padding:18px 16px 6px}
.seg{display:flex;flex-wrap:wrap;align-items:center;gap:4px}.seg .sc{margin-right:8px}
.seg button{background:none;border:1px solid transparent;color:var(--muted);font:12px "IBM Plex Mono";padding:5px 10px;cursor:pointer;white-space:nowrap}
.seg button:hover{color:var(--ink)}.seg button.on{color:var(--amber);border-color:var(--amber-dim)}
.cmp{display:flex;align-items:center;gap:12px}.cmp .btn[aria-disabled=true]{opacity:.4;pointer-events:none}
.rnote{padding:8px 16px 0;font-size:12px;color:var(--faint)}
.rank th[data-sort]{cursor:pointer;user-select:none}.rank th[data-sort]:hover,.rank th[data-sort].on{color:var(--amber)}.rank th[data-sort].on .tip{color:var(--amber)}
.rank td.mod{min-width:190px}.rank td.so{font-size:12.5px;line-height:1.55;min-width:170px}.rank .fp .trk s{top:-13px;bottom:-13px}
.pfl u{position:relative;display:inline-block;top:1px;height:10px;margin:0 3px}
.rank th,.rank td{padding:12px 10px}.rank th{vertical-align:bottom}.rank .r{text-align:right}
.rank td.rk{color:var(--muted);width:30px;font-size:13px;white-space:nowrap;cursor:help}
.rank tr.mr{cursor:pointer}.rank tr.mr:hover td,.rank tr.open td{background:rgba(255,255,255,.022)}
.rank td.sco,.rank th.sch{width:31%;min-width:230px}
.sch .fp{align-items:flex-end}.sch .num{font:inherit;color:inherit;width:auto;white-space:nowrap}
.rank td.spd b{display:block;font:500 17px "IBM Plex Mono";color:var(--ink)}.rank td.spd b.pred{color:var(--soft)}
.rank td.spd small{display:block;font-size:11px;color:var(--faint)}
.rank td.fit{font-size:12.5px;color:var(--soft);white-space:nowrap}.rank td.fit .no{color:var(--red)}
.rank td.act{white-space:nowrap;width:60px}
.exp{background:none;border:0;color:var(--muted);font-size:13px;padding:2px 6px;cursor:pointer;transition:transform .15s}.rank tr.open .exp{transform:rotate(180deg);color:var(--amber)}
.rank tr.gs td{border-top:1px dashed rgba(255,176,0,.6)}
.rank tr.cloud td{padding-top:7px;padding-bottom:7px;color:var(--muted)}.rank tr.cloud .m{font-size:14px;color:var(--muted);font-weight:500}.rank tr.cloud .num{color:var(--muted);font-size:14px}
.rank tr.cloud td.rk{font-size:13px;cursor:default}
.rank tr.nofit td{opacity:.42}
.rank tr.prof>td{padding:4px 10px 20px 50px;text-align:left;background:rgba(255,255,255,.022)}
.pfl a{display:block;margin-top:4px}
.below{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px;align-items:start}
.pf{padding:4px 0 0}.pfl{display:flex;flex-wrap:wrap;gap:6px 22px;align-items:baseline;margin-top:10px;font-size:12.5px;color:var(--soft)}
.optp .nf0{padding:14px 18px 0;margin:0;font-size:12px}.optp .nf{padding:0 18px 14px;margin:0}.optp li{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px}.optp .fv{margin-left:0}.optp em{font-style:normal;color:var(--amber);margin-left:auto}
@media (max-width:1100px){.below{grid-template-columns:1fr 1fr}}
@media (max-width:760px){
 .hero{padding:16px 10px 10px}.below{grid-template-columns:1fr}.cmp{width:100%;justify-content:space-between}
 .rank thead{display:none}.rank,.rank tbody{display:block}
 .rank tr.mr{display:grid;grid-template-columns:24px max-content minmax(0,1fr) auto;grid-template-areas:"rk mod mod act" "rk sco sco sco" "rk spd fit so";column-gap:12px;border-bottom:1px solid var(--line2);padding:10px 0}
 .rank tr.mr td{display:block;border:0;padding:2px 0;min-width:0;width:auto;background:none!important}
 .rank td.rk{grid-area:rk;padding-top:4px}.rank td.mod{grid-area:mod}.rank td.act{grid-area:act;text-align:right}.rank td.sco{grid-area:sco;padding:8px 0 6px}
 .rank td.spd{grid-area:spd;text-align:left}.rank td.fit{grid-area:fit;text-align:left;padding-top:4px}.rank td.so{grid-area:so;font-size:12px}
 .rank td.spd b{font-size:15px}.fp .num{font-size:15px}.fp .trk s{top:0;bottom:0}.rank td.spd b::after{content:" tok/s";font:400 11px "IBM Plex Mono";color:var(--faint)}
 .rank tr.gs{border-top:1px dashed rgba(255,176,0,.6)}.rank tr.gs td{border-top:0}
 .rank tr.cloud{display:grid;grid-template-columns:24px minmax(0,1fr) minmax(0,1.3fr);grid-template-areas:"rk mod sco";column-gap:12px;align-items:center;border-bottom:1px solid var(--line2);padding:4px 0}
 .rank tr.cloud td{display:block;border:0;padding:2px 0;min-width:0;width:auto}.rank tr.cloud td.spd,.rank tr.cloud td.fit,.rank tr.cloud td.so,.rank tr.cloud td.act{display:none}
 .rank tr.cloud .qt{display:none}
 .rank tr.prof{display:block}.rank tr.prof[hidden]{display:none}.rank tr.prof>td{display:block;padding:6px 0 16px 36px;border:0}
}
.pick2 input{accent-color:#FFB000;width:15px;height:15px;cursor:pointer}
.queue{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;padding:12px 16px;border-top:1px solid var(--line2);font-size:13px}
.queue b{font-weight:500}.queue .nx{margin-left:auto}
.live{color:#ffd27a;animation:blink 1.4s steps(2) infinite}@keyframes blink{50%{opacity:.45}}
@media (prefers-reduced-motion:reduce){.live{animation:none}}
.prog{width:120px;height:4px;background:var(--line);display:inline-block}.prog i{display:block;height:100%;background:var(--amber)}
.chart{padding:18px 16px 10px}.scatter{width:100%;height:auto;display:block}
.sc2 .ci{stroke-opacity:.35}#scatter.hov .pt{opacity:.25}#scatter.hov .pt.on{opacity:1}#scatter .pt.on .ci{stroke-opacity:1}
.lgd2{list-style:none;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 18px;margin:8px 8px 0;font-size:13px}
.lgd2 li{display:grid;grid-template-columns:22px minmax(0,1fr) auto auto;gap:8px;align-items:center;padding:3px 4px;cursor:default}
.lgd2 li.on{background:rgba(255,176,0,.08)}.lgd2 b{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;background:var(--amber);color:var(--bg);font-size:11px}
.lgd2 .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lgd2 .lv{color:var(--amber);font-size:12px}
@media (max-width:900px){.lgd2{grid-template-columns:1fr}}
.legend{display:flex;gap:10px;align-items:flex-start;font-size:12px;color:var(--muted);margin:6px 8px 4px;line-height:1.5}.legend svg{flex:none;margin-top:2px}
.feed ul{list-style:none;padding:14px 18px}.feed li{font-size:13px;padding:8px 0;border-bottom:1px solid var(--line2)}.feed li:last-child{border-bottom:0}
.feed .fv{color:var(--soft);margin-left:6px}.feed .when{display:block;font-size:11px;color:var(--faint)}
@media (max-width:900px){
 .q1{font-size:26px}.boxbar #boxnote{margin-left:0;width:100%}.boxbar select{max-width:100%;min-width:0}.boxbar #gpu{width:100%}
}
"""

_JS = r"""
const $ = s => document.querySelector(s);
const fmt = v => v.toFixed(0);
const kfmt = c => `${Math.round(c / 1024)}k`;
function sameClass(hw) { const r = DATA.ref; return hw.gpu === r.gpu && Math.abs(hw.rambw - r.rambw) / r.rambw < 0.15 && hw.ram >= r.ram * 0.9; }
function weighted(b, w) { let s = 0, n = 0; for (const k in w) { s += (b[k] || 0) * w[k]; n += w[k]; } return n ? s / n : 0; }
function scatter(pts) {   // up = closer to Claude Opus, right = faster on the box picked. Names sit at their points.
  // a phone gets its own proportions (narrower and taller), not the desktop chart shrunk until its names are unreadable
  const narrow = (document.querySelector("#scatter") || {}).clientWidth < 700;
  const W = narrow ? 460 : 1000, H = narrow ? 600 : 540, L = narrow ? 44 : 58, R = narrow ? 346 : 812, T = 26, B = narrow ? 536 : 478;   // the right margin names the Claude lines
  const loc = pts.filter(p => !p.cloud && p.t2 && p.vs != null), cloud = pts.filter(p => p.cloud && p.vs != null).sort((a, b) => b.vs - a.vs);
  if (!loc.length) return "";
  const fams = Object.fromEntries(DATA.families), col = p => fams[p.fam] || "#9AA0A6";
  // y: just under the weakest model at >= 50% of the frontier; weaker ones sit on the floor with an arrow
  const main = loc.filter(p => p.vs >= 50), lowest = Math.min(...(main.length ? main : loc).map(p => p.vs));
  const ymin = Math.max(0, Math.floor((lowest - 6) / 5) * 5), ystep = 100 - ymin > 40 ? 10 : 5;
  const xs = loc.map(p => p.t2), span = Math.max(10, Math.max(...xs) - Math.min(...xs));
  const xstep = span > 150 ? 50 : span > 60 ? 20 : span > 25 ? 10 : 5;
  const xmin = Math.max(0, Math.floor((Math.min(...xs) - span * 0.12) / xstep) * xstep), xmax = Math.ceil((Math.max(...xs) + span * 0.12) / xstep) * xstep;
  const X = v => L + (v - xmin) / (xmax - xmin) * (R - L), Y = v => B - (Math.max(v, ymin) - ymin) / (100 - ymin) * (B - T);
  const front = new Set(loc.filter(p => !loc.some(q => q !== p && q.t2 >= p.t2 && q.vs >= p.vs && (q.t2 > p.t2 || q.vs > p.vs))).map(p => p.id));
  let g = "";
  for (let v = ymin; v <= 100; v += ystep) g += `<line x1="${L}" y1="${Y(v)}" x2="${R}" y2="${Y(v)}" class="gl"/><text x="${L - 10}" y="${Y(v) + 4}" text-anchor="end" class="ax">${v}%</text>`;
  for (let v = xmin; v <= xmax; v += xstep) g += `<text x="${X(v)}" y="${B + 22}" text-anchor="middle" class="ax">${v}</text>`;
  g += `<line x1="${L}" y1="${B}" x2="${R}" y2="${B}" class="axl"/>`;
  // the cloud models: reference lines, named in the right margin (labels pushed apart when close)
  let lastY = -99;
  cloud.forEach(c => { const y = Y(c.vs), ly = Math.max(y + 4, lastY + 15); lastY = ly;
    g += `<line x1="${L}" y1="${y}" x2="${R}" y2="${y}" class="ref"/><text x="${R + 8}" y="${ly}" class="refl">${narrow ? c.name.replace("Claude ", "") : c.name} <tspan class="refv">${Math.round(c.vs)}%</tspan></text>`; });
  // one model in several variants (quants): a line through them, slowest to fastest
  const series = {};
  loc.forEach(p => (series[p.model] = series[p.model] || []).push(p));
  Object.values(series).filter(s => s.length > 1).forEach(s => { s.sort((a, b) => a.t2 - b.t2);
    g += `<polyline points="${s.map(p => `${X(p.t2)},${Y(p.vs)}`).join(" ")}" fill="none" stroke="${col(s[0])}" stroke-width="2" stroke-opacity=".55"/>`; });
  // markers: filled = the maker's own release, ring = a fine-tune, diamond = an uncensored remix; faded = another model is smarter and faster
  const boxes = [];
  const order = loc.slice().sort((a, b) => (front.has(b.id) - front.has(a.id)) || b.vs - a.vs);
  order.slice().reverse().forEach(p => { const x = X(p.t2), y = Y(p.vs), c = col(p), op = front.has(p.id) ? 1 : .42, low = p.vs < ymin;
    const mk = p.kind === "uncensored" ? `<path d="M${x} ${y - 7}L${x + 7} ${y}L${x} ${y + 7}L${x - 7} ${y}Z" fill="#0E0F0C" stroke="${c}" stroke-width="2.2"/>`
      : `<circle cx="${x}" cy="${y}" r="6.5" fill="${p.kind === "fine-tune" ? "#0E0F0C" : c}" stroke="${c}" stroke-width="2.2"${p.pred ? ' stroke-dasharray="3 2"' : ""}/>`;
    g += `<g class="pt" data-id="${p.id}" opacity="${op}">${mk}${low ? `<path d="M${x - 4} ${y + 11}L${x + 4} ${y + 11}L${x} ${y + 17}Z" fill="${c}"/>` : ""}` +
         `<circle cx="${x}" cy="${y}" r="16" fill="transparent"/></g>`;
    boxes.push([x - 8, y - 8, x + 8, y + 8]); });
  // names next to their points: the first free spot of eight around it, the models on the frontier first
  const hit = (b) => b[0] < L + 2 || b[2] > R + 2 || b[1] < T - 14 || b[3] > B + 2 || boxes.some(o => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]);
  const sq = q => (q || "").replace(/^UD-/, "");
  const labelOf = p => { const s = series[p.model]; if (!s || s.length < 2) return p.model;
    return s.reduce((a, b) => (b.vs > a.vs ? b : a)) === p ? `${p.model} · ${sq(p.quant)}` : sq(p.quant); };
  // the series lines are obstacles for names too: sample points along them
  Object.values(series).filter(s => s.length > 1).forEach(s => { for (let i = 1; i < s.length; i++) {
    const [a, b] = [s[i - 1], s[i]]; for (let t = 0.15; t < 0.9; t += 0.1) { const x = X(a.t2 + (b.t2 - a.t2) * t), y = Y(a.vs + (b.vs - a.vs) * t); boxes.push([x - 3, y - 3, x + 3, y + 3]); } } });
  order.forEach(p => { const x = X(p.t2), y = Y(p.vs), txt = labelOf(p) + (p.vs < ymin ? ` ${Math.round(p.vs)}%` : ""), w = txt.length * 7.7 + 4;   // IBM Plex Mono at 12.5: ~7.5 units a character
    const spots = [[x + 11, y + 4, "start"], [x - 11, y + 4, "end"], [x, y - 13, "middle"], [x, y + 21, "middle"],
                   [x + 10, y - 9, "start"], [x + 10, y + 17, "start"], [x - 10, y - 9, "end"], [x - 10, y + 17, "end"]];
    for (const [tx, ty, an] of spots) {
      const x0 = an === "start" ? tx : an === "end" ? tx - w : tx - w / 2, b = [x0, ty - 11, x0 + w, ty + 3];
      if (!hit(b)) { boxes.push(b); g += `<text x="${tx}" y="${ty}" text-anchor="${an}" class="lb${front.has(p.id) ? " on" : ""}" data-id="${p.id}">${txt}</text>`; return; }
    }
  });
  const famsUsed = [...new Set(loc.map(p => p.fam))];
  const legend = `<div class="clg">${famsUsed.map(f => `<span><i style="background:${fams[f]}"></i>${f}</span>`).join("")}` +
    `<span class="k"><svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="#8b877b"/></svg>release</span>` +
    `<span class="k"><svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="none" stroke="#8b877b" stroke-width="2"/></svg>fine-tune</span>` +
    `<span class="k"><svg width="14" height="14"><path d="M7 1L13 7L7 13L1 7Z" fill="none" stroke="#8b877b" stroke-width="2"/></svg>uncensored</span>` +
    `<span class="k"><svg width="22" height="10"><line x1="0" y1="5" x2="22" y2="5" stroke="#8b877b" stroke-width="2"/></svg>same model, other quant</span></div>`;
  return legend + `<svg viewBox="0 0 ${W} ${H}" class="scatter2" font-family="IBM Plex Mono" role="img" aria-label="score against speed">${g}` +
    `<text x="${(L + R) / 2}" y="${H - 8}" text-anchor="middle" class="axt">tokens per second on your box →</text>` +
    `<text transform="translate(16 ${(T + B) / 2}) rotate(-90)" text-anchor="middle" class="axt">↑ share of Claude Opus 5.5's score</text></svg><div class="ctip" hidden></div>`;
}
function hoverScatter(pts) {   // a model's point or name: its details in a tip, the others step back
  const box = document.querySelector("#scatter"), tip = box.querySelector(".ctip"), by = Object.fromEntries((pts || []).map(p => [p.id, p]));
  if (!tip) return;
  box.querySelectorAll(".pt, .lb").forEach(el => {
    el.addEventListener("mouseenter", () => { const p = by[el.dataset.id]; if (!p) return;
      box.classList.add("hov"); box.querySelectorAll(`[data-id="${p.id}"]`).forEach(x => x.classList.add("on2"));
      const k = p.vs / p.cap, lo = Math.round(p.ci[0] * k), hi = Math.min(100, Math.round(p.ci[1] * k));
      tip.innerHTML = `<b>${p.name}</b><span>${p.fam} · ${p.kind}</span><span>score <em>${Math.round(p.vs)}%</em> of Claude Opus (95%: ${lo}-${hi})</span>` +
        `<span>speed <em>${p.pred ? "~" : ""}${Math.round(p.t2)} tok/s</em>${p.pred ? " predicted for your box" : " measured"}${p.td ? ` · ${Math.round(p.td)} deep in context` : ""}</span>`;
      const r = box.getBoundingClientRect(), e = el.getBoundingClientRect();
      tip.hidden = false; tip.style.left = Math.min(r.width - 280, Math.max(0, e.left - r.left + 18)) + "px"; tip.style.top = (e.top - r.top - 8) + "px"; });
    el.addEventListener("mouseleave", () => { box.classList.remove("hov"); box.querySelectorAll(".on2").forEach(x => x.classList.remove("on2")); tip.hidden = true; });
    el.addEventListener("click", () => { location.href = `recipe-${el.dataset.id}.html`; });
  });
}
let resizeT = null, lastNarrow = null;   // the chart has phone and desktop proportions: redraw when the width crosses over
addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { const n = (document.querySelector("#scatter") || {}).clientWidth < 700;
  if (n !== lastNarrow) { lastNarrow = n; render(); } }, 200); });
let hwNow = null, preset = 0, sortBy = "score", sortDir = 1;   // any column with data-sort; a second click reverses it
document.querySelectorAll(".rank th[data-sort]").forEach(th => th.addEventListener("click", () => {
  sortDir = sortBy === th.dataset.sort ? -sortDir : 1; sortBy = th.dataset.sort;
  document.querySelectorAll(".rank th[data-sort]").forEach(t => t.classList.toggle("on", t === th)); render(); }));
const measured = {};
document.querySelectorAll("tr[data-rid]").forEach(r => measured[r.dataset.rid] = r.querySelector(".spd").innerHTML);
function axisOf(pts) {   // the score column's scale: as the chart's, from just under the weakest model at >= 50% to 100%
  const loc = pts.filter(p => !p.cloud && p.vs != null), main = loc.filter(p => p.vs >= 50);
  if (!loc.length) return null;
  const min = Math.max(0, Math.floor((Math.min(...(main.length ? main : loc).map(p => p.vs)) - 6) / 5) * 5);
  return { min, step: 100 - min > 40 ? 10 : 5, X: v => Math.max(0, Math.min(100, (v - min) / (100 - min) * 100)) };
}
function scoreCell(p, ax) {   // a dot at the score, a line over its 95% range; a Claude model: a dashed mark, its score being a reference
  if (p.vs == null || !ax) return "—";   // no reference model on this scale: no percent
  const c = p.cloud ? "#8b877b" : (Object.fromEntries(DATA.families)[p.fam] || "#9AA0A6");
  let g = ""; for (let v = ax.min; v <= 100; v += ax.step) g += `<s style="left:${ax.X(v)}%"></s>`;
  if (p.cloud) return `<div class="fp"><div class="trk">${g}<b class="rf" style="left:${ax.X(p.vs)}%"></b></div><span class="num">${Math.round(p.vs)}%</span></div>`;
  const k = p.vs / p.cap, lo = p.ci[0] * k, hi = Math.min(100, p.ci[1] * k);
  return `<div class="fp" title="95% range ${Math.round(lo)}–${Math.round(hi)}%"><div class="trk">${g}<i style="left:${ax.X(lo)}%;width:${ax.X(hi) - ax.X(lo)}%;background:${c}"></i>` +
    `<b style="left:${ax.X(p.vs)}%;background:${c}"></b>${p.vs < ax.min ? `<em>◂ ${Math.round(p.vs)}%</em>` : ""}</div><span class="num">${Math.round(p.vs)}%</span></div>`;
}
function render() {
  const w = DATA.presets[preset], refW = weighted(DATA.refBlocks, w);
  const pts = DATA.points.map(p => Object.assign({}, p, preset ? { vs: 100 * weighted(p.blocks, w) / refW, cap: weighted(p.blocks, w), ci: [p.ci[0] / p.cap * weighted(p.blocks, w), p.ci[1] / p.cap * weighted(p.blocks, w)] } : {}));
  const ax = axisOf(pts);
  let lab = ""; if (ax) for (let v = ax.min; v <= 100; v += ax.step) lab += `<span style="left:${ax.X(v)}%">${v}</span>`;
  $(".rank .axis").innerHTML = lab;
  for (const p of pts) {
    const sh = DATA.recipes[p.id], row = document.querySelector(`tr[data-rid="${p.id}"]`);
    if (!row) continue;
    row.querySelector(".sco").innerHTML = scoreCell(p, ax);
    row.classList.remove("nofit");
    if (p.cloud) continue;   // no box to predict for
    if (sh && hwNow && !sameClass(hwNow)) {
      const f = forBox(sh, hwNow); p.t2 = f.t2; p.td = f.td; p.pred = true;
      row.querySelector(".spd").innerHTML = f.fits ? `<b class="pred">~${fmt(f.t2)}</b><small>~${fmt(f.td)} long</small>` : "—";
      row.querySelector(".fit").innerHTML = f.fits ? `✓ ${kfmt(f.ctx)}` : `<span class="no">✗ too big</span>`;
      if (!f.fits) { row.classList.add("nofit"); p.t2 = null; }
    } else {
      row.querySelector(".spd").innerHTML = measured[p.id];
      row.querySelector(".fit").innerHTML = sh ? `✓ ${kfmt(sh.ctx)}` : "—";
    }
  }
  const tb = $(".rank tbody");
  const key = sortBy === "speed" ? p => p.t2 || 0 : p => p.vs ?? -1;
  const by = (a, b) => sortDir * (key(b) - key(a)) || (b.vs ?? -1) - (a.vs ?? -1);
  const ranked = pts.filter(p => !p.cloud).sort((a, b) => (b.vs ?? -1) - (a.vs ?? -1)).map(p => p.id);
  const byScore = !preset && sortBy === "score" && sortDir === 1;   // group lines only make sense in the score order they were cut in
  let prevG = null;
  pts.slice().sort(by).forEach((p) => { const i = ranked.indexOf(p.id); const row = document.querySelector(`tr[data-rid="${p.id}"]`);
    if (p.cloud) { tb.appendChild(row); return; }
    row.querySelector(".rk").textContent = preset ? i + 1 : p.rank[0];
    row.classList.toggle("gs", byScore && prevG !== null && p.rank[3] !== prevG); prevG = p.rank[3];
    tb.appendChild(row); tb.appendChild(document.querySelector(`tr.prof[data-for="${p.id}"]`)); });
  $("#scatter").innerHTML = scatter(pts);
  hoverScatter(pts);
}
document.querySelectorAll(".rank tr.mr").forEach(tr => tr.addEventListener("click", e => {   // a row opens its nine block scores
  if (e.target.closest("a, label, input")) return;
  const pr = document.querySelector(`tr.prof[data-for="${tr.dataset.rid}"]`), open = pr.hidden;
  pr.hidden = !open; tr.classList.toggle("open", open); tr.querySelector(".exp").setAttribute("aria-expanded", String(open)); }));
function readBox() {
  const g = DATA.gpus.find(x => x[0] === $("#gpu").value);
  ["#ram", "#bw", "#bwn"].forEach(s => $(s).disabled = !g);
  if (!g) { hwNow = null; $("#boxnote").textContent = "speeds measured on this box"; try { localStorage.removeItem("llmbox-box"); } catch (e) {} history.replaceState(null, "", location.pathname); render(); return; }
  const bw = parseFloat($("#bwn").value) || parseFloat($("#bw").value);
  hwNow = boxFrom(g, parseInt($("#ram").value), bw);
  ["#bw", "#bwn"].forEach(s => $(s).disabled = !!hwNow.mac);   // a Mac's memory speed comes with the chip
  $("#boxnote").textContent = hwNow.mac ? "Mac: rough, for MLX-class engines (llama.cpp on Metal is often slower) - nothing here is measured on a Mac" :
    sameClass(hwNow) ? "same class as the reference box: measured speeds" : "speeds predicted for this box (~)";
  try { localStorage.setItem("llmbox-box", JSON.stringify({ gpu: $("#gpu").value, ram: $("#ram").value, bw: $("#bw").value, bwn: $("#bwn").value })); } catch (e) {}
  history.replaceState(null, "", `#gpu=${encodeURIComponent(g[0])}&ram=${$("#ram").value}&bw=${bw}`);
  render();
}
$("#gpu").insertAdjacentHTML("beforeend", `<optgroup label="NVIDIA + system RAM">${DATA.gpus.filter(g => g[3] !== "mac").map(g => `<option>${g[0]}</option>`).join("")}</optgroup>`
  + `<optgroup label="Mac (unified memory)">${DATA.gpus.filter(g => g[3] === "mac").map(g => `<option>${g[0]}</option>`).join("")}</optgroup>`);
for (const r of DATA.ramKinds) $("#bw").insertAdjacentHTML("beforeend", `<option value="${r[1]}">${r[0]} · ${r[1]} GB/s</option>`);
$("#bw").value = String(DATA.ramKinds.reduce((a, r) => Math.abs(r[1] - DATA.ref.rambw) < Math.abs(a - DATA.ref.rambw) ? r[1] : a, DATA.ramKinds[0][1]));
["#gpu", "#ram", "#bwn"].forEach(s => $(s).addEventListener("change", readBox));
$("#bw").addEventListener("change", () => { $("#bwn").value = ""; readBox(); });   // a preset replaces a typed-in measurement
document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".seg button").forEach(u => u.classList.remove("on")); b.classList.add("on"); preset = +b.dataset.p; render(); }));
const order = DATA.points.slice().sort((a, b) => b.cap - a.cap || (a.id < b.id ? -1 : 1)).map(p => p.id);   // the better model of a ticked pair goes first
document.querySelectorAll(".pick2 input").forEach(c => c.addEventListener("change", () => {
  const on = [...document.querySelectorAll(".pick2 input:checked")];
  if (on.length > 2) { on.filter(x => x !== c)[0].checked = false; }
  const ids = [...document.querySelectorAll(".pick2 input:checked")].map(x => x.value).sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const go = $("#cmpgo");
  if (ids.length === 2) { go.href = `compare.html#${ids[0]}-vs-${ids[1]}`; go.setAttribute("aria-disabled", "false"); go.classList.add("solid"); $("#cmpn").textContent = `${ids[0]} vs ${ids[1]}`; }
  else { go.removeAttribute("href"); go.setAttribute("aria-disabled", "true"); go.classList.remove("solid"); $("#cmpn").textContent = ids.length ? `${ids[0]} vs …` : "tick two models to compare"; }
}));
try { const h = Object.fromEntries(new URLSearchParams(location.hash.slice(1))); const saved = h.gpu ? h : JSON.parse(localStorage.getItem("llmbox-box") || "null");
  if (saved && saved.gpu) { $("#gpu").value = saved.gpu; if (saved.ram) $("#ram").value = saved.ram; if (saved.bw) { const o = [...$("#bw").options].find(o => o.value == saved.bw); if (o) $("#bw").value = saved.bw; else $("#bwn").value = saved.bw; } if (saved.bwn) $("#bwn").value = saved.bwn; readBox(); } else { ["#ram", "#bw", "#bwn"].forEach(s => $(s).disabled = true); render(); }
} catch (e) { render(); }
"""


# ---------------------------------------------------------------------------------------------------------------------
# every page: records -> HTML. Flat folder, simple relative links: index, recipe-<id>, run-<id8>, hardware-<id>, compare-<a>-vs-<b>

PLAN_JS = r"""
function plan(sh, hw, ctx, depth, buf = 2100) {   // buf: compute buffer MiB for -ub 2048 / 1024 / 512 = 2100 / 1300 / 900
  const mib = 1 / 1048576, kv = sh.kvB * ctx + sh.rec, gpuFixed = (sh.nonexp + kv) * mib + buf + 700, free = hw.vram - gpuFixed;
  let gf, ramUsed, fits, perCpu, perGpu;
  if (hw.mac) { const fr = sh.moe ? sh.nUsed / sh.nExp : 1; fits = gpuFixed + ((sh.moe ? sh.exp : 0) + sh.embed) * mib <= hw.vram; gf = 1;
                ramUsed = 0; perCpu = 0; perGpu = sh.nonexp + (sh.moe ? sh.exp * fr : 0); }   // unified memory: all of it on the GPU
  else if (!sh.moe) { const need = gpuFixed + sh.embed * mib; fits = need <= hw.vram; gf = 1; ramUsed = sh.embed * mib; perGpu = sh.nonexp; perCpu = 0; }
  else { gf = Math.max(0, Math.min(1, free / (sh.exp * mib))); const cpuExp = sh.exp * (1 - gf); ramUsed = (cpuExp + sh.embed) * mib;
         fits = free > -1 && ramUsed + 4096 <= hw.ram; const fr = sh.nUsed / sh.nExp; perCpu = cpuExp * fr; perGpu = sh.nonexp + sh.exp * gf * fr; }
  // Metal: ~80% of the memory bandwidth, ~0.1 ms per layer per token (fitted to community M4 Pro / M5 Max runs of 35B-A3B MoE
  // models, llm-bench.io 2026-09: 70-86 and 113-141 tok/s); CUDA: 75% and 0.025 ms (the reference box)
  const eff = hw.mac ? 0.8 : 0.75, ovh = hw.mac ? 0.1 : 0.025;
  const tps = d => 1 / (perCpu / (hw.rambw * 1e9 * 0.8 * sh.cpuEff) + (perGpu + sh.kvB * d) / (hw.vrambw * 1e9 * eff) + sh.layers * ovh / 1000);
  return { fits, gf, ramUsed, vram: Math.min(hw.vram, gpuFixed + (sh.moe ? sh.exp * gf * mib : sh.embed * mib)), t2: tps(2000), td: tps(Math.min(depth, ctx)) };
}
function sameClassAs(hw, r) { return hw.gpu === r.gpu && Math.abs(hw.rambw - r.rambw) / r.rambw < 0.15 && hw.ram >= r.ram * 0.9; }
function forBox(sh, hw) {   // as llmbox fit: the recipe's context if it fits, else halve it; a smaller prompt batch before a smaller context
  let p = null, ctx = sh.ctx;
  for (let c = sh.ctx; c >= 8192 && !(p && p.fits); c = c / 2)
    for (const buf of [2100, 1300, 900]) { p = plan(sh, hw, c, sh.deepK * 1000, buf); ctx = c; if (p.fits) break; }
  // the reference box's measured/predicted ratio is about that box (experts streamed over PCIe, its MTP gain): not a Mac's
  return Object.assign(p, { ctx, t2: p.t2 * (hw.mac ? 1 : sh.k2), td: p.td * (hw.mac ? 1 : sh.kd) });
}
function boxFrom(g, ramGB, rambw) {   // a picker entry and the RAM fields -> what plan() needs
  if (g[3] === "mac") { const mem = Math.min(ramGB, g[4]) * 1024;   // macOS lets the GPU use ~2/3 (small Macs) to 3/4 of unified memory
    return { name: g[0], gpu: g[0], mac: true, mem, vram: mem * (mem >= 36864 ? 0.75 : 0.67), vrambw: g[2], ram: 0, rambw: g[2] }; }
  return { name: g[0], gpu: g[0].replace(/ \d+ GB$/, ""), vram: g[1], vrambw: g[2], ram: ramGB * 1024, rambw };
}
function boxLabel(b) { return b.mac ? `${b.name} · ${Math.round(b.mem / 1024)} GB unified · ${b.rambw} GB/s` : `${b.name} · ${Math.round(b.ram / 1024)} GB · ${b.rambw} GB/s`; }
function savedBox(DATA) {
  try { const s = JSON.parse(localStorage.getItem("llmbox-box") || "null"); if (!s || !s.gpu) return null;
    const g = DATA.gpus.find(x => x[0] === s.gpu); if (!g) return null;
    return boxFrom(g, parseInt(s.ram), parseFloat(s.bwn) || parseFloat(s.bw)); } catch (e) { return null; }
}
"""
TAB_LINKS = {"MODELS": "index.html", "NEW": "new.html", "COMPARE": "compare.html", "METHOD": "method.html"}


def _page(title: str, tab: str, body: str, css: str = "", js: str = "", links: dict | None = None) -> str:
    links = dict(TAB_LINKS, **(links or {}))
    nav = "".join(f'<a class="{"on" if t == tab else ""}" href="{esc(links.get(t) or "#")}">{t}</a>' for t in ("MODELS", "NEW", "COMPARE", "METHOD"))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><link rel="stylesheet" href="osc.css"><style>{_PAGES_CSS}{css}</style></head><body>'
            '<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="1.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>'
            '<div class="wrap"><header class="plate"><a class="brand glow" href="index.html">LLMBOX<small>LOCAL LLM BENCHMARK</small></a>'
            f'<nav class="tabs">{nav}</nav></header>{body}'
            f'<footer><span>Every number on this page comes from a saved run record.</span><span>generated {time.strftime("%b %d, %Y %H:%M")}</span></footer>'
            f'</div>{f"<script>{js}</script>" if js else ""}</body></html>')


def load_records(host: str, suite_version: str, tier: str) -> dict:
    """Newest suite record per recipe id for this host and suite, plus the frontier reference. Keeps the file path."""
    import json as _json
    out, ref, runs = {}, None, {}
    for h in (host, "cloud"):
        for path, rec in report.results.files(h):
            f = os.path.basename(path)
            su = rec.get("suite") or {}
            from . import report as _report, suite as _suite
            cur = suite_version == _suite.VERSION and bool(_report.current_pool())
            if rec.get("kind") != "suite" or _report.scale(su.get("tier")) != _report.scale(tier) or su.get("blocks") or (
                    not _report.ranked_now((rec.get("recipe") or {}).get("id") or "?", h) if cur else _report.version_of(su) != suite_version):
                continue   # the current suite: any run of a model whose answers still cover every block (report.current_pool)
            from . import bench
            rec = bench.rescore(rec)   # current suite weights: the task scores are the run's, the weighting is today's
            rec["_path"] = path
            rid = (rec.get("recipe") or {}).get("id")
            runs.setdefault((h, rid), []).append(rec)
            if h == "cloud":
                if not ref or rec["summary"]["capability"] > ref["summary"]["capability"]:
                    ref = rec
            elif rid:
                out[rid] = rec   # sorted by name = by time: the newest wins
    for (h, rid), recs in runs.items():   # the number of a model = the IRT estimate over all its runs (report._pool)
        tgt = ref if h == "cloud" and ref is not None and (ref.get("recipe") or {}).get("id") == rid else out.get(rid) if h != "cloud" else None
        if tgt is not None:
            _pool_summary(tgt, recs, h, current=suite_version == _suite.VERSION)
    return {"local": out, "ref": ref}


def optimize_records(host: str) -> dict:
    """{recipe id: newest "optimize" record} - stock llama.cpp vs the tuned recipe, measured back to back (llmbox optimize)."""
    import json as _json
    out = {}
    for path, rec in report.results.files(host):
        if "-optimize-" in os.path.basename(path):
            s = rec.get("summary") or {}
            if (s.get("stock") or {}).get("decode") and (s.get("llmbox") or {}).get("decode"):
                out[(rec.get("recipe") or {}).get("id")] = rec   # sorted by name = by time: the newest wins
    return out


# flags only the reference box needs (its port manager, RAM budget, core count, load mode)
_BOX_ONLY = {"--port": 1, "--cache-ram": 1, "--threads": 1, "--load-mode": 1}
# DRY flags that come with llmbox's --dry-think-only patch: without the patch they would penalize the answer too
_DRY = {"--dry-multiplier", "--dry-base", "--dry-allowed-length", "--dry-penalty-last-n"}


def portable_args(r: dict) -> tuple[list[str], list[str]]:
    """The recipe's llama-server flags as anyone can run them: the model by file name, without what only the reference
    box or llmbox's patched build needs. Returns (args, what was left out)."""
    from . import recipe as rc
    a = rc.server_args(r)
    out, left = [], []
    i = 0
    while i < len(a):
        x = a[i]
        if x in _BOX_ONLY:
            i += 1 + _BOX_ONLY[x]
        elif x == "--dry-think-only":
            left.append("the repetition penalty inside the thinking only (a llmbox patch)")
            i += 1
        elif x in _DRY and "--dry-think-only" in a:
            i += 2
        elif x == "--reasoning-loop":
            left.append("the reasoning-loop detector (a llmbox patch)")
            i += 2
        elif x == "-m":
            out += ["-m", "@MODEL@/" + os.path.basename(a[i + 1])]   # the caller puts its models folder in
            i += 2
        else:
            out.append(x)
            i += 1
    return out, left


def _FLAG_ORDER(flag: str) -> int:
    """Where a flag goes in a shown command line (llama-server does not care about the order; a reader does)."""
    if flag in ("-m", "--model"):
        return 0
    if flag in ("-c", "--ctx-size"):
        return 1
    if flag in ("--temp", "--top-p", "--top-k", "--min-p", "--presence-penalty", "--repeat-penalty", "-n", "--n-predict"):
        return 2
    if flag in ("--jinja", "--chat-template-kwargs", "--reasoning-budget", "--reasoning-budget-message", "--reasoning-format"):
        return 3
    if flag == "--logit-bias":
        return 9
    return 5


def _cmd_lines(args: list[str], win: bool = False) -> str:
    """One flag with its value per line, quoted for the shell (Windows: cmd.exe quoting and ^ continuations)."""
    import shlex

    def q(v: str) -> str:
        if v == "${PORT}":   # llama-swap's macro, substituted before the command runs
            return v
        if v.startswith("@MODEL@/"):
            return ("C:\\models\\" if win else "~/models/") + v[len("@MODEL@/"):]
        if not win:
            return shlex.quote(v)
        return '"' + v.replace('"', '\\"') + '"' if any(c in v for c in ' "{}') else v
    parts, cur = [], []
    for x in args:
        if x.startswith("-") and not x.lstrip("-").replace(".", "").isdigit() and cur:
            parts.append(" ".join(cur))
            cur = []
        cur.append(q(x))
    if cur:
        parts.append(" ".join(cur))
    parts.sort(key=lambda x: _FLAG_ORDER(x.split(" ")[0]))   # the model, context, sampling and thinking first; tuning after; the loop biases last
    exe = "llama-server.exe" if win else "llama-server"
    return (" ^\n  " if win else " \\\n  ").join([exe] + parts)


def _model_now(host: str, rid: str) -> tuple[dict, bool | None]:
    """The recipe's model block as it is now (a repo fixed since the run) and whether that exact file is on Hugging Face
    (None: could not ask)."""
    from . import hf, recipe as rc
    try:
        m = rc.load(host, rid)["model"]
    except (OSError, ValueError):
        return {}, None
    try:
        fs = hf.list_gguf(m["hf_repo"], ttl=86400)
        names = {f.name for f in fs} | {os.path.basename(p) for f in fs for p in f.parts}
        return m, os.path.basename(m.get("file") or "") in names or m.get("file") in names
    except Exception:
        return m, None


def _run_panel(rid: str, rec: dict, model_now: dict | None = None, on_hf: bool | None = None) -> str:
    """Run it yourself: the measured settings for llama-server (Linux, macOS, Windows), llama-swap, LM Studio and Ollama,
    each with a copy button. Only settings with a real equivalent in an app are listed there; what it lacks is said."""
    r = rec.get("recipe") or {}
    m = rec.get("model") or r.get("model") or {}
    if not r.get("placement") or not m.get("file"):
        return ""
    args, left = portable_args(r)
    p, sp, smp = r["placement"], r.get("speculative") or {}, r.get("sampling") or {}
    kw = (r.get("chat") or {}).get("template_kwargs") or {}
    m = dict(m, **{k: v for k, v in (model_now or {}).items() if k in ("hf_repo",) and v})   # a repo fixed in the recipe since the run
    fname, repo = os.path.basename(m["file"]), m.get("hf_repo") or ""
    ctx = p.get("ctx") or 0
    kv = p.get("kv_type") or "f16"
    dl = f"https://huggingface.co/{repo}/resolve/main/{m['file']}" if repo and on_hf is not False else ""
    swap = ("models:\n  " + rid + ":\n    cmd: |\n      "
            + _cmd_lines(["--port", "${PORT}"] + args).replace("~/models/", "/path/to/models/").replace("\n", "\n      "))
    names = [("temp", "Temperature", "temperature"), ("top_p", "Top P", "top_p"), ("top_k", "Top K", "top_k"),
             ("min_p", "Min P", "min_p"), ("presence_penalty", "Presence penalty", "presence_penalty"),
             ("repeat_penalty", "Repeat penalty", "repeat_penalty")]
    think = ", ".join(f"{k} = {json.dumps(v)}" for k, v in kw.items())
    max_tok = smp.get("max_tokens", 32768)
    try:
        from . import fit as _F
        moe = _F.shape_for(r).is_moe
    except Exception:
        moe = False
    lms = ([("Context Length", f"{ctx:,}" if ctx else "the model's maximum"), ("GPU Offload", "all layers")]
           + ([("MoE expert weights", "on the CPU when the model is bigger than your VRAM (LM Studio's option to keep MoE expert weights on the CPU)")] if moe else [])
           + [
            ("Flash Attention", "on"), ("K Cache / V Cache quantization", kv if kv != "f16" else "off (f16)")]
           + [(label, smp[k]) for k, label, _ in names if k in smp]
           + [("Max response length", f"{max_tok:,} tokens")] + ([("Thinking (chat template)", think)] if think else []))
    lms_txt = "\n".join(f"{k}: {v}" for k, v in lms)
    quant = _quant(fname).split(" ")[0]
    mf = ([f"FROM hf.co/{repo}:{quant}" if repo else f"FROM ./{fname}", f"PARAMETER num_ctx {ctx or 32768}"]
          + [f"PARAMETER {o} {smp[k]}" for k, _, o in names if k in smp] + [f"PARAMETER num_predict {max_tok}"])
    oll_kv = kv if kv in ("f16", "q8_0", "q4_0") else "q8_0"
    oll = "\n".join(mf) + f"\n\n# the server:\nOLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE={oll_kv} ollama serve\n# then:\nollama create {rid} -f Modelfile"
    lacks = ", ".join(x for x in ("MTP speculative decoding" if sp.get("type") == "draft-mtp" else "",
                                   "the anti-loop logit bias" if "--logit-bias" in args else "",
                                   "the thinking budget" if "--reasoning-budget" in args else "") if x)
    left_note = (" Left out: " + "; ".join(left) + " - the model runs without it and may loop a little more often.") if left else ""

    def tab(key: str, body: str, note: str, on: bool = False) -> str:
        n = body.count("\n") + 1
        more = f'<button class="more" type="button">show all {n} lines ▾</button>' if n > 14 else ""
        return (f'<div class="rp{" on" if on else ""}" data-t="{key}"><p class="q rpn">{esc(note)}</p>'
                f'<pre class="cp{" clip" if more else ""}">{esc(body)}</pre>{more}<button class="btn cpy" type="button">COPY</button></div>')
    made = ("" if on_hf is not False else
            f" This file was made on the reference box - the <a href=\"https://huggingface.co/{esc(repo)}\" rel=\"noopener\">{esc(repo)}</a> "
            "GGUF with Qwen3.6's MTP layer grafted in for speculative decoding. With the plain file from that repo, leave out the "
            "two --spec lines: same answers, a little slower" if "graft" in fname else
            " This exact file is not on Hugging Face under this name")
    head = (f'<p class="q" style="margin:0 0 12px">File <b>{esc(fname)}</b>'
            + (f' · <a href="{esc(dl)}" rel="noopener">download it from Hugging Face</a>' if dl else "") + made
            + ". The settings this model was measured with. On another box <code>--fit</code> places the weights for it by itself.</p>")
    return ('<section class="panel pad run"><div class="lbl">Run it yourself</div>' + head
            + '<div class="rtabs" role="tablist">'
            + "".join(f'<button type="button" class="{"on" if i == 0 else ""}" data-t="{k}">{t}</button>' for i, (k, t) in
                      enumerate([("srv", "llama-server"), ("win", "Windows"), ("swap", "llama-swap"), ("lms", "LM Studio"), ("oll", "Ollama")]))
            + "</div>"
            + tab("srv", _cmd_lines(args), "Linux and macOS (Metal), with a current llama.cpp." + left_note, on=True)
            + tab("win", _cmd_lines(args, win=True), "cmd.exe, with llama-server.exe from a llama.cpp release (the CUDA build for NVIDIA cards)." + left_note)
            + tab("swap", swap, "An entry for llama-swap's config.yaml: one server per model, started when a request asks for it.")
            + tab("lms", lms_txt, "LM Studio on macOS or Windows: the model's load and inference settings."
                  + (f" LM Studio has no {lacks}: expect less speed or more looping than measured here." if lacks else ""))
            + tab("oll", oll, "Ollama: a Modelfile and the server's environment." + (f" Ollama has no {lacks}." if lacks else ""))
            + "</section>")


RUN_JS = r"""
document.querySelectorAll(".run").forEach(sec => {
  const pick = t => { sec.querySelectorAll(".rtabs button").forEach(x => x.classList.toggle("on", x.dataset.t === t));
    sec.querySelectorAll(".rp").forEach(x => x.classList.toggle("on", x.dataset.t === t)); };
  sec.querySelectorAll(".rtabs button").forEach(b => b.addEventListener("click", () => {
    pick(b.dataset.t); try { localStorage.setItem("llmbox-runtab", b.dataset.t); } catch (e) {} }));
  try { const t = localStorage.getItem("llmbox-runtab"); if (t && sec.querySelector(`.rtabs button[data-t="${t}"]`)) pick(t); } catch (e) {}   // the app the visitor uses
  sec.querySelectorAll(".more").forEach(b => { const all = b.textContent; b.addEventListener("click", () => { const pre = b.parentElement.querySelector("pre");
    pre.classList.toggle("clip"); b.textContent = pre.classList.contains("clip") ? all : "show fewer lines ▴"; }); });
  sec.querySelectorAll(".cpy").forEach(b => b.addEventListener("click", async () => {
    const t = b.parentElement.querySelector("pre").innerText;
    try { await navigator.clipboard.writeText(t); b.textContent = "COPIED"; } catch (e) { b.textContent = "SELECT AND COPY"; }
    setTimeout(() => b.textContent = "COPY", 1500); }));
});
"""
RUN_CSS = """
.run .rtabs{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px}.run .rtabs button{background:none;border:1px solid transparent;color:var(--muted);font:12px "IBM Plex Mono";padding:5px 10px;cursor:pointer}
.run .rtabs button.on{color:var(--amber);border-color:var(--amber-dim)}.run .rp{display:none;position:relative}.run .rp.on{display:block}
.run pre.cp{background:#0b0c09;border:1px solid var(--line);padding:12px 14px;font-size:12px;line-height:1.6;color:var(--soft);overflow-x:auto;white-space:pre;margin:6px 0 0}
.run .cpy{position:absolute;top:34px;right:8px;font-size:11px;padding:4px 10px}.run .rpn{margin:0}
.run pre.clip{max-height:calc(14 * 1.6em + 24px);overflow:hidden;-webkit-mask-image:linear-gradient(#000 70%,transparent);mask-image:linear-gradient(#000 70%,transparent)}
.run .more{background:none;border:0;color:var(--amber);font:12px "IBM Plex Mono";padding:6px 0;cursor:pointer}
"""


def _pool_summary(rec: dict, recs: list[dict], where: str = "box", current: bool = False) -> None:
    from . import irt, report as _report
    p = _report.ranked_now((rec.get("recipe") or {}).get("id") or "?", where) if current else None
    if p:   # the current suite: every answer that still counts, from any run (report.current_pool)
        s = rec["summary"]
        rec["summary"] = dict(s, capability=p["score"]["capability"], capability_ci95=p["score"]["ci95"], blocks=p["score"]["blocks"],
                              runs=p["runs"], answers=p["score"]["n"], scoring="irt", capability_this_run=s.get("capability"),
                              ci_this_run=s.get("capability_ci95"))
        return
    bank = irt.bank_for((rec.get("suite") or {}).get("content_hash"))
    if bank is None:
        return
    key = lambda r: irt.canonical((r.get("suite") or {}).get("content_hash"))
    same = [r for r in recs if key(r) == key(rec)]
    sc = irt.score_rows(bank, [x for r in same for x in r.get("rows") or []])
    if sc["n"]:
        s = rec["summary"]
        rec["summary"] = dict(s, capability=sc["capability"], capability_ci95=sc["ci95"], blocks=sc["blocks"], runs=len(same),
                              answers=sc["n"], scoring="irt", capability_this_run=s.get("capability"))


def _trace_dir(rec: dict) -> str | None:
    """Traces are saved per queue job (~/.llmbox/traces/job-N); the queue knows which job produced which record."""
    db = os.path.join(HOME, "queue.db")
    if os.path.exists(db):
        row = sqlite3.connect(db).execute("SELECT id FROM jobs WHERE result=?", (rec.get("_path"),)).fetchone()
        if row:
            d = os.path.join(HOME, "traces", f"job-{row[0]}")
            return d if os.path.isdir(d) else None
    return None


def task_flags(rec: dict) -> dict:
    """item id -> {'cut': bool, 'loop': bool, 'max_reply': int} from the rows and the saved thinking."""
    import gzip
    import json as _json
    from . import loopdetect
    td = _trace_dir(rec)
    out = {}
    for r in rec.get("rows", []):
        cut = bool(r.get("reasoning_cut")) or "I have reasoned enough" in (r.get("reasoning_tail") or "")
        loop = False
        if td and os.path.exists(os.path.join(td, r["id"] + ".json.gz")):
            t = _json.load(gzip.open(os.path.join(td, r["id"] + ".json.gz"), "rt"))
            loop = any(loopdetect.scan(x)["fired_at"] for x in t["thinking"])
        out[r["id"]] = {"cut": cut, "loop": loop, "max_reply": max([x.get("predicted_n") or 0 for x in r.get("timings") or []] or [0])}
    return out


def _vs(rec: dict, ref: dict | None) -> float | None:
    return round(100 * rec["summary"]["capability"] / ref["summary"]["capability"], 1) if ref else None


def _spark(vals: list, lo: float, hi: float, w: int = 300, h: int = 44) -> str:
    if not vals or len(vals) < 2:
        return ""
    pts = " ".join(f"{2 + i*(w-4)/(len(vals)-1):.1f},{h-2-(min(max(v, lo), hi)-lo)/(hi-lo)*(h-4):.1f}" for i, v in enumerate(vals))
    return f'<svg viewBox="0 0 {w} {h}" class="spark"><polyline points="{pts}" fill="none" stroke="#FFB000" stroke-width="1.4" filter="url(#g)"/></svg>'


def _telemetry_panel(t: dict | None, title: str = "Hardware during the run") -> str:
    if not t or not t.get("samples"):
        return f'<section class="panel pad"><div class="lbl">{title}</div><span class="q">no telemetry recorded for this run</span></section>'
    cells = []
    spec = [("gpu_temp_c", "GPU temp", "°C avg", 30, 95), ("gpu_power_w", "GPU power", "W avg", 0, 300), ("gpu_util_pct", "GPU util", "% avg", 0, 100),
            ("cpu_temp_c", "CPU temp", "°C avg", 30, 95), ("ram_used_mib", "System RAM", "GB avg", 0, None), ("vram_used_mib", "VRAM", "GB", 0, None)]
    for k, name, unit, lo, hi in spec:
        v = t.get(k)
        if not v:
            continue
        mb = k.endswith("_mib")
        f = (lambda x: x / 1024) if mb else (lambda x: x)
        hi2 = hi or max(v.get("per_min") or [v["max"]]) * 1.2
        cells.append(f'<div><span class="sc">{name}</span><div class="tv">{f(v["avg"]):.{1 if mb else 0}f}<small>{unit} · max {f(v["max"]):.{1 if mb else 0}f}</small></div>'
                     f'{_spark(v.get("per_min") or [], lo, hi2)}</div>')
    return (f'<section class="panel"><div class="lbl">{title}</div>'
            f'<div class="tele" style="grid-template-columns:repeat({len(cells)},1fr)">{"".join(cells)}</div></section>')


def _depth_name(k: float) -> str:
    return "Short chat" if k <= 4 else "Long session" if k <= 40 else "Big document"


def _depth_bars(series: list[tuple[str, dict]]) -> str:
    """Decode speed as the context grows: one row per measured depth, one bar per recipe (the first is highlighted).
    Plain labels instead of a log axis: what the user feels is 'a short chat' vs 'a long session' vs 'a big document'."""
    rows = [(rid, sorted((report._depth_k(k), d) for k, d in bd.items() if d.get("decode_tps"))) for rid, bd in series]
    rows = [(rid, ds) for rid, ds in rows if ds]
    if not rows:
        return '<p class="q">no depth probe in this run</p>'
    top = max(d["decode_tps"] for _, ds in rows for _, d in ds) * 1.12
    out = []
    for i, (k, _) in enumerate(rows[0][1]):
        bars, ft = [], []
        for n, (rid, ds) in enumerate(rows):
            kk, d = min(ds, key=lambda x: abs(x[0] - k))
            t, first = d["decode_tps"], ds[0][1]["decode_tps"]
            delta = f'<em>{100 * (t / first - 1):+.0f}%</em>' if i else ""
            fw = kk * 1000 / d["prefill_tps"] if d.get("prefill_tps") else None
            if len(rows) > 1:   # several recipes: name and first-word time ride on each bar
                bars.append(f'<div class="tr{" b" if n else ""}"><i style="width:{100 * t / top:.1f}%"></i><span>{esc(rid)} {t:.0f} tok/s{delta}'
                            f'{f"<em>first word {fw:.1f} s</em>" if fw else ""}</span></div>')
            else:
                bars.append(f'<div class="tr"><i style="width:{100 * t / top:.1f}%"></i><span>{t:.0f} tok/s{delta}</span></div>')
                ft.append(f'<div class="ft"><b>{fw:.1f} s</b>first word</div>' if fw else '<div class="ft">—</div>')
        out.append(f'<div class="k">{_depth_name(k)}<small>{k:.0f}k tokens in context</small></div><div>{"".join(bars)}</div>{"".join(ft)}')
    return f'<div class="bars{" multi" if len(rows) > 1 else ""}">{"".join(out)}</div>'


_KNAMES = {"sampling.temp": "Temperature", "sampling.top_p": "Top-p", "sampling.top_k": "Top-k", "sampling.min_p": "Min-p",
           "sampling.presence_penalty": "Presence penalty", "sampling.repeat_penalty": "Repeat penalty",
           "chat.template_kwargs.enable_thinking": "Thinking", "chat.template_kwargs.preserve_thinking": "Keep thinking between turns",
           "chat.template_kwargs.reasoning_effort": "Reasoning effort", "placement.kv_type": "KV cache", "placement.ctx": "Context",
           "antiloop.reasoning_budget": "Reasoning reserve", "antiloop.marker_bias": "Loop-marker bias", "speculative.type": "Speculative decoding",
           "speculative.draft_max": "Draft tokens"}


def _human(v) -> str:
    return "default" if v is None else "on" if v is True else "off" if v is False else str(v)


def _axis_of(vss: list) -> tuple[int, int]:
    """The score scale shared by the ranking, the chart and the model pages: from just under the weakest model at >= 50%
    of the frontier to 100 (as the page script's axisOf)."""
    main = [v for v in vss if v is not None and v >= 50] or [v for v in vss if v is not None] or [50]
    lo = max(0, int((min(main) - 6) // 5 * 5))
    return lo, 10 if 100 - lo > 40 else 5


def _fp_html(vs: float, lo: float, hi: float, col: str, ax: tuple[int, int], cloud: bool = False) -> str:
    """A score as a dot on its 95% range (a Claude model: a dashed mark), the same drawing as the ranking's."""
    mn, step = ax
    X = lambda v: max(0.0, min(100.0, (v - mn) / (100 - mn) * 100))
    g = "".join(f"<s style='left:{X(v):.2f}%'></s>" for v in range(mn, 101, step))
    if cloud:
        return f"<div class='fp'><div class='trk'>{g}<b class='rf' style='left:{X(vs):.2f}%'></b></div><span class='num'>{vs:.0f}%</span></div>"
    return (f"<div class='fp' title='95% range {lo:.0f}–{min(100, hi):.0f}%'><div class='trk'>{g}<i style='left:{X(lo):.2f}%;width:{X(hi) - X(lo):.2f}%;background:{col}'></i>"
            f"<b style='left:{X(vs):.2f}%;background:{col}'></b>{f'<em>◂ {vs:.0f}%</em>' if vs < mn else ''}</div><span class='num'>{vs:.0f}%</span></div>")


def _range_pct(r: dict) -> tuple[float, float]:
    k = (r.get("vs_ref") or 0) / r["capability"] if r.get("capability") else 0
    return r["ci"][0] * k, min(100.0, r["ci"][1] * k)


def _cmp_href(a: str, b: str) -> str:
    return f"compare.html#{a}-vs-{b}"


def _near_html(rid: str, rs: list[dict], clouds: list[dict], ranks: dict, look: dict) -> str:
    """Where the model sits: the two models above and below it, and the Claude models in that span, on the ranking's scale."""
    order = sorted([r for r in rs if r.get("vs_ref") is not None], key=lambda r: (-r["vs_ref"], r["id"]))
    i = next((n for n, r in enumerate(order) if r["id"] == rid), None)
    if i is None:
        return ""
    pick = order[max(0, i - 2): i + 3]
    top, bot = max(r["vs_ref"] for r in pick), min(r["vs_ref"] for r in pick)
    top = 100.5 if i < 2 else top
    cl = [c for c in clouds if c.get("vs_ref") is not None and bot - 0.5 <= c["vs_ref"] <= top + 0.5]
    ax = _axis_of([r.get("vs_ref") for r in rs])
    rows = []
    for r in sorted(pick + cl, key=lambda r: -(r["vs_ref"])):
        cloud = r in cl
        nm = model_name(r)
        if cloud:
            rows.append(f"<div class='nr cl'><span class='rk'>☁</span><div class='mw'><span></span><span class='m'>{esc(nm)}</span></div>"
                        f"{_fp_html(r['vs_ref'], 0, 0, '', ax, cloud=True)}<span></span></div>")
            continue
        col, kind = look[r["id"]]
        lo, hi = _range_pct(r)
        me = r["id"] == rid
        link = f"<span class='m'>{esc(nm)}</span>" if me else f"<a class='m' href='recipe-{esc(r['id'])}.html'>{esc(nm)}</a>"
        rows.append(f"<div class='nr{' me' if me else ''}'><span class='rk'>{ranks[r['id']][0]}</span><div class='mw'>{_marker(col, kind)}{link}<span class='qt'>{esc(_quant(r['file']).split(' ')[0])}</span></div>"
                    f"{_fp_html(r['vs_ref'], lo, hi, col, ax)}"
                    + ("<span class='q'>this model</span>" if me else f"<a class='cmpl' href='{esc(_cmp_href(rid, r['id']))}'>compare →</a>") + "</div>")
    labels = "".join(f"<span style='left:{max(0, min(100, (v - ax[0]) / (100 - ax[0]) * 100)):.2f}%'>{v}</span>" for v in range(ax[0], 101, ax[1]))
    return (f"<div class='near'><div class='nr hd'><span></span><span></span><div class='fp'><div class='trk axis'>{labels}</div><span class='num'></span></div><span></span></div>"
            + "".join(rows) + "</div>")


def _settings_panel(rcp: dict, opt: dict | None) -> str:
    """What the model was measured with, split by what sets the score and what only sets the speed, why, and what the
    settings are worth against llama.cpp's defaults on the same box."""
    notes = (rcp.get("notes") or {}).get("lines", [])
    sam, al, pl = rcp.get("sampling") or {}, rcp.get("antiloop") or {}, rcp.get("placement") or {}
    spc, rt = rcp.get("speculative") or {}, rcp.get("runtime") or {}
    chat = (rcp.get("chat") or {}).get("template_kwargs") or {}
    portable = [("Sampling", " · ".join(f"{k.replace('_', '-')} {v}" for k, v in sam.items() if k != "max_tokens") or "server defaults"),
                ("Template", " · ".join(f"{k.replace('_', ' ')} {_human(v)}" for k, v in chat.items()) or "model default"),
                ("Thinking", (f'stops {al.get("reasoning_budget"):,} tokens before the limit' if (al.get("reasoning_budget") or -1) >= 0 else "no limit")
                 + (f' · loop detector {al["reasoning_loop"]}' if al.get("reasoning_loop") else "")),
                ("Anti-loop", (f'bias −{al.get("marker_bias")} on {len(al.get("marker_ids") or [])} tokens that start loops' if al.get("marker_ids") else "none")
                 + (f' · DRY in thinking, allowed {al.get("dry_allowed_length")}' if al.get("dry_think_only") else "")),
                ("KV cache", pl.get("kv_type", "?")),
                ("Speculative", f'{spc.get("type")} · draft {spc.get("draft_max")}' if spc.get("type") else "off")]
    hw = [("Context", f'{round((pl.get("ctx") or 0) / 1024)}k'), ("Batch", f'{pl.get("batch")} / ubatch {pl.get("ubatch")}'),
          ("Threads", f'{rt.get("threads")} on cores {rt.get("cpu_affinity") or "any"}')]
    worth = ""
    if opt:
        s = opt["summary"]
        st, lb = s["stock"], s["llmbox"]
        pct = lambda a, b: f" <em>{(b / a - 1) * 100:+.0f}%</em>" if a and b else ""
        worth = (f"<div class='worth'><div class='sc'>What they are worth on the reference PC</div><dl>"
                 f"<dt>Short chat</dt><dd>{st['decode']:.0f} → <b>{lb['decode']:.0f}</b> tok/s{pct(st['decode'], lb['decode'])}</dd>"
                 + (f"<dt>At 32k</dt><dd>{st['deep']:.0f} → <b>{lb['deep']:.0f}</b> tok/s{pct(st['deep'], lb['deep'])}</dd>" if st.get("deep") and lb.get("deep") else "")
                 + (f"<dt>First word, 12k prompt</dt><dd>{12000 / st['prefill']:.1f} → <b>{12000 / lb['prefill']:.1f}</b> s</dd>" if st.get("prefill") and lb.get("prefill") else "")
                 + f"</dl><p class='q'>Stock = <code>llama-server -m model.gguf -c {pl.get('ctx') or 0}</code> with llama.cpp's own defaults, same file, same box, measured back to back.</p></div>")
    return ('<section class="panel recipe"><div class="lbl">Settings and why</div><div class="rgrid">'
            '<div><div class="sc">Same on every box · these set the score</div><dl>' + "".join(f"<dt>{k}</dt><dd class='val'>{esc(v)}</dd>" for k, v in portable) + "</dl></div>"
            '<div><div class="sc">Fitted to each box · speed only</div><dl>' + "".join(f"<dt>{k}</dt><dd>{esc(v)}</dd>" for k, v in hw) + "</dl>"
            '<p class="q" style="margin-top:10px">values of the reference PC; <code>--fit</code> finds them on yours</p></div></div>'
            + (f'<div class="notes"><div class="sc">Why these settings</div><ul>{"".join(f"<li>{esc(n)}</li>" for n in notes)}</ul></div>' if notes else "")
            + worth + "</section>")


def _runs_panel(runs: list[dict], ref: dict | None, counted: dict) -> str:
    rows = []
    for x in sorted(runs, key=lambda r: r.get("created", ""), reverse=True):
        s, h, rt = x["summary"], x.get("host") or {}, x.get("runtime") or {}
        cap = s.get("capability_this_run", s.get("capability"))
        vs = 100 * cap / ref["summary"]["capability"] if ref and cap is not None else None
        su = x.get("suite") or {}
        rows.append(f"<tr><td class='l'><a href='run-{x['id'][:8]}.html'>{x['id'][:8]}</a><br><span class='q'>{_ago(x.get('created', ''))}</span></td>"
                    f"<td class='l'>v{esc(report.version_of(su))}<br><span class='q'>{'adaptive, ' + str(su.get('budget') or 40) + ' min' if su.get('adaptive') else 'every task once' if not su.get('blocks') else 'blocks: ' + ', '.join(su['blocks'])}</span></td>"
                    f"<td class='l cfg'>{esc((h.get('gpu') or '').replace('NVIDIA GeForce ', ''))} · {h.get('ram_gib')} GB<br><span class='q'>llama.cpp {esc(rt.get('llama_cpp_build') or '?')}</span></td>"
                    f"<td>{counted.get(x.get('created'), 0)}</td><td>{_pct(vs)}</td><td>{(s.get('speed') or {}).get('decode_tps') or 0:.0f}</td></tr>")
    return ('<section class="panel runs"><div class="lbl">Runs</div><div class="tw"><table><tr><th class="l">RUN</th><th class="l">SUITE</th><th class="l">BOX</th>'
            '<th>ANSWERS<br><span class="faint">counted</span></th><th>THIS RUN<br><span class="faint">% of Opus</span></th><th>TOK/S</th></tr>' + "".join(rows)
            + '</table></div><p class="q rn">The score at the top pools every answer from these runs that still counts in this version of the test. '
            'Answers to tasks that changed since a run are left out.</p></section>')


def recipe_page(rid: str, rec: dict, ref: dict | None, ctx: dict) -> str:
    """A model: the verdict (score, speed and fit on your box, reliability), where it ranks, what it is good at, speed as
    the context grows, how to run it, the settings and why, the runs behind the numbers."""
    s, sp = rec["summary"], rec["summary"]["speed"]
    m, rcp = rec.get("model") or {}, rec.get("recipe") or {}
    rs, ranks, med, look = ctx["rs"], ctx["ranks"], ctx["med"], ctx["look"]
    row = next(r for r in rs if r["id"] == rid)
    col, kind = look[rid]
    nm = _name_of(rec)
    pool, fl = ctx["pool_rows"], ctx["flags"]
    # reliability and lost tasks over the pooled answers, each with its own run's thinking flags
    fx = lambda x: (fl.get(x.get("_run")) or {}).get(x["id"], {})
    n_cut = sum(1 for x in pool if fx(x).get("cut"))
    n_loop = sum(1 for x in pool if fx(x).get("loop"))
    lost = {}
    for x in sorted(pool, key=lambda x: x["score"]):
        if x["score"] < 0.99:
            f = fx(x)
            why = "thinking looped" if f.get("loop") else "ran out of thinking room" if f.get("cut") else "wrong answer" if x["score"] < 0.01 else "partly right"
            lost.setdefault(x["block"], []).append(f"{esc(task_name(x['id']))} <span class='faint'>· {x['score'] * 100:.0f} · {why}</span>")
    pl, lo, hi, _ = ranks[rid]
    vs = row.get("vs_ref")
    rlo, rhi = _range_pct(row)
    tps, deep = sp.get("decode_tps"), report._deep(sp)
    shp = ctx["shape"] or {}
    ref_box = ctx["ref_box"]
    lin = _lineage(m.get("hf_repo"))
    org = (lin.get("model") or m.get("hf_repo") or "").split("/")[0]
    origin = (f"fine-tune of {lin['of'].split('/')[-1]}" if kind == "fine-tune" and lin.get("of") else
              f"uncensored remix of {lin['of'].split('/')[-1]}" if kind == "uncensored" and lin.get("of") else f"release by {org}" if org else "")
    size = _size(shp and {"params": shp.get("params"), "moe": shp.get("moe"), "active": shp.get("active")}, nm)
    hf = f"https://huggingface.co/{m['hf_repo']}" if m.get("hf_repo") else ""
    meta = " · ".join(x for x in (origin, size, f"{m['bytes'] / 1e9:.1f} GB file" if m.get("bytes") else "", f"<a href='{esc(hf)}' rel='noopener'>Hugging Face</a>" if hf else "") if x)
    runs_n = len(ctx["runs"])
    tiles = (f"<div><span class='sc'>Score</span><b>{_pct(vs)}</b><span>of Claude Opus 5.5 · range {rlo:.0f}–{rhi:.0f}</span>"
             f"<span>place {pl} of {len(rs)}{f' · tied with {lo}–{hi}' if lo != hi else ''}</span></div>"
             f"<div id='vspd'><span class='sc'>Speed</span><b>{f'{tps:.0f}' if tps else '—'}<small> tok/s</small></b>"
             f"<span class='sub'>short chat{f' · {float(deep):.0f} with a long document' if deep != '-' else ''}</span><span class='src'>measured on the reference PC</span></div>"
             f"<div id='vfit'><span class='sc'>Fits</span><b>{'✓ ' + str(round(shp['ctx'] / 1024)) + 'k' if shp.get('ctx') else '—'}</b>"
             f"<span class='sub'>context on the reference PC</span><span class='src'>{esc(ref_box)}</span></div>"
             f"<div><span class='sc'>Reliability</span><b>{n_cut + n_loop}<small> of {len(pool)}</small></b>"
             f"<span>answers {'where the thinking ran out of room (' + str(n_cut) + ') or looped (' + str(n_loop) + ')' if n_cut + n_loop else 'with a thinking problem: none'}</span>"
             f"<span class='src'>{runs_n} run{'s' if runs_n != 1 else ''} · {ctx['solved_h']:.0f} tasks solved per hour</span></div>")
    body = f'''
<section class="panel title"><div><div class="crumb"><a href="index.html">Models</a> / {esc(nm)}</div>
 <h1>{_marker(col, kind, 18)} {esc(nm)} <span class="muted" style="font-weight:500">· {esc(_quant(m.get("file")))}</span></h1>
 <div class="meta">{meta}</div></div>
 <div class="acts"><a class="btn solid" href="#run">RUN IT</a><a class="btn" href="{esc(ctx['cmp'])}">COMPARE</a></div></section>
<section class="panel verdict"><div class="tiles">{tiles}</div><p class="say">{_stands_sentence(s["blocks"], med)}</p></section>
<section class="panel pad"><div class="lbl">Where it ranks</div>{_near_html(rid, rs, ctx["clouds"], ranks, look)}
 <p class="q" style="margin-top:12px">% of Claude Opus 5.5's score on the same tasks. The line is the 95% range: models whose lines overlap are not measurably apart yet. <a href="index.html">Full ranking →</a></p></section>
<section class="panel pad"><div class="lbl">What it is good at</div>{_groups_html(s["blocks"], med, col, (ref or {}).get("summary", {}).get("blocks"), lost)}
 {_groups_legend(ref=bool(ref))}<p class="q">Click a line to see the tasks it lost and why.</p></section>
<section class="panel pad"><div class="lbl">Speed as the context grows</div>
 <p class="yb" id="yourbox" hidden></p>{_depth_bars([(rid, sp.get("by_depth") or {})])}
 <p class="q" style="margin-top:16px">Measured on the reference PC ({esc(ref_box)}). <a href="hardware-{esc(rid)}.html">Speed on 35 other graphics cards and Macs →</a></p></section>
<div id="run"></div>{_run_panel(rid, rec, ctx["model_now"], ctx["on_hf"])}
{_settings_panel(rcp, ctx["opt"])}
{_runs_panel(ctx["runs"], ref, ctx["counted"])}'''
    js = (PLAN_JS + f"\nconst DATA = {json.dumps({'sh': shp, 'gpus': GPUS, 'ref': ctx['ref_hw']})};\n" + RECIPE_JS + RUN_JS)
    return _page(f"llmbox · {nm} · {_quant(m.get('file'))}", "MODELS", body, _RECIPE_CSS + RUN_CSS, js)


RECIPE_JS = r"""
(function () {   // the visitor's box (picked on the home page): speed and fit predicted for it
  const box = savedBox(DATA);
  if (!box || !DATA.sh || !DATA.sh.layers || sameClassAs(box, DATA.ref)) return;
  const f = forBox(DATA.sh, box), sp = document.querySelector("#vspd"), ft = document.querySelector("#vfit"), yb = document.querySelector("#yourbox");
  if (f.fits) {
    sp.querySelector("b").innerHTML = `~${Math.round(f.t2)}<small> tok/s</small>`;
    sp.querySelector(".sub").textContent = `short chat · ~${Math.round(f.td)} with a long document`;
    ft.querySelector("b").textContent = `✓ ${Math.round(f.ctx / 1024)}k`;
    yb.innerHTML = `On your box (${boxLabel(box)}): <b>~${Math.round(f.t2)} tok/s</b> in a short chat, <b>~${Math.round(f.td)}</b> with a long document, up to ${Math.round(f.ctx / 1024)}k context. Predicted; the bars below are measured on the reference PC.`;
  } else {
    sp.querySelector("b").textContent = "—"; sp.querySelector(".sub").textContent = "does not fit on your box";
    ft.querySelector("b").innerHTML = '<span class="red">✗ too big</span>';
    yb.innerHTML = `On your box (${boxLabel(box)}) this model does not fit, even with a smaller context. The bars below are the reference PC.`;
  }
  sp.querySelector(".src").textContent = "predicted for your box"; ft.querySelector(".sub").textContent = "context on your box";
  ft.querySelector(".src").textContent = boxLabel(box); yb.hidden = false;
})();
"""


def _argv_lines(argv: list[str]) -> list[str]:
    """The command line as one flag per line: '-c 262144', '--temp 0.6' ... (the binary and model path first)."""
    out, cur = [], []
    for x in argv:
        if x.startswith("-") and not re.fullmatch(r"-\d.*", x) and cur:
            out.append(" ".join(cur)); cur = []
        cur.append(x)
    return out + ([" ".join(cur)] if cur else [])


def run_page(rid: str, rec: dict, ref: dict | None, flags: dict) -> str:
    s, sp, h, m, rt = rec["summary"], rec["summary"]["speed"], rec["host"], rec.get("model") or {}, rec.get("runtime") or {}
    vs = _vs(rec, ref)
    bd = sorted((report._depth_k(k), d) for k, d in (sp.get("by_depth") or {}).items())
    body_rows = []
    for b in BLOCKS:
        items = [r for r in rec.get("rows", []) if r["block"] == b]
        if not items:
            continue
        body_rows.append(f'<tr class="grp"><td class="l" colspan="6">{TIPS[b][0].split(" ·")[0].upper()} · {s["blocks"].get(b, 0):.0f} · {len(items)} tasks</td></tr>')
        for r in items:
            f = flags.get(r["id"], {})
            fl = ('<span class="flag lo">CUT</span>' if f.get("cut") else "") + ('<span class="flag lo">LOOP</span>' if f.get("loop") else "")
            lvl = r["id"].rsplit(".", 2)[-2]
            body_rows.append(f'<tr><td class="l"><span class="m2">{esc(r["kind"].replace("_", " "))}</span> <span class="q">{lvl}</span>{" <span class=flag>EXPERT</span>" if lvl == "L6" else ""}</td>'
                             f'<td>{_tile(r["score"] * 100)}</td><td>{r["seconds"]:.0f} s</td><td>{r.get("steps") or 1}</td><td>{f.get("max_reply", 0):,}</td><td class="l">{fl or "<span class=q>—</span>"}</td></tr>')
    argv = rt.get("argv") or []
    diff = rt.get("diff_vs_recipe")
    diff_html = ("<span class='v ok'>identical to the recipe</span>" if diff == [] else
                 "".join(f"<div><span class='flag {'lo' if d['class'] == 'quality' else ''}'>{d['class'].upper()}</span> {esc(d['flag'])}: recipe {esc(' '.join(d['recipe']) or '—')} → run {esc(' '.join(d['run']) or '—')}</div>" for d in diff) if diff
                 else "<span class='q'>not recorded for this run</span>")
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / <a href="recipe-{esc(rid)}.html">{esc(_name_of(rec))}</a> / run {rec["id"][:8]}</div><h1><a href="recipe-{esc(rid)}.html">{esc(_name_of(rec))}</a> on {esc((h.get("gpu") or "").replace("NVIDIA GeForce ", ""))} · {h.get("ram_gib")} GB</h1>
 <div class="id">{esc(rec.get("created", "")[:16].replace("T", " "))} · suite v{esc(rec["suite"]["version"])} · {s["wall_minutes"] / 60:.1f} h</div></div>
 <div class="acts">{'<button class="btn" id="copy">COPY SETTINGS</button>' if argv else ""}</div></section>
<section class="panel sum">
 <div><b>{f"{vs:.0f}%" if vs is not None else "—"}</b><span>of frontier · {s["capability"]:.1f} ({s["capability_ci95"][0]:.0f}–{s["capability_ci95"][1]:.0f})</span></div>
 <div><b class="w">{s["solved"]}</b><span>of {s["items"]} tasks solved</span></div>
 <div><b>{sp.get("decode_tps"):.0f}</b><span>tok/s in a short chat{f" · {float(report._deep(sp)):.0f} with a long context" if report._deep(sp) not in ("-", "") else ""}</span></div>
 <div><b class="w">{f"{2000/bd[0][1]['prefill_tps']:.1f} s" if bd and bd[0][1].get("prefill_tps") else "—"}</b><span>first word at 2k</span></div>
 <div><b class="w">{s["solved_per_hour"]}</b><span>solved tasks per hour</span></div>
 <div><b class="w" style="color:var(--red)">{sum(1 for f in flags.values() if f["cut"]) + sum(1 for f in flags.values() if f["loop"])}</b><span>replies flagged: {sum(1 for f in flags.values() if f["cut"])} out of thinking room, {sum(1 for f in flags.values() if f["loop"])} looped</span></div></section>
<div class="two"><section class="panel sys"><div class="lbl">System</div><dl>
 <dt>GPU</dt><dd>{esc(h.get("gpu"))} · {h.get("vram_gib")} GiB</dd><dt>DRIVER</dt><dd>{esc(h.get("gpu_driver"))} · power limit {esc(h.get("gpu_power_limit_w"))} W</dd>
 <dt>CPU</dt><dd>{esc(h.get("cpu"))}</dd><dt>RAM</dt><dd>{h.get("ram_gib")} GiB · measured {h.get("ram_read_gbs")} GB/s read</dd><dt>OS</dt><dd>{esc(h.get("os"))}</dd>
 <dt>RUNTIME</dt><dd>llama.cpp {esc(rt.get("llama_cpp_build") or "not recorded")}</dd><dt>MODEL</dt><dd>{esc(m.get("hf_repo") or "")}<br><span class="q">{esc(m.get("file") or "")}</span></dd>
 <dt>SHA256</dt><dd class="{"" if m.get("sha256") else "todo"}">{esc(m.get("sha256") or "not recorded")}</dd></dl></section>
 <section class="panel"><div class="lbl">Settings used · the server's command line</div>
  <div class="argv" id="argv">{"".join(f"<span>{esc(x)}</span> " for x in _argv_lines(argv)) if argv else "<span class=q>not recorded for this run</span>"}</div>
  <div class="diff">{diff_html}{" <span class='q'>· consistent start to end</span>" if rt.get("settings_consistent") else ""}</div></section></div>
{_telemetry_panel(rec.get("telemetry"))}
<section class="panel tasks"><div class="lbl">Every task</div>
 <div class="tw"><table><tr><th class="l">TASK</th><th>SCORE</th><th>TIME</th><th>STEPS</th><th>LONGEST REPLY<br><span class="faint">tokens</span></th><th class="l">FLAGS</th></tr>{"".join(body_rows)}</table></div></section>'''
    js = 'const c=document.getElementById("copy");if(c)c.onclick=()=>{navigator.clipboard.writeText(document.getElementById("argv").innerText.trim().replace(/\\s*\\n\\s*/g," ")).then(()=>{c.textContent="COPIED";setTimeout(()=>c.textContent="COPY SETTINGS",1500)})};'
    return _page(f"llmbox · run {rec['id'][:8]} · {rid}", "MODELS", body, _RUN_CSS, js)


def hardware_page(rid: str, rec: dict, shape: dict, data: dict) -> str:
    """Measured boxes for this recipe + predictions for common boxes; the upgrade advisor is computed in the page for
    the visitor's box (saved by the picker on the home page), else for the reference box."""
    import json as _json
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / <a href="recipe-{esc(rid)}.html">{esc(_name_of(rec))}</a> / other boxes</div><h1>How fast is <a href="recipe-{esc(rid)}.html">{esc(_name_of(rec))}</a> on other boxes?</h1>
 <p class="q" style="margin-top:6px">The score is the same on every box; only speed changes. One box is measured, the rest are predicted from the model file and each box's memory speed.</p></div></section>
<section class="panel"><div class="lbl" id="advbox">What would make the reference box faster</div><div class="adv" id="adv"></div>
 <div class="why">This model keeps most of its experts in system RAM on small cards, so RAM speed, not the GPU, sets the pace. More VRAM moves experts onto the GPU.</div></section>
<section class="panel"><div class="lbl" id="boxlbl">Boxes</div><div class="tw"><table id="boxes"></table></div></section>'''
    js = PLAN_JS + f"\nconst DATA = {_json.dumps(dict(data, sh=shape, measured={'gpu': data['ref']['gpu'], 'ram': data['ref']['ram'], 'rambw': data['ref']['rambw'], 't2': rec['summary']['speed'].get('decode_tps'), 'td': float(report._deep(rec['summary']['speed'])) if report._deep(rec['summary']['speed']) != '-' else None}))};\n" + _HW_JS
    return _page(f"llmbox · {_name_of(rec)} on other boxes", "MODELS", body, _HW_CSS, js)


def _flat_settings(rcp: dict) -> dict:
    """A recipe's settings that can differ between two models, by readable name (the model-specific loop-marker token ids
    left out: they are never the same across models)."""
    flat = lambda d, p="": {k2: v2 for k, v in (d or {}).items() for k2, v2 in (flat(v, f"{p}{k}.") if isinstance(v, dict) else {f"{p}{k}": v}).items()}
    f = flat({k: (rcp or {}).get(k) for k in ("sampling", "chat", "antiloop", "placement", "speculative")})
    return {_KNAMES.get(k, k.split(".")[-1].replace("_", " ")): "off" if v in ("", "none") else _human(v)
            for k, v in sorted(f.items()) if not k.endswith("marker_ids") and k != "placement.n_cpu_moe"}


def compare_data(rs: list[dict], clouds: list[dict], ranks: dict, local: dict, look: dict, data: dict, rel: dict) -> dict:
    """Everything the compare page needs for any pair: the ranked local models, then the Claude models for reference."""
    models = []
    for r in sorted(rs, key=lambda r: ranks[r["id"]][0]) + clouds:
        rid, cloud = r["id"], r in clouds
        pool = (report.ranked_now(rid, "cloud" if cloud else "box") or {}).get("rows") or []
        fam = {}
        for x in pool:
            fam.setdefault(x.get("family") or ".".join(x["id"].split(".")[:3]), []).append(x["score"])
        rec = local.get(rid) or {}
        lo, hi = _range_pct(r) if r.get("vs_ref") is not None else (None, None)
        bd = sorted((report._depth_k(k), d) for k, d in ((r.get("speed") or {}).get("by_depth") or {}).items() if d.get("decode_tps"))
        col, kind = look.get(rid, ("#8b877b", "release"))
        models.append({"id": rid, "name": model_name(r), "quant": "" if cloud else _quant(r.get("file")).split(" ")[0], "col": col, "kind": kind,
                       "cloud": cloud, "place": None if cloud else ranks[rid][0], "vs": r.get("vs_ref"), "lo": lo, "hi": hi, "cap": r["capability"], "ci": r["ci"],
                       "blocks": r["blocks"], "t2": None if cloud else (r.get("speed") or {}).get("decode_tps"),
                       "td": None if cloud or report._deep(r["speed"]) == "-" else float(report._deep(r["speed"])),
                       "depth": [[k, round(d["decode_tps"], 1), round(k * 1000 / d["prefill_tps"], 1) if d.get("prefill_tps") else None] for k, d in bd],
                       "fam": {f: round(sum(v) / len(v), 2) for f, v in fam.items()}, "set": _flat_settings(rec.get("recipe") or {}) if rec else {},
                       "rel": rel.get(rid)})
    from . import suite
    return {"models": models, "groups": GROUPS, "weights": suite.WEIGHTS, "short": SHORT, "tasks": TASK_NAMES,
            "recipes": data["recipes"], "gpus": data["gpus"], "ref": data["ref"]}


def compare_app(cdata: dict) -> str:
    """One page for any two models (the pair in the address: compare.html#a-vs-b): a verdict in words that says when a
    difference is inside the margin of error, the four uses and nine blocks side by side, speed and fit on the
    visitor's box, the tasks only one of them solved, the settings that differ."""
    opts = ("<optgroup label='Local models, by place'>" + "".join(f"<option value='{esc(m['id'])}'>{m['place']}. {esc(m['name'])} · {esc(m['quant'])}</option>" for m in cdata["models"] if not m["cloud"])
            + "</optgroup><optgroup label='Cloud, for reference'>" + "".join(f"<option value='{esc(m['id'])}'>{esc(m['name'])}</option>" for m in cdata["models"] if m["cloud"]) + "</optgroup>")
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / compare</div><h1>Compare two models</h1>
 <div class="pick"><span class="dot a"></span><select id="ma" aria-label="first model">{opts}</select><button class="btn sw" id="swap" type="button" title="swap">⇄</button>
 <span class="dot b"></span><select id="mb" aria-label="second model">{opts}</select></div></div></section>
<section class="panel verdict2" id="verdict"></section>
<section class="panel pad"><div class="lbl">Side by side</div><div id="dumb"></div>
 <p class="q" style="margin-top:14px">Out of 100. The number on the right is the gap; gaps under 6 points are within what a few more tasks could change. <a href="method.html">How scores work</a></p></section>
<div class="two2"><section class="panel pad"><div class="lbl" id="spdlbl">Speed as the context grows</div><div id="speed"></div></section>
<section class="panel pad"><div class="lbl">Tasks only one of them solved</div><div id="only"></div></section></div>
<section class="panel"><div class="lbl">Settings that differ</div><div class="tw" id="sets"></div></section>'''
    js = PLAN_JS + f"\nconst DATA = {json.dumps(cdata)};\n" + _CMP_JS
    return _page("llmbox · compare two models", "COMPARE", body, _CMP_CSS, js)


_CMP_JS = r"""
const $ = s => document.querySelector(s), M = Object.fromEntries(DATA.models.map(m => [m.id, m]));
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const wavg = (b, bs) => { let s = 0, n = 0; for (const k of bs) if (b[k] != null) { s += b[k] * DATA.weights[k]; n += DATA.weights[k]; } return n ? s / n : null; };
const se = (m, up) => Math.max(0.3, (up ? m.ci[1] - m.cap : m.cap - m.ci[0]) / 1.96);
const surely = (a, b) => a.cap - b.cap > 1.96 * Math.hypot(se(a, false), se(b, true));   // as the ranking: outside the difference's own 95% margin
const box = savedBox(DATA);
function speedOf(m) {   // measured on the reference PC, or predicted for the visitor's box
  if (m.cloud) return null;
  const sh = DATA.recipes[m.id];
  if (box && sh && !sameClassAs(box, DATA.ref)) { const f = forBox(sh, box); return { t2: f.fits ? f.t2 : null, td: f.fits ? f.td : null, fits: f.fits, ctx: f.ctx, pred: true }; }
  return { t2: m.t2, td: m.td, fits: true, ctx: sh ? sh.ctx : null, pred: false };
}
const nm = m => `${esc(m.name)}${m.quant ? ` <span class="qt">${esc(m.quant)}</span>` : ""}`;
function render(a, b) {
  const A = M[a], B = M[b];
  $("#ma").value = a; $("#mb").value = b;
  history.replaceState(null, "", `#${a}-vs-${b}`);
  document.title = `llmbox · ${A.name} vs ${B.name}`;
  // the verdict: score first (with the margin), then speed and fit on the box, then the uses that differ
  const sA = speedOf(A), sB = speedOf(B), say = [];
  if (A.vs != null && B.vs != null) {
    const [hi, lo] = A.cap >= B.cap ? [A, B] : [B, A];
    say.push(surely(hi, lo) ? `<b>${esc(hi.name)}</b> is measurably better: ${Math.round(hi.vs)}% against ${Math.round(lo.vs)}% of Claude Opus 5.5.`
      : `Not measurably apart yet: ${Math.round(A.vs)}% and ${Math.round(B.vs)}% of Claude Opus 5.5, and their ranges overlap (${Math.round(A.lo)}–${Math.round(A.hi)} and ${Math.round(B.lo)}–${Math.round(B.hi)}).`);
  }
  if (sA && sB && sA.t2 && sB.t2) { const [f, s] = sA.t2 >= sB.t2 ? [[A, sA], [B, sB]] : [[B, sB], [A, sA]], k = f[1].t2 / s[1].t2;
    say.push(k < 1.1 ? `About as fast as each other${sA.pred ? " on your box" : ""} (${Math.round(sA.t2)} and ${Math.round(sB.t2)} tok/s).`
      : `<b>${esc(f[0].name)}</b> writes ${k.toFixed(1)}× faster${f[1].pred ? " on your box" : ""}: ${sA.pred ? "~" : ""}${Math.round(f[1].t2)} against ${Math.round(s[1].t2)} tok/s.`); }
  for (const [m, s] of [[A, sA], [B, sB]]) if (s && !s.fits) say.push(`<b>${esc(m.name)}</b> does not fit on your box.`);
  if (A.cloud || B.cloud) say.push(`Claude runs in the cloud: it is here as a reference for quality, with no speed on your box.`);
  const gaps = DATA.groups.map(([g, bs]) => [g, wavg(A.blocks, bs) - wavg(B.blocks, bs)]).filter(x => Math.abs(x[1]) >= 6);
  if (gaps.length) say.push("Clear gaps: " + gaps.map(([g, d]) => `${esc(g.toLowerCase())} favours <b>${esc((d > 0 ? A : B).name)}</b> (${d > 0 ? "+" : "−"}${Math.round(Math.abs(d))})`).join("; ") + ".");
  const tile = (m, s, cls) => `<div class="side ${cls}"><div class="who"><span class="dot ${cls}"></span>${m.cloud ? nm(m) : `<a href="recipe-${m.id}.html">${nm(m)}</a>`}</div>` +
    `<div class="nums"><div><b>${m.vs != null ? Math.round(m.vs) + "%" : "—"}</b><span>${m.cloud ? "cloud reference" : `place ${m.place} · range ${Math.round(m.lo)}–${Math.round(m.hi)}`}</span></div>` +
    `<div><b>${s && s.t2 ? (s.pred ? "~" : "") + Math.round(s.t2) : "—"}</b><span>${m.cloud ? "cloud" : s && !s.fits ? "does not fit" : `tok/s ${s.pred ? "on your box" : "measured"}`}</span></div>` +
    `<div><b>${s && s.fits && s.ctx ? "✓ " + Math.round(s.ctx / 1024) + "k" : s && !s.fits ? "✗" : "—"}</b><span>${m.cloud ? "" : "context that fits"}</span></div>` +
    `<div><b>${m.rel ? m.rel[0] : "—"}</b><span>${m.rel ? `of ${m.rel[1]} answers with thinking problems` : ""}</span></div></div></div>`;
  $("#verdict").innerHTML = `<div class="sides">${tile(A, sA, "a")}${tile(B, sB, "b")}</div><p class="say">${say.join(" ")}</p>`;
  // side by side: the overall score, then each use with its blocks, on a scale from just under the lowest value to 100
  const vals = [A.vs, B.vs, ...Object.values(A.blocks), ...Object.values(B.blocks)].filter(v => v != null);
  const lo0 = Math.max(0, Math.floor((Math.min(...vals) - 5) / 10) * 10), X = v => (Math.max(v, lo0) - lo0) / (100 - lo0) * 100;
  const row = (label, x, y, cls) => { const d = x != null && y != null ? x - y : null;
    return `<div class="dr ${cls}"><span class="dl">${label}</span><span class="dv">${x != null ? Math.round(x) : "—"}</span><span class="dt">` +
      (x != null ? `<i class="a" style="left:${X(x)}%"></i>` : "") + (y != null ? `<i class="b" style="left:${X(y)}%"></i>` : "") +
      (x != null && y != null ? `<em style="left:${X(Math.min(x, y))}%;width:${X(Math.max(x, y)) - X(Math.min(x, y))}%"></em>` : "") + `</span><span class="dv">${y != null ? Math.round(y) : "—"}</span>` +
      `<span class="dd ${d == null || Math.abs(d) < 6 ? "" : d > 0 ? "wa" : "wb"}">${d == null ? "" : Math.abs(d) < 0.5 ? "=" : (d > 0 ? "+" : "−") + Math.round(Math.abs(d))}</span></div>`; };
  let ticks = ""; for (let v = lo0; v <= 100; v += 10) ticks += `<span style="left:${X(v)}%">${v}</span>`;
  let h = `<div class="dr hd"><span></span><span class="dv"><span class="dot a"></span></span><span class="dt axis">${ticks}</span><span class="dv"><span class="dot b"></span></span><span></span></div>`;
  h += row("% of Claude Opus 5.5", A.vs, B.vs, "top");
  for (const [g, bs] of DATA.groups) { h += row(esc(g), wavg(A.blocks, bs), wavg(B.blocks, bs), "g");
    if (bs.length > 1) for (const k of bs) h += row(esc(DATA.short[k]), A.blocks[k], B.blocks[k], "k"); }
  $("#dumb").innerHTML = h;
  // speed as the context grows: both on the reference PC (what was measured)
  const deps = A.depth.length ? A.depth : B.depth;
  if (A.cloud || B.cloud || !deps.length) $("#speed").innerHTML = `<p class="q">${A.cloud || B.cloud ? "A cloud model has no speed on a box." : "No speed probe recorded."}</p>`;
  else { const top = Math.max(...A.depth.map(x => x[1]), ...B.depth.map(x => x[1])) * 1.1;
    const near = (m, k) => m.depth.reduce((p, x) => Math.abs(x[0] - k) < Math.abs(p[0] - k) ? x : p, m.depth[0]);
    $("#speed").innerHTML = deps.map(([k]) => { const x = near(A, k), y = near(B, k);
      return `<div class="sd"><div class="k">${k <= 4 ? "Short chat" : k <= 40 ? "Long session" : "Big document"}<small>${Math.round(k)}k tokens in context</small></div><div>` +
        [[A, x, "a"], [B, y, "b"]].map(([m, v, c]) => `<div class="sb ${c}"><i style="width:${100 * v[1] / top}%"></i><span>${Math.round(v[1])} tok/s${v[2] ? ` · first word ${v[2]} s` : ""}</span></div>`).join("") + `</div></div>`; }).join("") +
      `<p class="q" style="margin-top:12px">Measured on the reference PC.${box && !sameClassAs(box, DATA.ref) ? " Your box: see the tiles above." : ""}</p>`; }
  // tasks one solved and the other did not (same task kind and level; runs pick different tasks, so only the shared ones count)
  const only = (x, y) => Object.keys(x.fam).filter(f => f in y.fam && x.fam[f] >= 0.99 && y.fam[f] < 0.5).sort();
  const tname = f => { const p = f.split("."); return `${esc(DATA.tasks[p[0] + "." + p[1]] || p[1])} <span class="faint">· level ${p[2].slice(1)}</span>`; };
  const shared = Object.keys(A.fam).filter(f => f in B.fam).length;
  $("#only").innerHTML = `<div class="only"><div><div class="sc"><span class="dot a"></span>only ${esc(A.name)}</div><ul>${only(A, B).map(f => `<li>${tname(f)}</li>`).join("") || "<li class=q>none</li>"}</ul></div>` +
    `<div><div class="sc"><span class="dot b"></span>only ${esc(B.name)}</div><ul>${only(B, A).map(f => `<li>${tname(f)}</li>`).join("") || "<li class=q>none</li>"}</ul></div></div>` +
    `<p class="q" style="margin-top:10px">Out of ${shared} kinds of task both were given.</p>`;
  // the settings that differ
  const keys = [...new Set([...Object.keys(A.set), ...Object.keys(B.set)])].filter(k => A.set[k] !== B.set[k]).sort();
  $("#sets").innerHTML = A.cloud || B.cloud ? `<p class="q" style="padding:16px">Cloud models have no local settings.</p>` : keys.length ?
    `<table><tr><th class="l">SETTING</th><th class="l"><span class="dot a"></span>${esc(A.name)}</th><th class="l"><span class="dot b"></span>${esc(B.name)}</th></tr>` +
    keys.map(k => `<tr><td class="l">${esc(k)}</td><td class="l amb">${esc(A.set[k] ?? "default")}</td><td class="l">${esc(B.set[k] ?? "default")}</td></tr>`).join("") + "</table>"
    : `<p class="q" style="padding:16px">Same settings.</p>`;
}
function fromHash() {
  const p = location.hash.slice(1).split("-vs-"), ids = DATA.models.filter(m => !m.cloud).map(m => m.id);
  return p.length === 2 && M[p[0]] && M[p[1]] && p[0] !== p[1] ? p : [ids[0], ids[1]];
}
$("#ma").addEventListener("change", () => { let b = $("#mb").value; if (b === $("#ma").value) b = DATA.models.find(m => m.id !== b).id; render($("#ma").value, b); });
$("#mb").addEventListener("change", () => { let a = $("#ma").value; if (a === $("#mb").value) a = DATA.models.find(m => m.id !== a).id; render(a, $("#mb").value); });
$("#swap").addEventListener("click", () => render($("#mb").value, $("#ma").value));
addEventListener("hashchange", () => render(...fromHash()));
render(...fromHash());
"""


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


def new_page(rs: list[dict], data: dict, host: str) -> str | None:
    """What came out in the last six months that runs on the visitor's box: releases, fine-tunes and remixes pulled by
    enough people (llmbox/candidates.py), each with the file this box runs well (4-bit first, a smaller quantization
    when the 4-bit file does not fit), its predicted speed, and its score - measured here when it was, otherwise the
    range expected from public benchmarks. Measured models are listed with their result, not hidden."""
    import datetime as _dt
    from . import watch as W
    first_seen = W.first_seen()
    week_ago = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()
    from . import candidates as C, estimate as E, fit as F, recipe as rc
    cs = C.load()
    if not cs:
        return None
    cutoff = (_dt.date.today() - _dt.timedelta(days=C.RECENT_DAYS)).isoformat()
    from . import eci
    table = eci.load()
    ref_cap = next((r["capability"] / (r["vs_ref"] / 100) for r in rs if r.get("vs_ref")), None)
    by_base: dict = {}
    chains: dict = {}
    measured: dict = {}   # candidate key -> measured row
    by_name: dict = {}    # the same without the org: a repo whose base tags could not be read still matches its model
    for r in rs:
        try:
            repo = rc.load(host, r["id"])["model"].get("hf_repo") or ""
        except (OSError, ValueError):
            continue
        chains[r["id"]] = C.base_chain(repo)
        # an anchor must BE the scored model (a quantization of it): a fine-tune of a base is a different model
        hit = eci.match(repo, table) if not eci.is_remix(repo) else None
        if hit:
            by_base.setdefault(hit[0], (hit[1]["eci"], []))[1].append((r["id"], r["capability"]))
        measured.setdefault(C.key_of(repo), []).append(dict(r, repo=repo))
        by_name.setdefault(C._key(re.sub(r"-gguf(-mtp)?$", "", repo, flags=re.I)).split("/")[-1], []).append(dict(r, repo=repo))
    anchors = ([(f"{eci.FRONTIER_PROXY} (stands in for the reference)", table[eci.FRONTIER_PROXY]["eci"], ref_cap)]
               if ref_cap and eci.FRONTIER_PROXY in table else [])
    anchors += [(f"{name} via {', '.join(i for i, _ in ms)}", e, sum(c for _, c in ms) / len(ms)) for name, (e, ms) in by_base.items()]
    pred = eci.predictor(table, anchors, ref_cap) if ref_cap else None
    kv = "q8_0"

    def shp(sh: E.ModelShape) -> dict:
        return {"moe": sh.is_moe, "nonexp": sh.nonexpert_bytes, "exp": sh.expert_bytes, "embed": sh.embed_bytes,
                "layers": sh.n_layers, "nExp": sh.n_expert, "nUsed": sh.n_expert_used,
                "rec": sh.recurrent_state_bytes + sh.kv_swa_bytes(kv), "cpuEff": sh.expert_cpu_eff,
                "kvB": sh.kv_bytes_per_token(kv), "ctx": sh.context_length or 32768, "k2": 1, "kd": 1, "deepK": 32}

    def expected(repo: str):
        hit = eci.match(repo, table) if pred else None
        if not hit:
            return None
        mid, lo, hi = pred(hit[1]["eci"], hit[1]["lo"], hit[1]["hi"])
        return {"mid": round(mid), "lo": round(lo), "hi": round(hi), "name": hit[0], "eci": hit[1]["eci"], "remix": eci.is_remix(repo)}

    def mrow(m: dict) -> dict:
        sp = m["speed"] or {}
        return {"rid": m["id"], "vs": m.get("vs_ref"), "cap": m["capability"], "t2": sp.get("decode_tps"),
                "td": float(report._deep(sp)) if report._deep(sp) != "-" else None}

    rows, seen = [], set()
    for c in cs:
        sh = E.ModelShape(**c["shape"])
        own = C.base_chain(c["repo"])
        roots = set(own[:2]) | {re.sub(r"-gguf$", "", c["repo"], flags=re.I)}   # the repo and the model it packages
        lin = c.get("lineage") or {"kind": "release", "of": None, "model": c["base"]}
        key = C._key(lin.get("model") or c["base"])
        ms = (measured.get(key) or measured.get(C._key(c["base"])) or by_name.get(key.split("/")[-1])
              or by_name.get(C._key(re.sub(r"-gguf(-mtp)?$", "", c["repo"], flags=re.I)).split("/")[-1]) or [])
        seen |= {m["id"] for m in ms}
        # measured fine-tunes of this model (declared on Hugging Face): context, not a prediction - they are other models
        rel = [(rid, (r.get("vs_ref") or 0)) for r in rs for rid in [r["id"]] if rid in chains and roots & set(chains[rid][1:]) and rid not in {m["id"] for m in ms}]
        seen_on = first_seen.get(key)
        rows.append({"repo": c["repo"], "rid": C.recipe_id(c["repo"]), "released": c.get("released") or c.get("created"),
                     "fresh": bool(seen_on and seen_on >= week_ago),
                     "dl": c["downloads"], "total": round(sh.total_params / 1e9, 1), "active": round(sh.active_params / 1e9, 1),
                     "kind": lin["kind"], "of": lin.get("of"), "rel": rel, "guess": expected(c["repo"]),
                     "measured": [mrow(m) for m in ms], "bytes0": c["bytes"], "sh": shp(sh),
                     "ladder": [{"file": x["file"], "quant": x["quant"], "bytes": x["bytes"]} for x in (c.get("ladder") or [{"file": c["file"], "quant": c["quant"], "bytes": c["bytes"]}])]})
    # measured models released in the window that the popularity cut left out: listed with their result
    for r in rs:
        if r["id"] in seen or r["id"] not in data["recipes"]:
            continue
        try:
            rec = rc.load(host, r["id"])
        except (OSError, ValueError):
            continue
        repo = rec["model"].get("hf_repo") or ""
        rel_day = C.released(repo) if repo else None
        if not rel_day or rel_day < cutoff:
            continue
        sh = F.shape_for(rec)
        lin = C.lineage(repo)
        rows.append({"repo": repo, "rid": r["id"], "released": rel_day, "dl": None, "total": round(sh.total_params / 1e9, 1),
                     "active": round(sh.active_params / 1e9, 1), "kind": lin["kind"], "of": lin.get("of"), "rel": [],
                     "guess": expected(repo), "measured": [mrow(r)], "bytes0": sh.total_bytes or 1, "sh": shp(sh),
                     "ladder": [{"file": rec["model"].get("file") or "", "quant": _quant(rec["model"].get("file") or ""), "bytes": sh.total_bytes or 1}]})
    body = f"""
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / new</div><h1>New models for your box</h1>
 <p class="q" style="margin-top:6px">Released in the last six months and pulled by enough people, one entry per model: releases, fine-tunes
 and uncensored remixes, labelled. Fit and speed are for <b id="boxname">the reference box</b> (<a href="index.html">pick your box</a>):
 when the 4-bit file does not fit, the largest 3- or 2-bit file that does is shown. Measured models show their result; the rest show
 the range expected from public benchmarks.</p></div></section>
<section class="panel"><div class="lbl"><span id="count">{len(rows)}</span> models · click a column to sort</div>
 <div class="nf"><label><input type="checkbox" id="fitonly" checked> only what runs on this box</label>
  <label><input type="checkbox" id="nouncens"> hide uncensored remixes</label></div>
 <div class="tw"><table class="cand"><tr><th class="l">MODEL</th><th data-sort="rel">RELEASED ↕</th><th data-sort="size">SIZE ↕<br><span class="faint">total · active</span></th>
 <th data-sort="exp">SCORE ↕<br><span class="faint">measured or expected</span></th><th data-sort="speed">TOK/S ON YOUR BOX ↕<br><span class="faint">chat · 32k</span></th>
 <th>FILE</th><th data-sort="dl">DOWNLOADS ↕<br><span class="faint">30 days</span></th><th></th></tr>
 <tbody id="rows"></tbody></table></div></section>"""
    note = ("<section class='panel pad'><div class='lbl'>Where the expected score comes from</div><p class='q' style='max-width:900px;line-height:1.7'>"
            "A measured model shows its own score. Otherwise the model's "
            "<a href='https://epoch.ai/benchmarks'>Epoch Capabilities Index</a> (one number fitted over many public benchmarks), put on our scale by a "
            "straight line through models that have both: " + "; ".join(f"{esc(n)}: ECI {e:.0f} = {c / ref_cap * 100:.0f}%" for n, e, c in anchors)
            + ". A fine-tune or remix gets its base model's range: training can move it either way. A 3- or 2-bit file scores lower than the 4-bit "
            "one the range is for. ECI data: Epoch AI, 'Capabilities &amp; benchmarking', epoch.ai/benchmarks, CC BY 4.0.</p></section>") if pred else ""
    body += note
    js = PLAN_JS + "\nconst DATA = " + json.dumps(dict(ref=data["ref"], gpus=data["gpus"], ramKinds=data["ramKinds"], rows=rows, eciReady=bool(pred))) + ";\n" + _NEW_JS
    return _page("llmbox · new models", "NEW", body, _NEW_CSS, js)


_NEW_CSS = """
.cand td{padding:10px 8px}.cand td.l .m{display:block}.cand .repo{font-size:11px;color:var(--faint)}
.cand tr.nofit td{opacity:.45}.cand .rel{display:block;font-size:10.5px;color:var(--muted)}
.cand .go{font-size:11px;padding:5px 10px;white-space:nowrap}
th[data-sort]{cursor:pointer;user-select:none}th[data-sort]:hover,th[data-sort].on{color:var(--amber)}
.cand .guess{color:var(--soft)}.cand .kind{display:inline-block;font-size:10px;letter-spacing:.08em;text-transform:uppercase;padding:1px 5px;margin-left:6px;border:1px solid var(--line);color:var(--muted);vertical-align:2px}
.cand .kind.release{color:var(--amber);border-color:var(--amber-dim)}.cand .kind.fresh{color:var(--bg);background:var(--amber);border-color:var(--amber)}.cand .kind.uncensored{color:#c46a5a;border-color:#5a2e26}
.cand .when{white-space:nowrap}.cand .low{color:#c49a5a}
.nf{display:flex;flex-wrap:wrap;gap:18px;padding:10px 16px 0;font-size:12.5px;color:var(--muted)}.nf input{accent-color:#FFB000;vertical-align:-2px;margin-right:6px}
.cmds{background:#0b0c09;border:1px solid var(--line);padding:12px 14px;margin:4px 0 8px;text-align:left;font-size:12.5px;line-height:1.8;color:var(--soft)}
.cmds code{display:block;color:var(--amber)}.cmds code:before{content:"$ ";color:var(--faint)}
"""
_NEW_JS = r"""
const $ = s => document.querySelector(s);
const saved = savedBox(DATA);
const box = saved || { name: "the reference box", gpu: DATA.ref.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: DATA.ref.ram, rambw: DATA.ref.rambw };
$("#boxname").textContent = box.name === "the reference box" ? `the reference box (${DATA.ref.gpu} · ${Math.round(DATA.ref.ram / 1024)} GB · ${DATA.ref.rambw} GB/s)` : `your box (${boxLabel(box)})`;
const fmtDl = n => n == null ? "—" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : n;
const cap = v => v > 200 ? "200+" : "~" + Math.round(v);   // above ~200 the formula ignores per-token overheads
const bits = q => { const m = q.replace("UD-", "").toUpperCase().match(/(?:I?Q|BF|F)(\d+)/); return m ? +m[1] : 16; };
const ago = d => { const n = Math.round((Date.now() - new Date(d)) / 864e5); return n < 1 ? "today" : n < 45 ? `${n} d ago` : `${Math.round(n / 30)} mo ago`; };
function pick(r) {   // the file this box runs well: 4-bit first, then the largest 3- or 2-bit file; 32k context or more
  let first = null;
  for (const f of r.ladder) {
    const k = f.bytes / r.bytes0, sh = Object.assign({}, r.sh, { nonexp: r.sh.nonexp * k, exp: r.sh.exp * k, embed: r.sh.embed * k });
    const p = forBox(sh, box);
    if (p.fits && !first) first = { f, p };
    if (p.fits && p.ctx >= 32768) return { f, p };
  }
  return first || { f: r.ladder[0], p: null };
}
const rows = DATA.rows.map(r => { const c = pick(r), m = r.measured[0];
  const onRef = !saved && m && m.t2;   // on the reference box a measured model shows what was measured
  const t2 = onRef ? m.t2 : c.p ? c.p.t2 : 0, td = onRef ? m.td : c.p ? c.p.td : 0;
  const score = m ? m.vs : r.guess ? r.guess.mid : null;
  return Object.assign({}, r, { c, m, fits: !!c.p, t2, td, onRef, score, low: c.p && bits(c.f.quant) < 4 }); });
function scoreCell(r) {
  const ft = r.rel.length ? `<span class="rel">fine-tunes measured: ${r.rel.map(x => x[0] + " " + Math.round(x[1]) + "%").join(", ")}</span>` : "";
  if (r.m) return `<span class="tile ${r.m.vs >= 85 ? "hi" : r.m.vs >= 50 ? "mid" : "lo"}">${Math.round(r.m.vs)}%<small>${r.m.cap.toFixed(1)}</small></span><span class="rel">measured: <a href="recipe-${r.m.rid}.html">${r.m.rid}</a></span>` + ft;
  if (!r.guess) return `<span class="q">—</span>` + (ft || `<span class="rel">not in the public index</span>`);
  return `<span class="guess">~${r.guess.mid}%</span><span class="rel" title="Epoch Capabilities Index of ${r.guess.name}: ${r.guess.eci.toFixed(1)}">${r.guess.lo}–${r.guess.hi} · from ECI${r.guess.remix || r.kind !== "release" ? " of its base" : ""}</span>` + ft;
}
const tile = v => `<span class="tile pred ${v >= 50 ? "hi" : v >= 25 ? "mid" : "lo"}">${cap(v)}</span>`;
let sortKey = "rel", sortDir = -1;
const keys = { rel: r => +new Date(r.released), exp: r => r.score ?? -1, speed: r => r.t2 || 0, size: r => r.total, dl: r => r.dl || 0 };
function render() {
  const k = keys[sortKey], fitOnly = $("#fitonly").checked, noU = $("#nouncens").checked;
  const list = rows.filter(r => (!fitOnly || r.fits) && (!noU || r.kind !== "uncensored")).sort((a, b) => sortDir * (k(a) - k(b)) || ((b.dl || 0) - (a.dl || 0)));
  $("#count").textContent = list.length;
  $("#rows").innerHTML = list.map((r, i) => `<tr class="${r.fits ? "" : "nofit"}"><td class="l"><span class="m">${r.repo.split("/")[1].replace(/-GGUF(-MTP)?$/i, "")}<span class="kind ${r.kind}">${r.kind}</span>${r.fresh ? '<span class="kind fresh" title="first listed here in the last 7 days">new this week</span>' : ""}</span>` +
    (r.of ? `<span class="rel">trained from ${r.of}</span>` : "") + `<a class="repo" href="https://huggingface.co/${r.repo}" rel="noopener">${r.repo}</a></td>` +
    `<td class="when" title="${r.released}">${ago(r.released)}</td><td>${r.total}B · ${r.active}B</td><td>${scoreCell(r)}</td>` +
    `<td>${r.fits ? (r.onRef ? `<span class="tile ${r.t2 >= 50 ? "hi" : r.t2 >= 25 ? "mid" : "lo"}">${Math.round(r.t2)}</span><span class="rel">${r.td ? Math.round(r.td) + " at 32k · " : ""}measured</span>` : tile(r.t2) + `<span class="rel">${cap(r.td)} at 32k · ${Math.round(r.c.p.ctx / 1024)}k ctx</span>`) : `<span class="red">✗ too big</span>`}</td>` +
    `<td><span class="q">${r.c.f.quant} · ${(r.c.f.bytes / 1e9).toFixed(1)} GB</span>${r.low ? `<span class="rel low">${bits(r.c.f.quant)}-bit: expect a lower score than 4-bit</span>` : ""}</td><td>${fmtDl(r.dl)}</td>` +
    `<td>${r.m ? `<a class="btn go" href="recipe-${r.m.rid}.html">RESULTS</a>` : `<button class="btn go" data-i="${i}">TEST IT</button>`}</td></tr>` +
    (r.m ? "" : `<tr class="cmdrow" id="c${i}" hidden><td colspan="8"><div class="cmds">Draft the recipe for this file, download it, tune the speed, run the 40-minute adaptive test:` +
    `<code>llmbox recipe new ${r.repo} --file ${r.c.f.file.split("/").pop()} --write</code><code>llmbox install ${r.rid} --apply</code><code>llmbox optimize ${r.rid}</code>` +
    `<code>llmbox bench ${r.rid} --recipe ${r.rid} --adaptive --budget 40 --speed-probe</code></div></td></tr>`)).join("");
  document.querySelectorAll(".go[data-i]").forEach(b => b.addEventListener("click", () => { const c = $("#c" + b.dataset.i); c.hidden = !c.hidden; }));
  document.querySelectorAll("th[data-sort]").forEach(th => th.classList.toggle("on", th.dataset.sort === sortKey));
}
document.querySelectorAll("th[data-sort]").forEach(th => th.addEventListener("click", () => {
  sortDir = sortKey === th.dataset.sort ? -sortDir : -1; sortKey = th.dataset.sort; render(); }));
["#fitonly", "#nouncens"].forEach(s => $(s).addEventListener("change", render));
render();
"""


def _optimize_table(opts: dict, ranked: set | None = None) -> str:
    rows = sorted(opts.items(), key=lambda kv: -(kv[1]["summary"]["llmbox"]["decode"] / kv[1]["summary"]["stock"]["decode"]))
    if not rows:
        return ""
    import statistics as _st
    gains = [o["summary"]["llmbox"]["decode"] / o["summary"]["stock"]["decode"] - 1 for _, o in rows]
    deep = [o["summary"]["llmbox"]["deep"] / o["summary"]["stock"]["deep"] - 1 for _, o in rows if o["summary"]["stock"].get("deep") and o["summary"]["llmbox"].get("deep")]
    link = lambda rid: f'<a href="recipe-{esc(rid)}.html">{esc(_name_of(opts[rid]))}</a>' if ranked is None or rid in ranked else esc(_name_of(opts[rid]))
    tr = "".join(f'<tr><td class="l">{link(rid)}</td><td>{o["summary"]["stock"]["decode"]:.0f}</td>'
                 f'<td>{o["summary"]["llmbox"]["decode"]:.0f}</td><td>{(o["summary"]["llmbox"]["decode"] / o["summary"]["stock"]["decode"] - 1) * 100:+.0f}%</td>'
                 f'<td>{o["summary"]["stock"].get("deep") or 0:.0f}</td><td>{o["summary"]["llmbox"].get("deep") or 0:.0f}</td>'
                 f'<td class="q">{esc(" ".join(o["summary"].get("tuned_flags") or []) or "defaults")}</td></tr>' for rid, o in rows)
    return (f"<h2 id='settings'>What the settings do</h2><p>Every model on the reference box, measured twice back to back: once the way "
            f"<code>llama-server -m model.gguf -c &lt;context&gt;</code> runs it with llama.cpp's own defaults, once with its llmbox recipe (placement of the "
            f"weights, KV cache type, speculative decoding with the model's MTP head where it has one, batch sizes, then <code>llmbox tune</code>). Same file, "
            f"same context. Median: <b>{_st.median(gains) * 100:+.0f}%</b> in a short chat"
            + (f", <b>{_st.median(deep) * 100:+.0f}%</b> at 32k" if deep else "") + ".</p>"
            f'<table class="opt"><tr><th class="l">model</th><th>stock tok/s</th><th>llmbox tok/s</th><th>gain</th><th>stock at 32k</th>'
            f"<th>llmbox at 32k</th><th class='l'>tuned</th></tr>{tr}</table>")


def method_page(ref: dict | None, opts: dict | None = None, ranked: set | None = None, ref_row: dict | None = None) -> str:
    """How the numbers are made. The figures (weights, task counts, versions, depths) come from the code."""
    from . import suite
    per = {}
    for b, _k, lvl in suite.QUICK_ITEMS:
        per.setdefault(b, []).append(lvl)
    from .suite import sessions
    counts = {"sessions": sum(1 for b, k, _l in suite.QUICK_ITEMS if b == "agentic" and k in sessions.KINDS), "agentic": len(per.get("agentic", []))}
    rows = "".join(f'<tr><td class="l"><span class="m2">{esc(TIPS[b][0].split(" ·")[0])}</span></td><td>{share(b) * 100:.0f}%</td>'
                   f'<td>{len(per.get(b, []))}</td><td class="l q">{esc(_GRADING[b].format(**counts))}</td></tr>' for b in BLOCKS)
    n = len(suite.QUICK_ITEMS)
    n6 = sum(1 for *_x, lvl in suite.QUICK_ITEMS if lvl >= 6)
    ref_name = _name_of(ref) if ref else "the frontier model"
    ref_cap = (ref_row or {}).get("capability") or (ref or {}).get("summary", {}).get("capability")
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / how scores work</div><h1>How the numbers are made</h1>
 <p class="q" style="margin-top:6px">Suite v{esc(suite.VERSION)} · content hash {esc(suite.content_hash())}</p></div></section>
<article class="doc">
<h2>The score</h2>
<p>Every model gets the same {n} tasks. A program grades each one from 0 to 100; no model grades another. The blocks are
weighted by how people use local models, and the weighted average is the <b>capability</b>.</p>
<p>The <b>score</b> is that capability as a share of what a frontier model gets on the same tasks: {esc(ref_name)}{f" scored {ref_cap:.1f}, which is 100%" if ref_cap else ""}.
It runs under the same conditions as a local model: the task's own system prompt, the same tools and the same graders.
Only the model differs.</p>

<h2>The tasks</h2>
<div class="tw"><table><tr><th class="l">BLOCK</th><th>WEIGHT</th><th>TASKS</th><th class="l">WHAT AND HOW IT IS GRADED</th></tr>{rows}</table></div>
<p>The weights are those of suite v{esc(suite.VERSION.split("-")[0])}, set for people who download and run local models, mostly developers.
Saved runs are re-weighted with them, and each task keeps the score it got.</p>
<p>Each kind of task has difficulty levels. The quick suite uses hard ones (levels 4–5), a few easier ones so that weak models
still register, and {n6} expert tasks (level 6) that local models rarely solve, so the frontier has room above them.
Tasks are generated from a seed: a new seed gives fresh tasks that test the same rules with other names, numbers and files,
so a model cannot have seen the answers. Most tasks ask several questions and give credit per question, test or constraint.</p>

<h2>How sure the numbers are</h2>
<p>A model's score is estimated from every answer it gave, in every run on these tasks, with item response theory. Each task family
(kind × level) has a measured difficulty and sharpness, calibrated on all measured models; a model has an overall level plus its own
strength or weakness per block. The score is the expected weighted result on the quick suite at that level, and the range next to it
is its 95% interval. Every further run adds answers and narrows the range.</p>
<p>A run is either <b>fixed</b> (every task family once: 40–110 minutes, depending on the model's speed) or <b>adaptive</b> (40 minutes:
after each task, the next one is the task that narrows the range most per second of this model's time, and tasks it always or never
solves are skipped). Measured on fresh tasks: six runs of one model, three of each kind, agreed within ±2.6 points.</p>
<p>Models are listed by place (1, 2, 3...). Two models are <b>measurably apart</b> when the gap between their scores is larger than the 95% margin of that gap; overlapping ranges alone do not mean a tie. A dashed line in the ranking separates groups: each group starts with the first model that is measurably worse than the top of the group above. Inside a group the order can still change with more runs.
Verdicts: 85% of the frontier or more is excellent, 70% very good, 50% good.</p>

<h2>Speed</h2>
<p>Speed is measured with one conversation at a time, the way one person uses the model: a fresh prompt of real code at about 2k, 30k
and 90k tokens, so nothing comes from the cache, with code as the answer, so speculative decoding sees realistic text.
<b>Tok/s</b> is how fast the answer is written; <b>first word</b> is how long the model reads the whole context before it starts.</p>
<p>Speed on other boxes is predicted: a token needs the active weights read once, from VRAM for what fits on the card and from system RAM for the rest,
so the time per token follows from the model file and the two memory speeds. The prediction is then scaled by what the measured run got
against the same prediction on its own box. When most of a model moves onto a bigger card, it is outside what was measured, and the page says
<i>rough estimate</i>.</p>

{_optimize_table(opts or {}, ranked)}
<h2>What a run records</h2>
<p>Every run keeps the server's exact command line and sampling defaults, the llama.cpp build, the model file's sha256, and the GPU and CPU
temperature, power and memory every five seconds. The settings are compared with the recipe: differences in speed settings keep the
recipe's score; differences in sampling, template, KV cache or model file make it a different recipe that needs its own score.</p>

<h2>Thinking</h2>
<p>Replies are capped at 32k tokens and thinking at 24k, so there is always room left for the answer. A reply cut at that limit, or
thinking that repeats itself (detected from the text, not from its length), is flagged on the run page. Flagged tasks still count as they were graded.</p>

<h2>Versions</h2>
<p>Scores compare only within one suite version. The content hash identifies the exact tasks and graders; a changed task means a new version,
and older results stay on their own version.</p>
</article>'''
    return _page(f"llmbox · how scores work (suite v{suite.VERSION})", "METHOD", body, _METHOD_CSS)


_METHOD_CSS = """
.doc{max-width:860px;margin:10px 0 0;padding:6px 22px 10px}
.doc h2{font:600 22px "IBM Plex Sans Condensed";margin:34px 0 10px}
.doc p{font-size:14px;line-height:1.75;color:var(--soft);max-width:74ch;margin-top:10px}.doc p b{color:var(--ink);font-weight:500}
.doc table{margin-top:12px}.doc td.q{font-size:12.5px;line-height:1.55;padding:12px 8px}.doc td{vertical-align:top}
"""


def build(out_dir: str, host: str = "box", suite_version: str | None = None, tier: str = "quick") -> list[str]:
    from . import suite as _s
    suite_version = suite_version or _s.VERSION
    """The whole site: home, a page per recipe, per run, hardware per recipe, compare per pair of measured recipes."""
    import json as _json
    out_dir = os.path.expanduser(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    written = [home(out_dir, host, suite_version, tier)]
    recs = load_records(host, suite_version, tier)
    local_run, ref = recs["local"], recs["ref"]
    allrecs = report.results.load_all(host)
    local = {rid: report.with_probe(rec, allrecs) for rid, rec in local_run.items()}   # speed re-measured after tune; run pages keep their own
    all_rs = report.rows(host, suite_version=suite_version, tier=tier)
    rs = [r for r in all_rs if r["host"].get("id") != "cloud" and not r.get("partial")]
    ranks = rank_ranges(rs)
    n_total = len(rs) + len([j for j in queue_state() if j["model"] not in local])
    data = shape_data(rs, host)
    opts = optimize_records(host)
    order = sorted(local, key=lambda k: (-local[k]["summary"]["capability"], k))   # ties by id: recipe pages and the home page name pairs the same way
    TAB_LINKS["COMPARE"] = "compare.html"
    def w(name, html_):
        p = os.path.join(out_dir, name)
        open(p, "w").write(html_)
        written.append(p)
    clouds = [r for r in all_rs if r["host"].get("id") == "cloud" and not r.get("partial")]
    med = {b: statistics.median(v) for b in BLOCKS if (v := [r["blocks"][b] for r in rs if r["blocks"].get(b) is not None])}
    look = {r["id"]: (family((data["recipes"].get(r["id"]) or {}).get("arch"))[1], _kind(r.get("hf_repo"))) for r in rs}
    ref_hw = data["ref"]
    ref_box = f'{ref_hw["gpu"]} + {round(ref_hw["ram"] / 1024)} GB RAM'
    from . import bench
    suite_files = [(pth, x) for pth, x in report.results.files(host) if x.get("kind") == "suite"]
    rel = {}
    for rid in order:
        rec = local[rid]
        now, avail = _model_now(host, rid)
        # the runs whose answers make up the score (report.current_pool), each with its own thinking flags
        pool = (report.ranked_now(rid, host) or {}).get("rows") or []
        made = {x.get("_run") for x in pool}
        runs = [bench.rescore(dict(x, _path=pth)) for pth, x in suite_files if (x.get("recipe") or {}).get("id") == rid and x.get("created") in made] or [local_run[rid]]
        fl = {x.get("created"): task_flags(x) for x in runs}
        rel[rid] = (sum(1 for x in pool if ((fl.get(x.get("_run")) or {}).get(x["id"]) or {}).get("cut") or ((fl.get(x.get("_run")) or {}).get(x["id"]) or {}).get("loop")), len(pool))
        counted = {c: sum(1 for x in pool if x.get("_run") == c) for c in made}
        hours = sum((x["summary"].get("wall_minutes") or 0) for x in runs) / 60
        rival = next((o for o in order if o != rid), None)
        w(f"recipe-{rid}.html", recipe_page(rid, rec, ref, {
            "rs": rs, "clouds": clouds, "ranks": ranks, "med": med, "look": look, "shape": data["recipes"].get(rid), "ref_hw": ref_hw, "ref_box": ref_box,
            "pool_rows": pool, "flags": fl, "runs": runs, "counted": counted, "solved_h": sum(x["summary"].get("solved") or 0 for x in runs) / hours if hours else 0,
            "opt": opts.get(rid), "model_now": now, "on_hf": avail, "cmp": _cmp_href(rid, rival) if rival else "#"}))
        for x in runs:
            w(f"run-{x['id'][:8]}.html", run_page(rid, x, ref, fl[x.get("created")]))
        if rid in data["recipes"]:
            w(f"hardware-{rid}.html", hardware_page(rid, rec, data["recipes"][rid], data))
    for old in os.listdir(out_dir):   # the pair pages of earlier builds: one compare page does any pair now
        if re.fullmatch(r"compare-.+-vs-.+\.html", old):
            os.remove(os.path.join(out_dir, old))
    w("compare.html", compare_app(compare_data(rs, clouds, ranks, local, look, data, rel)))
    ref_row = next((r for r in all_rs if r["host"].get("id") == "cloud" and ref and r["id"] == (ref.get("recipe") or {}).get("id")), None)
    w("method.html", method_page(ref, opts, set(local), ref_row))
    try:
        np_ = new_page(rs, data, host)
    except Exception as e:   # the list needs Hugging Face; the rest of the site must not depend on it
        np_ = None
        print(f"new.html skipped: {e}")
    if np_:
        w("new.html", np_)
    return written


_PAGES_CSS = """
.opt{border-collapse:collapse;margin:8px 0 4px;font-size:13px}.opt th,.opt td{padding:7px 12px;border-bottom:1px solid var(--line2);text-align:right}
.opt th{color:var(--muted);font-weight:500;font-size:11px;letter-spacing:.06em;text-transform:uppercase}.opt .l{text-align:left}.opt tr.me td{color:var(--amber)}.opt small{color:var(--muted);margin-left:4px}
.title,.hd{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:18px;padding:18px 22px}
.title h1,.hd h1{font:600 32px/1.1 "IBM Plex Sans Condensed";margin-top:6px}.title h1 a,.hd h1 a{color:var(--ink);border-bottom:1px dotted var(--faint)}
.title .meta{font-size:12px;color:var(--muted);margin-top:8px}
.title .acts,.hd .acts{display:flex;flex-wrap:wrap;gap:10px}
.hd .id{font-size:12px;color:var(--muted);margin-top:6px}
.tele{display:grid}.tele>div{padding:14px 16px;border-right:1px solid var(--line2)}.tele>div:last-child{border-right:0}
.tele .tv{font-size:22px;margin-top:2px}.tele .tv small{font-size:11px;color:var(--muted);margin-left:4px}
.spark{width:100%;height:40px;display:block;margin-top:6px}
.flag{font-size:10px;letter-spacing:.12em;border:1px solid var(--line);color:var(--soft);padding:1px 6px;margin-right:4px}.flag.lo{border-color:var(--red-dim);color:var(--red)}
.m2{font:600 14px "IBM Plex Sans Condensed";color:var(--ink)}
.bars{grid-template-columns:170px minmax(0,1fr) 170px}
.bars .tr{position:relative;height:26px;background:var(--line2)}.bars .tr+.tr{margin-top:4px}
.bars .tr i{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,rgba(255,176,0,.28),rgba(255,176,0,.7));box-shadow:0 0 10px rgba(255,176,0,.2)}
.bars .tr.b i{background:rgba(232,228,216,.2);box-shadow:none}
.bars .tr span{position:absolute;left:10px;top:4px;font-size:13px;white-space:nowrap}.bars .tr span em{font-style:normal;color:rgba(232,228,216,.7);margin-left:10px}
.bars.multi{grid-template-columns:150px minmax(0,1fr)}
@media (max-width:900px){.title,.hd{grid-template-columns:1fr}.tele{grid-template-columns:1fr 1fr!important}.tele>div{border-bottom:1px solid var(--line2)}
 .bars{grid-template-columns:110px minmax(0,1fr)}}
"""
_RECIPE_CSS = """
.title h1 .mk{vertical-align:2px;margin-right:4px}.title .meta a{color:var(--muted);border-bottom:1px dotted var(--faint)}
.verdict .tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))}
.verdict .tiles>div{padding:18px 22px;border-right:1px solid var(--line2);display:flex;flex-direction:column;gap:2px;min-width:0}.verdict .tiles .src{white-space:normal}.verdict .tiles>div:last-child{border-right:0}
.verdict .tiles b{font:300 40px/1.1 "IBM Plex Mono";color:var(--ink);margin:6px 0 4px}.verdict .tiles>div:first-child b{color:var(--amber)}
.verdict .tiles b small{font-size:14px;color:var(--muted);margin-left:2px}.verdict .tiles span{font-size:12.5px;color:var(--soft)}.verdict .tiles .src{margin-top:4px;font-size:11px}
.verdict .say{border-top:1px solid var(--line2);padding:14px 22px;font-size:14px;color:var(--soft)}.verdict .say .up{color:var(--amber);font-weight:500}.verdict .say .dn{color:#e0826a;font-weight:500}
.near .nr{display:grid;grid-template-columns:30px minmax(180px,1.1fr) minmax(0,2.2fr) 100px;gap:14px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line2)}
.near .nr.hd{padding:0 0 6px}.near .nr .rk{color:var(--muted);font-size:13px}.near .nr.me{background:rgba(255,176,0,.05)}.near .nr.me .m{color:var(--amber)}
.near .nr.cl{padding:5px 0}.near .nr.cl .m{font-size:14px;color:var(--muted);font-weight:500}.near .nr.cl .num{color:var(--muted);font-size:14px}
.near .m{font:600 15px "IBM Plex Sans Condensed";color:var(--ink)}.near .cmpl{font-size:12px;text-align:right}.near .q{text-align:right}
.near .fp .trk s{top:-10px;bottom:-10px}.near .nr.hd .trk{height:16px}
.yb{font-size:13px;color:var(--soft);margin:0 0 16px;padding:10px 14px;border:1px dashed var(--amber-dim)}.yb b{color:var(--ink);font-weight:500}
.rgrid{display:grid;grid-template-columns:1.3fr 1fr}.rgrid>div{padding:18px 22px}.rgrid>div:first-child{border-right:1px solid var(--line2)}
.rgrid .sc{margin-bottom:10px}.rgrid dl{display:grid;grid-template-columns:110px 1fr;row-gap:7px;font-size:13px}.rgrid dt{color:var(--muted)}.rgrid dd.val{color:var(--amber)}
.notes,.worth{padding:14px 22px 16px;border-top:1px solid var(--line2)}.notes .sc,.worth .sc{margin-bottom:8px}
.notes li{list-style:none;font-size:12.5px;color:var(--soft);padding:3px 0 3px 14px;position:relative}.notes li:before{content:"›";position:absolute;left:0;color:var(--amber)}
.worth dl{display:grid;grid-template-columns:190px 1fr;row-gap:6px;font-size:13px;margin-bottom:8px}.worth dt{color:var(--muted)}.worth b{color:var(--ink);font-weight:500}.worth em{font-style:normal;color:var(--amber);margin-left:6px}
.runs .cfg{font-size:12.5px;color:var(--soft)}.runs .rn{padding:10px 16px 14px;margin:0}
@media (max-width:1000px){.verdict .tiles{grid-template-columns:1fr 1fr}.verdict .tiles>div:nth-child(2){border-right:0}.verdict .tiles>div:nth-child(-n+2){border-bottom:1px solid var(--line2)}}
@media (max-width:760px){.near .nr{grid-template-columns:24px minmax(0,1fr) 70px;row-gap:4px}.near .nr .fp{grid-column:2/4;grid-row:2}.near .nr.hd{display:none}
 .near .nr.cl{grid-template-columns:24px minmax(0,1fr)}.near .nr.cl .fp{grid-column:2}.near .nr.cl>span:last-child{display:none}
 .rgrid{grid-template-columns:1fr}.rgrid>div:first-child{border-right:0;border-bottom:1px solid var(--line2)}.worth dl{grid-template-columns:1fr}.worth dd{margin-bottom:6px}
 .verdict .tiles b{font-size:32px}.verdict .tiles>div{padding:14px 16px}}
"""
_RUN_CSS = """
.sum{display:grid;grid-template-columns:repeat(6,minmax(0,1fr))}.sum>div{padding:16px 18px;border-right:1px solid var(--line2)}.sum>div:last-child{border-right:0}
.sum b{display:block;font:300 32px "IBM Plex Mono";color:var(--amber);line-height:1.1}.sum b.w{color:var(--ink)}.sum span{font-size:12px;color:var(--muted)}
.two{display:grid;grid-template-columns:minmax(0,420px) minmax(0,1fr);gap:22px}
.sys dl{display:grid;grid-template-columns:90px 1fr;row-gap:7px;font-size:12.5px;padding:18px 22px}.sys dt{color:var(--muted)}.sys dd{word-break:break-word}.sys dd.todo{color:var(--red)}
.argv{padding:16px 22px;font-size:12px;line-height:1.7;color:var(--soft);columns:2 260px;column-gap:28px}.argv span{display:block;word-break:break-all}
.diff{padding:10px 22px 16px;border-top:1px solid var(--line2);font-size:12.5px}
.tasks td{padding:7px 8px;font-size:12.5px}.tasks .tile{min-width:46px;font-size:13px;padding:3px 4px 2px}
.tasks tr.grp td{color:var(--muted);font-size:11px;letter-spacing:.16em;padding:16px 10px 6px;border-bottom:1px solid var(--line)}
@media (max-width:900px){.sum{grid-template-columns:1fr 1fr}.sum>div{border-bottom:1px solid var(--line2)}.two{grid-template-columns:1fr}}
"""
_HW_CSS = """
.adv{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}.adv>div{padding:20px 22px;border-right:1px solid var(--line2)}.adv>div:last-child{border-right:0}
.adv h4{font:600 16px "IBM Plex Sans Condensed"}.adv .g{font:300 34px "IBM Plex Mono";color:var(--amber);margin:6px 0 4px}.adv .g.no{color:var(--muted)}.adv p{font-size:12.5px;color:var(--soft);line-height:1.55}
.why{padding:14px 22px;font-size:12.5px;color:var(--muted);line-height:1.6;border-top:1px solid var(--line2)}
.lowc{font-size:11px;color:var(--faint)}.youb{font-size:10px;letter-spacing:.14em;color:var(--bg);background:var(--amber);padding:1px 6px;margin-left:8px}
#boxes td.l .m{font-size:15px}
@media (max-width:900px){.adv{grid-template-columns:1fr}.adv>div{border-right:0;border-bottom:1px solid var(--line2)}}
"""
_CMP_CSS = """
.pick{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:14px}
.pick select{background:#0b0c09;color:var(--ink);border:1px solid var(--line);padding:7px 10px;font:13px "IBM Plex Mono";max-width:100%}
.pick select:focus{border-color:var(--amber);outline:none}.pick .sw{padding:6px 12px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px;vertical-align:0}.dot.a{background:var(--amber)}.dot.b{border:2px solid var(--ink)}
.sides{display:grid;grid-template-columns:1fr 1fr}.side{padding:18px 22px}.side.a{border-right:1px solid var(--line2)}
.side .who{font:600 18px "IBM Plex Sans Condensed";color:var(--ink)}.side .who a{color:var(--ink)}.side .qt{font:400 12px "IBM Plex Mono";color:var(--faint);margin-left:6px}
.side .nums{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:12px}.side .nums b{display:block;font:300 28px "IBM Plex Mono";color:var(--ink)}
.side.a .nums>div:first-child b{color:var(--amber)}.side .nums span{font-size:11.5px;color:var(--muted)}
.verdict2 .say{border-top:1px solid var(--line2);padding:14px 22px;font-size:14px;line-height:1.7;color:var(--soft)}.verdict2 .say b{color:var(--ink);font-weight:500}
.dr{display:grid;grid-template-columns:minmax(120px,190px) 36px minmax(0,1fr) 36px 44px;gap:12px;align-items:center;padding:5px 0}
.dr.hd{padding:0 0 4px}.dr.hd .dt{height:16px}.dr .dl{font-size:12.5px;color:var(--soft)}.dr.g{margin-top:10px;border-top:1px solid var(--line2);padding-top:12px}.dr.g .dl{font:600 15px "IBM Plex Sans Condensed";color:var(--ink)}
.dr.top .dl{font:600 15px "IBM Plex Sans Condensed";color:var(--amber)}.dr.k .dl{padding-left:14px;color:var(--muted)}
.dr .dv{font:500 14px "IBM Plex Mono";text-align:center;color:var(--ink)}.dr.k .dv{font-size:13px;color:var(--soft)}
.dr .dt{position:relative;height:14px;background:linear-gradient(var(--line),var(--line)) 0 50%/100% 1px no-repeat}.dr.hd .dt{background:none}
.dr .dt i{position:absolute;top:2px;width:10px;height:10px;margin-left:-5px;border-radius:50%;z-index:2}.dr .dt i.a{background:var(--amber)}.dr .dt i.b{border:2px solid var(--ink);background:var(--bg);z-index:1}
.dr .dt em{position:absolute;top:6px;height:2px;background:rgba(255,176,0,.35)}
.dr .dd{font-size:12.5px;color:var(--faint);text-align:right}.dr .dd.wa{color:var(--amber)}.dr .dd.wb{color:var(--ink)}
.two2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px}
.sd{display:grid;grid-template-columns:130px minmax(0,1fr);gap:14px;align-items:center;margin-bottom:12px}.sd .k{font:600 14px "IBM Plex Sans Condensed"}.sd .k small{display:block;font:400 11px "IBM Plex Mono";color:var(--muted)}
.sb{position:relative;height:22px;background:var(--line2)}.sb+.sb{margin-top:3px}.sb i{position:absolute;left:0;top:0;bottom:0}.sb.a i{background:rgba(255,176,0,.55)}.sb.b i{background:rgba(232,228,216,.22)}
.sb span{position:absolute;left:8px;top:2px;font-size:12px;white-space:nowrap}
.only{display:grid;grid-template-columns:1fr 1fr;gap:18px}.only .sc{margin-bottom:8px;text-transform:none;letter-spacing:0;font-size:12.5px;color:var(--soft)}
.only li{list-style:none;font-size:12.5px;color:var(--soft);padding:3px 0}
@media (max-width:900px){.two2,.only,.sides{grid-template-columns:1fr}.side.a{border-right:0;border-bottom:1px solid var(--line2)}.side .nums{grid-template-columns:1fr 1fr}
 .dr{grid-template-columns:minmax(0,1fr) 30px minmax(0,1.3fr) 30px 36px;gap:8px}.pick select{width:100%}}
"""
_HW_JS = r"""
const $ = s => document.querySelector(s);
const saved = savedBox(DATA);
const box = saved || { name: "reference box", gpu: DATA.ref.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: DATA.ref.ram, rambw: DATA.ref.rambw };
if (saved) $("#advbox").textContent = `What would make your box faster · ${boxLabel(box)}`;
const cur = forBox(DATA.sh, box);
const low = hw => forBox(DATA.sh, hw).gf > 0.6;   // most experts on the GPU: outside the measured regime
function card(title, hw, note) {
  const f = forBox(DATA.sh, hw), g = f.t2 / cur.t2;
  const txt = g > 1.05 ? `+${Math.round((g - 1) * 100)}%` : g < 0.95 ? `${Math.round((g - 1) * 100)}%` : "±0%";
  return `<div><h4>${title}</h4><div class="g ${Math.abs(g - 1) < 0.05 ? "no" : ""}">${txt}</div><p>~${Math.round(f.t2)} tok/s. ${note}${low(hw) ? ' <span class="lowc">rough estimate</span>' : ""}</p></div>`;
}
const faster = DATA.ramKinds.map(r => r[1]).filter(v => v >= box.rambw * 1.2)[0];   // a step worth buying
const gp = n => { const g = DATA.gpus.find(x => x[0].startsWith(n)); return { gpu: n, vram: g[1], vrambw: g[2], ram: box.ram, rambw: box.rambw }; };
$("#adv").innerHTML = (faster ? card(`Faster RAM (${faster} GB/s)`, Object.assign({}, box, { rambw: faster }), "Same card, faster memory.") : "<div><h4>Faster RAM</h4><p class='q'>already at the fastest common speed</p></div>")
  + card("A 16 GB card (RTX 5070 Ti)", gp("RTX 5070 Ti"), "More experts fit on the GPU.")
  + card("A 24 GB card (RTX 4090)", gp("RTX 4090"), "Most experts on the GPU.");
// one row per GPU, all with the visitor's RAM (or 64 GB at the reference box's RAM speed)
const ram = saved ? saved.ram : 65536, rambw = saved ? saved.rambw : DATA.ref.rambw;
$("#boxlbl").textContent = `Boxes · each with ${Math.round(ram / 1024)} GB RAM at ${rambw} GB/s${saved ? " (your RAM)" : ""}`;
const m = DATA.measured, rows = [];
rows.push({ name: `${m.gpu} · ${Math.round(m.ram / 1024)} GB · ${m.rambw} GB/s`, measured: true, t2: m.t2, td: m.td, f: forBox(DATA.sh, { gpu: m.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: m.ram, rambw: m.rambw }) });
for (const g of DATA.gpus) {
  const hw = boxFrom(g, g[3] === "mac" ? g[4] : ram / 1024, rambw), f = forBox(DATA.sh, hw);
  rows.push({ name: g[3] === "mac" ? `${g[0]} · ${g[4]} GB` : g[0], measured: false, t2: f.t2, td: f.td, f, low: hw.mac || low(hw), mine: saved && saved.name === g[0] });
}
rows.sort((a, b) => b.t2 - a.t2);
const T = (v, pred) => `<span class="tile ${v >= 50 ? "hi" : v >= 25 ? "mid" : "lo"}${pred ? " pred" : ""}">${pred ? "~" : ""}${Math.round(v)}</span>`;
$("#boxes").innerHTML = `<tr><th class="l">BOX</th><th>TOK/S<br><span class="faint">short chat</span></th><th>TOK/S<br><span class="faint">long context</span></th><th>EXPERTS<br><span class="faint">on the GPU</span></th><th>CONTEXT</th><th class="l"></th></tr>` +
  rows.map(r => `<tr class="${r.measured || r.mine ? "sel" : ""}"><td class="l"><span class="m">${r.name}</span>${r.measured ? ' <span class="youb">MEASURED</span>' : r.mine ? ' <span class="youb">YOUR BOX</span>' : ""}</td>` +
    `<td>${T(r.t2, !r.measured)}</td><td>${r.td ? T(r.td, !r.measured) : "—"}</td><td>${Math.round(r.f.gf * 100)}%</td><td>${r.f.fits ? "✓ " + Math.round(r.f.ctx / 1024) + "k" : "✗"}</td>` +
    `<td class="l">${r.measured ? '<span class="q">1 run</span>' : r.low ? '<span class="lowc">rough estimate</span>' : '<span class="q">predicted</span>'}</td></tr>`).join("");
"""
