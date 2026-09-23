"""Thin, thread-safe adapter over `metro_router` for the HTTP layer."""
from __future__ import annotations
import threading
from typing import Any

from metro_router import (
    ALGORITHMS,
    SCENARIOS,
    MetroGraph,
    NoRouteFoundError,
    Route,
    StationNotActiveError,
    Tracer,
    Weights,
    optimal_duration,
)

# `_Meter` drives process-global tracemalloc, so two searches running at once
# would corrupt each other's peak_memory_bytes and can raise on the second
# stop(). Every engine call is serialised behind this lock. Searches are tens
# of milliseconds at worst, so the contention is irrelevant.
_ENGINE_LOCK = threading.Lock()

_GRAPHS: dict[str, MetroGraph] = {}
_GRAPH_LOCK = threading.Lock()

ALGORITHM_FAMILY = {
    "greedy": "heuristic search",
    "hill_climbing": "heuristic search",
    "beam": "heuristic search",
    "astar": "informed search",
    "ida_star": "informed search",
}

ALGORITHM_LABEL = {
    "greedy": "Greedy best-first",
    "hill_climbing": "Hill climbing",
    "beam": "Beam search",
    "astar": "A*",
    "ida_star": "IDA*",
}


class BadRequest(ValueError):
    """A request the caller got wrong; maps to HTTP 400."""


def get_graph(scenario: str) -> MetroGraph:
    """Return the cached graph for a scenario, building it at most once.

    A MetroGraph is read-only once constructed, so sharing one across requests
    is safe; rebuilding it would re-read five CSVs every time.
    """
    if scenario not in SCENARIOS:
        raise BadRequest(f"unknown scenario {scenario!r}, expected one of {sorted(SCENARIOS)}")
    graph = _GRAPHS.get(scenario)
    if graph is None:
        with _GRAPH_LOCK:
            graph = _GRAPHS.get(scenario)
            if graph is None:
                graph = MetroGraph(scenario=scenario)
                _GRAPHS[scenario] = graph
    return graph


def warm_up() -> None:
    for scenario in SCENARIOS:
        get_graph(scenario)


def build_weights(raw: dict[str, Any] | None) -> Weights:
    raw = raw or {}
    try:
        return Weights(
            time=float(raw.get("time", 1.0)),
            cost=float(raw.get("cost", 0.0)),
            transit=float(raw.get("transit", 0.0)),
        )
    except (TypeError, ValueError) as exc:
        raise BadRequest(str(exc)) from exc


def run_search(
    graph: MetroGraph,
    algorithm: str,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights,
    beam_width: int = 5,
    tracer: Tracer | None = None,
    trace_memory: bool = True,
) -> tuple[Route | None, str]:
    """Run one search. Returns (route, error_message).

    `NoRouteFoundError` is caught and returned rather than raised: a search
    that fails is a result worth showing, and when a tracer is attached the
    caller still holds a complete replay of how it failed.
    """
    if algorithm not in ALGORITHMS:
        raise BadRequest(f"unknown algorithm {algorithm!r}, expected one of {sorted(ALGORITHMS)}")
    search = ALGORITHMS[algorithm]
    kwargs: dict[str, Any] = {"weights": weights, "trace_memory": trace_memory, "tracer": tracer}
    if algorithm == "beam":
        kwargs["beam_width"] = beam_width

    with _ENGINE_LOCK:
        try:
            return search(graph, start_id, goal_id, **kwargs), ""
        except NoRouteFoundError as exc:
            return None, str(exc)


def reference_duration(graph: MetroGraph, start_id: str, goal_id: str) -> float | None:
    """Dijkstra's optimum, used as the yardstick every algorithm is scored against."""
    with _ENGINE_LOCK:
        try:
            return optimal_duration(graph, start_id, goal_id)
        except NoRouteFoundError:
            return None


def heuristic_by_station(graph: MetroGraph, goal_id: str) -> dict[str, float]:
    """h(n) per station. MetroGraph.heuristic depends only on the station, not
    the line, so one value per station is exact rather than an approximation."""
    values: dict[str, float] = {}
    for station_id in graph.stations:
        nodes = graph.start_nodes(station_id)
        if nodes:
            values[station_id] = graph.heuristic(nodes[0], goal_id)
    return values


__all__ = [
    "ALGORITHM_FAMILY", "ALGORITHM_LABEL", "BadRequest", "NoRouteFoundError",
    "StationNotActiveError", "Tracer", "build_weights", "get_graph",
    "heuristic_by_station", "reference_duration", "run_search", "warm_up",
]


def compare_all(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights,
    beam_width: int = 5,
    repeats: int = 5,
) -> list[Any]:
    """Benchmark every algorithm on one pair.

    Reuses `bench_informed.measure`, which already runs the search, catches
    NoRouteFoundError, takes a median over repeats with memory tracing off and
    scores the result against Dijkstra -- the exact numbers the CLI report
    quotes, so the web view and the report cannot disagree.
    """
    from bench_informed import measure

    if repeats < 1:
        raise BadRequest("repeats must be >= 1")

    results = []
    with _ENGINE_LOCK:
        for algorithm in sorted(ALGORITHMS):
            kwargs = {"beam_width": beam_width} if algorithm == "beam" else {}
            results.append(measure(graph, algorithm, start_id, goal_id, weights, repeats, **kwargs))
    return results


def require_stations(graph: MetroGraph, *station_ids: str) -> None:
    """Validate before running, so bad input fails as 400/422 not mid-search."""
    for station_id in station_ids:
        if station_id not in graph.stations:
            raise BadRequest(f"unknown station {station_id!r}")
        if not graph.active_station_lines(station_id):
            raise StationNotActiveError(
                f"{station_id!r} has no lines open under scenario {graph.scenario!r}"
            )
