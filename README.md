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

## Deploy on Render (Docker)

1. Create a new **Web Service** on Render and connect this repo.
2. Choose **Docker** as the environment (Render will use `Dockerfile`).
3. Set environment variables:
   - `FLASK_SECRET_KEY`: a long random string.
   - `APP_DB_PATH`: set to `/var/data/app.db` if you attach a Render disk.
4. (Recommended) Add a persistent disk:
   - Mount path: `/var/data`
   - Size: 1 GB (or more if needed)
5. Deploy. Render sets `PORT` automatically; the container binds to it.
