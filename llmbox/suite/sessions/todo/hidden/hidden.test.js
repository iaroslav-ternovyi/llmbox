const test = require("node:test");
const assert = require("node:assert");
const TURNS = Number(process.env.LLMBOX_TURNS || 5);
const mod = require("../todo.js");
const t = (n, name, fn) => test(`T${n}_${name}`, { skip: TURNS < n }, fn);
const pick = (o, keys) => Object.fromEntries(keys.map((k) => [k, o[k]]));
const flat = (list) => (TURNS >= 4 ? list.flatMap((x) => [x, ...flat(x.children || [])]) : list);

t(1, "parse done and open", () => {
  const r = mod.parseTodos("# Дела\n- [ ] купить хлеб\n- [x] позвонить маме\nзаметка без галочки\n");
  assert.deepStrictEqual(r.map((x) => pick(x, ["text", "done"])), [{ text: "купить хлеб", done: false }, { text: "позвонить маме", done: true }]);
});
t(2, "tags lowercased, deduped, removed from text", () => {
  const r = flat(mod.parseTodos("- [ ] отчёт #Работа #срочно по проекту #работа\n- [ ] купить #дом молоко\n- [ ] без тегов"));
  assert.deepStrictEqual(r.map((x) => pick(x, ["text", "tags"])), [
    { text: "отчёт по проекту", tags: ["работа", "срочно"] }, { text: "купить молоко", tags: ["дом"] }, { text: "без тегов", tags: [] }]);
});
t(3, "due parsed and removed", () => {
  const r = flat(mod.parseTodos("- [ ] налоги due:2026-10-15 #финансы\n- [x] визит к врачу"));
  assert.deepStrictEqual(r.map((x) => pick(x, ["text", "due", "tags"])), [
    { text: "налоги", due: "2026-10-15", tags: ["финансы"] }, { text: "визит к врачу", due: null, tags: [] }]);
});
t(4, "nested children", () => {
  const r = mod.parseTodos("- [ ] проект\n  - [ ] часть 1\n    - [x] деталь\n  - [ ] часть 2\n- [ ] другое");
  const shape = (x) => ({ text: x.text, children: x.children.map(shape) });
  assert.deepStrictEqual(r.map(shape), [
    { text: "проект", children: [{ text: "часть 1", children: [{ text: "деталь", children: [] }] }, { text: "часть 2", children: [] }] },
    { text: "другое", children: [] }]);
});
t(5, "overdue flat, nested, parent done", () => {
  const r = mod.parseTodos([
    "- [ ] отчёт due:2026-09-30", "  - [ ] цифры due:2026-09-20", "  - [x] слайды due:2026-09-01",
    "- [x] переезд due:2026-08-01", "  - [ ] коробки due:2026-07-01", "- [ ] налоги due:2026-10-15", "- [ ] сегодня due:2026-10-05",
  ].join("\n"));
  assert.deepStrictEqual(mod.overdue(r, "2026-10-05").map((x) => x.text), ["отчёт", "цифры"]);
});
