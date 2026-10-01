// account.html: sign in with GitHub on the site, see and manage the account. The API is another origin than the site,
// so no cookie: the server hands back a one-time ticket in the #fragment, traded here for a key kept in this browser.
const API = DATA.api, $ = s => document.querySelector(s), esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const store = {
  get() { try { return JSON.parse(localStorage.getItem("llmbox-account") || "null"); } catch (e) { return null; } },
  set(v) { try { v ? localStorage.setItem("llmbox-account", JSON.stringify(v)) : localStorage.removeItem("llmbox-account"); } catch (e) {} },
};
async function call(path, body) {
  const a = store.get(), h = { "Content-Type": "application/json" };
  if (a && a.key) h.Authorization = "Bearer " + a.key;
  const r = await fetch(API + path, { method: body === undefined ? "GET" : "POST", headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(j.error || `HTTP ${r.status}`); e.status = r.status; throw e; }
  return j;
}
function header(me) {   // the header's SIGN IN shows who is signed in
  const el = document.getElementById("signin");
  if (el) el.textContent = me ? (me.public && me.login ? me.login : me.handle).toUpperCase() : "SIGN IN";
}
function signedOut(msg) {
  $("#acct-h").textContent = "Sign in";
  $("#acct-sub").textContent = "With your GitHub account. Your profile shows a handle, not your name, unless you choose otherwise.";
  const start = `${API}/api/v1/login/web/start?return=${encodeURIComponent(location.href.split("#")[0])}`;
  $("#acct").innerHTML = `<section class="panel pad">` + (msg ? `<p class="no">${esc(msg)}</p>` : "") +
    `<p><a class="btn solid" id="ghsignin" href="${esc(start)}">SIGN IN WITH GITHUB</a></p></section>`;
  $("#ghsignin").addEventListener("click", () => llmboxCount("signin"));
  header(null);
}
function signedIn(me) {
  const name = me.public && me.login ? me.login : me.handle;
  $("#acct-h").textContent = name;
  $("#acct-sub").innerHTML = `Signed in with GitHub as <b>${esc(me.login)}</b> · ${me.submissions} submission${me.submissions === 1 ? "" : "s"}, ${me.records} result${me.records === 1 ? "" : "s"}`;
  $("#acct").innerHTML =
    `<section class="panel pad"><div class="lbl">Profile</div><p><a href="${esc(me.handle)}.html">Your profile page →</a> <span class="q">(appears with your first result)</span></p>` +
    `<p>It shows <b>${me.public ? "your GitHub name, " + esc(me.login) : "the handle " + esc(me.handle) + ", not your GitHub name"}</b>.</p>` +
    `<p><button class="btn" id="vis" type="button">${me.public ? "SHOW ONLY THE HANDLE" : "SHOW MY GITHUB NAME"}</button></p></section>` +
    `<section class="panel pad"><div class="lbl">This browser</div><p><button class="btn" id="out" type="button">SIGN OUT</button></p></section>` +
    `<section class="panel pad"><div class="lbl">Delete</div><p class="q">Deletes the account and every result you sent, here and on the site. It cannot be undone.</p>` +
    `<p><button class="btn" id="del" type="button">DELETE MY ACCOUNT AND DATA</button></p></section>`;
  header(me);
  $("#vis").onclick = async () => { try { signedIn(Object.assign(store.get(), await call("/api/v1/me", { public: !me.public }))); } catch (e) { alert(e.message); } };
  $("#out").onclick = async () => { try { await call("/api/v1/me/logout", {}); } catch (e) {} store.set(null); signedOut("Signed out."); };
  $("#del").onclick = async () => {
    if (!confirm("Delete your llmbox account and every result you sent? This cannot be undone.")) return;
    try { const r = await call("/api/v1/me/forget", {}); store.set(null); signedOut(`Deleted: the account and ${r.deleted_results} results.`); } catch (e) { alert(e.message); }
  };
  store.set(Object.assign(store.get() || {}, { handle: me.handle, login: me.login, public: me.public }));
}
(async () => {
  const m = location.hash.match(/ticket=([\w-]+)/);
  if (m) {
    history.replaceState(null, "", location.pathname);
    try { store.set(await call("/api/v1/login/web/redeem", { ticket: m[1] })); } catch (e) { return signedOut(e.message); }
  }
  if (!store.get()) return signedOut();
  try { signedIn(await call("/api/v1/me")); }
  catch (e) {
    if (e.status === 401) { store.set(null); return signedOut("That sign-in has ended; sign in again."); }
    $("#acct").innerHTML = `<p class="no">The llmbox server is not reachable right now (${esc(e.message)}).</p>`;
  }
})();
