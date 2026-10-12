from __future__ import annotations
import argparse
import csv
import heapq
import itertools
import logging
import math
import threading
import time
import tracemalloc
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, TypeVar

import landmarks

logger = logging.getLogger(__name__)

Node = tuple[str, str]
_T = TypeVar("_T")
# A node plus the rail hops of its open fare segment (see _fare_step).
FareState = tuple[Node, int]

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
    map_x: float = 0.0
    map_y: float = 0.0
    label_side: str = "right"

    @classmethod
    def from_row(cls, row: dict[str, str]) -> Station:
        return cls(
            id=row["station_id"],
            name_th=row["name_th"],
            name_en=row["name_en"],
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            lines=row["lines"].split("|"),
            map_x=float(row["map_x"]),
            map_y=float(row["map_y"]),
            label_side=row["label_side"] or "right",
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


TRACE_DETAIL = ("full", "expansions")
TRACE_PHASES = ("all", "last")


@dataclass
class TraceEvent:
    kind: str
    payload: dict


class Tracer:
    """Records the step-by-step behaviour of a search so a UI can replay it.

    Tracing is opt-in: every search takes ``tracer=None`` by default and skips
    each call site, so an untraced run stays byte-for-byte the search it always
    was and benchmark timings remain comparable.

    ``max_events`` bounds the recording. IDA* re-expands the tree once per
    threshold and can generate over a million events on this network, so
    ``keep_phases="last"`` keeps every phase *summary* while discarding all but
    the final phase's detail -- you still see the whole threshold ladder, and
    you get full detail for the round that actually finds the goal.
    """

    def __init__(
        self,
        max_events: int = 20_000,
        detail: str = "full",
        keep_phases: str = "all",
        frontier_limit: int = 12,
        prune_limit: int = 20,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be >= 1")
        if detail not in TRACE_DETAIL:
            raise ValueError(f"unknown detail {detail!r}, expected one of {list(TRACE_DETAIL)}")
        if keep_phases not in TRACE_PHASES:
            raise ValueError(f"unknown keep_phases {keep_phases!r}, expected one of {list(TRACE_PHASES)}")
        if frontier_limit < 0 or prune_limit < 0:
            raise ValueError("limits must be non-negative")

        self.max_events = max_events
        self.detail = detail
        self.keep_phases = keep_phases
        self.frontier_limit = frontier_limit
        self.prune_limit = prune_limit

        self.events: list[TraceEvent] = []
        self.phases: list[dict] = []
        self.truncated = False
        self.dropped = 0
        self.expanded = 0
        self.generated = 0
        self.seeded = 0

    def _count(self, field: str) -> None:
        setattr(self, field, getattr(self, field) + 1)
        if self.phases:
            phase = self.phases[-1]
            phase[field] = phase[field] + 1

    def _append(self, kind: str, payload: dict) -> None:
        if len(self.events) >= self.max_events:
            self.truncated = True
            self.dropped += 1
            return
        self.events.append(TraceEvent(kind, payload))

    @staticmethod
    def _top(entries: list[tuple[float, Node, bool]], limit: int) -> list[dict]:
        return [
            {"node": node, "priority": priority, "stale": stale}
            for priority, node, stale in sorted(entries, key=lambda item: item[0])[:limit]
        ]

    def seed(self, entries: list[tuple[Node, float]]) -> None:
        self.seeded += len(entries)
        self._append("seed", {"nodes": [{"node": node, "h": h} for node, h in entries]})

    def expand(
        self,
        node: Node,
        g: float,
        h: float,
        f: float,
        frontier: list[tuple[float, Node, bool]] | None = None,
        depth: int | None = None,
    ) -> None:
        self._count("expanded")
        payload: dict = {"node": node, "g": g, "h": h, "f": f}
        if depth is not None:
            payload["depth"] = depth
        if frontier is not None:
            payload["frontier"] = self._top(frontier, self.frontier_limit)
            payload["frontier_size"] = len(frontier)
        self._append("expand", payload)

    def generate(
        self,
        parent: Node,
        node: Node,
        cost: float,
        kind: str,
        action: str,
        g: float | None = None,
        h: float | None = None,
        f: float | None = None,
    ) -> None:
        self._count("generated")
        if self.detail != "full":
            return
        payload: dict = {"parent": parent, "node": node, "cost": cost, "edge": kind, "action": action}
        for key, value in (("g", g), ("h", h), ("f", f)):
            if value is not None:
                payload[key] = value
        self._append("generate", payload)

    def phase(self, label: str, **info: object) -> None:
        if self.keep_phases == "last" and self.phases:
            previous = self.phases[-1]
            start = previous["_detail_start"]
            previous["events_dropped"] = len(self.events) - start
            del self.events[start:]
        index = len(self.phases)
        entry: dict = {"label": label, "index": index, "expanded": 0, "generated": 0, **info}
        self.phases.append(entry)
        self._append("phase", {"label": label, "index": index, **info})
        entry["_detail_start"] = len(self.events)

    def update_phase(self, **info: object) -> None:
        if self.phases:
            self.phases[-1].update(info)

    def prune(self, kept: list[tuple[Node, float]], dropped: list[tuple[Node, float]]) -> None:
        self._append(
            "prune",
            {
                "kept": [{"node": node, "f": score} for node, score in kept],
                "dropped": [{"node": node, "f": score} for node, score in dropped[: self.prune_limit]],
                "dropped_total": len(dropped),
            },
        )

    def backtrack(self, node: Node, reason: str, f: float, limit: float) -> None:
        self._append("backtrack", {"node": node, "reason": reason, "f": f, "limit": limit})

    def stuck(self, node: Node, reason: str, h_current: float, h_best: float | None) -> None:
        payload: dict = {"node": node, "reason": reason, "h": h_current}
        if h_best is not None:
            payload["h_best"] = h_best
        self._append("stuck", payload)

    def solution(self, path: list[Node], duration_sec: float) -> None:
        self._append("solution", {"path": list(path), "duration_sec": duration_sec})

    @classmethod
    def _json_safe(cls, value: object) -> object:
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, dict):
            return {key: cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value

    def to_dict(self) -> dict:
        return {
            "events": [self._json_safe({"kind": event.kind, **event.payload}) for event in self.events],
            "phases": [
                self._json_safe({k: v for k, v in phase.items() if not k.startswith("_")})
                for phase in self.phases
            ],
            "truncated": self.truncated,
            "dropped": self.dropped,
            "detail": self.detail,
            "keep_phases": self.keep_phases,
            "max_events": self.max_events,
            "counts": {"expanded": self.expanded, "generated": self.generated, "seeded": self.seeded},
        }


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

        # ALT landmark tables, one per (weighting, landmark count). The table
        # *is* the metric -- its distances are exact shortest paths under one
        # Weights -- so a new weighting needs a new table. Guarded by a lock
        # because the server shares one graph across request threads and this
        # is the only mutable state on an otherwise read-only object.
        self._landmark_tables: dict[tuple[Weights, int], landmarks.LandmarkTable] = {}
        self._landmark_lock = threading.Lock()

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

    def edge_weight_fn(self, weights: Weights) -> Callable[[Node, Node, float, str], float]:
        """The weighted cost of one arc, as a plain function of the arc.

        Hands `landmarks` the exact metric the search minimises without telling
        it anything about trains, so its Dijkstras and the search agree by
        construction rather than by two places being kept in step.
        """
        return lambda node, neighbor, cost_sec, kind: _edge_weight(
            self, node, neighbor, cost_sec, kind, weights
        )

    def landmark_table(self, weights: Weights = Weights(),
                       count: int = landmarks.DEFAULT_LANDMARKS) -> landmarks.LandmarkTable:
        """Landmark distances for this weighting, computed once and cached.

        Preprocessing is 2k Dijkstras over the node graph -- tens of
        milliseconds here, and amortised over every later query at the same
        weights. Benchmarks must say which side of that line they are
        measuring; `bench_informed` warms the table before timing, and
        `landmark_preprocess_seconds` reports the build cost on its own.
        """
        key = (weights, count)
        table = self._landmark_tables.get(key)
        if table is None:
            with self._landmark_lock:
                table = self._landmark_tables.get(key)
                if table is None:
                    table = landmarks.build_table(
                        self.adjacency, self.edge_weight_fn(weights), count
                    )
                    self._landmark_tables[key] = table
                    logger.info(
                        "built %d landmarks for %s (%s): %s",
                        len(table), self.scenario,
                        "symmetric" if table.symmetric else "directed",
                        ", ".join(f"{sid}/{line}" for sid, line in table.landmarks),
                    )
        return table

    def landmark_heuristic(self, goal_id: str, weights: Weights = Weights(),
                           count: int = landmarks.DEFAULT_LANDMARKS,
                           geographic: bool = True) -> Callable[[Node], float]:
        """h(n) for one goal station: the ALT bound, raised by the geographic one.

        Both bounds are admissible and consistent, so their maximum is too --
        and it is never worse than either alone, which is why `astar_landmark`
        takes the max rather than replacing one with the other. ALT dominates
        almost everywhere; the geographic term still wins near the goal, where
        every landmark is far away and its triangle inequality goes slack.

        Returns a closure so the per-goal constants are folded in once instead
        of once per expansion.
        """
        bound = landmarks.GoalBound.build(
            self.landmark_table(weights, count), self.start_nodes(goal_id)
        )
        if not geographic:
            return bound
        scale = weights.time
        if scale <= 0.0:
            return bound

        def combined(node: Node) -> float:
            return max(bound(node), scale * self.heuristic(node, goal_id))

        return combined

    def landmark_preprocess_seconds(self, weights: Weights = Weights(),
                                    count: int = landmarks.DEFAULT_LANDMARKS) -> float:
        """Wall time to build this weighting's table from cold. For the report:
        ALT's expansion savings are only free once this has been paid."""
        self._landmark_tables.pop((weights, count), None)
        started = time.perf_counter()
        self.landmark_table(weights, count)
        return time.perf_counter() - started

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


def _reconstruct(came_from: dict[_T, _T], node: _T) -> list[_T]:
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


def _segment_fare(fare: dict[str, float], hops: int) -> float:
    if hops == 0:
        return 0.0
    return min(fare["base_fare"] + fare["per_station"] * hops, fare["max_fare"])


def _fare_step(graph: MetroGraph, node: Node, neighbor: Node, kind: str, segment_hops: int) -> tuple[float, int]:
    """Baht one edge adds to the trip, exactly as MetroGraph.fare_for charges it.

    segment_hops counts the rail hops of the open same-operator segment (0 = none open).
    The base fare lands on a segment's first hop and the max_fare cap is honoured, so
    the sum of steps along any path equals fare_for(path). Once a segment hits its cap
    the count stops growing, which keeps the (node, segment_hops) state space finite.
    """
    operator = graph.lines[neighbor[1]]["operator"]
    if kind != "rail":
        same_operator = graph.lines[node[1]]["operator"] == operator
        return 0.0, segment_hops if same_operator else 0
    fare = graph.fares[operator]
    added = _segment_fare(fare, segment_hops + 1) - _segment_fare(fare, segment_hops)
    return added, segment_hops + 1 if added > 0.0 else segment_hops


def _search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    use_g_score: bool,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
    heuristic: Callable[[Node], float] | None = None,
) -> Route:
    _require_active(graph, start_id, goal_id)

    # Default: the geographic bound, scaled into the weighted metric the search
    # minimises. `astar_landmark` passes the ALT bound instead. Everything below
    # is the same algorithm either way -- which is the point of the comparison:
    # only h changes, so any difference in expansions is the heuristic's doing.
    if heuristic is None:
        def heuristic(node: Node) -> float:
            return weights.time * graph.heuristic(node, goal_id)

    counter = itertools.count()
    open_heap: list[tuple[float, int, Node]] = []
    came_from: dict[Node, Node] = {}
    g_score: dict[Node, float] = {}
    time_score: dict[Node, float] = {}
    visited: set[Node] = set()
    path: list[Node] | None = None
    duration_sec = 0.0

    with _Meter(trace_memory) as meter:
        seeds: list[tuple[Node, float]] = []
        for node in graph.start_nodes(start_id):
            g_score[node] = 0.0
            time_score[node] = 0.0
            meter.generated += 1
            start_h = heuristic(node)
            heapq.heappush(open_heap, (start_h, next(counter), node))
            seeds.append((node, start_h))
        if tracer is not None:
            tracer.seed(seeds)

        while open_heap:
            _, _, node = heapq.heappop(open_heap)
            if node in visited:
                continue
            visited.add(node)
            meter.expanded += 1

            if tracer is not None:
                here_h = heuristic(node)
                tracer.expand(
                    node,
                    g_score[node],
                    here_h,
                    g_score[node] + here_h if use_g_score else here_h,
                    frontier=[(priority, other, other in visited) for priority, _c, other in open_heap],
                )

            if node[0] == goal_id:
                path = _reconstruct(came_from, node)
                duration_sec = time_score[node]
                if tracer is not None:
                    tracer.solution(path, duration_sec)
                break

            for neighbor, cost, kind in graph.neighbors(node):
                meter.generated += 1
                if neighbor in visited:
                    if tracer is not None:
                        tracer.generate(node, neighbor, cost, kind, "skip_visited")
                    continue
                tentative_g = g_score[node] + _edge_weight(graph, node, neighbor, cost, kind, weights)
                if use_g_score:
                    if tentative_g >= g_score.get(neighbor, math.inf):
                        if tracer is not None:
                            tracer.generate(node, neighbor, cost, kind, "skip_worse", g=tentative_g)
                        continue
                elif neighbor in g_score:
                    if tracer is not None:
                        tracer.generate(node, neighbor, cost, kind, "skip_seen", g=tentative_g)
                    continue
                g_score[neighbor] = tentative_g
                time_score[neighbor] = time_score[node] + cost
                came_from[neighbor] = node
                h = heuristic(neighbor)
                heapq.heappush(open_heap, (tentative_g + h if use_g_score else h, next(counter), neighbor))
                if tracer is not None:
                    tracer.generate(
                        node, neighbor, cost, kind, "improved",
                        g=tentative_g, h=h, f=tentative_g + h if use_g_score else h,
                    )

    if path is None:
        raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")
    return _route(graph, path, duration_sec, meter)


def greedy_best_first_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
) -> Route:
    return _search(
        graph, start_id, goal_id,
        use_g_score=False, weights=weights, trace_memory=trace_memory, tracer=tracer,
    )


def astar_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
) -> Route:
    return _search(
        graph, start_id, goal_id,
        use_g_score=True, weights=weights, trace_memory=trace_memory, tracer=tracer,
    )


def astar_landmark_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
    landmark_count: int = landmarks.DEFAULT_LANDMARKS,
) -> Route:
    """A* on the ALT landmark heuristic (Goldberg & Harrelson, 2005).

    Identical to `astar_search` in every respect but h. Where plain A* bounds
    the remaining trip by a straight line at 45 km/h, this bounds it by exact
    shortest-path distances measured from a handful of landmarks at the ends of
    the network, and takes the best of that and the geographic bound.

    Still optimal: the landmark bound is admissible *and* consistent -- it is a
    maximum of differences of exact shortest paths, each of which obeys the
    triangle inequality -- so the first time the goal leaves the queue the route
    is the cheapest one, exactly as for A*. `test_landmarks.py` asserts
    consistency on every edge and agreement with the Dijkstra oracle.

    The trade is preprocessing: 2k Dijkstras per weighting, cached on the graph.
    See `MetroGraph.landmark_preprocess_seconds` for that cost measured on its
    own, and the README for when it does and does not pay for itself.
    """
    # Validate before preprocessing. `_search` does this too, but the heuristic
    # has to be built first to be passed in, and an inactive goal station has no
    # platforms to bound against -- so without this the caller would get an
    # obscure error from the landmark code instead of StationNotActiveError.
    _require_active(graph, start_id, goal_id)
    return _search(
        graph, start_id, goal_id,
        use_g_score=True, weights=weights, trace_memory=trace_memory, tracer=tracer,
        heuristic=graph.landmark_heuristic(goal_id, weights, landmark_count),
    )


def hill_climbing_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
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
        if tracer is not None:
            tracer.seed([(node, h(node)) for node in starts])
        while True:
            meter.expanded += 1
            if tracer is not None:
                tracer.expand(current, duration_sec, h(current), h(current), frontier=[], depth=len(path) - 1)
            if current[0] == goal_id:
                if tracer is not None:
                    tracer.solution(path, duration_sec)
                break

            candidates: list[tuple[Node, float]] = []
            for neighbor, cost, kind in graph.neighbors(current):
                meter.generated += 1
                if neighbor not in visited:
                    candidates.append((neighbor, cost))
                    if tracer is not None:
                        tracer.generate(current, neighbor, cost, kind, "candidate", h=h(neighbor))
                elif tracer is not None:
                    tracer.generate(current, neighbor, cost, kind, "skip_visited")

            if not candidates:
                stuck = "dead end"
                if tracer is not None:
                    tracer.stuck(current, stuck, h(current), None)
                break

            best_node, best_cost = min(candidates, key=lambda nc: h(nc[0]))
            if h(best_node) > h(current):
                stuck = "local optimum"
                if tracer is not None:
                    tracer.stuck(current, stuck, h(current), h(best_node))
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
    tracer: Tracer | None = None,
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
        if tracer is not None:
            tracer.seed([(entry[2][-1], h(entry[2][-1])) for entry in beam])
        for round_index in range(max_rounds):
            if tracer is not None:
                tracer.phase("beam_round", round=round_index, beam_size=len(beam))
            found = best_at_goal(beam)
            if found is not None:
                meter.expanded += 1
                if tracer is not None:
                    goal_node = found[2][-1]
                    tracer.expand(goal_node, found[0], h(goal_node), found[0] + h(goal_node), frontier=[])
                    tracer.solution(found[2], found[1])
                break

            candidates: list[tuple[float, float, list[Node]]] = []
            for weighted_g, time_g, path in beam:
                node = path[-1]
                in_path = set(path)
                meter.expanded += 1
                if tracer is not None:
                    tracer.expand(
                        node, weighted_g, h(node), weighted_g + h(node),
                        frontier=[(g_b + h(p_b[-1]), p_b[-1], False) for g_b, _t_b, p_b in beam],
                        depth=len(path) - 1,
                    )
                for neighbor, cost, kind in graph.neighbors(node):
                    meter.generated += 1
                    if neighbor in in_path:
                        if tracer is not None:
                            tracer.generate(node, neighbor, cost, kind, "skip_on_path")
                        continue
                    new_weighted_g = weighted_g + _edge_weight(graph, node, neighbor, cost, kind, weights)
                    candidates.append((new_weighted_g, time_g + cost, path + [neighbor]))
                    if tracer is not None:
                        tracer.generate(
                            node, neighbor, cost, kind, "improved",
                            g=new_weighted_g, h=h(neighbor), f=new_weighted_g + h(neighbor),
                        )

            if not candidates:
                break

            candidates.sort(key=lambda entry: entry[0] + h(entry[2][-1]))
            if tracer is not None:
                tracer.prune(
                    [(entry[2][-1], entry[0] + h(entry[2][-1])) for entry in candidates[:beam_width]],
                    [(entry[2][-1], entry[0] + h(entry[2][-1])) for entry in candidates[beam_width:]],
                )
            beam = candidates[:beam_width]
        else:
            found = best_at_goal(beam)
            if tracer is not None and found is not None:
                tracer.solution(found[2], found[1])

    if found is None:
        raise NoRouteFoundError(
            f"beam search exhausted the beam (width={beam_width}) en route {start_id!r} -> {goal_id!r}"
        )
    return _route(graph, found[2], found[1], meter)


def ida_star_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
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
        if tracer is not None:
            tracer.seed([(node, h(node)) for node in starts])

        def dfs(path: list[Node], on_path: set[Node], g: float, time_g: float, limit: float) -> tuple[bool, float]:
            node = path[-1]
            f = g + h(node)
            if f > limit:
                if tracer is not None:
                    tracer.backtrack(node, "over_threshold", f, limit)
                return False, f
            meter.expanded += 1
            if tracer is not None:
                tracer.expand(node, g, h(node), f, frontier=[], depth=len(path) - 1)
            if node[0] == goal_id:
                if tracer is not None:
                    tracer.solution(path, time_g)
                return True, time_g

            next_limit = math.inf
            for neighbor, cost, kind in graph.neighbors(node):
                meter.generated += 1
                if neighbor in on_path:
                    if tracer is not None:
                        tracer.generate(node, neighbor, cost, kind, "skip_on_path")
                    continue
                path.append(neighbor)
                on_path.add(neighbor)
                edge_g = g + _edge_weight(graph, node, neighbor, cost, kind, weights)
                if tracer is not None:
                    tracer.generate(
                        node, neighbor, cost, kind, "improved",
                        g=edge_g, h=h(neighbor), f=edge_g + h(neighbor),
                    )
                found, value = dfs(path, on_path, edge_g, time_g + cost, limit)
                if found:
                    return True, value
                path.pop()
                on_path.discard(neighbor)
                if value < next_limit:
                    next_limit = value
            if tracer is not None:
                tracer.backtrack(node, "exhausted", f, limit)
            return False, next_limit

        threshold = min(h(node) for node in starts)
        for round_index in range(max_rounds):
            if tracer is not None:
                tracer.phase("threshold", round=round_index, threshold=threshold)
            next_threshold = math.inf
            for root in starts:
                path = [root]
                found, value = dfs(path, {root}, 0.0, 0.0, threshold)
                if found:
                    solution = (path, value)
                    break
                next_threshold = min(next_threshold, value)

            if solution is not None:
                if tracer is not None:
                    tracer.update_phase(solved=True)
                break
            if next_threshold == math.inf:
                exhausted = True
                if tracer is not None:
                    tracer.update_phase(exhausted=True)
                break
            if tracer is not None:
                tracer.update_phase(next_threshold=next_threshold)
            threshold = next_threshold

    if solution is None:
        if exhausted:
            raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")
        raise NoRouteFoundError(
            f"IDA* did not converge within {max_rounds} rounds en route {start_id!r} -> {goal_id!r}"
        )
    return _route(graph, solution[0], solution[1], meter)


def breadth_first_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
) -> Route:
    del weights
    _require_active(graph, start_id, goal_id)

    frontier: deque[Node] = deque()
    came_from: dict[Node, Node] = {}
    time_score: dict[Node, float] = {}
    reached: set[Node] = set()
    path: list[Node] | None = None
    duration_sec = 0.0

    with _Meter(trace_memory) as meter:
        seeds: list[tuple[Node, float]] = []
        for node in graph.start_nodes(start_id):
            meter.generated += 1
            frontier.append(node)
            reached.add(node)
            time_score[node] = 0.0
            seeds.append((node, 0.0))
        if tracer is not None:
            tracer.seed(seeds)

        # Early goal test (AIMA): a node is checked when it is generated, not when it
        # is expanded. BFS is still hop-optimal, and the goal layer is never expanded.
        goal = next((node for node in frontier if node[0] == goal_id), None)

        while frontier and goal is None:
            node = frontier.popleft()
            meter.expanded += 1

            if tracer is not None:
                depth = len(_reconstruct(came_from, node)) - 1
                tracer.expand(
                    node, time_score[node], 0.0, float(depth),
                    frontier=[(float(i), other, False) for i, other in enumerate(frontier)],
                    depth=depth,
                )

            for neighbor, cost, kind in graph.neighbors(node):
                meter.generated += 1
                if neighbor in reached:
                    if tracer is not None:
                        tracer.generate(node, neighbor, cost, kind, "skip_seen")
                    continue
                reached.add(neighbor)
                came_from[neighbor] = node
                time_score[neighbor] = time_score[node] + cost
                frontier.append(neighbor)
                if tracer is not None:
                    tracer.generate(node, neighbor, cost, kind, "improved", g=time_score[neighbor], h=0.0)
                if neighbor[0] == goal_id:
                    goal = neighbor
                    break

        if goal is not None:
            path = _reconstruct(came_from, goal)
            duration_sec = time_score[goal]
            if tracer is not None:
                tracer.solution(path, duration_sec)

    if path is None:
        raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")
    return _route(graph, path, duration_sec, meter)


def depth_first_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
) -> Route:
    del weights
    _require_active(graph, start_id, goal_id)

    stack: list[tuple[Node, Node | None, float]] = []
    came_from: dict[Node, Node] = {}
    expanded: set[Node] = set()
    path: list[Node] | None = None
    duration_sec = 0.0

    with _Meter(trace_memory) as meter:
        starts = graph.start_nodes(start_id)
        for node in reversed(starts):
            meter.generated += 1
            stack.append((node, None, 0.0))
        if tracer is not None:
            tracer.seed([(node, 0.0) for node in starts])

        while stack:
            node, parent, time_g = stack.pop()
            if node in expanded:
                continue  
            expanded.add(node)
            if parent is not None:
                came_from[node] = parent
            meter.expanded += 1

            if tracer is not None:
                depth = len(_reconstruct(came_from, node)) - 1
                tracer.expand(
                    node, time_g, 0.0, float(depth),
                    frontier=[
                        (float(i), other, other in expanded)
                        for i, (other, _p, _t) in enumerate(reversed(stack))
                    ],
                    depth=depth,
                )

            if node[0] == goal_id:
                path = _reconstruct(came_from, node)
                duration_sec = time_g
                if tracer is not None:
                    tracer.solution(path, duration_sec)
                break

            for neighbor, cost, kind in reversed(graph.neighbors(node)):
                meter.generated += 1
                if neighbor in expanded:
                    if tracer is not None:
                        tracer.generate(node, neighbor, cost, kind, "skip_visited")
                    continue
                stack.append((neighbor, node, time_g + cost))
                if tracer is not None:
                    tracer.generate(node, neighbor, cost, kind, "improved", g=time_g + cost, h=0.0)

    if path is None:
        raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")
    return _route(graph, path, duration_sec, meter)


def uniform_cost_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
) -> Route:

    _require_active(graph, start_id, goal_id)

    # Fares are not additive per edge (base fare per segment, max_fare cap), so when
    # cost carries weight the search state also tracks the open fare segment. With
    # cost weighted at zero the extra component stays 0 and this is plain Dijkstra.
    track_fare = weights.cost > 0.0

    counter = itertools.count()
    open_heap: list[tuple[float, int, FareState]] = []
    came_from: dict[FareState, FareState] = {}
    g_score: dict[FareState, float] = {}
    time_score: dict[FareState, float] = {}
    visited: set[FareState] = set()
    path: list[Node] | None = None
    duration_sec = 0.0

    with _Meter(trace_memory) as meter:
        seeds: list[tuple[Node, float]] = []
        for node in graph.start_nodes(start_id):
            state = (node, 0)
            g_score[state] = 0.0
            time_score[state] = 0.0
            meter.generated += 1
            heapq.heappush(open_heap, (0.0, next(counter), state))
            seeds.append((node, 0.0))
        if tracer is not None:
            tracer.seed(seeds)

        while open_heap:
            _, _, state = heapq.heappop(open_heap)
            if state in visited:
                continue
            visited.add(state)
            meter.expanded += 1
            node, segment_hops = state

            if tracer is not None:
                tracer.expand(
                    node, g_score[state], 0.0, g_score[state],
                    frontier=[(priority, other[0], other in visited) for priority, _c, other in open_heap],
                )

            if node[0] == goal_id:
                path = [node for node, _hops in _reconstruct(came_from, state)]
                duration_sec = time_score[state]
                if tracer is not None:
                    tracer.solution(path, duration_sec)
                break

            for neighbor, cost, kind in graph.neighbors(node):
                meter.generated += 1
                fare_added, next_hops = _fare_step(graph, node, neighbor, kind, segment_hops) if track_fare else (0.0, 0)
                next_state = (neighbor, next_hops)
                if next_state in visited:
                    if tracer is not None:
                        tracer.generate(node, neighbor, cost, kind, "skip_visited")
                    continue
                step = weights.time * cost + weights.cost * fare_added + weights.transit * (kind != "rail")
                tentative_g = g_score[state] + step
                if tentative_g >= g_score.get(next_state, math.inf):
                    if tracer is not None:
                        tracer.generate(node, neighbor, cost, kind, "skip_worse", g=tentative_g)
                    continue
                g_score[next_state] = tentative_g
                time_score[next_state] = time_score[state] + cost
                came_from[next_state] = state
                heapq.heappush(open_heap, (tentative_g, next(counter), next_state))
                if tracer is not None:
                    tracer.generate(node, neighbor, cost, kind, "improved", g=tentative_g, h=0.0, f=tentative_g)

    if path is None:
        raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")
    return _route(graph, path, duration_sec, meter)


DEFAULT_DEPTH_LIMIT = 30


def _depth_limited(
    graph: MetroGraph,
    starts: list[Node],
    goal_id: str,
    limit: int,
    meter: _Meter,
    tracer: Tracer | None,
) -> tuple[list[Node] | None, float, bool]:

    shallowest: dict[Node, int] = {}
    cutoff = False

    def dls(path: list[Node], depth: int, time_g: float) -> tuple[list[Node] | None, float]:
        nonlocal cutoff
        node = path[-1]
        shallowest[node] = depth
        meter.expanded += 1
        if tracer is not None:
            tracer.expand(node, time_g, 0.0, float(depth), frontier=[], depth=depth)

        if node[0] == goal_id:
            if tracer is not None:
                tracer.solution(path, time_g)
            return list(path), time_g

        if depth == limit:
            cutoff = True
            if tracer is not None:
                tracer.backtrack(node, "depth_limit", float(depth), float(limit))
            return None, 0.0

        for neighbor, cost, kind in graph.neighbors(node):
            meter.generated += 1
            if shallowest.get(neighbor, limit + 1) <= depth + 1:
                if tracer is not None:
                    tracer.generate(node, neighbor, cost, kind, "skip_seen")
                continue
            if tracer is not None:
                tracer.generate(node, neighbor, cost, kind, "improved", g=time_g + cost, h=0.0)
            path.append(neighbor)
            found, seconds = dls(path, depth + 1, time_g + cost)
            path.pop()
            if found is not None:
                return found, seconds

        if tracer is not None:
            tracer.backtrack(node, "exhausted", float(depth), float(limit))
        return None, 0.0

    for root in starts:
        if shallowest.get(root, limit + 1) <= 0:
            continue
        found, seconds = dls([root], 0, 0.0)
        if found is not None:
            return found, seconds, cutoff
    return None, 0.0, cutoff


def depth_limited_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    depth_limit: int = DEFAULT_DEPTH_LIMIT,
    trace_memory: bool = True,
    tracer: Tracer | None = None,
) -> Route:

    del weights
    if depth_limit < 0:
        raise ValueError("depth_limit must be >= 0")
    _require_active(graph, start_id, goal_id)

    starts = graph.start_nodes(start_id)
    with _Meter(trace_memory) as meter:
        meter.generated += len(starts)
        if tracer is not None:
            tracer.seed([(node, 0.0) for node in starts])
            tracer.phase("depth_limit", round=0, limit=depth_limit)
        path, duration_sec, cutoff = _depth_limited(graph, starts, goal_id, depth_limit, meter, tracer)

    if path is None:
        if cutoff:
            raise NoRouteFoundError(
                f"no route within {depth_limit} hops from {start_id!r} to {goal_id!r} (depth limit reached)"
            )
        raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")
    return _route(graph, path, duration_sec, meter)


def iterative_deepening_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
) -> Route:

    del weights
    _require_active(graph, start_id, goal_id)

    starts = graph.start_nodes(start_id)
    path: list[Node] | None = None
    duration_sec = 0.0

    with _Meter(trace_memory) as meter:
        meter.generated += len(starts)
        if tracer is not None:
            tracer.seed([(node, 0.0) for node in starts])

        for limit in range(graph.num_nodes + 1):
            if tracer is not None:
                tracer.phase("depth_limit", round=limit, limit=limit)
            path, duration_sec, cutoff = _depth_limited(graph, starts, goal_id, limit, meter, tracer)
            if path is not None:
                if tracer is not None:
                    tracer.update_phase(solved=True)
                break
            if not cutoff:
                if tracer is not None:
                    tracer.update_phase(exhausted=True)
                break

    if path is None:
        raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")
    return _route(graph, path, duration_sec, meter)


def bidirectional_search(
    graph: MetroGraph,
    start_id: str,
    goal_id: str,
    *,
    weights: Weights = Weights(),
    trace_memory: bool = True,
    tracer: Tracer | None = None,
) -> Route:

    del weights
    _require_active(graph, start_id, goal_id)

    starts = graph.start_nodes(start_id)
    goals = graph.start_nodes(goal_id)
    fwd_parent: dict[Node, Node | None] = {node: None for node in starts}
    bwd_parent: dict[Node, Node | None] = {node: None for node in goals}
    fwd_time: dict[Node, float] = {node: 0.0 for node in starts}
    bwd_time: dict[Node, float] = {node: 0.0 for node in goals}
    fwd_depth: dict[Node, int] = {node: 0 for node in starts}
    bwd_depth: dict[Node, int] = {node: 0 for node in goals}
    fwd_frontier: list[Node] = list(starts)
    bwd_frontier: list[Node] = list(goals)
    path: list[Node] = []
    duration_sec = 0.0
    meet: Node | None = None

    def chain(parents: dict[Node, Node | None], node: Node) -> list[Node]:
        out = [node]
        parent = parents[node]
        while parent is not None:
            out.append(parent)
            parent = parents[parent]
        return out

    with _Meter(trace_memory) as meter:
        meter.generated += len(starts) + len(goals)
        if tracer is not None:
            tracer.seed([(node, 0.0) for node in starts] + [(node, 0.0) for node in goals])

        common = [node for node in starts if node in bwd_parent]
        if common:
            meet = common[0]

        round_index = 0
        while meet is None and fwd_frontier and bwd_frontier:
            forward = len(fwd_frontier) <= len(bwd_frontier)
            if forward:
                frontier, parent, times, depths = fwd_frontier, fwd_parent, fwd_time, fwd_depth
                other_parent, other_depth = bwd_parent, bwd_depth
            else:
                frontier, parent, times, depths = bwd_frontier, bwd_parent, bwd_time, bwd_depth
                other_parent, other_depth = fwd_parent, fwd_depth
            if tracer is not None:
                tracer.phase(
                    "forward" if forward else "backward",
                    round=round_index, frontier_forward=len(fwd_frontier), frontier_backward=len(bwd_frontier),
                )

            next_layer: list[Node] = []
            meetings: list[tuple[int, Node]] = []
            for position, node in enumerate(frontier):
                meter.expanded += 1
                if tracer is not None:
                    tracer.expand(
                        node, times[node], 0.0, float(depths[node]),
                        frontier=[(float(i), other, False) for i, other in enumerate(frontier[position + 1:])],
                        depth=depths[node],
                    )
                for neighbor, cost, kind in graph.neighbors(node):
                    meter.generated += 1
                    if neighbor in parent:
                        if tracer is not None:
                            tracer.generate(node, neighbor, cost, kind, "skip_seen")
                        continue
                    parent[neighbor] = node
                    times[neighbor] = times[node] + cost
                    depths[neighbor] = depths[node] + 1
                    next_layer.append(neighbor)
                    if tracer is not None:
                        tracer.generate(node, neighbor, cost, kind, "improved", g=times[neighbor], h=0.0)
                    if neighbor in other_parent:
                        meetings.append((depths[neighbor] + other_depth[neighbor], neighbor))

            if forward:
                fwd_frontier = next_layer
            else:
                bwd_frontier = next_layer
            if meetings:
                meet = min(meetings, key=lambda item: item[0])[1]
            round_index += 1

        if meet is not None:
            path = list(reversed(chain(fwd_parent, meet))) + chain(bwd_parent, meet)[1:]
            duration_sec = fwd_time[meet] + bwd_time[meet]
            if tracer is not None:
                tracer.solution(path, duration_sec)

    if meet is None:
        raise NoRouteFoundError(f"no route from {start_id!r} to {goal_id!r} in scenario {graph.scenario!r}")
    return _route(graph, path, duration_sec, meter)


def search_kwargs(algorithm: str, *, beam_width: int = 5, depth_limit: int = DEFAULT_DEPTH_LIMIT,
                  landmark_count: int = landmarks.DEFAULT_LANDMARKS) -> dict:
    if algorithm == "beam":
        return {"beam_width": beam_width}
    if algorithm == "dls":
        return {"depth_limit": depth_limit}
    if algorithm == "astar_landmark":
        return {"landmark_count": landmark_count}
    return {}


ALGORITHM_PARAMS = {"beam": ["beam_width"], "dls": ["depth_limit"]}


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
    "bfs": breadth_first_search,
    "dfs": depth_first_search,
    "ucs": uniform_cost_search,
    "dls": depth_limited_search,
    "ids": iterative_deepening_search,
    "bidirectional": bidirectional_search,
    "greedy": greedy_best_first_search,
    "astar": astar_search,
    "astar_landmark": astar_landmark_search,
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
    parser.add_argument(
        "--depth-limit", type=int, default=DEFAULT_DEPTH_LIMIT,
        help=f"depth-limited search only, in hops (default: {DEFAULT_DEPTH_LIMIT})",
    )
    parser.add_argument(
        "--landmarks", type=int, default=landmarks.DEFAULT_LANDMARKS,
        help=f"astar_landmark only: how many landmarks (default: {landmarks.DEFAULT_LANDMARKS})",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    graph = MetroGraph(scenario=args.scenario, data_dir=args.data_dir)
    search = ALGORITHMS[args.algorithm]
    weights = Weights(time=args.weight_time, cost=args.weight_cost, transit=args.weight_transit)
    kwargs = {
        "weights": weights,
        **search_kwargs(args.algorithm, beam_width=args.beam_width,
                        depth_limit=args.depth_limit, landmark_count=args.landmarks),
    }

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
