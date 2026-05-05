Seed data snapshots exported from the local SQLite database.

These CSVs are used to initialize empty deployments (e.g., Render) with
reference data for plants, SKUs, lookups, rates, planning settings,
access profiles, and zip coordinate data for maps.

`access_profiles.csv` is updated automatically when profiles are created,
updated, or deleted through the app.

`prograde_access_profiles.csv` seeds ProGrade account names into
`prograde_access_profiles` on startup when those names are missing.

To regenerate from the local database:
  python scripts/export_seed_data.py
