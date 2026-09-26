const test = require("node:test");
const assert = require("node:assert");
const { parseLine } = require("../src/parse");
const { Stock } = require("../src/stock");
const { unitPrice, withVat } = require("../src/pricing");
const { createOrder } = require("../src/orders");

test("parse with qty", () => assert.deepStrictEqual(parseLine("3x ABC-123"), { qty: 3, sku: "ABC-123" }));
test("reserve reduces available", () => { const s = new Stock(); s.add("ABC-123", 5); s.reserve("ABC-123", 2); assert.strictEqual(s.available("ABC-123"), 3); });
test("tier 10", () => assert.strictEqual(unitPrice(1000, 10), 950));
test("vat ES", () => assert.strictEqual(withVat(1000, "ES"), 1210));
test("order net", () => { const s = new Stock(); s.add("ABC-123", 20); const o = createOrder(s, [{ sku: "ABC-123", qty: 2 }], { "ABC-123": 1000 }, "ES"); assert.strictEqual(o.net, 2000); });
