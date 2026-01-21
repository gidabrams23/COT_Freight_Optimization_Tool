from services import totals


def build_load_summary(orders):
    return {
        "order_count": len(orders),
        "total_rate_cents": totals.calculate_orders_total_cents(orders),
    }
