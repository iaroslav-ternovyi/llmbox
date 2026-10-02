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
from .words import _kind, BLOCKS, esc, family, set_variants


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
    written = copy_assets(out_dir)
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
        return {"name": f'{model_name(r)} {variant(r["id"], r.get("file"))}', "score": r.get("vs_ref"), "cap": r.get("capability"),
                # speeds people measured, per hardware class: [short, @32k, @80k, machines] (llmbox pick shows its own class)
                "measured": {c["class"]: [c["t2"], c["t32"], c["t80"], c["machines"]] for c in cs.get(r["id"], [])},
                "range": [round(r["ci"][0] * k, 1), round(r["ci"][1] * k, 1)] if r.get("ci") and k else None,
                "uses": {n: round(v, 1) for n, bs in GROUPS if (v := _wavg(r.get("blocks_vs_ref"), bs)) is not None}}
    meta = {r["id"]: _meta(r) for r in rs}
    written += registry.export(host, order, out_dir, meta)   # recipes/: what `llmbox recipe pull` installs
    board, run_pages, images, noindex = _people_runs(out_dir, host, order, meta, {rid: os.path.basename((local[rid].get("model") or {}).get("file") or "") for rid in order})
    written += run_pages
    written += _entries(out_dir, board, meta)
    from .hwmap import map_panel
    from .people import users as _users
    from ..hosts import HOME as _HOME
    written.append(home(out_dir, host, suite_version, tier, all_rs, data, map_panel(board, _users(os.path.join(_HOME, "intake", "users.json")))))
    ref_row = next((r for r in all_rs if r["host"].get("id") == "cloud" and ref and r["id"] == (ref.get("recipe") or {}).get("id")), None)
    w("method.html", method_page(ref, opts, set(local), ref_row, rs, look))
    from .install import account_page, install_page, privacy_page, terms_page
    from ..submit import DEFAULT_SERVER
    w("install.html", install_page())
    w("account.html", account_page(DEFAULT_SERVER.rstrip("/")))
    w("privacy.html", privacy_page())
    w("terms.html", terms_page())
    from .people import pages as people_pages, users
    from ..hosts import HOME
    for name, html_ in people_pages({r["id"]: model_name(r) for r in rs}, cs, users(os.path.join(HOME, "intake", "users.json"))).items():
        w(name, html_)
    try:
        np_ = new_page(rs, data, host)
    except Exception as e:   # the list needs Hugging Face; the rest of the site must not depend on it
        np_ = None
        print(f"new.html skipped: {e}")
    if np_:
        w("new.html", np_)
    from ..submit import DEFAULT_SERVER as _api
    # Pages serves 404.html at any missing address; at /r/<id> it is a run sent a moment ago: queue.js says where it stands
    w("404.html", _page("llmbox · page not found", "", '<section class="panel hd" id="notfound"><div><h1>Page not found</h1><p class="q" style="margin-top:8px">'
                        'The model or run may have been renamed. <a href="index.html">The ranking</a> · <a href="new.html">new models</a> · '
                        '<a href="compare.html">compare</a> · <a href="method.html">how scores work</a></p></div></section>'
                        f'<section class="panel hd" id="queue" hidden data-api="{esc(_api.rstrip("/"))}"><div><h1>Your result is on its way</h1>'
                        '<p class="q" id="qline" aria-live="polite" style="margin-top:8px"></p></div></section>', ("pages.css",), ("queue.js",), base=True))
    from .publish import finish
    written += finish(out_dir, written, images, noindex)   # canonical, social preview, CSP per page; sitemap.xml, robots.txt, _headers
    # pages of earlier builds this one did not write (a run that no longer counts, a renamed recipe): only the site's own
    # kinds of file, so a folder with other things in it keeps them
    keep = {os.path.relpath(p, out_dir) for p in written}
    for f in os.listdir(out_dir):
        if f not in keep and re.fullmatch(r"(index|new|method|compare|people|install|account|privacy|terms|404|(recipe|run|hardware|compare|hw)-.+|u-[0-9a-f]{8})\.html|[a-z0-9]+\.(css|js)", f):
            os.remove(os.path.join(out_dir, f))
    rdir = os.path.join(out_dir, "r")
    for f in os.listdir(rdir) if os.path.isdir(rdir) else []:   # a run page whose records are gone (card.draw keeps the images)
        if f"r/{f}" not in keep and re.fullmatch(r"[0-9a-f]{12}\.html", f):
            os.remove(os.path.join(rdir, f))
    return written


def _entries(out_dir: str, board: dict, meta: dict) -> list[str]:
    """A page and a feed per picker entry (hw.py); the feeds' new events are logged once both are written."""
    from ..hosts import HOME
    from ..public import site as _site
    from . import board as B
    from .hw import feed, hw_page
    from .people import users
    site, us = _site(), users(os.path.join(HOME, "intake", "users.json"))
    events, new = B.events(board)
    os.makedirs(os.path.join(out_dir, "feeds"), exist_ok=True)
    out = []
    for name, e in board["entries"].items():
        for path, text in ((f"hw-{e['slug']}.html", hw_page(name, board, meta, us, site)), (f"feeds/{e['slug']}.xml", feed(name, board, events, meta, site))):
            open(os.path.join(out_dir, path), "w", encoding="utf-8").write(text)
            out.append(os.path.join(out_dir, path))
    B.append(new)
    return out


def _people_runs(out_dir: str, host: str, order: list[str], meta: dict, files: dict) -> tuple[dict, list[str], dict, set]:
    """People's runs: the board (llmbox/site/board.py), each run's card (card.py) and page r/<id>.html, and the
    "removed" page of each run taken off. Returns (the board, files written, {page: its card as social preview},
    noindex pages)."""
    import json
    from .. import fit as F, registry
    from ..public import site as _site
    from . import board as B, card as C
    from .run import removed_page, user_run_page
    gone = C.removed()

    def ours(rec):   # a speed or quality record of a listed model, with the file the model list names
        rid = (rec.get("recipe") or {}).get("id")
        f = os.path.basename((rec.get("model") or {}).get("file") or (rec.get("model") or {}).get("path") or "")
        return rid in files and (not f or not files[rid] or f == files[rid])
    box = [r for _p, r in report.results.files(host) if r.get("kind") == "speed" and ours(r)]
    people = [r for _p, r in report.results.files("community") if ours(r) and (r.get("submission") or {}).get("id") not in gone]
    models = []
    for rid in order:
        try:
            p = registry.published(host, rid)
            models.append((dict(meta.get(rid) or {}, id=rid), p, F.shape_for(p)))
        except (OSError, ValueError, SystemExit) as e:
            print(f"board: {rid} left out: {e}")
    b = B.build(box, [r for r in people if r.get("kind") == "speed"], models)
    recs: dict = {}
    for r in people:
        recs.setdefault(r["submission"]["id"], []).append(r)
    site = _site()
    st = C.draw(b, recs, meta, site, out_dir)
    print("cards: not drawn here (no rsvg-convert or IBM Plex)" if st.get("unavailable")
          else f"cards: {st['drawn']} drawn, {st['failed']} failed, {st['waiting']} waiting for grades")
    machines = [[n, e["slug"], e["kind"], (e.get("best") or {}).get("name"),
                 (e.get("best") or {}).get("measured") or (e.get("best") or {}).get("predicted"), bool((e.get("best") or {}).get("measured")), e["testable"]]
                for n, e in b["entries"].items()]
    rdir = os.path.join(out_dir, "r")
    os.makedirs(rdir, exist_ok=True)
    written, images, noindex = [], {}, set()
    for sid in sorted(b["runs"]):
        if not re.fullmatch(r"[0-9a-f]{12}", sid or "") or sid in gone:
            continue
        live = C.spec(b, sid, recs.get(sid) or [], meta, site, wait=False)
        if not live:
            continue
        fp = os.path.join(C.CARDS, f"{sid}.json")
        frozen = json.load(open(fp)) if os.path.exists(fp) else None
        state = ("drawn" if os.path.exists(os.path.join(rdir, f"{sid}.png")) else "failed" if os.path.exists(os.path.join(C.CARDS, f"{sid}.failed"))
                 else "retrying" if os.path.exists(os.path.join(C.CARDS, f"{sid}.tries")) or st.get("unavailable") else "waiting")
        p = os.path.join(rdir, f"{sid}.html")
        open(p, "w", encoding="utf-8").write(user_run_page(sid, b, recs[sid], live, frozen, state, machines, site))
        written.append(p)
        if state == "drawn":
            from .run import _alt
            images[f"r/{sid}.html"] = (f"r/{sid}.png", _alt(frozen or live))
    for sid, reason in sorted(gone.items()):
        if re.fullmatch(r"[0-9a-f]{12}", sid):
            p = os.path.join(rdir, f"{sid}.html")
            open(p, "w", encoding="utf-8").write(removed_page(sid, reason))
            written.append(p)
            noindex.add(f"r/{sid}.html")
    return b, written, images, noindex
