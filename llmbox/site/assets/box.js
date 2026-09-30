// the box picked on the home page, named in every page's header
(function () {   // the box picked on the home page, named in every page's header
  try { const s = JSON.parse(localStorage.getItem("llmbox-box") || "null"), c = document.getElementById("boxchip");
    if (c && s && s.gpu) c.querySelector("b").textContent = s.gpu + (/^Mac/.test(s.gpu) ? ` · ${s.ram} GB` : ` · ${s.ram} GB RAM`); } catch (e) {}
})();
// the header's SIGN IN becomes the account's name once this browser signed in (account.html)
try {
  const a = JSON.parse(localStorage.getItem("llmbox-account") || "null"), el = document.getElementById("signin");
  if (a && a.handle && el) { el.textContent = (a.public && a.login ? a.login : a.handle).toUpperCase(); el.title = "your llmbox account"; }
} catch (e) {}
