# AppDev-V2
Try 2 at building a web optimization app.

## Repo layout

- `app.py`: Flask entrypoint
- `db.py`: SQLite helpers (DB lives at `data/db/app.db`)
- `services/`: business logic + optimization utilities
- `templates/`: Jinja templates
- `static/`: static assets (CSS, client-side data)
- `scripts/`: one-off import/maintenance scripts
- `docs/`: PRDs/specs/notes
- `data/`: local DB + reference/sample inputs

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
   - Optional Gunicorn tuning:
     - `WEB_CONCURRENCY` (default `4`)
     - `GUNICORN_THREADS` (default `2`)
     - `GUNICORN_TIMEOUT` (default `180`)
4. (Recommended) Add a persistent disk:
   - Mount path: `/var/data`
   - Size: 1 GB (or more if needed)
5. Deploy. Render sets `PORT` automatically; the container binds to it.

On first boot with an empty DB, app defaults (SKU specs, rate matrix, lookup tables, plants, and planning defaults) are seeded from `data/seed/`.
