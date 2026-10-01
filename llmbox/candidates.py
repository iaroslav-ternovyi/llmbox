"""Models on Hugging Face that nobody has measured yet: what fits which box, how fast, and what score to expect.

discover() takes what the main quantizers uploaded most recently, what is trending and what is most downloaded, keeps
models released in the last six months that enough people pulled, one entry per model (the quantizer repo with the
most downloads), drops what is too small to matter or too big for a desktop, and reads one file's header per model: the
4-bit-first file `recipe new` would pick. The 2-4-bit files of the repo come along, so a smaller box can step down. That shape feeds the same speed predictor as the
ranking, so the site can say "~70 tok/s on your box" before anyone downloads 20 GB.

The expected score comes only from measurements: models with the same architecture and size (same layers, experts,
parameters within 5%) are almost always fine-tunes of one base, and their measured range is the honest prior. A model
with no measured relative gets no number.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import draft as D
from . import estimate as E
from . import hf
from .hosts import HOME
from . import UA

API = "https://huggingface.co/api/models"
CACHE = os.path.join(HOME, "hf")
TTL = 24 * 3600
MIN_PARAMS, MAX_PARAMS = 3e9, 260e9      # below: toys; above: no desktop holds it
QUANTIZERS = ("unsloth", "bartowski", "lmstudio-community", "mradermacher", "ggml-org")
NOT_CHAT = {"clip", "audiocpp", "locateanything", "whisper", "bert", "nomic-bert", "t5", "t5encoder", "wavtokenizer-dec", "parakeet", "yue2"}
PER_FAMILY = 4


def _get(url: str, ttl: int = TTL):
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, urllib.parse.quote(url, safe="")[:200] + ".json")
    if os.path.exists(cp) and time.time() - os.path.getmtime(cp) < ttl:
        return json.load(open(cp))
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
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


RECENT_DAYS = 183          # "new": released in the last six months
MIN_DL, MIN_TREND = 20_000, 20  # enough people pulled it, or it is clearly trending now - filters one-off uploads
NOT_TEXT = re.compile(r"text[-_]?encoder|qwen-image|embedding|rerank|diffusion|[-_]tts|whisper|[-_]ocr", re.I)   # GGUFs that are not chat models


def _key(base: str) -> str:
    """One entry per model: packaging variants of one release (QAT, MTP heads, unquantized mirrors) share it."""
    import re
    n = base.lower()
    n = re.sub(r"-(gguf|qat|mtp|unquantized|q4_0)(?=-|$)", "", n)
    return n


def _fetch() -> list[dict]:
    """GGUF repos from three angles: each main quantizer's newest uploads (new releases land there within a day),
    what is trending now, and the most downloaded (for releases that have settled in)."""
    fields = "&expand[]=downloads&expand[]=likes&expand[]=tags&expand[]=createdAt&expand[]=trendingScore&expand[]=pipeline_tag"
    urls = [f"{API}?author={a}&filter=gguf&sort=createdAt&direction=-1&limit=100{fields}" for a in QUANTIZERS]
    urls += [f"{API}?filter=gguf&sort={s}&direction=-1&limit=200{fields}" for s in ("trendingScore", "downloads")]
    seen: dict = {}
    for u in urls:
        try:
            for m in _get(u):
                seen.setdefault(m["id"], m)
        except Exception:
            continue
    # no pipeline filter beyond this: vision-language models (Qwen3.6, Gemma 4) are tagged image-text-to-text but chat
    # fine as text; their projector files are skipped and non-LLM repos fall out at the header read
    return [m for m in seen.values() if m.get("pipeline_tag") in (None, "text-generation", "image-text-to-text", "any-to-any")]


def discover(n: int = 60, days: int = RECENT_DAYS) -> list[dict]:
    """New models worth a look: released in the last `days` (the first GGUF of the model = its release day), pulled
    by enough people, one entry per model, with the quantized files a box of any size could pick from."""
    import datetime as _dt
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    groups: dict = {}
    for m in _fetch():
        groups.setdefault(_key(_base(m)), []).append(m)
    picks = []
    for key, ms in groups.items():
        released = min((m.get("createdAt") or "9999")[:10] for m in ms)
        dl = sum(m.get("downloads") or 0 for m in ms)
        trend = max(m.get("trendingScore") or 0 for m in ms)
        if released < cutoff or (dl < MIN_DL and trend < MIN_TREND) or any(NOT_TEXT.search(m["id"]) for m in ms):
            continue
        # the quantizer repo people actually download
        ms.sort(key=lambda m: (not m["id"].split("/")[0] in QUANTIZERS, -(m.get("downloads") or 0)))
        m = ms[0]
        picks.append({"repo": m["id"], "base": _base(m), "downloads": dl, "likes": m.get("likes") or 0, "trending": trend,
                      "released": released, "created": released})
    picks.sort(key=lambda p: -(p["downloads"] + 20000 * p["trending"]))
    out: list = []
    fails: list = []

    def shape(p: dict):
        try:
            files = hf.list_gguf(p["repo"], ttl=TTL)
        except Exception as e:
            fails.append(f"{p['repo']}: {e}")
            return None
        files = [f for f in files if "mmproj" not in f.name.lower()]
        if not files:
            return None
        f = sorted(files, key=D._rank)[0]
        cp = os.path.join(HOME, "shapes", os.path.basename(f.name) + ".json")
        if os.path.exists(cp) and not E.stale(json.load(open(cp))):
            sh = E.ModelShape(**json.load(open(cp)))
        else:
            try:
                sh = E.analyze(hf.read_headers(f))
            except Exception:
                return None
            os.makedirs(os.path.dirname(cp), exist_ok=True)
            json.dump(dict(sh.__dict__), open(cp, "w"))
        # the files a smaller box steps down to: 4-bit first, then 3 and 2 (largest first in each), legacy Q4_0/Q4_1 last
        ladder = sorted((x for x in files if 2 <= D._bits(x.quant) <= 4 and x.size >= f.size * 0.4),   # not draft heads / helpers
                        key=lambda x: (-D._bits(x.quant), bool(__import__("re").fullmatch(r"Q\d_[01]", x.quant.replace("UD-", "").upper())), -x.size))
        return dict(p, file=f.name, quant=f.quant, bytes=f.size, sha256=f.sha256[0], shape=sh,
                    ladder=[{"file": x.name, "quant": x.quant, "bytes": x.size} for x in ladder])
    with ThreadPoolExecutor(max_workers=6) as ex:
        shaped = [c for c in ex.map(shape, picks[: n * 3]) if c and MIN_PARAMS <= c["shape"].total_params <= MAX_PARAMS
                  and c["shape"].arch not in NOT_CHAT and c["shape"].attn_layers]
        if len(fails) > 0.2 * min(len(picks), n * 3):   # rate-limited or offline: a short list would replace a good one
            raise RuntimeError(f"{len(fails)} repo listings failed, e.g. {fails[0]}")
        for c, lin in zip(shaped, ex.map(lambda c: lineage(c["repo"]), shaped)):
            c["lineage"] = lin
    # one entry per packaged model (two quantizers of one model, or a repo without base tags, would list it twice)
    by_model: dict = {}
    for c in sorted(shaped, key=lambda c: -c["downloads"]):
        by_model.setdefault(_key(c["lineage"]["model"]), c)
    # a newer release of the same org on the same architecture replaces the older one (Ornith 1.0 -> 1.5)
    newest: dict = {}
    for c in sorted(by_model.values(), key=lambda c: c["released"], reverse=True):
        newest.setdefault((family(c["shape"]), c["lineage"]["model"].split("/")[0].lower()), c)
    shaped = list(newest.values())
    # per architecture: releases, then fine-tunes, then uncensored remixes, newest first in each - a busy base's
    # remixes must not push out its newest release
    shaped.sort(key=lambda c: c["released"], reverse=True)
    shaped.sort(key=lambda c: ("release", "fine-tune", "uncensored").index(c["lineage"]["kind"]))   # stable
    fam_n: dict = {}
    for c in shaped:
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


def load(max_age: int = TTL, n: int = 60) -> list[dict]:
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


UNCENSORED = re.compile(r"uncensor|abliterat|heretic|derestrict|unfilter|nsfw|decensor", re.I)


def info(repo: str) -> dict:
    """Hugging Face model info (tags, createdAt, cardData), cached for a week."""
    return _get(f"{API}/{repo}?expand[]=tags&expand[]=createdAt&expand[]=cardData", ttl=7 * TTL)


def lineage(repo: str) -> dict:
    """What a GGUF repo packages: {'model': the packaged model, 'kind': 'release' | 'fine-tune' | 'uncensored',
    'of': the base it was trained from when that is another org's model}. A lab's own post-training of its own base is a
    release; anyone's training on someone else's base is a fine-tune (Tiel on Ornith, MiMo's distill on Qwen)."""
    chain = base_chain(repo)
    model = chain[1] if len(chain) > 1 else re.sub(r"-gguf$", "", repo, flags=re.I)
    org = lambda r: r.split("/")[0].lower()
    of = next((b for b in chain[2:] if org(b) != org(model)), None)
    if UNCENSORED.search(repo) or UNCENSORED.search(model):
        kind = "uncensored"
    elif of:
        kind = "fine-tune"
    else:
        kind = "release"
    return {"model": model, "kind": kind, "of": of}


def key_of(repo: str) -> str:
    """The same one-entry-per-model key discover() groups by, for a repo that came from somewhere else (a recipe)."""
    try:
        return _key(_base(info(repo)))
    except Exception:
        return _key(re.sub(r"-gguf$", "", repo, flags=re.I))


def released(repo: str) -> str | None:
    try:
        return (info(repo).get("createdAt") or "")[:10] or None
    except Exception:
        return None
