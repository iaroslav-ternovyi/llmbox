"""Content-based reasoning-loop detector (same algorithm as the llama.cpp patch `--reasoning-loop`, common/reasoning-loop.h).

Units are lines and sentences. A unit loops when it nearly repeats (word-bigram Jaccard >= 0.5) at least `repeats`
earlier units of the same block; the block loops when >= `threshold` of the last `window` units loop. Needing several
repeats separates a loop from a re-draft (a file or letter written out again once or twice is legal). Calibrated on 1816
thinking blocks of 6 models: window 24 / threshold 0.75 / repeats 3 fire on all 9 real loops and nothing else.
"""
from __future__ import annotations

import re

WORD = re.compile(r"\w+", re.U)


def units(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def bigrams(s: str) -> frozenset:
    w = [x.lower() for x in WORD.findall(s)]
    return frozenset(zip(w, w[1:]))


def scan(text: str, window: int = 24, threshold: float = 0.75, repeats: int = 3, similarity: float = 0.5,
         history: int = 600, min_bigrams: int = 4) -> dict:
    """{'peak': max share of looping units in any window, 'fired_at': char offset of detection or None,
    'excerpt': the text just before detection}"""
    hist, flags, pos, peak, fired = [], [], 0, 0.0, None
    for s in units(text):
        pos = text.find(s, pos) + len(s)
        g = bigrams(s)
        if len(g) < min_bigrams:
            continue
        occ = 0
        for p in hist[-history:]:
            if len(g & p) / len(g | p) >= similarity:
                occ += 1
                if occ >= repeats:
                    break
        hist.append(g)
        flags.append(occ >= repeats)
        if len(flags) >= window:
            share = sum(flags[-window:]) / window
            peak = max(peak, share)
            if fired is None and share >= threshold:
                fired = pos
    return {"peak": round(peak, 2), "fired_at": fired, "excerpt": text[max(0, (fired or 0) - 400):fired] if fired else ""}
