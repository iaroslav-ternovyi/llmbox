"""The llmbox standard suite: generated, objectively graded tasks weighted by how people actually use local LLMs.

Weights come from usage studies (2026-09): coding agents and agents/automation dominate local use, then documents/RAG,
writing and practical reasoning. v0 covers coding with single-file tasks; the long agentic coding block replaces it
when ready (same weight).

Every task kind has 5 difficulty levels; tiers mix levels so strong models do not hit the ceiling (v0.1 without levels:
Tiel scored 100/100; v0.4 quick 96.3). v0.5: strict pass/fail grading, quick = levels 3+5, agentic at level 5 (Tiel 87.5: writing, reasoning and tools
still 100). v0.6: implement-from-spec agentic projects, dunning/reconcile tools, minutes/i18n/proofread writing,
schedule/budget reasoning; the quick tier keeps only the kinds that still discriminate.
Target: a strong 35B-A3B local model scores ~50-60, leaving room above and below.
"""
from __future__ import annotations

from . import agentic, code, explain, knowledge, longctx, reasoning, sessions, techhelp, tools, writing
from .common import Item

VERSION = "0.11-dev3"
# v0.11: tools dedupe / bulk_discount graded by level with the discount policy part of the job (v0.10 hid it: only the
# frontier guessed it, and 15% of the score hung on one yes/no task), levels 7-8 (headroom above the frontier reference);
# answers to unchanged task families count across versions (llmbox/famfp.py), so only changed families need new runs.
# v0.10: weights for people who download and run local models (developers and enthusiasts; docs/usage-research.md):
# coding 30 (agentic 20 + single-file functions 10), agents/tools 15, tech help for their own machines 15 ("homelab" is the
# third-largest local use), knowledge and "I don't know" 10 and explanations 10 (information seeking and advice are the
# largest uses of assistants: a confident invented answer and an explanation nobody can act on are the costly failures),
# writing/editing/translation 10, long documents 5 (real prompts are ~6k tokens; 200k documents are rare and the costliest
# to prefill locally), exact reasoning 5.
# v0.9 was agentic 25, code 10, tools 20, longctx 15, writing 15, reasoning 15.
WEIGHTS = {"agentic": 0.20, "code": 0.10, "tools": 0.15, "techhelp": 0.15, "knowledge": 0.10, "explain": 0.10, "longctx": 0.05,
           "writing": 0.10, "reasoning": 0.05}
MODULES = {"agentic": agentic, "code": code, "tools": tools, "techhelp": techhelp, "knowledge": knowledge, "explain": explain,
           "longctx": longctx, "writing": writing, "reasoning": reasoning}
BLOCKS = {b: m.KINDS for b, m in MODULES.items()}
BLOCKS["agentic"] = {**agentic.KINDS, **sessions.KINDS}   # multi-turn sessions are agentic coding too
# ladder: one item at every level - where a model breaks, and whether a new block's levels are spaced right
LEVELS = {"quick": [3, 5], "medium": [2, 3, 4, 4, 5, 5, 5], "deep": [2, 3, 4, 4, 5, 5, 5] * 2, "ladder": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}
# agentic tasks take minutes each: fewer, but spread over all projects
AGENTIC_LEVELS = {"quick": [5], "medium": [4, 5], "deep": [3, 4, 5, 5], "ladder": [3, 4, 5]}
# which agentic projects each tier uses: from-scratch implementations discriminate best, bug hunts add breadth
# v0.7: half of the quick agentic items are multi-turn sessions (how people actually work with a coding assistant)
AGENTIC_PROJECTS = {"quick": ["tmpl", "shipping", "spend", "fetch", "cart", "todo"], "medium": None, "deep": None, "ladder": None}


def _level_for(block: str, kind: str, level: int) -> int:
    if block == "code":  # simple single-function kinds cover levels 1-2, stateful/parsing kinds 3-5
        return min(level, 2) if kind in code.SIMPLE else max(level, 3)
    return level


# v0.8 quick = hand-picked from the v0.7 item data (Tiel, Occamy, Nex + Opus 5.5 reference, 2026-09-26): the items that
# separated the local models, one easy anchor per block (so weak models still register) and a few items every local model
# failed but the frontier solved (headroom). 35 of the 54 v0.7 quick items gave all three local models the same score.
# Recalibrate with IRT once more models have run (tinyBenchmarks / metabench / MINCE style item selection).
# v0.10-dev4 quick: one run in ~40-45 min on a 60 tok/s thinking model (v0.9 quick took 1.5-3 h). Picked from the v0.9 item data
# (9 models) by information per minute: out went items every model solves (code.expr/csv L3, agentic.fetch, writing.proofread,
# techhelp L4), items every local model fails at 6 min each (longctx.audit L6, tools.outreach L6) and the slowest ones
# (agentic.shipping 10 min, code.cron 8 min, reasoning.schedule 8 min, longctx L5 at 165k tokens). The four 0/1 blocks
# now give partial credit (longctx and reasoning: 2-4 questions per item; code: share of hidden tests; tools: per action).
# dev4, after the first dev3 run (Tiel, 61 min of items): out longctx.latest (7.5 min) and tools.dunning (7.3 min),
# reasoning.code_trace L4 -> L3 (4.4 min), techhelp.log_root L6 -> L5.
# dev5, after three dev4 runs of Tiel (79.8 / 87.8 / 84.3; techhelp 57 / 83 / 67 on six 0/1 answers): techhelp asks 3-5
# questions per machine, writing gives credit per constraint, longctx asks 5 questions per item, knowledge gets a 4th item.
# dev6: explain quizzes ask 8 questions (4-5 before); agentic = the cart session + three bug-fix projects at level 3 (4-5
# seeded bugs each, a different combination per seed) instead of the spend session (10 min for Tiel, always solved by it;
# two fixed sessions were 2 items for 20% of the score). Explain at level 4: with the reference explanation the reader
# answers 0.96-1.0 there, 0.83-0.88 at level 5 (its own mistakes on the most tangled rules would be noise in every score).
# dev7: billing asks for the parts of a bill in turn; the reader answers 4 questions per call and says UNKNOWN when the
# explanation lacks a fact (it spent 16k tokens guessing a missing plan fee); access out of quick - with the reference
# explanation the reader still misses 1-2 of 8 "share" cases (medium / deep keep it); techhelp / knowledge / explain
# replies may use 32k tokens (16k was below Tiel's 24k reasoning budget and cut a knowledge answer to nothing).
# Generators test a fixed mix of rules per level (a seed changes names, numbers and values): techhelp compose/routing/
# chmod, agentic bugs one per module, knowledge exactly 2 made-up questions - a random mix made items score 0 / 0 / 1.
QUICK_ITEMS = [
    ("agentic", "cart", 5), ("agentic", "ledger", 3), ("agentic", "inventory", 3), ("agentic", "shipping", 3),
    ("code", "rooms", 5), ("code", "lru", 5), ("code", "csv", 5),
    ("tools", "conditional", 5), ("tools", "dedupe", 5), ("tools", "bulk_discount", 5), ("tools", "bulk_discount", 7),   # v0.11
    ("techhelp", "compose_port", 5), ("techhelp", "compose_port", 6), ("techhelp", "nginx_route", 6), ("techhelp", "log_root", 5),
    ("techhelp", "subnet", 6), ("techhelp", "chmod_seq", 5),
    ("knowledge", "python", 5), ("knowledge", "shell", 5), ("knowledge", "codes", 5), ("knowledge", "codes", 6),   # 8 questions each, 2 made up
    ("explain", "config", 4), ("explain", "billing", 4), ("explain", "billing", 3),     # graded by the reader model (8 questions)
    ("longctx", "lookup", 3), ("longctx", "multihop", 3),     # one ~80k-token document, 5 questions each
    ("writing", "constrained", 5), ("writing", "rewrite", 5), ("writing", "minutes", 5), ("writing", "i18n", 5),
    ("reasoning", "arith", 5), ("reasoning", "code_trace", 3),
]


def build(tier: str = "quick", seed0: int = 0, blocks: list[str] | None = None) -> list[Item]:
    """Deterministic item list for (tier, seed0). A different seed0 gives fresh items of the same difficulty."""
    if tier == "quick":
        items = [BLOCKS[b][k](seed0 * 1000 + 1, _level_for(b, k, lvl)) for b, k, lvl in QUICK_ITEMS if not blocks or b in blocks]
        lc = sorted((it for it in items if it.block == "longctx"), key=lambda it: it.meta.get("level", 0))
        it_lc = iter(lc)
        return [next(it_lc) if it.block == "longctx" else it for it in items]
    items = []
    for b, kinds in BLOCKS.items():
        if blocks and b not in blocks:
            continue
        for kind, gen in kinds.items():
            if b == "agentic" and AGENTIC_PROJECTS[tier] and kind not in AGENTIC_PROJECTS[tier]:
                continue
            if tier == "quick" and b != "agentic" and kind not in getattr(MODULES[b], "QUICK", kinds):
                continue   # blocks can name the kinds that still discriminate; the rest stay in medium / deep for breadth
            for i, lvl in enumerate(AGENTIC_LEVELS[tier] if b == "agentic" else LEVELS[tier]):
                if b == "code" and kind in code.SIMPLE and lvl >= 3:
                    continue   # trivial single functions only belong to the lowest levels
                if lvl > getattr(MODULES[b], "MAX_LEVEL", 5) and tier == "ladder":
                    continue   # the ladder climbs only as high as a block has levels
                items.append(gen(seed0 * 1000 + i + 1, _level_for(b, kind, lvl)))
    # questions on the same long document back to back, so the prompt cache holds it (v0.8: one document per level)
    lc = sorted((it for it in items if it.block == "longctx"), key=lambda it: (it.meta.get("level", 0), it.id.split(".")[-1]))
    it_lc = iter(lc)
    return [next(it_lc) if it.block == "longctx" else it for it in items]


def max_level(block: str, kind: str) -> int:
    """The highest level a kind generates: the module's MAX_LEVEL (5 by default); a bug-fix project as far as its
    bugs_per_level table goes; trivial code kinds stop at 2."""
    if block == "code" and kind in code.SIMPLE:
        return 2
    if block == "agentic":
        meta = getattr(BLOCKS[block][kind], "__closure__", None) and next(
            (c.cell_contents for c in BLOCKS[block][kind].__closure__ if isinstance(c.cell_contents, dict) and "prompt" in c.cell_contents), None)
        if meta and meta.get("bugs_per_level"):
            return len(meta["bugs_per_level"])
        return 5
    return getattr(MODULES[block], "MAX_LEVEL", 5)


def families(kinds: set | None = None) -> list[str]:
    """Every task family (block.kind.Llevel) the suite can generate; with kinds: only of those block.kind pairs."""
    out = []
    for b, ks in BLOCKS.items():
        for k in ks:
            if kinds is not None and f"{b}.{k}" not in kinds:
                continue
            out += [f"{b}.{k}.L{lv}" for lv in range(1, max_level(b, k) + 1)]
    return out


def content_hash() -> str:
    """sha256 (12 hex) over all suite sources, projects and sessions: identifies the exact tasks + graders of a result."""
    import hashlib
    import os
    root = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    for dp, dns, fns in sorted(os.walk(root)):
        dns[:] = sorted(d for d in dns if d != "__pycache__")
        for fn in sorted(fns):
            if fn.endswith((".pyc", ".tgz")):
                continue
            path = os.path.join(dp, fn)
            h.update(os.path.relpath(path, root).encode())
            h.update(open(path, "rb").read())
    return h.hexdigest()[:12]
