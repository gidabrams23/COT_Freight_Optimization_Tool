# AppDev-V2
Try 2 at building a web optimization app.

## Repo layout

- `app.py`: compatibility entrypoint/shim (exports Flask app)
- `blueprints/cot/routes.py`: COT blueprint routes and controller logic
- `blueprints/prograde/routes.py`: ProGrade blueprint routes + load-builder APIs
- `blueprints/prograde/db.py`: ProGrade SQLite schema/data helpers (sessions, positions, SKUs, settings, inventory snapshot)
- `blueprints/prograde/services/`: ProGrade rule engines and constraint checks (BT/PJ)
- `blueprints/prograde/templates/prograde/load_builder.html`: ProGrade stacking/schematic UI (markup + CSS + JS)
- `db.py`: SQLite helpers (DB lives at `data/db/app.db`)
- `services/`: business logic + optimization utilities
- `templates/`: Jinja templates
- `static/`: static assets (CSS, client-side data)
- `scripts/`: one-off import/maintenance scripts
- `docs/`: PRDs/specs/notes
- `data/`: local DB + reference/sample inputs

## ProGrade docs

- `docs/specs/PROGRADE_EDIT_MAP.md`: file-by-file edit map for ProGrade, focused on stacking logic and schematic changes.
- `docs/specs/PROGRADE_BT_INVENTORY_GAP_WORKFLOW.md`: Inventory gap workflow (BT upload mode + PJ catalog mode).
- `docs/specs/PROGRADE_VISUAL_GUIDELINES.md`: UI visual contract for ProGrade pages.

## ProGrade account workflow

- ProGrade account profiles are stored in the ProGrade database (`prograde_access_profiles`) and do not reuse COT `access_profiles`.
- Landing page for ProGrade account access is `/prograde` (also available at `/prograde/account`) with:
  - account dropdown selection
  - quick add account by name
- Once selected, the active account persists in the current user session and is used for new load creation.
- New loads inherit the currently selected ProGrade account as the builder and display that user in the All Sessions table.
- `All Sessions` (`/prograde/sessions`) is owner-scoped by default:
  - admin accounts can view all saved sessions
  - planner accounts only see sessions they built
- Default admin account name is seeded from `PROGRADE_DEFAULT_ADMIN_NAME`; fallback is OS `USERNAME` (or `Admin`).

## Setup

1. Create and activate a virtual environment.
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies.
   ```bash
   pip install -r requirements.txt
   ```

## Run

```bash
python app.py
```

Open `http://127.0.0.1:5000` in your browser. You should land on the home page and be able to navigate between sections using the app's navigation links or menu.

## Daily Open Orders Refresh Workflow

The Orders page now shows an **Up to Date / Outdated** intake status and a step-by-step guide modal.

If the latest orders upload is not from today, users are prompted after login with:
`Open order report outdated: please refresh data and upload to tool.`

Guide checklist:
1. Open SharePoint folder: `https://bigtextrailers.sharepoint.com/sites/COTLoadPlanning/Shared%20Documents/Forms/AllItems.aspx`
2. Open `COT Freight Tool_Open Order Query.xlsm`
3. Click **Refresh and Export** and wait for the message: `Done. File saved to...`
4. Click **Upload Orders** in Intake Hub and select today's export from the Exports folder
5. Wait for processing and complete upload

## Microsoft Entra SSO

This app supports Microsoft Entra (Azure AD) login with automatic access-profile mapping by email.

### 1) Register an Entra app

1. Create an Entra app registration.
2. Add a Web redirect URI:
   - Local example: `http://127.0.0.1:5000/auth/microsoft/callback`
   - Hosted example: `https://<your-host>/auth/microsoft/callback`
3. Create a client secret.
4. Grant delegated permissions for `openid`, `profile`, `email`, and `User.Read`.

### 2) Set environment variables

```bash
ENTRA_SSO_ENABLED=true
ENTRA_TENANT_ID=<tenant-id-or-domain>
ENTRA_CLIENT_ID=<application-client-id>
ENTRA_CLIENT_SECRET=<application-client-secret>
ENTRA_REDIRECT_URI=<optional-absolute-callback-uri>
```

Optional controls:

```bash
ENTRA_SSO_REQUIRED=true                 # force Microsoft SSO
ENTRA_ALLOW_LEGACY_LOGIN=false          # disable old profile/password login
ENTRA_ALLOWED_EMAIL_DOMAINS=company.com # optional comma-separated domain allowlist
ENTRA_SCOPES="openid profile email User.Read"
```

### 3) Map Entra emails to app profiles

1. Sign in as an admin.
2. Go to `Access Profiles` (`/access/manage`).
3. For each profile, populate `Microsoft Sign-In Emails`.
4. Users who sign in with a mapped email are automatically assigned to that profile.

## Road Routing Defaults

By default, optimization and cost calculations stay on haversine (straight-line) distance.
OpenRouteService is used only to fetch street-aware map geometry when a load map is viewed.
If ORS credentials are missing or provider limits/errors occur, map rendering falls back
to straight-line geometry.

To preserve API quota, detailed road geometry is fetched on-demand when a load map is first
viewed, then cached/persisted for reuse.

To get street-aware route geometry and road-mile distances, set only:

```bash
ORS_API_KEY=<your-openrouteservice-key>
```

Optional overrides:

```bash
ROUTING_ENABLED=true
ROUTING_PROVIDER=ors
ROUTING_PROFILE=driving-hgv
ROUTING_TIMEOUT_MS=5000
ROUTING_SNAP_RADIUS_M=5000
ROUTING_GEOMETRY_ONLY=true
```

To force straight-line behavior only:

```bash
ROUTING_ENABLED=false
```

## Deploy on Render (Docker)

1. Create a new **Web Service** on Render and connect this repo (or use `render.yaml` blueprint in this repo).
2. Choose **Docker** as the environment (Render will use `Dockerfile`).
3. Set environment variables:
   - `FLASK_SECRET_KEY`: a long random string.
   - `ADMIN_PASSWORD`: required for admin login in non-development environments.
   - `APP_DB_PATH`: set to `/var/data/app.db` if you attach a Render disk.
   - Optional: `ACCESS_PROFILES_SEED_PATH` (defaults to `data/seed/access_profiles.csv`).
   - Optional: `ACCESS_PROFILE_IDENTITIES_SEED_PATH` (defaults to `data/seed/access_profile_identities.csv`).
   - Optional Gunicorn tuning:
     - `WEB_CONCURRENCY` (default `1`, recommended for SQLite + in-process reoptimization jobs)
     - `GUNICORN_THREADS` (default `2`)
     - `GUNICORN_TIMEOUT` (default `180`)
4. (Recommended) Add a persistent disk:
   - Mount path: `/var/data`
   - Size: 1 GB (or more if needed)
5. Deploy. Render sets `PORT` automatically; the container binds to it.

On first boot with an empty DB, app defaults (SKU specs, rate matrix, lookup tables, plants, and planning defaults) are seeded from `data/seed/`.
Access profiles and Microsoft email mappings are seeded from:
- `data/seed/access_profiles.csv`
- `data/seed/access_profile_identities.csv`

Profile persistence notes:
- On Render, account changes persist across deploys when `APP_DB_PATH` points to a mounted disk (`/var/data/app.db`).
- The app snapshots access state on profile create/update/delete to:
  - `data/seed/access_profiles.csv`
  - `data/seed/access_profile_identities.csv`
  Commit both files to preserve accounts + Entra email mappings for fresh environments.

## Sync Local Settings To Render

If your local DB is the source of truth (plant defaults, auto-hotshot toggles, SKU dimensions), use this workflow before deploy:

1. Export local DB snapshots into seed CSV files:
   ```bash
   python scripts/export_seed_data.py --tables optimizer_settings sku_specifications planning_settings access_profiles access_profile_identities
   ```
2. Commit and push the updated files under `data/seed/`.
3. Deploy to Render.
4. For an existing Render disk (already has a DB), open a Render shell and apply the seed snapshots into the live DB:
   ```bash
   python scripts/apply_seed_snapshots.py --tables optimizer_settings sku_specifications planning_settings access_profiles access_profile_identities
   ```
5. Restart the service.

Notes:
- Fresh Render disks will auto-seed from `data/seed/` at boot; step 4 is mainly for already-initialized disks.
- `scripts/export_seed_data.py` now includes `optimizer_settings.auto_hotshot_enabled` so plant-level hotshot checkbox values are preserved.
