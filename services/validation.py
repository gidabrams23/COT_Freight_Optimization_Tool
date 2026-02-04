def validate_required(value, field_name, errors):
    if not value:
        errors[field_name] = f"{field_name.replace('_', ' ').title()} is required."


def validate_zip(zip_code, errors):
    if not zip_code:
        errors["zip"] = "ZIP is required."
        return
    if not zip_code.isdigit() or len(zip_code) != 5:
        errors["zip"] = "ZIP must be exactly 5 digits."


def validate_zip_code(value, field_name, errors):
    if not value:
        errors[field_name] = f"{field_name.replace('_', ' ').title()} is required."
        return
    if not value.isdigit() or len(value) != 5:
        errors[field_name] = (
            f"{field_name.replace('_', ' ').title()} must be exactly 5 digits."
        )


def validate_positive_int(value, field_name, errors):
    if value is None or value == "":
        errors[field_name] = f"{field_name.replace('_', ' ').title()} is required."
        return
    if not str(value).isdigit() or int(value) <= 0:
        errors[field_name] = (
            f"{field_name.replace('_', ' ').title()} must be a positive number."
        )


def validate_positive_float(value, field_name, errors):
    if value is None or value == "":
        errors[field_name] = f"{field_name.replace('_', ' ').title()} is required."
        return
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None or parsed <= 0:
        errors[field_name] = (
            f"{field_name.replace('_', ' ').title()} must be a positive number."
        )
