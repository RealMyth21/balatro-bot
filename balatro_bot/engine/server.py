"""Serve the exact engine over balatrobot's HTTP JSON-RPC 2.0 API (one game per port).

Any balatrobot client can then drive it: BalatroLLM, jackdaw's LiveBackend, our BalatroClient.

  python -m balatro_bot.engine.server --port 13000

Requests are handled one at a time (one game per server, like balatrobot). Unknown request fields
(e.g. BalatroLLM's `reasoning` argument) are ignored by balatrobot's validator, as in the real game.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from balatro_bot.engine.client import EngineClient
from balatro_bot.game.client import RPCError

ERROR_CODES = {"INTERNAL_ERROR": -32000, "BAD_REQUEST": -32001, "INVALID_STATE": -32002, "NOT_ALLOWED": -32003}


class EngineHTTPServer(HTTPServer):
    """Binds at construction; requests wait in the listen queue until the engine is attached and
    serve_forever runs."""

    def __init__(self, addr, engine: EngineClient | None = None):
        super().__init__(addr, _Handler)
        self.engine = engine
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    server: EngineHTTPServer

    def log_message(self, *args) -> None:  # quiet
        pass

    def _reply(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._reply({"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": "Parse error", "data": {"name": "BAD_REQUEST"}}})
            return
        rid = req.get("id")
        method = req.get("method")
        if not isinstance(method, str):
            self._reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32001, "message": "Request missing 'method'",
                                                                 "data": {"name": "BAD_REQUEST"}}})
            return
        with self.server.lock:
            try:
                result = self.server.engine.call(method, req.get("params"))
                self._reply({"jsonrpc": "2.0", "id": rid, "result": result})
            except RPCError as e:
                self._reply({"jsonrpc": "2.0", "id": rid, "error": {
                    "code": ERROR_CODES.get(e.name, e.code or -32000), "message": e.message, "data": {"name": e.name}}})
            except Exception as e:  # noqa: BLE001  engine died etc.
                self._reply({"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32000, "message": f"{type(e).__name__}: {e}", "data": {"name": "INTERNAL_ERROR"}}})


def serve(host: str = "127.0.0.1", port: int = 13000, ready: threading.Event | None = None) -> None:
    """Bind first (port 0 = any free port) and announce the port, then boot the engine and serve."""
    with EngineHTTPServer((host, port)) as httpd:
        print(f"engine serve: balatrobot API on http://{host}:{httpd.server_address[1]}", flush=True)
        with EngineClient() as eng:
            httpd.engine = eng
            if ready:
                ready.set()
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=13000, help="0 picks a free port (printed on startup)")
    args = ap.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
