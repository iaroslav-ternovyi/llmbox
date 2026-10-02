"""A page and a feed per picker entry: hw-<slug>.html and feeds/<slug>.xml (design review 2026-10-02, decisions 10,
12, 17 and 18). Both read the board (board.py), so the map's cell, the page and the feed say the same thing.

The page: the best model there at the picker's start values and its number, measured (the comparison group behind
it named) or predicted; the measurements per model and comparison group (RAM speed), our test PC's labelled;
the recent runs, each linking to its page; who was first; the feed; "be the first" with the commands when nobody has
measured it; AMD's "after launch" state. The feed: its first item, written once when the feed starts, then a model
measured there for the first time and every new best, from the append-only event log (board.events)."""
from __future__ import annotations

import calendar
import email.utils
import time

from .. import hwclass
from .layout import _page
from .words import CURL, esc


def _a(name: str) -> str:
    """'an RTX 3060', 'a Mac M2 Max', 'a Radeon AI PRO R9700'."""
    return ("an " if name.startswith(("RTX", "RX")) else "a ") + name


def start_values(name: str) -> str:
    """The machine a cell assumes: 64 GB of RAM at the default speed, or the memory the Mac is sold with nearest 64."""
    if hwclass.entry_kind(name) in ("mac", "chip"):
        return f"with {hwclass.mac_memory(name, 64) if hwclass.entry_kind(name) == 'mac' else 64} GB of memory"
    return "with 64 GB of DDR5-5600 RAM (60 GB/s)"


def number(best: dict | None) -> tuple[str, bool]:
    """(the cell's number as text, measured?): '63' measured, '~52' predicted."""
    if not best:
        return "", False
    if best.get("measured"):
        return f"{best['measured']:.0f}", True
    return f"~{best['predicted']:.0f}", False


def group(cls: str) -> str:
    """A comparison group without the entry's own name: 'RAM 45–65 GB/s', '38-core GPU · 32 GB'."""
    lab = hwclass.label(cls)
    return lab.split(" · ", 1)[1] if " · " in lab else lab


def _first(credit: dict | None, users: dict) -> str:
    if not credit:
        return ""
    u = users.get(credit["user"]) or {}
    who = f"@{esc(u['login'])}" if u.get("public") and u.get("login") else esc(credit["user"])
    return f'first: <a href="{esc(credit["user"])}.html">{who}</a> · {esc(credit["badge"].lower())} · <a href="r/{esc(credit["sid"])}.html">the run</a>'


def _cap(t: str) -> str:   # str.capitalize() would lower the rest ("64 gb of ddr5")
    return t[:1].upper() + t[1:]


def hw_page(name: str, board: dict, meta: dict, users: dict, site: str) -> str:
    from urllib.parse import quote
    ent = board["entries"][name]
    slug, best, testable = ent["slug"], ent.get("best"), ent["testable"]
    num, measured = number(best)
    one = f"{CURL} {site}/install.sh | sh"
    feed = f"feeds/{slug}.xml"
    # the answer: the best model here, its number, and the line that runs it
    if best:
        sc = f' · <b class="sco">{best["score"]:.0f}%</b> of Claude Opus 5.5' if best.get("score") is not None else ""
        if measured:
            n = best["machines"]
            ref_only = n == 1 and board["sets"].get((best["rid"], best["group"]), {}).get("ref")
            how = ("measured on our test PC" if ref_only else "measured on 1 machine" if n == 1 else f"the median of {n} machines") + f" ({esc(group(best['group']))})"
        elif testable:
            how = "predicted from the model file and the memory speeds; nobody has measured it here yet"
        else:
            how = "a rough prediction: there are no public runs of AMD cards to check it against yet"
        cmd = f"{CURL} {site}/install.sh | sh -s -- {best['rid']}"
        answer = (f'<p class="best">Best here: <a href="recipe-{esc(best["rid"])}.html">{esc(best["name"])}</a>{sc} · '
                  f'<b class="{"" if measured else "pred"}">{esc(num)}</b> tok/s{"" if testable or measured else ", rough"}</p>'
                  f'<p class="q">{how}; {esc(start_values(name))}. {_cap(esc(best.get("why") or ""))}.</p>'
                  f'<div class="runit"><span class="sc">Run it</span><div class="copy"><pre class="cmd" id="runcmd">{esc(cmd)}</pre>'
                  f'<button class="btn cpy" type="button" data-copy-from="runcmd">COPY</button></div>'
                  f'<p class="q">Linux or a Mac: installs llmbox and this model with its settings fitted to your computer, then starts it. '
                  f'Windows, LM Studio or Ollama: <a href="recipe-{esc(best["rid"])}.html#run">the settings for each</a>.</p></div>')
    else:
        answer = f'<p class="best">No listed model fits {esc(start_values(name))}.</p>'
    # every listed model here, best score first: measured where someone measured it, else predicted
    rows = []
    for m in ent.get("models") or []:
        t2 = m["measured"] or m["t2"]
        times = f" ×{m['machines']}" if m["machines"] > 1 else ""
        spd = (f'<b>{m["measured"]:.0f}</b> <span class="q">measured{times}</span>'
               if m["measured"] else f'<b class="pred">~{m["t2"]:.0f}</b> <span class="q">predicted</span>' if m["fits"] and m["t2"] else "—")
        slow = m["fits"] and t2 and t2 < 20
        fit = (f'✓ {round((m["ctx"] or 0) / 1024)}k' if m["fits"] else '<span class="no">✗ too big</span>')
        me = best and m["rid"] == best["rid"]
        score = f"{m['score']:.0f}%" if m["score"] is not None else "—"
        cv = (meta.get(m["rid"]) or {}).get("caveat")
        cav = f'<a class="cav" href="recipe-{esc(m["rid"])}.html" title="{esc(cv)}">⚠ flawed test</a>' if cv else ""
        rows.append(f'<tr class="{"me" if me else ""}{" nofit" if not m["fits"] else ""}"><td class="l" data-h="model"><a href="recipe-{esc(m["rid"])}.html">{esc(m["name"])}</a>'
                    f'{"<span class=tag>BEST HERE</span>" if me else ""}{cav}</td>'
                    f'<td data-h="score">{score}</td>'
                    f'<td data-h="tok/s">{spd}{" <span class=no>slow</span>" if slow else ""}</td><td data-h="fits">{fit}</td></tr>')
    models = (f'<section class="panel"><div class="lbl">Every model on {esc(_a(name))}</div><div class="tw"><table class="grp hwm"><tr><th class="l">MODEL</th>'
              f'<th>SCORE<br><span class="faint">% of Claude Opus</span></th><th>SPEED<br><span class="faint">tok/s, short chat</span></th><th>FITS<br><span class="faint">context</span></th></tr>'
              + "".join(rows) + '</table></div>'
              f'<p class="q pad0">{_cap(esc(start_values(name)))}. 20 tok/s reads comfortably; under that it is marked slow. '
              f'Other RAM? <a href="index.html#gpu={esc(quote(name))}">The Models page lists them for your exact box</a>.</p></section>') if rows else ""
    recent = sorted((r for r in board["runs"].values() if r["entry"] == name), key=lambda r: r["at"], reverse=True)[:20]
    runs = ("<section class=\"panel\"><div class=\"lbl\">Recent runs</div><ul class=\"runs\">"
            + "".join(f'<li><a href="r/{esc(r["sid"])}.html">{esc(r["at"][:10])} · {esc((meta.get(r["rid"]) or {}).get("name") or r["rid"])} · '
                      f'{r["t2"]:.0f} tok/s{" · not confirmed" if r["outlier"] else ""}</a></li>' for r in recent)
            + "</ul></section>") if recent else ""
    if not testable:
        state = (f'<section class="panel pad"><div class="lbl">AMD</div><p>Measuring on AMD comes after launch: llmbox cannot time '
                 f'{esc(_a(name))} yet, so these numbers are predictions. This page and <a href="{feed}">its feed</a> will say when it can.</p></section>')
    elif not ent["machines"]:
        badge = hwclass.first_badge(name)
        state = (f'<section class="panel pad"><div class="lbl">Own one? Measure it</div><p>Nobody has measured {esc(_a(name))} yet: the numbers above are predictions. '
                 f'About 20 minutes; sign in with GitHub when llmbox asks and your result card says <b>{esc(badge)}</b>.</p>'
                 f'<div class="copy"><pre class="cmd" id="cmd">{esc(one)}\nllmbox test</pre><button class="btn cpy" type="button" data-copy-from="cmd">COPY</button></div></section>')
    else:
        state = (f'<section class="panel pad"><div class="lbl">Own one? Add yours</div><p class="q">{ent["machines"]} machine{"s" if ent["machines"] > 1 else ""} so far. '
                 f'Yours adds to the median, and your result gets a page and a card.</p>'
                 f'<div class="copy"><pre class="cmd" id="cmd">{esc(one)}\nllmbox test</pre><button class="btn cpy" type="button" data-copy-from="cmd">COPY</button></div></section>')
    first = _first(ent.get("credit"), users)
    body = f'''
<section class="panel hd hwp"><div><div class="crumb"><a href="hardware.html">Hardware</a> / {esc(name)}</div>
 <h1>What runs best on {esc(_a(name))}</h1>{answer}
 <p class="q">{first + " · " if first else ""}get told when it is measured or a better model comes: <a href="{feed}">RSS</a></p></div></section>
{models}{runs}{state}'''
    return _page(f"llmbox · {name}", "HARDWARE", body, ("pages.css", "hardware.css"), ("runpage.js",),
                 about=f"The local AI model that runs best on {_a(name)}, measured by people with one or predicted, with the settings to run it.",
                 head=f'<link rel="alternate" type="application/rss+xml" title="llmbox · {esc(name)}" href="{feed}">')


def _when(iso: str) -> str:
    try:
        return email.utils.formatdate(calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")), usegmt=True)   # events are in UTC
    except ValueError:
        return email.utils.formatdate(usegmt=True)


def item_text(e: dict, meta: dict) -> str:
    """A feed item's words (decision 18)."""
    name = e["entry"]
    if e["kind"] == "start":
        model = e.get("model")
        if not e.get("testable", True):
            return "Feed started. AMD timing comes after launch; this feed will say when." + (f" Predicted best: {model}, ~{e['tps']:.0f} tok/s, rough." if model else "")
        if e.get("measured"):
            n = e.get("machines") or 0
            return (f"Feed started: {name} already measured on {n} machine{'s' if n != 1 else ''}; this feed lists what is measured from now on."
                    + (f" Best: {model}, {'~' if e.get('predicted') else ''}{e['tps']:.0f} tok/s." if model else ""))
        return f"Feed started: nobody has measured {_a(name)} yet." + (f" Predicted best: {model}, ~{e['tps']:.0f} tok/s." if model else "")
    if e["kind"] == "measured":
        model = (meta.get(e["rid"]) or {}).get("name") or e["rid"]
        return f"{name}: {model} measured, {e['tps']:.0f} tok/s ({e['machines']} machine{'s' if e['machines'] != 1 else ''})"
    score = f", {e['score']:.0f}% of Opus" if e.get("score") is not None else ""
    return f"{name}: new best, {e['model']}{score}, {'~' if e.get('predicted') else ''}{e['tps']:.0f} tok/s"


TAG = "tag:llmbox.pages.dev,2026:"   # fixed whatever the site's address becomes: a reader never sees an item twice


def feed(name: str, board: dict, events: list[dict], meta: dict, site: str) -> str:
    """feeds/<slug>.xml: RSS 2.0, newest first; each item's guid is its event id, so a reader never sees one twice."""
    slug = board["entries"][name]["slug"]
    page = f"{site}/hw-{slug}"
    mine = sorted((e for e in events if e["id"].split("/")[1] == slug and not e.get("seed")), key=lambda e: e["at"], reverse=True)   # by slug: a renamed entry keeps its feed
    items = "".join(f"<item><title>{esc(item_text(e, meta))}</title><link>{esc(page)}</link>"
                    f'<guid isPermaLink="false">{TAG}{esc(slug)}/{esc(e["id"])}</guid><pubDate>{_when(e["at"])}</pubDate></item>\n'
                    for e in mine)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
            f"<title>llmbox · {esc(name)}</title><link>{esc(page)}</link>"
            f'<atom:link href="{esc(site)}/feeds/{esc(slug)}.xml" rel="self" type="application/rss+xml"/>'
            f"<description>New results for {esc(_a(name))} on llmbox: models measured there and the best one.</description>"
            f"<language>en</language>\n{items}</channel></rss>\n")
