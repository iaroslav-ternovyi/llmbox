"""The home page: the chart, the ranking, what's new."""
from __future__ import annotations

import json
import os
import statistics

from .. import report
from .components import _marker, _pct, _profile, _spd, _stands_out
from .data import optimize_records, queue_state, shape_data
from .layout import _page
from .stats import rank_ranges
from .words import _ago, _kind, _size, BLOCKS, esc, FAMILIES, family, LABEL, model_name, PRESETS, SHORT, variant


def _scatter(local: list[dict]) -> str:
    """Quality (% of frontier) x decode speed; the page's JS redraws it for the visitor's box (same drawing as SCATTER_JS)."""
    pts = [{"id": r["id"], "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "t2": r["speed"].get("decode_tps")} for r in local]
    return f'<noscript>{len(pts)} models; enable JavaScript for the chart</noscript>'


def _pop(label: str, title: str, text: str) -> str:
    return f'<span class="tip">{label}<span class="pop"><b>{esc(title)}</b>{esc(text)}</span></span>'


def _settings_worth(host: str, local: list[dict], names0: dict) -> str:
    """What llmbox's settings are worth: the biggest measured gains over stock llama.cpp, same model, same box."""
    opts = {k: v for k, v in optimize_records(host).items() if k in {r["id"] for r in local}}
    gain = lambda o: o["summary"]["llmbox"]["decode"] / o["summary"]["stock"]["decode"] - 1
    top = sorted(opts.items(), key=lambda kv: -gain(kv[1]))[:4]
    return ("" if not top or gain(top[0][1]) < 0.15 else
            '<section class="panel feed optp"><div class="lbl">What the settings are worth</div><p class="q nf0">Same model, same PC: stock llama.cpp → llmbox settings.</p><ul>'
            + "".join(f'<li><a href="recipe-{esc(rid)}.html">{esc(names0[rid])}</a> <span class="fv">{o["summary"]["stock"]["decode"]:.0f} → {o["summary"]["llmbox"]["decode"]:.0f} tok/s</span>'
                      f'<em>{gain(o) * 100:+.0f}%</em></li>' for rid, o in top)
            + '</ul><p class="q nf"><a href="method.html#settings">all models →</a></p></section>')


def _ranking(local: list[dict], clouds: list[dict], ref: dict | None, ranks: dict, sd: dict) -> tuple[str, list[str]]:
    """The ranking's header and rows: one line per model (place, name, score with its range, speed, fit, what stands
    out), its uses and blocks opening under the line; Claude as reference rows. Colour and marker as on the chart."""
    med = {b: statistics.median(v) for b in BLOCKS if (v := [r["blocks"][b] for r in local if r["blocks"].get(b) is not None])}
    head = ("<tr><th class='rk'>#</th><th class='l'>MODEL</th><th class='sch' data-sort='score'><div class='fp'><div class='trk axis'></div><span class='num'>"
            + _pop("SCORE ↕", "Score · % of Claude Opus 5.5", "How close the model gets to Claude Opus 5.5 on the same tasks (Opus = 100%). "
                   "The dot is the score, the line its 95% range: models whose lines overlap are not measurably apart yet. Click to sort.")
            + "</span></div></th><th class='r' data-sort='speed'>" + _pop("TOK/S ↕", "Speed on your box", "Tokens per second while writing the answer, "
            "in a short chat (big number) and with a long document in context (small). Measured on the reference PC, predicted for the box you pick. Click to sort.")
            + "</th><th class='r'>" + _pop("FITS", "Does it fit?", "Whether the model and its context fit in the graphics card plus RAM of the box you pick, "
            "and the largest context that does.") + "</th><th class='l'>" + _pop("STANDS OUT", "Stands out", "Blocks where the model scores at least "
            "6 points above (▲) or below (▼) the typical (median) local model here. Click a row for all nine.") + "</th><th></th></tr>")
    body = []
    for r in local:
        rid, nm = r["id"], model_name(r)
        pl, lo, hi, grp = ranks[rid]
        tip = f"not measurably apart from places {lo}–{hi}" if lo != hi else "measurably apart from every other model"
        col, kind = family((sd["recipes"].get(rid) or {}).get("arch"))[1], _kind(r.get("hf_repo"))
        sub = " · ".join(x for x in (variant(rid, r["file"], full=True), _size(sd["recipes"].get(rid), nm), kind if kind != "release" else "") if x)
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
    return head, body


def _queue_line(q: list[dict]) -> str:
    """What is being measured now and what is next."""
    qline = ""
    run = next((j for j in q if j["status"] == "running"), None)
    nxt = [j["model"] for j in q if j is not run]
    if run or nxt:
        w = 100 * run["done"] / run["total"] if run and run["total"] else 0
        qline = ("<div class='queue'>" + (f"<span class='live'>●</span> Measuring <b>{esc(run['model'])}</b><span class='prog'><i style='width:{w:.0f}%'></i></span>"
                                          + (f"<span class='q'>{run['done']} of {run['total']} tasks</span>" if run["total"] else "<span class='q'>starting</span>") if run else "")
                 + (f"<span class='q nx'>Next: {esc(', '.join(nxt))}</span>" if nxt else "") + "</div>")

    return qline


def _feed(host: str, q: list[dict], local: list[dict], clouds: list[dict], suite_version: str, tier: str) -> list[str]:
    """The latest results comparable with the ranking, newest first (the model being measured on top)."""
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
    return feed


def _chart_data(sd: dict, local: list[dict], clouds: list[dict], ref: dict | None, ranks: dict, names0: dict) -> dict:
    """What the page script draws the chart and the ranking from."""
    # one model in two quants (Tiel Q4 and Q6): the chart says which is which
    dup = {n for n in names0.values() if list(names0.values()).count(n) > 1}
    labels = {rid: n + (f" · {variant(rid, next(r['file'] for r in local if r['id'] == rid))}" if n in dup else "") for rid, n in names0.items()}
    data = dict(sd, families=[[n, c] for _k, n, c in FAMILIES], presets=[w for _, w in PRESETS], refBlocks=(ref or {}).get("blocks") or {},
                points=[{"id": r["id"], "name": labels[r["id"]], "model": names0[r["id"]], "quant": variant(r["id"], r["file"]),
                         "fam": family((sd["recipes"].get(r["id"]) or {}).get("arch"))[0], "kind": _kind(r.get("hf_repo")), "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "blocks": r["blocks"], "t2": r["speed"].get("decode_tps"),
                         "td": float(report._deep(r["speed"])) if report._deep(r["speed"]) != "-" else None, "rank": list(ranks[r["id"]])} for r in local]
                + [{"id": r["id"], "name": model_name(r), "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "blocks": r["blocks"], "t2": None, "td": None,
                    "rank": None, "cloud": True} for r in clouds])
    return data


def _unranked(host: str, local: list[dict]) -> str:
    """Models with answers in this version that do not cover every block yet: named under the ranking, with what is
    missing, instead of being invisible until they qualify."""
    from .. import suite as _s
    ranked = {r["id"] for r in local}
    out = []
    for (rid, where), p in sorted(report.current_pool().items()):
        if where != host or rid in ranked or not p.get("rows"):
            continue
        missing = [SHORT[b] for b in BLOCKS if b in _s.WEIGHTS and b not in p["blocks"]]
        try:   # the model's own name, from its recipe
            from .. import recipe as rc
            m = rc.load(host, rid)["model"]
            nm = model_name({"id": rid, "hf_repo": m.get("hf_repo"), "file": m.get("file")})
        except (OSError, ValueError, KeyError):
            nm = rid
        if missing:
            out.append(f"<b>{esc(nm)}</b> <span class='q'>({len(p['rows'])} answers; not yet: {esc(', '.join(missing))})</span>")
    return (f"<p class='rnote unr'>Measured, not in the ranking until its answers cover every block: {' · '.join(out)}</p>") if out else ""


def home(out_dir: str, host: str = "box", suite_version: str | None = None, tier: str = "quick", rs: list[dict] | None = None,
         sd: dict | None = None) -> str:
    """The home page: the chart, the ranking by use, the settings' worth, the latest results, what's new."""
    from .. import suite as _s
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
    optpanel = _settings_worth(host, local, names0)
    head, body = _ranking(local, clouds, ref, ranks, sd)
    qline = _queue_line(q)
    feed = _feed(host, q, local, clouds, suite_version, tier)
    presets = "".join(f'<button class="{"on" if i == 0 else ""}" data-p="{i}" title="{esc(" · ".join(f"{LABEL[b].lower()} {v}" for b, v in w.items()))}">{esc(n)}</button>' for i, (n, w) in enumerate(PRESETS))
    data = _chart_data(sd, local, clouds, ref, ranks, names0)
    body = f"""
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
  <input id="find" type="search" placeholder="find a model" aria-label="find a model by name, quant or family" autocomplete="off">
  <div class="cmp"><span class="q" id="cmpn">tick two models to compare</span><a class="btn" id="cmpgo" aria-disabled="true">COMPARE</a></div></div>
 <div class="tw"><table class="rank"><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>
 <p class="rnote">Places by score. A dashed line between rows: every model above it is measurably better than the ones below; inside a group the order is not settled yet.
 Click a row for its nine block scores.</p>{_unranked(host, local)}{qline}</section>
<div class="below">{optpanel}<section class="panel feed"><div class="lbl">Latest results</div><ul>{''.join(feed)}</ul></section>
 {_news_panel({r["id"] for r in local})}</div>
"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "index.html")
    with open(path, "w") as f:
        f.write(_page("llmbox · What should I run on my box?", "MODELS", body, ("home.css",), ("plan.js", "home.js"), data))
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


