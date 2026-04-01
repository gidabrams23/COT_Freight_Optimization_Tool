"""
Shawn's measurement conventions for PJ trailers.

Called at seed time and whenever a Settings offset is saved.
Computes bed_length_measured and total_footprint for each PJ SKU.
"""


def compute_measured_length(model: str, bed_length_stated: float, pj_category: str, offsets: dict) -> float:
    """
    offsets: dict keyed by rule_key from pj_measurement_offsets table.

    Car haulers / deck overs: add spare mount offset (default +1').
    All dumps: add tarp kit offset (default +1').
    DTJ dumps: add additional cylinder offset (default +1').
    Everything else: stated length IS measured length.
    """
    car_hauler_cats = {"car_hauler", "deck_over", "car_hauler_deckover", "tilt_deckover"}
    dump_cats = {
        "dump_lowside", "dump_highside_3ft", "dump_highside_4ft",
        "dump_small", "dump_gn", "dump_variants",
    }

    if pj_category in car_hauler_cats:
        return bed_length_stated + offsets.get("car_hauler_spare_mount_offset", 1.0)

    if pj_category in dump_cats:
        base = bed_length_stated + offsets.get("dump_tarp_kit_offset", 1.0)
        if model.upper().startswith("DTJ"):
            base += offsets.get("dtj_cylinder_extra_offset", 1.0)
        return base

    # GNs, utilities, tilts, pintle
    return bed_length_stated


def compute_total_footprint(bed_length_measured: float, tongue_feet: float) -> float:
    return bed_length_measured + tongue_feet


def recompute_sku(sku: dict, offsets: dict) -> dict:
    """
    Given a sku row (dict) and the current offsets dict, return updated
    bed_length_measured and total_footprint.
    """
    measured = compute_measured_length(
        sku["model"],
        sku["bed_length_stated"],
        sku["pj_category"],
        offsets,
    )
    footprint = compute_total_footprint(measured, sku["tongue_feet"])
    return {"bed_length_measured": round(measured, 2), "total_footprint": round(footprint, 2)}
