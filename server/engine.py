"""Thin, thread-safe adapter over `metro_router` for the HTTP layer."""
from __future__ import annotations
import threading
from typing import Any

import landmarks

from metro_router import (
    ALGORITHMS,
    DEFAULT_DEPTH_LIMIT,
    SCENARIOS,
    MetroGraph,
    NoRouteFoundError,
    Route,
    StationNotActiveError,
    Tracer,
    Weights,
    optimal_duration,
    search_kwargs,
)

# `_Meter` drives process-global tracemalloc, so two searches running at once
# would corrupt each other's peak_memory_bytes and can raise on the second
# stop(). Every engine call is serialised behind this lock. Searches are tens
# of milliseconds at worst, so the contention is irrelevant.
_ENGINE_LOCK = threading.Lock()

_GRAPHS: dict[str, MetroGraph] = {}
_GRAPH_LOCK = threading.Lock()

# Two families. Greedy, hill climbing and beam use the same heuristic as A* to
# order their choices, so they are informed searches too -- just ones that give
# up optimality for speed. One group, with the trade visible in /api/compare,
# says that more honestly than a "heuristic" family that suggests the others
# do without one.
ALGORITHM_FAMILY = {
    "bfs": "blind search",
    "dfs": "blind search",
    "ucs": "blind search",
    "dls": "blind search",
    "ids": "blind search",
    "bidirectional": "blind search",
    "greedy": "informed (heuristic) search",
    "hill_climbing": "informed (heuristic) search",
    "beam": "informed (heuristic) search",
    "astar": "informed (heuristic) search",
    "astar_landmark": "informed (heuristic) search",
    "ida_star": "informed (heuristic) search",
}

ALGORITHM_LABEL = {
    "bfs": "Breadth-first (BFS)",
    "dfs": "Depth-first (DFS)",
    "ucs": "Uniform-cost (UCS)",
    "dls": "Depth-limited (DLS)",
    "ids": "Iterative deepening (IDS)",
    "bidirectional": "Bidirectional",
    "greedy": "Greedy best-first",
    "hill_climbing": "Hill climbing",
    "beam": "Beam search",
    "astar": "A*",
    "astar_landmark": "A* + ALT landmarks",
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
    """Build every scenario, and the default-weighting landmark table with it.

    Without this the first `astar_landmark` request would pay for 2k Dijkstras
    and look slow for a reason that has nothing to do with the search.
    """
    for scenario in SCENARIOS:
        get_graph(scenario).landmark_table()


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
    depth_limit: int = DEFAULT_DEPTH_LIMIT,
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
    kwargs: dict[str, Any] = {
        "weights": weights, "trace_memory": trace_memory, "tracer": tracer,
        **search_kwargs(algorithm, beam_width=beam_width, depth_limit=depth_limit),
    }

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


# Which heuristic each algorithm actually evaluates. Blind searches use none;
# the map shading still shows the straight-line one for them, as the reference
# every informed search starts from.
LANDMARK_ALGORITHMS = frozenset({"astar_landmark"})


def heuristic_view(graph: MetroGraph, goal_id: str, algorithm: str = "astar",
                   weights: Weights = Weights()) -> dict[str, Any]:
    """h(n) towards one goal, as the chosen algorithm really computes it.

    Returned per station (for the map halo) and, for ALT, per node with the
    landmark arithmetic behind each value (for the inspector). Values are in the
    same weighted units as the trace's g/h/f, so the two agree number for number.

    Per station the halo uses the *smallest* h over the station's platforms:
    the ALT bound depends on the line you are standing on, and the minimum is
    still a valid lower bound for "somewhere at this station".
    """
    if algorithm not in ALGORITHMS:
        raise BadRequest(f"unknown algorithm {algorithm!r}, expected one of {sorted(ALGORITHMS)}")
    scale = weights.time

    if algorithm not in LANDMARK_ALGORITHMS:
        values = {sid: scale * h for sid, h in heuristic_by_station(graph, goal_id).items()}
        return {"kind": "straight_line", "algorithm": algorithm,
                "max_h": max(values.values()) if values else 0.0, "h": values}

    table = graph.landmark_table(weights)   # cached per weighting; has its own lock
    bound = landmarks.GoalBound.build(table, graph.start_nodes(goal_id))
    per_station: dict[str, float] = {}
    explain: dict[str, dict[str, Any]] = {}
    for node in graph.adjacency:
        station_id, line_id = node
        straight = scale * graph.heuristic(node, goal_id)
        detail = bound.explain(node)
        h = max(detail["bound"] if detail else 0.0, straight)
        entry: dict[str, Any] = {"h": h, "straight_line": straight}
        if detail and detail["bound"] >= straight and detail["bound"] > 0.0:
            entry.update(source="landmark", landmark=list(detail["landmark"]), via=detail["via"],
                         first=detail["first"], second=detail["second"])
        else:
            entry["source"] = "straight_line" if straight > 0.0 else "goal"
        explain[f"{station_id}|{line_id}"] = entry
        per_station[station_id] = min(h, per_station.get(station_id, h))
    return {
        "kind": "landmark", "algorithm": algorithm,
        "max_h": max(per_station.values()) if per_station else 0.0,
        "h": per_station,
        "landmarks": [list(node) for node in table.landmarks],
        "explain": explain,
    }


def landmark_calculation(graph: MetroGraph, goal_id: str, node: tuple[str, str],
                         weights: Weights = Weights()) -> dict[str, Any]:
    """The ALT calculation of h(node), one landmark at a time, with routes.

    This is what the visualizer animates: for each landmark, the two shortest
    routes whose lengths are subtracted (drawn on the map), the difference, and
    the running maximum; then the straight-line bound it is compared against.
    Routes are station ids with same-station platform changes collapsed, since
    the map draws stations, not platforms.
    """
    if node not in graph.adjacency:
        raise BadRequest(f"unknown node {list(node)!r}")
    bound = landmarks.GoalBound.build(graph.landmark_table(weights), graph.start_nodes(goal_id))

    def stations(path: list) -> list[str]:
        out: list[str] = []
        for station_id, _line in path:
            if not out or out[-1] != station_id:
                out.append(station_id)
        return out

    steps = []
    for step in bound.breakdown(node):
        steps.append({
            "landmark": list(step["landmark"]), "via": step["via"],
            "first": step["first"], "second": step["second"], "bound": step["bound"],
            "first_path": stations(step["first_path"]),
            "second_path": stations(step["second_path"]),
        })
    straight = weights.time * graph.heuristic(node, goal_id)
    best_index = max(range(len(steps)), key=lambda i: steps[i]["bound"], default=None)
    best_bound = steps[best_index]["bound"] if best_index is not None else 0.0
    h = max(best_bound, straight, 0.0)
    source = ("landmark" if best_bound > 0.0 and best_bound >= straight
              else "straight_line" if straight > 0.0 else "goal")
    return {
        "node": list(node), "goal": goal_id, "steps": steps,
        "best": best_index if source == "landmark" else None,
        "straight_line": straight, "h": h, "source": source,
    }


__all__ = [
    "ALGORITHM_FAMILY", "ALGORITHM_LABEL", "BadRequest", "NoRouteFoundError",
    "StationNotActiveError", "Tracer", "build_weights", "get_graph",
    "heuristic_by_station", "heuristic_view", "landmark_calculation", "reference_duration",
    "run_search", "warm_up",
]


def compare_all(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights,
    beam_width: int = 5,
    depth_limit: int = DEFAULT_DEPTH_LIMIT,
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
            kwargs = search_kwargs(algorithm, beam_width=beam_width, depth_limit=depth_limit)
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
