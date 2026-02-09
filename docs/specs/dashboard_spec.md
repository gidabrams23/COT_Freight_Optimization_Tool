# Load Planning Tool: Dashboard Specification

## Overview

This document specifies the **Dashboard** page for the Load Planning Tool. The dashboard serves as a **reporting and visibility snapshot** for shipping managers and load planners, providing at-a-glance performance metrics across plants, order status tracking, and highlights of successful/unsuccessful loads.

---

## Visual Design System (Apply App-Wide)

The current app styling is too rounded and spread out. Apply these design tokens **globally across all pages** to achieve a tighter, more professional aesthetic.

### Spacing & Density

| Token | Current (Approx) | Target |
|-------|------------------|--------|
| Border radius | 8-12px | **2-4px** |
| Card padding | 20-24px | **12-16px** |
| Component gap | 16-24px | **8-12px** |
| Section margins | 24-32px | **16-20px** |

### Typography

| Element | Specification |
|---------|---------------|
| Data/numbers | `font-family: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace` |
| Labels/headers | `font-family: 'Inter', -apple-system, sans-serif` |
| Font weights | Labels: 500, Headers: 600, Data: 400 |
| Base size | 13px body, 11px labels, 24px hero metrics |

### Color Palette (Dark Theme)

```css
/* Backgrounds */
--bg-primary: #0D1117;      /* Main background */
--bg-secondary: #161B22;    /* Cards, panels */
--bg-tertiary: #21262D;     /* Hover states, nested elements */

/* Borders */
--border-default: #30363D;  /* Standard borders */
--border-subtle: #21262D;   /* Subtle separators */

/* Text */
--text-primary: #E6EDF3;    /* Primary text */
--text-secondary: #8B949E;  /* Labels, secondary info */
--text-muted: #6E7681;      /* Placeholder, disabled */

/* Status Colors */
--status-success: #3FB950;  /* Green - high utilization, on-time */
--status-warning: #D29922;  /* Amber - medium utilization, due soon */
--status-error: #F85149;    /* Red - low utilization, past due */
--status-info: #58A6FF;     /* Blue - informational, links */

/* Accent */
--accent-primary: #58A6FF;  /* Primary actions, active states */
--accent-hover: #79C0FF;    /* Hover states */
```

### Component Styling

```css
/* Cards */
.card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-default);
  border-radius: 4px;
  padding: 12px 16px;
}

/* Tables */
.table {
  border-collapse: collapse;
}
.table th {
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-secondary);
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-default);
}
.table td {
  font-family: monospace;
  font-size: 13px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border-subtle);
}

/* Buttons */
.btn {
  border-radius: 4px;
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 500;
}

/* Status Badges */
.badge {
  border-radius: 2px;
  padding: 2px 6px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
}
```

### Visual Hierarchy

- Use **1px solid borders** consistently (not shadows for separation)
- Status indicators: small colored dots (6px) or thin left-border accents (3px)
- Progress bars: 4px height, squared ends
- Icons: 16px standard, 14px inline with text

---

## Dashboard Layout

### Page Structure

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  [Sidebar]  │  DASHBOARD HEADER                              [Date Range ▼] │
│             │───────────────────────────────────────────────────────────────│
│  Dashboard  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ │
│  Orders     │  │ LAVONIA │ │  WACO   │ │ GLADE   │ │ COUNCIL │ │  RENO   │ │
│  Loads      │  │  92%    │ │  85%    │ │  78%    │ │  71%    │ │  --     │ │
│             │  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘ │
│             │───────────────────────────────────────────────────────────────│
│             │                                                               │
│             │  ┌─────────────────────────────────────────┐ ┌─────────────┐ │
│             │  │  UTILIZATION TREND (7-DAY)              │ │ ORDER       │ │
│             │  │  ┌─────────────────────────────────────┐│ │ STATUS      │ │
│             │  │  │ 82% ← Headline                      ││ │             │ │
│             │  │  │ ▁▂▃▄▅▆▇ ← Sparkline                 ││ │ Past Due  3 │ │
│             │  │  └─────────────────────────────────────┘│ │ This Week 12│ │
│             │  └─────────────────────────────────────────┘ │ Next Week 8 │ │
│             │                                              └─────────────┘ │
│             │  ┌───────────────────────────────────────────────────────────┐│
│             │  │  TOP 5 HIGHEST UTILIZATION LOADS                    [▼]  ││
│             │  │  ┌─────┬──────────┬──────────┬───────┬─────────────────┐ ││
│             │  │  │Load │ Plant    │ Util %   │ Date  │ Schematic       │ ││
│             │  │  ├─────┼──────────┼──────────┼───────┼─────────────────┤ ││
│             │  │  │L-001│ Lavonia  │ 98.2%    │ 01/28 │ [▓▓▓▓▓▓▓▓░░]   │ ││
│             │  │  │L-002│ Waco     │ 96.5%    │ 01/27 │ [▓▓▓▓▓▓▓▓░░]   │ ││
│             │  │  └─────┴──────────┴──────────┴───────┴─────────────────┘ ││
│             │  └───────────────────────────────────────────────────────────┘│
│             │                                                               │
│             │  ┌───────────────────────────────────────────────────────────┐│
│             │  │  LOW UTILIZATION LOADS (<70%)                       [▼]  ││
│             │  │  ... similar table structure ...                         ││
│             │  └───────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Specifications

### 1. Plant Filter Cards (Persistent Header)

**Purpose:** Multi-select plant filter that persists across all pages (Dashboard, Orders, Loads). Clicking filters all data; shows plant-level utilization at a glance.

**Behavior:**
- Clicking a card toggles selection (multi-select enabled)
- Selected cards have highlighted border (`var(--accent-primary)`) and subtle background tint
- Clicking "ALL" or clicking all selected cards deselects and shows all plants
- Filter state persists when navigating between tabs
- When navigating from a clickable metric (e.g., "3 Past Due" at Lavonia), the filter auto-applies

**Data per card:**
| Field | Source | Format |
|-------|--------|--------|
| Plant Name | Static | "LAVONIA", "WACO", etc. |
| Avg Utilization (7-day) | Calculated from accepted loads | "92%" or "--" if no data |
| Visual indicator | Color based on threshold | Green ≥80%, Amber 70-79%, Red <70% |

**Visual Spec:**
```
┌────────────────┐
│ LAVONIA        │  ← Plant name (11px, uppercase, secondary color)
│ 92%            │  ← Utilization (20px, monospace, status color)
│ ▓▓▓▓▓▓▓▓░░     │  ← Optional: thin progress bar (4px)
└────────────────┘
   ↑ 
   Subtle left border (3px) in status color when selected
```

---

### 2. Utilization Trend Widget

**Purpose:** Show rolling 7-day average utilization with daily trend.

**Layout:**
```
┌─────────────────────────────────────────────────────────────┐
│  SPACE-BASED UTILIZATION (7-DAY)           Lavonia, Waco ▼ │  ← Title + active filters
│                                                             │
│  82.4%                                              ↑ 3.2% │  ← Hero metric + delta
│                                                             │
│  ▁ ▂ ▃ ▄ ▄ ▅ ▆                                             │  ← Sparkline (7 bars)
│  Mon Tue Wed Thu Fri Sat Sun                               │  ← Day labels
└─────────────────────────────────────────────────────────────┘
```

**Data:**
| Field | Calculation |
|-------|-------------|
| Headline % | Average of (SKU Space Consumed / Trailer Capacity) for all accepted loads in period |
| Delta | Compare current 7-day avg to prior 7-day avg |
| Sparkline | Daily averages for each of the past 7 days |

**Metric Definition (space-based utilization):** Use the existing calculation from the app that accounts for SKU dimensions, stacking rules, and linear feet consumed. This is NOT weight-based.

---

### 3. Order Status Summary Widget

**Purpose:** Quick counts of orders needing attention, with navigation to filtered Orders page.

**Layout:**
```
┌─────────────────────────────────────┐
│  ORDERS REQUIRING ATTENTION         │
│─────────────────────────────────────│
│  🔴  Past Due (No Assignment)    3  │  ← Clickable row
│  🟡  Due This Week               12 │  ← Clickable row  
│  🟢  Due Next Week                8 │  ← Clickable row
└─────────────────────────────────────┘
```

**Behavior:**
- Each row is clickable
- Clicking navigates to **Orders** tab with filters auto-applied:
  - Plant filter = currently selected plant(s) from header
  - Due date filter = relevant category (Past Due, This Week, Next Week)
  - Assignment status = "Unassigned"
- Hover state: subtle background highlight + cursor pointer

**Orders Tab Enhancement Required:**
The Orders tab needs filter functionality to support:
- Due date range (Past Due, Due This Week, Due Next Week, Custom)
- Assignment status (Assigned, Unassigned, All)
- Plant (already exists via header filter)

Filters should be settable via URL params or state management so dashboard clicks can deep-link with pre-applied filters.

---

### 4. Top 5 Highest Utilization Loads

**Purpose:** Showcase successful loads as a "wins" highlight. Includes schematic thumbnail.

**Layout:**
```
┌───────────────────────────────────────────────────────────────────────────┐
│  TOP PERFORMERS: HIGHEST UTILIZATION                    This Week ▼  All │
│───────────────────────────────────────────────────────────────────────────│
│  LOAD ID      PLANT       UTIL %     SHIPPED     SCHEMATIC               │
│───────────────────────────────────────────────────────────────────────────│
│  L-24891      Lavonia     98.2%      01/28       [▓▓▓▓▓▓▓▓▓░]            │
│  L-24756      Waco        96.5%      01/27       [▓▓▓▓▓▓▓▓░░]            │
│  L-24802      Glade Spr   95.8%      01/28       [▓▓▓▓▓▓▓▓░░]            │
│  L-24699      Lavonia     94.1%      01/26       [▓▓▓▓▓▓▓░░░]            │
│  L-24715      Council B   93.7%      01/26       [▓▓▓▓▓▓▓░░░]            │
└───────────────────────────────────────────────────────────────────────────┘
```

**Schematic Column:**
The "Schematic" displays a **miniature thumbnail** of the load schematic visualization (the same schematic view that exists in the Loads tab). This should be:
- A simplified/minified version: just the colored blocks representing SKU placement
- Dimensions: ~80px wide × 24px tall
- Clickable to expand or navigate to full load detail

**Filters:**
- Time period dropdown: "Today", "This Week", "Last 7 Days", "This Month"
- Plant filter: Inherits from header selection OR local "All" toggle

**Behavior:**
- Clicking a row navigates to that load's detail view
- Table respects header plant filter, but includes local "All" option to override

---

### 5. Low Utilization Loads (Needs Improvement)

**Purpose:** Call out underperforming loads for visibility and improvement tracking.

**Layout:** Same structure as Top 5, but:
- Sorted ascending by utilization
- Filtered to loads with utilization <70%
- Red status color on utilization values
- Title: "NEEDS IMPROVEMENT: LOW UTILIZATION (<70%)"

**Threshold:** 70% is the target minimum. Loads shipping below this represent consolidation opportunity loss.

---

## Data Requirements

### Required Data Points

| Metric | Source | Refresh |
|--------|--------|---------|
| Load utilization % | Calculated: SKU space consumed ÷ trailer capacity | On load acceptance |
| Load ship date | Load record | On status change |
| Load plant | Load record | Static |
| Order due date | Order record | On upload/edit |
| Order assignment status | Order-Load relationship | On load building |
| Order plant | Order record | Static |

### Calculations

**Space-Based Utilization:**
```
Utilization % = (Sum of SKU Linear Feet Consumed) / (Trailer Linear Feet Capacity) × 100

Where SKU Linear Feet Consumed accounts for:
- SKU dimensions (L × W × H)
- Stacking rules (stackable vs non-stackable)
- Actual placement in trailer
```
This calculation should already exist in the app - surface it here.

**7-Day Rolling Average:**
```
Avg = Sum(Daily Utilization) / Count(Days with Loads)
```
Only include days where at least one load was accepted/shipped.

---

## Interaction Flows

### Flow 1: Dashboard → Orders (Filtered)
```
1. User views Dashboard
2. User clicks "3 Past Due" under Order Status for Lavonia
3. App navigates to Orders tab
4. Filters auto-apply: Plant = Lavonia, Due = Past Due, Status = Unassigned
5. Table shows only matching orders
```

### Flow 2: Plant Filter Persistence
```
1. User clicks "LAVONIA" and "WACO" plant cards in header
2. Dashboard metrics update to show only those plants
3. User clicks to Loads tab
4. Loads tab shows only Lavonia and Waco loads
5. User clicks back to Dashboard
6. Filter remains applied
```

### Flow 3: Top Load → Detail View
```
1. User sees high-performing load in Top 5 widget
2. User clicks the row
3. App navigates to Loads tab → Load Detail view for that specific load
4. User can see full schematic and load contents
```

---

## Implementation Notes for Codex

### State Management
- Plant filter selection should be stored in global state (context/redux) so it persists across tab navigation
- URL should reflect filter state for shareability: `/dashboard?plants=lavonia,waco`

### Orders Tab Enhancements
Add filter controls for:
- Due date category: Dropdown with "All", "Past Due", "Due This Week", "Due Next Week"
- Assignment status: Toggle or dropdown for "All", "Assigned", "Unassigned"
- Accept filter params from navigation state or URL

### Schematic Thumbnail Component
If the load schematic visualization already exists, create a `<SchematicThumbnail loadId={id} />` component that:
- Renders the same visualization at reduced scale
- Removes labels/annotations for clarity
- Is clickable to expand or navigate

### Responsive Considerations
- Plant cards should wrap on narrow screens (maintain same card size)
- Widgets stack vertically on mobile
- Tables become scrollable horizontally if needed

---

## Summary Checklist

- [ ] Apply visual design system (tighter spacing, sharper borders) **app-wide**
- [ ] Implement persistent plant filter cards in header across all pages
- [ ] Build Utilization Trend widget with headline + sparkline
- [ ] Build Order Status widget with clickable navigation
- [ ] Enhance Orders tab with due date and assignment status filters
- [ ] Build Top 5 / Low Utilization tables with schematic thumbnails
- [ ] Implement cross-page filter state persistence
- [ ] Wire up navigation flows with auto-applied filters
