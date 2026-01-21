from flask import Flask, redirect, render_template, request, url_for

import db

app = Flask(__name__)

db.init_db()


@app.route("/")
def index():
    return redirect(url_for("customers"))


@app.route("/customers", methods=["GET"])
def customers():
    customer_list = db.list_customers()
    return render_template(
        "customers.html",
        customers=customer_list,
        errors={},
        form_data={"name": "", "zip": "", "notes": ""},
        success_message="",
    )


@app.route("/customers/add", methods=["POST"])
def add_customer():
    name = request.form.get("name", "").strip()
    zip_code = request.form.get("zip", "").strip()
    notes = request.form.get("notes", "").strip()

    errors = {}
    if not name:
        errors["name"] = "Name is required."
    if not zip_code:
        errors["zip"] = "ZIP is required."
    elif not zip_code.isdigit() or len(zip_code) != 5:
        errors["zip"] = "ZIP must be exactly 5 digits."

    if errors:
        return render_template(
            "customers.html",
            customers=db.list_customers(),
            errors=errors,
            form_data={"name": name, "zip": zip_code, "notes": notes},
            success_message="",
        )

    db.add_customer(name=name, zip_code=zip_code, notes=notes or None)
    return render_template(
        "customers.html",
        customers=db.list_customers(),
        errors={},
        form_data={"name": "", "zip": "", "notes": ""},
        success_message="Customer added successfully.",
    )


@app.route("/customers/delete/<int:customer_id>", methods=["POST"])
def delete_customer(customer_id):
    db.delete_customer(customer_id)
    return redirect(url_for("customers"))


@app.route("/orders")
def orders():
    customer_list = db.list_customers()
    order_lines = db.list_order_lines()
    totals = {
        "total_lines": len(order_lines),
        "total_qty": sum(line["qty"] for line in order_lines),
        "total_feet": sum(line["qty"] * line["feet_per_unit"] for line in order_lines),
    }
    return render_template(
        "orders.html",
        customers=customer_list,
        order_lines=order_lines,
        totals=totals,
        errors={},
        form_data={
            "customer_id": "",
            "qty": "",
            "feet_per_unit": "",
            "due_date": "",
            "notes": "",
        },
        success_message="",
        active_page="orders",
    )


@app.route("/orders/add", methods=["POST"])
def add_order_line():
    customer_list = db.list_customers()
    order_lines = db.list_order_lines()
    totals = {
        "total_lines": len(order_lines),
        "total_qty": sum(line["qty"] for line in order_lines),
        "total_feet": sum(line["qty"] * line["feet_per_unit"] for line in order_lines),
    }

    customer_id_raw = request.form.get("customer_id", "").strip()
    qty_raw = request.form.get("qty", "").strip()
    feet_raw = request.form.get("feet_per_unit", "").strip()
    due_date = request.form.get("due_date", "").strip()
    notes = request.form.get("notes", "").strip()

    errors = {}
    customer_ids = {str(customer["id"]) for customer in customer_list}
    if not customer_id_raw:
        errors["customer_id"] = "Customer is required."
    elif customer_id_raw not in customer_ids:
        errors["customer_id"] = "Select a valid customer."

    try:
        qty = int(qty_raw)
    except ValueError:
        qty = None
        errors["qty"] = "Quantity must be a whole number."

    try:
        feet_per_unit = float(feet_raw)
    except ValueError:
        feet_per_unit = None
        errors["feet_per_unit"] = "Feet per unit must be a number."

    if qty is not None and feet_per_unit is not None:
        errors.update(db.validate_order_line(qty, feet_per_unit))

    if errors:
        return render_template(
            "orders.html",
            customers=customer_list,
            order_lines=order_lines,
            totals=totals,
            errors=errors,
            form_data={
                "customer_id": customer_id_raw,
                "qty": qty_raw,
                "feet_per_unit": feet_raw,
                "due_date": due_date,
                "notes": notes,
            },
            success_message="",
            active_page="orders",
        )

    db.add_order_line(
        customer_id=int(customer_id_raw),
        qty=qty,
        feet_per_unit=feet_per_unit,
        due_date=due_date or None,
        notes=notes or None,
    )

    order_lines = db.list_order_lines()
    totals = {
        "total_lines": len(order_lines),
        "total_qty": sum(line["qty"] for line in order_lines),
        "total_feet": sum(line["qty"] * line["feet_per_unit"] for line in order_lines),
    }
    return render_template(
        "orders.html",
        customers=customer_list,
        order_lines=order_lines,
        totals=totals,
        errors={},
        form_data={
            "customer_id": "",
            "qty": "",
            "feet_per_unit": "",
            "due_date": "",
            "notes": "",
        },
        success_message="Order line added successfully.",
        active_page="orders",
    )


@app.route("/orders/delete/<int:order_line_id>", methods=["POST"])
def delete_order_line(order_line_id):
    db.delete_order_line(order_line_id)
    return redirect(url_for("orders"))


@app.route("/orders/clear", methods=["POST"])
def clear_order_lines():
    db.clear_order_lines()
    return redirect(url_for("orders"))


@app.route("/loads")
def loads():
    return render_template("loads.html")


@app.route("/dispatch")
def dispatch():
    return render_template("dispatch.html")


if __name__ == "__main__":
    app.run(debug=True)
