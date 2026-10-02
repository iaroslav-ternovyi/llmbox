// the home page: the chart, the ranking by use, the box picker, compare ticks
const $ = s => document.querySelector(s);
const fmt = v => v.toFixed(0);
const kfmt = c => `${Math.round(c / 1024)}k`;
function sameClass(hw) { const r = DATA.ref; return hw.gpu === r.gpu && Math.abs(hw.rambw - r.rambw) / r.rambw < 0.15 && hw.ram >= r.ram * 0.9; }
function weighted(b, w) { let s = 0, n = 0; for (const k in w) { s += (b[k] || 0) * w[k]; n += w[k]; } return n ? s / n : 0; }
function scatter(pts) {   // up = closer to Claude Opus, right = faster on the box picked. Names sit at their points.
  // a phone gets its own proportions (narrower and taller), not the desktop chart shrunk until its names are unreadable
  const narrow = (document.querySelector("#scatter") || {}).clientWidth < 700;
  const W = narrow ? 460 : 1000, H = narrow ? 600 : 540, L = narrow ? 44 : 58, R = narrow ? 346 : 812, T = 26, B = narrow ? 536 : 478;   // the right margin names the Claude lines
  const loc = pts.filter(p => !p.cloud && p.t2 && p.vs != null), cloud = pts.filter(p => p.cloud && p.vs != null).sort((a, b) => b.vs - a.vs);
  if (!loc.length) return "";
  const fams = Object.fromEntries(DATA.families), col = p => fams[p.fam] || "#9AA0A6";
  // y: just under the weakest model at >= 50% of the frontier; weaker ones sit on the floor with an arrow
  const main = loc.filter(p => p.vs >= 50), lowest = Math.min(...(main.length ? main : loc).map(p => p.vs));
  const ymin = Math.max(0, Math.floor((lowest - 6) / 5) * 5), ystep = 100 - ymin > 40 ? 10 : 5;
  const xs = loc.map(p => p.t2), span = Math.max(10, Math.max(...xs) - Math.min(...xs));
  const xstep = span > 150 ? 50 : span > 60 ? 20 : span > 25 ? 10 : 5;
  const xmin = Math.max(0, Math.floor((Math.min(...xs) - span * 0.12) / xstep) * xstep), xmax = Math.ceil((Math.max(...xs) + span * 0.12) / xstep) * xstep;
  const X = v => L + (v - xmin) / (xmax - xmin) * (R - L), Y = v => B - (Math.max(v, ymin) - ymin) / (100 - ymin) * (B - T);
  const front = new Set(loc.filter(p => !loc.some(q => q !== p && q.t2 >= p.t2 && q.vs >= p.vs && (q.t2 > p.t2 || q.vs > p.vs))).map(p => p.id));
  let g = "";
  for (let v = ymin; v <= 100; v += ystep) g += `<line x1="${L}" y1="${Y(v)}" x2="${R}" y2="${Y(v)}" class="gl"/><text x="${L - 10}" y="${Y(v) + 4}" text-anchor="end" class="ax">${v}%</text>`;
  for (let v = xmin; v <= xmax; v += xstep) g += `<text x="${X(v)}" y="${B + 22}" text-anchor="middle" class="ax">${v}</text>`;
  g += `<line x1="${L}" y1="${B}" x2="${R}" y2="${B}" class="axl"/>`;
  // the cloud models: reference lines, named in the right margin (labels pushed apart when close)
  let lastY = -99;
  cloud.forEach(c => { const y = Y(c.vs), ly = Math.max(y + 4, lastY + 15); lastY = ly;
    g += `<line x1="${L}" y1="${y}" x2="${R}" y2="${y}" class="ref"/><text x="${R + 8}" y="${ly}" class="refl">${narrow ? c.name.replace("Claude ", "") : c.name} <tspan class="refv">${Math.round(c.vs)}%</tspan></text>`; });
  // one model in several variants (quants): a line through them, slowest to fastest
  const series = {};
  loc.forEach(p => (series[p.model] = series[p.model] || []).push(p));
  Object.values(series).filter(s => s.length > 1).forEach(s => { s.sort((a, b) => a.t2 - b.t2);
    g += `<polyline points="${s.map(p => `${X(p.t2)},${Y(p.vs)}`).join(" ")}" fill="none" stroke="${col(s[0])}" stroke-width="2" stroke-opacity=".55"/>`; });
  // markers: filled = the maker's own release, ring = a fine-tune, diamond = an uncensored remix; faded = another model is smarter and faster
  const boxes = [];
  const order = loc.slice().sort((a, b) => (front.has(b.id) - front.has(a.id)) || b.vs - a.vs);
  order.slice().reverse().forEach(p => { const x = X(p.t2), y = Y(p.vs), c = col(p), op = front.has(p.id) ? 1 : .42, low = p.vs < ymin;
    const mk = p.kind === "uncensored" ? `<path d="M${x} ${y - 7}L${x + 7} ${y}L${x} ${y + 7}L${x - 7} ${y}Z" fill="#0E0F0C" stroke="${c}" stroke-width="2.2"/>`
      : `<circle cx="${x}" cy="${y}" r="6.5" fill="${p.kind === "fine-tune" ? "#0E0F0C" : c}" stroke="${c}" stroke-width="2.2"${p.pred ? ' stroke-dasharray="3 2"' : ""}/>`;
    g += `<g class="pt" data-id="${p.id}" opacity="${op}">${mk}${low ? `<path d="M${x - 4} ${y + 11}L${x + 4} ${y + 11}L${x} ${y + 17}Z" fill="${c}"/>` : ""}` +
         `<circle cx="${x}" cy="${y}" r="16" fill="transparent"/></g>`;
    boxes.push([x - 8, y - 8, x + 8, y + 8]); });
  // names next to their points: the first free spot of eight around it, the models on the frontier first
  const hit = (b) => b[0] < L + 2 || b[2] > R + 2 || b[1] < T - 14 || b[3] > B + 2 || boxes.some(o => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]);
  const sq = q => (q || "").replace(/^UD-/, "");
  const labelOf = p => { const s = series[p.model]; if (!s || s.length < 2) return p.model;
    return s.reduce((a, b) => (b.vs > a.vs ? b : a)) === p ? `${p.model} · ${sq(p.quant)}` : sq(p.quant); };
  // the series lines are obstacles for names too: sample points along them
  Object.values(series).filter(s => s.length > 1).forEach(s => { for (let i = 1; i < s.length; i++) {
    const [a, b] = [s[i - 1], s[i]]; for (let t = 0.15; t < 0.9; t += 0.1) { const x = X(a.t2 + (b.t2 - a.t2) * t), y = Y(a.vs + (b.vs - a.vs) * t); boxes.push([x - 3, y - 3, x + 3, y + 3]); } } });
  order.forEach(p => { const x = X(p.t2), y = Y(p.vs), txt = labelOf(p) + (p.vs < ymin ? ` ${Math.round(p.vs)}%` : ""), w = txt.length * 7.7 + 4;   // IBM Plex Mono at 12.5: ~7.5 units a character
    const spots = [[x + 11, y + 4, "start"], [x - 11, y + 4, "end"], [x, y - 13, "middle"], [x, y + 21, "middle"],
                   [x + 10, y - 9, "start"], [x + 10, y + 17, "start"], [x - 10, y - 9, "end"], [x - 10, y + 17, "end"]];
    for (const [tx, ty, an] of spots) {
      const x0 = an === "start" ? tx : an === "end" ? tx - w : tx - w / 2, b = [x0, ty - 11, x0 + w, ty + 3];
      if (!hit(b)) { boxes.push(b); g += `<text x="${tx}" y="${ty}" text-anchor="${an}" class="lb${front.has(p.id) ? " on" : ""}" data-id="${p.id}">${txt}</text>`; return; }
    }
  });
  const famsUsed = [...new Set(loc.map(p => p.fam))];
  const legend = `<div class="clg">${famsUsed.map(f => `<span><i style="background:${fams[f]}"></i>${f}</span>`).join("")}` +
    `<span class="k"><svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="#8b877b"/></svg>release</span>` +
    `<span class="k"><svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="none" stroke="#8b877b" stroke-width="2"/></svg>fine-tune</span>` +
    `<span class="k"><svg width="14" height="14"><path d="M7 1L13 7L7 13L1 7Z" fill="none" stroke="#8b877b" stroke-width="2"/></svg>uncensored</span>` +
    `<span class="k"><svg width="22" height="10"><line x1="0" y1="5" x2="22" y2="5" stroke="#8b877b" stroke-width="2"/></svg>same model, other quant</span></div>`;
  return legend + `<svg viewBox="0 0 ${W} ${H}" class="scatter2" font-family="IBM Plex Mono" role="img" aria-label="score against speed">${g}` +
    `<text x="${(L + R) / 2}" y="${H - 8}" text-anchor="middle" class="axt">tokens per second on your box →</text>` +
    `<text transform="translate(16 ${(T + B) / 2}) rotate(-90)" text-anchor="middle" class="axt">↑ share of Claude Opus 5.5's score</text></svg><div class="ctip" hidden></div>`;
}
function hoverScatter(pts) {   // a model's point or name: its details in a tip, the others step back
  const box = document.querySelector("#scatter"), tip = box.querySelector(".ctip"), by = Object.fromEntries((pts || []).map(p => [p.id, p]));
  if (!tip) return;
  box.querySelectorAll(".pt, .lb").forEach(el => {
    el.addEventListener("mouseenter", () => { const p = by[el.dataset.id]; if (!p) return;
      box.classList.add("hov"); box.querySelectorAll(`[data-id="${p.id}"]`).forEach(x => x.classList.add("on2"));
      const k = p.vs / p.cap, lo = Math.round(p.ci[0] * k), hi = Math.min(100, Math.round(p.ci[1] * k));
      tip.innerHTML = `<b>${p.name}</b><span>${p.fam} · ${p.kind}</span><span>score <em>${Math.round(p.vs)}%</em> of Claude Opus (95%: ${lo}-${hi})</span>` +
        `<span>speed <em>${p.pred ? "~" : ""}${Math.round(p.t2)} tok/s</em>${p.pred ? " predicted for your box" : " measured"}${p.td ? ` · ${Math.round(p.td)} deep in context` : ""}</span>`;
      const r = box.getBoundingClientRect(), e = el.getBoundingClientRect();
      tip.hidden = false; tip.style.left = Math.min(r.width - 280, Math.max(0, e.left - r.left + 18)) + "px"; tip.style.top = (e.top - r.top - 8) + "px"; });
    el.addEventListener("mouseleave", () => { box.classList.remove("hov"); box.querySelectorAll(".on2").forEach(x => x.classList.remove("on2")); tip.hidden = true; });
    el.addEventListener("click", () => { location.href = `recipe-${el.dataset.id}.html`; });
  });
}
let resizeT = null, lastNarrow = null;   // the chart has phone and desktop proportions: redraw when the width crosses over
addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { const n = (document.querySelector("#scatter") || {}).clientWidth < 700;
  if (n !== lastNarrow) { lastNarrow = n; render(); } }, 200); });
let hwNow = null, preset = 0, sortBy = "score", sortDir = 1;   // any column with data-sort; a second click reverses it
document.querySelectorAll(".rank th[data-sort]").forEach(th => th.addEventListener("click", () => {
  sortDir = sortBy === th.dataset.sort ? -sortDir : 1; sortBy = th.dataset.sort;
  document.querySelectorAll(".rank th[data-sort]").forEach(t => t.classList.toggle("on", t === th)); render(); }));
const measured = {};
document.querySelectorAll("tr[data-rid]").forEach(r => measured[r.dataset.rid] = r.querySelector(".spd").innerHTML);
function axisOf(pts) {   // the score column's scale: as the chart's, from just under the weakest model at >= 50% to 100%
  const loc = pts.filter(p => !p.cloud && p.vs != null), main = loc.filter(p => p.vs >= 50);
  if (!loc.length) return null;
  const min = Math.max(0, Math.floor((Math.min(...(main.length ? main : loc).map(p => p.vs)) - 6) / 5) * 5);
  return { min, step: 100 - min > 40 ? 10 : 5, X: v => Math.max(0, Math.min(100, (v - min) / (100 - min) * 100)) };
}
function scoreCell(p, ax) {   // a dot at the score, a line over its 95% range; a Claude model: a dashed mark, its score being a reference
  if (p.vs == null || !ax) return "—";   // no reference model on this scale: no percent
  const c = p.cloud ? "#8b877b" : (Object.fromEntries(DATA.families)[p.fam] || "#9AA0A6");
  let g = ""; for (let v = ax.min; v <= 100; v += ax.step) g += `<s style="left:${ax.X(v)}%"></s>`;
  if (p.cloud) return `<div class="fp"><div class="trk">${g}<b class="rf" style="left:${ax.X(p.vs)}%"></b></div><span class="num">${Math.round(p.vs)}%</span></div>`;
  const k = p.vs / p.cap, lo = p.ci[0] * k, hi = Math.min(100, p.ci[1] * k);
  return `<div class="fp" title="95% range ${Math.round(lo)}–${Math.round(hi)}%"><div class="trk">${g}<i style="left:${ax.X(lo)}%;width:${ax.X(hi) - ax.X(lo)}%;background:${c}"></i>` +
    `<b style="left:${ax.X(p.vs)}%;background:${c}"></b>${p.vs < ax.min ? `<em>◂ ${Math.round(p.vs)}%</em>` : ""}</div><span class="num">${Math.round(p.vs)}%</span></div>`;
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function drawPick(pts) {   // the answer first: the best model for the picked box, and the one line that installs it
  // a model on an engine llmbox cannot install (ik_llama.cpp) is not what a newcomer's one line should start with
  const ok = pts.filter(p => !p.cloud && p.t2 && p.vs != null && ((DATA.recipes[p.id] || {}).engine || "llama.cpp") === "llama.cpp");
  if (!ok.length) { $("#pick").innerHTML = `<p class="q">No measured model fits this box.</p>`; return; }
  const rng = p => { const k = p.vs / p.cap; return [p.ci[0] * k, Math.min(100, p.ci[1] * k)]; };
  // as llmbox pick: the best score, unless a model not measurably apart from it, at most 5 points below, is 1.3x as fast
  const top = ok.reduce((a, b) => (b.vs > a.vs ? b : a));
  const long = p => Math.min(p.t2, p.td || p.t2);
  const quick = ok.filter(p => p !== top && rng(p)[1] >= rng(top)[0] && p.vs >= top.vs - 5 && long(p) >= 1.3 * long(top));
  const best = quick.length ? quick.reduce((a, b) => (long(b) > long(a) ? b : a)) : top;
  const why = best === top ? "the best score among the models that fit" :
    `${Math.round(top.vs - best.vs)} points below the best score, not measurably apart from it, and ${(long(best) / long(top)).toFixed(1)}× as fast here`;
  const cmd = `curl --proto '=https' --tlsv1.2 -fsSL ${DATA.site}/install.sh | sh -s -- ${best.id}`;   // words.CURL
  const use = document.querySelector(".seg button.on");
  $("#pick").innerHTML = `<div class="pk"><div><span class="sc">Best for this box${preset ? " · " + esc(use ? use.textContent.toLowerCase() : "") : ""}</span>` +
    `<h2><a href="recipe-${best.id}.html">${esc(best.model || best.name)}</a> <small>${esc(best.quant || "")}</small></h2>` +
    `<p><b>${Math.round(best.vs)}%</b> of Claude Opus · <b>${best.pred ? "~" : ""}${Math.round(best.t2)}</b> tokens/s` +
    (best.td ? `, ${Math.round(best.td)} with a long document` : "") +
    ` · ${why}</p></div>` +
    `<div class="pkc"><pre class="cmd" id="pickcmd">${esc(cmd)}</pre><button class="btn cpy" type="button" id="pickcpy">COPY</button>` +
    `<p class="q">installs llmbox and this model, with the settings measured here fitted to your computer, then starts it (Linux or a Mac` +
    (hwNow && (hwNow.amd || hwNow.apu) ? "; on AMD it installs the Vulkan build of llama.cpp, and timing there comes after launch" : "") + `; <a href="install.html">more</a>)</p></div></div>`;
  if ($("#pickcpy")) $("#pickcpy").onclick = () => (llmboxCount("copy-install/home"), navigator.clipboard) && navigator.clipboard.writeText(cmd).then(() => { $("#pickcpy").textContent = "COPIED"; setTimeout(() => $("#pickcpy").textContent = "COPY", 1500); });
}
function render() {
  const w = DATA.presets[preset], refW = weighted(DATA.refBlocks, w);
  const pts = DATA.points.map(p => Object.assign({}, p, preset ? { vs: 100 * weighted(p.blocks, w) / refW, cap: weighted(p.blocks, w), ci: [p.ci[0] / p.cap * weighted(p.blocks, w), p.ci[1] / p.cap * weighted(p.blocks, w)] } : {}));
  const ax = axisOf(pts);
  let lab = ""; if (ax) for (let v = ax.min; v <= 100; v += ax.step) lab += `<span style="left:${ax.X(v)}%">${v}</span>`;
  $(".rank .axis").innerHTML = lab;
  for (const p of pts) {
    const sh = DATA.recipes[p.id], row = document.querySelector(`tr[data-rid="${p.id}"]`);
    if (!row) continue;
    row.querySelector(".sco").innerHTML = scoreCell(p, ax);
    row.classList.remove("nofit");
    if (p.cloud) continue;   // no box to predict for
    const mm = hwNow && !sameClass(hwNow) && measuredFor(p.id, hwNow);
    if (mm) {   // people measured this model on machines of this class: their median, not a prediction
      p.t2 = mm[0]; p.td = mm[1]; p.pred = false;
      row.querySelector(".spd").innerHTML = `<b>${fmt(mm[0])}</b><small>${mm[1] ? fmt(mm[1]) + " long · " : ""}${mm[2]} machine${mm[2] > 1 ? "s" : ""}</small>`;
      row.querySelector(".fit").innerHTML = sh ? `✓ ${kfmt(forBox(sh, hwNow).ctx)}` : "✓";
    } else if (sh && hwNow && !sameClass(hwNow)) {
      const f = forBox(sh, hwNow); p.t2 = f.t2; p.td = f.td; p.pred = true;
      row.querySelector(".spd").innerHTML = f.fits ? `<b class="pred">~${fmt(f.t2)}</b><small>~${fmt(f.td)} long</small>` : "—";
      row.querySelector(".fit").innerHTML = f.fits ? `✓ ${kfmt(f.ctx)}` : `<span class="no">✗ too big</span>`;
      if (!f.fits) { row.classList.add("nofit"); p.t2 = null; }
    } else {
      row.querySelector(".spd").innerHTML = measured[p.id];
      row.querySelector(".fit").innerHTML = sh ? `✓ ${kfmt(sh.ctx)}` : "—";
    }
  }
  const loc = pts.filter(p => !p.cloud), nfit = loc.filter(p => p.t2).length;
  $("#fitsum").textContent = nfit === loc.length ? `all ${loc.length} models fit` : `${nfit} of ${loc.length} models fit`;
  const tb = $(".rank tbody");
  const key = sortBy === "speed" ? p => p.t2 || 0 : p => p.vs ?? -1;
  const by = (a, b) => sortDir * (key(b) - key(a)) || (b.vs ?? -1) - (a.vs ?? -1);
  const ranked = pts.filter(p => !p.cloud).sort((a, b) => (b.vs ?? -1) - (a.vs ?? -1)).map(p => p.id);
  const byScore = !preset && sortBy === "score" && sortDir === 1;   // group lines only make sense in the score order they were cut in
  let prevG = null;
  pts.slice().sort(by).forEach((p) => { const i = ranked.indexOf(p.id); const row = document.querySelector(`tr[data-rid="${p.id}"]`);
    if (p.cloud) { tb.appendChild(row); return; }
    row.querySelector(".rk").textContent = preset ? i + 1 : p.rank[0];
    row.classList.toggle("gs", byScore && prevG !== null && p.rank[3] !== prevG); prevG = p.rank[3];
    tb.appendChild(row); tb.appendChild(document.querySelector(`tr.prof[data-for="${p.id}"]`)); });
  drawPick(pts);
  $("#scatter").innerHTML = scatter(pts);
  hoverScatter(pts);
}
document.querySelectorAll(".rank tr.mr").forEach(tr => tr.addEventListener("click", e => {   // a row opens its nine block scores
  if (e.target.closest("a, label, input")) return;
  const pr = document.querySelector(`tr.prof[data-for="${tr.dataset.rid}"]`), open = pr.hidden;
  pr.hidden = !open; tr.classList.toggle("open", open); tr.querySelector(".exp").setAttribute("aria-expanded", String(open)); }));
const RAM0 = $("#ram").innerHTML;   // the RAM sizes for a PC; a Mac offers only the sizes its chip is sold with
function ramFor(g) {
  const was = parseInt($("#ram").value) || 64, sizes = g && g[6];
  $("#ram").innerHTML = sizes ? sizes.map(s => `<option value="${s}">${s} GB</option>`).join("") : RAM0;
  $("#ram").value = String(sizes ? macMem(sizes, was) : was);
  if (!$("#ram").value) $("#ram").value = "64";
}
function readBox() {
  const g = DATA.gpus.find(x => x[0] === $("#gpu").value);
  ramFor(g);
  ["#ram", "#bw", "#bwn"].forEach(s => $(s).disabled = !g);
  if (!g) { hwNow = null; $("#boxnote").textContent = "speeds measured on this box"; try { localStorage.removeItem("llmbox-box"); } catch (e) {} history.replaceState(null, "", location.pathname); render(); return; }
  const bw = parseFloat($("#bwn").value) || parseFloat($("#bw").value);
  hwNow = boxFrom(g, parseInt($("#ram").value), bw);
  ["#bw", "#bwn"].forEach(s => $(s).disabled = !!hwNow.uni);   // unified memory: its speed comes with the chip
  $("#boxnote").textContent = hwNow.mac ? "Mac: predicted for llama.cpp on Metal (~), rough until people with this Mac measure it" :
    hwNow.apu ? "Ryzen AI Max: unified memory, the whole model on the GPU; predicted for llama.cpp on Vulkan (~), nothing here is measured on one" :
    hwNow.amd ? "AMD: predicted for llama.cpp on Vulkan (~), fitted to public runs on Radeon cards; nothing here is measured on AMD" :
    sameClass(hwNow) ? "same class as the reference box: measured speeds" : "speeds predicted for this box (~)";
  try { localStorage.setItem("llmbox-box", JSON.stringify({ gpu: $("#gpu").value, ram: $("#ram").value, bw: $("#bw").value, bwn: $("#bwn").value })); } catch (e) {}
  history.replaceState(null, "", `#gpu=${encodeURIComponent(g[0])}&ram=${$("#ram").value}&bw=${bw}`);
  render();
}
$("#gpu").insertAdjacentHTML("beforeend", [["nv", "NVIDIA + system RAM"], ["amd", "AMD + system RAM"], ["apu", "AMD Ryzen AI Max (unified memory)"], ["mac", "Mac (unified memory)"]]
  .map(([k, label]) => `<optgroup label="${label}">${DATA.gpus.filter(g => gpuKind(g) === k).map(g => `<option>${g[0]}</option>`).join("")}</optgroup>`).join(""));
for (const r of DATA.ramKinds) $("#bw").insertAdjacentHTML("beforeend", `<option value="${r[1]}">${r[0]} · ${r[1]} GB/s</option>`);
$("#bw").value = String(DATA.ramKinds.reduce((a, r) => Math.abs(r[1] - DATA.ref.rambw) < Math.abs(a - DATA.ref.rambw) ? r[1] : a, DATA.ramKinds[0][1]));
["#gpu", "#ram", "#bwn"].forEach(s => $(s).addEventListener("change", readBox));
$("#gpu").addEventListener("change", () => llmboxCount(`pick/${$("#gpu").value || "reference"}`));   // which hardware people come with
$("#bw").addEventListener("change", () => { $("#bwn").value = ""; readBox(); });   // a preset replaces a typed-in measurement
document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".seg button").forEach(u => u.classList.remove("on")); b.classList.add("on"); preset = +b.dataset.p; render(); }));
const order = DATA.points.slice().sort((a, b) => b.cap - a.cap || (a.id < b.id ? -1 : 1)).map(p => p.id);   // the better model of a ticked pair goes first
document.querySelectorAll(".pick2 input").forEach(c => c.addEventListener("change", () => {
  const on = [...document.querySelectorAll(".pick2 input:checked")];
  if (on.length > 2) { on.filter(x => x !== c)[0].checked = false; }
  const ids = [...document.querySelectorAll(".pick2 input:checked")].map(x => x.value).sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const go = $("#cmpgo");
  if (ids.length === 2) { go.href = `compare.html#${ids[0]}-vs-${ids[1]}`; go.setAttribute("aria-disabled", "false"); go.classList.add("solid"); $("#cmpn").textContent = `${ids[0]} vs ${ids[1]}`; }
  else { go.removeAttribute("href"); go.setAttribute("aria-disabled", "true"); go.classList.remove("solid"); $("#cmpn").textContent = ids.length ? `${ids[0]} vs …` : "tick two models to compare"; }
}));
try { const h = Object.fromEntries(new URLSearchParams(location.hash.slice(1))); const saved = h.gpu ? h : JSON.parse(localStorage.getItem("llmbox-box") || "null");
  const guess = !saved || !saved.gpu ? browserGpu(DATA.gpus) : null;   // a first visit: the card this browser reports
  if (saved && saved.gpu) { $("#gpu").value = saved.gpu; if (saved.ram) $("#ram").value = saved.ram; if (saved.bw) { const o = [...$("#bw").options].find(o => o.value == saved.bw); if (o) $("#bw").value = saved.bw; else $("#bwn").value = saved.bw; } if (saved.bwn) $("#bwn").value = saved.bwn; readBox(); }
  else if (guess) { $("#gpu").value = guess[0]; readBox(); $("#boxnote").textContent = `${guess[0]}: what this browser reports - change it, and the RAM, if they are not yours`; }
  else { ["#ram", "#bw", "#bwn"].forEach(s => $(s).disabled = true); render(); }
} catch (e) { render(); }
$("#find").addEventListener("input", () => {   // find a model: rows whose name, quant or family match; Claude rows stay as references
  const q = $("#find").value.trim().toLowerCase(), fam = Object.fromEntries(DATA.points.map(p => [p.id, (p.fam || "").toLowerCase()]));
  document.querySelectorAll(".rank tr.mr").forEach(tr => { const hit = !q || tr.textContent.toLowerCase().includes(q) || (fam[tr.dataset.rid] || "").includes(q);
    tr.hidden = !hit; if (!hit) { const pr = document.querySelector(`tr.prof[data-for="${tr.dataset.rid}"]`); pr.hidden = true; tr.classList.remove("open"); } });
});
