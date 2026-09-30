"""Pieces every page draws the same way: the model marker, a score as a dot on its range, use and block bars, speed bars."""
from __future__ import annotations

from .. import report
from .stats import _stand_words
from .words import _wavg, esc, GROUP_TIPS, GROUPS, LABEL, NOT_MEASURED, SHORT, TIPS


def _tile(v: float | None, small: str = "", big: bool = False) -> str:
    if v is None:
        return '<span class="tile na">—</span>'
    c = "hi" if v >= 85 else ("mid" if v >= 50 else "lo")
    return f'<span class="tile {c}{" big" if big else ""}">{v:.0f}{"%" if big else ""}{f"<small>{esc(small)}</small>" if small else ""}</span>'


def _tip(block: str, open_: bool = False) -> str:
    title, text, ex = TIPS[block]
    lis = "".join(f"<li>{esc(e)}</li>" for e in ex)
    return f'<span class="tip{" open" if open_ else ""}">{LABEL[block]}<span class="pop"><b>{esc(title)}</b>{esc(text)}<ul>{lis}</ul></span></span>'


def _marker(col: str, kind: str, s: int = 14) -> str:
    """The chart's marker: filled = the maker's release, ring = a fine-tune, diamond = an uncensored remix."""
    c, r = s / 2, s / 2 - 2
    mk = (f'<path d="M{c} {c - r - 1}L{c + r + 1} {c}L{c} {c + r + 1}L{c - r - 1} {c}Z" fill="none" stroke="{col}" stroke-width="2"/>' if kind == "uncensored"
          else f'<circle cx="{c}" cy="{c}" r="{r}" fill="{"none" if kind == "fine-tune" else col}" stroke="{col}" stroke-width="2"/>')
    return f'<svg class="mk" width="{s}" height="{s}" aria-hidden="true">{mk}</svg>'


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f}%"


def _spd(sp: dict) -> str:
    tps, deep = sp.get("decode_tps"), report._deep(sp)
    return f"<b>{tps:.0f}</b><small>{'' if deep == '-' else f'{float(deep):.0f} long'}</small>" if tps else "—"


def _stands_out(blocks: dict, med: dict) -> str:
    """The blocks where a model is clearly above or below the typical local model (the median), two of each at most."""
    up, dn, behind = _stand_words(blocks, med)
    if behind:
        return '<span class="dn">▼ behind on every block</span>'
    out = ([f'<span class="up">▲ {" · ".join(SHORT[b] for b in up)}</span>'] if up else []) + ([f'<span class="dn">▼ {" · ".join(SHORT[b] for b in dn)}</span>'] if dn else [])
    return "".join(out) or '<span class="ev">even, close to typical</span>'


def _stands_sentence(blocks: dict, med: dict) -> str:
    up, dn, behind = _stand_words(blocks, med)
    if behind:
        return "Behind the typical local model here on every block."
    j = lambda bs: " and ".join(SHORT[b] for b in bs)
    parts = ([f"stronger at <b class='up'>{j(up)}</b>"] if up else []) + ([f"weaker at <b class='dn'>{j(dn)}</b>"] if dn else [])
    return ("Compared with the typical local model here: " + "; ".join(parts) + ".") if parts else "Close to the typical local model here on every block."


def _bar(v: float, col: str, med: float | None = None, ref: float | None = None, big: bool = False) -> str:
    return (f"<span class='bt{' big' if big else ''}'><i style='width:{v:.0f}%;background:{col}'></i>"
            + (f"<u style='left:{med:.0f}%' title='typical local model: {med:.0f}'></u>" if med is not None else "")
            + (f"<s style='left:{ref:.0f}%' title='Claude Opus 5.5: {ref:.0f}'></s>" if ref is not None else "") + "</span>")


def _groups_html(blocks: dict, med: dict, col: str, ref: dict | None = None, lost: dict | None = None) -> str:
    """The four uses, each a bar with its blocks under it (a lone block is the use itself). With `lost`, a block opens the
    tasks the model lost there and why."""
    out = []
    for name, bs in GROUPS:
        g = _wavg(blocks, bs)
        if g is None:
            continue
        rows = []
        for b in bs if len(bs) > 1 else []:
            v = blocks.get(b)
            if v is None:
                continue
            row = f"<span class='bn'>{SHORT[b]}</span>{_bar(v, col, med.get(b), (ref or {}).get(b))}<b>{v:.0f}</b>"
            ls = (lost or {}).get(b)
            rows.append(f"<details class='bb'><summary>{row}</summary><ul>{''.join(f'<li>{x}</li>' for x in ls)}</ul></details>" if ls else f"<div class='bb'>{row}</div>")
        one = (lost or {}).get(bs[0]) if len(bs) == 1 else None
        head = f"<span class='gn tip'>{esc(name)}<span class='pop'><b>{esc(name)}</b>{esc(GROUP_TIPS[name])}</span></span><b class='gv'>{g:.0f}</b>{_bar(g, col, _wavg(med, bs), _wavg(ref, bs) if ref else None, big=True)}"
        out.append("<div class='grp'>"
                   + (f"<details class='gh'><summary>{head}</summary><ul>{''.join(f'<li>{x}</li>' for x in one)}</ul></details>" if one else f"<div class='gh'>{head}</div>")
                   + "".join(rows) + "</div>")
    return f"<div class='groups'>{''.join(out)}</div>"


def _groups_legend(ref: bool = False) -> str:
    return ("<p class='q glg'>Out of 100 · <u></u> typical local model here" + (" · <s></s> Claude Opus 5.5" if ref else "")
            + f" · not measured: {NOT_MEASURED}</p>")


def _profile(r: dict, med: dict, col: str, rank: tuple) -> str:
    """Under a ranking line: the four uses with their blocks, the typical local model marked."""
    rid = r["id"]
    pl, lo, hi, _ = rank
    k = (r.get("vs_ref") or 0) / r["capability"] if r["capability"] else 0
    return (f"<div class='pf'>{_groups_html(r['blocks'], med, col)}{_groups_legend()}<div class='pfl'>"
            + (f"<span>Overall {r['vs_ref']:.0f}% of Claude Opus 5.5 · 95% range {r['ci'][0] * k:.0f}–{min(100, r['ci'][1] * k):.0f} · "
               f"place {pl}{f', tied with places {lo}–{hi}' if lo != hi else ''}</span>" if k else "")
            + f"<a href='recipe-{esc(rid)}.html'>File, settings and every task →</a><a href='hardware-{esc(rid)}.html'>Speed on other boxes →</a></div></div>")


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


def _fp_html(vs: float, lo: float, hi: float, col: str, ax: tuple[int, int], cloud: bool = False) -> str:
    """A score as a dot on its 95% range (a Claude model: a dashed mark), the same drawing as the ranking's."""
    mn, step = ax
    X = lambda v: max(0.0, min(100.0, (v - mn) / (100 - mn) * 100))
    g = "".join(f"<s style='left:{X(v):.2f}%'></s>" for v in range(mn, 101, step))
    if cloud:
        return f"<div class='fp'><div class='trk'>{g}<b class='rf' style='left:{X(vs):.2f}%'></b></div><span class='num'>{vs:.0f}%</span></div>"
    return (f"<div class='fp' title='95% range {lo:.0f}–{min(100, hi):.0f}%'><div class='trk'>{g}<i style='left:{X(lo):.2f}%;width:{X(hi) - X(lo):.2f}%;background:{col}'></i>"
            f"<b style='left:{X(vs):.2f}%;background:{col}'></b>{f'<em>◂ {vs:.0f}%</em>' if vs < mn else ''}</div><span class='num'>{vs:.0f}%</span></div>")


def _cmp_href(a: str, b: str) -> str:
    return f"compare.html#{a}-vs-{b}"
