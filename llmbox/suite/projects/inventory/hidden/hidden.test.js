const test = require("node:test");
const assert = require("node:assert");
const { parseLine } = require("../src/parse");
const { Stock } = require("../src/stock");
const { unitPrice, withVat } = require("../src/pricing");
const { createOrder, cancel, shipOrder } = require("../src/orders");
const { lowStock, valueOnHand } = require("../src/report");

test("parse lowercase + default qty", () => {
  assert.deepStrictEqual(parseLine("abc-001"), { qty: 1, sku: "ABC-001" });
  assert.deepStrictEqual(parseLine("12 X xyz-999"), { qty: 12, sku: "XYZ-999" });
  assert.throws(() => parseLine("3x AB-12"));
});
test("reserve exactly available", () => { const s = new Stock(); s.add("AAA-111", 4); s.reserve("AAA-111", 4); assert.strictEqual(s.available("AAA-111"), 0); assert.throws(() => s.reserve("AAA-111", 1)); });
test("available excludes reserved", () => { const s = new Stock(); s.add("AAA-111", 4); s.reserve("AAA-111", 3); assert.strictEqual(s.available("AAA-111"), 1); });
test("ship reduces on hand", () => { const s = new Stock(); s.add("AAA-111", 4); s.reserve("AAA-111", 3); s.ship("AAA-111", 3); assert.strictEqual(s.onHand.get("AAA-111"), 1); assert.strictEqual(s.available("AAA-111"), 1); });
test("tiers boundaries", () => { assert.strictEqual(unitPrice(1000, 50), 880); assert.strictEqual(unitPrice(999, 9), 999); assert.strictEqual(unitPrice(333, 10), 316); });
test("vat others zero", () => { assert.strictEqual(withVat(1000, "FR"), 1000); assert.strictEqual(withVat(1001, "PT"), 1231); });
test("order rollback all-or-nothing", () => {
  const s = new Stock(); s.add("AAA-111", 5); s.add("BBB-222", 1);
  assert.throws(() => createOrder(s, [{ sku: "AAA-111", qty: 2 }, { sku: "BBB-222", qty: 2 }], { "AAA-111": 100, "BBB-222": 100 }, "ES"));
  assert.strictEqual(s.available("AAA-111"), 5);
});
test("order net uses tier per line", () => {
  const s = new Stock(); s.add("AAA-111", 100);
  const o = createOrder(s, [{ sku: "AAA-111", qty: 10 }], { "AAA-111": 1000 }, "DE");
  assert.strictEqual(o.net, 9500); assert.strictEqual(o.gross, 11305);
});
test("cancel shipped throws, cancel reserved releases", () => {
  const s = new Stock(); s.add("AAA-111", 5);
  const o = createOrder(s, [{ sku: "AAA-111", qty: 2 }], { "AAA-111": 100 }, "ES");
  cancel(s, o); assert.strictEqual(s.available("AAA-111"), 5); assert.strictEqual(o.status, "cancelled");
  const o2 = createOrder(s, [{ sku: "AAA-111", qty: 1 }], { "AAA-111": 100 }, "ES");
  shipOrder(s, o2); assert.throws(() => cancel(s, o2), /already shipped/);
});
test("low stock threshold strict and ties", () => {
  const s = new Stock(); s.add("CCC-333", 2); s.add("AAA-111", 2); s.add("BBB-222", 5); s.add("DDD-444", 3);
  assert.deepStrictEqual(lowStock(s, 3), ["AAA-111", "CCC-333"]);
});
test("value on hand", () => { const s = new Stock(); s.add("AAA-111", 2); s.add("BBB-222", 3); assert.strictEqual(valueOnHand(s, { "AAA-111": 150, "BBB-222": 10 }), 330); });
