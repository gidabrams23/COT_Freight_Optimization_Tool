from flask import Flask, redirect, render_template, request, url_for

import db
from services import load_builder

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
    return render_template("orders.html")


@app.route("/loads")
def loads():
    return render_template(
        "loads.html",
        loads=db.list_loads_with_lines(),
        capacity_feet=53,
        errors={},
        success_message="",
    )


@app.route("/loads/build", methods=["POST"])
def build_loads():
    capacity_raw = request.form.get("capacity_feet", "").strip()
    errors = {}
    try:
        capacity_feet = float(capacity_raw)
        if capacity_feet <= 0:
            raise ValueError
    except ValueError:
        errors["capacity_feet"] = "Capacity must be a positive number."
        capacity_feet = 53

    order_lines = db.list_order_lines()
    if errors:
        return render_template(
            "loads.html",
            loads=db.list_loads_with_lines(),
            capacity_feet=capacity_feet,
            errors=errors,
            success_message="",
        )

    db.clear_loads()
    loads_data = load_builder.build_loads(order_lines, capacity_feet)
    db.insert_loads(loads_data)
    return render_template(
        "loads.html",
        loads=db.list_loads_with_lines(),
        capacity_feet=capacity_feet,
        errors={},
        success_message=(
            f"Built {len(loads_data)} load(s) from {len(order_lines)} line(s)."
        ),
    )


@app.route("/loads/clear", methods=["POST"])
def clear_loads():
    capacity_raw = request.form.get("capacity_feet", "").strip()
    try:
        capacity_feet = float(capacity_raw)
        if capacity_feet <= 0:
            raise ValueError
    except ValueError:
        capacity_feet = 53
    db.clear_loads()
    return render_template(
        "loads.html",
        loads=db.list_loads_with_lines(),
        capacity_feet=capacity_feet,
        errors={},
        success_message="Cleared all loads.",
    )


@app.route("/dispatch")
def dispatch():
    return render_template("dispatch.html")


if __name__ == "__main__":
    app.run(debug=True)
