"""
Seed all reference data and SKU masters into the ProGrade database.
Run once: python seed.py
Re-running is safe — uses INSERT OR REPLACE.
"""
import sqlite3
from datetime import datetime
import db
from services.pj_measurement import compute_measured_length, compute_total_footprint

NOW = datetime.utcnow().isoformat()


def seed_carrier_configs(conn):
    rows = [
        # carrier_type, brand, total_len, max_ht, lower_len, upper_len, lower_gnd, upper_gnd, gn_max_lower, notes
        ("53_step_deck", "pj",     53.0, 13.5, 41.0, 12.0, 3.5, 5.0, 32.0, "Standard PJ step-deck carrier"),
        ("53_flatbed",   "bigtex", 53.0, 13.5, 53.0,  0.0, 4.0, 0.0,  0.0, "Standard Big Tex flatbed carrier"),
    ]
    conn.executemany("""
        INSERT OR REPLACE INTO carrier_configs
        (carrier_type, brand, total_length_ft, max_height_ft,
         lower_deck_length_ft, upper_deck_length_ft,
         lower_deck_ground_height_ft, upper_deck_ground_height_ft,
         gn_max_lower_deck_ft, notes, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, [r + (NOW,) for r in rows])


def seed_pj_tongue_groups(conn):
    rows = [
        # group_id, label, tongue_feet, model_codes, notes
        ("c_channel",  "C-Channel Hitch",    4.0, "BH, CC, CH, CS, LP, SA, UT",         "Standard bumper-pull c-channel tongue"),
        ("deck_over",  "Deck Over",           5.0, "DO, LD, LD2, LD3",                   "Deck-over bumper-pull tongue"),
        ("dump_std",   "Dump Standard",       6.0, "DL, DM, DT, DV, DW, DX, DTJ, DT1",  "Standard dump trailer tongue"),
        ("dump_small", "Dump Small",          5.0, "D5, D7",                              "Small/compact dump tongue"),
        ("pintle",     "Pintle Hook",         4.0, "PHT, PT",                             "Pintle hook hitch"),
        ("gooseneck",  "Gooseneck / LDQ",     9.0, "GN, LDG, LDW, LDQ",                 "Gooseneck draw-bar; tongue hides inside GN neck"),
    ]
    conn.executemany("""
        INSERT OR REPLACE INTO pj_tongue_groups
        (group_id, group_label, tongue_feet, model_codes, notes, updated_at)
        VALUES (?,?,?,?,?,?)
    """, [r + (NOW,) for r in rows])


def seed_pj_height_reference(conn):
    # category, label, height_mid_ft, height_top_ft, gn_axle_dropped_ft, notes
    rows = [
        ("car_hauler",          "Car Hauler",           1.50, 2.50, None, "Flat bed with ramps; mid/top refer to position in stack"),
        ("car_hauler_deckover", "Car Hauler Deck Over", 1.75, 2.75, None, ""),
        ("deck_over",           "Deck Over",            1.75, 2.75, None, ""),
        ("tilt_deckover",       "Tilt Deck Over",       1.75, 2.75, None, ""),
        ("tilt",                "Tilt",                 1.50, 2.00, None, ""),
        ("utility",             "Utility",              1.25, 1.75, None, "Small utility trailer"),
        ("dump_lowside",        "Dump — Low Side",      2.00, 3.00, None, "Low-side dump; team to confirm side height"),
        ("dump_highside_3ft",   "Dump — High Side 3'",  2.50, 3.50, None, "3' high side"),
        ("dump_highside_4ft",   "Dump — High Side 4'",  3.00, 4.00, None, "4' high side"),
        ("dump_small",          "Dump — Small",         1.75, 2.50, None, "Compact dump (D5/D7 class)"),
        ("dump_gn",             "Dump — Gooseneck",     2.50, 3.50, None, "GN dump"),
        ("dump_variants",       "Dump — Variants",      2.25, 3.25, None, "DTJ, DT1 and other variant dumps"),
        ("gooseneck",           "Gooseneck",            2.50, 2.50, 2.00, "Axle-drop reduces stacked height"),
        ("pintle",              "Pintle",               1.50, 2.00, None, ""),
    ]
    conn.executemany("""
        INSERT OR REPLACE INTO pj_height_reference
        (category, label, height_mid_ft, height_top_ft, gn_axle_dropped_ft, notes, updated_at)
        VALUES (?,?,?,?,?,?,?)
    """, [r + (NOW,) for r in rows])


def seed_pj_measurement_offsets(conn):
    rows = [
        ("car_hauler_spare_mount_offset", "Extra feet for car hauler spare mount",     1.0, "Applied to all car hauler and deck-over categories"),
        ("dump_tarp_kit_offset",          "Extra feet for dump tarp kit",              1.0, "Applied to all dump categories"),
        ("dtj_cylinder_extra_offset",     "Additional feet for DTJ cylinder",         1.0, "Stacks on top of tarp offset; DTJ models only"),
        ("gn_in_dump_hidden_ft",          "Feet of GN tongue hidden inside dump body", 7.0, "Subtracted from GN footprint when nested inside dump"),
    ]
    conn.executemany("""
        INSERT OR REPLACE INTO pj_measurement_offsets
        (rule_key, label, offset_ft, notes, updated_at)
        VALUES (?,?,?,?,?)
    """, [r + (NOW,) for r in rows])


def seed_pj_skus(conn):
    offsets = {
        "car_hauler_spare_mount_offset": 1.0,
        "dump_tarp_kit_offset": 1.0,
        "dtj_cylinder_extra_offset": 1.0,
        "gn_in_dump_hidden_ft": 7.0,
    }
    tongue = {
        "c_channel": 4.0,
        "deck_over":  5.0,
        "dump_std":   6.0,
        "dump_small": 5.0,
        "pintle":     4.0,
        "gooseneck":  9.0,
    }

    # item_number, model, pj_category, description, gvwr, bed_length_stated, tongue_group,
    # dump_side_height_ft, can_nest_inside_dump, gn_axle_droppable, tongue_overlap_allowed
    raw = [
        # ── Car Haulers ──────────────────────────────────────────────────────
        ("83CC14", "CC", "car_hauler", "Car Hauler 14'",  7000,  14.0, "c_channel", None, 0, 0, 0),
        ("83CC16", "CC", "car_hauler", "Car Hauler 16'",  7000,  16.0, "c_channel", None, 0, 0, 0),
        ("83CC18", "CC", "car_hauler", "Car Hauler 18'",  7000,  18.0, "c_channel", None, 0, 0, 0),
        ("83CC20", "CC", "car_hauler", "Car Hauler 20'", 10000,  20.0, "c_channel", None, 0, 0, 0),
        # ── Deck Over ────────────────────────────────────────────────────────
        ("102DO20", "DO", "deck_over", "Deck Over 20'", 14000, 20.0, "deck_over", None, 0, 0, 0),
        ("102DO24", "DO", "deck_over", "Deck Over 24'", 14000, 24.0, "deck_over", None, 0, 0, 0),
        # ── Tilts ────────────────────────────────────────────────────────────
        ("83T814",  "T8", "tilt", "Tilt 8K 14'",  7000, 14.0, "c_channel", None, 0, 0, 0),
        ("83T820",  "T8", "tilt", "Tilt 8K 20'",  7000, 20.0, "c_channel", None, 0, 0, 0),
        ("83T826",  "T8", "tilt", "Tilt 8K 26'", 10000, 26.0, "c_channel", None, 0, 0, 0),
        # ── Utilities ────────────────────────────────────────────────────────
        ("83SA12",  "SA", "utility", "Single Axle Utility 12'",  3500, 12.0, "c_channel", None, 0, 0, 0),
        ("83SA14",  "SA", "utility", "Single Axle Utility 14'",  3500, 14.0, "c_channel", None, 0, 0, 0),
        ("83SA16",  "SA", "utility", "Single Axle Utility 16'",  3500, 16.0, "c_channel", None, 0, 0, 0),
        # ── Dumps — Low Side ─────────────────────────────────────────────────
        ("83DL10",  "DL", "dump_lowside", "Dump Low Side 10'",  7000, 10.0, "dump_std", None,   0, 0, 0),
        ("83DL12",  "DL", "dump_lowside", "Dump Low Side 12'",  7000, 12.0, "dump_std", None,   0, 0, 0),
        ("83DL14",  "DL", "dump_lowside", "Dump Low Side 14'", 10000, 14.0, "dump_std", None,   0, 0, 0),
        ("83DL16",  "DL", "dump_lowside", "Dump Low Side 16'", 14000, 16.0, "dump_std", None,   0, 0, 0),
        # ── Dumps — High Side 3' ─────────────────────────────────────────────
        ("83DV14",  "DV", "dump_highside_3ft", "Dump Hi-Side 3ft 14'", 14000, 14.0, "dump_std", 3.0, 0, 0, 0),
        ("83DV16",  "DV", "dump_highside_3ft", "Dump Hi-Side 3ft 16'", 14000, 16.0, "dump_std", 3.0, 0, 0, 0),
        # ── Dumps — High Side 4' ─────────────────────────────────────────────
        ("83DX14",  "DX", "dump_highside_4ft", "Dump Hi-Side 4ft 14'", 14000, 14.0, "dump_std", 4.0, 0, 0, 0),
        ("83DX16",  "DX", "dump_highside_4ft", "Dump Hi-Side 4ft 16'", 14000, 16.0, "dump_std", 4.0, 0, 0, 0),
        # ── Dump Variants — DM ───────────────────────────────────────────────
        ("83DM12",  "DM", "dump_variants", "Dump Master 12'", 14000, 12.0, "dump_std", None, 0, 0, 0),
        ("83DM14",  "DM", "dump_variants", "Dump Master 14'", 14000, 14.0, "dump_std", None, 0, 0, 0),
        # ── Dump Variants — DTJ (+ cylinder offset) ───────────────────────────
        ("83DTJ12", "DTJ", "dump_variants", "Dump Tarp Jack 12'", 14000, 12.0, "dump_std", None, 0, 0, 0),
        ("83DTJ14", "DTJ", "dump_variants", "Dump Tarp Jack 14'", 14000, 14.0, "dump_std", None, 0, 0, 0),
        # ── Dump Variants — DT1 ──────────────────────────────────────────────
        ("83DT114", "DT1", "dump_variants", "Dump Tarp 1 14'", 14000, 14.0, "dump_std", None, 0, 0, 0),
        # ── Dump — Small (D5 — nests inside dumps) ───────────────────────────
        ("72D510",  "D5",  "dump_small", "Dump Small 5K 10'",  5000, 10.0, "dump_small", None, 1, 0, 0),
        ("72D512",  "D5",  "dump_small", "Dump Small 5K 12'",  5000, 12.0, "dump_small", None, 1, 0, 0),
        # ── Dump — Small (D7) ─────────────────────────────────────────────────
        ("83D712",  "D7",  "dump_small", "Dump 7K 12'",  7000, 12.0, "dump_small", None, 0, 0, 0),
        ("83D714",  "D7",  "dump_small", "Dump 7K 14'",  7000, 14.0, "dump_small", None, 0, 0, 0),
        # ── Goosenecks (LDQ) ─────────────────────────────────────────────────
        ("LDQ220",  "LDQ", "gooseneck", "Low-Profile Deck-Over GN 20'", 14000, 20.0, "gooseneck", None, 0, 1, 0),
        ("LDQ224",  "LDQ", "gooseneck", "Low-Profile Deck-Over GN 24'", 14000, 24.0, "gooseneck", None, 0, 1, 0),
        ("LDQ228",  "LDQ", "gooseneck", "Low-Profile Deck-Over GN 28'", 14000, 28.0, "gooseneck", None, 0, 1, 0),
        ("LDQ232",  "LDQ", "gooseneck", "Low-Profile Deck-Over GN 32'", 14000, 32.0, "gooseneck", None, 0, 1, 0),
        ("LDQ240",  "LDQ", "gooseneck", "Low-Profile Deck-Over GN 40'", 21000, 40.0, "gooseneck", None, 0, 1, 0),
        # ── Goosenecks (LDG / LDW) ───────────────────────────────────────────
        ("LDG220",  "LDG", "gooseneck", "Low-Profile GN 20'",  14000, 20.0, "gooseneck", None, 0, 1, 0),
        ("LDG224",  "LDG", "gooseneck", "Low-Profile GN 24'",  14000, 24.0, "gooseneck", None, 0, 1, 0),
        ("LDW220",  "LDW", "gooseneck", "Low-Profile Wide GN 20'", 14000, 20.0, "gooseneck", None, 0, 1, 1),
        ("LDW224",  "LDW", "gooseneck", "Low-Profile Wide GN 24'", 14000, 24.0, "gooseneck", None, 0, 1, 1),
    ]

    rows = []
    for r in raw:
        (item_number, model, pj_category, description, gvwr, bed_stated,
         tongue_group, dump_side_ht, can_nest, gn_drop, overlap) = r
        tf = tongue[tongue_group]
        measured = compute_measured_length(model, bed_stated, pj_category, offsets)
        footprint = compute_total_footprint(measured, tf)
        rows.append((
            item_number, model, pj_category, description, gvwr,
            bed_stated, round(measured, 2), tongue_group, tf, round(footprint, 2),
            dump_side_ht, can_nest, gn_drop, overlap, None, None, NOW
        ))

    conn.executemany("""
        INSERT OR REPLACE INTO pj_skus
        (item_number, model, pj_category, description, gvwr,
         bed_length_stated, bed_length_measured, tongue_group, tongue_feet, total_footprint,
         dump_side_height_ft, can_nest_inside_dump, gn_axle_droppable, tongue_overlap_allowed,
         pairing_rule, notes, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)


def seed_bigtex_skus(conn):
    # item_number, mcat, tier, model, gvwr, floor_type, bed_length, width, tongue, stack_height
    raw = [
        # ── Goosenecks ────────────────────────────────────────────────────────
        ("BT-14GN-20", "Gooseneck", 1, "14GN", 14000, "standard",  20.0, 83.0,  9.0, 2.50),
        ("BT-14GN-22", "Gooseneck", 1, "14GN", 14000, "standard",  22.0, 83.0,  9.0, 2.50),
        ("BT-14GN-25", "Gooseneck", 1, "14GN", 14000, "standard",  25.0, 83.0,  9.0, 2.50),
        ("BT-14GN-28", "Gooseneck", 1, "14GN", 14000, "standard",  28.0, 83.0,  9.0, 2.50),
        ("BT-14GN-30", "Gooseneck", 1, "14GN", 14000, "standard",  30.0, 83.0,  9.0, 2.50),
        ("BT-22GN-20", "Gooseneck", 2, "22GN", 22000, "standard",  20.0, 83.0,  9.0, 2.50),
        ("BT-22GN-25", "Gooseneck", 2, "22GN", 22000, "standard",  25.0, 83.0,  9.0, 2.50),
        ("BT-22GN-30", "Gooseneck", 2, "22GN", 22000, "standard",  30.0, 83.0,  9.0, 2.50),
        ("BT-25GN-20", "Gooseneck", 2, "25GN", 25000, "standard",  20.0, 102.0, 9.0, 2.50),
        ("BT-25GN-25", "Gooseneck", 2, "25GN", 25000, "standard",  25.0, 102.0, 9.0, 2.50),
        # OA — cannot be bottom unit in a stack
        ("BT-OA-20",   "Gooseneck", 3, "OA",   14000, "standard",  20.0, 83.0,  9.0, 2.50),
        ("BT-OA-25",   "Gooseneck", 3, "OA",   14000, "standard",  25.0, 83.0,  9.0, 2.50),
        # ── Utility (Single Axle) ─────────────────────────────────────────────
        ("BT-35SA-10",  "Utility",   2, "35SA", 3500, "wood",   10.0, 83.0, 3.0, 1.25),
        ("BT-35SA-12",  "Utility",   2, "35SA", 3500, "wood",   12.0, 83.0, 3.0, 1.25),
        ("BT-35SA-14",  "Utility",   2, "35SA", 3500, "wood",   14.0, 83.0, 3.0, 1.25),
        ("BT-35SA-16",  "Utility",   2, "35SA", 3500, "wood",   16.0, 83.0, 3.0, 1.25),
        ("BT-70SA-12",  "Utility",   1, "70SA", 7000, "wood",   12.0, 83.0, 3.0, 1.25),
        ("BT-70SA-14",  "Utility",   1, "70SA", 7000, "wood",   14.0, 83.0, 3.0, 1.25),
        ("BT-70SA-16",  "Utility",   1, "70SA", 7000, "wood",   16.0, 83.0, 3.0, 1.25),
        # ── Utility (Tandem Axle) ─────────────────────────────────────────────
        ("BT-77TA-16",  "Utility",   1, "77TA", 7000, "wood",   16.0, 83.0, 4.0, 1.50),
        ("BT-77TA-18",  "Utility",   1, "77TA", 7000, "wood",   18.0, 83.0, 4.0, 1.50),
        ("BT-77TA-20",  "Utility",   1, "77TA", 7000, "wood",   20.0, 83.0, 4.0, 1.50),
        # ── Dump (Low Profile) ───────────────────────────────────────────────
        ("BT-14LP-10",  "Dump",      2, "14LP", 14000, "standard", 10.0, 83.0, 5.0, 2.00),
        ("BT-14LP-12",  "Dump",      2, "14LP", 14000, "standard", 12.0, 83.0, 5.0, 2.00),
        ("BT-14LP-14",  "Dump",      2, "14LP", 14000, "standard", 14.0, 83.0, 5.0, 2.00),
        ("BT-16LP-12",  "Dump",      1, "16LP", 16000, "standard", 12.0, 83.0, 5.0, 2.00),
        ("BT-16LP-14",  "Dump",      1, "16LP", 16000, "standard", 14.0, 83.0, 5.0, 2.00),
        # ── Dump (Hydraulic) ─────────────────────────────────────────────────
        ("BT-14HD-12",  "Dump",      3, "14HD", 14000, "hydraulic", 12.0, 83.0, 5.0, 2.25),
        ("BT-14HD-14",  "Dump",      3, "14HD", 14000, "hydraulic", 14.0, 83.0, 5.0, 2.25),
        ("BT-20HD-14",  "Dump",      2, "20HD", 20000, "hydraulic", 14.0, 83.0, 5.0, 2.25),
    ]

    rows = []
    for r in raw:
        (item_number, mcat, tier, model, gvwr, floor_type, bed_length, width, tongue, stack_height) = r
        total_fp = round(bed_length + tongue, 2)
        rows.append((item_number, mcat, tier, model, gvwr, floor_type,
                     bed_length, width, tongue, stack_height, total_fp, NOW))

    conn.executemany("""
        INSERT OR REPLACE INTO bigtex_skus
        (item_number, mcat, tier, model, gvwr, floor_type,
         bed_length, width, tongue, stack_height, total_footprint, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)


def seed_bt_stack_configs(conn):
    # config_id, label, load_type, stack_position, max_length_ft, max_height_ft, notes
    rows = [
        ("utility_3stack_stack_1",      "3-Stack Utility — Stack 1", "utility_3stack", "stack_1",      None, 5.25, "Individual cap; combined with S2 ≤ 40'"),
        ("utility_3stack_stack_2",      "3-Stack Utility — Stack 2", "utility_3stack", "stack_2",      None, 5.25, "Individual cap; combined with S1 ≤ 40'"),
        ("utility_3stack_stack_3",      "3-Stack Utility — Stack 3", "utility_3stack", "stack_3",      15.5, 4.00, ""),
        ("utility_3stack_combined_1_2", "3-Stack Utility — S1+S2 Combined", "utility_3stack", "combined_1_2", 40.0, None, "Longest unit in S1 + longest in S2 ≤ 40'"),
        ("utility_2stack_combined_1_2", "2-Stack Utility — S1+S2 Combined", "utility_2stack", "combined_1_2", 40.5, None, ""),
        ("dump_3stack_stack_1",         "3-Stack Dump — Stack 1",    "dump_3stack",    "stack_1",      21.0, 5.00, ""),
        ("dump_3stack_stack_2",         "3-Stack Dump — Stack 2",    "dump_3stack",    "stack_2",      16.0, 5.00, ""),
        ("dump_3stack_stack_3",         "3-Stack Dump — Stack 3",    "dump_3stack",    "stack_3",      16.0, 5.00, ""),
        ("gooseneck_stack_1",           "Gooseneck — Stack 1",       "gooseneck",      "stack_1",      53.0, 13.5, "Full deck length; no fixed stack cap for GN loads"),
    ]
    conn.executemany("""
        INSERT OR REPLACE INTO bt_stack_configs
        (config_id, label, load_type, stack_position, max_length_ft, max_height_ft, notes, updated_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, [r + (NOW,) for r in rows])


def seed_patterns(conn):
    """Seed known-good load patterns from Shawn's drawings and email examples."""
    import json
    rows = [
        # pattern_id, brand, pattern_name, load_type, carrier_type, source, confidence, positions_json, unit_count, notes
        (
            "pj-cc-mix-01",
            "pj",
            "CC Mix — 2x CC20 Lower + 1x CC18 Upper",
            "car_hauler",
            "53_step_deck",
            "manual_entry",
            4,
            json.dumps([
                {"deck_zone": "lower_deck", "sequence": 1, "layer": 1, "item_hint": "83CC20"},
                {"deck_zone": "lower_deck", "sequence": 2, "layer": 1, "item_hint": "83CC20"},
                {"deck_zone": "upper_deck", "sequence": 1, "layer": 1, "item_hint": "83CC18"},
            ]),
            3,
            "Standard car hauler mix. Total footprint approx 25+25+23=73' — verify LDG stacking if swapped.",
        ),
        (
            "pj-ldq-cc-01",
            "pj",
            "LDQ232 Lower + CC18 + CC16 Upper",
            "gooseneck_cc",
            "53_step_deck",
            "manual_entry",
            4,
            json.dumps([
                {"deck_zone": "lower_deck", "sequence": 1, "layer": 1, "item_hint": "LDQ232"},
                {"deck_zone": "upper_deck", "sequence": 1, "layer": 1, "item_hint": "83CC18"},
                {"deck_zone": "upper_deck", "sequence": 2, "layer": 1, "item_hint": "83CC16"},
            ]),
            3,
            "LDQ32 on lower (spans step — verify clearance). CC on upper. Total ~41+23+21=85' — exceeds cap; adjust bed lengths.",
        ),
        (
            "pj-dump-mix-01",
            "pj",
            "DL14 + DL12 Lower, CC18 Upper",
            "dump_cc",
            "53_step_deck",
            "manual_entry",
            3,
            json.dumps([
                {"deck_zone": "lower_deck", "sequence": 1, "layer": 1, "item_hint": "83DL14"},
                {"deck_zone": "lower_deck", "sequence": 2, "layer": 1, "item_hint": "83DL12"},
                {"deck_zone": "upper_deck", "sequence": 1, "layer": 1, "item_hint": "83CC18"},
            ]),
            3,
            "Two dumps lower deck, car hauler upper. Common PJ mixed load pattern.",
        ),
        (
            "bt-gn-3stack-01",
            "bigtex",
            "14GN 3-Stack — 25/22/20",
            "gooseneck",
            "53_flatbed",
            "manual_entry",
            5,
            json.dumps([
                {"deck_zone": "stack_1", "sequence": 1, "layer": 1, "item_hint": "BT-14GN-25"},
                {"deck_zone": "stack_1", "sequence": 1, "layer": 2, "item_hint": "BT-14GN-20"},
                {"deck_zone": "stack_2", "sequence": 1, "layer": 1, "item_hint": "BT-14GN-22"},
                {"deck_zone": "stack_2", "sequence": 1, "layer": 2, "item_hint": "BT-14GN-20"},
                {"deck_zone": "stack_3", "sequence": 1, "layer": 1, "item_hint": "BT-35SA-14"},
            ]),
            5,
            "High-confidence 14GN 3-stack pattern. S1+S2 combined = 34+31 = 65' — EXCEEDS utility cap; only valid for GN config.",
        ),
        (
            "bt-utility-3stack-01",
            "bigtex",
            "Utility 3-Stack — SA16/SA14/SA12",
            "utility",
            "53_flatbed",
            "manual_entry",
            4,
            json.dumps([
                {"deck_zone": "stack_1", "sequence": 1, "layer": 1, "item_hint": "BT-77TA-20"},
                {"deck_zone": "stack_1", "sequence": 1, "layer": 2, "item_hint": "BT-70SA-16"},
                {"deck_zone": "stack_2", "sequence": 1, "layer": 1, "item_hint": "BT-70SA-14"},
                {"deck_zone": "stack_2", "sequence": 1, "layer": 2, "item_hint": "BT-35SA-14"},
                {"deck_zone": "stack_3", "sequence": 1, "layer": 1, "item_hint": "BT-35SA-12"},
            ]),
            5,
            "Standard 3-stack utility. S1+S2 combined = 24+18 = 42' — check against 40' combined cap; may need to adjust.",
        ),
    ]
    conn.executemany("""
        INSERT OR REPLACE INTO load_patterns
        (pattern_id, brand, pattern_name, load_type, carrier_type, source, confidence,
         positions_json, unit_count, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, [r + (NOW,) for r in rows])


def run():
    db.init_db()
    conn = db.get_db()
    print("Seeding carrier configs...")
    seed_carrier_configs(conn)
    print("Seeding PJ tongue groups...")
    seed_pj_tongue_groups(conn)
    print("Seeding PJ height reference...")
    seed_pj_height_reference(conn)
    print("Seeding PJ measurement offsets...")
    seed_pj_measurement_offsets(conn)
    print("Seeding PJ SKUs...")
    seed_pj_skus(conn)
    print("Seeding Big Tex SKUs...")
    seed_bigtex_skus(conn)
    print("Seeding BT stack configs...")
    seed_bt_stack_configs(conn)
    print("Seeding load patterns...")
    seed_patterns(conn)
    conn.commit()
    conn.close()
    print("Done. Database seeded.")


if __name__ == "__main__":
    run()
