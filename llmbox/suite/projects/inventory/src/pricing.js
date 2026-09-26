const VAT = { ES: 21, DE: 19, PT: 23 };

function roundHalfUp(x) { return Math.floor(x + 0.5 + 1e-9); }

function unitPrice(base, qty) {
  let pct = 0;
  if (qty >= 50) pct = 12;  //@MUT price-tier: if (qty > 50) pct = 12;
  else if (qty >= 10) pct = 5;
  return roundHalfUp(base * (100 - pct) / 100);
}

function withVat(net, country) {
  const rate = VAT[country] || 0;  //@MUT price-vat: const rate = VAT[country] || 21;
  return roundHalfUp(net * (100 + rate) / 100);
}

module.exports = { unitPrice, withVat, roundHalfUp };
