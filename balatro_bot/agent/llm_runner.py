"""Run LLM games with BalatroLLM (coder/balatrollm, MIT) against our game servers, in parallel.

Reused from BalatroLLM: prompts/strategies, the OpenAI-compatible client with retries, per-decision
logs (requests.jsonl / responses.jsonl / gamestates.jsonl / stats.json), error handling.
Added here (BalatroLLM can't do these out of the box):
  - connects to already-running servers (our exact engine or `jackdaw serve`) instead of launching
    Balatro; one game per server, a port pool for parallel games
  - optional local analysis tools (agent/tools.py) answered in a multi-turn loop inside a decision
  - a model-id check (Moonshot falls back to an older model on a mistyped id), a step cap
  - per-decision outcomes (outcomes.jsonl) for the fine-tuning exporter
  - cost from token usage at your provider's prices, and a total budget cap
  - option to drop `cache_control` text-part keys that only OpenRouter/Anthropic understand
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from balatro_bot.agent.tools import TOOL_NAMES, TOOL_SCHEMAS, Shadow, run_tool


@dataclass
class Prices:
    """USD per million tokens. Defaults: Kimi K3.1 Pro as reported by third parties (Nov 2026);
    check Moonshot's pricing page before relying on them."""

    input: float = 2.0
    output: float = 10.0
    cached_input: float = 0.2

    def cost(self, usage: dict) -> float:
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                  or usage.get("cached_tokens") or 0)
        return ((prompt - cached) * self.input + cached * self.cached_input + completion * self.output) / 1e6


def run_cost(run_dir: Path, prices: Prices) -> dict:
    """Tokens and cost of one BalatroLLM run, from its responses.jsonl."""
    tok = {"prompt": 0, "completion": 0, "cached": 0, "calls": 0}
    cost = 0.0
    path = run_dir / "responses.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            body = ((json.loads(line).get("response") or {}).get("body") or {})
            usage = body.get("usage") or {}
            if not usage:
                continue
            tok["calls"] += 1
            tok["prompt"] += usage.get("prompt_tokens") or 0
            tok["completion"] += usage.get("completion_tokens") or 0
            tok["cached"] += (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            cost += prices.cost(usage)
    return {"tokens": tok, "cost_usd": round(cost, 6)}


def _make_bot_class():
    """Defined lazily so the rest of the package works without balatrollm installed."""
    from balatrollm.bot import Bot, BotError
    from balatrollm.collector import ChatCompletionError, ChatCompletionResponse
    from balatrollm.llm import LLMClientError, LLMTimeoutError
    from balatrollm.strategy import StrategyManager

    class MirroredClient:
        """Forwards to BalatroLLM's game client and mirrors successful requests to a jackdaw shadow."""

        def __init__(self, inner, shadow: Shadow):
            self._inner, self._shadow = inner, shadow

        async def call(self, method: str, params: dict | None = None):
            result = await self._inner.call(method, params)
            self._shadow.apply(method, params)
            return result

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

    class AgentBot(Bot):
        def __init__(self, task, config, port: int, *, tools: bool = False, max_steps: int = 2000,
                     max_tool_rounds: int = 3, strip_cache_control: bool = True,
                     strategies_dir: Path | None = None, drop_params: tuple[str, ...] = ()):
            super().__init__(task, config, port)
            for key in drop_params:  # BalatroLLM's config merge can add params but not remove them
                self.model_config.pop(key, None)
            if strategies_dir is not None and (strategies_dir / task.strategy).exists():
                self.strategy = StrategyManager(task.strategy, strategies_dir=strategies_dir)
            self.tools_enabled = tools
            self.max_steps, self.max_tool_rounds = max_steps, max_tool_rounds
            self.strip_cache_control = strip_cache_control
            self._steps = 0
            self._last_custom_id: str | None = None
            self._recorded = False
            self._shadow: Shadow | None = None

        async def __aenter__(self):
            await super().__aenter__()
            if self.tools_enabled:
                self._shadow = Shadow()
                self._balatro = MirroredClient(self._balatro, self._shadow)
            return self

        # -- outcomes for the fine-tuning exporter ---------------------------------------------
        def _outcome(self, outcome: str, **extra) -> None:
            self._recorded = True
            if self._collector is None or self._last_custom_id is None:
                return
            with (self._collector.run_dir / "outcomes.jsonl").open("a") as f:
                f.write(json.dumps({"custom_id": self._last_custom_id, "outcome": outcome, **extra}) + "\n")

        async def _handle_error_call(self, msg: str):
            self._outcome("error", detail=msg[:300])
            return await super()._handle_error_call(msg)

        async def _handle_failed_call(self, msg: str):
            self._outcome("failed", detail=msg[:300])
            return await super()._handle_failed_call(msg)

        async def _execute_tool_call(self, response):
            self._steps += 1
            if self._steps > self.max_steps:
                raise BotError(f"step cap reached ({self.max_steps} actions)")
            self._recorded = False
            state = await super()._execute_tool_call(response)
            if not self._recorded:  # the error/failed handlers above record their own outcome
                self._outcome("successful")
            return state

        # -- LLM call: BalatroLLM's prompt, plus an optional local-tool loop -------------------
        def _messages(self, gamestate: dict) -> list[dict]:
            strategy = {"type": "text", "text": self.strategy.render_strategy(gamestate)}
            if not self.strip_cache_control:
                strategy["cache_control"] = {"type": "ephemeral"}
            return [{"role": "user", "content": [
                strategy,
                {"type": "text", "text": self.strategy.render_gamestate(gamestate)},
                {"type": "text", "text": self.strategy.render_memory(
                    history=self._history[-10:], last_error=self._last_error_msg,
                    last_failure=self._last_failed_msg)},
            ]}]

        async def _call_logged(self, messages: list[dict], tools: list[dict]):
            custom_id = self._collector.write_request(
                {"model": self.task.model, "messages": messages, "tools": tools, **self.model_config})
            self._last_custom_id = custom_id
            request_id = str(time.time_ns() // 1_000_000)
            try:
                response = await self._llm.call(model=self.task.model, messages=messages, tools=tools,
                                                model_config=self.model_config)
            except (LLMTimeoutError, LLMClientError) as e:
                self._collector.write_response(id=request_id, custom_id=custom_id,
                                               error=ChatCompletionError(code="error", message=str(e)))
                raise BotError(f"LLM error: {e}") from e
            self._collector.write_response(id=str(time.time_ns() // 1_000_000), custom_id=custom_id,
                                           response=ChatCompletionResponse(request_id=request_id, status_code=200,
                                                                           body=response.model_dump()))
            served = getattr(response, "model", "") or ""
            if self.task.model not in served and served not in self.task.model:
                raise BotError(f"model mismatch: asked for {self.task.model!r}, provider served {served!r}")
            return response, custom_id

        async def _get_llm_response(self, gamestate: dict[str, Any]):
            messages = self._messages(gamestate)
            game_tools = self.strategy.get_tools(gamestate["state"])
            use_local = self.tools_enabled and gamestate.get("state") == "SELECTING_HAND"
            for rnd in range(self.max_tool_rounds + 1):
                tools = game_tools + (TOOL_SCHEMAS if use_local and rnd < self.max_tool_rounds else [])
                response, _ = await self._call_logged(messages, tools)
                msg = response.choices[0].message
                call = (msg.tool_calls or [None])[0] if getattr(msg, "tool_calls", None) else None
                if call is None or call.function.name not in TOOL_NAMES or not use_local:
                    return response
                result = run_tool(self._shadow, gamestate, call.function.name, call.function.arguments)
                self._outcome("local_tool", tool=call.function.name)
                messages = messages + [
                    msg.model_dump(exclude_none=True),
                    {"role": "tool", "tool_call_id": call.id, "content": result},
                ]
            return response

    return AgentBot


async def run_games(*, ports: list[int], seeds: list[str], model: str, base_url: str, api_key: str,
                    runs_dir: Path, summary: Path, label: str, deck: str = "BLACK", stake: str = "GOLD",
                    strategy: str = "default", tools: bool = False, model_config: dict | None = None,
                    prices: Prices | None = None, max_cost: float = float("inf"), max_steps: int = 2000,
                    strip_cache_control: bool = True, strategies_dir: Path | None = None,
                    drop_params: tuple[str, ...] = ()) -> dict:
    """Play each seed once (skipping seeds already in `summary`), len(ports) games at a time."""
    from balatrollm.config import Config, Task

    prices = prices or Prices()
    AgentBot = _make_bot_class()  # noqa: N806
    done = set()
    spent = 0.0
    if summary.exists():
        for line in summary.read_text().splitlines():
            r = json.loads(line)
            if r.get("policy") == label:
                done.add(r["seed"])
                spent += r.get("cost_usd") or 0.0
    todo = [s for s in seeds if s not in done]
    config = Config(model=[model], seed=todo, deck=[deck], stake=[stake], strategy=[strategy],
                    parallel=len(ports), base_url=base_url, api_key=api_key, model_config=model_config or {})
    port_q: asyncio.Queue[int] = asyncio.Queue()
    for p in ports:
        port_q.put_nowait(p)
    summary.parent.mkdir(parents=True, exist_ok=True)
    lock = asyncio.Lock()
    state = {"spent": spent, "stopped": False, "played": 0}

    async def one(seed: str) -> None:
        if state["stopped"] or state["spent"] >= max_cost:
            state["stopped"] = True
            return
        port = await port_q.get()
        try:
            task = Task(model=model, seed=seed, deck=deck, stake=stake, strategy=strategy)
            t0 = time.perf_counter()
            error = None
            stats = None
            bot = AgentBot(task, config, port, tools=tools, max_steps=max_steps,
                           strip_cache_control=strip_cache_control, strategies_dir=strategies_dir,
                           drop_params=drop_params)
            try:
                async with bot:
                    stats = await bot.play(runs_dir)
            except Exception as e:  # noqa: BLE001  record and continue with other seeds
                error = f"{type(e).__name__}: {e}"
            run_dir = bot._collector.run_dir if bot._collector else None
            cost = run_cost(run_dir, prices) if run_dir else {"tokens": {}, "cost_usd": 0.0}
            row = {"seed": seed, "policy": label, "deck": deck, "stake": stake,
                   "won": bool(getattr(stats, "run_won", False)), "ante": getattr(stats, "final_ante", None),
                   "round": getattr(stats, "final_round", None), "cost_usd": cost["cost_usd"],
                   "tokens": cost["tokens"], "seconds": round(time.perf_counter() - t0, 1), "error": error,
                   "run_dir": str(run_dir) if run_dir else None, "model": model, "tools": tools}
            async with lock:
                state["spent"] += cost["cost_usd"]
                state["played"] += 1
                with summary.open("a") as f:
                    f.write(json.dumps(row) + "\n")
                print(f"  {label} {seed}: ante {row['ante']} won={row['won']} ${cost['cost_usd']:.3f} "
                      f"(total ${state['spent']:.2f}){' ERROR ' + error if error else ''}", flush=True)
        finally:
            await asyncio.sleep(1.0)  # let the server settle before the next game on this port
            port_q.put_nowait(port)

    await asyncio.gather(*(one(s) for s in todo))
    return {"played": state["played"], "spent_usd": state["spent"], "stopped_on_budget": state["stopped"]}
