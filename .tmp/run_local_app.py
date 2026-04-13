import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app
app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
