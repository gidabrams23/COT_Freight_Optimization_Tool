# AppDev-V2 Current State Summary (Jan 22, 2026)

## Purpose (Current)
Lightweight internal web app to manage dispatch planning. It currently supports:
- Customer management.
- Order lines (line items) with qty/feet/due date/notes.
- Load building: greedy packing of order lines into loads based on trailer capacity.
- Basic load plan display.

## Tech Stack
- Python 3.x + Flask 2.3.3
- SQLite (local `app.db`)
- Jinja2 templates + single CSS file

## Primary Entities & Definitions
- **Customer**: name, ZIP, notes.
- **Order Line**: qty, feet per unit, due date, notes, linked to a customer.
- **Load**: shipment with origin, destination, miles, rate (cents), capacity (ft), total feet (ft).
- **Load Line**: association between a load and order line; stores line total feet.

## Database Schema (Current)
- `customers(id, name, zip, notes, created_at)`
- `order_lines(id, customer_id, qty, feet_per_unit, due_date, notes, created_at)`
- `loads(id, origin, destination, miles, rate_cents, capacity_feet, total_feet, created_at)`
- `load_lines(id, load_id, order_line_id, line_total_feet, created_at)`

## Routes & Pages
- `/` ? redirect to `/customers`
- `/customers` (GET): list + add customer form
- `/customers/add` (POST): create customer
- `/customers/delete/<id>` (GET): confirmation page showing order line count
- `/customers/delete/<id>` (POST): deletes customer + their order lines

- `/orders` (GET): order lines list + add form + totals summary
- `/orders/add` (POST): add order line
- `/orders/delete/<id>` (POST): delete order line
- `/orders/clear` (POST): clear all order lines

- `/loads` (GET): build form + load plan table
- `/loads/build` (POST): validate inputs, clear existing loads, then build loads
- `/loads/clear` (POST): clear all loads

- `/dispatch` (GET): placeholder

## Current UI Behavior
- Sidebar navigation to Customers, Orders, Loads, Dispatch.
- Customers: Add + list + delete (delete opens confirmation page with order count warning).
- Orders: Add order line (customer, qty, feet/unit, due date, notes), list order lines, show totals:
  - total lines, total quantity, total feet.
- Loads: Form inputs for origin, destination, miles, rate (cents), capacity (feet). Builds loads using a greedy pack algorithm.
- Loads table shows loads and their lines (customer name, qty, feet/unit, total feet, due date, notes).

## Load Build Logic (Current)
- Requires at least one order line.
- Validates origin, destination, miles, rate, capacity.
- Validates capacity >= largest single order line.
- Clears existing loads before building new ones.
- Greedy pack: sort by due_date (earlier first), then created_at, then id; add lines until capacity is reached, then start a new load.

## Known Constraints / Gaps
- No authentication or roles.
- No edit flows (customers, order lines, loads).
- Deleting a customer deletes their order lines (no soft delete).
- No persistence of “dispatch” data yet (page is placeholder).
- Rate is stored as cents (no formatting or currency display in UI).

## Files of Interest
- Backend routes: `app.py`
- DB + schema: `db.py`
- Services: `services/customers.py`, `services/orders.py`, `services/load_builder.py`, `services/totals.py`, `services/validation.py`
- Templates: `templates/*.html`
- Styles: `static/styles.css`

## Suggested PRD Topics
- User roles & permissions.
- Order lifecycle (draft ? scheduled ? dispatched ? completed).
- Load optimization strategy and constraints.
- Dispatch sheet generation + exports (PDF/CSV).
- Edit/undo flows and audit trail.
- Reporting and analytics (utilization, margin per load, on-time rate).

