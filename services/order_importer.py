import math
import re
from collections import Counter

import pandas as pd

import db
from services import geo_utils, stack_calculator

REQUIRED_COLUMNS = [
    "shipvia",
    "plant",
    "item",
    "qty",
    "state",
    "zip",
    "bin",
]

OPTIONAL_COLUMNS = [
    "plant2",
    "sales",
    "sonum",
    "customer",
    "custname",
    "cname",
    "cpo",
    "salesman",
    "custnum",
    "load #",
    "address1",
    "address2",
    "city",
    "createdate",
    "shipdate",
    "duedate",
    "desc",
]

COLUMN_ALIASES = {
    "salesorder": "sonum",
    "itemnum": "item",
    "ordqty": "qty",
    "zip_code": "zip",
    "cust_name": "custname",
    "cname": "cname",
    "cnum3": "custnum",
    "saddr1": "address1",
    "saddr2": "address2",
    "createdate": "createdate",
    "shipdate": "shipdate",
    "ship via": "shipvia",
    "linenum": "linenum",
    "desc": "desc",
}


class OrderImporter:
    def __init__(self):
        self.sku_lookup = self._load_sku_lookup()
        self.sku_specs = self._load_sku_specs()

    def parse_csv(self, file_stream):
        df = pd.read_csv(file_stream, dtype=str, keep_default_na=False)
        column_map = self._normalize_columns(df.columns)

        available = set(column_map.values())
        missing = [col for col in REQUIRED_COLUMNS if col not in available]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        df = df.rename(columns=column_map)
        allowed_columns = set(REQUIRED_COLUMNS + OPTIONAL_COLUMNS)
        df = df[[col for col in df.columns if col in allowed_columns]]

        orders = []
        order_lines = []
        unmapped_items = []

        for row in df.to_dict(orient="records"):
            line, reason, context = self.parse_order_line(row, return_reason=True)
            if line:
                order_lines.append(line)
            else:
                unmapped_items.append(
                    {
                        "item": context.get("item", ""),
                        "desc": context.get("desc", ""),
                        "plant": context.get("plant", ""),
                        "bin": context.get("bin_code", ""),
                        "sku": context.get("sku") or "",
                        "reason": reason or "No matching SKU data found.",
                    }
                )

        orders = self.aggregate_orders(order_lines)

        total_rows = len(df)
        mapped_count = len(order_lines)
        mapping_rate = (mapped_count / total_rows * 100) if total_rows else 0

        return {
            "orders": orders,
            "order_lines": order_lines,
            "unmapped_items": unmapped_items,
            "total_rows": total_rows,
            "successfully_mapped": mapped_count,
            "mapping_rate": mapping_rate,
            "orders_by_plant": self._count_by_field(orders, "plant"),
        }

    def parse_order_line(self, row, return_reason=False):
        context = self._resolve_row_fields(row)
        context["desc"] = self._clean_value(row.get("desc"))
        plant = context["plant"]
        bin_code = context["bin_code"]
        item = context["item"]

        if not plant or not item:
            if return_reason:
                return None, "Missing plant or item value.", context
            return None

        sku = self.lookup_sku(
            item,
            plant=plant,
            bin_code=bin_code,
            bin_raw=context.get("bin_raw"),
        )
        context["sku"] = sku
        if not sku:
            if return_reason:
                return None, "No SKU lookup match for item.", context
            return None

        specs = self.sku_specs.get(sku)
        cargo_length = self._cargo_length_from_item(item, sku)
        if not specs and not cargo_length:
            if return_reason:
                return None, f"Missing SKU spec for {sku}.", context
            return None

        qty = self._to_int(row.get("qty"))
        unit_length = specs.get("length_with_tongue_ft") if specs else None
        max_stack = (specs.get("max_stack_flat_bed") or 1) if specs else 1
        if cargo_length:
            unit_length = cargo_length
            max_stack = 1

        effective_units = math.ceil(qty / max_stack) if max_stack else qty
        total_length = effective_units * unit_length
        utilization_pct = (total_length / 53.0) * 100 if unit_length else 0

        due_date = self._resolve_due_date(row)
        zip_code = geo_utils.normalize_zip(row.get("zip"))

        customer_label = (
            self._clean_value(row.get("cname"))
            or self._clean_value(row.get("custname"))
            or self._clean_value(row.get("customer"))
        )

        created_date = self._parse_date(row.get("createdate"))
        ship_date = self._parse_date(row.get("shipdate"))
        item_desc = self._clean_value(row.get("desc"))

        line = {
            "due_date": due_date,
            "plant": plant,
            "plant_full": context["plant_full"],
            "plant2": context["plant2"],
            "item": item,
            "item_desc": item_desc,
            "qty": qty,
            "sales": self._to_float(row.get("sales")),
            "so_num": self._clean_value(row.get("sonum")),
            "customer": customer_label,
            "cust_name": customer_label,
            "cpo": self._clean_value(row.get("cpo")),
            "salesman": self._clean_value(row.get("salesman")),
            "cust_num": self._clean_value(row.get("custnum")),
            "bin": bin_code,
            "load_num": self._clean_value(row.get("load #")),
            "address1": self._clean_value(row.get("address1")),
            "address2": self._clean_value(row.get("address2")),
            "city": self._clean_value(row.get("city")),
            "state": self._clean_value(row.get("state")),
            "zip": zip_code,
            "created_date": created_date,
            "ship_date": ship_date,
            "sku": sku,
            "unit_length_ft": unit_length,
            "total_length_ft": total_length,
            "max_stack_height": max_stack,
            "stack_position": 1,
            "utilization_pct": utilization_pct,
            "is_excluded": 0,
        }
        if return_reason:
            return line, "", context
        return line

    def aggregate_orders(self, order_lines):
        grouped = {}
        for line in order_lines:
            key = line.get("so_num") or "UNKNOWN"
            grouped.setdefault(key, []).append(line)

        orders = []
        for so_num, lines in grouped.items():
            total_qty = sum(line.get("qty") or 0 for line in lines)
            total_sales = sum(line.get("sales") or 0 for line in lines)
            config = stack_calculator.calculate_stack_configuration(lines)
            total_length = config.get("total_linear_feet") or 0
            utilization_pct = config.get("utilization_pct") or 0
            utilization_grade = config.get("utilization_grade") or "F"
            utilization_credit_ft = config.get("utilization_credit_ft") or 0
            exceeds_capacity = 1 if config.get("exceeds_capacity") else 0

            due_dates = [line.get("due_date") for line in lines if line.get("due_date")]
            due_date = min(due_dates) if due_dates else ""
            created_dates = [line.get("created_date") for line in lines if line.get("created_date")]
            created_date = min(created_dates) if created_dates else ""
            ship_dates = [line.get("ship_date") for line in lines if line.get("ship_date")]
            ship_date = min(ship_dates) if ship_dates else ""

            plant = self._most_common(lines, "plant")
            customer = self._most_common(lines, "customer")
            cust_name = self._most_common(lines, "cust_name")
            state = self._most_common(lines, "state")
            zip_code = self._most_common(lines, "zip")
            address1 = self._most_common(lines, "address1")
            address2 = self._most_common(lines, "address2")
            city = self._most_common(lines, "city")

            orders.append(
                {
                    "so_num": so_num,
                    "due_date": due_date,
                    "created_date": created_date,
                    "ship_date": ship_date,
                    "plant": plant,
                    "customer": customer,
                    "cust_name": cust_name,
                    "address1": address1,
                    "address2": address2,
                    "city": city,
                    "state": state,
                    "zip": zip_code,
                    "total_qty": total_qty,
                    "total_sales": total_sales,
                    "total_length_ft": total_length,
                    "utilization_pct": utilization_pct,
                    "utilization_grade": utilization_grade,
                    "utilization_credit_ft": utilization_credit_ft,
                    "exceeds_capacity": exceeds_capacity,
                    "line_count": len(lines),
                    "is_excluded": 0,
                }
            )

        return orders

    def lookup_sku(self, item, plant="", bin_code="", bin_raw=""):
        if not item:
            return None
        normalized = str(item).strip().upper()
        if normalized in self.sku_specs:
            return normalized

        # Backward-compatible fallback for older flat lookup payloads.
        legacy_exact = self.sku_lookup.get("exact", {})
        if legacy_exact and all(isinstance(key, str) for key in legacy_exact.keys()):
            if normalized in legacy_exact:
                return legacy_exact[normalized]
        legacy_patterns = self.sku_lookup.get("patterns", [])
        if isinstance(legacy_patterns, list):
            for prefix, sku in legacy_patterns:
                if normalized.startswith(prefix):
                    return sku

        exact_by_scope = self.sku_lookup.get("exact", {})
        patterns_by_scope = self.sku_lookup.get("patterns", {})
        for scope in self._lookup_scope_sequence(plant, bin_code, bin_raw):
            scoped_exact = exact_by_scope.get(scope, {})
            if normalized in scoped_exact:
                return scoped_exact[normalized]

        for scope in self._lookup_scope_sequence(plant, bin_code, bin_raw):
            for prefix, sku in patterns_by_scope.get(scope, []):
                if normalized.startswith(prefix):
                    return sku
        return None

    def _load_sku_lookup(self):
        entries = db.list_item_lookups()
        lookup = {"exact": {}, "patterns": {}}
        for entry in entries:
            pattern = (entry.get("item_pattern") or "").strip()
            sku = entry.get("sku")
            if not pattern or not sku:
                continue
            scope = (
                self._normalize_lookup_scope(entry.get("plant")),
                self._normalize_lookup_scope(entry.get("bin")),
            )
            normalized = pattern.upper()
            if normalized.endswith("%") or normalized.endswith("*"):
                prefix = normalized.rstrip("%*").strip()
                if not prefix:
                    continue
                lookup["patterns"].setdefault(scope, []).append((prefix, sku))
            else:
                scoped_exact = lookup["exact"].setdefault(scope, {})
                if normalized not in scoped_exact:
                    scoped_exact[normalized] = sku

        for scope in lookup["patterns"]:
            lookup["patterns"][scope].sort(key=lambda entry: (-len(entry[0]), entry[0]))
        return lookup

    def _lookup_scope_sequence(self, plant, bin_code, bin_raw):
        plant_value = self._normalize_lookup_scope(plant)
        bins = []
        for candidate in (bin_raw, bin_code):
            normalized = self._normalize_lookup_scope(candidate)
            if normalized != "*" and normalized not in bins:
                bins.append(normalized)
        bins.append("*")

        scopes = []
        if plant_value != "*":
            for bin_value in bins:
                scopes.append((plant_value, bin_value))
        for bin_value in bins:
            scopes.append(("*", bin_value))

        ordered = []
        seen = set()
        for scope in scopes:
            if scope in seen:
                continue
            seen.add(scope)
            ordered.append(scope)
        return ordered

    def _normalize_lookup_scope(self, value):
        normalized = self._clean_value(value).upper()
        if not normalized or normalized in {"*", "ANY", "ALL"}:
            return "*"
        return normalized

    def _load_sku_specs(self):
        specs = {}
        for spec in db.list_sku_specs():
            specs[spec["sku"]] = spec
        return specs

    def _normalize_columns(self, columns):
        mapping = {}
        for col in columns:
            normalized = col.strip().lower()
            normalized = COLUMN_ALIASES.get(normalized, normalized)
            mapping[col] = normalized
        return mapping

    def _resolve_row_fields(self, row):
        plant_full = self._clean_value(row.get("plant")).upper()
        plant2 = self._clean_value(row.get("plant2")).upper()
        plant = plant2 or self._extract_code(plant_full)

        bin_raw = self._clean_value(row.get("bin")).upper()
        bin_code = self._extract_code(bin_raw)

        item = self._clean_value(row.get("item")).upper()
        return {
            "plant_full": plant_full,
            "plant2": plant2,
            "plant": plant,
            "bin_raw": bin_raw,
            "bin_code": bin_code,
            "item": item,
        }

    def _cargo_length_from_item(self, item, sku=None):
        normalized = (item or "").upper()
        sku_normalized = (sku or "").upper()
        if "CARGO" not in normalized and "CARGO" not in sku_normalized:
            return None
        source = sku_normalized if "CARGO" in sku_normalized else normalized
        match = re.search(r"(\d+)\s*(?:FT|FEET)", source)
        if not match:
            return None
        try:
            feet = int(match.group(1))
        except (TypeError, ValueError):
            return None
        return feet + 4

    def _parse_date(self, value):
        if pd.isna(value) or value == "":
            return ""
        try:
            parsed = pd.to_datetime(value)
            return parsed.strftime("%Y-%m-%d")
        except Exception:
            return str(value)

    def _resolve_due_date(self, row):
        ship_candidate = self._parse_date(row.get("shipvia"))
        if ship_candidate and not ship_candidate.startswith("1960-01-01"):
            return ship_candidate
        return ""

    def _clean_value(self, value):
        if value is None:
            return ""
        if isinstance(value, float) and math.isnan(value):
            return ""
        return str(value).strip()

    def _to_int(self, value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    def _to_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _count_by_field(self, orders, field):
        counts = Counter(order.get(field, "") for order in orders)
        return {key: count for key, count in counts.items() if key}

    def _most_common(self, lines, field):
        values = [line.get(field) for line in lines if line.get(field)]
        if not values:
            return ""
        return Counter(values).most_common(1)[0][0]

    def _extract_code(self, value):
        if not value:
            return ""
        if "-" in value:
            return value.split("-")[0].strip()
        return value
