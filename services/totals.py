def calculate_order_total_cents(order):
    return order.get("rate_cents", 0)


def calculate_orders_total_cents(orders):
    return sum(calculate_order_total_cents(order) for order in orders)
