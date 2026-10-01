// Visits and a few events, counted by llmbox's own GoatCounter: no cookies, nothing stored in the browser, no ids, no
// IP kept (privacy.html). Nothing at all when the browser asks not to be tracked (Global Privacy Control, Do Not Track).
// Pages call llmboxCount("copy-install") etc.; it does nothing when counting is off.
(function () {
  const el = document.currentScript, base = el && el.dataset.stats;
  window.llmboxCount = () => {};
  if (!base || navigator.globalPrivacyControl || navigator.doNotTrack === "1" || window.doNotTrack === "1") return;
  const send = q => { const i = new Image(); i.src = `${base}/count?${new URLSearchParams(Object.assign(q, { rnd: Math.random().toString(36).slice(2) }))}`; };
  const path = location.pathname.replace(/\.html$/, "").replace(/\/index$/, "/") || "/";
  send({ p: path, t: document.title, r: document.referrer, s: [screen.width, screen.height, Math.round(window.devicePixelRatio || 1)].join(",") });
  window.llmboxCount = name => send({ p: name, t: name, e: "true" });
})();
