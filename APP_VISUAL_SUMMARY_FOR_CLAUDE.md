# App Visual + Functional Overview (for Claude)

Last updated: 2026-01-28

## 1) Overall app purpose (1–2 lines)
Internal dispatch optimization web app: ingest open orders (CSV), compute utilization via stacking rules, and generate draft loads using geography/time‑window clustering for review and dispatch planning.


## 2) Visual system (high-level)
- Dark enterprise/logistics theme, bold cards and KPIs, rounded badges, and utilization meters.
- Inter typography + Material Symbols; dense tables with expandable detail panels.
- Card‑first layout and split views (main table + sidebar widgets).


## 3) Functional map (current pages + what they do)

Top Nav: Dashboard · Orders · Loads · Settings

- Dashboard (/dashboard): Executive overview with KPI tiles, plant summary cards, open orders table, and sidebar mini loads + map placeholder.
- Orders (/orders): Primary workbench. Upload/export, Quick Optimize form, filters + summary chips, orders table with expandable rows (line items + stack visualization and warnings).
- Loads (/loads): Review draft loads. Collapsible rows show utilization + metrics; expanded panel includes route timeline, stops map (Leaflet), manifest, schematic, and sidebar stats.
- Load Detail (/loads/<id>): Focused single‑load view (timeline, manifest, schematic, utilization/cost sidebar, over‑capacity banner).
- Upload (/upload): Drag‑drop CSV and import summary (mapping rate, plant totals, unmapped items). GET currently redirects to Orders.
- Settings (/settings?tab=rates|skus|lookups|plants): Admin hub with forms + tables for rates, SKU specs, lookup rules, and plant locations.

Secondary / legacy templates (not in top nav):
- Optimize studio template (full settings + results + draft load cards) exists but /optimize redirects to /orders.
- Standalone Rates/SKUs/Lookups pages mirror Settings tabs.
- Dispatch is a placeholder; Customers + delete confirm templates exist.


## 4) Functional flow (how data moves)
1. Upload CSV (Amanda report) → OrderImporter → DB (orders + order_lines + upload history).
2. Orders table expands → /api/orders/<so_num>/stack-config → stack_calculator → stack layout + utilization.
3. Quick Optimize → /orders/optimize → load_builder.build_loads → draft loads in loads table.
4. Loads page assembles route stops + schematic per load for UI display.


## 5) Core data objects (schema snapshot)
- orders: order‑level summary (so_num, due_date, plant, cust, state/zip, total_length_ft, utilization_pct, is_excluded, etc.)
- order_lines: line items with SKU, qty, length, stack height, destination info.
- loads: draft loads used by current UI (origin/destination, miles, rate, cost, utilization, score, status).
- load_lines: link loads to lines.
- zip_coordinates + plants: geo references.
- optimized_loads / optimization_runs / load_order_assignments: present but not yet wired to UI.


## 6) Current gaps / status notes
- UI still uses legacy loads table; optimized_loads pipeline not integrated.
- Upload page GET redirects to Orders (template exists, POST used for ingest).
- Optimization performance degrades on large datasets.
- Dashboard map is placeholder; Loads map is live.
- No async progress for upload/optimization.


## 7) Likely next phases (high-level)
Phase 1: Reliability + UX polish (progress states, better errors, faster expand rows).
Phase 2: Optimization pipeline rewire (use optimized_loads, run history, approvals).
Phase 3: Dispatch + analytics (dispatch workflows, route maps, plant KPIs).
