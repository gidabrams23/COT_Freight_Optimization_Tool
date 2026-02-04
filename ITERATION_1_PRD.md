# Iteration 1 PRD: Core Optimization Engine + Draft Load Management

## Executive Summary

**Objective**: Replace manual load batching with an algorithmic optimization engine that considers geography, time windows, stacking rules, and detour costs. All optimization happens in "draft" mode so planners can review, compare, and trust results before committing.

**Success Metrics**:
- System generates load plans with 10-15% higher utilization than current manual approach
- Planners can see side-by-side comparison of manual vs optimized plans
- Load building time reduced from hours to minutes
- All stacking and geographic rules codified (no tribal knowledge required)

---

## User Stories

### Primary Persona: Load Planner (Amanda/Russell)

**Story 1**: As a load planner, I want to import orders with full ship-to and product details so the system has everything needed to optimize loads.

**Story 2**: As a load planner, I want to exclude certain orders (parts, salvage, special requests) from optimization so they don't get batched incorrectly.

**Story 3**: As a load planner, I want the system to propose optimized loads based on geography, due dates, and stacking rules so I can quickly see if consolidation opportunities exist.

**Story 4**: As a load planner, I want to see a comparison of my manual plan vs the optimized plan with clear metrics (# loads, utilization %, total miles, cost) so I can decide whether to accept the optimization.

**Story 5**: As a load planner, I want all newly built loads to be in "DRAFT" status so I can review and adjust before committing them to production.

---

## Data Model Changes

### 1. Enhanced `order_lines` Table

**New columns**:
```sql
ALTER TABLE order_lines ADD COLUMN ship_to_zip TEXT NOT NULL DEFAULT '';
ALTER TABLE order_lines ADD COLUMN trailer_category TEXT DEFAULT 'STANDARD';
ALTER TABLE order_lines ADD COLUMN is_excluded INTEGER DEFAULT 0;
ALTER TABLE order_lines ADD COLUMN origin_plant TEXT DEFAULT '';
```

**Field definitions**:
- `ship_to_zip`: 5-digit ZIP code for delivery location
- `trailer_category`: SKU category for stacking rules (e.g., 'STANDARD', 'TALL', 'WIDE', 'MIXED')
- `is_excluded`: Boolean flag (0=include in optimization, 1=exclude)
- `origin_plant`: Plant code (e.g., 'GA', 'TX', 'VA', 'IA', 'OR', 'NV')

### 2. Enhanced `loads` Table

**New columns**:
```sql
ALTER TABLE loads ADD COLUMN status TEXT DEFAULT 'DRAFT';
ALTER TABLE loads ADD COLUMN utilization_pct REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN total_miles REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN detour_miles REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN optimization_score REAL DEFAULT 0.0;
```

**Field definitions**:
- `status`: Enum-like field ('DRAFT', 'PLANNED', 'DISPATCHED')
- `utilization_pct`: Calculated dimensional utilization (0-100)
- `total_miles`: Total route miles for multi-stop loads
- `detour_miles`: Additional miles beyond direct routing
- `optimization_score`: Composite score for ranking load quality

### 3. New `stacking_rules` Reference Table

```sql
CREATE TABLE stacking_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trailer_category TEXT NOT NULL,
    max_stack_height INTEGER DEFAULT 1,
    feet_per_unit REAL DEFAULT 0.0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Purpose**: Codifies Master Load Building Cheat Sheet rules. Planners can reference this table to validate stacking logic.

**Sample data** (from your Cheat Sheet):
```
trailer_category: 'STANDARD', max_stack_height: 2, feet_per_unit: 4.0
trailer_category: 'TALL', max_stack_height: 1, feet_per_unit: 5.5
trailer_category: 'WIDE', max_stack_height: 1, feet_per_unit: 6.0
```

---

## Algorithm Specification

### Enhanced Load Builder Logic

**Input parameters** (form on `/loads` page):
- Origin plant (dropdown)
- Trailer capacity in linear feet (default: 53 ft)
- Max detour percentage (default: 15%)
- Time window for consolidation in days (default: 7 days)
- Geographic radius for clustering in miles (default: 100 miles)

### Step-by-Step Algorithm

#### **Step 1: Filter & Prepare Orders**
```python
eligible_orders = SELECT * FROM order_lines 
                  WHERE is_excluded = 0 
                  AND origin_plant = [input_plant]
                  ORDER BY due_date ASC, created_at ASC
```

#### **Step 2: Geographic Clustering**
For each order, calculate Haversine distance to every other order:

```python
def haversine_distance(zip1, zip2):
    """
    Returns distance in miles between two ZIP codes
    Uses lat/lon lookup table or geocoding API
    """
    # Implementation uses standard Haversine formula
    # Return distance in miles
```

Group orders into clusters where all orders are within `geographic_radius` miles of cluster centroid.

#### **Step 3: Time Window Filtering**
Within each geographic cluster, only consolidate orders where:
```python
max(due_date) - min(due_date) <= time_window_days
```

#### **Step 4: Stacking Compatibility Check**
For each potential load, verify all order lines have compatible `trailer_category`:
```python
def check_stacking_compatibility(order_lines):
    """
    Returns True if all orders can stack together
    Uses stacking_rules table
    """
    categories = [order.trailer_category for order in order_lines]
    
    # Rule 1: All must be same category, OR
    # Rule 2: Mix of 'STANDARD' and 'TALL' allowed if total height <= capacity
    # Rule 3: 'WIDE' cannot mix with anything
    
    if 'WIDE' in categories and len(set(categories)) > 1:
        return False
    
    # Additional stacking logic from Master Cheat Sheet
    return True
```

#### **Step 5: Greedy Packing with Dimensional Calculations**
For each cluster:
```python
def build_load(cluster_orders, capacity_feet):
    load = []
    current_feet = 0.0
    
    for order in sorted(cluster_orders, key=lambda x: x.due_date):
        # Calculate order linear feet
        order_feet = order.qty * order.feet_per_unit
        
        # Check if fits AND stacking compatible
        if (current_feet + order_feet <= capacity_feet and 
            check_stacking_compatibility(load + [order])):
            load.append(order)
            current_feet += order_feet
        else:
            # Start new load
            if load:
                yield load
            load = [order]
            current_feet = order_feet
    
    if load:
        yield load
```

#### **Step 6: Detour Cost Calculation**
For multi-stop loads, calculate routing sequence:
```python
def calculate_detour_cost(load, origin_plant):
    """
    Returns (total_miles, detour_miles)
    Detour = actual route miles - direct miles to farthest point
    """
    # Simple nearest-neighbor routing
    destinations = [order.ship_to_zip for order in load]
    
    # Calculate optimal sequence (nearest neighbor TSP approximation)
    route = nearest_neighbor_route(origin_plant, destinations)
    
    total_miles = sum(distance between consecutive stops)
    direct_miles = distance(origin_plant, farthest_destination)
    detour_miles = total_miles - direct_miles
    
    return total_miles, detour_miles
```

Flag loads where `detour_miles / direct_miles > max_detour_pct`

#### **Step 7: Calculate Utilization & Optimization Score**
```python
def calculate_metrics(load, capacity_feet, total_miles):
    total_feet = sum(order.qty * order.feet_per_unit for order in load)
    utilization_pct = (total_feet / capacity_feet) * 100
    
    # Optimization score (higher = better)
    # Weights: 60% utilization, 30% consolidation benefit, 10% route efficiency
    consolidation_score = len(load) * 10  # More orders = better consolidation
    route_efficiency = 100 - (detour_miles / total_miles * 100)
    
    optimization_score = (
        utilization_pct * 0.6 + 
        min(consolidation_score, 50) * 0.3 + 
        route_efficiency * 0.1
    )
    
    return utilization_pct, optimization_score
```

---

## UI/UX Changes

### Orders Page (`/orders`)

**New form fields** (when adding order):
```
Customer: [dropdown - existing]
Ship-To ZIP: [text input, 5 digits, required]
Origin Plant: [dropdown: GA, TX, VA, IA, OR, NV]
Trailer Category: [dropdown: Standard, Tall, Wide, Mixed]
Qty: [number input]
Feet per Unit: [number input, default from category]
Due Date: [date picker]
Exclude from Optimization: [checkbox]
Notes: [text area]
```

**Order list table** - add columns:
- Ship-To ZIP
- Origin Plant
- Category
- Excluded (show ⚠️ icon if checked)

### Loads Page (`/loads`)

**Section 1: Build Parameters Form**
```
┌─────────────────────────────────────────┐
│ Load Build Parameters                   │
├─────────────────────────────────────────┤
│ Origin Plant: [dropdown]                │
│ Trailer Capacity (ft): [53]             │
│ Max Detour %: [15]                      │
│ Time Window (days): [7]                 │
│ Geographic Radius (miles): [100]        │
│                                         │
│ [Build Optimized Loads] [Clear Drafts] │
└─────────────────────────────────────────┘
```

**Section 2: Optimization Summary** (appears after build)
```
┌─────────────────────────────────────────────────────────────┐
│ Optimization Results                                        │
├─────────────────────────────────────────────────────────────┤
│                  Manual (Baseline)    Optimized    Δ        │
│ Total Loads:           12                8        -4 (-33%) │
│ Avg Utilization:      67%              82%       +15 pts    │
│ Total Miles:        8,450            7,200       -1,250     │
│ Est. Cost:         $6,760            $5,760       -$1,000   │
│                                                              │
│ [Accept All Loads] [Review Load Details]                    │
└─────────────────────────────────────────────────────────────┘
```

**Section 3: Load Plan Table** (existing, enhanced)

Add columns:
- **Status** badge (DRAFT in yellow)
- **Utilization %** (color-coded: <70% red, 70-85% yellow, >85% green)
- **Total Miles**
- **Detour Miles** (show ⚠️ if exceeds threshold)
- **Optimization Score**

Sort by optimization_score DESC by default.

**Row expansion** shows order lines with:
- Customer name
- Ship-To ZIP
- Qty × Feet/Unit = Total Feet
- Due Date
- Delivery sequence number (1st, 2nd, 3rd stop)

---

## Backend Implementation Guide

### New Service: `services/optimization_engine.py`

```python
class OptimizationEngine:
    def __init__(self, db_connection):
        self.db = db_connection
        self.zip_coords = self.load_zip_coordinates()
    
    def build_optimized_loads(self, params):
        """
        Main entry point for load optimization
        
        Args:
            params: dict with origin_plant, capacity_feet, 
                    max_detour_pct, time_window_days, geo_radius
        
        Returns:
            list of Load objects with associated order lines
        """
        # Step 1: Get eligible orders
        orders = self.get_eligible_orders(params['origin_plant'])
        
        # Step 2: Geographic clustering
        clusters = self.cluster_by_geography(orders, params['geo_radius'])
        
        # Step 3: Time window filtering
        clusters = self.filter_by_time_window(clusters, params['time_window_days'])
        
        # Step 4: Build loads within each cluster
        loads = []
        for cluster in clusters:
            cluster_loads = self.greedy_pack(cluster, params['capacity_feet'])
            loads.extend(cluster_loads)
        
        # Step 5: Calculate metrics for each load
        for load in loads:
            load.calculate_metrics(params)
        
        return loads
    
    def cluster_by_geography(self, orders, radius_miles):
        """Uses Haversine distance + DBSCAN-like clustering"""
        # Implementation here
        pass
    
    def greedy_pack(self, orders, capacity):
        """Packs orders into loads respecting capacity and stacking rules"""
        # Implementation here
        pass
    
    # ... additional helper methods
```

### Updated Service: `services/load_builder.py`

**Keep existing simple load builder for backward compatibility.**

Add new method:
```python
def build_loads_optimized(origin, params):
    """
    New optimized load builder
    Calls OptimizationEngine and persists results to database
    """
    engine = OptimizationEngine(db.get_connection())
    optimized_loads = engine.build_optimized_loads(params)
    
    # Persist to database with status='DRAFT'
    for load in optimized_loads:
        load_id = db.create_load(
            origin=origin,
            status='DRAFT',
            capacity_feet=params['capacity_feet'],
            total_feet=load.total_feet,
            utilization_pct=load.utilization_pct,
            total_miles=load.total_miles,
            detour_miles=load.detour_miles,
            optimization_score=load.optimization_score
        )
        
        # Create load_lines
        for order_line in load.order_lines:
            db.create_load_line(load_id, order_line.id, order_line.total_feet)
    
    return optimized_loads
```

### New Helper: `services/geo_utils.py`

```python
import math

def haversine_distance(zip1, zip2, zip_coords_dict):
    """
    Calculate distance in miles between two ZIP codes
    
    Args:
        zip1, zip2: 5-digit ZIP code strings
        zip_coords_dict: dict mapping ZIP -> (lat, lon)
    
    Returns:
        float: distance in miles
    """
    if zip1 not in zip_coords_dict or zip2 not in zip_coords_dict:
        return float('inf')  # Unknown ZIPs = infinite distance
    
    lat1, lon1 = zip_coords_dict[zip1]
    lat2, lon2 = zip_coords_dict[zip2]
    
    # Haversine formula
    R = 3959  # Earth radius in miles
    
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = (math.sin(dlat/2)**2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dlon/2)**2)
    
    c = 2 * math.asin(math.sqrt(a))
    distance = R * c
    
    return distance

def load_zip_coordinates():
    """
    Load ZIP code -> (lat, lon) mapping
    For MVP: Can use a static JSON file with ~40k US ZIPs
    Future: Integrate with geocoding API
    """
    # Returns dict: {'30301': (33.7490, -84.3880), ...}
    pass
```

**Note**: You'll need a ZIP code coordinates dataset. I can provide a Python script to generate this from public data sources.

---

## Acceptance Criteria

### Feature 1: Enhanced Order Entry
- [ ] Order form includes all new fields (ship-to ZIP, origin plant, trailer category, exclude flag)
- [ ] Form validation: ZIP must be 5 digits, origin plant required
- [ ] Order list table displays new columns
- [ ] Excluded orders show visual indicator (⚠️ icon)

### Feature 2: Stacking Rules Reference
- [ ] `stacking_rules` table created with sample data from Master Cheat Sheet
- [ ] Load builder validates stacking compatibility before adding order to load
- [ ] Loads with stacking conflicts are rejected or split

### Feature 3: Geographic Clustering
- [ ] System calculates Haversine distance between all order pairs
- [ ] Orders are grouped into geographic clusters within specified radius
- [ ] Cluster assignments are visible in load details (implicit through load grouping)

### Feature 4: Optimized Load Builder
- [ ] Build form accepts all new parameters (detour %, time window, geo radius)
- [ ] Algorithm generates loads respecting capacity, stacking, and time constraints
- [ ] Loads under 70% utilization are flagged with warning color
- [ ] Detour miles calculated and displayed for multi-stop loads
- [ ] Loads exceeding max detour % are flagged

### Feature 5: Draft Status & Comparison View
- [ ] All newly built loads have status='DRAFT'
- [ ] Optimization summary shows before/after metrics
- [ ] Summary includes: # loads, avg utilization, total miles, estimated cost
- [ ] "Clear Drafts" button only removes DRAFT loads, leaves others intact

### Feature 6: Utilization Calculation
- [ ] Utilization % calculated as: (total_feet / capacity_feet) * 100
- [ ] Optimization score calculated using weighted formula
- [ ] Load table sorted by optimization_score DESC by default
- [ ] Utilization % color-coded in UI (<70% red, 70-85% yellow, >85% green)

---

## Technical Dependencies

### Data Files Needed
1. **ZIP Code Coordinates**: JSON file mapping ZIP → (lat, lon)
   - Source: Free from simplemaps.com or US Census Bureau
   - Size: ~40k US ZIPs, ~2-3 MB
   - Location: `static/data/zip_coords.json`

2. **Stacking Rules**: Seed data from Master Load Building Cheat Sheet
   - Can be SQL insert script run during migration
   - Future: Make this editable via admin UI

### Python Libraries (add to requirements.txt)
```
Flask==2.3.3
geopy==2.4.0  # For geocoding if needed
numpy==1.24.0  # For distance matrix calculations
```

### Database Migrations
Create migration script `migrations/001_iteration1_schema.sql`:
```sql
-- Add new columns to order_lines
ALTER TABLE order_lines ADD COLUMN ship_to_zip TEXT NOT NULL DEFAULT '';
ALTER TABLE order_lines ADD COLUMN trailer_category TEXT DEFAULT 'STANDARD';
ALTER TABLE order_lines ADD COLUMN is_excluded INTEGER DEFAULT 0;
ALTER TABLE order_lines ADD COLUMN origin_plant TEXT DEFAULT '';

-- Add new columns to loads
ALTER TABLE loads ADD COLUMN status TEXT DEFAULT 'DRAFT';
ALTER TABLE loads ADD COLUMN utilization_pct REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN total_miles REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN detour_miles REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN optimization_score REAL DEFAULT 0.0;

-- Create stacking_rules table
CREATE TABLE stacking_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trailer_category TEXT NOT NULL,
    max_stack_height INTEGER DEFAULT 1,
    feet_per_unit REAL DEFAULT 0.0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed stacking rules (sample data)
INSERT INTO stacking_rules (trailer_category, max_stack_height, feet_per_unit, notes)
VALUES 
    ('STANDARD', 2, 4.0, 'Standard dry van trailers, 2-high stack'),
    ('TALL', 1, 5.5, 'Tall trailers, no stacking'),
    ('WIDE', 1, 6.0, 'Wide loads, no mixing'),
    ('MIXED', 1, 4.5, 'Mixed SKUs, conservative stacking');
```

---

## Out of Scope (Save for Iteration 2)

- Editing existing order lines or loads (create/delete only for now)
- Manual load adjustments (moving orders between loads)
- Load status transitions beyond DRAFT
- Carrier assignment
- Multi-user permissions
- Order import from CSV/ERP
- Detailed audit trail

---

## Testing Checklist

### Unit Tests
- [ ] `haversine_distance()` returns correct values for known ZIP pairs
- [ ] `check_stacking_compatibility()` correctly rejects incompatible mixes
- [ ] `greedy_pack()` respects capacity constraints
- [ ] `calculate_detour_cost()` correctly sequences multi-stop routes
- [ ] `calculate_metrics()` returns utilization % and score within expected ranges

### Integration Tests
- [ ] End-to-end: Add orders → Build loads → View summary → Check database
- [ ] Edge case: All orders excluded → No loads built
- [ ] Edge case: Single order exceeds capacity → Error message shown
- [ ] Edge case: Orders too far apart → Separate loads created
- [ ] Edge case: Orders too far in time → Not consolidated

### Manual QA Scenarios
1. **Scenario: Perfect consolidation**
   - Add 6 orders, same ZIP, same due date, total 48 ft
   - Build with 53 ft capacity → Expect 1 load, 90% utilization

2. **Scenario: Geographic split**
   - Add 3 orders in Georgia, 3 orders in Texas
   - Build with 100-mile radius → Expect 2 loads minimum

3. **Scenario: Time window violation**
   - Add 2 orders, same ZIP, 10 days apart
   - Build with 7-day window → Expect 2 loads

4. **Scenario: Stacking incompatibility**
   - Add 2 orders, same ZIP, one STANDARD + one WIDE
   - Build → Expect 2 loads (cannot mix)

5. **Scenario: Detour cost excessive**
   - Add 3 orders forming triangle with 50% detour
   - Build with 15% max detour → Expect warning flag

---

## Success Criteria for Iteration 1 Completion

1. **Functional completeness**: All acceptance criteria met
2. **Performance**: Load building completes in <5 seconds for 100 orders
3. **Accuracy**: Optimization matches or exceeds manual plan utilization by 10%+
4. **Usability**: Planner can build and review loads without documentation/training
5. **Data integrity**: No orphaned records, all constraints enforced

---

## Handoff Notes for Codex

**Priority sequence**:
1. Start with database migrations (schema changes + seed data)
2. Build geo_utils.py and test Haversine calculations
3. Implement OptimizationEngine core logic
4. Update UI forms and tables
5. Wire up routes and service calls
6. Add validation and error handling
7. Test with realistic sample data

**Key files to modify**:
- `db.py` → Add new columns and stacking_rules table
- `services/optimization_engine.py` → NEW FILE (core algorithm)
- `services/geo_utils.py` → NEW FILE (distance calculations)
- `services/load_builder.py` → Add optimized build method
- `app.py` → Update `/orders` and `/loads` routes
- `templates/orders.html` → Enhanced order form
- `templates/loads.html` → New build form + comparison summary
- `static/styles.css` → Add color-coding for utilization tiers

**Most complex component**: Geographic clustering + route optimization. Consider using nearest-neighbor TSP approximation initially (not full optimization). Can enhance in Iteration 2 if needed.

---

## Additional Context: Real-World Constraints

### From Current Operations (Carry-On / ATW)

**Load planning today is:**
- Manual, experience-driven
- Heavy on tribal knowledge
- No formal pre-build or simulation step
- Batching decisions are iterative and gut-check based

**Key constraints:**
- Not all trailers stack together (height/width/damage risk)
- Some products require specific stacking patterns or dunnage
- Customer delivery windows can override optimal consolidation
- Large backtracks are avoided, but no explicit detour threshold exists
- Orders change frequently after initial planning (qty changes, date shifts, cancellations)

**This tool's role:**
- **Not replacing dispatch judgment** — codifying it
- Making manual process faster, more consistent, and economically informed
- Preserving planners' ability to override and handle exceptions
- Primary goal: **Better load consolidation = freight savings**

### Design Philosophy

1. **Trust through transparency**: Show the math, show the comparison, let planners decide
2. **Draft-first workflow**: Nothing is committed until planner reviews
3. **Graceful degradation**: If optimization can't find perfect solution, show best effort
4. **Operational realism**: Algorithm must respect real-world constraints (stacking, timing, geography)

---

## Questions for Clarification During Development

If any of these are unclear during implementation, flag them:

1. **ZIP coordinate data**: Should we bundle a static JSON file, or require external geocoding API?
2. **Baseline comparison**: How do we establish "manual baseline" for comparison? Use existing loads table?
3. **Detour calculation**: Should we use straight-line Haversine or actual road routing (Google Maps API)?
4. **Stacking rules**: Are the 4 sample categories sufficient, or do we need more granular SKU-level rules?
5. **Time window**: Should consolidation window be based on due_date or expected ship_date?
6. **Plant locations**: Do we need a plants reference table with coordinates, or just use plant codes?

---

## Future Enhancements (Not in Scope, But Good to Know)

- Machine learning to predict actual utilization vs planned
- Historical load performance tracking (on-time %, actual vs planned utilization)
- Multi-plant consolidated routing (cross-origin optimization)
- Dynamic carrier rate negotiation based on load characteristics
- Real-time ERP integration for live order updates
- Mobile dispatch app for drivers with delivery sequence

---

**END OF PRD**
