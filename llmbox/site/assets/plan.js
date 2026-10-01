// what a model needs on a box and how fast it runs there: the same model llmbox fit uses (the pages' box picker)
function plan(sh, hw, ctx, depth, buf = 2100, cpuK = 1, gpuK = 1) {   // buf: compute buffer MiB for -ub 2048 / 1024 / 512 = 2100 / 1300 / 900
  const mib = 1 / 1048576, kv = sh.kvB * ctx + sh.rec, gpuFixed = (sh.nonexp + kv) * mib + buf + 700, free = hw.vram - gpuFixed;
  let gf, ramUsed, fits, perCpu, perGpu;
  // unified memory (Mac, Ryzen AI Max) as llmbox/hosts.spec has it: the GPU's share is the "VRAM", the rest is "RAM" at the
  // same speed, where llama.cpp keeps the experts that do not fit the GPU's share (read by the CPU)
  if (!sh.moe) { const need = gpuFixed + sh.embed * mib; fits = need <= hw.vram; gf = 1; ramUsed = sh.embed * mib; perGpu = sh.nonexp; perCpu = 0; }
  else { gf = Math.max(0, Math.min(1, free / (sh.exp * mib))); const cpuExp = sh.exp * (1 - gf); ramUsed = (cpuExp + sh.embed) * mib;
         fits = free > -1 && ramUsed + 4096 <= hw.ram; const fr = sh.nUsed / sh.nExp; perCpu = cpuExp * fr; perGpu = sh.nonexp + sh.exp * gf * fr; }
  // the card side as llmbox/estimate.py DECODE (efficiency, fixed ms per layer), fitted to public llama-bench runs: CUDA dense
  // 90% + 0.035 ms, CUDA MoE 72% + 0.06 ms; Metal 80% + 0.1 ms (community M4 Pro / M5 Max runs of 35B-A3B MoE models).
  // hw.gen: older NVIDIA generations reach less of their bandwidth (estimate.gpu_generation)
  // Vulkan (AMD): 80% + 0.022 ms dense (seven Radeon cards and a Ryzen AI Max+), MoE as CUDA's with 0.05 ms
  const [eff, ovh] = hw.mac ? (sh.moe ? [0.8, 0.1] : [0.9, 0.14]) : hw.backend === "vulkan" ? (sh.moe ? [0.72, 0.05] : [0.8, 0.022])
                     : sh.moe ? [0.72, 0.06] : [0.9, 0.035];
  const tps = d => 1 / (cpuK * perCpu / (hw.rambw * 1e9 * 0.8 * sh.cpuEff) + gpuK * (perGpu + sh.kvB * d + (sh.swaB || 0) * (d ? Math.min(sh.swaW, d) : sh.swaW)) / (hw.vrambw * 1e9 * eff * (hw.gen || 1))
                        + sh.layers * ovh / 1000);
  return { fits, gf, ramUsed, buf, vram: Math.min(hw.vram, gpuFixed + (sh.moe ? sh.exp * gf * mib : sh.embed * mib)), t2: tps(2000), td: tps(Math.min(depth, ctx)) };
}
function sameClassAs(hw, r) { return hw.gpu === r.gpu && Math.abs(hw.rambw - r.rambw) / r.rambw < 0.15 && hw.ram >= r.ram * 0.9; }
function forBox(sh, hw) {   // as llmbox fit: the recipe's context if it fits, else halve it; a smaller prompt batch before a smaller context
  let p = null, ctx = sh.ctx;
  for (let c = sh.ctx; c >= 8192 && !(p && p.fits); c = c / 2)
    for (const buf of [2100, 1300, 900]) { p = plan(sh, hw, c, sh.deepK * 1000, buf); ctx = c; if (p.fits) break; }
  // the reference box's calibration (fit.Calibration): not a Mac's. term "": a ratio of the whole speed (speculative
  // decoding); "cpu" / "gpu": a factor on the time of what the box read from RAM / on its card, so a card that holds the
  // whole model is not scaled by what the box's RAM did
  if (hw.uni || !p.fits) return Object.assign(p, { ctx });
  if (!sh.term) return Object.assign(p, { ctx, t2: p.t2 * sh.k2, td: p.td * sh.kd });
  const k = (t, x) => plan(sh, hw, ctx, sh.deepK * 1000, p.buf, t === "cpu" ? x : 1, t === "gpu" ? x : 1);
  return Object.assign(p, { ctx, t2: k(sh.term, sh.k2).t2, td: k(sh.term, sh.kd).td });
}
function gpuKind(g) { return g[3] === "mac" ? "mac" : g[3] === "apu" ? "apu" : g[4] === "vulkan" ? "amd" : "nv"; }   // a picker entry's kind
function boxFrom(g, ramGB, rambw) {   // a picker entry and the RAM fields -> what plan() needs
  if (g[3] === "mac" || g[3] === "apu") { const mem = Math.min(ramGB, g[4]) * 1024, mac = g[3] === "mac";
    // macOS lets the GPU use ~2/3 (small Macs) to 3/4 of unified memory; a Ryzen AI Max up to 96 of its 128 GB
    const vram = mem * (mac && mem < 36864 ? 0.67 : 0.75);
    return { name: g[0], gpu: g[0], mac, apu: !mac, uni: true, backend: mac ? "metal" : "vulkan", mem,
             vram, vrambw: g[2], ram: mem - vram, rambw: g[2], gen: g[5] || 1 }; }
  return { name: g[0], gpu: g[0].replace(/ \d+ GB$/, ""), vram: g[1], vrambw: g[2], ram: ramGB * 1024, rambw,
           gen: typeof g[3] === "number" ? g[3] : 1, backend: g[4] === "vulkan" ? "vulkan" : "cuda", amd: g[4] === "vulkan" };
}
function boxLabel(b) { return b.uni ? `${b.name} · ${Math.round(b.mem / 1024)} GB unified · ${b.rambw} GB/s` : `${b.name} · ${Math.round(b.ram / 1024)} GB · ${b.rambw} GB/s`; }
function savedBox(DATA) {
  try { const s = JSON.parse(localStorage.getItem("llmbox-box") || "null"); if (!s || !s.gpu) return null;
    const g = DATA.gpus.find(x => x[0] === s.gpu); if (!g) return null;
    return boxFrom(g, parseInt(s.ram), parseFloat(s.bwn) || parseFloat(s.bw)); } catch (e) { return null; }
}
function classOf(hw) {   // the hardware class of a picked box, as llmbox/hwclass.py keys people's measurements (not Macs yet)
  const g = DATA.gpuClass && DATA.gpuClass[hw.name];
  if (!g || hw.uni) return null;
  const e = DATA.ramEdges; let lo = 0, b = null;
  for (const x of e) { if (hw.rambw < x) { b = lo ? `ram-${lo}-${x}` : `ram-under-${x}`; break; } lo = x; }
  return `${g}|${b || `ram-${lo}-plus`}|${hw.backend === "vulkan" ? "vulkan" : "cuda"}`;
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
