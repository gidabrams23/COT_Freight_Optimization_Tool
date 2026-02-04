import db

from services import validation


def list_customers():
    return db.list_customers()


def get_customer(customer_id):
    return db.get_customer(customer_id)


def count_order_lines(customer_id):
    return db.count_order_lines_for_customer(customer_id)


def create_customer(form):
    name = form.get("name", "").strip()
    zip_code = form.get("zip", "").strip()
    notes = form.get("notes", "").strip()

    errors = {}
    validation.validate_required(name, "name", errors)
    validation.validate_zip(zip_code, errors)

    if errors:
        return {
            "errors": errors,
            "form_data": {"name": name, "zip": zip_code, "notes": notes},
            "success_message": "",
        }

    db.add_customer(name=name, zip_code=zip_code, notes=notes or None)
    return {
        "errors": {},
        "form_data": {"name": "", "zip": "", "notes": ""},
        "success_message": "Customer added successfully.",
    }


def delete_customer(customer_id):
    db.delete_customer(customer_id)
