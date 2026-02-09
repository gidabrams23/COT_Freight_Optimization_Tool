# Fix: Stack Calculation Logic & UX Layout Issues

## Problem Statement

**Issue 1: Incorrect Utilization Calculation**
- Current logic creates one position per line item regardless of stacking capacity
- Not maximizing trailer utilization by combining items in the same position
- Example: 2 items with max_stack=4 should only take 0.5 positions (1 stack), not 2 positions

**Issue 2: UX Layout Problems**
- Stack visualization overflowing container
- Utilization bar extending beyond boundaries
- Elements not properly contained within cards
- Visual spacing issues

---

## Fix 1: Correct Stack Calculation Algorithm

### Current (Incorrect) Logic

```python
# WRONG - Creates separate position for each line item
for line in sorted_lines:
    qty_remaining = line.qty
    
    while qty_remaining > 0:
        units_in_position = min(qty_remaining, line.max_stack_height)
        positions.append({
            'item': line.item,
            'sku': line.sku,
            'unit_length_ft': line.unit_length_ft,
            'units_stacked': units_in_position,
            # ...
        })
        qty_remaining -= units_in_position
```

**Problem**: This creates one position per line item, never combining items from different line items into the same stack position.

### Corrected Logic

The key insight: **Each item occupies a fraction of a stack position** based on `qty / max_stack_height`.

**Example**:
- Item A: 2 units, max_stack=4 → occupies 0.5 of a position
- Item B: 1 unit, max_stack=2 → occupies 0.5 of a position
- **Together**: 0.5 + 0.5 = 1.0 position (they can share!)

### New Algorithm

**File**: `services/stack_calculator.py`

Replace the `calculate_stack_configuration()` function with this corrected version:

```python
import math
from collections import defaultdict

def calculate_stack_configuration(order_lines):
    """
    Calculate optimal stacking arrangement for an order.
    
    Key principle: Items share positions based on their stack capacity fraction.
    Each item occupies (qty / max_stack_height) of a position.
    
    Args:
        order_lines: List of line items with qty, unit_length_ft, max_stack_height
    
    Returns:
        {
            'positions': [...],
            'total_linear_feet': float,
            'utilization_pct': float,
            'max_stack_height': int,
            'compatibility_issues': [...],
            'exceeds_capacity': bool
        }
    """
    
    if not order_lines:
        return {
            'positions': [],
            'total_linear_feet': 0,
            'utilization_pct': 0,
            'max_stack_height': 0,
            'compatibility_issues': [],
            'exceeds_capacity': False
        }
    
    # Step 1: Group items by length (items of same length can share positions)
    length_groups = defaultdict(list)
    for line in order_lines:
        length_groups[line['unit_length_ft']].append(line)
    
    positions = []
    
    # Step 2: For each length group, pack items into positions
    for length_ft, items in length_groups.items():
        # Calculate total stack positions needed for this length group
        # Each item occupies (qty / max_stack) fraction of a position
        
        # Sort items by max_stack (largest first) to fill positions efficiently
        sorted_items = sorted(items, key=lambda x: x['max_stack_height'], reverse=True)
        
        current_position = {
            'length_ft': length_ft,
            'items': [],
            'capacity_used': 0.0,  # Tracks fraction of stack position used (0.0 to 1.0)
            'units_count': 0
        }
        
        for item in sorted_items:
            qty_remaining = item['qty']
            
            while qty_remaining > 0:
                # How much capacity is left in current position?
                capacity_available = 1.0 - current_position['capacity_used']
                
                # How many units can we fit in the remaining capacity?
                max_units_that_fit = int(capacity_available * item['max_stack_height'])
                
                if max_units_that_fit > 0:
                    # Add units to current position
                    units_to_add = min(qty_remaining, max_units_that_fit)
                    capacity_fraction = units_to_add / item['max_stack_height']
                    
                    current_position['items'].append({
                        'item': item['item'],
                        'sku': item['sku'],
                        'category': item.get('category', 'UNKNOWN'),
                        'units': units_to_add,
                        'max_stack': item['max_stack_height']
                    })
                    current_position['capacity_used'] += capacity_fraction
                    current_position['units_count'] += units_to_add
                    qty_remaining -= units_to_add
                    
                    # If position is full or close to full, start new position
                    if current_position['capacity_used'] >= 0.99:
                        positions.append(current_position)
                        current_position = {
                            'length_ft': length_ft,
                            'items': [],
                            'capacity_used': 0.0,
                            'units_count': 0
                        }
                else:
                    # Current position is full, start new one
                    if current_position['items']:
                        positions.append(current_position)
                    
                    current_position = {
                        'length_ft': length_ft,
                        'items': [],
                        'capacity_used': 0.0,
                        'units_count': 0
                    }
        
        # Add last position if it has items
        if current_position['items']:
            positions.append(current_position)
    
    # Step 3: Calculate totals
    total_linear_feet = sum(pos['length_ft'] for pos in positions)
    utilization_pct = (total_linear_feet / 53.0) * 100
    max_stack_height = max((pos['units_count'] for pos in positions), default=0)
    
    # Step 4: Check compatibility
    compatibility_issues = check_stacking_compatibility(positions)
    
    # Step 5: Check if exceeds single trailer capacity
    exceeds_capacity = total_linear_feet > 53.0
    
    return {
        'positions': positions,
        'total_linear_feet': round(total_linear_feet, 1),
        'utilization_pct': round(utilization_pct, 1),
        'max_stack_height': max_stack_height,
        'compatibility_issues': compatibility_issues,
        'exceeds_capacity': exceeds_capacity
    }


def check_stacking_compatibility(positions):
    """
    Check if all items can physically stack together.
    
    Returns list of warning messages, empty if compatible.
    """
    issues = []
    
    # Check each position
    for idx, pos in enumerate(positions):
        categories = [item['category'] for item in pos['items']]
        
        # Rule 1: DUMP cannot mix with other categories
        if 'DUMP' in categories and len(set(categories)) > 1:
            issues.append(f"⚠️ Position {idx+1}: DUMP trailers cannot mix with other types")
        
        # Rule 2: Check if stack height is reasonable
        if pos['units_count'] > 5:
            issues.append(f"⚠️ Position {idx+1}: Stack of {pos['units_count']} units may be unstable")
        
        # Rule 3: Check for wooden floor mixing
        skus = [item['sku'] for item in pos['items']]
        has_woody = any('WOODY' in sku for sku in skus)
        if has_woody and len(pos['items']) > 1:
            issues.append(f"ℹ️ Position {idx+1}: Mix includes wooden floor - verify compatibility")
    
    return issues
```

---

## Fix 2: Correct Visual Rendering

### Update JavaScript Rendering

**File**: `static/js/app.js` or inline in `templates/orders.html`

Replace the `renderStackVisualization()` function:

```javascript
function renderStackVisualization(orderId, config) {
    const container = document.getElementById(`stack-visual-${orderId}`);
    
    if (!config.positions || config.positions.length === 0) {
        container.innerHTML = '<div class="no-data">No stack data available</div>';
        return;
    }
    
    // Start trailer bed container
    let html = '<div class="trailer-bed">';
    
    // Render each position
    config.positions.forEach((position, idx) => {
        html += `
            <div class="stack-position">
                <div class="position-blocks">
        `;
        
        // Render items in this position (bottom to top)
        position.items.forEach((item, itemIdx) => {
            // Render each unit of this item
            for (let i = 0; i < item.units; i++) {
                const isStacked = position.units_count > 1;
                const blockClass = isStacked ? 'stacked' : 'single';
                const symbol = isStacked ? '▓▓' : '░░';
                
                html += `
                    <div class="unit-block ${blockClass}" 
                         title="${item.item} (${item.sku}) - Unit ${i+1} of ${item.units}">
                        <span class="block-symbol">${symbol}</span>
                    </div>
                `;
            }
        });
        
        html += `
                </div>
                <div class="position-label">
                    <div class="position-number">Pos ${idx + 1}</div>
                    <div class="position-length">${position.length_ft} ft</div>
                    <div class="position-count">(${position.units_count})</div>
                </div>
            </div>
        `;
    });
    
    html += '</div>'; // Close trailer-bed
    
    // Add utilization bar
    const utilClass = getUtilizationClass(config.utilization_pct);
    const cappedPct = Math.min(config.utilization_pct, 100);
    
    html += `
        <div class="utilization-bar-container">
            <div class="utilization-bar">
                <div class="utilization-fill ${utilClass}" 
                     style="width: ${cappedPct}%">
                    ${config.utilization_pct.toFixed(0)}% Used
                </div>
            </div>
            <div class="utilization-label">
                ${config.total_linear_feet} ft of 53 ft capacity
            </div>
        </div>
    `;
    
    container.innerHTML = html;
    
    // Update summary metrics
    updateSummaryMetrics(orderId, config);
}

function updateSummaryMetrics(orderId, config) {
    const summaryContainer = document.getElementById(`summary-${orderId}`);
    
    // Count total items
    const totalItems = config.positions.reduce((sum, pos) => {
        return sum + pos.items.reduce((itemSum, item) => itemSum + item.units, 0);
    }, 0);
    
    let html = `
        <div class="stack-summary">
            <div class="summary-metric">
                <span class="metric-label">Items:</span>
                <span class="metric-value">${totalItems}</span>
            </div>
            <div class="summary-metric">
                <span class="metric-label">Positions:</span>
                <span class="metric-value">${config.positions.length}</span>
            </div>
            <div class="summary-metric">
                <span class="metric-label">Total Length:</span>
                <span class="metric-value">${config.total_linear_feet} ft</span>
            </div>
            <div class="summary-metric">
                <span class="metric-label">Utilization:</span>
                <span class="metric-value ${getUtilizationClass(config.utilization_pct)}">
                    ${config.utilization_pct.toFixed(1)}%
                </span>
            </div>
            <div class="summary-metric">
                <span class="metric-label">Max Stack:</span>
                <span class="metric-value">${config.max_stack_height} units</span>
            </div>
    `;
    
    // Compatibility status
    if (config.compatibility_issues && config.compatibility_issues.length > 0) {
        html += `
            <div class="summary-metric compatibility-warning">
                <span class="metric-label">Compatibility:</span>
                <span class="metric-value">⚠️ ${config.compatibility_issues.length} issue(s)</span>
            </div>
        `;
        
        // Show issues
        config.compatibility_issues.forEach(issue => {
            html += `<div class="compatibility-issue">${issue}</div>`;
        });
    } else {
        html += `
            <div class="summary-metric compatibility-ok">
                <span class="metric-label">Compatible:</span>
                <span class="metric-value">✓ All items can stack</span>
            </div>
        `;
    }
    
    // Capacity warning
    if (config.exceeds_capacity) {
        html += `
            <div class="capacity-warning">
                ⚠️ Order exceeds single trailer capacity (requires ${Math.ceil(config.total_linear_feet / 53)} loads)
            </div>
        `;
    }
    
    html += '</div>';
    
    summaryContainer.innerHTML = html;
}

function getUtilizationClass(pct) {
    if (pct < 30) return 'low';
    if (pct < 70) return 'medium';
    if (pct < 85) return 'good';
    return 'high';
}
```

---

## Fix 3: CSS Layout Improvements

**File**: `static/css/main.css` or `static/styles.css`

Add/update these styles to fix layout issues:

```css
/* ===== EXPANDED ORDER SECTION ===== */

.expanded-order {
    background: #f8f9fa;
    border-top: 2px solid #e9ecef;
    padding: 24px;
    animation: slideDown 0.3s ease-out;
}

@keyframes slideDown {
    from {
        opacity: 0;
        transform: translateY(-10px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.order-details-header {
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid #dee2e6;
}

.order-details-header h3 {
    margin: 0 0 8px 0;
    color: #2D3436;
    font-size: 18px;
    font-weight: 600;
}

.order-meta {
    font-size: 13px;
    color: #636E72;
}

/* ===== LINE ITEMS TABLE ===== */

.line-items-section {
    margin-bottom: 24px;
}

.line-items-table {
    width: 100%;
    background: white;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.line-items-table table {
    width: 100%;
    border-collapse: collapse;
}

.line-items-table thead {
    background: #1B4965;
    color: white;
}

.line-items-table th {
    padding: 12px;
    text-align: left;
    font-weight: 600;
    font-size: 13px;
    white-space: nowrap;
}

.line-items-table td {
    padding: 10px 12px;
    border-bottom: 1px solid #e9ecef;
    font-size: 13px;
}

.line-items-table tbody tr:last-child td {
    border-bottom: none;
}

.line-items-table tbody tr:hover {
    background: #f8f9fa;
}

/* ===== STACK VISUALIZATION ===== */

.stack-visual-section {
    background: white;
    border-radius: 8px;
    padding: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.stack-visual-section h4 {
    margin: 0 0 16px 0;
    color: #2D3436;
    font-size: 16px;
    font-weight: 600;
}

.stack-visual {
    background: #f8f9fa;
    border: 2px solid #1B4965;
    border-radius: 8px;
    padding: 20px;
    margin: 16px 0;
    overflow-x: auto; /* Allow horizontal scroll if needed */
}

.trailer-bed {
    display: flex;
    align-items: flex-end;
    min-height: 140px;
    max-height: 200px;
    border-bottom: 4px solid #2D3436;
    padding: 10px 10px 20px 10px;
    gap: 12px;
    position: relative;
    justify-content: flex-start;
    overflow-x: visible; /* Prevent cutoff */
}

.trailer-bed::before {
    content: '53 ft Trailer Bed';
    position: absolute;
    top: -8px;
    left: 50%;
    transform: translateX(-50%);
    font-size: 12px;
    color: #636E72;
    font-weight: 600;
    background: #f8f9fa;
    padding: 0 8px;
}

.stack-position {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    flex-shrink: 0; /* Prevent squishing */
}

.position-blocks {
    display: flex;
    flex-direction: column-reverse; /* Stack from bottom to top */
    gap: 3px;
    min-height: 40px;
}

.unit-block {
    width: 56px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    border-radius: 4px;
    border: 2px solid #2D3436;
    cursor: pointer;
    transition: all 0.2s ease;
    box-sizing: border-box;
}

.unit-block:hover {
    transform: scale(1.08);
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    z-index: 10;
}

.unit-block.stacked {
    background: linear-gradient(135deg, #0984E3 0%, #6C5CE7 100%);
    color: white;
    font-weight: 600;
}

.unit-block.single {
    background: linear-gradient(135deg, #DFE6E9 0%, #B2BEC3 100%);
    color: #2D3436;
    font-weight: 600;
}

.block-symbol {
    font-size: 16px;
    line-height: 1;
}

.position-label {
    text-align: center;
    font-size: 11px;
    padding: 4px 0;
}

.position-number {
    font-weight: 600;
    color: #2D3436;
    margin-bottom: 2px;
}

.position-length {
    color: #636E72;
    margin: 2px 0;
}

.position-count {
    color: #0984E3;
    font-weight: 600;
}

/* ===== UTILIZATION BAR ===== */

.utilization-bar-container {
    margin-top: 20px;
    position: relative;
}

.utilization-bar {
    height: 28px;
    background: #DFE6E9;
    border-radius: 14px;
    overflow: hidden;
    position: relative;
    box-shadow: inset 0 2px 4px rgba(0,0,0,0.1);
}

.utilization-fill {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-weight: 600;
    font-size: 13px;
    transition: width 0.5s ease, background 0.3s ease;
    white-space: nowrap;
    overflow: visible;
    min-width: fit-content;
    padding: 0 12px;
}

/* Utilization color classes */
.utilization-fill.low { 
    background: linear-gradient(90deg, #D63031 0%, #E17055 100%); 
}

.utilization-fill.medium { 
    background: linear-gradient(90deg, #F5B800 0%, #FDCB6E 100%); 
}

.utilization-fill.good { 
    background: linear-gradient(90deg, #6C5CE7 0%, #A29BFE 100%); 
}

.utilization-fill.high { 
    background: linear-gradient(90deg, #0984E3 0%, #74B9FF 100%); 
}

.utilization-label {
    text-align: center;
    margin-top: 8px;
    font-size: 12px;
    color: #636E72;
}

/* ===== STACK SUMMARY ===== */

.stack-summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px;
    margin-top: 20px;
    padding-top: 16px;
    border-top: 1px solid #e9ecef;
}

.summary-metric {
    display: flex;
    align-items: center;
    gap: 8px;
}

.metric-label {
    font-weight: 600;
    color: #636E72;
    font-size: 13px;
}

.metric-value {
    font-weight: 600;
    color: #2D3436;
    font-size: 14px;
}

.metric-value.low { color: #D63031; }
.metric-value.medium { color: #F5B800; }
.metric-value.good { color: #6C5CE7; }
.metric-value.high { color: #0984E3; }

.compatibility-ok .metric-value {
    color: #00B894;
}

.compatibility-warning .metric-value {
    color: #E17055;
}

.compatibility-issue {
    grid-column: 1 / -1;
    padding: 8px 12px;
    background: #FFF3CD;
    border-left: 3px solid #F5B800;
    border-radius: 4px;
    font-size: 12px;
    color: #856404;
}

.capacity-warning {
    grid-column: 1 / -1;
    padding: 12px;
    background: #F8D7DA;
    border-left: 3px solid #D63031;
    border-radius: 4px;
    font-size: 13px;
    color: #721C24;
    font-weight: 600;
}

/* ===== LEGEND ===== */

.stack-legend {
    display: flex;
    gap: 20px;
    margin-top: 16px;
    padding: 12px;
    background: white;
    border-radius: 6px;
    border: 1px solid #e9ecef;
    font-size: 12px;
}

.legend-item {
    display: flex;
    align-items: center;
    gap: 8px;
}

.legend-icon {
    width: 32px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 3px;
    border: 1px solid #2D3436;
    font-size: 12px;
}

.legend-icon.stacked {
    background: linear-gradient(135deg, #0984E3 0%, #6C5CE7 100%);
    color: white;
}

.legend-icon.single {
    background: linear-gradient(135deg, #DFE6E9 0%, #B2BEC3 100%);
    color: #2D3436;
}

/* ===== RESPONSIVE ADJUSTMENTS ===== */

@media (max-width: 768px) {
    .stack-visual {
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
    }
    
    .trailer-bed {
        min-width: max-content;
        padding: 10px;
    }
    
    .stack-summary {
        grid-template-columns: 1fr;
    }
}

/* ===== NO DATA STATE ===== */

.no-data {
    text-align: center;
    padding: 40px;
    color: #636E72;
    font-size: 14px;
}
```

---

## Fix 4: Update API Response Format

**File**: `app.py` (or wherever `/api/orders/<order_id>/stack-config` is defined)

Update the endpoint to return the new data structure:

```python
@app.route('/api/orders/<order_id>/stack-config')
def get_stack_configuration(order_id):
    """
    Calculate and return stack configuration for an order (by SONUM)
    """
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # Get all line items for this order (grouped by SONUM)
    cursor.execute('''
        SELECT 
            ol.item,
            ol.sku,
            ol.qty,
            ol.unit_length_ft,
            ss.max_stack_flat_bed as max_stack_height,
            ss.category
        FROM order_lines ol
        JOIN sku_specifications ss ON ol.sku = ss.sku
        WHERE ol.so_num = ?
        ORDER BY ol.unit_length_ft ASC
    ''', (order_id,))
    
    lines = cursor.fetchall()
    
    if not lines:
        return jsonify({'error': 'Order not found'}), 404
    
    # Convert to dict format
    order_lines = []
    for line in lines:
        order_lines.append({
            'item': line[0],
            'sku': line[1],
            'qty': line[2],
            'unit_length_ft': line[3],
            'max_stack_height': line[4],
            'category': line[5]
        })
    
    # Calculate stack configuration with corrected algorithm
    from services.stack_calculator import calculate_stack_configuration
    config = calculate_stack_configuration(order_lines)
    
    return jsonify(config)
```

---

## Testing the Fix

### Test Case 1: Your Example Order

**Input**:
- 6X14GW2BRKTP: 2 units, 18 ft, max_stack=4
- 6X16GW2BRKTP: 1 unit, 20 ft, max_stack=4
- 7X16CH2BRKTP: 3 units, 20 ft, max_stack=4
- 7X18SF2BRKTP: 1 unit, 22 ft, max_stack=4
- 7X20HDEQDTSRTP: 1 unit, 25 ft, max_stack=4

**Expected Result**:
- Position 1: 18 ft (2 units + parts of other items = 4 units stacked)
- Position 2: 20 ft (1 unit + parts of other items = 4 units stacked)
- Position 3: 20 ft (parts of 7X16CH2BRKTP items)
- Position 4: 22 ft (1 unit + fill space)
- Position 5: 25 ft (1 unit + fill space)

**Total**: Should be ~50-53 ft (94-100% utilization)

### Test Case 2: Simple Stack

**Input**:
- 6X10GW: 2 units, 14 ft, max_stack=4
- 6X10GW: 2 units, 14 ft, max_stack=4

**Expected Result**:
- Position 1: 14 ft (4 units stacked = full position)
- Total: 14 ft (26% utilization)

### Test Case 3: Mixed Lengths

**Input**:
- 4X6G: 3 units, 12 ft, max_stack=5
- 6X12GW: 2 units, 16 ft, max_stack=5

**Expected Result**:
- Position 1: 12 ft (3 units)
- Position 2: 16 ft (2 units)
- Total: 28 ft (53% utilization)
- Note: Different lengths cannot share positions

---

## Deployment Steps

1. **Backup current code**: 
   ```bash
   git commit -m "Pre-stack-fix backup"
   ```

2. **Update backend**:
   - Replace `calculate_stack_configuration()` in `services/stack_calculator.py`
   - Update API endpoint in `app.py`

3. **Update frontend**:
   - Replace `renderStackVisualization()` in JavaScript
   - Add `updateSummaryMetrics()` function
   - Update CSS styles

4. **Test locally**:
   - Upload Amanda's freight file
   - Expand several orders
   - Verify utilization calculations are correct
   - Check visual layout doesn't overflow

5. **Deploy**:
   ```bash
   git add .
   git commit -m "Fix: Correct stack calculation & improve UX layout"
   git push
   ```

---

## Validation Checklist

- [ ] Utilization percentage matches manual calculation
- [ ] Items of same length are grouped into shared positions
- [ ] Visual blocks don't overflow container
- [ ] Utilization bar stays within bounds (caps at 100% width)
- [ ] Stack positions display correctly (bottom to top)
- [ ] Hover tooltips show correct item details
- [ ] Summary metrics are accurate
- [ ] "Exceeds capacity" warning shows when >53 ft
- [ ] Compatibility warnings display when applicable
- [ ] Mobile/tablet layout works (horizontal scroll if needed)

---

## Expected Improvements

**Before Fix**:
- Order shows 105 ft, 198% utilization (WRONG)
- 5 positions shown, but not efficiently packed
- Visual overflows boundaries

**After Fix**:
- Order shows ~50 ft, ~94% utilization (CORRECT)
- Fewer positions needed (efficient packing)
- Visual contained within card
- Clear indication if order exceeds single trailer capacity

---

**END OF INSTRUCTIONS**
