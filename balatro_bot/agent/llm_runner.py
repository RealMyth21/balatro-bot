"""Run LLM games with BalatroLLM (coder/balatrollm, MIT) against our game servers, in parallel.

Reused from BalatroLLM: prompts/strategies, the OpenAI-compatible client with retries, per-decision
logs (requests.jsonl / responses.jsonl / gamestates.jsonl / stats.json), error handling.
Added here (BalatroLLM can't do these out of the box):
  - connects to already-running servers (our exact engine or `jackdaw serve`) instead of launching
    Balatro; one game per server, a port pool for parallel games
  - optional local analysis tools (agent/tools.py) answered in a multi-turn loop inside a decision
  - a model-id check (some providers serve a different model for a mistyped or retired id), a step cap
  - per-decision outcomes (outcomes.jsonl) for the fine-tuning exporter
  - cost from token usage at your provider's prices, and a budget cap checked before every LLM call
  - errors sorted into the policy's own failures (a loss) and infrastructure errors (left out of the
    results and retried on the next run)
  - option to drop `cache_control` text-part keys that only OpenRouter/Anthropic understand
  - a game record per game (record.jsonl in the run's folder, agent/recorder.py): every request in
    order, who decided it, the resulting state, and for model decisions the reasoning, every analysis
    tool call with its full result, tokens and cost; open it in viewer/index.html

Cost is computed from the usage of answered calls. Calls that timed out or were retried inside the
OpenAI client may still be billed by the provider; check its dashboard for the exact total.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from balatro_bot.agent.recorder import READ_ONLY, Decision, GameRecorder, versions
from balatro_bot.agent.scoring_trace import trace_play
from balatro_bot.agent.tools import (
    TOOL_NAMES,
    Shadow,
    analysis_snapshot,
    parse_tools,
    run_tool,
    tools_for_state,
    viewer_analysis,
)
from balatro_bot.eval.report import error_kind


def cached_tokens(usage: dict) -> int:
    """Cached prompt tokens: OpenAI-style `prompt_tokens_details.cached_tokens`, or a top-level
    `cached_tokens` as some OpenAI-compatible servers report it."""
    return ((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or usage.get("cached_tokens") or 0)


@dataclass
class Prices:
    """USD per million tokens. Defaults are 0: models run on our own GPU. Set them for a paid server
    to get costs and a working budget cap."""

    input: float = 0.0
    output: float = 0.0
    cached_input: float = 0.0

    def cost(self, usage: dict) -> float:
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        cached = cached_tokens(usage)
        return ((prompt - cached) * self.input + cached * self.cached_input + completion * self.output) / 1e6


def run_cost(run_dir: Path, prices: Prices) -> dict:
    """Tokens and cost of one BalatroLLM run, from its responses.jsonl (answered calls; `failed_calls`
    counts calls that ended in an error and may still have been billed)."""
    tok = {"prompt": 0, "completion": 0, "cached": 0, "calls": 0, "failed_calls": 0}
    cost = 0.0
    path = run_dir / "responses.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("error"):
                tok["failed_calls"] += 1
            body = ((rec.get("response") or {}).get("body") or {})
            usage = body.get("usage") or {}
            if not usage:
                continue
            tok["calls"] += 1
            tok["prompt"] += usage.get("prompt_tokens") or 0
            tok["completion"] += usage.get("completion_tokens") or 0
            tok["cached"] += cached_tokens(usage)
            cost += prices.cost(usage)
    return {"tokens": tok, "cost_usd": round(cost, 6)}


def _round_chips(result) -> float | None:
    """The chips scored so far this round in a game response, if it has them."""
    chips = ((result or {}).get("round") or {}).get("chips") if isinstance(result, dict) else None
    return float(chips) if isinstance(chips, (int, float)) else None

def reasoning_of(msg) -> str | None:
    """The model's thinking from a chat message: vLLM's reasoning parser puts it in `reasoning_content`
    (older versions) or `reasoning` (newer ones)."""
    extra = getattr(msg, "model_extra", None) or {}
    for key in ("reasoning_content", "reasoning"):
        value = getattr(msg, key, None) or extra.get(key)
        if value:
            return value
    return None


def same_model(asked: str, served: str) -> bool:
    """Whether the provider served the model asked for: the same id, possibly with a provider prefix
    ("Qwen/...") or a dated snapshot suffix ("...-2026-11-01"). Anything else (an older model a
    provider substitutes for a mistyped or retired id, or no id at all) is a mismatch."""
    if not served:
        return False
    a, s = asked.lower().rsplit("/", 1)[-1], served.lower().rsplit("/", 1)[-1]
    if s == a:
        return True
    rest = s[len(a):] if s.startswith(a) else ""
    return rest[:1] == "-" and rest[1:2].isdigit()


@dataclass
class Budget:
    """Spend shared by all games of a run (one asyncio loop), updated after every LLM call."""

    limit: float = float("inf")
    spent: float = 0.0
    prices: Prices = field(default_factory=Prices)

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit


def _without_seed(text: str) -> str:
    """BalatroLLM's game-state text names the run's seed. It is no help in playing, and a model trained
    on these prompts could learn seed-specific outcomes, so it is left out."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("- **Seed**"))


def _make_bot_class():
    """Defined lazily so the rest of the package works without balatrollm installed."""
    from balatrollm.bot import Bot, BotError
    from balatrollm.collector import ChatCompletionError, ChatCompletionResponse
    from balatrollm.llm import LLMClientError, LLMTimeoutError
    from balatrollm.strategy import StrategyManager

    class RecordingClient:
        """Forwards to BalatroLLM's game client; records every request in the game record and mirrors
        the ones the game accepted to a jackdaw shadow (used by the analysis tools, and for the
        scoring breakdown of played hands in the record)."""

        def __init__(self, inner, bot, shadow: Shadow | None):
            self._inner, self._bot, self._shadow = inner, bot, shadow
            self._chips = None  # the real game's chips this round, from the latest response

        async def call(self, method: str, params: dict | None = None):
            scoring = None
            if method == "play" and self._shadow is not None and self._shadow.ok:
                try:  # how this play scores, from the shadow's identical copy of the game
                    scoring = trace_play(self._shadow.sim.game, list((params or {}).get("cards") or []))
                except Exception:  # noqa: BLE001  malformed cards: the game will refuse the play anyway
                    scoring = None
            chips_before, was_ok = self._chips, self._shadow is not None and self._shadow.ok
            t0 = time.perf_counter()
            try:
                result = await self._inner.call(method, params)
            except Exception as e:
                if self._bot._acting and method not in READ_ONLY:
                    self._bot._action_failed = True
                data = getattr(e, "data", None) or {}
                self._bot._record_step(method, params, error={"name": data.get("name") or type(e).__name__,
                                                              "message": getattr(e, "message", str(e))},
                                       seconds=time.perf_counter() - t0)
                raise
            chips_after = _round_chips(result)
            if scoring is not None and chips_before is not None and chips_after is not None:
                real = chips_after - chips_before  # what the real game scored for this play
                if not self._shadow.verify_play(scoring["total"], real, (params or {}).get("cards") or []):
                    scoring = {**scoring, "matches_game": False, "game_total": real}
            if chips_after is not None:
                self._chips = chips_after
            if self._shadow is not None:
                self._shadow.apply(method, params)
            self._bot._record_step(method, params, result=result, seconds=time.perf_counter() - t0,
                                   scoring=scoring)
            if was_ok and not self._shadow.ok and self._bot.recorder is not None:
                self._bot.recorder.note(f"analysis offline for the rest of the game: {self._shadow.reason}",
                                        kind="analysis_offline")
            return result

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

    class AgentBot(Bot):
        def __init__(self, task, config, port: int, *, tools: bool | str = False, max_steps: int = 2000,
                     max_tool_rounds: int = 3, strip_cache_control: bool = True,
                     strategies_dir: Path | None = None, drop_params: tuple[str, ...] = (),
                     budget: Budget | None = None):
            super().__init__(task, config, port)
            self.budget = budget or Budget()
            self.game_spent = 0.0
            for key in drop_params:  # BalatroLLM's config merge can add params but not remove them
                self.model_config.pop(key, None)
            if strategies_dir is not None and (strategies_dir / task.strategy).exists():
                self.strategy = StrategyManager(task.strategy, strategies_dir=strategies_dir)
            # tools: False (none), True (all), or a parse_tools string ("baseline", "score_plays,optimize_jokers")
            self.tool_names = set(parse_tools(tools))
            self.tools_enabled = bool(self.tool_names)
            self.max_steps, self.max_tool_rounds = max_steps, max_tool_rounds
            self.strip_cache_control = strip_cache_control
            self._steps = 0
            self._last_custom_id: str | None = None
            self._recorded = False
            self._shadow: Shadow | None = None
            self.recorder: GameRecorder | None = None
            self.record_header: dict = {}
            self._decision: Decision | None = None  # the model decision waiting for its game action
            self._acting = False
            self._action_failed = False

        async def __aenter__(self):
            await super().__aenter__()
            self._shadow = Shadow()  # always: the record's scoring breakdowns come from it too
            self._balatro = RecordingClient(self._balatro, self, self._shadow)
            return self

        # -- the game record ------------------------------------------------------------------
        def _record_step(self, method: str, params: dict | None, *, result=None, error=None, seconds=None,
                         scoring=None) -> None:
            if method in READ_ONLY:  # reads (e.g. after an invalid answer) neither are steps nor use the decision
                return
            if self.recorder is None:
                if self._collector is None:  # BalatroLLM's connection check, before the run starts
                    return
                self.recorder = GameRecorder(self._collector.run_dir / "record.jsonl", **self.record_header)
            decision, actor = None, "auto"
            if self._acting and self._decision is not None:  # the action the model chose
                decision, actor, self._decision = self._decision, "llm", None
            clean = {k: v for k, v in (params or {}).items() if k != "reasoning"}
            self.recorder.step(method, clean, result=result, error=error, actor=actor, decision=decision,
                               seconds=seconds, scoring=scoring)

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
            if "[INTERNAL_ERROR]" in msg:  # the server broke, not the model's move: end as infrastructure
                raise BotError(f"game server internal error: {msg[:300]}")
            return await super()._handle_failed_call(msg)

        async def _execute_tool_call(self, response):
            self._steps += 1
            if self._steps > self.max_steps:
                raise BotError(f"step cap reached ({self.max_steps} actions)")
            self._recorded = False
            msg = response.choices[0].message
            calls = getattr(msg, "tool_calls", None) or []
            if self._decision is not None:  # the model's final answer: what a fine-tuned model should produce
                self._decision.extra["model_output"] = {
                    "content": msg.content,
                    "reasoning": reasoning_of(msg),
                    "tool_calls": [{"name": c.function.name, "arguments": c.function.arguments} for c in calls]}
            if calls and calls[0].function.name in TOOL_NAMES:  # an analysis call when none is offered
                msg = f"{calls[0].function.name} is not available now: choose a game action"
                if self._decision is not None and self.recorder is not None:
                    self.recorder.note(msg, self._decision)
                self._decision = None
                return await self._handle_error_call(msg)
            if self._decision is not None:  # the reasoning argument BalatroLLM asks for with each action
                calls = getattr(response.choices[0].message, "tool_calls", None) or []
                if calls:
                    try:
                        why = json.loads(calls[0].function.arguments or "{}").get("reasoning")
                    except (json.JSONDecodeError, AttributeError):
                        why = None
                    if why:
                        self._decision.extra["action_reasoning"] = why
            self._acting = True
            self._action_failed = False
            try:
                state = await super()._execute_tool_call(response)
            finally:
                self._acting = False
                if self._decision is not None and self.recorder is not None:  # no request reached the game
                    self.recorder.note(self._last_error_msg or "no valid action", self._decision)
                self._decision = None
            if not self._recorded:  # the error/failed handlers above record their own outcome
                # a transport error reaches neither handler: the action never took effect
                self._outcome("failed" if self._action_failed else "successful")
            return state

        # -- LLM call: BalatroLLM's prompt, plus an optional local-tool loop -------------------
        def _messages(self, gamestate: dict) -> list[dict]:
            strategy = {"type": "text", "text": self.strategy.render_strategy(gamestate)}
            if not self.strip_cache_control:
                strategy["cache_control"] = {"type": "ephemeral"}
            return [{"role": "user", "content": [
                strategy,
                {"type": "text", "text": _without_seed(self.strategy.render_gamestate(gamestate))},
                {"type": "text", "text": self.strategy.render_memory(
                    history=self._history[-10:], last_error=self._last_error_msg,
                    last_failure=self._last_failed_msg)},
            ]}]

        async def _call_logged(self, messages: list[dict], tools: list[dict]):
            if self.budget.exhausted:
                raise BotError(f"budget exhausted (${self.budget.spent:.2f} of ${self.budget.limit:.2f})")
            custom_id = self._collector.write_request(
                {"model": self.task.model, "messages": messages, "tools": tools, **self.model_config})
            self._last_custom_id = custom_id
            request_id = str(time.time_ns() // 1_000_000)
            try:
                response = await self._llm.call(model=self.task.model, messages=messages, tools=tools,
                                                model_config=self.model_config)
            except (LLMTimeoutError, LLMClientError) as e:
                cause = e.__cause__
                status = getattr(cause, "status_code", None)
                detail = f"{e} ({type(cause).__name__}: {cause})" if cause is not None else str(e)
                self._collector.write_response(id=request_id, custom_id=custom_id,
                                               error=ChatCompletionError(code="error", message=detail[:2000]))
                if isinstance(status, int) and 400 <= status < 500 and status not in (408, 429):
                    # The server rejected this request (e.g. prompt + tool rounds over the context length).
                    # It fails the same way every time, so it ends the game like the model's own failure.
                    raise BotError(f"model request rejected (HTTP {status}): {detail[:300]}") from e
                raise BotError(f"LLM error: {detail[:300]}") from e
            self._collector.write_response(id=str(time.time_ns() // 1_000_000), custom_id=custom_id,
                                           response=ChatCompletionResponse(request_id=request_id, status_code=200,
                                                                           body=response.model_dump()))
            usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
            cost = self.budget.prices.cost(usage)
            self.budget.spent += cost
            self.game_spent += cost
            if self._decision is not None:
                d = self._decision
                d.model_calls += 1
                d.cost_usd += cost
                d.tokens["prompt"] += usage.get("prompt_tokens") or 0
                d.tokens["completion"] += usage.get("completion_tokens") or 0
                d.tokens["cached"] += cached_tokens(usage)
                d.extra.setdefault("custom_ids", []).append(custom_id)  # links to requests/responses.jsonl
                msg = response.choices[0].message if response.choices else None
                if msg is not None:
                    d.reasoning.append(reasoning_of(msg) or "")
                    d.reasoning.append(msg.content or "")
            served = getattr(response, "model", "") or ""
            if not same_model(self.task.model, served):
                raise BotError(f"model mismatch: asked for {self.task.model!r}, provider served {served!r}")
            return response, custom_id

        async def _get_llm_response(self, gamestate: dict[str, Any]):
            t0 = time.perf_counter()
            self._decision = Decision(extra={"state_seen": gamestate.get("state")})
            if self._shadow is not None and gamestate.get("state") == "SELECTING_HAND":
                # every tool's answer, for the record only (the model sees just the tools it called)
                was_ok = self._shadow.ok
                snapshot = analysis_snapshot(self._shadow, gamestate)  # checks the shadow, copies the game
                if was_ok and not self._shadow.ok and self.recorder is not None:
                    self.recorder.note(f"analysis offline for the rest of the game: {self._shadow.reason}",
                                       kind="analysis_offline")
                # runs on its own copy while the model thinks; finished before the action is applied
                analysis_task = asyncio.ensure_future(asyncio.to_thread(viewer_analysis, snapshot))
            else:
                analysis_task = None
            decision = self._decision
            try:
                return await self._decide(gamestate)
            finally:
                if analysis_task is not None:
                    try:
                        analysis = await analysis_task
                    except Exception as e:  # noqa: BLE001  never let the record's extras stop a game
                        analysis = {"error": f"{type(e).__name__}: {e}"}
                    if analysis:
                        decision.extra["analysis"] = analysis
                if self._decision is not None:
                    self._decision.seconds = time.perf_counter() - t0

        async def _decide(self, gamestate: dict[str, Any]):
            messages = self._messages(gamestate)
            game_tools = self.strategy.get_tools(gamestate["state"])
            local_tools = tools_for_state(gamestate.get("state"), self.tool_names) if self.tools_enabled else []
            for rnd in range(self.max_tool_rounds + 1):
                offer = bool(local_tools) and rnd < self.max_tool_rounds
                if local_tools and rnd == self.max_tool_rounds:  # the analysis tools are no longer offered
                    messages = messages + [{"role": "user", "content": (
                        "You have used all analysis calls for this decision. Choose a game action now.")}]
                response, _ = await self._call_logged(messages, game_tools + (local_tools if offer else []))
                msg = response.choices[0].message
                calls = list(getattr(msg, "tool_calls", None) or [])
                if not offer or not calls or calls[0].function.name not in self.tool_names:
                    return response  # a game action (BalatroLLM executes the first call) or none
                # Answer every analysis call (they may come in parallel); game actions mixed in after
                # them are dropped from the replayed message, so no tool call is left unanswered.
                local = [c for c in calls if c.function.name in self.tool_names]
                answers = []
                for c in local:  # off the event loop: other games keep running meanwhile
                    t_tool = time.perf_counter()
                    result = await asyncio.to_thread(run_tool, self._shadow, gamestate, c.function.name,
                                                     c.function.arguments)
                    answers.append({"role": "tool", "tool_call_id": c.id, "content": result})
                    if self._decision is not None:
                        try:
                            args = json.loads(c.function.arguments or "{}")
                        except json.JSONDecodeError:
                            args = c.function.arguments
                        self._decision.tool_calls.append({"name": c.function.name, "arguments": args,
                                                          "result": json.loads(result),
                                                          "seconds": round(time.perf_counter() - t_tool, 3)})
                self._outcome("local_tool", tools=[c.function.name for c in local])
                assistant = msg.model_dump(exclude_none=True)
                for key in ("reasoning_content", "reasoning"):  # thinking text: recorded, not sent back
                    assistant.pop(key, None)
                assistant["tool_calls"] = [tc for tc in assistant.get("tool_calls") or []
                                           if tc["function"]["name"] in self.tool_names]
                messages = messages + [assistant, *answers]
            return response

    AgentBot.RecordingClient = RecordingClient  # for tests
    return AgentBot


async def run_games(*, ports: list[int], seeds: list[str], model: str, base_url: str, api_key: str,
                    runs_dir: Path, summary: Path, label: str, deck: str = "BLACK", stake: str = "GOLD",
                    strategy: str = "default", tools: bool | str = False, model_config: dict | None = None,
                    prices: Prices | None = None, max_cost: float = float("inf"), max_steps: int = 2000,
                    strip_cache_control: bool = True, strategies_dir: Path | None = None,
                    drop_params: tuple[str, ...] = (), port_alive=None) -> dict:
    """Play each seed once, len(ports) games at a time. Seeds already finished under the same label,
    deck, stake and model are skipped; seeds that stopped on an infrastructure error are retried.

    Budget: `max_cost` covers everything spent under this configuration, earlier runs included. It is
    checked before every LLM call (games still running stop when it is reached, and are retried next
    time), and a new game only starts if the games already running are expected to fit.

    port_alive(port) -> bool (e.g. ServerPool.alive): a port whose server died is taken out of use."""
    from balatrollm.config import Config, Task

    prices = prices or Prices()
    AgentBot = _make_bot_class()  # noqa: N806
    done: set[str] = set()
    budget = Budget(limit=max_cost, prices=prices)
    finished_costs: list[float] = []
    if summary.exists():
        for line in summary.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if (r.get("policy"), r.get("deck"), r.get("stake"), r.get("model")) != (label, deck, stake, model):
                continue
            budget.spent += r.get("cost_usd") or 0.0
            if error_kind(r) != "infra":
                done.add(r["seed"])
                finished_costs.append(r.get("cost_usd") or 0.0)
    todo = [s for s in seeds if s not in done]
    config = Config(model=[model], seed=todo, deck=[deck], stake=[stake], strategy=[strategy],
                    parallel=len(ports), base_url=base_url, api_key=api_key, model_config=model_config or {})
    port_q: asyncio.Queue[int | None] = asyncio.Queue()
    for p in ports:
        port_q.put_nowait(p)
    live = set(ports)
    summary.parent.mkdir(parents=True, exist_ok=True)
    running: dict[str, Any] = {}
    state = {"stopped": False, "played": 0, "start_spent": budget.spent}

    def fits() -> bool:
        if budget.exhausted:
            return False
        if not finished_costs:
            return True
        per_game = sum(finished_costs) / len(finished_costs)
        committed = sum(max(0.0, per_game - b.game_spent) for b in running.values())
        return budget.spent + committed + per_game <= budget.limit

    async def get_port() -> int | None:
        while True:
            port = await port_q.get()
            if port is None or port_alive is None or port_alive(port):
                return port
            live.discard(port)
            print(f"  server on port {port} died; continuing on {len(live)} servers", flush=True)
            if not live:  # wake every waiting game: nothing left to play on
                for _ in todo:
                    port_q.put_nowait(None)

    async def one(seed: str) -> None:
        port = await get_port()
        if port is None:
            state["stopped"] = True
            return
        played = False
        try:
            if state["stopped"] or not fits():
                state["stopped"] = True
                return
            played = True
            task = Task(model=model, seed=seed, deck=deck, stake=stake, strategy=strategy)
            t0 = time.perf_counter()
            error = None
            stats = None
            bot = AgentBot(task, config, port, tools=tools, max_steps=max_steps,
                           strip_cache_control=strip_cache_control, strategies_dir=strategies_dir,
                           drop_params=drop_params, budget=budget)
            running[seed] = bot
            bot.record_header = {
                "seed": seed, "deck": deck, "stake": stake, "port": port,
                "player": {"kind": "llm", "label": label, "model": model, "strategy": strategy, "tools": tools,
                           "model_config": model_config or {}},
                "versions": versions()}
            try:
                async with bot:
                    stats = await bot.play(runs_dir)
            except Exception as e:  # noqa: BLE001  record and continue with other seeds
                error = f"{type(e).__name__}: {e}"
                if bot._collector is not None:  # BalatroLLM still logged the game up to the error
                    try:
                        stats = bot._collector._calculate_stats()
                    except Exception:  # noqa: BLE001  too short to have stats
                        stats = None
            finally:
                running.pop(seed, None)
            run_dir = bot._collector.run_dir if bot._collector else None
            cost = run_cost(run_dir, prices) if run_dir else {"tokens": {}, "cost_usd": 0.0}
            if bot.recorder is not None:
                bot.recorder.end(won=bool(getattr(stats, "run_won", False)), ante=getattr(stats, "final_ante", None),
                                 round=getattr(stats, "final_round", None), error=error,
                                 error_kind=error_kind({"error": error}), cost_usd=bot.game_spent,
                                 analysis=bot._shadow.summary() if bot._shadow is not None else None,
                                 seconds=round(time.perf_counter() - t0, 1))
            row = {"seed": seed, "policy": label, "deck": deck, "stake": stake,
                   "won": bool(getattr(stats, "run_won", False)), "ante": getattr(stats, "final_ante", None),
                   "round": getattr(stats, "final_round", None), "cost_usd": cost["cost_usd"],
                   "tokens": cost["tokens"], "seconds": round(time.perf_counter() - t0, 1), "error": error,
                   "error_kind": error_kind({"error": error}),
                   "run_dir": str(run_dir) if run_dir else None, "model": model, "tools": tools,
                   "analysis": bot._shadow.summary() if bot._shadow is not None else None,
                   "record": str(bot.recorder.path) if bot.recorder else None}
            if row["error_kind"] != "infra":
                finished_costs.append(cost["cost_usd"])
            state["played"] += 1
            with summary.open("a") as f:
                f.write(json.dumps(row) + "\n")
            note = f" {row['error_kind'].upper()} ERROR {error}" if error else ""
            print(f"  {label} {seed}: ante {row['ante']} won={row['won']} ${cost['cost_usd']:.3f} "
                  f"(total ${budget.spent:.2f}){note}", flush=True)
        finally:
            if played:
                await asyncio.sleep(1.0)  # let the server settle before the next game on this port
            port_q.put_nowait(port)

    await asyncio.gather(*(one(s) for s in todo))
    return {"played": state["played"], "spent_usd": budget.spent - state["start_spent"],
            "total_spent_usd": budget.spent, "stopped_on_budget": state["stopped"] and bool(live),
            "servers_lost": len(ports) - len(live)}
