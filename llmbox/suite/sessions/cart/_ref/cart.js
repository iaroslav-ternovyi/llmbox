function cartTotal(items, codes = []) {
  const set = new Set((codes || []).map((c) => String(c).toUpperCase()));
  let subtotal = 0, eligible = 0;
  for (const it of items) {
    const line = it.price * it.qty;
    subtotal += line;
    if (!String(it.sku).startsWith("GIFT")) eligible += line;
  }
  const discount = set.has("SALE10") ? Math.floor(eligible * 0.1) : 0;
  const goods = subtotal - discount;
  let shipping = goods < 5000 ? 499 : 0;
  if (set.has("FREESHIP") && goods > 2000) shipping = 0;
  return { subtotal, discount, shipping, total: goods + shipping };
}
module.exports = { cartTotal };
