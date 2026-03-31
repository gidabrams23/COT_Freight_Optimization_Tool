# ProGrade Load Builder — Product Requirements Document
**Version:** 0.3 (Prototype Build Target)
**Date:** 2026-03-25
**Lens:** Build structures and logic the team can react to — not perfection on data, but a working skeleton end-to-end

---

## 1. What We're Building (Prototype Scope)

A Flask web app that lets a load planner:
1. Pick a brand (PJ or Big Tex) and start a load session
2. Select trailers from a SKU master and assign them to deck positions
3. See real-time validation against constraint rules
4. Adjust editable assumptions on a settings page
5. Export a printable load schematic

The prototype is not trying to be complete on every SKU and every rule edge case. It is trying to prove out the **data model, constraint engine structure, and UX flow** so the planning team can react with "this is right / this is wrong / we're missing X."

---

## 2. Resolved Open Questions

| OQ | Answer | Implementation note |
|---|---|---|
| OQ-S1 | Lower deck = **41'**, Upper deck = **12'** | Stored in carrier config; editable in Settings |
| OQ-S2 | Lower deck = **3.5' off ground** (10' clearance above), Upper deck = **5.0' off ground** (8.5' clearance above) | Stored in carrier config; editable in Settings |
| OQ-S3 | Width is **not a constraint** — N/A | Remove from constraint engine entirely |
| OQ-S4 | GNs can go on upper deck and **spill over onto lower deck** | Engine checks total footprint fits within 53'; zone assignment is a starting position, not a hard boundary |
| OQ-S5 | LDG/LDW left-right pairing rules — **unknown** | Stub: add `pairing_rule` field to settings cheat sheet; leave blank; planners fill in |
| OQ-S6 | Dump side heights (3' vs 4') mapping — **build settings structure, team fills in** | Add `dump_side_height` field to SKU settings table; default to 'unknown'; planner editable |
| OQ-BT1 | Big Tex Tier = **demand velocity** (Tier 1 = fastest moving) | Display as a badge on SKU card; not used in constraint logic |

---

## 3. Core Architecture

Single Flask app, SQLite, Jinja + vanilla JS. One codebase, brand-toggled at session level.

```
app.py                          # Routes
db.py                           # Raw SQL, WAL mode
brand_config.py                 # Carrier geometry, stack position defs per brand
services/
  load_constraint_checker.py    # Dispatcher → calls brand-specific rule modules
  pj_rules.py                   # PJ constraint engine
  bt_rules.py                   # Big Tex constraint engine
  pj_measurement.py             # Shawn's measurement conventions
  pattern_engine.py             # Suggests known-good patterns
templates/
  settings.html                 # Editable assumptions page (critical for prototype)
  session_start.html
  load_builder.html             # Main canvas
  load_detail.html
  export.html
```

---

## 4. The Settings Page — Most Important Screen for Prototype

The Settings page is where every uncertain assumption lives in an editable, documented form. The goal: when the planning team opens the tool, they can see exactly what the tool believes about dimensions and rules, correct anything wrong, and watch the schematic update.

### 4.1 Settings page sections

**Carrier Config** (one row per carrier type, currently just `53_step_deck` and `53_flatbed`)

| Setting | Default Value | Label shown in UI |
|---|---|---|
| `total_length_ft` | 53.0 | Total deck length (ft) |
| `max_height_ft` | 13.5 | Max load height — ground to top (ft) |
| `lower_deck_length_ft` | 41.0 | Lower deck length (ft) |
| `upper_deck_length_ft` | 12.0 | Upper deck length (ft) |
| `lower_deck_ground_height_ft` | 3.5 | Lower deck height off ground (ft) |
| `upper_deck_ground_height_ft` | 5.0 | Upper deck height off ground (ft) |
| `gn_max_lower_deck_ft` | 32.0 | Longest GN that sits flat on lower deck (ft) |

All rows editable inline. Change → saves to DB → all active calculations immediately reflect the update.

**PJ Tongue Length Groups** (editable table: model → tongue_feet)

Displays Shawn's six groups as a readable table. Each row: model code, tongue group label, tongue feet. Editable so if a new model is added or a measurement is corrected, no code change required.

**PJ Stack Height Reference** (editable table: category → height_mid, height_top)

Displays the full height table from Shawn's email. Each row: category label, height at bottom/middle, height at top. Editable.

**PJ Measurement Offsets** (editable table: rule → offset feet)

| Rule | Default | Label |
|---|---|---|
| `car_hauler_spare_mount_offset` | 1.0 | Extra feet added to car hauler bed for spare mount |
| `dump_tarp_kit_offset` | 1.0 | Extra feet added to all dump beds for tarp kit |
| `dtj_cylinder_extra_offset` | 1.0 | Additional feet for DTJ cylinder (stacks on top of tarp offset) |
| `gn_in_dump_hidden_ft` | 7.0 | Feet of GN hidden inside a dump body |

**PJ SKU Overrides** (editable cheat sheet table — the main one for team reaction)

One row per model code. Columns:
- `model` — e.g., `DL`
- `pj_category` — dropdown from category list
- `tongue_group` — dropdown from tongue groups
- `dump_side_height_ft` — numeric, nullable (for dumps: 0=lowside, 3, or 4 ft sides) — **team fills this in**
- `can_nest_inside_dump` — boolean
- `gn_axle_droppable` — boolean
- `tongue_overlap_allowed` — boolean
- `pairing_rule` — text field, nullable — **team fills in LDG/LDW pairing rules here**
- `notes` — free text

This table is visible and editable in the Settings page. It is the PJ equivalent of the COT Master Load Building Cheat Sheet — the single reference the tool uses for all model-level behavior.

**Big Tex SKU Tier Reference** (read-only display, editable via import)

Shows Tier 1–4 with label: "Tier 1 = fastest moving product." Planners can see the tier badge on each SKU card.

**Big Tex Stack Constraint Config** (editable table per stack configuration)

| Config | Setting | Default |
|---|---|---|
| 3-Stack Utility | Stack 1+2 max combined length | 40.0' |
| 3-Stack Utility | Stack 3 max length | 15.5' |
| 3-Stack Utility | Stack 1 max height | 5.25' |
| 3-Stack Utility | Stack 2 max height | 5.25' |
| 3-Stack Utility | Stack 3 max height | 4.0' |
| 2-Stack Utility | Stack 1+2 max combined | 40.5' |
| Dump 3-Stack | Stack 1 max | 21.0' |
| Dump 3-Stack | Stack 2 max | 16.0' |
| Dump 3-Stack | Stack 3 max | 16.0' |
| Dump 3-Stack | All stacks max height | 5.0' |

---

## 5. Data Model

### 5.1 `carrier_configs` table

```sql
CREATE TABLE carrier_configs (
  carrier_type TEXT PRIMARY KEY,  -- '53_step_deck', '53_flatbed'
  brand TEXT,
  total_length_ft REAL,
  max_height_ft REAL,
  lower_deck_length_ft REAL,
  upper_deck_length_ft REAL,
  lower_deck_ground_height_ft REAL,
  upper_deck_ground_height_ft REAL,
  gn_max_lower_deck_ft REAL,
  notes TEXT,
  updated_at DATETIME
);
```

### 5.2 `pj_tongue_groups` table

```sql
CREATE TABLE pj_tongue_groups (
  group_id TEXT PRIMARY KEY,  -- 'c_channel', 'deck_over', 'dump_std', 'dump_small', 'pintle', 'gooseneck'
  group_label TEXT,
  tongue_feet REAL,
  model_codes TEXT,  -- comma-separated list for display
  notes TEXT,
  updated_at DATETIME
);
```

### 5.3 `pj_height_reference` table

```sql
CREATE TABLE pj_height_reference (
  category TEXT PRIMARY KEY,
  label TEXT,
  height_mid_ft REAL,   -- when at bottom or middle of load
  height_top_ft REAL,   -- when at top of load
  gn_axle_dropped_ft REAL,  -- nullable; GN axle-drop override
  notes TEXT,
  updated_at DATETIME
);
```

### 5.4 `pj_measurement_offsets` table

```sql
CREATE TABLE pj_measurement_offsets (
  rule_key TEXT PRIMARY KEY,
  label TEXT,
  offset_ft REAL,
  notes TEXT,
  updated_at DATETIME
);
```

### 5.5 `pj_skus` table

```sql
CREATE TABLE pj_skus (
  item_number TEXT PRIMARY KEY,
  model TEXT,
  pj_category TEXT,
  description TEXT,
  gvwr INTEGER,
  bed_length_stated REAL,
  bed_length_measured REAL,  -- computed at seed time from measurement rules
  tongue_group TEXT,  -- FK to pj_tongue_groups
  tongue_feet REAL,   -- denormalized for fast access
  total_footprint REAL,  -- computed: bed_length_measured + tongue_feet
  dump_side_height_ft REAL,  -- null if not a dump
  can_nest_inside_dump BOOLEAN DEFAULT 0,
  gn_axle_droppable BOOLEAN DEFAULT 0,
  tongue_overlap_allowed BOOLEAN DEFAULT 0,
  pairing_rule TEXT,   -- null until filled in by team
  notes TEXT,
  updated_at DATETIME
);
```

### 5.6 `bigtex_skus` table

```sql
CREATE TABLE bigtex_skus (
  item_number TEXT PRIMARY KEY,
  mcat TEXT,
  tier INTEGER,
  model TEXT,
  gvwr INTEGER,
  floor_type TEXT,
  bed_length REAL,
  width REAL,
  tongue REAL,
  stack_height REAL,
  total_footprint REAL,  -- computed: bed_length + tongue
  updated_at DATETIME
);
```

### 5.7 `bt_stack_configs` table

```sql
CREATE TABLE bt_stack_configs (
  config_id TEXT PRIMARY KEY,  -- 'utility_3stack', 'utility_2stack', 'dump_3stack', 'gooseneck'
  label TEXT,
  stack_position TEXT,  -- 'stack_1', 'stack_2', 'stack_3', 'combined_1_2'
  max_length_ft REAL,
  max_height_ft REAL,
  notes TEXT,
  updated_at DATETIME
);
```

### 5.8 `load_sessions` table

```sql
CREATE TABLE load_sessions (
  session_id TEXT PRIMARY KEY,
  brand TEXT,  -- 'pj' | 'bigtex'
  carrier_type TEXT,
  status TEXT DEFAULT 'draft',  -- 'draft' | 'review' | 'approved'
  planner_name TEXT,
  session_label TEXT,
  created_at DATETIME,
  updated_at DATETIME,
  approved_by TEXT,
  approved_at DATETIME,
  notes TEXT
);
```

### 5.9 `load_positions` table

```sql
CREATE TABLE load_positions (
  position_id TEXT PRIMARY KEY,
  session_id TEXT,
  brand TEXT,
  item_number TEXT,
  deck_zone TEXT,   -- PJ: 'lower_deck' | 'upper_deck'; BT: 'stack_1' | 'stack_2' | 'stack_3'
  layer INTEGER,    -- 1=bottom of stack, 2=next up, etc.
  sequence INTEGER, -- order within zone (front-to-back)
  is_nested BOOLEAN DEFAULT 0,
  nested_inside TEXT,  -- position_id of container unit
  gn_axle_dropped BOOLEAN DEFAULT 0,
  override_reason TEXT,
  added_at DATETIME
);
```

### 5.10 `load_patterns` table

```sql
CREATE TABLE load_patterns (
  pattern_id TEXT PRIMARY KEY,
  brand TEXT,
  pattern_name TEXT,
  load_type TEXT,
  carrier_type TEXT,
  source TEXT,  -- 'drawing' | 'manual_entry' | 'historical'
  confidence INTEGER DEFAULT 3,  -- 1-5
  positions_json TEXT,  -- JSON array of position objects
  unit_count INTEGER,
  notes TEXT,
  created_at DATETIME
);
```

---

## 6. Constraint Engine — What to Build for Prototype

### 6.1 Design principle

The engine is a **stateless function**: given a complete load state (all positions for a session), return a list of violations. It does not mutate state. It is called after every add/remove/edit in the UI.

```python
def check_load(session_id, brand) -> list[Violation]:
    positions = db.get_positions(session_id)
    carrier = db.get_carrier_config(brand)
    if brand == 'pj':
        return pj_rules.check(positions, carrier)
    elif brand == 'bigtex':
        return bt_rules.check(positions, carrier)

@dataclass
class Violation:
    severity: str       # 'error' | 'warning' | 'info'
    rule_code: str      # e.g., 'PJ_TOTAL_LENGTH'
    message: str        # Human-readable; shown in UI
    position_ids: list  # Which positions are implicated
    suggested_fix: str  # Optional resolution hint
```

### 6.2 PJ rules to implement (prototype set)

**PJ_TOTAL_LENGTH** — `error`
- Sum of all unit footprints ≤ 53'
- GN-into-dump: count nested GN as (gn_footprint − 7') + dump_footprint
- GN spanning zones: count full footprint against total 53', not per-zone

**PJ_HEIGHT_LOWER** — `error`
- For each position column on lower deck: sum unit heights ≤ 10.0' (clearance above lower deck surface)
- Use `height_mid_ft` for all but the top unit; use `height_top_ft` for the top unit

**PJ_HEIGHT_UPPER** — `error`
- For each position column on upper deck: sum unit heights ≤ 8.5' (clearance above upper deck surface)

**PJ_GN_LOWER_DECK** — `warning`
- If GN bed_length_measured > 32': flag that this unit will need to span the step
- Not an error — planner acknowledges and proceeds

**PJ_DTJ_OFFSET** — `info`
- If a DTJ unit is placed, remind planner that measured length includes the +1' cylinder offset

**PJ_D5_NESTING** — `error`
- If a D5 is not nested inside a valid host dump (DL, DV, DX, D7, DM), flag as invalid placement
- If nested correctly, remove D5 footprint from length calculation

### 6.3 Big Tex rules to implement (prototype set)

**BT_TOTAL_LENGTH** — `error`
- Max footprint of any unit in each stack + max footprint of longest stack unit (for combined length check)
- 3-stack utility: Stack 1 + Stack 2 longest ≤ 40'; Stack 3 ≤ 15.5'
- Dump: Stack 1 ≤ 21', Stack 2 ≤ 16', Stack 3 ≤ 16'

**BT_HEIGHT** — `error`
- Cumulative stack_height sum per position ≤ position height cap
- Uses BT stack_height (single value, not layer-dependent like PJ)

**BT_DUMP_HYDRAULIC** — `warning`
- Units with hydraulic jacks: Stack 2 only, flag to move to Stack 1 or Stack 3

**BT_GN_SEQUENCE** — `warning`
- If all tandem dual: bottom unit must be ≤ 20+5 overall; second ≥ 33'
- OA model cannot be bottom unit

For prototype: implement these core rules cleanly. **Leave stubs for edge cases** (dump stuffing width checks, all GN wheel-type sequence permutations, etc.). Stubs return an `info`-level violation saying "Rule not yet implemented — verify manually."

---

## 7. Computed Fields — Where Shawn's Rules Live in Code

All of this lives in `pj_measurement.py` and is called at **seed time** to populate `bed_length_measured` and `total_footprint`. It also runs whenever a SKU override is saved in Settings.

```python
def compute_measured_length(model, bed_length_stated, pj_category, offsets):
    """
    offsets: dict from pj_measurement_offsets table (editable in Settings)
    """
    if pj_category in ('car_hauler', 'deck_over', 'car_hauler_deckover', 'tilt_deckover'):
        return bed_length_stated + offsets['car_hauler_spare_mount_offset']  # default 1.0

    elif pj_category in ('dump_lowside', 'dump_highside_3ft', 'dump_highside_4ft',
                         'dump_small', 'dump_gn', 'dump_variants'):
        base = bed_length_stated + offsets['dump_tarp_kit_offset']  # default 1.0
        if model.startswith('DTJ'):
            base += offsets['dtj_cylinder_extra_offset']  # default 1.0
        # DT1: no extra (cylinder behind tarp kit)
        return base

    else:
        # GNs, utilities, tilts: stated length is measured length
        return bed_length_stated


def compute_total_footprint(bed_length_measured, tongue_feet):
    return bed_length_measured + tongue_feet
```

When an offset is changed in Settings → re-compute `bed_length_measured` and `total_footprint` for all affected SKUs → trigger re-validation of any open sessions.

---

## 8. Load Canvas — UI Requirements for Prototype

The canvas doesn't need to be pixel-perfect. It needs to be correct and readable enough for a load planner to verify it matches their mental model.

### 8.1 PJ Canvas (step deck)

```
[ LOWER DECK — 41' ]                              [ UPPER DECK — 12' ]
┌──────────────────────────────────────────┐   ┌────────────┐
│  [LDQ32]  │  [CC20]  │  [CC18]  │       │   │  [T8-26]   │
│  34' vis  │  21'+4'  │  19'+4'  │       │   │  26'+6'    │
│  ht: 2.5' │  ht:1.5' │  ht:1.5' │       │   │  ht: 2.5'  │
└──────────────────────────────────────────┘   └────────────┘
  Used: 34+25+23 = —'  ← live counter          Used: 32' / 12'
  Height: OK ✓                                  Height: OK ✓
  Total deck used: —' / 53' 
```

Key behaviors:
- Units displayed as labeled cards in their zone column
- Each card shows: item number, total footprint, stack height at current layer
- Running counter at bottom of each zone: feet used / zone cap
- Running total across top: total deck feet used / 53'
- Height counter per column: cumulative height / zone clearance
- Color: green → yellow (within 10% of limit) → red (over limit)
- Nested units shown as indented card inside host card
- GN axle-drop toggle as a small checkbox on GN cards

### 8.2 Big Tex Canvas (3-stack flatbed)

```
[ STACK 1 — REAR ]   [ STACK 2 — MIDDLE ]   [ STACK 3 — FRONT ]
┌────────────────┐   ┌────────────────┐   ┌─────────────┐
│  [14GN-25]     │   │  [14GN-22]     │   │  [35SA-14]  │
│  25+9=34' fp   │   │  22+9=31' fp   │   │  14+3=17'   │
│  ht: 2.5'      │   │  ht: 2.5'      │   │  ht: 1.25'  │
├────────────────┤   ├────────────────┤   ├─────────────┤
│  [14GN-20]     │   │  [14GN-20]     │   │             │
│  20+9=29' fp   │   │  20+9=29' fp   │   │             │
│  ht: 2.5'      │   │  ht: 2.5'      │   │             │
└────────────────┘   └────────────────┘   └─────────────┘
  Len: 34'             Len: 31'             Len: 17'
  Ht: 5.0'/5.25' ✓    Ht: 5.0'/5.25' ✓   Ht: 1.25'/4.0' ✓
  S1+S2: 34+31=65' — EXCEEDS 40' ← ERROR shown in violation panel
```

---

## 9. Settings Page → Schematic Wiring

Every editable value in Settings must be **live-wired** to the constraint engine and schematic display:

1. User edits a value in Settings (e.g., changes `lower_deck_length_ft` from 41 to 40)
2. DB row updates immediately (no page reload required for the edit itself)
3. Any currently open load session is flagged as "assumptions changed — re-validate"
4. On next view of the load canvas, constraint engine re-runs with new values
5. Schematic display (zone labels, counters, cap indicators) reads live from DB — not hardcoded

This is the critical behavior that makes the Settings page useful beyond just a reference document. Planners and the team can tune assumptions and immediately see the impact.

---

## 10. Build Sequence — Prototype

**Sprint 1 — Foundation and data**
1. Clone COT repo → strip COT-specific logic → stand up blank app with brand toggle
2. DB schema: create all tables (Section 5)
3. Seed Big Tex SKU master from Stacking Guide Master — `bigtex_skus` (716 rows, clean data)
4. Seed PJ SKU master from Product Guide — `pj_skus` (apply `pj_measurement.py` to compute measured lengths at seed time)
5. Seed carrier configs, PJ tongue groups, PJ height reference, PJ measurement offsets, BT stack configs
6. Settings page: render all editable tables from DB; wire save → DB → no reload

**Sprint 2 — Constraint engine**
7. `pj_rules.py`: implement PJ prototype rule set (Section 6.2)
8. `bt_rules.py`: implement BT prototype rule set (Section 6.3)
9. `load_constraint_checker.py`: dispatcher
10. `pj_measurement.py`: measurement conventions + re-compute trigger on settings save

**Sprint 3 — Load canvas and workflow**
11. Session start screen (brand select, planner name)
12. SKU selection panel (search/filter by model, category, bed length)
13. PJ load canvas: step deck layout with live constraint feedback
14. Big Tex load canvas: 3-stack layout with live constraint feedback
15. Violation panel: grouped by severity; warnings with override acknowledgment

**Sprint 4 — Schematic and patterns**
16. Export/print schematic (PJ top-down style; BT 3-stack style)
17. Seed pattern library from Shawn's drawings and email examples (Section 8 of v0.2)
18. Pattern suggestion sidebar

---

## 11. What the Team Will React To

When the prototype is in front of planners, the key things to watch for:

- **Are the SKU cards showing the right dimensions?** If `bed_length_measured` looks wrong to Shawn, that means a measurement offset is wrong → fix it in Settings, watch it update
- **Does the height math feel right?** The mid/top height split is the most opaque rule — can planners validate it by building a known load and checking that the tool agrees?
- **Are the pattern suggestions helpful?** The pattern library is seeded from drawings; planners will quickly tell us if there are common combinations we're missing
- **What's still in planners' heads that isn't in the tool?** The Settings SKU override table (`pairing_rule`, `dump_side_height_ft`) will have gaps — prototype surfaces those gaps explicitly rather than hiding them

---

## 12. Explicit Non-Goals (Phase I Prototype)

- Live inventory or ERP integration
- Order upload workflow (planner picks SKUs manually from master list)
- Freight cost or routing optimization
- All Big Tex dump stuffing width rules (stub with info-level violation)
- All GN wheel-type sequence permutations (stub with info-level violation)
- LDG/LDW left-right pairing enforcement (Settings field exists, rule not yet enforced)
- Dump side height constraint enforcement (Settings field exists, rule not yet enforced)
- Multi-plant context
- ML suggestions
