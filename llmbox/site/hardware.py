"""How fast a model is on other computers (hardware-<id>.html)."""
from __future__ import annotations

from .. import report
from .layout import _page
from .words import _name_of, esc


def hardware_page(rid: str, rec: dict, shape: dict, data: dict) -> str:
    """How fast the model is on other boxes: one box measured, the rest predicted from the model file and each box's
    memory speeds (the same plan() the home page uses); the visitor's box first, what would make it faster."""
    nm = _name_of(rec)
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / <a href="recipe-{esc(rid)}.html">{esc(nm)}</a> / other boxes</div>
 <h1>How fast is <a href="recipe-{esc(rid)}.html">{esc(nm)}</a> on other computers?</h1>
 <p class="q" style="margin-top:6px">The score is the same on every computer; only the speed changes. One box is measured, the others are predicted from the model file and each box's memory speed.</p></div></section>
<p class="yb" id="yourbox"></p>
<section class="panel"><div class="lbl" id="advbox">What would make the reference PC faster</div><div class="adv" id="adv"></div>
 <div class="why" id="why"></div></section>
<section class="panel"><div class="lbl" id="boxlbl">Computers</div>
 <div class="hwf"><div class="seg" role="group" aria-label="show"><button class="on" data-k="all">All</button><button data-k="nv">NVIDIA</button><button data-k="mac">Mac</button></div>
  <label><input type="checkbox" id="fitonly"> only where it fits</label><span class="q" id="hwnote"></span></div>
 <div id="boxes"></div>
 <p class="q hwl">Bar: tokens per second in a short chat; the tick marks the speed with a long document in context. <span class="src">measured</span> = timed on that box ·
 <span class="src">predicted</span> = from memory speeds, checked against the measured box · <span class="src">rough</span> = outside what was measured (Macs, most of the model on a big card).</p></section>'''
    sp = rec["summary"]["speed"]
    measured = {"gpu": data["ref"]["gpu"], "ram": data["ref"]["ram"], "rambw": data["ref"]["rambw"], "t2": sp.get("decode_tps"),
                "td": float(report._deep(sp)) if report._deep(sp) != "-" else None}
    return _page(f"llmbox · {nm} on other computers", "MODELS", body, ("pages.css", "hardware.css"), ("plan.js", "hardware.js"),
                 dict(data, sh=shape, measured=measured))


