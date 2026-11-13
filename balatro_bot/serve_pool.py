"""Start N game servers (balatrobot's HTTP API) on consecutive free ports, for parallel games.

Backends:
  engine   our exact engine (real game code, real description text)   python -m balatro_bot.engine.server
  jackdaw  jackdaw's simulator (fast; text fields are names)            jackdaw serve

  with ServerPool("engine", n=8) as pool:
      pool.ports  # -> [13000, 13001, ...]

or from a shell:  uv run python scripts/serve.py --backend engine --n 8
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def healthy(port: int, host: str = "127.0.0.1") -> bool:
    try:
        r = httpx.post(f"http://{host}:{port}", json={"jsonrpc": "2.0", "method": "health", "id": 1}, timeout=2)
        return r.json().get("result", {}).get("status") == "ok"
    except Exception:  # noqa: BLE001
        return False


class ServerPool:
    def __init__(self, backend: str = "engine", n: int = 4, base_port: int = 13000, log_dir: Path | None = None):
        if backend not in ("engine", "jackdaw"):
            raise ValueError("backend must be 'engine' or 'jackdaw'")
        self.backend, self.n = backend, n
        self.ports: list[int] = []
        p = base_port
        while len(self.ports) < n:
            if _free(p):
                self.ports.append(p)
            p += 1
        self.log_dir = log_dir or ROOT / "runtime/logs/servers"
        self.procs: list[subprocess.Popen] = []

    def _cmd(self, port: int) -> list[str]:
        if self.backend == "engine":
            return [sys.executable, "-m", "balatro_bot.engine.server", "--port", str(port)]
        jd = shutil.which("jackdaw", path=str(Path(sys.executable).parent)) or shutil.which("jackdaw")
        if not jd:
            raise RuntimeError("jackdaw not installed (uv sync --extra sim)")
        return [jd, "serve", "--port", str(port)]

    def __enter__(self) -> ServerPool:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        for port in self.ports:
            log = open(self.log_dir / f"{self.backend}-{port}.log", "w")
            self.procs.append(subprocess.Popen(self._cmd(port), cwd=ROOT, env=env, stdout=log,
                                               stderr=subprocess.STDOUT, start_new_session=True))
        deadline = time.monotonic() + 60
        pending = set(self.ports)
        while pending and time.monotonic() < deadline:
            for port, proc in zip(self.ports, self.procs, strict=True):
                if port in pending:
                    if proc.poll() is not None:
                        self.__exit__()
                        raise RuntimeError(f"{self.backend} server on {port} exited; see {self.log_dir}")
                    if healthy(port):
                        pending.discard(port)
            time.sleep(0.1)
        if pending:
            self.__exit__()
            raise TimeoutError(f"servers not healthy on ports {sorted(pending)}")
        return self

    def __exit__(self, *exc) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.procs = []
