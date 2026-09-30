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
        if f.endswith((".css", ".js")):
            shutil.copy(os.path.join(ASSETS, f), os.path.join(out_dir, f))
            out.append(os.path.join(out_dir, f))
    return out


def _page(title: str, tab: str, body: str, css: tuple = (), js: tuple = (), data=None, links: dict | None = None) -> str:
    """A page: the header (tabs, the visitor's box), the body, the footer. Stylesheets and scripts are shared files
    (assets/); a page's own numbers go inline as DATA, before its scripts."""
    links = dict(TAB_LINKS, **(links or {}))
    nav = "".join(f'<a class="{"on" if t == tab else ""}" href="{esc(links.get(t) or "#")}">{t}</a>' for t in ("MODELS", "NEW", "COMPARE", "METHOD"))
    styles = "".join(f'<link rel="stylesheet" href="{asset_url(c)}">' for c in ("osc.css",) + tuple(css))
    scripts = ((f"<script>const DATA = {json.dumps(data).replace('</', '<\\/')};</script>" if data is not None else "")
               + "".join(f'<script src="{asset_url(j)}"></script>' for j in ("box.js",) + tuple(js)))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title>{styles}</head><body>'
            '<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="1.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>'
            '<div class="wrap"><header class="plate"><a class="brand glow" href="index.html">LLMBOX<small>LOCAL LLM BENCHMARK</small></a>'
            f'<nav class="tabs">{nav}</nav><a class="boxchip" id="boxchip" href="index.html#box" title="the box speeds and fit are shown for; change it on the home page">'
            f'Your box <b>reference PC</b></a></header>{body}'
            f'<footer><span>Every number comes from a saved run. The score does not depend on the box; speed does. <a href="method.html">How scores work →</a></span>'
            f'<span>generated {time.strftime("%b %d, %Y %H:%M")}</span></footer></div>{scripts}</body></html>')
