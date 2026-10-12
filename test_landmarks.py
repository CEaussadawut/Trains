"""Correctness gate for the ALT landmark heuristic (landmarks.py, astar_landmark).

A heuristic that *under*estimates is merely slow. One that overestimates is
wrong, silently: A* still returns a route, still looks fast, and is no longer
optimal. So the gate here is one-sided and strict, and it is checked in three
independent ways rather than one:

1. admissibility against the Dijkstra oracle, on every node of every pair;
2. consistency on every edge of every scenario (the stronger property, and the
   one that lets A* close a node without ever reopening it);
3. the route itself, against the same oracle plain A* is checked against.

Two things specific to this network get their own tests, because both are
places where the textbook one-line ALT formula is wrong here:

* **the weighted graph is directed** once fares are priced, so the bound needs
  distances measured both to and from each landmark (`DirectedMetricTest`);
* **the goal is a station, not a node**, so the bound has to hold for whichever
  of the goal's platforms the search reaches first (`GoalSetTest`).
"""
from __future__ import annotations
import math
import unittest

import landmarks
from landmarks import GoalBound, build_table, dijkstra, select_landmarks
from metro_router import (
    ALGORITHMS,
    SCENARIOS,
    MetroGraph,
    Weights,
    astar_landmark_search,
    astar_search,
    optimal_duration,
)
from test_metro_router import GRAPHS, TOLERANCE, active_stations, sample_pairs

WEIGHTINGS = {
    "time only": Weights(time=1.0),
    "time and fare": Weights(time=1.0, cost=30.0),
    "time and transfers": Weights(time=1.0, transit=600.0),
    "fare only": Weights(time=0.0, cost=1.0),
    "transfers only": Weights(time=0.0, transit=1.0),
}


def weighted_cost(graph: MetroGraph, path, weights: Weights) -> float:
    """What the search actually paid for a path, recomputed from the edges."""
    cost_fn = graph.edge_weight_fn(weights)
    total = 0.0
    for node, nxt in zip(path, path[1:]):
        steps = [cost_fn(node, nxt, raw, kind)
                 for neighbor, raw, kind in graph.neighbors(node) if neighbor == nxt]
        if not steps:
            raise AssertionError(f"{node!r} -> {nxt!r} is not an edge")
        total += min(steps)
    return total


def true_cost_to_goal(graph: MetroGraph, goal_id: str, weights: Weights) -> dict:
    """Exact weighted cost from every node to the goal *station*, by one
    backwards Dijkstra from all of the goal's platforms at once."""
    return dijkstra(graph.adjacency, graph.start_nodes(goal_id),
                    graph.edge_weight_fn(weights), reverse=True)


# --------------------------------------------------------------------------- #
# 1. the bound is a bound
# --------------------------------------------------------------------------- #
class AdmissibilityTest(unittest.TestCase):
    def test_never_overestimates_under_any_weighting(self) -> None:
        """h(n) <= the true remaining cost, for every node and every weighting.

        Checked against an exact backwards Dijkstra rather than against sampled
        routes, so this covers all ~400 nodes at once instead of the handful an
        A* run happens to touch.
        """
        graph = GRAPHS["operating"]
        checked = 0
        for label, weights in WEIGHTINGS.items():
            for goal_id in ("BL01", "E23", "A1", "PP01"):
                h = graph.landmark_heuristic(goal_id, weights)
                truth = true_cost_to_goal(graph, goal_id, weights)
                for node, remaining in truth.items():
                    self.assertLessEqual(
                        h(node), remaining + TOLERANCE,
                        f"{label}: h({node}) overestimates the cost to {goal_id}",
                    )
                    checked += 1
        self.assertGreater(checked, 3_500)

    def test_is_zero_at_the_goal(self) -> None:
        graph = GRAPHS["operating"]
        for weights in WEIGHTINGS.values():
            for station_id in ("BL01", "CEN", "N24", "A1"):
                h = graph.landmark_heuristic(station_id, weights)
                for node in graph.start_nodes(station_id):
                    self.assertAlmostEqual(h(node), 0.0, places=9)

    def test_is_consistent_on_every_edge_of_every_scenario(self) -> None:
        """h(u) <= w(u, v) + h(v) for every arc -- the property that lets A*
        close a node for good. Tested per scenario because each one activates a
        different edge set, and per direction because the weighted graph is not
        symmetric."""
        weights = Weights(time=1.0, cost=30.0, transit=120.0)
        for scenario, graph in GRAPHS.items():
            cost_fn = graph.edge_weight_fn(weights)
            checked = 0
            for goal_id in ("BL01", "E23"):
                h = graph.landmark_heuristic(goal_id, weights)
                for node, edges in graph.adjacency.items():
                    here = h(node)
                    for neighbor, raw, kind in edges:
                        self.assertLessEqual(
                            here, cost_fn(node, neighbor, raw, kind) + h(neighbor) + TOLERANCE,
                            f"{scenario}: h({node}) > w + h({neighbor}) for goal {goal_id}",
                        )
                        checked += 1
            self.assertGreater(checked, 800)


# --------------------------------------------------------------------------- #
# 2. it is a *better* bound -- the only reason to pay for preprocessing
# --------------------------------------------------------------------------- #
class DominanceTest(unittest.TestCase):
    def test_dominates_the_geographic_heuristic_everywhere(self) -> None:
        """By construction -- the heuristic is the max of the two -- but asserted
        so that a regression in the ALT term shows up as a failure rather than
        as the geographic bound quietly carrying the search."""
        graph = GRAPHS["operating"]
        weights = Weights(time=1.0)
        for goal_id in ("BL01", "E23", "RN10"):
            h_alt = graph.landmark_heuristic(goal_id, weights)
            for node in graph.adjacency:
                self.assertGreaterEqual(
                    h_alt(node), weights.time * graph.heuristic(node, goal_id) - TOLERANCE)

    def test_the_landmark_term_is_what_does_the_work(self) -> None:
        """The ALT term alone must beat the geographic one on most nodes. If it
        did not, `astar_landmark` would just be A* with extra steps."""
        graph = GRAPHS["operating"]
        weights = Weights(time=1.0)
        goal_id = "BL01"
        alt_only = graph.landmark_heuristic(goal_id, weights, geographic=False)
        wins = sum(1 for node in graph.adjacency
                   if alt_only(node) > weights.time * graph.heuristic(node, goal_id) + TOLERANCE)
        self.assertGreater(wins, len(graph.adjacency) * 0.8)

    def test_expands_fewer_nodes_than_plain_astar(self) -> None:
        graph = GRAPHS["operating"]
        better = equal = 0
        for start_id, goal_id in sample_pairs(graph, 40, seed=11):
            plain = astar_search(graph, start_id, goal_id, trace_memory=False)
            alt = astar_landmark_search(graph, start_id, goal_id, trace_memory=False)
            self.assertLessEqual(alt.nodes_expanded, plain.nodes_expanded,
                                 f"{start_id} -> {goal_id}")
            if alt.nodes_expanded < plain.nodes_expanded:
                better += 1
            else:
                equal += 1
        self.assertGreater(better, equal)


# --------------------------------------------------------------------------- #
# 3. the route is still the best one
# --------------------------------------------------------------------------- #
class OptimalityTest(unittest.TestCase):
    def test_matches_the_dijkstra_reference(self) -> None:
        graph = GRAPHS["operating"]
        for start_id, goal_id in sample_pairs(graph, 120, seed=1):
            route = astar_landmark_search(graph, start_id, goal_id, trace_memory=False)
            self.assertAlmostEqual(
                route.duration_sec, optimal_duration(graph, start_id, goal_id), delta=TOLERANCE)

    def test_agrees_with_plain_astar_on_cost_under_every_weighting(self) -> None:
        """Two admissible heuristics, same optimal cost. Compared on the
        *weighted* cost, which is what each one actually minimises -- comparing
        only duration would miss a fare- or transfer-weighted search going wrong."""
        graph = GRAPHS["operating"]
        for label, weights in WEIGHTINGS.items():
            for start_id, goal_id in sample_pairs(graph, 12, seed=5):
                plain = astar_search(graph, start_id, goal_id,
                                     weights=weights, trace_memory=False)
                alt = astar_landmark_search(graph, start_id, goal_id,
                                            weights=weights, trace_memory=False)
                self.assertAlmostEqual(
                    weighted_cost(graph, alt.path, weights),
                    weighted_cost(graph, plain.path, weights),
                    delta=1e-6, msg=f"{label}: {start_id} -> {goal_id}")

    def test_works_in_every_scenario(self) -> None:
        for scenario, graph in GRAPHS.items():
            for start_id, goal_id in sample_pairs(graph, 10, seed=7):
                route = astar_landmark_search(graph, start_id, goal_id, trace_memory=False)
                self.assertAlmostEqual(
                    route.duration_sec, optimal_duration(graph, start_id, goal_id),
                    delta=TOLERANCE, msg=scenario)

    def test_an_unopened_goal_station_raises_before_preprocessing(self) -> None:
        """The heuristic has to be built before `_search` can be handed it, so
        `astar_landmark` validates first -- otherwise a station with no open
        platforms surfaces as an error from inside the landmark code instead of
        the StationNotActiveError every other algorithm raises."""
        from metro_router import StationNotActiveError
        graph = GRAPHS["operating"]
        with self.assertRaises(StationNotActiveError):
            astar_landmark_search(graph, "CEN", "OR01", trace_memory=False)
        with self.assertRaises(ValueError):
            astar_landmark_search(graph, "CEN", "NOPE", trace_memory=False)

    def test_start_equals_goal_is_an_empty_trip(self) -> None:
        graph = GRAPHS["operating"]
        route = astar_landmark_search(graph, "CEN", "CEN", trace_memory=False)
        self.assertEqual(route.hops, 0)
        self.assertAlmostEqual(route.duration_sec, 0.0)


# --------------------------------------------------------------------------- #
# 4. the directed metric -- where the textbook one-liner would be wrong
# --------------------------------------------------------------------------- #
class DirectedMetricTest(unittest.TestCase):
    def test_pricing_fares_really_does_make_the_graph_directed(self) -> None:
        """The premise of the two-table design, asserted rather than assumed.

        A transfer between operators is charged the *destination* operator's
        base fare, and those differ (BTSC 17 THB, SRTET 12), so d(u,v) != d(v,u)
        as soon as cost is weighted. If this ever stops being true the extra
        table is dead weight -- but while it is true, dropping it breaks
        admissibility.
        """
        graph = GRAPHS["operating"]
        self.assertTrue(graph.landmark_table(Weights(time=1.0)).symmetric)
        self.assertFalse(graph.landmark_table(Weights(time=1.0, cost=30.0)).symmetric)

    def test_one_table_is_not_enough_once_fares_are_priced(self) -> None:
        """Why `LandmarkTable` keeps two tables instead of the textbook one.

        The classic ALT one-liner uses a single distance table per landmark,
        which is sound only when d(u, v) == d(v, u). Here it is not. This probes
        the single-table shortcut directly -- correct extremum over the goal
        set, but `dist_from` substituted where `dist_to` is required -- and
        measures how often it exceeds the true remaining cost:

            time only          symmetric      0 violations
            transfers only     symmetric      0 violations
            time + fare x30    DIRECTED     300 violations, up to 150 over
            fare only          DIRECTED     118 violations, up to   2 over

        Exactly the pattern the theory predicts: sound while the metric is
        symmetric, unsound the moment fares make it directed. An A* using the
        shortcut would keep returning routes and quietly stop being optimal.
        """
        graph = GRAPHS["operating"]
        goals = ("BL01", "E23", "A1", "CEN")

        def single_table_violations(weights: Weights) -> int:
            table = graph.landmark_table(weights)
            violations = 0
            for goal_id in goals:
                goal_nodes = graph.start_nodes(goal_id)
                truth = true_cost_to_goal(graph, goal_id, weights)
                for i in range(len(table)):
                    d_from = table.dist_from[i]
                    reach = [d_from[g] for g in goal_nodes if g in d_from]
                    if len(reach) != len(goal_nodes):
                        continue
                    cap = max(reach)
                    for node, remaining in truth.items():
                        if node in d_from and d_from[node] - cap > remaining + TOLERANCE:
                            violations += 1
            return violations

        self.assertEqual(single_table_violations(Weights(time=1.0)), 0)
        self.assertEqual(single_table_violations(Weights(time=0.0, transit=1.0)), 0)
        self.assertGreater(single_table_violations(Weights(time=1.0, cost=30.0)), 0)
        self.assertGreater(single_table_violations(Weights(time=0.0, cost=1.0)), 0)

    def test_the_two_table_bound_stays_valid_where_the_shortcut_fails(self) -> None:
        """The same weightings that break the shortcut, checked against the real
        implementation: no violations anywhere."""
        graph = GRAPHS["operating"]
        for weights in (Weights(time=1.0, cost=30.0), Weights(time=0.0, cost=1.0)):
            for goal_id in ("BL01", "E23", "A1", "CEN"):
                bound = GoalBound.build(graph.landmark_table(weights),
                                        graph.start_nodes(goal_id))
                for node, remaining in true_cost_to_goal(graph, goal_id, weights).items():
                    self.assertLessEqual(bound(node), remaining + TOLERANCE,
                                         f"{weights} -> {goal_id}: {node}")

    def test_reverse_dijkstra_pays_the_forward_price(self) -> None:
        """A backwards search must charge w(v -> u) when it walks u <- v. Checked
        against a forward Dijkstra run from every node to one target."""
        graph = GRAPHS["operating"]
        weights = Weights(time=1.0, cost=30.0)
        cost_fn = graph.edge_weight_fn(weights)
        target = ("BL01", "BLUE")
        to_target = dijkstra(graph.adjacency, [target], cost_fn, reverse=True)
        for node in list(graph.adjacency)[::40]:
            forward = dijkstra(graph.adjacency, [node], cost_fn)
            self.assertAlmostEqual(to_target[node], forward[target], delta=1e-9, msg=str(node))


# --------------------------------------------------------------------------- #
# 5. the goal is a station, not a node
# --------------------------------------------------------------------------- #
class GoalSetTest(unittest.TestCase):
    def test_bound_holds_for_every_platform_of_an_interchange(self) -> None:
        """At a station serving two lines the search may finish on either
        platform. The bound must be below the cost to the *cheaper* one."""
        graph = GRAPHS["operating"]
        weights = Weights(time=1.0, cost=30.0)
        multi = [s for s in active_stations(graph) if len(graph.start_nodes(s)) > 1]
        self.assertGreater(len(multi), 1, "expected at least one multi-line station")
        for goal_id in multi:
            h = graph.landmark_heuristic(goal_id, weights)
            truth = true_cost_to_goal(graph, goal_id, weights)
            for node, remaining in truth.items():
                self.assertLessEqual(h(node), remaining + TOLERANCE,
                                     f"goal {goal_id} ({len(graph.start_nodes(goal_id))} platforms)")

    def test_taking_the_wrong_extremum_over_the_goal_set_would_overestimate(self) -> None:
        """Guards the subtle half of GoalBound: `max_g` in one term and `min_g`
        in the other. Swapping them produces a heuristic that looks fine on most
        pairs and is wrong on some, which is the worst kind of bug to ship."""
        graph = GRAPHS["operating"]
        weights = Weights(time=1.0)
        multi = [s for s in active_stations(graph) if len(graph.start_nodes(s)) > 1]
        caught = 0
        for goal_id in multi:
            goal_nodes = graph.start_nodes(goal_id)
            table = graph.landmark_table(weights)
            truth = true_cost_to_goal(graph, goal_id, weights)
            for i in range(len(table)):
                d_to, d_from = table.dist_to[i], table.dist_from[i]
                wrong_to = min(d_to[g] for g in goal_nodes if g in d_to)   # should be max
                wrong_from = max(d_from[g] for g in goal_nodes if g in d_from)  # should be min
                for node, remaining in truth.items():
                    if node in d_to and d_to[node] - wrong_to > remaining + TOLERANCE:
                        caught += 1
                    if node in d_from and wrong_from - d_from[node] > remaining + TOLERANCE:
                        caught += 1
        self.assertGreater(caught, 0, "the swapped-extremum bug would not be detected here")


# --------------------------------------------------------------------------- #
# 6. preprocessing, caching, determinism
# --------------------------------------------------------------------------- #
class PreprocessingTest(unittest.TestCase):
    def test_landmarks_are_at_the_extremes_of_the_network(self) -> None:
        """Farthest-point sampling should pick line ends, which is what makes
        the triangle inequality tight for trips that run across the city."""
        graph = GRAPHS["operating"]
        chosen = {station for station, _line in graph.landmark_table().landmarks}
        self.assertEqual(len(chosen), landmarks.DEFAULT_LANDMARKS)
        terminals = set()
        for station_id in active_stations(graph):
            for node in graph.start_nodes(station_id):
                rail = [n for n, _c, kind in graph.neighbors(node) if kind == "rail"]
                if len(rail) <= 1:
                    terminals.add(station_id)
        self.assertGreaterEqual(len(chosen & terminals), landmarks.DEFAULT_LANDMARKS - 1)

    def test_selection_is_deterministic(self) -> None:
        graph = MetroGraph()
        other = MetroGraph()
        self.assertEqual(graph.landmark_table().landmarks, other.landmark_table().landmarks)

    def test_results_are_deterministic(self) -> None:
        graph = GRAPHS["operating"]
        first = astar_landmark_search(graph, "N24", "BL01", trace_memory=False)
        second = astar_landmark_search(graph, "N24", "BL01", trace_memory=False)
        self.assertEqual(first.path, second.path)
        self.assertEqual(first.nodes_expanded, second.nodes_expanded)

    def test_tables_are_cached_per_weighting(self) -> None:
        graph = MetroGraph()
        a = graph.landmark_table(Weights(time=1.0))
        b = graph.landmark_table(Weights(time=1.0))
        c = graph.landmark_table(Weights(time=1.0, cost=30.0))
        self.assertIs(a, b)                      # same weighting -> same object
        self.assertIsNot(a, c)                   # different metric -> rebuilt
        self.assertNotEqual(a.dist_from[0], c.dist_from[0])

    def test_more_landmarks_never_weaken_the_bound(self) -> None:
        """Adding a landmark adds a term to a maximum, so the bound can only
        rise -- provided the first k landmarks are the same ones, which
        farthest-point sampling guarantees."""
        graph = GRAPHS["operating"]
        weights = Weights(time=1.0)
        goal_id = "BL01"
        small = graph.landmark_heuristic(goal_id, weights, count=2, geographic=False)
        large = graph.landmark_heuristic(goal_id, weights, count=8, geographic=False)
        self.assertEqual(graph.landmark_table(weights, 2).landmarks,
                         graph.landmark_table(weights, 8).landmarks[:2])
        for node in graph.adjacency:
            self.assertGreaterEqual(large(node), small(node) - TOLERANCE)

    def test_preprocessing_is_reported_and_affordable(self) -> None:
        graph = MetroGraph()
        seconds = graph.landmark_preprocess_seconds()
        self.assertGreater(seconds, 0.0)
        self.assertLess(seconds, 5.0)

    def test_a_single_landmark_still_produces_a_valid_bound(self) -> None:
        graph = GRAPHS["operating"]
        weights = Weights(time=1.0)
        h = graph.landmark_heuristic("BL01", weights, count=1)
        truth = true_cost_to_goal(graph, "BL01", weights)
        for node, remaining in truth.items():
            self.assertLessEqual(h(node), remaining + TOLERANCE)


# --------------------------------------------------------------------------- #
# 7. it is wired in like every other algorithm
# --------------------------------------------------------------------------- #
class RegistrationTest(unittest.TestCase):
    def test_is_in_the_algorithm_registry(self) -> None:
        self.assertIn("astar_landmark", ALGORITHMS)
        self.assertIs(ALGORITHMS["astar_landmark"], astar_landmark_search)

    def test_is_labelled_and_grouped_for_the_api(self) -> None:
        from server import engine
        for table in (engine.ALGORITHM_FAMILY, engine.ALGORITHM_LABEL):
            self.assertEqual(sorted(table), sorted(ALGORITHMS),
                             "every algorithm needs a family and a label or /api/meta 500s")
        self.assertEqual(engine.ALGORITHM_FAMILY["astar_landmark"], "informed (heuristic) search")

    def test_accepts_the_same_tracer_contract(self) -> None:
        from metro_router import Tracer
        graph = GRAPHS["operating"]
        tracer = Tracer()
        traced = astar_landmark_search(graph, "N24", "BL01", tracer=tracer, trace_memory=False)
        untraced = astar_landmark_search(graph, "N24", "BL01", trace_memory=False)
        self.assertEqual(traced.path, untraced.path)
        self.assertEqual(traced.nodes_expanded, untraced.nodes_expanded)
        payload = tracer.to_dict()
        self.assertEqual(payload["counts"]["expanded"], traced.nodes_expanded)


# --------------------------------------------------------------------------- #
# 8. the pure module, on graphs small enough to check by hand
# --------------------------------------------------------------------------- #
class PureModuleTest(unittest.TestCase):
    CHAIN = {"a": [("b", 1.0, "rail")], "b": [("a", 1.0, "rail"), ("c", 2.0, "rail")],
             "c": [("b", 2.0, "rail")]}

    @staticmethod
    def unit(_u, _v, cost, _kind):
        return cost

    def test_dijkstra_on_a_chain(self) -> None:
        d = dijkstra(self.CHAIN, ["a"], self.unit)
        self.assertEqual(d, {"a": 0.0, "b": 1.0, "c": 3.0})

    def test_reverse_dijkstra_on_a_one_way_graph(self) -> None:
        """a -> b costs 1, b -> a costs 10. Distance *to* a is not distance from a."""
        def asymmetric(u, v, cost, _kind):
            return cost if (u, v) == ("a", "b") else 10.0
        graph = {"a": [("b", 1.0, "rail")], "b": [("a", 1.0, "rail")]}
        self.assertEqual(dijkstra(graph, ["a"], asymmetric)["b"], 1.0)
        self.assertEqual(dijkstra(graph, ["a"], asymmetric, reverse=True)["b"], 10.0)

    def test_unreachable_nodes_are_absent_not_infinite(self) -> None:
        graph = dict(self.CHAIN, z=[])
        self.assertNotIn("z", dijkstra(graph, ["a"], self.unit))

    def test_bound_is_exact_on_a_chain(self) -> None:
        """With a landmark at each end of a path graph the triangle inequality
        is tight, so ALT returns the true distance."""
        table = build_table(self.CHAIN, self.unit, count=2)
        bound = GoalBound.build(table, ["c"])
        self.assertAlmostEqual(bound("a"), 3.0)
        self.assertAlmostEqual(bound("b"), 2.0)
        self.assertAlmostEqual(bound("c"), 0.0)

    def test_bound_is_never_negative(self) -> None:
        table = build_table(self.CHAIN, self.unit, count=1)
        bound = GoalBound.build(table, ["a"])
        for node in self.CHAIN:
            self.assertGreaterEqual(bound(node), 0.0)

    def test_selecting_more_landmarks_than_nodes_is_harmless(self) -> None:
        self.assertEqual(len(select_landmarks(self.CHAIN, self.unit, 99)), len(self.CHAIN))

    def test_an_empty_graph_yields_no_landmarks(self) -> None:
        self.assertEqual(select_landmarks({}, self.unit, 4), [])
        self.assertEqual(len(build_table({}, self.unit, 4)), 0)

    def test_an_empty_goal_set_is_rejected(self) -> None:
        table = build_table(self.CHAIN, self.unit, count=2)
        with self.assertRaises(ValueError):
            GoalBound.build(table, [])

    def test_a_landmark_that_cannot_reach_the_goal_is_skipped_not_trusted(self) -> None:
        """One-way bridge: nothing can get back from 'island'. A landmark out
        there has no usable bound for a goal on the mainland, and must not
        contribute a bogus one."""
        graph = {"m": [("island", 1.0, "rail")], "island": [("m", 1.0, "rail")]}

        def one_way(u, v, cost, _kind):
            return cost if (u, v) == ("m", "island") else math.inf
        table = build_table(graph, one_way, count=2)
        bound = GoalBound.build(table, ["m"])
        self.assertGreaterEqual(bound("island"), 0.0)
        self.assertLess(bound("m"), TOLERANCE)


if __name__ == "__main__":
    unittest.main()
