"""Python side of the headless engine: one LuaJIT process per game, JSON lines over pipes.

EngineClient has the same interface as balatro_bot.game.client.BalatroClient (call/start/menu/
gamestate, raises RPCError), so agents and runners work unchanged on either backend.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from balatro_bot.game.client import CallTiming, RPCError
from balatro_bot.paths import game_src

ROOT = Path(__file__).resolve().parents[2]


def engine_env(src: str | os.PathLike | None = None, debug: bool = False, profile: int = 2) -> dict[str, str]:
    """Environment for one engine process.

    By default the engine needs no save files: ENGINE_UNLOCK_ALL=1 applies the game's own Unlock All in
    memory (shown identical to a real Unlock All profile). To use one of your own profiles instead:
    `scripts/setup.py profile --slot N`, then ENGINE_UNLOCK_ALL=0 ENGINE_SAVE=runtime/engine_save.
    """
    env = os.environ.copy()
    sandbox = Path(os.environ.get("ENGINE_SAVE", ROOT / "runtime/engine_sandbox"))
    sandbox.mkdir(parents=True, exist_ok=True)
    src_dir = Path(src) if src else game_src()
    bb = Path(os.environ.get("BB_DIR", ROOT / "vendor/balatrobot"))
    if not (src_dir / "main.lua").exists():
        raise FileNotFoundError(f"Balatro's Lua source not found at {src_dir}; set BALATRO_SRC "
                                "(Windows/Linux: `python scripts/setup.py extract-src`)")
    if not (bb / "src/lua/endpoints/play.lua").exists():
        raise FileNotFoundError(f"balatrobot not found at {bb}; run `python scripts/setup.py balatrobot`")
    env.update(
        BALATRO_SRC=str(src_dir),
        ENGINE_SAVE=str(sandbox),
        ENGINE_UNLOCK_ALL=os.environ.get("ENGINE_UNLOCK_ALL", "1"),
        BB_DIR=str(bb),
        ENGINE_LUA=str(ROOT / "engine/lua"),
        BOT_PROFILE=str(profile),
        ENGINE_DEBUG="1" if debug else "0",
    )
    return env


def default_luajit() -> str:
    """The Lua runtime for the engine, in order of preference.

    1. $ENGINE_LUAJIT
    2. engine/host/bhost-game (macOS, `scripts/setup.py host`): linked against the LuaJIT bundled
       with the game, i.e. exactly the VM the real game runs on.
    3. runtime/luajit/bin/luajit (`scripts/setup.py luajit`): the game's LuaJIT version built from source.
    4. luajit on PATH. The engine refuses to run on a LuaJIT whose random numbers differ from the
       game's (newer releases do) unless ENGINE_ALLOW_ANY_LUAJIT=1.
    """
    if os.environ.get("ENGINE_LUAJIT"):
        return os.environ["ENGINE_LUAJIT"]
    for candidate in (ROOT / "engine/host/bhost-game", ROOT / "runtime/luajit/bin/luajit"):
        if candidate.exists():
            return str(candidate)
    return shutil.which("luajit") or "luajit"


@dataclass
class EngineClient:
    src: str | None = None
    debug: bool = False
    profile: int = 2
    luajit: str = field(default_factory=default_luajit)
    timings: list[CallTiming] = field(default_factory=list)
    total_ticks: int = 0

    def __post_init__(self):
        # stderr goes to an anonymous temp file (never a pipe that could fill up); shown on boot failure
        self._err = None if self.debug else tempfile.TemporaryFile(mode="w+")
        self._proc = subprocess.Popen(
            [self.luajit, str(ROOT / "engine/lua/boot.lua")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._err,
            env=engine_env(self.src, self.debug, self.profile),
            text=True,
            bufsize=1,
        )
        hello = self._proc.stdout.readline()
        if not hello:
            self._proc.wait(timeout=10)
            detail = ""
            if self._err is not None:
                self._err.seek(0)
                detail = self._err.read()[-2000:].strip()
            raise RuntimeError(f"engine failed to boot (runtime {self.luajit}): {detail or 'no error output'}")
        info = json.loads(hello)
        if not info.get("ready"):
            raise RuntimeError(f"unexpected engine hello: {info}")
        self.version = info.get("version")
        self._id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        req: dict[str, Any] = {"id": self._id, "method": method}
        if params is not None:
            req["params"] = params
        t0 = time.perf_counter()
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        self.timings.append(CallTiming(method, time.perf_counter() - t0))
        if not line:
            raise RuntimeError(f"engine process died during {method}")
        data = json.loads(line)
        self.total_ticks += data.get("ticks", 0) or 0
        if "error" in data:
            e = data["error"]
            raise RPCError(method, e.get("code", 0), e.get("data", {}).get("name", "?"), e.get("message", ""))
        return data["result"]

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
        if self._proc.poll() is None:
            try:
                self._proc.stdin.write(json.dumps({"method": "engine.quit"}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()

    def __enter__(self) -> EngineClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
