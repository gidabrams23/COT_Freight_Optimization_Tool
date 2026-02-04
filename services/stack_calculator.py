TRAILER_CONFIGS = {
    "STEP_DECK": {"capacity": 53.0, "lower": 43.0, "upper": 10.0},
    "FLATBED": {"capacity": 53.0, "lower": 53.0, "upper": 0.0},
    "WEDGE": {"capacity": 51.0, "lower": 51.0, "upper": 0.0},
}


def _resolve_trailer_config(trailer_type, capacity_feet=None):
    trailer_key = (trailer_type or "STEP_DECK").strip().upper()
    base = dict(TRAILER_CONFIGS.get(trailer_key, TRAILER_CONFIGS["STEP_DECK"]))
    if capacity_feet:
        try:
            capacity = float(capacity_feet)
        except (TypeError, ValueError):
            capacity = base["capacity"]
        base["capacity"] = capacity
        if base["upper"] > 0:
            base["lower"] = max(capacity - base["upper"], 0)
        else:
            base["lower"] = capacity
    base["type"] = trailer_key
    return base


def calculate_stack_configuration(order_lines, trailer_type="STEP_DECK", capacity_feet=None):
    if not order_lines:
        return {
            "positions": [],
            "total_linear_feet": 0,
            "utilization_pct": 0,
            "max_stack_height": 0,
            "compatibility_issues": [],
            "exceeds_capacity": False,
            "utilization_credit_ft": 0,
            "utilization_grade": "F",
        }

    positions = []
    has_order_ids = any(item.get("order_id") for item in order_lines)

    if not has_order_ids:
        sorted_items = sorted(
            order_lines,
            key=lambda x: (x.get("unit_length_ft") or 0, x.get("max_stack_height") or 0),
            reverse=True,
        )

        for item in sorted_items:
            qty_remaining = item["qty"]
            max_stack = item["max_stack_height"] or 1
            length_ft = item["unit_length_ft"] or 0

            while qty_remaining > 0:
                candidates = [
                    pos
                    for pos in positions
                    if pos["length_ft"] >= length_ft and pos["capacity_used"] < 0.99
                ]
                if candidates:
                    candidates.sort(
                        key=lambda pos: (pos["length_ft"], -(1.0 - pos["capacity_used"]))
                    )
                    target = candidates[0]
                else:
                    target = {
                        "length_ft": length_ft,
                        "items": [],
                        "capacity_used": 0.0,
                        "units_count": 0,
                    }
                    positions.append(target)

                capacity_available = 1.0 - target["capacity_used"]
                max_units_that_fit = int(capacity_available * max_stack)
                if max_units_that_fit <= 0:
                    target["capacity_used"] = 1.0
                    continue

                units_to_add = min(qty_remaining, max_units_that_fit)
                capacity_fraction = units_to_add / max_stack
                target["items"].append(
                    {
                        "item": item["item"],
                        "sku": item["sku"],
                        "category": item.get("category", "UNKNOWN"),
                        "units": units_to_add,
                        "max_stack": max_stack,
                        "unit_length_ft": length_ft,
                        "order_id": item.get("order_id"),
                    }
                )
                target["capacity_used"] += capacity_fraction
                target["units_count"] += units_to_add
                qty_remaining -= units_to_add
    else:
        order_buckets = {}
        for line in order_lines:
            order_id = line.get("order_id") or "__UNSPECIFIED__"
            order_buckets.setdefault(order_id, []).append(line)

        # Fill the trailer left-to-right while keeping each order contiguous in the schematic.
        # If a stack has remaining capacity, the next order may stack on top of it.
        cursor = 0
        for order_id, items in order_buckets.items():
            sorted_items = sorted(
                items,
                key=lambda x: (x.get("unit_length_ft") or 0, x.get("max_stack_height") or 0),
                reverse=True,
            )
            for item in sorted_items:
                qty_remaining = item["qty"]
                max_stack = item["max_stack_height"] or 1
                length_ft = item["unit_length_ft"] or 0

                while qty_remaining > 0:
                    if cursor >= len(positions):
                        positions.append(
                            {
                                "length_ft": length_ft,
                                "items": [],
                                "capacity_used": 0.0,
                                "units_count": 0,
                            }
                        )

                    target = positions[cursor]
                    if target["length_ft"] < length_ft:
                        cursor += 1
                        continue

                    capacity_available = 1.0 - target["capacity_used"]
                    max_units_that_fit = int(capacity_available * max_stack)
                    if max_units_that_fit <= 0:
                        target["capacity_used"] = 1.0
                        cursor += 1
                        continue

                    units_to_add = min(qty_remaining, max_units_that_fit)
                    capacity_fraction = units_to_add / max_stack
                    target["items"].append(
                        {
                            "item": item["item"],
                            "sku": item["sku"],
                            "category": item.get("category", "UNKNOWN"),
                            "units": units_to_add,
                            "max_stack": max_stack,
                            "unit_length_ft": length_ft,
                            "order_id": order_id,
                        }
                    )
                    target["capacity_used"] += capacity_fraction
                    target["units_count"] += units_to_add
                    qty_remaining -= units_to_add

                    if (1.0 - target["capacity_used"]) < 0.01:
                        target["capacity_used"] = 1.0
                        cursor += 1

    trailer_config = _resolve_trailer_config(trailer_type, capacity_feet)
    upper_length = trailer_config["upper"]
    lower_length = trailer_config["lower"]
    capacity = trailer_config["capacity"]

    for pos in positions:
        pos["deck"] = "lower"

    if upper_length > 0:
        upper_candidates = [
            pos for pos in positions if pos["length_ft"] <= upper_length
        ]
        upper_candidates.sort(key=lambda pos: pos["length_ft"], reverse=True)
        remaining_upper = upper_length
        for pos in upper_candidates:
            if pos["length_ft"] <= remaining_upper:
                pos["deck"] = "upper"
                remaining_upper -= pos["length_ft"]

    for pos in positions:
        deck_length = upper_length if pos["deck"] == "upper" else lower_length
        if deck_length:
            pos["width_pct"] = min(round((pos["length_ft"] / deck_length) * 100, 1), 100)
        else:
            pos["width_pct"] = 0

    total_linear_feet = sum(pos["length_ft"] for pos in positions)
    total_credit_feet = sum(
        pos["length_ft"] * min(pos["capacity_used"], 1.0) for pos in positions
    )
    utilization_pct = (total_credit_feet / capacity) * 100 if total_credit_feet else 0
    max_stack_height = max((pos["units_count"] for pos in positions), default=0)
    compatibility_issues = check_stacking_compatibility(positions)
    exceeds_capacity = _exceeds_capacity(positions, trailer_config)
    utilization_grade = _grade_utilization(utilization_pct)

    return {
        "positions": positions,
        "total_linear_feet": round(total_linear_feet, 1),
        "utilization_pct": round(utilization_pct, 1),
        "max_stack_height": max_stack_height,
        "compatibility_issues": compatibility_issues,
        "exceeds_capacity": exceeds_capacity,
        "utilization_credit_ft": round(total_credit_feet, 1),
        "utilization_grade": utilization_grade,
        "trailer_type": trailer_config["type"],
        "capacity_feet": capacity,
        "lower_deck_length": lower_length,
        "upper_deck_length": upper_length,
    }


def check_stacking_compatibility(positions):
    issues = []
    for idx, pos in enumerate(positions):
        categories = [item["category"] for item in pos["items"] if item.get("category")]
        if "DUMP" in categories and len(set(categories)) > 1:
            issues.append(
                f"Position {idx + 1}: DUMP trailers cannot mix with other types."
            )

        if pos["units_count"] > 5:
            issues.append(
                f"Position {idx + 1}: Stack of {pos['units_count']} units may be unstable."
            )

        skus = [item["sku"] for item in pos["items"] if item.get("sku")]
        has_woody = any("WOODY" in sku for sku in skus)
        if has_woody and len(pos["items"]) > 1:
            issues.append(
                f"Position {idx + 1}: Mix includes wooden floor. Verify compatibility."
            )

    return issues


def _grade_utilization(utilization_pct):
    if utilization_pct >= 85:
        return "A"
    if utilization_pct >= 70:
        return "B"
    if utilization_pct >= 55:
        return "C"
    if utilization_pct >= 40:
        return "D"
    return "F"


def _exceeds_capacity(positions, trailer_config):
    lower_length = trailer_config["lower"]
    upper_length = trailer_config["upper"]

    if upper_length <= 0:
        total_length = sum(pos["length_ft"] for pos in positions)
        return total_length > lower_length

    lower_total = sum(
        pos["length_ft"] for pos in positions if pos.get("deck") == "lower"
    )
    upper_total = sum(
        pos["length_ft"] for pos in positions if pos.get("deck") == "upper"
    )
    return lower_total > lower_length or upper_total > upper_length
