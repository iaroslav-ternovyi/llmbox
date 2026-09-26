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
from .hosts import HOME

ASSETS = os.path.join(os.path.dirname(__file__), "site_assets")
BLOCKS = ["agentic", "code", "tools", "longctx", "writing", "reasoning"]
LABEL = {"agentic": "AGENTIC", "code": "CODE", "tools": "TOOLS", "longctx": "LONG DOCS", "writing": "WRITING", "reasoning": "REASONING"}
# hover text for each score: what it measures, its weight, example tasks (the suite's real task kinds)
TIPS = {
    "agentic": ("Agentic coding · 25% of the total", "Multi-turn work in a real repository: read, edit, run tests, keep up when the request changes mid-session.",
                ["Fix the shipping-fee bug; hidden tests decide", "Cart: add discount codes, then change the rule"]),
    "code": ("Code · 10%", "One function or module from a written spec. Graded only by hidden unit tests.",
             ["Expression evaluator, LRU cache, CSV parser", "Expert: cron schedule across DST changes"]),
    "tools": ("Tools & automation · 20%", "Calling business tools correctly on messy data: a CRM, invoices, payments. Wrong or extra calls cost points.",
              ["Payment reminders only to customers really overdue", "Expert: bulk discounts under an approval policy"]),
    "longctx": ("Long documents · 15%", "Exact answers from 100k–200k-token documents: logs, contracts, reports.",
                ["Which incident stayed open the longest?", "Count incidents matching three conditions"]),
    "writing": ("Writing · 15%", "Text with hard constraints a checker can verify: length, required facts, glossary terms, plural rules.",
                ["Meeting minutes with owners and dates", "UI strings in Russian / Ukrainian with ICU plurals"]),
    "reasoning": ("Reasoning · 15%", "Multi-step problems with one exact answer.",
                  ["Order total with tiered discounts and tax", "Trace a function by hand; schedule jobs"]),
}
# published verdict thresholds (percent of the frontier reference), versioned with the suite
QUADRANT = {"vs_ref": 70.0, "tps": 40.0}   # the "best for this box" corner of the quality x speed chart


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
    """Quality (% of frontier) x decode speed on this box, Pareto line, 'best for this box' corner, CI whiskers."""
    W, H, L, B, T, R = 640, 330, 58, 290, 20, 620
    xmax = max([60.0] + [r["speed"].get("decode_tps") or 0 for r in local]) * 1.25
    X = lambda v: L + v / xmax * (R - L)
    Y = lambda v: B - v / 100 * (B - T)
    g = []
    for v in range(0, 101, 25):
        g.append(f'<line x1="{L}" y1="{Y(v):.1f}" x2="{R}" y2="{Y(v):.1f}" stroke="#2a2c22" stroke-dasharray="2 4"/><text x="{L-8}" y="{Y(v)+4:.1f}" text-anchor="end">{v}%</text>')
    step = 20 if xmax > 60 else 10
    for v in range(0, int(xmax) + 1, step):
        g.append(f'<line x1="{X(v):.1f}" y1="{T}" x2="{X(v):.1f}" y2="{B}" stroke="#2a2c22" stroke-dasharray="2 4"/><text x="{X(v):.1f}" y="{B+16}" text-anchor="middle">{v}</text>')
    qx, qy = X(QUADRANT["tps"]), Y(QUADRANT["vs_ref"])
    quad = (f'<rect x="{qx:.1f}" y="{T}" width="{R-qx:.1f}" height="{qy-T:.1f}" fill="rgba(255,176,0,.06)" stroke="#7a5a14" stroke-dasharray="3 4"/>'
            f'<text x="{R-6}" y="{T+14}" text-anchor="end" fill="#b88419">best for this box</text>')
    ref = f'<line x1="{L}" y1="{Y(100):.1f}" x2="{R}" y2="{Y(100):.1f}" stroke="#E8E4D8" stroke-opacity=".7" stroke-dasharray="6 4"/><text x="{L+6}" y="{Y(100)-6:.1f}" fill="#E8E4D8">frontier reference = 100%</text>'
    pts, dots = [], []
    for r in local:
        tps = r["speed"].get("decode_tps")
        if not tps or r.get("vs_ref") is None:
            continue
        k = r["vs_ref"] / r["capability"] if r["capability"] else 1
        lo, hi = r["ci"][0] * k, min(100.0, r["ci"][1] * k)
        pts.append((tps, r["vs_ref"], r["id"]))
        dots.append(f'<line x1="{X(tps):.1f}" y1="{Y(hi):.1f}" x2="{X(tps):.1f}" y2="{Y(lo):.1f}" stroke="#FFB000" stroke-opacity=".45" stroke-width="6"/>'
                    f'<circle cx="{X(tps):.1f}" cy="{Y(r["vs_ref"]):.1f}" r="6" fill="#FFB000" filter="url(#g)"/>'
                    f'<text x="{X(tps)+11:.1f}" y="{Y(r["vs_ref"])+4:.1f}" fill="#E8E4D8" font-size="12">{esc(r["id"])} · {r["vs_ref"]:.0f}%</text>')
    fr = pareto(pts)
    line = ""
    if len(fr) > 1:
        line = f'<polyline points="{" ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b, _ in fr)}" fill="none" stroke="#FFB000" stroke-dasharray="3 3"/>'
    return (f'<svg viewBox="0 0 {W} {H+16}" class="scatter" font-family="IBM Plex Mono" font-size="10" fill="#6c695f">{"".join(g)}{quad}{ref}{line}{"".join(dots)}'
            f'<text x="{(L+R)/2}" y="{H+10}" text-anchor="middle" fill="#8b877b">decode tok/s on this box at 2k context →</text>'
            f'<text x="14" y="{T-6}" fill="#8b877b">↑ % of frontier (bar = 95% CI)</text></svg>')


# common GPUs: VRAM (MiB) and memory bandwidth (GB/s), for the "your box" picker
GPUS = [("RTX 3060 12 GB", 12288, 360), ("RTX 3090 24 GB", 24576, 936), ("RTX 4060 Ti 16 GB", 16380, 288),
        ("RTX 4070 12 GB", 12282, 504), ("RTX 4070 Ti Super 16 GB", 16376, 672), ("RTX 4080 16 GB", 16376, 717),
        ("RTX 4090 24 GB", 24564, 1008), ("RTX 5060 Ti 16 GB", 16311, 448), ("RTX 5070 12 GB", 12227, 672),
        ("RTX 5070 Ti 16 GB", 16303, 896), ("RTX 5080 16 GB", 16303, 960), ("RTX 5090 32 GB", 32607, 1792)]
RAM_KINDS = [("DDR4-3200", 40), ("DDR5-5600", 60), ("DDR5-6400", 75), ("DDR5-8000", 88)]


def shape_data(local: list[dict], host: str = "box") -> dict:
    """Per recipe: the GGUF shape numbers estimate.plan() uses, plus a calibration factor = measured / predicted on the
    reference box (it carries what the model does not: MTP speed-up, kernel quirks). Shapes are cached per file."""
    import json
    from . import estimate as E, hosts, recipe as rc, speed
    cache = os.path.join(HOME, "shapes")
    os.makedirs(cache, exist_ok=True)
    prof = hosts.load(host)
    ref_hw = hosts.spec(prof)
    h = None
    out = {}
    for r in local:
        try:
            rec = rc.load(host, r["id"])
        except (OSError, ValueError):
            continue
        path = rec["model"]["path"]
        cp = os.path.join(cache, os.path.basename(path) + ".json")
        if os.path.exists(cp):
            sh = E.ModelShape(**json.load(open(cp)))
        else:
            h = h or hosts.host_of(prof)
            sh = speed.shape_on_host(h, path)
            json.dump({k: v for k, v in sh.__dict__.items()}, open(cp, "w"))
        kv, ctx = rec["placement"]["kv_type"], rec["placement"]["ctx"] or sh.context_length
        bd = (r["speed"].get("by_depth") or {})
        m2 = r["speed"].get("decode_tps")
        deep = [(report._depth_k(k), d["decode_tps"]) for k, d in bd.items() if report._depth_k(k) >= 24 and d.get("decode_tps")]
        p2 = E.plan(sh, ref_hw, ctx=ctx, kv_type=kv, depth=2000).decode_tps_at_depth
        k2 = m2 / p2 if m2 else 1.0
        if deep:
            dk, dv = max(deep)
            kd = dv / E.plan(sh, ref_hw, ctx=ctx, kv_type=kv, depth=int(dk * 1000)).decode_tps_at_depth
        else:
            dk, kd = 88, k2
        out[r["id"]] = {"moe": sh.is_moe, "nonexp": sh.nonexpert_bytes, "exp": sh.expert_bytes, "embed": sh.embed_bytes,
                        "layers": sh.n_layers, "nExp": sh.n_expert, "nUsed": sh.n_expert_used, "rec": sh.recurrent_state_bytes,
                        "cpuEff": sh.expert_cpu_eff, "kvB": sh.kv_bytes_per_token(kv), "ctx": ctx, "k2": round(k2, 4), "kd": round(kd, 4),
                        "deepK": dk, "size": round((sh.total_bytes or 0) / 1e9, 1)}
    return {"recipes": out, "ref": {"gpu": prof["hw"]["gpus"][0]["name"].replace("NVIDIA GeForce ", "") if prof["hw"]["gpus"] else "",
                                    "vram": ref_hw.vram_mib, "ram": ref_hw.ram_mib, "rambw": ref_hw.ram_bw_gbs, "vrambw": ref_hw.vram_bw_gbs},
            "gpus": GPUS, "ramKinds": RAM_KINDS}


def home(out_dir: str, host: str = "box", suite_version: str = "0.9", tier: str = "quick") -> str:
    rs = report.rows(host, suite_version=suite_version, tier=tier)
    ref = next((r for r in rs if r["host"].get("id") == "cloud"), None)
    local = [r for r in rs if r["host"].get("id") != "cloud" and not r.get("partial")]
    hw = next((r["host"] for r in local), {})
    ranks = rank_ranges(local)
    q = [j for j in queue_state() if j["model"] not in {r["id"] for r in local}]
    total = len(local) + len(q)

    # auto summary: computed from the data, no editorial text
    def best(key, label, fmt):
        c = [r for r in local if key(r) is not None]
        if not c:
            return ""
        b = max(c, key=key)
        return f'<div><span class="sc">{label}</span><b>{esc(b["id"])}</b><span class="amb">{fmt(b)}</span></div>'
    summary = "".join([
        best(lambda r: r.get("vs_ref"), "Best quality (any box)", lambda r: f'{r["vs_ref"]:.0f}% of frontier'),
        best(lambda r: r["speed"].get("decode_tps"), "Fastest on your box", lambda r: f'<span id="fastest">{r["speed"]["decode_tps"]:.0f} tok/s</span>'),
        best(lambda r: r["blocks"].get("agentic"), "Best for agentic coding", lambda r: f'{r["blocks"]["agentic"]:.0f}'),
        best(lambda r: r["blocks"].get("longctx"), "Best for long documents", lambda r: f'{r["blocks"]["longctx"]:.0f}'),
    ])
    pend = f'<div><span class="sc">Still measuring</span><b>{len(q)} of {total}</b><span class="muted">results appear as runs finish</span></div>' if q else ""

    head = ("<tr><th></th><th class='l'>MODEL · RECIPE</th><th>RANK</th><th>OF FRONTIER ▾</th>"
            + "".join(f"<th>{_tip(b)}</th>" for b in BLOCKS)
            + "<th>TOK/S ON YOUR BOX<br><span class='faint'>2k · deep</span></th><th>FITS YOUR BOX</th><th>SOLVED/H</th></tr>")
    body = []
    if ref:
        body.append("<tr><td class='rk'>REF</td><td class='l mod'><span class='m'>" + esc(ref["id"]) + "</span><br><span class='q'>cloud · frontier reference · 1 run</span></td><td class='q'>—</td>"
                    + f"<td>{_tile(100, f'{ref["capability"]:.1f}', big=True)}</td>" + "".join(f"<td>{_tile(ref['blocks'].get(b))}</td>" for b in BLOCKS)
                    + f"<td class='q'>cloud</td><td class='q'>—</td><td class='num'>{ref['solved_per_hour']:.0f}</td></tr>")
    for i, r in enumerate(local, 1):
        sp = r["speed"]
        bd = sp.get("by_depth") or {}
        deep = report._deep(sp)
        k28 = next((d for k, d in bd.items() if report._depth_k(k) >= 24), None)
        ttft = f"{28000 / k28['prefill_tps']:.1f} s" if k28 and k28.get("prefill_tps") else "—"
        lo, hi = ranks[r["id"]]
        rk = f"{lo}" if lo == hi else f"{lo}–{hi}"
        tps = sp.get("decode_tps")
        body.append(f"<tr class='{'sel' if i == 1 else ''}' data-rid='{esc(r['id'])}'><td class='rk'>{i}</td><td class='l mod'><a class='m' href='recipe-{esc(r['id'])}.html'>{esc(r['id'])}</a><br><span class='q'>{esc(_quant(r['file']))} · 1 run</span><div class='cmpbox'><i></i>compare</div></td>"
                    f"<td><span class='rkr'>{rk}</span></td><td>{_tile(r.get('vs_ref'), f'{r['capability']:.1f} · {r['ci'][0]:.0f}–{r['ci'][1]:.0f}', big=True)}</td>"
                    + "".join(f"<td>{_tile(r['blocks'].get(b))}</td>" for b in BLOCKS)
                    + f"<td class='spd'>{_tile(tps, f'{deep} deep') if tps else '—'}<small class='how'>measured · reference box</small></td><td class='fit q'>—</td><td class='num'>{r['solved_per_hour']:.1f}</td></tr>")
    for j in q:
        if j["status"] == "running":
            w = 100 * j["done"] / j["total"] if j["total"] else 0
            st = f"<span class='live'>● MEASURING NOW</span><span class='prog'><i style='width:{w:.0f}%'></i></span> <span class='q'>{j['done']} / {j['total']} tasks</span>"
        else:
            st = "<span class='q'>queued</span>"
        body.append(f"<tr><td class='rk'>·</td><td class='l mod'><span class='m'>{esc(j['model'])}</span></td><td colspan='11' class='l' style='padding-left:16px'>{st}</td></tr>")

    feed = []
    for rec in sorted(report.results.load_all(host) + report.results.load_all("cloud"), key=lambda x: x.get("created", ""), reverse=True):
        if rec.get("kind") != "suite":
            continue
        su, s = rec.get("suite", {}), rec.get("summary", {})
        rid = (rec.get("recipe") or {}).get("id", "?")
        gpu = "cloud" if (rec.get("host") or {}).get("id") == "cloud" else (rec.get("host") or {}).get("gpu", "?").replace("NVIDIA GeForce ", "")
        tps = (s.get("speed") or {}).get("decode_tps")
        feed.append(f"<li><span class='amb'>{esc(rid)}</span> · {esc(gpu)} · v{esc(su.get('version'))} · {s.get('capability', 0):.1f}"
                    f"{f' · {tps:.0f} tok/s' if tps else ''}<span class='when'>{_ago(rec.get('created', ''))}</span></li>")
        if len(feed) >= 7:
            break
    for j in q:
        if j["status"] == "running":
            feed.insert(0, f"<li><span class='live'>●</span> <span class='amb'>{esc(j['model'])}</span> · {esc(hw.get('gpu', '').replace('NVIDIA GeForce ', ''))} · measuring {j['done']}/{j['total']}<span class='when'>now</span></li>")

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>llmbox · What should I run on my box?</title><link rel="stylesheet" href="osc.css"><style>{_HOME_CSS}</style></head><body>
<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>
<div class="wrap">
<header class="plate"><div class="brand glow">LLMBOX<small>LOCAL LLM BENCHMARK</small></div><div class="search">Search: tiel, qwen3.8, 12 GB …</div></header>
<nav class="tabs"><a class="on" href="index.html">MODELS</a><a href="recipe-{esc(local[0]['id']) if local else ''}.html">RECIPES</a><a href="hardware-{esc(local[0]['id']) if local else ''}.html">HARDWARE</a><a href="{f"compare-{local[0]['id']}-vs-{local[1]['id']}.html" if len(local) > 1 else '#'}">COMPARE</a><a href="#">METHODOLOGY</a><span class="sp"></span><a class="cta" href="#">TEST YOUR BOX ↓</a></nav>
<section class="panel summary"><div class="lbl">WHAT SHOULD I RUN ON MY BOX? · <span id="boxlbl">REFERENCE BOX: {esc(hw.get('gpu', '').replace('NVIDIA GeForce ', '').upper())} · {hw.get('ram_gib', '?')} GB @ {hw.get('ram_read_gbs', '?')} GB/s</span></div>
<div class="sumrow">{summary}{pend}</div></section>
<div class="layout">
<aside>
 <section class="panel pad box"><div class="lbl">YOUR BOX</div>
  <div class="pick"><label>GPU<select id="gpu"><option value="">— pick your GPU —</option></select></label>
   <label>System RAM<select id="ram"><option>16</option><option>32</option><option>48</option><option selected>64</option><option>96</option><option>128</option><option>192</option></select> GB</label>
   <label>RAM speed<select id="bw"></select></label>
   <label class="q">or measured by <b class="amb">llmbox host add</b>: <input id="bwn" placeholder="GB/s" size="5"></label></div>
  <div id="boxnote" class="q" style="margin-top:8px">Pick your hardware: speeds and fit below are recomputed for it. Until then they are the measured values of the reference box ({esc(hw.get('gpu', '').replace('NVIDIA GeForce ', ''))} · {hw.get('ram_gib', '?')} GB · {hw.get('ram_read_gbs', '?')} GB/s).</div>
  <div class="sec"><div class="sc">Weights · suite v{esc(suite_version)}</div>
   <div class="wts"><span class="on">All work</span><span>Agentic</span><span>Documents</span><span>Writing</span><span>Custom…</span></div>
   <div class="q" style="margin-top:6px">agentic 25 · code 10 · tools 20 · long docs 15 · writing 15 · reasoning 15. Pick your mix; the total is recomputed.</div></div>
  <div class="sec"><div class="sc">Filters</div><div class="chk"><i class="on"></i>Fits my box</div><div class="chk"><i class="on"></i>Tool calling works</div><div class="chk"><i></i>Hide models being tested</div></div>
 </section>
 <section class="panel pad feed"><div class="lbl">LATEST RESULTS</div><ul>{''.join(feed)}</ul></section>
</aside>
<div>
 <section class="panel chart"><div class="lbl">QUALITY × SPEED ON YOUR BOX · DASHED = PARETO LINE</div>
  <div class="xtabs"><span class="on" data-x="t2">tok/s at 2k</span><span data-x="td">tok/s deep</span></div><div id="scatter">{_scatter(local)}</div></section>
 <section class="panel"><div class="lbl">RANKING · {total} RECIPES · SUITE v{esc(suite_version)} {esc(tier.upper())} · HOVER A SCORE NAME FOR WHAT IT MEASURES</div>
  <table class="rank">{head}{''.join(body)}</table>
  <div class="cmpbar"><span>Rank is a range when confidence intervals overlap: the difference is noise until more runs arrive.</span><span class="btn">COMPARE (0)</span></div></section>
</div></div>
<footer><span>Every number on this page comes from a saved run. Capability is hardware-independent; speed is for the box shown. Verdict thresholds and weights are published and versioned with the suite.</span><span>generated {time.strftime('%b %d, %Y %H:%M')}</span></footer>
</div>
<script>const DATA = {json.dumps(dict(shape_data(local, host), points=[{"id": r["id"], "vs": r.get("vs_ref"), "cap": r["capability"], "ci": r["ci"], "t2": r["speed"].get("decode_tps"), "td": float(report._deep(r["speed"])) if report._deep(r["speed"]) != "-" else None} for r in local]))};
{_JS}</script></body></html>"""
    os.makedirs(out_dir, exist_ok=True)
    shutil.copy(os.path.join(ASSETS, "osc.css"), os.path.join(out_dir, "osc.css"))
    path = os.path.join(out_dir, "index.html")
    with open(path, "w") as f:
        f.write(page)
    return path


_HOME_CSS = """
.pick label{display:block;font-size:10.5px;letter-spacing:.14em;color:var(--muted);margin-top:10px;text-transform:uppercase}
.pick select,.pick input{display:block;width:100%;margin-top:4px;background:#0b0c09;color:var(--amber);border:1px solid var(--amber-dim);padding:6px 8px;font:13px "IBM Plex Mono";text-transform:none;letter-spacing:0}
.pick label.q{text-transform:none;letter-spacing:0;font-size:11.5px}.pick label.q input{display:inline-block;width:70px;margin-left:6px}
.spd small.how{display:block;font-size:10px;color:var(--faint);margin-top:3px;letter-spacing:.04em}
.tile.pred{border-style:dashed;background:transparent;box-shadow:none}
.fit b{font-weight:400;color:var(--ink)}.fit .no{color:var(--red)}
.summary{padding:0}.sumrow{display:grid;grid-template-columns:repeat(5,1fr)}
.sumrow>div{padding:16px 20px;border-right:1px dashed var(--amber-faint)}.sumrow>div:last-child{border-right:0}
.sumrow b{display:block;font:600 19px "IBM Plex Sans Condensed";margin:4px 0 2px}.sumrow span.amb,.sumrow span.muted{font-size:12.5px}
.layout{display:grid;grid-template-columns:250px minmax(0,1fr);gap:20px}
.box dt{font-size:10px;letter-spacing:.18em;color:var(--muted);margin-top:10px}.box dd b{color:var(--amber);font-weight:500}
.sec{border-top:1px dashed var(--amber-faint);margin-top:16px;padding-top:12px}
.wts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.wts span{font-size:11.5px;border:1px solid var(--amber-faint);padding:3px 8px;color:var(--muted)}
.wts span.on{border-color:var(--amber);color:var(--amber)}
.feed ul{list-style:none}.feed li{font-size:12px;padding:7px 0;border-bottom:1px dashed var(--amber-faint);color:#cfcabb}
.feed .when{display:block;font-size:10.5px;color:var(--faint)}
.chart{padding:14px 18px}.scatter{width:100%;height:auto;display:block}
.xtabs{display:flex;gap:0;margin:4px 0 8px}.xtabs span{font-size:11.5px;letter-spacing:.08em;border:1px solid var(--amber-faint);padding:5px 12px;color:var(--muted);margin-right:-1px}
.xtabs span.on{color:var(--bg);background:var(--amber);border-color:var(--amber)}
.rank th{padding:10px 4px;font-size:10.5px;letter-spacing:.1em}.rank td{padding:10px 4px}.rank td.l{padding-left:10px}
.rank td.mod{width:190px}.rank td.num{font-size:15px;color:var(--ink)}.rank td.fit{font-size:11.5px;line-height:1.35;min-width:96px}.rank td.l .m{white-space:nowrap}.rank .tile{min-width:46px;font-size:15px;padding:5px 4px 3px}.rank .tile.big{min-width:74px;font-size:20px}
td.rk{color:var(--muted);font-size:13px;width:34px}.rkr{font-size:15px;color:var(--ink)}
.cmpbox{font-size:11px;color:var(--muted);display:flex;gap:6px;align-items:center;margin-top:3px}.cmpbox i{width:10px;height:10px;border:1px solid var(--amber-dim);display:inline-block}
.live{font-size:11px;letter-spacing:.14em;color:#ffd27a;animation:blink 1.4s steps(2) infinite}@keyframes blink{50%{opacity:.45}}
.prog{width:140px;height:5px;border:1px solid var(--amber-dim);display:inline-block;vertical-align:middle;margin-left:8px}.prog i{display:block;height:100%;background:var(--amber)}
.cmpbar{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-top:1px solid var(--amber-dim);font-size:12px;color:var(--muted)}
"""

_JS = r"""
const $ = s => document.querySelector(s);
const fmt = v => v >= 100 ? v.toFixed(0) : v.toFixed(0);
function plan(sh, hw, ctx, depth) {
  const mib = 1 / 1048576, kv = sh.kvB * ctx + sh.rec, gpuFixed = (sh.nonexp + kv) * mib + 2100 + 700, free = hw.vram - gpuFixed;
  let gf, ramUsed, fits, perCpu, perGpu;
  if (!sh.moe) { const need = gpuFixed + sh.embed * mib; fits = need <= hw.vram; gf = 1; ramUsed = sh.embed * mib; perGpu = sh.nonexp; perCpu = 0; }
  else { gf = Math.max(0, Math.min(1, free / (sh.exp * mib))); const cpuExp = sh.exp * (1 - gf); ramUsed = (cpuExp + sh.embed) * mib;
         fits = free > -1 && ramUsed + 4096 <= hw.ram; const fr = sh.nUsed / sh.nExp; perCpu = cpuExp * fr; perGpu = sh.nonexp + sh.exp * gf * fr; }
  const tps = d => 1 / (perCpu / (hw.rambw * 1e9 * 0.8 * sh.cpuEff) + (perGpu + sh.kvB * d) / (hw.vrambw * 1e9 * 0.75) + sh.layers * 0.025 / 1000);
  return { fits, gf, ramUsed, t2: tps(2000), td: tps(Math.min(depth, ctx)) };
}
function forBox(sh, hw) {           // what `llmbox fit` would pick: the recipe's context, halved until it fits
  let ctx = sh.ctx, p = plan(sh, hw, ctx, sh.deepK * 1000);
  while (!p.fits && ctx > 8192) { ctx = ctx / 2; p = plan(sh, hw, ctx, sh.deepK * 1000); }
  return Object.assign(p, { ctx, t2: p.t2 * sh.k2, td: p.td * sh.kd });
}
function sameClass(hw) { const r = DATA.ref; return hw.gpu === r.gpu && Math.abs(hw.rambw - r.rambw) / r.rambw < 0.15 && hw.ram >= r.ram * 0.9; }
function tile(v, small, pred) { const c = v >= 85 ? "hi" : v >= 50 ? "mid" : "lo";
  return `<span class="tile ${c}${pred ? " pred" : ""}">${pred ? "~" : ""}${fmt(v)}<small>${small}</small></span>`; }
function scatter(pts, key) {
  const W = 640, H = 330, L = 58, B = 290, T = 20, R = 620, xmax = Math.max(60, ...pts.map(p => p[key] || 0)) * 1.25;
  const X = v => L + v / xmax * (R - L), Y = v => B - v / 100 * (B - T);
  let g = ""; for (let v = 0; v <= 100; v += 25) g += `<line x1="${L}" y1="${Y(v)}" x2="${R}" y2="${Y(v)}" stroke="#2a2c22" stroke-dasharray="2 4"/><text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${v}%</text>`;
  const step = xmax > 150 ? 50 : xmax > 60 ? 20 : 10;
  for (let v = 0; v <= xmax; v += step) g += `<line x1="${X(v)}" y1="${T}" x2="${X(v)}" y2="${B}" stroke="#2a2c22" stroke-dasharray="2 4"/><text x="${X(v)}" y="${B + 16}" text-anchor="middle">${v}</text>`;
  const qx = X(40), qy = Y(70);
  g += `<rect x="${qx}" y="${T}" width="${R - qx}" height="${qy - T}" fill="rgba(255,176,0,.06)" stroke="#7a5a14" stroke-dasharray="3 4"/><text x="${R - 6}" y="${T + 14}" text-anchor="end" fill="#b88419">best for your box</text>`;
  g += `<line x1="${L}" y1="${Y(100)}" x2="${R}" y2="${Y(100)}" stroke="#E8E4D8" stroke-opacity=".7" stroke-dasharray="6 4"/><text x="${R - 6}" y="${Y(100) - 6}" text-anchor="end" fill="#E8E4D8">frontier reference = 100%</text>`;
  const ok = pts.filter(p => p[key] && p.vs != null);
  const front = ok.filter(p => !ok.some(q => q[key] >= p[key] && q.vs >= p.vs && (q[key] > p[key] || q.vs > p.vs))).sort((a, b) => a[key] - b[key]);
  if (front.length > 1) g += `<polyline points="${front.map(p => X(p[key]) + "," + Y(p.vs)).join(" ")}" fill="none" stroke="#FFB000" stroke-dasharray="3 3"/>`;
  const used = [];
  for (const p of ok.sort((a, b) => b.vs - a.vs)) { const k = p.vs / p.cap, lo = p.ci[0] * k, hi = Math.min(100, p.ci[1] * k);
    let ly = Y(p.vs) + 4; while (used.some(u => Math.abs(u - ly) < 15)) ly += 15; used.push(ly);
    g += `<line x1="${X(p[key])}" y1="${Y(hi)}" x2="${X(p[key])}" y2="${Y(lo)}" stroke="#FFB000" stroke-opacity=".45" stroke-width="6"/>` +
         `<circle cx="${X(p[key])}" cy="${Y(p.vs)}" r="6" fill="${p.pred ? "none" : "#FFB000"}" stroke="#FFB000" stroke-width="2" filter="url(#g)"/>` +
         (X(p[key]) > R - 230 ? `<text x="${X(p[key]) - 11}" y="${ly}" text-anchor="end" fill="#E8E4D8" font-size="12">${p.id} · ${p.vs.toFixed(0)}%${p.pred ? " · predicted" : ""}</text>`
                               : `<text x="${X(p[key]) + 11}" y="${ly}" fill="#E8E4D8" font-size="12">${p.id} · ${p.vs.toFixed(0)}%${p.pred ? " · predicted" : ""}</text>`); }
  return `<svg viewBox="0 0 ${W} ${H + 16}" class="scatter" font-family="IBM Plex Mono" font-size="10" fill="#6c695f">${g}` +
         `<text x="${(L + R) / 2}" y="${H + 10}" text-anchor="middle" fill="#8b877b">decode tok/s on your box ${key === "t2" ? "at 2k" : "deep in the context"} →</text>` +
         `<text x="14" y="${T - 6}" fill="#8b877b">↑ % of frontier (bar = 95% CI)</text></svg>`;
}
let xkey = "t2", hwNow = null;
const measured = {};
document.querySelectorAll("tr[data-rid]").forEach(r => measured[r.dataset.rid] = r.querySelector(".spd").innerHTML);
const kfmt = c => `${Math.round(c / 1000)}k`;
function render() {
  const pts = DATA.points.map(p => Object.assign({}, p));
  let fastest = null;
  for (const p of pts) {
    const sh = DATA.recipes[p.id], row = document.querySelector(`tr[data-rid="${p.id}"]`);
    if (!sh || !row) continue;
    if (hwNow && !sameClass(hwNow)) {
      const f = forBox(sh, hwNow); p.t2 = f.t2; p.td = f.td; p.pred = true;
      row.querySelector(".spd").innerHTML = f.fits ? tile(f.t2, `${fmt(f.td)} deep`, true) + `<small class="how">predicted for your box</small>` : `<span class="tile lo">✗</span>`;
      row.querySelector(".fit").innerHTML = f.fits ? `<b>✓ ${kfmt(f.ctx)} ctx</b><br>${Math.round(f.gf * 100)}% experts on GPU` : `<span class="no">✗ needs more RAM/VRAM</span>`;
    } else {
      row.querySelector(".spd").innerHTML = measured[p.id];
      row.querySelector(".fit").innerHTML = `<b>✓ ${kfmt(sh.ctx)} ctx</b><br>${hwNow ? "measured on this class" : "reference box"}`;
    }
    if (p.t2 && (!fastest || p.t2 > fastest.t2)) fastest = p;
  }
  if (fastest && $("#fastest")) $("#fastest").textContent = `${fmt(fastest.t2)} tok/s${fastest.pred ? " (predicted)" : ""}`;
  $("#scatter").innerHTML = scatter(pts, xkey);
}
function readBox() {
  const g = DATA.gpus.find(x => x[0] === $("#gpu").value);
  if (!g) { hwNow = null; render(); return; }
  const bw = parseFloat($("#bwn").value) || parseFloat($("#bw").value);
  hwNow = { gpu: g[0].replace(/ \d+ GB$/, ""), vram: g[1], vrambw: g[2], ram: parseInt($("#ram").value) * 1024, rambw: bw };
  $("#boxlbl").textContent = `YOUR BOX: ${g[0]} · ${$("#ram").value} GB @ ${bw} GB/s`;
  $("#boxnote").textContent = sameClass(hwNow) ? "Same class as the reference box: the numbers are measured." :
    "Speeds and fit are predicted for your box from each model file and your memory bandwidth, calibrated on measured runs (dashed = predicted).";
  try { localStorage.setItem("llmbox-box", JSON.stringify({ gpu: $("#gpu").value, ram: $("#ram").value, bw: $("#bw").value, bwn: $("#bwn").value })); } catch (e) {}
  location.hash = `gpu=${encodeURIComponent(g[0])}&ram=${$("#ram").value}&bw=${bw}`;
  render();
}
for (const g of DATA.gpus) $("#gpu").insertAdjacentHTML("beforeend", `<option>${g[0]}</option>`);
for (const r of DATA.ramKinds) $("#bw").insertAdjacentHTML("beforeend", `<option value="${r[1]}">${r[0]} ~${r[1]} GB/s</option>`);
["#gpu", "#ram", "#bwn"].forEach(s => $(s).addEventListener("change", readBox));
$("#bw").addEventListener("change", () => { $("#bwn").value = ""; readBox(); });   // a preset replaces a typed-in measurement
document.querySelectorAll(".xtabs span").forEach(t => t.addEventListener("click", () => {
  document.querySelectorAll(".xtabs span").forEach(u => u.classList.remove("on")); t.classList.add("on"); xkey = t.dataset.x; render(); }));
try { const h = Object.fromEntries(new URLSearchParams(location.hash.slice(1))); const saved = h.gpu ? h : JSON.parse(localStorage.getItem("llmbox-box") || "null");
  if (saved && saved.gpu) { $("#gpu").value = saved.gpu; if (saved.ram) $("#ram").value = saved.ram; if (saved.bw) { const o = [...$("#bw").options].find(o => o.value == saved.bw); if (o) $("#bw").value = saved.bw; else $("#bwn").value = saved.bw; } if (saved.bwn) $("#bwn").value = saved.bwn; readBox(); } else render();
} catch (e) { render(); }
"""


# ---------------------------------------------------------------------------------------------------------------------
# every page: records -> HTML. Flat folder, simple relative links: index, recipe-<id>, run-<id8>, hardware-<id>, compare-<a>-vs-<b>

PLAN_JS = r"""
function plan(sh, hw, ctx, depth) {
  const mib = 1 / 1048576, kv = sh.kvB * ctx + sh.rec, gpuFixed = (sh.nonexp + kv) * mib + 2100 + 700, free = hw.vram - gpuFixed;
  let gf, ramUsed, fits, perCpu, perGpu;
  if (!sh.moe) { const need = gpuFixed + sh.embed * mib; fits = need <= hw.vram; gf = 1; ramUsed = sh.embed * mib; perGpu = sh.nonexp; perCpu = 0; }
  else { gf = Math.max(0, Math.min(1, free / (sh.exp * mib))); const cpuExp = sh.exp * (1 - gf); ramUsed = (cpuExp + sh.embed) * mib;
         fits = free > -1 && ramUsed + 4096 <= hw.ram; const fr = sh.nUsed / sh.nExp; perCpu = cpuExp * fr; perGpu = sh.nonexp + sh.exp * gf * fr; }
  const tps = d => 1 / (perCpu / (hw.rambw * 1e9 * 0.8 * sh.cpuEff) + (perGpu + sh.kvB * d) / (hw.vrambw * 1e9 * 0.75) + sh.layers * 0.025 / 1000);
  return { fits, gf, ramUsed, vram: Math.min(hw.vram, gpuFixed + (sh.moe ? sh.exp * gf * mib : sh.embed * mib)), t2: tps(2000), td: tps(Math.min(depth, ctx)) };
}
function forBox(sh, hw) {
  let ctx = sh.ctx, p = plan(sh, hw, ctx, sh.deepK * 1000);
  while (!p.fits && ctx > 8192) { ctx = ctx / 2; p = plan(sh, hw, ctx, sh.deepK * 1000); }
  return Object.assign(p, { ctx, t2: p.t2 * sh.k2, td: p.td * sh.kd });
}
function savedBox(DATA) {
  try { const s = JSON.parse(localStorage.getItem("llmbox-box") || "null"); if (!s || !s.gpu) return null;
    const g = DATA.gpus.find(x => x[0] === s.gpu); if (!g) return null;
    return { name: s.gpu, gpu: g[0].replace(/ \d+ GB$/, ""), vram: g[1], vrambw: g[2], ram: parseInt(s.ram) * 1024, rambw: parseFloat(s.bwn) || parseFloat(s.bw) }; } catch (e) { return null; }
}
"""
TAB_LINKS = {"MODELS": "index.html", "RECIPES": None, "HARDWARE": None, "COMPARE": None, "METHODOLOGY": "#"}


def _page(title: str, tab: str, body: str, css: str = "", js: str = "", links: dict | None = None) -> str:
    links = dict(TAB_LINKS, **(links or {}))
    nav = "".join(f'<a class="{"on" if t == tab else ""}" href="{esc(links.get(t) or "#")}">{t}</a>' for t in ("MODELS", "RECIPES", "HARDWARE", "COMPARE", "METHODOLOGY"))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><link rel="stylesheet" href="osc.css"><style>{_PAGES_CSS}{css}</style></head><body>'
            '<svg width="0" height="0" style="position:absolute"><defs><filter id="g"><feGaussianBlur stdDeviation="1.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs></svg>'
            '<div class="wrap"><header class="plate"><a class="brand glow" href="index.html" style="text-decoration:none">LLMBOX<small>LOCAL LLM BENCHMARK</small></a>'
            '<div class="search">Search: tiel, qwen3.8, 12 GB …</div></header>'
            f'<nav class="tabs">{nav}<span class="sp"></span><a class="cta" href="#">TEST YOUR BOX ↓</a></nav>{body}'
            f'<footer><span>Every number on this page comes from a saved run record; nothing is typed by hand.</span><span>generated {time.strftime("%b %d, %Y %H:%M")}</span></footer>'
            f'</div>{f"<script>{js}</script>" if js else ""}</body></html>')


def load_records(host: str, suite_version: str, tier: str) -> dict:
    """Newest suite record per recipe id for this host and suite, plus the frontier reference. Keeps the file path."""
    import json as _json
    out, ref = {}, None
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
            if rec.get("kind") != "suite" or su.get("version") != suite_version or su.get("tier") != tier or su.get("blocks"):
                continue
            rec["_path"] = os.path.join(d, f)
            rid = (rec.get("recipe") or {}).get("id")
            if h == "cloud":
                if not ref or rec["summary"]["capability"] > ref["summary"]["capability"]:
                    ref = rec
            elif rid:
                out[rid] = rec   # sorted by name = by time: the newest wins
    return {"local": out, "ref": ref}


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


def _telemetry_panel(t: dict | None, title: str = "HARDWARE DURING THE RUN") -> str:
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
    src = f' · {esc(t["source"])}' if t.get("source") else ""
    return (f'<section class="panel"><div class="lbl">{title} · {t.get("samples")} SAMPLES{src.upper()}</div>'
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
        labels.append(f'<div class="ax tip" style="left:calc({left:.1f}% - 40px);top:{y - 14:.0f}px">{LABEL[b]}<b class="{"r" if v is not None and v < 50 else ""}">{v:.0f}</b>'
                      f'<span class="pop"><b>{esc(title)}</b>{esc(text)}<ul>{ls}</ul></span></div>')
    return f'<div class="starw">{svg}{"".join(labels)}<div class="lgd"><span class="amb">━</span> this recipe &nbsp; <span>┅</span> frontier reference</div></div>'


def _depth_chart(series: list[tuple[str, dict, bool]]) -> str:
    """decode tok/s by context depth for several recipes (log x); the first one is highlighted."""
    import math
    X = lambda k: 60 + (math.log(max(k, 1)) / math.log(262)) * 500
    allv = [d["decode_tps"] for _, bd, _ in series for d in bd.values() if d.get("decode_tps")]
    ymax = max(allv + [60]) * 1.25
    Y = lambda t: 200 - t / ymax * 170
    g = "".join(f'<line x1="60" y1="{Y(v):.1f}" x2="560" y2="{Y(v):.1f}" stroke="#2a2c22" stroke-dasharray="2 4"/><text x="52" y="{Y(v)+4:.1f}" text-anchor="end">{v:.0f}</text>'
                for v in [ymax * f / 4 for f in range(5)])
    g += "".join(f'<line x1="{X(k):.1f}" y1="30" x2="{X(k):.1f}" y2="200" stroke="#2a2c22" stroke-dasharray="2 4"/><text x="{X(k):.1f}" y="218" text-anchor="middle">{k}k</text>' for k in (2, 8, 28, 88, 262))
    lines = []
    for n, (rid, bd, hl) in enumerate(series):
        pts = sorted((report._depth_k(k), d["decode_tps"]) for k, d in bd.items() if d.get("decode_tps"))
        if not pts:
            continue
        pl = " ".join(f"{X(k):.1f},{Y(t):.1f}" for k, t in pts)
        if hl:
            lines.append(f'<polyline points="{pl}" fill="none" stroke="#FFB000" stroke-width="2.4" filter="url(#g)"/>'
                         + "".join(f'<circle cx="{X(k):.1f}" cy="{Y(t):.1f}" r="3.6" fill="#FFB000"/><text x="{X(k)+6:.1f}" y="{Y(t)-8:.1f}" fill="#E8E4D8" font-size="12">{t:.1f}</text>' for k, t in pts))
        else:
            d = 100 * (pts[-1][1] / pts[0][1] - 1)
            lines.append(f'<polyline points="{pl}" fill="none" stroke="#b9b4a6" stroke-opacity=".55" stroke-width="1.2"/>'
                         f'<text x="{X(pts[-1][0])+6:.1f}" y="{Y(pts[-1][1])+4:.1f}" fill="#8b877b">{esc(rid)} {d:+.0f}%</text>')
    return (f'<svg viewBox="0 0 600 236" font-family="IBM Plex Mono" font-size="10" fill="#6c695f">{g}{"".join(lines[::-1])}'
            '<text x="60" y="234" fill="#8b877b">context already in the conversation (tokens, log scale)</text><text x="14" y="22" fill="#8b877b">tok/s</text></svg>')


def recipe_page(rid: str, rec: dict, ref: dict | None, others: dict, ranks: dict, flags: dict, n_measured: int, n_total: int) -> str:
    s, sp = rec["summary"], rec["summary"]["speed"]
    bd = sp.get("by_depth") or {}
    vs = _vs(rec, ref)
    depths = sorted((report._depth_k(k), d) for k, d in bd.items())
    deep = depths[-1] if depths else None
    d_pct = 100 * (deep[1]["decode_tps"] / depths[0][1]["decode_tps"] - 1) if len(depths) > 1 else None
    k28 = next((d for k, d in depths if k >= 24), None)
    ttft2 = 2000 / depths[0][1]["prefill_tps"] if depths and depths[0][1].get("prefill_tps") else None
    rows = rec.get("rows", [])
    n_cut = sum(1 for f in flags.values() if f["cut"])
    n_cut_ok = sum(1 for r in rows if flags.get(r["id"], {}).get("cut") and r["score"] >= 0.99)
    n_loop = sum(1 for f in flags.values() if f["loop"])
    lost = {}
    for r in rows:
        if r["score"] < 0.99:
            f = flags.get(r["id"], {})
            why = " — thinking looped" if f.get("loop") else " — ran out of thinking room" if f.get("cut") else ""
            lost.setdefault(r["block"], []).append(f'{r["kind"].replace("_", " ")} {r["id"].rsplit(".", 2)[-2]} ({r["score"]*100:.0f}){why}')
    lo, hi = ranks.get(rid, (1, 1))
    rk = f"{lo}" if lo == hi else f"{lo}–{hi}"
    m = rec.get("model") or {}
    rcp = rec.get("recipe") or {}
    lines = [
        ("Capability", f'{s["capability"]:.1f}', f'95% CI {s["capability_ci95"][0]:.0f}–{s["capability_ci95"][1]:.0f} · ' + " · ".join(f'{LABEL[b].lower()} {s["blocks"].get(b, 0):.0f}' for b in BLOCKS), _verdict(vs), vs is not None and vs >= 50),
        ("Speed on the reference box", f'{sp.get("decode_tps")} tok/s', f'prefill {sp.get("prefill_tps") or 0:,.0f} tok/s · typical agent step {sp.get("typical_agent_step_s")} s', sp.get("label") or "", True),
        ("Deep context", f"{d_pct:+.0f}%" if d_pct is not None else "—", " · ".join(f'{k:.0f}k {d["decode_tps"]}' for k, d in depths) + " tok/s", "steady" if (d_pct or 0) > -15 else "slows down", (d_pct or 0) > -15),
        ("First word", f"{ttft2:.1f} s" if ttft2 else "—", (f"at 2k context · {28000 / k28['prefill_tps']:.1f} s at 28k" if k28 and k28.get("prefill_tps") else "at 2k context") + (f" · {deep[0]*1000/deep[1]['prefill_tps']:.0f} s to read a fresh {deep[0]:.0f}k document" if deep and deep[1].get("prefill_tps") else ""), "quick" if (ttft2 or 9) < 2 else "slow", (ttft2 or 9) < 2),
        ("Reliability", f"{n_cut + n_loop} flags / {len(rows)}", f"{n_cut} replies cut at the reasoning reserve ({n_cut_ok} still solved) · {n_loop} reasoning loops · {s.get('errors', 0)} errors", "clean" if n_cut + n_loop == 0 else "thinking issues", n_cut + n_loop == 0),
        ("Efficiency", f'{s["solved_per_hour"]} solved/h', f'{s["solved"]} of {s["items"]} tasks in {s["wall_minutes"]} min', "", True),
    ]
    line_html = "".join(f'<div class="line"><div class="h"><span class="k">{k}<em class="{"" if ok else "r"}">{esc(v)}</em></span>{f'<span class="v {"ok" if ok else "bad"}">{esc(vd)}</span>' if vd else ""}</div><div class="s">{esc(sub)}</div></div>' for k, v, sub, vd, ok in lines)
    strong = sorted(((v, b) for b, v in s["blocks"].items() if v >= 85), reverse=True)[:3]
    weak = sorted((v, b) for b, v in s["blocks"].items() if v < 85)[:3]
    sw = ('<div class="sw"><div><div class="sc">Strengths</div><ul>' + ("".join(f'<li><b>{TIPS[b][0].split(" ·")[0]} {v:.0f}</b></li>' for v, b in strong) or "<li class='q'>none above 85 yet</li>")
          + '</ul></div><div class="w"><div class="sc">Weaknesses</div><ul>'
          + ("".join(f'<li><b>{TIPS[b][0].split(" ·")[0]} {v:.0f}</b> · lost: {esc("; ".join(lost.get(b, [])[:2]))}</li>' for v, b in weak) or "<li class='q'>none below 85</li>") + "</ul></div></div>")
    series = [(rid, bd, True)] + [(o, r["summary"]["speed"].get("by_depth") or {}, False) for o, r in others.items() if o != rid]
    dtable = "".join(f'<tr><td class="l">{k:.0f}k</td><td>{_tile(d["decode_tps"]) if d.get("decode_tps") else "—"}</td><td>{f"{d['prefill_tps']:,.0f}" if d.get("prefill_tps") else "—"}</td>'
                     f'<td>{(k*1000/d["prefill_tps"]):.1f} s</td></tr>' for k, d in depths if d.get("prefill_tps"))
    rt = rec.get("runtime") or {}
    notes = (rcp.get("notes") or {}).get("lines", [])
    sam = rcp.get("sampling") or {}
    al = rcp.get("antiloop") or {}
    pl = rcp.get("placement") or {}
    spc = rcp.get("speculative") or {}
    chat = (rcp.get("chat") or {}).get("template_kwargs") or {}
    portable = [("SAMPLING", " · ".join(f"{k.replace('_', '-')} {v}" for k, v in sam.items() if k != "max_tokens") or "server defaults"),
                ("TEMPLATE", " · ".join(f"{k} {v}" for k, v in chat.items()) or "model default"),
                ("REASONING", (f'reserve {al.get("reasoning_budget")}' if (al.get("reasoning_budget") or -1) >= 0 else "no budget") + (f' · loop detector {al["reasoning_loop"]}' if al.get("reasoning_loop") else "")),
                ("LOOPS", (f'marker bias −{al.get("marker_bias")} on {len(al.get("marker_ids") or [])} tokens' if al.get("marker_ids") else "none") + (f' · DRY in thinking, allowed {al.get("dry_allowed_length")}' if al.get("dry_think_only") else "")),
                ("KV CACHE", pl.get("kv_type", "?")),
                ("SPECULATIVE", f'{spc.get("type")} · draft {spc.get("draft_max")}' if spc.get("type") else "off")]
    hwrows = [("CONTEXT", f'{round((pl.get("ctx") or 0) / 1000)}k'), ("BATCH", f'{pl.get("batch")} / ubatch {pl.get("ubatch")}'), ("THREADS", f'{(rcp.get("runtime") or {}).get("threads")} on cores {(rcp.get("runtime") or {}).get("cpu_affinity") or "any"}')]
    recipe_html = ('<div class="cmdl"><code>llmbox install ' + esc(rid) + '</code><span class="q">download · fit to your box · register</span></div><div class="rgrid"><div><div class="sc" style="margin-bottom:6px">Travels with the recipe · carries the score</div><dl>'
                   + "".join(f"<dt>{k}</dt><dd class='val'>{esc(v)}</dd>" for k, v in portable)
                   + '</dl></div><div><div class="sc" style="margin-bottom:6px">Fitted to each box by llmbox fit · speed only · here: reference box</div><dl>'
                   + "".join(f"<dt>{k}</dt><dd>{esc(v)}</dd>" for k, v in hwrows)
                   + "</dl></div></div>" + (f'<div class="notes"><div class="sc">Why these settings (from the recipe)</div><ul>{"".join(f"<li>{esc(n)}</li>" for n in notes)}</ul></div>' if notes else ""))
    runs_html = (f'<table><tr><th class="l">RUN</th><th class="l">BOX · SETTINGS THAT DIFFER FROM THE RECIPE</th><th>OF FRONTIER</th><th>TOK/S 2k</th><th>DEEP</th><th></th></tr>'
                 f'<tr><td class="l"><a class="who" href="run-{rec["id"][:8]}.html">{rec["id"][:8]}</a><br><span class="q">{_ago(rec.get("created", ""))}</span></td>'
                 f'<td class="l cfg">{esc((rec["host"].get("gpu") or "").replace("NVIDIA GeForce ", ""))} · {rec["host"].get("ram_gib")} GB · {rec["host"].get("ram_read_gbs")} GB/s · llama.cpp {esc(rt.get("llama_cpp_build") or "?")}<br>{esc(rt.get("variant") or "not recorded")}</td>'
                 f'<td>{_tile(vs, f"{s["capability"]:.1f}")}</td><td>{_tile(sp.get("decode_tps"))}</td><td>{report._deep(sp)}</td><td><a class="btn" href="run-{rec["id"][:8]}.html">OPEN</a></td></tr></table>')
    body = f'''
<section class="panel title"><div><div class="org">{esc(m.get("hf_repo") or "")}</div><h1>{esc(rid)} <span class="muted" style="font-weight:500">· {esc(_quant(m.get("file")))}</span></h1>
 <div class="meta">{esc(rcp.get("description") or "")}</div><div class="meta">FILE <b>{esc(m.get("file") or "?")}</b>{f' · {m["bytes"]/1e9:.1f} GB' if m.get("bytes") else ""} · SHA256 <b>{esc((m.get("sha256") or "not recorded")[:16])}</b> · SUITE <b>v{esc(rec["suite"]["version"])} {esc(rec["suite"]["tier"])}</b></div></div>
 <div class="acts"><span class="btn solid">$ llmbox install {esc(rid)}</span><a class="btn" href="hardware-{esc(rid)}.html">HARDWARE</a></div></section>
<section class="panel"><div class="lbl">SCORE · MEASURED ON THE REFERENCE BOX · QUALITY IS THE SAME ON ANY BOX</div><div class="top">
 <div class="score"><div class="n glow">{f"{vs:.0f}" if vs is not None else "—"}<small>%</small></div><div class="of">OF FRONTIER</div><div class="rk">rank {rk} of {n_measured} measured</div>
  <div class="faint" style="font-size:10.5px;margin-top:3px">{n_total - n_measured} recipes still in the queue</div><div class="conf">confirmed by<b>1 run</b><span>more runs narrow the interval</span></div></div>
 <div class="lines">{line_html}</div>
 <div>{_star(s["blocks"], (ref or {}).get("summary", {}).get("blocks") or {}, lost)}</div></div></section>
<section class="panel"><div class="lbl">SPEED BY CONTEXT DEPTH · REFERENCE BOX · SINGLE STREAM · REAL CODE</div><div class="depth"><div class="dc">{_depth_chart(series)}</div>
 <div class="dt"><table><tr><th class="l">DEPTH</th><th>DECODE</th><th>PREFILL</th><th>READ WHOLE<br>CONTEXT</th></tr>{dtable}</table>
 <p class="q" style="margin-top:12px">Your box's speed: pick it on the home page, or see <a href="hardware-{esc(rid)}.html">hardware for this recipe</a>.</p></div></div>{sw}</section>
{_telemetry_panel(rec.get("telemetry"))}
<section class="panel recipe"><div class="lbl">RECIPE · WHAT EACH SETTING IS</div>{recipe_html}</section>
<section class="panel runs"><div class="lbl">RUNS OF THIS RECIPE · WITH THE SETTINGS EACH ONE USED</div>{runs_html}</section>'''
    return _page(f"llmbox · {rid}", "RECIPES", body, _RECIPE_CSS, links={"RECIPES": f"recipe-{rid}.html", "HARDWARE": f"hardware-{rid}.html"})


def run_page(rid: str, rec: dict, ref: dict | None, flags: dict) -> str:
    s, sp, h, m, rt = rec["summary"], rec["summary"]["speed"], rec["host"], rec.get("model") or {}, rec.get("runtime") or {}
    vs = _vs(rec, ref)
    bd = sorted((report._depth_k(k), d) for k, d in (sp.get("by_depth") or {}).items())
    body_rows = []
    for b in BLOCKS:
        items = [r for r in rec.get("rows", []) if r["block"] == b]
        if not items:
            continue
        body_rows.append(f'<tr class="grp"><td class="l" colspan="7">{TIPS[b][0].split(" ·")[0].upper()} · {s["blocks"].get(b, 0):.0f} · {len(items)} tasks</td></tr>')
        for r in items:
            f = flags.get(r["id"], {})
            fl = ('<span class="flag lo">CUT</span>' if f.get("cut") else "") + ('<span class="flag lo">LOOP</span>' if f.get("loop") else "")
            lvl = r["id"].rsplit(".", 2)[-2]
            body_rows.append(f'<tr><td class="l"><span class="m2">{esc(r["kind"].replace("_", " "))}</span> <span class="q">{lvl}</span>{" <span class=flag>EXPERT</span>" if lvl == "L6" else ""}</td>'
                             f'<td>{_tile(r["score"] * 100)}</td><td>{r["seconds"]:.0f} s</td><td>{r.get("steps") or 1}</td><td>{f.get("max_reply", 0):,}</td><td class="l">{fl or "<span class=q>—</span>"}</td><td class="q">{esc(r["id"])}</td></tr>')
    argv = rt.get("argv") or []
    diff = rt.get("diff_vs_recipe")
    diff_html = ("<span class='v ok'>identical to the recipe</span>" if diff == [] else
                 "".join(f"<div><span class='flag {'lo' if d['class'] == 'quality' else ''}'>{d['class'].upper()}</span> {esc(d['flag'])}: recipe {esc(' '.join(d['recipe']) or '—')} → run {esc(' '.join(d['run']) or '—')}</div>" for d in diff) if diff
                 else "<span class='q'>not recorded for this run</span>")
    body = f'''
<section class="panel hd"><div><div class="sc">Benchmark run</div><h1><a href="recipe-{esc(rid)}.html">{esc(rid)}</a> on {esc((h.get("gpu") or "").replace("NVIDIA GeForce ", ""))} · {h.get("ram_gib")} GB</h1>
 <div class="id">run <b>{rec["id"][:8]}</b> · {esc(rec.get("created", "")[:16].replace("T", " "))} · suite v{esc(rec["suite"]["version"])} {esc(rec["suite"]["tier"])}, hash {esc(rec["suite"].get("content_hash") or "?")} · {s["wall_minutes"]} min</div></div>
 <div class="acts"><span class="btn">COMPARE WITH MY RUN</span><span class="btn">COPY SETTINGS</span><span class="btn">JSON ↓</span></div></section>
<section class="panel sum">
 <div><b>{f"{vs:.0f}%" if vs is not None else "—"}</b><span>of frontier · {s["capability"]:.1f} ({s["capability_ci95"][0]:.0f}–{s["capability_ci95"][1]:.0f})</span></div>
 <div><b class="w">{s["solved"]}</b><span>of {s["items"]} tasks solved</span></div>
 <div><b>{sp.get("decode_tps")}</b><span>tok/s at 2k · {report._deep(sp)} deep</span></div>
 <div><b class="w">{f"{2000/bd[0][1]['prefill_tps']:.1f} s" if bd and bd[0][1].get("prefill_tps") else "—"}</b><span>first word at 2k</span></div>
 <div><b class="w">{s["solved_per_hour"]}</b><span>solved tasks per hour</span></div>
 <div><b class="w" style="color:var(--red)">{sum(1 for f in flags.values() if f["cut"]) + sum(1 for f in flags.values() if f["loop"])}</b><span>thinking flags: {sum(1 for f in flags.values() if f["cut"])} cut, {sum(1 for f in flags.values() if f["loop"])} loop</span></div></section>
<div class="two"><section class="panel sys"><div class="lbl">SYSTEM · FINGERPRINT {esc(h.get("id") or "?")}</div><dl>
 <dt>GPU</dt><dd>{esc(h.get("gpu"))} · {h.get("vram_gib")} GiB</dd><dt>DRIVER</dt><dd>{esc(h.get("gpu_driver"))} · power limit {esc(h.get("gpu_power_limit_w"))} W</dd>
 <dt>CPU</dt><dd>{esc(h.get("cpu"))}</dd><dt>RAM</dt><dd>{h.get("ram_gib")} GiB · measured {h.get("ram_read_gbs")} GB/s read</dd><dt>OS</dt><dd>{esc(h.get("os"))}</dd>
 <dt>RUNTIME</dt><dd>llama.cpp {esc(rt.get("llama_cpp_build") or "not recorded")}</dd><dt>MODEL</dt><dd>{esc(m.get("hf_repo") or "")}<br><span class="q">{esc(m.get("file") or "")}</span></dd>
 <dt>SHA256</dt><dd class="{"" if m.get("sha256") else "todo"}">{esc(m.get("sha256") or "not recorded")}</dd></dl></section>
 <section class="panel"><div class="lbl">SETTINGS USED · FROM THE SERVER'S COMMAND LINE AND /props{(" · " + esc(rt["source"].split(":")[0].upper())) if rt.get("source") else ""}</div>
  <div class="argv">{esc(" ".join(argv)) if argv else "<span class=q>not recorded for this run</span>"}</div>
  <div class="diff">{diff_html}{" <span class='q'>· consistent start to end</span>" if rt.get("settings_consistent") else ""}</div></section></div>
{_telemetry_panel(rec.get("telemetry"))}
<section class="panel tasks"><div class="lbl">EVERY TASK · SCORE · TIME · LONGEST REPLY · THINKING FLAGS</div>
 <table><tr><th class="l">TASK</th><th>SCORE</th><th>TIME</th><th>STEPS</th><th>LONGEST REPLY</th><th class="l">FLAGS</th><th>ID</th></tr>{"".join(body_rows)}</table></section>'''
    return _page(f"llmbox · run {rec['id'][:8]} · {rid}", "RECIPES", body, _RUN_CSS, links={"RECIPES": f"recipe-{rid}.html", "HARDWARE": f"hardware-{rid}.html"})


def hardware_page(rid: str, rec: dict, shape: dict, data: dict) -> str:
    """Measured boxes for this recipe + predictions for common boxes; the upgrade advisor is computed in the page for
    the visitor's box (saved by the picker on the home page), else for the reference box."""
    import json as _json
    body = f'''
<section class="panel hd"><div><div class="sc">Hardware</div><h1>Which box runs <a href="recipe-{esc(rid)}.html">{esc(rid)}</a> best?</h1>
 <p class="q" style="max-width:860px;margin-top:6px">Measured runs first, then predictions from the model file's header and each box's memory bandwidth, calibrated on the measured run. Quality does not depend on the box; only speed does.</p></div></section>
<section class="panel"><div class="lbl">UPGRADE ADVISOR · <span id="advbox">FOR THE REFERENCE BOX</span></div><div class="adv" id="adv"></div>
 <div class="why"><b>Why:</b> a mixture-of-experts model reads only its active experts per token, but which ones changes every token. Whatever does not fit in VRAM is read from system RAM, so on small cards RAM bandwidth, not the GPU, sets the pace. Measure yours with <b>llmbox host add</b>; pick your box on the home page.</div></section>
<section class="panel"><div class="lbl">BOXES · SORTED BY SPEED AT 2k · MEASURED SOLID, PREDICTED DASHED</div><table id="boxes"></table></section>'''
    js = PLAN_JS + f"\nconst DATA = {_json.dumps(dict(data, sh=shape, measured={'gpu': data['ref']['gpu'], 'ram': data['ref']['ram'], 'rambw': data['ref']['rambw'], 't2': rec['summary']['speed'].get('decode_tps'), 'td': float(report._deep(rec['summary']['speed'])) if report._deep(rec['summary']['speed']) != '-' else None}))};\n" + _HW_JS
    return _page(f"llmbox · hardware for {rid}", "HARDWARE", body, _HW_CSS, js, links={"RECIPES": f"recipe-{rid}.html", "HARDWARE": f"hardware-{rid}.html"})


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
    row("tok/s at 2k (reference box)", sa["speed"].get("decode_tps"), sb["speed"].get("decode_tps"), lambda v: f"{v:.1f}")
    dpa, dpb = report._deep(sa["speed"]), report._deep(sb["speed"])
    row("tok/s deep", float(dpa) if dpa != "-" else None, float(dpb) if dpb != "-" else None, lambda v: f"{v:.1f}")
    row("Solved tasks / hour", sa.get("solved_per_hour"), sb.get("solved_per_hour"), lambda v: f"{v:.1f}")
    ta, tb = (ra.get("telemetry") or {}), (rb.get("telemetry") or {})
    g = lambda t, k: (t.get(k) or {}).get("avg")
    row("VRAM used (GB)", (g(ta, "vram_used_mib") or 0) / 1024 or None, (g(tb, "vram_used_mib") or 0) / 1024 or None, lambda v: f"{v:.1f}", higher=False)
    row("GPU power avg (W)", g(ta, "gpu_power_w"), g(tb, "gpu_power_w"), lambda v: f"{v:.0f}", higher=False)
    row("Thinking flags (cut + loop)", sum(f["cut"] + f["loop"] for f in fa.values()), sum(f["cut"] + f["loop"] for f in fb.values()), lambda v: f"{v}", higher=False)
    # settings that differ between the two recipes
    import json as _json
    flat = lambda d, p="": {k2: v2 for k, v in (d or {}).items() for k2, v2 in (flat(v, f"{p}{k}.") if isinstance(v, dict) else {f"{p}{k}": v}).items()}
    fa_, fb_ = flat({k: (ra.get("recipe") or {}).get(k) for k in ("sampling", "chat", "antiloop", "placement", "speculative")}), flat({k: (rb.get("recipe") or {}).get(k) for k in ("sampling", "chat", "antiloop", "placement", "speculative")})
    diffs = [(k, fa_.get(k), fb_.get(k)) for k in sorted(set(fa_) | set(fb_)) if fa_.get(k) != fb_.get(k) and not k.endswith("marker_ids")]
    only = lambda rows_, other: [r["kind"].replace("_", " ") + " " + r["id"].rsplit(".", 2)[-2] for r in rows_ if r["score"] < 0.99 and next((o["score"] for o in other if o["id"] == r["id"]), 0) >= 0.99]
    la, lb = only(ra.get("rows", []), rb.get("rows", [])), only(rb.get("rows", []), ra.get("rows", []))
    body = f'''
<section class="panel hd"><div><div class="sc">Compare</div><h1><a href="recipe-{esc(a)}.html">{esc(a)}</a> <span class="muted">vs</span> <a href="recipe-{esc(b)}.html">{esc(b)}</a></h1>
 <p class="q" style="margin-top:6px">Same 31 tasks, same reference box. Wins count rows where one is strictly better; differences inside the confidence intervals ({sa["capability_ci95"][0]:.0f}–{sa["capability_ci95"][1]:.0f} vs {sb["capability_ci95"][0]:.0f}–{sb["capability_ci95"][1]:.0f}) are noise.</p></div>
 <div class="score2"><div><b>{wins[a]}</b><span>{esc(a)}</span></div><div class="vs">:</div><div><b>{wins[b]}</b><span>{esc(b)}</span></div></div></section>
<div class="two2"><section class="panel"><div class="lbl">HEAD TO HEAD · HOVER A SCORE NAME</div><table class="h2h"><tr><th class="l"></th><th>{esc(a)}</th><th>{esc(b)}</th></tr>{"".join(rows)}</table></section>
<div><section class="panel"><div class="lbl">SPEED BY CONTEXT DEPTH</div><div class="pad0">{_depth_chart([(a, da, True), (b, db, False)])}</div></section>
<section class="panel pad"><div class="lbl">TASKS ONLY ONE OF THEM SOLVED</div><div class="only"><div><div class="sc">{esc(a)} solved, {esc(b)} did not</div><ul>{"".join(f"<li>{esc(x)}</li>" for x in lb) or "<li class=q>none</li>"}</ul></div>
 <div><div class="sc">{esc(b)} solved, {esc(a)} did not</div><ul>{"".join(f"<li>{esc(x)}</li>" for x in la) or "<li class=q>none</li>"}</ul></div></div></section></div></div>
<section class="panel"><div class="lbl">SETTINGS THAT DIFFER BETWEEN THE RECIPES</div><table><tr><th class="l">SETTING</th><th class="l">{esc(a)}</th><th class="l">{esc(b)}</th></tr>
 {"".join(f"<tr><td class='l q'>{esc(k)}</td><td class='l amb'>{esc(x)}</td><td class='l amb'>{esc(y)}</td></tr>" for k, x, y in diffs)}</table></section>'''
    return _page(f"llmbox · {a} vs {b}", "COMPARE", body, _CMP_CSS, links={"COMPARE": f"compare-{a}-vs-{b}.html"})


def build(out_dir: str, host: str = "box", suite_version: str = "0.9", tier: str = "quick") -> list[str]:
    """The whole site: home, a page per recipe, per run, hardware per recipe, compare per pair of measured recipes."""
    import itertools
    import json as _json
    out_dir = os.path.expanduser(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    written = [home(out_dir, host, suite_version, tier)]
    recs = load_records(host, suite_version, tier)
    local, ref = recs["local"], recs["ref"]
    rs = [r for r in report.rows(host, suite_version=suite_version, tier=tier) if r["host"].get("id") != "cloud" and not r.get("partial")]
    ranks = rank_ranges(rs)
    n_total = len(rs) + len([j for j in queue_state() if j["model"] not in local])
    data = shape_data(rs, host)
    flags = {rid: task_flags(rec) for rid, rec in local.items()}
    order = sorted(local, key=lambda k: -local[k]["summary"]["capability"])
    def w(name, html_):
        p = os.path.join(out_dir, name)
        open(p, "w").write(html_)
        written.append(p)
    for rid in order:
        rec = local[rid]
        w(f"recipe-{rid}.html", recipe_page(rid, rec, ref, local, ranks, flags[rid], len(local), n_total))
        w(f"run-{rec['id'][:8]}.html", run_page(rid, rec, ref, flags[rid]))
        if rid in data["recipes"]:
            w(f"hardware-{rid}.html", hardware_page(rid, rec, data["recipes"][rid], data))
    for a, b in itertools.combinations(order, 2):
        w(f"compare-{a}-vs-{b}.html", compare_page(a, b, local[a], local[b], ref, flags[a], flags[b]))
    return written


_PAGES_CSS = """
a{color:var(--amber)} .tabs a{text-decoration:none}
.title{display:grid;grid-template-columns:1fr auto;align-items:end;padding:20px 22px 16px}.title .org{font-size:13px;color:var(--amber)}
.title h1,.hd h1{font:600 34px/1.05 "IBM Plex Sans Condensed"}.title h1 a,.hd h1 a{color:var(--ink);text-decoration:none;border-bottom:1px dotted var(--amber-dim)}
.title .meta{font-size:12px;color:var(--muted);margin-top:8px;letter-spacing:.06em}.title .meta b{color:var(--ink);font-weight:400}
.title .acts,.hd .acts{display:flex;gap:10px}
.hd{display:grid;grid-template-columns:1fr auto;align-items:end;padding:18px 22px}.hd .id{font-size:12px;color:var(--muted);margin-top:6px}.hd .id b{color:var(--amber);font-weight:400}
.tele{display:grid}.tele>div{padding:14px 16px;border-right:1px dashed var(--amber-faint)}.tele>div:last-child{border-right:0}
.tele .tv{font-size:22px;margin-top:2px}.tele .tv small{font-size:11px;color:var(--muted);margin-left:4px}
.spark{width:100%;height:44px;display:block;margin-top:6px}
.flag{font-size:10px;letter-spacing:.14em;border:1px solid var(--amber-dim);color:var(--amber);padding:1px 6px;margin-right:4px}.flag.lo{border-color:var(--red-dim);color:var(--red)}
.m2{font:600 14px "IBM Plex Sans Condensed";color:var(--ink)}
"""
_RECIPE_CSS = """
.top{display:grid;grid-template-columns:150px minmax(0,1fr) 420px}
.score{padding:24px 10px;text-align:center;border-right:1px dashed var(--amber-faint)}.score .n{font:300 64px/1 "IBM Plex Mono";color:var(--amber)}.score .n small{font-size:26px}
.score .of{font-size:11px;letter-spacing:.16em;color:var(--muted);margin-top:6px}.score .rk{margin-top:16px;font-size:12px}
.conf{margin-top:18px;font-size:11px;color:var(--muted);border-top:1px dashed var(--amber-faint);padding-top:12px}.conf b{display:block;font:400 18px "IBM Plex Mono";color:var(--ink)}.conf span{display:block;font-size:10px;color:var(--faint)}
.lines{padding:16px 22px;border-right:1px dashed var(--amber-faint)}.line{padding:8px 0;border-bottom:1px dashed var(--amber-faint)}.line:last-child{border-bottom:0}
.line .h{display:flex;justify-content:space-between;align-items:baseline}.line .k{font:600 16px "IBM Plex Sans Condensed"}
.line .k em{font-style:normal;font:400 14px "IBM Plex Mono";color:var(--amber);margin-left:10px}.line .k em.r{color:var(--red)}.line .s{font-size:11.5px;color:var(--muted);margin-top:2px}
.starw{position:relative;padding:18px 10px 10px}.starw svg{width:100%;height:auto;display:block}
.ax{position:absolute;width:80px;font-size:10.5px;letter-spacing:.12em;color:var(--muted);text-align:center;line-height:1.3}.ax>b{display:block;font:400 16px "IBM Plex Mono";color:var(--ink);letter-spacing:0}.ax>b.r{color:var(--red)}
.ax .pop{left:-120px;width:320px;text-align:left;letter-spacing:0;font-size:12.5px}.ax .pop b{display:block;font:500 11px "IBM Plex Mono";color:var(--amber);letter-spacing:.14em;text-transform:uppercase}
.lgd{text-align:center;font-size:11px;color:var(--muted);margin-top:22px}
.depth{display:grid;grid-template-columns:minmax(0,1fr) 400px}.depth .dc{padding:14px 18px;border-right:1px dashed var(--amber-faint)}.depth svg{width:100%;height:auto}.depth .dt{padding:14px 18px}
.sw{display:grid;grid-template-columns:1fr 1fr;border-top:1px dashed var(--amber-faint)}.sw>div{padding:14px 22px}.sw>div:first-child{border-right:1px dashed var(--amber-faint)}
.sw li{list-style:none;font-size:12.5px;line-height:1.7;color:#cfcabb}.sw li b{font-weight:500;color:var(--amber)}.sw .w li b{color:var(--red)}
.cmdl{display:flex;justify-content:space-between;align-items:center;padding:14px 22px;border-bottom:1px dashed var(--amber-faint)}.cmdl code{font:500 18px "IBM Plex Mono";color:var(--amber)}.cmdl code:before{content:"$ ";color:var(--muted)}
.rgrid{display:grid;grid-template-columns:1.3fr 1fr}.rgrid>div{padding:14px 22px}.rgrid>div:first-child{border-right:1px dashed var(--amber-faint)}
.rgrid dl{display:grid;grid-template-columns:120px 1fr;row-gap:7px;font-size:12.5px}.rgrid dt{color:var(--muted);font-size:11px;letter-spacing:.14em}.rgrid dd.val{color:var(--amber)}
.notes{padding:12px 22px 16px;border-top:1px dashed var(--amber-faint)}.notes li{list-style:none;font-size:12.5px;color:#cfcabb;padding:3px 0 3px 14px;position:relative}.notes li:before{content:"›";position:absolute;left:0;color:var(--amber)}
.runs .cfg{font-size:12px;color:#cfcabb}.runs .who{color:var(--amber)}
"""
_RUN_CSS = """
.sum{display:grid;grid-template-columns:repeat(6,1fr)}.sum>div{padding:16px 18px;border-right:1px dashed var(--amber-faint)}.sum>div:last-child{border-right:0}
.sum b{display:block;font:300 34px "IBM Plex Mono";color:var(--amber);line-height:1.1}.sum b.w{color:var(--ink)}.sum span{font-size:11.5px;color:var(--muted)}
.two{display:grid;grid-template-columns:430px minmax(0,1fr);gap:22px}
.sys dl{display:grid;grid-template-columns:110px 1fr;row-gap:7px;font-size:12.5px;padding:18px 22px}.sys dt{color:var(--muted);font-size:11px;letter-spacing:.14em}.sys dd{word-break:break-word}.sys dd.todo{color:var(--red)}
.argv{padding:16px 22px;font-size:12px;line-height:1.75;color:#cfcabb;word-break:break-word}.diff{padding:10px 22px 16px;border-top:1px dashed var(--amber-faint);font-size:12.5px}
.tasks td{padding:7px 8px;font-size:12.5px}.tasks .tile{min-width:46px;font-size:13px;padding:3px 4px 2px}
.tasks tr.grp td{background:rgba(255,176,0,.05);color:var(--amber);font-size:11px;letter-spacing:.18em;padding:9px 12px}
"""
_HW_CSS = """
.adv{display:grid;grid-template-columns:repeat(3,1fr)}.adv>div{padding:18px 22px;border-right:1px dashed var(--amber-faint)}.adv>div:last-child{border-right:0}
.adv h4{font:600 17px "IBM Plex Sans Condensed"}.adv .g{font:300 34px "IBM Plex Mono";color:var(--amber);margin:6px 0}.adv .g.no{color:var(--muted)}.adv p{font-size:12.5px;color:#cfcabb;line-height:1.55}
.why{padding:14px 22px;font-size:12.5px;color:#cfcabb;line-height:1.6;border-top:1px dashed var(--amber-faint)}.why b{color:var(--amber);font-weight:500}
.st{font-size:10.5px;letter-spacing:.12em;padding:2px 7px;border:1px solid}.st.meas{color:var(--amber);border-color:var(--amber-dim)}.st.h{color:#cfcabb;border-color:#45443a}.st.l{color:var(--faint);border-color:#2f2f28;border-style:dashed}
.tile.pred{border-style:dashed;background:transparent;box-shadow:none}.youb{font-size:10px;letter-spacing:.14em;color:var(--bg);background:var(--amber);padding:1px 6px;margin-left:8px}
"""
_CMP_CSS = """
.score2{display:flex;align-items:center;gap:18px}.score2 b{display:block;font:300 44px "IBM Plex Mono";color:var(--amber);text-align:center}.score2 span{font-size:11px;color:var(--muted)}.score2 .vs{font-size:30px;color:var(--muted)}
.two2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px}.h2h td{font-size:15px}.h2h td.win{color:var(--amber);background:rgba(255,176,0,.06)}
.pad0{padding:12px 16px}.pad0 svg{width:100%;height:auto}.only{display:grid;grid-template-columns:1fr 1fr;gap:18px}.only li{list-style:none;font-size:12.5px;color:#cfcabb;padding:3px 0}
"""
_HW_JS = r"""
const $ = s => document.querySelector(s);
const box = savedBox(DATA) || { name: "reference box", gpu: DATA.ref.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: DATA.ref.ram, rambw: DATA.ref.rambw };
if (savedBox(DATA)) $("#advbox").textContent = `FOR YOUR BOX: ${box.name} · ${Math.round(box.ram / 1024)} GB @ ${box.rambw} GB/s`;
const cur = forBox(DATA.sh, box);
const conf = hw => { const f = forBox(DATA.sh, hw); return f.gf > 0.6 ? "low" : "high"; };
function card(title, hw, note) {
  const f = forBox(DATA.sh, hw), g = f.t2 / cur.t2;
  const txt = g > 1.05 ? `+${Math.round((g - 1) * 100)}%` : g < 0.95 ? `${Math.round((g - 1) * 100)}%` : "±0%";
  return `<div><h4>${title}</h4><div class="g ${Math.abs(g - 1) < 0.05 ? "no" : ""}">${txt}</div><p>~${Math.round(f.t2)} tok/s, ${Math.round(f.gf * 100)}% of the experts on the GPU. ${note} <span class="st ${conf(hw) === "high" ? "h" : "l"}">${conf(hw).toUpperCase()} CONFIDENCE</span></p></div>`;
}
const faster = DATA.ramKinds.map(r => r[1]).filter(v => v >= box.rambw * 1.2)[0];   // a step worth buying
const gp = n => { const g = DATA.gpus.find(x => x[0].startsWith(n)); return { gpu: n, vram: g[1], vrambw: g[2], ram: box.ram, rambw: box.rambw }; };
$("#adv").innerHTML = (faster ? card(`Faster RAM (~${faster} GB/s)`, Object.assign({}, box, { rambw: faster }), "RAM bandwidth sets the pace while experts live in system RAM.") : "<div><h4>Faster RAM</h4><p class='q'>already at the fastest preset</p></div>")
  + card("A 16 GB card (RTX 5070 Ti)", gp("RTX 5070 Ti"), "More experts fit on the GPU.")
  + card("A 24 GB card (RTX 4090)", gp("RTX 4090"), "Most experts on the GPU.");
const rows = [];
const m = DATA.measured;
rows.push({ name: `${m.gpu} · ${Math.round(m.ram / 1024)} GB · ${m.rambw} GB/s`, measured: true, t2: m.t2, td: m.td, f: forBox(DATA.sh, { gpu: m.gpu, vram: DATA.ref.vram, vrambw: DATA.ref.vrambw, ram: m.ram, rambw: m.rambw }) });
for (const g of DATA.gpus) for (const r of [DATA.ramKinds[1], DATA.ramKinds[2]]) {
  const hw = { gpu: g[0], vram: g[1], vrambw: g[2], ram: 65536, rambw: r[1] }, f = forBox(DATA.sh, hw);
  rows.push({ name: `${g[0]} · 64 GB ${r[0]}`, measured: false, t2: f.t2, td: f.td, f, conf: conf(hw) });
}
rows.sort((a, b) => b.t2 - a.t2);
const T = (v, pred) => `<span class="tile ${v >= 50 ? "hi" : v >= 25 ? "mid" : "lo"}${pred ? " pred" : ""}">${pred ? "~" : ""}${Math.round(v)}</span>`;
$("#boxes").innerHTML = `<tr><th class="l">BOX</th><th>STATUS</th><th>EXPERTS ON GPU</th><th>VRAM</th><th>RAM</th><th>TOK/S 2k</th><th>TOK/S DEEP</th><th>CONTEXT</th></tr>` +
  rows.map(r => `<tr class="${r.measured ? "sel" : ""}"><td class="l"><span class="m">${r.name}</span>${r.measured ? " <span class=youb>MEASURED</span>" : ""}</td>` +
    `<td>${r.measured ? '<span class="st meas">MEASURED · 1 RUN</span>' : `<span class="st ${r.conf === "high" ? "h" : "l"}">PREDICTED · ${r.conf.toUpperCase()}</span>`}</td>` +
    `<td>${Math.round(r.f.gf * 100)}%</td><td>${(r.f.vram / 1024).toFixed(1)} GB</td><td>${(r.f.ramUsed / 1024).toFixed(1)} GB</td>` +
    `<td>${T(r.t2, !r.measured)}</td><td>${r.td ? T(r.td, !r.measured) : "—"}</td><td>${r.f.fits ? "✓ " + Math.round(r.f.ctx / 1000) + "k" : "✗"}</td></tr>`).join("");
"""
