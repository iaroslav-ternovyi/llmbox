// compare any two models: the pair lives in the address (compare.html#a-vs-b)
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
