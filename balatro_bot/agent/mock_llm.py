"""A stand-in for an OpenAI-compatible chat API (e.g. Moonshot), for testing the LLM pipeline without
an API key: prompts, tool calls, logging, parallel games, retries and cost accounting.

It answers every chat completion with a valid tool call chosen from the tools offered in the request
(a deliberately simple policy: play one card, leave shops, skip packs). Token usage is estimated from
text length so cost accounting can be exercised. Optional fault injection exercises error paths.

  python -m balatro_bot.agent.mock_llm --port 18080
  # then point the client at base_url=http://127.0.0.1:18080/v1, any api key, any model name
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# preferred action per tool name, in priority order (first offered tool wins)
PREFERENCES: list[tuple[str, dict]] = [
    ("play", {"cards": [0]}),
    ("next_round", {}),
    ("pack", {"skip": True}),
    ("cash_out", {}),
    ("select", {}),
    ("play_hand_or_discard", {"action": "play_hand", "cards": [0]}),
]


ANALYSIS_TOOLS = ("score_plays", "estimate_clear_chance")


def choose_tool_call(tools: list[dict], messages: list[dict] | None = None) -> tuple[str, dict]:
    names = [t.get("function", {}).get("name") for t in tools]
    # with analysis tools offered, call one first (once per decision) to exercise the tool loop
    already_used = any(m.get("role") == "tool" for m in (messages or []) if isinstance(m, dict))
    if not already_used:
        for name in ANALYSIS_TOOLS:
            if name in names:
                return name, ({"top": 3} if name == "score_plays" else {})
    for name, args in PREFERENCES:
        if name in names:
            fn = next(t["function"] for t in tools if t.get("function", {}).get("name") == name)
            props = (fn.get("parameters") or {}).get("properties") or {}
            if "reasoning" in props:
                args = {**args, "reasoning": "mock"}
            return name, args
    if names:
        return names[0], {}
    return "", {}


def estimate_tokens(obj) -> int:
    return max(1, len(json.dumps(obj)) // 4)


class MockState:
    def __init__(self, latency: float = 0.0, error_rate: float = 0.0, seed: int = 0):
        self.latency, self.error_rate = latency, error_rate
        self.rng = random.Random(seed)
        self.calls = 0
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def log_message(self, *args) -> None:
        pass

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802  /v1/models
        self._send(200, {"object": "list", "data": [{"id": "mock-model", "object": "model"}]})

    def do_POST(self) -> None:  # noqa: N802
        state: MockState = self.server.mock  # type: ignore[attr-defined]
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": f"unknown path {self.path}"}})
            return
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with state.lock:
            state.calls += 1
            fault = state.rng.random() < state.error_rate
            kind = state.rng.choice(["no_tool", "bad_json", "bad_args"]) if fault else None
        if state.latency:
            time.sleep(state.latency)
        name, args = choose_tool_call(req.get("tools") or [], req.get("messages"))
        message: dict = {"role": "assistant", "content": None}
        if kind == "no_tool" or not name:
            message["content"] = "I think I should play my best hand."
        else:
            arguments = "{not json" if kind == "bad_json" else json.dumps(
                {"cards": [99]} if kind == "bad_args" and name == "play" else args)
            message["tool_calls"] = [{"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                                      "function": {"name": name, "arguments": arguments}}]
        prompt_tokens = estimate_tokens(req.get("messages")) + estimate_tokens(req.get("tools"))
        completion_tokens = 40
        self._send(200, {
            "id": f"chatcmpl-{uuid.uuid4().hex[:16]}", "object": "chat.completion", "created": int(time.time()),
            "model": req.get("model", "mock-model"),
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens},
        })


def start(port: int = 0, latency: float = 0.0, error_rate: float = 0.0) -> tuple[ThreadingHTTPServer, threading.Thread]:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    httpd.mock = MockState(latency, error_rate)  # type: ignore[attr-defined]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--latency", type=float, default=0.0, help="seconds per call (simulate model latency)")
    ap.add_argument("--error-rate", type=float, default=0.0, help="share of malformed/invalid replies")
    args = ap.parse_args()
    httpd, t = start(args.port, args.latency, args.error_rate)
    print(f"mock LLM: OpenAI-compatible API on http://127.0.0.1:{httpd.server_address[1]}/v1", flush=True)
    try:
        t.join()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
