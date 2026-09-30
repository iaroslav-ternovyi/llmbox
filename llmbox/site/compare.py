"""One page that compares any two models (compare.html#a-vs-b)."""
from __future__ import annotations

import json

from .. import report
from .layout import _page, PLAN_JS
from .stats import _range_pct
from .words import _human, _KNAMES, _quant, esc, GROUPS, model_name, SHORT, TASK_NAMES


def _flat_settings(rcp: dict) -> dict:
    """A recipe's settings that can differ between two models, by readable name (the model-specific loop-marker token ids
    left out: they are never the same across models)."""
    flat = lambda d, p="": {k2: v2 for k, v in (d or {}).items() for k2, v2 in (flat(v, f"{p}{k}.") if isinstance(v, dict) else {f"{p}{k}": v}).items()}
    f = flat({k: (rcp or {}).get(k) for k in ("sampling", "chat", "antiloop", "placement", "speculative")})
    return {_KNAMES.get(k, k.split(".")[-1].replace("_", " ")): "off" if v in ("", "none") else _human(v)
            for k, v in sorted(f.items()) if not k.endswith("marker_ids") and k != "placement.n_cpu_moe"}


def compare_data(rs: list[dict], clouds: list[dict], ranks: dict, local: dict, look: dict, data: dict, rel: dict) -> dict:
    """Everything the compare page needs for any pair: the ranked local models, then the Claude models for reference."""
    models = []
    for r in sorted(rs, key=lambda r: ranks[r["id"]][0]) + clouds:
        rid, cloud = r["id"], r in clouds
        pool = (report.ranked_now(rid, "cloud" if cloud else "box") or {}).get("rows") or []
        fam = {}
        for x in pool:
            fam.setdefault(x.get("family") or ".".join(x["id"].split(".")[:3]), []).append(x["score"])
        rec = local.get(rid) or {}
        lo, hi = _range_pct(r) if r.get("vs_ref") is not None else (None, None)
        bd = sorted((report._depth_k(k), d) for k, d in ((r.get("speed") or {}).get("by_depth") or {}).items() if d.get("decode_tps"))
        col, kind = look.get(rid, ("#8b877b", "release"))
        models.append({"id": rid, "name": model_name(r), "quant": "" if cloud else _quant(r.get("file")).split(" ")[0], "col": col, "kind": kind,
                       "cloud": cloud, "place": None if cloud else ranks[rid][0], "vs": r.get("vs_ref"), "lo": lo, "hi": hi, "cap": r["capability"], "ci": r["ci"],
                       "blocks": r["blocks"], "t2": None if cloud else (r.get("speed") or {}).get("decode_tps"),
                       "td": None if cloud or report._deep(r["speed"]) == "-" else float(report._deep(r["speed"])),
                       "depth": [[k, round(d["decode_tps"], 1), round(k * 1000 / d["prefill_tps"], 1) if d.get("prefill_tps") else None] for k, d in bd],
                       "fam": {f: round(sum(v) / len(v), 2) for f, v in fam.items()}, "set": _flat_settings(rec.get("recipe") or {}) if rec else {},
                       "rel": rel.get(rid)})
    from ..import suite
    return {"models": models, "groups": GROUPS, "weights": suite.WEIGHTS, "short": SHORT, "tasks": TASK_NAMES,
            "recipes": data["recipes"], "gpus": data["gpus"], "ref": data["ref"]}


def compare_app(cdata: dict) -> str:
    """One page for any two models (the pair in the address: compare.html#a-vs-b): a verdict in words that says when a
    difference is inside the margin of error, the four uses and nine blocks side by side, speed and fit on the
    visitor's box, the tasks only one of them solved, the settings that differ."""
    opts = ("<optgroup label='Local models, by place'>" + "".join(f"<option value='{esc(m['id'])}'>{m['place']}. {esc(m['name'])} · {esc(m['quant'])}</option>" for m in cdata["models"] if not m["cloud"])
            + "</optgroup><optgroup label='Cloud, for reference'>" + "".join(f"<option value='{esc(m['id'])}'>{esc(m['name'])}</option>" for m in cdata["models"] if m["cloud"]) + "</optgroup>")
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / compare</div><h1>Compare two models</h1>
 <div class="pick"><span class="dot a"></span><select id="ma" aria-label="first model">{opts}</select><button class="btn sw" id="swap" type="button" title="swap">⇄</button>
 <span class="dot b"></span><select id="mb" aria-label="second model">{opts}</select></div></div></section>
<section class="panel verdict2" id="verdict"></section>
<section class="panel pad"><div class="lbl">Side by side</div><div id="dumb"></div>
 <p class="q" style="margin-top:14px">Out of 100. The number on the right is the gap; gaps under 6 points are within what a few more tasks could change. <a href="method.html">How scores work</a></p></section>
<div class="two2"><section class="panel pad"><div class="lbl" id="spdlbl">Speed as the context grows</div><div id="speed"></div></section>
<section class="panel pad"><div class="lbl">Tasks only one of them solved</div><div id="only"></div></section></div>
<section class="panel"><div class="lbl">Settings that differ</div><div class="tw" id="sets"></div></section>'''
    js = PLAN_JS + f"\nconst DATA = {json.dumps(cdata)};\n" + _CMP_JS
    return _page("llmbox · compare two models", "COMPARE", body, _CMP_CSS, js)


_CMP_JS = r"""
const $ = s => document.querySelector(s), M = Object.fromEntries(DATA.models.map(m => [m.id, m]));
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const wavg = (b, bs) => { let s = 0, n = 0; for (const k of bs) if (b[k] != null) { s += b[k] * DATA.weights[k]; n += DATA.weights[k]; } return n ? s / n : null; };
const se = (m, up) => Math.max(0.3, (up ? m.ci[1] - m.cap : m.cap - m.ci[0]) / 1.96);
const surely = (a, b) => a.cap - b.cap > 1.96 * Math.hypot(se(a, false), se(b, true));   // as the ranking: outside the difference's own 95% margin
const box = savedBox(DATA);
function speedOf(m) {   // measured on the reference PC, or predicted for the visitor's box
  if (m.cloud) return null;
  const sh = DATA.recipes[m.id];
  if (box && sh && !sameClassAs(box, DATA.ref)) { const f = forBox(sh, box); return { t2: f.fits ? f.t2 : null, td: f.fits ? f.td : null, fits: f.fits, ctx: f.ctx, pred: true }; }
  return { t2: m.t2, td: m.td, fits: true, ctx: sh ? sh.ctx : null, pred: false };
}
const nm = m => `${esc(m.name)}${m.quant ? ` <span class="qt">${esc(m.quant)}</span>` : ""}`;
function render(a, b) {
  const A = M[a], B = M[b];
  $("#ma").value = a; $("#mb").value = b;
  history.replaceState(null, "", `#${a}-vs-${b}`);
  document.title = `llmbox · ${A.name} vs ${B.name}`;
  // the verdict: score first (with the margin), then speed and fit on the box, then the uses that differ
  const sA = speedOf(A), sB = speedOf(B), say = [];
  if (A.vs != null && B.vs != null) {
    const [hi, lo] = A.cap >= B.cap ? [A, B] : [B, A];
    say.push(surely(hi, lo) ? `<b>${esc(hi.name)}</b> is measurably better: ${Math.round(hi.vs)}% against ${Math.round(lo.vs)}% of Claude Opus 5.5.`
      : `Not measurably apart yet: ${Math.round(A.vs)}% and ${Math.round(B.vs)}% of Claude Opus 5.5, and their ranges overlap (${Math.round(A.lo)}–${Math.round(A.hi)} and ${Math.round(B.lo)}–${Math.round(B.hi)}).`);
  }
  if (sA && sB && sA.t2 && sB.t2) { const [f, s] = sA.t2 >= sB.t2 ? [[A, sA], [B, sB]] : [[B, sB], [A, sA]], k = f[1].t2 / s[1].t2;
    say.push(k < 1.1 ? `About as fast as each other${sA.pred ? " on your box" : ""} (${Math.round(sA.t2)} and ${Math.round(sB.t2)} tok/s).`
      : `<b>${esc(f[0].name)}</b> writes ${k.toFixed(1)}× faster${f[1].pred ? " on your box" : ""}: ${sA.pred ? "~" : ""}${Math.round(f[1].t2)} against ${Math.round(s[1].t2)} tok/s.`); }
  for (const [m, s] of [[A, sA], [B, sB]]) if (s && !s.fits) say.push(`<b>${esc(m.name)}</b> does not fit on your box.`);
  if (A.cloud || B.cloud) say.push(`Claude runs in the cloud: it is here as a reference for quality, with no speed on your box.`);
  const gaps = DATA.groups.map(([g, bs]) => [g, wavg(A.blocks, bs) - wavg(B.blocks, bs)]).filter(x => Math.abs(x[1]) >= 6);
  if (gaps.length) say.push("Clear gaps: " + gaps.map(([g, d]) => `${esc(g.toLowerCase())} favours <b>${esc((d > 0 ? A : B).name)}</b> (${d > 0 ? "+" : "−"}${Math.round(Math.abs(d))})`).join("; ") + ".");
  const tile = (m, s, cls) => `<div class="side ${cls}"><div class="who"><span class="dot ${cls}"></span>${m.cloud ? nm(m) : `<a href="recipe-${m.id}.html">${nm(m)}</a>`}</div>` +
    `<div class="nums"><div><b>${m.vs != null ? Math.round(m.vs) + "%" : "—"}</b><span>${m.cloud ? "cloud reference" : `place ${m.place} · range ${Math.round(m.lo)}–${Math.round(m.hi)}`}</span></div>` +
    `<div><b>${s && s.t2 ? (s.pred ? "~" : "") + Math.round(s.t2) : "—"}</b><span>${m.cloud ? "cloud" : s && !s.fits ? "does not fit" : `tok/s ${s.pred ? "on your box" : "measured"}`}</span></div>` +
    `<div><b>${s && s.fits && s.ctx ? "✓ " + Math.round(s.ctx / 1024) + "k" : s && !s.fits ? "✗" : "—"}</b><span>${m.cloud ? "" : "context that fits"}</span></div>` +
    `<div><b>${m.rel ? m.rel[0] : "—"}</b><span>${m.rel ? `of ${m.rel[1]} answers with thinking problems` : ""}</span></div></div></div>`;
  $("#verdict").innerHTML = `<div class="sides">${tile(A, sA, "a")}${tile(B, sB, "b")}</div><p class="say">${say.join(" ")}</p>`;
  // side by side: the overall score, then each use with its blocks, on a scale from just under the lowest value to 100
  const vals = [A.vs, B.vs, ...Object.values(A.blocks), ...Object.values(B.blocks)].filter(v => v != null);
  const lo0 = Math.max(0, Math.floor((Math.min(...vals) - 5) / 10) * 10), X = v => (Math.max(v, lo0) - lo0) / (100 - lo0) * 100;
  const row = (label, x, y, cls) => { const d = x != null && y != null ? x - y : null;
    return `<div class="dr ${cls}"><span class="dl">${label}</span><span class="dv">${x != null ? Math.round(x) : "—"}</span><span class="dt">` +
      (x != null ? `<i class="a" style="left:${X(x)}%"></i>` : "") + (y != null ? `<i class="b" style="left:${X(y)}%"></i>` : "") +
      (x != null && y != null ? `<em style="left:${X(Math.min(x, y))}%;width:${X(Math.max(x, y)) - X(Math.min(x, y))}%"></em>` : "") + `</span><span class="dv">${y != null ? Math.round(y) : "—"}</span>` +
      `<span class="dd ${d == null || Math.abs(d) < 6 ? "" : d > 0 ? "wa" : "wb"}">${d == null ? "" : Math.abs(d) < 0.5 ? "=" : (d > 0 ? "+" : "−") + Math.round(Math.abs(d))}</span></div>`; };
  let ticks = ""; for (let v = lo0; v <= 100; v += 10) ticks += `<span style="left:${X(v)}%">${v}</span>`;
  let h = `<div class="dr hd"><span></span><span class="dv"><span class="dot a"></span></span><span class="dt axis">${ticks}</span><span class="dv"><span class="dot b"></span></span><span></span></div>`;
  h += row("% of Claude Opus 5.5", A.vs, B.vs, "top");
  for (const [g, bs] of DATA.groups) { h += row(esc(g), wavg(A.blocks, bs), wavg(B.blocks, bs), "g");
    if (bs.length > 1) for (const k of bs) h += row(esc(DATA.short[k]), A.blocks[k], B.blocks[k], "k"); }
  $("#dumb").innerHTML = h;
  // speed as the context grows: both on the reference PC (what was measured)
  const deps = A.depth.length ? A.depth : B.depth;
  if (A.cloud || B.cloud || !deps.length) $("#speed").innerHTML = `<p class="q">${A.cloud || B.cloud ? "A cloud model has no speed on a box." : "No speed probe recorded."}</p>`;
  else { const top = Math.max(...A.depth.map(x => x[1]), ...B.depth.map(x => x[1])) * 1.1;
    const near = (m, k) => m.depth.reduce((p, x) => Math.abs(x[0] - k) < Math.abs(p[0] - k) ? x : p, m.depth[0]);
    $("#speed").innerHTML = deps.map(([k]) => { const x = near(A, k), y = near(B, k);
      return `<div class="sd"><div class="k">${k <= 4 ? "Short chat" : k <= 40 ? "Long session" : "Big document"}<small>${Math.round(k)}k tokens in context</small></div><div>` +
        [[A, x, "a"], [B, y, "b"]].map(([m, v, c]) => `<div class="sb ${c}"><i style="width:${100 * v[1] / top}%"></i><span>${Math.round(v[1])} tok/s${v[2] ? ` · first word ${v[2]} s` : ""}</span></div>`).join("") + `</div></div>`; }).join("") +
      `<p class="q" style="margin-top:12px">Measured on the reference PC.${box && !sameClassAs(box, DATA.ref) ? " Your box: see the tiles above." : ""}</p>`; }
  // tasks one solved and the other did not (same task kind and level; runs pick different tasks, so only the shared ones count)
  const only = (x, y) => Object.keys(x.fam).filter(f => f in y.fam && x.fam[f] >= 0.99 && y.fam[f] < 0.5).sort();
  const tname = f => { const p = f.split("."); return `${esc(DATA.tasks[p[0] + "." + p[1]] || p[1])} <span class="faint">· level ${p[2].slice(1)}</span>`; };
  const shared = Object.keys(A.fam).filter(f => f in B.fam).length;
  $("#only").innerHTML = `<div class="only"><div><div class="sc"><span class="dot a"></span>only ${esc(A.name)}</div><ul>${only(A, B).map(f => `<li>${tname(f)}</li>`).join("") || "<li class=q>none</li>"}</ul></div>` +
    `<div><div class="sc"><span class="dot b"></span>only ${esc(B.name)}</div><ul>${only(B, A).map(f => `<li>${tname(f)}</li>`).join("") || "<li class=q>none</li>"}</ul></div></div>` +
    `<p class="q" style="margin-top:10px">Out of ${shared} kinds of task both were given.</p>`;
  // the settings that differ
  const keys = [...new Set([...Object.keys(A.set), ...Object.keys(B.set)])].filter(k => A.set[k] !== B.set[k]).sort();
  $("#sets").innerHTML = A.cloud || B.cloud ? `<p class="q" style="padding:16px">Cloud models have no local settings.</p>` : keys.length ?
    `<table><tr><th class="l">SETTING</th><th class="l"><span class="dot a"></span>${esc(A.name)}</th><th class="l"><span class="dot b"></span>${esc(B.name)}</th></tr>` +
    keys.map(k => `<tr><td class="l">${esc(k)}</td><td class="l amb">${esc(A.set[k] ?? "default")}</td><td class="l">${esc(B.set[k] ?? "default")}</td></tr>`).join("") + "</table>"
    : `<p class="q" style="padding:16px">Same settings.</p>`;
}
function fromHash() {
  const p = location.hash.slice(1).split("-vs-"), ids = DATA.models.filter(m => !m.cloud).map(m => m.id);
  return p.length === 2 && M[p[0]] && M[p[1]] && p[0] !== p[1] ? p : [ids[0], ids[1]];
}
$("#ma").addEventListener("change", () => { let b = $("#mb").value; if (b === $("#ma").value) b = DATA.models.find(m => m.id !== b).id; render($("#ma").value, b); });
$("#mb").addEventListener("change", () => { let a = $("#ma").value; if (a === $("#mb").value) a = DATA.models.find(m => m.id !== a).id; render(a, $("#mb").value); });
$("#swap").addEventListener("click", () => render($("#mb").value, $("#ma").value));
addEventListener("hashchange", () => render(...fromHash()));
render(...fromHash());
"""


_CMP_CSS = """
.pick{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:14px}
.pick select{background:#0b0c09;color:var(--ink);border:1px solid var(--line);padding:7px 10px;font:13px "IBM Plex Mono";max-width:100%}
.pick select:focus{border-color:var(--amber);outline:none}.pick .sw{padding:6px 12px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px;vertical-align:0}.dot.a{background:var(--amber)}.dot.b{border:2px solid var(--ink)}
.sides{display:grid;grid-template-columns:1fr 1fr}.side{padding:18px 22px}.side.a{border-right:1px solid var(--line2)}
.side .who{font:600 18px "IBM Plex Sans Condensed";color:var(--ink)}.side .who a{color:var(--ink)}.side .qt{font:400 12px "IBM Plex Mono";color:var(--faint);margin-left:6px}
.side .nums{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:12px}.side .nums b{display:block;font:300 28px "IBM Plex Mono";color:var(--ink)}
.side.a .nums>div:first-child b{color:var(--amber)}.side .nums span{font-size:11.5px;color:var(--muted)}
.verdict2 .say{border-top:1px solid var(--line2);padding:14px 22px;font-size:14px;line-height:1.7;color:var(--soft)}.verdict2 .say b{color:var(--ink);font-weight:500}
.dr{display:grid;grid-template-columns:minmax(120px,190px) 36px minmax(0,1fr) 36px 44px;gap:12px;align-items:center;padding:5px 0}
.dr.hd{padding:0 0 4px}.dr.hd .dt{height:16px}.dr .dl{font-size:12.5px;color:var(--soft)}.dr.g{margin-top:10px;border-top:1px solid var(--line2);padding-top:12px}.dr.g .dl{font:600 15px "IBM Plex Sans Condensed";color:var(--ink)}
.dr.top .dl{font:600 15px "IBM Plex Sans Condensed";color:var(--amber)}.dr.k .dl{padding-left:14px;color:var(--muted)}
.dr .dv{font:500 14px "IBM Plex Mono";text-align:center;color:var(--ink)}.dr.k .dv{font-size:13px;color:var(--soft)}
.dr .dt{position:relative;height:14px;background:linear-gradient(var(--line),var(--line)) 0 50%/100% 1px no-repeat}.dr.hd .dt{background:none}
.dr .dt i{position:absolute;top:2px;width:10px;height:10px;margin-left:-5px;border-radius:50%;z-index:2}.dr .dt i.a{background:var(--amber)}.dr .dt i.b{border:2px solid var(--ink);background:var(--bg);z-index:1}
.dr .dt em{position:absolute;top:6px;height:2px;background:rgba(255,176,0,.35)}
.dr .dd{font-size:12.5px;color:var(--faint);text-align:right}.dr .dd.wa{color:var(--amber)}.dr .dd.wb{color:var(--ink)}
.two2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px}
.sd{display:grid;grid-template-columns:130px minmax(0,1fr);gap:14px;align-items:center;margin-bottom:12px}.sd .k{font:600 14px "IBM Plex Sans Condensed"}.sd .k small{display:block;font:400 11px "IBM Plex Mono";color:var(--muted)}
.sb{position:relative;height:22px;background:var(--line2)}.sb+.sb{margin-top:3px}.sb i{position:absolute;left:0;top:0;bottom:0}.sb.a i{background:rgba(255,176,0,.55)}.sb.b i{background:rgba(232,228,216,.22)}
.sb{overflow:hidden}.sb span{position:absolute;left:8px;top:2px;font-size:12px;white-space:nowrap}
.only{display:grid;grid-template-columns:1fr 1fr;gap:18px}.only .sc{margin-bottom:8px;text-transform:none;letter-spacing:0;font-size:12.5px;color:var(--soft)}
.only li{list-style:none;font-size:12.5px;color:var(--soft);padding:3px 0}
@media (max-width:900px){.two2,.only,.sides{grid-template-columns:minmax(0,1fr)}.sd{grid-template-columns:minmax(0,1fr);gap:6px}.side.a{border-right:0;border-bottom:1px solid var(--line2)}.side .nums{grid-template-columns:1fr 1fr}
 .dr{grid-template-columns:minmax(0,1fr) 30px minmax(0,1.3fr) 30px 36px;gap:8px}.pick select{width:calc(100% - 26px)}.pick .sw{order:9}}
"""
