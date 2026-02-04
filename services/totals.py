def calculate_order_line_total_feet(order_line):
    qty = order_line.get("qty", 0) or 0
    feet_per_unit = order_line.get("feet_per_unit", 0) or 0
    return qty * feet_per_unit


def calculate_order_line_totals(order_lines):
    total_lines = len(order_lines)
    total_qty = sum(line.get("qty", 0) or 0 for line in order_lines)
    total_feet = sum(calculate_order_line_total_feet(line) for line in order_lines)
    return {
        "total_lines": total_lines,
        "total_qty": total_qty,
        "total_feet": total_feet,
    }
