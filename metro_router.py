from __future__ import annotations
import argparse
import csv
import heapq
import itertools
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

Node = tuple[str, str]  # (station_id, line_id)

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
    """No path exists between the requested stations under the given scenario."""


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
class Route:
    path: list[Node]
    duration_sec: float
    fare_thb: float

    @property
    def hops(self) -> int:
        return len(self.path) - 1


class MetroGraph:
    """Adjacency list over (station_id, line_id) nodes, built from the CSV dataset."""

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

        # stations that sit on >1 active line but aren't listed in transfers.csv are
        # assumed to share a paid fare zone (see datasets/README.md)
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
        """Straight-line distance at the network's fastest speed -- an admissible lower bound."""
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
        """
        Fare in THB for a path of (station, line) nodes. The journey splits into
        maximal segments by operator; each segment costs
        min(base_fare + per_station * hops, max_fare).
        """
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


def _reconstruct(came_from: dict[Node, Node], node: Node) -> list[Node]:
    path = [node]
    while node in came_from:
        node = came_from[node]
        path.append(node)
    path.reverse()
    return path


def _search(graph: MetroGraph, start_id: str, goal_id: str, *, use_g_score: bool) -> Route:
    if start_id not in graph.stations:
        raise ValueError(f"unknown station {start_id!r}")
    if goal_id not in graph.stations:
        raise ValueError(f"unknown station {goal_id!r}")

    counter = itertools.count()
    open_heap: list[tuple[float, int, Node]] = []
    came_from: dict[Node, Node] = {}
    g_score: dict[Node, float] = {}
    visited: set[Node] = set()

    for node in graph.start_nodes(start_id):
        g_score[node] = 0.0
        heapq.heappush(open_heap, (graph.heuristic(node, goal_id), next(counter), node))

    while open_heap:
        _, _, node = heapq.heappop(open_heap)
        if node in visited:
            continue
        visited.add(node)

        if node[0] == goal_id:
            path = _reconstruct(came_from, node)
            return Route(path=path, duration_sec=g_score[node], fare_thb=graph.fare_for(path))

        for neighbor, cost, _kind in graph.neighbors(node):
            if neighbor in visited:
                continue
            tentative_g = g_score[node] + cost
            if tentative_g < g_score.get(neighbor, math.inf):
                g_score[neighbor] = tentative_g
                came_from[neighbor] = node
                h = graph.heuristic(neighbor, goal_id)
                priority = tentative_g + h if use_g_score else h
                heapq.heappush(open_heap, (priority, next(counter), neighbor))

    raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")


def greedy_best_first_search(graph: MetroGraph, start_id: str, goal_id: str) -> Route:
    """f(n) = h(n) only. Fast, but not guaranteed to return the shortest route."""
    return _search(graph, start_id, goal_id, use_g_score=False)


def astar_search(graph: MetroGraph, start_id: str, goal_id: str) -> Route:
    """f(n) = g(n) + h(n). Optimal here, since MetroGraph.heuristic never overestimates."""
    return _search(graph, start_id, goal_id, use_g_score=True)


ALGORITHMS = {
    "greedy": greedy_best_first_search,
    "astar": astar_search,
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
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    graph = MetroGraph(scenario=args.scenario, data_dir=args.data_dir)
    search = ALGORITHMS[args.algorithm]

    try:
        route = search(graph, args.start, args.goal)
    except (ValueError, NoRouteFoundError) as exc:
        print(exc)
        return 1

    print(f"{route.duration_sec / 60:.1f} min | {route.fare_thb:.0f} THB | {route.hops} hops")
    print(describe(graph, route))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Example usage:
# python metro_router.py N24 BL01 --scenario operating --algorithm astar --data-dir datasets/ --verbose
