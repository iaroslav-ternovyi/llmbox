"""Shared pieces of the standard suite: the Item contract, seeded randomness, answer extraction, language detection.

Every task is GENERATED from a seed: each run can get fresh instances of equal difficulty, so a model cannot have
seen them in training and a published score cannot be reproduced by memorizing answers. A (block, kind, seed)
triple always generates the same item, so any result is reproducible.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Item:
    id: str                      # "<block>.<kind>.<seed>"
    block: str                   # tools | longctx | writing | reasoning | code
    kind: str                    # task family inside the block
    messages: list[dict]         # OpenAI chat messages
    check: Callable[..., float]  # (final_text, transcript) -> score in [0, 1]
    tools: list[dict] | None = None
    tool_impl: Callable[[str, dict], object] | None = None   # executes a tool call against the item's simulated world
    max_tokens: int = 32000
    lang: str = "en"
    meta: dict = field(default_factory=dict)


def rng(block: str, kind: str, seed: int) -> random.Random:
    return random.Random(f"{block}/{kind}/{seed}")


_ANSWER = re.compile(r"ANSWER\s*[:：]\s*(.+?)\s*$", re.I | re.M)


def final_answer(text: str) -> str | None:
    """The value after the last 'ANSWER:' line (models are told to end with one)."""
    m = _ANSWER.findall(text or "")
    return m[-1].strip().strip("`*").strip() if m else None


def num(s: str | None) -> float | None:
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s.replace(" ", " "))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def strip_think(text: str) -> str:
    """Remove inline <think>...</think> if a server did not split reasoning out."""
    return re.sub(r"(?s)<think>.*?</think>", "", text or "").strip()


# Tiny stopword-based language identification: enough to tell which of these languages a text is written in.
_STOP = {
    "en": "the and of to in is that for with it on as are this be was by from have or not you".split(),
    "es": "el la de que y en los se del las por un para con una es su al lo como más pero sus".split(),
    "de": "der die und in den von zu das mit sich des auf für ist im dem nicht ein eine als auch".split(),
    "fr": "le la de et les des en un une du est que pour dans qui pas par sur au avec ce".split(),
    "ru": "и в не на что с по как это он к но из у за от то все так же для".split(),
    "it": "il di che e la per un in non una sono del è le si con da gli".split(),
    "pt": "de que o a e do da em um para com não uma os no se na por mais".split(),
}


def detect_lang(text: str) -> str:
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁäöüßéèêàçñíóúõãâ']+", (text or "").lower())
    if not words:
        return "?"
    cyr = sum(1 for w in words if re.match(r"[а-яё]", w))
    if cyr > len(words) * 0.5:
        return "ru"
    scores = {lang: sum(1 for w in words if w in set(sw)) for lang, sw in _STOP.items() if lang != "ru"}
    return max(scores, key=scores.get)


def words(text: str) -> list[str]:
    return re.findall(r"\b[\w'-]+\b", text or "")
