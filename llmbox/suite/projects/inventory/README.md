# inventory

Small inventory / order library (Node.js, CommonJS, no dependencies). Money is integer **cents**.

- `src/parse.js`: `parseLine("3x ABC-123")` -> `{ qty: 3, sku: "ABC-123" }`. Quantity prefix is optional (default 1),
  written as `<n>x` or `<n> x` (case-insensitive x). SKU = 3 uppercase letters, dash, 3 digits; lowercase input is
  upper-cased. Anything else throws `Error("bad line")`.
- `src/stock.js`: `new Stock()` with `add(sku, qty)`, `available(sku)` (on hand minus reserved), `reserve(sku, qty)`
  (throws `Error("insufficient stock")` if more than available is requested, changing nothing), `release(sku, qty)`,
  `ship(sku, qty)` (removes reserved units from on-hand and from reserved).
- `src/pricing.js`: `unitPrice(base, qty)` - volume tiers on the unit price: qty >= 10 -> 5% off, qty >= 50 -> 12% off
  (only the highest applicable tier), rounded half up to the cent. `withVat(net, country)` adds VAT: ES 21%, DE 19%,
  PT 23%, any other country 0%, rounded half up to the cent.
- `src/orders.js`: `createOrder(stock, lines, prices, country)` reserves every line (all-or-nothing: if one line cannot
  be reserved, the reservations already made for this order are released and the error is rethrown) and returns
  `{ lines, net, gross, status: "reserved" }`; `cancel(stock, order)` releases and sets status "cancelled";
  `shipOrder(stock, order)` ships and sets status "shipped". Cancelling a shipped order throws `Error("already shipped")`.
- `src/report.js`: `lowStock(stock, threshold)` -> SKUs whose AVAILABLE quantity is below the threshold, sorted by
  available ascending, ties by SKU; `valueOnHand(stock, prices)` -> total cents of ON-HAND units at base price.

Run the tests: `node --test`
