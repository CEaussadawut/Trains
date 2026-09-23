from __future__ import annotations
import argparse
import logging
from pathlib import Path

from server.api import serve

DEFAULT_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the metro search visualizer API.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST, help="built frontend to serve")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    serve(port=args.port, host=args.host, dist=args.dist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
