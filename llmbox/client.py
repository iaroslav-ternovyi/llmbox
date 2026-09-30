"""OpenAI-compatible chat client with a tool-call loop. Works with llama.cpp llama-server, llama-swap, Ollama,
LM Studio, vLLM. Sampling is NOT set here: the served recipe's defaults are part of what is being measured."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class ChatError(RuntimeError):
    pass


def _post(url: str, body: dict, api_key: str | None, timeout: int) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise ChatError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}") from None


# end-of-thinking tags a server's reasoning parser can miss when the model writes them as plain text: the answer then
# stays inside reasoning_content and content comes back empty (K2-Horizon at reasoning_effort medium on ik_llama.cpp
# closes with </ifm|think_fast>; 3 of 9 answers on 2026-09-30)
END_TAGS = ("</ifm|think_faster>", "</ifm|think_fast>", "</ifm|think>", "</think>")


def recover_answer(content: str | None, reasoning: str | None) -> tuple[str, str, bool]:
    """(content, reasoning, recovered): when content is empty and the reasoning holds the model's own end-of-thinking
    tag, the text after the last such tag is the answer."""
    if (content or "").strip() or not reasoning:
        return content or "", reasoning or "", False
    i, tag = max((reasoning.rfind(t), t) for t in END_TAGS)
    ans = reasoning[i + len(tag):].strip() if i >= 0 else ""
    return (ans, reasoning[:i], True) if ans else (content or "", reasoning, False)


def run_chat(base_url: str, model: str, messages: list[dict], tools: list[dict] | None = None, tool_impl=None,
             max_tokens: int = 8192, max_steps: int = 16, api_key: str | None = None, timeout: int = 1800,
             extra: dict | None = None, deadline_s: float | None = None, followups: list[str] | None = None) -> dict:
    """Runs a conversation until the model answers without tool calls (or max_steps, or the wall-clock deadline:
    no new request starts after it, the item is graded on the state reached - finish_reason "deadline").
    Returns final text, full transcript, per-request timings and token usage."""
    msgs = [dict(m) for m in messages]
    pending = list(followups or [])   # later user turns of a multi-turn session, sent after each final answer
    calls, timings, usage = [], [], {"prompt_tokens": 0, "completion_tokens": 0}
    t0 = time.time()
    final, finish, recovered = "", None, 0
    for _step in range(max_steps):
        if deadline_s and time.time() - t0 > deadline_s:
            finish = "deadline"
            break
        body = {"model": model, "messages": msgs, "max_tokens": max_tokens, **(extra or {})}
        if tools:
            body["tools"] = tools
        r = None
        for attempt in range(3):   # a server error (e.g. llama.cpp cannot parse the model's tool-call format) is retried:
            try:                   # sampling can give a valid reply the next time
                r = _post(base_url.rstrip("/") + "/v1/chat/completions", body, api_key, timeout)
                break
            except ChatError as e:
                if not str(e).startswith(("HTTP 500", "HTTP 502", "HTTP 503")) or attempt == 2:
                    if _step:   # after some steps (e.g. a long agent session overflowing the context): stop here
                        r = None   # and let the task be graded on the state reached, like at a deadline
                        break
                    raise
        if r is None:
            finish = "server_error"
            break
        ch = r["choices"][0]
        msg = ch["message"]
        finish = ch.get("finish_reason")
        u = r.get("usage") or {}
        usage["prompt_tokens"] += u.get("prompt_tokens") or 0
        usage["completion_tokens"] += u.get("completion_tokens") or 0
        if r.get("timings"):
            t = r["timings"]
            timings.append({**{k: t.get(k) for k in ("prompt_n", "prompt_per_second", "predicted_n", "predicted_per_second",
                                                     "draft_n", "draft_n_accepted")},
                            "ctx": u.get("prompt_tokens")})   # context depth of this request (whole prompt, cached or not)
        tcs = msg.get("tool_calls") or []
        content, reasoning, rec = recover_answer(msg.get("content"), msg.get("reasoning_content"))
        recovered += rec
        msg = dict(msg, content=content, reasoning_content=reasoning)
        assistant = {"role": "assistant", "content": content}
        if reasoning:
            assistant["reasoning_content"] = reasoning
        if tcs:
            assistant["tool_calls"] = tcs
        msgs.append(assistant)
        if not tcs or tool_impl is None:
            final = msg.get("content") or ""
            if pending:
                msgs.append({"role": "user", "content": pending.pop(0)})
                continue
            break
        for tc in tcs:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"__invalid_json__": fn.get("arguments")}
            try:
                result = tool_impl(fn.get("name"), args)
            except Exception as e:  # tool errors are returned to the model, like a real API would
                result = {"error": str(e)}
            calls.append({"name": fn.get("name"), "args": args, "result": result})
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": json.dumps(result, ensure_ascii=False)})
    return {"final": final, "finish_reason": finish, "messages": msgs, "tool_calls": calls, "timings": timings,
            "usage": usage, "seconds": round(time.time() - t0, 2), "steps": len([m for m in msgs if m["role"] == "assistant"]),
            "recovered": recovered}
