"""Run jackdaw's own MaskablePPO examples (train / evaluate) on a chosen deck and stake.

jackdaw's examples (examples/train_maskable_ppo.py, evaluate_agents.py, balatro_sb3.py at v0.2.6)
always use the environment's default deck and stake. This launcher sets those defaults (e.g. Black
Deck / Gold Stake) and then runs the unmodified example; every other flag passes straight through.

  uv sync --extra sim --extra train
  uv run python scripts/rl_ppo.py fetch                                      # download the pinned examples
  uv run python scripts/rl_ppo.py train --deck BLACK --stake GOLD -- --timesteps 200000 --n-envs 8
  uv run python scripts/rl_ppo.py eval  --deck BLACK --stake GOLD -- --model runs/.../final.zip --episodes 200
"""

from __future__ import annotations

import argparse
import runpy
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "vendor/jackdaw-examples"
TAG = "v0.2.6"
FILES = ("train_maskable_ppo.py", "evaluate_agents.py", "balatro_sb3.py", "README.md")
DECKS = {"RED": "b_red", "BLUE": "b_blue", "YELLOW": "b_yellow", "GREEN": "b_green", "BLACK": "b_black"}
STAKES = {"WHITE": 1, "RED": 2, "GREEN": 3, "BLACK": 4, "BLUE": 5, "PURPLE": 6, "ORANGE": 7, "GOLD": 8}


def fetch() -> None:
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        url = f"https://raw.githubusercontent.com/TylerFlar/jackdaw-balatro/{TAG}/examples/{f}"
        urllib.request.urlretrieve(url, EXAMPLES / f)
        print(f"fetched {f}")


def set_defaults(back: str, stake: int) -> None:
    """Make jackdaw's envs start on this deck/stake unless a caller says otherwise."""
    import jackdaw.env as env_pkg
    from jackdaw.env import gymnasium_wrapper, vector_env

    def with_defaults(cls):
        def make(*args, **kwargs):
            kwargs.setdefault("back_keys", [back])
            kwargs.setdefault("stakes", [stake])
            return cls(*args, **kwargs)

        make.__wrapped__ = cls
        return make

    vec, gym = vector_env.BalatroVectorEnv, gymnasium_wrapper.BalatroGymnasiumEnv
    env_pkg.BalatroVectorEnv = with_defaults(vec)
    env_pkg.BalatroGymnasiumEnv = with_defaults(gym)


def main() -> None:
    argv = sys.argv[1:]
    passthrough = argv[argv.index("--") + 1:] if "--" in argv else []
    ours = argv[: argv.index("--")] if "--" in argv else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["fetch", "train", "eval"])
    ap.add_argument("--deck", default="BLACK", choices=sorted(DECKS))
    ap.add_argument("--stake", default="GOLD", choices=sorted(STAKES))
    args = ap.parse_args(ours)
    if args.mode == "fetch":
        fetch()
        return
    if not (EXAMPLES / "balatro_sb3.py").exists():
        sys.exit("examples missing: run `scripts/rl_ppo.py fetch` first")
    set_defaults(DECKS[args.deck], STAKES[args.stake])
    script = EXAMPLES / ("train_maskable_ppo.py" if args.mode == "train" else "evaluate_agents.py")
    sys.path.insert(0, str(EXAMPLES))
    sys.argv = [str(script), *passthrough]
    print(f"jackdaw {script.name} on {args.deck} Deck / {args.stake} Stake", flush=True)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
