"""Hugging Face Hub access (stdlib only): list GGUF files of a repo, group split files, read their headers."""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import gguf

HF = "https://huggingface.co"
_SPLIT = re.compile(r"^(?P<base>.+)-(?P<i>\d{5})-of-(?P<n>\d{5})\.gguf$")
# quant label as it usually appears in file names (…-UD-Q4_K_XL.gguf, …Q4_K_M.gguf, …IQ3_XXS…)
_QUANT = re.compile(r"(UD-)?(I?Q\d(?:_[0-9A-Z]+)*|BF16|F16|F32|MXFP4(?:_MOE)?|TQ\d_\d|PQ\d_\d|PTQ\d_\d)", re.I)


def _token() -> str | None:
    tok = os.environ.get("HF_TOKEN")
    if tok:
        return tok
    path = os.path.expanduser("~/.cache/huggingface/token")
    if os.path.exists(path):
        return open(path).read().strip() or None
    return None


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "llmbox"})
    tok = _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


@dataclass
class GGUFFile:
    """One logical model file; split GGUFs (…-00001-of-00003.gguf) are one entry with several parts."""
    repo: str
    name: str                      # display name (first part for splits, without the split suffix)
    parts: list[str] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)
    sha256: list[str | None] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(self.sizes)

    @property
    def quant(self) -> str:
        m = None
        for m in _QUANT.finditer(os.path.basename(self.name)):
            pass
        return m.group(0).upper() if m else "?"

    def url(self, part: str) -> str:
        return f"{HF}/{self.repo}/resolve/main/{urllib.parse.quote(part)}"


def _tree(repo: str, ttl: int = 0) -> list:
    """The repo's file listing; with ttl > 0 cached on disk (~/.llmbox/hf/tree/) - a site build lists ~150 repos and
    Hugging Face answers 429 to a second build within minutes."""
    url = f"{HF}/api/models/{repo}/tree/main?recursive=true"
    if not ttl:
        return _get_json(url)
    import time as _t
    from .hosts import HOME
    cp = os.path.join(HOME, "hf", "tree", repo.replace("/", "__") + ".json")
    if os.path.exists(cp) and _t.time() - os.path.getmtime(cp) < ttl:
        return json.load(open(cp))
    d = _get_json(url)
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    json.dump(d, open(cp, "w"))
    return d


def list_gguf(repo: str, ttl: int = 0) -> list[GGUFFile]:
    entries = _tree(repo, ttl)
    files: dict[str, GGUFFile] = {}
    for e in entries:
        path = e.get("path", "")
        if e.get("type") != "file" or not path.endswith(".gguf"):
            continue
        base = os.path.basename(path)
        if base.startswith("mmproj") or "mtp-layer" in base:
            continue  # vision projectors / standalone draft layers are not models
        m = _SPLIT.match(path)
        key = m.group("base") if m else path
        f = files.setdefault(key, GGUFFile(repo=repo, name=key if m else path))
        f.parts.append(path)
        f.sizes.append(int(e.get("size", 0)))
        f.sha256.append((e.get("lfs") or {}).get("oid"))
    for f in files.values():  # keep split parts in order
        order = sorted(range(len(f.parts)), key=lambda i: f.parts[i])
        f.parts = [f.parts[i] for i in order]
        f.sizes = [f.sizes[i] for i in order]
        f.sha256 = [f.sha256[i] for i in order]
    return sorted(files.values(), key=lambda f: f.size)


def read_headers(f: GGUFFile, keep: frozenset = frozenset()) -> list[gguf.GGUFHeader]:
    """Header of every part (tensors of split models are spread over the parts). `keep`: big arrays to read in full
    (the vocabulary lives in the first part)."""
    tok = _token()
    return [gguf.read_header(gguf.http_fetcher(f.url(p), tok), size, keep=keep if i == 0 else frozenset())
            for i, (p, size) in enumerate(zip(f.parts, f.sizes))]


def model_card(repo: str) -> str:
    """README text of the repo (vendor settings are extracted from it by a human or an LLM, not parsed)."""
    req = urllib.request.Request(f"{HF}/{repo}/raw/main/README.md", headers={"User-Agent": "llmbox"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return ""
