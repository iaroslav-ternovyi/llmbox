"""How fast a model is on other computers (hardware-<id>.html)."""
from __future__ import annotations

from .. import report
from .layout import _page
from .words import INSTALL, _name_of, esc, variant


def hardware_page(rid: str, rec: dict, shape: dict, data: dict, community: list | None = None) -> str:
    """How fast the model is on other boxes: one box measured, the rest predicted from the model file and each box's
    memory speeds (the same plan() the home page uses); the visitor's box first, what would make it faster."""
    nm = _name_of(rec)
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / <a href="recipe-{esc(rid)}.html">{esc(nm)}</a> / other boxes</div>
 <h1>How fast is <a href="recipe-{esc(rid)}.html">{esc(nm)}</a> on other computers?</h1>
 <p class="q" style="margin-top:6px">The score is the same on every computer; only the speed changes. Computers people measured come first; the others are predicted from the model file and each box's memory speed.</p></div></section>
<p class="yb" id="yourbox"></p>
<section class="panel"><div class="lbl" id="advbox">What would make the reference PC faster</div><div class="adv" id="adv"></div>
 <div class="why" id="why"></div></section>
<section class="panel"><div class="lbl" id="boxlbl">Computers</div>
 <div class="hwf"><div class="seg" role="group" aria-label="show"><button class="on" data-k="all">All</button><button data-k="nv">NVIDIA</button><button data-k="amd">AMD</button><button data-k="mac">Mac</button></div>
  <label><input type="checkbox" id="fitonly"> only where it fits</label><span class="q" id="hwnote"></span></div>
 <div id="boxes"></div>
 <p class="q hwl">Bar: tokens per second in a short chat; the tick marks the speed with a long document in context. <span class="src">measured</span> = timed on that box ·
 <span class="src">predicted</span> = from memory speeds, checked against the measured box and public llama.cpp runs · <span class="src">rough</span> = a mixture-of-experts model on a Mac or an AMD card, where there are no public runs to check against yet.
 A measured row is the median of the machines in that class (card, RAM speed); each machine counts once.</p></section>
<section class="panel pad"><div class="lbl">Add your computer</div>
 <p class="q">The first line installs llmbox and this model with the settings measured here, fitted to your hardware, times it and offers to send the time. The second, about 13 minutes, adds a 10-minute quality test: it counts toward the model's score when you are signed in (it offers to sign you in with GitHub), and our server grades every answer again. <code>llmbox submit --dry-run</code> shows exactly what is sent; nothing in it names you or the machine.</p>
 <pre class="cmd">{esc(INSTALL)} -s -- {esc(rid)}
llmbox test {esc(rid)}          # then: speed and the 10-minute quality test (about 13 minutes)</pre></section>'''
    sp = rec["summary"]["speed"]
    measured = {"gpu": data["ref"]["gpu"], "ram": data["ref"]["ram"], "rambw": data["ref"]["rambw"], "t2": sp.get("decode_tps"),
                "td": float(report._deep(sp)) if report._deep(sp) != "-" else None}
    return _page(f"llmbox · {nm} · {variant(rid, (rec.get('model') or {}).get('file'), full=True)} on other computers", "MODELS", body, ("pages.css", "hardware.css"), ("plan.js", "hardware.js"),
                 dict(data, sh=shape, measured=measured, community=community or []), about=f"How fast {nm} runs on {len(data['gpus'])} graphics cards and Macs: one measured, the rest predicted from the model file and memory speeds.")


