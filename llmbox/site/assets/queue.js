// The queue page. Cloudflare Pages answers an address it does not have with 404.html; at /r/<id> that is a run sent a
// moment ago whose page is not published yet (llmbox test prints the address right away). This asks the intake where
// the run stands (GET /api/v1/runs/<id>), says it in plain words, checks again every minute and loads the real page
// once it is out. Text from the server goes in as text, never as markup.
function queueView(st) {   // a status reply (null: no such run) -> {line} to show, {page} once published, or null
  if (!st) return null;   // an address nobody sent: the page stays the ordinary "not found"
  if (st.status === "rejected") return { title: "This result was not published", line: `It was not accepted: ${st.reason || "it did not pass the checks"}.`, done: true };
  if (st.published) return { page: true };
  if (st.delayed) return { line: "Your result is checked. Publishing is delayed; it goes out with the next publish. This page fills in by itself." };
  const at = st.live_at ? new Date(st.live_at) : null;
  const when = at && !isNaN(at) ? ` · live at about ${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}` : "";
  return { line: st.status === "received"
    ? `Your result is in the queue · ${st.ahead || 0} ahead${when}. This page fills in by itself.`
    : `Your result is checked${when}. This page fills in by itself.` };
}

if (typeof module !== "undefined") module.exports = { queueView };   // tests/test_queue_js.py

if (typeof document !== "undefined") (function () {
  const m = location.pathname.match(/^\/r\/([0-9a-f]{12})(?:\.html)?\/?$/), box = document.getElementById("queue");
  if (!m || !box) return;
  const line = document.getElementById("qline");
  let tries = 0;
  async function check() {
    let st;
    try {
      const r = await fetch(`${box.dataset.api}/api/v1/runs/${m[1]}`, { cache: "no-store" });
      st = r.status === 404 ? null : await r.json();
    } catch (e) {
      show("The result's status is unavailable right now; retrying in a minute.");
      return setTimeout(check, 60000);
    }
    const v = queueView(st);
    if (!v) return;
    if (v.page) {   // published: load the page once it answers (a fresh upload can take a moment to reach every edge)
      const r = await fetch(location.pathname, { method: "HEAD", cache: "no-store" }).catch(() => null);
      if (r && r.ok) return location.reload();
      show("Your result was just published; the page appears here in a moment.");
      return tries++ < 10 && setTimeout(check, 30000);
    }
    show(v.line, v.title);
    if (!v.done) setTimeout(check, 60000);
  }
  function show(text, title) {
    document.getElementById("notfound").hidden = true;
    if (title) box.querySelector("h1").textContent = title;
    box.hidden = false;
    line.textContent = text;
  }
  check();
})();
