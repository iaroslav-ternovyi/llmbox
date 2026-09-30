"""llmbox command line."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

from . import hosts


def cmd_host(a: argparse.Namespace) -> None:
    if a.action == "add":
        prof = hosts.detect(a.name, a.ssh, ram_bw=a.ram_bw, measure=not a.no_measure)
        _print_host(prof)
    elif a.action == "info":
        _print_host(hosts.load(a.name))
    elif a.action == "list":
        for n in hosts.names():
            p = hosts.load(n)
            g = p["hw"]["gpus"][0]["name"] if p["hw"]["gpus"] else "no GPU"
            print(f"{n:12s} {p.get('ssh') or 'local':28s} {g:28s} {p['hw']['ram_mib']/1024:5.0f} GiB RAM")


def _print_host(p: dict) -> None:
    hw = p["hw"]
    print(f"{p['name']}  ({p.get('ssh') or 'local'})  {hw['hostname']}  {hw['os']}")
    print(f"  CPU  {hw['cpu']['model']}  ({hw['cpu']['threads']} threads)")
    print(f"  RAM  {hw['ram_mib']/1024:.1f} GiB   read bandwidth: {p['ram_bw'].get('ram_read_gbs') or '?'} GB/s ({p['ram_bw'].get('source')})")
    for g in hw["gpus"]:
        if g.get("unified"):
            print(f"  GPU  {g['name']}  {g.get('gpu_cores')} cores, {g['vram_mib']/1024:.1f} GiB of the unified memory  bandwidth {p.get('vram_bw_gbs') or '?'} GB/s")
            continue
        print(f"  GPU  {g['name']}  {g['vram_mib']/1024:.1f} GiB  driver {g.get('driver', '?')}  PCIe {g.get('pcie_gen', '?')}x{g.get('pcie_width', '?')}"
              f"  power {g.get('power_limit_w', '?')} W (default {g.get('power_default_w', '?')})  bandwidth {p.get('vram_bw_gbs') or '?'} GB/s")
    for r in hw["runtimes"]:
        print(f"  llama-server  {r['path']}")
    if hw.get("llama_swap", {}).get("config"):
        running = [m.get("model") for m in hw["llama_swap"].get("running", [])]
        print(f"  llama-swap    {hw['llama_swap']['config']}  running: {', '.join(running) or '-'}")
    print(f"  disk free {hw['disk_free_gib']} GiB")


def cmd_scout(a: argparse.Namespace) -> None:
    from . import scout
    prof = hosts.load(a.host)
    hw = hosts.spec(prof, ram_headroom_mib=a.headroom)
    rows = scout.scout(a.repo, hw, kv_type=a.kv, depth=a.depth, quants=a.quant)
    if a.json:
        json.dump([{"file": r.file.name, "quant": r.file.quant, "size": r.file.size, "sha256": r.file.sha256,
                    "fits": r.plan.fits, "ctx": r.max_ctx, "gpu_expert_frac": round(r.plan.gpu_expert_frac, 3),
                    "decode_tps": round(r.plan.decode_tps, 1), "decode_tps_at_depth": round(r.at_depth, 1)} for r in rows],
                  sys.stdout, indent=1)
        print()
    else:
        print(scout.render(a.repo, rows, hw))


def _ctx_arg(v: str | None) -> int | None:
    if not v:
        return None
    v = v.lower().strip()
    return int(float(v[:-1]) * 1024) if v.endswith("k") else int(v)


def cmd_fit(a: argparse.Namespace) -> None:
    """Fit the hardware layer of a recipe to a host (or to hardware described on the command line)."""
    import re as _re
    from . import estimate as E, fit as F, recipe as rc
    r = rc.load(a.src, a.recipe)
    src_prof = hosts.load(a.src) if os.path.exists(hosts.path(a.src)) else None   # none for the registry: its [reference] calibrates
    prof, cores = None, None
    if a.gpu:
        vram = int(a.vram_gb * 1024) if a.vram_gb else hosts.gpu_vram(a.gpu)
        if not vram:
            raise SystemExit(f"unknown VRAM for {a.gpu!r}: pass --vram-gb")
        hw = E.HostSpec(vram_mib=vram, ram_mib=int(a.ram_gb * 1024), ram_bw_gbs=a.ram_bw, vram_bw_gbs=hosts.gpu_bw(a.gpu) or 500.0)
        target, cores = f"{a.gpu} · {a.ram_gb:g} GB RAM @ {a.ram_bw:g} GB/s (what-if)", a.cores
    else:
        prof = hosts.load(a.host or a.src)
        hw = hosts.spec(prof)
        target = prof["name"]
        cpu = prof["hw"].get("cpu") or {}
        cores = a.cores or cpu.get("cores") or cpu.get("threads")
    same = prof is not None and prof["name"] == a.src
    shape = F.shape_for(r, host=hosts.host_of(src_prof) if src_prof else None)
    cal = F.calibration(r, shape, a.src) if src_prof or r.get("reference") else F.Calibration()
    f = F.fit(r, shape, hw, cores=cores, cal=cal, ctx=_ctx_arg(a.ctx), same_host=same)
    if a.json:
        json.dump({"recipe": a.recipe, "target": target, "fits": f.fits, "ctx": f.ctx, "want_ctx": f.want_ctx, "ubatch": f.ubatch,
                   "threads": f.threads, "cpu_affinity": f.cpu_affinity, "tps": round(f.tps, 1), "tps_deep": round(f.tps_deep, 1),
                   "deep_k": f.deep_k, "gpu_expert_frac": round(f.plan.gpu_expert_frac, 3) if f.plan else None,
                   "calibration": f.calibration.source, "warnings": f.warnings, "options": f.options, "alternatives": f.alternatives,
                   "hardware": F.layer(F.apply(r, f), F.HARDWARE) if f.fits else None}, sys.stdout, indent=1)
        print()
    else:
        print(F.render(a.recipe, r, f, target, shape, hw))
    if not a.write:
        return
    if not prof:
        raise SystemExit("--write needs a registered host (--host); a what-if fit has nowhere to go")
    if not f.fits:
        raise SystemExit("nothing written: the model does not fit")
    out = os.path.join(rc.recipes_dir(prof["name"]), f"{a.recipe}.toml")
    if os.path.exists(out) and not a.force:
        raise SystemExit(f"\n{out} exists (the recipe itself on its own host); --force to replace it with the fitted copy")
    runtimes = sorted(prof["hw"].get("runtimes") or [], key=lambda x: int((_re.search(r"build (\d+)", x.get("version", "")) or [0, 0])[1]))
    fitted = F.apply(r, f, models_dir=None if same else prof["hw"].get("models_dir_guess"),
                     server=None if same or not runtimes else runtimes[-1]["path"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(F.to_toml(fitted, header=F.stamp(target, hw, f)))
    print(f"\nwrote {out}")


def cmd_install(a: argparse.Namespace) -> None:
    from . import install
    raise SystemExit(install.run(a.recipe, a.src, a.host or a.src, dry_run=not a.apply, force=a.force, unload=a.unload))


def cmd_tune(a: argparse.Namespace) -> None:
    from . import fit as F, recipe as rc, tune
    for rid in a.recipes:
        if a.plan:
            r = rc.load(a.host, rid)
            print(f"{rid}:")
            for n, ov, ok in tune.variants(r, F.shape_for(r, host=hosts.host_of(hosts.load(a.host))), cores=tune.cores_of(a.host)):
                print(f"  {n:32s} {' '.join(ov):50s} {'' if ok else '(reported, not chosen: it trades long documents for speed)'}")
            continue
        tune.run(a.host, rid)


def cmd_verify(a: argparse.Namespace) -> None:
    from . import results, verify
    todo = [(p, None) for p in a.results] or [(p, r) for p, r in results.files() if r.get("kind") == "suite"]
    tot: dict = {}
    for path, rec in todo:
        res = verify.run_one(path, rec)
        name = os.path.basename(path)
        if res.get("error"):
            print(f"{name}: {res['error']}")
            continue
        verify.save(res)
        for k, v in res["counts"].items():
            tot[k] = tot.get(k, 0) + v
        print(f"{name}: v{res['version']} " + ", ".join(f"{k} {v}" for k, v in sorted(res["counts"].items())) + f"  ({res['seconds']} s)")
        for r in res["rows"]:
            if r["status"] in ("mismatch", "error") or (a.verbose and r["status"] == "unverifiable"):
                print(f"    {r['status']:12s} {r['id']:32s} saved {r.get('client')} server {r.get('server')} {r.get('reason') or ''}")
    print("total: " + ", ".join(f"{k} {v}" for k, v in sorted(tot.items())))


def cmd_db(a: argparse.Namespace) -> None:
    from . import db
    if a.action == "sync":
        print(f"{db.sync(progress=print)} result file(s) taken in -> {db.DB}")
    elif a.action == "stats":
        st = db.stats()
        for k, v in st.items():
            print(f"  {k:18s} {v}")
    elif a.action == "sql":   # read-only: the database is derived from the result files and must not drift from them
        db.sync()
        import sqlite3
        c = sqlite3.connect(f"file:{db.DB}?mode=ro", uri=True)
        try:
            cur = c.execute(a.query)
        except sqlite3.Error as e:   # a typo in a column name: say what there is instead of a traceback
            tables = {t: [r[1] for r in c.execute(f"PRAGMA table_info({t})")] for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")}
            raise SystemExit(f"sql error: {e}\n\n" + "\n".join(f"  {t}: {', '.join(cs)}" for t, cs in tables.items()))
        cols = [d[0] for d in cur.description or []]
        print("\t".join(cols))
        for r in cur.fetchmany(a.limit):
            print("\t".join("" if x is None else str(x) for x in r))


def cmd_watch(a: argparse.Namespace) -> None:
    from . import watch
    if a.list:
        for e in watch.events(a.list):
            print(f"{e['at'][:16]}  {e['type']:12s} {e['title']} - {e['detail'][:100]}")
        return
    watch.run()


def cmd_grade_pending(a: argparse.Namespace) -> None:
    from . import pending
    todo = a.results or pending.records()
    print(f"{len(todo)} result(s) with pending explanations")
    for f in todo:
        print(f"== {os.path.basename(f)}")
        print("  done" if pending.grade(f) else "  still pending")


def cmd_optimize(a: argparse.Namespace) -> None:
    from . import optimize
    for rid in a.recipes:
        print(f"== {rid}", flush=True)
        optimize.run(a.host, rid, retune=a.retune, out=lambda m: print(m, flush=True), endpoint=a.endpoint)


def cmd_probe(a: argparse.Namespace) -> None:
    """Re-measure only the 1-stream speed of a served recipe (after tune); quality stays from its suite run."""
    import json as _json
    from . import bench, recipe as rc, results, runinfo
    rid = a.recipe or a.model
    cap = runinfo.begin(a.host, a.endpoint, a.model)
    sp = bench.speed_probe(a.endpoint, a.model)
    r = rc.load(a.host, rid)
    info = runinfo.end(cap, r)
    rec = results.new("probe", hosts.load(a.host), recipe=r, model={k: r["model"].get(k) for k in ("hf_repo", "file", "path", "sha256")})
    rec["summary"] = {"speed": sp}
    rec["runtime"], rec["telemetry"] = info["runtime"], info["telemetry"]
    tuned = os.path.join(hosts.HOME, "tuned", a.host, f"{rid}.json")
    rec["tuned"] = bool(os.path.exists(tuned) and (_json.load(open(tuned)).get("applied_flags")))
    print(f"{rid}: {sp['decode_tps']} tok/s short, " + " | ".join(f"{k} {v['decode_tps']}" for k, v in (sp.get("by_depth") or {}).items())
          + f"  ({(info['runtime'] or {}).get('variant', '?')})")
    print(f"  saved {results.save(a.host, rec)}")


def cmd_irt(a: argparse.Namespace) -> None:
    """Calibrate the task families on every full run, report them, or replay adaptive runs on recorded answers."""
    from . import irt, suite
    hs = (a.content_hash or suite.content_hash()).split(",")   # several: versions whose tasks are the same
    ch = irt.canonical(hs[-1])   # runs of every suite content with the same tasks (irt.SAME_TASKS) share its bank
    # the current suite: every answer to a family that is unchanged in it, from any version and any run (irt.pool);
    # an explicit --content-hash: the full runs of exactly that content, as before
    resp = irt.pooled_responses() if not a.content_hash else irt.responses(hs)
    models = sorted({r.model for r in resp})
    if not resp:
        raise SystemExit(f"no full runs of suite content {ch}; pass --content-hash")
    if a.action == "calibrate":
        # floor 0.5: with few models the held-out likelihood barely separates 0.3 from 0.5, and a small tau lets the
        # other blocks overrule a model's own answers in one (gpt-oss: long documents 50 with the rest 83-100)
        tau, cv = irt.choose_tau(resp, suite.WEIGHTS, taus=(0.5, 0.7, 1.0, 1.4)) if len(models) >= 4 else (1.0, {})
        bank = irt.calibrate_blocks(resp, suite.WEIGHTS, fixed_tau=tau)
        if cv:
            print("tau by cross-validation (held-out log-likelihood): " + ", ".join(f"{k}: {v:.1f}" for k, v in cv.items()))
        print(f"calibrated {len(bank.a)} families on {len(models)} models ({len(resp)} answers), tau {bank.tau:.2f} "
              f"-> {irt.save(bank, ch, len(models))}")
        for m, t in sorted(bank.theta.items(), key=lambda x: -x[1]):
            dv = bank.dev.get(m, {})
            print(f"  {m:22s} theta {t:5.2f}  " + " ".join(f"{b[:4]} {v:+.1f}" for b, v in dv.items()))
    elif a.action == "rescore":   # adaptive runs again with the current bank; fixed runs: the IRT estimate beside their own
        bank = irt.load(ch)
        if not bank:
            raise SystemExit(f"no bank for {ch}: run `llmbox irt calibrate` first")
        import glob as _g
        for path in a.paths or sorted(_g.glob(os.path.join(hosts.HOME, "results", "*", "*-suite-*.json"))):
            rec = json.load(open(path))
            su = rec.get("suite") or {}
            if irt.canonical(su.get("content_hash")) != ch or su.get("blocks"):
                continue
            sc = irt.score_rows(bank, rec["rows"])
            s = rec["summary"]
            rid = (rec.get("recipe") or {}).get("id") or "?"
            print(f"{os.path.basename(path)[:17]} {rid:18s} {su.get('tier'):8s} run {s['capability']:5.1f} "
                  f"({s['capability_ci95'][0]:.0f}-{s['capability_ci95'][1]:.0f})  irt {sc['capability']:5.1f} ({sc['ci95'][0]:.0f}-{sc['ci95'][1]:.0f})  n {sc['n']}")
            if a.write and su.get("tier") == "adaptive":
                s.setdefault("capability_published", s["capability"])
                s.update(capability=sc["capability"], capability_ci95=sc["ci95"], blocks=sc["blocks"])
                s.setdefault("irt", {})["rescored_with"] = {"content_hash": ch, "models": len(bank.theta), "tau": bank.tau}
                json.dump(rec, open(path, "w"), indent=1)
    elif a.action == "report":
        bank = irt.load(ch) or irt.calibrate(resp, suite.WEIGHTS)
        print(f"{'family':28s} {'a':>5s} {'b':>6s} {'sec':>6s} {'info/min at 0.5':>16s}")
        for f in sorted(bank.a, key=lambda f: -bank.info(f, 0.5) / bank.seconds[f]):
            print(f"{f:28s} {bank.a[f]:5.2f} {bank.b[f]:6.2f} {bank.seconds[f]:6.0f} {60 * bank.info(f, 0.5) / bank.seconds[f]:16.3f}")
    elif a.action == "simulate":   # leave-one-model-out replay: calibrate without the model, test it adaptively on its answers
        for m in [x for x in models if not x.startswith("claude")]:
            bank = irt.calibrate([r for r in resp if r.model != m], suite.WEIGHTS)
            mine = {r.family: r for r in resp if r.model == m}
            _t, _s, w = irt.posterior(bank, [(f, r.score) for f, r in mine.items()])
            full = irt.capability_interval(bank, w)
            traj = irt.simulate(bank, {f: r.score for f, r in mine.items()}, {f: r.seconds for f, r in mine.items()}, budget_s=a.budget * 60)
            last = traj[-1] if traj else {}
            print(f"{m:22s} all {len(mine)} tasks: {full[0]:5.1f} ({full[1]:.0f}-{full[2]:.0f})   adaptive {last.get('minutes')} min, "
                  f"{last.get('n')} tasks: {last.get('cap')} ({last.get('lo')}-{last.get('hi')})")


def cmd_loops(a: argparse.Namespace) -> None:
    import os
    from . import loops
    if a.action == "extract":
        cases = loops.extract(os.path.expanduser(a.transcripts), min_tokens=a.min_tokens)
        loops.save_cases(cases, a.cases)
        print(f"{len(cases)} loop cases -> {a.cases}")
        return
    cases = loops.pick(loops.load_cases(a.cases), a.limit)
    tmpl = json.load(open(os.path.expanduser(a.request_template)))["main"]
    results = []
    out = open(a.out, "a") if a.out else None
    print(f"replaying {len(cases)} loop contexts on {a.model} (cap {a.cap} tokens, {a.samples} sample(s) each)", flush=True)
    for c in cases:
        for i in range(a.samples):
            r = loops.replay_one(a.endpoint, a.model, tmpl, c, a.cap)
            r.update(model=a.model, label=a.label or a.model, sample=i)
            results.append(r)
            if out:
                out.write(json.dumps(r) + "\n"); out.flush()
            print(f"  {'LOOP' if r['looped'] else 'ok  '} {r['output_tokens']:6d} tok {r['seconds']:6.0f}s  "
                  f"markers {r['markers_per_1k']:5.2f}/1k  tail-zlib {r['tail_compress']}  {c.id}", flush=True)
    print("summary:", json.dumps(loops.summarize(results)))


def cmd_pick(a: argparse.Namespace) -> None:
    from . import pick
    if a.pull or not os.path.exists(os.path.join(hosts.HOME, "recipes", "registry", "index.json")):
        from . import registry
        registry.pull(a.url or registry.DEFAULT_URL)
    what_if = (a.gpu, a.vram_gb, a.ram_gb, a.ram_bw) if a.gpu else None
    if not what_if and not a.host:
        a.host = hosts.default_host()
        if not a.host:
            raise SystemExit("which machine? --host <name> (llmbox host list), or describe one: --gpu 'RTX 4090' --ram-gb 64 --ram-bw 60")
    raise SystemExit(pick.run(a.host, a.use, what_if))


def cmd_test(a: argparse.Namespace) -> None:
    """The measurement of a model on this machine: speed, a quality test (10 minutes, or 40 with --full), where it stands
    against machines like it and against the model's published score, then - on a yes - the upload."""
    import random
    from . import irt, recipe as rc, submit, suite
    host = a.host or _only_host()
    try:
        rc.load(host, a.recipe)
    except OSError:
        raise SystemExit(f"{a.recipe} is not installed on {host}: llmbox install {a.recipe} --from registry --host {host} --apply")
    ch = irt.canonical(suite.content_hash())
    if ch not in irt.RELEASES:
        raise SystemExit(f"this llmbox's tasks ({suite.VERSION}, {ch}) are not a released version: update llmbox")
    if not irt.load(ch):
        raise SystemExit("no task bank for this version here: llmbox recipe pull")
    server = a.server or submit.DEFAULT_SERVER
    seed = None if a.no_submit else submit.fresh_seed(server)
    if seed is None and not a.no_submit:
        print(f"{server} did not answer: the test runs on a seed of its own, and its answers are filed but not pooled")
    from . import account
    if not a.no_submit and not account.key_for(server):
        print("not signed in: the quality run is filed but counts toward the model's score only from an account (llmbox login)")
    seed = seed if seed is not None else random.randrange(10**6, 10**9)
    import time as _t
    t0 = _t.time()
    print(f"1/3 speed of {a.recipe} on {host}", flush=True)
    main(["speed", a.recipe, "--host", host, "--depth", "32000", "--depth", "80000", "--unload"])
    budget = a.budget or (40 if a.full else 10)
    print(f"\n2/3 quality: adaptive test, {budget:g} minutes" + ("" if a.full else " (--full: 40 minutes, a narrower range)"), flush=True)
    from . import serving
    with serving.served(host, a.recipe) as url:   # llmbox serves the recipe itself: no llama-swap needed
        print(f"  serving {a.recipe} at {url}", flush=True)
        main(["bench", a.recipe, "--host", host, "--recipe", a.recipe, "--endpoint", url, "--adaptive", "--budget", str(budget),
              "--target", "2.5", "--seed", str(seed), "--speed-probe"])
    from . import results
    mine = [(p, r) for p, r in results.files(host) if os.path.getmtime(p) >= t0 and r.get("kind") in submit.KINDS
            and (r.get("recipe") or {}).get("id") == a.recipe]   # this test's records only, not the machine's history
    print("\n3/3 where this stands", flush=True)
    _standing(host, a.recipe, [r for _p, r in mine])
    if a.no_submit:
        print("not sent (--no-submit): `llmbox submit` sends it later")
        return
    submit.ask_and_send([p for p, _r in mine], host, server, yes=a.yes)


def cmd_run(a: argparse.Namespace) -> None:
    """Serve an installed recipe (OpenAI-compatible) until Ctrl-C, or in the background: no llama-swap needed."""
    import time as _t
    from . import serving
    host = a.host or _only_host()
    if a.background:
        s = serving.start_background(host, a.recipe, port=a.port)
        print(f"{a.recipe} on {host}: {s['url']}/v1 (model name {a.recipe}), running in the background - `llmbox stop {a.recipe}` ends it")
        return
    s = serving.start(host, a.recipe, port=a.port)
    print(f"{a.recipe} on {host}: {s['url']}/v1  (OpenAI-compatible; model name: {a.recipe})\n"
          f"  loaded in {s.get('load_seconds')} s, log {s['log']} on {host}; Ctrl-C stops it", flush=True)
    try:
        while True:
            _t.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        serving.stop(host, s["pid"])
        print("stopped")


def cmd_update(a: argparse.Namespace) -> None:
    """The newest llmbox, the way it was installed (the installer's venv, or pip), and the newest list of models."""
    venv = os.path.expanduser("~/.llmbox/venv/bin/pip")
    pip = [venv] if os.path.exists(venv) else [sys.executable, "-m", "pip"]
    r = subprocess.run(pip + ["install", "-q", "--upgrade", "git+https://github.com/iaroslav-ternovyi/llmbox"])
    if r.returncode:
        raise SystemExit("the update failed (see above)")
    from . import registry
    registry.pull(registry.DEFAULT_URL, out=lambda m: None)
    print("llmbox is up to date, and so is its list of models: `llmbox` shows if something better fits this computer now")


def cmd_doctor(a: argparse.Namespace) -> None:
    from . import doctor
    raise SystemExit(doctor.run(a.host))


def cmd_start(a: argparse.Namespace) -> None:
    from . import wizard
    raise SystemExit(wizard.main(yes=a.yes, model=a.model, host=a.host, plan_only=a.plan))


def cmd_stop(a: argparse.Namespace) -> None:
    from . import serving
    gone = [s for s in serving.running() if not a.recipes or s["rid"] in a.recipes]
    for s in gone:
        serving.stop_background(s["host"], s["rid"])
        print(f"stopped {s['rid']} on {s['host']}")
    if not gone:
        print("nothing llmbox started is running")


def _standing(host: str, rid: str, recs: list[dict]) -> None:
    """This machine against machines like it (the registry's per-class medians), and this run's quality against the
    model's published score: the same file and settings should score the same, so a clear miss means a setup problem."""
    from . import hwclass, pick, results as res
    e = next((x for x in pick.index()["recipes"] if x["id"] == rid), {}) if os.path.exists(
        os.path.join(hosts.HOME, "recipes", "registry", "index.json")) else {}
    cls = hwclass.of_host(res.host_fingerprint(hosts.load(host)))
    sp = next((r["speed"] for r in recs if r.get("kind") == "speed" and (r.get("speed") or {}).get("decode_tps")), None)
    same = (e.get("measured") or {}).get(cls)
    if sp:
        print(f"  speed: {sp['decode_tps']:.0f} tokens/s" + (f"; machines like it ({hwclass.label(cls)}): median {same[0]:.0f} of {same[3]}"
                                                            if same else "; the first of its kind here - nobody has sent this hardware yet"))
    q = next((r for r in recs if r.get("kind") == "suite"), None)
    if q and any(x.get("pending") for x in q.get("rows") or []):
        print("  (its explanations are graded by the server's reader model once sent: the score here leaves them out)")
    if q and e.get("range"):
        s = q["summary"]
        lo, hi = s.get("capability_ci95") or [None, None]
        k = e["score"] / e["cap"] if e.get("cap") else 1.0   # capability scale -> the site's % of Claude Opus
        ok = lo is not None and hi is not None and lo * k <= e["range"][1] and hi * k >= e["range"][0]
        print(f"  quality: this run {s.get('capability', 0) * k:.0f}% of Claude Opus (range {lo * k:.0f}-{hi * k:.0f}) vs the model's {e['score']:.0f}% "
              f"({e['range'][0]:.0f}-{e['range'][1]:.0f}): " + ("consistent - your setup gives the published quality" if ok else
                                                                "NOT consistent - check the settings (llmbox recipe check) and the llama.cpp build"))


def _only_host() -> str:
    h = hosts.default_host()
    if not h:
        raise SystemExit("which machine? --host <name> (llmbox host list), or `llmbox` to set this computer up")
    return h


def cmd_account(a: argparse.Namespace) -> None:
    """login / logout / whoami / profile / forget against the intake server."""
    from . import account, registry, submit
    server = a.server or submit.DEFAULT_SERVER
    page = lambda h: f"{registry.DEFAULT_URL.rstrip('/')}/{h}.html"
    if a.cmd == "login":
        acc = account.login(server)
        print(f"signed in as {acc.get('login')} - your profile page: {page(acc['handle'])} "
              f"(shows {'your GitHub name' if acc.get('public') else 'the handle ' + acc['handle'] + ', not your GitHub name; llmbox profile --public shows it'})")
    elif a.cmd == "logout":
        print("signed out (the key is removed from this machine)" if account.logout(server) else "not signed in")
    elif a.cmd == "whoami":
        me = account.whoami(server)
        print(f"{me['login']} ({me['handle']}, {'public' if me['public'] else 'private'} profile): {me['submissions']} submissions, "
              f"{me['records']} results - {page(me['handle'])}" if me else "not signed in: llmbox login")
    elif a.cmd == "profile":
        if a.public == a.private:
            raise SystemExit("llmbox profile --public (show your GitHub name) or --private (only the handle)")
        me = account.set_public(server, a.public)
        print(f"profile {page(me['handle'])} now shows {'your GitHub name ' + str(me['login']) if me['public'] else 'only ' + me['handle']}")
    elif a.cmd == "forget":
        if input("delete your account and every result you sent? type yes: ").strip() != "yes":
            raise SystemExit("nothing deleted")
        r = account.forget(server)
        print(f"deleted: the account and {r['deleted_results']} results")


def cmd_submit(a: argparse.Namespace) -> None:
    from . import submit
    raise SystemExit(submit.run(a.results, a.host, a.server or submit.DEFAULT_SERVER, a.dry_run))


def cmd_serve(a: argparse.Namespace) -> None:
    from . import server
    if a.ingest_once:
        ids = server.Intake(a.data).ingest()
        print(f"{len(ids)} submission(s) checked")
        if (ids or a.force_rebuild) and a.rebuild:
            _rebuild_site(a.rebuild, a.deploy)
        return
    server.serve(a.data, port=a.port, bind=a.bind, on_accept=(lambda ids: _rebuild_site(a.rebuild, a.deploy)) if a.rebuild else None)


def _rebuild_site(out: str, deploy: str | None = None) -> None:
    """Rebuild the site; then publish it with the deploy command ({site} = the folder), e.g. wrangler pages deploy."""
    import shlex
    import subprocess
    from . import site
    site.build(out)
    print(f"site rebuilt in {out}", flush=True)
    if deploy:
        r = subprocess.run(deploy.replace("{site}", shlex.quote(os.path.expanduser(out))), shell=True, capture_output=True, text=True, timeout=600)
        print(f"deploy: {'ok' if r.returncode == 0 else 'FAILED ' + (r.stderr or r.stdout)[-400:]}", flush=True)


def cmd_speed(a: argparse.Namespace) -> None:
    from . import speed
    rec = speed.measure(a.host, a.recipe, overrides=a.set, depths=a.depth, unload=a.unload)
    if rec.get("error"):
        raise SystemExit(f"{a.recipe}: {rec['error']}")
    s, p = rec["speed"], rec.get("prediction", {})
    print(f"{a.recipe} {' '.join(a.set or [])}".strip())
    print(f"  decode {s['decode_tps']} tok/s" + (f" (draft acceptance {s['draft_acceptance']:.0%})" if s.get("draft_acceptance") else "")
          + f"   prefill {s['prefill_tps']} tok/s   load {s['load_seconds']} s   cache-ram {s['cache_ram_mib']} MiB")
    for d in s["depth"]:
        print(f"  @{d['depth']} tokens: prefill {d['prefill_tps']} tok/s, decode {d['decode_tps']} tok/s")
    if "decode_tps_no_spec" in p:
        print(f"  predicted (no speculative decoding): {p['decode_tps_no_spec']} tok/s, @depth {p['decode_tps_at_depth_no_spec']}")
    print(f"  saved {rec['path']}")


def cmd_bench(a: argparse.Namespace) -> None:
    from . import bench, results
    print(f"llmbox standard suite · tier {a.tier} · model {a.model} @ {a.endpoint}", flush=True)
    jl = a.jsonl or os.path.expanduser(f"~/.llmbox/runs/{a.model}-{a.tier}-{int(__import__('time').time())}.jsonl")
    os.makedirs(os.path.dirname(jl), exist_ok=True)
    if a.endpoint.startswith("claude-code"):
        from . import frontier
        ok, why = frontier.available()
        if not ok:
            raise SystemExit(why)
    cap = None
    if a.host and not a.endpoint.startswith("claude-code"):   # record what the server really runs with + telemetry
        from . import runinfo
        cap = runinfo.begin(a.host, a.endpoint, a.model)
        st = (cap.get("start") or {})
        print(f"  settings captured: build {((st.get('props') or {}).get('build_info'))}, {len(st.get('argv') or [])} argv items, "
              f"telemetry {'on' if (cap.get('telemetry') or {}).get('pid') else 'off'}" + (f"  ({'; '.join(cap['errors'])})" if cap.get("errors") else ""), flush=True)
    if cap is not None and cap.get("h") is not None:   # a timeout while the box rebooted is not the model's 0
        bench.REBOOTED_SINCE = lambda t0, h=cap["h"]: (lambda b: b if b and b > t0 else None)(runinfo.boot_time(h))
    if a.adaptive:
        from . import irt, suite as _suite
        bank = irt.load(a.bank) if a.bank else irt.bank_for(_suite.content_hash())   # a released version shares its dev tasks' bank (irt.SAME_TASKS)
        if not bank:
            raise SystemExit("no calibrated task bank for this suite: run `llmbox irt calibrate` (or pass --bank <content hash>)")
        # task families the bank has no answers for yet (new levels, rewritten kinds) join with guessed parameters, for
        # the kinds the suite already measures (quick items and calibrated kinds): the run's answers calibrate them later
        known = {f.rsplit(".", 1)[0] for f in bank.a} | {f"{b_}.{k_}" for b_, k_, _l in _suite.QUICK_ITEMS}
        bank = irt.with_provisional(bank, _suite.families(known))
        pr = tuple(float(x) for x in a.prior.split(",")) if a.prior else None   # default: the bank's population prior
        res = bench.run_adaptive(a.endpoint, a.model, bank, budget_min=a.budget, target=a.target, prior=pr,
                                 seed0=7000 + 1000 * a.seed,   # --seed: fresh instances for a repeated adaptive run
                                 api_key=None, progress=lambda m: print(m, flush=True), jsonl_path=jl, explore=a.explore, blocks=a.block)
    else:
        res = bench.run(a.endpoint, a.model, tier=a.tier, seed0=a.seed, blocks=a.block, jsonl_path=jl,
                        progress=lambda m: print(m, flush=True), resume=a.resume, rerun=a.rerun, parallel=a.parallel)
    if not res.get("rows"):   # stopped before the first answer (usage limit): nothing to save
        print(f"  no answers - {res['summary'].get('stopped', 'nothing ran')}", flush=True)
        return
    info = None
    if cap is not None:
        from . import recipe as rc
        rr = rc.load(a.host, a.recipe or a.model) if (a.recipe or a.model) in rc.ids(a.host) else None
        info = runinfo.end(cap, rr)
        rt = info["runtime"] or {}
        print(f"  settings: {rt.get('variant', 'no recipe to compare')} · consistent {rt.get('settings_consistent')} · "
              f"sha256 {str((info.get('sha256') or {}).get('sha256'))[:12]} · telemetry {(info.get('telemetry') or {}).get('samples', 0)} samples", flush=True)
    if (a.parallel > 1 or a.speed_probe) and not a.endpoint.startswith("claude-code"):   # 1-stream speed as a user feels it
        pm = a.speed_model or a.recipe or a.model
        print(f"  speed probe (1 stream) on {pm} ...", flush=True)
        try:
            par = res["summary"]["speed"]
            res["summary"]["speed"] = bench.speed_probe(a.endpoint, pm)
            if a.parallel > 1:   # aggregate throughput of the parallel run (several agents at once)
                res["summary"]["speed"]["parallel_streams"] = a.parallel
                res["summary"]["speed"]["parallel_throughput_tps"] = round((par.get("output_tokens") or 0) / (res["summary"]["wall_minutes"] * 60), 1) \
                    if res["summary"].get("wall_minutes") else None
        except Exception as e:
            print(f"  speed probe failed: {e}")
    s = res["summary"]
    sp = s["speed"]
    print(f"\n{a.model}: capability {s['capability']} (95% CI {s['capability_ci95'][0]}-{s['capability_ci95'][1]})  "
          f"blocks {s['blocks']}")
    print(f"  speed: decode {sp['decode_tps']} tok/s ({sp['label']}), prefill {sp['prefill_tps']} tok/s, "
          f"typical agent step {sp['typical_agent_step_s']} s")
    if sp.get("by_depth"):
        print("  by context depth: " + " | ".join(f"{k}: decode {v['decode_tps']}, prefill {v['prefill_tps']}"
                                                   for k, v in sp["by_depth"].items()))
    print(f"  {s['solved']}/{s['items']} solved in {s['wall_minutes']} min -> {s['solved_per_hour']} solved tasks/hour"
          + (f"   ({s['errors']} item errors)" if s["errors"] else ""))
    if a.endpoint.startswith("claude-code"):   # frontier reference: capability only, the "hardware" is the vendor's cloud
        rec = {"schema": results.SCHEMA, "id": __import__("uuid").uuid4().hex, "kind": "suite", "reference": True,
               "created": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"), "host": {"id": "cloud", "gpu": "vendor cloud"},
               "model": {"file": a.model}, "recipe": {"id": a.model, "description": f"frontier reference via Claude Code ({a.endpoint})"}}
        rec.update(res)
        print(f"  saved {results.save('cloud', rec)}")
    elif a.host:
        prof = hosts.load(a.host)
        from . import recipe as rc
        r = rc.load(a.host, a.recipe or a.model) if (a.recipe or a.model) in rc.ids(a.host) else {"id": a.recipe or a.model}
        rec = results.new("suite", prof, recipe=r, model={k: (r.get("model") or {}).get(k) for k in ("hf_repo", "file", "path", "sha256")})
        rec.update(res)
        if info:
            rec["runtime"] = info["runtime"]
            rec["telemetry"] = info["telemetry"]
            if info.get("sha256"):
                rec["model"]["sha256"] = info["sha256"].get("sha256")
                rec["model"]["bytes"] = info["sha256"].get("bytes")
            if info.get("errors"):
                rec["capture_errors"] = info["errors"]
        rec["vram"] = _vram_check(prof)
        print(f"  saved {results.save(a.host, rec)}")


IDLE_VRAM_WARN_MIB = 1024


def _vram_check(prof: dict) -> dict:
    """VRAM use with the benchmarked model still loaded. Idle VRAM > 1 GiB means the recipe leaves capacity unused
    (bigger context, better KV type, bigger quant, more experts on GPU) - the 'use every resource' rule, checked every run."""
    try:
        h = hosts.host_of(prof)
        out = h.run("nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits", timeout=30).stdout
        used, total = (int(x) for x in out.strip().splitlines()[0].split(","))
        idle = total - used
        if idle > IDLE_VRAM_WARN_MIB:
            print(f"  WARNING: {idle} MiB of {total} MiB VRAM idle with the model loaded - the recipe leaves capacity unused "
                  f"(raise context / KV type / quant, or document why not)")
        else:
            print(f"  VRAM {used}/{total} MiB used ({idle} MiB idle)")
        return {"used_mib": used, "total_mib": total, "idle_mib": idle}
    except Exception as e:  # never fail a finished run on a telemetry problem
        return {"error": str(e)[:200]}


def cmd_queue(a: argparse.Namespace) -> None:
    from . import queue as q
    if a.action == "add":
        import shlex
        extra = shlex.split(a.bench_args or "")
        for m in a.models:
            jid = q.add(m, a.suite, a.tier, a.host, extra, a.priority, a.note or "")
            print(f"queued job {jid}: {m} [{a.suite} {a.tier}] {' '.join(extra)}")
    elif a.action in ("list", "status"):
        rows = q.jobs(all_=a.all)
        if not rows:
            print("no jobs")
        for r in rows:
            prog = q.progress(r) if r["status"] == "running" else ""
            res = os.path.basename(r["result"] or "") if r["status"] == "done" else (r["note"] or "")
            print(f"{r['id']:>4} {r['status']:<11} {r['model']:<24} {r['suite']:<11} {r['tier']:<6} "
                  f"{' '.join(json.loads(r['args'] or '[]'))[:60]:<60} {prog or res}")
    elif a.action == "precision":   # where one more run narrows the ranking most (llmbox/precision.py)
        import random
        from . import precision
        skip = set((a.skip or "").split(",")) - {""}
        pl = precision.plan(a.host or "box", jobs=a.jobs, target=a.target, skip=skip)
        for m in pl:
            print(f"  {m['rid']:18s} {'top ' if m['top'] else 'open' if m['open'] else '    '} {m['n']:4d} answers  "
                  f"±{m['half']:.1f} -> ±{m['half_after']:.1f}  {m['runs']} run{'s' if m['runs'] > 1 else ''} of {a.budget:g} min (~{m['per_run']} answers each)")
        total = sum(m["runs"] for m in pl)
        print(f"{total} runs, ~{total * (a.budget + 5) / 60:.1f} h of the box" + ("" if a.apply else " (--apply queues them)"))
        if a.apply:
            for m in pl:
                for _ in range(m["runs"]):   # a fresh seed each: new instances of the tasks, not the same ones again
                    from . import irt, suite as _su
                    tag = "suite-v" + irt.RELEASES[irt.canonical(_su.content_hash())]   # the released suite's snapshot
                    jid = q.add(m["rid"], tag, tier="quick", host=a.host or "box",
                                args=["--recipe", m["rid"], "--adaptive", "--budget", f"{a.budget:g}", "--target", "2.5",
                                      "--seed", str(random.randrange(1, 10**6))],
                                note=f"precision: ±{m['half']:.1f} with {m['n']} answers")
                    print(f"  queued job {jid}: {m['rid']}")
    elif a.action == "run":
        q.run(until_empty=a.until_empty)
    elif a.action == "cancel":
        for i in a.ids:
            running = any(r["id"] == int(i) and r["status"] == "running" for r in q.jobs())
            q.set_status(int(i), "cancelled")
            if running:   # the worker sees the cancelled status when the run exits and does not retry it
                subprocess.run(["pkill", "-TERM", "-f", f"job-{int(i)}.jsonl"])
            print(f"job {i} cancelled" + (" and stopped" if running else ""))
    elif a.action == "retry":
        for i in a.ids:
            q.set_status(int(i), "queued", attempts=0)
            print(f"job {i} re-queued")
    elif a.action == "pause":
        os.makedirs(q.QDIR, exist_ok=True)
        open(q.PAUSE, "w").close()
        print("paused: the worker starts no new job (the running one finishes)")
    elif a.action == "resume":
        if os.path.exists(q.PAUSE):
            os.remove(q.PAUSE)
        print("resumed")


def cmd_validate(a: argparse.Namespace) -> None:
    from . import validate as v
    seeds = tuple(range(1, a.seeds + 1))
    res = v.run(None if a.all else "quick", a.kind, seeds, "claude-code:high" if a.frontier else None,
                progress=lambda m: print(m, flush=True))
    bad = [r for r in res if not r["ok"]]
    review = [r for r in res if r.get("review")]
    print(f"\n{len(res) - len(bad)}/{len(res)} kinds pass" + (f"; frontier failures to review: {[r['kind'] for r in review]}" if review else ""))
    if bad:
        raise SystemExit(1)


def cmd_snapshot(a: argparse.Namespace) -> None:
    from . import queue as q
    dst = q.snapshot(a.tag)
    if not a.no_validate:   # a frozen suite must pass validate (run from the snapshot itself, i.e. the tagged code)
        import subprocess
        env = dict(os.environ, PYTHONPATH=dst, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-m", "llmbox.cli", "validate", "--seeds", "1"], cwd=dst, env=env)
        if r.returncode:
            import shutil
            shutil.rmtree(dst, ignore_errors=True)
            raise SystemExit(f"{a.tag}: validate failed - snapshot removed (use --no-validate to force)")
    print(dst)


def cmd_regrade(a: argparse.Namespace) -> None:
    import json as _json
    from . import bench
    for path in a.results:
        rec = _json.load(open(path))
        from . import suite as _suite
        if rec.get("suite", {}).get("version") != _suite.VERSION:   # items of other versions differ: never cross-grade
            print(f"{os.path.basename(path)}: suite v{rec.get('suite', {}).get('version')} != current v{_suite.VERSION}, skipped")
            continue
        new, changes = bench.regrade(rec, reader_url=a.reader)
        print(f"{os.path.basename(path)}: capability {rec['summary']['capability']} -> {new['summary']['capability']}")
        for c in changes:
            print("   ", c)
        if changes and not a.dry_run:
            _json.dump(new, open(path, "w"), indent=1)


def cmd_report(a: argparse.Namespace) -> None:
    from . import report
    rs = report.rows(a.host, a.suite_version, a.tier)
    print(report.text_table(rs))
    if a.html:
        open(os.path.expanduser(a.html), "w").write(report.html_page(rs))
        print(f"\nwrote {a.html}")


def cmd_recipe(a) -> None:
    from . import recipe as rc
    if a.action == "new":
        from . import draft
        if not a.ids:
            raise SystemExit("recipe new <hf-repo>")
        repo = a.ids[0]
        from .candidates import recipe_id
        rid = a.rid or recipe_id(repo)
        prof = hosts.load(a.host)
        cpu = prof["hw"].get("cpu") or {}
        runtimes = sorted(prof["hw"].get("runtimes") or [], key=lambda x: int((re.search(r"build (\d+)", x.get("version", "")) or [0, 0])[1]))
        r, report = draft.draft(repo, hosts.spec(prof), rid, file=a.file, models_dir=prof["hw"].get("models_dir_guess") or "",
                                server=runtimes[-1]["path"] if runtimes else "", cores=cpu.get("cores") or cpu.get("threads"))
        from . import fit as F
        print(report + "\n")
        text = F.to_toml(r, header=[f"Draft recipe {rid} for {a.host}, {time.strftime('%Y-%m-%d')}. Edit the TODOs, then `llmbox bench` gives it a score."])
        out = os.path.join(rc.recipes_dir(a.host), f"{rid}.toml")
        if not a.write:
            print(text + f"\n(not written; --write saves it as {out})")
        elif os.path.exists(out):
            raise SystemExit(f"{out} exists; pick another --id")
        else:
            open(out, "w").write(text)
            print(f"wrote {out}")
        return
    if a.action == "pull":
        from . import registry
        got = registry.pull(a.url or registry.DEFAULT_URL, only=a.ids or None)
        print("next: llmbox pick (what fits here), or llmbox start (the best one, installed and running)")
        return
    if a.action == "list":
        for rid in rc.ids(a.host):
            r = rc.load(a.host, rid)
            print(f"{rid:22s} {r.get('description', '')}")
        return
    ids = a.ids or (rc.ids(a.host) if a.action == "check" else [])
    if not ids:
        raise SystemExit("which recipe?")
    if a.action == "show":
        for rid in ids:
            r = rc.load(a.host, rid)
            print(json.dumps(r, indent=1, ensure_ascii=False))
    elif a.action == "render":
        for rid in ids:
            r = rc.load(a.host, rid)
            print(rc.launcher(r))
            print(rc.llama_swap_entry(r, f"<launchers>/start-{rid}.sh"))
    elif a.action == "check":   # recipe vs what the host's llama-swap entry really starts
        h = hosts.host_of(hosts.load(a.host))
        bad = 0
        for rid in ids:
            try:
                d = rc.diff(rc.load(a.host, rid), rc.live_argv(h, rid))
            except RuntimeError as e:
                print(f"?? {rid}: {e}")
                bad += 1
                continue
            print(f"{'OK' if not d else 'DIFF'} {rid}" + "".join(f"\n     {x}" for x in d))
            bad += bool(d)
        if bad:
            raise SystemExit(1)


def cmd_traces(a) -> None:
    """Budget / loop audit of a run: per item the longest reply, whether the reasoning budget cut it, loop detection."""
    import glob
    import gzip
    from . import loopdetect
    from .bench import TRACES
    run = a.run if os.path.isdir(a.run) else os.path.join(TRACES, a.run)
    rows = {}
    jsonl = os.path.join(os.path.expanduser("~/.llmbox/queue"), os.path.basename(run) + ".jsonl")
    if os.path.exists(jsonl):
        for line in open(jsonl):
            r = json.loads(line)
            rows[r["id"]] = r
    print(f"{'item':30s} {'score':>5s} {'max reply':>9s} {'cut':>4s} {'loop':>5s}  note")
    for f in sorted(glob.glob(os.path.join(run, "*.json.gz"))):
        t = json.load(gzip.open(f, "rt"))
        r = rows.get(t["id"], {})
        mx = max([x or 0 for x in t.get("reply_tokens") or []] or [0])
        cut = sum("I have reasoned enough" in th for th in t["thinking"])
        scans = [loopdetect.scan(th) for th in t["thinking"]]
        fired = [s for s in scans if s["fired_at"]]
        peak = max([s["peak"] for s in scans] or [0])
        note = "LOOP" if fired else ("near budget" if mx >= a.near else "")
        if a.all or cut or fired or mx >= a.near:
            print(f"{t['id']:30s} {r.get('score', float('nan')):5.2f} {mx:9d} {cut:4d} {peak:5.2f}  {note}")
            if fired and a.excerpts:
                print("      ..." + fired[0]["excerpt"][-300:].replace("\n", " / "))


def cmd_site(a) -> None:
    from . import site, suite
    for p in site.build(a.out, host=a.host, suite_version=a.suite_version or suite.VERSION, tier=a.tier):
        print(p)


# `llmbox --help` lists the commands by what you want to do, most used first
COMMAND_GROUPS = [
    ("Pick and run a model on your box", ["start", "doctor", "update", "host", "pick", "scout", "fit", "recipe", "install", "run", "stop", "tune", "optimize"]),
    ("Measure it", ["test", "bench", "queue", "speed", "probe", "loops", "traces"]),
    ("Share and compare", ["login", "whoami", "profile", "submit", "logout", "forget", "serve"]),
    ("Scores, results and the site", ["report", "site", "irt", "db", "verify", "regrade", "grade-pending", "watch"]),
    ("Develop the test", ["validate", "snapshot"]),
]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="llmbox", description="Get the most quality x speed out of local LLMs on your hardware.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    from . import __version__
    ap.add_argument("--version", action="version", version=f"llmbox {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<command>")
    helps: dict = {}

    def command(name: str, help: str) -> argparse.ArgumentParser:
        """A command: listed by purpose in `llmbox --help` (COMMAND_GROUPS), its line as the description of `llmbox <name> --help`."""
        helps[name] = help
        return sub.add_parser(name, description=help)   # no help=: the flat list stays out of the way

    h = command("host", "register / inspect machines that run models")
    h.add_argument("action", choices=["add", "info", "list"])
    h.add_argument("name", nargs="?")
    h.add_argument("--ssh", help="user@host or ~/.ssh/config alias (omit for this machine)")
    h.add_argument("--ram-bw", type=float, help="RAM read bandwidth in GB/s if it cannot be measured now")
    h.add_argument("--no-measure", action="store_true", help="skip the bandwidth probe")
    h.set_defaults(fn=cmd_host)

    s = command("scout", "which quants of a Hugging Face GGUF repo fit a host, and how fast (no download)")
    s.add_argument("repo", help="e.g. unsloth/Qwen3.6-35B-A3B-GGUF")
    s.add_argument("--host", required=True)
    s.add_argument("--kv", default="q8_0", help="KV cache type (default q8_0)")
    s.add_argument("--depth", type=int, default=50_000, help="context depth for the at-depth prediction")
    s.add_argument("--headroom", type=int, default=4096, help="RAM kept free for the OS, MiB")
    s.add_argument("--quant", action="append", help="only these quants (repeatable), e.g. --quant Q4_K_M")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_scout)

    fp = command("fit", "fit a recipe's hardware layer (context, batch, threads...) to a host; no download")
    fp.add_argument("recipe")
    fp.add_argument("--from", dest="src", default="box", help="host whose recipe and measurements to start from (default box)")
    fp.add_argument("--host", help="registered target host (default: the --from host)")
    fp.add_argument("--gpu", help="what-if target instead of a host, e.g. 'RTX 4090'")
    fp.add_argument("--vram-gb", type=float, help="what-if VRAM (default: the card's common size)")
    fp.add_argument("--ram-gb", type=float, default=64, help="what-if system RAM, GB (default 64)")
    fp.add_argument("--ram-bw", type=float, default=60, help="what-if RAM read bandwidth, GB/s (default 60)")
    fp.add_argument("--cores", type=int, help="CPU cores for llama.cpp threads")
    fp.add_argument("--ctx", help="context to fit instead of the recipe's, e.g. 128k")
    fp.add_argument("--write", action="store_true", help="save the fitted recipe under the target host")
    fp.add_argument("--force", action="store_true", help="with --write: replace an existing recipe file")
    fp.add_argument("--json", action="store_true")
    fp.set_defaults(fn=cmd_fit)
    ip = command("install", "put a recipe on a host: fit, download, verify, launcher, llama-swap entry (dry run unless --apply)")
    ip.add_argument("recipe")
    ip.add_argument("--host", help="target host (default: the --from host)")
    ip.add_argument("--from", dest="src", default="box", help="host whose recipe to install (default box)")
    ip.add_argument("--apply", action="store_true", help="do it (default: show the plan and every command, write nothing)")
    ip.add_argument("--force", action="store_true", help="replace a different existing launcher (kept as .bak); act while the host is busy")
    ip.add_argument("--unload", action="store_true", help="with --apply: unload llama-swap's idle model and wait for the host to go idle first")
    ip.set_defaults(fn=cmd_install)
    tp = command("tune", "measure speed knobs (speculative decoding, VRAM margin, prompt batch) on the idle box; keep what wins")
    tp.add_argument("recipes", nargs="+")
    tp.add_argument("--host", default="box")
    tp.add_argument("--plan", action="store_true", help="list the variants only, measure nothing")
    tp.set_defaults(fn=cmd_tune)
    vp = command("verify", "re-grade saved runs from their answers (the server-side check of a submitted run)")
    vp.add_argument("results", nargs="*", help="result files (default: every suite run)")
    vp.add_argument("-v", "--verbose", action="store_true", help="also list the answers that cannot be re-graded")
    vp.set_defaults(fn=cmd_verify)
    dp = command("db", "the results database (~/.llmbox/llmbox.db): sync the result files in, stats, read-only SQL")
    dp.add_argument("action", choices=["sync", "stats", "sql"])
    dp.add_argument("query", nargs="?", default="SELECT recipe_id, suite_version, tier, capability FROM runs WHERE kind='suite' ORDER BY created DESC")
    dp.add_argument("--limit", type=int, default=50)
    dp.set_defaults(fn=cmd_db)
    wp = command("watch", "look for new models, new files of measured models, runtime releases and watched PRs (daily job)")
    wp.add_argument("--list", type=int, metavar="N", help="print the newest N events instead of looking")
    wp.set_defaults(fn=cmd_watch)
    gp = command("grade-pending", "grade explanations that cloud runs left pending (reader on the box; box must be free)")
    gp.add_argument("results", nargs="*", help="result files (default: every one with pending rows)")
    gp.set_defaults(fn=cmd_grade_pending)
    op = command("optimize", "tune (if not yet), apply, then stock llama.cpp vs llmbox back to back, then re-probe; box must be idle")
    op.add_argument("recipes", nargs="+")
    op.add_argument("--host", default="box")
    op.add_argument("--retune", action="store_true", help="tune again even if the recipe was tuned before")
    op.add_argument("--endpoint", help="the host's model server (default: from its profile, see hosts.endpoint)")
    op.set_defaults(fn=cmd_optimize)
    pp = command("probe", "re-measure only the 1-stream speed of a served recipe (e.g. after tune)")
    pp.add_argument("model", help="llama-swap id")
    pp.add_argument("--host", default="box")
    pp.add_argument("--recipe", help="recipe id (default: the model id)")
    pp.add_argument("--endpoint", help="the host's model server (default: from its profile, see hosts.endpoint)")
    pp.set_defaults(fn=cmd_probe)
    ir = command("irt", "task-family calibration (IRT) for adaptive runs: calibrate / report / simulate")
    ir.add_argument("action", choices=["calibrate", "report", "simulate", "rescore"])
    ir.add_argument("paths", nargs="*", help="rescore: result files (default: every run of this suite content)")
    ir.add_argument("--write", action="store_true", help="rescore: store the new estimate in adaptive results")
    ir.add_argument("--content-hash", help="suite content hash (default: this suite)")
    ir.add_argument("--budget", type=float, default=45, help="simulate: minutes")
    ir.set_defaults(fn=cmd_irt)
    lp = command("loops", "fast reasoning-loop test: replay contexts where models looped before")
    lp.add_argument("action", choices=["extract", "replay"])
    lp.add_argument("--transcripts", default="~/agent-bench-runs/claude-config/projects")
    lp.add_argument("--min-tokens", type=int, default=15000)
    lp.add_argument("--cases", default=os.path.expanduser("~/.cache/llmbox/loop-cases.json"))
    lp.add_argument("--endpoint", help="Anthropic-compatible endpoint (agent-proxy; default: the host profile's agent_endpoint)")
    lp.add_argument("--model", help="served model id (llama-swap)")
    lp.add_argument("--label", help="name for this configuration in the results")
    lp.add_argument("--limit", type=int, default=8, help="number of contexts (one per task, shortest first)")
    lp.add_argument("--samples", type=int, default=1)
    lp.add_argument("--cap", type=int, default=10000, help="output tokens; reaching it counts as a loop")
    lp.add_argument("--request-template", default="~/.cache/llmbox/claude-code-request.json")
    lp.add_argument("--out", help="append per-sample results (JSONL)")
    lp.set_defaults(fn=cmd_loops)

    sp = command("speed", "measure a recipe (or a variant) on its host; the box must be idle")
    sp.add_argument("recipe")
    sp.add_argument("--host", required=True)
    sp.add_argument("--set", action="append", help="override, e.g. --set speculative.draft_max=3 (repeatable)")
    sp.add_argument("--depth", type=int, action="append", help="decode-at-depth probe, tokens (repeatable; default 32000)")
    sp.add_argument("--unload", action="store_true", help="unload llama-swap models first (only when nothing is using them!)")
    sp.set_defaults(fn=cmd_speed)

    b = command("bench", "run the standard suite against a served model (OpenAI-compatible endpoint)")
    b.add_argument("model", help="model id at the endpoint (e.g. a llama-swap id)")
    b.add_argument("--endpoint", default=None,
                   help="OpenAI-compatible base URL, or claude-code[:effort] for a frontier reference via the Claude subscription")
    b.add_argument("--tier", default="quick", choices=["quick", "medium", "deep", "ladder"])
    b.add_argument("--seed", type=int, default=0, help="item set; a new seed gives fresh items of equal difficulty")
    b.add_argument("--block", action="append", help="only these blocks (repeatable)")
    b.add_argument("--host", help="host profile to attach to the saved result")
    b.add_argument("--recipe", help="recipe id to attach (default: same as model)")
    b.add_argument("--resume", help="jsonl of an interrupted run: reuse its finished items")
    b.add_argument("--rerun", action="append", help="with --resume: run this item id again (repeatable)")
    b.add_argument("--jsonl", help="where to append per-item rows (default: ~/.llmbox/runs/<model>-<tier>-<time>.jsonl)")
    b.add_argument("--parallel", type=int, default=1, help="concurrent items (the served entry needs that many slots, unified KV)")
    b.add_argument("--speed-model", help="single-slot model id for the 1-stream speed probe (default: --recipe or model)")
    b.add_argument("--speed-probe", action="store_true", help="also run the 1-stream speed-by-depth probe after the run")
    b.add_argument("--adaptive", action="store_true", help="adaptive run (llmbox irt): stop at +-target points or the time budget")
    b.add_argument("--budget", type=float, default=45, help="adaptive: minutes (default 45)")
    b.add_argument("--target", type=float, default=5, help="adaptive: stop when the 95%% interval is within +-this many points (default 5)")
    b.add_argument("--prior", help="adaptive: 'theta,sd' to start from (default 0,1.5); e.g. the base model's theta")
    b.add_argument("--explore", type=int, help="adaptive: new task families (no calibration yet) to try; default 6. A strong "
                   "cloud model calibrating new levels: 20+, every task goes to them once each block has its minimum")
    b.add_argument("--bank", help="adaptive: content hash of the calibrated bank to use (default: this suite's)")
    b.set_defaults(fn=cmd_bench)

    qp = command("queue", "persistent benchmark job queue with GPU health gating and auto-resume")
    qp.add_argument("action", choices=["add", "list", "status", "run", "cancel", "retry", "pause", "resume", "precision"])
    qp.add_argument("models", nargs="*", help="add: model ids at the endpoint")
    qp.add_argument("--suite", default="suite-v0.8", help="git tag of the suite to run (frozen via llmbox snapshot)")
    qp.add_argument("--tier", default="quick")
    qp.add_argument("--host", default="box")
    qp.add_argument("--priority", type=int, default=0)
    qp.add_argument("--note")
    qp.add_argument("--all", action="store_true", help="list: include done / cancelled jobs")
    qp.add_argument("--until-empty", action="store_true", help="run: exit when no job is left")
    qp.add_argument("--ids", nargs="*", default=[], help="cancel / retry: job ids")
    qp.add_argument("--bench-args", default="", help='add: extra bench args, e.g. --bench-args "--recipe x --parallel 3"')
    qp.add_argument("--jobs", type=int, default=8, help="precision: how many runs to hand out")
    qp.add_argument("--target", type=float, default=3.0, help="precision: stop narrowing a range at +- this many points")
    qp.add_argument("--budget", type=float, default=40, help="precision: minutes per run")
    qp.add_argument("--skip", help="precision: recipe ids to leave out, comma-separated")
    qp.add_argument("--apply", action="store_true", help="precision: queue the runs (default: show the plan)")
    qp.set_defaults(fn=cmd_queue)
    va = command("validate", "check every task kind: determinism, oracle = 1, empty = 0, answer-format tolerance")
    va.add_argument("--all", action="store_true", help="every kind of every block (default: the quick tier's kinds)")
    va.add_argument("--kind", action="append", help="only these kinds (e.g. tools.outreach or outreach), repeatable")
    va.add_argument("--seeds", type=int, default=3)
    va.add_argument("--frontier", action="store_true", help="also run the frontier reference (Claude subscription) and flag its failures")
    va.set_defaults(fn=cmd_validate)
    sn = command("snapshot", "freeze a git tag of llmbox into ~/.llmbox/snapshots/<tag>")
    sn.add_argument("tag")
    sn.add_argument("--no-validate", action="store_true", help="skip the validate gate (not recommended)")
    sn.set_defaults(fn=cmd_snapshot)

    rg = command("regrade", "re-score text-graded items of saved results with the current graders")
    rg.add_argument("results", nargs="+", help="result json files (~/.llmbox/results/<host>/*.json)")
    rg.add_argument("--dry-run", action="store_true")
    rg.add_argument("--reader", nargs="?", const="", default=None,
                    help="also grade the explanations again with the reader model at this endpoint (default: the box)")
    rg.set_defaults(fn=cmd_regrade)

    pk = command("pick", "what to run on this machine: every measured model fitted to it, ranked by score, the fastest of the best")
    pk.add_argument("--host", help="registered machine (default: the only one)")
    pk.add_argument("--use", choices=["all", "coding", "agents", "ask", "docs"], default="all", help="rank by the score for this use")
    pk.add_argument("--gpu", help="what-if: a graphics card by name instead of a registered host (e.g. 'RTX 4090')")
    pk.add_argument("--vram-gb", type=float, help="what-if: its memory if the name is not known")
    pk.add_argument("--ram-gb", type=float, default=64.0, help="what-if: system RAM")
    pk.add_argument("--ram-bw", type=float, default=60.0, help="what-if: RAM read speed GB/s (DDR4 ~40, DDR5 ~60-80)")
    pk.add_argument("--pull", action="store_true", help="fetch the site's recipes first (done once automatically)")
    pk.add_argument("--url", help="the site to pull from")
    pk.set_defaults(fn=cmd_pick)

    ru = command("run", "serve an installed model (OpenAI-compatible) until Ctrl-C, without llama-swap")
    ru.add_argument("recipe")
    ru.add_argument("--host", help="the machine (default: the only one registered)")
    ru.add_argument("--port", type=int, help="port (default: a free one)")
    ru.add_argument("--background", "-d", action="store_true", help="leave it running; `llmbox stop` ends it")
    ru.set_defaults(fn=cmd_run)
    st_ = command("start", "the guided start (also plain `llmbox`): this computer, the best model for it, install, measure, run")
    st_.add_argument("model", nargs="?", help="a model id to install instead of the pick")
    st_.add_argument("--yes", "-y", action="store_true", help="take every default (never sends anything)")
    st_.add_argument("--host", help="a registered machine (default: this computer)")
    st_.add_argument("--plan", action="store_true", help="only show the plan")
    st_.set_defaults(fn=cmd_start)
    up_ = command("update", "the newest llmbox and list of models")
    up_.set_defaults(fn=cmd_update)
    dr = command("doctor", "is this computer ready? each problem with the command that fixes it")
    dr.add_argument("--host", help="a registered machine (default: this computer)")
    dr.set_defaults(fn=cmd_doctor)
    so = command("stop", "stop models llmbox serves in the background (all, or the ones named)")
    so.add_argument("recipes", nargs="*")
    so.set_defaults(fn=cmd_stop)

    te = command("test", "measure a model on this machine and send it: speed, the 40-minute quality test, the upload")
    te.add_argument("recipe", help="an installed recipe (llmbox install <id> --from registry ...)")
    te.add_argument("--host", help="this machine's name (default: the only one registered)")
    te.add_argument("--full", action="store_true", help="the 40-minute quality test (default: 10 minutes)")
    te.add_argument("--budget", type=float, help="minutes for the quality test (overrides --full)")
    te.add_argument("--yes", "-y", action="store_true", help="no questions (it then sends nothing: sending needs its own yes)")
    te.add_argument("--server", help="intake address (default $LLMBOX_SERVER)")
    te.add_argument("--no-submit", action="store_true", help="measure only; `llmbox submit` sends it later")
    te.set_defaults(fn=cmd_test)

    for name, what in (("login", "sign in with GitHub (a code to enter at github.com): your results get an account and a profile page"),
                       ("logout", "remove this machine's llmbox key"), ("whoami", "who you are signed in as, and your profile page"),
                       ("profile", "show your GitHub name on your profile page (--public) or only its handle (--private)"),
                       ("forget", "delete your account and every result you sent")):
        ac = command(name, what)
        ac.add_argument("--server", help="intake address (default $LLMBOX_SERVER)")
        if name == "profile":
            ac.add_argument("--public", action="store_true")
            ac.add_argument("--private", action="store_true")
        ac.set_defaults(fn=cmd_account)

    sb = command("submit", "send your measurements (speed and quality) to the shared results; --dry-run shows exactly what is sent")
    sb.add_argument("results", nargs="*", help="result files (default: every speed, optimize and quality record not sent yet)")
    sb.add_argument("--host", help="only this host's records")
    sb.add_argument("--server", help="intake address (default $LLMBOX_SERVER or the local intake)")
    sb.add_argument("--dry-run", action="store_true", help="print the bundle, send nothing")
    sb.set_defaults(fn=cmd_submit)

    sv = command("serve", "the intake for submitted measurements: HTTP API + checks; accepted records become the site's community results")
    sv.add_argument("--data", default=os.path.join(hosts.HOME, "intake"), help="where bundles and the intake database live")
    sv.add_argument("--port", type=int, default=8767)
    sv.add_argument("--bind", default="127.0.0.1")
    sv.add_argument("--rebuild", metavar="SITE_DIR", help="rebuild the site into SITE_DIR after accepting submissions")
    sv.add_argument("--ingest-once", action="store_true", help="check waiting submissions once and exit")
    sv.add_argument("--deploy", help="after a rebuild, run this to publish the site ({site} = the folder), e.g. wrangler pages deploy")
    sv.add_argument("--force-rebuild", action="store_true", help="with --ingest-once: rebuild (and deploy) even when nothing new came in")
    sv.set_defaults(fn=cmd_serve)

    rc_ = command("recipe", "recipes: list / show / render a launcher / check the host runs what the recipe says / pull the site's")
    rc_.add_argument("action", choices=["list", "show", "render", "check", "new", "pull"])
    rc_.add_argument("ids", nargs="*", help="recipe ids; for `new`: the Hugging Face repo")
    rc_.add_argument("--host", default="box")
    rc_.add_argument("--file", help="new: use this GGUF file (substring) instead of picking one")
    rc_.add_argument("--id", dest="rid", help="new: recipe id (default: from the repo name)")
    rc_.add_argument("--write", action="store_true", help="new: save the draft under the host's recipes")
    rc_.add_argument("--url", help="pull: the site to take the published recipes from (default $LLMBOX_SITE or the local site)")
    rc_.set_defaults(fn=cmd_recipe)

    tr = command("traces", "budget / loop audit of a run's saved thinking (~/.llmbox/traces/<run>)")
    tr.add_argument("run", help="run name (e.g. job-8) or a traces directory")
    tr.add_argument("--near", type=int, default=20000, help="flag replies at least this long (tokens)")
    tr.add_argument("--all", action="store_true", help="list every item")
    tr.add_argument("--excerpts", action="store_true", help="print the text before each detected loop")
    tr.set_defaults(fn=cmd_traces)

    st = command("site", "build the static site (ranking, model pages, runs, other computers, compare, new models, method) from saved results")
    st.add_argument("--out", default="~/.llmbox/site")
    st.add_argument("--host", default="box")
    st.add_argument("--suite-version", help="default: the current suite version")
    st.add_argument("--tier", default="quick")
    st.set_defaults(fn=cmd_site)

    rp = command("report", "leaderboard of saved suite results (terminal + optional static HTML)")
    rp.add_argument("--host")
    rp.add_argument("--suite-version")
    rp.add_argument("--tier")
    rp.add_argument("--html", help="write a self-contained HTML page here")
    rp.set_defaults(fn=cmd_report)

    missing = set(helps) - {c for _g, cs in COMMAND_GROUPS for c in cs}
    assert not missing, f"commands without a group in COMMAND_GROUPS: {missing}"
    ap.epilog = "\n\n".join(f"{g}:\n" + "\n".join(f"  {c:14s} {helps[c]}" for c in cs if c in helps) for g, cs in COMMAND_GROUPS) + \
        "\n\nllmbox <command> --help for its options. Data lives in ~/.llmbox (results, recipes, queue, site)."
    if not (argv if argv is not None else sys.argv[1:]):   # plain `llmbox`: the guided start
        from . import wizard
        raise SystemExit(wizard.main())
    a = ap.parse_args(argv)
    if hasattr(a, "endpoint") and not a.endpoint:   # the host's own model server (its profile), not a fixed address
        a.endpoint = hosts.endpoint(getattr(a, "host", None), agent=a.cmd == "loops")
    if getattr(a, "reader", None) == "":   # --reader without a URL: the box's own server
        a.reader = hosts.endpoint(getattr(a, "host", None) or "box")
    a.fn(a)


if __name__ == "__main__":
    main()
