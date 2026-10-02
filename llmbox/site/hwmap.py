"""The hardware map on the home page (design review 2026-10-02, decisions 1, 4, 5, 6, 9, 12, 13, 17): one cell per
picker entry, from the board (board.py), between "best for your box" and the chart.

  cards:  rows by VRAM tier (8 · 10–12 · 16 · 20–24 · 32+), NVIDIA then AMD in the picker's order
  Macs:   a chip matrix, M1-M5 x base / Pro / Max / Max with more GPU cores / Ultra; Ryzen AI Max its own row
  a cell: name; the best model at the picker's start values; a tick per machine (up to 10, then +N) or "be the first";
          the number, "63" measured in ink or "~52" predicted in grey. A solid frame: measured on this card; dashed:
          nobody yet; none: cannot be timed yet (AMD, after launch)
  phone:  each tier lists its measured entries as rows; the rest fold into one "be the first" line
map.js marks the visitor's own cell (amber, YOU; only for a box really picked), points "RSS for your card" at its
feed, pins it on top on a phone, and drops NEW tags older than a day."""
from __future__ import annotations

import re
import time

from .. import hwclass
from .hw import number
from .words import esc

TIERS = (("8 GB", 0, 9), ("10–12 GB", 9, 13), ("16 GB", 13, 17), ("20–24 GB", 17, 25), ("32 GB and more", 25, 10**3))
MAC_COLS = ("base", "Pro", "Max", "Max, more GPU cores", "Ultra")


def _vram_gb(name: str) -> int:
    m = re.search(r"(\d+) GB$", name)
    return int(m.group(1)) if m else 0


def _mac_place(name: str) -> tuple[int, int]:
    """(row, column) of a Mac in the chip matrix: M1..M5; base, Pro, Max, the Max with more GPU cores, Ultra."""
    m = re.match(r"Mac M(\d)(?: (Pro|Max|Ultra))?(?: (\d+)-core GPU)?$", name)
    gen, tier, cores = int(m.group(1)), m.group(2), m.group(3)
    col = {None: 0, "Pro": 1, "Ultra": 4}.get(tier, 2)
    if tier == "Max" and cores and int(cores) >= 40:
        col = 3
    return gen - 1, col


def cell(name: str, ent: dict, users: dict, now: float) -> str:
    best, testable, n = ent.get("best"), ent["testable"], ent["machines"]
    num, measured = number(best)
    kind = "m" if n else "u" if testable else "x"
    model = best["name"] if best else "nothing fits at 64 GB"
    # bottom left says what the number is (measured, by how many machines, or predicted); bottom right the speed with its unit
    if not testable:
        ticks = '<span class="tk">AMD: later</span>'   # timing on AMD comes after launch (the legend says so)
        said = f"AMD timing comes after launch; best model {model}, predicted roughly {num.lstrip('~')} tokens per second" if best else "AMD timing comes after launch"
    elif n:
        ticks = f'<span class="tk" aria-hidden="true">measured{f" ×{n}" if n > 1 else ""}</span>'
        said = (f"measured on {n} machine{'s' if n > 1 else ''}, best model {model} at {num.lstrip('~')} tokens per second" + (" (predicted)" if not measured else "")) if best else f"measured on {n} machines"
    else:
        ticks = '<span class="tk" aria-hidden="true">predicted</span>'
        said = f"best model {model} predicted about {num.lstrip('~')} tokens per second, nobody has measured this {'Mac' if ent['kind'] == 'mac' else 'card'} yet" if best else "nobody has measured it yet"
    new = ""
    try:
        first = time.mktime(time.strptime((ent.get("first_at") or "")[:19], "%Y-%m-%dT%H:%M:%S"))
        if n and now - first < 86400:
            new = f'<span class="new" data-at="{esc(ent["first_at"][:19])}">NEW</span>'
    except ValueError:
        pass
    cr = ent.get("credit")
    title = ""
    if cr:
        u = users.get(cr["user"]) or {}
        title = f' title="first: {esc("@" + u["login"] if u.get("public") and u.get("login") else cr["user"])}"'
    return (f'<a class="cell {kind}" href="hw-{ent["slug"]}.html" data-e="{esc(name)}" data-slug="{ent["slug"]}" aria-label="{esc(name)}: {esc(said)}"{title}>'
            f'<b class="nm">{esc(name)}</b>{new}<span class="md" title="{esc(model)}">{esc(model)}</span>{ticks}'
            f'<span class="n{"" if measured else " pred"}">{esc(num)}{"<small> tok/s</small>" if best else ""}</span></a>')


def _fold(items: list[tuple[str, dict]]) -> str:
    """The phone's one line for a tier's unmeasured entries."""
    be = [(n, e) for n, e in items if not e["machines"] and e["testable"]]
    amd = [(n, e) for n, e in items if not e["testable"]]
    out = ""
    if be:
        out += '<p class="fold">not measured yet, predicted tok/s: ' + " · ".join(f'<a href="hw-{e["slug"]}.html">{esc(n)} {esc(number(e.get("best"))[0])}</a>' for n, e in be) + "</p>"
    if amd:
        out += '<p class="fold">AMD, measured after launch, predicted tok/s: ' + " · ".join(f'<a href="hw-{e["slug"]}.html">{esc(n)} {esc(number(e.get("best"))[0])}</a>' for n, e in amd) + "</p>"
    return out


def map_panel(board: dict, users: dict, now: float | None = None) -> str:
    now = now or time.time()
    ents = board["entries"]
    order = [g for g in hwclass.SLUGS]   # the picker's order: NVIDIA by series, then AMD, then Macs, then Ryzen AI Max
    measured = sum(1 for e in ents.values() if e["machines"])
    later = sum(1 for e in ents.values() if not e["testable"])
    tiers = []
    for label, lo, hi in TIERS:
        items = [(n, ents[n]) for n in order if ents[n]["kind"] in ("card", "amd") and lo <= _vram_gb(n) < hi]
        lis = "".join(f'<li class="{ "m" if e["machines"] else "u" if e["testable"] else "x"}">{cell(n, e, users, now)}</li>' for n, e in items)
        tiers.append(f'<div class="tier"><h3 class="sc">{label}</h3><ul class="cells">{lis}</ul>{_fold(items)}</div>')
    macs = [(n, ents[n]) for n in order if ents[n]["kind"] == "mac"]
    lis = "".join(f'<li class="{"m" if e["machines"] else "u"}" style="grid-row:{_mac_place(n)[0] + 2};grid-column:{_mac_place(n)[1] + 2}">{cell(n, e, users, now)}</li>' for n, e in macs)
    heads = "".join(f'<li class="ch" aria-hidden="true" style="grid-row:1;grid-column:{i + 2}">{c}</li>' for i, c in enumerate(MAC_COLS))
    gens = max(_mac_place(n)[0] for n, _e in macs) + 1   # M1 .. the newest chip in the picker
    rows = "".join(f'<li class="rh" aria-hidden="true" style="grid-row:{g + 2};grid-column:1">M{g + 1}</li>' for g in range(gens))
    tiers.append(f'<div class="tier mac"><h3 class="sc">Mac</h3><ul class="cells chips">{heads}{rows}{lis}</ul>{_fold(macs)}</div>')
    apus = [(n, ents[n]) for n in order if ents[n]["kind"] == "chip"]
    lis = "".join(f'<li class="x">{cell(n, e, users, now)}</li>' for n, e in apus)
    tiers.append(f'<div class="tier"><h3 class="sc">Ryzen AI Max</h3><ul class="cells">{lis}</ul>{_fold(apus)}</div>')
    other = ""
    if board.get("other"):
        o = board["other"]
        links = " · ".join(f'<a href="r/{esc(x["newest"])}.html">{esc(x["label"])}</a> · {x["machines"]}' for x in o[:12] if x.get("newest"))
        other = (f'<div class="other"><span class="sc">Other machines</span> {links}{f" · +{len(o) - 12} more" if len(o) > 12 else ""}'
                 f'<p class="q">Not in the picker yet, so not counted in the {len(ents)}.</p></div>')
    return f'''<section class="panel mapp" id="map" aria-labelledby="maph"><div class="lbl" id="maph">Hardware map</div>
 <div class="mhead"><span><b>{measured}</b> of {len(ents)} measured · each new machine fills a cell: <code id="mapcmd">$ llmbox test</code>
  <button class="btn" data-copy-from="mapcmd">COPY</button></span>
  <span class="q">{later} AMD entries open after launch</span><a id="maprss" href="#box">RSS for your card</a></div>
 <p class="mlegend">Each cell: a graphics card or Mac, the best model for it, and how fast that model writes, in tokens per second (about ¾ of a
 word each; 20 reads comfortably, 50+ feels instant). <span class="fr m"></span> <b>measured</b>: someone ran it on that hardware (×3: on three machines) ·
 <span class="fr u"></span> <b>predicted</b> <span class="n pred">~</span>: worked out from the hardware until someone measures it ·
 <span class="fr x"></span> <b>AMD: later</b>: measuring on AMD opens after launch. Best = the highest score that fits with 64 GB of RAM (a Mac: its memory nearest 64).</p>
 <p class="q mapwho" id="mapwho"></p>
 <div id="mapyou" class="mapyou" hidden></div>
 {"".join(tiers)}
 {other}</section>'''


def hardware_page(board: dict, users: dict, now: float | None = None, latest: str = "") -> str:
    """hardware.html: the map on its own page, then the latest results."""
    from .layout import _page
    body = ('<section class="panel hd"><div><h1>What runs on each graphics card and Mac</h1>'
            '<p class="lede">Find your graphics card or Mac below. Its cell shows the best local AI model for it and how fast that '
            'model writes there. Click the cell for every model on that hardware.</p></div></section>'
            + map_panel(board, users, now) + latest)
    return _page("llmbox · hardware: what runs on each graphics card and Mac", "HARDWARE", body, ("pages.css", "map.css"), ("map.js",),
                 about="The best local AI model for every graphics card and Mac, measured by people with one or predicted.")
