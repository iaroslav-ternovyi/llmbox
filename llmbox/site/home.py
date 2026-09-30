"""The home page: the chart, the ranking, what's new."""
from __future__ import annotations

import json
import os
import shutil
import statistics
import time

from .. import report
from .components import _marker, _pct, _profile, _spd, _stands_out
from .data import optimize_records, queue_state, shape_data
from .layout import ASSETS, PLAN_JS
from .stats import rank_ranges
from .words import _ago, _kind, _quant, _size, BLOCKS, esc, FAMILIES, family, LABEL, model_name, PRESETS


def _scatter(local: list[dict]) -> str:
    """Quality (% of frontier) x decode speed; the page's JS redraws it for the visitor's box (same drawing as SCATTER_JS)."""
    pts = [{"id": r["id"], "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "t2": r["speed"].get("decode_tps")} for r in local]
    return f'<noscript>{len(pts)} models; enable JavaScript for the chart</noscript>'


def home(out_dir: str, host: str = "box", suite_version: str | None = None, tier: str = "quick", rs: list[dict] | None = None,
         sd: dict | None = None) -> str:
    from ..import suite as _s
    suite_version = suite_version or _s.VERSION
    rs = rs if rs is not None else report.rows(host, suite_version=suite_version, tier=tier)
    ref = next((r for r in rs if r["host"].get("id") == "cloud"), None)
    local = [r for r in rs if r["host"].get("id") != "cloud" and not r.get("partial")]
    clouds = [r for r in rs if r["host"].get("id") == "cloud" and not r.get("partial")]
    hw = next((r["host"] for r in local), {})
    ref_box = f'{hw.get("gpu", "").replace("NVIDIA GeForce ", "")} + {hw.get("ram_gib", "?")} GB RAM'
    ranks = rank_ranges(local)
    q = [j for j in queue_state() if j["model"] not in {r["id"] for r in local}]
    sd = sd or shape_data(local, host)
    names0 = {r["id"]: model_name(r) for r in local}
    # one model in two quants (Tiel Q4 and Q6): the chart legend and the picks say which is which
    dup = {n for n in names0.values() if list(names0.values()).count(n) > 1}
    labels = {rid: n + (f" · {_quant(next(r['file'] for r in local if r['id'] == rid)).split(' ')[0]}" if n in dup else "") for rid, n in names0.items()}

    def pop(label: str, title: str, text: str) -> str:
        return f'<span class="tip">{label}<span class="pop"><b>{esc(title)}</b>{esc(text)}</span></span>'
    # what llmbox's settings are worth: the biggest measured gains over stock llama.cpp, same model, same box
    opts = {k: v for k, v in optimize_records(host).items() if k in {r["id"] for r in local}}
    gain = lambda o: o["summary"]["llmbox"]["decode"] / o["summary"]["stock"]["decode"] - 1
    top = sorted(opts.items(), key=lambda kv: -gain(kv[1]))[:4]
    optpanel = ("" if not top or gain(top[0][1]) < 0.15 else
                '<section class="panel feed optp"><div class="lbl">What the settings are worth</div><p class="q nf0">Same model, same PC: stock llama.cpp → llmbox settings.</p><ul>'
                + "".join(f'<li><a href="recipe-{esc(rid)}.html">{esc(names0[rid])}</a> <span class="fv">{o["summary"]["stock"]["decode"]:.0f} → {o["summary"]["llmbox"]["decode"]:.0f} tok/s</span>'
                          f'<em>{gain(o) * 100:+.0f}%</em></li>' for rid, o in top)
                + '</ul><p class="q nf"><a href="method.html#settings">all models →</a></p></section>')
    # the ranking: one line per model (place, name, score with its range, speed, fit, what stands out); the nine block
    # scores open under the line. Colour and marker as on the chart.
    med = {b: statistics.median(v) for b in BLOCKS if (v := [r["blocks"][b] for r in local if r["blocks"].get(b) is not None])}
    head = ("<tr><th class='rk'>#</th><th class='l'>MODEL</th><th class='sch' data-sort='score'><div class='fp'><div class='trk axis'></div><span class='num'>"
            + pop("SCORE ↕", "Score · % of Claude Opus 5.5", "How close the model gets to Claude Opus 5.5 on the same tasks (Opus = 100%). "
                  "The dot is the score, the line its 95% range: models whose lines overlap are not measurably apart yet. Click to sort.")
            + "</span></div></th><th class='r' data-sort='speed'>" + pop("TOK/S ↕", "Speed on your box", "Tokens per second while writing the answer, "
            "in a short chat (big number) and with a long document in context (small). Measured on the reference PC, predicted for the box you pick. Click to sort.")
            + "</th><th class='r'>" + pop("FITS", "Does it fit?", "Whether the model and its context fit in the graphics card plus RAM of the box you pick, "
            "and the largest context that does.") + "</th><th class='l'>" + pop("STANDS OUT", "Stands out", "Blocks where the model scores at least "
            "6 points above (▲) or below (▼) the typical (median) local model here. Click a row for all nine.") + "</th><th></th></tr>")
    body = []
    for r in local:
        rid, nm = r["id"], model_name(r)
        pl, lo, hi, grp = ranks[rid]
        tip = f"not measurably apart from places {lo}–{hi}" if lo != hi else "measurably apart from every other model"
        col, kind = family((sd["recipes"].get(rid) or {}).get("arch"))[1], _kind(r.get("hf_repo"))
        sub = " · ".join(x for x in (_quant(r["file"]), _size(sd["recipes"].get(rid), nm), kind if kind != "release" else "") if x)
        body.append(f"<tr class='mr' data-rid='{esc(rid)}' data-g='{grp}'><td class='rk' title='{tip}'>{pl}</td>"
                    f"<td class='l mod'><div class='mw'>{_marker(col, kind)}<a class='m' href='recipe-{esc(rid)}.html'>{esc(nm)}</a><span class='qt'>{esc(sub)}</span></div></td>"
                    f"<td class='sco'>{_pct(r.get('vs_ref'))}</td><td class='spd r'>{_spd(r['speed'])}</td><td class='fit r'>—</td>"
                    f"<td class='l so'>{_stands_out(r['blocks'], med)}</td>"
                    f"<td class='act'><label class='pick2' title='tick two to compare'><input type='checkbox' value='{esc(rid)}' aria-label='compare {esc(nm)}'></label>"
                    f"<button class='exp' aria-expanded='false' aria-label='all block scores of {esc(nm)}'>▾</button></td></tr>")
        body.append(f"<tr class='prof' data-for='{esc(rid)}' hidden><td colspan='7'>{_profile(r, med, col, ranks[rid])}</td></tr>")
    # cloud models: reference lines in the order (same tasks, same scale), not places in a ranking of what runs on a box
    for r in clouds:
        note = "cloud · the 100% mark" if ref and r["id"] == ref["id"] else "cloud · for comparison"
        body.append(f"<tr class='cloud' data-rid='{esc(r['id'])}'><td class='rk'>☁</td><td class='l mod'><div class='mw'><span></span><span class='m'>{esc(model_name(r))}</span><span class='qt'>{note}</span></div></td>"
                    f"<td class='sco'>{_pct(r.get('vs_ref'))}</td><td class='spd r'></td><td class='fit'></td><td class='so'></td><td class='act'></td></tr>")
    qline = ""
    run = next((j for j in q if j["status"] == "running"), None)
    nxt = [j["model"] for j in q if j is not run]
    if run or nxt:
        w = 100 * run["done"] / run["total"] if run and run["total"] else 0
        qline = ("<div class='queue'>" + (f"<span class='live'>●</span> Measuring <b>{esc(run['model'])}</b><span class='prog'><i style='width:{w:.0f}%'></i></span>"
                                          + (f"<span class='q'>{run['done']} of {run['total']} tasks</span>" if run["total"] else "<span class='q'>starting</span>") if run else "")
                 + (f"<span class='q nx'>Next: {esc(', '.join(nxt))}</span>" if nxt else "") + "</div>")

    feed, names = [], {r["id"]: model_name(r) for r in local + clouds}
    for j in q:
        if j["status"] == "running":
            feed.append(f"<li><span class='live'>●</span> <b>{esc(j['model'])}</b> <span class='q'>{f"measuring {j['done']}/{j['total']}" if j['total'] else "starting"}</span><span class='when'>now</span></li>")
    for rec in sorted(report.results.load_all(host) + report.results.load_all("cloud"), key=lambda x: x.get("created", ""), reverse=True):
        su, s = rec.get("suite", {}), rec.get("summary", {})
        if rec.get("kind") != "suite" or report.version_of(su) != suite_version or report.scale(su.get("tier")) != report.scale(tier) \
                or su.get("blocks"):
            continue   # only results comparable with the ranking above
        rid = (rec.get("recipe") or {}).get("id", "?")
        cloud = (rec.get("host") or {}).get("id") == "cloud"
        tps = (s.get("speed") or {}).get("decode_tps")
        nm = names.get(rid, rid)
        name = f"<a href='recipe-{esc(rid)}.html'>{esc(nm)}</a>" if rid in {r["id"] for r in local} else f"<b>{esc(nm)}</b>"
        feed.append(f"<li>{name} <span class='fv' title='the score of this one run; the ranking pools every run of the model'>"
                    f"{s.get('capability', 0):.1f} this run{f' · {tps:.0f} tok/s' if tps else ''}</span>"
                    f"<span class='when'>{'cloud' if cloud else esc((rec.get('host') or {}).get('gpu', '?').replace('NVIDIA GeForce ', ''))} · {_ago(rec.get('created', ''))}</span></li>")
        if len(feed) >= 6:
            break

    presets = "".join(f'<button class="{"on" if i == 0 else ""}" data-p="{i}" title="{esc(" · ".join(f"{LABEL[b].lower()} {v}" for b, v in w.items()))}">{esc(n)}</button>' for i, (n, w) in enumerate(PRESETS))
    data = dict(sd, families=[[n, c] for _k, n, c in FAMILIES], presets=[w for _, w in PRESETS], refBlocks=(ref or {}).get("blocks") or {},
                points=[{"id": r["id"], "name": labels[r["id"]], "model": names0[r["id"]], "quant": _quant(r["file"]).split(" ")[0],
                         "fam": family((sd["recipes"].get(r["id"]) or {}).get("arch"))[0], "kind": _kind(r.get("hf_repo")), "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "blocks": r["blocks"], "t2": r["speed"].get("decode_tps"),
                         "td": float(report._deep(r["speed"])) if report._deep(r["speed"]) != "-" else None, "rank": list(ranks[r["id"]])} for r in local]
                + [{"id": r["id"], "name": model_name(r), "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "blocks": r["blocks"], "t2": None, "td": None,
                    "rank": None, "cloud": True} for r in clouds])
    compare_tab = "compare.html"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>llmbox · What should I run on my box?</title><link rel="stylesheet" href="osc.css"><style>{_HOME_CSS}</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>
<div class="wrap">
<header class="plate"><a class="brand glow" href="index.html">LLMBOX<small>LOCAL LLM BENCHMARK</small></a>
 <nav class="tabs"><a class="on" href="index.html">MODELS</a><a href="new.html">NEW</a><a href="{esc(compare_tab)}">COMPARE</a><a href="method.html">METHOD</a></nav></header>
<h1 class="q1">What should I run on my box?</h1>
<p class="lede">AI models you can run on your own computer, graded on real work (coding, tools, documents, writing) and timed on a real PC.
Pick your graphics card or Mac: the table shows what fits, how fast it answers and how close it gets to Claude. Every model page has the file to download and settings to copy.</p>
{NEWBIE}
<section class="boxbar" id="box"><span class="sc">Your box</span>
 <select id="gpu" aria-label="GPU or Mac"><option value="">the reference PC ({esc(ref_box)})</option></select>
 <span class="bl">RAM</span><select id="ram" aria-label="System RAM or a Mac's unified memory"><option value="8">8 GB</option><option value="16">16 GB</option><option value="24">24 GB</option><option value="32">32 GB</option><option value="36">36 GB</option><option value="48">48 GB</option><option value="64" selected>64 GB</option><option value="96">96 GB</option><option value="128">128 GB</option><option value="192">192 GB</option><option value="256">256 GB</option><option value="512">512 GB</option></select>
 <span class="bl" id="bwl">speed</span><select id="bw" aria-label="RAM speed"></select>
 <input id="bwn" placeholder="GB/s" size="5" aria-label="measured RAM read speed, GB/s" title="your measured RAM read speed (llmbox host add)">
 <span id="boxnote" class="q">speeds measured on this box</span><span id="fitsum" class="q"></span></section>
<section class="panel chart hero"><h2 class="ch2">Smarter or faster: what runs best on your box</h2>
 <div id="scatter">{_scatter(local)}</div>
 <p class="cap">Each point is a model with the settings it was measured with. Higher = closer to Claude Opus 5.5 on the same tasks;
 further right = faster on the box you picked above. Bright points are the best trade-offs: no other model is both smarter and faster.
 Point at a model for its range and speed, click it for its page. <a href="method.html">How scores work</a></p></section>
<section class="panel rankp"><div class="lbl">Ranking <span class="faint">· suite v{esc(suite_version)}{" · preliminary: runs of this version are still coming in" if "-dev" in suite_version else ""}</span></div>
 <div class="rhead"><div class="seg" role="group" aria-label="rank by"><span class="sc">Rank by</span>{presets}</div>
  <div class="cmp"><span class="q" id="cmpn">tick two models to compare</span><a class="btn" id="cmpgo" aria-disabled="true">COMPARE</a></div></div>
 <div class="tw"><table class="rank"><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>
 <p class="rnote">Places by score. A dashed line between rows: every model above it is measurably better than the ones below; inside a group the order is not settled yet.
 Click a row for its nine block scores.</p>{qline}</section>
<div class="below">{optpanel}<section class="panel feed"><div class="lbl">Latest results</div><ul>{''.join(feed)}</ul></section>
 {_news_panel({r["id"] for r in local})}</div>
<footer><span>Every number comes from a saved run. The score does not depend on the box; speed does. <a href="method.html">How scores work →</a></span><span>generated {time.strftime('%b %d, %Y %H:%M')}</span></footer>
</div>
<script>const DATA = {json.dumps(data)};
{PLAN_JS}{_JS}</script></body></html>"""
    os.makedirs(out_dir, exist_ok=True)
    shutil.copy(os.path.join(ASSETS, "osc.css"), os.path.join(out_dir, "osc.css"))
    path = os.path.join(out_dir, "index.html")
    with open(path, "w") as f:
        f.write(page)
    return path


_NEWS_LABEL = {"new_model": "NEW MODEL", "new_files": "NEW FILES", "repo_update": "UPDATED", "runtime": "RUNTIME", "pr": "LLAMA.CPP"}


def _news_panel(ranked: set) -> str:
    """What's new out there (llmbox/watch.py, a daily look): new models, new files of measured ones, runtime releases."""
    from ..import watch as W
    evs = W.events(8)
    try:
        checked = json.load(open(W.STATE)).get("checked", "")
    except (OSError, ValueError):
        checked = ""
    if not evs and not checked:
        return ""
    items = []
    for e in evs:
        rids = [r for r in e.get("rids") or [] if r in ranked]
        items.append(f'<li><span class="nt {esc(e["type"])}">{_NEWS_LABEL.get(e["type"], e["type"].upper())}</span> '
                     + (f'<a href="{esc(e["url"])}" rel="noopener">{esc(e["title"])}</a>' if e.get("url") else f"<b>{esc(e['title'])}</b>")
                     + (f'<span class="nd">{esc(e["detail"])}</span>' if e.get("detail") else "")
                     + "".join(f'<a class="nr" href="recipe-{esc(r)}.html">our results →</a>' for r in rids[:1])
                     + f'<span class="when">{_ago(e["at"])}</span></li>')
    foot = f'<p class="q nf">Checked daily for new models, new files of the models above and runtime releases{f" · last look {_ago(checked)}" if checked else ""}.</p>'
    return ('<section class="panel feed news"><div class="lbl">What\'s new</div>'
            + (f"<ul>{''.join(items)}</ul>" if items else '<p class="q nf">Nothing new since the first look.</p>') + foot + "</section>")


# for visitors new to local models: how to read the page and the words it uses; closed by default
NEWBIE = """<details class="newbie"><summary>New to local models? How to use this page and what the words mean</summary>
<div class="nb"><ol class="steps">
<li><b>Pick your box.</b> Choose your graphics card (or your Mac) and how much RAM it has. Speeds and the FITS column change to your box.</li>
<li><b>Choose between smarter and faster.</b> The score says how close a model gets to Claude on the same tasks; tok/s says how fast it writes.
&ldquo;Rank by&rdquo; re-sorts for coding, documents or writing.</li>
<li><b>Open the model.</b> Its page has the file to download and the settings it was measured with, ready to copy into llama.cpp,
LM Studio or Ollama, on Linux, Windows or macOS.</li></ol>
<dl class="gl">
<dt>tok/s</dt><dd>Tokens per second, how fast the answer appears. A token is about &frac34; of a word. 20 reads comfortably; a coding agent feels quick from about 50.</dd>
<dt>Context</dt><dd>How much text the model keeps in view at once: the chat, your files, a document. 256k tokens is roughly a 500-page book.
A bigger context needs more memory, and answers get slower as it fills up (the &ldquo;long&rdquo; speed).</dd>
<dt>Quant (Q4_K_M, UD-Q4_K_XL, IQ3_XXS)</dt><dd>The model&rsquo;s numbers stored in fewer bits so it fits in memory. 4-bit (Q4) is the usual choice:
about a quarter of the original size for a small loss. Q3 and Q2 fit smaller boxes and lose more; Q6 and Q8 lose almost nothing.</dd>
<dt>MoE, &ldquo;35B-A3B&rdquo;</dt><dd>Mixture of experts: 35 billion parameters in total, but only 3 billion work on each token. It needs memory for all of them
and runs about as fast as a 3B model, which is why it still runs well when part of it sits in ordinary RAM. A dense model uses all of its parameters for every token.</dd>
<dt>VRAM and RAM</dt><dd>The graphics card&rsquo;s memory is fast; what does not fit there runs from system RAM, several times slower.
On a Mac both are the same unified memory.</dd>
<dt>MTP</dt><dd>Multi-token prediction: the model drafts the next few tokens at once and checks them, so it writes faster with the same answers.</dd>
<dt>Thinking</dt><dd>The model reasons before it answers: better answers, more waiting.</dd>
<dt>% of frontier</dt><dd>The score relative to Claude Opus 5.5, a leading cloud model, on the same tasks and graders (Opus = 100%).</dd>
</dl></div></details>"""


_HOME_CSS = """
.q1{font:600 34px/1.1 "IBM Plex Sans Condensed";margin:26px 0 14px;letter-spacing:.01em}
.lede{font-size:14px;line-height:1.65;color:var(--soft);max-width:92ch;margin:-4px 0 10px}
.newbie{border:1px solid var(--line2);margin:0 0 14px;font-size:13px}.newbie summary{cursor:pointer;padding:9px 14px;color:var(--amber);list-style:none}
.newbie summary::-webkit-details-marker{display:none}.newbie summary::before{content:"+ ";color:var(--muted)}.newbie[open] summary::before{content:"− "}
.newbie .nb{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);gap:10px 34px;padding:4px 18px 16px}
.newbie .steps{padding-left:18px;line-height:1.6;color:var(--soft)}.newbie .steps li{margin:0 0 8px}.newbie b{color:var(--ink);font-weight:500}
.newbie .gl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:7px 14px;line-height:1.55;margin:0}
.newbie dt{color:var(--amber);font-size:12px;padding-top:1px;max-width:150px}.newbie dd{margin:0;color:var(--soft)}
@media (max-width:760px){.newbie .nb{grid-template-columns:1fr}.newbie .gl{grid-template-columns:1fr}.newbie dd{margin-bottom:6px}}
.boxbar .bl{font-size:12px;color:var(--muted);margin-left:6px}
.side{display:flex;flex-direction:column;gap:22px;min-width:0}
.hero{padding:22px 22px 14px;margin:0 0 22px}.hero .ch2{font:600 22px "IBM Plex Sans Condensed";margin:0 0 12px;color:var(--ink)}
.hero .cap{font-size:12.5px;line-height:1.6;color:var(--muted);max-width:110ch;margin:8px 4px 2px}
#scatter{position:relative}.scatter2{width:100%;height:auto;display:block}
.scatter2 .gl{stroke:#1d1e19}.scatter2 .axl{stroke:#3a3b33}.scatter2 .ax{fill:#6c695f;font-size:12px}.scatter2 .axt{fill:#8b877b;font-size:12px}
.scatter2 .ref{stroke:#56544b;stroke-dasharray:6 5}.scatter2 .refl{fill:#8b877b;font-size:12px}.scatter2 .refv{fill:#c9c4b5}
.scatter2 .lb{fill:#7d7a70;font-size:12.5px;cursor:pointer}.scatter2 .lb.on{fill:#ece7da}.scatter2 .pt{cursor:pointer}
#scatter.hov .pt,#scatter.hov .lb{opacity:.18}#scatter.hov .on2{opacity:1!important}
.clg{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12.5px;color:var(--soft);margin:0 0 6px 4px}.clg span{display:inline-flex;align-items:center;gap:6px}
.clg i{width:10px;height:10px;border-radius:50%;display:inline-block}.clg .k{color:var(--muted)}
.ctip{position:absolute;z-index:5;width:270px;background:#15160f;border:1px solid var(--amber-dim);padding:10px 12px;font-size:12px;line-height:1.55;pointer-events:none}
.ctip b{display:block;color:var(--ink);font-weight:500;margin-bottom:2px}.ctip span{display:block;color:var(--muted)}.ctip em{font-style:normal;color:var(--amber)}

.news .nt{display:inline-block;font-size:10px;letter-spacing:.08em;padding:1px 5px;margin-right:4px;border:1px solid var(--amber-dim);color:var(--amber)}
.news .nt.repo_update,.news .nt.runtime,.news .nt.pr{border-color:var(--line);color:var(--muted)}
.news .nd{display:block;font-size:12px;color:var(--faint);margin-top:3px;overflow-wrap:anywhere}.news .nr{font-size:12px;margin-left:8px}
.news .nf{font-size:11.5px;padding:0 18px 14px;margin:0}
.boxbar{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:12px 16px;border:1px solid var(--line);background:var(--panel)}
.boxbar .sc{margin-right:4px}
.boxbar select,.boxbar input{background:#0b0c09;color:var(--ink);border:1px solid var(--line);padding:6px 8px;font:13px "IBM Plex Mono"}
.boxbar select:focus,.boxbar input:focus{border-color:var(--amber);outline:none}.boxbar select:disabled,.boxbar input:disabled{opacity:.35}
.boxbar #boxnote{margin-left:auto}.boxbar #fitsum{color:var(--soft)}.boxbar #fitsum:before{content:"· ";color:var(--faint)}
.rhead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;padding:18px 16px 6px}
.seg{display:flex;flex-wrap:wrap;align-items:center;gap:4px}.seg .sc{margin-right:8px}
.seg button{background:none;border:1px solid transparent;color:var(--muted);font:12px "IBM Plex Mono";padding:5px 10px;cursor:pointer;white-space:nowrap}
.seg button:hover{color:var(--ink)}.seg button.on{color:var(--amber);border-color:var(--amber-dim)}
.cmp{display:flex;align-items:center;gap:12px}.cmp .btn[aria-disabled=true]{opacity:.4;pointer-events:none}
.rnote{padding:8px 16px 0;font-size:12px;color:var(--faint)}
.rank th[data-sort]{cursor:pointer;user-select:none}.rank th[data-sort]:hover,.rank th[data-sort].on{color:var(--amber)}.rank th[data-sort].on .tip{color:var(--amber)}
.rank td.mod{min-width:190px}.rank td.so{font-size:12.5px;line-height:1.55;min-width:170px}.rank .fp .trk s{top:-13px;bottom:-13px}
.pfl u{position:relative;display:inline-block;top:1px;height:10px;margin:0 3px}
.rank th,.rank td{padding:12px 10px}.rank th{vertical-align:bottom}.rank .r{text-align:right}
.rank td.rk{color:var(--muted);width:30px;font-size:13px;white-space:nowrap;cursor:help}
.rank tr.mr{cursor:pointer}.rank tr.mr:hover td,.rank tr.open td{background:rgba(255,255,255,.022)}
.rank td.sco,.rank th.sch{width:31%;min-width:230px}
.sch .fp{align-items:flex-end}.sch .num{font:inherit;color:inherit;width:auto;white-space:nowrap}
.rank td.spd b{display:block;font:500 17px "IBM Plex Mono";color:var(--ink)}.rank td.spd b.pred{color:var(--soft)}
.rank td.spd small{display:block;font-size:11px;color:var(--faint)}
.rank td.fit{font-size:12.5px;color:var(--soft);white-space:nowrap}.rank td.fit .no{color:var(--red)}
.rank td.act{white-space:nowrap;width:60px}
.exp{background:none;border:0;color:var(--muted);font-size:13px;padding:2px 6px;cursor:pointer;transition:transform .15s}.rank tr.open .exp{transform:rotate(180deg);color:var(--amber)}
.rank tr.gs td{border-top:1px dashed rgba(255,176,0,.6)}
.rank tr.cloud td{padding-top:7px;padding-bottom:7px;color:var(--muted)}.rank tr.cloud .m{font-size:14px;color:var(--muted);font-weight:500}.rank tr.cloud .num{color:var(--muted);font-size:14px}
.rank tr.cloud td.rk{font-size:13px;cursor:default}
.rank tr.nofit td{opacity:.42}
.rank tr.prof>td{padding:4px 10px 20px 50px;text-align:left;background:rgba(255,255,255,.022)}
.pfl a{display:block;margin-top:4px}
.below{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px;align-items:start}
.pf{padding:4px 0 0}.pfl{display:flex;flex-wrap:wrap;gap:6px 22px;align-items:baseline;margin-top:10px;font-size:12.5px;color:var(--soft)}
.optp .nf0{padding:14px 18px 0;margin:0;font-size:12px}.optp .nf{padding:0 18px 14px;margin:0}.optp li{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px}.optp .fv{margin-left:0}.optp em{font-style:normal;color:var(--amber);margin-left:auto}
@media (max-width:1100px){.below{grid-template-columns:1fr 1fr}}
@media (max-width:760px){
 .hero{padding:16px 10px 10px}.below{grid-template-columns:1fr}.cmp{width:100%;justify-content:space-between}
 .rank thead{display:none}.rank,.rank tbody{display:block}
 .rank tr.mr{display:grid;grid-template-columns:24px max-content minmax(0,1fr) auto;grid-template-areas:"rk mod mod act" "rk sco sco sco" "rk spd fit so";column-gap:12px;border-bottom:1px solid var(--line2);padding:10px 0}
 .rank tr.mr td{display:block;border:0;padding:2px 0;min-width:0;width:auto;background:none!important}
 .rank td.rk{grid-area:rk;padding-top:4px}.rank td.mod{grid-area:mod}.rank td.act{grid-area:act;text-align:right}.rank td.sco{grid-area:sco;padding:8px 0 6px}
 .rank td.spd{grid-area:spd;text-align:left}.rank td.fit{grid-area:fit;text-align:left;padding-top:4px}.rank td.so{grid-area:so;font-size:12px}
 .rank td.spd b{font-size:15px}.fp .num{font-size:15px}.fp .trk s{top:0;bottom:0}.rank td.spd b::after{content:" tok/s";font:400 11px "IBM Plex Mono";color:var(--faint)}
 .rank tr.gs{border-top:1px dashed rgba(255,176,0,.6)}.rank tr.gs td{border-top:0}
 .rank tr.cloud{display:grid;grid-template-columns:24px minmax(0,1fr) minmax(0,1.3fr);grid-template-areas:"rk mod sco";column-gap:12px;align-items:center;border-bottom:1px solid var(--line2);padding:4px 0}
 .rank tr.cloud td{display:block;border:0;padding:2px 0;min-width:0;width:auto}.rank tr.cloud td.spd,.rank tr.cloud td.fit,.rank tr.cloud td.so,.rank tr.cloud td.act{display:none}
 .rank tr.cloud .qt{display:none}
 .rank tr.prof{display:block}.rank tr.prof[hidden]{display:none}.rank tr.prof>td{display:block;padding:6px 0 16px 36px;border:0}
}
.pick2 input{accent-color:#FFB000;width:15px;height:15px;cursor:pointer}
.queue{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;padding:12px 16px;border-top:1px solid var(--line2);font-size:13px}
.queue b{font-weight:500}.queue .nx{margin-left:auto}
.live{color:#ffd27a;animation:blink 1.4s steps(2) infinite}@keyframes blink{50%{opacity:.45}}
@media (prefers-reduced-motion:reduce){.live{animation:none}}
.prog{width:120px;height:4px;background:var(--line);display:inline-block}.prog i{display:block;height:100%;background:var(--amber)}
.chart{padding:18px 16px 10px}.scatter{width:100%;height:auto;display:block}
.sc2 .ci{stroke-opacity:.35}#scatter.hov .pt{opacity:.25}#scatter.hov .pt.on{opacity:1}#scatter .pt.on .ci{stroke-opacity:1}
.lgd2{list-style:none;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 18px;margin:8px 8px 0;font-size:13px}
.lgd2 li{display:grid;grid-template-columns:22px minmax(0,1fr) auto auto;gap:8px;align-items:center;padding:3px 4px;cursor:default}
.lgd2 li.on{background:rgba(255,176,0,.08)}.lgd2 b{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;background:var(--amber);color:var(--bg);font-size:11px}
.lgd2 .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lgd2 .lv{color:var(--amber);font-size:12px}
@media (max-width:900px){.lgd2{grid-template-columns:1fr}}
.legend{display:flex;gap:10px;align-items:flex-start;font-size:12px;color:var(--muted);margin:6px 8px 4px;line-height:1.5}.legend svg{flex:none;margin-top:2px}
.feed ul{list-style:none;padding:14px 18px}.feed li{font-size:13px;padding:8px 0;border-bottom:1px solid var(--line2)}.feed li:last-child{border-bottom:0}
.feed .fv{color:var(--soft);margin-left:6px}.feed .when{display:block;font-size:11px;color:var(--faint)}
@media (max-width:900px){
 .q1{font-size:26px}.boxbar #boxnote{margin-left:0;width:100%}.boxbar select{max-width:100%;min-width:0}.boxbar #gpu{width:100%}
}
"""


_JS = r"""
const $ = s => document.querySelector(s);
const fmt = v => v.toFixed(0);
const kfmt = c => `${Math.round(c / 1024)}k`;
function sameClass(hw) { const r = DATA.ref; return hw.gpu === r.gpu && Math.abs(hw.rambw - r.rambw) / r.rambw < 0.15 && hw.ram >= r.ram * 0.9; }
function weighted(b, w) { let s = 0, n = 0; for (const k in w) { s += (b[k] || 0) * w[k]; n += w[k]; } return n ? s / n : 0; }
function scatter(pts) {   // up = closer to Claude Opus, right = faster on the box picked. Names sit at their points.
  // a phone gets its own proportions (narrower and taller), not the desktop chart shrunk until its names are unreadable
  const narrow = (document.querySelector("#scatter") || {}).clientWidth < 700;
  const W = narrow ? 460 : 1000, H = narrow ? 600 : 540, L = narrow ? 44 : 58, R = narrow ? 346 : 812, T = 26, B = narrow ? 536 : 478;   // the right margin names the Claude lines
  const loc = pts.filter(p => !p.cloud && p.t2 && p.vs != null), cloud = pts.filter(p => p.cloud && p.vs != null).sort((a, b) => b.vs - a.vs);
  if (!loc.length) return "";
  const fams = Object.fromEntries(DATA.families), col = p => fams[p.fam] || "#9AA0A6";
  // y: just under the weakest model at >= 50% of the frontier; weaker ones sit on the floor with an arrow
  const main = loc.filter(p => p.vs >= 50), lowest = Math.min(...(main.length ? main : loc).map(p => p.vs));
  const ymin = Math.max(0, Math.floor((lowest - 6) / 5) * 5), ystep = 100 - ymin > 40 ? 10 : 5;
  const xs = loc.map(p => p.t2), span = Math.max(10, Math.max(...xs) - Math.min(...xs));
  const xstep = span > 150 ? 50 : span > 60 ? 20 : span > 25 ? 10 : 5;
  const xmin = Math.max(0, Math.floor((Math.min(...xs) - span * 0.12) / xstep) * xstep), xmax = Math.ceil((Math.max(...xs) + span * 0.12) / xstep) * xstep;
  const X = v => L + (v - xmin) / (xmax - xmin) * (R - L), Y = v => B - (Math.max(v, ymin) - ymin) / (100 - ymin) * (B - T);
  const front = new Set(loc.filter(p => !loc.some(q => q !== p && q.t2 >= p.t2 && q.vs >= p.vs && (q.t2 > p.t2 || q.vs > p.vs))).map(p => p.id));
  let g = "";
  for (let v = ymin; v <= 100; v += ystep) g += `<line x1="${L}" y1="${Y(v)}" x2="${R}" y2="${Y(v)}" class="gl"/><text x="${L - 10}" y="${Y(v) + 4}" text-anchor="end" class="ax">${v}%</text>`;
  for (let v = xmin; v <= xmax; v += xstep) g += `<text x="${X(v)}" y="${B + 22}" text-anchor="middle" class="ax">${v}</text>`;
  g += `<line x1="${L}" y1="${B}" x2="${R}" y2="${B}" class="axl"/>`;
  // the cloud models: reference lines, named in the right margin (labels pushed apart when close)
  let lastY = -99;
  cloud.forEach(c => { const y = Y(c.vs), ly = Math.max(y + 4, lastY + 15); lastY = ly;
    g += `<line x1="${L}" y1="${y}" x2="${R}" y2="${y}" class="ref"/><text x="${R + 8}" y="${ly}" class="refl">${narrow ? c.name.replace("Claude ", "") : c.name} <tspan class="refv">${Math.round(c.vs)}%</tspan></text>`; });
  // one model in several variants (quants): a line through them, slowest to fastest
  const series = {};
  loc.forEach(p => (series[p.model] = series[p.model] || []).push(p));
  Object.values(series).filter(s => s.length > 1).forEach(s => { s.sort((a, b) => a.t2 - b.t2);
    g += `<polyline points="${s.map(p => `${X(p.t2)},${Y(p.vs)}`).join(" ")}" fill="none" stroke="${col(s[0])}" stroke-width="2" stroke-opacity=".55"/>`; });
  // markers: filled = the maker's own release, ring = a fine-tune, diamond = an uncensored remix; faded = another model is smarter and faster
  const boxes = [];
  const order = loc.slice().sort((a, b) => (front.has(b.id) - front.has(a.id)) || b.vs - a.vs);
  order.slice().reverse().forEach(p => { const x = X(p.t2), y = Y(p.vs), c = col(p), op = front.has(p.id) ? 1 : .42, low = p.vs < ymin;
    const mk = p.kind === "uncensored" ? `<path d="M${x} ${y - 7}L${x + 7} ${y}L${x} ${y + 7}L${x - 7} ${y}Z" fill="#0E0F0C" stroke="${c}" stroke-width="2.2"/>`
      : `<circle cx="${x}" cy="${y}" r="6.5" fill="${p.kind === "fine-tune" ? "#0E0F0C" : c}" stroke="${c}" stroke-width="2.2"${p.pred ? ' stroke-dasharray="3 2"' : ""}/>`;
    g += `<g class="pt" data-id="${p.id}" opacity="${op}">${mk}${low ? `<path d="M${x - 4} ${y + 11}L${x + 4} ${y + 11}L${x} ${y + 17}Z" fill="${c}"/>` : ""}` +
         `<circle cx="${x}" cy="${y}" r="16" fill="transparent"/></g>`;
    boxes.push([x - 8, y - 8, x + 8, y + 8]); });
  // names next to their points: the first free spot of eight around it, the models on the frontier first
  const hit = (b) => b[0] < L + 2 || b[2] > R + 2 || b[1] < T - 14 || b[3] > B + 2 || boxes.some(o => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]);
  const sq = q => (q || "").replace(/^UD-/, "");
  const labelOf = p => { const s = series[p.model]; if (!s || s.length < 2) return p.model;
    return s.reduce((a, b) => (b.vs > a.vs ? b : a)) === p ? `${p.model} · ${sq(p.quant)}` : sq(p.quant); };
  // the series lines are obstacles for names too: sample points along them
  Object.values(series).filter(s => s.length > 1).forEach(s => { for (let i = 1; i < s.length; i++) {
    const [a, b] = [s[i - 1], s[i]]; for (let t = 0.15; t < 0.9; t += 0.1) { const x = X(a.t2 + (b.t2 - a.t2) * t), y = Y(a.vs + (b.vs - a.vs) * t); boxes.push([x - 3, y - 3, x + 3, y + 3]); } } });
  order.forEach(p => { const x = X(p.t2), y = Y(p.vs), txt = labelOf(p) + (p.vs < ymin ? ` ${Math.round(p.vs)}%` : ""), w = txt.length * 7.7 + 4;   // IBM Plex Mono at 12.5: ~7.5 units a character
    const spots = [[x + 11, y + 4, "start"], [x - 11, y + 4, "end"], [x, y - 13, "middle"], [x, y + 21, "middle"],
                   [x + 10, y - 9, "start"], [x + 10, y + 17, "start"], [x - 10, y - 9, "end"], [x - 10, y + 17, "end"]];
    for (const [tx, ty, an] of spots) {
      const x0 = an === "start" ? tx : an === "end" ? tx - w : tx - w / 2, b = [x0, ty - 11, x0 + w, ty + 3];
      if (!hit(b)) { boxes.push(b); g += `<text x="${tx}" y="${ty}" text-anchor="${an}" class="lb${front.has(p.id) ? " on" : ""}" data-id="${p.id}">${txt}</text>`; return; }
    }
  });
  const famsUsed = [...new Set(loc.map(p => p.fam))];
  const legend = `<div class="clg">${famsUsed.map(f => `<span><i style="background:${fams[f]}"></i>${f}</span>`).join("")}` +
    `<span class="k"><svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="#8b877b"/></svg>release</span>` +
    `<span class="k"><svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="none" stroke="#8b877b" stroke-width="2"/></svg>fine-tune</span>` +
    `<span class="k"><svg width="14" height="14"><path d="M7 1L13 7L7 13L1 7Z" fill="none" stroke="#8b877b" stroke-width="2"/></svg>uncensored</span>` +
    `<span class="k"><svg width="22" height="10"><line x1="0" y1="5" x2="22" y2="5" stroke="#8b877b" stroke-width="2"/></svg>same model, other quant</span></div>`;
  return legend + `<svg viewBox="0 0 ${W} ${H}" class="scatter2" font-family="IBM Plex Mono" role="img" aria-label="score against speed">${g}` +
    `<text x="${(L + R) / 2}" y="${H - 8}" text-anchor="middle" class="axt">tokens per second on your box →</text>` +
    `<text transform="translate(16 ${(T + B) / 2}) rotate(-90)" text-anchor="middle" class="axt">↑ share of Claude Opus 5.5's score</text></svg><div class="ctip" hidden></div>`;
}
function hoverScatter(pts) {   // a model's point or name: its details in a tip, the others step back
  const box = document.querySelector("#scatter"), tip = box.querySelector(".ctip"), by = Object.fromEntries((pts || []).map(p => [p.id, p]));
  if (!tip) return;
  box.querySelectorAll(".pt, .lb").forEach(el => {
    el.addEventListener("mouseenter", () => { const p = by[el.dataset.id]; if (!p) return;
      box.classList.add("hov"); box.querySelectorAll(`[data-id="${p.id}"]`).forEach(x => x.classList.add("on2"));
      const k = p.vs / p.cap, lo = Math.round(p.ci[0] * k), hi = Math.min(100, Math.round(p.ci[1] * k));
      tip.innerHTML = `<b>${p.name}</b><span>${p.fam} · ${p.kind}</span><span>score <em>${Math.round(p.vs)}%</em> of Claude Opus (95%: ${lo}-${hi})</span>` +
        `<span>speed <em>${p.pred ? "~" : ""}${Math.round(p.t2)} tok/s</em>${p.pred ? " predicted for your box" : " measured"}${p.td ? ` · ${Math.round(p.td)} deep in context` : ""}</span>`;
      const r = box.getBoundingClientRect(), e = el.getBoundingClientRect();
      tip.hidden = false; tip.style.left = Math.min(r.width - 280, Math.max(0, e.left - r.left + 18)) + "px"; tip.style.top = (e.top - r.top - 8) + "px"; });
    el.addEventListener("mouseleave", () => { box.classList.remove("hov"); box.querySelectorAll(".on2").forEach(x => x.classList.remove("on2")); tip.hidden = true; });
    el.addEventListener("click", () => { location.href = `recipe-${el.dataset.id}.html`; });
  });
}
let resizeT = null, lastNarrow = null;   // the chart has phone and desktop proportions: redraw when the width crosses over
addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { const n = (document.querySelector("#scatter") || {}).clientWidth < 700;
  if (n !== lastNarrow) { lastNarrow = n; render(); } }, 200); });
let hwNow = null, preset = 0, sortBy = "score", sortDir = 1;   // any column with data-sort; a second click reverses it
document.querySelectorAll(".rank th[data-sort]").forEach(th => th.addEventListener("click", () => {
  sortDir = sortBy === th.dataset.sort ? -sortDir : 1; sortBy = th.dataset.sort;
  document.querySelectorAll(".rank th[data-sort]").forEach(t => t.classList.toggle("on", t === th)); render(); }));
const measured = {};
document.querySelectorAll("tr[data-rid]").forEach(r => measured[r.dataset.rid] = r.querySelector(".spd").innerHTML);
function axisOf(pts) {   // the score column's scale: as the chart's, from just under the weakest model at >= 50% to 100%
  const loc = pts.filter(p => !p.cloud && p.vs != null), main = loc.filter(p => p.vs >= 50);
  if (!loc.length) return null;
  const min = Math.max(0, Math.floor((Math.min(...(main.length ? main : loc).map(p => p.vs)) - 6) / 5) * 5);
  return { min, step: 100 - min > 40 ? 10 : 5, X: v => Math.max(0, Math.min(100, (v - min) / (100 - min) * 100)) };
}
function scoreCell(p, ax) {   // a dot at the score, a line over its 95% range; a Claude model: a dashed mark, its score being a reference
  if (p.vs == null || !ax) return "—";   // no reference model on this scale: no percent
  const c = p.cloud ? "#8b877b" : (Object.fromEntries(DATA.families)[p.fam] || "#9AA0A6");
  let g = ""; for (let v = ax.min; v <= 100; v += ax.step) g += `<s style="left:${ax.X(v)}%"></s>`;
  if (p.cloud) return `<div class="fp"><div class="trk">${g}<b class="rf" style="left:${ax.X(p.vs)}%"></b></div><span class="num">${Math.round(p.vs)}%</span></div>`;
  const k = p.vs / p.cap, lo = p.ci[0] * k, hi = Math.min(100, p.ci[1] * k);
  return `<div class="fp" title="95% range ${Math.round(lo)}–${Math.round(hi)}%"><div class="trk">${g}<i style="left:${ax.X(lo)}%;width:${ax.X(hi) - ax.X(lo)}%;background:${c}"></i>` +
    `<b style="left:${ax.X(p.vs)}%;background:${c}"></b>${p.vs < ax.min ? `<em>◂ ${Math.round(p.vs)}%</em>` : ""}</div><span class="num">${Math.round(p.vs)}%</span></div>`;
}
function render() {
  const w = DATA.presets[preset], refW = weighted(DATA.refBlocks, w);
  const pts = DATA.points.map(p => Object.assign({}, p, preset ? { vs: 100 * weighted(p.blocks, w) / refW, cap: weighted(p.blocks, w), ci: [p.ci[0] / p.cap * weighted(p.blocks, w), p.ci[1] / p.cap * weighted(p.blocks, w)] } : {}));
  const ax = axisOf(pts);
  let lab = ""; if (ax) for (let v = ax.min; v <= 100; v += ax.step) lab += `<span style="left:${ax.X(v)}%">${v}</span>`;
  $(".rank .axis").innerHTML = lab;
  for (const p of pts) {
    const sh = DATA.recipes[p.id], row = document.querySelector(`tr[data-rid="${p.id}"]`);
    if (!row) continue;
    row.querySelector(".sco").innerHTML = scoreCell(p, ax);
    row.classList.remove("nofit");
    if (p.cloud) continue;   // no box to predict for
    if (sh && hwNow && !sameClass(hwNow)) {
      const f = forBox(sh, hwNow); p.t2 = f.t2; p.td = f.td; p.pred = true;
      row.querySelector(".spd").innerHTML = f.fits ? `<b class="pred">~${fmt(f.t2)}</b><small>~${fmt(f.td)} long</small>` : "—";
      row.querySelector(".fit").innerHTML = f.fits ? `✓ ${kfmt(f.ctx)}` : `<span class="no">✗ too big</span>`;
      if (!f.fits) { row.classList.add("nofit"); p.t2 = null; }
    } else {
      row.querySelector(".spd").innerHTML = measured[p.id];
      row.querySelector(".fit").innerHTML = sh ? `✓ ${kfmt(sh.ctx)}` : "—";
    }
  }
  const loc = pts.filter(p => !p.cloud), nfit = loc.filter(p => p.t2).length;
  $("#fitsum").textContent = nfit === loc.length ? `all ${loc.length} models fit` : `${nfit} of ${loc.length} models fit`;
  const tb = $(".rank tbody");
  const key = sortBy === "speed" ? p => p.t2 || 0 : p => p.vs ?? -1;
  const by = (a, b) => sortDir * (key(b) - key(a)) || (b.vs ?? -1) - (a.vs ?? -1);
  const ranked = pts.filter(p => !p.cloud).sort((a, b) => (b.vs ?? -1) - (a.vs ?? -1)).map(p => p.id);
  const byScore = !preset && sortBy === "score" && sortDir === 1;   // group lines only make sense in the score order they were cut in
  let prevG = null;
  pts.slice().sort(by).forEach((p) => { const i = ranked.indexOf(p.id); const row = document.querySelector(`tr[data-rid="${p.id}"]`);
    if (p.cloud) { tb.appendChild(row); return; }
    row.querySelector(".rk").textContent = preset ? i + 1 : p.rank[0];
    row.classList.toggle("gs", byScore && prevG !== null && p.rank[3] !== prevG); prevG = p.rank[3];
    tb.appendChild(row); tb.appendChild(document.querySelector(`tr.prof[data-for="${p.id}"]`)); });
  $("#scatter").innerHTML = scatter(pts);
  hoverScatter(pts);
}
document.querySelectorAll(".rank tr.mr").forEach(tr => tr.addEventListener("click", e => {   // a row opens its nine block scores
  if (e.target.closest("a, label, input")) return;
  const pr = document.querySelector(`tr.prof[data-for="${tr.dataset.rid}"]`), open = pr.hidden;
  pr.hidden = !open; tr.classList.toggle("open", open); tr.querySelector(".exp").setAttribute("aria-expanded", String(open)); }));
function readBox() {
  const g = DATA.gpus.find(x => x[0] === $("#gpu").value);
  ["#ram", "#bw", "#bwn"].forEach(s => $(s).disabled = !g);
  if (!g) { hwNow = null; $("#boxnote").textContent = "speeds measured on this box"; try { localStorage.removeItem("llmbox-box"); } catch (e) {} history.replaceState(null, "", location.pathname); render(); return; }
  const bw = parseFloat($("#bwn").value) || parseFloat($("#bw").value);
  hwNow = boxFrom(g, parseInt($("#ram").value), bw);
  ["#bw", "#bwn"].forEach(s => $(s).disabled = !!hwNow.mac);   // a Mac's memory speed comes with the chip
  $("#boxnote").textContent = hwNow.mac ? "Mac: rough, for MLX-class engines (llama.cpp on Metal is often slower) - nothing here is measured on a Mac" :
    sameClass(hwNow) ? "same class as the reference box: measured speeds" : "speeds predicted for this box (~)";
  try { localStorage.setItem("llmbox-box", JSON.stringify({ gpu: $("#gpu").value, ram: $("#ram").value, bw: $("#bw").value, bwn: $("#bwn").value })); } catch (e) {}
  history.replaceState(null, "", `#gpu=${encodeURIComponent(g[0])}&ram=${$("#ram").value}&bw=${bw}`);
  render();
}
$("#gpu").insertAdjacentHTML("beforeend", `<optgroup label="NVIDIA + system RAM">${DATA.gpus.filter(g => g[3] !== "mac").map(g => `<option>${g[0]}</option>`).join("")}</optgroup>`
  + `<optgroup label="Mac (unified memory)">${DATA.gpus.filter(g => g[3] === "mac").map(g => `<option>${g[0]}</option>`).join("")}</optgroup>`);
for (const r of DATA.ramKinds) $("#bw").insertAdjacentHTML("beforeend", `<option value="${r[1]}">${r[0]} · ${r[1]} GB/s</option>`);
$("#bw").value = String(DATA.ramKinds.reduce((a, r) => Math.abs(r[1] - DATA.ref.rambw) < Math.abs(a - DATA.ref.rambw) ? r[1] : a, DATA.ramKinds[0][1]));
["#gpu", "#ram", "#bwn"].forEach(s => $(s).addEventListener("change", readBox));
$("#bw").addEventListener("change", () => { $("#bwn").value = ""; readBox(); });   // a preset replaces a typed-in measurement
document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".seg button").forEach(u => u.classList.remove("on")); b.classList.add("on"); preset = +b.dataset.p; render(); }));
const order = DATA.points.slice().sort((a, b) => b.cap - a.cap || (a.id < b.id ? -1 : 1)).map(p => p.id);   // the better model of a ticked pair goes first
document.querySelectorAll(".pick2 input").forEach(c => c.addEventListener("change", () => {
  const on = [...document.querySelectorAll(".pick2 input:checked")];
  if (on.length > 2) { on.filter(x => x !== c)[0].checked = false; }
  const ids = [...document.querySelectorAll(".pick2 input:checked")].map(x => x.value).sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const go = $("#cmpgo");
  if (ids.length === 2) { go.href = `compare.html#${ids[0]}-vs-${ids[1]}`; go.setAttribute("aria-disabled", "false"); go.classList.add("solid"); $("#cmpn").textContent = `${ids[0]} vs ${ids[1]}`; }
  else { go.removeAttribute("href"); go.setAttribute("aria-disabled", "true"); go.classList.remove("solid"); $("#cmpn").textContent = ids.length ? `${ids[0]} vs …` : "tick two models to compare"; }
}));
try { const h = Object.fromEntries(new URLSearchParams(location.hash.slice(1))); const saved = h.gpu ? h : JSON.parse(localStorage.getItem("llmbox-box") || "null");
  if (saved && saved.gpu) { $("#gpu").value = saved.gpu; if (saved.ram) $("#ram").value = saved.ram; if (saved.bw) { const o = [...$("#bw").options].find(o => o.value == saved.bw); if (o) $("#bw").value = saved.bw; else $("#bwn").value = saved.bw; } if (saved.bwn) $("#bwn").value = saved.bwn; readBox(); } else { ["#ram", "#bw", "#bwn"].forEach(s => $(s).disabled = true); render(); }
} catch (e) { render(); }
"""
