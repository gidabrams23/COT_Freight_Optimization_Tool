# PRD: Expandable Order Rows with Visual Stack Configuration

## Executive Summary

**Objective**: Enhance the Orders page with expandable rows that show detailed line items and an intelligent visual representation of how items should be stacked on a trailer. This gives load planners immediate visibility into the physical configuration of each order without manual calculation.

**Success Metrics**:
- Planners can click any order row to see line item details
- Visual stack diagram accurately represents physical trailer configuration
- Stack configuration follows cheat sheet rules (length, max stack height)
- Expansion/collapse is smooth and intuitive
- Visual helps planners identify problematic orders (too tall, incompatible items)

---

## Feature Overview

When a user clicks on any order row in the Orders table, the row expands to show:
1. **Line Item Details Table** - All SKUs in that order with quantities and specifications
2. **Visual Stack Diagram** - A to-scale representation of how items would stack on a trailer bed
3. **Stack Summary Metrics** - Total linear feet, utilization %, stack height, compatibility warnings

---

## Data Requirements

### Order Line Data Structure

Each order (grouped by SONUM) contains multiple line items. Example:

```
Order #12535881 (SONUM: 12535881)
├─ Line 1: 6X10AGW × 1      (SKU: 5.5X10AGW, 14 ft, max_stack: 5)
├─ Line 2: 5.5X10GWPTLED × 2 (SKU: 5.5X10GWHDP, 14 ft, max_stack: 5)
├─ Line 3: 5X8SPWOODY × 1    (SKU: 4X8WOODY, 14 ft, max_stack: 3)
└─ Line 4: 6X10GWHSL3K × 3   (SKU: 5.5X10AGW, 14 ft, max_stack: 5)
```

### Required Calculations

For each line item, system needs:
- `unit_length_ft` - From sku_specifications table
- `max_stack_height` - From sku_specifications table (use max_stack_flat_bed)
- `qty` - From order line
- `positions_required` - CEILING(qty / max_stack_height)
- `linear_feet_consumed` - positions_required × unit_length_ft

For the entire order:
- `total_linear_feet` - SUM of all line items' linear_feet_consumed
- `utilization_pct` - (total_linear_feet / 53) × 100
- `tallest_stack` - MAX stack height across all positions
- `compatibility_issues` - Check if SKUs can stack together

---

## UI Design Specification

### Orders Table (Before Expansion)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Due Date  │ Customer        │ State │ SO#      │ Items │ Util  │ Status      │
├──────────────────────────────────────────────────────────────────────────────┤
│ 2/13/26   │ RUNNINGS        │ MN    │ 12535881 │ [▼ 7] │ ██ 67%│ Ready       │
│ 2/13/26   │ RUNNINGS        │ MN    │ 12535882 │ [▼ 5] │ ██ 45%│ Ready       │
│ 1/13/26   │ TRACTOR SUPPLY  │ FL    │ 12534033 │ [▼ 8] │ ███ 84%│ Ready      │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Notes**:
- `[▼ 7]` indicates 7 line items, click to expand
- Clicking anywhere on the row triggers expansion
- Utilization bar color-coded (red <30%, yellow 30-70%, green >70%, blue >85%)

### Expanded Order View

When clicked, row expands to show:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 2/13/26   │ RUNNINGS        │ MN    │ 12535881 │ [▲ 7] │ ██ 67%│ Ready       │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Order Details - RUNNINGS (Marshall, MN 56258)                               │
│  SO# 12535881  |  CPO: 4397716-1  |  Due: Feb 13, 2026                      │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Line Items                                                          │   │
│  ├────────┬──────────────┬─────┬──────────┬──────────┬────────────────┤   │
│  │ Item   │ SKU          │ Qty │ Length   │ Max Stack│ Positions Req. │   │
│  ├────────┼──────────────┼─────┼──────────┼──────────┼────────────────┤   │
│  │ 6X10AGW│ 5.5X10AGW    │  1  │ 14 ft    │    5     │      1         │   │
│  │ 5.5X10G│ 5.5X10GWHDP  │  2  │ 14 ft    │    5     │      1         │   │
│  │ 5X8SPWO│ 4X8WOODY     │  1  │ 14 ft    │    3     │      1         │   │
│  │ 6X10GWH│ 5.5X10AGW    │  3  │ 14 ft    │    5     │      1         │   │
│  │ 6X12GWP│ 5.5X10GWHDP  │  1  │ 14 ft    │    5     │      1         │   │
│  └────────┴──────────────┴─────┴──────────┴──────────┴────────────────┘   │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Visual Stack Configuration                                          │   │
│  │                                                                     │   │
│  │  ┌────────────────────────── 53 ft Trailer Bed ─────────────────┐  │   │
│  │  │                                                                │  │   │
│  │  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐               │  │   │
│  │  │  │  ▓▓  │ │  ▓▓  │ │  ▓▓  │ │  ▓▓  │ │  ▓▓  │               │  │   │
│  │  │  │  ▓▓  │ │  ▓▓  │ │  ░░  │ │  ▓▓  │ │  ▓▓  │               │  │   │
│  │  │  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘               │  │   │
│  │  │   Pos 1    Pos 2    Pos 3    Pos 4    Pos 5                  │  │   │
│  │  │   14 ft    14 ft    14 ft    14 ft    14 ft                  │  │   │
│  │  │    (2)      (2)      (1)      (3)      (1)                   │  │   │
│  │  │                                                                │  │   │
│  │  │  [━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━] 70 ft used     │  │   │
│  │  │  └─────────────── 67% Utilization ───────────┘               │  │   │
│  │  │                                                                │  │   │
│  │  └────────────────────────────────────────────────────────────┘  │   │
│  │                                                                     │   │
│  │  Legend:                                                            │   │
│  │  ▓▓ = Stacked (multiple units)    ░░ = Single unit (no stack)     │   │
│  │  Numbers in parentheses = units in that position                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  Summary: 7 items, 5 positions, 70 ft total, 67% utilization               │
│  Max stack height: 2 units  |  Compatible: ✓ All items can stack          │
│                                                                              │
│  [Close]                                                                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Stack Configuration Algorithm

### Step 1: Group Line Items by SKU Compatibility

```python
def calculate_stack_configuration(order_lines):
    """
    Calculate optimal stacking arrangement for an order
    
    Args:
        order_lines: List of line items in the order
    
    Returns:
        {
            'positions': [...],  # List of position objects
            'total_linear_feet': float,
            'utilization_pct': float,
            'max_stack_height': int,
            'compatibility_issues': [...]
        }
    """
    positions = []
    
    # Step 1: Sort lines by max_stack_height (lowest first)
    # This ensures we don't waste stack capacity
    sorted_lines = sorted(order_lines, key=lambda x: x.max_stack_height)
    
    # Step 2: Distribute units across positions
    for line in sorted_lines:
        qty_remaining = line.qty
        
        while qty_remaining > 0:
            # How many units can we stack in this position?
            units_in_position = min(qty_remaining, line.max_stack_height)
            
            positions.append({
                'item': line.item,
                'sku': line.sku,
                'unit_length_ft': line.unit_length_ft,
                'units_stacked': units_in_position,
                'max_stack_height': line.max_stack_height,
                'category': line.category
            })
            
            qty_remaining -= units_in_position
    
    # Step 3: Calculate totals
    total_linear_feet = sum(pos['unit_length_ft'] for pos in positions)
    utilization_pct = (total_linear_feet / 53.0) * 100
    max_stack_height = max(pos['units_stacked'] for pos in positions)
    
    # Step 4: Check compatibility
    compatibility_issues = check_stacking_compatibility(positions)
    
    return {
        'positions': positions,
        'total_linear_feet': total_linear_feet,
        'utilization_pct': utilization_pct,
        'max_stack_height': max_stack_height,
        'compatibility_issues': compatibility_issues
    }

def check_stacking_compatibility(positions):
    """
    Check if all items in the order can physically stack together
    
    Returns list of warning messages, empty if compatible
    """
    issues = []
    categories = [pos['category'] for pos in positions]
    
    # Rule 1: DUMP cannot mix with other categories
    if 'DUMP' in categories and len(set(categories)) > 1:
        issues.append("⚠️ DUMP trailers cannot mix with other types")
    
    # Rule 2: Check if any position exceeds trailer height
    # (Assume 8 ft max height, each unit ~2 ft)
    for pos in positions:
        estimated_height_ft = pos['units_stacked'] * 2
        if estimated_height_ft > 8:
            issues.append(f"⚠️ Position with {pos['units_stacked']} units may exceed height limit")
    
    # Rule 3: Wooden floor (WOODY) requires care
    woody_items = [pos for pos in positions if 'WOODY' in pos['sku']]
    if woody_items and len(woody_items) < len(positions):
        issues.append("ℹ️ Mix of wooden and non-wooden floors - verify compatibility")
    
    return issues
```

---

## Visual Rendering Specification

### Position Block Rendering

Each position on the trailer is represented as a vertical block:

**HTML Structure**:
```html
<div class="stack-position">
    <div class="position-blocks">
        <!-- Stack visualization - bottom to top -->
        <div class="unit-block stacked" title="6X10AGW">▓▓</div>
        <div class="unit-block stacked" title="6X10AGW">▓▓</div>
    </div>
    <div class="position-label">
        <div class="position-number">Pos 1</div>
        <div class="position-length">14 ft</div>
        <div class="position-count">(2)</div>
    </div>
</div>
```

**CSS Styling**:
```css
.stack-visual {
    background: #f5f5f5;
    border: 2px solid #1B4965;
    border-radius: 8px;
    padding: 20px;
    margin: 16px 0;
}

.trailer-bed {
    display: flex;
    align-items: flex-end;
    height: 120px;
    border-bottom: 4px solid #2D3436;
    padding: 10px;
    gap: 8px;
    position: relative;
}

.trailer-bed::before {
    content: '53 ft Trailer Bed';
    position: absolute;
    top: -20px;
    left: 50%;
    transform: translateX(-50%);
    font-size: 12px;
    color: #636E72;
    font-weight: 600;
}

.stack-position {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
}

.position-blocks {
    display: flex;
    flex-direction: column-reverse; /* Stack from bottom to top */
    gap: 2px;
}

.unit-block {
    width: 60px;
    height: 30px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    border-radius: 4px;
    border: 1px solid #2D3436;
    cursor: pointer;
    transition: transform 0.2s;
}

.unit-block:hover {
    transform: scale(1.05);
    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}

.unit-block.stacked {
    background: linear-gradient(135deg, #0984E3 0%, #6C5CE7 100%);
    color: white;
}

.unit-block.single {
    background: linear-gradient(135deg, #DFE6E9 0%, #B2BEC3 100%);
    color: #2D3436;
}

.position-label {
    text-align: center;
    font-size: 11px;
}

.position-number {
    font-weight: 600;
    color: #2D3436;
}

.position-length {
    color: #636E72;
    margin: 2px 0;
}

.position-count {
    color: #0984E3;
    font-weight: 600;
}

/* Utilization bar below trailer */
.utilization-bar-container {
    margin-top: 16px;
    position: relative;
}

.utilization-bar {
    height: 24px;
    background: #DFE6E9;
    border-radius: 12px;
    overflow: hidden;
    position: relative;
}

.utilization-fill {
    height: 100%;
    background: linear-gradient(90deg, #F5B800 0%, #0984E3 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-weight: 600;
    font-size: 12px;
    transition: width 0.3s ease;
}

.utilization-label {
    position: absolute;
    bottom: -20px;
    left: 50%;
    transform: translateX(-50%);
    font-size: 12px;
    color: #636E72;
    white-space: nowrap;
}

/* Color coding based on utilization */
.utilization-fill.low { background: #D63031; }  /* <30% */
.utilization-fill.medium { background: #F5B800; } /* 30-70% */
.utilization-fill.good { background: #6C5CE7; }  /* 70-85% */
.utilization-fill.high { background: #0984E3; }  /* >85% */
```

### Legend & Summary

```html
<div class="stack-legend">
    <div class="legend-item">
        <div class="legend-icon stacked">▓▓</div>
        <span>Stacked (multiple units)</span>
    </div>
    <div class="legend-item">
        <div class="legend-icon single">░░</div>
        <span>Single unit (no stack)</span>
    </div>
    <div class="legend-item">
        <span><strong>Numbers in parentheses</strong> = units in that position</span>
    </div>
</div>

<div class="stack-summary">
    <div class="summary-metric">
        <span class="metric-label">Items:</span>
        <span class="metric-value">7</span>
    </div>
    <div class="summary-metric">
        <span class="metric-label">Positions:</span>
        <span class="metric-value">5</span>
    </div>
    <div class="summary-metric">
        <span class="metric-label">Total Length:</span>
        <span class="metric-value">70 ft</span>
    </div>
    <div class="summary-metric">
        <span class="metric-label">Utilization:</span>
        <span class="metric-value">67%</span>
    </div>
    <div class="summary-metric">
        <span class="metric-label">Max Stack:</span>
        <span class="metric-value">2 units</span>
    </div>
    <div class="summary-metric compatibility-ok">
        <span class="metric-label">Compatible:</span>
        <span class="metric-value">✓ All items can stack</span>
    </div>
</div>
```

---

## JavaScript Interaction

### Expand/Collapse Behavior

```javascript
// Toggle expansion when clicking order row
document.querySelectorAll('.order-row').forEach(row => {
    row.addEventListener('click', function(e) {
        // Don't expand if clicking action buttons
        if (e.target.closest('.action-button')) return;
        
        const orderId = this.dataset.orderId;
        const expandedRow = document.getElementById(`expanded-${orderId}`);
        const expandIcon = this.querySelector('.expand-icon');
        
        if (expandedRow.classList.contains('hidden')) {
            // Expand
            expandedRow.classList.remove('hidden');
            expandIcon.textContent = '▲';
            
            // Load stack configuration if not already loaded
            if (!expandedRow.dataset.loaded) {
                loadStackConfiguration(orderId);
                expandedRow.dataset.loaded = 'true';
            }
        } else {
            // Collapse
            expandedRow.classList.add('hidden');
            expandIcon.textContent = '▼';
        }
    });
});

// Load and render stack configuration
async function loadStackConfiguration(orderId) {
    const response = await fetch(`/api/orders/${orderId}/stack-config`);
    const data = await response.json();
    
    renderStackVisualization(orderId, data);
}

function renderStackVisualization(orderId, config) {
    const container = document.getElementById(`stack-visual-${orderId}`);
    
    // Render trailer bed
    let html = '<div class="trailer-bed">';
    
    // Render each position
    config.positions.forEach((position, idx) => {
        html += `
            <div class="stack-position">
                <div class="position-blocks">
        `;
        
        // Render stacked units (bottom to top)
        for (let i = 0; i < position.units_stacked; i++) {
            const blockClass = position.units_stacked > 1 ? 'stacked' : 'single';
            const symbol = position.units_stacked > 1 ? '▓▓' : '░░';
            html += `
                <div class="unit-block ${blockClass}" 
                     title="${position.item} (${position.sku})">
                    ${symbol}
                </div>
            `;
        }
        
        html += `
                </div>
                <div class="position-label">
                    <div class="position-number">Pos ${idx + 1}</div>
                    <div class="position-length">${position.unit_length_ft} ft</div>
                    <div class="position-count">(${position.units_stacked})</div>
                </div>
            </div>
        `;
    });
    
    html += '</div>';
    
    // Add utilization bar
    const utilClass = getUtilizationClass(config.utilization_pct);
    html += `
        <div class="utilization-bar-container">
            <div class="utilization-bar">
                <div class="utilization-fill ${utilClass}" 
                     style="width: ${Math.min(config.utilization_pct, 100)}%">
                    ${config.utilization_pct.toFixed(0)}% Used
                </div>
            </div>
            <div class="utilization-label">
                ${config.total_linear_feet.toFixed(1)} ft of 53 ft capacity
            </div>
        </div>
    `;
    
    container.innerHTML = html;
}

function getUtilizationClass(pct) {
    if (pct < 30) return 'low';
    if (pct < 70) return 'medium';
    if (pct < 85) return 'good';
    return 'high';
}
```

---

## Backend API Endpoint

### New Route: `/api/orders/<order_id>/stack-config`

```python
@app.route('/api/orders/<order_id>/stack-config')
def get_stack_configuration(order_id):
    """
    Calculate and return stack configuration for an order
    
    Returns:
        {
            'order_id': str,
            'positions': [...],
            'total_linear_feet': float,
            'utilization_pct': float,
            'max_stack_height': int,
            'compatibility_issues': [...]
        }
    """
    # Get all line items for this order (SONUM)
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT ol.*, ss.length_with_tongue_ft, ss.max_stack_flat_bed, ss.category
        FROM order_lines ol
        JOIN sku_specifications ss ON ol.sku = ss.sku
        WHERE ol.so_num = ?
        ORDER BY ss.max_stack_flat_bed ASC
    ''', (order_id,))
    
    lines = cursor.fetchall()
    
    if not lines:
        return jsonify({'error': 'Order not found'}), 404
    
    # Calculate stack configuration
    config = calculate_stack_configuration(lines)
    
    return jsonify(config)
```

---

## Database Query Optimization

Since we're querying by SONUM frequently, add index:

```sql
CREATE INDEX IF NOT EXISTS idx_order_lines_so_num ON order_lines(so_num);
```

---

## Acceptance Criteria

### Functional Requirements
- [ ] Clicking any order row toggles expansion/collapse
- [ ] Expanded view shows all line items in a table
- [ ] Stack visualization renders with correct number of positions
- [ ] Each position shows correct number of stacked units
- [ ] Visual matches calculated utilization percentage
- [ ] Utilization bar color matches percentage thresholds
- [ ] Compatibility warnings display when items can't mix
- [ ] Multiple orders can be expanded simultaneously
- [ ] Expansion state persists during page interactions (until refresh)

### Visual Requirements
- [ ] Stack blocks display vertically (bottom to top)
- [ ] Stacked units use blue gradient, single units use gray
- [ ] Hover over blocks shows tooltip with item details
- [ ] Utilization bar animates smoothly on load
- [ ] Legend clearly explains symbols
- [ ] Summary metrics are easy to read
- [ ] Layout is responsive (works on tablets)

### Performance Requirements
- [ ] Stack calculation completes in <200ms
- [ ] Expand animation is smooth (no jank)
- [ ] Page can handle 50+ orders without lag
- [ ] API endpoint returns data in <500ms

---

## Testing Scenarios

### Test Case 1: Simple Order (Single Item Type)
**Input**: 
- Order with 1 line item: 6X10GW × 3 units
- SKU: 6X10GW, 14 ft, max_stack: 5

**Expected**:
- 1 position shown
- 3 units stacked vertically
- 14 ft total length
- 26% utilization
- Green checkmark for compatibility

### Test Case 2: Mixed Order (Multiple Item Types)
**Input**:
- Line 1: 6X10AGW × 1 (14 ft, max_stack: 5)
- Line 2: 5.5X10GWPTLED × 2 (14 ft, max_stack: 5)
- Line 3: 5X8SPWOODY × 1 (14 ft, max_stack: 3)

**Expected**:
- 3 positions shown
- Pos 1: 1 unit (single block)
- Pos 2: 2 units (stacked blocks)
- Pos 3: 1 unit (single block)
- 42 ft total length
- 79% utilization

### Test Case 3: Incompatible Mix (DUMP + Other)
**Input**:
- Line 1: 5X8DUMPLP5K × 1 (SKU: DUMP, 12 ft, max_stack: 3)
- Line 2: 6X10GW × 2 (SKU: 6X10GW, 14 ft, max_stack: 5)

**Expected**:
- 2 positions shown
- Warning message: "⚠️ DUMP trailers cannot mix with other types"
- Warning displayed in red/amber
- Total shown but flagged as incompatible

### Test Case 4: High Utilization Order
**Input**:
- Order totaling 50 ft across multiple lines

**Expected**:
- Utilization bar shows 94%
- Bar color is blue (high utilization)
- Message indicates "Good for standalone shipping"

### Test Case 5: Low Utilization Order
**Input**:
- Order totaling 12 ft

**Expected**:
- Utilization bar shows 23%
- Bar color is red (low utilization)
- Message indicates "High priority for consolidation"

---

## Edge Cases to Handle

1. **Order with no SKU mapping**: 
   - Show "Unable to calculate - missing SKU data"
   - Disable stack visualization

2. **Order exceeding trailer capacity**:
   - Show warning: "⚠️ Order exceeds single trailer capacity (requires multiple loads)"
   - Visualize first 53 ft, indicate overflow

3. **Very tall stacks** (>8 ft estimated):
   - Show warning: "⚠️ Stack height may exceed trailer limits"
   - Recommend splitting order

4. **Empty order** (no line items):
   - Show message: "No line items in this order"
   - Hide visualization section

---

## Future Enhancements (Out of Scope)

- Drag-and-drop to rearrange positions
- 3D visualization of trailer loading
- Export stack diagram as image/PDF
- Edit stack configuration manually
- Compare multiple stack arrangements
- Weight distribution visualization
- Interactive load planning (move items between orders)

---

## Implementation Checklist

### Backend (Python)
- [ ] Create `calculate_stack_configuration()` function in `services/stack_calculator.py`
- [ ] Create `check_stacking_compatibility()` function
- [ ] Add API endpoint `/api/orders/<order_id>/stack-config`
- [ ] Add database index on `so_num`
- [ ] Write unit tests for stack calculation logic

### Frontend (HTML/CSS/JS)
- [ ] Update `orders.html` template with expandable row structure
- [ ] Create CSS for stack visualization (`.stack-visual`, `.trailer-bed`, etc.)
- [ ] Add JavaScript for expand/collapse interaction
- [ ] Add JavaScript for rendering stack visualization
- [ ] Add loading spinner for stack calculation
- [ ] Add error handling for failed API calls

### Testing
- [ ] Test all 5 test case scenarios
- [ ] Test with real Amanda freight data
- [ ] Test edge cases (no SKU, exceeds capacity, etc.)
- [ ] Performance test with 100+ orders
- [ ] Cross-browser testing (Chrome, Firefox, Safari)

---

## Success Metrics

**User Experience**:
- Planners can understand order configuration in <5 seconds
- 90%+ of orders calculate correctly without manual verification
- Visual accurately represents physical trailer loading

**Technical Performance**:
- Page load time <2 seconds with 50 orders
- Expand/collapse feels instant (<100ms)
- No JavaScript console errors

**Business Impact**:
- Reduces time spent manually calculating stack configurations
- Helps identify problematic orders before load building
- Provides visual aid for training new planners

---

**END OF PRD**
