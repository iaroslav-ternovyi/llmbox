"""A run's page (run-<id8>.html): the machine, the exact command line, telemetry, every task."""
from __future__ import annotations

import re

from .. import report
from .components import _telemetry_panel
from .data import _vs
from .layout import _page
from .words import _name_of, BLOCKS, esc, task_name, TIPS


def _argv_lines(argv: list[str]) -> list[str]:
    """The command line as one flag per line: '-c 262144', '--temp 0.6' ... (the binary and model path first)."""
    out, cur = [], []
    for x in argv:
        if x.startswith("-") and not re.fullmatch(r"-\d.*", x) and cur:
            out.append(" ".join(cur)); cur = []
        cur.append(x)
    return out + ([" ".join(cur)] if cur else [])


def run_page(rid: str, rec: dict, ref: dict | None, flags: dict) -> str:
    s, sp, h, m, rt = rec["summary"], rec["summary"]["speed"], rec["host"], rec.get("model") or {}, rec.get("runtime") or {}
    vs = _vs(rec, ref)
    bd = sorted((report._depth_k(k), d) for k, d in (sp.get("by_depth") or {}).items())
    body_rows = []
    for b in BLOCKS:
        items = [r for r in rec.get("rows", []) if r["block"] == b]
        if not items:
            continue
        body_rows.append(f'<tr class="grp"><td class="l" colspan="6">{TIPS[b][0].split(" ·")[0].upper()} · {s["blocks"].get(b, 0):.0f} · {len(items)} tasks</td></tr>')
        for r in items:
            f = flags.get(r["id"], {})
            fl = ('<span class="flag lo">CUT</span>' if f.get("cut") else "") + ('<span class="flag lo">LOOP</span>' if f.get("loop") else "")
            v = r["score"] * 100
            body_rows.append(f'<tr><td class="l"><span class="m2">{esc(task_name(r["id"]))}</span></td>'
                             f'<td class="l"><span class="mb {"ok" if v >= 99 else "no" if v < 1 else ""}"><i style="width:{v:.0f}%"></i></span><b class="mv">{v:.0f}</b></td>'
                             f'<td>{r["seconds"]:.0f} s</td><td>{r.get("steps") or 1}</td><td>{f.get("max_reply", 0):,}</td><td class="l">{fl or "<span class=q>—</span>"}</td></tr>')
    argv = rt.get("argv") or []
    diff = rt.get("diff_vs_recipe")
    diff_html = ("<span class='v ok'>identical to the recipe</span>" if diff == [] else
                 "".join(f"<div><span class='flag {'lo' if d['class'] == 'quality' else ''}'>{d['class'].upper()}</span> {esc(d['flag'])}: recipe {esc(' '.join(d['recipe']) or '—')} → run {esc(' '.join(d['run']) or '—')}</div>" for d in diff) if diff
                 else "<span class='q'>not recorded for this run</span>")
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / <a href="recipe-{esc(rid)}.html">{esc(_name_of(rec))}</a> / run {rec["id"][:8]}</div><h1><a href="recipe-{esc(rid)}.html">{esc(_name_of(rec))}</a> on {esc((h.get("gpu") or "").replace("NVIDIA GeForce ", ""))} · {h.get("ram_gib")} GB</h1>
 <div class="id">{esc(rec.get("created", "")[:16].replace("T", " "))} · suite v{esc(rec["suite"]["version"])} · {s["wall_minutes"] / 60:.1f} h</div></div>
 <div class="acts">{'<button class="btn" id="copy">COPY SETTINGS</button>' if argv else ""}</div></section>
<section class="panel sum">
 <div><b>{f"{vs:.0f}%" if vs is not None else "—"}</b><span>of Claude Opus 5.5 in this run · range {s["capability_ci95"][0] / s["capability"] * vs if vs else 0:.0f}–{min(100, s["capability_ci95"][1] / s["capability"] * vs) if vs else 0:.0f}</span></div>
 <div><b class="w">{s["solved"]}</b><span>of {s["items"]} tasks solved</span></div>
 <div><b>{sp.get("decode_tps"):.0f}</b><span>tok/s in a short chat{f" · {float(report._deep(sp)):.0f} with a long context" if report._deep(sp) not in ("-", "") else ""}</span></div>
 <div><b class="w">{f"{2000/bd[0][1]['prefill_tps']:.1f} s" if bd and bd[0][1].get("prefill_tps") else "—"}</b><span>first word at 2k</span></div>
 <div><b class="w">{s["solved_per_hour"]}</b><span>solved tasks per hour</span></div>
 <div><b class="w" style="color:var(--red)">{sum(1 for f in flags.values() if f["cut"]) + sum(1 for f in flags.values() if f["loop"])}</b><span>replies flagged: {sum(1 for f in flags.values() if f["cut"])} out of thinking room, {sum(1 for f in flags.values() if f["loop"])} looped</span></div></section>
<div class="two"><section class="panel sys"><div class="lbl">System</div><dl>
 <dt>GPU</dt><dd>{esc(h.get("gpu"))} · {h.get("vram_gib")} GiB</dd><dt>DRIVER</dt><dd>{esc(h.get("gpu_driver"))} · power limit {esc(h.get("gpu_power_limit_w"))} W</dd>
 <dt>CPU</dt><dd>{esc(h.get("cpu"))}</dd><dt>RAM</dt><dd>{h.get("ram_gib")} GiB · measured {h.get("ram_read_gbs")} GB/s read</dd><dt>OS</dt><dd>{esc(h.get("os"))}</dd>
 <dt>RUNTIME</dt><dd>llama.cpp {esc(rt.get("llama_cpp_build") or "not recorded")}</dd><dt>MODEL</dt><dd>{esc(m.get("hf_repo") or "")}<br><span class="q">{esc(m.get("file") or "")}</span></dd>
 <dt>SHA256</dt><dd class="{"" if m.get("sha256") else "todo"}">{esc(m.get("sha256") or "not recorded")}</dd></dl></section>
 <section class="panel"><div class="lbl">Settings used · the server's command line</div>
  <div class="argv" id="argv">{"".join(f"<span>{esc(x)}</span> " for x in _argv_lines(argv)) if argv else "<span class=q>not recorded for this run</span>"}</div>
  <div class="diff">{diff_html}{" <span class='q'>· consistent start to end</span>" if rt.get("settings_consistent") else ""}</div></section></div>
{_telemetry_panel(rec.get("telemetry"))}
<section class="panel tasks"><div class="lbl">Every task</div>
 <div class="tw"><table><tr><th class="l">TASK</th><th>SCORE</th><th>TIME</th><th>STEPS</th><th>LONGEST REPLY<br><span class="faint">tokens</span></th><th class="l">FLAGS</th></tr>{"".join(body_rows)}</table></div></section>'''
    return _page(f"llmbox · run {rec['id'][:8]} · {rid}", "MODELS", body, ("pages.css", "run.css"), ("run.js",))


