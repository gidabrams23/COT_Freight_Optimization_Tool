import unittest
from unittest.mock import patch

from blueprints.prograde import routes as prograde_routes
from blueprints.prograde.services import pj_rules


class ProgradePjStackingRulesTests(unittest.TestCase):
    @staticmethod
    def _projected_upper_intrusion_intervals(canvas):
        step_x_ft = float(canvas["trailer_geometry"]["step_x_ft"])
        upper_intervals = []
        for seq, col in (canvas["zone_cols"].get("upper_deck") or {}).items():
            interval = prograde_routes._upper_column_intrusion_interval_on_lower(
                col,
                float((canvas["x_positions"].get("upper_deck") or {}).get(seq) or 0.0),
                step_x_ft,
                step_x_ft,
            )
            if interval is not None:
                upper_intervals.append(interval)
        return prograde_routes._merge_intervals(upper_intervals)

    def test_dump_stacked_height_mapping_uses_stacked_envelope_values(self):
        self.assertEqual(pj_rules.pj_dump_stacked_height_ft(3), 4.0)
        self.assertEqual(pj_rules.pj_dump_stacked_height_ft(4), 6.0)
        self.assertIsNone(pj_rules.pj_dump_stacked_height_ft(5))

    def test_non_dump_stacking_height_override_applies_to_non_gooseneck_non_dump_units(self):
        utility_sku = {"pj_category": "utility", "model": "UL"}
        gn_sku = {"pj_category": "gooseneck", "model": "LS"}
        dump_sku = {"pj_category": "dump_lowside", "model": "DL"}
        self.assertEqual(pj_rules.pj_non_dump_stacking_height_ft({"layer": 1}, utility_sku, is_top=True), 1.3)
        self.assertEqual(pj_rules.pj_non_dump_stacking_height_ft({"layer": 2}, utility_sku, is_top=False), 1.3)
        self.assertIsNone(
            pj_rules.pj_non_dump_stacking_height_ft(
                {"override_reason": "tongue_profile:gooseneck"},
                gn_sku,
                is_top=True,
            )
        )
        self.assertIsNone(pj_rules.pj_non_dump_stacking_height_ft({"layer": 1}, dump_sku, is_top=True))

    def test_gooseneck_height_rule_uses_6ft_except_when_gooseneck_above(self):
        one_gn = [
            {
                "position_id": "gn-only",
                "item_number": "LS30",
                "layer": 1,
                "is_rotated": 0,
                "override_reason": "tongue_profile:gooseneck",
            }
        ]
        two_gn = [
            {
                "position_id": "gn-bottom",
                "item_number": "LS30",
                "layer": 1,
                "is_rotated": 0,
                "override_reason": "tongue_profile:gooseneck",
            },
            {
                "position_id": "gn-top",
                "item_number": "DL14",
                "layer": 2,
                "is_rotated": 0,
                "override_reason": "tongue_profile:gooseneck",
            },
        ]
        skus = {
            "LS30": {"pj_category": "gooseneck", "model": "LS"},
            "DL14": {"pj_category": "gooseneck", "model": "DL"},
        }
        height_ref = {
            "gooseneck": {
                "height_mid_ft": 2.4,
                "height_top_ft": 2.8,
                "gn_axle_dropped_ft": None,
            }
        }
        self.assertEqual(
            pj_rules._col_height(one_gn, skus, height_ref=height_ref, offsets={}),
            6.0,
        )
        self.assertEqual(
            pj_rules._col_height(two_gn, skus, height_ref=height_ref, offsets={}),
            7.4,
        )

    def test_gn_crisscross_reduces_column_height(self):
        col = [
            {
                "position_id": "gn-l1",
                "item_number": "LS30",
                "layer": 1,
                "is_rotated": 0,
                "override_reason": "tongue_profile:gooseneck",
            },
            {
                "position_id": "gn-l2",
                "item_number": "DL14",
                "layer": 2,
                "is_rotated": 0,
                "override_reason": "tongue_profile:gooseneck",
            },
        ]
        skus = {
            "LS30": {"pj_category": "gooseneck", "model": "LS"},
            "DL14": {"pj_category": "gooseneck", "model": "DL"},
        }
        total_height = pj_rules._col_height(
            col,
            skus,
            height_ref={},
            offsets={"gn_crisscross_height_save_ft": 1.0},
        )
        self.assertEqual(total_height, 5.0)

    def test_canvas_uses_utility_stack_heights_and_right_aligns_upper_stack(self):
        session = {"brand": "pj", "carrier_type": "53_step_deck"}
        carrier = {
            "total_length_ft": 53.0,
            "lower_deck_length_ft": 41.5,
            "upper_deck_length_ft": 11.5,
            "lower_deck_ground_height_ft": 3.5,
            "upper_deck_ground_height_ft": 5.0,
            "max_height_ft": 13.5,
            "gn_max_lower_deck_ft": 32.0,
        }
        positions = [
            {
                "position_id": "p-bottom",
                "item_number": "UL20",
                "deck_zone": "upper_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:00",
            },
            {
                "position_id": "p-top",
                "item_number": "UL16",
                "deck_zone": "upper_deck",
                "sequence": 1,
                "layer": 2,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:01",
            },
        ]
        sku_map = {
            "UL20": {
                "item_number": "UL20",
                "model": "UL",
                "description": "Utility 20",
                "pj_category": "utility",
                "bed_length_measured": 20.0,
                "bed_length_stated": 20.0,
                "tongue_feet": 4.0,
                "total_footprint": 24.0,
            },
            "UL16": {
                "item_number": "UL16",
                "model": "UL",
                "description": "Utility 16",
                "pj_category": "utility",
                "bed_length_measured": 16.0,
                "bed_length_stated": 16.0,
                "tongue_feet": 4.0,
                "total_footprint": 20.0,
            },
        }
        height_ref = {
            "utility": {
                "height_mid_ft": 2.6,
                "height_top_ft": 2.9,
                "gn_axle_dropped_ft": None,
            }
        }

        with (
            patch.object(prograde_routes.db, "get_pj_height_ref_dict", return_value=height_ref),
            patch.object(prograde_routes.db, "get_pj_sku", side_effect=lambda item_number: sku_map[item_number]),
            patch.object(prograde_routes.db, "get_pj_offsets_dict", return_value={"gn_in_dump_hidden_ft": 7.0}),
            patch.object(prograde_routes.db, "mark_session_active", return_value=None),
            patch.object(prograde_routes.db, "get_acknowledged_violations", return_value=[]),
            patch.object(prograde_routes, "check_load", return_value=[]),
        ):
            canvas = prograde_routes._build_canvas_data(
                "session-1",
                session,
                carrier,
                ["lower_deck", "upper_deck"],
                positions,
                "pj",
            )

        upper_stack = sorted(canvas["zone_cols"]["upper_deck"][1], key=lambda row: int(row["layer"]))
        bottom = upper_stack[0]
        top = upper_stack[1]

        self.assertAlmostEqual(bottom["deck_component_height_ft"], 1.3, places=2)
        self.assertAlmostEqual(top["deck_component_height_ft"], 1.3, places=2)
        self.assertAlmostEqual(canvas["col_heights"]["upper_deck"][1], 2.6, places=2)

        self.assertAlmostEqual(bottom["deck_x_end_ft"], top["deck_x_end_ft"], places=3)
        self.assertGreater(top["deck_x_start_ft"], bottom["deck_x_start_ft"])

        upper_segments = [seg for seg in canvas["measure_segments_by_zone"]["upper_deck"] if seg.get("kind") == "stack"]
        self.assertEqual(len(upper_segments), 1)
        self.assertAlmostEqual(upper_segments[0]["length_ft"], 24.0, places=3)
        self.assertAlmostEqual(upper_segments[0]["x_local_ft"], -12.58, places=2)

        manifest_heights = sorted(float(row["height_each"]) for row in canvas["manifest_rows"])
        self.assertEqual(manifest_heights, [1.3, 1.3])

    def test_canvas_cross_deck_guard_resolves_upper_lower_overlap(self):
        session = {"brand": "pj", "carrier_type": "53_step_deck"}
        carrier = {
            "total_length_ft": 53.0,
            "lower_deck_length_ft": 41.5,
            "upper_deck_length_ft": 11.5,
            "lower_deck_ground_height_ft": 3.5,
            "upper_deck_ground_height_ft": 5.0,
            "max_height_ft": 13.5,
            "gn_max_lower_deck_ft": 32.0,
        }
        positions = [
            {
                "position_id": "l1",
                "item_number": "L1",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:00",
            },
            {
                "position_id": "l2",
                "item_number": "L2",
                "deck_zone": "lower_deck",
                "sequence": 2,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:01",
            },
            {
                "position_id": "u1",
                "item_number": "U1",
                "deck_zone": "upper_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": "dump_door_removed:1",
                "added_at": "2026-01-01T00:00:02",
            },
        ]
        sku_map = {
            "L1": {
                "item_number": "L1",
                "model": "L1",
                "description": "Lower 1",
                "pj_category": "dump_lowside",
                "bed_length_measured": 19.0,
                "bed_length_stated": 19.0,
                "tongue_feet": 4.5,
                "total_footprint": 23.5,
                "dump_side_height_ft": 3.0,
            },
            "L2": {
                "item_number": "L2",
                "model": "L2",
                "description": "Lower 2",
                "pj_category": "dump_lowside",
                "bed_length_measured": 15.5,
                "bed_length_stated": 15.5,
                "tongue_feet": 5.0,
                "total_footprint": 20.5,
                "dump_side_height_ft": 3.0,
            },
            "U1": {
                "item_number": "U1",
                "model": "U1",
                "description": "Upper 1",
                "pj_category": "dump_lowside",
                "bed_length_measured": 15.5,
                "bed_length_stated": 15.5,
                "tongue_feet": 5.0,
                "total_footprint": 20.5,
                "dump_side_height_ft": 3.0,
            },
        }
        height_ref = {
            "dump_lowside": {
                "height_mid_ft": 3.0,
                "height_top_ft": 3.0,
                "gn_axle_dropped_ft": None,
            }
        }
        mocked_metrics = {
            "legacy_total_ft": 53.0,
            "effective_total_ft": 52.9,
            "overlap_credit_ft": 0.0,
            "rear_underride_credit_ft": 0.0,
            "seam_clearance_credit_ft": 0.0,
            "dump_door_insert_credit_ft": 5.0,
            "gn_crisscross_credit_ft": 0.0,
            "gn_crisscross_pair_count": 0,
            "blocked_lower_ft": 4.0,
            "blocked_upper_ft": 0.0,
            "lower_base_ft": 44.0,
            "upper_base_ft": 20.5,
            "upper_seam_span_ft": 9.0,
            "lower_seam_span_ft": 2.5,
            "low_profile_seam_span_ft": 0.0,
            "lower_toward_ft": 5.0,
            "upper_toward_ft": 0.0,
        }

        with (
            patch.object(prograde_routes.db, "get_pj_height_ref_dict", return_value=height_ref),
            patch.object(prograde_routes.db, "get_pj_sku", side_effect=lambda item_number: sku_map[item_number]),
            patch.object(prograde_routes.db, "get_pj_offsets_dict", return_value={"gn_in_dump_hidden_ft": 7.0}),
            patch.object(prograde_routes, "compute_pj_length_metrics", return_value=mocked_metrics),
            patch.object(prograde_routes.db, "mark_session_active", return_value=None),
            patch.object(prograde_routes.db, "get_acknowledged_violations", return_value=[]),
            patch.object(prograde_routes, "check_load", return_value=[]),
        ):
            canvas = prograde_routes._build_canvas_data(
                "session-2",
                session,
                carrier,
                ["lower_deck", "upper_deck"],
                positions,
                "pj",
            )

        # Lower-right seam barrier should still hold.
        right_lower = canvas["zone_cols"]["lower_deck"][2][0]
        occupied_tongue_ft = float(
            right_lower.get("occupied_tongue_length_ft")
            if right_lower.get("occupied_tongue_length_ft") is not None
            else (right_lower.get("render_tongue_length_ft") or 0.0)
        )
        right_edge = (
            float(right_lower["deck_x_start_ft"])
            + float(right_lower["deck_length_ft"] or 0.0)
            + occupied_tongue_ft
        )
        self.assertLessEqual(right_edge, 37.42 + 1e-6)

        # Lower rendered envelopes should not intersect projected upper occupancy.
        step_x_ft = float(canvas["trailer_geometry"]["step_x_ft"])
        upper_intervals = []
        for seq, col in (canvas["zone_cols"].get("upper_deck") or {}).items():
            base_dims = prograde_routes._column_base_dims(col)
            render_dims = prograde_routes._column_render_envelope_dims(col)
            start = float((canvas["x_positions"].get("upper_deck") or {}).get(seq) or 0.0)
            col_right = step_x_ft + start + float(base_dims.get("deck_len_ft") or 0.0) + float(base_dims.get("right_tongue_ft") or 0.0)
            left = col_right - float(render_dims.get("full_span_ft") or 0.0)
            right = col_right
            projected_left = max(min(left, right), 0.0)
            projected_right = min(max(left, right), step_x_ft)
            if projected_right > projected_left:
                upper_intervals.append((projected_left, projected_right))
        upper_intervals = prograde_routes._merge_intervals(upper_intervals)
        self.assertTrue(upper_intervals)

        for col in (canvas.get("spatial_columns", {}).get("lower_deck") or []):
            seq = int(col.get("sequence") or 0)
            dims = prograde_routes._column_render_envelope_dims(
                (canvas["zone_cols"].get("lower_deck") or {}).get(seq) or [],
                zone="lower_deck",
            )
            start = float((canvas["x_positions"].get("lower_deck") or {}).get(seq) or 0.0)
            col_left = start - float(dims.get("left_tongue_ft") or 0.0)
            col_right = start + float(dims.get("right_reach_ft") or 0.0)
            for blocked_left, blocked_right in upper_intervals:
                overlaps = not (col_right <= blocked_left + 1e-6 or col_left >= blocked_right - 1e-6)
                self.assertFalse(
                    overlaps,
                    f"Lower column seq={seq} intersects upper intrusion [{blocked_left:.3f}, {blocked_right:.3f}]",
                )

    def test_canvas_shifts_lower_cluster_left_out_of_upper_intrusion(self):
        session = {"brand": "pj", "carrier_type": "53_step_deck"}
        carrier = {
            "total_length_ft": 53.0,
            "lower_deck_length_ft": 41.5,
            "upper_deck_length_ft": 11.5,
            "lower_deck_ground_height_ft": 3.5,
            "upper_deck_ground_height_ft": 5.0,
            "max_height_ft": 13.5,
            "gn_max_lower_deck_ft": 32.0,
        }
        positions = [
            {
                "position_id": "l-1",
                "item_number": "LOW-A",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:00",
            },
            {
                "position_id": "l-2-a",
                "item_number": "TALL-A",
                "deck_zone": "lower_deck",
                "sequence": 2,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:01",
            },
            {
                "position_id": "l-2-b",
                "item_number": "TALL-B",
                "deck_zone": "lower_deck",
                "sequence": 2,
                "layer": 2,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:02",
            },
            {
                "position_id": "l-3",
                "item_number": "LOW-ROT",
                "deck_zone": "lower_deck",
                "sequence": 3,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 1,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:03",
            },
            {
                "position_id": "u-1",
                "item_number": "UPPER-LONG",
                "deck_zone": "upper_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:04",
            },
        ]
        sku_map = {
            "LOW-A": {
                "item_number": "LOW-A",
                "model": "UL",
                "description": "Lower A",
                "pj_category": "utility",
                "bed_length_measured": 10.0,
                "bed_length_stated": 10.0,
                "tongue_feet": 2.0,
                "total_footprint": 12.0,
            },
            "TALL-A": {
                "item_number": "TALL-A",
                "model": "UL",
                "description": "Tall A",
                "pj_category": "utility",
                "bed_length_measured": 12.0,
                "bed_length_stated": 12.0,
                "tongue_feet": 2.0,
                "total_footprint": 14.0,
            },
            "TALL-B": {
                "item_number": "TALL-B",
                "model": "UL",
                "description": "Tall B",
                "pj_category": "utility",
                "bed_length_measured": 12.0,
                "bed_length_stated": 12.0,
                "tongue_feet": 2.0,
                "total_footprint": 14.0,
            },
            "LOW-ROT": {
                "item_number": "LOW-ROT",
                "model": "UL",
                "description": "Lower Rot",
                "pj_category": "utility",
                "bed_length_measured": 12.0,
                "bed_length_stated": 12.0,
                "tongue_feet": 2.0,
                "total_footprint": 14.0,
            },
            "UPPER-LONG": {
                "item_number": "UPPER-LONG",
                "model": "UL",
                "description": "Upper Long",
                "pj_category": "utility",
                "bed_length_measured": 20.0,
                "bed_length_stated": 20.0,
                "tongue_feet": 4.0,
                "total_footprint": 24.0,
            },
        }
        height_ref = {
            "utility": {
                "height_mid_ft": 2.6,
                "height_top_ft": 2.9,
                "gn_axle_dropped_ft": None,
            }
        }

        with (
            patch.object(prograde_routes.db, "get_pj_height_ref_dict", return_value=height_ref),
            patch.object(prograde_routes.db, "get_pj_sku", side_effect=lambda item_number: sku_map[item_number]),
            patch.object(prograde_routes.db, "get_pj_offsets_dict", return_value={"gn_in_dump_hidden_ft": 7.0}),
            patch.object(prograde_routes.db, "mark_session_active", return_value=None),
            patch.object(prograde_routes.db, "get_acknowledged_violations", return_value=[]),
            patch.object(prograde_routes, "check_load", return_value=[]),
        ):
            canvas = prograde_routes._build_canvas_data(
                "session-2",
                session,
                carrier,
                ["lower_deck", "upper_deck"],
                positions,
                "pj",
            )

        step_x_ft = float(canvas["trailer_geometry"]["step_x_ft"])
        upper_intervals = []
        for seq, col in (canvas["zone_cols"].get("upper_deck") or {}).items():
            base_dims = prograde_routes._column_base_dims(col)
            render_dims = prograde_routes._column_render_envelope_dims(col)
            start = float((canvas["x_positions"].get("upper_deck") or {}).get(seq) or 0.0)
            col_right = step_x_ft + start + float(base_dims.get("deck_len_ft") or 0.0) + float(base_dims.get("right_tongue_ft") or 0.0)
            left = col_right - float(render_dims.get("full_span_ft") or 0.0)
            right = col_right
            projected_left = max(min(left, right), 0.0)
            projected_right = min(max(left, right), step_x_ft)
            if projected_right > projected_left:
                upper_intervals.append((projected_left, projected_right))
        upper_intervals = prograde_routes._merge_intervals(upper_intervals)

        step_height_ft = float(canvas["trailer_geometry"]["step_height_ft"])
        low_profile_overlap_found = False
        for col in (canvas.get("spatial_columns", {}).get("lower_deck") or []):
            seq = int(col.get("sequence") or 0)
            dims = prograde_routes._column_render_envelope_dims(
                (canvas["zone_cols"].get("lower_deck") or {}).get(seq) or [],
                zone="lower_deck",
            )
            start = float((canvas["x_positions"].get("lower_deck") or {}).get(seq) or 0.0)
            col_left = start - float(dims.get("left_tongue_ft") or 0.0)
            col_right = start + float(dims.get("right_reach_ft") or 0.0)
            is_tall = float(col.get("height_ft") or 0.0) > step_height_ft + 1e-6
            for blocked_left, blocked_right in upper_intervals:
                overlaps = not (col_right <= blocked_left + 1e-6 or col_left >= blocked_right - 1e-6)
                if is_tall:
                    self.assertFalse(
                        overlaps,
                        f"Tall lower column seq={seq} intersects upper intrusion [{blocked_left:.3f}, {blocked_right:.3f}]",
                    )
                elif overlaps:
                    low_profile_overlap_found = True
        self.assertTrue(low_profile_overlap_found)

    def test_canvas_dump_door_overlap_window_shifts_lower_stack_right(self):
        session = {"brand": "pj", "carrier_type": "53_step_deck"}
        carrier = {
            "total_length_ft": 53.0,
            "lower_deck_length_ft": 41.5,
            "upper_deck_length_ft": 11.5,
            "lower_deck_ground_height_ft": 3.5,
            "upper_deck_ground_height_ft": 5.0,
            "max_height_ft": 13.5,
            "gn_max_lower_deck_ft": 32.0,
        }
        base_positions = [
            {
                "position_id": "l1",
                "item_number": "D714",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:00",
            },
            {
                "position_id": "l2a",
                "item_number": "D714",
                "deck_zone": "lower_deck",
                "sequence": 2,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:01",
            },
            {
                "position_id": "l2b",
                "item_number": "D716",
                "deck_zone": "lower_deck",
                "sequence": 2,
                "layer": 2,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:02",
            },
            {
                "position_id": "u1",
                "item_number": "D312",
                "deck_zone": "upper_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:03",
            },
        ]
        sku_map = {
            "D714": {
                "item_number": "D714",
                "model": "D7",
                "description": "Dump 14",
                "pj_category": "dump_lowside",
                "bed_length_measured": 14.0,
                "bed_length_stated": 14.0,
                "tongue_feet": 5.0,
                "total_footprint": 19.0,
                "dump_side_height_ft": 3.0,
            },
            "D716": {
                "item_number": "D716",
                "model": "DM",
                "description": "Dump 16",
                "pj_category": "dump_lowside",
                "bed_length_measured": 16.0,
                "bed_length_stated": 16.0,
                "tongue_feet": 6.0,
                "total_footprint": 22.0,
                "dump_side_height_ft": 3.0,
            },
            "D312": {
                "item_number": "D312",
                "model": "D3",
                "description": "Dump 12",
                "pj_category": "dump_lowside",
                "bed_length_measured": 12.0,
                "bed_length_stated": 12.0,
                "tongue_feet": 4.0,
                "total_footprint": 16.0,
                "dump_side_height_ft": 3.0,
            },
        }
        height_ref = {
            "dump_lowside": {
                "height_mid_ft": 3.0,
                "height_top_ft": 3.0,
                "gn_axle_dropped_ft": None,
            }
        }

        def _build_canvas(upper_door_off):
            positions = [dict(p) for p in base_positions]
            if upper_door_off:
                for p in positions:
                    if p["position_id"] == "u1":
                        p["override_reason"] = "dump_door_removed:1"
            with (
                patch.object(prograde_routes.db, "get_pj_height_ref_dict", return_value=height_ref),
                patch.object(prograde_routes.db, "get_pj_sku", side_effect=lambda item_number: sku_map[item_number]),
                patch.object(prograde_routes.db, "get_pj_offsets_dict", return_value={"gn_in_dump_hidden_ft": 7.0}),
                patch.object(prograde_routes.db, "mark_session_active", return_value=None),
                patch.object(prograde_routes.db, "get_acknowledged_violations", return_value=[]),
                patch.object(prograde_routes, "check_load", return_value=[]),
            ):
                return prograde_routes._build_canvas_data(
                    "session-door-overlap",
                    session,
                    carrier,
                    ["lower_deck", "upper_deck"],
                    positions,
                    "pj",
                )

        canvas_on = _build_canvas(False)
        canvas_off = _build_canvas(True)

        x_on = float((canvas_on["x_positions"].get("lower_deck") or {}).get(2) or 0.0)
        x_off = float((canvas_off["x_positions"].get("lower_deck") or {}).get(2) or 0.0)
        self.assertGreater(
            x_off,
            x_on,
            "Door-off upper dump should allow the rightmost lower stack to sit farther right.",
        )
        lower_col_on = {row["position_id"]: float(row.get("render_tongue_length_ft") or 0.0) for row in canvas_on["zone_cols"]["lower_deck"][2]}
        lower_col_off = {row["position_id"]: float(row.get("render_tongue_length_ft") or 0.0) for row in canvas_off["zone_cols"]["lower_deck"][2]}
        lower_col_off_occupied = {
            row["position_id"]: float(row.get("occupied_tongue_length_ft") or row.get("render_tongue_length_ft") or 0.0)
            for row in canvas_off["zone_cols"]["lower_deck"][2]
        }
        self.assertAlmostEqual(lower_col_on["l2a"], 5.0, places=2)
        self.assertAlmostEqual(lower_col_on["l2b"], 6.0, places=2)
        self.assertAlmostEqual(lower_col_off["l2a"], 5.0, places=2)
        self.assertAlmostEqual(lower_col_off["l2b"], 6.0, places=2)
        self.assertAlmostEqual(lower_col_off_occupied["l2a"], 1.0, places=2)
        self.assertAlmostEqual(lower_col_off_occupied["l2b"], 1.0, places=2)

        stack_on = next(
            seg for seg in (canvas_on["measure_segments_by_zone"]["lower_deck"] or [])
            if seg.get("kind") == "stack" and int(seg.get("sequence") or 0) == 2
        )
        stack_off = next(
            seg for seg in (canvas_off["measure_segments_by_zone"]["lower_deck"] or [])
            if seg.get("kind") == "stack" and int(seg.get("sequence") or 0) == 2
        )
        self.assertAlmostEqual(float(stack_off["length_ft"]), 15.0, places=3)
        self.assertAlmostEqual(float(stack_on["length_ft"]) - float(stack_off["length_ft"]), 4.0, places=3)

        upper_intrusion = self._projected_upper_intrusion_intervals(canvas_off)
        seam_gap_ft = None

        step_height_ft = float(canvas_off["trailer_geometry"]["step_height_ft"])
        for col in (canvas_off.get("spatial_columns", {}).get("lower_deck") or []):
            seq = int(col.get("sequence") or 0)
            is_tall = float(col.get("height_ft") or 0.0) > step_height_ft + 1e-6
            if not is_tall:
                continue
            dims = prograde_routes._column_render_envelope_dims(
                (canvas_off["zone_cols"].get("lower_deck") or {}).get(seq) or [],
                zone="lower_deck",
            )
            start = float((canvas_off["x_positions"].get("lower_deck") or {}).get(seq) or 0.0)
            col_left = start - float(dims.get("left_tongue_ft") or 0.0)
            col_right = start + float(dims.get("right_reach_ft") or 0.0)
            if seq == 2:
                gaps = [
                    float(blocked_left) - col_right
                    for blocked_left, _ in upper_intrusion
                    if float(blocked_left) >= col_right - 1e-6
                ]
                if gaps:
                    seam_gap_ft = min(gaps)
            for blocked_left, blocked_right in upper_intrusion:
                overlaps = not (col_right <= blocked_left + 1e-6 or col_left >= blocked_right - 1e-6)
                self.assertFalse(
                    overlaps,
                    f"Tall lower column seq={seq} intersects upper intrusion [{blocked_left:.3f}, {blocked_right:.3f}]",
                )
        self.assertIsNotNone(seam_gap_ft)
        self.assertAlmostEqual(seam_gap_ft, 1.0, places=2)

    def test_canvas_non_dump_door_override_token_does_not_shift_utility_layout(self):
        session = {"brand": "pj", "carrier_type": "53_step_deck"}
        carrier = {
            "total_length_ft": 53.0,
            "lower_deck_length_ft": 41.5,
            "upper_deck_length_ft": 11.5,
            "lower_deck_ground_height_ft": 3.5,
            "upper_deck_ground_height_ft": 5.0,
            "max_height_ft": 13.5,
            "gn_max_lower_deck_ft": 32.0,
        }
        base_positions = [
            {
                "position_id": "l1",
                "item_number": "UL20",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:00",
            },
            {
                "position_id": "l2",
                "item_number": "UL16",
                "deck_zone": "lower_deck",
                "sequence": 2,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:01",
            },
            {
                "position_id": "u1",
                "item_number": "UL16",
                "deck_zone": "upper_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 0,
                "override_reason": None,
                "added_at": "2026-01-01T00:00:02",
            },
        ]
        sku_map = {
            "UL20": {
                "item_number": "UL20",
                "model": "UL",
                "description": "Utility 20",
                "pj_category": "utility",
                "bed_length_measured": 20.0,
                "bed_length_stated": 20.0,
                "tongue_feet": 4.0,
                "total_footprint": 24.0,
            },
            "UL16": {
                "item_number": "UL16",
                "model": "UL",
                "description": "Utility 16",
                "pj_category": "utility",
                "bed_length_measured": 16.0,
                "bed_length_stated": 16.0,
                "tongue_feet": 4.0,
                "total_footprint": 20.0,
            },
        }
        height_ref = {
            "utility": {
                "height_mid_ft": 2.6,
                "height_top_ft": 2.9,
                "gn_axle_dropped_ft": None,
            }
        }

        def _build_canvas(force_non_dump_door_token):
            positions = [dict(p) for p in base_positions]
            if force_non_dump_door_token:
                positions[2]["override_reason"] = "dump_door_removed:1"
            with (
                patch.object(prograde_routes.db, "get_pj_height_ref_dict", return_value=height_ref),
                patch.object(prograde_routes.db, "get_pj_sku", side_effect=lambda item_number: sku_map[item_number]),
                patch.object(prograde_routes.db, "get_pj_offsets_dict", return_value={"gn_in_dump_hidden_ft": 7.0}),
                patch.object(prograde_routes.db, "mark_session_active", return_value=None),
                patch.object(prograde_routes.db, "get_acknowledged_violations", return_value=[]),
                patch.object(prograde_routes, "check_load", return_value=[]),
            ):
                return prograde_routes._build_canvas_data(
                    "session-utility-regression",
                    session,
                    carrier,
                    ["lower_deck", "upper_deck"],
                    positions,
                    "pj",
                )

        canvas_base = _build_canvas(False)
        canvas_with_token = _build_canvas(True)
        self.assertEqual(canvas_base["x_positions"], canvas_with_token["x_positions"])
        self.assertEqual(canvas_base["measure_segments_by_zone"], canvas_with_token["measure_segments_by_zone"])

    def test_canvas_utility_layers_keep_tongues_clear_of_gooseneck_wall_plane(self):
        session = {"brand": "pj", "carrier_type": "53_step_deck"}
        carrier = {
            "total_length_ft": 53.0,
            "lower_deck_length_ft": 41.5,
            "upper_deck_length_ft": 11.5,
            "lower_deck_ground_height_ft": 3.5,
            "upper_deck_ground_height_ft": 5.0,
            "max_height_ft": 13.5,
            "gn_max_lower_deck_ft": 32.0,
        }
        positions = [
            {
                "position_id": "l1",
                "item_number": "TS24",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 1,
                "override_reason": "tongue_profile:gooseneck",
                "added_at": "2026-01-01T00:00:00",
            },
            {
                "position_id": "l2",
                "item_number": "DL16",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 2,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 1,
                "override_reason": "dump_height_ft:3.0;tongue_profile:gooseneck",
                "added_at": "2026-01-01T00:00:01",
            },
            {
                "position_id": "l3",
                "item_number": "UL12",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 3,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 1,
                "override_reason": "tongue_profile:standard",
                "added_at": "2026-01-01T00:00:02",
            },
            {
                "position_id": "l4",
                "item_number": "UC10",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 4,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 1,
                "override_reason": "tongue_profile:standard",
                "added_at": "2026-01-01T00:00:03",
            },
            {
                "position_id": "u1",
                "item_number": "DM16",
                "deck_zone": "upper_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 1,
                "override_reason": "tongue_profile:gooseneck",
                "added_at": "2026-01-01T00:00:04",
            },
        ]
        sku_map = {
            "TS24": {
                "item_number": "TS24",
                "model": "TS",
                "description": "Tilt 24",
                "pj_category": "tilt",
                "bed_length_measured": 24.0,
                "bed_length_stated": 24.0,
                "tongue_feet": 4.5,
                "total_footprint": 28.5,
            },
            "DL16": {
                "item_number": "DL16",
                "model": "DL",
                "description": "Dump 16",
                "pj_category": "dump_lowside",
                "bed_length_measured": 17.5,
                "bed_length_stated": 17.5,
                "tongue_feet": 6.0,
                "total_footprint": 23.5,
                "dump_side_height_ft": 3.0,
            },
            "UL12": {
                "item_number": "UL12",
                "model": "UL",
                "description": "Utility 12",
                "pj_category": "utility",
                "bed_length_measured": 12.0,
                "bed_length_stated": 12.0,
                "tongue_feet": 4.5,
                "total_footprint": 16.5,
            },
            "UC10": {
                "item_number": "UC10",
                "model": "UC",
                "description": "Utility 10",
                "pj_category": "utility",
                "bed_length_measured": 10.0,
                "bed_length_stated": 10.0,
                "tongue_feet": 4.5,
                "total_footprint": 14.5,
            },
            "DM16": {
                "item_number": "DM16",
                "model": "DM",
                "description": "Dump 16",
                "pj_category": "dump_variants",
                "bed_length_measured": 17.5,
                "bed_length_stated": 17.5,
                "tongue_feet": 6.0,
                "total_footprint": 23.5,
                "dump_side_height_ft": 3.0,
            },
        }
        height_ref = {
            "tilt": {
                "height_mid_ft": 3.0,
                "height_top_ft": 3.2,
                "gn_axle_dropped_ft": None,
            },
            "dump_lowside": {
                "height_mid_ft": 3.0,
                "height_top_ft": 3.0,
                "gn_axle_dropped_ft": None,
            },
            "dump_variants": {
                "height_mid_ft": 3.0,
                "height_top_ft": 3.0,
                "gn_axle_dropped_ft": None,
            },
            "utility": {
                "height_mid_ft": 2.6,
                "height_top_ft": 2.9,
                "gn_axle_dropped_ft": None,
            },
        }

        with (
            patch.object(prograde_routes.db, "get_pj_height_ref_dict", return_value=height_ref),
            patch.object(prograde_routes.db, "get_pj_sku", side_effect=lambda item_number: sku_map[item_number]),
            patch.object(prograde_routes.db, "get_pj_offsets_dict", return_value={"gn_in_dump_hidden_ft": 7.0}),
            patch.object(prograde_routes.db, "mark_session_active", return_value=None),
            patch.object(prograde_routes.db, "get_acknowledged_violations", return_value=[]),
            patch.object(prograde_routes, "check_load", return_value=[]),
        ):
            canvas = prograde_routes._build_canvas_data(
                "session-gn-utility-anchor",
                session,
                carrier,
                ["lower_deck", "upper_deck"],
                positions,
                "pj",
            )

        lower_col = sorted(canvas["zone_cols"]["lower_deck"][1], key=lambda row: int(row.get("layer") or 0))
        dl_host = next(row for row in lower_col if row["position_id"] == "l2")
        utility_layers = [row for row in lower_col if row["position_id"] in {"l3", "l4"}]
        gn_clearance = float(prograde_routes._GOOSENECK_WALL_CLEARANCE_FT)
        for row in utility_layers:
            self.assertAlmostEqual(
                float(row["tongue_x_end_ft"]),
                float(dl_host["deck_x_start_ft"]) + gn_clearance,
                places=3,
            )
            self.assertGreater(float(row["deck_x_start_ft"]), float(dl_host["deck_x_start_ft"]))
        utility_layers = sorted(utility_layers, key=lambda row: int(row.get("layer") or 0))
        self.assertAlmostEqual(
            float(utility_layers[0]["y_surface_ft"]),
            float(dl_host["y_surface_ft"]) + float(dl_host["deck_component_height_ft"]),
            places=3,
        )
        self.assertAlmostEqual(
            float(utility_layers[1]["y_surface_ft"]),
            float(utility_layers[0]["y_surface_ft"]) + float(utility_layers[0]["deck_component_height_ft"]),
            places=3,
        )

        ts_row = next(row for row in lower_col if row["position_id"] == "l1")
        self.assertAlmostEqual(float(ts_row["deck_component_height_ft"]), float(ts_row["true_height_ft"]), places=3)
        self.assertAlmostEqual(float(dl_host["deck_component_height_ft"]), float(dl_host["true_height_ft"]), places=3)
        self.assertAlmostEqual(float(dl_host["stacking_height_ft"]), 6.0, places=3)

        upper_intrusion = self._projected_upper_intrusion_intervals(canvas)
        self.assertTrue(upper_intrusion)
        dims = prograde_routes._column_render_envelope_dims(
            canvas["zone_cols"]["lower_deck"][1],
            zone="lower_deck",
        )
        lower_start = float((canvas["x_positions"].get("lower_deck") or {}).get(1) or 0.0)
        lower_right = lower_start + float(dims.get("right_reach_ft") or 0.0)
        gaps = [
            float(blocked_left) - lower_right
            for blocked_left, _ in upper_intrusion
            if float(blocked_left) >= lower_right - 1e-6
        ]
        self.assertTrue(gaps)
        self.assertAlmostEqual(min(gaps), 0.08, places=2)

    def test_canvas_right_packs_stack_into_adjacent_gooseneck_wall(self):
        session = {"brand": "pj", "carrier_type": "53_step_deck"}
        carrier = {
            "total_length_ft": 53.0,
            "lower_deck_length_ft": 41.5,
            "upper_deck_length_ft": 11.5,
            "lower_deck_ground_height_ft": 3.5,
            "upper_deck_ground_height_ft": 5.0,
            "max_height_ft": 13.5,
            "gn_max_lower_deck_ft": 32.0,
        }
        positions = [
            {
                "position_id": "l1",
                "item_number": "DL16",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 1,
                "override_reason": "dump_height_ft:3.0;tongue_profile:gooseneck",
                "added_at": "2026-01-01T00:00:00",
            },
            {
                "position_id": "l2",
                "item_number": "UL12",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 2,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 1,
                "override_reason": "tongue_profile:standard",
                "added_at": "2026-01-01T00:00:01",
            },
            {
                "position_id": "l3",
                "item_number": "UC10",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 3,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 1,
                "override_reason": "tongue_profile:standard",
                "added_at": "2026-01-01T00:00:02",
            },
            {
                "position_id": "r1",
                "item_number": "DM16",
                "deck_zone": "lower_deck",
                "sequence": 2,
                "layer": 1,
                "is_nested": 0,
                "nested_inside": None,
                "gn_axle_dropped": 0,
                "is_rotated": 1,
                "override_reason": "dump_height_ft:3.0;tongue_profile:gooseneck",
                "added_at": "2026-01-01T00:00:03",
            },
        ]
        sku_map = {
            "DL16": {
                "item_number": "DL16",
                "model": "DL",
                "description": "Dump 16",
                "pj_category": "dump_lowside",
                "bed_length_measured": 17.5,
                "bed_length_stated": 17.5,
                "tongue_feet": 6.0,
                "total_footprint": 23.5,
                "dump_side_height_ft": 3.0,
            },
            "UL12": {
                "item_number": "UL12",
                "model": "UL",
                "description": "Utility 12",
                "pj_category": "utility",
                "bed_length_measured": 12.0,
                "bed_length_stated": 12.0,
                "tongue_feet": 4.5,
                "total_footprint": 16.5,
            },
            "UC10": {
                "item_number": "UC10",
                "model": "UC",
                "description": "Utility 10",
                "pj_category": "utility",
                "bed_length_measured": 10.0,
                "bed_length_stated": 10.0,
                "tongue_feet": 4.5,
                "total_footprint": 14.5,
            },
            "DM16": {
                "item_number": "DM16",
                "model": "DM",
                "description": "Dump 16",
                "pj_category": "dump_variants",
                "bed_length_measured": 17.5,
                "bed_length_stated": 17.5,
                "tongue_feet": 6.0,
                "total_footprint": 23.5,
                "dump_side_height_ft": 3.0,
            },
        }
        height_ref = {
            "dump_lowside": {
                "height_mid_ft": 3.0,
                "height_top_ft": 3.0,
                "gn_axle_dropped_ft": None,
            },
            "dump_variants": {
                "height_mid_ft": 3.0,
                "height_top_ft": 3.0,
                "gn_axle_dropped_ft": None,
            },
            "utility": {
                "height_mid_ft": 2.6,
                "height_top_ft": 2.9,
                "gn_axle_dropped_ft": None,
            },
        }

        with (
            patch.object(prograde_routes.db, "get_pj_height_ref_dict", return_value=height_ref),
            patch.object(prograde_routes.db, "get_pj_sku", side_effect=lambda item_number: sku_map[item_number]),
            patch.object(prograde_routes.db, "get_pj_offsets_dict", return_value={"gn_in_dump_hidden_ft": 7.0}),
            patch.object(prograde_routes.db, "mark_session_active", return_value=None),
            patch.object(prograde_routes.db, "get_acknowledged_violations", return_value=[]),
            patch.object(prograde_routes, "check_load", return_value=[]),
        ):
            canvas = prograde_routes._build_canvas_data(
                "session-right-pack-gn",
                session,
                carrier,
                ["lower_deck", "upper_deck"],
                positions,
                "pj",
            )

        left_col = (canvas["zone_cols"].get("lower_deck") or {}).get(1) or []
        right_col = (canvas["zone_cols"].get("lower_deck") or {}).get(2) or []
        left_start = float((canvas["x_positions"].get("lower_deck") or {}).get(1) or 0.0)
        right_start = float((canvas["x_positions"].get("lower_deck") or {}).get(2) or 0.0)
        left_dims = prograde_routes._column_render_envelope_dims(left_col, zone="lower_deck")
        left_right_x = left_start + float(left_dims.get("right_reach_ft") or 0.0)
        right_wall_x = prograde_routes._column_gooseneck_wall_x_local(right_col, right_start)
        self.assertIsNotNone(right_wall_x)
        self.assertAlmostEqual(
            float(right_wall_x) - float(left_right_x),
            float(prograde_routes._GOOSENECK_WALL_CLEARANCE_FT),
            places=2,
        )


if __name__ == "__main__":
    unittest.main()
