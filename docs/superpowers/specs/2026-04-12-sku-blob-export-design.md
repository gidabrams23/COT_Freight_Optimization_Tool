# Daily SKU Blob Snapshot Export

**Date:** 2026-04-12
**Status:** Revised Draft

## Problem

The `cot_utilization` scorer package needs current SKU specifications to score historical loads. The app owns canonical SKU data and must publish a snapshot that downstream analytics can consume.

Today `scripts/export_sku_snapshot.py` writes the snapshot to local disk and must be run manually. The prior design proposed exporting on every SKU mutation via background threads from request handlers, but that approach adds operational complexity and does not provide durable delivery guarantees.

The downstream need is batch-oriented and does not require immediate export after every edit. A periodic authoritative snapshot is sufficient.

## Goal

Publish a private SKU snapshot to Azure Blob Storage on a fixed daily schedule, with a manual rerun path for recovery. The export must not block planner workflows or depend on web-request lifecycle behavior.

## Non-Goals

- Exporting after every SKU mutation.
- Adding background-thread blob uploads to request handlers.
- Adding a public or unauthenticated API for SKU access.
- Guaranteeing real-time freshness for downstream analytics.

## Design

### Export Model

Use a scheduled batch export, not request-triggered export.

The application publishes one authoritative SKU snapshot per day to Azure Blob Storage. Downstream analytics reads the latest available snapshot.

This spec intentionally changes the contract from:

- "publish immediately whenever SKUs change"

to:

- "publish a daily authoritative snapshot for downstream batch consumers"

### Export Implementation

Continue to use `scripts/export_sku_snapshot.py` as the primary export path, extended to support blob upload.

Responsibilities:

- read current SKU data from `db.list_sku_specs()`
- serialize snapshot content in a stable CSV format
- upload the snapshot to Azure Blob Storage
- log success and failure details
- exit non-zero on export failure when run as a script

The export should remain runnable manually from the command line for recovery and backfill.

### Blob Destination

- **Storage account:** configured via `SKU_EXPORT_STORAGE_ACCOUNT`
- **Container:** `reference`
- **Blob path:** `freight/cot_load_scoring/sku_specifications.csv`

If `SKU_EXPORT_STORAGE_ACCOUNT` is unset, blob export is disabled and the script should fail fast with a clear message when blob mode is requested.

### Authentication

Use `DefaultAzureCredential` from `azure-identity`.

- **Production (Azure):** Managed Identity
- **Local/dev support:** Azure CLI credentials via `az login`

No storage keys or connection strings should be introduced.

The hosting identity must have the minimum required blob-write role for the target container or storage account.

### Schedule

Run the export once daily at a fixed time that precedes downstream analytics consumption.

The exact scheduler can be environment-specific, but the contract should be:

- one scheduled export attempt per day
- same blob path overwritten with the latest authoritative snapshot
- manual rerun available at any time

This spec does not require the scheduler to live inside the Flask web process.

Preferred operational approach:

- run the export as a scheduled job, cron task, or platform scheduler
- keep it outside request/response handling

### CSV Format

Use the same stable snapshot schema already produced by `scripts/export_sku_snapshot.py`:

```text
# generated_at: 2026-04-12T15:30:00+00:00
# row_count: 261
sku,category,description,length_with_tongue_ft,max_stack_step_deck,max_stack_flat_bed
5X8GW,USA,,12.0,5,4
...
```

Required data columns:

- `sku`
- `category`
- `description`
- `length_with_tongue_ft`
- `max_stack_step_deck`
- `max_stack_flat_bed`

Required metadata comments:

- `generated_at`
- `row_count`

### Failure Behavior

- A failed scheduled export must not affect planner workflows.
- The last successful blob remains available to downstream consumers.
- Export failures must be logged clearly and surfaced through normal operational monitoring.
- A manual rerun must be possible without code changes.

### Freshness Contract

Consumers should treat the blob as a daily snapshot, not a real-time feed.

This means:

- same-day SKU edits may not appear until the next scheduled export
- downstream batch jobs should be scheduled accordingly
- emergency fixes can be handled via manual rerun of the export script

## Dependencies

Add to runtime dependencies:

- `azure-identity`
- `azure-storage-blob`

## Configuration

Minimum configuration:

| Var | Required | Description |
|---|---|---|
| `SKU_EXPORT_STORAGE_ACCOUNT` | Yes for blob export | Azure storage account name |

Optional future configuration may include container/path overrides, but this spec keeps the destination fixed to reduce configuration drift.

## Operational Notes

The export path should be documented for IT/dev support alongside:

- how the daily schedule is configured
- how to run a manual export
- what identity/role assignment is required
- where failures appear in logs

This is an ops/runtime behavior change and should be reflected in the Azure handoff documentation when implemented.

## Testing Strategy

- Unit test the snapshot serialization content and blob path.
- Unit test failure behavior when Azure auth or upload fails.
- Unit test failure behavior when `SKU_EXPORT_STORAGE_ACCOUNT` is missing.
- Smoke test manual export execution path.
- Verify the exported CSV remains compatible with `UtilizationScorer.from_csv()`.

## File Change Summary

| File | Change |
|---|---|
| `scripts/export_sku_snapshot.py` | Modified to support Azure Blob upload in addition to local export |
| `requirements.txt` | Modified to add Azure blob dependencies |
| ops/scheduler configuration | New or updated scheduled daily execution path |
| docs/IT handoff doc | Updated to describe export scheduling and recovery workflow |

