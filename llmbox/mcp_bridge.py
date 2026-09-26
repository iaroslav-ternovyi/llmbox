"""Minimal MCP stdio server that exposes a suite item's tools to an external agent (e.g. headless Claude Code).

`python3 -m llmbox.mcp_bridge <tools.json> <callback-url>`: tools.json holds the item's OpenAI-style tool list; every
tools/call is forwarded as POST {name, args} to the callback served by the llmbox process that owns the item's world, so
a frontier model acts through exactly the same tools (and side-effect log) as a local model. Stdlib only.
"""
from __future__ import annotations

import json
import sys
import urllib.request


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    tools_path, url = sys.argv[1], sys.argv[2]
    with open(tools_path) as f:
        tools = json.load(f)
    listed = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
               "inputSchema": t["function"].get("parameters") or {"type": "object", "properties": {}}} for t in tools]
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid, method = req.get("id"), req.get("method")
        if rid is None:          # notification (e.g. notifications/initialized)
            continue
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": (req.get("params") or {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "llmbox", "version": "1"}}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": listed}})
        elif method == "tools/call":
            p = req.get("params") or {}
            body = json.dumps({"name": p.get("name"), "args": p.get("arguments") or {}}).encode()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}),
                                            timeout=600) as r:
                    result = json.load(r)["result"]
                text, err = (result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)), False
            except Exception as e:  # the bridge must never crash the agent session
                text, err = json.dumps({"error": f"bridge: {e}"}), True
            _send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}], "isError": err}})
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    main()
