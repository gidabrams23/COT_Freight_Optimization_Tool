import db


def list_orders(filters=None, sort_key="due_date"):
    orders = db.list_orders(filters=filters, sort_key=sort_key)
    summary = summarize_orders(orders)
    return {"orders": orders, "summary": summary}


def summarize_orders(orders):
    total_orders = len(orders)
    total_length = sum(order.get("total_length_ft") or 0 for order in orders)
    avg_utilization = (
        sum(order.get("utilization_pct") or 0 for order in orders) / total_orders
        if total_orders
        else 0.0
    )
    trailers_required = total_length / 53 if total_length else 0.0
    return {
        "total_orders": total_orders,
        "total_length": total_length,
        "avg_utilization": avg_utilization,
        "trailers_required": trailers_required,
    }


def exclude_orders(order_ids):
    db.update_orders_excluded(order_ids, True)


def include_orders(order_ids):
    db.update_orders_excluded(order_ids, False)
