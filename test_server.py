from __future__ import annotations
import json
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

from server.api import Handler
from server.engine import warm_up


def reject_constant(literal: str) -> None:
    raise AssertionError(f"non-finite JSON literal in response: {literal}")


class ApiTestCase(unittest.TestCase):
    server: ThreadingHTTPServer
    thread: threading.Thread
    base: str

    @classmethod
    def setUpClass(cls) -> None:
        warm_up()
        Handler.dist = None
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def get(self, path: str) -> tuple[int, dict]:
        return self._call(urllib.request.Request(self.base + path))

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._call(request)

    def _call(self, request: urllib.request.Request) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode("utf-8")
                status = response.status
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8")
            status = exc.code
        # Every response must be strict JSON -- json.dumps would otherwise emit
        # bare Infinity for IDA* thresholds, which JSON.parse rejects.
        return status, json.loads(text, parse_constant=reject_constant)


class MetaTest(ApiTestCase):
    def test_meta_lists_both_families(self) -> None:
        status, body = self.get("/api/meta")
        self.assertEqual(status, 200)
        families = {entry["id"]: entry["family"] for entry in body["algorithms"]}
        self.assertEqual(families["astar"], "informed search")
        self.assertEqual(families["ida_star"], "informed search")
        self.assertEqual(families["greedy"], "heuristic search")
        self.assertEqual(families["hill_climbing"], "heuristic search")
        self.assertEqual(families["beam"], "heuristic search")


class NetworkTest(ApiTestCase):
    def test_full_geometry_ships_for_every_scenario(self) -> None:
        for scenario, expected_active in (
            ("operating", 194), ("under_construction", 238), ("planned", 302)
        ):
            status, body = self.get(f"/api/network?scenario={scenario}")
            self.assertEqual(status, 200, scenario)
            self.assertEqual(len(body["stations"]), 302, scenario)
            self.assertEqual(len(body["edges"]), 289, scenario)
            self.assertEqual(body["stats"]["active_stations"], expected_active, scenario)

    def test_every_line_has_a_usable_colour(self) -> None:
        _status, body = self.get("/api/network?scenario=planned")
        for line in body["lines"]:
            self.assertRegex(line["color_hex"], r"^#[0-9A-Fa-f]{6}$", line["id"])

    def test_stations_carry_both_layouts(self) -> None:
        _status, body = self.get("/api/network")
        for station in body["stations"]:
            self.assertIsInstance(station["map_x"], float)
            self.assertIsInstance(station["lat"], float)
            self.assertIn(station["label_side"], {"left", "right"})

    def test_unknown_scenario_is_rejected(self) -> None:
        status, body = self.get("/api/network?scenario=tomorrow")
        self.assertEqual(status, 400)
        self.assertEqual(body["code"], "invalid_request")


class SearchTest(ApiTestCase):
    def test_a_two_operator_trip_matches_the_engine(self) -> None:
        status, body = self.post("/api/search", {"algorithm": "astar", "start": "N24", "goal": "BL01"})
        self.assertEqual(status, 200)
        self.assertTrue(body["solved"])
        self.assertAlmostEqual(body["route"]["fare_thb"], 106.0)
        self.assertEqual(body["route"]["transfers"], 1)

    def test_a_single_operator_trip_matches_the_engine(self) -> None:
        _status, body = self.post("/api/search", {"algorithm": "astar", "start": "CEN", "goal": "E4"})
        self.assertAlmostEqual(body["route"]["fare_thb"], 29.0)
        self.assertEqual(body["route"]["transfers"], 0)

    def test_the_itinerary_accounts_for_the_whole_journey(self) -> None:
        _status, body = self.post("/api/search", {"algorithm": "astar", "start": "N24", "goal": "BL01"})
        legs = body["route"]["itinerary"]
        self.assertEqual([leg["kind"] for leg in legs], ["ride", "transfer", "ride"])
        self.assertAlmostEqual(
            sum(leg["seconds"] for leg in legs), body["route"]["duration_sec"], places=6
        )

    def test_a_failed_search_is_reported_not_raised(self) -> None:
        status, body = self.post(
            "/api/search", {"algorithm": "hill_climbing", "start": "S4", "goal": "E20"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["solved"])
        self.assertIn("local optimum", body["error"])
        self.assertIsNone(body["route"])
        # The optimum still comes back, so the UI can say what was missed.
        self.assertGreater(body["optimal_sec"], 0)

    def test_unknown_station_is_a_bad_request(self) -> None:
        status, body = self.post("/api/search", {"start": "NOPE", "goal": "CEN"})
        self.assertEqual(status, 400)
        self.assertEqual(body["code"], "invalid_request")

    def test_a_station_on_an_unopened_line_is_unprocessable(self) -> None:
        status, body = self.post("/api/search", {"start": "CEN", "goal": "OR01"})
        self.assertEqual(status, 422)
        self.assertEqual(body["code"], "station_not_active")

    def test_the_same_station_works_once_its_line_opens(self) -> None:
        status, body = self.post(
            "/api/search", {"start": "CEN", "goal": "OR01", "scenario": "planned"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["solved"])

    def test_invalid_parameters_are_rejected(self) -> None:
        for payload in (
            {"start": "CEN", "goal": "E4", "algorithm": "beam", "beam_width": 0},
            {"start": "CEN", "goal": "E4", "weights": {"time": 0, "cost": 0, "transit": 0}},
            {"start": "CEN", "goal": "E4", "weights": {"time": -1}},
            {"start": "CEN", "goal": "E4", "algorithm": "dijkstra"},
        ):
            status, _body = self.post("/api/search", payload)
            self.assertEqual(status, 400, payload)

    def test_penalising_transfers_changes_the_route(self) -> None:
        _s, plain = self.post("/api/search", {"start": "N24", "goal": "BL01"})
        _s, penalised = self.post(
            "/api/search",
            {"start": "N24", "goal": "BL01", "weights": {"time": 1.0, "transit": 900.0}},
        )
        self.assertLessEqual(penalised["route"]["transfers"], plain["route"]["transfers"])


class TraceTest(ApiTestCase):
    def test_every_algorithm_returns_a_replayable_trace(self) -> None:
        for algorithm in ("greedy", "astar", "beam", "ida_star"):
            status, body = self.post(
                "/api/trace", {"algorithm": algorithm, "start": "CEN", "goal": "E4"}
            )
            self.assertEqual(status, 200, algorithm)
            self.assertTrue(body["solved"], algorithm)
            self.assertTrue(body["events"], algorithm)
            # Every expansion is accounted for, whether or not its detail was kept.
            self.assertEqual(body["counts"]["expanded"], body["route"]["nodes_expanded"], algorithm)

    def test_kept_detail_matches_the_meter_when_nothing_is_discarded(self) -> None:
        """greedy/astar/beam keep every phase, so events are the whole story."""
        for algorithm in ("greedy", "astar", "beam"):
            _status, body = self.post(
                "/api/trace", {"algorithm": algorithm, "start": "N24", "goal": "BL01"}
            )
            self.assertFalse(body["truncated"], algorithm)
            self.assertEqual(
                sum(1 for e in body["events"] if e["kind"] == "expand"),
                body["route"]["nodes_expanded"],
                algorithm,
            )

    def test_ida_star_summarises_the_phases_it_discards(self) -> None:
        """Only the final threshold keeps its detail, but no expansion is lost."""
        _status, body = self.post(
            "/api/trace", {"algorithm": "ida_star", "start": "CEN", "goal": "E4"}
        )
        phases = [p for p in body["phases"] if p["label"] == "threshold"]
        self.assertGreater(len(phases), 1)
        self.assertEqual(sum(p["expanded"] for p in phases), body["route"]["nodes_expanded"])
        self.assertEqual(
            sum(p["generated"] for p in phases) + body["counts"]["seeded"],
            body["route"]["nodes_generated"],
        )
        # The discarded detail is exactly what keeps the payload small.
        kept = sum(1 for e in body["events"] if e["kind"] == "expand")
        self.assertLess(kept, body["route"]["nodes_expanded"])

    def test_a_failed_search_still_replays(self) -> None:
        status, body = self.post(
            "/api/trace", {"algorithm": "hill_climbing", "start": "S4", "goal": "E20"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["solved"])
        self.assertEqual(body["events"][-1]["kind"], "stuck")
        self.assertEqual(body["events"][-1]["reason"], "local optimum")

    def test_ida_star_on_a_long_pair_stays_bounded(self) -> None:
        """RN10->A7 expands ~14.6k nodes; the response must not be enormous."""
        status, body = self.post(
            "/api/trace", {"algorithm": "ida_star", "start": "RN10", "goal": "A7"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["solved"])
        self.assertLessEqual(len(body["events"]), body["max_events"] + 1)
        self.assertGreater(len(body["phases"]), 2)
        self.assertLess(len(json.dumps(body)) / 1024 / 1024, 8.0)

    def test_trace_options_are_validated(self) -> None:
        status, _body = self.post(
            "/api/trace",
            {"start": "CEN", "goal": "E4", "trace": {"detail": "everything"}},
        )
        self.assertEqual(status, 400)


class HeuristicTest(ApiTestCase):
    def test_heuristic_is_zero_at_the_goal_and_positive_elsewhere(self) -> None:
        status, body = self.get("/api/heuristic?scenario=operating&goal=BL15")
        self.assertEqual(status, 200)
        self.assertAlmostEqual(body["h"]["BL15"], 0.0)
        self.assertEqual(len(body["h"]), 194)
        self.assertGreater(body["max_h"], 0)

    def test_unknown_goal_is_rejected(self) -> None:
        status, _body = self.get("/api/heuristic?goal=NOPE")
        self.assertEqual(status, 400)


class CompareTest(ApiTestCase):
    def test_all_five_algorithms_are_scored_against_dijkstra(self) -> None:
        status, body = self.post(
            "/api/compare", {"start": "N24", "goal": "BL01", "repeats": 2}
        )
        self.assertEqual(status, 200)
        results = {entry["algorithm"]: entry for entry in body["results"]}
        self.assertEqual(len(results), 5)
        self.assertTrue(results["astar"]["optimal"])
        self.assertFalse(results["hill_climbing"]["solved"])
        self.assertIn("error", results["hill_climbing"])
        self.assertGreater(results["greedy"]["excess_pct"], 0.0)
        self.assertAlmostEqual(
            body["optimal_sec"], results["astar"]["route"]["duration_sec"], places=6
        )


class ConcurrencyTest(ApiTestCase):
    def test_concurrent_traced_searches_do_not_collide(self) -> None:
        """_Meter drives process-global tracemalloc; without a lock these corrupt
        each other's peak_memory_bytes or raise on the second stop()."""
        payloads = [
            {"algorithm": name, "start": "CEN", "goal": "E4"}
            for name in ("astar", "greedy", "beam", "ida_star")
        ] * 2
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda p: self.post("/api/trace", p), payloads))
        for status, body in outcomes:
            self.assertEqual(status, 200)
            self.assertTrue(body["solved"])
            self.assertGreater(body["route"]["peak_memory_bytes"], 0)


class StaticTest(ApiTestCase):
    def test_a_missing_frontend_explains_itself(self) -> None:
        status, body = self.get("/")
        self.assertEqual(status, 404)
        self.assertEqual(body["code"], "no_frontend")


if __name__ == "__main__":
    unittest.main()
