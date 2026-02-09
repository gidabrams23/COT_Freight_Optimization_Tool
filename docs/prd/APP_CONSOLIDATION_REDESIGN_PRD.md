# PRD: App Consolidation & Enterprise UI Redesign

## Executive Summary

**Objective**: Consolidate the dispatch optimization app from 7 pages to 4 focused tabs with an enterprise logistics aesthetic. Streamline navigation, integrate upload into order management, create an executive dashboard, and enhance load detail views with color-coded visual schematics matching professional load sheets.

**Current State**: 7 separate pages (Upload, Orders, Optimize, Loads, Rates, SKUs, Lookups) with modern UI but fragmented workflow.

**Target State**: 4 consolidated tabs (Dashboard, Orders, Loads, Settings) with dark theme, colored accents, and professional logistics interface matching the attached screenshots.

---

## Design Philosophy

**Inspiration**: Enterprise logistics software (LogisticsPro, TMS platforms)
- **Dark background** (#0f172a, #1e293b) as primary canvas
- **Colored accents** for status and highlights (blue #137fec, green #10b981, amber #f59e0b, red #ef4444)
- **Information density** without clutter
- **Interactive visuals** over static tables
- **Color-coded load schematics** resembling real shipping documents

---

## Part 1: Navigation Structure Redesign

### Current Navigation (7 pages)
```
Upload | Orders | Optimize | Loads | Rates | SKUs | Lookups
```

### New Navigation (4 tabs)
```
Dashboard | Orders | Loads | Settings
```

**Mapping**:
- **Dashboard** (NEW): Executive overview with KPIs, load summary by plant, spending trends
- **Orders**: Combines Upload + Orders + Optimize functionality
- **Loads**: Enhanced version of current Loads page with drill-down details
- **Settings**: Consolidates Rates + SKUs + Lookups into tabbed interface

---

## Part 2: Page-by-Page Redesign

### Page 1: Dashboard (NEW HOME PAGE)

**Route**: `/` or `/dashboard`

**Purpose**: Executive command center showing current state of operations at a glance

#### Layout Structure

**Top Row: Global KPI Cards** (4 cards, equal width)

```html
<div class="kpi-grid">
    <!-- Card 1: Pending Weight -->
    <div class="kpi-card">
        <div class="kpi-header">
            <span class="kpi-label">PENDING WEIGHT</span>
            <span class="kpi-trend positive">
                <span class="material-symbols-outlined">trending_up</span>
                +12%
            </span>
        </div>
        <div class="kpi-value">145,200 <span class="kpi-unit">lbs</span></div>
        <div class="kpi-footer">Across 48 open orders</div>
    </div>
    
    <!-- Card 2: Available Trucks -->
    <div class="kpi-card">
        <div class="kpi-header">
            <span class="kpi-label">AVAILABLE TRUCKS</span>
            <span class="kpi-trend negative">
                <span class="material-symbols-outlined">trending_down</span>
                -5%
            </span>
        </div>
        <div class="kpi-value">24</div>
        <div class="kpi-footer">Ready for dispatch</div>
    </div>
    
    <!-- Card 3: Avg Capacity Use -->
    <div class="kpi-card">
        <div class="kpi-header">
            <span class="kpi-label">AVG. CAPACITY USE</span>
            <span class="kpi-trend positive">
                <span class="material-symbols-outlined">trending_up</span>
                +3%
            </span>
        </div>
        <div class="kpi-value">82<span class="kpi-unit">%</span></div>
        <div class="kpi-footer">Utilization efficiency</div>
    </div>
    
    <!-- Card 4: Est Daily Cost -->
    <div class="kpi-card">
        <div class="kpi-header">
            <span class="kpi-label">EST. DAILY COST</span>
            <span class="badge badge-success">On Budget</span>
        </div>
        <div class="kpi-value">$12,450</div>
        <div class="kpi-footer">Based on current loads</div>
    </div>
</div>
```

**KPI Data Sources**:
```sql
-- Pending Weight
SELECT SUM(total_length_ft * 100) as pending_weight_lbs
FROM orders WHERE is_excluded = 0;

-- Available Trucks (placeholder - will need loads table count)
SELECT COUNT(*) FROM loads WHERE status = 'DRAFT';

-- Avg Capacity Use
SELECT AVG(utilization_pct) FROM orders WHERE is_excluded = 0;

-- Est Daily Cost
SELECT SUM(estimated_cost) FROM loads WHERE DATE(created_at) = DATE('now');
```

#### Main Content: Split Layout

**Left Panel (60%): Open Orders Table**

```html
<div class="dashboard-section">
    <div class="section-header">
        <h3>
            <span class="material-symbols-outlined">list_alt</span>
            Open Orders (48)
        </h3>
        <div class="header-actions">
            <button class="btn-icon" title="Filter">
                <span class="material-symbols-outlined">filter_list</span>
            </button>
            <button class="btn-icon" title="View Options">
                <span class="material-symbols-outlined">view_column</span>
            </button>
        </div>
    </div>
    
    <div class="orders-table-container">
        <table class="dashboard-table">
            <thead>
                <tr>
                    <th>ORDER ID</th>
                    <th>DESTINATION</th>
                    <th>WEIGHT</th>
                    <th>VOLUME</th>
                    <th>STATUS</th>
                </tr>
            </thead>
            <tbody>
                <tr class="table-row clickable" onclick="viewOrder('ORD-8821')">
                    <td class="order-id">#ORD-8821</td>
                    <td class="destination">Chicago, IL</td>
                    <td class="weight">4,200 lbs</td>
                    <td class="volume">120 ft³</td>
                    <td><span class="badge badge-ready">READY</span></td>
                </tr>
                <tr class="table-row clickable" onclick="viewOrder('ORD-8825')">
                    <td class="order-id">#ORD-8825</td>
                    <td class="destination">Austin, TX</td>
                    <td class="weight highlight-warning">12,500 lbs</td>
                    <td class="volume">450 ft³</td>
                    <td><span class="badge badge-late">LATE</span></td>
                </tr>
                <tr class="table-row clickable" onclick="viewOrder('ORD-8829')">
                    <td class="order-id">#ORD-8829</td>
                    <td class="destination">Denver, CO</td>
                    <td class="weight">8,400 lbs</td>
                    <td class="volume">210 ft³</td>
                    <td><span class="badge badge-ready">READY</span></td>
                </tr>
                <!-- More rows... -->
            </tbody>
        </table>
    </div>
    
    <div class="table-footer">
        <button class="btn-link">View All Orders →</button>
    </div>
</div>
```

**Right Panel (40%): Load Builder & Map**

```html
<div class="sidebar-panel">
    <!-- Load Builder Card -->
    <div class="load-builder-widget">
        <div class="widget-header">
            <h3>
                <span class="material-symbols-outlined">local_shipping</span>
                Load Builder
            </h3>
            <button class="btn-icon">
                <span class="material-symbols-outlined">add</span>
            </button>
        </div>
        
        <!-- Active Load Card 1 -->
        <div class="load-card-mini ready">
            <div class="load-header">
                <span class="load-id">Load #TRK-1092</span>
                <span class="badge badge-ready">READY</span>
            </div>
            <div class="load-route">Route: Chicago Express</div>
            
            <div class="utilization-bars-mini">
                <div class="util-bar-mini">
                    <span class="util-label">Weight</span>
                    <div class="util-progress">
                        <div class="util-fill high" style="width: 88%"></div>
                    </div>
                    <span class="util-value">88%</span>
                </div>
                <div class="util-bar-mini">
                    <span class="util-label">Volume</span>
                    <div class="util-progress">
                        <div class="util-fill medium" style="width: 72%"></div>
                    </div>
                    <span class="util-value">72%</span>
                </div>
            </div>
            
            <div class="load-footer">
                <div class="load-stat">
                    <span class="icon">🚚</span>
                    <span>Swift Logistics</span>
                </div>
                <div class="load-stat">
                    <span class="icon">💵</span>
                    <span>$1,250.00</span>
                </div>
            </div>
        </div>
        
        <!-- Active Load Card 2 -->
        <div class="load-card-mini overweight">
            <div class="load-header">
                <span class="load-id">Load #TRK-1095</span>
                <span class="badge badge-danger">OVERWEIGHT</span>
            </div>
            <div class="load-route">Route: Austin Hub</div>
            
            <div class="utilization-bars-mini">
                <div class="util-bar-mini">
                    <span class="util-label">Weight</span>
                    <div class="util-progress">
                        <div class="util-fill danger" style="width: 104%"></div>
                    </div>
                    <span class="util-value">104%</span>
                </div>
            </div>
            
            <div class="alert-message danger">
                <span class="material-symbols-outlined">warning</span>
                Action required: Remove 1,200 lbs to meet safety limits.
            </div>
        </div>
        
        <!-- Draft Load Card 3 -->
        <div class="load-card-mini draft">
            <div class="load-header">
                <span class="load-id">Load #TRK-1098</span>
                <span class="badge badge-neutral">DRAFTING</span>
            </div>
            <div class="load-route">Route: Regional West</div>
            <div class="load-footer">
                <span class="pending-text">Pending Carrier</span>
                <span class="est-cost">Est. $840</span>
            </div>
        </div>
        
        <button class="btn-secondary btn-block">
            <span class="material-symbols-outlined">add</span>
            Create New Load
        </button>
    </div>
    
    <!-- Geographic Distribution Map -->
    <div class="map-widget">
        <div class="widget-header">
            <h3>GEOGRAPHIC DISTRIBUTION</h3>
            <button class="btn-icon">
                <span class="material-symbols-outlined">fullscreen</span>
            </button>
        </div>
        <div class="map-container">
            <!-- Placeholder map - dots showing order locations -->
            <div class="map-placeholder">
                <canvas id="order-distribution-map" width="400" height="200"></canvas>
                <div class="map-legend">
                    <span class="legend-item">
                        <span class="dot ga"></span> GA Plant
                    </span>
                    <span class="legend-item">
                        <span class="dot order"></span> Orders
                    </span>
                </div>
            </div>
        </div>
        <div class="widget-footer">
            <button class="btn-link">View Full Map Details →</button>
        </div>
    </div>
</div>
```

#### Bottom Section: Load Summary by Plant

```html
<div class="plant-summary-section">
    <h3>Load Summary by Plant</h3>
    <div class="plant-cards-grid">
        <!-- GA Plant -->
        <div class="plant-card">
            <div class="plant-header">
                <h4>GA - Atlanta</h4>
                <span class="badge badge-success">LIVE</span>
            </div>
            <div class="plant-metrics">
                <div class="metric">
                    <span class="metric-label">Open Orders</span>
                    <span class="metric-value">18</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Active Loads</span>
                    <span class="metric-value">4</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Avg Util</span>
                    <span class="metric-value">87%</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Est Cost</span>
                    <span class="metric-value">$4,280</span>
                </div>
            </div>
        </div>
        
        <!-- IA Plant -->
        <div class="plant-card">
            <div class="plant-header">
                <h4>IA - Iowa</h4>
                <span class="badge badge-success">LIVE</span>
            </div>
            <div class="plant-metrics">
                <div class="metric">
                    <span class="metric-label">Open Orders</span>
                    <span class="metric-value">15</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Active Loads</span>
                    <span class="metric-value">3</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Avg Util</span>
                    <span class="metric-value">79%</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Est Cost</span>
                    <span class="metric-value">$3,650</span>
                </div>
            </div>
        </div>
        
        <!-- TX Plant -->
        <div class="plant-card">
            <div class="plant-header">
                <h4>TX - Dallas</h4>
                <span class="badge badge-success">LIVE</span>
            </div>
            <div class="plant-metrics">
                <div class="metric">
                    <span class="metric-label">Open Orders</span>
                    <span class="metric-value">12</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Active Loads</span>
                    <span class="metric-value">2</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Avg Util</span>
                    <span class="metric-value">82%</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Est Cost</span>
                    <span class="metric-value">$2,890</span>
                </div>
            </div>
        </div>
        
        <!-- VA Plant -->
        <div class="plant-card">
            <div class="plant-header">
                <h4>VA - Richmond</h4>
                <span class="badge badge-warning">LOW ACTIVITY</span>
            </div>
            <div class="plant-metrics">
                <div class="metric">
                    <span class="metric-label">Open Orders</span>
                    <span class="metric-value">3</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Active Loads</span>
                    <span class="metric-value">1</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Avg Util</span>
                    <span class="metric-value">64%</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Est Cost</span>
                    <span class="metric-value">$980</span>
                </div>
            </div>
        </div>
    </div>
</div>
```

**Backend Data for Dashboard**:

```python
# New route: app.py
@app.route('/')
@app.route('/dashboard')
def dashboard():
    """
    Dashboard home page with executive KPIs and load summary
    """
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # Global KPIs
    cursor.execute('''
        SELECT 
            COUNT(*) as total_orders,
            SUM(total_length_ft * 100) as pending_weight,
            AVG(utilization_pct) as avg_util
        FROM orders
        WHERE is_excluded = 0
    ''')
    orders_stats = cursor.fetchone()
    
    cursor.execute('''
        SELECT COUNT(*) FROM loads WHERE status = 'DRAFT'
    ''')
    available_trucks = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT SUM(estimated_cost) 
        FROM loads 
        WHERE DATE(created_at) = DATE('now')
    ''')
    daily_cost = cursor.fetchone()[0] or 0
    
    # Open orders (recent 10)
    cursor.execute('''
        SELECT 
            so_num,
            COALESCE(city, '') || ', ' || state as destination,
            total_length_ft * 100 as weight_lbs,
            total_length_ft as volume_ft3,
            CASE 
                WHEN DATE(due_date) < DATE('now') THEN 'LATE'
                WHEN DATE(due_date) = DATE('now') THEN 'EXPEDITED'
                ELSE 'READY'
            END as status
        FROM orders
        WHERE is_excluded = 0
        ORDER BY due_date ASC
        LIMIT 10
    ''')
    open_orders = cursor.fetchall()
    
    # Active loads (recent 3)
    cursor.execute('''
        SELECT 
            id,
            'TRK-' || PRINTF('%04d', id) as load_number,
            destination_state as route,
            utilization_pct,
            estimated_cost,
            status,
            CASE 
                WHEN utilization_pct > 100 THEN 'overweight'
                WHEN status = 'DRAFT' THEN 'draft'
                ELSE 'ready'
            END as card_type
        FROM loads
        ORDER BY created_at DESC
        LIMIT 3
    ''')
    active_loads = cursor.fetchall()
    
    # Plant summary
    cursor.execute('''
        SELECT 
            plant,
            COUNT(*) as open_orders,
            AVG(utilization_pct) as avg_util
        FROM orders
        WHERE is_excluded = 0
        GROUP BY plant
    ''')
    plant_orders = {row[0]: {'orders': row[1], 'avg_util': row[2]} for row in cursor.fetchall()}
    
    cursor.execute('''
        SELECT 
            origin_plant,
            COUNT(*) as active_loads,
            SUM(estimated_cost) as est_cost
        FROM loads
        WHERE status IN ('DRAFT', 'PLANNED')
        GROUP BY origin_plant
    ''')
    plant_loads = {row[0]: {'loads': row[1], 'cost': row[2]} for row in cursor.fetchall()}
    
    # Combine plant data
    plants = []
    for plant_code in ['GA', 'IA', 'TX', 'VA', 'OR', 'NV']:
        orders_data = plant_orders.get(plant_code, {'orders': 0, 'avg_util': 0})
        loads_data = plant_loads.get(plant_code, {'loads': 0, 'cost': 0})
        
        plants.append({
            'code': plant_code,
            'name': PLANT_NAMES.get(plant_code, plant_code),
            'open_orders': orders_data['orders'],
            'active_loads': loads_data['loads'],
            'avg_util': orders_data['avg_util'],
            'est_cost': loads_data['cost'],
            'status': 'LIVE' if orders_data['orders'] > 5 else 'LOW ACTIVITY'
        })
    
    return render_template('dashboard.html',
        total_orders=orders_stats[0],
        pending_weight=orders_stats[1],
        avg_util=orders_stats[2],
        available_trucks=available_trucks,
        daily_cost=daily_cost,
        open_orders=open_orders,
        active_loads=active_loads,
        plants=plants
    )
```

---

### Page 2: Orders (Consolidated Upload + Orders + Optimize)

**Route**: `/orders`

**Purpose**: Complete order management workflow - upload, review, filter, optimize

#### Key Changes from Current

1. **Integrated Upload**: Upload widget embedded in right sidebar (not separate page)
2. **Last Upload Summary**: Shows stats from most recent upload
3. **Optimization Panel**: Collapsible section to run optimizer without leaving page
4. **Streamlined Actions**: Bulk operations available in-line

#### Layout Structure

**Top Section: KPI Bar + Quick Actions**

```html
<div class="page-header">
    <div class="header-left">
        <h1>Order Management</h1>
        <p class="page-subtitle">Upload, review, and optimize orders</p>
    </div>
    <div class="header-actions">
        <button class="btn-secondary" onclick="exportOrders()">
            <span class="material-symbols-outlined">download</span>
            Export CSV
        </button>
        <button class="btn-primary" onclick="toggleOptimizer()">
            <span class="material-symbols-outlined">auto_fix</span>
            Run Optimizer
        </button>
    </div>
</div>

<div class="kpi-bar">
    <div class="kpi-item">
        <span class="kpi-label">OPEN ORDERS</span>
        <span class="kpi-value">48</span>
    </div>
    <div class="kpi-item">
        <span class="kpi-label">TOTAL CAPACITY</span>
        <span class="kpi-value">2,850 ft</span>
    </div>
    <div class="kpi-item">
        <span class="kpi-label">AVG UTILIZATION</span>
        <span class="kpi-value">76%</span>
    </div>
    <div class="kpi-item">
        <span class="kpi-label">EXCLUDED</span>
        <span class="kpi-value">5</span>
    </div>
</div>
```

**Main Content: Split Layout**

**Left Panel (65%): Orders Table**

```html
<div class="orders-main-panel">
    <!-- Filter Bar -->
    <div class="filter-bar">
        <div class="filter-group">
            <select class="filter-select" id="plant-filter">
                <option value="">All Plants</option>
                <option value="GA">GA - Atlanta</option>
                <option value="IA">IA - Iowa</option>
                <option value="TX">TX - Dallas</option>
                <option value="VA">VA - Richmond</option>
            </select>
            <select class="filter-select" id="state-filter">
                <option value="">All States</option>
                <!-- Populated dynamically -->
            </select>
            <select class="filter-select" id="customer-filter">
                <option value="">All Customers</option>
                <!-- Populated dynamically -->
            </select>
        </div>
        <div class="search-box">
            <span class="material-symbols-outlined">search</span>
            <input type="text" placeholder="Search orders, SKUs..." id="order-search"/>
        </div>
    </div>
    
    <!-- Bulk Actions Bar (appears when rows selected) -->
    <div class="bulk-actions-bar hidden" id="bulk-actions">
        <div class="selection-info">
            <span id="selected-count">0</span> orders selected
        </div>
        <div class="actions">
            <button class="btn-secondary btn-sm" onclick="bulkExclude()">
                <span class="material-symbols-outlined">block</span>
                Exclude
            </button>
            <button class="btn-secondary btn-sm" onclick="bulkInclude()">
                <span class="material-symbols-outlined">check_circle</span>
                Include
            </button>
            <button class="btn-danger btn-sm" onclick="bulkDelete()">
                <span class="material-symbols-outlined">delete</span>
                Delete
            </button>
        </div>
    </div>
    
    <!-- Orders Table -->
    <div class="table-container">
        <table class="orders-table">
            <thead>
                <tr>
                    <th class="checkbox-col">
                        <input type="checkbox" id="select-all"/>
                    </th>
                    <th>SO NUM</th>
                    <th>CUSTOMER</th>
                    <th>DESTINATION</th>
                    <th>DUE DATE</th>
                    <th>ITEMS</th>
                    <th>LENGTH</th>
                    <th>UTIL %</th>
                    <th>STATUS</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                {% for order in orders %}
                <tr class="order-row {% if order.is_excluded %}excluded{% endif %}" 
                    data-order-id="{{ order.so_num }}">
                    <td>
                        <input type="checkbox" class="row-select" 
                               value="{{ order.so_num }}"/>
                    </td>
                    <td class="order-id">{{ order.so_num }}</td>
                    <td>{{ order.cust_name }}</td>
                    <td>{{ order.city }}, {{ order.state }}</td>
                    <td>{{ order.due_date|date }}</td>
                    <td>
                        <button class="btn-link" onclick="expandOrder('{{ order.so_num }}')">
                            <span class="material-symbols-outlined expand-icon">expand_more</span>
                            {{ order.line_count }}
                        </button>
                    </td>
                    <td>{{ order.total_length_ft|round(1) }} ft</td>
                    <td>
                        <div class="util-badge {{ order.utilization_grade|lower }}">
                            {{ order.utilization_pct|round(0) }}%
                        </div>
                    </td>
                    <td>
                        {% if order.is_excluded %}
                        <span class="badge badge-neutral">EXCLUDED</span>
                        {% elif order.exceeds_capacity %}
                        <span class="badge badge-danger">OVERSIZED</span>
                        {% else %}
                        <span class="badge badge-ready">READY</span>
                        {% endif %}
                    </td>
                    <td>
                        <button class="btn-icon" onclick="toggleOrderMenu('{{ order.so_num }}')">
                            <span class="material-symbols-outlined">more_vert</span>
                        </button>
                    </td>
                </tr>
                
                <!-- Expandable Row Content -->
                <tr class="expanded-content hidden" id="expanded-{{ order.so_num }}">
                    <td colspan="10">
                        <div class="order-details">
                            <!-- Line items table + stack schematic loaded via AJAX -->
                            <div id="order-detail-{{ order.so_num }}"></div>
                        </div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    
    <div class="table-footer">
        Showing {{ orders|length }} orders
    </div>
</div>
```

**Right Sidebar (35%): Upload + Last Upload Summary + Quick Optimizer**

```html
<div class="orders-sidebar">
    <!-- Upload Widget -->
    <div class="upload-widget">
        <div class="widget-header">
            <h3>
                <span class="material-symbols-outlined">cloud_upload</span>
                Upload Orders
            </h3>
        </div>
        
        <div class="upload-dropzone" id="upload-dropzone">
            <span class="material-symbols-outlined upload-icon">cloud_upload</span>
            <h4>Drag CSV file here</h4>
            <p>or click to browse</p>
            <input type="file" id="file-input" accept=".csv,.xlsx" hidden/>
            <button class="btn-primary btn-sm" onclick="document.getElementById('file-input').click()">
                Choose File
            </button>
        </div>
        
        <!-- Upload Progress (hidden by default) -->
        <div class="upload-progress hidden" id="upload-progress">
            <div class="progress-header">
                <span>Uploading...</span>
                <span class="progress-pct" id="progress-pct">0%</span>
            </div>
            <div class="progress-bar">
                <div class="progress-fill" id="progress-fill"></div>
            </div>
            <div class="progress-steps">
                <div class="step" id="step-1">⟳ Validating file...</div>
                <div class="step" id="step-2">○ Mapping SKUs...</div>
                <div class="step" id="step-3">○ Calculating utilizations...</div>
            </div>
        </div>
    </div>
    
    <!-- Last Upload Summary -->
    <div class="last-upload-widget">
        <div class="widget-header">
            <h3>Last Upload Summary</h3>
            <span class="upload-date">{{ last_upload.date|datetime }}</span>
        </div>
        <div class="upload-stats">
            <div class="stat">
                <span class="stat-label">Orders Imported</span>
                <span class="stat-value">{{ last_upload.total_orders }}</span>
            </div>
            <div class="stat">
                <span class="stat-label">SKU Mapping</span>
                <span class="stat-value success">{{ last_upload.mapping_rate }}%</span>
            </div>
            <div class="stat">
                <span class="stat-label">Unmapped Items</span>
                <span class="stat-value {% if last_upload.unmapped > 0 %}warning{% endif %}">
                    {{ last_upload.unmapped }}
                </span>
            </div>
        </div>
        {% if last_upload.unmapped > 0 %}
        <button class="btn-link" onclick="showUnmappedItems()">
            View Unmapped Items →
        </button>
        {% endif %}
    </div>
    
    <!-- Quick Optimizer Panel -->
    <div class="quick-optimizer-widget">
        <div class="widget-header">
            <h3>
                <span class="material-symbols-outlined">auto_fix</span>
                Quick Optimizer
            </h3>
        </div>
        <div class="optimizer-form">
            <div class="form-group">
                <label>Plant</label>
                <select id="opt-plant" class="form-select">
                    <option value="GA">GA - Atlanta</option>
                    <option value="IA">IA - Iowa</option>
                    <option value="TX">TX - Dallas</option>
                    <option value="VA">VA - Richmond</option>
                </select>
            </div>
            <div class="form-group">
                <label>Flexibility (days)</label>
                <input type="number" id="opt-flex" class="form-input" value="7" min="0" max="30"/>
            </div>
            <div class="form-group">
                <label>Max Detour</label>
                <input type="number" id="opt-detour" class="form-input" value="15" min="0" max="50"/>
                <span class="input-suffix">%</span>
            </div>
            <button class="btn-primary btn-block" onclick="runQuickOptimizer()">
                <span class="material-symbols-outlined">play_arrow</span>
                Run Optimizer
            </button>
        </div>
        
        <!-- Optimizer Results Preview (after running) -->
        <div class="optimizer-results hidden" id="opt-results">
            <div class="results-summary">
                <div class="result-metric">
                    <span class="metric-label">Loads Created</span>
                    <span class="metric-value" id="opt-loads">18</span>
                </div>
                <div class="result-metric">
                    <span class="metric-label">Avg Utilization</span>
                    <span class="metric-value success" id="opt-util">84%</span>
                </div>
                <div class="result-metric">
                    <span class="metric-label">Est. Savings</span>
                    <span class="metric-value success" id="opt-savings">$3,720</span>
                </div>
            </div>
            <button class="btn-secondary btn-block" onclick="viewOptimizedLoads()">
                View Optimized Loads →
            </button>
        </div>
    </div>
</div>
```

**Backend Changes**:

- Merge `/upload` functionality into `/orders` route
- Add `/api/orders/upload` endpoint that returns progress updates
- Track last upload in database or session
- Quick optimizer runs and redirects to `/loads` page with results

---

### Page 3: Loads (Enhanced Detail View)

**Route**: `/loads`

**Purpose**: View all loads with detailed drill-down including color-coded load schematics

#### Key Changes from Current

1. **Load List**: Card-based instead of table for better visual hierarchy
2. **Detail View**: Click load → full-page detail view with timeline and schematic
3. **Color-Coded Schematic**: Visual load sheet showing position/stack with order colors
4. **Interactive Manifest**: Drag-drop reordering (from previous PRD)

#### Layout: Load List View

```html
<div class="loads-page">
    <div class="page-header">
        <h1>Active Loads</h1>
        <div class="header-actions">
            <button class="btn-secondary">
                <span class="material-symbols-outlined">filter_list</span>
                Filter
            </button>
            <button class="btn-secondary">
                <span class="material-symbols-outlined">sort</span>
                Sort By
            </button>
            <button class="btn-primary" onclick="createNewLoad()">
                <span class="material-symbols-outlined">add</span>
                New Load
            </button>
        </div>
    </div>
    
    <div class="loads-grid">
        {% for load in loads %}
        <div class="load-card" onclick="viewLoadDetail('{{ load.id }}')">
            <div class="load-card-header">
                <div>
                    <h3 class="load-id">#L-{{ load.id|pad(4) }}</h3>
                    <p class="load-route">{{ load.origin_plant }} → {{ load.destination_state }}</p>
                </div>
                <span class="badge badge-{{ load.status|lower }}">{{ load.status }}</span>
            </div>
            
            <div class="load-metrics">
                <div class="metric">
                    <span class="metric-icon">📦</span>
                    <div>
                        <span class="metric-label">Orders</span>
                        <span class="metric-value">{{ load.order_count }}</span>
                    </div>
                </div>
                <div class="metric">
                    <span class="metric-icon">📏</span>
                    <div>
                        <span class="metric-label">Utilization</span>
                        <span class="metric-value">{{ load.utilization_pct|round(0) }}%</span>
                    </div>
                </div>
                <div class="metric">
                    <span class="metric-icon">🛣️</span>
                    <div>
                        <span class="metric-label">Miles</span>
                        <span class="metric-value">{{ load.estimated_miles|round(0) }}</span>
                    </div>
                </div>
                <div class="metric">
                    <span class="metric-icon">💵</span>
                    <div>
                        <span class="metric-label">Cost</span>
                        <span class="metric-value">${{ load.estimated_cost|round(2) }}</span>
                    </div>
                </div>
            </div>
            
            <div class="utilization-preview">
                <div class="util-bar">
                    <div class="util-fill {{ load.utilization_grade|lower }}" 
                         style="width: {{ load.utilization_pct }}%">
                    </div>
                </div>
            </div>
            
            <div class="load-card-footer">
                <span class="created-date">Created {{ load.created_at|timeago }}</span>
                <button class="btn-link" onclick="event.stopPropagation(); viewLoadDetail('{{ load.id }}')">
                    View Details →
                </button>
            </div>
        </div>
        {% endfor %}
    </div>
</div>
```

#### Layout: Load Detail View (Full Page)

**Route**: `/loads/<load_id>`

**Inspired by Screenshot #2**: Timeline at top, manifest table, utilization sidebar, load schematic

```html
<div class="load-detail-page">
    <!-- Breadcrumbs + Header -->
    <div class="breadcrumbs">
        <a href="/dashboard">Dashboard</a>
        <span class="separator">›</span>
        <a href="/loads">Loads</a>
        <span class="separator">›</span>
        <span class="current">Load #L-9842</span>
    </div>
    
    <div class="load-detail-header">
        <div class="header-left">
            <h1>
                #L-9842
                <span class="badge badge-planning">IN PLANNING</span>
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
    
    <!-- Horizontal Timeline (from screenshot) -->
    <div class="route-timeline">
        <div class="timeline-track"></div>
        
        <div class="timeline-stop active">
            <div class="stop-icon">
                <span class="material-symbols-outlined">warehouse</span>
            </div>
            <div class="stop-info">
                <h4>Warehouse A (Origin)</h4>
                <p class="time">08:00 AM - 10:00 AM</p>
            </div>
        </div>
        
        <div class="timeline-stop">
            <div class="stop-icon">
                <span class="material-symbols-outlined">local_shipping</span>
            </div>
            <div class="stop-info">
                <h4>Crossdock B</h4>
                <p class="time">01:00 PM - 02:00 PM</p>
            </div>
        </div>
        
        <div class="timeline-stop">
            <div class="stop-icon">
                <span class="material-symbols-outlined">person</span>
            </div>
            <div class="stop-info">
                <h4>Customer C (Dest)</h4>
                <p class="time">04:30 PM (ETA)</p>
            </div>
        </div>
    </div>
    
    <!-- Main Content: Manifest Table + Sidebar -->
    <div class="load-detail-content">
        <!-- Left: Manifest Table -->
        <div class="manifest-section">
            <div class="section-header">
                <h3>
                    <span class="material-symbols-outlined">list_alt</span>
                    Load Manifest Adjustment
                </h3>
                <div class="header-actions">
                    <button class="btn-icon">
                        <span class="material-symbols-outlined">download</span>
                    </button>
                    <button class="btn-icon">
                        <span class="material-symbols-outlined">settings</span>
                    </button>
                </div>
            </div>
            
            <div class="manifest-table-container">
                <table class="manifest-table">
                    <thead>
                        <tr>
                            <th class="drag-col"></th>
                            <th>ORDER</th>
                            <th>SKU/ITEM ID</th>
                            <th>DESCRIPTION</th>
                            <th>QTY</th>
                            <th>WEIGHT (LBS)</th>
                            <th>PALLETS</th>
                            <th>STATUS</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody class="sortable">
                        {% for item in load.manifest %}
                        <tr class="manifest-row" draggable="true" data-order="{{ item.order_id }}">
                            <td class="drag-handle">
                                <span class="material-symbols-outlined">drag_indicator</span>
                            </td>
                            <td class="order-num">#{{ loop.index }}</td>
                            <td class="sku">{{ item.sku }}</td>
                            <td class="description">{{ item.description }}</td>
                            <td>
                                <input type="number" class="qty-input" value="{{ item.qty }}" 
                                       onchange="updateManifest('{{ item.id }}', this.value)"/>
                            </td>
                            <td class="weight">{{ item.weight|format_number }}</td>
                            <td class="pallets">{{ item.pallets }}</td>
                            <td class="status">
                                <span class="status-dot {{ item.status }}"></span>
                            </td>
                            <td>
                                <button class="btn-icon btn-delete" onclick="removeFromLoad('{{ item.id }}')">
                                    <span class="material-symbols-outlined">delete</span>
                                </button>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            
            <div class="manifest-footer">
                <div>Showing {{ load.manifest|length }} Items</div>
                <div class="footer-totals">
                    <span>Total Weight: <strong>{{ load.total_weight|format_number }} lbs</strong></span>
                    <span>Total Pallets: <strong>{{ load.total_pallets }} / 28</strong></span>
                </div>
            </div>
            
            <!-- Load Schematic (Color-coded by Order) -->
            <div class="load-schematic-section">
                <div class="section-header">
                    <h3>
                        <span class="material-symbols-outlined">view_in_ar</span>
                        Load Schematic
                    </h3>
                    <div class="schematic-legend">
                        {% for order in load.orders %}
                        <div class="legend-item">
                            <span class="color-box" style="background: {{ order.color }}"></span>
                            <span>Order {{ order.so_num }}</span>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                
                <div class="trailer-schematic">
                    <div class="trailer-container">
                        <div class="trailer-label">53 ft Trailer Bed</div>
                        <div class="trailer-positions">
                            {% for position in load.schematic %}
                            <div class="position-column">
                                <div class="position-label">
                                    Pos {{ position.number }}
                                </div>
                                <div class="position-stack">
                                    {% for unit in position.units %}
                                    <div class="unit-block" 
                                         style="background: {{ unit.order_color }}"
                                         title="{{ unit.order_id }} - {{ unit.sku }}">
                                        <span class="unit-label">{{ unit.sku_short }}</span>
                                    </div>
                                    {% endfor %}
                                </div>
                                <div class="position-footer">
                                    <span>{{ position.length_ft }} ft</span>
                                    <span>({{ position.units|length }})</span>
                                </div>
                            </div>
                            {% endfor %}
                        </div>
                        <div class="trailer-utilization">
                            <div class="util-bar">
                                <div class="util-fill" style="width: {{ load.utilization_pct }}%"></div>
                            </div>
                            <div class="util-label">
                                {{ load.total_length_ft }} ft of 53 ft capacity ({{ load.utilization_pct }}% utilized)
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Right: Utilization Sidebar -->
        <div class="utilization-sidebar">
            <div class="sidebar-card">
                <h3>
                    <span class="material-symbols-outlined">monitoring</span>
                    Utilization Insights
                </h3>
                
                <!-- Utilization Meters -->
                <div class="meter-group">
                    <div class="utilization-meter">
                        <div class="meter-header">
                            <span class="meter-label">FLOOR SPACE USAGE</span>
                            <span class="meter-value">88%</span>
                        </div>
                        <div class="meter-bar">
                            <div class="meter-fill high" style="width: 88%"></div>
                        </div>
                        <div class="meter-footer">
                            24 of 28 standard pallet spots occupied
                        </div>
                    </div>
                    
                    <div class="utilization-meter">
                        <div class="meter-header">
                            <span class="meter-label">WEIGHT CAPACITY</span>
                            <span class="meter-value">58%</span>
                        </div>
                        <div class="meter-bar">
                            <div class="meter-fill medium" style="width: 58%"></div>
                        </div>
                        <div class="meter-footer">
                            26,150 lbs of 45,000 lbs max payload
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- KPI Cards -->
            <div class="kpi-cards-mini">
                <div class="kpi-card-mini">
                    <span class="kpi-label">COST PER MILE</span>
                    <span class="kpi-value">$2.45</span>
                    <div class="kpi-trend positive">
                        <span class="material-symbols-outlined">trending_down</span>
                        4.2%
                    </div>
                </div>
                <div class="kpi-card-mini">
                    <span class="kpi-label">EST. FUEL</span>
                    <span class="kpi-value">$642.00</span>
                    <span class="kpi-note">Based on $3.89/gal</span>
                </div>
            </div>
            
            <!-- Map Preview -->
            <div class="sidebar-card">
                <h3>MAP PREVIEW</h3>
                <div class="map-preview">
                    <!-- Map placeholder -->
                    <div class="map-embed" id="route-map">
                        <div class="map-placeholder">
                            <span class="material-symbols-outlined">map</span>
                            <p>Route map</p>
                            <small>Google Maps integration needed</small>
                        </div>
                    </div>
                </div>
                <button class="btn-link">View Full Map Details →</button>
            </div>
            
            <!-- Route Optimization Alert -->
            <div class="alert-card info">
                <span class="material-symbols-outlined">info</span>
                <div>
                    <h4>Route Optimization Available</h4>
                    <p>We found a more efficient route through Crossdock B that saves 14 miles.</p>
                    <button class="btn-link">Apply Suggestion →</button>
                </div>
            </div>
        </div>
    </div>
</div>
```

**Key Feature: Color-Coded Load Schematic**

The schematic assigns each order a unique color and shows exactly how items are positioned and stacked:

```python
# Backend: services/load_schematic.py

ORDER_COLORS = [
    '#3b82f6',  # blue
    '#10b981',  # green
    '#f59e0b',  # amber
    '#ef4444',  # red
    '#8b5cf6',  # purple
    '#ec4899',  # pink
    '#14b8a6',  # teal
    '#f97316',  # orange
]

def generate_load_schematic(load_id):
    """
    Generate visual schematic for load showing position/stack with order colors
    
    Returns:
        {
            'positions': [
                {
                    'number': 1,
                    'length_ft': 14.0,
                    'units': [
                        {
                            'order_id': 'ORD-8821',
                            'order_color': '#3b82f6',
                            'sku': 'MFG-2994-A',
                            'sku_short': 'MFG',
                            'stack_level': 1
                        },
                        {
                            'order_id': 'ORD-8821',
                            'order_color': '#3b82f6',
                            'sku': 'MFG-2994-A',
                            'sku_short': 'MFG',
                            'stack_level': 2
                        }
                    ]
                },
                ...
            ],
            'orders': [
                {
                    'so_num': 'ORD-8821',
                    'color': '#3b82f6',
                    'customer': 'Acme Corp'
                },
                ...
            ]
        }
    """
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # Get load lines with order info
    cursor.execute('''
        SELECT 
            ll.id,
            ll.order_so_num,
            ol.item as sku,
            ol.qty,
            ol.unit_length_ft,
            ol.max_stack_height,
            o.cust_name
        FROM load_lines ll
        JOIN order_lines ol ON ll.order_so_num = ol.so_num
        JOIN orders o ON ol.so_num = o.so_num
        WHERE ll.load_id = ?
        ORDER BY ol.unit_length_ft, ll.id
    ''', (load_id,))
    
    lines = cursor.fetchall()
    
    # Assign colors to orders
    order_colors = {}
    unique_orders = list(set(line[1] for line in lines))
    for idx, order_so_num in enumerate(unique_orders):
        order_colors[order_so_num] = ORDER_COLORS[idx % len(ORDER_COLORS)]
    
    # Build schematic using same stacking logic as stack_calculator
    from services.stack_calculator import calculate_stack_configuration
    
    # Convert to order-level format for stack calculator
    order_data = []
    for line in lines:
        order_data.append({
            'order_so_num': line[1],
            'sku': line[2],
            'qty': line[3],
            'unit_length_ft': line[4],
            'max_stack_height': line[5],
            'customer': line[6],
            'color': order_colors[line[1]]
        })
    
    # Calculate positions
    stack_config = calculate_stack_configuration(order_data)
    
    # Format for UI
    positions = []
    for idx, pos in enumerate(stack_config['positions']):
        position = {
            'number': idx + 1,
            'length_ft': pos['length_ft'],
            'units': []
        }
        
        for item in pos['items']:
            for unit_num in range(item['units']):
                position['units'].append({
                    'order_id': item['order_so_num'],
                    'order_color': item['color'],
                    'sku': item['sku'],
                    'sku_short': item['sku'][:3],  # First 3 chars
                    'stack_level': unit_num + 1
                })
        
        positions.append(position)
    
    # Build orders list for legend
    orders = [
        {
            'so_num': order_so_num,
            'color': color,
            'customer': next((line[6] for line in lines if line[1] == order_so_num), '')
        }
        for order_so_num, color in order_colors.items()
    ]
    
    return {
        'positions': positions,
        'orders': orders
    }
```

---

### Page 4: Settings (Consolidated Rates + SKUs + Lookups)

**Route**: `/settings`

**Purpose**: All reference data management in one tabbed interface

#### Layout Structure

**Tabbed Interface**:

```html
<div class="settings-page">
    <div class="page-header">
        <h1>Settings</h1>
        <p class="page-subtitle">Manage reference data and configuration</p>
    </div>
    
    <div class="settings-tabs">
        <button class="tab-button active" data-tab="rates">
            <span class="material-symbols-outlined">attach_money</span>
            Rate Matrix
        </button>
        <button class="tab-button" data-tab="skus">
            <span class="material-symbols-outlined">inventory_2</span>
            SKU Specifications
        </button>
        <button class="tab-button" data-tab="lookups">
            <span class="material-symbols-outlined">find_replace</span>
            Item Lookups
        </button>
        <button class="tab-button" data-tab="plants">
            <span class="material-symbols-outlined">factory</span>
            Plants
        </button>
    </div>
    
    <!-- Tab 1: Rate Matrix -->
    <div class="tab-content active" id="tab-rates">
        <div class="content-header">
            <h2>Freight Rate Matrix</h2>
            <div class="actions">
                <button class="btn-secondary">
                    <span class="material-symbols-outlined">upload</span>
                    Import from Excel
                </button>
                <button class="btn-secondary">
                    <span class="material-symbols-outlined">download</span>
                    Export CSV
                </button>
                <button class="btn-primary">
                    <span class="material-symbols-outlined">add</span>
                    Add Rate
                </button>
            </div>
        </div>
        
        <div class="table-container">
            <table class="settings-table">
                <thead>
                    <tr>
                        <th>ORIGIN PLANT</th>
                        <th>DESTINATION STATE</th>
                        <th>RATE PER MILE</th>
                        <th>EFFECTIVE YEAR</th>
                        <th>LAST UPDATED</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    <!-- Rate rows... -->
                </tbody>
            </table>
        </div>
    </div>
    
    <!-- Tab 2: SKU Specifications -->
    <div class="tab-content" id="tab-skus">
        <div class="content-header">
            <h2>SKU Specifications</h2>
            <div class="actions">
                <button class="btn-secondary">
                    <span class="material-symbols-outlined">upload</span>
                    Import Cheat Sheet
                </button>
                <button class="btn-primary">
                    <span class="material-symbols-outlined">add</span>
                    Add SKU
                </button>
            </div>
        </div>
        
        <div class="table-container">
            <table class="settings-table">
                <thead>
                    <tr>
                        <th>SKU</th>
                        <th>CATEGORY</th>
                        <th>LENGTH (ft)</th>
                        <th>MAX STACK (STEP DECK)</th>
                        <th>MAX STACK (FLAT BED)</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    <!-- SKU rows... -->
                </tbody>
            </table>
        </div>
    </div>
    
    <!-- Tab 3: Item Lookups -->
    <div class="tab-content" id="tab-lookups">
        <div class="content-header">
            <h2>Item to SKU Lookup Rules</h2>
            <div class="actions">
                <button class="btn-primary">
                    <span class="material-symbols-outlined">add</span>
                    Add Lookup Rule
                </button>
            </div>
        </div>
        
        <div class="table-container">
            <table class="settings-table">
                <thead>
                    <tr>
                        <th>PLANT</th>
                        <th>BIN</th>
                        <th>ITEM PATTERN</th>
                        <th>MAPS TO SKU</th>
                        <th>USAGE COUNT</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    <!-- Lookup rows... -->
                </tbody>
            </table>
        </div>
    </div>
    
    <!-- Tab 4: Plants -->
    <div class="tab-content" id="tab-plants">
        <div class="content-header">
            <h2>Plant Locations</h2>
            <div class="actions">
                <button class="btn-primary">
                    <span class="material-symbols-outlined">add</span>
                    Add Plant
                </button>
            </div>
        </div>
        
        <div class="table-container">
            <table class="settings-table">
                <thead>
                    <tr>
                        <th>PLANT CODE</th>
                        <th>NAME</th>
                        <th>LATITUDE</th>
                        <th>LONGITUDE</th>
                        <th>STATUS</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    <!-- Plant rows... -->
                </tbody>
            </table>
        </div>
    </div>
</div>
```

**Backend**: Existing routes for `/rates`, `/skus`, `/lookups` remain but are rendered within tabbed interface

---

## Part 3: Color Scheme & Dark Theme

### Color Palette (from Screenshots)

**Background Colors**:
```css
--bg-darkest: #0f172a;      /* Page background */
--bg-dark: #1e293b;         /* Card backgrounds */
--bg-medium: #334155;       /* Hover states */
--bg-light: #475569;        /* Borders, dividers */
```

**Accent Colors**:
```css
--primary-blue: #137fec;    /* Primary buttons, links, active states */
--success-green: #10b981;   /* Success badges, positive trends */
--warning-amber: #f59e0b;   /* Warning badges, late orders */
--danger-red: #ef4444;      /* Error states, overweight */
--neutral-gray: #64748b;    /* Secondary text, disabled states */
```

**Text Colors**:
```css
--text-primary: #f1f5f9;    /* Main headings, body text */
--text-secondary: #94a3b8;  /* Labels, meta info */
--text-tertiary: #64748b;   /* Muted text, placeholders */
```

**Status Colors**:
```css
--status-ready: #10b981;    /* Green - ready to ship */
--status-late: #f59e0b;     /* Amber - late order */
--status-expedited: #ef4444; /* Red - urgent */
--status-staged: #3b82f6;   /* Blue - staged/in-progress */
--status-neutral: #64748b;  /* Gray - neutral/draft */
```

### CSS Variables Setup

```css
/* File: static/styles.css */

:root {
    /* Dark Theme Colors */
    --bg-darkest: #0f172a;
    --bg-dark: #1e293b;
    --bg-medium: #334155;
    --bg-light: #475569;
    
    /* Primary Colors */
    --primary: #137fec;
    --primary-hover: #0e6fd9;
    --primary-dark: #0b5bb8;
    
    /* Accent Colors */
    --success: #10b981;
    --warning: #f59e0b;
    --danger: #ef4444;
    --info: #3b82f6;
    
    /* Text Colors */
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --text-tertiary: #64748b;
    
    /* Status Colors */
    --status-ready: var(--success);
    --status-late: var(--warning);
    --status-expedited: var(--danger);
    --status-staged: var(--info);
    --status-neutral: var(--text-tertiary);
    
    /* Utilization Grades */
    --util-low: var(--danger);
    --util-medium: var(--warning);
    --util-good: #8b5cf6;
    --util-high: var(--primary);
    
    /* Spacing */
    --spacing-xs: 4px;
    --spacing-sm: 8px;
    --spacing-md: 16px;
    --spacing-lg: 24px;
    --spacing-xl: 32px;
    
    /* Border Radius */
    --radius-sm: 4px;
    --radius-md: 8px;
    --radius-lg: 12px;
    --radius-xl: 16px;
    
    /* Shadows */
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3);
    --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.4);
    --shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.5);
    
    /* Typography */
    --font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-mono: 'SF Mono', Monaco, 'Cascadia Code', monospace;
}

body {
    margin: 0;
    padding: 0;
    font-family: var(--font-family);
    background: var(--bg-darkest);
    color: var(--text-primary);
    font-size: 14px;
    line-height: 1.5;
}

/* Global resets */
* {
    box-sizing: border-box;
}

button, input, select, textarea {
    font-family: inherit;
    font-size: inherit;
}

/* Top Navigation */
.top-nav {
    background: var(--bg-dark);
    border-bottom: 1px solid var(--bg-light);
    padding: 0 var(--spacing-lg);
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 64px;
}

.nav-logo {
    display: flex;
    align-items: center;
    gap: var(--spacing-md);
    color: var(--primary);
    font-weight: 800;
    font-size: 20px;
}

.nav-tabs {
    display: flex;
    gap: var(--spacing-lg);
}

.nav-tab {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-sm) var(--spacing-md);
    color: var(--text-secondary);
    text-decoration: none;
    font-weight: 500;
    border-bottom: 2px solid transparent;
    transition: all 0.2s;
}

.nav-tab:hover {
    color: var(--text-primary);
}

.nav-tab.active {
    color: var(--primary);
    border-bottom-color: var(--primary);
}

/* KPI Cards */
.kpi-card {
    background: linear-gradient(135deg, var(--bg-dark) 0%, var(--bg-medium) 100%);
    border: 1px solid var(--bg-light);
    border-radius: var(--radius-lg);
    padding: var(--spacing-lg);
    display: flex;
    flex-direction: column;
    gap: var(--spacing-sm);
}

.kpi-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.kpi-label {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-secondary);
}

.kpi-value {
    font-size: 36px;
    font-weight: 900;
    color: var(--text-primary);
    line-height: 1;
}

.kpi-unit {
    font-size: 16px;
    font-weight: 600;
    color: var(--text-tertiary);
    margin-left: 4px;
}

.kpi-trend {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    font-weight: 700;
}

.kpi-trend.positive {
    color: var(--success);
}

.kpi-trend.negative {
    color: var(--danger);
}

/* Badges */
.badge {
    display: inline-flex;
    align-items: center;
    padding: 4px 12px;
    border-radius: 9999px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border: 1px solid transparent;
}

.badge-ready {
    background: rgba(16, 185, 129, 0.1);
    color: var(--success);
    border-color: rgba(16, 185, 129, 0.2);
}

.badge-late {
    background: rgba(245, 158, 11, 0.1);
    color: var(--warning);
    border-color: rgba(245, 158, 11, 0.2);
}

.badge-expedited {
    background: rgba(239, 68, 68, 0.1);
    color: var(--danger);
    border-color: rgba(239, 68, 68, 0.2);
}

.badge-staged {
    background: rgba(59, 130, 246, 0.1);
    color: var(--info);
    border-color: rgba(59, 130, 246, 0.2);
}

.badge-neutral, .badge-draft {
    background: rgba(100, 116, 139, 0.1);
    color: var(--text-tertiary);
    border-color: rgba(100, 116, 139, 0.2);
}

/* Tables */
.dashboard-table,
.orders-table,
.manifest-table,
.settings-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}

.dashboard-table thead,
.orders-table thead,
.manifest-table thead,
.settings-table thead {
    background: var(--bg-dark);
    border-bottom: 1px solid var(--bg-light);
}

.dashboard-table th,
.orders-table th,
.manifest-table th,
.settings-table th {
    padding: 12px;
    text-align: left;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-secondary);
}

.dashboard-table tbody tr,
.orders-table tbody tr,
.manifest-table tbody tr,
.settings-table tbody tr {
    border-bottom: 1px solid var(--bg-light);
    transition: background 0.2s;
}

.dashboard-table tbody tr:hover,
.orders-table tbody tr:hover,
.manifest-table tbody tr:hover,
.settings-table tbody tr:hover {
    background: rgba(255, 255, 255, 0.03);
}

.dashboard-table td,
.orders-table td,
.manifest-table td,
.settings-table td {
    padding: 12px;
}

.table-row.clickable {
    cursor: pointer;
}

/* Utilization Meters */
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
    color: var(--text-secondary);
}

.meter-value {
    font-size: 24px;
    font-weight: 900;
    color: var(--primary);
}

.meter-bar {
    height: 12px;
    width: 100%;
    background: var(--bg-medium);
    border-radius: 6px;
    overflow: hidden;
}

.meter-fill {
    height: 100%;
    border-radius: 6px;
    transition: width 0.5s ease;
}

.meter-fill.low {
    background: linear-gradient(90deg, var(--danger) 0%, #f87171 100%);
}

.meter-fill.medium {
    background: linear-gradient(90deg, var(--warning) 0%, #fbbf24 100%);
}

.meter-fill.good {
    background: linear-gradient(90deg, #8b5cf6 0%, #a78bfa 100%);
}

.meter-fill.high {
    background: linear-gradient(90deg, var(--primary) 0%, #3b82f6 100%);
}

.meter-footer {
    font-size: 10px;
    color: var(--text-tertiary);
}

/* Buttons */
.btn-primary {
    background: var(--primary);
    color: white;
    border: none;
    padding: 10px 20px;
    border-radius: var(--radius-md);
    font-weight: 600;
    font-size: 14px;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    transition: all 0.2s;
}

.btn-primary:hover {
    background: var(--primary-hover);
    box-shadow: var(--shadow-md);
}

.btn-secondary {
    background: var(--bg-medium);
    color: var(--text-primary);
    border: 1px solid var(--bg-light);
    padding: 10px 20px;
    border-radius: var(--radius-md);
    font-weight: 600;
    font-size: 14px;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    transition: all 0.2s;
}

.btn-secondary:hover {
    background: var(--bg-light);
}

.btn-danger {
    background: var(--danger);
    color: white;
    border: none;
    padding: 10px 20px;
    border-radius: var(--radius-md);
    font-weight: 600;
    font-size: 14px;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    transition: all 0.2s;
}

.btn-icon {
    background: transparent;
    color: var(--text-secondary);
    border: none;
    padding: 8px;
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: all 0.2s;
    display: inline-flex;
    align-items: center;
    justify-content: center;
}

.btn-icon:hover {
    background: var(--bg-medium);
    color: var(--text-primary);
}

.btn-link {
    background: transparent;
    color: var(--primary);
    border: none;
    padding: 0;
    font-weight: 600;
    cursor: pointer;
    text-decoration: none;
    transition: color 0.2s;
}

.btn-link:hover {
    color: var(--primary-hover);
}

/* Material Icons */
.material-symbols-outlined {
    font-family: 'Material Symbols Outlined';
    font-weight: normal;
    font-style: normal;
    font-size: 20px;
    line-height: 1;
    letter-spacing: normal;
    text-transform: none;
    display: inline-block;
    white-space: nowrap;
    word-wrap: normal;
    direction: ltr;
    font-feature-settings: 'liga';
}

/* Load Schematic */
.trailer-schematic {
    background: var(--bg-dark);
    border: 2px solid var(--primary);
    border-radius: var(--radius-lg);
    padding: var(--spacing-lg);
    margin: var(--spacing-lg) 0;
}

.trailer-container {
    position: relative;
}

.trailer-label {
    text-align: center;
    font-size: 12px;
    font-weight: 700;
    color: var(--text-secondary);
    margin-bottom: var(--spacing-md);
}

.trailer-positions {
    display: flex;
    gap: 12px;
    align-items: flex-end;
    min-height: 200px;
    padding-bottom: var(--spacing-lg);
    border-bottom: 4px solid var(--text-tertiary);
}

.position-column {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    flex: 1;
}

.position-label {
    font-size: 11px;
    font-weight: 700;
    color: var(--text-secondary);
    text-align: center;
}

.position-stack {
    display: flex;
    flex-direction: column-reverse;
    gap: 3px;
    min-height: 40px;
}

.unit-block {
    width: 60px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm);
    border: 2px solid rgba(0, 0, 0, 0.3);
    cursor: pointer;
    transition: all 0.2s;
    font-size: 10px;
    font-weight: 700;
    color: white;
}

.unit-block:hover {
    transform: scale(1.08);
    box-shadow: var(--shadow-md);
    z-index: 10;
}

.position-footer {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    color: var(--text-tertiary);
}

.trailer-utilization {
    margin-top: var(--spacing-lg);
}

.util-bar {
    height: 24px;
    background: var(--bg-medium);
    border-radius: var(--radius-lg);
    overflow: hidden;
    position: relative;
}

.util-fill {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-weight: 700;
    font-size: 12px;
    transition: width 0.5s ease;
}

.util-label {
    text-align: center;
    margin-top: 8px;
    font-size: 12px;
    color: var(--text-secondary);
}

/* Responsive Design */
@media (max-width: 1024px) {
    .load-detail-content {
        flex-direction: column;
    }
    
    .utilization-sidebar {
        width: 100%;
    }
}
```

---

## Part 4: Backend Changes Summary

### Routes to Update

```python
# app.py modifications

# NEW: Dashboard route
@app.route('/')
@app.route('/dashboard')
def dashboard():
    # Implementation above
    pass

# MODIFIED: Orders page (merge upload functionality)
@app.route('/orders')
def orders():
    # Add last_upload summary
    # Keep existing orders table logic
    pass

# NEW: Quick optimizer API
@app.route('/api/optimize/quick', methods=['POST'])
def quick_optimize():
    # Run optimizer with minimal params
    # Return summary for sidebar display
    pass

# MODIFIED: Load detail page
@app.route('/loads/<load_id>')
def load_detail(load_id):
    # Add schematic generation
    from services.load_schematic import generate_load_schematic
    schematic = generate_load_schematic(load_id)
    # Render load detail with timeline + schematic
    pass

# NEW: Settings page (tabbed interface)
@app.route('/settings')
def settings():
    # Render tabbed settings page
    # Load initial tab data (rates)
    pass

# REMOVE: Separate /upload route (merged into /orders)
# REMOVE: Separate /optimize route (merged into /orders sidebar)
```

### Database Schema (No Major Changes)

Existing schema supports all new features. Minor additions:

```sql
-- Track last upload stats (optional)
CREATE TABLE IF NOT EXISTS upload_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    filename TEXT,
    total_rows INTEGER,
    total_orders INTEGER,
    mapping_rate REAL,
    unmapped_count INTEGER
);
```

---

## Part 5: Implementation Checklist

### Phase 1: Core Redesign (Week 1)

**Priority: Get 4-tab structure working**

- [ ] Create `/dashboard` route with KPI cards
- [ ] Add plant summary cards to dashboard
- [ ] Merge upload widget into `/orders` sidebar
- [ ] Add "Last Upload Summary" section
- [ ] Create `/settings` tabbed page
- [ ] Update navigation to 4 tabs

### Phase 2: Visual Redesign (Week 1-2)

**Priority: Apply dark theme and color scheme**

- [ ] Update `styles.css` with new color variables
- [ ] Apply dark background colors throughout
- [ ] Update all badges to use new color scheme
- [ ] Style KPI cards with gradients
- [ ] Update tables with dark theme
- [ ] Test visual consistency across pages

### Phase 3: Load Detail Enhancement (Week 2)

**Priority: Timeline + color-coded schematic**

- [ ] Create horizontal timeline component
- [ ] Implement `generate_load_schematic()` function
- [ ] Assign order colors (8-color palette)
- [ ] Build trailer schematic visual
- [ ] Position/stack rendering with colors
- [ ] Add schematic legend

### Phase 4: Interactive Features (Week 2-3)

**Priority: Upload progress, quick optimizer**

- [ ] Upload progress with AJAX
- [ ] Quick optimizer sidebar widget
- [ ] Optimizer results preview
- [ ] Bulk order selection
- [ ] Drag-drop manifest reordering
- [ ] Inline quantity editing

### Phase 5: Polish & Testing (Week 3)

- [ ] Responsive layout testing
- [ ] Cross-browser compatibility
- [ ] Performance optimization
- [ ] Error handling
- [ ] User testing with real data
- [ ] Documentation updates

---

## Part 6: Migration Guide

### For Existing Users

**Navigation Changes**:
- **Upload** tab → Now part of **Orders** page (right sidebar)
- **Optimize** tab → Now part of **Orders** page (sidebar widget)
- **Rates/SKUs/Lookups** tabs → Combined into **Settings** page

**Workflow Changes**:
- Upload now happens inline while viewing orders
- Optimization runs from sidebar, results appear on Loads page
- Settings consolidated for easier management

**Data Preservation**:
- All existing data preserved (orders, loads, rates, SKUs, lookups)
- No database migrations required (schema compatible)
- URL changes: `/upload` → `/orders`, `/optimize` → `/orders`

---

## Part 7: Acceptance Criteria

### Dashboard Page
- [ ] Shows 4 KPI cards with live data
- [ ] Displays recent orders table (10 rows)
- [ ] Shows 3 active load cards in sidebar
- [ ] Displays plant summary cards with metrics
- [ ] Map placeholder with order dots
- [ ] All links navigate correctly

### Orders Page
- [ ] Upload widget embedded in sidebar
- [ ] Drag-and-drop file upload works
- [ ] Progress states display correctly
- [ ] Last upload summary shows stats
- [ ] Quick optimizer sidebar functional
- [ ] Bulk selection and actions work
- [ ] Expandable rows show line items

### Loads Page (List)
- [ ] Card-based layout displays loads
- [ ] Utilization bars color-coded correctly
- [ ] Click card → navigates to detail view
- [ ] Filter and sort options work

### Load Detail Page
- [ ] Horizontal timeline displays stops
- [ ] Manifest table with drag-drop
- [ ] Color-coded load schematic visible
- [ ] Each order has unique color
- [ ] Units positioned and stacked correctly
- [ ] Legend shows order colors
- [ ] Utilization sidebar with meters
- [ ] Map placeholder visible

### Settings Page
- [ ] Tabbed interface works
- [ ] Rate matrix table displays
- [ ] SKU specifications table displays
- [ ] Item lookups table displays
- [ ] Plants table displays
- [ ] Import/export buttons functional

### Visual Design
- [ ] Dark theme applied consistently
- [ ] Color scheme matches screenshots
- [ ] Primary blue for actions/links
- [ ] Status badges use correct colors
- [ ] Utilization bars color-coded
- [ ] Typography consistent (Inter font)
- [ ] Spacing and padding consistent

### Performance
- [ ] Dashboard loads in <2 seconds
- [ ] Orders table handles 100+ rows smoothly
- [ ] Load detail page renders in <1 second
- [ ] Upload progress updates in real-time
- [ ] No JavaScript console errors

---

**END OF PRD**
