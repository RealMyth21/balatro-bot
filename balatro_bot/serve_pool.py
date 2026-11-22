"""Start N game servers (balatrobot's HTTP API) for parallel games, one game per server.

Backends:
  engine   our exact engine (real game code, real description text)
  jackdaw  jackdaw's simulator with our corrections and card text (fast)
Both are served by `python -m balatro_bot.engine.server [--backend jackdaw]`.

  with ServerPool("engine", n=8) as pool:
      pool.ports  # -> the ports the servers bound, e.g. [53012, 53013, ...]

or from a shell:  uv run python scripts/serve.py --backend engine --n 8

Each server binds its port itself (port 0 = any free port, or the next free one from base_port) and
announces it; the pool reads that announcement, so it can never mistake another process's server
(say, a second pool started at the same time) for its own.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ANNOUNCE = re.compile(r"http://[^\s:/]+:(\d+)")


def healthy(port: int, host: str = "127.0.0.1") -> bool:
    try:
        r = httpx.post(f"http://{host}:{port}", json={"jsonrpc": "2.0", "method": "health", "id": 1}, timeout=2)
        return r.json().get("result", {}).get("status") == "ok"
    except Exception:  # noqa: BLE001
        return False


class ServerPool:
    def __init__(self, backend: str = "engine", n: int = 4, base_port: int | None = None,
                 log_dir: Path | None = None, boot_timeout: float = 60.0):
        if backend not in ("engine", "jackdaw"):
            raise ValueError("backend must be 'engine' or 'jackdaw'")
        self.backend, self.n, self.base_port, self.boot_timeout = backend, n, base_port, boot_timeout
        self.log_dir = log_dir or ROOT / "runtime/logs/servers"
        self.ports: list[int] = []
        self.procs: list[subprocess.Popen] = []
        self._spawned: list[subprocess.Popen] = []  # everything started, for cleanup
        self._tag = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"  # log names unique to this pool

    def _cmd(self, port: int) -> list[str]:
        # jackdaw runs in our own server so it gets our corrections (sim/jackdaw_fixes.py)
        return [sys.executable, "-m", "balatro_bot.engine.server", "--port", str(port), "--backend", self.backend]

    def _spawn(self, port: int, index: int) -> tuple[subprocess.Popen, Path]:
        env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        path = self.log_dir / f"{self.backend}-{self._tag}-{index}-{len(self._spawned)}.log"
        with open(path, "w") as log:  # the child keeps its own handle
            proc = subprocess.Popen(self._cmd(port), cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        self._spawned.append(proc)
        return proc, path

    def __enter__(self) -> ServerPool:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._start()
        except BaseException:
            self.__exit__()
            raise
        return self

    def _start(self) -> None:
        next_port = self.base_port
        pending: dict[int, tuple[subprocess.Popen, Path]] = {}

        def launch(i: int) -> None:
            nonlocal next_port
            port = 0 if next_port is None else next_port
            if next_port is not None:
                next_port += 1
            pending[i] = self._spawn(port, i)

        for i in range(self.n):
            launch(i)
        ports: dict[int, int] = {}
        deadline = time.monotonic() + self.boot_timeout
        attempts = 0
        while len(ports) < self.n:
            if time.monotonic() > deadline:
                raise TimeoutError(f"{self.n - len(ports)} {self.backend} servers did not start; see {self.log_dir}")
            for i, (proc, log) in list(pending.items()):
                text = log.read_text(errors="replace")
                if proc.poll() is not None:  # died, e.g. its fixed port was taken: try the next one
                    attempts += 1
                    if self.base_port is None or attempts > 50:
                        raise RuntimeError(f"{self.backend} server exited:\n{text[-2000:]}")
                    launch(i)
                    continue
                m = ANNOUNCE.search(text)
                if i not in ports and m and healthy(int(m.group(1))):
                    ports[i] = int(m.group(1))
            time.sleep(0.05)
        self.procs = [pending[i][0] for i in range(self.n)]
        self.ports = [ports[i] for i in range(self.n)]

    def alive(self, port: int) -> bool:
        """Whether the server on this port (one of ours) is still running."""
        return self.procs[self.ports.index(port)].poll() is None

    def __exit__(self, *exc) -> None:
        procs = self._spawned
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.procs, self._spawned = [], []
