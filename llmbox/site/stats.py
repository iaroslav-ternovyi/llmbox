"""Places, ties and scales: when two models are measurably apart, the shared score axis."""
from __future__ import annotations

from .words import BLOCKS


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


def _stand_words(blocks: dict, med: dict, gap: float = 6) -> tuple[list, list, bool]:
    """(blocks clearly above the typical local model, blocks clearly below, behind everywhere) - two of each at most."""
    d = {b: blocks[b] - med[b] for b in BLOCKS if blocks.get(b) is not None and b in med}
    up = [b for b, v in sorted(d.items(), key=lambda x: -x[1]) if v >= gap][:2]
    dn = [b for b, v in sorted(d.items(), key=lambda x: x[1]) if v <= -gap][:2]
    return up, dn, bool(d) and all(v <= -gap for v in d.values())


def pareto(points: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Non-dominated points in (speed, quality), sorted by speed."""
    front = [p for p in points if not any(q[0] >= p[0] and q[1] >= p[1] and (q[0] > p[0] or q[1] > p[1]) for q in points)]
    return sorted(front)


def _axis_of(vss: list) -> tuple[int, int]:
    """The score scale shared by the ranking, the chart and the model pages: from just under the weakest model at >= 50%
    of the frontier to 100 (as the page script's axisOf)."""
    main = [v for v in vss if v is not None and v >= 50] or [v for v in vss if v is not None] or [50]
    lo = max(0, int((min(main) - 6) // 5 * 5))
    return lo, 10 if 100 - lo > 40 else 5


def _range_pct(r: dict) -> tuple[float, float]:
    k = (r.get("vs_ref") or 0) / r["capability"] if r.get("capability") else 0
    return r["ci"][0] * k, min(100.0, r["ci"][1] * k)
