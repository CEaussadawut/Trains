from __future__ import annotations
import argparse
import csv
import heapq
import itertools
import logging
import math
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

Node = tuple[str, str]

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "datasets"

V_MAX_KMH = 45.0
V_MAX_MPS = V_MAX_KMH * 1000.0 / 3600.0
IMPLICIT_TRANSFER_SEC = 180.0

SCENARIOS = {
    "operating": {"operating"},
    "under_construction": {"operating", "under_construction"},
    "planned": {"operating", "under_construction", "planned"},
}


class NoRouteFoundError(Exception):
    pass


class StationNotActiveError(ValueError):
    pass


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@dataclass
class Station:
    id: str
    name_th: str
    name_en: str
    lat: float
    lon: float
    lines: list[str]

    @classmethod
    def from_row(cls, row: dict[str, str]) -> Station:
        return cls(
            id=row["station_id"],
            name_th=row["name_th"],
            name_en=row["name_en"],
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            lines=row["lines"].split("|"),
        )


@dataclass(frozen=True)
class Weights:
    time: float = 1.0
    cost: float = 0.0
    transit: float = 0.0

    def __post_init__(self) -> None:
        if min(self.time, self.cost, self.transit) < 0.0:
            raise ValueError("weights must be non-negative")
        if max(self.time, self.cost, self.transit) == 0.0:
            raise ValueError("at least one weight must be positive")


@dataclass(frozen=True)
class Route:
    path: list[Node]
    duration_sec: float
    fare_thb: float
    nodes_expanded: int
    nodes_generated: int
    runtime_sec: float
    peak_memory_bytes: int

    @property
    def hops(self) -> int:
        return len(self.path) - 1

    @property
    def transfers(self) -> int:
        return sum(1 for (_, line_a), (_, line_b) in zip(self.path, self.path[1:]) if line_a != line_b)


class MetroGraph:
    def __init__(self, scenario: str = "operating", data_dir: Path | str = DEFAULT_DATA_DIR) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}, expected one of {sorted(SCENARIOS)}")

        self.scenario = scenario
        self.data_dir = Path(data_dir)
        self.allowed_status = SCENARIOS[scenario]

        self.stations: dict[str, Station] = {}
        self.lines: dict[str, dict[str, str]] = {}
        self.fares: dict[str, dict[str, float]] = {}
        self.adjacency: dict[Node, list[tuple[Node, float, str]]] = {}
        self.active_lines: set[str] = set()

        self._load_lines()
        self._load_fares()
        self._load_stations()
        self._build_rail_edges()
        self._build_transfer_edges()

        logger.info(
            "loaded %s scenario: %d stations, %d nodes, %d rail edges",
            scenario, len(self.stations), self.num_nodes, self.num_rail_edges,
        )

    def _rows(self, filename: str) -> Iterator[dict[str, str]]:
        with open(self.data_dir / filename, encoding="utf-8", newline="") as f:
            yield from csv.DictReader(f)

    def _load_lines(self) -> None:
        for row in self._rows("lines.csv"):
            self.lines[row["line_id"]] = row
            if row["status"] in self.allowed_status:
                self.active_lines.add(row["line_id"])

    def _load_fares(self) -> None:
        for row in self._rows("fares.csv"):
            self.fares[row["operator"]] = {
                "base_fare": float(row["base_fare"]),
                "per_station": float(row["per_station"]),
                "max_fare": float(row["max_fare"]),
                "entry_fee": float(row["entry_fee"]),
            }

    def _load_stations(self) -> None:
        for row in self._rows("stations.csv"):
            self.stations[row["station_id"]] = Station.from_row(row)

    def active_station_lines(self, station_id: str) -> list[str]:
        station = self.stations.get(station_id)
        if station is None:
            return []
        return [line for line in station.lines if line in self.active_lines]

    def _add_edge(self, a: Node, b: Node, cost: float, kind: str) -> None:
        self.adjacency.setdefault(a, []).append((b, cost, kind))
        self.adjacency.setdefault(b, []).append((a, cost, kind))

    def _build_rail_edges(self) -> None:
        for row in self._rows("edges.csv"):
            line_id = row["line_id"]
            if line_id not in self.active_lines:
                continue
            if row["from_id"] not in self.stations or row["to_id"] not in self.stations:
                continue
            self._add_edge(
                (row["from_id"], line_id), (row["to_id"], line_id), float(row["travel_sec"]), "rail",
            )

    def _build_transfer_edges(self) -> None:
        seen: set[frozenset[Node]] = set()

        for row in self._rows("transfers.csv"):
            a_id, b_id = row["from_id"], row["to_id"]
            walk_sec = float(row["walk_sec"])
            for line_a in self.active_station_lines(a_id):
                for line_b in self.active_station_lines(b_id):
                    if a_id == b_id and line_a == line_b:
                        continue
                    a, b = (a_id, line_a), (b_id, line_b)
                    key = frozenset((a, b))
                    if key in seen:
                        continue
                    seen.add(key)
                    self._add_edge(a, b, walk_sec, "transfer")

        for station_id in self.stations:
            lines_here = self.active_station_lines(station_id)
            for line_a, line_b in itertools.combinations(lines_here, 2):
                a, b = (station_id, line_a), (station_id, line_b)
                key = frozenset((a, b))
                if key in seen:
                    continue
                seen.add(key)
                self._add_edge(a, b, IMPLICIT_TRANSFER_SEC, "transfer")

    def start_nodes(self, station_id: str) -> list[Node]:
        return [(station_id, line) for line in self.active_station_lines(station_id)]

    def neighbors(self, node: Node) -> list[tuple[Node, float, str]]:
        return self.adjacency.get(node, [])

    def heuristic(self, node: Node, goal_id: str) -> float:
        origin = self.stations[node[0]]
        goal = self.stations[goal_id]
        return haversine_m(origin.lat, origin.lon, goal.lat, goal.lon) / V_MAX_MPS

    @property
    def num_nodes(self) -> int:
        return len(self.adjacency)

    @property
    def num_rail_edges(self) -> int:
        return sum(1 for edges in self.adjacency.values() for _, _, kind in edges if kind == "rail") // 2

    def fare_for(self, path: list[Node]) -> float:
        if not path:
            return 0.0

        def operator_of(line_id: str) -> str:
            return self.lines[line_id]["operator"]

        def segment_cost(operator: str | None, hops: int) -> float:
            if operator is None:
                return 0.0
            fare = self.fares[operator]
            return min(fare["base_fare"] + fare["per_station"] * hops, fare["max_fare"])

        total = 0.0
        segment_operator: str | None = None
        segment_hops = 0

        for (_, prev_line), (_, cur_line) in zip(path, path[1:]):
            if cur_line == prev_line:
                operator = operator_of(cur_line)
                if segment_operator is None or operator == segment_operator:
                    segment_operator, segment_hops = operator, segment_hops + 1
                else:
                    total += segment_cost(segment_operator, segment_hops)
                    segment_operator, segment_hops = operator, 1
            else:
                next_operator = operator_of(cur_line)
                if segment_operator is not None and next_operator != segment_operator:
                    total += segment_cost(segment_operator, segment_hops)
                    segment_operator, segment_hops = None, 0

        total += segment_cost(segment_operator, segment_hops)
        return total


def _require_active(graph: MetroGraph, start_id: str, goal_id: str) -> None:
    if start_id not in graph.stations:
        raise ValueError(f"unknown station {start_id!r}")
    if goal_id not in graph.stations:
        raise ValueError(f"unknown station {goal_id!r}")
    if not graph.active_station_lines(start_id):
        raise StationNotActiveError(f"{start_id!r} has no lines open under scenario {graph.scenario!r}")
    if not graph.active_station_lines(goal_id):
        raise StationNotActiveError(f"{goal_id!r} has no lines open under scenario {graph.scenario!r}")


def _reconstruct(came_from: dict[Node, Node], node: Node) -> list[Node]:
    path = [node]
    while node in came_from:
        node = came_from[node]
        path.append(node)
    path.reverse()
    return path


class _Meter:
    def __init__(self, trace_memory: bool = True) -> None:
        self.trace_memory = trace_memory
        self.expanded = 0
        self.generated = 0
        self.runtime_sec = 0.0
        self.peak_memory_bytes = 0
        self._started = 0.0

    def __enter__(self) -> _Meter:
        if self.trace_memory:
            tracemalloc.start()
        self._started = time.perf_counter()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.runtime_sec = time.perf_counter() - self._started
        if self.trace_memory:
            self.peak_memory_bytes = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()


def _route(graph: MetroGraph, path: list[Node], duration_sec: float, meter: _Meter) -> Route:
    return Route(
        path=path,
        duration_sec=duration_sec,
        fare_thb=graph.fare_for(path),
        nodes_expanded=meter.expanded,
        nodes_generated=meter.generated,
        runtime_sec=meter.runtime_sec,
        peak_memory_bytes=meter.peak_memory_bytes,
    )


def _edge_weight(graph: MetroGraph, node: Node, neighbor: Node, cost_sec: float, kind: str, weights: Weights) -> float:
    if kind == "rail":
        fare_component = graph.fares[graph.lines[neighbor[1]]["operator"]]["per_station"]
        transit_component = 0.0
    else:
        from_operator = graph.lines[node[1]]["operator"]
        to_operator = graph.lines[neighbor[1]]["operator"]
        fare_component = 0.0 if from_operator == to_operator else graph.fares[to_operator]["base_fare"]
        transit_component = 1.0
    return weights.time * cost_sec + weights.cost * fare_component + weights.transit * transit_component


def _search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    use_g_score: bool,
    weights: Weights = Weights(),
    trace_memory: bool = True,
) -> Route:
    _require_active(graph, start_id, goal_id)

    counter = itertools.count()
    open_heap: list[tuple[float, int, Node]] = []
    came_from: dict[Node, Node] = {}
    g_score: dict[Node, float] = {}
    time_score: dict[Node, float] = {}
    visited: set[Node] = set()
    path: list[Node] | None = None
    duration_sec = 0.0

    with _Meter(trace_memory) as meter:
        for node in graph.start_nodes(start_id):
            g_score[node] = 0.0
            time_score[node] = 0.0
            meter.generated += 1
            heapq.heappush(open_heap, (weights.time * graph.heuristic(node, goal_id), next(counter), node))

        while open_heap:
            _, _, node = heapq.heappop(open_heap)
            if node in visited:
                continue
            visited.add(node)
            meter.expanded += 1

            if node[0] == goal_id:
                path = _reconstruct(came_from, node)
                duration_sec = time_score[node]
                break

            for neighbor, cost, kind in graph.neighbors(node):
                meter.generated += 1
                if neighbor in visited:
                    continue
                tentative_g = g_score[node] + _edge_weight(graph, node, neighbor, cost, kind, weights)
                if use_g_score:
                    if tentative_g >= g_score.get(neighbor, math.inf):
                        continue
                elif neighbor in g_score:
                    continue
                g_score[neighbor] = tentative_g
                time_score[neighbor] = time_score[node] + cost
                came_from[neighbor] = node
                h = weights.time * graph.heuristic(neighbor, goal_id)
                heapq.heappush(open_heap, (tentative_g + h if use_g_score else h, next(counter), neighbor))

    if path is None:
        raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")
    return _route(graph, path, duration_sec, meter)


def greedy_best_first_search(
    graph: MetroGraph, start_id: str, goal_id: str, *, weights: Weights = Weights(), trace_memory: bool = True
) -> Route:
    return _search(graph, start_id, goal_id, use_g_score=False, weights=weights, trace_memory=trace_memory)


def astar_search(
    graph: MetroGraph, start_id: str, goal_id: str, *, weights: Weights = Weights(), trace_memory: bool = True
) -> Route:
    return _search(graph, start_id, goal_id, use_g_score=True, weights=weights, trace_memory=trace_memory)


def hill_climbing_search(
    graph: MetroGraph, start_id: str, goal_id: str, *, weights: Weights = Weights(), trace_memory: bool = True
) -> Route:
    _require_active(graph, start_id, goal_id)

    def h(node: Node) -> float:
        return weights.time * graph.heuristic(node, goal_id)

    starts = graph.start_nodes(start_id)
    current = min(starts, key=h)
    path = [current]
    visited = {current}
    duration_sec = 0.0
    stuck = ""

    with _Meter(trace_memory) as meter:
        meter.generated += len(starts)
        while True:
            meter.expanded += 1
            if current[0] == goal_id:
                break

            candidates: list[tuple[Node, float]] = []
            for neighbor, cost, _kind in graph.neighbors(current):
                meter.generated += 1
                if neighbor not in visited:
                    candidates.append((neighbor, cost))

            if not candidates:
                stuck = "dead end"
                break

            best_node, best_cost = min(candidates, key=lambda nc: h(nc[0]))
            if h(best_node) > h(current):
                stuck = "local optimum"
                break

            duration_sec += best_cost
            current = best_node
            path.append(current)
            visited.add(current)

    if stuck:
        raise NoRouteFoundError(
            f"hill climbing stuck at {current!r} ({stuck}) en route {start_id!r} -> {goal_id!r}"
        )
    return _route(graph, path, duration_sec, meter)


def beam_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    beam_width: int = 5,
    trace_memory: bool = True,
) -> Route:
    if beam_width < 1:
        raise ValueError("beam_width must be >= 1")
    _require_active(graph, start_id, goal_id)

    def h(node: Node) -> float:
        return weights.time * graph.heuristic(node, goal_id)

    def best_at_goal(entries: list[tuple[float, float, list[Node]]]) -> tuple[float, float, list[Node]] | None:
        reached = [entry for entry in entries if entry[2][-1][0] == goal_id]
        return min(reached, key=lambda entry: entry[0]) if reached else None

    beam = [(0.0, 0.0, [node]) for node in graph.start_nodes(start_id)]
    max_rounds = 2 * max(graph.num_nodes, 1)
    found: tuple[float, float, list[Node]] | None = None

    with _Meter(trace_memory) as meter:
        meter.generated += len(beam)
        for _ in range(max_rounds):
            found = best_at_goal(beam)
            if found is not None:
                meter.expanded += 1
                break

            candidates: list[tuple[float, float, list[Node]]] = []
            for weighted_g, time_g, path in beam:
                node = path[-1]
                in_path = set(path)
                meter.expanded += 1
                for neighbor, cost, kind in graph.neighbors(node):
                    meter.generated += 1
                    if neighbor in in_path:
                        continue
                    new_weighted_g = weighted_g + _edge_weight(graph, node, neighbor, cost, kind, weights)
                    candidates.append((new_weighted_g, time_g + cost, path + [neighbor]))

            if not candidates:
                break

            candidates.sort(key=lambda entry: entry[0] + h(entry[2][-1]))
            beam = candidates[:beam_width]
        else:
            found = best_at_goal(beam)

    if found is None:
        raise NoRouteFoundError(
            f"beam search exhausted the beam (width={beam_width}) en route {start_id!r} -> {goal_id!r}"
        )
    return _route(graph, found[2], found[1], meter)


def ida_star_search(
    graph: MetroGraph, start_id: str, goal_id: str, *, weights: Weights = Weights(), trace_memory: bool = True
) -> Route:
    _require_active(graph, start_id, goal_id)

    def h(node: Node) -> float:
        return weights.time * graph.heuristic(node, goal_id)

    starts = graph.start_nodes(start_id)
    max_rounds = 10_000
    solution: tuple[list[Node], float] | None = None
    exhausted = False

    with _Meter(trace_memory) as meter:
        meter.generated += len(starts)

        def dfs(path: list[Node], on_path: set[Node], g: float, time_g: float, limit: float) -> tuple[bool, float]:
            node = path[-1]
            f = g + h(node)
            if f > limit:
                return False, f
            meter.expanded += 1
            if node[0] == goal_id:
                return True, time_g

            next_limit = math.inf
            for neighbor, cost, kind in graph.neighbors(node):
                meter.generated += 1
                if neighbor in on_path:
                    continue
                path.append(neighbor)
                on_path.add(neighbor)
                edge_g = g + _edge_weight(graph, node, neighbor, cost, kind, weights)
                found, value = dfs(path, on_path, edge_g, time_g + cost, limit)
                if found:
                    return True, value
                path.pop()
                on_path.discard(neighbor)
                if value < next_limit:
                    next_limit = value
            return False, next_limit

        threshold = min(h(node) for node in starts)
        for _ in range(max_rounds):
            next_threshold = math.inf
            for root in starts:
                path = [root]
                found, value = dfs(path, {root}, 0.0, 0.0, threshold)
                if found:
                    solution = (path, value)
                    break
                next_threshold = min(next_threshold, value)

            if solution is not None:
                break
            if next_threshold == math.inf:
                exhausted = True
                break
            threshold = next_threshold

    if solution is None:
        if exhausted:
            raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")
        raise NoRouteFoundError(
            f"IDA* did not converge within {max_rounds} rounds en route {start_id!r} -> {goal_id!r}"
        )
    return _route(graph, solution[0], solution[1], meter)


def optimal_duration(graph: MetroGraph, start_id: str, goal_id: str) -> float:
    _require_active(graph, start_id, goal_id)

    counter = itertools.count()
    heap: list[tuple[float, int, Node]] = []
    dist: dict[Node, float] = {}
    visited: set[Node] = set()

    for node in graph.start_nodes(start_id):
        dist[node] = 0.0
        heapq.heappush(heap, (0.0, next(counter), node))

    while heap:
        travelled, _, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node[0] == goal_id:
            return travelled
        for neighbor, cost, _kind in graph.neighbors(node):
            if travelled + cost < dist.get(neighbor, math.inf):
                dist[neighbor] = travelled + cost
                heapq.heappush(heap, (travelled + cost, next(counter), neighbor))

    raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")


ALGORITHMS = {
    "greedy": greedy_best_first_search,
    "astar": astar_search,
    "hill_climbing": hill_climbing_search,
    "beam": beam_search,
    "ida_star": ida_star_search,
}


def describe(graph: MetroGraph, route: Route) -> str:
    stops = [f"{graph.stations[sid].name_en} [{line}]" for sid, line in route.path]
    return " -> ".join(stops)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find a route through the Bangkok metro network.")
    parser.add_argument("start", help="origin station_id, e.g. N24")
    parser.add_argument("goal", help="destination station_id, e.g. BL01")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="operating")
    parser.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="astar")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--weight-time", type=float, default=1.0, help="seconds per second (default: 1.0)")
    parser.add_argument("--weight-cost", type=float, default=0.0, help="seconds per THB (default: 0.0)")
    parser.add_argument("--weight-transit", type=float, default=0.0, help="seconds per transfer (default: 0.0)")
    parser.add_argument("--beam-width", type=int, default=5, help="beam search only (default: 5)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    graph = MetroGraph(scenario=args.scenario, data_dir=args.data_dir)
    search = ALGORITHMS[args.algorithm]
    weights = Weights(time=args.weight_time, cost=args.weight_cost, transit=args.weight_transit)
    kwargs = {"weights": weights}
    if args.algorithm == "beam":
        kwargs["beam_width"] = args.beam_width

    try:
        route = search(graph, args.start, args.goal, **kwargs)
    except (ValueError, NoRouteFoundError) as exc:
        print(exc)
        return 1

    print(f"{route.duration_sec / 60:.1f} min | {route.fare_thb:.0f} THB | {route.transfers} transfers | {route.hops} hops")
    print(describe(graph, route))
    print(
        f"nodes expanded={route.nodes_expanded} generated={route.nodes_generated} | "
        f"runtime={route.runtime_sec * 1000:.2f} ms | peak memory={route.peak_memory_bytes / 1024:.1f} KB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
