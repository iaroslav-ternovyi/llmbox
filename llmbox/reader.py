"""The fixed reader of the explanation block (suite/explain.py): a small open model that knows nothing about the
made-up system and has only the tested model's explanation to go on. It is not a judge - it never rates anything; it
answers quiz questions with exact answers, and those are graded by code. What it measures is the explanation's use to
someone who has to act on it (teacher-student simulatability: Pruthi et al., TACL 2022).

The reader is fixed across all runs: gpt-oss-20b (reasoning medium, greedy), served as the llama-swap entry
`llmbox-reader` next to the tested model, with its own pinned recipe. A reader must follow what it reads even where it
contradicts habit: Phi-4-mini (the first choice: small, MIT, a family the ranking does not test) answered from habit
instead - the reference explanation got 0.08-0.3 of the cases right - while gpt-oss-20b gets 0.93-0.97 with it and
0.0-0.13 without any explanation (2026-09-27). gpt-oss-20b is itself scored, so only that one model shares its idioms. Its answers are cached
by prompt, so a re-grade or a resumed run does not ask again and gets the same answer.
"""
from __future__ import annotations

import hashlib
import json
import os

from . import client

MODEL = os.environ.get("LLMBOX_READER_MODEL", "llmbox-reader")
# what the entry serves; part of the cache key, so a different reader never reuses another reader's answers
READER_ID = "gpt-oss-20b UD-Q4_K_XL, reasoning medium, greedy, loop detector 24"
CACHE = os.path.join(os.path.expanduser("~"), ".llmbox", "reader-cache.jsonl")
URL: str | None = os.environ.get("LLMBOX_READER_URL")   # set by bench from the run's endpoint when not given
MAX_TOKENS = 16000   # the reader reasons; the hardest access cases need ~5k tokens

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


TRACES = os.path.join(os.path.expanduser("~"), ".llmbox", "reader-traces")


def _trace(key: str, res: dict) -> None:
    """The reader's reasoning for every answer, so a long or empty one can be audited (loop or honest deliberation)."""
    import gzip
    try:
        os.makedirs(TRACES, exist_ok=True)
        think = [m.get("reasoning_content") for m in res.get("messages") or [] if m.get("role") == "assistant" and m.get("reasoning_content")]
        with gzip.open(os.path.join(TRACES, f"{key[:16]}.json.gz"), "wt", encoding="utf-8") as f:
            json.dump({"reader": READER_ID, "finish": res.get("finish_reason"), "usage": res.get("usage"), "final": res.get("final"),
                       "reasoning": think}, f, ensure_ascii=False)
    except OSError:
        pass


def ask(prompt: str) -> str:
    key = hashlib.sha256(f"{READER_ID}\n{prompt}".encode()).hexdigest()
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
    _trace(key, res)
    if not (text or "").strip() or res.get("finish_reason") == "length":
        # the reader ran out of tokens before answering: a failure of the reader, not a wrong answer of the explanation
        # (2026-09-27: two good Tiel explanations scored 0 this way). Not cached; the row stays pending for a regrade.
        raise RuntimeError(f"reader gave no answer (finish {res.get('finish_reason')}, {len(text or '')} chars)")
    c[key] = text
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "reader": READER_ID, "text": text}, ensure_ascii=False) + "\n")
    return text
