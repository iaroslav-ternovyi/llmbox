"""What the pages are built from: saved records, pooled scores, speed shapes of the models, thinking flags, the job queue."""
from __future__ import annotations

import os
import re
import sqlite3

from .. import report
from .. import suite as _suite
from ..estimate import gpu_generation
from ..hwclass import AMD_CARDS, APUS, MAC_MEMORY, MACS, NVIDIA_CARDS
from ..hosts import HOME


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


# the hardware picker's entries live in hwclass (NVIDIA_CARDS, AMD_CARDS, APUS, MACS): the CLI maps a machine to one too
GPUS, AMD = NVIDIA_CARDS, AMD_CARDS


# the generation factor plan.js reads: NVIDIA g[3], Mac g[5] (g[3] is "mac" there); a Mac's memory sizes g[6]
# AMD: g[3] the generation factor, g[4] "vulkan"; Ryzen AI Max: g[5] the factor (1)
GPUS = ([(n, v, b, gpu_generation(n)) for n, v, b in GPUS] + [(n, v, b, 1.0, "vulkan") for n, v, b in AMD]
        + [m + (gpu_generation(m[0]), MAC_MEMORY[m[0]]) for m in MACS] + [a + (1.0,) for a in APUS])


RAM_KINDS = [("DDR4-3200", 40), ("DDR5-5600", 60), ("DDR5-6400", 75), ("DDR5-8000", 88)]


def gpu_classes() -> dict:
    """Each discrete card's class prefix ("RTX 5070 12 GB" -> "rtx-5070-12g"), for plan.js classOf: the class people's
    measurements of a picked box are filed under (hwclass.key)."""
    from .. import hwclass
    return {g[0]: hwclass.key(g[0], g[1], vendor="amd" if g[4:5] == ("vulkan",) else "nvidia").split("|")[0]
            for g in GPUS if g[3] not in ("mac", "apu")}


def js_shape(sh, kv: str, ctx: int, cal) -> dict:
    from .. import estimate as E
    """What plan.js reads about a model (the pages' DATA): estimate.plan's inputs from its GGUF shape, the KV type and
    context of its recipe, and its calibration on the reference box. tests/test_parity.py holds the two to each other."""
    return {"moe": sh.is_moe, "nonexp": sh.nonexpert_bytes + sh.output_tied, "exp": sh.expert_bytes, "embed": sh.embed_bytes,
            "layers": sh.n_layers, "nExp": sh.n_expert, "nUsed": sh.n_expert_used, "rec": sh.recurrent_state_bytes + sh.kv_swa_bytes(kv),
            "cpuEff": sh.expert_cpu_eff, "kvB": sh.kv_bytes_per_token(kv), "swaB": sh.kv_swa_bytes(kv, 1), "swaW": sh.swa_window, "ctx": ctx,
            "k2": round(cal.k2, 4), "kd": round(cal.kd, 4), "term": cal.term, "deepK": cal.deep_k,
            # Metal's attention at depth (estimate.metal_attention_s): the work per context token, the cost per unit of it
            "qF": sh.q_dims()[0], "qS": sh.q_dims()[1], "attPs": E.metal_attention_ps(sh.k_len), "attPsS": E.metal_attention_ps(sh.k_swa_len or sh.k_len)}


def shape_data(local: list[dict], host: str = "box") -> dict:
    """Per recipe: the GGUF shape numbers estimate.plan() uses, plus the calibration measured / predicted on the
    reference box (llmbox.fit, the same numbers `llmbox fit` prints)."""
    from .. import fit as F, hosts, hwclass, recipe as rc
    prof = hosts.load(host)
    ref_hw = hosts.spec(prof)
    out, files = {}, {}
    for r in local:
        try:
            rec = rc.load(host, r["id"])
        except (OSError, ValueError):
            continue
        files[r["id"]] = os.path.basename(rec["model"].get("file") or rec["model"].get("path") or "")
        sh = F.shape_for(rec, host=hosts.host_of(prof))
        cal = F.calibration(rec, sh, host)
        kv, ctx = rec["placement"]["kv_type"], rec["placement"]["ctx"] or sh.context_length
        out[r["id"]] = dict(js_shape(sh, kv, ctx, cal), size=round((sh.total_bytes or 0) / 1e9, 1), arch=sh.arch,
                            params=int(sh.total_params * (1 - (sh.mtp_bytes or 0) / sh.total_bytes)) if sh.total_bytes else 0,
                            active=sh.active_params, engine=rec["runtime"].get("engine") or "llama.cpp")
    return {"recipes": out, "ref": {"gpu": prof["hw"]["gpus"][0]["name"].replace("NVIDIA GeForce ", "") if prof["hw"]["gpus"] else "",
                                    "vram": ref_hw.vram_mib, "ram": ref_hw.ram_mib, "rambw": ref_hw.ram_bw_gbs, "vrambw": ref_hw.vram_bw_gbs},
            "gpus": GPUS, "ramKinds": RAM_KINDS,
            # speeds people measured, per recipe and hardware class: [t2, deep, machines, people] (community_speeds)
            "cm": {rid: {c["class"]: [c["t2"], c["t80"] or c["t32"], c["machines"], c["people"]] for c in cls}
                   for rid, cls in community_speeds(files, host).items()},
            "gpuClass": gpu_classes(),
            "ramEdges": list(hwclass.RAM_EDGES)}


def load_records(host: str, suite_version: str, tier: str) -> dict:
    """Newest suite record per recipe id for this host and suite, plus the frontier reference. Keeps the file path."""
    out, ref, runs = {}, None, {}
    for h in (host, "cloud"):
        for path, rec in report.results.files(h):
            su = rec.get("suite") or {}
            cur = suite_version == _suite.VERSION and bool(report.current_pool())
            if rec.get("kind") != "suite" or report.scale(su.get("tier")) != report.scale(tier) or su.get("blocks") or (
                    not report.ranked_now((rec.get("recipe") or {}).get("id") or "?", h) if cur else report.version_of(su) != suite_version):
                continue   # the current suite: any run of a model whose answers still cover every block (report.current_pool)
            from .. import bench
            rec = bench.rescore(rec)   # current suite weights: the task scores are the run's, the weighting is today's
            rec["_path"] = path
            rid = (rec.get("recipe") or {}).get("id")
            runs.setdefault((h, rid), []).append(rec)
            if h == "cloud":
                if not ref or rec["summary"]["capability"] > ref["summary"]["capability"]:
                    ref = rec
            elif rid:
                out[rid] = rec   # sorted by name = by time: the newest wins
    for (h, rid), recs in runs.items():   # the number of a model = the IRT estimate over all its runs (report._pool)
        tgt = ref if h == "cloud" and ref is not None and (ref.get("recipe") or {}).get("id") == rid else out.get(rid) if h != "cloud" else None
        if tgt is not None:
            _pool_summary(tgt, recs, h, current=suite_version == _suite.VERSION)
    return {"local": out, "ref": ref}


def optimize_records(host: str) -> dict:
    """{recipe id: newest "optimize" record} - stock llama.cpp vs the tuned recipe, measured back to back (llmbox optimize)."""
    out = {}
    for path, rec in report.results.files(host):
        if "-optimize-" in os.path.basename(path):
            s = rec.get("summary") or {}
            if (s.get("stock") or {}).get("decode") and (s.get("llmbox") or {}).get("decode"):
                out[(rec.get("recipe") or {}).get("id")] = rec   # sorted by name = by time: the newest wins
    return out


def _model_now(host: str, rid: str) -> tuple[dict, bool | None]:
    """The recipe's model block as it is now (a repo fixed since the run) and whether that exact file is on Hugging Face
    (None: could not ask)."""
    from .. import hf, recipe as rc
    try:
        m = rc.load(host, rid)["model"]
    except (OSError, ValueError):
        return {}, None
    try:
        fs = hf.list_gguf(m["hf_repo"], ttl=86400)
        names = {f.name for f in fs} | {os.path.basename(p) for f in fs for p in f.parts}
        return m, os.path.basename(m.get("file") or "") in names or m.get("file") in names
    except Exception:
        return m, None


def _pool_summary(rec: dict, recs: list[dict], where: str = "box", current: bool = False) -> None:
    from .. import irt, report as _report
    p = _report.ranked_now((rec.get("recipe") or {}).get("id") or "?", where) if current else None
    if p:   # the current suite: every answer that still counts, from any run (report.current_pool)
        s = rec["summary"]
        rec["summary"] = dict(s, capability=p["score"]["capability"], capability_ci95=p["score"]["ci95"], blocks=p["score"]["blocks"],
                              runs=p["runs"], answers=p["score"]["n"], scoring="irt", capability_this_run=s.get("capability"),
                              ci_this_run=s.get("capability_ci95"))
        return
    bank = irt.bank_for((rec.get("suite") or {}).get("content_hash"))
    if bank is None:
        return
    key = lambda r: irt.canonical((r.get("suite") or {}).get("content_hash"))
    same = [r for r in recs if key(r) == key(rec)]
    sc = irt.score_rows(bank, [x for r in same for x in r.get("rows") or []])
    if sc["n"]:
        s = rec["summary"]
        rec["summary"] = dict(s, capability=sc["capability"], capability_ci95=sc["ci95"], blocks=sc["blocks"], runs=len(same),
                              answers=sc["n"], scoring="irt", capability_this_run=s.get("capability"))


def _trace_dir(rec: dict) -> str | None:
    """Traces are saved per queue job (~/.llmbox/traces/job-N); the queue knows which job produced which record."""
    db = os.path.join(HOME, "queue.db")
    if os.path.exists(db):
        row = sqlite3.connect(db).execute("SELECT id FROM jobs WHERE result=?", (rec.get("_path"),)).fetchone()
        if row:
            d = os.path.join(HOME, "traces", f"job-{row[0]}")
            return d if os.path.isdir(d) else None
    return None


def task_flags(rec: dict) -> dict:
    """item id -> {'cut': bool, 'loop': bool, 'max_reply': int} from the rows and the saved thinking."""
    import gzip
    import json as _json
    from .. import cache, loopdetect
    td = _trace_dir(rec)
    scanner = cache.key(open(loopdetect.__file__).read())   # a changed detector re-scans
    out = {}
    for r in rec.get("rows", []):
        cut = bool(r.get("reasoning_cut")) or "I have reasoned enough" in (r.get("reasoning_tail") or "")
        loop = False
        f = os.path.join(td, r["id"] + ".json.gz") if td else ""
        if f and os.path.exists(f):   # scanning the saved thinking is slow: cached by the file's size and time
            st = os.stat(f)
            loop = cache.memo("loops", cache.key(f, st.st_size, st.st_mtime, scanner),
                              lambda: any(loopdetect.scan(x)["fired_at"] for x in _json.load(gzip.open(f, "rt"))["thinking"]))
        out[r["id"]] = {"cut": cut, "loop": loop, "max_reply": max([x.get("predicted_n") or 0 for x in r.get("timings") or []] or [0])}
    return out


def _vs(rec: dict, ref: dict | None) -> float | None:
    return round(100 * rec["summary"]["capability"] / ref["summary"]["capability"], 1) if ref else None


# ---- speeds from other people's machines (llmbox submit -> llmbox serve -> the "community" host) ----------------------

def _pct(v: list[float], q: float) -> float:
    s = sorted(v)
    i = (len(s) - 1) * q
    lo = int(i)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (i - lo)


def _at(sp: dict, lo: int, hi: int) -> float | None:
    """decode tok/s at the measured depth in [lo, hi) tokens (speed records list depths; suite records map them)."""
    pts = [(d.get("depth") or 0, d.get("decode_tps")) for d in sp.get("depth") or []]
    pts += [(report._depth_k(k) * 1000, d.get("decode_tps")) for k, d in (sp.get("by_depth") or {}).items()]
    v = [t for dep, t in pts if lo <= dep < hi and t]
    return v[0] if v else None


def community_speeds(recipes: dict, ref_host: str = "box") -> dict:
    """Per recipe id: its speed on every hardware class measured, the reference box included, as
    [{class, label, machines, people, runs, t2, t32, t80, pp, p5, p95, outliers, ctx, ref}], most machines first.
    recipes: {rid: model file name}. A machine counts once (the median of its runs), a class is the median of its
    machines; a figure the intake flagged as an outlier is left out and counted. p5/p95 only from 5 machines on."""
    import os as _os
    import statistics as st
    from .. import hwclass
    per: dict = {}   # (rid, class) -> machine id -> list of (t2, t32, t80, pp, client, flags)
    for h in (ref_host, "community"):
        for _p, rec in report.results.files(h):
            rid = (rec.get("recipe") or {}).get("id")
            if rec.get("kind") != "speed" or rid not in recipes or rec.get("overrides"):   # a variant is not the recipe
                continue
            f = _os.path.basename((rec.get("model") or {}).get("file") or (rec.get("model") or {}).get("path") or "")
            if f and recipes[rid] and f != recipes[rid]:
                continue
            sp = rec.get("speed") or {}
            if not sp.get("decode_tps"):
                continue
            hostfp = rec.get("host") or {}
            k = hostfp.get("class") or hwclass.of_host(hostfp)
            sub = rec.get("submission") or {}
            row = (sp["decode_tps"], _at(sp, 24000, 48000), _at(sp, 64000, 10**7), sp.get("prefill_tps"),
                   sub.get("client") or "reference", "outlier" in (sub.get("flags") or []), h == ref_host,
                   ((rec.get("recipe") or {}).get("placement") or {}).get("ctx") or 0)
            per.setdefault((rid, k), {}).setdefault(hostfp.get("id") or "?", []).append(row)
    out: dict = {}
    for (rid, k), machines in per.items():
        good = {m: [r for r in rows if not r[5]] for m, rows in machines.items()}
        good = {m: rows for m, rows in good.items() if rows}
        med = lambda i, rows: st.median([r[i] for r in rows if r[i]]) if any(r[i] for r in rows) else None
        mach = [{"t2": med(0, rows), "t32": med(1, rows), "t80": med(2, rows), "pp": med(3, rows)} for rows in good.values()]
        if not mach:
            continue
        cls = lambda f: round(st.median([m[f] for m in mach if m[f]]), 1) if any(m[f] for m in mach) else None
        t2s = [m["t2"] for m in mach]
        out.setdefault(rid, []).append({
            "class": k, "label": hwclass.label(k), "machines": len(mach),
            "people": len({r[4] for rows in good.values() for r in rows}), "runs": sum(len(r) for r in good.values()),
            "t2": cls("t2"), "t32": cls("t32"), "t80": cls("t80"), "pp": cls("pp"),
            "p5": round(_pct(t2s, 0.05), 1) if len(t2s) >= 5 else None, "p95": round(_pct(t2s, 0.95), 1) if len(t2s) >= 5 else None,
            "outliers": sum(1 for rows in machines.values() for r in rows if r[5]),
            "ctx": int(st.median([r[7] for rows in good.values() for r in rows if r[7]] or [0])),
            "ref": any(r[6] for rows in machines.values() for r in rows), "values": sorted(t2s)})   # values: for a person's place
    for v in out.values():
        v.sort(key=lambda c: (-c["machines"], -(c["t2"] or 0)))
    return out
