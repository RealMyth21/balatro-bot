"""Launch and manage headless Balatro instances for bot runs (macOS).

Design choices:
- Game is started by exec'ing LOVE directly from Python. Launching through a
  SIP-protected binary (nohup, env, sh from /usr/bin) strips DYLD_INSERT_LIBRARIES,
  which silently disables lovely and the bot API.
- Mods come from an isolated folder (runtime/mods) via lovely's --mod-dir, so the
  user's own Mods folder (multiplayer etc.) is never loaded or touched.
- Every bot game is forced onto a dedicated profile (default 2, the "bot" profile).
- settings.jkr is snapshotted before launch and restored after shutdown, because the
  balatrobot mod mutes audio and changes graphics settings, and the profile switch
  would otherwise change the user's default profile.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from balatro_bot.paths import game_dir, save_dir

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GAME_DIR = game_dir()
LOVE_PATH = GAME_DIR / "Balatro.app/Contents/MacOS/love"  # macOS only (see README)
LOVELY_PATH = GAME_DIR / "liblovely.dylib"
SAVE_DIR = save_dir()
MOD_DIR = PROJECT_ROOT / "runtime/mods"  # built by `scripts/setup.py real-game`
LOG_DIR = PROJECT_ROOT / "runtime/logs"


@dataclass
class InstanceConfig:
    port: int = 12346
    fast: bool = True
    headless: bool = True
    no_shaders: bool = True
    profile: int = 2
    extra_env: dict[str, str] = field(default_factory=dict)


class BalatroInstance:
    """One headless Balatro process exposing the balatrobot JSON-RPC API on `port`."""

    def __init__(self, cfg: InstanceConfig):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.log_path = LOG_DIR / f"game-{cfg.port}.log"

    def start(self, timeout: float = 60.0) -> None:
        if not LOVE_PATH.exists() or not LOVELY_PATH.exists():
            raise RuntimeError(f"Balatro (macOS) or lovely not found under {GAME_DIR}; set BALATRO_GAME_DIR")
        if not (MOD_DIR / "balatrobot/balatrobot.lua").exists():
            raise RuntimeError(f"{MOD_DIR} is not set up; run `python scripts/setup.py real-game`")
        if not _steam_running():
            raise RuntimeError("Steam is not running. Open Steam first; Balatro exits without it.")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update(
            DYLD_INSERT_LIBRARIES=str(LOVELY_PATH),
            BALATROBOT_PORT=str(self.cfg.port),
            BALATROBOT_FAST="1" if self.cfg.fast else "0",
            BALATROBOT_HEADLESS="1" if self.cfg.headless else "0",
            BALATROBOT_NO_SHADERS="1" if self.cfg.no_shaders else "0",
            BOT_PROFILE=str(self.cfg.profile),
        )
        env.update(self.cfg.extra_env)
        log = open(self.log_path, "w")
        self.proc = subprocess.Popen(
            [str(LOVE_PATH), "--mod-dir", str(MOD_DIR)],
            cwd=str(GAME_DIR),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Balatro exited early (code {self.proc.returncode}); see {self.log_path}")
            if _healthy(self.cfg.port):
                return
            time.sleep(0.25)
        self.stop()
        raise TimeoutError(f"balatrobot API did not come up on port {self.cfg.port}; see {self.log_path}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class InstancePool:
    """Starts N instances on consecutive ports and restores user settings on exit."""

    def __init__(self, n: int, base_port: int = 12346, **cfg_kwargs):
        ports: list[int] = []
        p = base_port
        while len(ports) < n:
            if _port_free(p):
                ports.append(p)
            p += 1
        self.instances = [BalatroInstance(InstanceConfig(port=port, **cfg_kwargs)) for port in ports]
        self._settings_backup = LOG_DIR / "settings.jkr.pre-run"

    def __enter__(self) -> InstancePool:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        # keep the real game's helper mod and determinism shim identical to the repo's
        helper = MOD_DIR / "bb_profile"
        helper.mkdir(parents=True, exist_ok=True)
        for f in (PROJECT_ROOT / "mods/bb_profile").iterdir():
            shutil.copy2(f, helper / f.name)
        shutil.copy2(PROJECT_ROOT / "engine/lua/determinism.lua", helper / "determinism.lua")
        settings = SAVE_DIR / "settings.jkr"
        if settings.exists():
            shutil.copy2(settings, self._settings_backup)
        try:
            for inst in self.instances:
                inst.start()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc) -> None:
        for inst in self.instances:
            inst.stop()
        if self._settings_backup.exists():
            shutil.copy2(self._settings_backup, SAVE_DIR / "settings.jkr")

    @property
    def ports(self) -> list[int]:
        return [i.cfg.port for i in self.instances]


def _healthy(port: int) -> bool:
    try:
        r = httpx.post(
            f"http://127.0.0.1:{port}",
            json={"jsonrpc": "2.0", "method": "health", "id": 1},
            timeout=1.0,
        )
        return r.json().get("result", {}).get("status") == "ok"
    except Exception:
        return False


def _port_free(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _steam_running() -> bool:
    out = subprocess.run(["pgrep", "-x", "steam_osx"], capture_output=True)
    return out.returncode == 0
