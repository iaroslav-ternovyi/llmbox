// the hardware map (hwmap.py): the visitor's own cell in amber with YOU (only for a box picked above), "RSS for your
// card" pointing at its feed, the cell pinned on top on a phone, NEW tags older than a day removed. Text only.
(() => {
  const map = document.getElementById("map");
  if (!map) return;
  for (const t of map.querySelectorAll(".new[data-at]")) if (Date.now() - Date.parse(t.dataset.at) > 864e5) t.remove();
  const rss = document.getElementById("maprss"), pin = document.getElementById("mapyou"), sel = document.getElementById("gpu");
  function mark() {
    for (const c of map.querySelectorAll(".cell.you")) { c.classList.remove("you"); const y = c.querySelector(".yt"); if (y) y.remove(); }
    pin.textContent = ""; pin.hidden = true;
    const name = sel && sel.value, c = name && [...map.querySelectorAll(".cells .cell")].find(x => x.dataset.e === name);
    if (!c) { rss.href = "#box"; rss.textContent = "RSS for your card: pick it above"; return; }
    c.classList.add("you");
    const y = document.createElement("span"); y.className = "yt"; y.textContent = "YOU";
    const n = c.querySelector(".new"); if (n) n.remove();
    c.append(y);
    rss.href = `feeds/${c.dataset.slug}.xml`; rss.textContent = `RSS for ${name}`;
    pin.append(c.cloneNode(true)); pin.hidden = false;
  }
  if (sel) sel.addEventListener("change", () => setTimeout(mark));
  mark();
  const b = map.querySelector("[data-copy-from]");
  if (b) b.onclick = () => navigator.clipboard.writeText("llmbox test").then(() => { b.textContent = "COPIED"; setTimeout(() => b.textContent = "COPY", 1500); });
})();
