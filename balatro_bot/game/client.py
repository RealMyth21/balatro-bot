"""Thin JSON-RPC client for the balatrobot API with per-call timing."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx


class RPCError(Exception):
    def __init__(self, method: str, code: int, name: str, message: str):
        super().__init__(f"{method}: {name} ({code}) {message}")
        self.method, self.code, self.name, self.message = method, code, name, message


@dataclass
class CallTiming:
    method: str
    seconds: float


@dataclass
class BalatroClient:
    port: int = 12346
    host: str = "127.0.0.1"
    timeout: float = 120.0
    timings: list[CallTiming] = field(default_factory=list)

    def __post_init__(self):
        self._http = httpx.Client(timeout=self.timeout)
        self._id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": self._id}
        if params is not None:
            body["params"] = params
        t0 = time.perf_counter()
        r = self._http.post(f"http://{self.host}:{self.port}", json=body)
        self.timings.append(CallTiming(method, time.perf_counter() - t0))
        data = r.json()
        if "error" in data:
            e = data["error"]
            raise RPCError(method, e.get("code", 0), e.get("data", {}).get("name", "?"), e.get("message", ""))
        return data["result"]

    # convenience wrappers -------------------------------------------------
    def gamestate(self) -> dict:
        return self.call("gamestate")

    def menu(self) -> dict:
        return self.call("menu")

    def start(self, deck: str, stake: str, seed: str | None = None) -> dict:
        p = {"deck": deck, "stake": stake}
        if seed:
            p["seed"] = seed
        return self.call("start", p)

    def close(self) -> None:
        self._http.close()
