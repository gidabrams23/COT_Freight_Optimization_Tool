"""
Stateless constraint dispatcher. Called after every add/remove/edit in the UI.
Returns a list of Violation objects for the given session.
"""
from .. import db
from . import pj_rules, bt_rules
from .models import Violation


def check_load(session_id: str) -> list:
    session = db.get_session(session_id)
    if not session:
        return []

    positions = db.get_positions(session_id)
    carrier = db.get_carrier_config(session["carrier_type"])

    if session["brand"] == "pj":
        return pj_rules.check(positions, carrier)
    elif session["brand"] == "bigtex":
        return bt_rules.check(positions, carrier)
    return []

