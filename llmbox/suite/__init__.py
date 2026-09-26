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

from . import agentic, code, longctx, reasoning, sessions, tools, writing
from .common import Item

VERSION = "0.8"
# coding = long agentic tasks (25) + single-file functions (10); then agents/tools, documents, writing, reasoning
WEIGHTS = {"agentic": 0.25, "code": 0.10, "tools": 0.20, "longctx": 0.15, "writing": 0.15, "reasoning": 0.15}
MODULES = {"agentic": agentic, "code": code, "tools": tools, "longctx": longctx, "writing": writing, "reasoning": reasoning}
BLOCKS = {b: m.KINDS for b, m in MODULES.items()}
BLOCKS["agentic"] = {**agentic.KINDS, **sessions.KINDS}   # multi-turn sessions are agentic coding too
LEVELS = {"quick": [3, 5], "medium": [2, 3, 4, 4, 5, 5, 5], "deep": [2, 3, 4, 4, 5, 5, 5] * 2}
# agentic tasks take minutes each: fewer, but spread over all projects
AGENTIC_LEVELS = {"quick": [5], "medium": [4, 5], "deep": [3, 4, 5, 5]}
# which agentic projects each tier uses: from-scratch implementations discriminate best, bug hunts add breadth
# v0.7: half of the quick agentic items are multi-turn sessions (how people actually work with a coding assistant)
AGENTIC_PROJECTS = {"quick": ["tmpl", "shipping", "spend", "fetch", "cart", "todo"], "medium": None, "deep": None}


def _level_for(block: str, kind: str, level: int) -> int:
    if block == "code":  # simple single-function kinds cover levels 1-2, stateful/parsing kinds 3-5
        return min(level, 2) if kind in code.SIMPLE else max(level, 3)
    return level


# v0.8 quick = hand-picked from the v0.7 item data (Tiel, Occamy, Nex + Opus 5.5 reference, 2026-09-26): the items that
# separated the local models, one easy anchor per block (so weak models still register) and a few items every local model
# failed but the frontier solved (headroom). 35 of the 54 v0.7 quick items gave all three local models the same score.
# Recalibrate with IRT once more models have run (tinyBenchmarks / metabench / MINCE style item selection).
QUICK_ITEMS = [
    ("agentic", "shipping", 5), ("agentic", "spend", 5), ("agentic", "fetch", 5), ("agentic", "cart", 5),
    ("code", "expr", 3), ("code", "rooms", 5), ("code", "lru", 5), ("code", "csv", 3),
    ("tools", "total", 5), ("tools", "conditional", 5), ("tools", "dunning", 5), ("tools", "reconcile", 5), ("tools", "reminders", 3),
    ("longctx", "lookup", 5), ("longctx", "multihop", 5), ("longctx", "latest", 5), ("longctx", "count", 5),
    ("writing", "constrained", 5), ("writing", "rewrite", 5), ("writing", "proofread", 5), ("writing", "minutes", 5), ("writing", "i18n", 5),
    ("reasoning", "arith", 5), ("reasoning", "code_trace", 5), ("reasoning", "arith", 3), ("reasoning", "schedule", 5),
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
                items.append(gen(seed0 * 1000 + i + 1, _level_for(b, kind, lvl)))
    # questions on the same long document back to back, so the prompt cache holds it (v0.8: one document per level)
    lc = sorted((it for it in items if it.block == "longctx"), key=lambda it: (it.meta.get("level", 0), it.id.split(".")[-1]))
    it_lc = iter(lc)
    return [next(it_lc) if it.block == "longctx" else it for it in items]
