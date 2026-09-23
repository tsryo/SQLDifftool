"""Entry point: python -m sqldifftool [--port 8765] [--no-browser] [--demo]"""
from __future__ import annotations

import argparse
import threading
import webbrowser

from .web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="sqldifftool", description="Compare SQL Server schemas in your browser.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: 127.0.0.1, local only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument("--demo", action="store_true", help="preload a comparison of two built-in sample databases")
    args = parser.parse_args()

    app = create_app(demo=args.demo)
    url = f"http://{args.host}:{args.port}/"
    print(f"SQL Difftool running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
