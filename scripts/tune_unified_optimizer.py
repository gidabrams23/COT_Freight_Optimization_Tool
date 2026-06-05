import argparse
import json
import sys
from pathlib import Path
from statistics import mean

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import db
from services import load_builder
from services.optimizer import Optimizer


def _coerce_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_session_params(session_row):
    config = {}
    if session_row and session_row.get("config_json"):
        try:
            config = json.loads(session_row.get("config_json") or "{}")
        except json.JSONDecodeError:
            config = {}
    params = dict(load_builder.DEFAULT_BUILD_PARAMS)
    params.update(config)
    params["origin_plant"] = (
        params.get("origin_plant")
        or session_row.get("plant_code")
        or ""
    )
    params["algorithm_version"] = "v2"
    params["compare_algorithms"] = False
    params["optimize_focus"] = load_builder.normalize_optimize_focus(
        params.get("optimize_focus"),
        default=load_builder.UNIFIED_OPTIMIZER_PROFILE,
    )
    return params


def _load_metrics(loads):
    load_list = list(loads or [])
    if not load_list:
        return {
            "loads": 0,
            "ge_90": 0,
            "ge_85": 0,
            "lt_85": 0,
            "lt_70": 0,
            "avg_fill": 0.0,
            "avg_util": 0.0,
            "avg_stops": 0.0,
            "spend": 0.0,
            "avg_detour_pct": 0.0,
            "mixed_state_loads": 0,
            "two_across_loads": 0,
        }
    utilizations = [_coerce_float(load.get("utilization_pct")) for load in load_list]
    fills = [
        max(
            _coerce_float(load.get("effective_fill_pct")),
            _coerce_float(load.get("practical_fill_pct")),
            _coerce_float(load.get("utilization_pct")),
        )
        for load in load_list
    ]
    detours = []
    for load in load_list:
        estimated = _coerce_float(load.get("estimated_miles"))
        detour = _coerce_float(load.get("detour_miles"))
        direct = max(estimated - detour, 0.0)
        detours.append((detour / direct) * 100.0 if direct > 0 else 0.0)
    return {
        "loads": len(load_list),
        "ge_90": sum(1 for value in fills if value >= 90.0),
        "ge_85": sum(1 for value in fills if value >= 85.0),
        "lt_85": sum(1 for value in fills if value < 85.0),
        "lt_70": sum(1 for value in fills if value < 70.0),
        "avg_fill": round(mean(fills), 1),
        "avg_util": round(mean(utilizations), 1),
        "avg_stops": round(mean(int(load.get("stop_count") or 0) for load in load_list), 2),
        "spend": round(sum(_coerce_float(load.get("estimated_cost")) for load in load_list), 2),
        "avg_detour_pct": round(mean(detours), 1) if detours else 0.0,
        "mixed_state_loads": sum(
            1
            for load in load_list
            if int(load.get("unique_states_count") or (1 if load.get("destination_state") else 0)) > 1
        ),
        "two_across_loads": sum(
            1 for load in load_list if int(load.get("upper_two_across_applied_count") or 0) > 0
        ),
    }


def _format_metrics(metrics):
    return (
        f"loads={metrics['loads']} | >=90={metrics['ge_90']} | >=85={metrics['ge_85']} | "
        f"<85={metrics['lt_85']} | <70={metrics['lt_70']} | avg_fill={metrics['avg_fill']}% | avg_util={metrics['avg_util']}% | "
        f"avg_stops={metrics['avg_stops']} | spend=${metrics['spend']:.2f} | "
        f"avg_detour={metrics['avg_detour_pct']}% | mixed_state={metrics['mixed_state_loads']} | "
        f"two_across={metrics['two_across_loads']}"
    )


def _select_sessions(session_ids, plant_codes, limit):
    if session_ids:
        selected = []
        for session_id in session_ids:
            row = db.get_planning_session(session_id)
            if row:
                selected.append(row)
        return selected

    filters = {"limit": max(limit, 1)} if limit else {}
    sessions = db.list_planning_sessions(filters)
    if plant_codes:
        allowed = {str(code or "").strip().upper() for code in plant_codes if str(code or "").strip()}
        sessions = [
            session for session in sessions
            if str(session.get("plant_code") or "").strip().upper() in allowed
        ]
    return sessions[:limit] if limit else sessions


def main():
    parser = argparse.ArgumentParser(
        description="Replay recent planning-session configs through the unified optimizer and compare metrics."
    )
    parser.add_argument("--session-id", action="append", type=int, default=[], help="Specific planning session id to replay")
    parser.add_argument("--plant", action="append", default=[], help="Limit to plant code(s)")
    parser.add_argument("--limit", type=int, default=8, help="Max sessions to inspect when session ids are not supplied")
    parser.add_argument("--skip-rebuild", action="store_true", help="Only print historical session metrics")
    args = parser.parse_args()

    sessions = _select_sessions(args.session_id, args.plant, args.limit)
    if not sessions:
        print("No planning sessions matched the requested scope.")
        return

    for session_row in sessions:
        session_id = int(session_row.get("id"))
        session_code = session_row.get("session_code") or f"session_{session_id}"
        plant_code = session_row.get("plant_code") or ""
        status = session_row.get("status") or ""
        historical_loads = db.list_loads(session_id=session_id)
        print(f"\nSession {session_id} | {session_code} | plant={plant_code} | status={status}")
        print(f"Historical: {_format_metrics(_load_metrics(historical_loads))}")

        if args.skip_rebuild:
            continue

        params = _normalize_session_params(session_row)
        optimizer = Optimizer()
        rebuilt_loads = optimizer.build_optimized_loads_v2(params)
        print(f"Unified replay on current open orders: {_format_metrics(_load_metrics(rebuilt_loads))}")


if __name__ == "__main__":
    main()
