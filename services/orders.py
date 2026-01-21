import db

from services import totals, validation


def list_orders():
    orders = db.list_orders()
    total_cents = totals.calculate_orders_total_cents(orders)
    return {"orders": orders, "total_cents": total_cents}


def create_order(form):
    customer_id = form.get("customer_id", "").strip()
    origin = form.get("origin", "").strip()
    destination = form.get("destination", "").strip()
    miles = form.get("miles", "").strip()
    rate_cents = form.get("rate_cents", "").strip()

    errors = {}
    if customer_id and not customer_id.isdigit():
        errors["customer_id"] = "Customer ID must be numeric."
    validation.validate_required(origin, "origin", errors)
    validation.validate_required(destination, "destination", errors)
    validation.validate_positive_int(miles, "miles", errors)
    validation.validate_positive_int(rate_cents, "rate_cents", errors)

    if errors:
        return {"errors": errors, "form_data": form}

    db.add_order(
        customer_id=int(customer_id) if customer_id else None,
        origin=origin,
        destination=destination,
        miles=int(miles),
        rate_cents=int(rate_cents),
    )
    return {"errors": {}, "form_data": {}}


def delete_order(order_id):
    db.delete_order(order_id)
