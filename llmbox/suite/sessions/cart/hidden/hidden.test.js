const test = require("node:test");
const assert = require("node:assert");
const TURNS = Number(process.env.LLMBOX_TURNS || 5);
const { cartTotal } = require("../cart.js");
const total = (r) => (TURNS >= 4 ? r.total : r);
const t = (n, name, fn) => test(`T${n}_${name}`, { skip: TURNS < n }, fn);

t(1, "sum of lines", () => {
  assert.strictEqual(total(cartTotal([{ sku: "A1", price: 2500, qty: 2 }, { sku: "B2", price: 100, qty: 3 }])), 5300);
});
t(2, "shipping below threshold", () => {
  assert.strictEqual(total(cartTotal([{ sku: "A1", price: 1000, qty: 2 }], [])), 2499);
});
t(2, "sale10 and freeship", () => {
  assert.strictEqual(total(cartTotal([{ sku: "A1", price: 6000, qty: 1 }], ["SALE10"])), 5400);
  assert.strictEqual(total(cartTotal([{ sku: "A1", price: 3000, qty: 1 }], ["FREESHIP"])), 3000);
});
t(3, "discount floors to cent", () => {
  assert.strictEqual(total(cartTotal([{ sku: "A1", price: 999, qty: 1 }], ["SALE10"])), 1399);
});
t(3, "gift cards excluded and threshold after discount", () => {
  assert.strictEqual(total(cartTotal([{ sku: "GIFT50", price: 5000, qty: 1 }, { sku: "A1", price: 1000, qty: 1 }], ["SALE10"])), 5900);
  assert.strictEqual(total(cartTotal([{ sku: "A1", price: 5200, qty: 1 }], ["SALE10"])), 5179);
});
t(4, "breakdown object", () => {
  assert.deepStrictEqual(cartTotal([{ sku: "A1", price: 999, qty: 1 }], ["SALE10"]), { subtotal: 999, discount: 99, shipping: 499, total: 1399 });
  assert.deepStrictEqual(cartTotal([{ sku: "A1", price: 2500, qty: 2 }]), { subtotal: 5000, discount: 0, shipping: 0, total: 5000 });
});
t(5, "freeship needs more than 2000 after discount", () => {
  assert.strictEqual(total(cartTotal([{ sku: "A1", price: 1500, qty: 1 }], ["FREESHIP"])), 1999);
  assert.strictEqual(total(cartTotal([{ sku: "A1", price: 2200, qty: 1 }], ["FREESHIP", "SALE10"])), 2479);
});
t(5, "codes case-insensitive, unknown ignored", () => {
  assert.strictEqual(total(cartTotal([{ sku: "A1", price: 3000, qty: 1 }], ["freeship", "Sale10"])), 2700);
  assert.strictEqual(total(cartTotal([{ sku: "A1", price: 6000, qty: 1 }], ["XMAS"])), 6000);
});
