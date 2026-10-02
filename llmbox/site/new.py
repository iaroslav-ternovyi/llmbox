"""New models for the visitor's box (new.html)."""
from __future__ import annotations

import re

from .. import report
from .layout import _page
from .stats import _axis_of, _range_pct
from .words import _quant, esc, family, model_name


def new_page(rs: list[dict], data: dict, host: str, news: str = "") -> str | None:
    """What came out in the last six months that runs on the visitor's box: releases, fine-tunes and remixes pulled by
    enough people (llmbox/candidates.py), each with the file this box runs well (4-bit first, a smaller quantization
    when the 4-bit file does not fit), its predicted speed, and its score - measured here when it was, otherwise the
    range expected from public benchmarks. Measured models are listed with their result, not hidden."""
    import datetime as _dt
    from .. import watch as W
    first_seen = W.first_seen()
    week_ago = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()
    from .. import candidates as C, estimate as E, fit as F, recipe as rc
    cs = C.load()
    if not cs:
        return None
    cutoff = (_dt.date.today() - _dt.timedelta(days=C.RECENT_DAYS)).isoformat()
    from .. import eci
    table = eci.load()
    ref_cap = next((r["capability"] / (r["vs_ref"] / 100) for r in rs if r.get("vs_ref")), None)
    by_base: dict = {}
    chains: dict = {}
    measured: dict = {}   # candidate key -> measured row
    by_name: dict = {}    # the same without the org: a repo whose base tags could not be read still matches its model
    for r in rs:
        try:
            repo = rc.load(host, r["id"])["model"].get("hf_repo") or ""
        except (OSError, ValueError):
            continue
        chains[r["id"]] = C.base_chain(repo)
        # an anchor must BE the scored model (a quantization of it): a fine-tune of a base is a different model; and its
        # score must be comparable (a result with a caveat, e.g. tested without tools, would bend the line)
        from .words import caveat
        hit = eci.match(repo, table) if not eci.is_remix(repo) and not caveat(host, r["id"]) else None
        if hit:
            by_base.setdefault(hit[0], (hit[1]["eci"], []))[1].append((r["id"], r["capability"]))
        measured.setdefault(C.key_of(repo), []).append(dict(r, repo=repo))
        by_name.setdefault(C._key(re.sub(r"-gguf(-mtp)?$", "", repo, flags=re.I)).split("/")[-1], []).append(dict(r, repo=repo))
    anchors = ([(f"{eci.FRONTIER_PROXY} (stands in for the reference)", table[eci.FRONTIER_PROXY]["eci"], ref_cap)]
               if ref_cap and eci.FRONTIER_PROXY in table else [])
    anchors += [(f"{name} via {', '.join(i for i, _ in ms)}", e, sum(c for _, c in ms) / len(ms)) for name, (e, ms) in by_base.items()]
    pred = eci.predictor(table, anchors, ref_cap) if ref_cap else None
    kv = "q8_0"

    def shp(sh: E.ModelShape) -> dict:   # what plan.js reads, as the model pages have it (data.js_shape: one copy, held by test_parity)
        from .data import js_shape
        from .. import fit as F
        return js_shape(sh, kv, sh.context_length or 32768, F.Calibration(deep_k=32))

    def expected(repo: str):
        hit = eci.match(repo, table) if pred else None
        if not hit:
            return None
        mid, lo, hi = pred(hit[1]["eci"], hit[1]["lo"], hit[1]["hi"])
        return {"mid": round(mid), "lo": round(lo), "hi": round(hi), "name": hit[0], "eci": hit[1]["eci"], "remix": eci.is_remix(repo)}

    def mrow(m: dict) -> dict:
        sp = m["speed"] or {}
        lo, hi = _range_pct(m) if m.get("vs_ref") is not None else (None, None)
        return {"rid": m["id"], "name": model_name(m), "vs": m.get("vs_ref"), "lo": lo, "hi": hi, "cap": m["capability"], "t2": sp.get("decode_tps"),
                "td": float(report._deep(sp)) if report._deep(sp) != "-" else None}

    rows, seen = [], set()
    for c in cs:
        sh = E.ModelShape(**c["shape"])
        own = C.base_chain(c["repo"])
        roots = set(own[:2]) | {re.sub(r"-gguf$", "", c["repo"], flags=re.I)}   # the repo and the model it packages
        lin = c.get("lineage") or {"kind": "release", "of": None, "model": c["base"]}
        key = C._key(lin.get("model") or c["base"])
        ms = (measured.get(key) or measured.get(C._key(c["base"])) or by_name.get(key.split("/")[-1])
              or by_name.get(C._key(re.sub(r"-gguf(-mtp)?$", "", c["repo"], flags=re.I)).split("/")[-1]) or [])
        seen |= {m["id"] for m in ms}
        # measured fine-tunes of this model (declared on Hugging Face): context, not a prediction - they are other models
        rel = [(rid, (r.get("vs_ref") or 0)) for r in rs for rid in [r["id"]] if rid in chains and roots & set(chains[rid][1:]) and rid not in {m["id"] for m in ms}]
        seen_on = first_seen.get(key)
        rows.append({"repo": c["repo"], "rid": C.recipe_id(c["repo"]), "released": c.get("released") or c.get("created"),
                     "fresh": bool(seen_on and seen_on >= week_ago),
                     "dl": c["downloads"], "total": round(sh.total_params / 1e9, 1), "active": round(sh.active_params / 1e9, 1),
                     "kind": lin["kind"], "of": lin.get("of"), "rel": rel, "guess": expected(c["repo"]), "col": family(getattr(sh, "arch", None))[1],
                     "measured": [mrow(m) for m in ms], "bytes0": c["bytes"], "sh": shp(sh),
                     "ladder": [{"file": x["file"], "quant": x["quant"], "bytes": x["bytes"]} for x in (c.get("ladder") or [{"file": c["file"], "quant": c["quant"], "bytes": c["bytes"]}])]})
    # measured models released in the window that the popularity cut left out: listed with their result
    for r in rs:
        if r["id"] in seen or r["id"] not in data["recipes"]:
            continue
        try:
            rec = rc.load(host, r["id"])
        except (OSError, ValueError):
            continue
        repo = rec["model"].get("hf_repo") or ""
        rel_day = C.released(repo) if repo else None
        if not rel_day or rel_day < cutoff:
            continue
        sh = F.shape_for(rec)
        lin = C.lineage(repo)
        rows.append({"repo": repo, "rid": r["id"], "released": rel_day, "dl": None, "total": round(sh.total_params / 1e9, 1),
                     "active": round(sh.active_params / 1e9, 1), "kind": lin["kind"], "of": lin.get("of"), "rel": [], "col": family(sh.arch)[1],
                     "guess": expected(repo), "measured": [mrow(r)], "bytes0": sh.total_bytes or 1, "sh": shp(sh),
                     "ladder": [{"file": rec["model"].get("file") or "", "quant": _quant(rec["model"].get("file") or ""), "bytes": sh.total_bytes or 1}]})
    body = f"""
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / new</div><h1>New models for your box</h1>
 <p class="lede">Local models from the last six months that people actually download: which file fits <b id="boxname">the reference PC</b>
 (<a href="index.html#box">pick your box</a>), how fast it runs there, and how good it is: measured here, or the range public benchmarks suggest.</p></div></section>
<p class="nsum" id="nsum"></p>
<section class="panel"><div class="lbl"><span id="count">{len(rows)}</span> models</div>
 <div class="nctl"><div class="chips" role="group" aria-label="show">
  <button class="on" data-f="fit" aria-pressed="true">fits my box</button><button class="on" data-f="release" aria-pressed="true">releases</button>
  <button class="on" data-f="fine-tune" aria-pressed="true">fine-tunes</button><button class="on" data-f="uncensored" aria-pressed="true">uncensored</button></div>
  <label class="q">sort <select id="sort"><option value="rel">newest</option><option value="exp">score</option><option value="speed">speed on your box</option><option value="dl">most downloaded</option></select></label></div>
 <div class="nhd"><span>MODEL</span><div class="fp"><div class="trk axis" id="nax"></div><span class="num">SCORE</span></div><span class="r">TOK/S</span><span>FILE THAT FITS</span><span></span></div>
 <div id="rows"></div>
 <p class="q nlg"><b class="dotm"></b> measured here · <b class="doth"></b> expected from public benchmarks (a fine-tune or remix: its base model's range) · % of Claude Opus 5.5 on this site's tasks</p></section>"""
    note = ("<section class='panel pad'><div class='lbl'>Where the expected score comes from</div><p class='q' style='max-width:900px;line-height:1.7'>"
            "A measured model shows its own score. Otherwise the model's "
            "<a href='https://epoch.ai/benchmarks'>Epoch Capabilities Index</a> (one number fitted over many public benchmarks), put on our scale by a "
            "straight line through models that have both: " + "; ".join(f"{esc(n)}: ECI {e:.0f} = {c / ref_cap * 100:.0f}%" for n, e, c in anchors)
            + ". A fine-tune or remix gets its base model's range: training can move it either way. A 3- or 2-bit file scores lower than the 4-bit "
            "one the range is for. ECI data: Epoch AI, 'Capabilities &amp; benchmarking', epoch.ai/benchmarks, CC BY 4.0.</p></section>") if pred else ""
    body += note + news
    return _page("llmbox · new models", "NEW", body, ("pages.css", "new.css"), ("plan.js", "new.js"),
                 dict(ref=data["ref"], gpus=data["gpus"], ramKinds=data["ramKinds"], rows=rows, eciReady=bool(pred), ax=_axis_of([r.get("vs_ref") for r in rs])),
                 about="New local AI models from the last six months: which file fits your graphics card or Mac, how fast it runs, measured or expected quality.")


