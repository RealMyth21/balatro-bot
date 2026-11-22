"""Open the game viewer (viewer/index.html) on your records in a browser.

  uv run python scripts/view_record.py                       # list every record, pick one
  uv run python scripts/view_record.py runtime/records/search_BLACK_GOLD_EYEQG8GT.jsonl

Serves the viewer and the records over http://127.0.0.1 only (never on the network), so the page can
load them by name. Records come from the LLM runner (runtime/llm_runs/.../record.jsonl) and from
scripts/record_game.py (runtime/records/). The page also opens a record by drag-and-drop without this
script: open viewer/index.html directly.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = [(ROOT / d).resolve() for d in ("viewer", "runtime/records", "runtime/llm_runs")]


def find_records() -> list[Path]:
    found = list((ROOT / "runtime/records").glob("*.jsonl")) + list((ROOT / "runtime/llm_runs").rglob("record.jsonl"))
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] in ("/", "/index.html"):
            self.path = "/viewer/index.html" + (self.path[len(self.path.split("?")[0]):])
        elif self.path == "/records.json":
            body = json.dumps([{"name": str(p.relative_to(ROOT)), "url": "/" + quote(str(p.relative_to(ROOT)))}
                               for p in find_records()]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # only the viewer and the records, nothing else in the repo: checked on the resolved file, so
        # "..", symlinks and URL-encoding can't step outside
        target = Path(self.translate_path(self.path)).resolve()
        if not any(target.is_relative_to(d) for d in ALLOWED):
            self.send_error(404)
            return
        super().do_GET()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record", nargs="?", type=Path, help="a record to open (default: show the list)")
    ap.add_argument("--port", type=int, default=0, help="default: any free port")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), functools.partial(Handler, directory=str(ROOT)))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    if args.record:
        rec = args.record.resolve()
        if not rec.is_relative_to(ROOT / "runtime"):
            sys.exit("records must be under runtime/ (runtime/records or runtime/llm_runs)")
        url += "?record=" + quote("/" + str(rec.relative_to(ROOT)))
    print(f"viewer: {url}  (Ctrl-C to stop)", flush=True)
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
