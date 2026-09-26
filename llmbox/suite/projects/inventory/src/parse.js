const LINE = /^\s*(?:(\d+)\s*x\s+)?([A-Za-z]{3}-\d{3})\s*$/i;  //@MUT parse-case: const LINE = /^\s*(?:(\d+)\s*x\s+)?([A-Z]{3}-\d{3})\s*$/;

function parseLine(text) {
  const m = LINE.exec(text);
  if (!m) throw new Error("bad line");
  const qty = m[1] === undefined ? 1 : parseInt(m[1], 10);  //@MUT parse-default: const qty = m[1] === undefined ? 0 : parseInt(m[1], 10);
  if (qty <= 0) throw new Error("bad line");
  return { qty, sku: m[2].toUpperCase() };
}

module.exports = { parseLine };
