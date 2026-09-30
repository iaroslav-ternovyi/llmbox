"""How fast a model is on other computers (hardware-<id>.html)."""
from __future__ import annotations

from .. import report
from .layout import _page, PLAN_JS
from .words import _name_of, esc


def hardware_page(rid: str, rec: dict, shape: dict, data: dict) -> str:
    """How fast the model is on other boxes: one box measured, the rest predicted from the model file and each box's
    memory speeds (the same plan() the home page uses); the visitor's box first, what would make it faster."""
    import json as _json
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
    js = PLAN_JS + f"\nconst DATA = {_json.dumps(dict(data, sh=shape, measured={'gpu': data['ref']['gpu'], 'ram': data['ref']['ram'], 'rambw': data['ref']['rambw'], 't2': rec['summary']['speed'].get('decode_tps'), 'td': float(report._deep(rec['summary']['speed'])) if report._deep(rec['summary']['speed']) != '-' else None}))};\n" + _HW_JS
    return _page(f"llmbox · {nm} on other computers", "MODELS", body, _HW_CSS, js)


_HW_CSS = """
.yb{font-size:13.5px;color:var(--soft);margin:22px 0 0;padding:12px 16px;border:1px dashed var(--amber-dim)}.yb:empty{display:none}.yb b{color:var(--ink);font-weight:500}
.adv{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}.adv>div{padding:20px 22px;border-right:1px solid var(--line2)}.adv>div:last-child{border-right:0}
.adv h4{font:600 16px "IBM Plex Sans Condensed"}.adv .g{font:300 34px "IBM Plex Mono";color:var(--amber);margin:6px 0 4px}.adv .g.no{color:var(--muted)}.adv p{font-size:12.5px;color:var(--soft);line-height:1.55}
.why{padding:14px 22px;font-size:12.5px;color:var(--muted);line-height:1.6;border-top:1px solid var(--line2)}
.hwf{display:flex;flex-wrap:wrap;align-items:center;gap:10px 22px;padding:18px 16px 8px;font-size:12.5px;color:var(--soft)}
.hwf input{accent-color:#FFB000;margin-right:6px;vertical-align:-2px}
.seg{display:flex;gap:4px}.seg button{background:none;border:1px solid transparent;color:var(--muted);font:12px "IBM Plex Mono";padding:5px 10px;cursor:pointer}
.seg button:hover{color:var(--ink)}.seg button.on{color:var(--amber);border-color:var(--amber-dim)}
.hr{display:grid;grid-template-columns:minmax(170px,1.1fr) 150px minmax(0,2.2fr) 90px 80px;gap:14px;align-items:center;padding:8px 16px;border-bottom:1px solid var(--line2)}
.hr .nv{font:500 15px "IBM Plex Mono";color:var(--ink);white-space:nowrap}.hr .nv small{display:block;font:400 11px "IBM Plex Mono";color:var(--faint)}
.hr .bn{font:600 15px "IBM Plex Sans Condensed";color:var(--ink)}.hr .bn small{display:block;font:400 11px "IBM Plex Mono";color:var(--faint)}
.hr.me{background:rgba(255,176,0,.05)}.hr.nofit{opacity:.45}.hr .grp{grid-column:1/-1}
.hh{padding:14px 16px 6px;font-size:11px;letter-spacing:.16em;color:var(--muted);text-transform:uppercase;border-bottom:1px solid var(--line)}
.hb{position:relative;height:10px;background:var(--line2)}.hb i{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,rgba(255,176,0,.25),rgba(255,176,0,.65))}
.hb i.p{background:linear-gradient(90deg,rgba(232,228,216,.12),rgba(232,228,216,.3))}.hb u{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink);opacity:.6}
.hr .ft{font-size:12.5px;color:var(--soft);white-space:nowrap}.hr .ft .no{color:var(--red)}.hr .src{text-align:right}
.tag{font-size:10px;letter-spacing:.12em;color:var(--bg);background:var(--amber);padding:1px 6px;margin-left:8px;vertical-align:2px}.tag.m{background:none;color:var(--amber);border:1px solid var(--amber-dim)}
.hwl{padding:12px 16px 16px;margin:0;line-height:1.7}
@media (max-width:900px){.adv{grid-template-columns:1fr}.adv>div{border-right:0;border-bottom:1px solid var(--line2)}
 .hr{grid-template-columns:minmax(0,1fr) auto;row-gap:6px}.hr .hb{grid-column:1/-1;grid-row:2}.hr .nv{grid-column:2;grid-row:1;text-align:right}.hr .ft{grid-row:3}.hr .src{grid-row:3;grid-column:2}}
"""


_HW_JS = r"""
const $ = s => document.querySelector(s);
const saved = savedBox(DATA);
const refBox = { name: "the reference PC", gpu: DATA.ref.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: DATA.ref.ram, rambw: DATA.ref.rambw };
const box = saved || refBox;
const cur = forBox(DATA.sh, box);
const rough = hw => hw.mac || forBox(DATA.sh, hw).gf > 0.6;   // most experts on the GPU, or a Mac: outside the measured regime
if (saved) { $("#advbox").textContent = `What would make your box faster · ${boxLabel(box)}`;
  $("#yourbox").innerHTML = cur.fits ? `Your box (${boxLabel(box)}): <b>~${Math.round(cur.t2)} tok/s</b> in a short chat, <b>~${Math.round(cur.td)}</b> with a long document, up to ${Math.round(cur.ctx / 1024)}k context.`
    : `Your box (${boxLabel(box)}): the model does not fit, even with a smaller context.`; }
else $("#yourbox").innerHTML = `Pick your graphics card or Mac <a href="index.html#box">on the home page</a> and this page shows your box first.`;
function card(title, hw, note) {
  const f = forBox(DATA.sh, hw), g = f.t2 / cur.t2;
  const txt = g > 1.05 ? `+${Math.round((g - 1) * 100)}%` : g < 0.95 ? `${Math.round((g - 1) * 100)}%` : "±0%";
  return `<div><h4>${title}</h4><div class="g ${Math.abs(g - 1) < 0.05 ? "no" : ""}">${txt}</div><p>~${Math.round(f.t2)} tok/s. ${note}${rough(hw) ? ' <span class="src">rough</span>' : ""}</p></div>`;
}
const faster = DATA.ramKinds.map(r => r[1]).filter(v => v >= box.rambw * 1.2)[0];   // a step worth buying
const gp = n => { const g = DATA.gpus.find(x => x[0].startsWith(n)); return { gpu: n, vram: g[1], vrambw: g[2], ram: box.ram, rambw: box.rambw }; };
if (box.mac) { $("#adv").innerHTML = `<div><h4>A Mac with more memory bandwidth</h4><p>On a Mac the whole model sits in unified memory: its bandwidth sets the speed (Max and Ultra chips have 2–4× a base chip's).</p></div>`; }
else $("#adv").innerHTML = (faster ? card(`Faster RAM (${faster} GB/s)`, Object.assign({}, box, { rambw: faster }), "Same card, faster memory.") : "<div><h4>Faster RAM</h4><p class='q'>already at the fastest common speed</p></div>")
  + card("A 16 GB card (RTX 5070 Ti)", gp("RTX 5070 Ti"), "More of the model on the graphics card.")
  + card("A 24 GB card (RTX 4090)", gp("RTX 4090"), "Most of the model on the graphics card.");
$("#why").textContent = DATA.sh.moe ? `A mixture-of-experts model: what does not fit on the graphics card runs from system RAM, so on small cards the RAM speed, not the GPU, sets the pace. At ${Math.round(cur.gf * 100)}% of the experts on the card now.`
  : "A dense model: every weight is read for every token, so it has to fit on the graphics card; the card's memory speed sets the pace.";
// every box: NVIDIA cards with the visitor's RAM (or 64 GB at the reference PC's RAM speed), then Macs
const ram = saved && !saved.mac ? saved.ram : 65536, rambw = saved && !saved.mac ? saved.rambw : DATA.ref.rambw;
const m = DATA.measured, rows = [];
rows.push({ k: "nv", name: `${m.gpu}`, sub: `${Math.round(m.ram / 1024)} GB RAM · ${m.rambw} GB/s`, measured: true, t2: m.t2, td: m.td, f: forBox(DATA.sh, refBox) });
for (const g of DATA.gpus) {
  const mac = g[3] === "mac", hw = boxFrom(g, mac ? g[4] : ram / 1024, rambw), f = forBox(DATA.sh, hw);
  rows.push({ k: mac ? "mac" : "nv", name: g[0], sub: mac ? `${g[4]} GB unified · ${g[2]} GB/s` : `${Math.round(ram / 1024)} GB RAM · ${rambw} GB/s`, measured: false,
              t2: f.fits ? f.t2 : null, td: f.fits ? f.td : null, f, rough: rough(hw), mine: saved && saved.name === g[0] });
}
const vmax = Math.max(...rows.map(r => r.t2 || 0)) * 1.05;
let kind = "all";
function draw() {
  const only = $("#fitonly").checked;
  const show = rows.filter(r => (kind === "all" || r.k === kind) && (!only || r.f.fits || r.measured)).sort((a, b) => (b.mine - a.mine) || ((b.t2 || 0) - (a.t2 || 0)));
  const nfit = rows.filter(r => r.f.fits || r.measured).length;
  $("#hwnote").textContent = `fits on ${nfit} of ${rows.length}`;
  $("#boxlbl").textContent = `Computers · NVIDIA cards with ${Math.round(ram / 1024)} GB RAM at ${rambw} GB/s${saved && !saved.mac ? " (your RAM)" : ""}`;
  $("#boxes").innerHTML = show.map(r => `<div class="hr${r.mine ? " me" : ""}${r.f.fits || r.measured ? "" : " nofit"}"><div class="bn">${r.name}${r.measured ? '<span class="tag m">MEASURED</span>' : r.mine ? '<span class="tag">YOUR BOX</span>' : ""}<small>${r.sub}</small></div>` +
    (r.t2 ? `<div class="nv">${r.measured ? "" : "~"}${Math.round(r.t2)} tok/s<small>${r.td ? `${Math.round(r.td)} with a long document` : ""}</small></div>` +
      `<div class="hb" title="${Math.round(r.f.gf * 100)}% of the model's experts on the graphics card"><i class="${r.measured ? "" : "p"}" style="width:${100 * r.t2 / vmax}%"></i>${r.td ? `<u style="left:${100 * r.td / vmax}%"></u>` : ""}</div>`
      : `<div class="nv q">does not fit</div><div class="hb"></div>`) +
    `<div class="ft">${r.measured || r.f.fits ? `✓ ${Math.round(r.f.ctx / 1024)}k ctx` : '<span class="no">✗ too big</span>'}</div>` +
    `<div class="src">${r.measured ? "measured" : r.rough ? "rough" : "predicted"}</div></div>`).join("");
}
document.querySelectorAll(".hwf .seg button").forEach(b => b.addEventListener("click", () => { kind = b.dataset.k;
  document.querySelectorAll(".hwf .seg button").forEach(x => x.classList.toggle("on", x === b)); draw(); }));
$("#fitonly").addEventListener("change", draw);
draw();
"""
