"""Start N headless game servers (balatrobot's HTTP API) and keep them running until Ctrl-C.

  uv run python scripts/serve.py --backend engine --n 8                      # exact engine, free ports
  uv run python scripts/serve.py --backend jackdaw --n 32 --base-port 13000  # simulator, from port 13000

Any balatrobot client can then play one game per port, e.g. BalatroLLM, jackdaw's LiveBackend or
balatro_bot.game.client.BalatroClient.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balatro_bot.serve_pool import ServerPool  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="engine", choices=["engine", "jackdaw"])
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--base-port", type=int, default=None, help="first port to try (default: any free ports)")
    args = ap.parse_args()
    with ServerPool(args.backend, n=args.n, base_port=args.base_port) as pool:
        print(f"{args.n} {args.backend} servers ready on ports {', '.join(map(str, pool.ports))} (Ctrl-C to stop)",
              flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("stopping")


if __name__ == "__main__":
    main()
