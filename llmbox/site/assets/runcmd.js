// the run-it panel: tabs (the app remembered), copy, show all lines
document.querySelectorAll(".run").forEach(sec => {
  const pick = t => { sec.querySelectorAll(".rtabs button").forEach(x => x.classList.toggle("on", x.dataset.t === t));
    sec.querySelectorAll(".rp").forEach(x => x.classList.toggle("on", x.dataset.t === t)); };
  sec.querySelectorAll(".rtabs button").forEach(b => b.addEventListener("click", () => {
    pick(b.dataset.t); try { localStorage.setItem("llmbox-runtab", b.dataset.t); } catch (e) {} }));
  try { const t = localStorage.getItem("llmbox-runtab"); if (t && sec.querySelector(`.rtabs button[data-t="${t}"]`)) pick(t); } catch (e) {}   // the app the visitor uses
  sec.querySelectorAll(".more").forEach(b => { const all = b.textContent; b.addEventListener("click", () => { const pre = b.parentElement.querySelector("pre");
    pre.classList.toggle("clip"); b.textContent = pre.classList.contains("clip") ? all : "show fewer lines ▴"; }); });
  sec.querySelectorAll(".cpy").forEach(b => b.addEventListener("click", async () => {
    const t = b.parentElement.querySelector("pre").innerText;
    llmboxCount(`copy-run/${b.closest(".rp") ? b.closest(".rp").dataset.t : "?"}`);   // which app people run models with
    try { await navigator.clipboard.writeText(t); b.textContent = "COPIED"; } catch (e) { b.textContent = "SELECT AND COPY"; }
    setTimeout(() => b.textContent = "COPY", 1500); }));
});
