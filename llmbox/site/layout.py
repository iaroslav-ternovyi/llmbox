"""The page frame: header with the tabs and the visitor's box, footer, the shared speed-prediction script."""
from __future__ import annotations

import os
import time

from .words import esc


ASSETS = os.path.join(os.path.dirname(__file__), "assets")


PLAN_JS = r"""
function plan(sh, hw, ctx, depth, buf = 2100) {   // buf: compute buffer MiB for -ub 2048 / 1024 / 512 = 2100 / 1300 / 900
  const mib = 1 / 1048576, kv = sh.kvB * ctx + sh.rec, gpuFixed = (sh.nonexp + kv) * mib + buf + 700, free = hw.vram - gpuFixed;
  let gf, ramUsed, fits, perCpu, perGpu;
  if (hw.mac) { const fr = sh.moe ? sh.nUsed / sh.nExp : 1; fits = gpuFixed + ((sh.moe ? sh.exp : 0) + sh.embed) * mib <= hw.vram; gf = 1;
                ramUsed = 0; perCpu = 0; perGpu = sh.nonexp + (sh.moe ? sh.exp * fr : 0); }   // unified memory: all of it on the GPU
  else if (!sh.moe) { const need = gpuFixed + sh.embed * mib; fits = need <= hw.vram; gf = 1; ramUsed = sh.embed * mib; perGpu = sh.nonexp; perCpu = 0; }
  else { gf = Math.max(0, Math.min(1, free / (sh.exp * mib))); const cpuExp = sh.exp * (1 - gf); ramUsed = (cpuExp + sh.embed) * mib;
         fits = free > -1 && ramUsed + 4096 <= hw.ram; const fr = sh.nUsed / sh.nExp; perCpu = cpuExp * fr; perGpu = sh.nonexp + sh.exp * gf * fr; }
  // Metal: ~80% of the memory bandwidth, ~0.1 ms per layer per token (fitted to community M4 Pro / M5 Max runs of 35B-A3B MoE
  // models, llm-bench.io 2026-09: 70-86 and 113-141 tok/s); CUDA: 75% and 0.025 ms (the reference box)
  const eff = hw.mac ? 0.8 : 0.75, ovh = hw.mac ? 0.1 : 0.025;
  const tps = d => 1 / (perCpu / (hw.rambw * 1e9 * 0.8 * sh.cpuEff) + (perGpu + sh.kvB * d) / (hw.vrambw * 1e9 * eff) + sh.layers * ovh / 1000);
  return { fits, gf, ramUsed, vram: Math.min(hw.vram, gpuFixed + (sh.moe ? sh.exp * gf * mib : sh.embed * mib)), t2: tps(2000), td: tps(Math.min(depth, ctx)) };
}
function sameClassAs(hw, r) { return hw.gpu === r.gpu && Math.abs(hw.rambw - r.rambw) / r.rambw < 0.15 && hw.ram >= r.ram * 0.9; }
function forBox(sh, hw) {   // as llmbox fit: the recipe's context if it fits, else halve it; a smaller prompt batch before a smaller context
  let p = null, ctx = sh.ctx;
  for (let c = sh.ctx; c >= 8192 && !(p && p.fits); c = c / 2)
    for (const buf of [2100, 1300, 900]) { p = plan(sh, hw, c, sh.deepK * 1000, buf); ctx = c; if (p.fits) break; }
  // the reference box's measured/predicted ratio is about that box (experts streamed over PCIe, its MTP gain): not a Mac's
  return Object.assign(p, { ctx, t2: p.t2 * (hw.mac ? 1 : sh.k2), td: p.td * (hw.mac ? 1 : sh.kd) });
}
function boxFrom(g, ramGB, rambw) {   // a picker entry and the RAM fields -> what plan() needs
  if (g[3] === "mac") { const mem = Math.min(ramGB, g[4]) * 1024;   // macOS lets the GPU use ~2/3 (small Macs) to 3/4 of unified memory
    return { name: g[0], gpu: g[0], mac: true, mem, vram: mem * (mem >= 36864 ? 0.75 : 0.67), vrambw: g[2], ram: 0, rambw: g[2] }; }
  return { name: g[0], gpu: g[0].replace(/ \d+ GB$/, ""), vram: g[1], vrambw: g[2], ram: ramGB * 1024, rambw };
}
function boxLabel(b) { return b.mac ? `${b.name} · ${Math.round(b.mem / 1024)} GB unified · ${b.rambw} GB/s` : `${b.name} · ${Math.round(b.ram / 1024)} GB · ${b.rambw} GB/s`; }
function savedBox(DATA) {
  try { const s = JSON.parse(localStorage.getItem("llmbox-box") || "null"); if (!s || !s.gpu) return null;
    const g = DATA.gpus.find(x => x[0] === s.gpu); if (!g) return null;
    return boxFrom(g, parseInt(s.ram), parseFloat(s.bwn) || parseFloat(s.bw)); } catch (e) { return null; }
}
"""


BOXCHIP_JS = r"""(function () {   // the box picked on the home page, named in every page's header
  try { const s = JSON.parse(localStorage.getItem("llmbox-box") || "null"), c = document.getElementById("boxchip");
    if (c && s && s.gpu) c.querySelector("b").textContent = s.gpu + (/^Mac/.test(s.gpu) ? ` · ${s.ram} GB` : ` · ${s.ram} GB RAM`); } catch (e) {}
})();"""


TAB_LINKS = {"MODELS": "index.html", "NEW": "new.html", "COMPARE": "compare.html", "METHOD": "method.html"}


def _page(title: str, tab: str, body: str, css: str = "", js: str = "", links: dict | None = None) -> str:
    links = dict(TAB_LINKS, **(links or {}))
    nav = "".join(f'<a class="{"on" if t == tab else ""}" href="{esc(links.get(t) or "#")}">{t}</a>' for t in ("MODELS", "NEW", "COMPARE", "METHOD"))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><link rel="stylesheet" href="osc.css"><style>{_PAGES_CSS}{css}</style></head><body>'
            '<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="1.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>'
            '<div class="wrap"><header class="plate"><a class="brand glow" href="index.html">LLMBOX<small>LOCAL LLM BENCHMARK</small></a>'
            f'<nav class="tabs">{nav}</nav><a class="boxchip" id="boxchip" href="index.html#box" title="the box speeds and fit are shown for; change it on the home page">'
            f'Your box <b>reference PC</b></a></header>{body}'
            f'<footer><span>Every number on this page comes from a saved run record.</span><span>generated {time.strftime("%b %d, %Y %H:%M")}</span></footer>'
            f'</div><script>{BOXCHIP_JS}</script>{f"<script>{js}</script>" if js else ""}</body></html>')


_PAGES_CSS = """
.opt{border-collapse:collapse;margin:8px 0 4px;font-size:13px}.opt th,.opt td{padding:7px 12px;border-bottom:1px solid var(--line2);text-align:right}
.opt th{color:var(--muted);font-weight:500;font-size:11px;letter-spacing:.06em;text-transform:uppercase}.opt .l{text-align:left}.opt tr.me td{color:var(--amber)}.opt small{color:var(--muted);margin-left:4px}
.title,.hd{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:18px;padding:18px 22px}
.title h1,.hd h1{font:600 32px/1.1 "IBM Plex Sans Condensed";margin-top:6px}.title h1 a,.hd h1 a{color:var(--ink);border-bottom:1px dotted var(--faint)}
.title .meta{font-size:12px;color:var(--muted);margin-top:8px}
.title .acts,.hd .acts{display:flex;flex-wrap:wrap;gap:10px}
.hd .id{font-size:12px;color:var(--muted);margin-top:6px}
.tele{display:grid}.tele>div{padding:14px 16px;border-right:1px solid var(--line2)}.tele>div:last-child{border-right:0}
.tele .tv{font-size:22px;margin-top:2px}.tele .tv small{font-size:11px;color:var(--muted);margin-left:4px}
.spark{width:100%;height:40px;display:block;margin-top:6px}
.flag{font-size:10px;letter-spacing:.12em;border:1px solid var(--line);color:var(--soft);padding:1px 6px;margin-right:4px}.flag.lo{border-color:var(--red-dim);color:var(--red)}
.m2{font:600 14px "IBM Plex Sans Condensed";color:var(--ink)}
.bars{grid-template-columns:170px minmax(0,1fr) 170px}
.bars .tr{position:relative;height:26px;background:var(--line2)}.bars .tr+.tr{margin-top:4px}
.bars .tr i{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,rgba(255,176,0,.28),rgba(255,176,0,.7));box-shadow:0 0 10px rgba(255,176,0,.2)}
.bars .tr.b i{background:rgba(232,228,216,.2);box-shadow:none}
.bars .tr span{position:absolute;left:10px;top:4px;font-size:13px;white-space:nowrap}.bars .tr span em{font-style:normal;color:rgba(232,228,216,.7);margin-left:10px}
.bars.multi{grid-template-columns:150px minmax(0,1fr)}
@media (max-width:900px){.title,.hd{grid-template-columns:1fr}.tele{grid-template-columns:1fr 1fr!important}.tele>div{border-bottom:1px solid var(--line2)}
 .bars{grid-template-columns:110px minmax(0,1fr)}}
"""
