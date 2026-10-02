"""`llmbox audit`: every check of the saved results and the built site in one pass, so nobody has to spot problems by
hand. Each finding says what is wrong, where, and what to do; errors make the command exit 1.

Data: every result file reads, the results database matches the files, the newest results have a backup.
Tests (the reference box): no unfinished test is counted; every counted model was tuned (llmbox optimize) and its
speed measured after the tuning (a probe, with a figure near 32k and runs that agree); quality runs are the standard
one (adaptive, 40 minutes, +-2.5); runs keep their saved thinking (loop checks); no run lost many answers to errors or
waits for grading; a run's settings match its recipe; runs of one model agree; no task family fails the Claude models
(a broken task) where local models answered it.
Site (a built folder): no home-folder paths, one speed per model on every page, every internal link resolves, the
build is newer than the results it shows.
"""
from __future__ import annotations

import glob
import json
import os
import re
import statistics
import time

from .hosts import HOME

STANDARD_BUDGET = 40.0    # minutes: a reference-box quality test (llmbox bench --adaptive --budget 40)
SPREAD_MAX = 25.0         # % between a probe's runs at one depth before its figure is called unreliable
ERRORS_MAX = 0.10         # share of a run's answers lost to errors before the run is questioned
RUNS_APART = 15.0         # points between two runs of one model (% of Opus) worth a look
VRAM_IDLE_MAX = 1024      # MiB of the card left idle with the model loaded, without a written reason (the box method)
BACKUP_MARK = "last-backup"


def _f(level: str, area: str, what: str, fix: str = "") -> dict:
    return {"level": level, "area": area, "what": what, "fix": fix}


def check_data(home: str = HOME) -> list[dict]:
    out = []
    files = glob.glob(os.path.join(home, "results", "*", "*.json"))
    bad = []
    for p in files:
        try:
            json.load(open(p))
        except (OSError, ValueError):
            bad.append(os.path.relpath(p, home))
    if bad:
        out.append(_f("error", "data", f"{len(bad)} result file(s) do not read: {', '.join(bad[:5])}", "restore them from the backup"))
    if not os.environ.get("LLMBOX_NO_DB"):
        from . import db
        for h in sorted({os.path.basename(os.path.dirname(p)) for p in files}):
            n_db, n_disk = len(db.files(h)), len(glob.glob(os.path.join(home, "results", h, "*.json")))
            if n_db != n_disk:
                out.append(_f("error", "data", f"the results database has {n_db} {h} records, the folder {n_disk}", "llmbox db sync"))
    newest = max((os.path.getmtime(p) for p in files), default=0)
    mark = os.path.join(home, BACKUP_MARK)
    last = os.path.getmtime(mark) if os.path.exists(mark) else 0
    if newest > last:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "never"
        out.append(_f("warn", "data", f"results newer than the last backup ({when}): ~/.llmbox is the only copy",
                      "llmbox audit --backup (copies results, traces, runs and recipes to the box)"))
    return out


def _box_runs(host: str) -> tuple[list, dict]:
    from . import results
    recs = [dict(r, _path=p) for p, r in results.files(host)]
    return recs, {r.get("created"): r for r in recs if r.get("kind") == "suite"}


def check_tests(host: str = "box") -> list[dict]:
    """A model whose recipe has a caveat (a flawed test the site already explains) gets its findings as notes."""
    from . import irt, report
    from .site.data import _trace_dir
    from .site.words import caveat
    out = _Explained(lambda rid: caveat(host, rid))
    recs, by_created = _box_runs(host)
    pool = report.current_pool()
    ranked = [r for r in report.rows(host) if r["host"].get("id") != "cloud"]
    counted = {}
    for (rid, h), p in pool.items():
        if h == host:
            counted[rid] = sorted({x.get("_run") for x in p["rows"]})
    # unfinished runs must count nowhere
    for rid, runs in counted.items():
        for cr in runs:
            r = by_created.get(cr)
            if r and irt.unfinished(r):
                out.append(_f("error", "tests", f"{rid}: an unfinished run is counted ({cr[:16]}: {irt.unfinished(r)})", "irt.pool must skip it"))
    # measured and finished, yet on no ranking: its answers miss a block (a slow model's 40 minutes reach 7 tasks)
    from . import suite as _suite
    ranked_ids = {r["id"] for r in ranked}
    for rid, runs in counted.items():
        if rid not in ranked_ids:
            have = {x["block"] for x in pool[(rid, host)]["rows"]}
            gaps = [b for b in _suite.WEIGHTS if b not in have]
            if gaps and _on_box(host, rid) is False:   # its file is gone (a retired model): listed as measured, nothing to run
                out.append(_f("info", "tests", f"{rid}: not ranked (no answers in {', '.join(gaps)}) and its file is gone from {host}"))
            elif gaps:
                out.append(_f("warn", "tests", f"{rid}: measured ({len(pool[(rid, host)]['rows'])} answers) but not ranked: no answers in {', '.join(gaps)}",
                              f"llmbox bench {rid} --host {host} --recipe {rid} --adaptive --budget 40 --seed <n> (missing blocks go first)"))
    # the standard test on the box
    for r in recs:
        su, s = r.get("suite") or {}, r.get("summary") or {}
        if r.get("kind") != "suite" or su.get("tier") != "adaptive" or su.get("blocks") or irt.unfinished(r):
            continue
        if (su.get("budget_min") or 0) < STANDARD_BUDGET or (su.get("target") or 5) > irt.STANDARD_TARGET:
            rid = (r.get("recipe") or {}).get("id")
            if r.get("created") in set(counted.get(rid, [])):
                out.append(_f("warn", "tests", f"{rid}: a counted run is not the standard test ({su.get('budget_min')} min, +-{su.get('target')}) "
                                               f"from {r['created'][:16]}", "re-test with --budget 40 --target 2.5"))
    for row in ranked:
        rid = row["id"]
        runs = [by_created[c] for c in counted.get(rid, []) if c in by_created]
        if not runs:
            continue
        newest_run = max(r["created"] for r in runs)
        opts = sorted((r for r in recs if r.get("kind") == "optimize" and (r.get("recipe") or {}).get("id") == rid), key=lambda r: r["created"])
        if not opts:
            out.append(_f("warn", "tests", f"{rid}: never tuned (no llmbox optimize record)", f"llmbox optimize {rid} --host {host} --retune, then a probe"))
        elif opts[0]["created"] > newest_run:
            out.append(_f("warn", "tests", f"{rid}: tuned only after its quality runs ({opts[-1]['created'][:16]})", "re-test it with the tuned settings"))
        p = report.speed_probe(recs, rid, host)
        if not p:
            here = _on_box(host, rid)
            out.append(_f("warn" if here is not False else "info", "speed",
                          f"{rid}: no speed probe; the site shows the figure of its quality run and says so"
                          + ("" if here is not False else " (its file is no longer on the box: it cannot be measured again)"),
                          f"probe it: llmbox bench {rid} --host {host} --speed-probe" if here is not False else ""))
        else:
            if opts and p["created"] < opts[-1]["created"]:
                out.append(_f("warn", "speed", f"{rid}: its speed probe ({p['created'][:16]}) predates its last tuning ({opts[-1]['created'][:16]})", "probe again"))
            bd = ((p.get("summary") or {}).get("speed") or {}).get("by_depth") or {}
            if not any(24 <= report._depth_k(k) < 48 for k in bd):
                out.append(_f("warn", "speed", f"{rid}: its probe has no figure near 32k (the site's 'at 32k')", "probe again (depths 1k, 28k, 87k)"))
            for k, d in bd.items():
                if (d.get("spread_pct") or 0) > SPREAD_MAX:
                    out.append(_f("warn", "speed", f"{rid}: probe runs at {k} disagree by {d['spread_pct']:.0f}% ({d.get('decode_runs')})",
                                  "the median stands; probe again when the box is idle"))
        for r in runs:
            rows = r.get("rows") or []
            err = sum(1 for x in rows if x.get("error") and not irt.real_zero(x))   # too long for its context / out of time: a real 0, counted
            if rows and err >= 3 and err / len(rows) > ERRORS_MAX:
                out.append(_f("warn", "tests", f"{rid}: {err} of {len(rows)} answers of the run from {r['created'][:16]} are errors (left out of the score)", "look at the run's log"))
            pend = sum(1 for x in rows if x.get("pending"))
            if pend:
                out.append(_f("warn", "tests", f"{rid}: {pend} answers of {r['created'][:16]} wait for the reader model", "llmbox grade-pending"))
            d = (r.get("runtime") or {}).get("diff_vs_recipe")
            if d:
                out.append(_f("warn", "tests", f"{rid}: the run from {r['created'][:16]} ran other settings than its recipe: "
                                               + "; ".join(f"{x['flag']} {x['recipe']} -> {x['run']}" for x in d[:3]), "re-test, or record why"))
            if not _trace_dir(r):
                out.append(_f("info", "tests", f"{rid}: the run from {r['created'][:16]} has no saved thinking: its loops are not checked"))
        v = (max(runs, key=lambda r: r["created"]).get("vram") or {})
        notes = " ".join(((_recipe(host, rid).get("notes") or {}).get("lines") or []))
        if (v.get("idle_mib") or 0) > VRAM_IDLE_MAX and not re.search(r"idle|VRAM (left|free)|headroom", notes, re.I):
            out.append(_f("warn", "speed", f"{rid}: {v['idle_mib']} MiB of {v.get('total_mib')} MiB VRAM idle with the model loaded, and its notes say no reason",
                          "spend it (context, KV type, quant) or write why not in the recipe's notes"))
        caps = [((r.get("summary") or {}).get("capability_this_run") or (r.get("summary") or {}).get("capability"), r["created"]) for r in runs]
        caps = [(c, w) for c, w in caps if c is not None]
        k = (row.get("vs_ref") or 0) / row["capability"] if row.get("capability") else 0
        if k and len(caps) > 1 and (max(caps)[0] - min(caps)[0]) * k > RUNS_APART:
            out.append(_f("warn", "tests", f"{rid}: its runs are {(max(caps)[0] - min(caps)[0]) * k:.0f} points apart "
                                           f"({min(caps)[0] * k:.0f}% on {min(caps)[1][:10]} vs {max(caps)[0] * k:.0f}% on {max(caps)[1][:10]})",
                          "check what changed between them (settings, runtime, box)"))
    # a task family the Claude models fail is probably broken: does it touch local scores?
    fam: dict = {}
    for (rid, h), p in pool.items():
        for x in p["rows"]:
            fam.setdefault(irt.family_of(x["id"]), {}).setdefault(h, []).append(x["score"])
    for f, by in sorted(fam.items()):
        c, loc = by.get("cloud") or [], by.get(host) or []
        if c and loc and statistics.mean(c) < 0.5:
            out.append(_f("warn", "tests", f"task family {f}: the Claude models average {statistics.mean(c) * 100:.0f}% and {len(loc)} local answers count",
                          "check the task's expected answers"))
    return list(out)


class _Explained(list):
    """Findings about a model with a caveat become notes: the site already says what is wrong with its test."""
    def __init__(self, why):
        super().__init__()
        self.why = why

    def append(self, f: dict) -> None:
        m = re.match(r"([a-z0-9][a-z0-9.-]*): ", f["what"])
        if m and f["level"] != "error" and self.why(m.group(1)):
            f = dict(f, level="info", what=f["what"] + " (its caveat says why)", fix="")
        super().append(f)


def _recipe(host: str, rid: str) -> dict:
    from . import recipe as rc
    try:
        return rc.load(host, rid)
    except (OSError, ValueError):
        return {}


_ON_BOX: dict = {}


def _on_box(host: str, rid: str) -> bool | None:
    """Whether the recipe's model file is still on the host (None: the host cannot be asked)."""
    if (host, rid) in _ON_BOX:
        return _ON_BOX[(host, rid)]
    from . import hosts, recipe as rc
    try:
        path = rc.load(host, rid)["model"].get("path") or ""
        r = hosts.host_of(hosts.load(host)).run(f"test -e {path!r} && echo yes || echo no", timeout=30)
        v = {"yes": True, "no": False}.get(r.stdout.strip()) if r.returncode == 0 else None
    except Exception:
        v = None
    _ON_BOX[(host, rid)] = v
    return v


def check_site(site: str) -> list[dict]:
    out = []
    pages = glob.glob(os.path.join(site, "*.html")) + glob.glob(os.path.join(site, "r", "*.html"))
    if not pages:
        return [_f("warn", "site", f"no built pages in {site}", "llmbox site")]
    leaks = [os.path.relpath(p, site) for p in pages if re.search(r"/home/[a-z_][\w-]*/|/Users/[A-Za-z][\w.-]*/", open(p, encoding="utf-8").read())]
    if leaks:
        out.append(_f("error", "site", f"{len(leaks)} page(s) show a home folder: {', '.join(leaks[:4])}", "publish paths with ~"))
    names = {os.path.relpath(p, site) for p in glob.glob(os.path.join(site, "**", "*"), recursive=True)}
    broken = {}
    for p in glob.glob(os.path.join(site, "*.html")):
        for h in re.findall(r'href="([^"#?:]+\.html)', open(p, encoding="utf-8").read()):
            if not h.startswith(("http", "/")) and h not in names:
                broken.setdefault(h, os.path.basename(p))
    if broken:
        out.append(_f("warn", "site", f"{len(broken)} link(s) to missing pages: " + ", ".join(f"{k} (from {v})" for k, v in list(broken.items())[:5]),
                      "build the page or drop the link"))
    # one speed per model: the model page's tile, the home ranking and the reference card's page
    home = open(os.path.join(site, "index.html"), encoding="utf-8").read() if os.path.exists(os.path.join(site, "index.html")) else ""
    ref = next(iter(glob.glob(os.path.join(site, "hw-rtx-5070-12gb.html"))), None)
    hw = open(ref, encoding="utf-8").read() if ref else ""
    for p in glob.glob(os.path.join(site, "recipe-*.html")):
        rid = os.path.basename(p)[7:-5]
        page = open(p, encoding="utf-8").read()
        m = re.search(r"<div id='vspd'><span class='sc'>Speed</span><b>(\d+)", page)
        h = re.search(rf"data-rid='{re.escape(rid)}'(?:(?!</tr>).)*?<td class='spd r'><b>(\d+)</b>", home, re.S)
        c = re.search(rf'href="recipe-{re.escape(rid)}\.html">[^<]*</a>(?:(?!</tr>).)*?<td data-h="tok/s"><b>(\d+)</b>', hw, re.S)
        seen = {k: v.group(1) for k, v in (("model page", m), ("home", h), ("RTX 5070 page", c)) if v}
        if len(set(seen.values())) > 1:
            out.append(_f("error", "site", f"{rid}: one model, different speeds: " + ", ".join(f"{k} {v}" for k, v in seen.items()), "one source for the figure"))
    # text broken by a bad replace: an entity without its & ("the “long” speed)ldquo;at 32k" on the home page)
    torn = [os.path.relpath(p, site) for p in pages if re.search(r"[^&#\w](?:ldquo|rdquo|rsquo|lsquo|nbsp|mdash|ndash|middot|hellip|frac34);",
                                                                 open(p, encoding="utf-8").read())]
    if torn:
        out.append(_f("error", "site", f"{len(torn)} page(s) with a torn HTML entity: {', '.join(torn[:4])}", "fix the text in the page's source"))
    # every "at 32k" figure predicted at 32k: a calibration from another depth labelled 32k (Ling's 96k) is wrong on every box
    m = re.search(r"const DATA = (\{.*?\});</script>", home, re.S)
    if m:
        try:
            deep = {rid: r.get("deepK") for rid, r in json.loads(m.group(1)).get("recipes", {}).items() if r.get("deepK") not in (None, 32)}
        except ValueError:
            deep = {}
        if deep:
            out.append(_f("error", "site", "speeds labelled 'at 32k' predicted at another depth: " + ", ".join(f"{k} {v}k" for k, v in list(deep.items())[:5]),
                          "fit.calibration(at_k=32) must predict at 32k"))
    built = os.path.getmtime(os.path.join(site, "index.html")) if home else 0
    newest = max((os.path.getmtime(p) for p in glob.glob(os.path.join(HOME, "results", "*", "*.json"))), default=0)
    if newest > built:
        out.append(_f("warn", "site", "results newer than the built site", "llmbox site"))
    return out


def backup(host: str = "box", home: str = HOME, keep: int = 14) -> str:
    """A second copy of what cannot be measured again (results, saved thinking, run logs, recipes, task bank) on the
    given machine: a dated tar.gz under ~/llmbox-backup (the newest `keep` stay), so a file deleted or broken here
    survives in the copies before it; the mark the data check reads. Only ssh and tar (the box has no rsync)."""
    import subprocess
    from . import hosts
    ssh = hosts.load(host).get("ssh")
    if not ssh:
        raise SystemExit(f"{host} has no ssh address: back up elsewhere")
    dirs = [d for d in ("results", "traces", "runs", "recipes", "irt", "reader-traces", "cloud-runs") if os.path.isdir(os.path.join(home, d))]
    name = f"llmbox-{time.strftime('%Y-%m-%dT%H%M%S')}.tar.gz"
    tar = subprocess.Popen(["tar", "czf", "-", "-C", home, *dirs], stdout=subprocess.PIPE)
    remote = (f"mkdir -p llmbox-backup && cat > llmbox-backup/{name}.part && gzip -t llmbox-backup/{name}.part && "
              f"mv llmbox-backup/{name}.part llmbox-backup/{name} && ls -1t llmbox-backup/llmbox-*.tar.gz | tail -n +{keep + 1} | xargs -r rm -f && "
              f"du -h llmbox-backup/{name} | cut -f1")
    r = subprocess.run(["ssh", ssh, remote], stdin=tar.stdout, capture_output=True, text=True, timeout=3600)
    tar.stdout.close()
    if tar.wait() or r.returncode:
        raise SystemExit(f"backup failed: {(r.stderr or r.stdout).strip()[-300:] or 'tar failed'}")
    open(os.path.join(home, BACKUP_MARK), "w").write(time.strftime("%Y-%m-%dT%H:%M:%S") + f" {ssh}:llmbox-backup/{name}\n")
    return f"{', '.join(dirs)} -> {ssh}:llmbox-backup/{name} ({r.stdout.strip()})"


def run(host: str = "box", site: str | None = None) -> list[dict]:
    out = check_data() + check_tests(host)
    if site:
        out += check_site(os.path.expanduser(site))
    return out


def report_text(found: list[dict]) -> str:
    if not found:
        return "audit: nothing found"
    order = {"error": 0, "warn": 1, "info": 2}
    lines = []
    for f in sorted(found, key=lambda f: (order[f["level"]], f["area"])):
        lines.append(f"{f['level'].upper():5s} {f['area']:6s} {f['what']}" + (f"\n             -> {f['fix']}" if f["fix"] else ""))
    n = {k: sum(1 for f in found if f["level"] == k) for k in order}
    return "\n".join(lines) + f"\naudit: {n['error']} error(s), {n['warn']} warning(s), {n['info']} note(s)"
