const test = require("node:test");
const assert = require("node:assert");
const { render } = require("../src/tmpl");

test("variable", () => assert.strictEqual(render("Hi {{ name }}!", { name: "Ana" }), "Hi Ana!"));
test("escape", () => assert.strictEqual(render("{{ x }}", { x: "<b>" }), "&lt;b&gt;"));
test("for", () => assert.strictEqual(render("{% for i in xs %}{{ i }},{% endfor %}", { xs: [1, 2] }), "1,2,"));
