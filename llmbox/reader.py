"""The fixed reader of the explanation block (suite/explain.py): a small open model that knows nothing about the
made-up system and has only the tested model's explanation to go on. It is not a judge - it never rates anything; it
answers quiz questions with exact answers, and those are graded by code. What it measures is the explanation's use to
someone who has to act on it (teacher-student simulatability: Pruthi et al., TACL 2022).

The reader is fixed across all runs (Phi-4-mini-instruct Q8_0: MIT, ungated, 3.8B, not a family the ranking tests),
greedy (temperature 0), served as the llama-swap entry `llmbox-reader` next to the tested model. Its answers are cached
by prompt, so a re-grade or a resumed run does not ask again and gets the same answer.
"""
from __future__ import annotations

import hashlib
import json
import os

from . import client

MODEL = os.environ.get("LLMBOX_READER_MODEL", "llmbox-reader")
CACHE = os.path.join(os.path.expanduser("~"), ".llmbox", "reader-cache.jsonl")
URL: str | None = os.environ.get("LLMBOX_READER_URL")   # set by bench from the run's endpoint when not given
MAX_TOKENS = 3000

_cache: dict | None = None


def configure(base_url: str | None) -> None:
    """The reader lives on the same OpenAI-compatible endpoint as the tested model unless LLMBOX_READER_URL says otherwise
    (a frontier run through Claude Code has no local endpoint: then the env var must point at the box)."""
    global URL
    if not os.environ.get("LLMBOX_READER_URL") and base_url and base_url.startswith("http"):
        URL = base_url


def default_url() -> str | None:
    """The box's llama-swap as seen from here (the host profile knows the ssh host and the proxy port)."""
    try:
        from . import hosts
        prof = hosts.load("box")
        port = ((prof["hw"].get("llama_swap") or {}).get("url") or "http://localhost:8080").rsplit(":", 1)[1]
        return f"http://{prof['ssh'].split('@')[-1]}:{port}"
    except Exception:
        return None


def _load() -> dict:
    global _cache
    if _cache is None:
        _cache = {}
        if os.path.exists(CACHE):
            for line in open(CACHE, encoding="utf-8"):
                try:
                    d = json.loads(line)
                    _cache[d["key"]] = d["text"]
                except (ValueError, KeyError):
                    pass
    return _cache


def ask(prompt: str) -> str:
    key = hashlib.sha256(f"{MODEL}\n{prompt}".encode()).hexdigest()
    c = _load()
    if key in c:
        return c[key]
    if not URL:
        configure(default_url())
    if not URL:
        raise RuntimeError("no reader endpoint: run through `llmbox bench` against the box, or set LLMBOX_READER_URL")
    res = client.run_chat(URL, MODEL, [{"role": "user", "content": prompt}], max_tokens=MAX_TOKENS, max_steps=1, timeout=900,
                          extra={"temperature": 0, "top_k": 1, "seed": 1})
    text = res["final"]
    c[key] = text
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "model": MODEL, "text": text}, ensure_ascii=False) + "\n")
    return text
