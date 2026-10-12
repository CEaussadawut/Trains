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
    Tracer,
    Weights,
    _fare_step,
    astar_search,
    beam_search,
    bidirectional_search,
    breadth_first_search,
    depth_first_search,
    depth_limited_search,
    greedy_best_first_search,
    hill_climbing_search,
    ida_star_search,
    iterative_deepening_search,
    optimal_duration,
    uniform_cost_search,
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


BLIND_SEARCHES = (
    breadth_first_search,
    uniform_cost_search,
    depth_first_search,
    depth_limited_search,
    iterative_deepening_search,
    bidirectional_search,
)


class BlindSearchTest(unittest.TestCase):
    def test_blind_searches_never_consult_the_heuristic(self) -> None:
        graph = MetroGraph(scenario="operating")

        def forbidden(*_args: object) -> float:
            raise AssertionError("a blind search called the heuristic")

        graph.heuristic = forbidden  # type: ignore[method-assign]
        for search in BLIND_SEARCHES:
            search(graph, "N24", "BL01", trace_memory=False)

    def test_ucs_matches_the_dijkstra_reference(self) -> None:
        for scenario, graph in GRAPHS.items():
            for start_id, goal_id in sample_pairs(graph, 40, seed=7):
                route = uniform_cost_search(graph, start_id, goal_id, trace_memory=False)
                self.assertAlmostEqual(
                    route.duration_sec, optimal_duration(graph, start_id, goal_id),
                    delta=TOLERANCE, msg=f"{scenario} {start_id}->{goal_id}",
                )

    def test_bfs_finds_the_fewest_hops(self) -> None:
        graph = GRAPHS["operating"]
        for start_id, goal_id in sample_pairs(graph, 30, seed=8):
            bfs = breadth_first_search(graph, start_id, goal_id, trace_memory=False)
            for name, search in ALGORITHMS.items():
                try:
                    other = search(graph, start_id, goal_id, trace_memory=False)
                except NoRouteFoundError:
                    continue
                self.assertLessEqual(bfs.hops, other.hops, f"{name} {start_id}->{goal_id}")

    def test_the_heuristic_saves_work_over_ucs(self) -> None:
        graph = GRAPHS["operating"]
        for start_id, goal_id in sample_pairs(graph, 30, seed=9):
            ucs = uniform_cost_search(graph, start_id, goal_id, trace_memory=False)
            astar = astar_search(graph, start_id, goal_id, trace_memory=False)
            self.assertLessEqual(astar.nodes_expanded, ucs.nodes_expanded, f"{start_id}->{goal_id}")

    def test_ids_and_bidirectional_match_bfs_on_hops(self) -> None:
        for scenario, graph in GRAPHS.items():
            for start_id, goal_id in sample_pairs(graph, 25, seed=11):
                bfs = breadth_first_search(graph, start_id, goal_id, trace_memory=False)
                ids = iterative_deepening_search(graph, start_id, goal_id, trace_memory=False)
                both = bidirectional_search(graph, start_id, goal_id, trace_memory=False)
                label = f"{scenario} {start_id}->{goal_id}"
                self.assertEqual(ids.hops, bfs.hops, label)
                self.assertEqual(both.hops, bfs.hops, label)

    def test_ids_re_expands_the_shallow_levels(self) -> None:
        graph = GRAPHS["operating"]
        bfs = breadth_first_search(graph, "N24", "BL01", trace_memory=False)
        ids = iterative_deepening_search(graph, "N24", "BL01", trace_memory=False)
        self.assertGreater(ids.nodes_expanded, bfs.nodes_expanded)

    def test_bidirectional_expands_fewer_nodes_than_bfs(self) -> None:
        graph = GRAPHS["operating"]
        cheaper = 0
        pairs = sample_pairs(graph, 30, seed=12)
        for start_id, goal_id in pairs:
            bfs = breadth_first_search(graph, start_id, goal_id, trace_memory=False)
            both = bidirectional_search(graph, start_id, goal_id, trace_memory=False)
            cheaper += both.nodes_expanded < bfs.nodes_expanded
        self.assertGreater(cheaper, len(pairs) // 2)

    def test_dls_fails_past_its_limit_and_succeeds_within_it(self) -> None:
        graph = GRAPHS["operating"]
        hops = breadth_first_search(graph, "N24", "E23", trace_memory=False).hops
        self.assertGreater(hops, 30)
        with self.assertRaisesRegex(NoRouteFoundError, "depth limit"):
            depth_limited_search(graph, "N24", "E23", depth_limit=hops - 1, trace_memory=False)
        route = depth_limited_search(graph, "N24", "E23", depth_limit=hops, trace_memory=False)
        self.assertEqual(route.hops, hops)

    def test_dls_never_exceeds_its_limit(self) -> None:
        graph = GRAPHS["operating"]
        for start_id, goal_id in sample_pairs(graph, 30, seed=13):
            for limit in (5, 15, 30):
                try:
                    route = depth_limited_search(graph, start_id, goal_id, depth_limit=limit, trace_memory=False)
                except NoRouteFoundError:
                    continue
                self.assertLessEqual(route.hops, limit)

    def test_negative_depth_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            depth_limited_search(GRAPHS["operating"], "CEN", "E4", depth_limit=-1)

    def test_dfs_is_complete_but_not_optimal(self) -> None:
        graph = GRAPHS["operating"]
        slower = 0
        for start_id, goal_id in sample_pairs(graph, 30, seed=10):
            route = depth_first_search(graph, start_id, goal_id, trace_memory=False)
            if route.duration_sec > optimal_duration(graph, start_id, goal_id) + TOLERANCE:
                slower += 1
        self.assertGreater(slower, 0)

    def test_blind_routes_are_real_walks_in_every_scenario(self) -> None:
        for scenario, graph in GRAPHS.items():
            for start_id, goal_id in sample_pairs(graph, 25, seed=14):
                for search in BLIND_SEARCHES:
                    label = f"{search.__name__} {scenario} {start_id}->{goal_id}"
                    try:
                        route = search(graph, start_id, goal_id, trace_memory=False)
                    except NoRouteFoundError:
                        self.assertIs(search, depth_limited_search, label)
                        continue
                    self.assertEqual(route.path[0][0], start_id, label)
                    self.assertEqual(route.path[-1][0], goal_id, label)
                    self.assertEqual(len(set(route.path)), len(route.path), label)
                    for node, neighbor in zip(route.path, route.path[1:]):
                        self.assertIn(neighbor, [other for other, _c, _k in graph.neighbors(node)], label)
                    self.assertAlmostEqual(walk_duration(graph, route), route.duration_sec, delta=TOLERANCE)

    def test_bfs_goal_tests_on_generation(self) -> None:
        graph = GRAPHS["operating"]
        for start_id, goal_id in sample_pairs(graph, 20, seed=15):
            tracer = Tracer(max_events=10**7)
            route = breadth_first_search(graph, start_id, goal_id, trace_memory=False, tracer=tracer)
            expanded = [event.payload["node"] for event in tracer.events if event.kind == "expand"]
            self.assertNotIn(goal_id, [node[0] for node in expanded], f"{start_id}->{goal_id}")
            self.assertEqual(route.path[-2], expanded[-1], f"{start_id}->{goal_id}")


class FareAwareUcsTest(unittest.TestCase):
    def test_fare_steps_sum_to_fare_for(self) -> None:
        for scenario, graph in GRAPHS.items():
            rng = random.Random(16)
            stations = active_stations(graph)
            for _ in range(500):
                path = [rng.choice(graph.start_nodes(rng.choice(stations)))]
                segment_hops, total = 0, 0.0
                for _ in range(rng.randint(0, 60)):
                    neighbor, _cost, kind = rng.choice(graph.neighbors(path[-1]))
                    added, segment_hops = _fare_step(graph, path[-1], neighbor, kind, segment_hops)
                    total += added
                    path.append(neighbor)
                self.assertAlmostEqual(total, graph.fare_for(path), delta=TOLERANCE, msg=f"{scenario} {path}")

    def test_cost_only_ucs_finds_the_cheapest_fare(self) -> None:
        cheapest = Weights(time=0.0, cost=1.0)
        for scenario, graph in GRAPHS.items():
            for start_id, goal_id in sample_pairs(graph, 20, seed=17):
                ucs = uniform_cost_search(graph, start_id, goal_id, weights=cheapest, trace_memory=False)
                rivals = [
                    uniform_cost_search(graph, start_id, goal_id, trace_memory=False),
                    breadth_first_search(graph, start_id, goal_id, trace_memory=False),
                    bidirectional_search(graph, start_id, goal_id, trace_memory=False),
                    astar_search(graph, start_id, goal_id, weights=Weights(time=1e-6, cost=1.0), trace_memory=False),
                ]
                for rival in rivals:
                    self.assertLessEqual(ucs.fare_thb, rival.fare_thb + TOLERANCE, f"{scenario} {start_id}->{goal_id}")

    def test_the_first_operator_base_fare_counts(self) -> None:
        """The old per-edge model skipped the first base fare and the cap, so it could pick a pricier route."""
        graph = GRAPHS["planned"]
        cheapest = Weights(time=0.0, cost=1.0)
        beaten = 0
        for start_id, goal_id in sample_pairs(graph, 40, seed=18):
            ucs = uniform_cost_search(graph, start_id, goal_id, weights=cheapest, trace_memory=False)
            per_edge = astar_search(graph, start_id, goal_id, weights=Weights(time=1e-6, cost=1.0), trace_memory=False)
            beaten += ucs.fare_thb < per_edge.fare_thb - TOLERANCE
        self.assertGreater(beaten, 0)


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
