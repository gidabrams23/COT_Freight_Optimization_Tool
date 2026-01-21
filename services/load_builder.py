def build_loads(order_lines, capacity_feet):
    if capacity_feet <= 0:
        return []

    def sort_key(line):
        due_date = line.get("due_date")
        created_at = line.get("created_at") or ""
        return (0 if due_date else 1, due_date or "", created_at)

    sorted_lines = sorted(order_lines, key=sort_key)
    loads = []
    current_load = {"capacity_feet": capacity_feet, "total_feet": 0, "lines": []}

    for line in sorted_lines:
        line_total = float(line.get("line_total_feet") or 0)
        if line_total > capacity_feet:
            if current_load["lines"]:
                loads.append(current_load)
                current_load = {
                    "capacity_feet": capacity_feet,
                    "total_feet": 0,
                    "lines": [],
                }
            loads.append(
                {
                    "capacity_feet": capacity_feet,
                    "total_feet": line_total,
                    "lines": [line],
                }
            )
            continue

        if (
            current_load["lines"]
            and current_load["total_feet"] + line_total > capacity_feet
        ):
            loads.append(current_load)
            current_load = {
                "capacity_feet": capacity_feet,
                "total_feet": 0,
                "lines": [],
            }

        current_load["lines"].append(line)
        current_load["total_feet"] += line_total

    if current_load["lines"]:
        loads.append(current_load)

    return loads
