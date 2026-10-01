// how fast a model is on other computers: the visitor's box first, filters
const $ = s => document.querySelector(s);
const saved = savedBox(DATA);
const refBox = { name: "the reference PC", gpu: DATA.ref.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: DATA.ref.ram, rambw: DATA.ref.rambw };
const box = saved || refBox;
const cur = forBox(DATA.sh, box);
const rough = hw => hw.uni || forBox(DATA.sh, hw).gf > 0.6;   // most experts on the GPU, or unified memory: outside the measured regime
if (saved) { $("#advbox").textContent = `What would make your box faster · ${boxLabel(box)}`;
  $("#yourbox").innerHTML = cur.fits ? `Your box (${boxLabel(box)}): <b>~${Math.round(cur.t2)} tok/s</b> in a short chat, <b>~${Math.round(cur.td)}</b> with a long document, up to ${Math.round(cur.ctx / 1024)}k context.`
    : `Your box (${boxLabel(box)}): the model does not fit, even with a smaller context.`; }
else $("#yourbox").innerHTML = `Pick your graphics card or Mac <a href="index.html#box">on the home page</a> and this page shows your box first.`;
function card(title, hw, note) {
  const f = forBox(DATA.sh, hw), g = f.t2 / cur.t2;
  const txt = g > 1.05 ? `+${Math.round((g - 1) * 100)}%` : g < 0.95 ? `${Math.round((g - 1) * 100)}%` : "±0%";
  return `<div><h2>${title}</h2><div class="g ${Math.abs(g - 1) < 0.05 ? "no" : ""}">${txt}</div><p>~${Math.round(f.t2)} tok/s. ${note}${rough(hw) ? ' <span class="src">rough</span>' : ""}</p></div>`;
}
const faster = DATA.ramKinds.map(r => r[1]).filter(v => v >= box.rambw * 1.2)[0];   // a step worth buying
const gp = n => { const g = DATA.gpus.find(x => x[0].startsWith(n)); return { gpu: n, vram: g[1], vrambw: g[2], ram: box.ram, rambw: box.rambw }; };
if (box.mac) { $("#adv").innerHTML = `<div><h2>A Mac with more memory bandwidth</h2><p>On a Mac the whole model sits in unified memory: its bandwidth sets the speed (Max and Ultra chips have 2–4× a base chip's).</p></div>`; }
else if (box.apu) { $("#adv").innerHTML = `<div><h2>More of the memory for the GPU</h2><p>On a Ryzen AI Max the whole model sits in unified memory at 256 GB/s: more memory fits bigger models, it does not make them faster.</p></div>`; }
else $("#adv").innerHTML = (faster ? card(`Faster RAM (${faster} GB/s)`, Object.assign({}, box, { rambw: faster }), "Same card, faster memory.") : "<div><h2>Faster RAM</h2><p class='q'>already at the fastest common speed</p></div>")
  + card("A 16 GB card (RTX 5070 Ti)", gp("RTX 5070 Ti"), "More of the model on the graphics card.")
  + card("A 24 GB card (RTX 4090)", gp("RTX 4090"), "Most of the model on the graphics card.");
$("#why").textContent = DATA.sh.moe ? `A mixture-of-experts model: what does not fit on the graphics card runs from system RAM, so on small cards the RAM speed, not the GPU, sets the pace. At ${Math.round(cur.gf * 100)}% of the experts on the card now.`
  : "A dense model: every weight is read for every token, so it has to fit on the graphics card; the card's memory speed sets the pace.";
// every box: NVIDIA cards with the visitor's RAM (or 64 GB at the reference PC's RAM speed), then Macs
const ram = saved && !saved.uni ? saved.ram : 65536, rambw = saved && !saved.uni ? saved.rambw : DATA.ref.rambw;
const m = DATA.measured, rows = [];
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
// computers people measured (llmbox submit), a row per hardware class: the median machine, the spread from 5 machines on
for (const c of DATA.community) {
  const [card, ...rest] = c.label.split(" · ");
  rows.push({ k: c.class.endsWith("|metal") ? "mac" : c.class.endsWith("|vulkan") ? "amd" : "nv", name: card, measured: true, t2: c.t2, td: c.t80 || c.t32,
              sub: [rest.join(" · "), `${plural(c.machines, "machine")}${c.people > 1 ? ` of ${c.people} people` : ""}${c.ref ? (c.machines > 1 ? ", the reference PC among them" : " (the reference PC)") : ""}`,
                    c.p5 ? `90% between ${Math.round(c.p5)} and ${Math.round(c.p95)} tok/s` : ""].filter(Boolean).join(" · "),
              f: c.ref ? forBox(DATA.sh, refBox) : { fits: true, gf: 0, ctx: c.ctx || DATA.sh.ctx } });
}
if (!DATA.community.length) rows.push({ k: "nv", name: `${m.gpu}`, sub: `${Math.round(m.ram / 1024)} GB RAM · ${m.rambw} GB/s`, measured: true, t2: m.t2, td: m.td, f: forBox(DATA.sh, refBox) });
for (const g of DATA.gpus) {
  const gk = gpuKind(g), uni = gk === "mac" || gk === "apu", hw = boxFrom(g, uni ? g[4] : ram / 1024, rambw), f = forBox(DATA.sh, hw);
  rows.push({ k: gk === "apu" ? "amd" : gk, name: g[0], sub: uni ? `${g[4]} GB unified · ${g[2]} GB/s` : `${Math.round(ram / 1024)} GB RAM · ${rambw} GB/s`, measured: false,
              t2: f.fits ? f.t2 : null, td: f.fits ? f.td : null, f, rough: rough(hw), mine: saved && saved.name === g[0] });
}
const vmax = Math.max(...rows.map(r => r.t2 || 0)) * 1.05;
let kind = "all";
function draw() {
  const only = $("#fitonly").checked;
  const show = rows.filter(r => (kind === "all" || r.k === kind) && (!only || r.f.fits || r.measured)).sort((a, b) => (b.mine - a.mine) || (b.measured - a.measured) || ((b.t2 || 0) - (a.t2 || 0)));
  const nfit = rows.filter(r => r.f.fits || r.measured).length;
  $("#hwnote").textContent = `fits on ${nfit} of ${rows.length}`;
  $("#boxlbl").textContent = `Computers · graphics cards with ${Math.round(ram / 1024)} GB RAM at ${rambw} GB/s${saved && !saved.uni ? " (your RAM)" : ""}`;
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
