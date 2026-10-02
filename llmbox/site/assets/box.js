// numbers as the pages written in Python show them: an exact half to the even neighbour (84.5 -> 84), so a score or a
// speed reads the same on the home page (drawn here) and on the model page (written by the build)
function rnd(v) { const f = Math.floor(v), d = v - f; return d > 0.5 ? f + 1 : d < 0.5 ? f : f % 2 ? f + 1 : f; }
window.llmboxCount = window.llmboxCount || function () {};   // stats.js counts events; without it (no counter set up) nothing
(function () {   // an uncaught error in the site's scripts goes to the server: message, page, build - no address (privacy.html)
  const me = document.currentScript, api = me && me.dataset.err;
  if (!api || !navigator.sendBeacon) return;
  let sent = 0;
  window.addEventListener("error", e => {
    if (sent++ >= 3 || !e.filename || new URL(e.filename, location.href).origin !== location.origin) return;   // ours only, a few a page
    navigator.sendBeacon(api, JSON.stringify({ msg: String(e.message).slice(0, 300), page: location.pathname, build: me.dataset.build || "",
                                               src: `${new URL(e.filename).pathname}:${e.lineno}:${e.colno}` }));
  });
})();
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
