// Reference implementation of README.md (NOT shipped to the agent).
class TemplateError extends Error {}

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escape = s => s.replace(/[&<>"']/g, c => ESC[c]);

function tokenize(src) {
  const toks = [];
  let i = 0;
  while (i < src.length) {
    const next = src.slice(i).search(/\{\{|\{%|\{#/);
    if (next < 0) { toks.push({ t: "text", v: src.slice(i) }); break; }
    if (next > 0) toks.push({ t: "text", v: src.slice(i, i + next) });
    i += next;
    let open, close, kind;
    if (src.startsWith("{{{", i)) { open = "{{{"; close = "}}}"; kind = "raw"; }
    else if (src.startsWith("{{", i)) { open = "{{"; close = "}}"; kind = "out"; }
    else if (src.startsWith("{%", i)) { open = "{%"; close = "%}"; kind = "tag"; }
    else { open = "{#"; close = "#}"; kind = "comment"; }
    const end = src.indexOf(close, i + open.length);
    if (end < 0) throw new TemplateError(`unterminated ${open}`);
    let inner = src.slice(i + open.length, end);
    let lt = false, rt = false;
    if (kind !== "comment" && kind !== "raw") {
      if (inner.startsWith("-")) { lt = true; inner = inner.slice(1); }
      if (inner.endsWith("-")) { rt = true; inner = inner.slice(0, -1); }
    }
    toks.push({ t: kind, v: inner.trim(), lt, rt });
    i = end + close.length;
  }
  for (let k = 0; k < toks.length; k++) {
    const tk = toks[k];
    if (tk.lt && k > 0 && toks[k - 1].t === "text") toks[k - 1].v = toks[k - 1].v.replace(/\s+$/, "");
    if (tk.rt && k + 1 < toks.length && toks[k + 1].t === "text") toks[k + 1].v = toks[k + 1].v.replace(/^\s+/, "");
  }
  return toks;
}

function parse(toks) {
  let pos = 0;
  function block(stops) {
    const nodes = [];
    while (pos < toks.length) {
      const tk = toks[pos];
      if (tk.t === "tag") {
        const word = tk.v.split(/\s+/)[0];
        if (stops.includes(word)) return { nodes, stop: word, tk };
        pos++;
        if (word === "if") {
          const branches = [];
          let cond = tk.v.slice(2).trim();
          let r = block(["elif", "else", "endif"]);
          branches.push([cond, r.nodes]);
          let elseNodes = null;
          while (r.stop === "elif") { cond = r.tk.v.slice(4).trim(); pos++; r = block(["elif", "else", "endif"]); branches.push([cond, r.nodes]); }
          if (r.stop === "else") { pos++; r = block(["endif"]); elseNodes = r.nodes; }
          if (r.stop !== "endif") throw new TemplateError("missing endif");
          pos++;
          nodes.push({ k: "if", branches, elseNodes });
        } else if (word === "for") {
          const m = /^for\s+(\w+)\s+in\s+(.+)$/.exec(tk.v);
          if (!m) throw new TemplateError("bad for");
          let r = block(["else", "endfor"]);
          const body = r.nodes;
          let elseNodes = null;
          if (r.stop === "else") { pos++; r = block(["endfor"]); elseNodes = r.nodes; }
          if (r.stop !== "endfor") throw new TemplateError("missing endfor");
          pos++;
          nodes.push({ k: "for", v: m[1], expr: m[2].trim(), body, elseNodes });
        } else {
          throw new TemplateError(`unexpected tag ${word}`);
        }
      } else {
        pos++;
        if (tk.t === "text") nodes.push({ k: "text", v: tk.v });
        else if (tk.t === "out" || tk.t === "raw") nodes.push({ k: "out", expr: tk.v, raw: tk.t === "raw" });
      }
    }
    if (stops.length) throw new TemplateError(`missing ${stops[stops.length - 1]}`);
    return { nodes, stop: null };
  }
  return block([]).nodes;
}

function splitTop(s, sep) {
  const out = []; let cur = "", q = false;
  for (const c of s) {
    if (c === '"') q = !q;
    if (c === sep && !q) { out.push(cur); cur = ""; } else cur += c;
  }
  out.push(cur);
  return out;
}

function lookup(path, scopes) {
  const parts = path.split(".");
  let v;
  let found = false;
  for (const sc of scopes) {
    if (sc !== null && typeof sc === "object" && parts[0] in sc) { v = sc[parts[0]]; found = true; break; }
  }
  if (!found) return undefined;
  for (const p of parts.slice(1)) {
    if (v === null || v === undefined) return undefined;
    v = v[/^\d+$/.test(p) ? Number(p) : p];
  }
  return v;
}

function atom(s, scopes) {
  s = s.trim();
  if (/^".*"$/.test(s)) return s.slice(1, -1);
  if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s);
  if (s === "true") return true;
  if (s === "false") return false;
  return lookup(s, scopes);
}

function truthy(v) { return !(v === undefined || v === null || v === false || v === 0 || v === "" || (Array.isArray(v) && v.length === 0)); }

function cond(expr, scopes) {
  expr = expr.trim();
  if (expr.startsWith("not ")) return !cond(expr.slice(4), scopes);
  const m = /^(.+?)\s*(==|!=|>|<)\s*(.+)$/.exec(expr);
  if (m) {
    const a = atom(m[1], scopes), b = atom(m[3], scopes);
    if (m[2] === "==") return a === b;
    if (m[2] === "!=") return a !== b;
    if (m[2] === ">") return a > b;
    return a < b;
  }
  return truthy(atom(expr, scopes));
}

const FILTERS = {
  upper: v => String(v ?? "").toUpperCase(),
  lower: v => String(v ?? "").toLowerCase(),
  trim: v => String(v ?? "").trim(),
  default: (v, x) => (v === undefined || v === null || v === "") ? x : v,
  truncate: (v, n) => { const s = String(v ?? ""); return s.length > n ? s.slice(0, n) + "..." : s; },
  join: (v, sep = ", ") => Array.isArray(v) ? v.join(sep) : String(v ?? ""),
  length: v => (v ?? "").length,
  date: (v, fmt) => { const [y, mo, d] = String(v).split("-"); return fmt.replace("YYYY", y).replace("MM", mo).replace("DD", d); },
};

function toText(v) {
  if (v === undefined || v === null) return "";
  return String(v);
}

function output(expr, raw, scopes) {
  const [head, ...filters] = splitTop(expr, "|");
  let v = atom(head, scopes);
  for (const f of filters) {
    const m = /^\s*(\w+)\s*(?::(.*))?$/.exec(f);
    if (!m || !(m[1] in FILTERS)) throw new TemplateError(`unknown filter ${m ? m[1] : f.trim()}`);
    const args = m[2] !== undefined ? splitTop(m[2], ",").map(a => atom(a, [])) : [];
    v = FILTERS[m[1]](v, ...args);
  }
  const s = toText(v);
  return raw ? s : escape(s);
}

function run(nodes, scopes) {
  let out = "";
  for (const n of nodes) {
    if (n.k === "text") out += n.v;
    else if (n.k === "out") out += output(n.expr, n.raw, scopes);
    else if (n.k === "if") {
      let done = false;
      for (const [c, body] of n.branches) { if (cond(c, scopes)) { out += run(body, scopes); done = true; break; } }
      if (!done && n.elseNodes) out += run(n.elseNodes, scopes);
    } else if (n.k === "for") {
      const arr = atom(n.expr, scopes);
      if (!Array.isArray(arr) || arr.length === 0) { if (n.elseNodes) out += run(n.elseNodes, scopes); continue; }
      arr.forEach((item, i) => {
        const frame = { [n.v]: item, loop: { index: i + 1, first: i === 0, last: i === arr.length - 1 } };
        out += run(n.body, [frame, ...scopes]);
      });
    }
  }
  return out;
}

function render(template, data) {
  return run(parse(tokenize(template)), [data || {}]);
}

module.exports = { render, TemplateError };
