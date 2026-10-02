"""The page frame: header with the tabs and the visitor's box, footer, the shared stylesheets and scripts (assets/)."""
from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import time

from .words import esc


ASSETS = os.path.join(os.path.dirname(__file__), "assets")


TAB_LINKS = {"MODELS": "index.html", "HARDWARE": "hardware.html", "NEW": "new.html", "COMPARE": "compare.html", "METHOD": "method.html"}
# what a tab says, long and on a phone (the keys stay: pages name their tab by them)
TAB_NAMES = {"NEW": ("NEW MODELS", "NEW"), "METHOD": ("HOW WE TEST", "METHOD")}


def asset_url(name: str) -> str:
    """name?v=<content hash>: a browser keeps an asset until it changes."""
    return f"{name}?v={_asset_hash(name)}"


@functools.cache
def _asset_hash(name: str) -> str:
    return hashlib.sha1(open(os.path.join(ASSETS, name), "rb").read()).hexdigest()[:8]


def copy_assets(out_dir: str) -> list[str]:
    """Every stylesheet, script and image next to the pages."""
    out = []
    for f in sorted(os.listdir(ASSETS)):
        if f.endswith((".css", ".js", ".sh", ".png", ".ico", ".svg", ".webmanifest", ".woff2", ".txt")):   # install.sh: `curl -fsSL <site>/install.sh | sh`
            shutil.copy(os.path.join(ASSETS, f), os.path.join(out_dir, f))
            out.append(os.path.join(out_dir, f))
    return out


ABOUT = ("Local AI models graded on real work (coding, tools, documents, writing) and timed on a real PC: what fits your "
         "graphics card or Mac, how fast it answers, how close it gets to Claude, and the settings to run it.")


def _err_attrs() -> str:
    """box.js reports the site's script errors to the public server (an https one: none from a local build)."""
    from ..public import server
    api = server()
    return f' data-err="{esc(api)}/api/v1/err" data-build="{time.strftime("%Y-%m-%dT%H:%M")}"' if api.startswith("https://") else ""


def _tab_label(t: str) -> str:
    if t not in TAB_NAMES:
        return t
    long, short = TAB_NAMES[t]
    return f'<span class="tl">{long}</span><span class="ts">{short}</span>'


def _page(title: str, tab: str, body: str, css: tuple = (), js: tuple = (), data=None, links: dict | None = None,
          about: str | None = None, base: bool = False, head: str = "") -> str:
    """A page: the header (tabs, the visitor's box), the body, the footer. Stylesheets and scripts are shared files
    (assets/); a page's own numbers go inline as DATA, before its scripts. base: links resolve from the site's root (a
    page in a folder, r/<id>, and 404.html, which Pages serves at any missing address); such a page has no #anchors.
    head: more of the head (a page's feed link)."""
    links = dict(TAB_LINKS, **(links or {}))
    nav = "".join(f'<a class="{"on" if t == tab else ""}" href="{esc(links.get(t) or "#")}">{_tab_label(t)}</a>' for t in TAB_LINKS)
    styles = "".join(f'<link rel="stylesheet" href="{asset_url(c)}">' for c in ("fonts.css", "osc.css") + tuple(css))
    from ..public import stats
    counter = f'<script src="{asset_url("stats.js")}" data-stats="{esc(stats())}"></script>' if stats() else ""   # before the page's scripts: they count events
    scripts = ((f"<script>const DATA = {json.dumps(data).replace('</', '<\\/')};</script>" if data is not None else "") + counter
               + "".join(f'<script src="{asset_url(j)}"{_err_attrs() if j == "box.js" else ""}></script>' for j in ("box.js",) + tuple(js)))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            + ('<base href="/">' if base else "") +
            f'<title>{esc(title)}</title><meta name="description" content="{esc(about or ABOUT)}">'
            '<link rel="icon" href="favicon.ico" sizes="32x32"><link rel="icon" href="icon.svg" type="image/svg+xml">'
            '<link rel="apple-touch-icon" href="apple-touch-icon.png"><link rel="manifest" href="manifest.webmanifest">'
            f'<link rel="preload" href="plex-sans-latin.woff2" as="font" type="font/woff2" crossorigin>'
            f'<link rel="preload" href="plex-mono-400-latin.woff2" as="font" type="font/woff2" crossorigin>{styles}{head}</head><body>'
            '<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="1.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>'
            '<div class="wrap"><header class="plate"><a class="brand glow" href="index.html">LLMBOX<small>WHICH AI RUNS ON YOUR PC</small></a>'
            f'<nav class="tabs" aria-label="sections">{nav}</nav><a class="boxchip" id="boxchip" href="index.html#box" title="the box speeds and fit are shown for; change it on the home page">'
            f'Your box <b>not set</b></a><a class="getbtn" href="install.html">GET LLMBOX</a>'
            f'<a class="signin" id="signin" href="account.html">SIGN IN</a></header><main id="main">{body}</main>'
            f'<footer><span>Every number comes from a saved run. The score does not depend on the box; speed does. <a href="method.html">How scores work →</a> · <a href="privacy.html">Privacy</a> · <a href="terms.html">Terms</a> · <a href="https://github.com/iaroslav-ternovyi/llmbox">GitHub</a></span>'
            f'<span>generated {time.strftime("%b %d, %Y %H:%M")}</span></footer></div>{scripts}</body></html>')
