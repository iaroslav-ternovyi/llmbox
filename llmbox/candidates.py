"""Models on Hugging Face that nobody has measured yet: what fits which box, how fast, and what score to expect.

discover() takes the most downloaded and the trending GGUF text-generation repos, keeps one repo per base model (the
quantizer with the most downloads), drops what is too small to matter or too big for a desktop, and reads one file's
header per model: the 4-bit-first file `recipe new` would pick. That shape feeds the same speed predictor as the
ranking, so the site can say "~70 tok/s on your box" before anyone downloads 20 GB.

The expected score comes only from measurements: models with the same architecture and size (same layers, experts,
parameters within 5%) are almost always fine-tunes of one base, and their measured range is the honest prior. A model
with no measured relative gets no number.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import draft as D
from . import estimate as E
from . import hf
from .hosts import HOME

API = "https://huggingface.co/api/models"
CACHE = os.path.join(HOME, "hf")
TTL = 24 * 3600
MIN_PARAMS, MAX_PARAMS = 3e9, 260e9      # below: toys; above: no desktop holds it
QUANTIZERS = ("unsloth", "bartowski", "lmstudio-community", "mradermacher", "ggml-org")
NOT_CHAT = {"clip", "audiocpp", "locateanything", "whisper", "bert", "nomic-bert", "t5", "t5encoder", "wavtokenizer-dec", "parakeet", "yue2"}
PER_FAMILY = 3


def _get(url: str, ttl: int = TTL):
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, urllib.parse.quote(url, safe="")[:200] + ".json")
    if os.path.exists(cp) and time.time() - os.path.getmtime(cp) < ttl:
        return json.load(open(cp))
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "llmbox"}), timeout=60) as r:
        d = json.loads(r.read())
    json.dump(d, open(cp, "w"))
    return d


def _base(m: dict) -> str:
    tags = m.get("tags") or []
    for pre in ("base_model:quantized:", "base_model:finetune:", "base_model:"):
        b = next((t[len(pre):] for t in tags if t.startswith(pre)), None)
        if b:
            return b
    return m["id"].replace("-GGUF", "").replace("-gguf", "")


def discover(n: int = 30) -> list[dict]:
    # no pipeline filter: vision-language models (Qwen3.6, Gemma 4) are tagged image-text-to-text but chat fine as text;
    # their projector files are skipped below and non-LLM repos fall out at the header read
    q = f"{API}?filter=gguf&limit=200&expand[]=downloads&expand[]=likes&expand[]=tags&expand[]=createdAt&expand[]=trendingScore&expand[]=pipeline_tag"
    seen: dict = {}
    for sort in ("downloads", "trendingScore"):
        for m in _get(f"{q}&sort={sort}&direction=-1"):
            if m.get("pipeline_tag") in (None, "text-generation", "image-text-to-text", "any-to-any"):
                seen.setdefault(m["id"], m)
    groups: dict = {}
    for m in seen.values():
        groups.setdefault(_base(m), []).append(m)
    # per base: the quantizer repo people actually download
    picks = []
    for base, ms in groups.items():
        ms.sort(key=lambda m: (not m["id"].split("/")[0] in QUANTIZERS, -(m.get("downloads") or 0)))
        m = ms[0]
        picks.append({"repo": m["id"], "base": base, "downloads": sum(x.get("downloads") or 0 for x in ms),
                      "likes": m.get("likes") or 0, "trending": max(x.get("trendingScore") or 0 for x in ms),
                      "created": (m.get("createdAt") or "")[:10]})
    picks.sort(key=lambda p: -(p["downloads"] + 20000 * p["trending"]))
    out = []

    def shape(p: dict):
        try:
            files = hf.list_gguf(p["repo"])
        except Exception:
            return None
        files = [f for f in files if "mmproj" not in f.name.lower()]
        if not files:
            return None
        f = sorted(files, key=D._rank)[0]
        cp = os.path.join(HOME, "shapes", os.path.basename(f.name) + ".json")
        if os.path.exists(cp):
            sh = E.ModelShape(**json.load(open(cp)))
        else:
            try:
                sh = E.analyze(hf.read_headers(f))
            except Exception:
                return None
            os.makedirs(os.path.dirname(cp), exist_ok=True)
            json.dump(dict(sh.__dict__), open(cp, "w"))
        return dict(p, file=f.name, quant=f.quant, bytes=f.size, sha256=f.sha256[0], shape=sh)
    fam_n: dict = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for c in ex.map(shape, picks[: n * 4]):
            if not c or not MIN_PARAMS <= c["shape"].total_params <= MAX_PARAMS or c["shape"].arch in NOT_CHAT or not c["shape"].attn_layers:
                continue
            fam = family(c["shape"])
            fam_n[fam] = fam_n.get(fam, 0) + 1
            if fam_n[fam] > PER_FAMILY:   # one base with a dozen uncensored remixes would fill the list
                continue
            out.append(c)
            if len(out) >= n:
                break
    return out


def family(sh: E.ModelShape) -> tuple:
    """Same pretrained base, as far as the file can tell: architecture, layers (MTP excluded), experts, attention layout."""
    return (sh.arch, sh.n_layers, sh.n_expert, sh.n_expert_used, sh.kv_heads, sh.attn_layers)


def relatives(sh: E.ModelShape, measured: dict) -> list[tuple[str, float]]:
    """(recipe id, % of frontier) of measured models with the same architecture and size."""
    fam = family(sh)
    return sorted(((rid, vs) for rid, (msh, vs) in measured.items() if family(msh) == fam and vs is not None), key=lambda x: -x[1])


def load(max_age: int = TTL, n: int = 40) -> list[dict]:
    """Cached discovery (~/.llmbox/candidates.json); refreshed when older than max_age, the old list kept if offline."""
    cp = os.path.join(HOME, "candidates.json")
    if os.path.exists(cp) and time.time() - os.path.getmtime(cp) < max_age:
        return json.load(open(cp))
    try:
        cs = discover(n)
    except Exception:
        return json.load(open(cp)) if os.path.exists(cp) else []
    rows = [dict({k: v for k, v in c.items() if k != "shape"}, shape=dict(c["shape"].__dict__)) for c in cs]
    json.dump(rows, open(cp, "w"))
    return rows


def recipe_id(repo: str) -> str:
    """The id `llmbox recipe new` gives this repo."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", repo.split("/")[-1].lower().replace("-gguf", "")).strip("-")


def base_chain(repo: str, hops: int = 5) -> list[str]:
    """repo, its declared base, that one's base... (Hugging Face cardData.base_model), cached."""
    chain = [repo]
    for _ in range(hops):
        try:
            m = _get(f"{API}/{chain[-1]}?expand[]=cardData", ttl=7 * TTL)
        except Exception:
            break
        b = (m.get("cardData") or {}).get("base_model")
        b = (b[0] if isinstance(b, list) and b else b) or None
        if not b or b in chain:
            break
        chain.append(b)
    return chain
