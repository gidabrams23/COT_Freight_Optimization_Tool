import json

import db
from services import stack_calculator
from services.optimizer import Optimizer


class OptimizerEngine:
    def __init__(self):
        self.optimizer = Optimizer()

    def run_optimization(
        self,
        plant_code,
        flexibility_days=7,
        proximity_miles=200,
        capacity_feet=53.0,
        trailer_type="STEP_DECK",
    ):
        plant_code = (plant_code or "").strip().upper()
        if not plant_code:
            return {"error": "Plant code is required"}

        params = {
            "origin_plant": plant_code,
            "capacity_feet": float(capacity_feet),
            "trailer_type": stack_calculator.normalize_trailer_type(
                trailer_type,
                default="STEP_DECK",
            ),
            "max_detour_pct": 15.0,
            "time_window_days": int(flexibility_days),
            "geo_radius": float(proximity_miles),
        }

        optimized_loads = self.optimizer.build_optimized_loads(params)
        baseline_loads = self.optimizer.build_baseline_loads(params)

        if not optimized_loads:
            return {"error": f"No eligible orders found for plant {plant_code}"}

        optimized_summary = self._summarize_loads(optimized_loads)
        baseline_summary = self._summarize_loads(baseline_loads)

        run_id = self._save_optimization_results(
            plant_code,
            params,
            optimized_loads,
            baseline_summary,
            optimized_summary,
        )

        return {
            "run_id": run_id,
            "loads": self.format_loads_for_ui(optimized_loads),
            "summary": {
                "num_loads": optimized_summary["num_loads"],
                "num_orders": optimized_summary["num_orders"],
                "total_cost": optimized_summary["total_cost"],
                "avg_utilization": optimized_summary["avg_utilization"],
                "before": baseline_summary,
                "after": optimized_summary,
            },
        }

    def format_loads_for_ui(self, loads):
        ui_loads = []
        for idx, load in enumerate(loads):
            order_numbers = sorted(
                {
                    line.get("so_num")
                    for line in load.get("lines", [])
                    if line.get("so_num")
                }
            )
            ui_loads.append(
                {
                    "load_number": f"OPT-{idx + 1:03d}",
                    "total_util": load.get("utilization_pct", 0.0),
                    "total_miles": load.get("estimated_miles", 0.0),
                    "total_cost": load.get("estimated_cost", 0.0),
                    "num_orders": len(order_numbers),
                    "status": load.get("status", "PROPOSED"),
                    "order_numbers": order_numbers,
                }
            )
        return ui_loads

    def _summarize_loads(self, loads):
        num_loads = len(loads)
        total_cost = sum(load.get("estimated_cost") or 0 for load in loads)
        total_miles = sum(load.get("estimated_miles") or 0 for load in loads)
        total_util_pct = sum(load.get("utilization_pct") or 0 for load in loads)
        avg_util_pct = total_util_pct / num_loads if num_loads else 0.0

        order_numbers = set()
        for load in loads:
            for line in load.get("lines", []):
                if line.get("so_num"):
                    order_numbers.add(line["so_num"])

        return {
            "num_loads": num_loads,
            "num_orders": len(order_numbers),
            "total_cost": total_cost,
            "total_miles": total_miles,
            "avg_utilization": avg_util_pct / 100.0,
        }

    def _save_optimization_results(
        self,
        plant_code,
        params,
        optimized_loads,
        baseline_summary,
        optimized_summary,
    ):
        with db.get_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO optimization_runs (
                    plant_code,
                    flexibility_days,
                    num_orders_input,
                    num_loads_before,
                    num_loads_after,
                    cost_before,
                    cost_after,
                    avg_util_before,
                    avg_util_after,
                    config_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plant_code,
                    params.get("time_window_days"),
                    optimized_summary["num_orders"],
                    baseline_summary["num_loads"],
                    optimized_summary["num_loads"],
                    baseline_summary["total_cost"],
                    optimized_summary["total_cost"],
                    baseline_summary["avg_utilization"],
                    optimized_summary["avg_utilization"],
                    json.dumps(params),
                ),
            )
            run_id = cursor.lastrowid

            for idx, load in enumerate(optimized_loads):
                order_numbers = sorted(
                    {
                        line.get("so_num")
                        for line in load.get("lines", [])
                        if line.get("so_num")
                    }
                )
                cursor.execute(
                    """
                    INSERT INTO optimized_loads (
                        run_id,
                        load_number,
                        plant_code,
                        total_util,
                        total_miles,
                        total_cost,
                        num_orders,
                        route_json,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        idx + 1,
                        plant_code,
                        (load.get("utilization_pct") or 0) / 100.0,
                        load.get("estimated_miles", 0.0),
                        load.get("estimated_cost", 0.0),
                        len(order_numbers),
                        json.dumps(load.get("route") or []),
                        load.get("status", "PROPOSED"),
                    ),
                )
                load_id = cursor.lastrowid

                for seq, so_num in enumerate(order_numbers):
                    cursor.execute(
                        """
                        INSERT INTO load_order_assignments (
                            load_id, order_so_num, sequence
                        )
                        VALUES (?, ?, ?)
                        """,
                        (load_id, so_num, seq),
                    )

            connection.commit()
            return run_id
