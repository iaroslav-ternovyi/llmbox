// what a model needs on a box and how fast it runs there: the same model llmbox fit uses (the pages' box picker)
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
function classOf(hw) {   // the hardware class of a picked box, as llmbox/hwclass.py keys people's measurements (not Macs yet)
  const g = DATA.gpuClass && DATA.gpuClass[hw.name];
  if (!g || hw.mac) return null;
  const e = DATA.ramEdges; let lo = 0, b = null;
  for (const x of e) { if (hw.rambw < x) { b = lo ? `ram-${lo}-${x}` : `ram-under-${x}`; break; } lo = x; }
  return `${g}|${b || `ram-${lo}-plus`}|cuda`;
}
function measuredFor(rid, hw) {   // [t2, deep, machines, people] people measured on this box's class, or null
  const k = hw && classOf(hw), c = k && DATA.cm && DATA.cm[rid];
  return c && c[k] ? c[k] : null;
}

function browserGpu(gpus) {   // the graphics card this browser reports (WebGL renderer), matched to a picker entry; nothing is sent
  try {
    const gl = document.createElement("canvas").getContext("webgl"), ext = gl && gl.getExtension("WEBGL_debug_renderer_info");
    const r = (ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : "").toLowerCase();   // "ANGLE (NVIDIA, NVIDIA GeForce RTX 5070 ..." / "... Apple M2 Max ..."
    if (!r) return null;
    const base = g => g[0].toLowerCase().replace(/ \d+ gb$/, "").replace(/^mac /, "apple ");
    const hits = gpus.filter(g => r.includes(base(g)));
    if (!hits.length) return null;
    hits.sort((a, b) => base(b).length - base(a).length);   // "rtx 4070 ti super" before "rtx 4070"
    return hits.length > 1 && base(hits[0]) === base(hits[1]) ? null : hits[0];
  } catch (e) { return null; }
}
