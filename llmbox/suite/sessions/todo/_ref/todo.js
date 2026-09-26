function parseTodos(text) {
  const roots = [], stack = [];
  for (const line of String(text).split(/\r?\n/)) {
    const m = line.match(/^( *)- \[( |x|X)\] (.*)$/);
    if (!m) continue;
    const indent = m[1].length;
    let body = m[3];
    const tags = [];
    for (const t of body.matchAll(/#([\p{L}\p{N}_-]+)/gu)) {
      const tag = t[1].toLowerCase();
      if (!tags.includes(tag)) tags.push(tag);
    }
    const dm = body.match(/\bdue:(\d{4}-\d{2}-\d{2})/);
    body = body.replace(/#[\p{L}\p{N}_-]+/gu, " ").replace(/\bdue:\d{4}-\d{2}-\d{2}/, " ").replace(/\s+/g, " ").trim();
    const node = { text: body, done: m[2] !== " ", tags, due: dm ? dm[1] : null, children: [] };
    while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop();
    if (stack.length) stack[stack.length - 1].node.children.push(node);
    else roots.push(node);
    stack.push({ indent, node });
  }
  return roots;
}

function overdue(todos, today) {
  const out = [];
  const walk = (list, parentDone) => {
    for (const t of list) {
      const done = parentDone || t.done;
      if (!done && t.due && t.due < today) out.push(t);
      walk(t.children || [], done);
    }
  };
  walk(todos, false);
  return out;
}

module.exports = { parseTodos, overdue };
