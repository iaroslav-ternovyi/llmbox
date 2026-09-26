# shipping

Shipping-quote library + CLI (Python 3, stdlib only). Money is integer **cents**, weight integer **grams**, sizes **cm**.
Round half up everywhere a rounding step is mentioned.

## units (`shipping/units.py`)
`to_grams(value, unit)`: units `g`, `kg`, `lb` (453.59237 g), `oz` (28.349523125 g) -> int grams, rounded half up.
`to_cm(value, unit)`: `cm`, `mm`, `in` (2.54 cm) -> float cm.

## packing (`shipping/packing.py`)
`pack(items, boxes)`: `items` = list of `Item(sku, qty, weight_g, l, w, h)`, `boxes` = list of `Box(name, l, w, h, max_g, tare_g)`.
Expand items by qty; sort units by volume DESCENDING (ties: sku ascending); put each unit into the FIRST already-open
parcel whose remaining volume and remaining weight capacity are enough; otherwise open a new parcel with the SMALLEST
box type (by volume, then max_g) that can hold the unit. Returns a list of `Parcel(box, skus, weight_g, l, w, h)` in the
order parcels were opened; `weight_g` includes the box tare; `l, w, h` are the box dimensions. A unit that fits no box
raises `ValueError`.

## rates (`shipping/rates.py`)
Zones: ES -> 1; PT, FR -> 2; DE, IT -> 3; anything else -> 4.
Billable weight of a parcel = max(actual weight, dimensional weight) where dimensional weight in grams =
l*w*h / 5000 * 1000, then rounded UP to the next multiple of 500 g.
Price per parcel = base[zone] + per_500g[zone] * (billable_g / 500), with base = {1: 450, 2: 700, 3: 900, 4: 1500} and
per_500g = {1: 80, 2: 120, 3: 150, 4: 300}. Express service multiplies the parcel price by 1.5 (rounded half up).

## calendar (`shipping/calendar.py`)
`estimate(ordered_at, zone, service, holidays)` -> delivery `date`. Business days are Mon-Fri except `holidays`.
Orders placed at or after 14:00, or on a non-business day, start counting from the next business day; otherwise from the
order day. Transit business days: zone 1 -> 1, 2 -> 2, 3 -> 3, 4 -> 5; express = transit - 1 but at least 1.
Delivery = the start day advanced by `transit` business days.

## promos (`shipping/promos.py`)
`apply(codes, shipping_cents, zone, n_parcels)` -> discounted shipping cents. Codes are case-insensitive; unknown codes
are ignored. `FREESHIP`: shipping becomes 0, only for zones 1-2 (otherwise it has no effect), and when it applies it
overrides every other code. `TENOFF`: 10% off. `BULK`: 5% off when there are at least 3 parcels. TENOFF and BULK stack
multiplicatively; round half up once at the end.

## invoice (`shipping/invoice.py`)
`quote(order, boxes, holidays)` -> dict with `parcels`, `shipping_cents` (after promos), `vat_cents`, `total_cents`,
`delivery` (ISO date). VAT on the discounted shipping by destination: ES 21%, PT 23%, FR 20%, DE 19%, IT 22%, else 0.
`total_cents = shipping_cents + vat_cents` (+ insurance, see below, if requested).

## insurance
If `order.insured_value_cents` is set (> 0): insurance fee = 1% of the insured value (round half up), minimum 150 cents;
VAT applies to the fee at the destination rate; `quote` then also returns `insurance_cents` (fee before VAT) and adds
fee + its VAT to `total_cents`. The CLI flag `--insure <cents>` sets it.

## CLI
`python3 -m shipping.cli order.json [--insure CENTS]` prints the quote as JSON (sorted keys). `order.json` has
`id, country, service, ordered_at (ISO datetime), promos (list), items (list of {sku, qty, weight: [value, unit],
size: [l, w, h, unit]})`. The box catalogue is `shipping.boxes.DEFAULT_BOXES`, holidays `shipping.boxes.HOLIDAYS`.

Run the tests: `python3 -m unittest discover -s tests -v`
