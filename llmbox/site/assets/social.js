// a model page's comments and votes (llmbox/social.py, server.py): read from the API, so a comment shows at once and
// people's text never goes into the page's HTML (it is set as text, never as markup). Signing in is account.js's flow:
// GitHub, then back here with a one-time ticket in the #fragment, traded for a key kept in this browser.
(function () {
  const API = (typeof DATA !== "undefined" && DATA.api) || "", RID = (typeof DATA !== "undefined" && DATA.rid) || "";
  const box = document.getElementById("comments");
  if (!API || !RID || !box) return;
  const store = {
    get() { try { return JSON.parse(localStorage.getItem("llmbox-account") || "null"); } catch (e) { return null; } },
    set(v) { try { v ? localStorage.setItem("llmbox-account", JSON.stringify(v)) : localStorage.removeItem("llmbox-account"); } catch (e) {} },
  };
  async function call(path, body) {
    const a = store.get(), h = {};
    if (body !== undefined) h["Content-Type"] = "application/json";
    if (a && a.key) h.Authorization = "Bearer " + a.key;
    const r = await fetch(API + path, { method: body === undefined ? "GET" : "POST", headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(j.error || `HTTP ${r.status}`); e.status = r.status; throw e; }
    return j;
  }
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const when = iso => { const d = new Date(iso + "Z"); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }); };
  const signinUrl = () => `${API}/api/v1/login/web/start?return=${encodeURIComponent(location.href.split("#")[0])}`;
  let state = null;

  // ---- votes: llmbox's settings and each variant people measured (the .vote cells of the "Settings people measured" panel)
  function votes() {
    document.querySelectorAll("#shared .vote").forEach(cell => {
      const t = cell.dataset.target, v = (state && state.votes[t]) || { up: 0, down: 0 }, mine = (state && state.mine[t]) || 0;
      cell.textContent = "";
      for (const [val, sym, n, name] of [[1, "▲", v.up, "works for me"], [-1, "▼", v.down, "did not work for me"]]) {
        const b = el("button", "vb" + (mine === val ? " on" : ""), `${sym} ${n}`);
        b.type = "button"; b.title = name; b.setAttribute("aria-pressed", mine === val ? "true" : "false");
        b.setAttribute("aria-label", `${name}: ${n}`);
        b.disabled = !state;
        b.onclick = async () => {
          if (!store.get()) { location.href = signinUrl(); return; }
          const next = mine === val ? 0 : val;
          try { const r = await call("/api/v1/votes", { target: t, value: next }); state.votes[t] = { up: r.up, down: r.down }; state.mine[t] = next; votes(); sortVariants(); llmboxCount("vote"); }
          catch (e) { if (e.status === 401) { store.set(null); location.href = signinUrl(); } else alert(e.message); }
        };
        cell.appendChild(b);
      }
    });
  }
  function sortVariants() {   // the ones people found best first, llmbox's own settings always on top
    const panel = document.getElementById("shared"), vs = [...panel.querySelectorAll(".var:not(.ours)")];
    const net = d => { const v = (state && state.votes[d.dataset.key]) || { up: 0, down: 0 }; return v.up - v.down; };
    const after = panel.querySelector(".var.ours");
    vs.sort((a, b) => net(b) - net(a)).reverse().forEach(d => after.after(d));
  }
  document.querySelectorAll("#shared .vcopy").forEach(b => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "COPIED"; llmboxCount("copy-variant"); } catch (e) { b.textContent = "SELECT AND COPY"; }
    setTimeout(() => b.textContent = "COPY", 1500);
  }));

  // ---- comments
  function comment(c) {
    const a = el("article", "cm" + (c.held ? " held" : "")), hd = el("div", "cmh");
    const who = el("a", "who", c.name || c.by); who.href = `${c.by}.html`;
    hd.appendChild(who);
    if (c.tested.length) { const t = el("span", "tested", "TESTED ON " + c.tested.join(" · ")); t.title = "measured this model on these machines"; hd.appendChild(t); }
    hd.appendChild(el("span", "q", when(c.when)));
    a.appendChild(hd);
    if (c.held) a.appendChild(el("p", "heldnote", `Only you see this: ${c.held}.`));
    a.appendChild(el("p", "cmb", c.body));
    const ft = el("div", "cmf");
    if (c.mine) {
      const d = el("button", "lnk", "delete"); d.type = "button";
      d.onclick = async () => { if (!confirm("Delete your comment?")) return; try { await call(`/api/v1/comments/${c.id}/delete`, {}); load(); } catch (e) { alert(e.message); } };
      ft.appendChild(d);
    } else if (state.me) {
      if (c.reported) ft.appendChild(el("span", "q", "reported"));
      else {
        const r = el("button", "lnk", "report"); r.type = "button";
        r.onclick = () => { r.hidden = true; ft.appendChild(reportForm(c, () => { r.remove(); load(); })); };
        ft.appendChild(r);
      }
    }
    a.appendChild(ft);
    return a;
  }
  function reportForm(c, done) {
    const f = el("form", "rep"), s = el("select"), d = el("input"), b = el("button", "btn", "SEND REPORT");
    for (const [v, t] of [["spam", "spam or advertising"], ["abuse", "abusive or harassing"], ["illegal", "illegal content"], ["other", "something else"]]) {
      const o = el("option", null, t); o.value = v; s.appendChild(o);
    }
    s.setAttribute("aria-label", "why"); d.placeholder = "what is wrong (optional; for illegal content say which law)"; d.maxLength = 500; d.setAttribute("aria-label", "details");
    b.type = "submit";
    f.append(s, d, b);
    f.onsubmit = async e => { e.preventDefault(); b.disabled = true;
      try { await call(`/api/v1/comments/${c.id}/report`, { reason: s.value, detail: d.value }); done(); } catch (er) { alert(er.message); b.disabled = false; } };
    return f;
  }
  function form() {
    const wrap = el("div", "cform");
    if (!state.me) {
      const a = el("a", "btn", "SIGN IN WITH GITHUB TO COMMENT"); a.href = signinUrl();
      a.addEventListener("click", () => llmboxCount("signin"));
      wrap.appendChild(a);
      return wrap;
    }
    if (!state.me.can_post) { wrap.appendChild(el("p", "q", "This account cannot post comments.")); return wrap; }
    const f = el("form"), t = el("textarea"), row = el("div", "crow"), n = el("span", "q"), b = el("button", "btn solid", "POST");
    t.maxLength = 2000; t.rows = 3; t.required = true; t.setAttribute("aria-label", "your comment");
    t.placeholder = "What did you see with this model: on what machine, with which settings, what went well or wrong?";
    t.oninput = () => { n.textContent = t.value.length > 1500 ? `${2000 - t.value.length} left` : ""; };
    b.type = "submit";
    row.append(n, b);
    f.append(t, row);
    f.onsubmit = async e => { e.preventDefault(); b.disabled = true;
      try { await call("/api/v1/comments", { rid: RID, body: t.value }); t.value = ""; llmboxCount("comment"); await load(); }
      catch (er) { alert(er.message); } b.disabled = false; };
    wrap.appendChild(f);
    return wrap;
  }
  function render() {
    box.textContent = "";
    const cs = state.comments;
    box.appendChild(el("p", "ccount q", cs.length ? `${cs.length} comment${cs.length === 1 ? "" : "s"}` : "No comments yet."));
    cs.forEach(c => box.appendChild(comment(c)));
    box.appendChild(form());
  }
  async function load() {
    try { state = await call(`/api/v1/models/${encodeURIComponent(RID)}/social`); }
    catch (e) {
      if (e.status === 401 && store.get()) { store.set(null); return load(); }
      box.textContent = ""; box.appendChild(el("p", "q", "Comments and votes are not reachable right now."));
      return;
    }
    if (state.me) store.set(Object.assign(store.get() || {}, { handle: state.me.handle }));
    render(); votes(); sortVariants();
  }
  (async () => {
    const m = location.hash.match(/ticket=([\w-]+)/);
    if (m) {   // back from GitHub: trade the ticket for a key, then on to the comments
      history.replaceState(null, "", location.pathname + "#talk");
      try { store.set(await call("/api/v1/login/web/redeem", { ticket: m[1] })); } catch (e) { alert(e.message); }
      const s = document.getElementById("signin"), a = store.get();
      if (s && a) s.textContent = (a.public && a.login ? a.login : a.handle).toUpperCase();
      document.getElementById("talk").scrollIntoView();
    }
    votes();
    load();
  })();
})();
