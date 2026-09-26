const { unitPrice, withVat } = require("./pricing");

function createOrder(stock, lines, prices, country) {
  const done = [];
  try {
    for (const l of lines) {
      stock.reserve(l.sku, l.qty);
      done.push(l);
    }
  } catch (e) {
    for (const l of done) stock.release(l.sku, l.qty);  //@MUT ord-rollback: for (const l of []) stock.release(l.sku, l.qty);
    throw e;
  }
  let net = 0;
  for (const l of lines) net += unitPrice(prices[l.sku], l.qty) * l.qty;  //@MUT ord-net: for (const l of lines) net += unitPrice(prices[l.sku], 1) * l.qty;
  return { lines, net, gross: withVat(net, country), status: "reserved" };
}

function cancel(stock, order) {
  if (order.status === "shipped") throw new Error("already shipped");  //@MUT ord-cancel: if (order.status === "cancelled") throw new Error("already shipped");
  for (const l of order.lines) stock.release(l.sku, l.qty);
  order.status = "cancelled";
}

function shipOrder(stock, order) {
  for (const l of order.lines) stock.ship(l.sku, l.qty);
  order.status = "shipped";
}

module.exports = { createOrder, cancel, shipOrder };
