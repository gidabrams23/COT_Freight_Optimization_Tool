import argparse
import csv
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db


def _clean_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return " ".join(text.split())


def _normalized_compare(value):
    return _clean_text(value).upper()


def _fetch_sku_description_signals():
    with db.get_connection() as connection:
        rows = connection.execute(
            """
            WITH normalized AS (
                SELECT
                    UPPER(TRIM(ol.sku)) AS sku,
                    TRIM(ol.item_desc) AS item_desc
                FROM order_lines ol
                WHERE TRIM(COALESCE(ol.sku, '')) <> ''
                  AND TRIM(COALESCE(ol.item_desc, '')) <> ''
            ),
            description_counts AS (
                SELECT
                    sku,
                    item_desc,
                    COUNT(*) AS item_desc_count
                FROM normalized
                GROUP BY sku, item_desc
            ),
            sku_totals AS (
                SELECT
                    sku,
                    SUM(item_desc_count) AS total_samples,
                    COUNT(*) AS distinct_descriptions
                FROM description_counts
                GROUP BY sku
            ),
            ranked AS (
                SELECT
                    dc.sku,
                    dc.item_desc,
                    dc.item_desc_count,
                    st.total_samples,
                    st.distinct_descriptions,
                    ROW_NUMBER() OVER (
                        PARTITION BY dc.sku
                        ORDER BY dc.item_desc_count DESC, dc.item_desc ASC
                    ) AS rank_in_sku
                FROM description_counts dc
                JOIN sku_totals st
                  ON st.sku = dc.sku
            )
            SELECT
                r.sku,
                r.item_desc AS suggested_description,
                r.item_desc_count AS suggested_count,
                r.total_samples,
                r.distinct_descriptions,
                COALESCE(TRIM(ss.description), '') AS current_description,
                COALESCE(ss.category, '') AS category
            FROM ranked r
            LEFT JOIN sku_specifications ss
              ON UPPER(TRIM(ss.sku)) = r.sku
            WHERE r.rank_in_sku = 1
            ORDER BY r.item_desc_count DESC, r.sku ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _classify(rows, min_count, min_share):
    classified = []
    for row in rows:
        suggested_count = int(row.get("suggested_count") or 0)
        total_samples = int(row.get("total_samples") or 0)
        share = (suggested_count / total_samples) if total_samples else 0.0

        current_description = _clean_text(row.get("current_description"))
        suggested_description = _clean_text(row.get("suggested_description"))

        confident = suggested_count >= min_count and share >= min_share
        same_as_current = (
            bool(current_description)
            and _normalized_compare(current_description) == _normalized_compare(suggested_description)
        )

        if current_description:
            if same_as_current:
                status = "already_matches"
            elif confident:
                status = "review_populated_conflict"
            else:
                status = "keep_existing"
        else:
            if confident:
                status = "candidate_fill"
            else:
                status = "review_low_confidence"

        classified.append(
            {
                "sku": row.get("sku") or "",
                "category": row.get("category") or "",
                "current_description": current_description,
                "suggested_description": suggested_description,
                "suggested_count": suggested_count,
                "total_samples": total_samples,
                "suggested_share": round(share, 4),
                "distinct_descriptions": int(row.get("distinct_descriptions") or 0),
                "status": status,
            }
        )
    return classified


def _print_summary(rows, min_count, min_share):
    total = len(rows)
    missing = sum(1 for row in rows if not row.get("current_description"))
    candidates = sum(1 for row in rows if row.get("status") == "candidate_fill")
    conflicts = sum(1 for row in rows if row.get("status") == "review_populated_conflict")
    low_conf = sum(1 for row in rows if row.get("status") == "review_low_confidence")

    print(f"Analyzed SKUs with order-line descriptions: {total}")
    print(f"SKUs with blank description today: {missing}")
    print(
        "High-confidence fill candidates: "
        f"{candidates} (thresholds: count>={min_count}, share>={min_share:.2f})"
    )
    print(f"Low-confidence blank-description rows: {low_conf}")
    print(f"Populated-description conflict rows: {conflicts}")


def _print_preview(rows, max_rows):
    if not rows:
        print("\nNo rows matched the requested filters.")
        return

    print("\nPreview:")
    header = (
        f"{'SKU':<24} {'status':<28} {'count':>7} {'share':>7} "
        f"{'current_description':<36} suggested_description"
    )
    print(header)
    print("-" * len(header))
    for row in rows[:max_rows]:
        current = row.get("current_description") or "-"
        suggested = row.get("suggested_description") or "-"
        print(
            f"{(row.get('sku') or '')[:24]:<24} "
            f"{(row.get('status') or '')[:28]:<28} "
            f"{int(row.get('suggested_count') or 0):>7} "
            f"{float(row.get('suggested_share') or 0.0):>7.2f} "
            f"{current[:36]:<36} {suggested}"
        )


def _write_csv(path, rows):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "sku",
        "category",
        "current_description",
        "suggested_description",
        "suggested_count",
        "total_samples",
        "suggested_share",
        "distinct_descriptions",
        "status",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run report for backfilling sku_specifications.description from "
            "order_lines.item_desc signals."
        )
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=5,
        help="Minimum top-description sample count to consider high confidence (default: 5).",
    )
    parser.add_argument(
        "--min-share",
        type=float,
        default=0.60,
        help="Minimum top-description share to consider high confidence (default: 0.60).",
    )
    parser.add_argument(
        "--include-populated",
        action="store_true",
        help="Include SKUs that already have descriptions in preview/CSV output.",
    )
    parser.add_argument(
        "--max-preview",
        type=int,
        default=30,
        help="Maximum rows to print in terminal preview (default: 30).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Optional CSV output path for the filtered dry-run result set.",
    )
    args = parser.parse_args()

    base_rows = _fetch_sku_description_signals()
    rows = _classify(base_rows, min_count=max(int(args.min_count or 0), 1), min_share=max(float(args.min_share or 0.0), 0.0))
    _print_summary(rows, min_count=max(int(args.min_count or 0), 1), min_share=max(float(args.min_share or 0.0), 0.0))

    filtered = rows if args.include_populated else [row for row in rows if not row.get("current_description")]
    filtered.sort(
        key=lambda row: (
            0 if row.get("status") == "candidate_fill" else 1,
            -int(row.get("suggested_count") or 0),
            row.get("sku") or "",
        )
    )
    _print_preview(filtered, max_rows=max(int(args.max_preview or 0), 1))

    if args.output:
        written_path = _write_csv(args.output, filtered)
        print(f"\nWrote dry-run report CSV: {written_path}")


if __name__ == "__main__":
    main()
