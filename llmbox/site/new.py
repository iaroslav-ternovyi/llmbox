"""New models for the visitor's box (new.html)."""
from __future__ import annotations

import json
import re

from .. import report
from .layout import _page, PLAN_JS
from .stats import _axis_of, _range_pct
from .words import _quant, esc, family, model_name


def new_page(rs: list[dict], data: dict, host: str) -> str | None:
    """What came out in the last six months that runs on the visitor's box: releases, fine-tunes and remixes pulled by
    enough people (llmbox/candidates.py), each with the file this box runs well (4-bit first, a smaller quantization
    when the 4-bit file does not fit), its predicted speed, and its score - measured here when it was, otherwise the
    range expected from public benchmarks. Measured models are listed with their result, not hidden."""
    import datetime as _dt
    from ..import watch as W
    first_seen = W.first_seen()
    week_ago = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()
    from ..import candidates as C, estimate as E, fit as F, recipe as rc
    cs = C.load()
    if not cs:
        return None
    cutoff = (_dt.date.today() - _dt.timedelta(days=C.RECENT_DAYS)).isoformat()
    from ..import eci
    table = eci.load()
    ref_cap = next((r["capability"] / (r["vs_ref"] / 100) for r in rs if r.get("vs_ref")), None)
    by_base: dict = {}
    chains: dict = {}
    measured: dict = {}   # candidate key -> measured row
    by_name: dict = {}    # the same without the org: a repo whose base tags could not be read still matches its model
    for r in rs:
        try:
            repo = rc.load(host, r["id"])["model"].get("hf_repo") or ""
        except (OSError, ValueError):
            continue
        chains[r["id"]] = C.base_chain(repo)
        # an anchor must BE the scored model (a quantization of it): a fine-tune of a base is a different model
        hit = eci.match(repo, table) if not eci.is_remix(repo) else None
        if hit:
            by_base.setdefault(hit[0], (hit[1]["eci"], []))[1].append((r["id"], r["capability"]))
        measured.setdefault(C.key_of(repo), []).append(dict(r, repo=repo))
        by_name.setdefault(C._key(re.sub(r"-gguf(-mtp)?$", "", repo, flags=re.I)).split("/")[-1], []).append(dict(r, repo=repo))
    anchors = ([(f"{eci.FRONTIER_PROXY} (stands in for the reference)", table[eci.FRONTIER_PROXY]["eci"], ref_cap)]
               if ref_cap and eci.FRONTIER_PROXY in table else [])
    anchors += [(f"{name} via {', '.join(i for i, _ in ms)}", e, sum(c for _, c in ms) / len(ms)) for name, (e, ms) in by_base.items()]
    pred = eci.predictor(table, anchors, ref_cap) if ref_cap else None
    kv = "q8_0"

    def shp(sh: E.ModelShape) -> dict:
        return {"moe": sh.is_moe, "nonexp": sh.nonexpert_bytes, "exp": sh.expert_bytes, "embed": sh.embed_bytes,
                "layers": sh.n_layers, "nExp": sh.n_expert, "nUsed": sh.n_expert_used,
                "rec": sh.recurrent_state_bytes + sh.kv_swa_bytes(kv), "cpuEff": sh.expert_cpu_eff,
                "kvB": sh.kv_bytes_per_token(kv), "ctx": sh.context_length or 32768, "k2": 1, "kd": 1, "deepK": 32}

    def expected(repo: str):
        hit = eci.match(repo, table) if pred else None
        if not hit:
            return None
        mid, lo, hi = pred(hit[1]["eci"], hit[1]["lo"], hit[1]["hi"])
        return {"mid": round(mid), "lo": round(lo), "hi": round(hi), "name": hit[0], "eci": hit[1]["eci"], "remix": eci.is_remix(repo)}

    def mrow(m: dict) -> dict:
        sp = m["speed"] or {}
        lo, hi = _range_pct(m) if m.get("vs_ref") is not None else (None, None)
        return {"rid": m["id"], "name": model_name(m), "vs": m.get("vs_ref"), "lo": lo, "hi": hi, "cap": m["capability"], "t2": sp.get("decode_tps"),
                "td": float(report._deep(sp)) if report._deep(sp) != "-" else None}

    rows, seen = [], set()
    for c in cs:
        sh = E.ModelShape(**c["shape"])
        own = C.base_chain(c["repo"])
        roots = set(own[:2]) | {re.sub(r"-gguf$", "", c["repo"], flags=re.I)}   # the repo and the model it packages
        lin = c.get("lineage") or {"kind": "release", "of": None, "model": c["base"]}
        key = C._key(lin.get("model") or c["base"])
        ms = (measured.get(key) or measured.get(C._key(c["base"])) or by_name.get(key.split("/")[-1])
              or by_name.get(C._key(re.sub(r"-gguf(-mtp)?$", "", c["repo"], flags=re.I)).split("/")[-1]) or [])
        seen |= {m["id"] for m in ms}
        # measured fine-tunes of this model (declared on Hugging Face): context, not a prediction - they are other models
        rel = [(rid, (r.get("vs_ref") or 0)) for r in rs for rid in [r["id"]] if rid in chains and roots & set(chains[rid][1:]) and rid not in {m["id"] for m in ms}]
        seen_on = first_seen.get(key)
        rows.append({"repo": c["repo"], "rid": C.recipe_id(c["repo"]), "released": c.get("released") or c.get("created"),
                     "fresh": bool(seen_on and seen_on >= week_ago),
                     "dl": c["downloads"], "total": round(sh.total_params / 1e9, 1), "active": round(sh.active_params / 1e9, 1),
                     "kind": lin["kind"], "of": lin.get("of"), "rel": rel, "guess": expected(c["repo"]), "col": family(getattr(sh, "arch", None))[1],
                     "measured": [mrow(m) for m in ms], "bytes0": c["bytes"], "sh": shp(sh),
                     "ladder": [{"file": x["file"], "quant": x["quant"], "bytes": x["bytes"]} for x in (c.get("ladder") or [{"file": c["file"], "quant": c["quant"], "bytes": c["bytes"]}])]})
    # measured models released in the window that the popularity cut left out: listed with their result
    for r in rs:
        if r["id"] in seen or r["id"] not in data["recipes"]:
            continue
        try:
            rec = rc.load(host, r["id"])
        except (OSError, ValueError):
            continue
        repo = rec["model"].get("hf_repo") or ""
        rel_day = C.released(repo) if repo else None
        if not rel_day or rel_day < cutoff:
            continue
        sh = F.shape_for(rec)
        lin = C.lineage(repo)
        rows.append({"repo": repo, "rid": r["id"], "released": rel_day, "dl": None, "total": round(sh.total_params / 1e9, 1),
                     "active": round(sh.active_params / 1e9, 1), "kind": lin["kind"], "of": lin.get("of"), "rel": [], "col": family(sh.arch)[1],
                     "guess": expected(repo), "measured": [mrow(r)], "bytes0": sh.total_bytes or 1, "sh": shp(sh),
                     "ladder": [{"file": rec["model"].get("file") or "", "quant": _quant(rec["model"].get("file") or ""), "bytes": sh.total_bytes or 1}]})
    body = f"""
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / new</div><h1>New models for your box</h1>
 <p class="q" style="margin-top:6px">Released in the last six months and pulled by enough people: releases, fine-tunes and uncensored remixes,
 one entry per model. Fit and speed are for <b id="boxname">the reference PC</b> (<a href="index.html#box">pick your box</a>). Measured models show
 their score; the rest show the range expected from public benchmarks.</p></div></section>
<p class="nsum" id="nsum"></p>
<section class="panel"><div class="lbl"><span id="count">{len(rows)}</span> models</div>
 <div class="nctl"><div class="chips" role="group" aria-label="show">
  <button class="on" data-f="fit" aria-pressed="true">fits my box</button><button class="on" data-f="release" aria-pressed="true">releases</button>
  <button class="on" data-f="fine-tune" aria-pressed="true">fine-tunes</button><button class="on" data-f="uncensored" aria-pressed="true">uncensored</button></div>
  <label class="q">sort <select id="sort"><option value="rel">newest</option><option value="exp">score</option><option value="speed">speed on your box</option><option value="dl">most downloaded</option></select></label></div>
 <div class="nhd"><span>MODEL</span><div class="fp"><div class="trk axis" id="nax"></div><span class="num">SCORE</span></div><span class="r">TOK/S</span><span>FILE THAT FITS</span><span></span></div>
 <div id="rows"></div>
 <p class="q nlg"><b class="dotm"></b> measured here · <b class="doth"></b> expected from public benchmarks (a fine-tune or remix: its base model's range) · % of Claude Opus 5.5 on this site's tasks</p></section>"""
    note = ("<section class='panel pad'><div class='lbl'>Where the expected score comes from</div><p class='q' style='max-width:900px;line-height:1.7'>"
            "A measured model shows its own score. Otherwise the model's "
            "<a href='https://epoch.ai/benchmarks'>Epoch Capabilities Index</a> (one number fitted over many public benchmarks), put on our scale by a "
            "straight line through models that have both: " + "; ".join(f"{esc(n)}: ECI {e:.0f} = {c / ref_cap * 100:.0f}%" for n, e, c in anchors)
            + ". A fine-tune or remix gets its base model's range: training can move it either way. A 3- or 2-bit file scores lower than the 4-bit "
            "one the range is for. ECI data: Epoch AI, 'Capabilities &amp; benchmarking', epoch.ai/benchmarks, CC BY 4.0.</p></section>") if pred else ""
    body += note
    js = PLAN_JS + "\nconst DATA = " + json.dumps(dict(ref=data["ref"], gpus=data["gpus"], ramKinds=data["ramKinds"], rows=rows, eciReady=bool(pred),
                                                     ax=_axis_of([r.get("vs_ref") for r in rs]))) + ";\n" + _NEW_JS
    return _page("llmbox · new models", "NEW", body, _NEW_CSS, js)


_NEW_CSS = """
.nsum{margin:22px 0 0;font-size:14px;color:var(--soft)}.nsum b{color:var(--ink);font-weight:500}
.nctl{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:10px 18px;padding:18px 16px 8px}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chips button{background:none;border:1px solid var(--line);color:var(--faint);font:12px "IBM Plex Mono";padding:5px 10px;cursor:pointer}
.chips button.on{color:var(--amber);border-color:var(--amber-dim)}.chips button:before{content:"✓ ";opacity:0}.chips button.on:before{opacity:1}
.nctl select{background:#0b0c09;color:var(--ink);border:1px solid var(--line);padding:5px 8px;font:12px "IBM Plex Mono";margin-left:6px}
.nhd,.nr2{display:grid;grid-template-columns:minmax(220px,1.3fr) minmax(0,1.7fr) 110px 170px 110px;gap:14px;align-items:center;padding:10px 16px}
.nhd{font-size:11px;letter-spacing:.12em;color:var(--muted);border-bottom:1px solid var(--line);padding-top:4px}.nhd .num{font:inherit;color:inherit;width:auto}.nhd .trk{height:16px}
.nr2{border-bottom:1px solid var(--line2)}.nr2.nofit{opacity:.45}.r{text-align:right}
.nr2 .mw .m{font:600 15px "IBM Plex Sans Condensed";color:var(--ink);white-space:normal;overflow-wrap:anywhere}.nr2 .mw .qt{line-height:1.5}.nr2 .mw .qt a{color:var(--faint)}
.tag{font-size:10px;letter-spacing:.1em;color:var(--bg);background:var(--amber);padding:1px 6px;margin-left:8px;vertical-align:2px;white-space:nowrap}
.fp .trk b.h{background:var(--bg)!important;border:2px solid}.fp .trk i.d{background:none!important;border-top:2px dashed;height:0;opacity:.6}
.nr2 .fp .num{font-size:15px}.nr2 .fp .num.e{color:var(--soft)}
.nr2 .sp b{display:block;font:500 15px "IBM Plex Mono";color:var(--ink)}.nr2 .sp small{font-size:11px;color:var(--faint)}
.nr2 .fl{font-size:12.5px;color:var(--soft)}.nr2 .fl small{display:block;font-size:11px;color:var(--faint)}.nr2 .fl .low{color:#c49a5a}.nr2 .fl .no{color:var(--red)}
.nr2 .go{font-size:11px;padding:5px 10px;white-space:nowrap;justify-self:end}
.cmds{grid-column:1/-1;background:#0b0c09;border:1px solid var(--line);padding:12px 14px;font-size:12.5px;line-height:1.8;color:var(--soft)}
.cmds code{display:block;color:var(--amber);overflow-wrap:anywhere}.cmds code:before{content:"$ ";color:var(--faint)}
.nlg{padding:12px 16px 16px;margin:0}.nlg b{display:inline-block;width:10px;height:10px;border-radius:50%;margin:0 4px -1px 0}.nlg .dotm{background:var(--amber)}.nlg .doth{border:2px solid var(--amber)}
@media (max-width:900px){.nhd{display:none}.nr2{grid-template-columns:minmax(0,1fr) auto;row-gap:8px}.nr2 .fp{grid-column:1/-1;grid-row:2}
 .nr2 .sp{grid-row:3}.nr2 .fl{grid-row:3;grid-column:2;text-align:right}.nr2 .go{grid-column:2;grid-row:1}}
"""


_NEW_JS = r"""
const $ = s => document.querySelector(s);
const saved = savedBox(DATA);
const box = saved || { name: "the reference PC", gpu: DATA.ref.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: DATA.ref.ram, rambw: DATA.ref.rambw };
$("#boxname").textContent = saved ? `your box (${boxLabel(box)})` : `the reference PC (${DATA.ref.gpu} · ${Math.round(DATA.ref.ram / 1024)} GB)`;
const fmtDl = n => n == null ? "" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : n;
const cap = v => v > 200 ? "200+" : "~" + Math.round(v);   // above ~200 the formula ignores per-token overheads
const bits = q => { const m = q.replace("UD-", "").toUpperCase().match(/(?:I?Q|BF|F)(\d+)/); return m ? +m[1] : 16; };
const ago = d => { const n = Math.round((Date.now() - new Date(d)) / 864e5); return n < 1 ? "today" : n < 45 ? `${n} d ago` : `${Math.round(n / 30)} mo ago`; };
const [amin, astep] = DATA.ax, X = v => Math.max(0, Math.min(100, (v - amin) / (100 - amin) * 100));
let axl = ""; for (let v = amin; v <= 100; v += astep) axl += `<span style="left:${X(v)}%">${v}</span>`;
$("#nax").innerHTML = axl;
function pick(r) {   // the file this box runs well: 4-bit first, then the largest 3- or 2-bit file; 32k context or more
  let first = null;
  for (const f of r.ladder) {
    const k = f.bytes / r.bytes0, sh = Object.assign({}, r.sh, { nonexp: r.sh.nonexp * k, exp: r.sh.exp * k, embed: r.sh.embed * k });
    const p = forBox(sh, box);
    if (p.fits && !first) first = { f, p };
    if (p.fits && p.ctx >= 32768) return { f, p };
  }
  return first || { f: r.ladder[0], p: null };
}
const rows = DATA.rows.map(r => { const c = pick(r), m = r.measured[0];
  const onRef = !saved && m && m.t2;   // on the reference PC a measured model shows what was measured
  const t2 = onRef ? m.t2 : c.p ? c.p.t2 : 0, td = onRef ? m.td : c.p ? c.p.td : 0;
  const score = m ? m.vs : r.guess ? r.guess.mid : null;
  return Object.assign({}, r, { c, m, fits: !!c.p, t2, td, onRef, score, low: c.p && bits(c.f.quant) < 4 }); });
const nweek = rows.filter(r => (Date.now() - new Date(r.released)) / 864e5 <= 14);
$("#nsum").innerHTML = `In the last two weeks: <b>${nweek.length}</b> new model${nweek.length === 1 ? "" : "s"}, <b>${nweek.filter(r => r.fits).length}</b> fit ${saved ? "your box" : "the reference PC"}, <b>${nweek.filter(r => r.m).length}</b> measured here.`;
function scoreCell(r) {
  const g = () => { let t = ""; for (let v = amin; v <= 100; v += astep) t += `<s style="left:${X(v)}%"></s>`; return t; };
  if (r.m && r.m.vs != null) return `<div class="fp" title="measured: 95% range ${Math.round(r.m.lo)}–${Math.round(r.m.hi)}%"><div class="trk">${g()}<i style="left:${X(r.m.lo)}%;width:${X(r.m.hi) - X(r.m.lo)}%;background:${r.col}"></i><b style="left:${X(r.m.vs)}%;background:${r.col}"></b></div><span class="num">${Math.round(r.m.vs)}%</span></div>`;
  if (!r.guess) return `<div class="fp"><div class="trk">${g()}</div><span class="num e q" title="not in the public benchmark index">—</span></div>`;
  return `<div class="fp" title="expected from the Epoch Capabilities Index of ${r.guess.name} (${r.guess.eci.toFixed(1)})${r.guess.remix || r.kind !== "release" ? ", its base model" : ""}"><div class="trk">${g()}<i class="d" style="left:${X(r.guess.lo)}%;width:${X(r.guess.hi) - X(r.guess.lo)}%;border-color:${r.col}"></i><b class="h" style="left:${X(r.guess.mid)}%;border-color:${r.col}"></b></div><span class="num e">~${r.guess.mid}%</span></div>`;
}
const marker = r => r.kind === "uncensored" ? `<svg class="mk" width="14" height="14"><path d="M7 1L13 7L7 13L1 7Z" fill="none" stroke="${r.col}" stroke-width="2"/></svg>`
  : `<svg class="mk" width="14" height="14"><circle cx="7" cy="7" r="5" fill="${r.kind === "fine-tune" ? "none" : r.col}" stroke="${r.col}" stroke-width="2"/></svg>`;
const on = new Set(["fit", "release", "fine-tune", "uncensored"]);
const keys = { rel: r => +new Date(r.released), exp: r => r.score ?? -1, speed: r => r.t2 || 0, dl: r => r.dl || 0 };
function render() {
  const k = keys[$("#sort").value];
  const list = rows.filter(r => (!on.has("fit") || r.fits) && on.has(r.kind === "release" || r.kind === "fine-tune" || r.kind === "uncensored" ? r.kind : "release")).sort((a, b) => (k(b) - k(a)) || ((b.dl || 0) - (a.dl || 0)));
  $("#count").textContent = list.length;
  $("#rows").innerHTML = list.map((r, i) => { const name = r.repo.split("/")[1].replace(/-GGUF(-MTP)?$/i, "");
    const sub = [ago(r.released), `${r.total}B${r.active < r.total ? `, ${r.active}B active` : ""}`, r.of ? `${r.kind} of ${r.of.split("/").pop()}` : r.kind === "release" ? "" : r.kind, r.dl ? `${fmtDl(r.dl)} downloads` : ""].filter(Boolean).join(" · ");
    return `<div class="nr2${r.fits ? "" : " nofit"}"><div class="mw">${marker(r)}<span class="m">${r.m ? `<a href="recipe-${r.m.rid}.html" style="color:inherit">${name}</a>` : name}${r.fresh ? '<span class="tag" title="first listed here in the last 7 days">NEW THIS WEEK</span>' : ""}</span>` +
      `<span class="qt">${sub} · <a href="https://huggingface.co/${r.repo}" rel="noopener">HF</a>${r.rel.length ? `<br>fine-tunes measured: ${r.rel.map(x => `<a href="recipe-${x[0]}.html">${x[0]}</a> ${Math.round(x[1])}%`).join(", ")}` : ""}</span></div>` +
      scoreCell(r) +
      `<div class="sp r">${r.fits ? `<b>${r.onRef ? Math.round(r.t2) : cap(r.t2)}</b><small>${r.td ? (r.onRef ? "" : "~") + Math.round(r.td) + " at 32k" : ""}${r.onRef ? " · measured" : ""}</small>` : "—"}</div>` +
      `<div class="fl">${r.fits ? `✓ ${r.c.f.quant} · ${(r.c.f.bytes / 1e9).toFixed(1)} GB<small>${Math.round(r.c.p.ctx / 1024)}k context${r.low ? ` · <span class="low">${bits(r.c.f.quant)}-bit: expect a lower score</span>` : ""}</small>` : `<span class="no">✗ too big</span><small>${r.c.f.quant} · ${(r.c.f.bytes / 1e9).toFixed(1)} GB</small>`}</div>` +
      (r.m ? `<a class="btn go" href="recipe-${r.m.rid}.html">RESULTS →</a>` : `<button class="btn go" data-i="${i}">HOW TO TEST ▾</button>`) +
      (r.m ? "" : `<div class="cmds" id="c${i}" hidden>Draft the recipe for this file, download it, tune the speed, run the 40-minute adaptive test:` +
      `<code>llmbox recipe new ${r.repo} --file ${r.c.f.file.split("/").pop()} --write</code><code>llmbox install ${r.rid} --apply</code><code>llmbox optimize ${r.rid}</code>` +
      `<code>llmbox bench ${r.rid} --recipe ${r.rid} --adaptive --budget 40 --speed-probe</code></div>`) + "</div>"; }).join("");
  document.querySelectorAll(".go[data-i]").forEach(b => b.addEventListener("click", () => { const c = $("#c" + b.dataset.i); c.hidden = !c.hidden; }));
}
document.querySelectorAll(".chips button").forEach(b => b.addEventListener("click", () => { const f = b.dataset.f;
  on.has(f) ? on.delete(f) : on.add(f); b.classList.toggle("on", on.has(f)); b.setAttribute("aria-pressed", String(on.has(f))); render(); }));
$("#sort").addEventListener("change", render);
render();
"""
