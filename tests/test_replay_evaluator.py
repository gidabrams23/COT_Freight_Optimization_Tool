import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import replay_evaluator


def _csv_file(content, filename="report.csv"):
    return SimpleNamespace(filename=filename, stream=io.BytesIO(content.encode("utf-8")))


class _FakeOptimizer:
    def _group_by_so_num(self, line_rows, order_summary_map):
        grouped = {}
        for line in line_rows:
            so_num = str(line.get("so_num") or "").strip()
            if not so_num:
                continue
            grouped.setdefault(so_num, []).append(line)
        groups = []
        for so_num, lines in grouped.items():
            groups.append({"key": so_num, "lines": lines})
        return groups

    def _build_load(self, groups, params):
        lines = []
        for group in groups:
            lines.extend(group.get("lines") or [])
        return {
            "lines": lines,
            "utilization_pct": 60.0,
            "estimated_miles": 100.0,
            "estimated_cost": 1000.0,
        }


class _ParityOptimizer:
    def __init__(self, overfill_by_group):
        self.overfill_by_group = dict(overfill_by_group or {})

    def _preferred_trailer_for_groups(self, groups, _fallback):
        return "STEP_DECK"

    def _groups_require_wedge(self, _groups):
        return False

    def _stack_config_for_groups(self, groups, _params, trailer_type=None):
        key = tuple(sorted(str(group.get("key") or "") for group in (groups or [])))
        overfill = float(self.overfill_by_group.get(key, 0.0))
        allowance = 4.0
        lower_length = 53.0
        return {
            "positions": [{"length_ft": lower_length + allowance + overfill, "deck": "lower"}],
            "lower_deck_length": lower_length,
            "upper_deck_length": 0.0,
            "max_back_overhang_ft": allowance,
            "trailer_type": (trailer_type or "STEP_DECK").upper(),
            "utilization_pct": 80.0,
            "exceeds_capacity": overfill > 0.0,
        }


class ReplayEvaluatorParserTests(unittest.TestCase):
    def test_parse_report_accepts_expected_columns(self):
        file_obj = _csv_file(
            "Load Number,Date Created,Order Number,MOH Est Freight Cost ($),Truck Use,Miles\n"
            "GA26-1987,02/17/2026,12605527,1080.0,,450\n"
        )
        result = replay_evaluator.parse_report(file_obj)
        self.assertEqual(result["total_rows"], 1)
        self.assertEqual(result["valid_rows"], 1)
        self.assertEqual(result["rows"][0]["plant_code"], "GA")
        self.assertEqual(result["rows"][0]["order_number"], "12605527")

    def test_parse_report_rejects_missing_required_columns(self):
        file_obj = _csv_file("Load Number,Date Created\nGA26-1987,02/17/2026\n")
        with self.assertRaises(ValueError):
            replay_evaluator.parse_report(file_obj)

    def test_parse_report_extracts_plant_code(self):
        file_obj = _csv_file(
            "Load Number,Date Created,Order Number\n"
            "VA26-2101,2026-02-18,12600001\n"
        )
        result = replay_evaluator.parse_report(file_obj)
        self.assertEqual(result["rows"][0]["plant_code"], "VA")


class ReplayEvaluatorServiceTests(unittest.TestCase):
    @patch("services.replay_evaluator.Optimizer", new=_FakeOptimizer)
    @patch("services.replay_evaluator._optimize_groups_v2")
    @patch("services.replay_evaluator.db.list_order_lines_for_so_nums")
    @patch("services.replay_evaluator.db.list_orders_by_so_nums")
    def test_duplicate_order_is_flagged_but_day_still_evaluates(
        self,
        mock_orders_by_so,
        mock_lines_by_so,
        mock_optimize_groups,
    ):
        rows = [
            {
                "date_created": "2026-02-18",
                "plant_code": "GA",
                "load_number": "GA26-2001",
                "order_number": "126001",
                "moh_est_freight_cost": 100.0,
                "truck_use": None,
                "miles": 50.0,
                "ship_via_date": "",
                "full_name": "",
            },
            {
                "date_created": "2026-02-18",
                "plant_code": "GA",
                "load_number": "GA26-2002",
                "order_number": "126001",
                "moh_est_freight_cost": 120.0,
                "truck_use": None,
                "miles": 60.0,
                "ship_via_date": "",
                "full_name": "",
            },
        ]
        mock_orders_by_so.return_value = [{"so_num": "126001"}]
        mock_lines_by_so.return_value = [{"so_num": "126001", "id": 1}]
        mock_optimize_groups.return_value = [
            {
                "lines": [{"so_num": "126001"}],
                "utilization_pct": 80.0,
                "estimated_miles": 70.0,
                "estimated_cost": 900.0,
            }
        ]

        day_rows, issues, _ = replay_evaluator._evaluate_buckets(rows, preset={})
        self.assertEqual(len(day_rows), 1)
        issue_types = {item["issue_type"] for item in issues}
        self.assertIn("duplicate_order_multiple_loads", issue_types)

    @patch("services.replay_evaluator.Optimizer", new=_FakeOptimizer)
    @patch("services.replay_evaluator._optimize_groups_v2")
    @patch("services.replay_evaluator.db.list_order_lines_for_so_nums")
    @patch("services.replay_evaluator.db.list_orders_by_so_nums")
    def test_missing_order_is_recorded_and_delta_is_computed(
        self,
        mock_orders_by_so,
        mock_lines_by_so,
        mock_optimize_groups,
    ):
        rows = [
            {
                "date_created": "2026-02-19",
                "plant_code": "IA",
                "load_number": "IA26-1001",
                "order_number": "A1",
                "moh_est_freight_cost": 50.0,
                "truck_use": None,
                "miles": 20.0,
                "ship_via_date": "",
                "full_name": "",
            },
            {
                "date_created": "2026-02-19",
                "plant_code": "IA",
                "load_number": "IA26-1002",
                "order_number": "MISSING",
                "moh_est_freight_cost": 40.0,
                "truck_use": None,
                "miles": 15.0,
                "ship_via_date": "",
                "full_name": "",
            },
        ]
        mock_orders_by_so.return_value = [{"so_num": "A1"}]
        mock_lines_by_so.return_value = [{"so_num": "A1", "id": 1}]
        mock_optimize_groups.return_value = [
            {
                "lines": [{"so_num": "A1"}],
                "utilization_pct": 70.0,
                "estimated_miles": 90.0,
                "estimated_cost": 800.0,
            }
        ]

        day_rows, issues, _ = replay_evaluator._evaluate_buckets(rows, preset={})
        self.assertEqual(len(day_rows), 1)
        day_row = day_rows[0]
        self.assertGreaterEqual(day_row["missing_orders"], 1)
        self.assertLess(day_row["delta_total_cost"], 0.0)
        issue_types = {item["issue_type"] for item in issues}
        self.assertIn("missing_order", issue_types)

    @patch("services.replay_evaluator.Optimizer", new=_FakeOptimizer)
    @patch("services.replay_evaluator._optimize_groups_v2")
    @patch("services.replay_evaluator.db.list_order_lines_for_so_nums")
    @patch("services.replay_evaluator.db.list_orders_by_so_nums")
    def test_v2_can_select_flatbed_trailer_candidate_when_lower_cost(
        self,
        mock_orders_by_so,
        mock_lines_by_so,
        mock_optimize_groups,
    ):
        rows = [
            {
                "date_created": "2026-02-19",
                "plant_code": "GA",
                "load_number": "GA26-1001",
                "order_number": "A1",
                "moh_est_freight_cost": 50.0,
                "truck_use": None,
                "miles": 20.0,
                "ship_via_date": "",
                "full_name": "",
            }
        ]
        mock_orders_by_so.return_value = [{"so_num": "A1"}]
        mock_lines_by_so.return_value = [{"so_num": "A1", "id": 1}]

        def _side_effect(_optimizer, _groups, params):
            trailer = (params.get("trailer_type") or "").upper()
            if trailer == "FLATBED":
                return [
                    {
                        "lines": [{"so_num": "A1"}],
                        "trailer_type": "FLATBED",
                        "utilization_pct": 82.0,
                        "estimated_miles": 95.0,
                        "estimated_cost": 700.0,
                    }
                ]
            return [
                {
                    "lines": [{"so_num": "A1"}],
                    "trailer_type": "STEP_DECK",
                    "utilization_pct": 70.0,
                    "estimated_miles": 90.0,
                    "estimated_cost": 900.0,
                }
            ]

        mock_optimize_groups.side_effect = _side_effect

        day_rows, issues, _ = replay_evaluator._evaluate_buckets(rows, preset={})
        self.assertEqual(len(day_rows), 1)
        self.assertEqual(day_rows[0]["optimized_strategy"], "v2_flatbed")
        issue_types = {item["issue_type"] for item in issues}
        self.assertIn("optimizer_trailer_mode", issue_types)

    @patch("services.replay_evaluator._optimize_groups_v2_with_trailer_candidates")
    def test_ops_parity_falls_back_to_strict_when_overfill_count_exceeds_envelope(
        self,
        mock_optimize,
    ):
        g1 = {"key": "G1"}
        g2 = {"key": "G2"}
        optimizer = _ParityOptimizer(
            overfill_by_group={
                ("G1",): 5.0,
                ("G2",): 3.0,
            }
        )
        strict_loads = [
            {
                "groups": [g1, g2],
                "estimated_cost": 900.0,
                "utilization_pct": 82.0,
                "estimated_miles": 100.0,
                "lines": [],
            }
        ]
        parity_loads = [
            {"groups": [g1], "estimated_cost": 350.0, "utilization_pct": 82.0, "estimated_miles": 50.0, "lines": []},
            {"groups": [g2], "estimated_cost": 350.0, "utilization_pct": 81.0, "estimated_miles": 50.0, "lines": []},
        ]
        mock_optimize.side_effect = [
            ("v2_step_deck", strict_loads, {"trailer_type": "STEP_DECK"}),
            ("v2_step_deck", parity_loads, {"trailer_type": "STEP_DECK"}),
        ]

        result = replay_evaluator._select_optimized_replay_result(
            optimizer=optimizer,
            optimization_groups=[g1, g2],
            params={
                "trailer_type": "STEP_DECK",
                "max_back_overhang_ft": 4.0,
                "ops_parity_enabled": True,
                "ops_parity_max_utilization_pct": 120.0,
            },
            baseline_group_sets=[("LOAD-1", [g1])],
        )

        self.assertTrue(result["parity_enabled"])
        self.assertFalse(result["parity_applied"])
        self.assertEqual(result["strategy"], "v2_step_deck")
        self.assertIn("count", (result["parity_reject_reason"] or "").lower())

    @patch("services.replay_evaluator._optimize_groups_v2_with_trailer_candidates")
    def test_ops_parity_applies_when_guardrails_pass_and_cost_beats_strict(
        self,
        mock_optimize,
    ):
        g1 = {"key": "G1"}
        g2 = {"key": "G2"}
        optimizer = _ParityOptimizer(
            overfill_by_group={
                ("G1",): 5.0,
                ("G2",): 4.0,
            }
        )
        strict_loads = [
            {
                "groups": [g1, g2],
                "estimated_cost": 950.0,
                "utilization_pct": 80.0,
                "estimated_miles": 100.0,
                "lines": [],
            }
        ]
        parity_loads = [
            {
                "groups": [g2],
                "estimated_cost": 800.0,
                "utilization_pct": 79.0,
                "estimated_miles": 90.0,
                "lines": [],
            }
        ]
        mock_optimize.side_effect = [
            ("v2_step_deck", strict_loads, {"trailer_type": "STEP_DECK"}),
            ("v2_step_deck", parity_loads, {"trailer_type": "STEP_DECK"}),
        ]

        result = replay_evaluator._select_optimized_replay_result(
            optimizer=optimizer,
            optimization_groups=[g1, g2],
            params={
                "trailer_type": "STEP_DECK",
                "max_back_overhang_ft": 4.0,
                "ops_parity_enabled": True,
                "ops_parity_max_utilization_pct": 120.0,
            },
            baseline_group_sets=[("LOAD-1", [g1])],
        )

        self.assertTrue(result["parity_enabled"])
        self.assertTrue(result["parity_applied"])
        self.assertEqual(result["strategy"], "v2_step_deck_ops_parity")


class ReplayEvaluatorReproduceTests(unittest.TestCase):
    @patch("services.replay_evaluator.db.get_replay_eval_run")
    def test_reproduce_requires_completed_source_run(self, mock_get_run):
        mock_get_run.return_value = {"id": 99, "status": "RUNNING"}
        with self.assertRaises(ValueError):
            replay_evaluator.reproduce_replay_bucket(99, "2026-02-18", "GA")

    @patch("services.replay_evaluator.db.list_replay_eval_source_rows")
    @patch("services.replay_evaluator.db.get_replay_eval_run")
    def test_reproduce_requires_stored_source_rows(self, mock_get_run, mock_list_rows):
        mock_get_run.return_value = {"id": 99, "status": "COMPLETED", "params_json": "{}"}
        mock_list_rows.return_value = []
        with self.assertRaises(ValueError):
            replay_evaluator.reproduce_replay_bucket(99, "2026-02-18", "GA")

    @patch("services.replay_evaluator._finalize_replay_run")
    @patch("services.replay_evaluator._start_replay_run")
    @patch("services.replay_evaluator.db.list_replay_eval_source_rows")
    @patch("services.replay_evaluator.db.get_replay_eval_run")
    def test_reproduce_creates_new_run_from_source_rows(
        self,
        mock_get_run,
        mock_list_rows,
        mock_start_run,
        mock_finalize,
    ):
        mock_get_run.return_value = {
            "id": 15,
            "status": "COMPLETED",
            "filename": "baseline.xlsx",
            "params_json": "{\"capacity_feet\": 53}",
        }
        mock_list_rows.return_value = [
            {
                "date_created": "2026-02-18",
                "plant_code": "GA",
                "load_number": "GA26-1001",
                "order_number": "126001",
            }
        ]
        mock_start_run.return_value = 77

        run_id = replay_evaluator.reproduce_replay_bucket(15, "2026-02-18", "ga", created_by="Admin")
        self.assertEqual(run_id, 77)
        mock_finalize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
