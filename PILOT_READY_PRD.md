# Pilot-Ready PRD: Real-World Data Integration & Production Workflow

## Executive Summary

**Objective**: Transform the app from a prototype to a pilot-ready tool that Amanda and the dispatch team can use with actual open orders data. The app will automatically parse uploaded order reports, calculate accurate utilizations using the Master Load Building Cheat Sheet, apply freight rates from the rate matrix, and provide an intuitive workflow for load optimization.

**Success Metrics**:
- Amanda can upload weekly open orders CSV and immediately see all orders with calculated utilizations
- System accurately maps 95%+ of order Items to SKUs using the lookup tables
- Utilization calculations match manual spreadsheet calculations within 1-2%
- Load optimization workflow is intuitive and requires minimal training
- Visual design is professional, cohesive, and matches brand color scheme

---

## Key Design Philosophy

**"Upload → Review → Optimize → Execute"**
- Start with the data they already have (Amanda's open orders report)
- Make the system smart enough to understand their SKU nomenclature
- Calculate utilizations the same way they do manually
- Let planners review and validate before committing

---

## Data Model Changes

### 1. New `sku_specifications` Table (replaces `stacking_rules`)

Maps SKU identifiers to physical specifications from Master Load Building Cheat Sheet.

```sql
CREATE TABLE sku_specifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    length_with_tongue_ft REAL NOT NULL,
    max_stack_step_deck INTEGER DEFAULT 1,
    max_stack_flat_bed INTEGER DEFAULT 1,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Sample data** (from Cheat Sheet):
```sql
INSERT INTO sku_specifications (sku, category, length_with_tongue_ft, max_stack_step_deck, max_stack_flat_bed, notes)
VALUES
    ('3.5X5LSHS', 'SPECIAL UTILITY', 12, 3, 2, '5X8DUMPLP5K'),
    ('4X6G', 'USA', 12, 5, 4, 'Standard utility trailer'),
    ('4X6GW2K', 'USA', 12, 5, 4, 'GW 2K series'),
    ('4X6T', 'USA', 11, 5, 4, 'Tilt bed'),
    ('4X7G', 'USA', 13, 5, 4, '4x7 utility'),
    ('4X8G', 'USA', 14, 5, 4, 'Standard 4x8'),
    ('4X8GW2K', 'USA', 14, 5, 4, '4x8 GW 2K'),
    ('4X8WOODY', 'USA', 14, 3, 3, 'Wooden floor'),
    ('CARGO | 6 ft', 'CARGO', 10, 5, 4, '4X6 cargo trailers'),
    ('5.5X10AGW', 'USA-AL', 14, 5, 4, 'Aluminum utility'),
    ('5.5X10GWHDP', 'USA', 14, 5, 4, 'Heavy duty package'),
    -- ... additional SKUs from cheat sheet
;
```

### 2. New `item_sku_lookup` Table

Maps Plant + BIN (or Item codes) to standardized SKU identifiers.

```sql
CREATE TABLE item_sku_lookup (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plant TEXT NOT NULL,
    bin TEXT NOT NULL,
    item_pattern TEXT,
    sku TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sku) REFERENCES sku_specifications(sku)
);

CREATE INDEX idx_item_lookup ON item_sku_lookup(plant, bin);
```

**Sample data** (from Cheat Sheet Lookups):
```sql
INSERT INTO item_sku_lookup (plant, bin, item_pattern, sku)
VALUES
    ('GA', 'CARGO', '4X6CG%', '4X6G'),
    ('GA', 'CARGO', '4X6CGVEC%', 'CARGO | 6 ft'),
    ('GA', 'CARGO', '5X10CG%', 'CARGO | 6 ft'),
    ('GA', 'USA', '4X6G%', '4X6G'),
    ('GA', 'USA', '4X8G%', '4X8G'),
    ('IA', 'USA', '5X8GW2K', '4X6GW2K'),
    -- ... additional mappings
;
```

### 3. New `rate_matrix` Table

Stores freight rates ($/mile) from each origin plant to each destination state.

```sql
CREATE TABLE rate_matrix (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_plant TEXT NOT NULL,
    destination_state TEXT NOT NULL,
    rate_per_mile REAL NOT NULL,
    effective_year INTEGER DEFAULT 2026,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(origin_plant, destination_state, effective_year)
);

CREATE INDEX idx_rate_lookup ON rate_matrix(origin_plant, destination_state);
```

**Sample data** (from Rate Matrix):
```sql
INSERT INTO rate_matrix (origin_plant, destination_state, rate_per_mile, effective_year)
VALUES
    ('GA', 'AL', 3.25, 2026),
    ('GA', 'AR', 2.57, 2026),
    ('GA', 'FL', 3.35, 2026),
    ('OR', 'CA', 3.40, 2026),
    ('TX', 'CO', 3.55, 2026),
    ('IA', 'MN', 2.20, 2026),
    ('VA', 'NY', 3.99, 2026),
    ('NV', 'AZ', 3.10, 2026),
    -- ... all plant-state combinations from rate matrix
;
```

### 4. Enhanced `order_lines` Table

Updated to match Amanda's open orders report structure.

**New/modified columns**:
```sql
ALTER TABLE order_lines DROP COLUMN feet_per_unit;
ALTER TABLE order_lines ADD COLUMN due_date TEXT NOT NULL;
ALTER TABLE order_lines ADD COLUMN plant TEXT NOT NULL;
ALTER TABLE order_lines ADD COLUMN item TEXT NOT NULL;
ALTER TABLE order_lines ADD COLUMN qty INTEGER NOT NULL;
ALTER TABLE order_lines ADD COLUMN sales REAL;
ALTER TABLE order_lines ADD COLUMN so_num TEXT;
ALTER TABLE order_lines ADD COLUMN cust_name TEXT;
ALTER TABLE order_lines ADD COLUMN cpo TEXT;
ALTER TABLE order_lines ADD COLUMN salesman TEXT;
ALTER TABLE order_lines ADD COLUMN cust_num TEXT;
ALTER TABLE order_lines ADD COLUMN bin TEXT;
ALTER TABLE order_lines ADD COLUMN load_num TEXT;
ALTER TABLE order_lines ADD COLUMN address1 TEXT;
ALTER TABLE order_lines ADD COLUMN address2 TEXT;
ALTER TABLE order_lines ADD COLUMN city TEXT;
ALTER TABLE order_lines ADD COLUMN state TEXT NOT NULL;
ALTER TABLE order_lines ADD COLUMN zip TEXT NOT NULL;

-- Calculated fields (populated by system)
ALTER TABLE order_lines ADD COLUMN sku TEXT;
ALTER TABLE order_lines ADD COLUMN unit_length_ft REAL;
ALTER TABLE order_lines ADD COLUMN total_length_ft REAL;
ALTER TABLE order_lines ADD COLUMN max_stack_height INTEGER;
ALTER TABLE order_lines ADD COLUMN stack_position INTEGER DEFAULT 1;
ALTER TABLE order_lines ADD COLUMN utilization_pct REAL;
ALTER TABLE order_lines ADD COLUMN is_excluded INTEGER DEFAULT 0;

FOREIGN KEY (sku) REFERENCES sku_specifications(sku)
```

### 5. Enhanced `loads` Table

Add fields for cost calculation and optimization metrics.

```sql
ALTER TABLE loads ADD COLUMN origin_plant TEXT NOT NULL;
ALTER TABLE loads ADD COLUMN destination_state TEXT NOT NULL;
ALTER TABLE loads ADD COLUMN estimated_miles REAL;
ALTER TABLE loads ADD COLUMN rate_per_mile REAL;
ALTER TABLE loads ADD COLUMN estimated_cost REAL;
ALTER TABLE loads ADD COLUMN status TEXT DEFAULT 'DRAFT';
ALTER TABLE loads ADD COLUMN utilization_pct REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN optimization_score REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN created_by TEXT;
```

---

## Core Workflow & Features

### Feature 1: CSV Upload & Auto-Parsing

**UI Location**: New `/upload` page (becomes the main landing page)

**Upload Form**:
```
┌────────────────────────────────────────────────┐
│ Upload Open Orders Report                     │
├────────────────────────────────────────────────┤
│                                                │
│   [Drag CSV file here or click to browse]     │
│                                                │
│   Expected format: Amanda's Open Orders CSV    │
│   (DueDate, Customer, Plant, Item, QTY, etc.)  │
│                                                │
│   [Upload & Parse]                             │
└────────────────────────────────────────────────┘
```

**Backend Processing** (service: `services/order_importer.py`):

```python
class OrderImporter:
    def __init__(self, db_connection):
        self.db = db_connection
        self.sku_lookup = self.load_sku_lookup()
        self.sku_specs = self.load_sku_specs()
    
    def parse_csv(self, file_path):
        """
        Parse Amanda's open orders CSV format
        Expected columns: DueDate, Customer, Plant, Item, QTY, Sales, 
                         SONUM, CustName, CPO, Salesman, CustNum, BIN, 
                         Plant2, Load #, Address1, Address2, City, State, ZIP
        """
        df = pd.read_csv(file_path)
        
        # Validate required columns
        required = ['DueDate', 'Customer', 'Plant', 'Item', 'QTY', 
                   'State', 'ZIP', 'BIN']
        missing = [col for col in required if col not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        
        # Parse each row
        orders = []
        unmapped_items = []
        
        for idx, row in df.iterrows():
            order = self.parse_order_line(row)
            if order:
                orders.append(order)
            else:
                unmapped_items.append({
                    'item': row['Item'],
                    'plant': row['Plant'],
                    'bin': row['BIN']
                })
        
        return {
            'orders': orders,
            'unmapped_items': unmapped_items,
            'total_rows': len(df),
            'successfully_mapped': len(orders),
            'mapping_rate': len(orders) / len(df) * 100
        }
    
    def parse_order_line(self, row):
        """
        Convert CSV row to order line with SKU mapping and calculations
        """
        # Step 1: Look up SKU using Plant + BIN + Item pattern
        sku = self.lookup_sku(row['Plant'], row['BIN'], row['Item'])
        
        if not sku:
            return None  # Cannot map this item
        
        # Step 2: Get SKU specifications
        specs = self.sku_specs.get(sku)
        if not specs:
            return None
        
        # Step 3: Calculate utilization
        unit_length = specs['length_with_tongue_ft']
        qty = int(row['QTY'])
        max_stack = specs['max_stack_flat_bed']  # Use flat bed by default
        
        # Calculate how much trailer space this order consumes
        # Assumption: Orders can stack up to max_stack_height
        effective_units = math.ceil(qty / max_stack)
        total_length = effective_units * unit_length
        
        # Utilization = total_length / 53 ft trailer capacity
        utilization_pct = (total_length / 53.0) * 100
        
        # Step 4: Build order object
        order = {
            'due_date': row['DueDate'],
            'customer': row['Customer'],
            'plant': row['Plant'],
            'item': row['Item'],
            'qty': qty,
            'sales': row.get('Sales', 0),
            'so_num': row.get('SONUM', ''),
            'cust_name': row.get('CustName', ''),
            'cpo': row.get('CPO', ''),
            'salesman': row.get('Salesman', ''),
            'cust_num': row.get('CustNum', ''),
            'bin': row.get('BIN', ''),
            'load_num': row.get('Load #', ''),
            'address1': row.get('Address1', ''),
            'address2': row.get('Address2', ''),
            'city': row.get('City', ''),
            'state': row['State'],
            'zip': row['ZIP'],
            # Calculated fields
            'sku': sku,
            'unit_length_ft': unit_length,
            'total_length_ft': total_length,
            'max_stack_height': max_stack,
            'stack_position': 1,  # Default, can be adjusted
            'utilization_pct': utilization_pct,
            'is_excluded': 0
        }
        
        return order
    
    def lookup_sku(self, plant, bin_code, item):
        """
        Look up SKU using plant, BIN, and Item pattern matching
        Uses item_sku_lookup table with LIKE pattern matching
        """
        # Try exact match first
        key = f"{plant}|{bin_code}|{item}"
        if key in self.sku_lookup:
            return self.sku_lookup[key]
        
        # Try pattern matching (e.g., 4X6CG% matches 4X6CGVEC, 4X6CGSD, etc.)
        for pattern_key, sku in self.sku_lookup.items():
            p_plant, p_bin, p_pattern = pattern_key.split('|')
            if p_plant == plant and p_bin == bin_code:
                if p_pattern.endswith('%'):
                    # Prefix match
                    prefix = p_pattern[:-1]
                    if item.startswith(prefix):
                        return sku
        
        # No match found
        return None
```

**Post-Upload Summary Page**:
```
┌─────────────────────────────────────────────────────────┐
│ Upload Summary                                          │
├─────────────────────────────────────────────────────────┤
│ ✓ Successfully imported 420 of 420 orders (100%)        │
│                                                         │
│ Orders by Plant:                                        │
│   GA: 156 orders                                        │
│   IA: 142 orders                                        │
│   TX: 78 orders                                         │
│   VA: 44 orders                                         │
│                                                         │
│ Total Trailer Capacity Required: 89 trailers (if unoptimized) │
│                                                         │
│ ⚠ Unmapped Items: 0                                     │
│                                                         │
│ [Review Orders] [Start Optimization]                   │
└─────────────────────────────────────────────────────────┘
```

If unmapped items exist:
```
⚠ Unmapped Items: 12 items could not be mapped to SKUs

[Show Details]  [Download Unmapped Items CSV]

These orders will be excluded from optimization until SKUs are added
to the lookup table.
```

### Feature 2: Order Review Page (Enhanced)

**UI Location**: `/orders` (redesigned)

**Page Layout**:

**Section 1: Filter & Summary Bar**
```
┌────────────────────────────────────────────────────────────────┐
│ Filter: [All Plants ▾] [All States ▾] [All Customers ▾]       │
│                                                                │
│ Showing: 420 orders | Total Capacity: 312 ft (5.9 trailers)   │
│ Avg Utilization: 67% per order                                │
└────────────────────────────────────────────────────────────────┘
```

**Section 2: Orders Table** (expandable rows)

Table columns:
- **Due Date** (sortable)
- **Customer** 
- **State/ZIP**
- **SO#**
- **Plant**
- **Items** (collapsed: shows count, e.g., "3 items")
- **Qty** (total across all items in order)
- **Utilization %** (color-coded bar)
- **Status** (Ready / Excluded / Needs Review)
- **Actions** (Exclude / Edit)

**Row expansion** shows line item details:
```
Order #12438032 - OMAHA TRAILERS
├─ 7X14CGRCM × 1     |  SKU: CARGO | 6 ft  |  10 ft  |  19% utilization
└─ 7X12DLPE12K × 1   |  SKU: DUMP          |  16 ft  |  30% utilization
   Total: 2 items, 26 ft, 49% utilization
```

**Color-coded utilization bars**:
- Red (<30%): Very inefficient, high priority for consolidation
- Yellow (30-70%): Moderate, candidate for consolidation
- Green (70-90%): Good utilization, may ship as-is
- Blue (>90%): Near-full, ship as-is

**Section 3: Bulk Actions**
```
[Select All]  [Exclude Selected]  [Export to CSV]  [Clear All Orders]
```

### Feature 3: Optimization Engine (Enhanced)

**UI Location**: `/optimize` (new page)

**Page Layout**:

**Section 1: Optimization Parameters**
```
┌────────────────────────────────────────────────────────────┐
│ Optimization Settings                                      │
├────────────────────────────────────────────────────────────┤
│ Select Plant: [All ▾] [GA] [IA] [TX] [VA] [OR] [NV]       │
│                                                            │
│ Trailer Type: [◉ Flat Bed  ○ Step Deck]                   │
│ Trailer Capacity: [53] ft                                  │
│                                                            │
│ Advanced Settings:                                         │
│   Time Window: [7] days (consolidate orders within window)│
│   Geographic Radius: [100] miles                           │
│   Min Utilization Target: [75]%                            │
│   Max Detour: [15]%                                        │
│                                                            │
│ [Run Optimization]  [Use Previous Settings]                │
└────────────────────────────────────────────────────────────┘
```

**Section 2: Optimization Results** (appears after running)
```
┌─────────────────────────────────────────────────────────────┐
│ Optimization Results - GA Plant                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   Current State          Optimized            Improvement   │
│   ─────────────          ──────────            ──────────   │
│   32 loads               18 loads              -14 (-44%)   │
│   67% avg util           84% avg util          +17 pts      │
│   4,280 miles            3,650 miles           -630 mi      │
│   $12,480                $8,760                -$3,720      │
│                                                             │
│ Estimated Annual Savings: $193,440                          │
│                                                             │
│ [Accept & Create Loads]  [Adjust Parameters]  [Export]     │
└─────────────────────────────────────────────────────────────┘
```

**Section 3: Load Plan Preview**

Shows proposed loads in card format:
```
┌────────────────────────────────────────────┐
│ Load GA-001  [DRAFT]                       │
│ ┌────────────────────────────────────────┐ │
│ │ ████████████████░░░░░░░  84%           │ │
│ └────────────────────────────────────────┘ │
│ GA → FL  |  3 stops  |  780 mi  |  $2,730 │
│                                            │
│ • TRACTOR SUPPLY (FL-34758) - 6 units     │
│ • OMAHA TRAILERS (IA-51555) - 2 units     │
│ • RUNNINGS (MN-56258) - 4 units            │
│                                            │
│ Total: 12 items, 44.5 ft, Score: 87.3     │
│ [View Details] [Edit] [Remove]             │
└────────────────────────────────────────────┘
```

**Optimization Algorithm** (service: `services/optimizer.py`):

```python
class LoadOptimizer:
    def __init__(self, db_connection):
        self.db = db_connection
        self.rate_matrix = self.load_rate_matrix()
    
    def optimize_loads(self, params):
        """
        Main optimization entry point
        
        Args:
            params: {
                'plant': 'GA',
                'trailer_type': 'flat_bed',
                'capacity_ft': 53,
                'time_window_days': 7,
                'geo_radius_miles': 100,
                'min_utilization_pct': 75,
                'max_detour_pct': 15
            }
        
        Returns:
            {
                'current_state': {...},
                'optimized_loads': [...],
                'metrics': {...}
            }
        """
        # Step 1: Get eligible orders
        orders = self.get_eligible_orders(params['plant'])
        
        # Step 2: Calculate current state baseline
        current_state = self.calculate_baseline(orders, params)
        
        # Step 3: Cluster by geography
        clusters = self.cluster_by_destination(orders, params['geo_radius_miles'])
        
        # Step 4: Within each cluster, batch by time window
        time_batches = self.apply_time_windows(clusters, params['time_window_days'])
        
        # Step 5: Greedy pack with stacking logic
        loads = []
        for batch in time_batches:
            batch_loads = self.greedy_pack_with_stacking(
                batch, 
                params['capacity_ft'],
                params['trailer_type']
            )
            loads.extend(batch_loads)
        
        # Step 6: Calculate routes and costs
        for load in loads:
            load.calculate_route(params['plant'])
            load.calculate_cost(self.rate_matrix)
            load.calculate_optimization_score()
        
        # Step 7: Post-process - merge under-utilized loads if possible
        loads = self.merge_underutilized_loads(loads, params['min_utilization_pct'])
        
        # Step 8: Calculate overall metrics
        metrics = self.calculate_optimization_metrics(current_state, loads)
        
        return {
            'current_state': current_state,
            'optimized_loads': loads,
            'metrics': metrics
        }
    
    def greedy_pack_with_stacking(self, orders, capacity_ft, trailer_type):
        """
        Pack orders into loads considering:
        1. Physical trailer capacity (53 ft)
        2. Stacking constraints (different SKUs stack differently)
        3. Order cannot be split across loads (all items in order go together)
        """
        loads = []
        current_load = []
        current_length = 0.0
        
        # Sort orders: by total_length (smallest first), then due_date
        sorted_orders = sorted(orders, key=lambda x: (x.total_length_ft, x.due_date))
        
        for order in sorted_orders:
            order_length = order.total_length_ft
            
            # Check if order fits in current load
            if current_length + order_length <= capacity_ft:
                # Check stacking compatibility
                if self.check_stacking_compatible(current_load + [order], trailer_type):
                    current_load.append(order)
                    current_length += order_length
                else:
                    # Stacking conflict - start new load
                    if current_load:
                        loads.append(self.create_load(current_load))
                    current_load = [order]
                    current_length = order_length
            else:
                # Doesn't fit - start new load
                if current_load:
                    loads.append(self.create_load(current_load))
                current_load = [order]
                current_length = order_length
        
        # Add final load
        if current_load:
            loads.append(self.create_load(current_load))
        
        return loads
    
    def check_stacking_compatible(self, orders, trailer_type):
        """
        Verify that all orders can physically stack together
        Rules:
        - Different SKU categories may have different max stack heights
        - Total stacked height cannot exceed trailer height
        - Some SKUs cannot mix (e.g., DUMP + CARGO)
        """
        categories = [order.sku_category for order in orders]
        
        # Rule 1: DUMP cannot mix with other categories
        if 'DUMP' in categories and len(set(categories)) > 1:
            return False
        
        # Rule 2: Check total vertical space required
        # (This is simplified - real logic would be more complex)
        max_stack = min([order.max_stack_height for order in orders])
        
        return True  # Compatible if passes all checks
    
    def calculate_route(self, load, origin_plant):
        """
        Calculate routing sequence and total miles
        Uses nearest-neighbor TSP approximation
        """
        destinations = [order.zip for order in load.orders]
        
        if len(destinations) == 1:
            # Single stop - calculate direct distance
            load.total_miles = self.calculate_distance(origin_plant, destinations[0])
            load.detour_miles = 0
        else:
            # Multi-stop - calculate optimal sequence
            route = self.nearest_neighbor_route(origin_plant, destinations)
            load.route_sequence = route
            load.total_miles = self.calculate_route_miles(route)
            load.detour_miles = load.total_miles - self.calculate_distance(origin_plant, route[-1])
    
    def calculate_cost(self, load, rate_matrix):
        """
        Calculate estimated freight cost using rate matrix
        Rate varies by origin plant and destination state
        """
        # Get primary destination state (where most units are going)
        primary_state = self.get_primary_destination_state(load)
        
        # Look up rate
        rate_per_mile = rate_matrix.get((load.origin_plant, primary_state), 0)
        
        load.rate_per_mile = rate_per_mile
        load.estimated_cost = load.total_miles * rate_per_mile
```

### Feature 4: Reference Data Management

**UI Location**: New navigation tabs for reference data

**Tab 1: Rate Matrix** (`/rates`)
- Editable table showing rates by plant × state
- Import from Excel capability
- Export to CSV
- Audit trail (who changed what rate, when)

**Tab 2: SKU Specifications** (`/skus`)
- Table of all SKUs with length, category, stack heights
- Add/Edit/Delete SKUs
- Import from Cheat Sheet Excel
- Flag SKUs that are referenced by orders but missing specs

**Tab 3: Item Lookup Rules** (`/lookups`)
- Shows Plant + BIN + Item Pattern → SKU mappings
- Add new mapping rules for unmapped items
- Test mapping (enter Item, see what SKU it maps to)
- Shows usage statistics (how many orders use each mapping)

---

## UI/UX Design Specifications

### Color Scheme

**Primary Colors** (from uploaded image):
- **Amber/Gold**: `#F5B800` (headers, primary buttons, highlights)
- **Navy Blue**: `#1B4965` (secondary buttons, links, accents)
- **Red**: `#D63031` (warnings, low utilization alerts)
- **Blue**: `#0984E3` (info, high utilization positive)
- **Gray**: `#2D3436` (text)
- **Light Gray**: `#DFE6E9` (backgrounds, borders)

**Usage Guidelines**:
- **Amber/Gold**: Main CTAs, active navigation, success states
- **Navy Blue**: Secondary actions, table headers
- **Red**: <30% utilization, errors, critical warnings
- **Blue**: >85% utilization, informational messages
- **Yellow/Orange**: 30-85% utilization (gradient based on value)

### Navigation Bar

```
┌────────────────────────────────────────────────────────────────┐
│ 🚚 Dispatch Optimizer            [Amanda Smith ▾] [Settings]  │
├────────────────────────────────────────────────────────────────┤
│ [Upload] [Orders] [Optimize] [Loads] | [Rates] [SKUs] [Lookups]│
└────────────────────────────────────────────────────────────────┘
```

**Styling**:
- Background: Navy Blue (#1B4965)
- Text: White
- Active tab: Amber/Gold underline
- Hover: Lighter blue background

### Card-Based Layout

Use card design for:
- Load previews
- Order groups
- Summary statistics
- Optimization results

**Card Style**:
```css
.card {
    background: white;
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    padding: 20px;
    margin-bottom: 16px;
}

.card-header {
    background: #F5B800;
    color: #2D3436;
    font-weight: 600;
    padding: 12px 20px;
    border-radius: 8px 8px 0 0;
    margin: -20px -20px 16px -20px;
}
```

### Utilization Visualization

**Progress bars** with color coding:
```html
<div class="utilization-bar">
    <div class="fill" style="width: 84%; background: #0984E3;">84%</div>
</div>
```

**Color gradient**:
- 0-30%: `#D63031` (red)
- 30-50%: `#FDCB6E` (light orange)
- 50-70%: `#F5B800` (amber)
- 70-85%: `#6C5CE7` (purple)
- 85-100%: `#0984E3` (blue)

### Status Badges

```html
<span class="badge draft">DRAFT</span>
<span class="badge planned">PLANNED</span>
<span class="badge dispatched">DISPATCHED</span>
<span class="badge excluded">EXCLUDED</span>
```

**Badge styles**:
```css
.badge {
    padding: 4px 12px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
}

.badge.draft { background: #FDCB6E; color: #2D3436; }
.badge.planned { background: #0984E3; color: white; }
.badge.dispatched { background: #00B894; color: white; }
.badge.excluded { background: #B2BEC3; color: #2D3436; }
```

---

## Technical Implementation

### File Structure

```
app/
├── services/
│   ├── order_importer.py          # NEW - CSV parsing & SKU mapping
│   ├── optimizer.py                # NEW - Load optimization engine
│   ├── route_calculator.py         # NEW - Geographic routing
│   ├── cost_calculator.py          # NEW - Freight cost estimation
│   ├── reference_data_manager.py   # NEW - Manage rates, SKUs, lookups
│   └── load_builder.py             # KEEP - Original simple builder
├── static/
│   ├── css/
│   │   └── main.css                # ENHANCED - New color scheme
│   ├── js/
│   │   └── app.js                  # NEW - Interactive features
│   └── data/
│       └── zip_coords.json         # Geographic data for routing
├── templates/
│   ├── upload.html                 # NEW - CSV upload page
│   ├── orders.html                 # ENHANCED - Expandable table
│   ├── optimize.html               # NEW - Optimization interface
│   ├── loads.html                  # ENHANCED - Card-based load view
│   ├── rates.html                  # NEW - Rate matrix editor
│   ├── skus.html                   # NEW - SKU specifications
│   └── lookups.html                # NEW - Item lookup rules
└── app.py                          # ENHANCED - New routes
```

### Database Migrations

**Migration Script**: `migrations/002_pilot_ready.sql`

```sql
-- Create new tables
CREATE TABLE sku_specifications (...);
CREATE TABLE item_sku_lookup (...);
CREATE TABLE rate_matrix (...);

-- Modify order_lines table
ALTER TABLE order_lines ADD COLUMN due_date TEXT NOT NULL;
-- ... (all new columns from above)

-- Modify loads table  
ALTER TABLE loads ADD COLUMN origin_plant TEXT NOT NULL;
-- ... (all new columns from above)

-- Seed data from CSV/Excel files
-- (Separate script to import Master Cheat Sheet & Rate Matrix)
```

### Python Dependencies

Add to `requirements.txt`:
```
Flask==2.3.3
pandas==2.0.0           # For CSV/Excel parsing
openpyxl==3.1.0         # For Excel file support
geopy==2.4.0            # For geographic calculations
numpy==1.24.0           # For optimization math
python-dateutil==2.8.2  # For date parsing
```

### Data Import Scripts

**Script**: `scripts/import_reference_data.py`

```python
import pandas as pd
from db import get_connection

def import_sku_specs(excel_file):
    """Import SKU specifications from Master Cheat Sheet"""
    df = pd.read_excel(excel_file, sheet_name='Sheet1', skiprows=1)
    # Parse and insert into sku_specifications table
    pass

def import_sku_lookups(excel_file):
    """Import Item to SKU mappings from Lookups sheet"""
    df = pd.read_excel(excel_file, sheet_name='Lookups')
    # Parse and insert into item_sku_lookup table
    pass

def import_rate_matrix(excel_file):
    """Import freight rates from Rate Matrix"""
    df = pd.read_excel(excel_file, sheet_name='2026 Bid', skiprows=3)
    # Parse and insert into rate_matrix table
    pass

if __name__ == '__main__':
    import_sku_specs('Master_Load_Building_Cheat_Sheet.xlsx')
    import_sku_lookups('Master_Load_Building_Cheat_Sheet.xlsx')
    import_rate_matrix('COT_Rate_Matrix_2026.xlsx')
```

---

## Acceptance Criteria

### CSV Upload & Parsing
- [ ] System accepts Amanda's open orders CSV format without modification
- [ ] Auto-detects column headers (case-insensitive)
- [ ] Successfully maps 95%+ of items to SKUs using lookup table
- [ ] Shows clear error messages for unmapped items
- [ ] Calculates utilization for each order line accurately
- [ ] Upload completes in <10 seconds for 500 orders

### Order Review
- [ ] Orders table displays all key fields from CSV
- [ ] Expandable rows show line item details
- [ ] Utilization bars color-coded correctly (<30% red, 30-70% yellow, >70% green)
- [ ] Filters work correctly (plant, state, customer)
- [ ] Sort by any column
- [ ] Export filtered orders to CSV

### Optimization Engine
- [ ] Greedy packing respects trailer capacity (53 ft)
- [ ] Orders within same SONUM stay together
- [ ] Geographic clustering groups nearby destinations
- [ ] Time window filtering works correctly
- [ ] Cost calculation uses correct rates from rate matrix
- [ ] Optimization runs in <30 seconds for 500 orders
- [ ] Results show before/after comparison with savings estimate

### Reference Data
- [ ] Rate matrix can be imported from Excel
- [ ] SKU specs can be imported from Cheat Sheet
- [ ] New lookup rules can be added via UI
- [ ] Changes to reference data are logged (audit trail)
- [ ] Test mapping tool works correctly

### UI/UX
- [ ] Color scheme matches uploaded image (amber, navy, red, blue)
- [ ] Navigation is intuitive and clear
- [ ] Cards and badges display correctly
- [ ] Mobile-responsive (basic support)
- [ ] Loading indicators for long operations
- [ ] No JavaScript errors in console

---

## Testing Scenarios

### Scenario 1: Full Workflow Test
1. Upload `Amanda_Freight_File_v1.csv` (420 orders)
2. Verify all orders imported correctly
3. Review orders, check utilizations match manual calculations
4. Run optimization for GA plant
5. Verify optimized loads have 75%+ average utilization
6. Accept loads and create them
7. Export load plan to CSV

**Expected**: Complete workflow without errors, 15-20% reduction in load count

### Scenario 2: Unmapped Item Handling
1. Upload CSV with intentionally misspelled Item codes
2. Verify system flags unmapped items
3. Add new lookup rule via UI
4. Re-process orders
5. Verify newly mapped items now included

**Expected**: Clear error handling, easy remediation path

### Scenario 3: Multi-Plant Optimization
1. Upload orders for GA, IA, and TX plants
2. Run optimization separately for each plant
3. Verify rates used are plant-specific
4. Verify no cross-plant consolidation (orders stay within origin plant)

**Expected**: Plant-specific optimization with correct rates

### Scenario 4: Reference Data Updates
1. Import updated Rate Matrix (2027 rates)
2. Verify new rates applied to cost calculations
3. Import updated Cheat Sheet with new SKUs
4. Verify new SKUs available for mapping

**Expected**: Reference data updates reflected immediately

---

## Out of Scope (Future Enhancements)

- Multi-user authentication and roles
- Real-time collaboration (multiple users editing simultaneously)
- ERP integration (live order sync)
- Mobile app
- Advanced routing optimization (full TSP solver)
- Machine learning for demand forecasting
- Automated carrier booking
- Driver dispatch mobile app
- Real-time GPS tracking
- Post-dispatch actual vs planned analysis

---

## Success Metrics (Pilot Phase)

**Week 1-2**: Setup & Training
- Amanda and team trained on system
- Reference data imported and validated
- First upload of real orders successful

**Week 3-4**: Parallel Operation
- Run optimization in parallel with manual process
- Compare results weekly
- Track accuracy of SKU mapping

**Week 5-8**: Primary Operation
- System becomes primary tool for load planning
- Manual process as backup only
- Measure actual savings vs baseline

**Target Metrics by End of Pilot**:
- 95%+ SKU mapping accuracy
- 15-20% reduction in total loads
- 10-15 pt improvement in average utilization
- 20-30% reduction in planning time
- Zero critical errors (data loss, incorrect calculations)
- 90%+ user satisfaction (Amanda's team)

---

## Handoff Notes for Codex

**Implementation Priority**:

**Phase 1: Data Foundation** (Week 1)
1. Create new database tables (sku_specifications, item_sku_lookup, rate_matrix)
2. Write data import scripts for Excel files
3. Seed database with Cheat Sheet and Rate Matrix data
4. Test SKU lookup logic thoroughly

**Phase 2: Upload & Parsing** (Week 1-2)
1. Build CSV upload UI
2. Implement order_importer.py service
3. Test with Amanda_Freight_File_v1.csv
4. Validate utilization calculations against manual spreadsheet

**Phase 3: Order Review UI** (Week 2)
1. Redesign orders.html with expandable rows
2. Add filters and sorting
3. Implement color-coded utilization bars
4. Add export functionality

**Phase 4: Optimization Engine** (Week 2-3)
1. Implement optimizer.py with greedy packing
2. Add geographic clustering
3. Integrate rate matrix for cost calculations
4. Build optimize.html UI
5. Test with realistic data sets

**Phase 5: Reference Data Management** (Week 3)
1. Build rates.html, skus.html, lookups.html
2. Add CRUD operations for reference data
3. Implement import/export
4. Add audit logging

**Phase 6: UI Polish** (Week 4)
1. Apply color scheme throughout
2. Add loading indicators
3. Improve error messages
4. Mobile responsiveness
5. Final QA and bug fixes

**Critical Files**:
- `services/order_importer.py` - Most complex logic
- `services/optimizer.py` - Core optimization algorithm
- `templates/orders.html` - Main user interface
- `static/css/main.css` - Visual design

**Testing Requirements**:
- Unit tests for SKU lookup logic
- Integration test for full upload → optimize workflow
- Performance test with 1000+ orders
- Manual UAT with Amanda using real data

---

## Questions for Clarification

If any of these are unclear during implementation:

1. **Stacking Rules**: Are there specific SKU combinations that should never mix (beyond DUMP)? Should we enforce this strictly or show warnings?

2. **Order Splitting**: Can a single SONUM (sales order) be split across multiple loads, or must all items in an order go together?

3. **Rate Matrix**: If a destination state is not in the rate matrix, should we use a default rate or flag as error?

4. **Time Windows**: Should the 7-day consolidation window be based on due_date or some earlier "ready to ship" date?

5. **Multi-Stop Routing**: What's the maximum number of stops allowed on a single load? Should we penalize multi-stop loads in the optimization score?

6. **Utilization Targets**: Is 75% utilization the minimum acceptable, or just a guideline? Should we allow 60-70% loads to ship if they meet time deadlines?

---

**END OF PRD**
