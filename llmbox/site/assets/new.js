// new models for the visitor's box: the file that fits, speed, filters, sort
const $ = s => document.querySelector(s);
const saved = savedBox(DATA);
const box = saved || { name: "our test PC", gpu: DATA.ref.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: DATA.ref.ram, rambw: DATA.ref.rambw };
$("#boxname").textContent = saved ? `your box (${boxLabel(box)})` : `our test PC (${DATA.ref.gpu} · ${Math.round(DATA.ref.ram / 1024)} GB)`;
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
    // scaled to this file's real size: a shape read from a split file's first part can count a fraction of the weights
    // (a 97.5 GB file whose parts summed to 68.7 GB "fitted" a 73 GB PC)
    const k = f.bytes / ((r.sh.nonexp + r.sh.exp + r.sh.embed) || r.bytes0), sh = Object.assign({}, r.sh, { nonexp: r.sh.nonexp * k, exp: r.sh.exp * k, embed: r.sh.embed * k });
    const p = forBox(sh, box);
    if (p.fits && !first) first = { f, p };
    if (p.fits && p.ctx >= 32768) return { f, p };
  }
  return first || { f: r.ladder[0], p: null };
}
const rows = DATA.rows.map(r => { const c = pick(r), m = r.measured[0];
  const onRef = !saved && m && m.t2;   // on our test PC a measured model shows what was measured
  const t2 = onRef ? m.t2 : c.p ? c.p.t2 : 0, td = onRef ? m.td : c.p ? c.p.td : 0;
  const score = m ? m.vs : r.guess ? r.guess.mid : null;
  return Object.assign({}, r, { c, m, fits: !!c.p, t2, td, onRef, score, low: c.p && bits(c.f.quant) < 4 }); });
const nweek = rows.filter(r => (Date.now() - new Date(r.released)) / 864e5 <= 14);
$("#nsum").innerHTML = `In the last two weeks: <b>${nweek.length}</b> new model${nweek.length === 1 ? "" : "s"}, <b>${nweek.filter(r => r.fits).length}</b> fit ${saved ? "your box" : "our test PC"}, <b>${nweek.filter(r => r.m).length}</b> measured here.`;
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
    const sub = [ago(r.released), `${r.total}B${r.active < 0.7 * r.total ? `, ${r.active}B active` : ""}`, r.of ? `${r.kind} of ${r.of.split("/").pop()}` : r.kind === "release" ? "" : r.kind, r.dl ? `${fmtDl(r.dl)} downloads` : ""].filter(Boolean).join(" · ");
    return `<div class="nr2${r.fits ? "" : " nofit"}"><div class="mw">${marker(r)}<span class="m">${r.m ? `<a href="recipe-${r.m.rid}.html" style="color:inherit">${name}</a>` : name}${r.fresh ? '<span class="tag" title="first listed here in the last 7 days">NEW THIS WEEK</span>' : ""}</span>` +
      `<span class="qt">${sub} · <a href="https://huggingface.co/${r.repo}" rel="noopener">HF</a>${r.rel.length ? `<br>fine-tunes measured: ${r.rel.map(x => `<a href="recipe-${x[0]}.html">${x[0]}</a> ${Math.round(x[1])}%`).join(", ")}` : ""}</span></div>` +
      scoreCell(r) +
      `<div class="sp r">${r.fits ? `<b>${r.onRef ? Math.round(r.t2) : cap(r.t2)}</b><small>${r.td ? (r.onRef ? Math.round(r.td) : cap(r.td)) + " at 32k" : ""}${r.onRef ? " · measured" : ""}</small>` : "—"}</div>` +
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
