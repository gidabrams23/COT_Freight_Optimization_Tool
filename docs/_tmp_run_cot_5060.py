import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import app
app.run(host="127.0.0.1", port=5060, debug=False, use_reloader=False)
