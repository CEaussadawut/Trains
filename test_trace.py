from __future__ import annotations
import json
import math
import unittest

from metro_router import (
    ALGORITHMS,
    MetroGraph,
    NoRouteFoundError,
    Tracer,
    Weights,
    astar_search,
    beam_search,
    hill_climbing_search,
    ida_star_search,
)

GRAPH = MetroGraph(scenario="operating")

# CEN->E4 is a single-operator hop, N24->BL01 crosses operators with one transfer,
# S4->E20 is the long diagonal that defeats hill climbing.
PAIRS = [("CEN", "E4"), ("N24", "BL01"), ("S4", "E20")]


def run(algorithm: str, start_id: str, goal_id: str, **kwargs: object):
    """Run an algorithm, returning (route or None, tracer). Never raises NoRouteFoundError."""
    tracer = kwargs.pop("tracer", None)
    try:
        return ALGORITHMS[algorithm](GRAPH, start_id, goal_id, trace_memory=False, tracer=tracer, **kwargs), tracer
    except NoRouteFoundError:
        return None, tracer


def kinds(tracer: Tracer, kind: str) -> list[dict]:
    return [event.payload for event in tracer.events if event.kind == kind]


class TracingIsInertTest(unittest.TestCase):
    """The engine is the graded artifact: tracing must not perturb it."""

    def test_traced_and_untraced_runs_agree_exactly(self) -> None:
        for name in ALGORITHMS:
            for start_id, goal_id in PAIRS:
                plain, _ = run(name, start_id, goal_id)
                traced, _ = run(name, start_id, goal_id, tracer=Tracer(max_events=10**7))
                label = f"{name} {start_id}->{goal_id}"
                if plain is None:
                    self.assertIsNone(traced, label)
                    continue
                self.assertIsNotNone(traced, label)
                self.assertEqual(plain.path, traced.path, label)
                self.assertEqual(plain.nodes_expanded, traced.nodes_expanded, label)
                self.assertEqual(plain.nodes_generated, traced.nodes_generated, label)
                self.assertAlmostEqual(plain.duration_sec, traced.duration_sec, msg=label)
                self.assertAlmostEqual(plain.fare_thb, traced.fare_thb, msg=label)

    def test_a_tracer_cannot_be_passed_positionally(self) -> None:
        with self.assertRaises(TypeError):
            astar_search(GRAPH, "CEN", "E4", Tracer())  # type: ignore[misc]


class CounterAgreementTest(unittest.TestCase):
    """Every meter increment must have exactly one matching event."""

    def test_expansions_match_the_meter(self) -> None:
        for name in ALGORITHMS:
            for start_id, goal_id in PAIRS:
                route, tracer = run(name, start_id, goal_id, tracer=Tracer(max_events=10**7))
                if route is None:
                    continue
                label = f"{name} {start_id}->{goal_id}"
                self.assertEqual(tracer.expanded, route.nodes_expanded, label)
                self.assertEqual(len(kinds(tracer, "expand")), route.nodes_expanded, label)

    def test_seeds_plus_generations_match_the_meter(self) -> None:
        for name in ALGORITHMS:
            for start_id, goal_id in PAIRS:
                route, tracer = run(name, start_id, goal_id, tracer=Tracer(max_events=10**7))
                if route is None:
                    continue
                label = f"{name} {start_id}->{goal_id}"
                self.assertEqual(tracer.seeded + tracer.generated, route.nodes_generated, label)
                self.assertEqual(
                    len(kinds(tracer, "generate")) + tracer.seeded, route.nodes_generated, label
                )

    def test_counters_still_agree_when_events_are_dropped(self) -> None:
        """Truncation loses events but must not lose count of what happened."""
        route, tracer = run("astar", "N24", "BL01", tracer=Tracer(max_events=25))
        self.assertTrue(tracer.truncated)
        self.assertLessEqual(len(tracer.events), 25)
        self.assertEqual(tracer.expanded, route.nodes_expanded)
        self.assertEqual(tracer.seeded + tracer.generated, route.nodes_generated)


class FailedSearchTest(unittest.TestCase):
    """A search that fails is the most instructive thing the app can replay."""

    def test_hill_climbing_keeps_its_trace_after_raising(self) -> None:
        tracer = Tracer()
        with self.assertRaises(NoRouteFoundError):
            hill_climbing_search(GRAPH, "S4", "E20", trace_memory=False, tracer=tracer)

        stuck = kinds(tracer, "stuck")
        self.assertEqual(len(stuck), 1)
        self.assertEqual(stuck[0]["reason"], "local optimum")
        self.assertEqual(stuck[0]["node"], ("S3", "SILOM"))
        # It stalled because every unvisited neighbour looked worse than where it stood.
        self.assertGreater(stuck[0]["h_best"], stuck[0]["h"])
        self.assertTrue(kinds(tracer, "expand"))
        self.assertFalse(kinds(tracer, "solution"))

    def test_a_dead_end_is_reported_distinctly_from_a_local_optimum(self) -> None:
        reasons = set()
        for station in GRAPH.stations:
            if not GRAPH.active_station_lines(station):
                continue
            tracer = Tracer()
            try:
                hill_climbing_search(GRAPH, station, "E20", trace_memory=False, tracer=tracer)
            except NoRouteFoundError:
                stuck = kinds(tracer, "stuck")
                if stuck:
                    reasons.add(stuck[0]["reason"])
        self.assertIn("local optimum", reasons)


class BeamTest(unittest.TestCase):
    def test_pruning_is_recorded_with_what_was_discarded(self) -> None:
        route, tracer = run("beam", "S4", "E20", beam_width=2, tracer=Tracer(max_events=10**7))
        self.assertIsNotNone(route)
        prunes = kinds(tracer, "prune")
        self.assertTrue(prunes)
        self.assertTrue(any(prune["dropped_total"] > 0 for prune in prunes))
        for prune in prunes:
            self.assertLessEqual(len(prune["kept"]), 2)
            self.assertLessEqual(len(prune["dropped"]), tracer.prune_limit)
            # Everything kept must score at least as well as everything dropped.
            if prune["kept"] and prune["dropped"]:
                self.assertLessEqual(
                    max(item["f"] for item in prune["kept"]),
                    min(item["f"] for item in prune["dropped"]) + 1e-9,
                )

    def test_every_round_is_a_phase(self) -> None:
        _route, tracer = run("beam", "CEN", "E4", tracer=Tracer(max_events=10**7))
        rounds = [phase for phase in tracer.phases if phase["label"] == "beam_round"]
        self.assertTrue(rounds)
        self.assertEqual([phase["round"] for phase in rounds], list(range(len(rounds))))


class IdaStarTest(unittest.TestCase):
    def test_thresholds_rise_strictly_and_stay_finite(self) -> None:
        _route, tracer = run("ida_star", "CEN", "E4", tracer=Tracer(max_events=10**7))
        thresholds = [phase["threshold"] for phase in tracer.phases if phase["label"] == "threshold"]
        self.assertGreater(len(thresholds), 1)
        for earlier, later in zip(thresholds, thresholds[1:]):
            self.assertLess(earlier, later)
        self.assertTrue(all(math.isfinite(value) for value in thresholds))

    def test_backtracking_is_visible(self) -> None:
        _route, tracer = run("ida_star", "CEN", "E4", tracer=Tracer(max_events=10**7))
        backtracks = kinds(tracer, "backtrack")
        self.assertTrue(backtracks)
        self.assertTrue(any(event["reason"] == "over_threshold" for event in backtracks))

    def test_keeping_only_the_last_phase_bounds_a_huge_search(self) -> None:
        """RN10->A7 expands ~14.6k nodes across rising thresholds."""
        route, tracer = run(
            "ida_star", "RN10", "A7", tracer=Tracer(max_events=10**7, keep_phases="last")
        )
        self.assertIsNotNone(route)
        phases = [phase for phase in tracer.phases if phase["label"] == "threshold"]
        self.assertGreater(len(phases), 2)
        # Every phase keeps a summary...
        for phase in phases:
            self.assertIn("expanded", phase)
            self.assertIn("generated", phase)
        # ...but only the final phase keeps its detail, so the trace stays far
        # smaller than the search it describes.
        self.assertLess(len(tracer.events), tracer.expanded + tracer.generated)
        self.assertTrue(kinds(tracer, "solution"))

    def test_detail_expansions_drops_generate_events_but_keeps_the_count(self) -> None:
        route, tracer = run(
            "ida_star", "CEN", "E4", tracer=Tracer(max_events=10**7, detail="expansions")
        )
        self.assertEqual(kinds(tracer, "generate"), [])
        self.assertEqual(len(kinds(tracer, "expand")), route.nodes_expanded)
        self.assertEqual(tracer.seeded + tracer.generated, route.nodes_generated)


class FrontierTest(unittest.TestCase):
    def test_snapshots_are_ordered_capped_and_honest_about_size(self) -> None:
        _route, tracer = run("astar", "N24", "BL01", tracer=Tracer(max_events=10**7))
        snapshots = [event for event in kinds(tracer, "expand") if "frontier" in event]
        self.assertTrue(snapshots)
        for event in snapshots:
            frontier = event["frontier"]
            self.assertLessEqual(len(frontier), 12)
            self.assertLessEqual(len(frontier), event["frontier_size"])
            priorities = [entry["priority"] for entry in frontier]
            self.assertEqual(priorities, sorted(priorities))

    def test_stale_heap_entries_are_flagged(self) -> None:
        """A* leaves superseded entries in the heap; the UI should show them as ghosts."""
        _route, tracer = run("astar", "N24", "BL01", tracer=Tracer(max_events=10**7))
        entries = [
            entry
            for event in kinds(tracer, "expand")
            for entry in event.get("frontier", [])
        ]
        self.assertTrue(any(entry["stale"] for entry in entries))


class SerialisationTest(unittest.TestCase):
    def test_every_trace_is_strict_json(self) -> None:
        """json.dumps emits bare Infinity, which JSON.parse rejects."""

        def reject(literal: str) -> None:
            raise AssertionError(f"non-finite JSON literal: {literal}")

        for name in ALGORITHMS:
            for start_id, goal_id in PAIRS:
                _route, tracer = run(
                    name, start_id, goal_id, tracer=Tracer(max_events=5_000)
                )
                if tracer is None:
                    continue
                text = json.dumps(tracer.to_dict())
                json.loads(text, parse_constant=reject)

    def test_a_failed_search_serialises_too(self) -> None:
        tracer = Tracer()
        with self.assertRaises(NoRouteFoundError):
            hill_climbing_search(GRAPH, "S4", "E20", trace_memory=False, tracer=tracer)
        payload = tracer.to_dict()
        self.assertEqual(payload["events"][-1]["kind"], "stuck")
        self.assertEqual(payload["counts"]["expanded"], tracer.expanded)
        json.loads(json.dumps(payload))


class TracerValidationTest(unittest.TestCase):
    def test_bad_settings_are_rejected(self) -> None:
        for kwargs in ({"max_events": 0}, {"detail": "verbose"}, {"keep_phases": "some"},
                       {"frontier_limit": -1}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                Tracer(**kwargs)

    def test_weights_reach_the_trace(self) -> None:
        """A transfer penalty should change which nodes get expanded."""
        _plain, plain_tracer = run("astar", "N24", "BL01", tracer=Tracer(max_events=10**7))
        penalised, pen_tracer = run(
            "astar", "N24", "BL01",
            weights=Weights(time=1.0, transit=900.0), tracer=Tracer(max_events=10**7),
        )
        self.assertIsNotNone(penalised)
        self.assertNotEqual(plain_tracer.expanded, pen_tracer.expanded)


if __name__ == "__main__":
    unittest.main()
