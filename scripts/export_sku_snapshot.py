"""Export current SKU specifications to a CSV snapshot.

Usage:
    python scripts/export_sku_snapshot.py [--output PATH]
    python scripts/export_sku_snapshot.py --blob

Local mode (default):
    Writes to ``data/exports/sku_specifications_snapshot.csv``.

Blob mode (--blob):
    Uploads to Azure Blob Storage using Managed Identity.
    Requires ``SKU_EXPORT_STORAGE_ACCOUNT`` env var.
    Destination: reference/freight/cot_load_scoring/sku_specifications.csv
"""

import argparse
import csv
import io
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db

logger = logging.getLogger(__name__)

EXPORT_FIELDS = [
    "sku",
    "category",
    "description",
    "length_with_tongue_ft",
    "max_stack_step_deck",
    "max_stack_flat_bed",
]

DEFAULT_OUTPUT = ROOT / "data" / "exports" / "sku_specifications_snapshot.csv"

BLOB_CONTAINER = "reference"
BLOB_PATH = "freight/cot_load_scoring/sku_specifications.csv"


def _serialize_snapshot(specs):
    """Serialize SKU specs to CSV string with metadata header."""
    generated_at = datetime.now(timezone.utc).isoformat()
    row_count = len(specs)

    buf = io.StringIO()
    buf.write(f"# generated_at: {generated_at}\n")
    buf.write(f"# row_count: {row_count}\n")
    writer = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for spec in specs:
        row = {field: spec.get(field, "") for field in EXPORT_FIELDS}
        writer.writerow(row)

    return buf.getvalue()


def export_sku_snapshot(output_path=None):
    """Read SKU specs from DB and write to local CSV file."""
    output_path = Path(output_path or DEFAULT_OUTPUT)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    specs = db.list_sku_specs()
    if not specs:
        logger.warning("No SKU specifications found in database.")
        return None

    content = _serialize_snapshot(specs)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        f.write(content)

    logger.info("Exported %d SKU specs to %s", len(specs), output_path)
    return str(output_path)


def export_sku_snapshot_to_blob(storage_account=None):
    """Read SKU specs from DB and upload to Azure Blob Storage.

    Uses DefaultAzureCredential (Managed Identity in production,
    Azure CLI fallback for local dev).

    Returns the blob URL on success, or None on failure.
    """
    account = storage_account or os.environ.get("SKU_EXPORT_STORAGE_ACCOUNT", "")
    if not account:
        logger.error(
            "SKU_EXPORT_STORAGE_ACCOUNT is not set. Cannot upload to blob storage."
        )
        return None

    specs = db.list_sku_specs()
    if not specs:
        logger.warning("No SKU specifications found in database.")
        return None

    content = _serialize_snapshot(specs)

    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient

        account_url = f"https://{account}.blob.core.windows.net"
        credential = DefaultAzureCredential()
        blob = BlobClient(
            account_url=account_url,
            container_name=BLOB_CONTAINER,
            blob_name=BLOB_PATH,
            credential=credential,
        )
        blob.upload_blob(content.encode("utf-8"), overwrite=True)

        blob_url = f"{account_url}/{BLOB_CONTAINER}/{BLOB_PATH}"
        logger.info("Uploaded %d SKU specs to %s", len(specs), blob_url)
        return blob_url

    except Exception:
        logger.exception("Failed to upload SKU snapshot to blob storage.")
        return None


def main():
    parser = argparse.ArgumentParser(description="Export SKU specifications snapshot")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path (local mode)")
    parser.add_argument("--blob", action="store_true", help="Upload to Azure Blob Storage")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.blob:
        result = export_sku_snapshot_to_blob()
        if result:
            print(f"Snapshot uploaded to {result}")
        else:
            print("Blob export failed.", file=sys.stderr)
            sys.exit(1)
    else:
        result = export_sku_snapshot(args.output)
        if result:
            print(f"Snapshot written to {result}")
        else:
            print("No data to export.", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
