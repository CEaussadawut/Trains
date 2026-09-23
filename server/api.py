"""A dependency-free JSON API over the metro search engine.

Uses only the standard library so the Python side needs no pip install: the
engine it exposes is stdlib-pure too, and a grader can run this with nothing
but `python3`.
"""
from __future__ import annotations
import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from metro_router import ALGORITHMS, SCENARIOS, Tracer

from server import engine, serialize

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1 << 20
DEFAULT_MAX_EVENTS = 20_000


def _meta(_query: dict, _body: dict) -> dict:
    return {
        "scenarios": sorted(SCENARIOS),
        "algorithms": [
            {
                "id": name,
                "label": engine.ALGORITHM_LABEL[name],
                "family": engine.ALGORITHM_FAMILY[name],
                "params": ["beam_width"] if name == "beam" else [],
            }
            for name in sorted(ALGORITHMS)
        ],
        "defaults": {
            "scenario": "operating",
            "algorithm": "astar",
            "weights": {"time": 1.0, "cost": 0.0, "transit": 0.0},
            "beam_width": 5,
            "max_events": DEFAULT_MAX_EVENTS,
        },
    }


def _one(query: dict, key: str, fallback: str) -> str:
    values = query.get(key)
    return values[0] if values else fallback


def _network(query: dict, _body: dict) -> dict:
    graph = engine.get_graph(_one(query, "scenario", "operating"))
    return serialize.network_payload(graph, engine.get_graph("planned"))


def _heuristic(query: dict, _body: dict) -> dict:
    graph = engine.get_graph(_one(query, "scenario", "operating"))
    goal_id = _one(query, "goal", "")
    engine.require_stations(graph, goal_id)
    values = engine.heuristic_by_station(graph, goal_id)
    return {
        "goal": goal_id,
        "scenario": graph.scenario,
        "max_h": max(values.values()) if values else 0.0,
        "h": values,
    }


def _request_common(body: dict) -> tuple[Any, str, str, Any, int]:
    graph = engine.get_graph(str(body.get("scenario", "operating")))
    start_id = str(body.get("start", ""))
    goal_id = str(body.get("goal", ""))
    engine.require_stations(graph, start_id, goal_id)
    weights = engine.build_weights(body.get("weights"))
    try:
        beam_width = int(body.get("beam_width", 5))
    except (TypeError, ValueError) as exc:
        raise engine.BadRequest("beam_width must be an integer") from exc
    if beam_width < 1:
        raise engine.BadRequest("beam_width must be >= 1")
    return graph, start_id, goal_id, weights, beam_width


def _search(_query: dict, body: dict) -> dict:
    graph, start_id, goal_id, weights, beam_width = _request_common(body)
    algorithm = str(body.get("algorithm", "astar"))
    route, error = engine.run_search(
        graph, algorithm, start_id, goal_id, weights=weights, beam_width=beam_width
    )
    return {
        "scenario": graph.scenario,
        "algorithm": algorithm,
        "start": start_id,
        "goal": goal_id,
        "solved": route is not None,
        "error": error,
        "route": serialize.route_payload(graph, route) if route else None,
        "optimal_sec": engine.reference_duration(graph, start_id, goal_id),
    }


def _trace(_query: dict, body: dict) -> dict:
    graph, start_id, goal_id, weights, beam_width = _request_common(body)
    algorithm = str(body.get("algorithm", "astar"))
    options = body.get("trace") or {}

    # IDA* re-walks the tree once per threshold and can expand hundreds of
    # thousands of nodes. Keeping only the last phase's detail bounds the
    # payload while preserving the whole threshold ladder as summaries.
    default_phases = "last" if algorithm == "ida_star" else "all"
    try:
        tracer = Tracer(
            max_events=int(options.get("max_events", DEFAULT_MAX_EVENTS)),
            detail=str(options.get("detail", "full")),
            keep_phases=str(options.get("keep_phases", default_phases)),
        )
    except ValueError as exc:
        raise engine.BadRequest(str(exc)) from exc

    route, error = engine.run_search(
        graph, algorithm, start_id, goal_id,
        weights=weights, beam_width=beam_width, tracer=tracer,
    )
    payload = tracer.to_dict()
    payload.update({
        "scenario": graph.scenario,
        "algorithm": algorithm,
        "start": start_id,
        "goal": goal_id,
        "solved": route is not None,
        "error": error,
        "route": serialize.route_payload(graph, route) if route else None,
        "optimal_sec": engine.reference_duration(graph, start_id, goal_id),
    })
    return payload


def _compare(_query: dict, body: dict) -> dict:
    graph, start_id, goal_id, weights, beam_width = _request_common(body)
    try:
        repeats = int(body.get("repeats", 5))
    except (TypeError, ValueError) as exc:
        raise engine.BadRequest("repeats must be an integer") from exc

    measurements = engine.compare_all(
        graph, start_id, goal_id, weights=weights, beam_width=beam_width, repeats=repeats
    )
    results = []
    for row in measurements:
        entry: dict[str, Any] = {
            "algorithm": row.algorithm,
            "label": engine.ALGORITHM_LABEL[row.algorithm],
            "family": engine.ALGORITHM_FAMILY[row.algorithm],
            "solved": row.solved,
            "error": row.error,
            "median_ms": row.median_ms,
        }
        if row.route is not None:
            entry.update({
                "optimal": row.optimal,
                "excess_pct": row.excess_pct,
                "route": serialize.route_payload(graph, row.route),
            })
        results.append(entry)

    return {
        "scenario": graph.scenario,
        "start": start_id,
        "goal": goal_id,
        "repeats": repeats,
        "optimal_sec": measurements[0].optimal_sec if measurements else None,
        "results": results,
    }


ROUTES: dict[tuple[str, str], Callable[[dict, dict], dict]] = {
    ("GET", "/api/meta"): _meta,
    ("GET", "/api/network"): _network,
    ("GET", "/api/heuristic"): _heuristic,
    ("POST", "/api/search"): _search,
    ("POST", "/api/trace"): _trace,
    ("POST", "/api/compare"): _compare,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "MetroVisualizer/1.0"
    dist: Path | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    # -- helpers ---------------------------------------------------------
    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        body = (
            json.dumps(serialize.json_safe(payload)).encode("utf-8")
            if content_type == "application/json"
            else payload
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send(status, {"code": code, "message": message})

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise engine.BadRequest("request body too large")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise engine.BadRequest(f"invalid JSON body: {exc}") from exc

    # -- verbs -----------------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        handler = ROUTES.get((method, parsed.path))

        if handler is None:
            if method == "GET" and not parsed.path.startswith("/api/"):
                self._serve_static(parsed.path)
            else:
                self._error(HTTPStatus.NOT_FOUND, "not_found", f"no route for {method} {parsed.path}")
            return

        try:
            body = self._read_body() if method == "POST" else {}
            self._send(HTTPStatus.OK, handler(parse_qs(parsed.query), body))
        except engine.StationNotActiveError as exc:
            # Subclasses ValueError, so it must be caught first.
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "station_not_active", str(exc))
        except (engine.BadRequest, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
        except Exception:  # pragma: no cover - genuine server faults
            logger.exception("unhandled error serving %s %s", method, self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "see server logs")

    def _serve_static(self, path: str) -> None:
        """Serve the built single-page app, if one has been built."""
        if self.dist is None:
            self._error(
                HTTPStatus.NOT_FOUND, "no_frontend",
                "no built frontend; run the Vite dev server or `npm run build`",
            )
            return

        relative = path.lstrip("/") or "index.html"
        candidate = (self.dist / relative).resolve()
        if not str(candidate).startswith(str(self.dist.resolve())) or not candidate.is_file():
            candidate = self.dist / "index.html"  # SPA fallback
        if not candidate.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not_found", path)
            return

        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, candidate.read_bytes(), content_type)


def serve(port: int = 8000, host: str = "127.0.0.1", dist: Path | None = None) -> None:
    engine.warm_up()
    Handler.dist = dist if dist and dist.is_dir() else None
    httpd = ThreadingHTTPServer((host, port), Handler)
    where = f"http://{host}:{port}"
    logger.info("serving %s (frontend: %s)", where, Handler.dist or "dev server")
    print(f"metro visualizer API on {where}")
    print(f"  frontend: {Handler.dist or 'not built - use `npm run dev`'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.server_close()
