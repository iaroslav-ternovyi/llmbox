// Cloudflare Pages Function, on the two paths _routes.json sends here (every other request stays a plain static file):
// counts, without any id, that install.sh was fetched (installs started; curl/wget against a browser looking at it) and
// that a llmbox fetched the model list (by its version, from the User-Agent `llmbox/<version>`), then serves the file as
// Pages would. GoatCounter's API, server to server: the visitor's IP is not passed on. Set in the Pages project:
// GOATCOUNTER_URL (https://stats.<host>) and the secret GOATCOUNTER_TOKEN (an API token with the "count" permission).
export async function onRequest(ctx) {
  const { request, env } = ctx;
  const res = await env.ASSETS.fetch(request);
  if (request.method === "GET" && env.GOATCOUNTER_URL && env.GOATCOUNTER_TOKEN) {
    const path = new URL(request.url).pathname, ua = request.headers.get("user-agent") || "";
    const v = ua.match(/^llmbox\/([0-9.]+)/);
    const name = path === "/install.sh" ? (/^(curl|wget)\//i.test(ua) ? "fetch/install.sh" : "view/install.sh")
      : path === "/recipes/index.json" && v ? `cli/${v[1]}` : null;
    if (name) ctx.waitUntil(fetch(`${env.GOATCOUNTER_URL}/api/v0/count`, {
      method: "POST", headers: { "Authorization": `Bearer ${env.GOATCOUNTER_TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ no_sessions: true, hits: [{ path: name, title: name, event: true }] }) }).catch(() => {}));
  }
  return res;
}
