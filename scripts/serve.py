"""Start N headless game servers (balatrobot's HTTP API) and keep them running until Ctrl-C.

  uv run python scripts/serve.py --backend engine --n 8      # exact engine, ports 13000..13007
  uv run python scripts/serve.py --backend jackdaw --n 32    # fast simulator

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
    ap.add_argument("--base-port", type=int, default=13000)
    args = ap.parse_args()
    with ServerPool(args.backend, n=args.n, base_port=args.base_port) as pool:
        print(f"{args.n} {args.backend} servers ready on ports {pool.ports[0]}..{pool.ports[-1]} (Ctrl-C to stop)",
              flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("stopping")


if __name__ == "__main__":
    main()
