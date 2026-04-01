import sys

from blueprints.cot import routes as _cot_routes

app = _cot_routes.app

if __name__ == "__main__":
    app.run(debug=True)
else:
    sys.modules[__name__] = _cot_routes
