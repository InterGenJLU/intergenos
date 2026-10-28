# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A loopback model double with faults selected for each completion request.

The chat client uses POST /v1/chat/completions, with OpenAI-shaped SSE
``data:`` records and a final ``[DONE]``. The manager probes GET /health and
/props. Those are the supported model endpoints; no embedding engine is
started or simulated. POST /_control replaces the pending script atomically:
``{"script": [{"latency_ms": 200000, "fault": "empty"}, {}]}``.
GET /_control reports requests actually received and their completion times.
The optional FAKE_MODEL_SCRIPT environment variable has the same list shape.

Faults are ``none``, ``empty``, ``truncated_stream`` and ``malformed_json``.
Latency is applied before the response headers. Exhausting a script gives
normal completions; health and control requests never consume script entries.
Run with ``python -m intergen.tests.fake_model_server --port 0``; the first
stdout line names the bound endpoint. All code and serving are stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CONTENT = "A quiet river runs between the hills."
FAULTS = {"none", "empty", "truncated_stream", "malformed_json"}


def _script(value):
    if not isinstance(value, list):
        raise ValueError("script must be a list")
    result = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) - {"latency_ms", "fault"}:
            raise ValueError("each entry accepts only latency_ms and fault")
        latency = entry.get("latency_ms", 0)
        if type(latency) is not int or not 0 <= latency <= 3_600_000:
            raise ValueError("latency_ms must be an integer from 0 to 3600000")
        fault = entry.get("fault", "none")
        if not isinstance(fault, str) or fault not in FAULTS:
            raise ValueError("unknown fault")
        result.append({"latency_ms": latency, "fault": fault})
    return result


class FakeModelServer:
    """Own the listening socket, handlers and their interruptible delays."""

    def __init__(self, script=None, *, port=0):
        self._script = _script(script if script is not None else
                               json.loads(os.environ.get("FAKE_MODEL_SCRIPT", "[]")))
        self._lock = threading.Lock()
        self._requests = []
        self._stop = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def _body(self, status, body, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status, value):
                self._body(status, json.dumps(value).encode())

            def do_GET(self):
                if self.path == "/health":
                    self._json(200, {"status": "ok"})
                elif self.path == "/props":
                    self._json(200, {
                        "chat_template_caps": {"supports_tools": True},
                        "modalities": {"vision": False},
                    })
                elif self.path == "/_control":
                    self._json(200, owner.snapshot())
                else:
                    self._json(404, {"error": "unsupported endpoint"})

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 1_048_576:
                        raise ValueError("body must be 1 to 1048576 bytes")
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError("body must be an object")
                    if self.path == "/_control":
                        script = _script(payload.get("script"))
                        with owner._lock:
                            owner._script = script
                        self._json(200, owner.snapshot())
                        return
                except (ValueError, UnicodeError) as exc:
                    self._json(400, {"error": str(exc)})
                    return
                if self.path != "/v1/chat/completions":
                    self._json(404, {"error": "unsupported endpoint"})
                    return
                if (not isinstance(payload.get("messages"), list)
                        or type(payload.get("stream", False)) is not bool):
                    self._json(400, {"error": "messages and stream have invalid types"})
                    return
                with owner._lock:
                    step = (owner._script.pop(0) if owner._script else
                            {"latency_ms": 0, "fault": "none"})
                    record = {"index": len(owner._requests), "path": self.path,
                              "script": step, "received_at": time.monotonic(),
                              "stream": payload.get("stream", False),
                              "messages": payload["messages"], "finished_at": None}
                    owner._requests.append(record)
                try:
                    if owner._stop.wait(step["latency_ms"] / 1000):
                        return
                    self._completion(payload, step["fault"])
                except (BrokenPipeError, ConnectionResetError):
                    with owner._lock:
                        record["client_disconnected"] = True
                finally:
                    with owner._lock:
                        record["finished_at"] = time.monotonic()

            def _completion(self, payload, fault):
                content = "" if fault == "empty" else CONTENT
                if not payload.get("stream", False):
                    if fault == "malformed_json":
                        self._body(200, b'{"choices":')
                    elif fault == "truncated_stream":
                        self._json(400, {"error": "truncated_stream requires stream=true"})
                    else:
                        self._json(200, {"choices": [{"index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if fault == "malformed_json":
                    self.wfile.write(b'data: {"choices":\n\n')
                    self.wfile.flush()
                    return
                chunk = {"choices": [{"index": 0, "delta": {"content": content},
                                      "finish_reason": None}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()
                if fault == "truncated_stream":
                    return
                final = {"choices": [{"index": 0, "delta": {},
                                      "finish_reason": "stop"}],
                         "timings": {"prompt_n": 1, "predicted_n": 1}}
                self.wfile.write(b"data: " + json.dumps(final).encode() +
                                 b"\n\ndata: [DONE]\n\n")
                self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self.endpoint = self.url + "/v1/chat/completions"
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.05})

    def snapshot(self):
        with self._lock:
            return json.loads(json.dumps({"pending": self._script,
                                         "requests": self._requests}))

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("fake server did not stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    with FakeModelServer(port=args.port) as server:
        print(json.dumps({"endpoint": server.endpoint}), flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
