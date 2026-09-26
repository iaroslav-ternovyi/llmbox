const test = require("node:test");
const assert = require("node:assert");
const { render, TemplateError } = require("../src/tmpl");
const R = (t, d = {}) => render(t, d);

test("L3_output paths and missing", () => {
  assert.strictEqual(R("{{ user.name }}/{{ items.1.t }}/{{ nope.x }}|", { user: { name: "Ana" }, items: [{ t: "a" }, { t: "b" }] }), "Ana/b/|");
  assert.strictEqual(R("{{ n }} {{ f }} {{ z }}", { n: 0, f: false, z: null }), "0 false ");
});
test("L3_escape and raw", () => {
  assert.strictEqual(R("{{ x }}|{{{ x }}}", { x: `<a href="x">'&'</a>` }), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;|<a href=\"x\">'&'</a>");
});
test("L3_if elif else", () => {
  const t = "{% if a %}A{% elif b %}B{% else %}C{% endif %}";
  assert.deepStrictEqual([R(t, { a: 1 }), R(t, { b: 1 }), R(t, {}), R(t, { a: [], b: "" })], ["A", "B", "C", "C"]);
});
test("L3_for basic and else", () => {
  assert.strictEqual(R("{% for x in xs %}[{{ x }}]{% else %}none{% endfor %}", { xs: ["a", "b"] }), "[a][b]");
  assert.strictEqual(R("{% for x in xs %}[{{ x }}]{% else %}none{% endfor %}", { xs: [] }), "none");
  assert.strictEqual(R("{% for x in missing %}x{% else %}none{% endfor %}"), "none");
});
test("L3_comment", () => assert.strictEqual(R("a{# hidden {{ x }} #}b"), "ab"));
test("L4_filters chain", () => {
  assert.strictEqual(R("{{ s | trim | upper | truncate:4 }}", { s: "  hello world " }), "HELL...");
  assert.strictEqual(R("{{ s | truncate:20 }}", { s: "short" }), "short");
  assert.strictEqual(R("{{ n | default:\"n/a\" }}|{{ e | default:\"-\" }}|{{ z | default:5 }}", { e: "", z: 0 }), "n/a|-|0");
});
test("L4_join length date", () => {
  assert.strictEqual(R("{{ xs | join }}|{{ xs | join:\" / \" }}|{{ xs | length }}|{{ s | length }}", { xs: ["a", "b", "c"], s: "abcd" }), "a, b, c|a / b / c|3|4");
  assert.strictEqual(R("{{ d | date:\"DD.MM.YYYY\" }}", { d: "2026-09-25" }), "25.09.2026");
});
test("L4_escape after filters", () => assert.strictEqual(R("{{ s | upper }}", { s: "<i>" }), "&lt;I&gt;"));
test("L4_unknown filter", () => assert.throws(() => R("{{ x | shout }}", { x: 1 }), e => e instanceof TemplateError && /shout/.test(e.message)));
test("L4_comparisons and not", () => {
  const t = "{% if n > 2 %}big{% elif n == 2 %}two{% else %}small{% endif %}";
  assert.deepStrictEqual([R(t, { n: 5 }), R(t, { n: 2 }), R(t, { n: 1 })], ["big", "two", "small"]);
  assert.strictEqual(R("{% if not u.admin %}guest{% endif %}{% if role != \"x\" %}!{% endif %}", { u: {}, role: "y" }), "guest!");
  assert.strictEqual(R("{% if name == \"Ana\" %}hi{% endif %}", { name: "Ana" }), "hi");
});
test("L5_loop vars and nesting", () => {
  const t = "{% for r in rows %}{% for c in r.cells %}{{ loop.index }}{% if loop.last %};{% endif %}{% endfor %}{% if not loop.last %}|{% endif %}{% endfor %}";
  assert.strictEqual(R(t, { rows: [{ cells: [1, 2, 3] }, { cells: [4] }] }), "123;|1;");
  assert.strictEqual(R("{% for x in xs %}{% if loop.first %}^{% endif %}{{ x }}{% endfor %}", { xs: [7, 8] }), "^78");
});
test("L5_shadowing", () => {
  assert.strictEqual(R("{{ x }}{% for x in xs %}{{ x }}{% endfor %}{{ x }}", { x: "o", xs: ["i"] }), "oio");
});
test("L5_whitespace control", () => {
  const t = "<ul>\n  {%- for x in xs -%}\n    <li>{{ x }}</li>\n  {%- endfor -%}\n</ul>";
  assert.strictEqual(R(t, { xs: [1, 2] }), "<ul><li>1</li><li>2</li></ul>");
  assert.strictEqual(R("a  {{- x -}}  b", { x: "X" }), "aXb");
  assert.strictEqual(R("a  {{ x }}  b", { x: "X" }), "a  X  b");
});
test("L5_errors", () => {
  assert.throws(() => R("{% if x %}no end"), TemplateError);
  assert.throws(() => R("text {% endfor %}"), TemplateError);
  assert.throws(() => R("{{ x "), TemplateError);
  assert.throws(() => R("{% for x in xs %}{% endif %}{% endfor %}", { xs: [1] }), TemplateError);
});
