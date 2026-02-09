# Load Approval Feature - Requirements Specification

**Project:** COT Freight Optimization Tool  
**Feature:** Load Review & Approval Interface  
**Version:** 1.0  
**Date:** February 2, 2026

---

## 1. Overview

Build an interactive load approval interface within the expandable load detail pane. The interface enables planners to review system-optimized loads, visualize trailer stacking configurations, and approve/reject loads with structured feedback. All four widgets (stacking schematic, freight manifest, stop sequence, route map) must be visible without scrolling on laptop and external monitor displays.

---

## 2. Layout Requirements

### 2.1 Viewport Targets
- **Primary:** Laptop displays (1366×768 minimum, 1920×1080 typical)
- **Secondary:** External monitors (1920×1080 to 2560×1440)
- **Excluded:** Mobile phones, tablets
- **Constraint:** All four widgets visible without vertical scrolling

### 2.2 Widget Arrangement
Implement a responsive grid layout with four widgets:
1. Stacking Schematic (largest - primary focus)
2. Freight Manifest (order/item table)
3. Stop Sequence Timeline (horizontal timeline)
4. Route Map (OpenStreetMap integration)

Layout should optimize space utilization while keeping stacking schematic as the dominant visual element. Suggested arrangement: 2×2 grid or asymmetric layout with schematic spanning full width top.

---

## 3. Stacking Schematic Widget

### 3.1 Visual Representation
- **View Type:** Side view (height × length)
- **Scale:** Proportional to actual linear feet measurements
- **Trailer Types:**
  - **Standard Deck:** 53' flat representation
  - **Step Deck:** 43' lower deck + 10' upper "step" deck (visually elevated on right side)
- **Truck Icon:** Add simplified truck cab icon to the right of the trailer schematic

### 3.2 Product Unit Display
- Each unit rendered as a rectangle with:
  - **Width:** Proportional to product length in linear feet (e.g., 14' product visually shorter than 18' product)
  - **Height:** Proportional to product height, respecting max trailer height (13'6" standard)
  - **Color:** Consistent color per order (visually distinct between orders within same load)
  - **Label:** SKU code displayed on each unit rectangle
- Stacked units shown vertically based on physical stacking rules from Master Load Building Cheat Sheet

### 3.3 Utilization Metrics
Display below schematic:
- **Linear Feet Utilization:** Percentage of 53' (or 43'/10' for step deck) consumed
- **Utilization Grade:** A-F scale based on existing grading logic
- Remove weight balance, height utilization, and payload capacity widgets from mockup

### 3.4 Dynamic Behavior
- When order is removed, schematic re-renders with updated:
  - Unit positions (re-stacked/repositioned)
  - Utilization percentage
  - Utilization grade

---

## 4. Freight Manifest Widget

### 4.1 Table Columns
| Column | Description |
|--------|-------------|
| Order ID | Unique order identifier (e.g., #ORD-55291) |
| Customer | Customer name |
| Stop | Destination city, state |
| SKU | Product SKU(s) in order |
| Qty | Unit count |
| Status | Allocated / Pending |
| Action | Remove button |

### 4.2 Order Removal Flow
1. Planner clicks "Remove" on specific order row
2. **Inline feedback panel slides open** (not a modal, not a separate page)
3. Feedback panel contains:
   - **Required dropdown:** Removal reason category
     - Customer mixing conflict
     - Capacity exceeded
     - Geographic infeasibility
     - Delivery date conflict
     - Other
   - **Required text field:** Additional details (minimum 10 characters)
   - **Submit button**
   - **Cancel button**
4. On submit:
   - Order removed from load
   - Order returned to unassigned pool (existing functionality)
   - Stacking schematic re-renders
   - Feedback logged with planner ID and timestamp
5. Only one order can be removed at a time

---

## 5. Stop Sequence Timeline Widget

### 5.1 Visual Elements
- Horizontal timeline showing:
  - Origin point (plant identifier, e.g., "IA-04")
  - Numbered stop sequence (1, 2, 3...)
  - Final destination marker
- Stop labels: City abbreviation (e.g., DBQ for Dubuque)
- Connecting line between stops

### 5.2 Data Display
- Total stop count badge (e.g., "4 Stops")
- Total route distance (e.g., "342 mi")

---

## 6. Route Map Widget

### 6.1 Map Integration
- Use existing OpenStreetMap API integration
- Center map to show all stops with appropriate zoom

### 6.2 Route Visualization
- Draw route line/polyline connecting stops in sequence
- Add directional arrows on route line indicating travel direction
- **Stop markers** with sequence numbers (1, 2, 3...) displayed on or adjacent to each marker
- Origin marker: Distinct style (e.g., home icon or plant icon)
- Final destination marker: Distinct style (e.g., flag or pin)

---

## 7. Load-Level Actions

### 7.1 Header Bar
- **Load ID:** Display prominently (e.g., "LOAD: LD-CHI-2044")
- **Status Badge:** Current status (Pending Approval, Approved, Rejected)
- **Reject Load Button:** Red/danger styling
- **Approve Load Button:** Green/success styling

### 7.2 Approve Flow
1. Planner clicks "Approve Load"
2. Load status changes to "Approved"
3. Load moves to Approved Loads category (existing functionality)
4. Interface closes or navigates to next pending load

### 7.3 Reject Flow
1. Planner clicks "Reject Load"
2. **Inline feedback panel opens** (same component as order removal)
3. Feedback panel contains:
   - **Required dropdown:** Rejection reason category
     - Customer mixing conflict
     - Capacity exceeded
     - Geographic infeasibility
     - Route inefficiency
     - Delivery date conflicts
     - Other
   - **Required text field:** Additional details (minimum 10 characters)
   - **Submit button**
   - **Cancel button**
4. On submit:
   - Load status changes to "Rejected"
   - All orders returned to unassigned pool
   - Feedback logged with planner ID and timestamp

---

## 8. Feedback Log

### 8.1 Navigation
- Add "Feedback Log" item to left sidebar navigation menu
- Icon suggestion: clipboard or comment bubble
- Position: Below existing navigation items

### 8.2 Feedback Log View
Display table/list of all feedback entries:

| Column | Description |
|--------|-------------|
| Timestamp | Date and time of feedback |
| Planner | Planner name/ID who submitted |
| Action Type | "Order Removed" or "Load Rejected" |
| Load ID | Associated load identifier |
| Order ID | Associated order (if order removal) |
| Reason Category | Selected dropdown value |
| Details | Free text feedback |

### 8.3 Filtering & Sorting
- Filter by: Date range, Planner, Action type, Reason category
- Sort by: Timestamp (default descending), Planner, Load ID
- Search: Free text search across details field

---

## 9. Data Requirements

### 9.1 Input Data (from optimization algorithm output)
- Load ID
- Load status
- Plant/origin identifier
- List of orders with:
  - Order ID
  - Customer name
  - Destination (city, state, zip, coordinates)
  - SKU(s)
  - Quantity per SKU
  - Linear feet per unit
  - Unit height
  - Stacking eligibility
- Stop sequence with distances
- Total route distance
- Linear feet utilization percentage
- Utilization grade

### 9.2 Stored Feedback Data
- Feedback ID (auto-generated)
- Timestamp
- Planner ID
- Action type (order_removed | load_rejected)
- Load ID
- Order ID (nullable - only for order removals)
- Reason category
- Details text

---

## 10. Styling Requirements

### 10.1 Theme
- Dark theme matching existing application
- Color palette from mockup:
  - Background: slate-dark (#1e293b), slate-deeper (#0f172a)
  - Borders: slate-border (#334155)
  - Text: text-main (#f1f5f9), text-muted (#94a3b8)
  - Primary: blue (#3b82f6)
  - Success: emerald (#10b981)
  - Danger: red (#ef4444)
  - Warning: amber (#f59e0b)

### 10.2 Order Color Coding
- Generate visually distinct colors for each order within a load
- Colors should have good contrast against dark background
- Suggested palette: blue, emerald, amber, purple, cyan, rose (cycling)

### 10.3 Typography
- Font: Inter
- Uppercase labels with letter-spacing for section headers
- Monospace for IDs and codes

---

## 11. Acceptance Criteria

1. [ ] All four widgets visible without scrolling on 1366×768 viewport
2. [ ] Stacking schematic shows proportional unit sizes based on linear feet
3. [ ] Step deck schematic correctly shows 43'+10' step configuration
4. [ ] Truck icon appears to right of trailer schematic
5. [ ] Each unit displays its SKU label
6. [ ] Orders have distinct colors within a load
7. [ ] Removing an order triggers inline feedback panel (not modal/new page)
8. [ ] Feedback requires both category selection and text input
9. [ ] Schematic dynamically updates after order removal
10. [ ] Removed orders return to unassigned pool
11. [ ] Load approval moves load to Approved category
12. [ ] Load rejection uses same feedback flow as order removal
13. [ ] Feedback Log accessible from left navigation
14. [ ] Feedback Log displays all entries with filtering capability
15. [ ] Route map shows numbered stops with connecting route line
16. [ ] Linear feet utilization and A-F grade displayed (not weight/height metrics)

---

## 12. Out of Scope (for this feature)

- Stop reordering functionality
- Multi-order removal in single action
- Mobile/tablet responsive design
- Manual load creation (only optimized loads)
- Carrier selection within this view

---

## 13. Dependencies

- Existing optimization algorithm output
- Existing unassigned order pool functionality
- Existing Approved Loads category
- Existing OpenStreetMap API integration
- Master Load Building Cheat Sheet (stacking rules, SKU dimensions)

---

## 14. Technical Notes

### 14.1 Stacking Logic Reference
Pull product dimensions and stacking rules from Master Load Building Cheat Sheet:
- Product length (linear feet)
- Product height
- Stackable flag
- Max stack height

### 14.2 Schematic Rendering
Consider using SVG or Canvas for proportional rendering. Key calculations:
- `unitWidth = (productLengthFt / trailerLengthFt) * schematicWidth`
- `unitHeight = (productHeightFt / maxTrailerHeightFt) * schematicHeight`

### 14.3 Step Deck Rendering
```
┌─────────────────────────────────────────┬──────────┐
│                                         │  10' Upper│  ← Step (elevated)
│              43' Lower Deck             │   Deck   │
└─────────────────────────────────────────┴──────────┘ [TRUCK]
```
