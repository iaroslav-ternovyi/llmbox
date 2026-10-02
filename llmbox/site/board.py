"""One table per picker entry, built once per site build: the hardware map's cells, the result cards' comparisons, the
feeds and the "first" credit all read it, so they cannot disagree.

  speed records (the reference box's and people's) -> hwclass.display_class -> one of the 65 entries, or "other"
     |-> sets:   (recipe, comparison class) -> each machine's median (a flagged outlier is left out, the box is in)
     |-> credit: per entry, the first signed-in run by when it was received (never the box's, never an outlier);
     |           FIRST USER ON THIS CARD where the box measured the entry before anyone
     |-> cells:  per entry, the pick rule's best model at the picker's start values (64 GB, the default RAM speed; a
     |           Mac the size nearest 64 it is sold with); its median from the entry's comparison class with the most
     |           machines, else its prediction (~)
     |-> other:  classes outside the picker (several cards, no card, an unlisted one): machines and newest run
     '-> events: what the feeds say - a model measured on an entry for the first time, a new best, the feed's start;
                 each written once to <HOME>/feeds/events.jsonl and never changed
"""
from __future__ import annotations

import json
import os
import statistics as st
import time

from .. import hwclass, pick
from ..hosts import HOME

EVENTS = os.path.join(HOME, "feeds", "events.jsonl")


def _speed_rows(records: list[dict], ref: bool) -> list[dict]:
    """The speed records that count (kind speed, a decode figure, not a variant of its recipe), flattened."""
    out = []
    for rec in records:
        sp = rec.get("speed") or {}
        if rec.get("kind") != "speed" or not sp.get("decode_tps") or rec.get("overrides"):
            continue
        host, sub = rec.get("host") or {}, rec.get("submission") or {}
        cls = host.get("class") or hwclass.of_host(host)
        out.append({"rid": (rec.get("recipe") or {}).get("id"), "cls": cls, "entry": hwclass.display_class(cls), "machine": host.get("id") or "?",
                    "t2": sp["decode_tps"], "deep": _at(sp, 24000, 48000), "ref": ref, "sid": sub.get("id"), "user": sub.get("user"),
                    "outlier": "outlier" in (sub.get("flags") or []), "at": sub.get("received") or rec.get("created") or ""})
    return out


def _at(sp: dict, lo: int, hi: int) -> float | None:
    d = [x for x in sp.get("depth") or [] if x.get("decode_tps") and lo <= (x.get("depth") or 0) < hi]
    return min(d, key=lambda x: abs(x["depth"] - (lo + hi) / 2))["decode_tps"] if d else None


def build(box_records: list[dict], community_records: list[dict], models: list[tuple], start_bw: float = 60.0) -> dict:
    """The board: {"entries": {name: ...}, "sets": {(rid, cls): ...}, "runs": {sid: ...}, "other": [...]}.
    models: (model list entry, recipe, shape) for every model the site lists, as pick.rank_rows takes them."""
    rows = _speed_rows(box_records, True) + _speed_rows(community_records, False)
    sets: dict = {}
    for r in rows:
        if r["outlier"] or not r["rid"]:
            continue
        sets.setdefault((r["rid"], r["cls"]), {}).setdefault(r["machine"], []).append(r["t2"])
    sets = {k: {"values": sorted(st.median(v) for v in ms.values()), "machines": len(ms)} for k, ms in sets.items()}
    for v in sets.values():
        v["median"] = round(st.median(v["values"]), 1)

    entries = {}
    for name in hwclass.SLUGS:
        mine = [r for r in rows if r["entry"] == name and not r["outlier"]]
        firsts = sorted((r for r in mine if not r["ref"] and r["user"]), key=lambda r: r["at"])
        box_first = min((r["at"] for r in mine if r["ref"]), default=None)
        holder = firsts[0] if firsts else None
        entries[name] = {"slug": hwclass.SLUGS[name], "kind": hwclass.entry_kind(name), "machines": len({r["machine"] for r in mine}),
                         "first_at": min((r["at"] for r in mine), default=None), "reference": box_first is not None,
                         "credit": {"sid": holder["sid"], "user": holder["user"], "at": holder["at"],
                                    # the box measured before launch: a record of it without a date still came first
                                    "badge": hwclass.first_badge(name, after_reference=box_first is not None and box_first <= holder["at"])} if holder else None,
                         "testable": hwclass.entry_kind(name) not in ("amd", "chip")}   # AMD timing comes after launch
        entries[name]["best"] = _best(name, models, sets, start_bw)

    runs = {}
    for r in rows:
        if r["ref"] or not r["sid"]:
            continue
        runs.setdefault(r["sid"], r)   # a submission's first speed record is its run
    other: dict = {}
    for r in rows:
        if r["entry"] is None and not r["ref"]:
            o = other.setdefault(r["cls"].split("|")[0], {"label": hwclass.label(r["cls"].split("|")[0]), "machines": set(), "newest": None, "at": ""})
            o["machines"].add(r["machine"])
            if r["at"] >= o["at"]:
                o["at"], o["newest"] = r["at"], r["sid"]
    other_list = sorted(({"label": o["label"], "machines": len(o["machines"]), "newest": o["newest"]} for o in other.values()),
                        key=lambda o: (-o["machines"], o["label"]))
    return {"entries": entries, "sets": sets, "runs": runs, "other": other_list}


def _best(name: str, models: list[tuple], sets: dict, start_bw: float) -> dict | None:
    """The entry's cell: the pick rule's best model at the picker's start values, and its number - the median of the
    entry's comparison class with the most machines for that model, else its prediction."""
    kind = hwclass.entry_kind(name)
    hw = pick.entry_spec(name, 64, start_bw)
    ranked = pick.rank_rows(hw, None, models, "all", None, kind == "mac", {"llama.cpp"})
    ok = [x for x in ranked if x["fits"] and x.get("use_score") is not None]
    if not ok:
        return None
    best, why = pick.choose(ok)
    groups = sorted(((c, v) for (rid, c), v in sets.items() if rid == best["id"] and hwclass.display_class(c) == name),
                    key=lambda cv: (-cv[1]["machines"], cv[0]))
    out = {"rid": best["id"], "name": best.get("name") or best["id"], "score": best.get("score"), "why": why,
           "predicted": round(best["t2"], 1), "measured": None, "group": None, "machines": 0}
    if groups:
        c, v = groups[0]
        out.update(measured=v["median"], group=c, group_label=hwclass.label(c), machines=v["machines"])
    return out


def place(board: dict, rid: str, cls: str, t2: float, machine: str | None = None) -> dict:
    """Where a run stands among the machines like it (same recipe, same comparison class), itself left out: M other
    machines, their speeds, and with five or more the share it is faster than."""
    s = board["sets"].get((rid, cls)) or {"values": [], "machines": 0}
    others = list(s["values"])
    if machine is not None and t2 in others:   # its own machine is in the set: take one copy of its figure out
        others.remove(t2)
    m = len(others)
    return {"others": others, "m": m, "faster_than": round(100 * sum(1 for v in others if v < t2) / m) if m >= 5 else None}


def events(board: dict, now: str | None = None, path: str = EVENTS) -> tuple[list[dict], list[dict]]:
    """(every feed event so far, the ones this build adds). An event is written once and never changed: a model
    measured on an entry for the first time, a new best for an entry, and each feed's first item. The caller appends
    the new ones after the feeds are written (append())."""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    old = []
    if os.path.exists(path):
        old = [json.loads(x) for x in open(path) if x.strip()]
    seen = {e["id"] for e in old}
    last_best = {}   # the best each feed last named: its first item's, then every "new best"
    for e in old:
        if e["kind"] in ("start", "best"):
            last_best[e["entry"]] = e.get("rid")
    new = []
    for name, ent in board["entries"].items():
        b = ent.get("best")
        if f"start/{ent['slug']}" not in seen:
            new.append({"id": f"start/{ent['slug']}", "kind": "start", "entry": name, "at": now, "testable": ent["testable"],
                        "measured": ent["machines"] > 0, "rid": b and b["rid"], "model": b and b["name"],
                        "tps": b and (b["measured"] or b["predicted"]), "predicted": not (b and b["measured"])})
        if b and name in last_best and last_best[name] != b["rid"]:
            new.append({"id": f"best/{ent['slug']}/{b['rid']}/{now[:10]}", "kind": "best", "entry": name, "at": now, "rid": b["rid"],
                        "model": b["name"], "score": b["score"], "tps": b["measured"] or b["predicted"], "predicted": not b["measured"]})
    for (rid, cls), v in sorted(board["sets"].items()):
        name = hwclass.display_class(cls)
        if name and f"measured/{hwclass.SLUGS[name]}/{rid}" not in seen and not any(e["id"] == f"measured/{hwclass.SLUGS[name]}/{rid}" for e in new):
            new.append({"id": f"measured/{hwclass.SLUGS[name]}/{rid}", "kind": "measured", "entry": name, "at": now, "rid": rid,
                        "tps": v["median"], "machines": v["machines"]})
    return old + new, new


def append(new: list[dict], path: str = EVENTS) -> None:
    if not new:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        for e in new:
            f.write(json.dumps(e) + "\n")
