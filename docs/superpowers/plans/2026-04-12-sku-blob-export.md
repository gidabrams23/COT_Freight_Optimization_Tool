# Daily SKU Blob Snapshot Export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `scripts/export_sku_snapshot.py` to upload the SKU snapshot to Azure Blob Storage using Managed Identity, add Azure SDK dependencies, and add tests.

**Architecture:** The existing script gains a `--blob` flag and a `export_sku_snapshot_to_blob()` function. Blob auth uses `DefaultAzureCredential`. Container (`reference`) and blob path (`freight/cot_load_scoring/sku_specifications.csv`) are constants. No changes to Flask routes or services.

**Tech Stack:** `azure-identity`, `azure-storage-blob`, existing `csv`/`io` stdlib

**Spec:** `docs/superpowers/specs/2026-04-12-sku-blob-export-design.md`

---

## File Map

| File | Role |
|---|---|
| `scripts/export_sku_snapshot.py` | Modified — add blob upload function and `--blob` CLI flag |
| `requirements.txt` | Modified — add `azure-identity`, `azure-storage-blob` |
| `tests/test_sku_export.py` | New — unit tests for serialization, blob upload, failure isolation |

---

### Task 1: Add Azure Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add azure packages to requirements.txt**

Add these two lines at the end of `/home/atw/COT_Freight_Optimization_Tool/requirements.txt`:

```
azure-identity>=1.15.0
azure-storage-blob>=12.19.0
```

- [ ] **Step 2: Commit**

```bash
git add requirements.txt
git commit -m "chore: add azure-identity and azure-storage-blob to requirements"
```

---

### Task 2: Add Blob Upload to Export Script

**Files:**
- Modify: `scripts/export_sku_snapshot.py`

- [ ] **Step 1: Rewrite the export script**

Replace the entire contents of `scripts/export_sku_snapshot.py` with:

```python
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
```

- [ ] **Step 2: Verify syntax**

```bash
python3 -c "import ast; ast.parse(open('scripts/export_sku_snapshot.py').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit**

```bash
git add scripts/export_sku_snapshot.py
git commit -m "feat(scripts): add Azure Blob upload to SKU snapshot export"
```

---

### Task 3: Write Tests

**Files:**
- Create: `tests/test_sku_export.py`

- [ ] **Step 1: Write the test file**

```python
import io
import os
import unittest
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_sku_snapshot import (
    BLOB_CONTAINER,
    BLOB_PATH,
    EXPORT_FIELDS,
    _serialize_snapshot,
    export_sku_snapshot,
    export_sku_snapshot_to_blob,
)


SAMPLE_SPECS = [
    {
        "sku": "5X8GW",
        "category": "USA",
        "description": "",
        "length_with_tongue_ft": 12.0,
        "max_stack_step_deck": 5,
        "max_stack_flat_bed": 4,
    },
    {
        "sku": "7X16TA",
        "category": "CARGO",
        "description": "Tandem axle",
        "length_with_tongue_ft": 22.0,
        "max_stack_step_deck": 2,
        "max_stack_flat_bed": 2,
    },
]


class TestSerializeSnapshot(unittest.TestCase):
    def test_contains_metadata_header(self):
        content = _serialize_snapshot(SAMPLE_SPECS)
        lines = content.split("\n")
        self.assertTrue(lines[0].startswith("# generated_at:"))
        self.assertEqual(lines[1], "# row_count: 2")

    def test_contains_csv_header(self):
        content = _serialize_snapshot(SAMPLE_SPECS)
        lines = content.split("\n")
        self.assertEqual(lines[2], ",".join(EXPORT_FIELDS))

    def test_contains_data_rows(self):
        content = _serialize_snapshot(SAMPLE_SPECS)
        lines = [l for l in content.split("\n") if l and not l.startswith("#")]
        # header + 2 data rows
        self.assertEqual(len(lines), 3)

    def test_empty_specs_still_has_metadata(self):
        content = _serialize_snapshot([])
        self.assertIn("# row_count: 0", content)
        self.assertIn(",".join(EXPORT_FIELDS), content)


class TestExportLocal(unittest.TestCase):
    @patch("scripts.export_sku_snapshot.db.list_sku_specs", return_value=SAMPLE_SPECS)
    def test_writes_file(self, _mock_db, tmp_path=None):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            tmp = f.name

        try:
            result = export_sku_snapshot(output_path=tmp)
            self.assertEqual(result, tmp)
            with open(tmp, "r") as f:
                content = f.read()
            self.assertIn("5X8GW", content)
            self.assertIn("# generated_at:", content)
        finally:
            os.unlink(tmp)

    @patch("scripts.export_sku_snapshot.db.list_sku_specs", return_value=[])
    def test_returns_none_when_no_specs(self, _mock_db):
        result = export_sku_snapshot()
        self.assertIsNone(result)


class TestExportToBlob(unittest.TestCase):
    def test_returns_none_when_no_storage_account(self):
        with patch.dict(os.environ, {}, clear=True):
            result = export_sku_snapshot_to_blob(storage_account="")
            self.assertIsNone(result)

    @patch("scripts.export_sku_snapshot.db.list_sku_specs", return_value=SAMPLE_SPECS)
    def test_uploads_to_correct_blob_path(self, _mock_db):
        mock_blob = MagicMock()

        with patch(
            "scripts.export_sku_snapshot.BlobClient", return_value=mock_blob
        ) as mock_cls, patch(
            "scripts.export_sku_snapshot.DefaultAzureCredential"
        ) as mock_cred:
            # The imports inside the function need to be patchable at module level
            # since they're imported inside the function body. Patch at the point of use.
            pass

        # Since azure imports are inside the function, we need to patch them differently
        mock_blob_client = MagicMock()
        mock_credential = MagicMock()

        with patch.dict("sys.modules", {
            "azure": MagicMock(),
            "azure.identity": MagicMock(DefaultAzureCredential=lambda: mock_credential),
            "azure.storage": MagicMock(),
            "azure.storage.blob": MagicMock(
                BlobClient=MagicMock(return_value=mock_blob_client)
            ),
        }):
            result = export_sku_snapshot_to_blob(storage_account="teststorage")

        self.assertIsNotNone(result)
        self.assertIn("teststorage", result)
        mock_blob_client.upload_blob.assert_called_once()
        uploaded_content = mock_blob_client.upload_blob.call_args[0][0]
        self.assertIn(b"5X8GW", uploaded_content)

    @patch("scripts.export_sku_snapshot.db.list_sku_specs", return_value=SAMPLE_SPECS)
    def test_returns_none_on_upload_failure(self, _mock_db):
        with patch.dict("sys.modules", {
            "azure": MagicMock(),
            "azure.identity": MagicMock(
                DefaultAzureCredential=MagicMock(side_effect=Exception("auth failed"))
            ),
            "azure.storage": MagicMock(),
            "azure.storage.blob": MagicMock(),
        }):
            result = export_sku_snapshot_to_blob(storage_account="teststorage")

        self.assertIsNone(result)

    @patch("scripts.export_sku_snapshot.db.list_sku_specs", return_value=[])
    def test_returns_none_when_no_specs(self, _mock_db):
        result = export_sku_snapshot_to_blob(storage_account="teststorage")
        self.assertIsNone(result)


class TestFromCsvCompatibility(unittest.TestCase):
    """Verify the exported CSV is compatible with UtilizationScorer.from_csv()."""

    def test_snapshot_loadable_by_scorer(self):
        content = _serialize_snapshot(SAMPLE_SPECS)

        from cot_utilization.scorer import UtilizationScorer

        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = f.name

        try:
            scorer = UtilizationScorer.from_csv(tmp)
            self.assertIn("5X8GW", scorer._sku_lookup)
            self.assertIn("7X16TA", scorer._sku_lookup)
            self.assertEqual(scorer._sku_lookup["5X8GW"]["length_with_tongue_ft"], 12.0)
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
PYTHONPATH=/home/atw/COT_Freight_Optimization_Tool uvx --with pandas pytest tests/test_sku_export.py -v
```

Expected: all tests pass. The blob tests mock the Azure SDK so no real credentials are needed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_sku_export.py
git commit -m "test(scripts): add tests for SKU snapshot export and blob upload"
```

---

### Task 4: Final Verification

- [ ] **Step 1: Run all tests**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
PYTHONPATH=/home/atw/COT_Freight_Optimization_Tool uvx --with pandas pytest tests/test_cot_utilization_core.py tests/test_cot_utilization_scorer.py tests/test_stack_calculator_assumptions.py tests/test_sku_export.py -v
```

Expected: all tests pass, no regressions.

- [ ] **Step 2: Verify script CLI works for local mode**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 scripts/export_sku_snapshot.py --help
```

Expected: shows `--output` and `--blob` flags.

- [ ] **Step 3: Verify blob mode fails cleanly without credentials**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
SKU_EXPORT_STORAGE_ACCOUNT="" python3 scripts/export_sku_snapshot.py --blob 2>&1
```

Expected: exits with error message about missing storage account.
