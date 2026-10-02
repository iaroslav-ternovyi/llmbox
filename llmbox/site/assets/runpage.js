// a person's run page (r/<id>): copy buttons, and "Your machine?" - the best model for the machine picked (text only)
const D = typeof document !== "undefined" ? document : null;
const r0 = typeof rnd === "function" ? rnd : Math.round;   // box.js's rounding on the site; the tests load this file alone in Node
for (const b of D ? D.querySelectorAll("[data-copy],[data-copy-from]") : []) {
  const label = b.textContent;
  b.onclick = () => {
    const src = b.dataset.copyFrom && document.getElementById(b.dataset.copyFrom);
    // the settings are one command line; the install commands stay two lines
    const text = !src ? b.dataset.copy : src.id === "argv" ? src.innerText.trim().replace(/\s*\n\s*/g, " ") : src.innerText.trim();
    navigator.clipboard.writeText(text).then(() => { b.textContent = "COPIED"; setTimeout(() => b.textContent = label, 1500); });
  };
}

function bestLine(m) {
  // m: [name, slug, kind, best model, number, measured, testable]
  if (!m) return null;
  const [name, slug, , model, n, measured, testable] = m;
  const num = n == null ? "" : (measured ? `${r0(n)} tok/s` : `~${r0(n)} tok/s predicted`);
  const line = model ? `best here: ${model}${num ? " · " + num : ""}` : "nothing fits at 64 GB of RAM";
  const after = testable ? "" : " · AMD timing comes after launch";
  return { line: line + after, page: `hw-${slug}.html`, feed: `feeds/${slug}.xml`, name };
}

const sel = D && D.getElementById("mach"), out = D && D.getElementById("mbest");
function show() {
  out.textContent = "";
  const b = bestLine(DATA.machines.find(m => m[1] === sel.value));
  if (!b) return;
  const p = D.createElement("p");
  p.textContent = b.line;
  const links = D.createElement("p");
  links.className = "q";
  const a = D.createElement("a");
  a.href = b.page; a.textContent = `${b.name} →`;
  const r = D.createElement("a");
  r.href = b.feed; r.textContent = "RSS";
  links.append(a, " · new results: ", r);
  out.append(p, links);
}
if (sel && out && typeof DATA !== "undefined") { sel.onchange = show; show(); }
if (typeof module !== "undefined") module.exports = { bestLine };
