// the box picked on the home page, named in every page's header
(function () {   // the box picked on the home page, named in every page's header
  try { const s = JSON.parse(localStorage.getItem("llmbox-box") || "null"), c = document.getElementById("boxchip");
    if (c && s && s.gpu) c.querySelector("b").textContent = s.gpu + (/^Mac/.test(s.gpu) ? ` · ${s.ram} GB` : ` · ${s.ram} GB RAM`); } catch (e) {}
})();
