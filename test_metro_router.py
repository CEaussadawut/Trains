from __future__ import annotations
import random
import tracemalloc
import unittest

from metro_router import (
    ALGORITHMS,
    SCENARIOS,
    MetroGraph,
    NoRouteFoundError,
    Route,
    StationNotActiveError,
    Weights,
    astar_search,
    beam_search,
    greedy_best_first_search,
    hill_climbing_search,
    ida_star_search,
    optimal_duration,
)

TOLERANCE = 1e-6

GRAPHS = {scenario: MetroGraph(scenario=scenario) for scenario in SCENARIOS}


def active_stations(graph: MetroGraph) -> list[str]:
    return sorted(station for station in graph.stations if graph.active_station_lines(station))


def sample_pairs(graph: MetroGraph, count: int, seed: int) -> list[tuple[str, str]]:
    stations = active_stations(graph)
    rng = random.Random(seed)
    pairs = []
    while len(pairs) < count:
        start_id, goal_id = rng.choice(stations), rng.choice(stations)
        if start_id != goal_id:
            pairs.append((start_id, goal_id))
    return pairs


def walk_duration(graph: MetroGraph, route: Route) -> float:
    total = 0.0
    for node, following in zip(route.path, route.path[1:]):
        costs = [cost for neighbor, cost, _kind in graph.neighbors(node) if neighbor == following]
        if not costs:
            raise AssertionError(f"{node!r} -> {following!r} is not an edge")
        total += min(costs)
    return total


class HeuristicTest(unittest.TestCase):
    def test_zero_at_the_goal(self) -> None:
        graph = GRAPHS["operating"]
        for station_id in active_stations(graph):
            for node in graph.start_nodes(station_id):
                self.assertAlmostEqual(graph.heuristic(node, station_id), 0.0)

    def test_never_overestimates_the_true_remaining_time(self) -> None:
        graph = GRAPHS["operating"]
        stations = active_stations(graph)
        checked = 0
        for goal_id in stations:
            for start_id in stations:
                if start_id == goal_id:
                    continue
                remaining = optimal_duration(graph, start_id, goal_id)
                for node in graph.start_nodes(start_id):
                    self.assertLessEqual(graph.heuristic(node, goal_id), remaining + TOLERANCE)
                    checked += 1
        self.assertGreater(checked, 37_000)

    def test_is_consistent_on_every_edge_of_every_scenario(self) -> None:
        for scenario, graph in GRAPHS.items():
            checked = 0
            for goal_id in active_stations(graph):
                for node, edges in graph.adjacency.items():
                    here = graph.heuristic(node, goal_id)
                    for neighbor, cost, _kind in edges:
                        self.assertLessEqual(
                            here,
                            cost + graph.heuristic(neighbor, goal_id) + TOLERANCE,
                            f"{scenario}: h({node}) > cost + h({neighbor}) for goal {goal_id}",
                        )
                        checked += 1
            self.assertGreater(checked, 80_000)


class OptimalityTest(unittest.TestCase):
    def test_astar_matches_the_dijkstra_reference(self) -> None:
        graph = GRAPHS["operating"]
        for start_id, goal_id in sample_pairs(graph, 120, seed=1):
            route = astar_search(graph, start_id, goal_id, trace_memory=False)
            self.assertAlmostEqual(route.duration_sec, optimal_duration(graph, start_id, goal_id), delta=TOLERANCE)

    def test_ida_star_matches_the_dijkstra_reference(self) -> None:
        graph = GRAPHS["operating"]
        for start_id, goal_id in sample_pairs(graph, 20, seed=2):
            route = ida_star_search(graph, start_id, goal_id, trace_memory=False)
            self.assertAlmostEqual(route.duration_sec, optimal_duration(graph, start_id, goal_id), delta=TOLERANCE)

    def test_astar_expands_fewer_nodes_than_it_generates(self) -> None:
        graph = GRAPHS["operating"]
        for start_id, goal_id in sample_pairs(graph, 30, seed=3):
            route = astar_search(graph, start_id, goal_id, trace_memory=False)
            self.assertLessEqual(route.nodes_expanded, route.nodes_generated)

    def test_greedy_is_cheaper_to_run_and_sometimes_wrong(self) -> None:
        graph = GRAPHS["operating"]
        greedy = greedy_best_first_search(graph, "N24", "BL01", trace_memory=False)
        astar = astar_search(graph, "N24", "BL01", trace_memory=False)
        self.assertLess(greedy.nodes_expanded, astar.nodes_expanded)
        self.assertGreater(greedy.duration_sec, astar.duration_sec + TOLERANCE)

    def test_a_wider_beam_is_never_worse(self) -> None:
        graph = GRAPHS["operating"]
        improved = 0
        for start_id, goal_id in sample_pairs(graph, 20, seed=8):
            wide = beam_search(graph, start_id, goal_id, beam_width=20, trace_memory=False)
            try:
                narrow = beam_search(graph, start_id, goal_id, beam_width=1, trace_memory=False)
            except NoRouteFoundError:
                improved += 1
                continue
            self.assertLessEqual(wide.duration_sec, narrow.duration_sec + TOLERANCE)
            if wide.duration_sec < narrow.duration_sec - TOLERANCE:
                improved += 1
        self.assertGreater(improved, 0)

    def test_hill_climbing_gets_stuck_on_a_long_pair(self) -> None:
        graph = GRAPHS["operating"]
        with self.assertRaises(NoRouteFoundError):
            hill_climbing_search(graph, "S4", "E20", trace_memory=False)


class PathIntegrityTest(unittest.TestCase):
    def test_every_algorithm_returns_a_walk_whose_duration_matches_it(self) -> None:
        graph = GRAPHS["operating"]
        for name, search in ALGORITHMS.items():
            solved = 0
            for start_id, goal_id in sample_pairs(graph, 12, seed=4):
                try:
                    route = search(graph, start_id, goal_id, trace_memory=False)
                except NoRouteFoundError:
                    continue
                solved += 1
                self.assertEqual(route.path[0][0], start_id, name)
                self.assertEqual(route.path[-1][0], goal_id, name)
                self.assertEqual(len(set(route.path)), len(route.path), name)
                self.assertAlmostEqual(walk_duration(graph, route), route.duration_sec, delta=TOLERANCE)
            self.assertGreater(solved, 0, name)

    def test_start_equals_goal_is_an_empty_trip(self) -> None:
        graph = GRAPHS["operating"]
        for name, search in ALGORITHMS.items():
            route = search(graph, "CEN", "CEN", trace_memory=False)
            self.assertEqual(route.hops, 0, name)
            self.assertEqual(route.transfers, 0, name)
            self.assertAlmostEqual(route.duration_sec, 0.0, msg=name)
            self.assertAlmostEqual(route.fare_thb, 0.0, msg=name)

    def test_results_are_deterministic(self) -> None:
        graph = GRAPHS["operating"]
        for name, search in ALGORITHMS.items():
            start_id, goal_id = ("CEN", "E4") if name == "hill_climbing" else ("N12", "PK03")
            first = search(graph, start_id, goal_id, trace_memory=False)
            second = search(graph, start_id, goal_id, trace_memory=False)
            self.assertEqual(first.path, second.path, name)
            self.assertEqual(first.nodes_expanded, second.nodes_expanded, name)
            self.assertEqual(first.nodes_generated, second.nodes_generated, name)


class InstrumentationTest(unittest.TestCase):
    def test_counters_are_comparable_across_algorithms(self) -> None:
        graph = GRAPHS["operating"]
        for name, search in ALGORITHMS.items():
            route = search(graph, "CEN", "E4", trace_memory=False)
            self.assertGreaterEqual(route.nodes_generated, route.nodes_expanded, name)
            self.assertGreaterEqual(route.nodes_expanded, route.hops, name)

    def test_untraced_runs_report_no_memory_and_leave_tracing_off(self) -> None:
        graph = GRAPHS["operating"]
        for name, search in ALGORITHMS.items():
            route = search(graph, "CEN", "E4", trace_memory=False)
            self.assertEqual(route.peak_memory_bytes, 0, name)
            self.assertFalse(tracemalloc.is_tracing(), name)
            self.assertGreater(route.runtime_sec, 0.0, name)

    def test_traced_runs_report_memory_and_stop_tracing(self) -> None:
        graph = GRAPHS["operating"]
        for name, search in ALGORITHMS.items():
            route = search(graph, "CEN", "E4")
            self.assertGreater(route.peak_memory_bytes, 0, name)
            self.assertFalse(tracemalloc.is_tracing(), name)

    def test_tracing_stops_even_when_no_route_is_found(self) -> None:
        graph = GRAPHS["operating"]
        with self.assertRaises(NoRouteFoundError):
            hill_climbing_search(graph, "S4", "E20")
        self.assertFalse(tracemalloc.is_tracing())


class WeightsTest(unittest.TestCase):
    def test_negative_weights_are_rejected(self) -> None:
        for kwargs in ({"time": -1.0}, {"cost": -0.5}, {"transit": -2.0}):
            with self.assertRaises(ValueError):
                Weights(**kwargs)

    def test_all_zero_weights_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Weights(time=0.0, cost=0.0, transit=0.0)

    def test_penalising_transfers_does_not_increase_them(self) -> None:
        graph = GRAPHS["operating"]
        for start_id, goal_id in sample_pairs(graph, 20, seed=5):
            plain = astar_search(graph, start_id, goal_id, trace_memory=False)
            penalised = astar_search(
                graph, start_id, goal_id, weights=Weights(time=1.0, transit=900.0), trace_memory=False
            )
            self.assertLessEqual(penalised.transfers, plain.transfers)

    def test_a_pure_time_search_is_still_optimal_in_time(self) -> None:
        graph = GRAPHS["operating"]
        for start_id, goal_id in sample_pairs(graph, 20, seed=6):
            route = astar_search(graph, start_id, goal_id, weights=Weights(time=2.5), trace_memory=False)
            self.assertAlmostEqual(route.duration_sec, optimal_duration(graph, start_id, goal_id), delta=TOLERANCE)


class GraphTest(unittest.TestCase):
    def test_each_scenario_is_a_superset_of_the_previous_one(self) -> None:
        sizes = [GRAPHS[scenario].num_nodes for scenario in ("operating", "under_construction", "planned")]
        self.assertEqual(sizes, sorted(sizes))
        self.assertLess(sizes[0], sizes[-1])

    def test_every_scenario_is_connected(self) -> None:
        for scenario, graph in GRAPHS.items():
            start = next(iter(graph.adjacency))
            reached = {start}
            stack = [start]
            while stack:
                node = stack.pop()
                for neighbor, _cost, _kind in graph.neighbors(node):
                    if neighbor not in reached:
                        reached.add(neighbor)
                        stack.append(neighbor)
            self.assertEqual(len(reached), graph.num_nodes, scenario)

    def test_unknown_scenario_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MetroGraph(scenario="tomorrow")


class ErrorTest(unittest.TestCase):
    def test_unknown_station_is_rejected(self) -> None:
        graph = GRAPHS["operating"]
        for search in ALGORITHMS.values():
            with self.assertRaises(ValueError):
                search(graph, "NOPE", "CEN", trace_memory=False)
            with self.assertRaises(ValueError):
                search(graph, "CEN", "NOPE", trace_memory=False)

    def test_station_on_an_unopened_line_is_rejected(self) -> None:
        graph = GRAPHS["operating"]
        for search in ALGORITHMS.values():
            with self.assertRaises(StationNotActiveError):
                search(graph, "CEN", "OR01", trace_memory=False)

    def test_the_same_station_is_reachable_once_its_line_opens(self) -> None:
        route = astar_search(GRAPHS["planned"], "CEN", "OR01", trace_memory=False)
        self.assertEqual(route.path[-1][0], "OR01")

    def test_beam_width_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            beam_search(GRAPHS["operating"], "CEN", "E4", beam_width=0)


class FareTest(unittest.TestCase):
    def test_an_empty_path_is_free(self) -> None:
        self.assertAlmostEqual(GRAPHS["operating"].fare_for([]), 0.0)

    def test_a_single_operator_trip_is_one_fare(self) -> None:
        route = astar_search(GRAPHS["operating"], "CEN", "E4", trace_memory=False)
        self.assertEqual(route.transfers, 0)
        self.assertAlmostEqual(route.fare_thb, 29.0)

    def test_a_two_operator_trip_is_two_fares(self) -> None:
        graph = GRAPHS["operating"]
        route = astar_search(graph, "N24", "BL01", trace_memory=False)
        self.assertEqual(route.transfers, 1)
        self.assertAlmostEqual(route.fare_thb, 106.0)

    def test_a_fare_never_exceeds_the_sum_of_the_operator_caps(self) -> None:
        graph = GRAPHS["operating"]
        cap = sum(fare["max_fare"] for fare in graph.fares.values())
        for start_id, goal_id in sample_pairs(graph, 30, seed=7):
            self.assertLessEqual(astar_search(graph, start_id, goal_id, trace_memory=False).fare_thb, cap)


if __name__ == "__main__":
    unittest.main()
