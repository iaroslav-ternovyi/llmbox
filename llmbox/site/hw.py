"""A page and a feed per picker entry: hw-<slug>.html and feeds/<slug>.xml (design review 2026-10-02, decisions 10,
12, 17 and 18). Both read the board (board.py), so the map's cell, the page and the feed say the same thing.

The page: the best model there at the picker's start values and its number, measured (the comparison group behind
it named) or predicted; the measurements per model and comparison group (RAM speed), the reference PC's labelled;
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


def hw_page(name: str, board: dict, meta: dict, users: dict, site: str) -> str:
    ent = board["entries"][name]
    slug, best, testable = ent["slug"], ent.get("best"), ent["testable"]
    num, measured = number(best)
    one = f"{CURL} {site}/install.sh | sh"
    feed = f"feeds/{slug}.xml"
    # the answer
    if best:
        sc = f' · {best["score"]:.0f}% of Claude Opus 5.5' if best.get("score") is not None else ""
        if measured:
            n = best["machines"]
            ref_only = n == 1 and board["sets"].get((best["rid"], best["group"]), {}).get("ref")
            how = ("measured on llmbox's own PC" if ref_only else "measured on 1 machine" if n == 1 else f"the median of {n} machines") + f" ({esc(group(best['group']))})"
        elif testable:
            how = "predicted from the model file and the memory speeds; nobody has measured it here yet"
        else:
            how = "a rough prediction: there are no public runs of AMD cards to check it against yet"
        answer = (f'<p class="best">Best here: <a href="recipe-{esc(best["rid"])}.html">{esc(best["name"])}</a>{sc} · '
                  f'<b class="{"" if measured else "pred"}">{esc(num)}</b> tok/s{"" if testable or measured else ", rough"}</p><p class="q">{how}; {esc(start_values(name))}.</p>')
    else:
        answer = f'<p class="best">No listed model fits {esc(start_values(name))}.</p>'
    # what has been measured: per model and comparison group
    groups = sorted(((rid, c, v) for (rid, c), v in board["sets"].items() if hwclass.display_class(c) == name),
                    key=lambda x: (-x[2]["machines"], -x[2]["median"]))
    rows = "".join(f'<tr><td class="l" data-h="model"><a href="recipe-{esc(rid)}.html">{esc((meta.get(rid) or {}).get("name") or rid)}</a></td>'
                   f'<td class="l" data-h="group">{esc(group(c))}{" · reference" if v["ref"] else ""}</td>'
                   f'<td data-h="machines">{v["machines"]}</td><td data-h="tok/s"><b>{v["median"]:.0f}</b></td></tr>' for rid, c, v in groups)
    table = (f'<section class="panel"><div class="lbl">Measured here</div><div class="tw"><table class="grp"><tr><th class="l">MODEL</th>'
             f'<th class="l">COMPARISON GROUP</th><th>MACHINES</th><th>TOK/S</th></tr>{rows}</table></div>'
             '<p class="q pad0">A group is the card plus the RAM speed; each machine counts once (the median of its runs); '
             '"reference" includes llmbox\'s own PC.</p></section>') if groups else ""
    recent = sorted((r for r in board["runs"].values() if r["entry"] == name), key=lambda r: r["at"], reverse=True)[:20]
    runs = ("<section class=\"panel\"><div class=\"lbl\">Recent runs</div><ul class=\"runs\">"
            + "".join(f'<li><a href="r/{esc(r["sid"])}.html">{esc(r["at"][:10])} · {esc((meta.get(r["rid"]) or {}).get("name") or r["rid"])} · '
                      f'{r["t2"]:.0f} tok/s{" · not confirmed" if r["outlier"] else ""}</a></li>' for r in recent)
            + "</ul></section>") if recent else ""
    if not testable:
        state = (f'<section class="panel pad"><div class="lbl">AMD</div><p>AMD timing comes after launch: llmbox cannot measure '
                 f'{esc(_a(name))} yet. This page and <a href="{feed}">its feed</a> will say when it can.</p></section>')
    elif not ent["machines"]:
        badge = hwclass.first_badge(name)
        state = (f'<section class="panel pad"><div class="lbl">Be the first</div><p>Nobody has measured {esc(_a(name))} yet. '
                 f'Sign in with GitHub when llmbox asks and your result card says <b>{esc(badge)}</b>.</p>'
                 f'<pre class="cmd" id="cmd">{esc(one)}\nllmbox test</pre><button class="btn solid" data-copy-from="cmd">COPY</button></section>')
    else:
        state = (f'<section class="panel pad"><div class="lbl">Add yours</div><p class="q">{ent["machines"]} machine{"s" if ent["machines"] > 1 else ""} so far. '
                 f'Yours adds to the median, and your result gets a page and a card.</p>'
                 f'<pre class="cmd" id="cmd">{esc(one)}\nllmbox test</pre><button class="btn solid" data-copy-from="cmd">COPY</button></section>')
    first = _first(ent.get("credit"), users)
    body = f'''
<section class="panel hd hwp"><div><div class="crumb"><a href="index.html">Models</a> / <a href="index.html#map">hardware</a> / {esc(name)}</div>
 <h1>What runs best on {esc(_a(name))}</h1>{answer}
 <p class="q">{first + " · " if first else ""}new results: <a href="{feed}">RSS</a></p></div></section>
{state}{table}{runs}'''
    return _page(f"llmbox · {name}", "MODELS", body, ("pages.css", "hardware.css"), ("runpage.js",),
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
    mine = sorted((e for e in events if e["entry"] == name and not e.get("seed")), key=lambda e: e["at"], reverse=True)
    items = "".join(f"<item><title>{esc(item_text(e, meta))}</title><link>{esc(page)}</link>"
                    f'<guid isPermaLink="false">{TAG}{esc(slug)}/{esc(e["id"])}</guid><pubDate>{_when(e["at"])}</pubDate></item>\n'
                    for e in mine)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
            f"<title>llmbox · {esc(name)}</title><link>{esc(page)}</link>"
            f'<atom:link href="{esc(site)}/feeds/{esc(slug)}.xml" rel="self" type="application/rss+xml"/>'
            f"<description>New results for {esc(_a(name))} on llmbox: models measured there and the best one.</description>"
            f"<language>en</language>\n{items}</channel></rss>\n")
