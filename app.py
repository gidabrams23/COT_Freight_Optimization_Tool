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
    return render_template("orders.html")


@app.route("/loads")
def loads():
    return render_template("loads.html")


@app.route("/dispatch")
def dispatch():
    return render_template("dispatch.html")


if __name__ == "__main__":
    app.run(debug=True)
