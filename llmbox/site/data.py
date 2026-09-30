"""What the pages are built from: saved records, pooled scores, speed shapes of the models, thinking flags, the job queue."""
from __future__ import annotations

import os
import re
import sqlite3

from .. import report
from .. import suite as _suite
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


# common GPUs: VRAM (MiB) and memory bandwidth (GB/s), for the "your box" picker
GPUS = [("RTX 3060 12 GB", 12288, 360), ("RTX 3090 24 GB", 24576, 936), ("RTX 4060 Ti 16 GB", 16380, 288),
        ("RTX 4070 12 GB", 12282, 504), ("RTX 4070 Ti Super 16 GB", 16376, 672), ("RTX 4080 16 GB", 16376, 717),
        ("RTX 4090 24 GB", 24564, 1008), ("RTX 5060 Ti 16 GB", 16311, 448), ("RTX 5070 12 GB", 12227, 672),
        ("RTX 5070 Ti 16 GB", 16303, 896), ("RTX 5080 16 GB", 16303, 960), ("RTX 5090 32 GB", 32607, 1792)]


# Apple Silicon: unified memory, so no VRAM/RAM split - (name, 0, memory bandwidth GB/s, "mac", largest memory GB).
# Bandwidth: Apple's specs (M5 Pro 307, M5 Max 460 / 614: apple.com newsroom 2026-03). Nothing is measured on a Mac here.
MACS = [("Mac M1", 0, 68, "mac", 16), ("Mac M1 Pro", 0, 200, "mac", 32), ("Mac M1 Max", 0, 400, "mac", 64), ("Mac M1 Ultra", 0, 800, "mac", 128),
        ("Mac M2", 0, 100, "mac", 24), ("Mac M2 Pro", 0, 200, "mac", 32), ("Mac M2 Max", 0, 400, "mac", 96), ("Mac M2 Ultra", 0, 800, "mac", 192),
        ("Mac M3", 0, 100, "mac", 24), ("Mac M3 Pro", 0, 150, "mac", 36), ("Mac M3 Max 30-core GPU", 0, 300, "mac", 96),
        ("Mac M3 Max 40-core GPU", 0, 400, "mac", 128), ("Mac M3 Ultra", 0, 819, "mac", 512),
        ("Mac M4", 0, 120, "mac", 32), ("Mac M4 Pro", 0, 273, "mac", 64), ("Mac M4 Max 32-core GPU", 0, 410, "mac", 36),
        ("Mac M4 Max 40-core GPU", 0, 546, "mac", 128), ("Mac M5", 0, 153, "mac", 32), ("Mac M5 Pro", 0, 307, "mac", 64),
        ("Mac M5 Max 32-core GPU", 0, 460, "mac", 128), ("Mac M5 Max 40-core GPU", 0, 614, "mac", 128)]


GPUS = GPUS + MACS


RAM_KINDS = [("DDR4-3200", 40), ("DDR5-5600", 60), ("DDR5-6400", 75), ("DDR5-8000", 88)]


def shape_data(local: list[dict], host: str = "box") -> dict:
    """Per recipe: the GGUF shape numbers estimate.plan() uses, plus the calibration measured / predicted on the
    reference box (llmbox.fit, the same numbers `llmbox fit` prints)."""
    from .. import fit as F, hosts, recipe as rc
    prof = hosts.load(host)
    ref_hw = hosts.spec(prof)
    out = {}
    for r in local:
        try:
            rec = rc.load(host, r["id"])
        except (OSError, ValueError):
            continue
        sh = F.shape_for(rec, host=hosts.host_of(prof))
        cal = F.calibration(rec, sh, host)
        kv, ctx = rec["placement"]["kv_type"], rec["placement"]["ctx"] or sh.context_length
        out[r["id"]] = {"moe": sh.is_moe, "nonexp": sh.nonexpert_bytes, "exp": sh.expert_bytes, "embed": sh.embed_bytes,
                        "layers": sh.n_layers, "nExp": sh.n_expert, "nUsed": sh.n_expert_used, "rec": sh.recurrent_state_bytes + sh.kv_swa_bytes(kv),
                        "cpuEff": sh.expert_cpu_eff, "kvB": sh.kv_bytes_per_token(kv), "ctx": ctx, "k2": round(cal.k2, 4), "kd": round(cal.kd, 4),
                        "deepK": cal.deep_k, "size": round((sh.total_bytes or 0) / 1e9, 1),
                        "arch": sh.arch, "params": int(sh.total_params * (1 - (sh.mtp_bytes or 0) / sh.total_bytes)) if sh.total_bytes else 0,
                        "active": sh.active_params}
    return {"recipes": out, "ref": {"gpu": prof["hw"]["gpus"][0]["name"].replace("NVIDIA GeForce ", "") if prof["hw"]["gpus"] else "",
                                    "vram": ref_hw.vram_mib, "ram": ref_hw.ram_mib, "rambw": ref_hw.ram_bw_gbs, "vrambw": ref_hw.vram_bw_gbs},
            "gpus": GPUS, "ramKinds": RAM_KINDS}


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
