# SKU Blob Export on Change

**Date:** 2026-04-12
**Status:** Approved

## Problem

The `cot_utilization` scorer package needs current SKU specifications to score historical loads. The app owns SKU data and must publish a snapshot when SKUs change. The export script (`scripts/export_sku_snapshot.py`) exists but writes to local disk and must be run manually.

## Goal

Automatically export SKU specifications to Azure Blob Storage whenever a SKU is created, updated, or deleted in the web app. The export must not block planner workflows.

## Design

### Trigger

After each SKU mutation route returns successfully, a background thread exports the current SKU specs to blob storage. The HTTP response returns immediately.

SKU mutation routes:
- `POST /skus/save`
- `POST /skus/source-led/save`
- `POST /skus/add`
- `POST /skus/delete/<id>`
- `POST /api/skus/bulk-add`

### Service Module: `services/sku_export.py`

Single file. Responsibilities:
- Read all SKU specs from DB via `db.list_sku_specs()`
- Serialize to CSV in memory (no temp file)
- Upload to Azure Blob Storage
- Auth via `DefaultAzureCredential` (Managed Identity in production, Azure CLI fallback for local dev)
- Log success/failure, never raise into the caller

Public function:

```python
def export_sku_snapshot_to_blob():
    """Background-safe: catches all exceptions, logs, never raises."""
```

### Blob Destination

- **Storage account:** configured via `SKU_EXPORT_STORAGE_ACCOUNT` env var
- **Container:** `reference`
- **Blob path:** `freight/cot_load_scoring/sku_specifications.csv`

If `SKU_EXPORT_STORAGE_ACCOUNT` is not set, the export is a no-op.

### Authentication

`DefaultAzureCredential` from `azure-identity`. No connection strings or keys.

- **Production (Azure):** uses the app's Managed Identity. Requires Storage Blob Data Contributor role on the storage account.
- **Local dev:** falls back to Azure CLI credentials (`az login`).

### CSV Format

Same schema as `scripts/export_sku_snapshot.py`:

```
# generated_at: 2026-04-12T15:30:00+00:00
# row_count: 261
sku,category,description,length_with_tongue_ft,max_stack_step_deck,max_stack_flat_bed
5X8GW,USA,,12.0,5,4
...
```

### Route Integration

A helper in `blueprints/cot/routes.py`:

```python
def _trigger_sku_export():
    threading.Thread(target=sku_export.export_sku_snapshot_to_blob, daemon=True).start()
```

Called at the end of each SKU mutation route, after the DB write succeeds.

### Failure Behavior

- The entire export runs in a try/except that logs the error and returns silently.
- A failed export never blocks the HTTP response or the planner workflow.
- The last successful blob remains available to downstream consumers.
- If the storage account is not configured, the function returns immediately with a debug log.

### Dependencies

Add to `requirements.txt`:
- `azure-identity`
- `azure-storage-blob`

### Configuration

Single env var:

| Var | Required | Description |
|---|---|---|
| `SKU_EXPORT_STORAGE_ACCOUNT` | No | Azure storage account name. If unset, export is disabled. |

## Testing Strategy

- Unit test for `services/sku_export.py`: mock `db.list_sku_specs()` and `BlobClient.upload_blob()`, verify CSV content and blob path.
- Unit test for failure isolation: verify exceptions in export don't propagate.
- Unit test for no-op when storage account is unset.
- Integration: verify `_trigger_sku_export()` is called from each mutation route (inspect the route or mock the function).

## File Change Summary

| File | Change |
|---|---|
| `services/sku_export.py` | New — blob export logic |
| `blueprints/cot/routes.py` | Modified — add `_trigger_sku_export()` calls to 5 SKU routes |
| `requirements.txt` | Modified — add `azure-identity`, `azure-storage-blob` |
| `scripts/export_sku_snapshot.py` | Modified — update to also support blob destination (optional, for manual runs) |
