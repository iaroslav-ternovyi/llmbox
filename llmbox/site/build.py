"""The whole site from the saved records."""
from __future__ import annotations

import os
import re
import statistics

from .. import report
from .compare import compare_app, compare_data
from .components import _cmp_href
from .data import _model_now, load_records, optimize_records, shape_data, task_flags
from .hardware import hardware_page
from .home import home
from .layout import _page, copy_assets
from .method import method_page
from .model import recipe_page
from .new import new_page
from .run import run_page
from .stats import rank_ranges
from .words import _kind, BLOCKS, family, set_variants


def build(out_dir: str, host: str = "box", suite_version: str | None = None, tier: str = "quick") -> list[str]:
    from .. import suite as _s
    suite_version = suite_version or _s.VERSION
    """The whole site: home, a page per recipe, per run, hardware per recipe, compare per pair of measured recipes."""
    out_dir = os.path.expanduser(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    with report.results.frozen():   # every page reads the same records: once
        return _build(out_dir, host, suite_version, tier)


def _build(out_dir: str, host: str, suite_version: str, tier: str) -> list[str]:
    all_rs = report.rows(host, suite_version=suite_version, tier=tier)
    rs = [r for r in all_rs if r["host"].get("id") != "cloud" and not r.get("partial")]
    set_variants(host, rs)   # the same file measured with other settings (K2-Horizon high / medium): named apart
    data = shape_data(rs, host)
    written = copy_assets(out_dir) + [home(out_dir, host, suite_version, tier, all_rs, data)]
    recs = load_records(host, suite_version, tier)
    local_run, ref = recs["local"], recs["ref"]
    allrecs = report.results.load_all(host)
    local = {rid: report.with_probe(rec, allrecs) for rid, rec in local_run.items()}   # speed re-measured after tune; run pages keep their own
    ranks = rank_ranges(rs)
    opts = optimize_records(host)
    order = sorted(local, key=lambda k: (-local[k]["summary"]["capability"], k))   # ties by id: recipe pages and the home page name pairs the same way
    def w(name, html_):
        p = os.path.join(out_dir, name)
        open(p, "w").write(html_)
        written.append(p)
    clouds = [r for r in all_rs if r["host"].get("id") == "cloud" and not r.get("partial")]
    med = {b: statistics.median(v) for b in BLOCKS if (v := [r["blocks"][b] for r in rs if r["blocks"].get(b) is not None])}
    look = {r["id"]: (family((data["recipes"].get(r["id"]) or {}).get("arch"))[1], _kind(r.get("hf_repo"))) for r in rs}
    ref_hw = data["ref"]
    ref_box = f'{ref_hw["gpu"]} + {round(ref_hw["ram"] / 1024)} GB RAM'
    from .. import bench
    suite_files = [(pth, x) for pth, x in report.results.files(host) if x.get("kind") == "suite"]
    from .data import community_speeds
    cs = community_speeds({rid: os.path.basename((local[rid].get("model") or {}).get("file") or "") for rid in order}, host)
    rel = {}
    for rid in order:
        rec = local[rid]
        now, avail = _model_now(host, rid)
        # the runs whose answers make up the score (report.current_pool), each with its own thinking flags
        pool = (report.ranked_now(rid, host) or {}).get("rows") or []
        made = {x.get("_run") for x in pool}
        runs = [bench.rescore(dict(x, _path=pth)) for pth, x in suite_files if (x.get("recipe") or {}).get("id") == rid and x.get("created") in made] or [local_run[rid]]
        fl = {x.get("created"): task_flags(x) for x in runs}
        rel[rid] = (sum(1 for x in pool if ((fl.get(x.get("_run")) or {}).get(x["id"]) or {}).get("cut") or ((fl.get(x.get("_run")) or {}).get(x["id"]) or {}).get("loop")), len(pool))
        counted = {c: sum(1 for x in pool if x.get("_run") == c) for c in made}
        hours = sum((x["summary"].get("wall_minutes") or 0) for x in runs) / 60
        rival = next((o for o in order if o != rid), None)
        w(f"recipe-{rid}.html", recipe_page(rid, rec, ref, {
            "rs": rs, "clouds": clouds, "ranks": ranks, "med": med, "look": look, "shape": data["recipes"].get(rid), "ref_hw": ref_hw, "ref_box": ref_box,
            "pool_rows": pool, "flags": fl, "runs": runs, "counted": counted, "solved_h": sum(x["summary"].get("solved") or 0 for x in runs) / hours if hours else 0,
            "opt": opts.get(rid), "model_now": now, "on_hf": avail, "cmp": _cmp_href(rid, rival) if rival else "#", "community": cs.get(rid)}))
        for x in runs:
            w(f"run-{x['id'][:8]}.html", run_page(rid, x, ref, fl[x.get("created")]))
        if rid in data["recipes"]:
            w(f"hardware-{rid}.html", hardware_page(rid, rec, data["recipes"][rid], data, cs.get(rid)))
    w("compare.html", compare_app(compare_data(rs, clouds, ranks, local, look, data, rel)))
    from .. import registry
    from .words import GROUPS, _wavg, model_name, variant
    def _meta(r):   # what llmbox pick shows: % of Opus with its 95% range, and per use
        k = (r.get("vs_ref") or 0) / r["capability"] if r.get("capability") else 0
        return {"name": f'{model_name(r)} {variant(r["id"], r.get("file"))}', "score": r.get("vs_ref"),
                "range": [round(r["ci"][0] * k, 1), round(r["ci"][1] * k, 1)] if r.get("ci") and k else None,
                "uses": {n: round(v, 1) for n, bs in GROUPS if (v := _wavg(r.get("blocks_vs_ref"), bs)) is not None}}
    written += registry.export(host, order, out_dir, {r["id"]: _meta(r) for r in rs})   # recipes/: what `llmbox recipe pull` installs
    ref_row = next((r for r in all_rs if r["host"].get("id") == "cloud" and ref and r["id"] == (ref.get("recipe") or {}).get("id")), None)
    w("method.html", method_page(ref, opts, set(local), ref_row, rs, look))
    try:
        np_ = new_page(rs, data, host)
    except Exception as e:   # the list needs Hugging Face; the rest of the site must not depend on it
        np_ = None
        print(f"new.html skipped: {e}")
    if np_:
        w("new.html", np_)
    w("404.html", _page("llmbox · page not found", "", '<section class="panel hd"><div><h1>Page not found</h1><p class="q" style="margin-top:8px">'
                        'The model or run may have been renamed. <a href="index.html">The ranking</a> · <a href="new.html">new models</a> · '
                        '<a href="compare.html">compare</a> · <a href="method.html">how scores work</a></p></div></section>', ("pages.css",)))
    # pages of earlier builds this one did not write (a run that no longer counts, a renamed recipe): only the site's own
    # kinds of file, so a folder with other things in it keeps them
    keep = {os.path.basename(p) for p in written}
    for f in os.listdir(out_dir):
        if f not in keep and re.fullmatch(r"(index|new|method|compare|404|(recipe|run|hardware|compare)-.+)\.html|[a-z0-9]+\.(css|js)", f):
            os.remove(os.path.join(out_dir, f))
    return written
