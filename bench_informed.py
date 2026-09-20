from __future__ import annotations
import argparse
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from metro_router import (
    ALGORITHMS,
    SCENARIOS,
    MetroGraph,
    NoRouteFoundError,
    Route,
    Weights,
    optimal_duration,
)

BENCHMARK_PAIRS = [
    ("N8", "BL15"),
    ("CEN", "E4"),
    ("S4", "E20"),
    ("N12", "PK03"),
    ("RN10", "A7"),
    ("N24", "BL01"),
]

REPEATS = 25


@dataclass
class Measurement:
    algorithm: str
    start_id: str
    goal_id: str
    route: Route | None
    optimal_sec: float
    median_ms: float
    error: str

    @property
    def solved(self) -> bool:
        return self.route is not None

    @property
    def optimal(self) -> bool:
        return self.route is not None and self.route.duration_sec <= self.optimal_sec + 1e-6

    @property
    def excess_pct(self) -> float:
        if self.route is None or self.optimal_sec <= 0.0:
            return 0.0
        return (self.route.duration_sec / self.optimal_sec - 1.0) * 100.0


def measure(
    graph: MetroGraph, algorithm: str, start_id: str, goal_id: str, weights: Weights, repeats: int, **kwargs: object
) -> Measurement:
    search = ALGORITHMS[algorithm]
    reference = optimal_duration(graph, start_id, goal_id)

    try:
        route = search(graph, start_id, goal_id, weights=weights, **kwargs)
    except NoRouteFoundError as exc:
        return Measurement(algorithm, start_id, goal_id, None, reference, 0.0, str(exc))

    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        search(graph, start_id, goal_id, weights=weights, trace_memory=False, **kwargs)
        samples.append((time.perf_counter() - started) * 1000.0)

    return Measurement(algorithm, start_id, goal_id, route, reference, statistics.median(samples), "")


def per_pair_table(rows: list[Measurement]) -> str:
    lines = [
        "| algorithm | pair | expanded | generated | minutes | vs optimal | THB | transfers | median ms | peak KiB |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        pair = f"{row.start_id} -> {row.goal_id}"
        if row.route is None:
            lines.append(f"| {row.algorithm} | {pair} | - | - | - | no route | - | - | - | - |")
            continue
        route = row.route
        excess = "optimal" if row.optimal else f"+{row.excess_pct:.1f}%"
        lines.append(
            f"| {row.algorithm} | {pair} | {route.nodes_expanded} | {route.nodes_generated} | "
            f"{route.duration_sec / 60:.1f} | {excess} | {route.fare_thb:.0f} | {route.transfers} | "
            f"{row.median_ms:.3f} | {route.peak_memory_bytes / 1024:.1f} |"
        )
    return "\n".join(lines)


def summary_table(rows: list[Measurement], algorithms: list[str], pairs: int) -> str:
    lines = [
        "| algorithm | solved | optimal | total expanded | total generated | worst vs optimal | total median ms | worst peak KiB |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for algorithm in algorithms:
        group = [row for row in rows if row.algorithm == algorithm]
        solved = [row for row in group if row.solved]
        if not solved:
            lines.append(f"| {algorithm} | 0/{pairs} | - | - | - | - | - | - |")
            continue
        worst = max(row.excess_pct for row in solved)
        lines.append(
            f"| {algorithm} | {len(solved)}/{pairs} | {sum(1 for row in solved if row.optimal)}/{len(solved)} | "
            f"{sum(row.route.nodes_expanded for row in solved)} | "
            f"{sum(row.route.nodes_generated for row in solved)} | "
            f"{'optimal' if worst <= 0.0 else f'+{worst:.1f}%'} | "
            f"{sum(row.median_ms for row in solved):.3f} | "
            f"{max(row.route.peak_memory_bytes for row in solved) / 1024:.1f} |"
        )
    return "\n".join(lines)


def report(graph: MetroGraph, algorithms: list[str], weights: Weights, repeats: int, beam_width: int) -> str:
    rows = []
    for algorithm in algorithms:
        kwargs = {"beam_width": beam_width} if algorithm == "beam" else {}
        for start_id, goal_id in BENCHMARK_PAIRS:
            rows.append(measure(graph, algorithm, start_id, goal_id, weights, repeats, **kwargs))

    return "\n\n".join([
        f"# Informed search benchmark — {graph.scenario}",
        f"{len(graph.stations)} stations, {graph.num_nodes} nodes, {graph.num_rail_edges} rail edges, "
        f"{len(BENCHMARK_PAIRS)} pairs, median of {repeats} runs, beam width {beam_width}",
        "## Totals",
        summary_table(rows, algorithms, len(BENCHMARK_PAIRS)),
        "## Per pair",
        per_pair_table(rows),
        "Expansion and generation counts are deterministic. Runtimes are timed with memory tracing off; "
        "peak memory comes from one separately traced run and is interpreter specific, so compare ratios "
        "rather than absolute bytes.",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the informed search strategies.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="operating")
    parser.add_argument("--algorithm", action="append", choices=sorted(ALGORITHMS), default=None)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--beam-width", type=int, default=5)
    parser.add_argument("--weight-time", type=float, default=1.0)
    parser.add_argument("--weight-cost", type=float, default=0.0)
    parser.add_argument("--weight-transit", type=float, default=0.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.repeats < 1:
        parser.error("--repeats must be >= 1")

    graph = MetroGraph(scenario=args.scenario)
    algorithms = args.algorithm or sorted(ALGORITHMS)
    weights = Weights(time=args.weight_time, cost=args.weight_cost, transit=args.weight_transit)
    text = report(graph, algorithms, weights, args.repeats, args.beam_width)

    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
