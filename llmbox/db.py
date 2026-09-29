"""The results database (step 1 of docs/results-db.md): SQLite at ~/.llmbox/llmbox.db over content-addressed bundles.

Every result record ends up here as an immutable bundle - its exact JSON, gzip, named by its sha256 in
~/.llmbox/bundles - plus normalized rows for querying: runs, answers (one per task, no big texts), speed points,
hardware profiles and classes, model files, settings split into the part that sets the score (portable) and the part
that only sets the speed (hardware), suites. Raw answers never change; scores, rankings and pages are derived and can
be recomputed at any time.

For now the JSON files in ~/.llmbox/results stay where every tool writes (the queue's snapshots too): `sync()` takes
in each new or changed file as a new bundle version (a record graded later keeps its history) and readers go through
`files()`, which returns what the database holds. LLMBOX_NO_DB=1 reads the files directly instead.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import time

from .hosts import HOME

DB = os.path.join(HOME, "llmbox.db")
BUNDLES = os.path.join(HOME, "bundles")
RESULTS = os.path.join(HOME, "results")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bundles (sha TEXT PRIMARY KEY, bytes INTEGER, stored_at TEXT);
CREATE TABLE IF NOT EXISTS sources (path TEXT PRIMARY KEY, host_dir TEXT, size INTEGER, mtime REAL, sha TEXT, run_id TEXT,
    seen_at TEXT, deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS hardware_profiles (id TEXT PRIMARY KEY, gpu TEXT, vram_gib REAL, gpu_driver TEXT,
    gpu_power_limit_w REAL, cpu TEXT, threads INTEGER, ram_gib REAL, ram_read_gbs REAL, os TEXT, backend TEXT, hw_class TEXT);
CREATE TABLE IF NOT EXISTS model_files (hf_repo TEXT, file TEXT, sha256 TEXT, PRIMARY KEY (hf_repo, file));
CREATE TABLE IF NOT EXISTS settings (hash TEXT PRIMARY KEY, part TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS suites (content_hash TEXT PRIMARY KEY, version TEXT);
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, path TEXT, host_dir TEXT, kind TEXT, created TEXT, hardware_id TEXT,
    recipe_id TEXT, hf_repo TEXT, file TEXT, portable_hash TEXT, hardware_hash TEXT, runtime_build TEXT, suite_hash TEXT,
    suite_version TEXT, tier TEXT, capability REAL, ci_lo REAL, ci_hi REAL, decode_tps REAL, n_answers INTEGER,
    n_pending INTEGER, bundle TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS bundle_history (run_id TEXT, sha TEXT, at TEXT, PRIMARY KEY (run_id, sha));
CREATE TABLE IF NOT EXISTS answers (run_id TEXT, n INTEGER, item_id TEXT, family TEXT, block TEXT, score REAL,
    seconds REAL, completion_tokens INTEGER, finish TEXT, error TEXT, pending INTEGER, reasoning_cut INTEGER,
    PRIMARY KEY (run_id, n));
CREATE TABLE IF NOT EXISTS speed_points (run_id TEXT, depth TEXT, depth_k REAL, decode_tps REAL, prefill_tps REAL,
    PRIMARY KEY (run_id, depth));
CREATE INDEX IF NOT EXISTS answers_family ON answers (family);
CREATE INDEX IF NOT EXISTS runs_recipe ON runs (recipe_id, suite_version);
CREATE VIEW IF NOT EXISTS hardware_classes AS
    SELECT hw_class, COUNT(*) AS profiles, GROUP_CONCAT(DISTINCT gpu) AS gpus FROM hardware_profiles GROUP BY hw_class;
"""

# the recipe keys that set the score (llmbox/fit.py PORTABLE without the prose and the model identity)
_SCORE_KEYS = ("chat", "sampling", "antiloop", "placement.kv_type")
_RAM_SIZES = (8, 16, 24, 32, 36, 48, 64, 96, 128, 192, 256, 512)


def connect() -> sqlite3.Connection:
    os.makedirs(HOME, exist_ok=True)
    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=60000")
    c.executescript(SCHEMA)
    return c


def _v(x):
    """A value SQLite can hold: scalars as they are, anything else (a split file's list of hashes) as JSON."""
    return x if x is None or isinstance(x, (str, int, float)) else json.dumps(x, sort_keys=True)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _bundle_path(sha: str) -> str:
    return os.path.join(BUNDLES, sha[:2], sha + ".json.gz")


def put_bundle(c: sqlite3.Connection, raw: bytes) -> str:
    """Store a record's exact bytes once; the name is their sha256."""
    sha = hashlib.sha256(raw).hexdigest()
    p = _bundle_path(sha)
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with gzip.open(p + ".tmp", "wb", compresslevel=6) as f:
            f.write(raw)
        os.replace(p + ".tmp", p)
    c.execute("INSERT OR IGNORE INTO bundles VALUES (?,?,?)", (sha, len(raw), _now()))
    return sha


def get_bundle(sha: str) -> dict:
    with gzip.open(_bundle_path(sha), "rb") as f:
        return json.load(f)


def hw_class(h: dict) -> str:
    """A coarse group for comparing speed across people: GPU and its VRAM, RAM size and read speed, backend."""
    if h.get("id") == "cloud" or not h:
        return "cloud"
    gpu = (h.get("gpu") or "").replace("NVIDIA GeForce ", "") or "no GPU"
    ram = h.get("ram_gib") or 0
    size = min(_RAM_SIZES, key=lambda s: abs(s - ram)) if ram else 0
    bw = h.get("ram_read_gbs")
    return f"{gpu} {h.get('vram_gib') or 0:.0f} GB · RAM {size} GB" + (f" · ~{round(bw / 10) * 10:.0f} GB/s" if bw else "") + f" · {_backend(h)}"


def _backend(h: dict) -> str:
    g = (h.get("gpu") or "").lower()
    return "cuda" if "nvidia" in g or "geforce" in g or "rtx" in g else "metal" if "apple" in g or (h.get("os") or "").lower().startswith("mac") else "cpu" if not g else "gpu"


def _settings(c: sqlite3.Connection, rec: dict) -> tuple[str | None, str | None]:
    from . import fit
    r = rec.get("recipe") or {}
    if not r:
        return None, None
    hard_keys = tuple(k for k in fit.HARDWARE if k not in ("model.path", "serve"))
    out = []
    for part, keys in (("portable", _SCORE_KEYS), ("hardware", hard_keys)):
        layer = fit.layer(r, keys)
        h = _hash(layer)
        c.execute("INSERT OR IGNORE INTO settings VALUES (?,?,?)", (h, part, json.dumps(layer, sort_keys=True)))
        out.append(h)
    return out[0], out[1]


def ingest(c: sqlite3.Connection, path: str, raw: bytes | None = None) -> str | None:
    """One result file into the database: a bundle (new version if its content changed) and its derived rows."""
    from .irt import family_of
    raw = raw if raw is not None else open(path, "rb").read()
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    sha = put_bundle(c, raw)
    rid = rec.get("id") or sha[:32]
    prev = c.execute("SELECT path FROM runs WHERE id=?", (rid,)).fetchone()
    if prev and prev["path"] != path:   # the same record id in another file (a copied result): keep both, apart
        rid = f"{rid}@{hashlib.sha256(path.encode()).hexdigest()[:8]}"
    h = rec.get("host") or {}
    hw_id = h.get("id") or "unknown"
    c.execute("INSERT OR REPLACE INTO hardware_profiles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              tuple(_v(x) for x in (hw_id, h.get("gpu"), h.get("vram_gib"), h.get("gpu_driver"), h.get("gpu_power_limit_w"), h.get("cpu"),
               h.get("threads"), h.get("ram_gib"), h.get("ram_read_gbs"), h.get("os"), _backend(h), hw_class(h))))
    m = rec.get("model") or (rec.get("recipe") or {}).get("model") or {}
    if m.get("hf_repo") and m.get("file"):
        c.execute("INSERT INTO model_files VALUES (?,?,?) ON CONFLICT (hf_repo, file) DO UPDATE SET sha256=COALESCE(excluded.sha256, sha256)",
                  (_v(m["hf_repo"]), _v(m["file"]), _v(m.get("sha256"))))
    ph, hh = _settings(c, rec)
    su, s = rec.get("suite") or {}, rec.get("summary") or {}
    if su.get("content_hash"):
        c.execute("INSERT OR IGNORE INTO suites VALUES (?,?)", (su["content_hash"], su.get("version")))
    rows = rec.get("rows") or []
    ci = s.get("capability_ci95") or [None, None]
    rt = rec.get("runtime") or {}
    c.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              tuple(_v(x) for x in (rid, path, os.path.basename(os.path.dirname(path)), rec.get("kind"), rec.get("created"), hw_id,
               (rec.get("recipe") or {}).get("id"), m.get("hf_repo"), m.get("file"), ph, hh,
               rt.get("build") or rt.get("version"), su.get("content_hash"), su.get("version"), su.get("tier"),
               s.get("capability"), ci[0], ci[1], (s.get("speed") or {}).get("decode_tps"),
               len(rows), sum(1 for x in rows if x.get("pending")), sha, "ok")))
    c.execute("INSERT OR IGNORE INTO bundle_history VALUES (?,?,?)", (rid, sha, _now()))
    c.execute("DELETE FROM answers WHERE run_id=?", (rid,))
    c.executemany("INSERT INTO answers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  [tuple(_v(v) for v in (rid, i, x.get("id"), x.get("family") or (family_of(x["id"]) if x.get("id") else None), x.get("block"),
                    x.get("score"), x.get("seconds"), (x.get("usage") or {}).get("completion_tokens"), x.get("finish_reason"),
                    x.get("error"), 1 if x.get("pending") else 0, x.get("reasoning_cut"))) for i, x in enumerate(rows) if isinstance(x, dict)])
    c.execute("DELETE FROM speed_points WHERE run_id=?", (rid,))
    for k, d in ((s.get("speed") or {}).get("by_depth") or {}).items():
        from .report import _depth_k
        if isinstance(d, dict):
            c.execute("INSERT OR REPLACE INTO speed_points VALUES (?,?,?,?,?)", (rid, k, _depth_k(k), _v(d.get("decode_tps")), _v(d.get("prefill_tps"))))
    return rid


def sync(c: sqlite3.Connection | None = None, progress=None) -> int:
    """Take in every result file that is new or changed since the last look; mark removed ones. Returns the number
    taken in. Cheap when nothing changed (one stat per file)."""
    own = c is None
    c = c or connect()
    known = {r["path"]: r for r in c.execute("SELECT path, size, mtime, deleted FROM sources")}
    seen, n = set(), 0
    for h in sorted(os.listdir(RESULTS)) if os.path.isdir(RESULTS) else []:
        d = os.path.join(RESULTS, h)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not f.endswith(".json"):
                continue
            p = os.path.join(d, f)
            st = os.stat(p)
            seen.add(p)
            k = known.get(p)
            if k and k["size"] == st.st_size and k["mtime"] == st.st_mtime and not k["deleted"]:
                continue
            raw = open(p, "rb").read()
            with c:
                rid = ingest(c, p, raw)
                c.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,0)",
                          (p, h, st.st_size, st.st_mtime, hashlib.sha256(raw).hexdigest(), rid, _now()))
            n += 1
            if progress and n % 50 == 0:
                progress(f"  {n} files taken in")
    gone = [p for p, k in known.items() if p not in seen and not k["deleted"]]
    if gone:
        with c:
            c.executemany("UPDATE sources SET deleted=1 WHERE path=?", [(p,) for p in gone])
            c.executemany("UPDATE runs SET status='file removed' WHERE path=?", [(p,) for p in gone])
    if own:
        c.close()
    return n


def files(host: str | None = None) -> list[tuple[str, dict]]:
    """(path, record) of every present result file, per host directory, in file-name order - from the database."""
    c = connect()
    sync(c)
    q = "SELECT s.path, s.sha FROM sources s WHERE s.deleted=0" + (" AND s.host_dir=?" if host else "") + " ORDER BY s.host_dir, s.path"
    out = [(r["path"], get_bundle(r["sha"])) for r in c.execute(q, (host,) if host else ())]
    c.close()
    return out


def stats() -> dict:
    c = connect()
    sync(c)
    q = lambda s: c.execute(s).fetchone()[0]
    out = {"runs": q("SELECT COUNT(*) FROM runs WHERE status='ok'"), "answers": q("SELECT COUNT(*) FROM answers"),
           "bundles": q("SELECT COUNT(*) FROM bundles"), "bundle_versions": q("SELECT COUNT(*) FROM bundle_history"),
           "speed_points": q("SELECT COUNT(*) FROM speed_points"), "hardware_profiles": q("SELECT COUNT(*) FROM hardware_profiles"),
           "model_files": q("SELECT COUNT(*) FROM model_files"), "settings": q("SELECT COUNT(*) FROM settings"),
           "suites": q("SELECT COUNT(*) FROM suites"),
           "by_kind": {r[0]: r[1] for r in c.execute("SELECT kind, COUNT(*) FROM runs WHERE status='ok' GROUP BY kind")},
           "hardware_classes": [dict(r) for r in c.execute("SELECT * FROM hardware_classes")],
           "db_mb": round(os.path.getsize(DB) / 2**20, 2)}
    c.close()
    return out
