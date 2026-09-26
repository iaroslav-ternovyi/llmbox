"""llmbox command line."""
from __future__ import annotations

import argparse
import json
import os
import sys

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
        print(f"  GPU  {g['name']}  {g['vram_mib']/1024:.1f} GiB  driver {g['driver']}  PCIe {g['pcie_gen']}x{g['pcie_width']}"
              f"  power {g['power_limit_w']} W (default {g['power_default_w']})  bandwidth {p.get('vram_bw_gbs') or '?'} GB/s")
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
    jl = os.path.expanduser(f"~/.llmbox/runs/{a.model}-{a.tier}-{int(__import__('time').time())}.jsonl")
    os.makedirs(os.path.dirname(jl), exist_ok=True)
    if a.endpoint.startswith("claude-code"):
        from . import frontier
        ok, why = frontier.available()
        if not ok:
            raise SystemExit(why)
    res = bench.run(a.endpoint, a.model, tier=a.tier, seed0=a.seed, blocks=a.block, jsonl_path=jl,
                    progress=lambda m: print(m, flush=True), resume=a.resume, rerun=a.rerun, parallel=a.parallel)
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


def cmd_regrade(a: argparse.Namespace) -> None:
    import json as _json
    from . import bench
    for path in a.results:
        rec = _json.load(open(path))
        from . import suite as _suite
        if rec.get("suite", {}).get("version") != _suite.VERSION:   # items of other versions differ: never cross-grade
            print(f"{os.path.basename(path)}: suite v{rec.get('suite', {}).get('version')} != current v{_suite.VERSION}, skipped")
            continue
        new, changes = bench.regrade(rec)
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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="llmbox", description="Get the most quality x speed out of local LLMs on your hardware.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("host", help="register / inspect machines that run models")
    h.add_argument("action", choices=["add", "info", "list"])
    h.add_argument("name", nargs="?")
    h.add_argument("--ssh", help="user@host or ~/.ssh/config alias (omit for this machine)")
    h.add_argument("--ram-bw", type=float, help="RAM read bandwidth in GB/s if it cannot be measured now")
    h.add_argument("--no-measure", action="store_true", help="skip the bandwidth probe")
    h.set_defaults(fn=cmd_host)

    s = sub.add_parser("scout", help="which quants of a Hugging Face GGUF repo fit a host, and how fast (no download)")
    s.add_argument("repo", help="e.g. unsloth/Qwen3.6-35B-A3B-GGUF")
    s.add_argument("--host", required=True)
    s.add_argument("--kv", default="q8_0", help="KV cache type (default q8_0)")
    s.add_argument("--depth", type=int, default=50_000, help="context depth for the at-depth prediction")
    s.add_argument("--headroom", type=int, default=4096, help="RAM kept free for the OS, MiB")
    s.add_argument("--quant", action="append", help="only these quants (repeatable), e.g. --quant Q4_K_M")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_scout)

    lp = sub.add_parser("loops", help="fast reasoning-loop test: replay contexts where models looped before")
    lp.add_argument("action", choices=["extract", "replay"])
    lp.add_argument("--transcripts", default="~/agent-bench-runs/claude-config/projects")
    lp.add_argument("--min-tokens", type=int, default=15000)
    lp.add_argument("--cases", default=os.path.expanduser("~/.cache/llmbox/loop-cases.json"))
    lp.add_argument("--endpoint", default="http://192.0.2.10:8081", help="Anthropic-compatible endpoint (agent-proxy)")
    lp.add_argument("--model", help="served model id (llama-swap)")
    lp.add_argument("--label", help="name for this configuration in the results")
    lp.add_argument("--limit", type=int, default=8, help="number of contexts (one per task, shortest first)")
    lp.add_argument("--samples", type=int, default=1)
    lp.add_argument("--cap", type=int, default=10000, help="output tokens; reaching it counts as a loop")
    lp.add_argument("--request-template", default="~/.cache/llmbox/claude-code-request.json")
    lp.add_argument("--out", help="append per-sample results (JSONL)")
    lp.set_defaults(fn=cmd_loops)

    sp = sub.add_parser("speed", help="measure a recipe (or a variant) on its host; the box must be idle")
    sp.add_argument("recipe")
    sp.add_argument("--host", required=True)
    sp.add_argument("--set", action="append", help="override, e.g. --set speculative.draft_max=3 (repeatable)")
    sp.add_argument("--depth", type=int, action="append", help="decode-at-depth probe, tokens (repeatable; default 32000)")
    sp.add_argument("--unload", action="store_true", help="unload llama-swap models first (only when nothing is using them!)")
    sp.set_defaults(fn=cmd_speed)

    b = sub.add_parser("bench", help="run the standard suite against a served model (OpenAI-compatible endpoint)")
    b.add_argument("model", help="model id at the endpoint (e.g. a llama-swap id)")
    b.add_argument("--endpoint", default="http://192.0.2.10:8080",
                   help="OpenAI-compatible base URL, or claude-code[:effort] for a frontier reference via the Claude subscription")
    b.add_argument("--tier", default="quick", choices=["quick", "medium", "deep"])
    b.add_argument("--seed", type=int, default=0, help="item set; a new seed gives fresh items of equal difficulty")
    b.add_argument("--block", action="append", help="only these blocks (repeatable)")
    b.add_argument("--host", help="host profile to attach to the saved result")
    b.add_argument("--recipe", help="recipe id to attach (default: same as model)")
    b.add_argument("--resume", help="jsonl of an interrupted run: reuse its finished items")
    b.add_argument("--rerun", action="append", help="with --resume: run this item id again (repeatable)")
    b.add_argument("--parallel", type=int, default=1, help="concurrent items (the served entry needs that many slots, unified KV)")
    b.add_argument("--speed-model", help="single-slot model id for the 1-stream speed probe (default: --recipe or model)")
    b.add_argument("--speed-probe", action="store_true", help="also run the 1-stream speed-by-depth probe after the run")
    b.set_defaults(fn=cmd_bench)

    rg = sub.add_parser("regrade", help="re-score text-graded items of saved results with the current graders")
    rg.add_argument("results", nargs="+", help="result json files (~/.llmbox/results/<host>/*.json)")
    rg.add_argument("--dry-run", action="store_true")
    rg.set_defaults(fn=cmd_regrade)

    rp = sub.add_parser("report", help="leaderboard of saved suite results (terminal + optional static HTML)")
    rp.add_argument("--host")
    rp.add_argument("--suite-version")
    rp.add_argument("--tier")
    rp.add_argument("--html", help="write a self-contained HTML page here")
    rp.set_defaults(fn=cmd_report)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
