"""One page that compares any two models (compare.html#a-vs-b)."""
from __future__ import annotations


from .. import report
from .layout import _page
from .stats import _range_pct
from .words import _human, _KNAMES, esc, GROUPS, model_name, SHORT, TASK_NAMES, variant, bits_of


def _flat_settings(rcp: dict) -> dict:
    """A recipe's settings that can differ between two models, by readable name (the model-specific loop-marker token ids
    left out: they are never the same across models)."""
    flat = lambda d, p="": {k2: v2 for k, v in (d or {}).items() for k2, v2 in (flat(v, f"{p}{k}.") if isinstance(v, dict) else {f"{p}{k}": v}).items()}
    f = flat({k: (rcp or {}).get(k) for k in ("sampling", "chat", "antiloop", "placement", "speculative")})
    return {_KNAMES.get(k, k.split(".")[-1].replace("_", " ")): "off" if v in ("", "none") else _human(v)
            for k, v in sorted(f.items()) if not k.endswith("marker_ids") and k != "placement.n_cpu_moe"}


def compare_data(rs: list[dict], clouds: list[dict], ranks: dict, local: dict, look: dict, data: dict, rel: dict) -> dict:
    """Everything the compare page needs for any pair: the ranked local models, then the Claude models for reference."""
    models = []
    for r in sorted(rs, key=lambda r: ranks[r["id"]][0]) + clouds:
        rid, cloud = r["id"], r in clouds
        pool = (report.ranked_now(rid, "cloud" if cloud else "box") or {}).get("rows") or []
        fam = {}
        for x in pool:
            fam.setdefault(x.get("family") or ".".join(x["id"].split(".")[:3]), []).append(x["score"])
        rec = local.get(rid) or {}
        lo, hi = _range_pct(r) if r.get("vs_ref") is not None else (None, None)
        bd = sorted((report._depth_k(k), d) for k, d in ((r.get("speed") or {}).get("by_depth") or {}).items() if d.get("decode_tps"))
        col, kind = look.get(rid, ("#8b877b", "release"))
        models.append({"id": rid, "name": model_name(r), "quant": "" if cloud else variant(rid, r.get("file")), "col": col, "kind": kind,
                       "cloud": cloud, "place": None if cloud else ranks[rid][0], "vs": r.get("vs_ref"), "lo": lo, "hi": hi, "cap": r["capability"], "ci": r["ci"],
                       "blocks": r["blocks"], "t2": None if cloud else (r.get("speed") or {}).get("decode_tps"),
                       "td": None if cloud or report._deep(r["speed"]) == "-" else float(report._deep(r["speed"])),
                       "depth": [[k, round(d["decode_tps"], 1), round(k * 1000 / d["prefill_tps"], 1) if d.get("prefill_tps") else None] for k, d in bd],
                       "fam": {f: round(sum(v) / len(v), 2) for f, v in fam.items()},
                       # what differs first: the file (its precision and size) and what kind of model it is, then the settings
                       "set": dict({"file": " · ".join(x for x in (variant(rid, r.get("file")), bits_of(variant(rid, r.get("file"))),
                                                                     f'{data["recipes"][rid]["size"]} GB' if (data["recipes"].get(rid) or {}).get("size") else "") if x),
                                    "kind of model": {"uncensored": "uncensored remix"}.get(kind, kind)},
                                   **_flat_settings(rec.get("recipe") or {})) if rec else {},
                       "rel": rel.get(rid)})
    from ..import suite
    return {"models": models, "groups": GROUPS, "weights": suite.WEIGHTS, "short": SHORT, "tasks": TASK_NAMES,
            "recipes": data["recipes"], "gpus": data["gpus"], "ref": data["ref"]}


def compare_app(cdata: dict) -> str:
    """One page for any two models (the pair in the address: compare.html#a-vs-b): a verdict in words that says when a
    difference is inside the margin of error, the four uses and nine blocks side by side, speed and fit on the
    visitor's box, the tasks only one of them solved, the settings that differ."""
    opts = ("<optgroup label='Local models, by place'>" + "".join(f"<option value='{esc(m['id'])}'>{m['place']}. {esc(m['name'])} · {esc(m['quant'])}</option>" for m in cdata["models"] if not m["cloud"])
            + "</optgroup><optgroup label='Cloud, for reference'>" + "".join(f"<option value='{esc(m['id'])}'>{esc(m['name'])}</option>" for m in cdata["models"] if m["cloud"]) + "</optgroup>")
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / compare</div><h1>Compare two models</h1>
 <div class="pick"><span class="dot a"></span><select id="ma" aria-label="first model">{opts}</select><button class="btn sw" id="swap" type="button" title="swap">⇄</button>
 <span class="dot b"></span><select id="mb" aria-label="second model">{opts}</select></div></div></section>
<section class="panel verdict2" id="verdict"></section>
<section class="panel pad"><div class="lbl">Side by side</div><div id="dumb"></div>
 <p class="q" style="margin-top:14px">Out of 100. The number on the right is the gap; gaps under 6 points are within what a few more tasks could change. <a href="method.html">How scores work</a></p></section>
<div class="two2"><section class="panel pad"><div class="lbl" id="spdlbl">Speed as the context grows</div><div id="speed"></div></section>
<section class="panel pad"><div class="lbl">Tasks only one of them solved</div><div id="only"></div></section></div>
<section class="panel"><div class="lbl">Settings that differ</div><div class="tw" id="sets"></div></section>'''
    return _page("llmbox · compare two models", "COMPARE", body, ("pages.css", "compare.css"), ("plan.js", "compare.js"), cdata)


