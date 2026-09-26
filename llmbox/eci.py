"""Expected score for a model nobody measured here, from the Epoch Capabilities Index (ECI).

Epoch AI publishes the ECI: one capability number per model, fitted over dozens of public benchmarks (CC BY 4.0,
epoch.ai/benchmarks, downloaded as benchmark_data.zip). It covers most popular open base models. We map it onto our
scale with anchors, models that have both an ECI and a measured score here:
  - the frontier reference (ECI of Claude Opus 5 stands in for Opus 5.5, which Epoch has not scored yet);
  - measured base models, or the mean of measured fine-tunes of one base while the base itself is unmeasured.
The map is a straight line through the anchors (least squares with more than two). With so few anchors it is rough,
so the prediction is a band: the ECI's own interval carried through the line, plus the anchors' scatter (at least
±8 points). Remixes of a base (abliterated, merges) get the base's band, marked as such.
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
import urllib.request
import zipfile

from .hosts import HOME

URL = "https://epoch.ai/data/benchmark_data.zip"
DIR = os.path.join(HOME, "epoch")
TTL = 7 * 24 * 3600
FRONTIER_PROXY = "Claude Opus 5"
MIN_BAND = 8.0
_FAM = re.compile(r"(qwen|gemma|glm|kimi|deepseek|minimax|mistral small|devstral|granite|llama|phi|ling|nemotron|lfm|gpt-oss|mimo|olmo|hermes|seed-oss)"
                  r"[\s\-_]*v?(\d+(?:\.\d+)?(?![\d.]*b))?", re.I)   # a version, not a size (gpt-oss-20b)
_SIZE = re.compile(r"(?<![a-z0-9])(a?\d+(?:\.\d+)?b)(?![a-z0-9])", re.I)
PLAIN = {"it", "instruct", "gguf", "qat", "mtp", "chat", "thinking", "base", "hf", "ud", "q4", "k", "xl", "m"}


def load() -> dict:
    """ECI rows by display name; the zip is refreshed weekly, the old copy kept when offline."""
    zp = os.path.join(DIR, "benchmark_data.zip")
    if not os.path.exists(zp) or time.time() - os.path.getmtime(zp) > TTL:
        try:
            os.makedirs(DIR, exist_ok=True)
            with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "llmbox"}), timeout=60) as r:
                data = r.read()
            open(zp, "wb").write(data)
        except Exception:
            if not os.path.exists(zp):
                return {}
    with zipfile.ZipFile(zp) as z:
        name = next(n for n in z.namelist() if n.endswith("epoch_capabilities_index/eci_scores.csv"))
        rows = list(csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8")))
    out = {}
    for r in rows:
        try:
            out[r["Display name"]] = {"eci": float(r["eci"]), "lo": float(r["eci_ci_low"]), "hi": float(r["eci_ci_high"]),
                                      "open": r.get("Accessibility group") == "Open weights"}
        except (ValueError, KeyError):
            continue
    return out


def key(name: str) -> tuple | None:
    """(family+version, sizes) - 'unsloth/Qwen3.6-35B-A3B-GGUF' and 'Qwen 3.6 35B-A3B' give the same key."""
    s = name.split("/")[-1].lower().replace("_", "-")
    m = _FAM.search(s)
    if not m:
        return None
    fam = m.group(1).replace(" ", "-") + (m.group(2) or "")
    sizes = tuple(sorted(x.lower() for x in _SIZE.findall(s[m.end():] if m.group(2) else s)))
    return (fam, sizes) if sizes or fam == "gpt-oss" else None


def is_remix(repo_name: str) -> bool:
    """Words beyond family, size and the usual packaging suffixes: an abliterated / merged / renamed variant."""
    s = repo_name.split("/")[-1].lower()
    m = _FAM.search(s)
    rest = (s[:m.start()] + " " + s[m.end():]) if m else s
    rest = _SIZE.sub(" ", rest)
    words = [w for w in re.split(r"[^a-z0-9.]+", rest) if w and not w.isdigit()]
    return any(w not in PLAIN and not re.fullmatch(r"\d{4}|v\d+|q\d.*|iq\d.*|bf16|f16", w) for w in words)


def match(repo: str, table: dict) -> tuple[str, dict] | None:
    k = key(repo)
    if not k:
        return None
    hits = [(n, r) for n, r in table.items() if key(n) == k]
    return min(hits, key=lambda h: len(h[0])) if hits else None


def fit_line(anchors: list[tuple[float, float]]) -> tuple[float, float, float]:
    """capability = a + b * eci through the anchors; returns (a, b, rms residual)."""
    n = len(anchors)
    mx = sum(x for x, _ in anchors) / n
    my = sum(y for _, y in anchors) / n
    sxx = sum((x - mx) ** 2 for x, _ in anchors)
    b = sum((x - mx) * (y - my) for x, y in anchors) / sxx if sxx else 0.0
    a = my - b * mx
    rms = (sum((y - (a + b * x)) ** 2 for x, y in anchors) / n) ** 0.5
    return a, b, rms


def predictor(table: dict, anchors: list[tuple[str, float, float]], ref_capability: float):
    """anchors: (label, eci, measured capability). Returns f(eci, lo, hi) -> (mid, low, high) in % of the frontier."""
    if len(anchors) < 2:
        return None
    a, b, rms = fit_line([(e, c) for _, e, c in anchors])
    pad = max(MIN_BAND, 2 * rms)
    pct = lambda cap: max(0.0, min(100.0, 100 * cap / ref_capability))

    def f(eci: float, lo: float, hi: float) -> tuple[float, float, float]:
        return pct(a + b * eci), pct(a + b * lo - pad), pct(a + b * hi + pad)
    f.anchors, f.slope = anchors, b
    return f
