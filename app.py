from flask import Flask, redirect, render_template, request, url_for

import db
from services import customers as customer_service
from services import load_builder, orders as order_service

app = Flask(__name__)

db.init_db()


@app.route("/")
def index():
    return redirect(url_for("customers"))


@app.route("/customers", methods=["GET"])
def customers():
    customer_list = customer_service.list_customers()
    return render_template(
        "customers.html",
        customers=customer_list,
        errors={},
        form_data={"name": "", "zip": "", "notes": ""},
        success_message="",
    )


@app.route("/customers/add", methods=["POST"])
def add_customer():
    result = customer_service.create_customer(request.form)
    return render_template(
        "customers.html",
        customers=customer_service.list_customers(),
        errors=result["errors"],
        form_data=result["form_data"],
        success_message=result["success_message"],
    )


@app.route("/customers/delete/<int:customer_id>", methods=["POST"])
def delete_customer(customer_id):
    customer_service.delete_customer(customer_id)
    return redirect(url_for("customers"))


@app.route("/orders")
def orders():
    order_data = order_service.list_orders()
    return render_template("orders.html", orders=order_data["orders"])


@app.route("/loads")
def loads():
    order_data = order_service.list_orders()
    load_summary = load_builder.build_load_summary(order_data["orders"])
    return render_template("loads.html", load_summary=load_summary)


@app.route("/dispatch")
def dispatch():
    return render_template("dispatch.html")


if __name__ == "__main__":
    app.run(debug=True)
