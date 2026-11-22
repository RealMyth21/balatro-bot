"""Game records: one JSON-lines file per game, for reviewing a game afterwards in viewer/index.html.

Every line is one JSON object with a "type":
  header  schema version, seed, deck, stake, who played (model or policy), settings and versions
  state   the game state at the start (balatrobot's gamestate view)
  step    one request sent to the game, in order: who decided it ("llm", "policy", "auto" or
          "assist"), the method and params, the resulting game state (or the error), timing; for a
          played hand how it scored (`scoring`: hand, level, chips x mult, the cards that scored and
          every scoring event); for decisions the reasoning, every analysis-tool call with its full
          result, the model's final output (`model_output`, the training target), tokens and cost,
          and the ids of the exact prompts in BalatroLLM's requests.jsonl (`custom_ids`)
  note    a decision that sent nothing to the game (e.g. the model answered without a valid action)
  end     the outcome (won, ante, round), errors, total cost and time

The state before step i is the "state" line or the result of step i-1, so each state is stored once.
Lines are flushed as they are written, so a game in progress can be opened in the viewer.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = "balatro-bot.record/1"
# requests that only read the game; they are not steps
READ_ONLY = {"gamestate", "health", "screenshot", "rpc.discover"}


def _jsonable(x: Any) -> Any:
    try:
        json.dumps(x)
        return x
    except TypeError:
        return json.loads(json.dumps(x, default=str))


@dataclass
class Decision:
    """What went into one decision: reasoning text, analysis-tool calls, model usage."""

    reasoning: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    model_calls: int = 0
    tokens: dict = field(default_factory=lambda: {"prompt": 0, "completion": 0, "cached": 0})
    cost_usd: float = 0.0
    seconds: float = 0.0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = {"reasoning": [r for r in self.reasoning if r], "tool_calls": self.tool_calls,
               "model_calls": self.model_calls, "tokens": self.tokens, "cost_usd": round(self.cost_usd, 6),
               "seconds": round(self.seconds, 2)}
        out.update(self.extra)
        return out


class GameRecorder:
    """Writes a game record (see the module docstring). Use as a context manager or call end()."""

    def __init__(self, path: str | Path, **header: Any):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w")
        self._t0 = time.time()
        self._i = 0
        self._ended = False
        self._write({"type": "header", "schema": SCHEMA, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                     **_jsonable(header)})

    def _write(self, obj: dict) -> None:
        self._fh.write(json.dumps(obj) + "\n")
        self._fh.flush()

    def state(self, view: dict) -> None:
        self._write({"type": "state", "t": round(time.time() - self._t0, 3), "state": view})

    def step(self, method: str, params: dict | None, *, result: dict | None = None, error: dict | None = None,
             actor: str = "auto", decision: Decision | dict | None = None, seconds: float | None = None,
             **extra: Any) -> None:
        if method in READ_ONLY:
            return
        if isinstance(decision, Decision):
            decision = decision.as_dict()
        self._write({"type": "step", "i": self._i, "t": round(time.time() - self._t0, 3), "actor": actor,
                     "method": method, "params": _jsonable(params or {}), "result": result, "error": error,
                     "seconds": None if seconds is None else round(seconds, 3), "decision": decision,
                     **{k: _jsonable(v) for k, v in extra.items() if v is not None}})
        self._i += 1

    def note(self, text: str, decision: Decision | dict | None = None, kind: str = "invalid_answer") -> None:
        """Something that happened after the latest step: an invalid model answer (with the model's
        reply), or the analysis going offline because jackdaw disagreed with the game."""
        if isinstance(decision, Decision):
            decision = decision.as_dict()
        self._write({"type": "note", "after_step": self._i - 1, "t": round(time.time() - self._t0, 3),
                     "kind": kind, "text": text, "decision": decision})

    def end(self, **summary: Any) -> None:
        if self._ended:
            return
        self._ended = True
        self._write({"type": "end", "t": round(time.time() - self._t0, 3), "steps": self._i, **_jsonable(summary)})
        self._fh.close()

    def close(self) -> None:
        if not self._ended:
            self._fh.close()
            self._ended = True

    def __enter__(self) -> GameRecorder:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def versions() -> dict:
    """Versions of what produced a record (best effort)."""
    import subprocess
    from importlib import metadata

    out = {}
    for pkg in ("balatro-bot", "jackdaw-balatro", "jackdaw", "balatrollm"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    try:
        root = Path(__file__).resolve().parents[2]
        out["commit"] = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                                       text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    out.update(game="Balatro 1.0.1o", balatrobot="v1.5.2")
    return out


def load(path: str | Path) -> list[dict]:
    """A record's lines (for analysis scripts and tests)."""
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
