"""Check a remote model server before playing games with it.

  uv run python scripts/check_model_server.py --base-url http://127.0.0.1:8000/v1 --model Qwen/Qwen3-8B

Run from the computer that plays the games, with the key in BALATRO_GPU_KEY (or ~/.config/balatro-bot/
gpu_key). It checks, in order, and stops at the first problem:
  1. the server answers and serves the model asked for;
  2. a request the size of a real game prompt (~13k tokens: BalatroLLM's strategy guide plus a game
     state) fits, and comes back as a call to one of the game's own tools (play, discard, ...), so the
     tool-call parser works;
  3. with thinking on, the model's thinking comes back in its own field (the reasoning parser works);
and reports the speed, so you can pick how many games to run at once.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
KEY_FILE = Path.home() / ".config" / "balatro-bot" / "gpu_key"


def read_key(env: str) -> str:
    key = os.environ.get(env, "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--api-key-env", default="BALATRO_GPU_KEY")
    ap.add_argument("--model-config", default="{}", help="the same JSON the games will send (sampling, thinking)")
    ap.add_argument("--timeout", type=float, default=240.0, help="BalatroLLM's limit per model call")
    args = ap.parse_args()

    from openai import OpenAI

    from balatro_bot.agent.llm_runner import reasoning_of, same_model

    key = read_key(args.api_key_env)
    if not key:
        sys.exit(f"no key: set {args.api_key_env} or put it in {KEY_FILE}")
    client = OpenAI(base_url=args.base_url, api_key=key, timeout=args.timeout, max_retries=0)
    config = json.loads(args.model_config)

    print(f"1. server at {args.base_url}")
    try:
        served = [m.id for m in client.models.list().data]
    except Exception as e:  # noqa: BLE001
        sys.exit(f"   cannot reach it: {type(e).__name__}: {e}\n"
                 "   Is the model server running, and the tunnel (or Tailscale) connected? Is the key right?")
    if not any(same_model(args.model, s) for s in served):
        sys.exit(f"   it serves {served}, not {args.model}")
    print(f"   ok: serves {served}")

    import balatrollm

    strategy = Path(balatrollm.__file__).parent / "strategies" / "default"
    tools = json.loads((strategy / "TOOLS.json").read_text())["SELECTING_HAND"]
    guide = (strategy / "STRATEGY.md.jinja").read_text()
    state = ("## Current game state\n- State: SELECTING_HAND\n- Hand: 0: K of Hearts, 1: K of Spades, "
             "2: 7 of Clubs, 3: 4 of Diamonds, 4: 2 of Hearts, 5: 9 of Spades, 6: 3 of Clubs, 7: Q of Hearts\n"
             "- Hands left: 3, discards left: 3. Blind: 300 chips, 0 scored.\n\n"
             "This is a connection test. Play the pair of kings now with the play tool.")
    messages = [{"role": "user", "content": guide + "\n\n" + state}]
    print("2. a game-sized request (guide + state, with the game's tools)")
    t0 = time.perf_counter()
    try:
        r = client.chat.completions.create(model=args.model, messages=messages, tools=tools, tool_choice="auto",
                                           **config)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"   failed: {type(e).__name__}: {str(e)[:500]}\n"
                 "   If it says the prompt is too long, start the server with a larger MAX_LEN.")
    secs = time.perf_counter() - t0
    msg = r.choices[0].message
    usage = r.usage
    calls = msg.tool_calls or []
    print(f"   {usage.prompt_tokens} prompt tokens, {usage.completion_tokens} output tokens in {secs:.1f} s "
          f"({usage.completion_tokens / max(secs, 1e-6):.0f} output tokens/s, finish: {r.choices[0].finish_reason})")
    if not calls:
        sys.exit(f"   no tool call came back (content: {(msg.content or '')[:300]!r}).\n"
                 "   Check the server's --tool-call-parser (hermes for Qwen3) and --enable-auto-tool-choice.")
    names = {t["function"]["name"] for t in tools}
    call = calls[0].function
    if call.name not in names:
        sys.exit(f"   called {call.name!r}, which is not one of the game's tools")
    try:
        json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        sys.exit(f"   the tool arguments are not JSON: {call.arguments!r}")
    print(f"   ok: called {call.name}({call.arguments})")

    thinking = (config.get("extra_body") or {}).get("chat_template_kwargs", {}).get("enable_thinking", True)
    print("3. thinking")
    reasoning = reasoning_of(msg)
    if thinking and not reasoning:
        if "<think>" in (msg.content or ""):
            sys.exit("   the thinking is mixed into the answer: start the server with --reasoning-parser qwen3")
        print("   warning: no thinking came back (fine for models that don't think)")
    elif thinking:
        print(f"   ok: {len(reasoning)} characters of thinking, kept separate from the answer")
    else:
        print("   off (as asked)")
    if secs > args.timeout / 2:
        print(f"   warning: one call took {secs:.0f} s, over half the {args.timeout:.0f} s limit; "
              "run fewer games at once")
    print("\nThe model server is ready.")


if __name__ == "__main__":
    main()
