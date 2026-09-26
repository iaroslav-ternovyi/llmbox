"""Fast reasoning-loop test: replay the exact contexts where models looped before, instead of running agents for hours.

extract: scan agent transcripts (Claude Code JSONL, main agent + subagents) for replies that ran past a token threshold,
         and save the conversation right BEFORE that reply as a replay case.
replay:  send each case to an Anthropic-compatible endpoint (our agent-proxy) with the harness's real system prompt and
         tools (see capture.py), N samples each, capped at `cap` output tokens. A sample that hits the cap = loop.
         Streams the answer, so each sample also yields its thinking text for the verbatim/marker analysis.
"""
from __future__ import annotations

import glob
import json
import os
import re
import time
import urllib.request
import zlib
from dataclasses import dataclass, field

MARKERS = re.compile(r"\b(Wait|Hmm|Alternatively|Actually)\b")


@dataclass
class Case:
    id: str
    source_model: str
    task: str
    original_tokens: int
    messages: list = field(default_factory=list)
    subagent: bool = False


def _load_rows(path: str) -> list[dict]:
    rows = []
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") in ("user", "assistant") and isinstance(r.get("message"), dict):
            rows.append(r)
    return rows


def _to_messages(rows: list[dict]) -> list[dict]:
    """Transcript rows -> Anthropic messages. One assistant message may be split over several rows (one per block)."""
    msgs: list[dict] = []
    last_id = None
    for r in rows:
        m = r["message"]
        role = m.get("role") or r["type"]
        content = m.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        content = [dict(b) for b in (content or []) if isinstance(b, dict)]
        for b in content:
            b.pop("signature", None) if b.get("type") == "thinking" else None
            b.pop("cache_control", None)
        mid = m.get("id") if role == "assistant" else None
        if msgs and msgs[-1]["role"] == role and (role == "user" or (mid and mid == last_id)):
            msgs[-1]["content"].extend(content)
        else:
            msgs.append({"role": role, "content": content})
        last_id = mid
    return msgs


def extract(transcripts_root: str, min_tokens: int = 15000, models: list[str] | None = None) -> list[Case]:
    cases = []
    for d in sorted(glob.glob(os.path.join(transcripts_root, "*agent-bench-runs-results-*"))):
        m = re.match(r".*results-(screen|main)-(.+?)-([rsn]\d-[^/]+?)-r\d+-workspace$", d)
        if not m or (models and m.group(2) not in models):
            continue
        model, task = m.group(2), m.group(3)
        for f in glob.glob(d + "/*.jsonl") + glob.glob(d + "/*/subagents/*.jsonl"):
            rows = _load_rows(f)
            seen = set()
            for i, r in enumerate(rows):
                if r["type"] != "assistant":
                    continue
                usage = r["message"].get("usage") or {}
                mid = r["message"].get("id")
                if (usage.get("output_tokens") or 0) < min_tokens or mid in seen:
                    continue
                seen.add(mid)
                start = i
                while start > 0 and rows[start - 1]["type"] == "assistant" and rows[start - 1]["message"].get("id") == mid:
                    start -= 1
                msgs = _to_messages(rows[:start])
                if not msgs or msgs[-1]["role"] != "user":
                    continue
                cid = f"{model}.{task}.{os.path.basename(f)[:12]}.{i}"
                cases.append(Case(cid, model, task, usage["output_tokens"], msgs, subagent="/subagents/" in f))
    return cases


def save_cases(cases: list[Case], path: str) -> None:
    json.dump([c.__dict__ for c in cases], open(path, "w"))


def load_cases(path: str) -> list[Case]:
    return [Case(**d) for d in json.load(open(path))]


def _analyze(thinking: str) -> dict:
    tail = thinking[-6000:]
    return {"thinking_chars": len(thinking),
            "tail_compress": round(len(zlib.compress(tail.encode())) / max(1, len(tail)), 3) if tail else None,
            "markers_per_1k": round(1000 * len(MARKERS.findall(thinking)) / max(1, len(thinking)), 2)}


def replay_one(endpoint: str, model: str, req_template: dict, case: Case, cap: int, timeout: int = 1800) -> dict:
    body = {k: v for k, v in req_template.items() if k in ("system", "tools", "thinking", "output_config", "metadata")}
    body.update(model=model, messages=case.messages, max_tokens=cap, stream=True)
    req = urllib.request.Request(endpoint.rstrip("/") + "/v1/messages", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json", "x-api-key": "llmbox",
                                          "anthropic-version": "2023-06-01"})
    t0 = time.time()
    thinking, text, stop, out_tokens, first = [], [], None, 0, None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:])
            except ValueError:
                continue
            t = ev.get("type")
            if t == "content_block_delta":
                first = first or time.time()
                d = ev.get("delta", {})
                if d.get("type") == "thinking_delta":
                    thinking.append(d.get("thinking", ""))
                elif d.get("type") == "text_delta":
                    text.append(d.get("text", ""))
                elif d.get("type") == "input_json_delta":
                    text.append(d.get("partial_json", ""))
            elif t == "message_delta":
                stop = ev.get("delta", {}).get("stop_reason") or stop
                out_tokens = (ev.get("usage") or {}).get("output_tokens") or out_tokens
    th = "".join(thinking)
    res = {"case": case.id, "stop_reason": stop, "output_tokens": out_tokens, "seconds": round(time.time() - t0, 1),
           "ttft_s": round(first - t0, 1) if first else None, "looped": stop == "max_tokens" or out_tokens >= cap - 16}
    res.update(_analyze(th))
    return res


def summarize(results: list[dict]) -> dict:
    n = len(results)
    loops = sum(1 for r in results if r["looped"])
    toks = sorted(r["output_tokens"] for r in results)
    return {"samples": n, "loops": loops, "loop_rate": round(loops / n, 3) if n else None,
            "median_tokens": toks[n // 2] if n else None, "max_tokens": toks[-1] if n else None,
            "markers_per_1k": round(sum(r["markers_per_1k"] for r in results) / n, 2) if n else None,
            "minutes": round(sum(r["seconds"] for r in results) / 60, 1)}


def pick(cases: list[Case], limit: int) -> list[Case]:
    """Diverse, cheap subset: one case per task (round-robin), shortest contexts first."""
    by_task: dict[str, list[Case]] = {}
    for c in sorted(cases, key=lambda c: len(json.dumps(c.messages))):
        by_task.setdefault(c.task, []).append(c)
    out: list[Case] = []
    while len(out) < limit and any(by_task.values()):
        for t in sorted(by_task, key=lambda t: len(json.dumps(by_task[t][0].messages)) if by_task[t] else 1 << 60):
            if by_task[t] and len(out) < limit:
                out.append(by_task[t].pop(0))
    return out
