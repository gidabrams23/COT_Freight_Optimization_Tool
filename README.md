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
