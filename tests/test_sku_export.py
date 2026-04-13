import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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

from scripts.export_sku_snapshot import (
    BLOB_CONTAINER,
    BLOB_PATH,
    EXPORT_FIELDS,
    _serialize_snapshot,
    export_sku_snapshot,
    export_sku_snapshot_to_blob,
)


class TestSerializeSnapshot(unittest.TestCase):
    def test_contains_metadata_header(self):
        content = _serialize_snapshot(SAMPLE_SPECS)
        lines = content.split("\n")
        self.assertTrue(lines[0].startswith("# generated_at:"))
        self.assertEqual(lines[1], "# row_count: 2")

    def test_contains_csv_header(self):
        content = _serialize_snapshot(SAMPLE_SPECS)
        lines = content.split("\n")
        self.assertEqual(lines[2].rstrip("\r"), ",".join(EXPORT_FIELDS))

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
    def test_writes_file(self, _mock_db):
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
        result = export_sku_snapshot(output_path="/tmp/test_empty.csv")
        self.assertIsNone(result)


class TestExportToBlob(unittest.TestCase):
    def test_returns_none_when_no_storage_account(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SKU_EXPORT_STORAGE_ACCOUNT", None)
            result = export_sku_snapshot_to_blob(storage_account="")
            self.assertIsNone(result)

    @patch("scripts.export_sku_snapshot.db.list_sku_specs", return_value=SAMPLE_SPECS)
    def test_uploads_to_correct_blob_path(self, _mock_db):
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


class TestManualPathSmokeTest(unittest.TestCase):
    """Verify the script entrypoint works for both modes."""

    @patch("scripts.export_sku_snapshot.db.list_sku_specs", return_value=SAMPLE_SPECS)
    def test_main_local_mode(self, _mock_db):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            tmp = f.name
        try:
            with patch("sys.argv", ["export_sku_snapshot.py", "--output", tmp]):
                from scripts.export_sku_snapshot import main
                main()
            with open(tmp, "r") as f:
                content = f.read()
            self.assertIn("5X8GW", content)
        finally:
            os.unlink(tmp)

    @patch("scripts.export_sku_snapshot.db.list_sku_specs", return_value=SAMPLE_SPECS)
    def test_main_blob_mode_success(self, _mock_db):
        mock_blob_client = MagicMock()
        with patch.dict("sys.modules", {
            "azure": MagicMock(),
            "azure.identity": MagicMock(DefaultAzureCredential=lambda: MagicMock()),
            "azure.storage": MagicMock(),
            "azure.storage.blob": MagicMock(
                BlobClient=MagicMock(return_value=mock_blob_client)
            ),
        }), patch.dict(os.environ, {"SKU_EXPORT_STORAGE_ACCOUNT": "testaccount"}):
            with patch("sys.argv", ["export_sku_snapshot.py", "--blob"]):
                from scripts.export_sku_snapshot import main
                main()
        mock_blob_client.upload_blob.assert_called_once()


if __name__ == "__main__":
    unittest.main()
