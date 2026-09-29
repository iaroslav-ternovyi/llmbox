"""Reference runs of frontier models through headless Claude Code (the user's Claude subscription, no API key).

The item is run under the same conditions as a local model: the item's own system prompt replaces Claude Code's, all
built-in tools are disabled, and the item's tools (simulated CRM, agentic workspace tools) are exposed through the MCP
bridge, which forwards every call to this process - so side effects land in the same world / workspace and the same
grader scores them. Only the model differs.

Two auth modes:
- API key (preferred - does not spend the user's subscription limits): the key lives in ~/.llmbox/anthropic.key
  (chmod 600, written by the user); it is passed only into the claude process env and Claude Code runs with --bare
  (no CLAUDE.md, hooks, plugins, keychain).
- Subscription: a dedicated, empty config dir (~/.llmbox/claude-config: no CLAUDE.md, hooks, plugins or MCP servers of
  the user). One-time login: `CLAUDE_CONFIG_DIR=~/.llmbox/claude-config claude auth login`.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import shutil
import re
import subprocess
import sys
import tempfile
import threading
import time

from .suite.common import Item

CONFIG_DIR = pathlib.Path.home() / ".llmbox" / "claude-config"
KEY_FILE = pathlib.Path.home() / ".llmbox" / "anthropic.key"


def _api_key() -> str | None:
    try:
        k = KEY_FILE.read_text().strip()
        return k or None
    except OSError:
        return None
PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Callback(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, tool_impl, calls: list):
        self.tool_impl, self.calls, self.lock = tool_impl, calls, threading.Lock()

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(h):  # noqa: N805
                body = json.loads(h.rfile.read(int(h.headers["Content-Length"])))
                with self.lock:   # one world, one call at a time (parallel tool calls are serialized, like the local client)
                    try:
                        res = self.tool_impl(body["name"], body.get("args") or {})
                    except Exception as e:
                        res = {"error": str(e)}
                    self.calls.append({"name": body["name"], "args": body.get("args"), "result": res})
                out = json.dumps({"result": res}, ensure_ascii=False, default=str).encode()
                h.send_response(200)
                h.send_header("Content-Type", "application/json")
                h.end_headers()
                h.wfile.write(out)

            def log_message(h, *a):  # noqa: N805
                pass
        super().__init__(("127.0.0.1", 0), H)


def available() -> tuple[bool, str]:
    if not shutil.which("claude"):
        return False, "claude CLI not found"
    if _api_key():
        return True, "api key"
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(CONFIG_DIR))
    out = subprocess.run(["claude", "auth", "status"], env=env, capture_output=True, text=True, timeout=60).stdout
    try:
        ok = json.loads(out).get("loggedIn")
    except ValueError:
        ok = False
    return bool(ok), ("subscription" if ok else f"no auth: put an API key into {KEY_FILE} (chmod 600) or run  "
                                                f"CLAUDE_CONFIG_DIR={CONFIG_DIR} claude auth login")


class UsageLimit(RuntimeError):
    """The subscription's session or weekly limit: the reply is Claude Code's notice, not an answer. A run must stop
    here (and be resumed after the reset), never score it: on 2026-09-29 a night of such notices recorded as answers
    turned a Haiku calibration run into 32.8."""


LIMIT_RE = re.compile(r"(?i)hit your (session|weekly|usage|opus) limit|usage limit reached|limit reached.{0,40}resets|rate[_ ]limit")


def run_item(model: str, it: Item, effort: str | None = None, deadline_s: float = 1800) -> dict:
    """Runs one item through headless Claude Code; returns the same shape as client.run_chat."""
    t0 = time.time()
    calls: list = []
    work = pathlib.Path(tempfile.mkdtemp(prefix="llmbox-cc-"))
    system = "\n\n".join(m["content"] for m in it.messages if m["role"] == "system") or "You are a helpful assistant."
    users = [m["content"] for m in it.messages if m["role"] == "user"] + list(it.meta.get("followups") or [])
    cmd = ["claude", "-p", "--model", model, "--tools", "", "--system-prompt", system, "--input-format", "stream-json",
           "--output-format", "stream-json", "--verbose", "--no-session-persistence", "--setting-sources", ""]
    if effort:
        cmd += ["--effort", effort]
    srv = None
    if it.tools and it.tool_impl:
        srv = _Callback(it.tool_impl, calls)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        (work / "tools.json").write_text(json.dumps(it.tools))
        mcp = {"mcpServers": {"llmbox": {"command": sys.executable, "args": ["-m", "llmbox.mcp_bridge", str(work / "tools.json"),
                                                                           f"http://127.0.0.1:{srv.server_address[1]}/call"],
                                         "env": {"PYTHONPATH": str(PKG_ROOT)}}}}
        (work / "mcp.json").write_text(json.dumps(mcp))
        cmd += ["--mcp-config", str(work / "mcp.json"), "--strict-mcp-config", "--allowedTools", "mcp__llmbox"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC", "MCP_"))}
    key = _api_key()
    if key:
        cmd.insert(2, "--bare")
        env["ANTHROPIC_API_KEY"] = key
    else:
        env["CLAUDE_CONFIG_DIR"] = str(CONFIG_DIR)
    proc = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    timer = threading.Timer(deadline_s, proc.kill)
    timer.start()
    final, finish, steps, results, seen_msgs = "", None, 0, [], set()
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0}
    max_steps = it.meta.get("max_steps", 16)   # whole conversation, like the local client (sessions budget all turns)
    try:
        turn = 0
        proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": users[0]}}) + "\n")
        proc.stdin.flush()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                # one event per content block (text, each tool_use): count distinct messages, i.e. model turns
                mid = (ev.get("message") or {}).get("id")
                if mid not in seen_msgs:
                    seen_msgs.add(mid)
                    steps += 1
                if steps > max_steps:
                    finish = "max_steps"
                    proc.kill()
                    break
            elif ev.get("type") == "result":
                results.append(ev)
                final = ev.get("result") or ""
                finish = ev.get("subtype")
                u = ev.get("usage") or {}
                usage["prompt_tokens"] += (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                usage["cache_read_tokens"] += u.get("cache_read_input_tokens") or 0
                usage["completion_tokens"] += u.get("output_tokens") or 0
                turn += 1
                if turn < len(users):
                    proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": users[turn]}}) + "\n")
                    proc.stdin.flush()
                else:
                    proc.stdin.close()
        proc.wait(timeout=60)
    except (BrokenPipeError, subprocess.TimeoutExpired):
        proc.kill()
    finally:
        timer.cancel()
        if srv:
            srv.shutdown()
    if not timer.is_alive() and time.time() - t0 >= deadline_s:
        finish = "deadline"
    err = proc.stderr.read()[-400:] if proc.stderr else ""
    shutil.rmtree(work, ignore_errors=True)
    notices = [final, err] + [r.get("result") or "" for r in results if r.get("is_error")]
    if any(LIMIT_RE.search(x or "") for x in notices) and len(final) < 400:
        raise UsageLimit(f"claude-code: {next(x for x in notices if LIMIT_RE.search(x or ''))[:200]}")
    if final.startswith(("Failed to authenticate", "Invalid API key", "API Error")) or (not results and err):
        raise RuntimeError(f"claude-code: {final or err}")
    return {"final": final, "finish_reason": finish, "messages": [], "tool_calls": calls, "timings": [], "usage": usage,
            "seconds": round(time.time() - t0, 2), "steps": steps,
            "cost_usd_equiv": round(sum(r.get("total_cost_usd") or 0 for r in results), 4)}
