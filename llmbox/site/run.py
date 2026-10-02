"""A run's page (run-<id8>.html): the machine, the exact command line, telemetry, every task."""
from __future__ import annotations

import re

from .. import hwclass, report
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
 <div><b>{f"{sp['decode_tps']:.0f}" if sp.get("decode_tps") else "—"}</b><span>tok/s in a short chat{f" · {float(report._deep(sp)):.0f} with a long context" if report._deep(sp) not in ("-", "") else ""}</span></div>
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




# ---- a person's run: /r/<id> (design review 2026-10-02, decisions 7, 14 and 17) ----

QUALITY_HELD = {"self-seeded": "its tasks were not the ones the server drew for it",
                "outlier": "its answers read like another model's"}


def _server_flags(rec: dict) -> list[str]:
    """The llama-server flags of the run's recipe, the model as its file name only (the path is the sender's own)."""
    from .. import recipe as rc
    try:
        a = rc.server_args(rc._merge(rc.DEFAULTS, rec.get("recipe") or {}))
    except (KeyError, TypeError, ValueError):
        return []
    if "--port" in a:
        i = a.index("--port")
        del a[i:i + 2]
    if "-m" in a:
        i = a.index("-m") + 1
        a[i] = (a[i] or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "$CRAM" in a:   # "auto": the size the run used, else left to the server's default
        i = a.index("$CRAM")
        cram = (rec.get("speed") or {}).get("cache_ram_mib")
        a[i - 1:i + 1] = ["--cache-ram", str(int(cram))] if isinstance(cram, (int, float)) and cram > 0 else []
    return ["llama-server"] + [str(x) for x in a]


def _strip(others: list[float], t2: float) -> str:
    """The machines like it as ticks on a line, this run as a ring (the card's strip, for the page)."""
    vals = list(others) + [t2]
    lo, hi = min(vals), max(vals)
    pos = (lambda v: 50.0) if hi == lo else (lambda v: (v - lo) / (hi - lo) * 100)
    ticks = "".join(f'<i style="left:{pos(v):.1f}%"></i>' for v in others)
    return (f'<div class="strip" aria-hidden="true"><div class="line">{ticks}<b style="left:{pos(t2):.1f}%"></b></div>'
            f'<span>{lo:.0f}</span><span>{hi:.0f} tok/s</span></div>')


def _alt(c: dict) -> str:
    """The card's text as one sentence: the image's alt text and its social preview's."""
    parts = [f'{c["hardware"]} runs {c["model"]} {c["quant"]}'.strip() + f' at {c["t2"]} tokens per second in a short chat']
    if c.get("deep"):
        parts.append(f'{c["deep"]} at 32k context')
    parts += [x for x in (c.get("badge") and c["badge"].capitalize(), (c.get("place") or {}).get("text"), c.get("quality"), c.get("last")) if x]
    return ". ".join(parts) + "."


def user_run_page(sid: str, board: dict, records: list[dict], live: dict, frozen: dict | None, card_state: str,
                  machines: list, site: str) -> str:
    """A person's run. live: the card's values now (card.spec, wait=False); frozen: the values its card was drawn
    with; card_state: drawn / waiting / retrying / failed; machines: the picker entries for "Your machine?" as
    [name, slug, kind, best model, number, measured, testable]."""
    from .words import CURL
    run = board["runs"][sid]
    sp = next(r for r in records if r.get("kind") == "speed")
    q = next((r for r in records if r.get("kind") == "suite"), None)
    sub, h = sp.get("submission") or {}, sp.get("host") or {}
    speed = sp.get("speed") or {}
    c = live
    entry = run["entry"]
    slug = hwclass.SLUGS.get(entry) if entry else None
    t2 = c["t2"]
    # 1) the answer: the sentence, where it stands, the strip
    badge = f'<span class="badge">{esc(c["badge"])}</span>' if c.get("badge") else ""
    place = c["place"]["text"]
    if frozen and frozen.get("place", {}).get("text") != place:
        place = f'Card drawn {esc(frozen["drawn"][:10])}: {esc(frozen["place"]["text"])}. Today: {esc(place)}.'
    else:
        place = esc(place)
    notes = []
    was_first = frozen and frozen.get("badge") and frozen["badge"].startswith("FIRST")
    if was_first and c.get("badge") != frozen["badge"]:
        why = "its speed is left out of comparisons" if run["outlier"] else "a run received earlier was published after it"
        notes.append(f'no longer the first here ({why})')
    if c.get("outlier"):
        notes.append('<a href="method.html#trust">how runs are checked →</a>')   # the place line already says why
    strip = _strip(c["place"]["others"], t2) if c["place"]["kind"] == "strip" else ""
    # quality, in words
    qflags = ((q or {}).get("submission") or {}).get("flags") or []
    if not q:
        quality = "speed only: this run had no quality test" + (" · sent without an account" if "anonymous" in (sub.get("flags") or []) else "")
    elif "anonymous" in qflags:
        quality = "quality not counted: sent without an account · next time: <code>llmbox login</code>"
    elif held := [QUALITY_HELD[f] for f in qflags if f in QUALITY_HELD]:
        quality = f'quality held for review: {esc("; ".join(held))} · <a href="method.html#trust">how runs are checked →</a>'
    elif c.get("quality"):
        quality = esc(c["quality"])
    else:
        lo, hi = ((q.get("summary") or {}).get("capability_ci95") or [None, None])
        quality = (f"quality: this run solved {lo * 100:.0f}–{hi * 100:.0f}% of the tasks it drew; it does not match the model's published score"
                   if lo is not None and hi is not None else "quality: still being graded")
    # 2) the card and the share tools
    img = f"r/{sid}.png"
    fname = f"llmbox-{slug or 'run'}-{t2}tps.png"
    title = f'{c["hardware"]}: {c["model"]} at {t2} tok/s' + (f' ({c["place"]["text"]})' if c["place"]["kind"] in ("strip", "others") else "")
    url = f"{site}/r/{sid}"
    from urllib.parse import quote
    if card_state == "drawn":
        card = (f'<img class="card" src="{img}" width="1200" height="630" alt="{esc(_alt(frozen or c))}">'
                f'<div class="acts"><a class="btn wide" href="{img}" download="{esc(fname)}">DOWNLOAD CARD</a>'
                f'<button class="btn wide" data-copy="{esc(url)}">COPY LINK</button></div>')
    else:
        why = {"waiting": "The card is drawn once this run's quality answers are graded (within a day).",
               "retrying": "The card image could not be drawn yet; it is retried at the next build.",
               "failed": "The card image could not be drawn; the site's owner has been told."}[card_state]
        card = (f'<div class="card none"><p>{why}</p></div>'
                f'<div class="acts"><button class="btn wide" data-copy="{esc(url)}">COPY LINK</button></div>')
    share = (f'<div class="post"><span class="sc">Suggested title</span><div class="ttl"><span id="ttl">{esc(title)}</span>'
             f'<button class="btn" data-copy-from="ttl">COPY</button></div>'
             f'<div class="q">post it: <a href="https://www.reddit.com/r/LocalLLaMA/submit" rel="noopener">r/LocalLLaMA</a> · '
             f'<a href="https://x.com/intent/post?text={quote(title + " " + url)}" rel="noopener">X</a></div></div>')
    # "Your machine?": the picker preset to this entry
    opts, kinds = [], (("card", "NVIDIA"), ("amd", "AMD"), ("mac", "Mac"), ("chip", "Ryzen AI Max"))
    for k, label in kinds:
        o = "".join(f'<option value="{esc(m[1])}"{" selected" if m[0] == entry else ""}>{esc(m[0])}</option>' for m in machines if m[2] == k)
        opts.append(f'<optgroup label="{label}">{o}</optgroup>')
    one = f"{CURL} {site}/install.sh | sh"
    yours = (f'<section class="panel yours"><div class="lbl">Your machine?</div>'
             f'<label class="sc" for="mach">the computer you have</label><select id="mach">'
             + ("" if entry else '<option value="" selected>choose yours</option>') + "".join(opts) + '</select>'
             '<div id="mbest" class="mbest" aria-live="polite"></div>'
             f'<div class="cmd"><code id="cmd">{esc(one)}<br>llmbox test</code><button class="btn solid" data-copy-from="cmd">COPY</button></div>'
             '<p class="q">10 minutes for the speed, 10 more for the quality test; your result gets a page like this one.</p></section>')
    flags = _server_flags(sp)
    settings = (f'<section class="panel"><div class="lbl">Run it with these settings</div>'
                f'<div class="argv" id="argv">{"".join(f"<span>{esc(x)}</span> " for x in _argv_lines(flags))}</div>'
                f'<div class="diff"><button class="btn" data-copy-from="argv">COPY SETTINGS</button></div></section>') if flags else ""
    tag = (sp.get("runtime") or {}).get("llama_cpp_build")
    from .card import build_tag
    dl = [("32K CONTEXT", f'{run["deep"]:.0f} tok/s' if run.get("deep") else "not measured"),
          ("PROMPT", f'{speed["prefill_tps"]:,.0f} tok/s read' if speed.get("prefill_tps") else "not measured"),
          ("QUALITY", None), ("GPU", f'{h.get("gpu") or "none"}' + (f' · {h["vram_gib"]} GiB' if h.get("vram_gib") else "")),
          ("CPU", h.get("cpu") or "—"), ("RAM", f'{h["ram_gib"]} GiB' + (f' · {h["ram_read_gbs"]} GB/s read' if h.get("ram_read_gbs") else "") if h.get("ram_gib") else "—"),
          ("LLAMA.CPP", build_tag(tag) or "not recorded"), ("RECEIVED", (sub.get("received") or "")[:10])]
    details = ('<section class="panel sys"><div class="lbl">Details</div><dl>'
               + "".join(f"<dt>{k}</dt><dd>{quality if v is None else esc(v)}</dd>" for k, v in dl) + "</dl></section>")
    crumb = f'<a href="index.html">Models</a> / ' + (f'<a href="hw-{slug}.html">{esc(entry)}</a> / ' if slug else "") + f"run {sid}"
    body = f'''
<section class="panel hd rp"><div><div class="crumb">{crumb}</div>
 <h1>{esc(c["hardware"])} runs {esc(c["model"])} at <span class="amb">{t2}</span> tok/s{badge}</h1>
 <p class="place">{place}</p>{strip}{"".join(f'<p class="q">{n}</p>' for n in notes)}</div></section>
<div class="share"><section class="panel shot">{card}{share}</section>{yours}</div>
{settings}{details}'''
    return _page(f"llmbox · {c['hardware']} runs {c['model']} at {t2} tok/s", "", body, ("pages.css", "run.css"), ("runpage.js",),
                 data={"machines": machines, "url": url}, base=True, about=_alt(c))


def removed_page(sid: str, reason: str) -> str:
    """What a posted link shows once its run is gone: why, and where to go instead."""
    why = {"owner": "Its owner removed it.", "review": "It was removed after review."}.get(reason, "It was removed.")
    body = (f'<section class="panel hd"><div><h1>This result was removed</h1><p class="q" style="margin-top:8px">{why}</p>'
            '<p style="margin-top:14px">What runs best on your machine: <a href="index.html">llmbox.pages.dev</a> · '
            '<code>$ llmbox test</code></p></div></section>')
    return _page("llmbox · result removed", "", body, ("pages.css",), base=True)
