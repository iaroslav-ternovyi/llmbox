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
        body.append(f"<tr class='{'sel' if i == 1 else ''}' data-rid='{esc(r['id'])}'><td class='rk'>{i}</td><td class='l mod'><span class='m'>{esc(r['id'])}</span><br><span class='q'>{esc(_quant(r['file']))} · 1 run</span><div class='cmpbox'><i></i>compare</div></td>"
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
<nav class="tabs"><a class="on">MODELS</a><a>RECIPES</a><a>HARDWARE</a><a>COMPARE</a><a>METHODOLOGY</a><span class="sp"></span><a class="cta">TEST YOUR BOX ↓</a></nav>
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
  for (const p of ok) { const k = p.vs / p.cap, lo = p.ci[0] * k, hi = Math.min(100, p.ci[1] * k);
    g += `<line x1="${X(p[key])}" y1="${Y(hi)}" x2="${X(p[key])}" y2="${Y(lo)}" stroke="#FFB000" stroke-opacity=".45" stroke-width="6"/>` +
         `<circle cx="${X(p[key])}" cy="${Y(p.vs)}" r="6" fill="${p.pred ? "none" : "#FFB000"}" stroke="#FFB000" stroke-width="2" filter="url(#g)"/>` +
         (X(p[key]) > R - 230 ? `<text x="${X(p[key]) - 11}" y="${Y(p.vs) + 4}" text-anchor="end" fill="#E8E4D8" font-size="12">${p.id} · ${p.vs.toFixed(0)}%${p.pred ? " · predicted" : ""}</text>`
                               : `<text x="${X(p[key]) + 11}" y="${Y(p.vs) + 4}" fill="#E8E4D8" font-size="12">${p.id} · ${p.vs.toFixed(0)}%${p.pred ? " · predicted" : ""}</text>`); }
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
