# Daily SKU Blob Snapshot Export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the daily SKU snapshot blob export described in `docs/superpowers/specs/2026-04-12-sku-blob-export-design.md`, including script support, a documented scheduling contract, ops handoff updates, and verification of the manual recovery workflow.

**Architecture:** `scripts/export_sku_snapshot.py` remains the single export implementation and gains blob-upload support. The web app does not trigger exports from routes. A platform or ops scheduler runs the script once daily. The export stays safe to rerun manually.

**Tech Stack:** `azure-identity`, `azure-storage-blob`, existing `csv`/`io` stdlib, current SQLite DB access helpers

**Spec:** `docs/superpowers/specs/2026-04-12-sku-blob-export-design.md`

---

## File Map

| File | Role |
|---|---|
| `scripts/export_sku_snapshot.py` | Modified — add blob upload support and shared serialization |
| `requirements.txt` | Modified — add `azure-identity`, `azure-storage-blob` |
| `tests/test_sku_export.py` | New — unit tests for serialization, blob upload, and CLI/manual-path compatibility |
| `docs/IT_HANDOFF_AZURE_ARCHITECTURE_AND_MAINTENANCE.md` | Modified — document schedule, identity, recovery, and monitoring |
| `README.md` or scheduler runbook doc | Modified if needed — document manual execution path for developers/support |
| ops scheduler configuration artifact or runbook note | New or updated — define the daily execution contract |

---

## Task 1: Add Azure Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add Azure SDK dependencies**

Add these packages to `requirements.txt`:

```text
azure-identity>=1.15.0
azure-storage-blob>=12.19.0
```

- [ ] **Step 2: Verify dependency placement**

Keep the additions consistent with the repo’s existing dependency style. Do not move unrelated packages or refactor dependency management as part of this task.

---

## Task 2: Extend the Export Script for Blob Upload

**Files:**
- Modify: `scripts/export_sku_snapshot.py`

- [ ] **Step 1: Extract reusable serialization**

Refactor the script to expose a shared helper:

```python
def _serialize_snapshot(specs) -> str:
    ...
```

Requirements:

- preserve the current CSV column order
- preserve the metadata comment lines
- keep output compatible with `UtilizationScorer.from_csv()`

- [ ] **Step 2: Add blob upload support**

Add a blob-upload path:

```python
def export_sku_snapshot_to_blob(storage_account=None):
    ...
```

Requirements:

- use `DefaultAzureCredential`
- read the account name from `SKU_EXPORT_STORAGE_ACCOUNT` unless explicitly passed
- write to container `reference`
- write to blob `freight/cot_load_scoring/sku_specifications.csv`
- overwrite the existing blob
- return a useful success value such as the blob URL
- return `None` on failure
- log failures clearly

- [ ] **Step 3: Extend CLI surface**

Add a `--blob` flag to `scripts/export_sku_snapshot.py`.

Expected modes:

- local mode: `python3 scripts/export_sku_snapshot.py [--output PATH]`
- blob mode: `python3 scripts/export_sku_snapshot.py --blob`

Behavior requirements:

- local mode keeps current behavior
- blob mode exits non-zero on upload failure
- missing storage account configuration fails clearly in blob mode

- [ ] **Step 4: Keep manual recovery straightforward**

The script must remain safe for manual reruns by support or ops:

- no Flask app startup required
- no request context required
- no temp-file dependency for blob mode

---

## Task 3: Add Targeted Tests

**Files:**
- Create: `tests/test_sku_export.py`

- [ ] **Step 1: Test serialization**

Add tests for `_serialize_snapshot(...)` that verify:

- metadata comment lines are present
- CSV header is present
- data rows are present
- empty-spec behavior is well-defined

- [ ] **Step 2: Test local export**

Mock `db.list_sku_specs()` and verify:

- local file is written
- expected content appears
- no-spec case returns `None`

- [ ] **Step 3: Test blob export**

Mock Azure SDK usage and verify:

- correct account URL is constructed
- correct container and blob path are used
- uploaded bytes contain serialized CSV content
- no-op/failure behavior when storage account is unset
- failure isolation when Azure auth or upload fails

- [ ] **Step 4: Test scorer compatibility**

Add a compatibility test proving the exported snapshot format is still readable by:

```python
UtilizationScorer.from_csv(...)
```

This protects the downstream integration contract.

- [ ] **Step 5: Add a manual-path smoke test**

Add at least one test or verification step that exercises the supported manual execution path more directly than unit-testing helper functions.

Acceptable options:

- invoke the script entrypoint in local mode against mocked DB data, or
- invoke the script entrypoint in blob mode with mocked Azure modules and environment

The purpose is to verify the supported recovery workflow, not just internal helpers.

---

## Task 4: Implement the Scheduling Contract

**Files:**
- Add or update: scheduler/runbook artifact appropriate to this repo
- Modify: `docs/IT_HANDOFF_AZURE_ARCHITECTURE_AND_MAINTENANCE.md`

- [ ] **Step 1: Define the scheduler ownership explicitly**

Document the daily execution contract in repo-controlled artifacts.

At minimum, specify:

- the command to run
- that it runs once daily
- that it runs outside the Flask request lifecycle
- required environment variables
- expected identity/permission prerequisites

If there is no platform-specific scheduler file in this repo today, add a runbook-style artifact or doc section that operations can implement directly.

- [ ] **Step 2: Choose and document a concrete daily schedule**

Record a fixed daily export time or a required scheduling window in UTC.

The implementation plan must not leave the cadence ambiguous. It should state when the export is expected to run relative to downstream analytics jobs.

- [ ] **Step 3: Document manual rerun procedure**

Document the exact manual recovery command, including any required environment variables, for example:

```bash
SKU_EXPORT_STORAGE_ACCOUNT=<account> python3 scripts/export_sku_snapshot.py --blob
```

---

## Task 5: Update Ops and Handoff Documentation

**Files:**
- Modify: `docs/IT_HANDOFF_AZURE_ARCHITECTURE_AND_MAINTENANCE.md`
- Modify: `README.md` if developer/support usage needs to be discoverable there

- [ ] **Step 1: Update Azure IT handoff doc**

Add a section covering:

- purpose of the daily SKU blob snapshot
- where the blob is written
- required Managed Identity permissions
- how the daily schedule is configured
- how to run the export manually
- what log messages indicate success or failure
- what to do if the scheduled export fails

- [ ] **Step 2: Update developer-facing docs if needed**

If support or local development workflows benefit from it, add a brief note to `README.md` or another appropriate runtime doc explaining manual blob export usage.

Do not add broad platform architecture changes unrelated to this feature.

---

## Task 6: Verification

- [ ] **Step 1: Run targeted export tests**

Run the new export test file plus scorer compatibility coverage:

```bash
cd /home/atw/COT_Freight_Optimization_Tool
PYTHONPATH=/home/atw/COT_Freight_Optimization_Tool uvx --with pandas pytest tests/test_sku_export.py tests/test_cot_utilization_scorer.py -v
```

- [ ] **Step 2: Run relevant regression tests**

Run the package/core tests that guard the utilization logic:

```bash
cd /home/atw/COT_Freight_Optimization_Tool
PYTHONPATH=/home/atw/COT_Freight_Optimization_Tool uvx --with pandas pytest tests/test_cot_utilization_core.py tests/test_stack_calculator_assumptions.py -v
```

- [ ] **Step 3: Verify CLI help**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 scripts/export_sku_snapshot.py --help
```

Expected:

- help output shows both local-output and `--blob` usage

- [ ] **Step 4: Verify blob mode fails cleanly when config is missing**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
SKU_EXPORT_STORAGE_ACCOUNT="" python3 scripts/export_sku_snapshot.py --blob
```

Expected:

- clear error message about missing storage account
- non-zero exit code

- [ ] **Step 5: Verify the manual success path**

Exercise the supported manual recovery path in a controlled environment, using either:

- real credentials in an approved environment, or
- a fully mocked execution path that enters the script’s `main()` blob mode end-to-end

Record what was actually validated. Do not claim the manual recovery workflow works without evidence.

---

## Definition of Done

- [ ] The export script supports both local output and blob upload.
- [ ] The daily scheduling contract is captured in repo-controlled docs or scheduler artifacts.
- [ ] The Azure IT handoff doc is updated with schedule, identity, logging, and recovery guidance.
- [ ] Tests cover serialization, blob upload behavior, and scorer compatibility.
- [ ] The manual recovery workflow is explicitly verified or any remaining gap is clearly documented.
- [ ] No Flask route hooks or request-triggered export logic are introduced.

