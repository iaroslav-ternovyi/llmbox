"""`llmbox site`: the public static site, generated from saved results (design "Oscilloscope", llmbox/site_assets/osc.css).

Pages are built only from records: suite results, the job queue (what is being measured now) and speed probes. Nothing
on a page is typed by hand, so the site cannot drift from the data. Structure follows what users of benchmark sites
value: UserBenchmark (ranked tiles, your box among the same hardware), Artificial Analysis (quality x speed with a
Pareto line), LocalScore (time to first token), LMArena (rank ranges when confidence intervals overlap).
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime

from . import report
from . import suite as _suite
from .hosts import HOME

ASSETS = os.path.join(os.path.dirname(__file__), "site_assets")
BLOCKS = ["agentic", "code", "tools", "techhelp", "knowledge", "explain", "longctx", "writing", "reasoning"]
LABEL = {"agentic": "AGENTIC", "code": "CODE", "tools": "TOOLS", "techhelp": "TECH HELP", "knowledge": "KNOWLEDGE",
         "explain": "EXPLAINING", "longctx": "LONG DOCS", "writing": "WRITING", "reasoning": "REASONING"}
# hover text for each score: what it measures, its weight, example tasks (the suite's real task kinds)
TIPS = {
    "agentic": ("Agentic coding", "Work in a real repository: find and fix the bugs the README contradicts, or keep up with a request that changes mid-session. Hidden tests decide.",
                ["Ledger, inventory, shipping: 4-5 hidden bugs each", "Cart: add discount codes, then change the rule"]),
    "code": ("Code", "One function or module from a written spec. Graded only by hidden unit tests.",
             ["Expression evaluator, LRU cache, CSV parser", "Expert: cron schedule across DST changes"]),
    "tools": ("Tools & automation", "Calling business tools correctly on messy data: a CRM, invoices, payments. Wrong or extra calls cost points.",
              ["Payment reminders only to customers really overdue", "Expert: bulk discounts under an approval policy"]),
    "longctx": ("Long documents", "Exact answers from a long incident log and org chart (~80k tokens in the quick test): five questions per task.",
                ["Final root-cause code after later corrections", "Office of the manager of the engineer on an incident"]),
    "writing": ("Writing", "Text with hard constraints a checker can verify: length, required facts, glossary terms, plural rules.",
                ["Meeting minutes with owners and dates", "UI strings in Russian / Ukrainian with ICU plurals"]),
    "reasoning": ("Reasoning", "Multi-step problems with exact answers, several per problem.",
                  ["Order total with tiered discounts and tax", "Trace a function by hand; schedule jobs"]),
    "techhelp": ("Tech help", "Questions about your own machines, answered from configs and logs: 3-5 per machine, every answer computed.",
                 ["Which host ports does docker compose publish?", "Which interface does this packet leave through?"]),
    "knowledge": ("Knowledge & \"I don't know\"", "Exact facts developers look up (Python, shell, error codes); two questions per task ask about things that do not exist.",
                  ["What does this one-liner print?", "Invented flags and functions: saying UNKNOWN scores"]),
    "explain": ("Explaining", "Explain a made-up system in 130-190 words; a fixed reader model must then work out 8 cases from the explanation alone.",
                ["How a CLI decides each setting", "What a month of an API costs"]),
}


def share(b: str) -> float:
    """A block's share of the site's total: the current suite weights over the blocks the ranked suite has."""
    from . import suite
    return suite.WEIGHTS[b] / sum(suite.WEIGHTS[x] for x in BLOCKS)


for _b in TIPS:   # "Agentic coding · 31% of the total": from the code, not typed by hand
    TIPS[_b] = (f"{TIPS[_b][0]} · {share(_b) * 100:.0f}% of the total",) + TIPS[_b][1:]

# ranking presets: block weights (the current suite weights first); the page recomputes the total and the order
PRESETS = [("All work", {b: round(share(b) * 100) for b in BLOCKS}),
           ("Coding", {"agentic": 50, "code": 30, "tools": 20}),
           ("Documents", {"longctx": 50, "writing": 25, "reasoning": 25}),
           ("Writing", {"writing": 70, "longctx": 15, "reasoning": 15})]


def esc(s) -> str:
    return html.escape(str(s))


def _tile(v: float | None, small: str = "", big: bool = False) -> str:
    if v is None:
        return '<span class="tile na">—</span>'
    c = "hi" if v >= 85 else ("mid" if v >= 50 else "lo")
    return f'<span class="tile {c}{" big" if big else ""}">{v:.0f}{"%" if big else ""}{f"<small>{esc(small)}</small>" if small else ""}</span>'


def _tip(block: str, open_: bool = False) -> str:
    title, text, ex = TIPS[block]
    lis = "".join(f"<li>{esc(e)}</li>" for e in ex)
    return f'<span class="tip{" open" if open_ else ""}">{LABEL[block]}<span class="pop"><b>{esc(title)}</b>{esc(text)}<ul>{lis}</ul></span></span>'


def _ago(ts: str) -> str:
    try:
        dt = datetime.strptime(ts[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return ts
    s = (datetime.now() - dt).total_seconds()
    return "just now" if s < 90 else f"{s/60:.0f} min ago" if s < 5400 else f"{s/3600:.0f} h ago" if s < 172800 else f"{s/86400:.0f} d ago"


def _quant(fname: str | None) -> str:
    m = re.search(r"(UD-)?(IQ\d_[A-Z]+|Q\d_[A-Z0-9_]+?|PQ\d_\d|BF16|F16)(?=[-.])", fname or "")
    extra = " · MTP" if fname and "MTP" in fname.upper() else ""
    return (m.group(0) if m else (fname or "")[:24]) + extra


def rank_ranges(rs: list[dict]) -> dict:
    """LMArena-style rank: a model ranks between (1 + models surely better) and (models not surely worse)."""
    out = {}
    for r in rs:
        lo, hi = r["ci"]
        better = sum(1 for o in rs if o is not r and o["ci"][0] > hi)
        not_worse = sum(1 for o in rs if o is not r and o["ci"][1] >= lo)
        out[r["id"]] = (1 + better, 1 + not_worse)
    return out


def pareto(points: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Non-dominated points in (speed, quality), sorted by speed."""
    front = [p for p in points if not any(q[0] >= p[0] and q[1] >= p[1] and (q[0] > p[0] or q[1] > p[1]) for q in points)]
    return sorted(front)


def queue_state() -> list[dict]:
    """Jobs being measured or waiting (running first), with progress from the job log."""
    db = os.path.join(HOME, "queue.db")
    if not os.path.exists(db):
        return []
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    out = []
    for j in c.execute("SELECT * FROM jobs WHERE status IN ('running','queued','interrupted') ORDER BY status='running' DESC, priority DESC, id"):
        done = total = 0
        try:
            m = re.findall(r"\[\s*(\d+)/(\d+)\]", open(j["log"]).read())
            if m:
                done, total = int(m[-1][0]), int(m[-1][1])
        except (OSError, TypeError):
            pass
        out.append({"model": j["model"], "status": j["status"], "done": done, "total": total})
    return out


def _scatter(local: list[dict]) -> str:
    """Quality (% of frontier) x decode speed; the page's JS redraws it for the visitor's box (same drawing as SCATTER_JS)."""
    pts = [{"id": r["id"], "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "t2": r["speed"].get("decode_tps")} for r in local]
    return f'<noscript>{len(pts)} models; enable JavaScript for the chart</noscript>'


# common GPUs: VRAM (MiB) and memory bandwidth (GB/s), for the "your box" picker
GPUS = [("RTX 3060 12 GB", 12288, 360), ("RTX 3090 24 GB", 24576, 936), ("RTX 4060 Ti 16 GB", 16380, 288),
        ("RTX 4070 12 GB", 12282, 504), ("RTX 4070 Ti Super 16 GB", 16376, 672), ("RTX 4080 16 GB", 16376, 717),
        ("RTX 4090 24 GB", 24564, 1008), ("RTX 5060 Ti 16 GB", 16311, 448), ("RTX 5070 12 GB", 12227, 672),
        ("RTX 5070 Ti 16 GB", 16303, 896), ("RTX 5080 16 GB", 16303, 960), ("RTX 5090 32 GB", 32607, 1792)]
RAM_KINDS = [("DDR4-3200", 40), ("DDR5-5600", 60), ("DDR5-6400", 75), ("DDR5-8000", 88)]


def shape_data(local: list[dict], host: str = "box") -> dict:
    """Per recipe: the GGUF shape numbers estimate.plan() uses, plus the calibration measured / predicted on the
    reference box (llmbox.fit, the same numbers `llmbox fit` prints)."""
    from . import fit as F, hosts, recipe as rc
    prof = hosts.load(host)
    ref_hw = hosts.spec(prof)
    out = {}
    for r in local:
        try:
            rec = rc.load(host, r["id"])
        except (OSError, ValueError):
            continue
        sh = F.shape_for(rec, host=hosts.host_of(prof))
        cal = F.calibration(rec, sh, host)
        kv, ctx = rec["placement"]["kv_type"], rec["placement"]["ctx"] or sh.context_length
        out[r["id"]] = {"moe": sh.is_moe, "nonexp": sh.nonexpert_bytes, "exp": sh.expert_bytes, "embed": sh.embed_bytes,
                        "layers": sh.n_layers, "nExp": sh.n_expert, "nUsed": sh.n_expert_used, "rec": sh.recurrent_state_bytes + sh.kv_swa_bytes(kv),
                        "cpuEff": sh.expert_cpu_eff, "kvB": sh.kv_bytes_per_token(kv), "ctx": ctx, "k2": round(cal.k2, 4), "kd": round(cal.kd, 4),
                        "deepK": cal.deep_k, "size": round((sh.total_bytes or 0) / 1e9, 1)}
    return {"recipes": out, "ref": {"gpu": prof["hw"]["gpus"][0]["name"].replace("NVIDIA GeForce ", "") if prof["hw"]["gpus"] else "",
                                    "vram": ref_hw.vram_mib, "ram": ref_hw.ram_mib, "rambw": ref_hw.ram_bw_gbs, "vrambw": ref_hw.vram_bw_gbs},
            "gpus": GPUS, "ramKinds": RAM_KINDS}


def home(out_dir: str, host: str = "box", suite_version: str | None = None, tier: str = "quick") -> str:
    from . import suite as _s
    suite_version = suite_version or _s.VERSION
    rs = report.rows(host, suite_version=suite_version, tier=tier)
    ref = next((r for r in rs if r["host"].get("id") == "cloud"), None)
    local = [r for r in rs if r["host"].get("id") != "cloud" and not r.get("partial")]
    hw = next((r["host"] for r in local), {})
    ref_box = f'{hw.get("gpu", "").replace("NVIDIA GeForce ", "")} · {hw.get("ram_gib", "?")} GB · {hw.get("ram_read_gbs", "?")} GB/s'
    ranks = rank_ranges(local)
    q = [j for j in queue_state() if j["model"] not in {r["id"] for r in local}]

    # the answer first: computed from the data, no editorial text
    def best(key, label, fmt, cid=""):
        c = [r for r in local if key(r) is not None]
        if not c:
            return ""
        b = max(c, key=key)
        return (f'<div{f" id={cid}" if cid else ""}><span class="sc">{label}</span><a class="pk" href="recipe-{esc(b["id"])}.html">{esc(b["id"])}</a>'
                f'<span class="pv">{fmt(b)}</span></div>')
    picks = "".join([
        best(lambda r: r.get("vs_ref"), "Best overall", lambda r: f'{r["vs_ref"]:.0f}% of frontier'),
        best(lambda r: r["speed"].get("decode_tps"), "Fastest on your box", lambda r: f'{r["speed"]["decode_tps"]:.0f} tok/s', "fastest"),
        best(lambda r: r["blocks"].get("agentic"), "Best for agentic coding", lambda r: f'agentic {r["blocks"]["agentic"]:.0f}'),
        best(lambda r: r["blocks"].get("longctx"), "Best for long documents", lambda r: f'long docs {r["blocks"]["longctx"]:.0f}'),
    ])

    head = ("<tr><th>#</th><th class='l'>MODEL</th><th data-sort='score'>SCORE ↕</th>" + "".join(f"<th data-sort='{b}'>{_tip(b)}</th>" for b in BLOCKS)
            + "<th data-sort='speed'>TOK/S ↕<br><span class='faint'>chat · long ctx</span></th><th>FITS</th><th></th></tr>")
    body = []
    for i, r in enumerate(local, 1):
        tps, deep = r["speed"].get("decode_tps"), report._deep(r["speed"])
        lo, hi = ranks[r["id"]]
        body.append(f"<tr data-rid='{esc(r['id'])}'><td class='rk'>{lo if lo == hi else f'{lo}–{hi}'}</td>"
                    f"<td class='l mod'><a class='m' href='recipe-{esc(r['id'])}.html'>{esc(r['id'])}</a><span class='qt'>{esc(_quant(r['file']))}</span></td>"
                    f"<td class='sco'>{_tile(r.get('vs_ref'), f'{r['capability']:.1f}', big=True)}</td>"
                    + "".join(f"<td class='b'>{_tile(r['blocks'].get(b))}</td>" for b in BLOCKS)
                    + f"<td class='spd'>{_tile(tps, f'{deep} long') if tps else '—'}</td><td class='fit'>—</td>"
                    f"<td><label class='pick2' title='pick two to compare'><input type='checkbox' value='{esc(r['id'])}'></label></td></tr>")
    if ref:
        body.append("<tr class='ref'><td class='rk'>ref</td><td class='l mod'><span class='m'>" + esc(ref["id"]) + "</span><span class='qt'>cloud · the 100% mark</span></td>"
                    + "<td class='sco'>100%</td>" + "".join(f"<td class='b'>{ref['blocks'].get(b, 0):.0f}</td>" for b in BLOCKS) + "<td>cloud</td><td></td><td></td></tr>")
    qline = ""
    run = next((j for j in q if j["status"] == "running"), None)
    nxt = [j["model"] for j in q if j is not run]
    if run or nxt:
        w = 100 * run["done"] / run["total"] if run and run["total"] else 0
        qline = ("<div class='queue'>" + (f"<span class='live'>●</span> Measuring <b>{esc(run['model'])}</b><span class='prog'><i style='width:{w:.0f}%'></i></span>"
                                          + (f"<span class='q'>{run['done']} of {run['total']} tasks</span>" if run["total"] else "<span class='q'>starting</span>") if run else "")
                 + (f"<span class='q nx'>Next: {esc(', '.join(nxt))}</span>" if nxt else "") + "</div>")

    feed = []
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
        name = f"<a href='recipe-{esc(rid)}.html'>{esc(rid)}</a>" if rid in {r["id"] for r in local} else f"<b>{esc(rid)}</b>"
        feed.append(f"<li>{name} <span class='fv'>{s.get('capability', 0):.1f}{f' · {tps:.0f} tok/s' if tps else ''}</span>"
                    f"<span class='when'>{'cloud' if cloud else esc((rec.get('host') or {}).get('gpu', '?').replace('NVIDIA GeForce ', ''))} · {_ago(rec.get('created', ''))}</span></li>")
        if len(feed) >= 6:
            break

    presets = "".join(f'<button class="{"on" if i == 0 else ""}" data-p="{i}" title="{esc(" · ".join(f"{LABEL[b].lower()} {v}" for b, v in w.items()))}">{esc(n)}</button>' for i, (n, w) in enumerate(PRESETS))
    data = dict(shape_data(local, host), presets=[w for _, w in PRESETS], refBlocks=(ref or {}).get("blocks") or {},
                points=[{"id": r["id"], "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "blocks": r["blocks"], "t2": r["speed"].get("decode_tps"),
                         "td": float(report._deep(r["speed"])) if report._deep(r["speed"]) != "-" else None, "rank": list(ranks[r["id"]])} for r in local])
    compare_tab = f"compare-{local[0]['id']}-vs-{local[1]['id']}.html" if len(local) > 1 else "#"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>llmbox · What should I run on my box?</title><link rel="stylesheet" href="osc.css"><style>{_HOME_CSS}</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>
<div class="wrap">
<header class="plate"><a class="brand glow" href="index.html">LLMBOX<small>LOCAL LLM BENCHMARK</small></a>
 <nav class="tabs"><a class="on" href="index.html">MODELS</a><a href="new.html">NEW</a><a href="{esc(compare_tab)}">COMPARE</a><a href="method.html">METHOD</a></nav></header>
<h1 class="q1">What should I run on my box?</h1>
<section class="boxbar"><span class="sc">Your box</span>
 <select id="gpu" aria-label="GPU"><option value="">reference box ({esc(ref_box)})</option></select>
 <select id="ram" aria-label="System RAM"><option value="16">16 GB RAM</option><option value="32">32 GB RAM</option><option value="48">48 GB RAM</option><option value="64" selected>64 GB RAM</option><option value="96">96 GB RAM</option><option value="128">128 GB RAM</option><option value="192">192 GB RAM</option></select>
 <select id="bw" aria-label="RAM speed"></select>
 <input id="bwn" placeholder="GB/s" size="5" aria-label="measured RAM read speed, GB/s" title="your measured RAM read speed (llmbox host add)">
 <span id="boxnote" class="q">speeds measured on this box</span></section>
<section class="picks">{picks}</section>
<section class="panel rankp"><div class="lbl">Ranking · suite v{esc(suite_version)} tasks · v{esc(_suite.VERSION.split("-")[0])} weights</div>
 <div class="rhead"><div class="seg" role="group" aria-label="rank by"><span class="sc">Rank by</span>{presets}</div>
  <div class="cmp"><span class="q" id="cmpn">tick two models to compare</span><a class="btn" id="cmpgo" aria-disabled="true">COMPARE</a></div></div>
 <div class="tw"><table class="rank">{head}{''.join(body)}</table></div>{qline}</section>
<div class="below">
 <section class="panel chart"><div class="lbl">Smarter vs faster</div><div id="scatter">{_scatter(local)}</div>
  <p class="legend"><svg width="12" height="16" viewBox="0 0 12 16"><g stroke="#FFB000" stroke-opacity=".6" stroke-width="1.5"><line x1="6" y1="1" x2="6" y2="15"/><line x1="1" y1="1" x2="11" y2="1"/><line x1="1" y1="15" x2="11" y2="15"/></g></svg>
  <span>where the score probably really is (95%). With ~30 tasks one task moves it a few points; when two ranges overlap, the difference is not settled yet. <a href="method.html">More</a></span></p></section>
 <section class="panel feed"><div class="lbl">Latest results</div><ul>{''.join(feed)}</ul></section>
</div>
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


_HOME_CSS = """
.q1{font:600 34px/1.1 "IBM Plex Sans Condensed";margin:26px 0 14px;letter-spacing:.01em}
.boxbar{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:12px 16px;border:1px solid var(--line);background:var(--panel)}
.boxbar .sc{margin-right:4px}
.boxbar select,.boxbar input{background:#0b0c09;color:var(--ink);border:1px solid var(--line);padding:6px 8px;font:13px "IBM Plex Mono"}
.boxbar select:focus,.boxbar input:focus{border-color:var(--amber);outline:none}.boxbar select:disabled,.boxbar input:disabled{opacity:.35}
.boxbar #boxnote{margin-left:auto}
.picks{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid var(--line);border-top:0}
.picks>div{padding:16px 18px;border-right:1px solid var(--line2)}.picks>div:last-child{border-right:0}
.picks .pk{display:block;font:600 22px "IBM Plex Sans Condensed";color:var(--ink);margin:4px 0 0}
.picks .pv{font-size:13px;color:var(--amber)}
.rhead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;padding:18px 16px 6px}
.seg{display:flex;flex-wrap:wrap;align-items:center;gap:4px}.seg .sc{margin-right:8px}
.seg button{background:none;border:1px solid transparent;color:var(--muted);font:12px "IBM Plex Mono";padding:5px 10px;cursor:pointer;white-space:nowrap}
.seg button:hover{color:var(--ink)}.seg button.on{color:var(--amber);border-color:var(--amber-dim)}
.cmp{display:flex;align-items:center;gap:12px}.cmp .btn[aria-disabled=true]{opacity:.4;pointer-events:none}
.rank th{padding:10px 5px}.rank td{padding:10px 5px}.rank th[data-sort]{cursor:pointer;user-select:none}.rank th[data-sort].on .tip{color:var(--amber)}.rank th[data-sort]:hover,.rank th[data-sort].on{color:var(--amber)}
.rank td.rk{color:var(--muted);width:44px;font-size:13px;white-space:nowrap}
.rank td.mod{min-width:170px}.rank td.mod .m{display:block;white-space:nowrap}.rank .qt{display:block;font-size:11px;color:var(--faint)}
.rank .tile{min-width:44px}.rank td.sco .tile{min-width:70px;font-size:20px}
.rank tr.ref td{color:var(--faint);font-size:13px}.rank tr.ref .m{color:var(--muted);font-weight:500}
.rank tr.nofit td{opacity:.45}
.rank td.fit{font-size:12px;color:var(--soft);white-space:nowrap}.rank td.fit .no{color:var(--red)}
.pick2 input{accent-color:#FFB000;width:15px;height:15px;cursor:pointer}
.queue{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;padding:12px 16px;border-top:1px solid var(--line2);font-size:13px}
.queue b{font-weight:500}.queue .nx{margin-left:auto}
.live{color:#ffd27a;animation:blink 1.4s steps(2) infinite}@keyframes blink{50%{opacity:.45}}
@media (prefers-reduced-motion:reduce){.live{animation:none}}
.prog{width:120px;height:4px;background:var(--line);display:inline-block}.prog i{display:block;height:100%;background:var(--amber)}
.below{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:22px}
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
 .picks{grid-template-columns:1fr 1fr}.picks>div:nth-child(2){border-right:0}.picks>div:nth-child(-n+2){border-bottom:1px solid var(--line2)}
 .below{grid-template-columns:1fr}.cmp{width:100%;justify-content:space-between}
}
"""

_JS = r"""
const $ = s => document.querySelector(s);
const fmt = v => v.toFixed(0);
const kfmt = c => `${Math.round(c / 1024)}k`;
function sameClass(hw) { const r = DATA.ref; return hw.gpu === r.gpu && Math.abs(hw.rambw - r.rambw) / r.rambw < 0.15 && hw.ram >= r.ram * 0.9; }
function tile(v, small, pred) { if (v == null) return `<span class="tile">—<small>${small}</small></span>`;   // no reference on this scale: no percent, the rest of the page still draws
  const c = v >= 85 ? "hi" : v >= 50 ? "mid" : "lo";
  return `<span class="tile ${c}${pred ? " pred" : ""}">${pred ? "~" : ""}${fmt(v)}<small>${small}</small></span>`; }
function weighted(b, w) { let s = 0, n = 0; for (const k in w) { s += (b[k] || 0) * w[k]; n += w[k]; } return n ? s / n : 0; }
function scatter(pts) {   // up = smarter, right = faster. Numbered dots + a list: names never drift away from their dot
  const W = 640, H = 300, L = 54, B = 262, T = 24, R = 618;
  const ok = pts.filter(p => p.t2 && p.vs != null).sort((a, b) => b.vs - a.vs);
  if (!ok.length) return "";
  const rng = p => { const k = p.vs / p.cap; return [p.ci[0] * k, Math.min(100, p.ci[1] * k)]; };
  const ymin = Math.max(0, Math.floor((Math.min(...ok.map(p => rng(p)[0])) - 3) / 10) * 10);
  const xs = ok.map(p => p.t2), span = Math.max(10, Math.max(...xs) - Math.min(...xs));
  const step = span > 150 ? 50 : span > 60 ? 20 : span > 25 ? 10 : 5;   // zoom the speed axis onto the models, not onto 0
  const xmin = Math.max(0, Math.floor((Math.min(...xs) - span * 0.25) / step) * step), xmax = Math.ceil((Math.max(...xs) + span * 0.25) / step) * step;
  const X = v => L + (v - xmin) / (xmax - xmin) * (R - L), Y = v => B - (v - ymin) / (100 - ymin) * (B - T);
  let g = "";
  for (let v = ymin; v <= 100; v += 10) g += `<line x1="${L}" y1="${Y(v)}" x2="${R}" y2="${Y(v)}" stroke="#1f201b"/><text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${v}%</text>`;
  for (let v = xmin; v <= xmax; v += step) g += `<line x1="${X(v)}" y1="${T}" x2="${X(v)}" y2="${B}" stroke="#18190f"/><text x="${X(v)}" y="${B + 18}" text-anchor="middle">${v}</text>`;
  g += `<line x1="${L}" y1="${Y(100)}" x2="${R}" y2="${Y(100)}" stroke="#8b877b" stroke-dasharray="5 4"/><text x="${R}" y="${Y(100) - 6}" text-anchor="end" fill="#8b877b">frontier model = 100%</text>`;
  ok.forEach((p, i) => {
    const [lo, hi] = rng(p), x = X(p.t2), y = Y(p.vs);
    g += `<g class="pt" data-id="${p.id}"><g class="ci" stroke="#FFB000" stroke-width="1.5"><line x1="${x}" y1="${Y(hi)}" x2="${x}" y2="${Y(lo)}"/>` +
         `<line x1="${x - 5}" y1="${Y(hi)}" x2="${x + 5}" y2="${Y(hi)}"/><line x1="${x - 5}" y1="${Y(lo)}" x2="${x + 5}" y2="${Y(lo)}"/></g>` +
         `<circle cx="${x}" cy="${y}" r="10" fill="${p.pred ? "#0E0F0C" : "#FFB000"}" stroke="#FFB000" stroke-width="2"/>` +
         `<text x="${x}" y="${y + 4}" text-anchor="middle" font-size="12" font-weight="600" fill="${p.pred ? "#FFB000" : "#0E0F0C"}">${i + 1}</text></g>`;
  });
  const legend = ok.map((p, i) => `<li class="pt" data-id="${p.id}"><b>${i + 1}</b><span class="nm">${p.id}</span><span class="lv">${p.vs.toFixed(0)}%</span><span class="lv">${p.pred ? "~" : ""}${Math.round(p.t2)} tok/s</span></li>`).join("");
  return `<div class="sc2"><svg viewBox="0 0 ${W} ${H}" class="scatter" font-family="IBM Plex Mono" font-size="11" fill="#6c695f" role="img" aria-label="score against speed">${g}` +
         `<text x="${R}" y="${H - 2}" text-anchor="end" fill="#8b877b">faster on your box (tok/s) →</text><text x="${L}" y="12" fill="#8b877b">↑ smarter</text></svg>` +
         `<ol class="lgd2">${legend}</ol></div>`;
}
function hoverScatter() {   // point at a dot or a name: that model and its range light up, the rest step back
  const box = document.querySelector("#scatter");
  box.querySelectorAll(".pt").forEach(el => {
    el.addEventListener("mouseenter", () => { box.classList.add("hov"); box.querySelectorAll(`.pt[data-id="${el.dataset.id}"]`).forEach(x => x.classList.add("on")); });
    el.addEventListener("mouseleave", () => { box.classList.remove("hov"); box.querySelectorAll(".pt.on").forEach(x => x.classList.remove("on")); });
  });
}
let hwNow = null, preset = 0, sortBy = "score", sortDir = 1;   // any column with data-sort; a second click reverses it
document.querySelectorAll(".rank th[data-sort]").forEach(th => th.addEventListener("click", () => {
  sortDir = sortBy === th.dataset.sort ? -sortDir : 1; sortBy = th.dataset.sort;
  document.querySelectorAll(".rank th[data-sort]").forEach(t => t.classList.toggle("on", t === th)); render(); }));
const measured = {};
document.querySelectorAll("tr[data-rid]").forEach(r => measured[r.dataset.rid] = r.querySelector(".spd").innerHTML);
function render() {
  const w = DATA.presets[preset], refW = weighted(DATA.refBlocks, w);
  const pts = DATA.points.map(p => Object.assign({}, p, preset ? { vs: 100 * weighted(p.blocks, w) / refW, cap: weighted(p.blocks, w), ci: [p.ci[0] / p.cap * weighted(p.blocks, w), p.ci[1] / p.cap * weighted(p.blocks, w)] } : {}));
  let fastest = null;
  for (const p of pts) {
    const sh = DATA.recipes[p.id], row = document.querySelector(`tr[data-rid="${p.id}"]`);
    if (!row) continue;
    row.querySelector(".sco").innerHTML = tile(p.vs, p.cap.toFixed(1)).replace("<small>", "%<small>");
    row.classList.remove("nofit");
    if (sh && hwNow && !sameClass(hwNow)) {
      const f = forBox(sh, hwNow); p.t2 = f.t2; p.td = f.td; p.pred = true;
      row.querySelector(".spd").innerHTML = f.fits ? tile(f.t2, `~${fmt(f.td)} long`, true) : "—";
      row.querySelector(".fit").innerHTML = f.fits ? `✓ ${kfmt(f.ctx)} ctx` : `<span class="no">✗ too big</span>`;
      if (!f.fits) { row.classList.add("nofit"); p.t2 = null; }
    } else {
      row.querySelector(".spd").innerHTML = measured[p.id];
      row.querySelector(".fit").innerHTML = sh ? `✓ ${kfmt(sh.ctx)} ctx` : "—";
    }
    if (p.t2 && (!fastest || p.t2 > fastest.t2)) fastest = p;
  }
  const tb = document.querySelector(".rank tbody") || document.querySelector(".rank"), refRow = document.querySelector(".rank tr.ref");
  const key = sortBy === "speed" ? p => p.t2 || 0 : sortBy === "score" ? p => p.vs ?? -1 : p => p.blocks[sortBy] ?? -1;
  const by = (a, b) => sortDir * (key(b) - key(a)) || (b.vs ?? -1) - (a.vs ?? -1);
  const ranked = pts.slice().sort((a, b) => (b.vs ?? -1) - (a.vs ?? -1)).map(p => p.id);
  pts.slice().sort(by).forEach((p) => { const i = ranked.indexOf(p.id); const row = document.querySelector(`tr[data-rid="${p.id}"]`);
    row.querySelector(".rk").textContent = preset ? i + 1 : (p.rank[0] === p.rank[1] ? p.rank[0] : `${p.rank[0]}–${p.rank[1]}`); tb.insertBefore(row, refRow); });
  if (fastest && $("#fastest")) { $("#fastest .pk").textContent = fastest.id; $("#fastest .pk").href = `recipe-${fastest.id}.html`;
    $("#fastest .pv").textContent = `${fastest.pred ? "~" : ""}${fmt(fastest.t2)} tok/s${fastest.pred ? " predicted" : ""}`; }
  $("#scatter").innerHTML = scatter(pts);
  hoverScatter();
}
function readBox() {
  const g = DATA.gpus.find(x => x[0] === $("#gpu").value);
  ["#ram", "#bw", "#bwn"].forEach(s => $(s).disabled = !g);
  if (!g) { hwNow = null; $("#boxnote").textContent = "speeds measured on this box"; try { localStorage.removeItem("llmbox-box"); } catch (e) {} history.replaceState(null, "", location.pathname); render(); return; }
  const bw = parseFloat($("#bwn").value) || parseFloat($("#bw").value);
  hwNow = { gpu: g[0].replace(/ \d+ GB$/, ""), vram: g[1], vrambw: g[2], ram: parseInt($("#ram").value) * 1024, rambw: bw };
  $("#boxnote").textContent = sameClass(hwNow) ? "same class as the reference box: measured speeds" : "speeds predicted for this box (dashed)";
  try { localStorage.setItem("llmbox-box", JSON.stringify({ gpu: $("#gpu").value, ram: $("#ram").value, bw: $("#bw").value, bwn: $("#bwn").value })); } catch (e) {}
  history.replaceState(null, "", `#gpu=${encodeURIComponent(g[0])}&ram=${$("#ram").value}&bw=${bw}`);
  render();
}
for (const g of DATA.gpus) $("#gpu").insertAdjacentHTML("beforeend", `<option>${g[0]}</option>`);
for (const r of DATA.ramKinds) $("#bw").insertAdjacentHTML("beforeend", `<option value="${r[1]}">${r[0]} · ${r[1]} GB/s</option>`);
$("#bw").value = String(DATA.ramKinds.reduce((a, r) => Math.abs(r[1] - DATA.ref.rambw) < Math.abs(a - DATA.ref.rambw) ? r[1] : a, DATA.ramKinds[0][1]));
["#gpu", "#ram", "#bwn"].forEach(s => $(s).addEventListener("change", readBox));
$("#bw").addEventListener("change", () => { $("#bwn").value = ""; readBox(); });   // a preset replaces a typed-in measurement
document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".seg button").forEach(u => u.classList.remove("on")); b.classList.add("on"); preset = +b.dataset.p; render(); }));
const order = DATA.points.slice().sort((a, b) => b.cap - a.cap).map(p => p.id);   // compare pages are named best-first
document.querySelectorAll(".pick2 input").forEach(c => c.addEventListener("change", () => {
  const on = [...document.querySelectorAll(".pick2 input:checked")];
  if (on.length > 2) { on.filter(x => x !== c)[0].checked = false; }
  const ids = [...document.querySelectorAll(".pick2 input:checked")].map(x => x.value).sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const go = $("#cmpgo");
  if (ids.length === 2) { go.href = `compare-${ids[0]}-vs-${ids[1]}.html`; go.setAttribute("aria-disabled", "false"); go.classList.add("solid"); $("#cmpn").textContent = `${ids[0]} vs ${ids[1]}`; }
  else { go.removeAttribute("href"); go.setAttribute("aria-disabled", "true"); go.classList.remove("solid"); $("#cmpn").textContent = ids.length ? `${ids[0]} vs …` : "tick two models to compare"; }
}));
try { const h = Object.fromEntries(new URLSearchParams(location.hash.slice(1))); const saved = h.gpu ? h : JSON.parse(localStorage.getItem("llmbox-box") || "null");
  if (saved && saved.gpu) { $("#gpu").value = saved.gpu; if (saved.ram) $("#ram").value = saved.ram; if (saved.bw) { const o = [...$("#bw").options].find(o => o.value == saved.bw); if (o) $("#bw").value = saved.bw; else $("#bwn").value = saved.bw; } if (saved.bwn) $("#bwn").value = saved.bwn; readBox(); } else { ["#ram", "#bw", "#bwn"].forEach(s => $(s).disabled = true); render(); }
} catch (e) { render(); }
"""


# ---------------------------------------------------------------------------------------------------------------------
# every page: records -> HTML. Flat folder, simple relative links: index, recipe-<id>, run-<id8>, hardware-<id>, compare-<a>-vs-<b>

PLAN_JS = r"""
function plan(sh, hw, ctx, depth, buf = 2100) {   // buf: compute buffer MiB for -ub 2048 / 1024 / 512 = 2100 / 1300 / 900
  const mib = 1 / 1048576, kv = sh.kvB * ctx + sh.rec, gpuFixed = (sh.nonexp + kv) * mib + buf + 700, free = hw.vram - gpuFixed;
  let gf, ramUsed, fits, perCpu, perGpu;
  if (!sh.moe) { const need = gpuFixed + sh.embed * mib; fits = need <= hw.vram; gf = 1; ramUsed = sh.embed * mib; perGpu = sh.nonexp; perCpu = 0; }
  else { gf = Math.max(0, Math.min(1, free / (sh.exp * mib))); const cpuExp = sh.exp * (1 - gf); ramUsed = (cpuExp + sh.embed) * mib;
         fits = free > -1 && ramUsed + 4096 <= hw.ram; const fr = sh.nUsed / sh.nExp; perCpu = cpuExp * fr; perGpu = sh.nonexp + sh.exp * gf * fr; }
  const tps = d => 1 / (perCpu / (hw.rambw * 1e9 * 0.8 * sh.cpuEff) + (perGpu + sh.kvB * d) / (hw.vrambw * 1e9 * 0.75) + sh.layers * 0.025 / 1000);
  return { fits, gf, ramUsed, vram: Math.min(hw.vram, gpuFixed + (sh.moe ? sh.exp * gf * mib : sh.embed * mib)), t2: tps(2000), td: tps(Math.min(depth, ctx)) };
}
function forBox(sh, hw) {   // as llmbox fit: the recipe's context if it fits, else halve it; a smaller prompt batch before a smaller context
  let p = null, ctx = sh.ctx;
  for (let c = sh.ctx; c >= 8192 && !(p && p.fits); c = c / 2)
    for (const buf of [2100, 1300, 900]) { p = plan(sh, hw, c, sh.deepK * 1000, buf); ctx = c; if (p.fits) break; }
  return Object.assign(p, { ctx, t2: p.t2 * sh.k2, td: p.td * sh.kd });
}
function savedBox(DATA) {
  try { const s = JSON.parse(localStorage.getItem("llmbox-box") || "null"); if (!s || !s.gpu) return null;
    const g = DATA.gpus.find(x => x[0] === s.gpu); if (!g) return null;
    return { name: s.gpu, gpu: g[0].replace(/ \d+ GB$/, ""), vram: g[1], vrambw: g[2], ram: parseInt(s.ram) * 1024, rambw: parseFloat(s.bwn) || parseFloat(s.bw) }; } catch (e) { return null; }
}
"""
TAB_LINKS = {"MODELS": "index.html", "NEW": "new.html", "COMPARE": "#", "METHOD": "method.html"}   # build() points COMPARE at the top pair


def _page(title: str, tab: str, body: str, css: str = "", js: str = "", links: dict | None = None) -> str:
    links = dict(TAB_LINKS, **(links or {}))
    nav = "".join(f'<a class="{"on" if t == tab else ""}" href="{esc(links.get(t) or "#")}">{t}</a>' for t in ("MODELS", "NEW", "COMPARE", "METHOD"))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><link rel="stylesheet" href="osc.css"><style>{_PAGES_CSS}{css}</style></head><body>'
            '<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="1.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>'
            '<div class="wrap"><header class="plate"><a class="brand glow" href="index.html">LLMBOX<small>LOCAL LLM BENCHMARK</small></a>'
            f'<nav class="tabs">{nav}</nav></header>{body}'
            f'<footer><span>Every number on this page comes from a saved run record.</span><span>generated {time.strftime("%b %d, %Y %H:%M")}</span></footer>'
            f'</div>{f"<script>{js}</script>" if js else ""}</body></html>')


def load_records(host: str, suite_version: str, tier: str) -> dict:
    """Newest suite record per recipe id for this host and suite, plus the frontier reference. Keeps the file path."""
    import json as _json
    out, ref, runs = {}, None, {}
    for h in (host, "cloud"):
        d = os.path.join(HOME, "results", h)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not f.endswith(".json"):
                continue
            try:
                rec = _json.load(open(os.path.join(d, f)))
            except ValueError:
                continue
            su = rec.get("suite") or {}
            from . import report as _report
            if rec.get("kind") != "suite" or _report.version_of(su) != suite_version or _report.scale(su.get("tier")) != _report.scale(tier) \
                    or su.get("blocks"):
                continue
            from . import bench
            rec = bench.rescore(rec)   # current suite weights: the task scores are the run's, the weighting is today's
            rec["_path"] = os.path.join(d, f)
            rid = (rec.get("recipe") or {}).get("id")
            runs.setdefault((h, rid), []).append(rec)
            if h == "cloud":
                if not ref or rec["summary"]["capability"] > ref["summary"]["capability"]:
                    ref = rec
            elif rid:
                out[rid] = rec   # sorted by name = by time: the newest wins
    for (h, rid), recs in runs.items():   # the number of a model = the IRT estimate over all its runs (report._pool)
        tgt = ref if h == "cloud" and ref is not None and (ref.get("recipe") or {}).get("id") == rid else out.get(rid) if h != "cloud" else None
        if tgt is not None:
            _pool_summary(tgt, recs)
    return {"local": out, "ref": ref}


def _pool_summary(rec: dict, recs: list[dict]) -> None:
    from . import irt
    bank = irt.bank_for((rec.get("suite") or {}).get("content_hash"))
    if bank is None:
        return
    key = lambda r: irt.canonical((r.get("suite") or {}).get("content_hash"))
    same = [r for r in recs if key(r) == key(rec)]
    sc = irt.score_rows(bank, [x for r in same for x in r.get("rows") or []])
    if sc["n"]:
        s = rec["summary"]
        rec["summary"] = dict(s, capability=sc["capability"], capability_ci95=sc["ci95"], blocks=sc["blocks"], runs=len(same),
                              answers=sc["n"], scoring="irt", capability_this_run=s.get("capability"))


def _trace_dir(rec: dict) -> str | None:
    """Traces are saved per queue job (~/.llmbox/traces/job-N); the queue knows which job produced which record."""
    db = os.path.join(HOME, "queue.db")
    if os.path.exists(db):
        row = sqlite3.connect(db).execute("SELECT id FROM jobs WHERE result=?", (rec.get("_path"),)).fetchone()
        if row:
            d = os.path.join(HOME, "traces", f"job-{row[0]}")
            return d if os.path.isdir(d) else None
    return None


def task_flags(rec: dict) -> dict:
    """item id -> {'cut': bool, 'loop': bool, 'max_reply': int} from the rows and the saved thinking."""
    import gzip
    import json as _json
    from . import loopdetect
    td = _trace_dir(rec)
    out = {}
    for r in rec.get("rows", []):
        cut = bool(r.get("reasoning_cut")) or "I have reasoned enough" in (r.get("reasoning_tail") or "")
        loop = False
        if td and os.path.exists(os.path.join(td, r["id"] + ".json.gz")):
            t = _json.load(gzip.open(os.path.join(td, r["id"] + ".json.gz"), "rt"))
            loop = any(loopdetect.scan(x)["fired_at"] for x in t["thinking"])
        out[r["id"]] = {"cut": cut, "loop": loop, "max_reply": max([x.get("predicted_n") or 0 for x in r.get("timings") or []] or [0])}
    return out


def _vs(rec: dict, ref: dict | None) -> float | None:
    return round(100 * rec["summary"]["capability"] / ref["summary"]["capability"], 1) if ref else None


def _verdict(vs: float | None) -> str:
    if vs is None:
        return "—"
    return "excellent" if vs >= 85 else "very good" if vs >= 70 else "good" if vs >= 50 else "weak"


def _spark(vals: list, lo: float, hi: float, w: int = 300, h: int = 44) -> str:
    if not vals or len(vals) < 2:
        return ""
    pts = " ".join(f"{2 + i*(w-4)/(len(vals)-1):.1f},{h-2-(min(max(v, lo), hi)-lo)/(hi-lo)*(h-4):.1f}" for i, v in enumerate(vals))
    return f'<svg viewBox="0 0 {w} {h}" class="spark"><polyline points="{pts}" fill="none" stroke="#FFB000" stroke-width="1.4" filter="url(#g)"/></svg>'


def _telemetry_panel(t: dict | None, title: str = "Hardware during the run") -> str:
    if not t or not t.get("samples"):
        return f'<section class="panel pad"><div class="lbl">{title}</div><span class="q">no telemetry recorded for this run</span></section>'
    cells = []
    spec = [("gpu_temp_c", "GPU temp", "°C avg", 30, 95), ("gpu_power_w", "GPU power", "W avg", 0, 300), ("gpu_util_pct", "GPU util", "% avg", 0, 100),
            ("cpu_temp_c", "CPU temp", "°C avg", 30, 95), ("ram_used_mib", "System RAM", "GB avg", 0, None), ("vram_used_mib", "VRAM", "GB", 0, None)]
    for k, name, unit, lo, hi in spec:
        v = t.get(k)
        if not v:
            continue
        mb = k.endswith("_mib")
        f = (lambda x: x / 1024) if mb else (lambda x: x)
        hi2 = hi or max(v.get("per_min") or [v["max"]]) * 1.2
        cells.append(f'<div><span class="sc">{name}</span><div class="tv">{f(v["avg"]):.{1 if mb else 0}f}<small>{unit} · max {f(v["max"]):.{1 if mb else 0}f}</small></div>'
                     f'{_spark(v.get("per_min") or [], lo, hi2)}</div>')
    return (f'<section class="panel"><div class="lbl">{title}</div>'
            f'<div class="tele" style="grid-template-columns:repeat({len(cells)},1fr)">{"".join(cells)}</div></section>')


def _star(blocks: dict, ref_blocks: dict, lost: dict, W: int = 400) -> str:
    """Six-axis star with the reference outline; each axis label has a hover listing the tasks this recipe lost there."""
    import math
    cx, cy, R = W / 2, 150, 105
    pt = lambda i, v: (cx + R * v / 100 * math.cos(math.radians(-90 + 60 * i)), cy + R * v / 100 * math.sin(math.radians(-90 + 60 * i)))
    ring = lambda f: " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, f) for i in range(6)))
    poly = lambda d: " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, d.get(b) or 0) for i, b in enumerate(BLOCKS)))
    spokes = "".join(f'<line x1="{cx}" y1="{cy}" x2="{pt(i,100)[0]:.1f}" y2="{pt(i,100)[1]:.1f}"/>' for i in range(6))
    dots = "".join(f'<circle cx="{pt(i, blocks.get(b) or 0)[0]:.1f}" cy="{pt(i, blocks.get(b) or 0)[1]:.1f}" r="3.2" fill="{"#ff5a36" if (blocks.get(b) or 0) < 50 else "#FFB000"}"/>' for i, b in enumerate(BLOCKS))
    svg = (f'<svg viewBox="0 0 {W} 300"><g stroke="#2a2c22" fill="none"><polygon points="{ring(100)}"/><polygon points="{ring(50)}"/>'
           f'<polygon points="{ring(75)}" stroke-dasharray="2 4"/>{spokes}</g>'
           + (f'<polygon points="{poly(ref_blocks)}" fill="none" stroke="#E8E4D8" stroke-opacity=".85" stroke-dasharray="4 4" stroke-width="1.2"/>' if ref_blocks else "")
           + f'<polygon points="{poly(blocks)}" fill="rgba(255,176,0,.14)" stroke="#FFB000" stroke-width="2" filter="url(#g)"/>{dots}</svg>')
    labels = []
    for i, b in enumerate(BLOCKS):
        x, y = pt(i, 128)
        v = blocks.get(b)
        title, text, _ = TIPS[b]
        ls = "".join(f"<li>Lost: {esc(n)}</li>" for n in lost.get(b, [])) or "<li>no task lost here</li>"
        left = x / W * 100
        labels.append(f'<div class="ax tip" style="left:calc({left:.1f}% - 40px);top:{y - 14:.0f}px">{LABEL[b]}<b class="{"r" if v is not None and v < 50 else ""}">{"–" if v is None else f"{v:.0f}"}</b>'
                      f'<span class="pop"><b>{esc(title)}</b>{esc(text)}<ul>{ls}</ul></span></div>')
    return f'<div class="starw">{svg}{"".join(labels)}<div class="lgd"><span class="amb">━</span> this recipe &nbsp; <span>┅</span> frontier reference</div></div>'


def _depth_name(k: float) -> str:
    return "Short chat" if k <= 4 else "Long session" if k <= 40 else "Big document"


def _depth_bars(series: list[tuple[str, dict]]) -> str:
    """Decode speed as the context grows: one row per measured depth, one bar per recipe (the first is highlighted).
    Plain labels instead of a log axis: what the user feels is 'a short chat' vs 'a long session' vs 'a big document'."""
    rows = [(rid, sorted((report._depth_k(k), d) for k, d in bd.items() if d.get("decode_tps"))) for rid, bd in series]
    rows = [(rid, ds) for rid, ds in rows if ds]
    if not rows:
        return '<p class="q">no depth probe in this run</p>'
    top = max(d["decode_tps"] for _, ds in rows for _, d in ds) * 1.12
    out = []
    for i, (k, _) in enumerate(rows[0][1]):
        bars, ft = [], []
        for n, (rid, ds) in enumerate(rows):
            kk, d = min(ds, key=lambda x: abs(x[0] - k))
            t, first = d["decode_tps"], ds[0][1]["decode_tps"]
            delta = f'<em>{100 * (t / first - 1):+.0f}%</em>' if i else ""
            fw = kk * 1000 / d["prefill_tps"] if d.get("prefill_tps") else None
            if len(rows) > 1:   # several recipes: name and first-word time ride on each bar
                bars.append(f'<div class="tr{" b" if n else ""}"><i style="width:{100 * t / top:.1f}%"></i><span>{esc(rid)} {t:.0f} tok/s{delta}'
                            f'{f"<em>first word {fw:.1f} s</em>" if fw else ""}</span></div>')
            else:
                bars.append(f'<div class="tr"><i style="width:{100 * t / top:.1f}%"></i><span>{t:.0f} tok/s{delta}</span></div>')
                ft.append(f'<div class="ft"><b>{fw:.1f} s</b>first word</div>' if fw else '<div class="ft">—</div>')
        out.append(f'<div class="k">{_depth_name(k)}<small>{k:.0f}k tokens in context</small></div><div>{"".join(bars)}</div>{"".join(ft)}')
    return f'<div class="bars{" multi" if len(rows) > 1 else ""}">{"".join(out)}</div>'


_KNAMES = {"sampling.temp": "Temperature", "sampling.top_p": "Top-p", "sampling.top_k": "Top-k", "sampling.min_p": "Min-p",
           "sampling.presence_penalty": "Presence penalty", "sampling.repeat_penalty": "Repeat penalty",
           "chat.template_kwargs.enable_thinking": "Thinking", "chat.template_kwargs.preserve_thinking": "Keep thinking between turns",
           "chat.template_kwargs.reasoning_effort": "Reasoning effort", "placement.kv_type": "KV cache", "placement.ctx": "Context",
           "antiloop.reasoning_budget": "Reasoning reserve", "antiloop.marker_bias": "Loop-marker bias", "speculative.type": "Speculative decoding",
           "speculative.draft_max": "Draft tokens"}


def _human(v) -> str:
    return "default" if v is None else "on" if v is True else "off" if v is False else str(v)


def recipe_page(rid: str, rec: dict, ref: dict | None, others: dict, ranks: dict, flags: dict, n_measured: int, n_total: int) -> str:
    s, sp = rec["summary"], rec["summary"]["speed"]
    bd = sp.get("by_depth") or {}
    vs = _vs(rec, ref)
    depths = sorted((report._depth_k(k), d) for k, d in bd.items())
    deep = depths[-1] if depths else None
    d_pct = 100 * (deep[1]["decode_tps"] / depths[0][1]["decode_tps"] - 1) if len(depths) > 1 else None
    k28 = next(((k, d) for k, d in depths if k >= 24), None)
    ttft2 = 2000 / depths[0][1]["prefill_tps"] if depths and depths[0][1].get("prefill_tps") else None
    rows = rec.get("rows", [])
    n_cut = sum(1 for f in flags.values() if f["cut"])
    n_cut_ok = sum(1 for r in rows if flags.get(r["id"], {}).get("cut") and r["score"] >= 0.99)
    n_loop = sum(1 for f in flags.values() if f["loop"])
    lost, why = {}, {}
    for r in rows:
        if r["score"] < 0.99:
            f = flags.get(r["id"], {})
            w = " — thinking looped" if f.get("loop") else " — ran out of thinking room" if f.get("cut") else ""
            lost.setdefault(r["block"], []).append(f'{r["kind"].replace("_", " ")} {r["id"].rsplit(".", 2)[-2]} ({r["score"]*100:.0f}){w}')
            why.setdefault(r["block"], []).append("looped" if f.get("loop") else "out of thinking room" if f.get("cut") else "wrong answer")
    lo, hi = ranks.get(rid, (1, 1))
    rk = f"{lo}" if lo == hi else f"{lo}–{hi}"
    m = rec.get("model") or {}
    rcp = rec.get("recipe") or {}
    lines = [
        ("Capability", f'{s["capability"]:.1f}', f'95% range {s["capability_ci95"][0]:.0f}–{s["capability_ci95"][1]:.0f}', _verdict(vs), vs is not None and vs >= 50),
        ("Speed", f'{sp.get("decode_tps"):.0f} tok/s' if sp.get("decode_tps") else "—", "short chat, reference box", sp.get("label") or "", True),
        ("Long context", f"{d_pct:+.0f}%" if d_pct is not None else "—", f'{deep[1]["decode_tps"]:.0f} tok/s with {deep[0]:.0f}k in context' if deep else "", "steady" if (d_pct or 0) > -15 else "slows down", (d_pct or 0) > -15),
        ("First word", f"{ttft2:.1f} s" if ttft2 else "—", f"{k28[0] * 1000 / k28[1]['prefill_tps']:.0f} s with {k28[0]:.0f}k in context" if k28 and k28[1].get("prefill_tps") else "", "quick" if (ttft2 or 9) < 2 else "slow", (ttft2 or 9) < 2),
        ("Reliability", f"{n_cut + n_loop} of {len(rows)} flagged", f"{n_cut} ran out of thinking room ({n_cut_ok} still solved) · {n_loop} looped", "clean" if n_cut + n_loop == 0 else "thinking issues", n_cut + n_loop == 0),
        ("Efficiency", f'{s["solved_per_hour"]:.1f} solved/h', f'{s["solved"]:.0f} of {s["items"]} tasks in {s["wall_minutes"] / 60:.1f} h', "", True),
    ]
    line_html = "".join(f'<div class="line"><span class="k">{k}</span><em class="{"" if ok else "r"}">{esc(v)}</em><span class="s">{esc(sub)}</span>'
                        f'{f"<span class=\"v {'ok' if ok else 'bad'}\">{esc(vd)}</span>" if vd else "<span></span>"}</div>' for k, v, sub, vd, ok in lines)
    strong = sorted(((v, b) for b, v in s["blocks"].items() if v >= 85), reverse=True)[:3]
    weak = sorted((v, b) for b, v in s["blocks"].items() if v < 85)[:3]
    def reasons(b):
        c = {w: why.get(b, []).count(w) for w in dict.fromkeys(why.get(b, []))}
        n = sum(1 for r in rows if r["block"] == b)
        return f'lost {len(why.get(b, []))} of {n}' + (f' ({", ".join(f"{v} {k}" for k, v in c.items())})' if c else "")
    sw = ('<div class="sw"><div><span class="sc">Strong</span>' + (" · ".join(f'<b>{TIPS[b][0].split(" ·")[0]} {v:.0f}</b>' for v, b in strong) or "<span class='q'>nothing above 85 yet</span>")
          + '</div><div class="w"><span class="sc">Weak</span>'
          + ("".join(f'<div><b>{TIPS[b][0].split(" ·")[0]} {v:.0f}</b> <span class="q">{esc(reasons(b))}</span></div>' for v, b in weak) or "<span class='q'>nothing below 85</span>") + "</div></div>")
    rt = rec.get("runtime") or {}
    notes = (rcp.get("notes") or {}).get("lines", [])
    sam = rcp.get("sampling") or {}
    al = rcp.get("antiloop") or {}
    pl = rcp.get("placement") or {}
    spc = rcp.get("speculative") or {}
    chat = (rcp.get("chat") or {}).get("template_kwargs") or {}
    portable = [("Sampling", " · ".join(f"{k.replace('_', '-')} {v}" for k, v in sam.items() if k != "max_tokens") or "server defaults"),
                ("Template", " · ".join(f"{k.replace('_', ' ')} {_human(v)}" for k, v in chat.items()) or "model default"),
                ("Reasoning", (f'{al.get("reasoning_budget")} token reserve' if (al.get("reasoning_budget") or -1) >= 0 else "no limit") + (f' · loop detector {al["reasoning_loop"]}' if al.get("reasoning_loop") else "")),
                ("Anti-loop", (f'marker bias −{al.get("marker_bias")} on {len(al.get("marker_ids") or [])} tokens' if al.get("marker_ids") else "none") + (f' · DRY in thinking, allowed {al.get("dry_allowed_length")}' if al.get("dry_think_only") else "")),
                ("KV cache", pl.get("kv_type", "?")),
                ("Speculative", f'{spc.get("type")} · draft {spc.get("draft_max")}' if spc.get("type") else "off")]
    hwrows = [("Context", f'{round((pl.get("ctx") or 0) / 1024)}k'), ("Batch", f'{pl.get("batch")} / ubatch {pl.get("ubatch")}'), ("Threads", f'{(rcp.get("runtime") or {}).get("threads")} on cores {(rcp.get("runtime") or {}).get("cpu_affinity") or "any"}')]
    recipe_html = ('<div class="rgrid"><div><div class="sc">Same on every box · these set the score</div><dl>'
                   + "".join(f"<dt>{k}</dt><dd class='val'>{esc(v)}</dd>" for k, v in portable)
                   + '</dl></div><div><div class="sc">Fitted to each box · speed only</div><dl>'
                   + "".join(f"<dt>{k}</dt><dd>{esc(v)}</dd>" for k, v in hwrows)
                   + '</dl><p class="q" style="margin-top:10px">values of the reference box</p></div></div>'
                   + (f'<div class="notes"><div class="sc">Why these settings</div><ul>{"".join(f"<li>{esc(n)}</li>" for n in notes)}</ul></div>' if notes else ""))
    runs_html = (f'<div class="tw"><table><tr><th class="l">RUN</th><th class="l">BOX</th><th class="l">SETTINGS</th><th>SCORE</th><th>TOK/S</th><th></th></tr>'
                 f'<tr><td class="l"><a href="run-{rec["id"][:8]}.html">{rec["id"][:8]}</a><br><span class="q">{_ago(rec.get("created", ""))}</span></td>'
                 f'<td class="l cfg">{esc((rec["host"].get("gpu") or "").replace("NVIDIA GeForce ", ""))} · {rec["host"].get("ram_gib")} GB · {rec["host"].get("ram_read_gbs")} GB/s<br><span class="q">llama.cpp {esc(rt.get("llama_cpp_build") or "?")}</span></td>'
                 f'<td class="l cfg">{esc(rt.get("variant") or "not recorded")}</td>'
                 f'<td>{_tile(vs, f"{s["capability"]:.1f}")}</td><td>{_tile(sp.get("decode_tps"))}</td><td><a class="btn" href="run-{rec["id"][:8]}.html">OPEN</a></td></tr></table></div>')
    rival = next((o for o in sorted(others, key=lambda k: -others[k]["summary"]["capability"]) if o != rid), None)
    cmp_href = "" if not rival else (f"compare-{rid}-vs-{rival}.html" if others[rid]["summary"]["capability"] >= others[rival]["summary"]["capability"] else f"compare-{rival}-vs-{rid}.html")
    body = f'''
<section class="panel title"><div><div class="crumb"><a href="index.html">Models</a> / {esc(rid)}</div><h1>{esc(rid)} <span class="muted" style="font-weight:500">· {esc(_quant(m.get("file")))}</span></h1>
 <div class="meta">{esc(m.get("hf_repo") or "")}{f' · {m["bytes"]/1e9:.1f} GB' if m.get("bytes") else ""} · suite v{esc(rec["suite"]["version"])}</div></div>
 <div class="acts"><a class="btn" href="hardware-{esc(rid)}.html">SPEED ON OTHER BOXES →</a>{f'<a class="btn" href="{esc(cmp_href)}">COMPARE WITH {esc(rival.upper())}</a>' if rival else ""}</div></section>
<section class="panel"><div class="lbl">Score</div><div class="top">
 <div class="score"><div class="n glow">{f"{vs:.0f}" if vs is not None else "—"}<small>%</small></div><div class="of">of frontier</div><div class="rk">rank {rk} of {n_measured}<br><span class="q">{s.get("runs", 1)} run{"s" if s.get("runs", 1) > 1 else ""}</span></div><a class="q" href="method.html" style="display:block;margin-top:14px">how scores work</a></div>
 <div class="lines">{line_html}</div>
 <div>{_star(s["blocks"], (ref or {}).get("summary", {}).get("blocks") or {}, lost)}</div></div>{sw}</section>
<section class="panel pad"><div class="lbl">Speed as the context grows</div>{_depth_bars([(rid, bd)])}
 <p class="q" style="margin-top:16px">Measured on the reference box ({esc((rec["host"].get("gpu") or "").replace("NVIDIA GeForce ", ""))} · {rec["host"].get("ram_gib")} GB · {rec["host"].get("ram_read_gbs")} GB/s). <a href="hardware-{esc(rid)}.html">Other boxes →</a></p></section>
<section class="panel recipe"><div class="lbl">Settings</div>{recipe_html}</section>
{_telemetry_panel(rec.get("telemetry"))}
<section class="panel runs"><div class="lbl">Runs</div>{runs_html}</section>'''
    return _page(f"llmbox · {rid}", "MODELS", body, _RECIPE_CSS)


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
            lvl = r["id"].rsplit(".", 2)[-2]
            body_rows.append(f'<tr><td class="l"><span class="m2">{esc(r["kind"].replace("_", " "))}</span> <span class="q">{lvl}</span>{" <span class=flag>EXPERT</span>" if lvl == "L6" else ""}</td>'
                             f'<td>{_tile(r["score"] * 100)}</td><td>{r["seconds"]:.0f} s</td><td>{r.get("steps") or 1}</td><td>{f.get("max_reply", 0):,}</td><td class="l">{fl or "<span class=q>—</span>"}</td></tr>')
    argv = rt.get("argv") or []
    diff = rt.get("diff_vs_recipe")
    diff_html = ("<span class='v ok'>identical to the recipe</span>" if diff == [] else
                 "".join(f"<div><span class='flag {'lo' if d['class'] == 'quality' else ''}'>{d['class'].upper()}</span> {esc(d['flag'])}: recipe {esc(' '.join(d['recipe']) or '—')} → run {esc(' '.join(d['run']) or '—')}</div>" for d in diff) if diff
                 else "<span class='q'>not recorded for this run</span>")
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / <a href="recipe-{esc(rid)}.html">{esc(rid)}</a> / run {rec["id"][:8]}</div><h1><a href="recipe-{esc(rid)}.html">{esc(rid)}</a> on {esc((h.get("gpu") or "").replace("NVIDIA GeForce ", ""))} · {h.get("ram_gib")} GB</h1>
 <div class="id">{esc(rec.get("created", "")[:16].replace("T", " "))} · suite v{esc(rec["suite"]["version"])} · {s["wall_minutes"] / 60:.1f} h</div></div>
 <div class="acts">{'<button class="btn" id="copy">COPY SETTINGS</button>' if argv else ""}</div></section>
<section class="panel sum">
 <div><b>{f"{vs:.0f}%" if vs is not None else "—"}</b><span>of frontier · {s["capability"]:.1f} ({s["capability_ci95"][0]:.0f}–{s["capability_ci95"][1]:.0f})</span></div>
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
    js = 'const c=document.getElementById("copy");if(c)c.onclick=()=>{navigator.clipboard.writeText(document.getElementById("argv").innerText.trim().replace(/\\s*\\n\\s*/g," ")).then(()=>{c.textContent="COPIED";setTimeout(()=>c.textContent="COPY SETTINGS",1500)})};'
    return _page(f"llmbox · run {rec['id'][:8]} · {rid}", "MODELS", body, _RUN_CSS, js)


def hardware_page(rid: str, rec: dict, shape: dict, data: dict) -> str:
    """Measured boxes for this recipe + predictions for common boxes; the upgrade advisor is computed in the page for
    the visitor's box (saved by the picker on the home page), else for the reference box."""
    import json as _json
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / <a href="recipe-{esc(rid)}.html">{esc(rid)}</a> / other boxes</div><h1>How fast is <a href="recipe-{esc(rid)}.html">{esc(rid)}</a> on other boxes?</h1>
 <p class="q" style="margin-top:6px">The score is the same on every box; only speed changes. One box is measured, the rest are predicted from the model file and each box's memory speed.</p></div></section>
<section class="panel"><div class="lbl" id="advbox">What would make the reference box faster</div><div class="adv" id="adv"></div>
 <div class="why">This model keeps most of its experts in system RAM on small cards, so RAM speed, not the GPU, sets the pace. More VRAM moves experts onto the GPU.</div></section>
<section class="panel"><div class="lbl" id="boxlbl">Boxes</div><div class="tw"><table id="boxes"></table></div></section>'''
    js = PLAN_JS + f"\nconst DATA = {_json.dumps(dict(data, sh=shape, measured={'gpu': data['ref']['gpu'], 'ram': data['ref']['ram'], 'rambw': data['ref']['rambw'], 't2': rec['summary']['speed'].get('decode_tps'), 'td': float(report._deep(rec['summary']['speed'])) if report._deep(rec['summary']['speed']) != '-' else None}))};\n" + _HW_JS
    return _page(f"llmbox · {rid} on other boxes", "MODELS", body, _HW_CSS, js)


def compare_page(a: str, b: str, ra: dict, rb: dict, ref: dict | None, fa: dict, fb: dict) -> str:
    sa, sb = ra["summary"], rb["summary"]
    va, vb = _vs(ra, ref), _vs(rb, ref)
    rows = []
    wins = {a: 0, b: 0}
    def row(name, x, y, fmt, higher=True, tip=""):
        w = None if x is None or y is None or fmt(x) == fmt(y) else (a if (x > y) == higher else b)
        if w:
            wins[w] += 1
        cell = lambda v, me: f'<td class="{"win" if w == me else ""}">{fmt(v) if v is not None else "—"}{" ◀" if w == me and me == a else ""}{" ▶" if w == me and me == b else ""}</td>'
        rows.append(f'<tr><td class="l">{tip or name}</td>{cell(x, a)}{cell(y, b)}</tr>')
    row("Of frontier", va, vb, lambda v: f"{v:.0f}%")
    for bl in BLOCKS:
        row(LABEL[bl], sa["blocks"].get(bl), sb["blocks"].get(bl), lambda v: f"{v:.0f}", tip=_tip(bl))
    da, db = sa["speed"].get("by_depth") or {}, sb["speed"].get("by_depth") or {}
    row("Speed, short chat (tok/s)", sa["speed"].get("decode_tps"), sb["speed"].get("decode_tps"), lambda v: f"{v:.1f}")
    dpa, dpb = report._deep(sa["speed"]), report._deep(sb["speed"])
    row("Speed, long context (tok/s)", float(dpa) if dpa != "-" else None, float(dpb) if dpb != "-" else None, lambda v: f"{v:.1f}")
    row("Solved tasks / hour", sa.get("solved_per_hour"), sb.get("solved_per_hour"), lambda v: f"{v:.1f}")
    ta, tb = (ra.get("telemetry") or {}), (rb.get("telemetry") or {})
    g = lambda t, k: (t.get(k) or {}).get("avg")
    row("VRAM used (GB)", (g(ta, "vram_used_mib") or 0) / 1024 or None, (g(tb, "vram_used_mib") or 0) / 1024 or None, lambda v: f"{v:.1f}", higher=False)
    row("GPU power avg (W)", g(ta, "gpu_power_w"), g(tb, "gpu_power_w"), lambda v: f"{v:.0f}", higher=False)
    row("Replies flagged (thinking)", sum(f["cut"] + f["loop"] for f in fa.values()), sum(f["cut"] + f["loop"] for f in fb.values()), lambda v: f"{v}", higher=False)
    # settings that differ between the two recipes
    import json as _json
    flat = lambda d, p="": {k2: v2 for k, v in (d or {}).items() for k2, v2 in (flat(v, f"{p}{k}.") if isinstance(v, dict) else {f"{p}{k}": v}).items()}
    fa_, fb_ = flat({k: (ra.get("recipe") or {}).get(k) for k in ("sampling", "chat", "antiloop", "placement", "speculative")}), flat({k: (rb.get("recipe") or {}).get(k) for k in ("sampling", "chat", "antiloop", "placement", "speculative")})
    diffs = [(k, fa_.get(k), fb_.get(k)) for k in sorted(set(fa_) | set(fb_)) if fa_.get(k) != fb_.get(k) and not k.endswith("marker_ids")]
    only = lambda rows_, other: [r["kind"].replace("_", " ") + " " + r["id"].rsplit(".", 2)[-2] for r in rows_ if r["score"] < 0.99 and next((o["score"] for o in other if o["id"] == r["id"]), 0) >= 0.99]
    la, lb = only(ra.get("rows", []), rb.get("rows", [])), only(rb.get("rows", []), ra.get("rows", []))
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / compare</div><h1><a href="recipe-{esc(a)}.html">{esc(a)}</a> <span class="muted">vs</span> <a href="recipe-{esc(b)}.html">{esc(b)}</a></h1>
 <p class="q" style="margin-top:6px">Same {len(ra.get("rows", []))} tasks on the same box. The score ranges overlap ({sa["capability_ci95"][0]:.0f}–{sa["capability_ci95"][1]:.0f} vs {sb["capability_ci95"][0]:.0f}–{sb["capability_ci95"][1]:.0f}), so the overall difference is not settled yet.</p></div>
 <div class="score2"><div><b>{wins[a]}</b><span>{esc(a)}</span></div><div class="vs">:</div><div><b>{wins[b]}</b><span>{esc(b)}</span></div></div></section>
<div class="two2"><section class="panel"><div class="lbl">Head to head</div><table class="h2h"><tr><th class="l"></th><th>{esc(a)}</th><th>{esc(b)}</th></tr>{"".join(rows)}</table></section>
<div><section class="panel pad"><div class="lbl">Speed as the context grows</div>{_depth_bars([(a, da), (b, db)])}</section>
<section class="panel pad"><div class="lbl">Tasks only one of them solved</div><div class="only"><div><div class="sc">{esc(a)} solved, {esc(b)} did not</div><ul>{"".join(f"<li>{esc(x)}</li>" for x in lb) or "<li class=q>none</li>"}</ul></div>
 <div><div class="sc">{esc(b)} solved, {esc(a)} did not</div><ul>{"".join(f"<li>{esc(x)}</li>" for x in la) or "<li class=q>none</li>"}</ul></div></div></section></div></div>
<section class="panel"><div class="lbl">Settings that differ</div><div class="tw"><table><tr><th class="l">SETTING</th><th class="l">{esc(a)}</th><th class="l">{esc(b)}</th></tr>
 {"".join(f"<tr><td class='l'>{esc(_KNAMES.get(k, k.split('.')[-1].replace('_', ' ')))}</td><td class='l amb'>{esc(_human(x))}</td><td class='l amb'>{esc(_human(y))}</td></tr>" for k, x, y in diffs)}</table></div></section>'''
    return _page(f"llmbox · {a} vs {b}", "COMPARE", body, _CMP_CSS, links={"COMPARE": f"compare-{a}-vs-{b}.html"})


_GRADING = {   # how each block is graded (from the suite modules' own descriptions)
    "agentic": "A sandboxed copy of a real multi-module project with injected bugs or a missing feature; the model works with bash / read / edit tools, "
               "and in {sessions} of {agentic} tasks the requirements change turn by turn, the way people talk to a coding assistant. "
               "Graded by hidden tests copied in only at grading time: doing nothing scores 0.",
    "code": "One function or module from a written spec, in Python or JavaScript. Hidden unit tests from a reference implementation decide.",
    "tools": "Function calls against a simulated CRM with fresh customers and invoices. Graded on the final state of that world: "
             "a wrong or extra email, discount or payment costs points.",
    "techhelp": "Generated configs and logs of a machine (docker compose, nginx, routing tables, chmod chains, journal logs) with 3-5 "
                "questions each; every answer is computed from the config (chmod checked against GNU chmod). Credit per question.",
    "knowledge": "8 short questions per task on Python, shell and error codes, answers taken from running the real thing; two per task "
                 "ask about something that does not exist. Right 1, UNKNOWN 1/3 (made-up: 1), invented 0.",
    "explain": "The model explains a made-up system within a word limit; a fixed reader model (gpt-oss-20b, greedy) then works out 8 "
               "cases from the explanation alone. The score is the share it gets right.",
    "longctx": "Five questions per task on a long incident log (~80k tokens in the quick test), with later corrections that override "
               "earlier facts. Exact answers, credit per question.",
    "writing": "Minutes, rewrites, proofreading, UI strings with plural rules. Every constraint (length, facts, glossary terms) is checked by a program; "
               "the score is the share of constraints met.",
    "reasoning": "Multi-step problems with exact answers, several per problem (subtotal, tax, total; every printed line of a traced "
                 "program). Credit per answer.",
}


def new_page(rs: list[dict], data: dict, host: str) -> str | None:
    """Models on Hugging Face nobody has measured here: speed predicted for the visitor's box, the expected score from
    measured relatives (same architecture and size), and the four commands that measure one."""
    from . import candidates as C, estimate as E, fit as F, recipe as rc
    cs = C.load()
    if not cs:
        return None
    measured = {}
    in_campaign = set()
    for rid in rc.ids(host):
        try:
            r = rc.load(host, rid)
            in_campaign.add(r["model"].get("hf_repo"))
        except (OSError, ValueError):
            continue
    for r in rs:
        try:
            measured[r["id"]] = (F.shape_for(rc.load(host, r["id"])), r.get("vs_ref"))
        except (OSError, ValueError, SystemExit):
            continue
    from . import eci
    table = eci.load()
    ref_cap = next((r["capability"] / (r["vs_ref"] / 100) for r in rs if r.get("vs_ref")), None)
    by_base: dict = {}
    chains: dict = {}
    for r in rs:
        try:
            repo = rc.load(host, r["id"])["model"].get("hf_repo") or ""
        except (OSError, ValueError):
            continue
        chains[r["id"]] = C.base_chain(repo)
        # an anchor must BE the scored model (a quantization of it): a fine-tune of a base is a different model
        hit = eci.match(repo, table) if not eci.is_remix(repo) else None
        if hit:
            by_base.setdefault(hit[0], (hit[1]["eci"], []))[1].append((r["id"], r["capability"]))
    anchors = ([(f"{eci.FRONTIER_PROXY} (stands in for the reference)", table[eci.FRONTIER_PROXY]["eci"], ref_cap)]
               if ref_cap and eci.FRONTIER_PROXY in table else [])
    anchors += [(f"{name} via {', '.join(i for i, _ in ms)}", e, sum(c for _, c in ms) / len(ms)) for name, (e, ms) in by_base.items()]
    pred = eci.predictor(table, anchors, ref_cap) if ref_cap else None
    rows = []
    for c in cs:
        if c["repo"] in in_campaign:
            continue
        sh = E.ModelShape(**c["shape"])
        own = C.base_chain(c["repo"])
        roots = set(own[:2]) | {re.sub(r"-gguf$", "", c["repo"], flags=re.I)}   # the repo and the model it packages
        # measured fine-tunes of this model (declared on Hugging Face): context, not a prediction - they are other models
        rel = [(rid, (r.get("vs_ref") or 0)) for r in rs for rid in [r["id"]] if rid in chains and roots & set(chains[rid][1:])]
        guess = None
        hit = eci.match(c["repo"], table) if pred else None
        if hit:
            mid, lo, hi = pred(hit[1]["eci"], hit[1]["lo"], hit[1]["hi"])
            guess = {"mid": round(mid), "lo": round(lo), "hi": round(hi), "name": hit[0], "eci": hit[1]["eci"], "remix": eci.is_remix(c["repo"])}
        kv = "q8_0"
        rows.append({"repo": c["repo"], "rid": C.recipe_id(c["repo"]), "quant": c["quant"], "gb": round(c["bytes"] / 1e9, 1),
                     "dl": c["downloads"], "total": round(sh.total_params / 1e9, 1), "active": round(sh.active_params / 1e9, 1),
                     "arch": sh.arch, "mtp": bool(sh.n_mtp_layers), "rel": rel, "guess": guess,
                     "sh": {"moe": sh.is_moe, "nonexp": sh.nonexpert_bytes, "exp": sh.expert_bytes, "embed": sh.embed_bytes,
                            "layers": sh.n_layers, "nExp": sh.n_expert, "nUsed": sh.n_expert_used,
                            "rec": sh.recurrent_state_bytes + sh.kv_swa_bytes(kv), "cpuEff": sh.expert_cpu_eff,
                            "kvB": sh.kv_bytes_per_token(kv), "ctx": sh.context_length or 32768, "k2": 1, "kd": 1, "deepK": 32}})
    body = f"""
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / not tested yet</div><h1>Not tested yet</h1>
 <p class="q" style="margin-top:6px">Popular models on Hugging Face that have no run here. Speed is predicted for <b id="boxname">the reference box</b>
 from each model's file (<a href="index.html">pick your box</a>); the expected score comes only from measured models with the same
 architecture and size. Measure one and it moves into the ranking.</p></div></section>
<section class="panel"><div class="lbl">{len(rows)} models · most downloaded and trending GGUF · click a column to sort</div>
 <div class="tw"><table class="cand"><tr><th class="l">MODEL</th><th data-sort="size">SIZE ↕<br><span class="faint">total · active</span></th><th data-sort="exp">EXPECTED SCORE ↕</th>
 <th data-sort="speed">TOK/S ON YOUR BOX ↕<br><span class="faint">predicted</span></th><th>FILE</th><th data-sort="dl">DOWNLOADS ↕<br><span class="faint">30 days</span></th><th></th></tr>
 <tbody id="rows"></tbody></table></div></section>"""
    note = ("<section class='panel pad'><div class='lbl'>Where the expected score comes from</div><p class='q' style='max-width:900px;line-height:1.7'>"
            "Measured relatives first: models with the same architecture and size measured here. Otherwise the model's "
            "<a href='https://epoch.ai/benchmarks'>Epoch Capabilities Index</a> (one number fitted over many public benchmarks), put on our scale by a "
            "straight line through models that have both: " + "; ".join(f"{esc(n)}: ECI {e:.0f} = {c / ref_cap * 100:.0f}%" for n, e, c in anchors)
            + ". Two or three anchors make it rough, hence the wide range; every model measured here adds one. A remix (abliterated, merged, renamed) "
            "gets its base model's range. ECI data: Epoch AI, 'Capabilities &amp; benchmarking', epoch.ai/benchmarks, CC BY 4.0.</p></section>") if pred else (
        "<section class='panel pad'><div class='lbl'>Where the expected score will come from</div><p class='q' style='max-width:900px;line-height:1.7'>"
        "From the <a href='https://epoch.ai/benchmarks'>Epoch Capabilities Index</a>, put on our scale by models measured here that are themselves in the "
        "index. A fine-tune does not count: it is a different model. The frontier reference is the only such model so far; the first base models are in "
        "the queue, and the predictions appear when they finish. Fine-tunes of a model that were measured here are listed with it, as context. "
        "ECI data: Epoch AI, 'Capabilities &amp; benchmarking', epoch.ai/benchmarks, CC BY 4.0.</p></section>")
    body += note
    js = PLAN_JS + "\nconst DATA = " + json.dumps(dict(ref=data["ref"], gpus=data["gpus"], ramKinds=data["ramKinds"], rows=rows, eciReady=bool(pred))) + ";\n" + _NEW_JS
    return _page("llmbox · not tested yet", "NEW", body, _NEW_CSS, js)


_NEW_CSS = """
.cand td{padding:10px 8px}.cand td.l .m{display:block}.cand .repo{font-size:11px;color:var(--faint)}
.cand tr.nofit td{opacity:.45}.cand .rel{display:block;font-size:10.5px;color:var(--muted)}
.cand .go{font-size:11px;padding:5px 10px;white-space:nowrap}
th[data-sort]{cursor:pointer;user-select:none}th[data-sort]:hover,th[data-sort].on{color:var(--amber)}
.cand .guess{color:var(--soft)}
.cmds{background:#0b0c09;border:1px solid var(--line);padding:12px 14px;margin:4px 0 8px;text-align:left;font-size:12.5px;line-height:1.8;color:var(--soft)}
.cmds code{display:block;color:var(--amber)}.cmds code:before{content:"$ ";color:var(--faint)}
"""
_NEW_JS = r"""
const $ = s => document.querySelector(s);
const box = savedBox(DATA) || { name: "the reference box", gpu: DATA.ref.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: DATA.ref.ram, rambw: DATA.ref.rambw };
$("#boxname").textContent = box.name === "the reference box" ? `the reference box (${DATA.ref.gpu} · ${Math.round(DATA.ref.ram / 1024)} GB · ${DATA.ref.rambw} GB/s)` : `your box (${box.name} · ${Math.round(box.ram / 1024)} GB · ${box.rambw} GB/s)`;
const fmtDl = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : n;
const cap = v => v > 200 ? "200+" : "~" + Math.round(v);   // above ~200 the formula ignores per-token overheads
const rows = DATA.rows.map(r => { const f = forBox(r.sh, box);
  const exp = r.guess ? { mid: r.guess.mid, lo: r.guess.lo, hi: r.guess.hi, src: "eci" } : null;
  return Object.assign({}, r, { f, fits: f.fits, exp, speed: f.fits ? f.t2 : 0 }); });
const tile = v => `<span class="tile pred ${v >= 50 ? "hi" : v >= 25 ? "mid" : "lo"}">${cap(v)}</span>`;
function expCell(r) {
  const ft = r.rel.length ? `<span class="rel">fine-tunes measured: ${r.rel.map(x => x[0] + " " + Math.round(x[1]) + "%").join(", ")}</span>` : "";
  if (!r.exp) return `<span class="q">—</span>` + (ft || `<span class="rel">${DATA.eciReady ? "not in the public index" : "prediction after the first base models are measured"}</span>`);
  return `<span class="guess">~${r.exp.mid}%</span><span class="rel" title="Epoch Capabilities Index of ${r.guess.name}: ${r.guess.eci.toFixed(1)}">${r.exp.lo}–${r.exp.hi} · from ECI${r.guess.remix ? " of its base" : ""}</span>` + ft;
}
let sortKey = "default", sortDir = -1;
const keys = { exp: r => r.exp ? r.exp.mid : -1, speed: r => r.speed, size: r => r.total, dl: r => r.dl,
  default: r => (r.fits ? 1e9 : 0) + (r.exp ? r.exp.mid * 1e6 : 0) + r.dl / 1e3 };
function render() {
  const k = keys[sortKey];
  const list = rows.slice().sort((a, b) => sortDir * (k(a) - k(b)) || (b.dl - a.dl));
  $("#rows").innerHTML = list.map((r, i) => `<tr class="${r.fits ? "" : "nofit"}"><td class="l"><span class="m">${r.repo.split("/")[1].replace(/-GGUF$/i, "")}</span><a class="repo" href="https://huggingface.co/${r.repo}" rel="noopener">${r.repo}</a></td>` +
    `<td>${r.total}B · ${r.active}B</td><td>${expCell(r)}</td>` +
    `<td>${r.fits ? tile(r.f.t2) + `<span class="rel">${cap(r.f.td)} at 32k · ${Math.round(r.f.ctx / 1024)}k ctx</span>` : `<span class="red">✗ too big</span>`}</td>` +
    `<td><span class="q">${r.quant} · ${r.gb} GB</span></td><td>${fmtDl(r.dl)}</td>` +
    `<td><button class="btn go" data-i="${i}">TEST IT</button></td></tr>` +
    `<tr class="cmdrow" id="c${i}" hidden><td colspan="7"><div class="cmds">Draft the recipe, download and fit it, tune the speed, run the suite (~2 h):` +
    `<code>llmbox recipe new ${r.repo} --write</code><code>llmbox install ${r.rid} --apply</code><code>llmbox tune ${r.rid}</code>` +
    `<code>llmbox bench ${r.rid} --recipe ${r.rid} --speed-probe</code></div></td></tr>`).join("");
  document.querySelectorAll(".go").forEach(b => b.addEventListener("click", () => { const c = $("#c" + b.dataset.i); c.hidden = !c.hidden; }));
  document.querySelectorAll("th[data-sort]").forEach(th => th.classList.toggle("on", th.dataset.sort === sortKey));
}
document.querySelectorAll("th[data-sort]").forEach(th => th.addEventListener("click", () => {
  sortDir = sortKey === th.dataset.sort ? -sortDir : -1; sortKey = th.dataset.sort; render(); }));
render();
"""


def method_page(ref: dict | None) -> str:
    """How the numbers are made. The figures (weights, task counts, versions, depths) come from the code."""
    from . import suite
    per = {}
    for b, _k, lvl in suite.QUICK_ITEMS:
        per.setdefault(b, []).append(lvl)
    from .suite import sessions
    counts = {"sessions": sum(1 for b, k, _l in suite.QUICK_ITEMS if b == "agentic" and k in sessions.KINDS), "agentic": len(per.get("agentic", []))}
    rows = "".join(f'<tr><td class="l"><span class="m2">{esc(TIPS[b][0].split(" ·")[0])}</span></td><td>{share(b) * 100:.0f}%</td>'
                   f'<td>{len(per.get(b, []))}</td><td class="l q">{esc(_GRADING[b].format(**counts))}</td></tr>' for b in BLOCKS)
    n = len(suite.QUICK_ITEMS)
    n6 = sum(1 for *_x, lvl in suite.QUICK_ITEMS if lvl >= 6)
    ref_name = (ref or {}).get("recipe", {}).get("id") or "the frontier model"
    ref_cap = (ref or {}).get("summary", {}).get("capability")
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / how scores work</div><h1>How the numbers are made</h1>
 <p class="q" style="margin-top:6px">Suite v{esc(suite.VERSION)} · content hash {esc(suite.content_hash())}</p></div></section>
<article class="doc">
<h2>The score</h2>
<p>Every model gets the same {n} tasks. A program grades each one from 0 to 100; no model grades another. The blocks are
weighted by how people use local models, and the weighted average is the <b>capability</b>.</p>
<p>The <b>score</b> is that capability as a share of what a frontier model gets on the same tasks: {esc(ref_name)}{f" scored {ref_cap:.1f}, which is 100%" if ref_cap else ""}.
It runs under the same conditions as a local model: the task's own system prompt, the same tools and the same graders.
Only the model differs.</p>

<h2>The tasks</h2>
<div class="tw"><table><tr><th class="l">BLOCK</th><th>WEIGHT</th><th>TASKS</th><th class="l">WHAT AND HOW IT IS GRADED</th></tr>{rows}</table></div>
<p>The weights are those of suite v{esc(suite.VERSION.split("-")[0])}, set for people who download and run local models, mostly developers.
Saved runs are re-weighted with them, and each task keeps the score it got.</p>
<p>Each kind of task has difficulty levels. The quick suite uses hard ones (levels 4–5), a few easier ones so that weak models
still register, and {n6} expert tasks (level 6) that local models rarely solve, so the frontier has room above them.
Tasks are generated from a seed: a new seed gives fresh tasks that test the same rules with other names, numbers and files,
so a model cannot have seen the answers. Most tasks ask several questions and give credit per question, test or constraint.</p>

<h2>How sure the numbers are</h2>
<p>A model's score is estimated from every answer it gave, in every run on these tasks, with item response theory. Each task family
(kind × level) has a measured difficulty and sharpness, calibrated on all measured models; a model has an overall level plus its own
strength or weakness per block. The score is the expected weighted result on the quick suite at that level, and the range next to it
is its 95% interval. Every further run adds answers and narrows the range.</p>
<p>A run is either <b>fixed</b> (every task family once: 40–110 minutes, depending on the model's speed) or <b>adaptive</b> (40 minutes:
after each task, the next one is the task that narrows the range most per second of this model's time, and tasks it always or never
solves are skipped). Measured on fresh tasks: six runs of one model, three of each kind, agreed within ±2.6 points.</p>
<p>When two ranges overlap, the difference is not settled, and both models share a rank range such as <b>1–2</b>.
Verdicts: 85% of the frontier or more is excellent, 70% very good, 50% good.</p>

<h2>Speed</h2>
<p>Speed is measured with one conversation at a time, the way one person uses the model: a fresh prompt of real code at about 2k, 30k
and 90k tokens, so nothing comes from the cache, with code as the answer, so speculative decoding sees realistic text.
<b>Tok/s</b> is how fast the answer is written; <b>first word</b> is how long the model reads the whole context before it starts.</p>
<p>Speed on other boxes is predicted: a token needs the active weights read once, from VRAM for what fits on the card and from system RAM for the rest,
so the time per token follows from the model file and the two memory speeds. The prediction is then scaled by what the measured run got
against the same prediction on its own box. When most of a model moves onto a bigger card, it is outside what was measured, and the page says
<i>rough estimate</i>.</p>

<h2>What a run records</h2>
<p>Every run keeps the server's exact command line and sampling defaults, the llama.cpp build, the model file's sha256, and the GPU and CPU
temperature, power and memory every five seconds. The settings are compared with the recipe: differences in speed settings keep the
recipe's score; differences in sampling, template, KV cache or model file make it a different recipe that needs its own score.</p>

<h2>Thinking</h2>
<p>Replies are capped at 32k tokens and thinking at 24k, so there is always room left for the answer. A reply cut at that limit, or
thinking that repeats itself (detected from the text, not from its length), is flagged on the run page. Flagged tasks still count as they were graded.</p>

<h2>Versions</h2>
<p>Scores compare only within one suite version. The content hash identifies the exact tasks and graders; a changed task means a new version,
and older results stay on their own version.</p>
</article>'''
    return _page(f"llmbox · how scores work (suite v{suite.VERSION})", "METHOD", body, _METHOD_CSS)


_METHOD_CSS = """
.doc{max-width:860px;margin:10px 0 0;padding:6px 22px 10px}
.doc h2{font:600 22px "IBM Plex Sans Condensed";margin:34px 0 10px}
.doc p{font-size:14px;line-height:1.75;color:var(--soft);max-width:74ch;margin-top:10px}.doc p b{color:var(--ink);font-weight:500}
.doc table{margin-top:12px}.doc td.q{font-size:12.5px;line-height:1.55;padding:12px 8px}.doc td{vertical-align:top}
"""


def build(out_dir: str, host: str = "box", suite_version: str | None = None, tier: str = "quick") -> list[str]:
    from . import suite as _s
    suite_version = suite_version or _s.VERSION
    """The whole site: home, a page per recipe, per run, hardware per recipe, compare per pair of measured recipes."""
    import itertools
    import json as _json
    out_dir = os.path.expanduser(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    written = [home(out_dir, host, suite_version, tier)]
    recs = load_records(host, suite_version, tier)
    local_run, ref = recs["local"], recs["ref"]
    allrecs = report.results.load_all(host)
    local = {rid: report.with_probe(rec, allrecs) for rid, rec in local_run.items()}   # speed re-measured after tune; run pages keep their own
    rs = [r for r in report.rows(host, suite_version=suite_version, tier=tier) if r["host"].get("id") != "cloud" and not r.get("partial")]
    ranks = rank_ranges(rs)
    n_total = len(rs) + len([j for j in queue_state() if j["model"] not in local])
    data = shape_data(rs, host)
    flags = {rid: task_flags(rec) for rid, rec in local.items()}
    order = sorted(local, key=lambda k: -local[k]["summary"]["capability"])
    TAB_LINKS["COMPARE"] = f"compare-{order[0]}-vs-{order[1]}.html" if len(order) > 1 else "#"
    def w(name, html_):
        p = os.path.join(out_dir, name)
        open(p, "w").write(html_)
        written.append(p)
    for rid in order:
        rec = local[rid]
        w(f"recipe-{rid}.html", recipe_page(rid, rec, ref, local, ranks, flags[rid], len(local), n_total))
        w(f"run-{rec['id'][:8]}.html", run_page(rid, local_run[rid], ref, flags[rid]))
        if rid in data["recipes"]:
            w(f"hardware-{rid}.html", hardware_page(rid, rec, data["recipes"][rid], data))
    for a, b in itertools.combinations(order, 2):
        w(f"compare-{a}-vs-{b}.html", compare_page(a, b, local[a], local[b], ref, flags[a], flags[b]))
    w("method.html", method_page(ref))
    try:
        np_ = new_page(rs, data, host)
    except Exception as e:   # the list needs Hugging Face; the rest of the site must not depend on it
        np_ = None
        print(f"new.html skipped: {e}")
    if np_:
        w("new.html", np_)
    return written


_PAGES_CSS = """
.title,.hd{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:18px;padding:18px 22px}
.title h1,.hd h1{font:600 32px/1.1 "IBM Plex Sans Condensed";margin-top:6px}.title h1 a,.hd h1 a{color:var(--ink);border-bottom:1px dotted var(--faint)}
.title .meta{font-size:12px;color:var(--muted);margin-top:8px}
.title .acts,.hd .acts{display:flex;flex-wrap:wrap;gap:10px}
.hd .id{font-size:12px;color:var(--muted);margin-top:6px}
.tele{display:grid}.tele>div{padding:14px 16px;border-right:1px solid var(--line2)}.tele>div:last-child{border-right:0}
.tele .tv{font-size:22px;margin-top:2px}.tele .tv small{font-size:11px;color:var(--muted);margin-left:4px}
.spark{width:100%;height:40px;display:block;margin-top:6px}
.flag{font-size:10px;letter-spacing:.12em;border:1px solid var(--line);color:var(--soft);padding:1px 6px;margin-right:4px}.flag.lo{border-color:var(--red-dim);color:var(--red)}
.m2{font:600 14px "IBM Plex Sans Condensed";color:var(--ink)}
.bars{grid-template-columns:170px minmax(0,1fr) 170px}
.bars .tr{position:relative;height:26px;background:var(--line2)}.bars .tr+.tr{margin-top:4px}
.bars .tr i{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,rgba(255,176,0,.28),rgba(255,176,0,.7));box-shadow:0 0 10px rgba(255,176,0,.2)}
.bars .tr.b i{background:rgba(232,228,216,.2);box-shadow:none}
.bars .tr span{position:absolute;left:10px;top:4px;font-size:13px;white-space:nowrap}.bars .tr span em{font-style:normal;color:rgba(232,228,216,.7);margin-left:10px}
.bars.multi{grid-template-columns:150px minmax(0,1fr)}
@media (max-width:900px){.title,.hd{grid-template-columns:1fr}.tele{grid-template-columns:1fr 1fr!important}.tele>div{border-bottom:1px solid var(--line2)}
 .bars{grid-template-columns:110px minmax(0,1fr)}}
"""
_RECIPE_CSS = """
.top{display:grid;grid-template-columns:150px minmax(0,1fr) 380px}
.score{padding:26px 10px;text-align:center;border-right:1px solid var(--line2)}.score .n{font:300 64px/1 "IBM Plex Mono";color:var(--amber)}.score .n small{font-size:26px}
.score .of{font-size:11px;letter-spacing:.14em;color:var(--muted);margin-top:6px;text-transform:uppercase}.score .rk{margin-top:18px;font-size:13px;line-height:1.6}
.lines{padding:14px 22px;border-right:1px solid var(--line2)}
.line{display:grid;grid-template-columns:120px 150px minmax(0,1fr) auto;align-items:baseline;gap:12px;padding:9px 0;border-bottom:1px solid var(--line2)}.line:last-child{border-bottom:0}
.line .k{font:600 15px "IBM Plex Sans Condensed"}.line em{font-style:normal;font-size:14px;color:var(--amber)}.line em.r{color:var(--red)}
.line .s{font-size:12px;color:var(--muted)}
.starw{position:relative;padding:18px 10px 10px}.starw svg{width:100%;height:auto;display:block}
.ax{position:absolute;width:80px;font-size:10.5px;letter-spacing:.12em;color:var(--muted);text-align:center;line-height:1.3}.ax>b{display:block;font:400 16px "IBM Plex Mono";color:var(--ink);letter-spacing:0}.ax>b.r{color:var(--red)}
.ax .pop{left:-120px;width:320px;text-align:left;letter-spacing:0;font-size:12.5px}.ax .pop b{display:block;font:500 11px "IBM Plex Mono";color:var(--amber);letter-spacing:.14em;text-transform:uppercase}
.lgd{text-align:center;font-size:11px;color:var(--muted);margin-top:22px}
.sw{display:grid;grid-template-columns:1fr 1fr;border-top:1px solid var(--line2)}.sw>div{padding:14px 22px;font-size:13px;line-height:1.8}.sw>div:first-child{border-right:1px solid var(--line2)}
.sw .sc{margin-right:12px}.sw b{font-weight:500;color:var(--amber)}.sw .w b{color:var(--red)}
.rgrid{display:grid;grid-template-columns:1.3fr 1fr}.rgrid>div{padding:18px 22px}.rgrid>div:first-child{border-right:1px solid var(--line2)}
.rgrid .sc{margin-bottom:10px}.rgrid dl{display:grid;grid-template-columns:110px 1fr;row-gap:7px;font-size:13px}.rgrid dt{color:var(--muted)}.rgrid dd.val{color:var(--amber)}
.notes{padding:14px 22px 16px;border-top:1px solid var(--line2)}.notes .sc{margin-bottom:6px}.notes li{list-style:none;font-size:12.5px;color:var(--soft);padding:3px 0 3px 14px;position:relative}.notes li:before{content:"›";position:absolute;left:0;color:var(--amber)}
.runs .cfg{font-size:12.5px;color:var(--soft)}
@media (max-width:1100px){.top{grid-template-columns:130px minmax(0,1fr)}.top>div:last-child{grid-column:1/-1;border-top:1px solid var(--line2)}.line{grid-template-columns:110px 1fr}.line .s{grid-column:1/-1}}
@media (max-width:900px){.top{grid-template-columns:1fr}.score{border-right:0}.lines{border-right:0}.sw,.rgrid{grid-template-columns:1fr}.sw>div:first-child,.rgrid>div:first-child{border-right:0}}
"""
_RUN_CSS = """
.sum{display:grid;grid-template-columns:repeat(6,minmax(0,1fr))}.sum>div{padding:16px 18px;border-right:1px solid var(--line2)}.sum>div:last-child{border-right:0}
.sum b{display:block;font:300 32px "IBM Plex Mono";color:var(--amber);line-height:1.1}.sum b.w{color:var(--ink)}.sum span{font-size:12px;color:var(--muted)}
.two{display:grid;grid-template-columns:minmax(0,420px) minmax(0,1fr);gap:22px}
.sys dl{display:grid;grid-template-columns:90px 1fr;row-gap:7px;font-size:12.5px;padding:18px 22px}.sys dt{color:var(--muted)}.sys dd{word-break:break-word}.sys dd.todo{color:var(--red)}
.argv{padding:16px 22px;font-size:12px;line-height:1.7;color:var(--soft);columns:2 260px;column-gap:28px}.argv span{display:block;word-break:break-all}
.diff{padding:10px 22px 16px;border-top:1px solid var(--line2);font-size:12.5px}
.tasks td{padding:7px 8px;font-size:12.5px}.tasks .tile{min-width:46px;font-size:13px;padding:3px 4px 2px}
.tasks tr.grp td{color:var(--muted);font-size:11px;letter-spacing:.16em;padding:16px 10px 6px;border-bottom:1px solid var(--line)}
@media (max-width:900px){.sum{grid-template-columns:1fr 1fr}.sum>div{border-bottom:1px solid var(--line2)}.two{grid-template-columns:1fr}}
"""
_HW_CSS = """
.adv{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}.adv>div{padding:20px 22px;border-right:1px solid var(--line2)}.adv>div:last-child{border-right:0}
.adv h4{font:600 16px "IBM Plex Sans Condensed"}.adv .g{font:300 34px "IBM Plex Mono";color:var(--amber);margin:6px 0 4px}.adv .g.no{color:var(--muted)}.adv p{font-size:12.5px;color:var(--soft);line-height:1.55}
.why{padding:14px 22px;font-size:12.5px;color:var(--muted);line-height:1.6;border-top:1px solid var(--line2)}
.lowc{font-size:11px;color:var(--faint)}.youb{font-size:10px;letter-spacing:.14em;color:var(--bg);background:var(--amber);padding:1px 6px;margin-left:8px}
#boxes td.l .m{font-size:15px}
@media (max-width:900px){.adv{grid-template-columns:1fr}.adv>div{border-right:0;border-bottom:1px solid var(--line2)}}
"""
_CMP_CSS = """
.score2{display:flex;align-items:center;gap:18px}.score2 b{display:block;font:300 44px "IBM Plex Mono";color:var(--amber);text-align:center}.score2 span{font-size:11px;color:var(--muted)}.score2 .vs{font-size:30px;color:var(--muted)}
.two2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px}.h2h td{font-size:14px}.h2h td.win{color:var(--amber);background:rgba(255,176,0,.05)}
.two2 .bars{grid-template-columns:120px minmax(0,1fr)}
.only{display:grid;grid-template-columns:1fr 1fr;gap:18px}.only .sc{margin-bottom:6px}.only li{list-style:none;font-size:12.5px;color:var(--soft);padding:3px 0}
@media (max-width:900px){.two2,.only{grid-template-columns:1fr}.two2 .bars{grid-template-columns:100px minmax(0,1fr)}}
"""
_HW_JS = r"""
const $ = s => document.querySelector(s);
const saved = savedBox(DATA);
const box = saved || { name: "reference box", gpu: DATA.ref.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: DATA.ref.ram, rambw: DATA.ref.rambw };
if (saved) $("#advbox").textContent = `What would make your box faster · ${box.name} · ${Math.round(box.ram / 1024)} GB · ${box.rambw} GB/s`;
const cur = forBox(DATA.sh, box);
const low = hw => forBox(DATA.sh, hw).gf > 0.6;   // most experts on the GPU: outside the measured regime
function card(title, hw, note) {
  const f = forBox(DATA.sh, hw), g = f.t2 / cur.t2;
  const txt = g > 1.05 ? `+${Math.round((g - 1) * 100)}%` : g < 0.95 ? `${Math.round((g - 1) * 100)}%` : "±0%";
  return `<div><h4>${title}</h4><div class="g ${Math.abs(g - 1) < 0.05 ? "no" : ""}">${txt}</div><p>~${Math.round(f.t2)} tok/s. ${note}${low(hw) ? ' <span class="lowc">rough estimate</span>' : ""}</p></div>`;
}
const faster = DATA.ramKinds.map(r => r[1]).filter(v => v >= box.rambw * 1.2)[0];   // a step worth buying
const gp = n => { const g = DATA.gpus.find(x => x[0].startsWith(n)); return { gpu: n, vram: g[1], vrambw: g[2], ram: box.ram, rambw: box.rambw }; };
$("#adv").innerHTML = (faster ? card(`Faster RAM (${faster} GB/s)`, Object.assign({}, box, { rambw: faster }), "Same card, faster memory.") : "<div><h4>Faster RAM</h4><p class='q'>already at the fastest common speed</p></div>")
  + card("A 16 GB card (RTX 5070 Ti)", gp("RTX 5070 Ti"), "More experts fit on the GPU.")
  + card("A 24 GB card (RTX 4090)", gp("RTX 4090"), "Most experts on the GPU.");
// one row per GPU, all with the visitor's RAM (or 64 GB at the reference box's RAM speed)
const ram = saved ? saved.ram : 65536, rambw = saved ? saved.rambw : DATA.ref.rambw;
$("#boxlbl").textContent = `Boxes · each with ${Math.round(ram / 1024)} GB RAM at ${rambw} GB/s${saved ? " (your RAM)" : ""}`;
const m = DATA.measured, rows = [];
rows.push({ name: `${m.gpu} · ${Math.round(m.ram / 1024)} GB · ${m.rambw} GB/s`, measured: true, t2: m.t2, td: m.td, f: forBox(DATA.sh, { gpu: m.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: m.ram, rambw: m.rambw }) });
for (const g of DATA.gpus) {
  const hw = { gpu: g[0], vram: g[1], vrambw: g[2], ram, rambw }, f = forBox(DATA.sh, hw);
  rows.push({ name: g[0], measured: false, t2: f.t2, td: f.td, f, low: low(hw), mine: saved && saved.name === g[0] });
}
rows.sort((a, b) => b.t2 - a.t2);
const T = (v, pred) => `<span class="tile ${v >= 50 ? "hi" : v >= 25 ? "mid" : "lo"}${pred ? " pred" : ""}">${pred ? "~" : ""}${Math.round(v)}</span>`;
$("#boxes").innerHTML = `<tr><th class="l">BOX</th><th>TOK/S<br><span class="faint">short chat</span></th><th>TOK/S<br><span class="faint">long context</span></th><th>EXPERTS<br><span class="faint">on the GPU</span></th><th>CONTEXT</th><th class="l"></th></tr>` +
  rows.map(r => `<tr class="${r.measured || r.mine ? "sel" : ""}"><td class="l"><span class="m">${r.name}</span>${r.measured ? ' <span class="youb">MEASURED</span>' : r.mine ? ' <span class="youb">YOUR BOX</span>' : ""}</td>` +
    `<td>${T(r.t2, !r.measured)}</td><td>${r.td ? T(r.td, !r.measured) : "—"}</td><td>${Math.round(r.f.gf * 100)}%</td><td>${r.f.fits ? "✓ " + Math.round(r.f.ctx / 1024) + "k" : "✗"}</td>` +
    `<td class="l">${r.measured ? '<span class="q">1 run</span>' : r.low ? '<span class="lowc">rough estimate</span>' : '<span class="q">predicted</span>'}</td></tr>`).join("");
"""
