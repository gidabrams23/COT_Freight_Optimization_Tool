# PRD: Load Consolidation Optimizer with Modern UI Redesign

## Executive Summary

**Objective**: Transform the dispatch optimization app with a sophisticated load consolidation engine and modern, interactive UI inspired by enterprise logistics software. The new design emphasizes real-time visual feedback, interactive load building, and professional aesthetics suitable for operational deployment.

**Success Metrics**:
- 20-40% reduction in total loads through intelligent consolidation
- Cost savings clearly visualized in dashboard metrics
- Seamless upload-to-optimize workflow with live status updates
- Professional UI that builds user confidence and trust
- Interactive load management with drag-and-drop capabilities

---

## Part 1: Load Consolidation Optimizer Engine

### Algorithm Overview

The optimizer uses a sophisticated two-phase approach:

**Phase 1: Greedy Load Building**
- Sort orders by distance from plant (furthest first)
- For each unassigned order, test it as a "seed" for a new load
- Add nearby, date-compatible orders until capacity is reached
- Score loads by cost-per-utilization efficiency
- Select the best-scoring load and repeat

**Phase 2: Rebalancing & Merging**
- Iteratively improve solution by moving orders between loads
- Merge underutilized loads when possible
- Re-optimize routes using nearest-neighbor TSP

**Key Features**:
- Considers geographic proximity (configurable radius filter)
- Respects delivery date flexibility windows
- Handles special requirements (e.g., Lowe's orders requiring backhaul)
- Calculates true routing cost with per-mile and per-stop charges
- Applies load minimums ($800 default)

### Cost Model

```
Load Cost = MAX(
    (Total Miles × $/mile) + (Number of Stops × $/stop),
    Load Minimum
)

Where:
- $/mile = $3.12 (configurable by plant/state from rate matrix)
- $/stop = $55.00 
- Load Minimum = $800.00
- Total Miles = Route miles × 1.20 (driving multiplier for road routing)
```

### Required Data Inputs

#### 1. ZIP Code Coordinates (YES, this is required)

**Format**: Excel or JSON file with columns:
```
zip    | lat      | lng
30301  | 33.7490  | -84.3880
30302  | 33.7710  | -84.3990
...
```

**Where to get it**:
- Free source: SimpleMaps.com US ZIP Codes Database (free version has 40k+ ZIPs)
- Alternative: US Census Bureau ZIP Code Tabulation Areas (ZCTAs)
- Format: Can be Excel (.xlsx) or JSON

**File location**: `/static/data/uszips.xlsx` or `/static/data/uszips.json`

**Why it's needed**: The optimizer calculates Haversine distances between order ZIPs to determine which orders can be consolidated together. Without lat/long data, geographic clustering is impossible.

#### 2. Plant Coordinates

**Format**: Dictionary or database table
```python
PLANT_COORDS = {
    'GA': (33.7490, -84.3880),  # Atlanta, GA
    'IA': (41.5868, -93.6250),  # Des Moines, IA
    'TX': (32.7767, -96.7970),  # Dallas, TX
    'VA': (37.5407, -77.4360),  # Richmond, VA
    'OR': (45.5152, -122.6784), # Portland, OR
    'NV': (36.1699, -115.1398), # Las Vegas, NV
}
```

**Storage**: Add to database or config file

#### 3. Order Data Requirements

Each order must have:
```python
{
    'order_number': str,      # Unique identifier (SONUM)
    'ship_date': datetime,    # Requested delivery date
    'zip_code': str,          # 5-digit destination ZIP
    'util': float,            # Trailer utilization (0.15 = 15%)
    'is_lowes': bool,         # Requires backhaul?
    'customer': str,          # Customer name
    'coords': tuple,          # (lat, lon) - looked up from zip_code
    'sku_detail': str,        # Line items: "SKU (qty) [Category], ..."
}
```

---

## Part 2: Modern UI Redesign

### Design Principles

**Inspiration**: Enterprise logistics software (see attached screenshots)
- **Dark mode ready**: Professional dark theme with high contrast
- **Real-time feedback**: Live status updates, progress indicators
- **Information density**: Maximize data visibility without clutter
- **Interactive elements**: Drag-and-drop, inline editing, hover states
- **Visual hierarchy**: Clear primary/secondary actions with color coding

### Color Scheme

**Primary Palette**:
```css
--primary-blue: #137fec;        /* Primary actions, links, highlights */
--success-green: #10b981;       /* Success states, positive metrics */
--warning-amber: #f59e0b;       /* Warnings, attention needed */
--danger-red: #ef4444;          /* Errors, over-capacity, critical */
--neutral-gray: #64748b;        /* Secondary text, borders */

--bg-light: #f6f7f8;            /* Light mode background */
--bg-dark: #101922;             /* Dark mode background */
--card-light: #ffffff;          /* Light mode cards */
--card-dark: #1e293b;           /* Dark mode cards */
```

**Usage Guidelines**:
- Primary Blue: CTAs, active states, progress bars, links
- Success Green: Ready status, good utilization (>85%)
- Warning Amber: Late orders, medium utilization (50-85%)
- Danger Red: Expedited orders, overweight, low utilization (<50%)

### Typography

**Font Stack**:
```css
font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
```

**Font Weights**:
- Regular (400): Body text
- Medium (500): Labels, secondary headings
- Semibold (600): Buttons, table headers
- Bold (700): Primary headings
- Black (900): Hero numbers, KPI values

### Component Library

#### 1. Status Badges

```html
<span class="badge badge-ready">Ready</span>
<span class="badge badge-late">Late</span>
<span class="badge badge-expedited">Expedited</span>
<span class="badge badge-staged">Staged</span>
```

**CSS**:
```css
.badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 9999px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.badge-ready {
    background: rgba(16, 185, 129, 0.1);
    color: #10b981;
    border: 1px solid rgba(16, 185, 129, 0.2);
}

.badge-late {
    background: rgba(245, 158, 11, 0.1);
    color: #f59e0b;
    border: 1px solid rgba(245, 158, 11, 0.2);
}

.badge-expedited {
    background: rgba(239, 68, 68, 0.1);
    color: #ef4444;
    border: 1px solid rgba(239, 68, 68, 0.2);
}
```

#### 2. Progress Bars / Utilization Meters

```html
<div class="utilization-meter">
    <div class="meter-header">
        <span class="meter-label">Weight Utilization</span>
        <span class="meter-value">88%</span>
    </div>
    <div class="meter-bar">
        <div class="meter-fill" style="width: 88%"></div>
    </div>
    <div class="meter-footer">
        26,150 lbs of 45,000 lbs max payload
    </div>
</div>
```

**CSS**:
```css
.utilization-meter {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.meter-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
}

.meter-label {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #64748b;
}

.meter-value {
    font-size: 24px;
    font-weight: 900;
    color: #137fec;
}

.meter-bar {
    height: 12px;
    width: 100%;
    background: #e2e8f0;
    border-radius: 6px;
    overflow: hidden;
}

.meter-fill {
    height: 100%;
    background: linear-gradient(90deg, #137fec 0%, #0ea5e9 100%);
    border-radius: 6px;
    transition: width 0.5s ease;
}

.meter-footer {
    font-size: 10px;
    color: #94a3b8;
}
```

#### 3. KPI Cards

```html
<div class="kpi-card">
    <div class="kpi-label">Pending Weight</div>
    <div class="kpi-value">145,200 <span class="kpi-unit">lbs</span></div>
    <div class="kpi-change positive">
        <span class="material-symbols-outlined">trending_up</span>
        +12%
    </div>
</div>
```

**CSS**:
```css
.kpi-card {
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 12px;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.kpi-label {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #94a3b8;
}

.kpi-value {
    font-size: 36px;
    font-weight: 900;
    color: #ffffff;
    line-height: 1;
}

.kpi-unit {
    font-size: 16px;
    font-weight: 600;
    color: #64748b;
    margin-left: 4px;
}

.kpi-change {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    font-weight: 700;
}

.kpi-change.positive {
    color: #10b981;
}

.kpi-change.negative {
    color: #ef4444;
}

.kpi-change .material-symbols-outlined {
    font-size: 16px;
}
```

---

## Part 3: Page-by-Page Redesign

### Page 1: Orders Dashboard (Upload + Review)

**NEW LAYOUT**: Single-page workflow combining upload, order review, and quick actions.

#### Top Bar: KPI Summary

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PENDING WEIGHT        AVAILABLE TRUCKS      AVG. CAPACITY USE   EST. DAILY COST │
│  145,200 lbs  +12%    24  -5%              82%  +3%            $12,450  On Budget │
└─────────────────────────────────────────────────────────────────────────┘
```

**Implementation**: 4-column grid of KPI cards with live updates

#### Main Content Area: Split View

**Left Panel (60% width): Orders Table**

```html
<div class="orders-panel">
    <div class="panel-header">
        <h3>
            <span class="material-symbols-outlined">list_alt</span>
            Open Orders (48)
        </h3>
        <div class="header-actions">
            <button class="btn-upload">
                <span class="material-symbols-outlined">cloud_upload</span>
                Upload Orders
            </button>
            <button class="btn-icon"><span class="material-symbols-outlined">filter_list</span></button>
            <button class="btn-icon"><span class="material-symbols-outlined">view_column</span></button>
        </div>
    </div>
    
    <table class="orders-table">
        <thead>
            <tr>
                <th><input type="checkbox" class="select-all"/></th>
                <th>Order ID</th>
                <th>Destination</th>
                <th>Weight</th>
                <th>Volume</th>
                <th>Status</th>
                <th></th>
            </tr>
        </thead>
        <tbody>
            <tr class="order-row" data-order-id="ORD-8821">
                <td><input type="checkbox"/></td>
                <td class="order-id">#ORD-8821</td>
                <td>Chicago, IL</td>
                <td class="weight">4,200 lbs</td>
                <td class="volume">120 ft³</td>
                <td><span class="badge badge-ready">Ready</span></td>
                <td class="row-actions">
                    <button class="btn-icon"><span class="material-symbols-outlined">more_vert</span></button>
                </td>
            </tr>
            <!-- More rows -->
        </tbody>
    </table>
</div>
```

**Key Features**:
- **Sortable columns**: Click headers to sort
- **Inline filters**: Search box, date range picker, status filter
- **Bulk actions**: Select multiple orders → "Add to Load" or "Optimize Selected"
- **Row hover**: Shows quick actions (view details, add to load, exclude)
- **Color coding**: Late orders in amber, expedited in red
- **Expandable rows**: Click to see SKU line items (like previous PRD)

**Right Panel (40% width): Quick Actions + Load Builder**

```html
<div class="sidebar-panel">
    <!-- Upload Section -->
    <div class="upload-section">
        <div class="upload-dropzone">
            <span class="material-symbols-outlined">cloud_upload</span>
            <h4>Upload Orders CSV</h4>
            <p>Drag file here or click to browse</p>
            <input type="file" accept=".csv,.xlsx" id="order-upload"/>
        </div>
        
        <!-- Upload Progress (shows when uploading) -->
        <div class="upload-progress hidden">
            <div class="progress-header">
                <span>Uploading...</span>
                <span class="progress-pct">67%</span>
            </div>
            <div class="progress-bar">
                <div class="progress-fill" style="width: 67%"></div>
            </div>
            <div class="progress-steps">
                <div class="step complete">✓ File validated</div>
                <div class="step active">⟳ Mapping SKUs...</div>
                <div class="step pending">○ Calculating utilizations</div>
            </div>
        </div>
        
        <!-- Upload Success -->
        <div class="upload-success hidden">
            <div class="success-icon">✓</div>
            <h4>420 orders imported</h4>
            <p>95% SKU mapping success</p>
            <button class="btn-primary">Review Orders</button>
        </div>
    </div>
    
    <!-- Load Builder Card -->
    <div class="load-builder-card">
        <h4>
            <span class="material-symbols-outlined">local_shipping</span>
            Load Builder
        </h4>
        
        <!-- Active Load -->
        <div class="active-load">
            <div class="load-header">
                <span class="load-id">Load #TRK-1092</span>
                <span class="badge badge-ready">Ready</span>
            </div>
            <div class="load-route">Route: Chicago Express</div>
            
            <!-- Utilization Meters -->
            <div class="utilization-meters">
                <div class="meter-mini">
                    <span class="meter-label">Weight</span>
                    <div class="meter-bar-mini">
                        <div class="meter-fill-mini" style="width: 88%"></div>
                    </div>
                    <span class="meter-value-mini">88%</span>
                </div>
                <div class="meter-mini">
                    <span class="meter-label">Volume</span>
                    <div class="meter-bar-mini">
                        <div class="meter-fill-mini" style="width: 72%"></div>
                    </div>
                    <span class="meter-value-mini">72%</span>
                </div>
            </div>
            
            <!-- Quick Stats -->
            <div class="load-stats">
                <div class="stat">
                    <span class="stat-icon">📦</span>
                    <span>Swift Logistics</span>
                </div>
                <div class="stat">
                    <span class="stat-icon">💵</span>
                    <span>$1,250.00</span>
                </div>
            </div>
            
            <button class="btn-primary btn-block">View Load Details</button>
        </div>
        
        <!-- Other Loads -->
        <div class="other-loads">
            <div class="load-summary">
                <span class="load-id">Load #TRK-1095</span>
                <span class="badge badge-warning">Overweight</span>
            </div>
            <div class="load-summary">
                <span class="load-id">Load #TRK-1098</span>
                <span class="badge-gray">Drafting</span>
            </div>
        </div>
        
        <button class="btn-secondary btn-block">+ Create New Load</button>
    </div>
    
    <!-- Geographic Distribution Map -->
    <div class="map-preview-card">
        <h4>Geographic Distribution</h4>
        <div class="map-container">
            <!-- Map placeholder or embed -->
            <div class="map-placeholder">
                [Interactive map showing order locations]
            </div>
        </div>
        <button class="btn-link">View Full Map Details</button>
    </div>
</div>
```

#### Upload Workflow States

**State 1: Ready to Upload**
- Show dropzone with "Drag file here" message
- On hover: Highlight dropzone border in primary blue
- On click: Open file picker

**State 2: File Selected / Validating**
```
Processing: Amanda_Freight_File_v1.csv (2.4 MB)
[=====>          ] 35%
✓ File format validated
⟳ Reading 420 rows...
```

**State 3: Mapping & Calculating**
```
Mapping SKUs & Calculating Utilizations
[============>   ] 67%
✓ 398 of 420 items mapped (95%)
⟳ Calculating utilizations...
⚠ 22 items need manual mapping
```

**State 4: Success with Summary**
```
✓ Import Complete!
──────────────────
420 orders imported
398 fully mapped (95%)
22 need attention

Orders by Plant:
  GA: 156 orders
  IA: 142 orders
  TX: 78 orders
  VA: 44 orders

[Review Orders] [Run Optimizer]
```

**State 5: Partial Success (Unmapped Items)**
```
⚠ Import Complete with Warnings
──────────────────
398 orders ready for optimization
22 orders need SKU mapping

[Fix Unmapped Items] [Continue with 398]
```

### Page 2: Optimization Results

**NEW PAGE**: After clicking "Run Optimizer" from Orders page

#### Hero Section: Before/After Comparison

```
┌────────────────────────────────────────────────────────────────────┐
│                    OPTIMIZATION RESULTS                            │
│                                                                    │
│   BEFORE                      AFTER                   SAVINGS      │
│   ───────                     ─────                   ────────    │
│   32 loads                    18 loads                -14 loads    │
│   67% avg util               84% avg util             +17 pts     │
│   4,280 miles                3,650 miles              -630 mi     │
│   $12,480                    $8,760                   -$3,720     │
│                                                                    │
│   Estimated Annual Savings: $193,440                              │
│                                                                    │
│   [Accept Solution] [Adjust Parameters] [Export Report]           │
└────────────────────────────────────────────────────────────────────┘
```

**Implementation**: Large comparison cards with animated counters

#### Load Cards Grid

```html
<div class="optimized-loads-grid">
    <!-- Load Card 1 -->
    <div class="load-card">
        <div class="load-card-header">
            <div>
                <h4>Load #OPT-001</h4>
                <p class="route">GA → FL (3 stops)</p>
            </div>
            <span class="badge badge-ready">Optimized</span>
        </div>
        
        <!-- Utilization Bars -->
        <div class="utilization-section">
            <div class="util-row">
                <span class="util-label">Weight</span>
                <div class="util-bar">
                    <div class="util-fill high" style="width: 94%">94%</div>
                </div>
            </div>
            <div class="util-row">
                <span class="util-label">Space</span>
                <div class="util-bar">
                    <div class="util-fill high" style="width: 88%">88%</div>
                </div>
            </div>
        </div>
        
        <!-- Load Details -->
        <div class="load-details">
            <div class="detail-row">
                <span class="detail-icon">📍</span>
                <span>780 miles • $2,730</span>
            </div>
            <div class="detail-row">
                <span class="detail-icon">📦</span>
                <span>6 orders • 12 items</span>
            </div>
            <div class="detail-row">
                <span class="detail-icon">🚚</span>
                <span>Swift Logistics</span>
            </div>
        </div>
        
        <!-- Orders in Load -->
        <div class="load-orders">
            <h5>Orders</h5>
            <ul>
                <li>#ORD-8821 • Chicago, IL • 4,200 lbs</li>
                <li>#ORD-8825 • Austin, TX • 12,500 lbs</li>
                <li>#ORD-8829 • Denver, CO • 8,400 lbs</li>
            </ul>
        </div>
        
        <div class="load-actions">
            <button class="btn-secondary">View Details</button>
            <button class="btn-primary">Approve Load</button>
        </div>
    </div>
    
    <!-- More load cards... -->
</div>
```

**Key Features**:
- **Visual utilization**: Color-coded bars (red <50%, yellow 50-85%, green >85%)
- **Route preview**: Mini map or route summary
- **Cost breakdown**: Show per-mile and per-stop costs
- **Order list**: Expandable list of orders in load
- **Actions**: Approve, edit, or reject each load

### Page 3: Load Detail & Manifest Adjustment

**MATCHES ATTACHED HTML**: Professional load detail page with interactive manifest

#### Top Section: Load Header + Timeline

```html
<div class="load-header">
    <div class="header-left">
        <div class="breadcrumbs">
            <a href="#">Loads</a>
            <span>›</span>
            <span>Load #L-9842</span>
        </div>
        <h1>
            #L-9842
            <span class="badge badge-planning">In Planning</span>
        </h1>
        <p class="load-meta">
            53' Reefer Trailer • Assigned to <strong>Mike Thompson</strong>
        </p>
    </div>
    <div class="header-actions">
        <button class="btn-secondary">
            <span class="material-symbols-outlined">print</span>
            Print BOL
        </button>
        <button class="btn-secondary">
            <span class="material-symbols-outlined">auto_fix</span>
            Optimize Route
        </button>
        <button class="btn-primary">
            <span class="material-symbols-outlined">send</span>
            Finalize & Dispatch
        </button>
    </div>
</div>

<!-- Horizontal Timeline -->
<div class="route-timeline">
    <div class="timeline-line"></div>
    <div class="timeline-stop active">
        <div class="stop-icon">🏭</div>
        <div class="stop-info">
            <h4>Warehouse A (Origin)</h4>
            <p>08:00 AM - 10:00 AM</p>
        </div>
    </div>
    <div class="timeline-stop">
        <div class="stop-icon">🚚</div>
        <div class="stop-info">
            <h4>Crossdock B</h4>
            <p>01:00 PM - 02:00 PM</p>
        </div>
    </div>
    <div class="timeline-stop">
        <div class="stop-icon">👤</div>
        <div class="stop-info">
            <h4>Customer C (Dest)</h4>
            <p>04:30 PM (ETA)</p>
        </div>
    </div>
</div>
```

#### Main Content: Manifest Table + Utilization Sidebar

**Left Panel: Interactive Manifest Table**

```html
<div class="manifest-table-container">
    <div class="table-header">
        <h3>
            <span class="material-symbols-outlined">list_alt</span>
            Load Manifest Adjustment
        </h3>
        <div class="table-actions">
            <button class="btn-icon"><span class="material-symbols-outlined">download</span></button>
            <button class="btn-icon"><span class="material-symbols-outlined">settings</span></button>
        </div>
    </div>
    
    <table class="manifest-table">
        <thead>
            <tr>
                <th class="drag-handle-col"></th>
                <th>Order</th>
                <th>SKU/Item ID</th>
                <th>Description</th>
                <th>Qty</th>
                <th>Weight (lbs)</th>
                <th>Pallets</th>
                <th>Status</th>
                <th></th>
            </tr>
        </thead>
        <tbody class="sortable-tbody">
            <tr class="manifest-row" draggable="true">
                <td class="drag-handle">
                    <span class="material-symbols-outlined">drag_indicator</span>
                </td>
                <td class="order-num">#1</td>
                <td class="sku">MFG-2994-A</td>
                <td class="description">Aluminum Casings - Grade A</td>
                <td>
                    <input type="number" class="qty-input" value="120"/>
                </td>
                <td class="weight">14,400</td>
                <td class="pallets">12</td>
                <td class="status">
                    <span class="status-dot green"></span>
                </td>
                <td class="actions">
                    <button class="btn-icon btn-delete">
                        <span class="material-symbols-outlined">delete</span>
                    </button>
                </td>
            </tr>
            <!-- More rows -->
        </tbody>
    </table>
    
    <div class="table-footer">
        <div>Showing 4 Items</div>
        <div class="footer-totals">
            <span>Total Weight: <strong>26,150 lbs</strong></span>
            <span>Total Pallets: <strong>24 / 28</strong></span>
        </div>
    </div>
</div>
```

**Key Features**:
- **Drag-and-drop reordering**: Grab drag handle to rearrange stops
- **Inline editing**: Click qty to edit, auto-recalculates totals
- **Delete with confirmation**: Click delete → confirm modal
- **Status indicators**: Green dot = ready, amber = late, red = issue
- **Live totals**: Footer updates as you edit

**Right Sidebar: Utilization Insights**

```html
<div class="utilization-sidebar">
    <!-- Utilization Meters -->
    <div class="insights-card">
        <h3>
            <span class="material-symbols-outlined">monitoring</span>
            Utilization Insights
        </h3>
        
        <div class="meter-group">
            <div class="utilization-meter">
                <div class="meter-header">
                    <span class="meter-label">Floor Space Usage</span>
                    <span class="meter-value">88%</span>
                </div>
                <div class="meter-bar">
                    <div class="meter-fill" style="width: 88%"></div>
                </div>
                <div class="meter-footer">
                    24 of 28 standard pallet spots occupied
                </div>
            </div>
            
            <div class="utilization-meter">
                <div class="meter-header">
                    <span class="meter-label">Weight Capacity</span>
                    <span class="meter-value">58%</span>
                </div>
                <div class="meter-bar">
                    <div class="meter-fill" style="width: 58%"></div>
                </div>
                <div class="meter-footer">
                    26,150 lbs of 45,000 lbs max payload
                </div>
            </div>
        </div>
    </div>
    
    <!-- KPI Cards -->
    <div class="kpi-grid">
        <div class="kpi-card-mini">
            <p class="kpi-label">Cost Per Mile</p>
            <p class="kpi-value">$2.45</p>
            <div class="kpi-trend positive">
                <span class="material-symbols-outlined">trending_down</span>
                4.2%
            </div>
        </div>
        <div class="kpi-card-mini">
            <p class="kpi-label">Est. Fuel</p>
            <p class="kpi-value">$642.00</p>
            <p class="kpi-note">Based on $3.89/gal</p>
        </div>
    </div>
    
    <!-- Map Preview -->
    <div class="map-preview">
        <h4>Map Preview</h4>
        <div class="map-embed">
            <!-- PLACEHOLDER: Will need Google Maps API or Mapbox -->
            <div class="map-placeholder">
                <span class="material-symbols-outlined">map</span>
                <p>Route map will display here</p>
                <small>Requires Google Maps API key</small>
            </div>
        </div>
        <button class="btn-link">View Full Map Details</button>
    </div>
    
    <!-- Route Optimization Suggestion -->
    <div class="alert-card info">
        <span class="material-symbols-outlined">info</span>
        <div>
            <h4>Route Optimization Available</h4>
            <p>We found a more efficient route through Crossdock B that saves 14 miles.</p>
            <button class="btn-link">Apply Suggestion</button>
        </div>
    </div>
</div>
```

---

## Part 4: Technical Implementation

### Database Schema Changes

#### New Tables for Optimizer

```sql
-- ZIP code coordinates (seeded from uszips.xlsx)
CREATE TABLE zip_coordinates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zip TEXT NOT NULL UNIQUE,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    city TEXT,
    state TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_zip_lookup ON zip_coordinates(zip);

-- Plant locations
CREATE TABLE plants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Optimization runs (history)
CREATE TABLE optimization_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    plant_code TEXT NOT NULL,
    flexibility_days INTEGER,
    num_orders_input INTEGER,
    num_loads_before INTEGER,
    num_loads_after INTEGER,
    cost_before REAL,
    cost_after REAL,
    avg_util_before REAL,
    avg_util_after REAL,
    config_json TEXT,
    created_by TEXT,
    FOREIGN KEY (plant_code) REFERENCES plants(plant_code)
);

-- Optimized loads (one row per load)
CREATE TABLE optimized_loads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    load_number INTEGER NOT NULL,
    plant_code TEXT NOT NULL,
    total_util REAL,
    total_miles REAL,
    total_cost REAL,
    num_orders INTEGER,
    route_json TEXT,
    status TEXT DEFAULT 'DRAFT',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES optimization_runs(id)
);

-- Load assignments (which orders go in which load)
CREATE TABLE load_order_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    load_id INTEGER NOT NULL,
    order_so_num TEXT NOT NULL,
    sequence INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (load_id) REFERENCES optimized_loads(id)
);
```

### Backend Services

#### New Service: `services/optimizer_engine.py`

**Integration with Python code**:

```python
# File: services/optimizer_engine.py

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'lib'))

from load_consolidation_optimizer import (
    optimize_loads,
    CostConfig,
    OptimizationConfig,
    get_coords_for_zip
)
import db

class OptimizerEngine:
    def __init__(self):
        self.zip_coords = self.load_zip_coords()
        self.plant_coords = self.load_plant_coords()
    
    def load_zip_coords(self):
        """Load ZIP coordinates from database"""
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT zip, lat, lng FROM zip_coordinates')
        return {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
    
    def load_plant_coords(self):
        """Load plant coordinates from database"""
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT plant_code, lat, lng FROM plants')
        return {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
    
    def prepare_orders_for_optimization(self, plant_code):
        """
        Fetch orders from database and prepare for optimizer
        
        Returns: List of order dicts ready for optimize_loads()
        """
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                so_num,
                due_date,
                zip,
                utilization_pct,
                customer,
                item,
                qty
            FROM order_lines
            WHERE plant = ? AND is_excluded = 0
            ORDER BY due_date
        ''', (plant_code,))
        
        rows = cursor.fetchall()
        
        # Group by SONUM and build order list
        orders_dict = {}
        for row in rows:
            so_num = row[0]
            if so_num not in orders_dict:
                coords = get_coords_for_zip(row[2], self.zip_coords)
                orders_dict[so_num] = {
                    'order_number': so_num,
                    'ship_date': pd.to_datetime(row[1]),
                    'zip_code': row[2],
                    'coords': coords,
                    'util': row[3] / 100.0,  # Convert % to decimal
                    'is_lowes': False,  # TODO: Detect from customer name
                    'customer': row[4],
                    'sku_detail': f"{row[5]} ({row[6]})"
                }
            else:
                # Append SKU detail
                orders_dict[so_num]['sku_detail'] += f", {row[5]} ({row[6]})"
                orders_dict[so_num]['util'] += row[3] / 100.0
        
        return list(orders_dict.values())
    
    def run_optimization(self, plant_code, flexibility_days=7):
        """
        Run optimization and save results to database
        
        Args:
            plant_code: Plant to optimize (e.g., 'GA')
            flexibility_days: Date flexibility window
        
        Returns:
            {
                'run_id': int,
                'loads': [...],
                'summary': {...}
            }
        """
        # Get orders
        orders = self.prepare_orders_for_optimization(plant_code)
        
        if not orders:
            return {'error': 'No orders to optimize'}
        
        # Get plant coordinates
        plant_coords = self.plant_coords.get(plant_code)
        if not plant_coords:
            return {'error': f'Plant {plant_code} not found'}
        
        # Get rate for this plant (from rate_matrix table)
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT AVG(rate_per_mile) 
            FROM rate_matrix 
            WHERE origin_plant = ?
        ''', (plant_code,))
        avg_rate = cursor.fetchone()[0] or 3.12
        
        # Configure optimizer
        cost_config = CostConfig(cost_per_mile=avg_rate)
        opt_config = OptimizationConfig()
        
        # Run optimizer
        loads, summary = optimize_loads(
            orders,
            plant_coords,
            flexibility_days,
            cost_config,
            opt_config
        )
        
        # Save to database
        run_id = self.save_optimization_results(
            plant_code,
            flexibility_days,
            orders,
            loads,
            summary
        )
        
        return {
            'run_id': run_id,
            'loads': self.format_loads_for_ui(loads),
            'summary': summary
        }
    
    def save_optimization_results(self, plant_code, flexibility_days, 
                                   orders, loads, summary):
        """Save optimization run and results to database"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Create run record
        cursor.execute('''
            INSERT INTO optimization_runs 
            (plant_code, flexibility_days, num_orders_input, 
             num_loads_after, cost_after, avg_util_after)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            plant_code,
            flexibility_days,
            summary['num_orders'],
            summary['num_loads'],
            summary['total_cost'],
            summary['avg_utilization']
        ))
        
        run_id = cursor.lastrowid
        
        # Save each load
        for idx, load in enumerate(loads):
            load_util = sum(o['util'] for o in load)
            load_cost, load_miles, route = calculate_load_cost(
                load,
                self.plant_coords[plant_code],
                CostConfig()
            )
            
            cursor.execute('''
                INSERT INTO optimized_loads
                (run_id, load_number, plant_code, total_util, 
                 total_miles, total_cost, num_orders)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                run_id,
                idx + 1,
                plant_code,
                load_util,
                load_miles,
                load_cost,
                len(load)
            ))
            
            load_id = cursor.lastrowid
            
            # Save order assignments
            for seq, order in enumerate(load):
                cursor.execute('''
                    INSERT INTO load_order_assignments
                    (load_id, order_so_num, sequence)
                    VALUES (?, ?, ?)
                ''', (load_id, order['order_number'], seq))
        
        conn.commit()
        return run_id
    
    def format_loads_for_ui(self, loads):
        """Convert optimizer output to UI-friendly format"""
        ui_loads = []
        for idx, load in enumerate(loads):
            ui_load = {
                'load_number': f"OPT-{idx+1:03d}",
                'orders': [
                    {
                        'order_id': o['order_number'],
                        'customer': o['customer'],
                        'zip': o['zip_code'],
                        'util': o['util'] * 100,  # Convert to %
                        'sku_detail': o['sku_detail']
                    }
                    for o in load
                ],
                'total_util': sum(o['util'] for o in load) * 100,
                'num_orders': len(load),
                'status': 'DRAFT'
            }
            ui_loads.append(ui_load)
        return ui_loads
```

#### New API Endpoints

```python
# File: app.py

from services.optimizer_engine import OptimizerEngine

@app.route('/api/optimize', methods=['POST'])
def run_optimization():
    """
    Run load consolidation optimizer
    
    Body:
        {
            "plant_code": "GA",
            "flexibility_days": 7,
            "proximity_miles": 200
        }
    
    Returns:
        {
            "run_id": 123,
            "loads": [...],
            "summary": {
                "num_loads": 18,
                "num_orders": 48,
                "total_cost": 8760,
                "avg_utilization": 0.84
            }
        }
    """
    data = request.json
    plant_code = data.get('plant_code')
    flexibility_days = data.get('flexibility_days', 7)
    
    engine = OptimizerEngine()
    result = engine.run_optimization(plant_code, flexibility_days)
    
    return jsonify(result)

@app.route('/api/optimize/<run_id>/loads')
def get_optimization_loads(run_id):
    """Get loads from a specific optimization run"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            l.id,
            l.load_number,
            l.total_util,
            l.total_miles,
            l.total_cost,
            l.num_orders,
            l.status,
            GROUP_CONCAT(a.order_so_num) as order_nums
        FROM optimized_loads l
        LEFT JOIN load_order_assignments a ON l.id = a.load_id
        WHERE l.run_id = ?
        GROUP BY l.id
        ORDER BY l.load_number
    ''', (run_id,))
    
    loads = []
    for row in cursor.fetchall():
        loads.append({
            'id': row[0],
            'load_number': row[1],
            'total_util': row[2] * 100,
            'total_miles': row[3],
            'total_cost': row[4],
            'num_orders': row[5],
            'status': row[6],
            'order_numbers': row[7].split(',') if row[7] else []
        })
    
    return jsonify({'loads': loads})
```

### Frontend JavaScript

#### Upload with Progress

```javascript
// File: static/js/upload.js

class OrderUploader {
    constructor() {
        this.dropzone = document.getElementById('upload-dropzone');
        this.fileInput = document.getElementById('order-upload');
        this.progressSection = document.querySelector('.upload-progress');
        this.successSection = document.querySelector('.upload-success');
        
        this.initEventListeners();
    }
    
    initEventListeners() {
        // Drag and drop
        this.dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            this.dropzone.classList.add('dragover');
        });
        
        this.dropzone.addEventListener('dragleave', () => {
            this.dropzone.classList.remove('dragover');
        });
        
        this.dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            this.dropzone.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                this.handleFile(files[0]);
            }
        });
        
        // File input
        this.fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                this.handleFile(e.target.files[0]);
            }
        });
    }
    
    async handleFile(file) {
        // Hide dropzone, show progress
        this.dropzone.classList.add('hidden');
        this.progressSection.classList.remove('hidden');
        
        // Create form data
        const formData = new FormData();
        formData.append('file', file);
        
        try {
            // Upload with progress tracking
            const response = await this.uploadWithProgress(formData);
            
            // Show success
            this.showSuccess(response);
        } catch (error) {
            this.showError(error);
        }
    }
    
    async uploadWithProgress(formData) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            
            // Progress tracking
            xhr.upload.addEventListener('progress', (e) => {
                if (e.lengthComputable) {
                    const pct = Math.round((e.loaded / e.total) * 100);
                    this.updateProgress(pct, 'uploading');
                }
            });
            
            xhr.addEventListener('load', () => {
                if (xhr.status === 200) {
                    const response = JSON.parse(xhr.responseText);
                    resolve(response);
                } else {
                    reject(new Error('Upload failed'));
                }
            });
            
            xhr.addEventListener('error', () => {
                reject(new Error('Network error'));
            });
            
            xhr.open('POST', '/api/orders/upload');
            xhr.send(formData);
        });
    }
    
    updateProgress(pct, stage) {
        const progressFill = document.querySelector('.progress-fill');
        const progressPct = document.querySelector('.progress-pct');
        const steps = document.querySelectorAll('.progress-steps .step');
        
        progressFill.style.width = `${pct}%`;
        progressPct.textContent = `${pct}%`;
        
        // Update steps
        if (pct < 30) {
            steps[0].classList.add('active');
        } else if (pct < 70) {
            steps[0].classList.remove('active');
            steps[0].classList.add('complete');
            steps[1].classList.add('active');
        } else {
            steps[1].classList.remove('active');
            steps[1].classList.add('complete');
            steps[2].classList.add('active');
        }
    }
    
    showSuccess(response) {
        this.progressSection.classList.add('hidden');
        this.successSection.classList.remove('hidden');
        
        document.querySelector('.success-message h4').textContent = 
            `${response.total_orders} orders imported`;
        document.querySelector('.success-message p').textContent = 
            `${response.mapping_rate}% SKU mapping success`;
    }
    
    showError(error) {
        alert(`Upload failed: ${error.message}`);
        this.dropzone.classList.remove('hidden');
        this.progressSection.classList.add('hidden');
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    new OrderUploader();
});
```

#### Optimizer Control

```javascript
// File: static/js/optimizer.js

class LoadOptimizer {
    async runOptimization(plantCode, flexibilityDays = 7) {
        // Show loading state
        this.showOptimizingState();
        
        try {
            const response = await fetch('/api/optimize', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    plant_code: plantCode,
                    flexibility_days: flexibilityDays
                })
            });
            
            const result = await response.json();
            
            // Display results
            this.displayResults(result);
        } catch (error) {
            this.showError(error);
        }
    }
    
    showOptimizingState() {
        const container = document.getElementById('results-container');
        container.innerHTML = `
            <div class="optimizing-state">
                <div class="spinner"></div>
                <h3>Optimizing loads...</h3>
                <p>This may take 30-60 seconds for large order sets</p>
                <div class="progress-steps">
                    <div class="step active">⟳ Clustering orders by geography</div>
                    <div class="step pending">○ Building initial loads</div>
                    <div class="step pending">○ Rebalancing solution</div>
                    <div class="step pending">○ Calculating costs</div>
                </div>
            </div>
        `;
    }
    
    displayResults(result) {
        // Render comparison cards
        this.renderComparisonSection(result.summary);
        
        // Render load cards
        this.renderLoadCards(result.loads);
        
        // Animate counters
        this.animateCounters();
    }
    
    renderComparisonSection(summary) {
        // Implementation...
    }
    
    renderLoadCards(loads) {
        const container = document.getElementById('load-cards-grid');
        container.innerHTML = '';
        
        loads.forEach(load => {
            const card = this.createLoadCard(load);
            container.appendChild(card);
        });
    }
    
    createLoadCard(load) {
        const card = document.createElement('div');
        card.className = 'load-card';
        card.innerHTML = `
            <div class="load-card-header">
                <div>
                    <h4>Load #${load.load_number}</h4>
                    <p class="route">Route info</p>
                </div>
                <span class="badge badge-ready">Optimized</span>
            </div>
            
            <div class="utilization-section">
                <div class="util-row">
                    <span class="util-label">Utilization</span>
                    <div class="util-bar">
                        <div class="util-fill ${this.getUtilClass(load.total_util)}" 
                             style="width: ${load.total_util}%">
                            ${load.total_util.toFixed(0)}%
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="load-details">
                <div class="detail-row">
                    <span>📦</span>
                    <span>${load.num_orders} orders</span>
                </div>
                <div class="detail-row">
                    <span>💵</span>
                    <span>$${load.total_cost.toFixed(2)}</span>
                </div>
            </div>
            
            <div class="load-actions">
                <button class="btn-secondary" onclick="viewLoadDetails('${load.id}')">
                    View Details
                </button>
                <button class="btn-primary" onclick="approveLoad('${load.id}')">
                    Approve Load
                </button>
            </div>
        `;
        return card;
    }
    
    getUtilClass(pct) {
        if (pct < 50) return 'low';
        if (pct < 85) return 'medium';
        return 'high';
    }
    
    animateCounters() {
        // Animate numbers counting up
        const counters = document.querySelectorAll('.counter');
        counters.forEach(counter => {
            const target = parseInt(counter.dataset.target);
            const duration = 1000;
            const step = target / (duration / 16);
            let current = 0;
            
            const timer = setInterval(() => {
                current += step;
                if (current >= target) {
                    counter.textContent = target;
                    clearInterval(timer);
                } else {
                    counter.textContent = Math.floor(current);
                }
            }, 16);
        });
    }
}

// Initialize
window.optimizer = new LoadOptimizer();
```

---

## Part 5: Placeholder Components

### Components That Need External Data

#### 1. **Map Previews** (Needs Google Maps API)

**Placeholder HTML**:
```html
<div class="map-placeholder">
    <span class="material-symbols-outlined">map</span>
    <p>Route map will display here</p>
    <small>Requires Google Maps API integration</small>
</div>
```

**What you need to provide**:
- Google Maps API key
- Or Mapbox access token
- Alternatively: Static map image URLs generated server-side

**Implementation when ready**:
```javascript
// Using Google Maps
function initMap(loadId) {
    const map = new google.maps.Map(document.getElementById('map'), {
        center: {lat: 33.7490, lng: -84.3880},
        zoom: 8
    });
    
    // Add markers for each stop
    // Draw route polyline
}
```

#### 2. **Carrier Assignment** (Needs Carrier Database)

**Placeholder**:
```html
<div class="carrier-assignment">
    <span class="carrier-icon">🚚</span>
    <span>Pending Carrier</span>
    <span class="badge-gray">Unassigned</span>
</div>
```

**What you need**:
- Carrier database table with rates and availability
- Integration with TMS or carrier API
- Carrier selection logic (lowest cost, preferred, etc.)

#### 3. **Real-Time Truck Tracking** (Needs GPS Integration)

**Placeholder**:
```html
<div class="tracking-placeholder">
    <span class="material-symbols-outlined">local_shipping</span>
    <p>Live tracking unavailable</p>
    <small>Requires GPS/ELD integration</small>
</div>
```

**What you need**:
- GPS device integration (ELD, telematics)
- Real-time location API
- WebSocket for live updates

#### 4. **Weather Alerts** (Needs Weather API)

**Placeholder**:
```html
<div class="weather-widget">
    <span class="material-symbols-outlined">cloud</span>
    <p>Weather data unavailable</p>
</div>
```

**What you need**:
- OpenWeatherMap API key (free tier available)
- Route corridor weather monitoring

---

## Part 6: Implementation Roadmap

### Phase 1: Core Optimizer (Week 1-2)

**Priority**: Get optimizer working with uploaded data

1. **Data Setup**
   - Download uszips.xlsx from SimpleMaps.com
   - Create seed script to import into `zip_coordinates` table
   - Add plant coordinates to `plants` table
   - Test Haversine distance calculations

2. **Optimizer Integration**
   - Add `load_consolidation_optimizer.py` to project
   - Create `OptimizerEngine` service class
   - Add API endpoints: `/api/optimize`, `/api/optimize/<run_id>/loads`
   - Test with sample order data

3. **Basic UI**
   - Create optimization results page
   - Display before/after comparison
   - Show load cards with utilization bars
   - No fancy animations yet

**Validation**: Can upload orders, click "Optimize", see results

### Phase 2: Modern UI Redesign (Week 2-3)

**Priority**: Implement professional visual design

1. **Design System**
   - Create CSS variables for color scheme
   - Build component library (badges, buttons, cards, meters)
   - Implement dark mode toggle
   - Add Material Icons font

2. **Orders Page Redesign**
   - Split layout: Orders table + Sidebar
   - Inline upload with progress states
   - Interactive table with sorting/filtering
   - KPI cards at top

3. **Load Detail Page**
   - Horizontal timeline
   - Interactive manifest table with drag-drop
   - Utilization sidebar with meters
   - Map placeholder

4. **Animations**
   - Progress bar transitions
   - Counter animations
   - Smooth expand/collapse
   - Loading spinners

**Validation**: App looks like attached screenshots

### Phase 3: Interactive Features (Week 3-4)

**Priority**: Add drag-drop, inline editing, real-time updates

1. **Drag-and-Drop**
   - Reorder manifest rows
   - Move orders between loads
   - Visual feedback during drag

2. **Inline Editing**
   - Edit quantities in manifest
   - Auto-recalculate totals
   - Validation on blur

3. **Real-Time Updates**
   - WebSocket for live order updates
   - Status badge changes
   - Alert notifications

4. **Bulk Actions**
   - Select multiple orders
   - Batch operations (exclude, add to load, delete)
   - Confirmation modals

**Validation**: Can manage loads interactively

### Phase 4: Polish & Placeholders (Week 4)

**Priority**: Fill in missing pieces, document placeholders

1. **Map Integration**
   - If Google Maps API available: implement
   - Otherwise: clear placeholder with instructions

2. **Carrier Assignment**
   - If carrier data available: implement
   - Otherwise: placeholder with "Pending Carrier"

3. **Error Handling**
   - Graceful failures
   - User-friendly error messages
   - Retry mechanisms

4. **Documentation**
   - Setup guide for ZIP code data
   - API documentation
   - User manual

**Validation**: App is deployment-ready

---

## Part 7: Acceptance Criteria

### Functional Requirements

**Optimizer Engine**:
- [ ] Imports ZIP coordinates from uszips.xlsx
- [ ] Calculates Haversine distances between orders
- [ ] Groups orders by geographic proximity
- [ ] Respects date flexibility windows
- [ ] Generates optimized loads with 15-30% fewer loads
- [ ] Calculates accurate costs using rate matrix
- [ ] Saves optimization results to database
- [ ] Handles edge cases (single order, all excluded, etc.)

**Upload Workflow**:
- [ ] Drag-and-drop file upload works
- [ ] Shows progress with percentage and steps
- [ ] Displays success summary with order count
- [ ] Handles errors gracefully
- [ ] Shows unmapped items warnings
- [ ] Allows continuing with partial data

**Orders Page**:
- [ ] Displays orders in sortable table
- [ ] Shows KPI cards with live data
- [ ] Upload section visible in sidebar
- [ ] Can select multiple orders for bulk actions
- [ ] Expandable rows show SKU details
- [ ] Color-coded status badges

**Optimization Results**:
- [ ] Shows before/after comparison clearly
- [ ] Displays load cards in grid
- [ ] Each card shows utilization, cost, orders
- [ ] Can approve, reject, or edit loads
- [ ] Animations smooth and not janky
- [ ] Results save to database

**Load Detail Page**:
- [ ] Shows horizontal timeline
- [ ] Manifest table with drag-drop reordering
- [ ] Inline quantity editing works
- [ ] Totals update in real-time
- [ ] Utilization sidebar shows accurate metrics
- [ ] Map placeholder visible with instructions

### Visual Requirements

**Design System**:
- [ ] Color scheme matches specification (primary blue, success green, etc.)
- [ ] Typography uses Inter font with correct weights
- [ ] Dark mode fully functional
- [ ] Components consistent across pages
- [ ] Material Icons display correctly
- [ ] Spacing and padding consistent

**Responsiveness**:
- [ ] Desktop (1920x1080): Full layout
- [ ] Laptop (1366x768): Adjusted sidebar
- [ ] Tablet (768x1024): Stacked layout
- [ ] Mobile (375x667): Mobile-optimized

**Animations**:
- [ ] Progress bars animate smoothly
- [ ] Counters count up on load
- [ ] Expand/collapse is smooth
- [ ] Drag-drop has visual feedback
- [ ] Loading spinners when needed

### Performance Requirements

**Optimizer**:
- [ ] Handles 100 orders in <30 seconds
- [ ] Handles 500 orders in <2 minutes
- [ ] Does not freeze browser during optimization
- [ ] Progress updates every 5 seconds

**UI**:
- [ ] Page load <2 seconds
- [ ] Table with 100 rows renders in <1 second
- [ ] Drag-drop feels immediate (<100ms)
- [ ] No JavaScript errors in console

---

## Part 8: Data Requirements Summary

### Required Files

1. **ZIP Code Coordinates**
   - **Format**: Excel (.xlsx) or JSON
   - **Columns**: zip, lat, lng, city, state
   - **Source**: SimpleMaps.com (free) or US Census
   - **Size**: ~40k US ZIPs, ~5 MB
   - **Location**: `/static/data/uszips.xlsx`

2. **Plant Locations**
   - **Format**: SQL insert or JSON config
   - **Data**: Plant code, name, lat, lng
   - **Example**:
     ```json
     {
       "GA": {"name": "Atlanta Plant", "lat": 33.7490, "lng": -84.3880},
       "IA": {"name": "Iowa Plant", "lat": 41.5868, "lng": -93.6250}
     }
     ```

### Optional Enhancements (Can Add Later)

1. **Google Maps API Key** - For route maps
2. **Carrier Database** - For carrier assignment
3. **ELD/GPS Integration** - For live tracking
4. **Weather API Key** - For route weather alerts
5. **TMS Integration** - For automated dispatch

---

## Questions for Clarification

1. **Do you have Google Maps API key available?** If yes, we can integrate maps. If no, we'll use placeholders.

2. **Is there a carrier database or TMS?** If yes, we can add carrier selection logic. If no, we'll show "Pending Carrier".

3. **What's the typical order volume?** This affects performance tuning (100/day vs 1000/day).

4. **Do you want real-time updates via WebSocket?** Or is refresh-based UI acceptable?

5. **Authentication needed?** Or is this internal tool without login?

---

**END OF PRD**
