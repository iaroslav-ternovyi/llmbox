"""Capture the exact request an agent harness sends (system prompt, tool schemas) without any model.

Runs a tiny local Anthropic-compatible endpoint that records request bodies and answers with a canned streamed
"OK", then runs the harness against it. Used to rebuild full requests for loop replays from transcripts
(transcripts store messages, not the system prompt or tool definitions).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SSE = [
    ("message_start", {"type": "message_start", "message": {"id": "msg_capture", "type": "message", "role": "assistant",
                                                             "model": "capture", "content": [], "stop_reason": None,
                                                             "usage": {"input_tokens": 1, "output_tokens": 0}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
    ("message_stop", {"type": "message_stop"}),
]


def capture_claude_code(claude_bin: str, env: dict, extra_args: list[str], out_path: str) -> dict:
    bodies: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_POST(self):
            n = int(self.headers.get("content-length", 0))
            try:
                bodies.append(json.loads(self.rfile.read(n)))
            except ValueError:
                pass
            if bodies and bodies[-1].get("stream"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for ev, data in _SSE:
                    self.wfile.write(f"event: {ev}\ndata: {json.dumps(data)}\n\n".encode())
            else:
                body = json.dumps({"id": "msg_capture", "type": "message", "role": "assistant", "model": "capture",
                                   "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
                                   "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    ws = tempfile.mkdtemp(prefix="llmbox-capture-")
    e = dict(env, ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}")
    try:
        subprocess.run([claude_bin, "-p", "Reply with OK.", *extra_args], cwd=ws, env=e, capture_output=True, text=True, timeout=180)
    finally:
        srv.shutdown()
    main = max(bodies, key=lambda b: len(b.get("tools") or []), default={})
    json.dump({"all": bodies, "main": main}, open(out_path, "w"))
    return main
