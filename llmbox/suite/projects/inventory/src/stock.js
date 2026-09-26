class Stock {
  constructor() { this.onHand = new Map(); this.reserved = new Map(); }

  add(sku, qty) { this.onHand.set(sku, (this.onHand.get(sku) || 0) + qty); }

  available(sku) {
    return (this.onHand.get(sku) || 0) - (this.reserved.get(sku) || 0);  //@MUT stock-avail: return (this.onHand.get(sku) || 0);
  }

  reserve(sku, qty) {
    if (qty > this.available(sku)) throw new Error("insufficient stock");  //@MUT stock-reserve: if (qty >= this.available(sku)) throw new Error("insufficient stock");
    this.reserved.set(sku, (this.reserved.get(sku) || 0) + qty);
  }

  release(sku, qty) { this.reserved.set(sku, Math.max(0, (this.reserved.get(sku) || 0) - qty)); }

  ship(sku, qty) {
    this.reserved.set(sku, (this.reserved.get(sku) || 0) - qty);
    this.onHand.set(sku, (this.onHand.get(sku) || 0) - qty);  //@MUT stock-ship: this.onHand.set(sku, (this.onHand.get(sku) || 0));
  }
}

module.exports = { Stock };
