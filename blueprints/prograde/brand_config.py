# Brand-level constants and structural definitions.
# These are the defaults; live values are stored in carrier_configs and editable in Settings.

BRANDS = ["pj", "bigtex"]

# Default carrier geometry (mirrors seed data in seed.py)
CARRIER_DEFAULTS = {
    "53_step_deck": {
        "brand": "pj",
        "label": "53' Step Deck",
        "total_length_ft": 53.0,
        "max_height_ft": 13.5,
        "lower_deck_length_ft": 41.0,
        "upper_deck_length_ft": 12.0,
        "lower_deck_ground_height_ft": 3.5,
        "upper_deck_ground_height_ft": 5.0,
        "lower_deck_clearance_ft": 10.0,   # 13.5 - 3.5
        "upper_deck_clearance_ft": 8.5,    # 13.5 - 5.0
        "gn_max_lower_deck_ft": 32.0,
    },
    "53_flatbed": {
        "brand": "bigtex",
        "label": "53' Flatbed",
        "total_length_ft": 53.0,
        "max_height_ft": 13.5,
        "lower_deck_length_ft": 53.0,
        "upper_deck_length_ft": 0.0,
        "lower_deck_ground_height_ft": 4.0,
        "upper_deck_ground_height_ft": 0.0,
        "lower_deck_clearance_ft": 9.5,
        "upper_deck_clearance_ft": 0.0,
        "gn_max_lower_deck_ft": 0.0,
    },
}

# Deck zones per brand/carrier
DECK_ZONES = {
    "pj": ["lower_deck", "upper_deck"],
    "bigtex": ["lower_deck", "upper_deck"],
}

ZONE_LABELS = {
    "lower_deck": "Lower Deck (41')",
    "upper_deck": "Upper Deck (12')",
}

# PJ categories used throughout constraint engine and Settings
PJ_CATEGORIES = [
    ("car_hauler",          "Car Hauler"),
    ("deck_over",           "Deck Over"),
    ("car_hauler_deckover", "Car Hauler Deck Over"),
    ("tilt_deckover",       "Tilt Deck Over"),
    ("tilt",                "Tilt"),
    ("utility",             "Utility"),
    ("dump_lowside",        "Dump — Low Side"),
    ("dump_highside_3ft",   "Dump — High Side 3'"),
    ("dump_highside_4ft",   "Dump — High Side 4'"),
    ("dump_small",          "Dump — Small"),
    ("dump_gn",             "Dump — Gooseneck"),
    ("dump_variants",       "Dump — Variants"),
    ("gooseneck",           "Gooseneck"),
    ("pintle",              "Pintle"),
]

PJ_CATEGORY_DICT = dict(PJ_CATEGORIES)

# Models that can host a nested D5
PJ_DUMP_HOSTS = {"DL", "DV", "DX", "D7", "DM"}

# Big Tex load types used in stack config logic
BT_LOAD_TYPES = [
    ("utility_3stack", "3-Stack Utility"),
    ("utility_2stack", "2-Stack Utility"),
    ("dump_3stack",    "3-Stack Dump"),
    ("gooseneck",      "Gooseneck"),
]
