from flask import Flask, redirect, render_template, url_for

app = Flask(__name__)


@app.route("/")
def index():
    return redirect(url_for("customers"))


@app.route("/customers")
def customers():
    return render_template("customers.html")


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
