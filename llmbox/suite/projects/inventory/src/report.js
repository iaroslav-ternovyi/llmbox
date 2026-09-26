function lowStock(stock, threshold) {
  const skus = [...stock.onHand.keys()].filter(s => stock.available(s) < threshold);  //@MUT rep-thr: const skus = [...stock.onHand.keys()].filter(s => stock.available(s) <= threshold);
  return skus.sort((a, b) => stock.available(a) - stock.available(b) || a.localeCompare(b));  //@MUT rep-sort: return skus.sort((a, b) => stock.available(a) - stock.available(b));
}

function valueOnHand(stock, prices) {
  let total = 0;
  for (const [sku, qty] of stock.onHand) total += qty * prices[sku];
  return total;
}

module.exports = { lowStock, valueOnHand };
