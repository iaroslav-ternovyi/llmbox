"""People's pages: u-<handle>.html for everyone who sent results signed in (their machines, how fast each model ran
there against machines like it, their quality runs and whether they count), and people.html listing them. A page
shows the GitHub name only when its owner made it public (llmbox profile --public); otherwise the handle."""
from __future__ import annotations

import json
import os

from .. import hwclass, irt, report
from .data import _at
from .layout import _page
from .words import esc

HELD_WHY = {"self-seeded": "its tasks did not come from the server", "outlier": "it scored unlike every other run of this model",
            "anonymous": "sent before signing in"}


def users(path: str) -> dict:
    """handle -> {login (public ones only), public}: the intake's users.json (llmbox serve writes it)."""
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


def _name(handle: str, us: dict) -> str:
    return (us.get(handle) or {}).get("login") or handle


def _place(v: float, values: list[float]) -> str:
    """'faster than 70% of 14 machines like it' from the class's machine medians."""
    others = len(values) - 1
    if others < 1:
        return "the only machine of its kind so far"
    below = sum(1 for x in values if x < v)
    return f"faster than {round(100 * below / others)}% of the {others} other machine{'s' if others > 1 else ''} like it"


def pages(names: dict, cs: dict, us: dict) -> dict[str, str]:
    """{file name: html}: a page per person with results, and people.html."""
    by: dict = {}
    for _p, r in report.results.files("community"):
        h = (r.get("submission") or {}).get("user")
        if h:
            by.setdefault(h, []).append(r)
    out, rows = {}, []
    for h, recs in sorted(by.items()):
        nm = _name(h, us)
        machines = {}
        for r in recs:
            fp = r.get("host") or {}
            machines.setdefault(fp.get("id"), (hwclass.label(fp.get("class") or hwclass.of_host(fp)), fp.get("cpu") or "", fp.get("ram_gib")))
        sp_rows, q_rows, counted = [], [], 0
        for r in sorted(recs, key=lambda x: x.get("created", ""), reverse=True):
            rid = (r.get("recipe") or {}).get("id") or "?"
            model = names.get(rid) or os.path.basename((r.get("model") or {}).get("file") or rid)
            link = f'<a href="recipe-{esc(rid)}.html">{esc(model)}</a>' if rid in names else esc(model)
            k = (r.get("host") or {}).get("class") or hwclass.of_host(r.get("host") or {})
            if r.get("kind") == "speed" and (r.get("speed") or {}).get("decode_tps"):
                sp = r["speed"]
                c = next((x for x in cs.get(rid, []) if x["class"] == k), None)
                t32 = _at(sp, 24000, 48000)
                sp_rows.append(f"<tr><td class='l'>{link}</td><td class='l q'>{esc(hwclass.label(k))}</td><td>{sp['decode_tps']:.0f}</td>"
                               f"<td>{f'{t32:.0f}' if t32 else '—'}</td><td>{c['t2']:.0f}</td>" if c else
                               f"<tr><td class='l'>{link}</td><td class='l q'>{esc(hwclass.label(k))}</td><td>{sp['decode_tps']:.0f}</td>"
                               f"<td>{f'{t32:.0f}' if t32 else '—'}</td><td>—</td>")
                sp_rows[-1] += f"<td class='l q'>{esc(_place(sp['decode_tps'], c['values']) if c else '')}</td><td class='q'>{esc(r.get('created', '')[:10])}</td></tr>"
            elif r.get("kind") == "suite":
                flags = set((r.get("submission") or {}).get("flags") or []) & irt.HELD
                bank = irt.bank_for((r.get("suite") or {}).get("content_hash"))
                ok = [x for x in (irt.counted(y) for y in r.get("rows") or []) if x]
                sc = irt.score_rows(bank, ok) if bank and ok else None
                ver = r.get("verified") or {}
                why = "; ".join(HELD_WHY.get(f, f) for f in sorted(flags))
                counted += not flags
                q_rows.append(f"<tr><td class='l'>{link}</td><td>{sc['capability']:.1f}</td>" if sc else f"<tr><td class='l'>{link}</td><td>—</td>")
                q_rows[-1] += (f"<td>{ver.get('graded', 0)}</td><td class='l'>{'<b>counts</b> toward the score' if not flags else 'kept aside: ' + esc(why)}</td>"
                               f"<td class='q'>{esc(r.get('created', '')[:10])}</td></tr>")
        mach = "".join(f"<li>{esc(lab)} <span class='q'>· {esc(cpu)} · {ram or '?'} GB RAM</span></li>" for lab, cpu, ram in machines.values())
        body = f'''
<section class="panel hd"><div><div class="crumb"><a href="people.html">People</a> / {esc(nm)}</div>
 <h1>{esc(nm)}</h1><p class="q" style="margin-top:6px">{len(machines)} machine{"s" if len(machines) != 1 else ""} · {len(sp_rows)} speed measurements · {len(q_rows)} quality runs, {counted} counted</p></div></section>
<section class="panel pad"><div class="lbl">Machines</div><ul class="plain">{mach}</ul></section>
<section class="panel pad"><div class="lbl">Speed</div>''' + (f'''<div class="tw"><table class="opt"><thead><tr><th class="l">Model</th><th class="l">Hardware</th><th>tok/s</th><th>@32k</th><th>class median</th><th class="l">place</th><th>date</th></tr></thead>
 <tbody>{"".join(sp_rows)}</tbody></table></div>''' if sp_rows else "<p class='q'>No speed measurements yet.</p>") + '''</section>
<section class="panel pad"><div class="lbl">Quality runs</div>''' + (f'''<div class="tw"><table class="opt"><thead><tr><th class="l">Model</th><th>score</th><th>answers re-graded</th><th class="l">status</th><th>date</th></tr></thead>
 <tbody>{"".join(q_rows)}</tbody></table></div><p class="q" style="margin-top:8px">Score: % of the capability scale from this run alone; the model's page pools every counted run. <a href="method.html#trust">How runs are checked →</a></p>''' if q_rows else "<p class='q'>No quality runs yet (llmbox test).</p>") + "</section>"
        out[f"{h}.html"] = _page(f"llmbox · {nm}", "", body, ("pages.css", "method.css"),
                                 about=f"{nm}'s machines and results on llmbox: {len(sp_rows)} speed measurements, {len(q_rows)} quality runs.")
        rows.append((len(sp_rows) + len(q_rows), f"<tr><td class='l'><a href='{esc(h)}.html'>{esc(nm)}</a></td><td>{len(machines)}</td>"
                                                 f"<td>{len(sp_rows)}</td><td>{counted} / {len(q_rows)}</td></tr>"))
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / people</div><h1>People who measured</h1>
 <p class="q" style="margin-top:6px">Everyone who sent results signed in (llmbox login). A name shows only if its owner chose so; otherwise a handle.
 <a href="method.html#people">Measure your own computer →</a></p></div></section>
<section class="panel pad">''' + (f'''<div class="tw"><table class="opt"><thead><tr><th class="l">Who</th><th>machines</th><th>speed</th><th>quality runs counted</th></tr></thead>
 <tbody>{"".join(r for _n, r in sorted(rows, key=lambda x: -x[0]))}</tbody></table></div>''' if rows else "<p class='q'>Nobody yet: the first results will show here.</p>") + "</section>"
    out["people.html"] = _page("llmbox · people", "", body, ("pages.css", "method.css"),
                               about="The people who measured local AI models on their own computers for llmbox.")
    return out
