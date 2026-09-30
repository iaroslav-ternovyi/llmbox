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


TAB_LINKS = {"MODELS": "index.html", "NEW": "new.html", "COMPARE": "compare.html", "METHOD": "method.html"}


def asset_url(name: str) -> str:
    """name?v=<content hash>: a browser keeps an asset until it changes."""
    return f"{name}?v={_asset_hash(name)}"


@functools.cache
def _asset_hash(name: str) -> str:
    return hashlib.sha1(open(os.path.join(ASSETS, name), "rb").read()).hexdigest()[:8]


def copy_assets(out_dir: str) -> list[str]:
    """Every stylesheet and script next to the pages."""
    out = []
    for f in sorted(os.listdir(ASSETS)):
        if f.endswith((".css", ".js", ".sh")):   # install.sh: `curl -fsSL <site>/install.sh | sh`
            shutil.copy(os.path.join(ASSETS, f), os.path.join(out_dir, f))
            out.append(os.path.join(out_dir, f))
    return out


FONTS = "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&family=IBM+Plex+Sans+Condensed:wght@500;600;700&display=swap"
ICON = ("data:image/svg+xml," "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%230E0F0C'/%3E"
        "%3Cpath d='M5 22 L11 22 L14 9 L18 25 L21 16 L27 16' fill='none' stroke='%23FFB000' stroke-width='3' stroke-linejoin='round'/%3E%3C/svg%3E")
ABOUT = ("Local AI models graded on real work (coding, tools, documents, writing) and timed on a real PC: what fits your "
         "graphics card or Mac, how fast it answers, how close it gets to Claude, and the settings to run it.")


def _page(title: str, tab: str, body: str, css: tuple = (), js: tuple = (), data=None, links: dict | None = None,
          about: str | None = None) -> str:
    """A page: the header (tabs, the visitor's box), the body, the footer. Stylesheets and scripts are shared files
    (assets/); a page's own numbers go inline as DATA, before its scripts."""
    links = dict(TAB_LINKS, **(links or {}))
    nav = "".join(f'<a class="{"on" if t == tab else ""}" href="{esc(links.get(t) or "#")}">{t}</a>' for t in ("MODELS", "NEW", "COMPARE", "METHOD"))
    styles = "".join(f'<link rel="stylesheet" href="{asset_url(c)}">' for c in ("osc.css",) + tuple(css))
    scripts = ((f"<script>const DATA = {json.dumps(data).replace('</', '<\\/')};</script>" if data is not None else "")
               + "".join(f'<script src="{asset_url(j)}"></script>' for j in ("box.js",) + tuple(js)))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><meta name="description" content="{esc(about or ABOUT)}"><link rel="icon" href="{ICON}">'
            f'<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link rel="stylesheet" href="{FONTS}">{styles}</head><body>'
            '<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="1.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>'
            '<div class="wrap"><header class="plate"><a class="brand glow" href="index.html">LLMBOX<small>LOCAL LLM BENCHMARK</small></a>'
            f'<nav class="tabs">{nav}</nav><a class="boxchip" id="boxchip" href="index.html#box" title="the box speeds and fit are shown for; change it on the home page">'
            f'Your box <b>reference PC</b></a><a class="getbtn" href="install.html">GET LLMBOX</a>'
            f'<a class="signin" id="signin" href="account.html">SIGN IN</a></header>{body}'
            f'<footer><span>Every number comes from a saved run. The score does not depend on the box; speed does. <a href="method.html">How scores work →</a></span>'
            f'<span>generated {time.strftime("%b %d, %Y %H:%M")}</span></footer></div>{scripts}</body></html>')
