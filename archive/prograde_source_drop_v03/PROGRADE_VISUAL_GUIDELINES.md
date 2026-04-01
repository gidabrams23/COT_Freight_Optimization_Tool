# ProGrade Visual Guidelines (Derived from COT Freight Optimization UI)

## Purpose
Use this document as the visual contract for the new ProGrade app so it feels like a deliberate extension of the current COT tool, not a separate design language.

Source of truth for current visuals:
- `static/styles.css`
- `templates/base.html`
- `templates/orders.html`
- `templates/loads.html`

## 1) Design Intent
The current interface is designed for planners/dispatch operators who need:
- High information density
- Fast scanability
- Clear status signaling (success/warning/error)
- Compact controls with minimal visual noise

The visual style is dark-first, data-centric, and utilitarian with restrained accent color use.

## 2) Core Color System

### Base theme tokens (current)
```css
:root {
  --bg-primary: #1e293b;
  --bg-secondary: #334155;
  --bg-tertiary: #475569;
  --bg-surface: #0f172a;

  --border-default: #334155;
  --border-subtle: #475569;

  --text-primary: #f8fafc;
  --text-secondary: #94a3b8;
  --text-tertiary: #64748b;

  --accent-primary: #3b82f6;
  --accent-hover: #2563eb;

  --status-success: #22c55e;
  --status-warning: #eab308;
  --status-error: #ef4444;
  --status-info: #3b82f6;
}
```

### Usage rules
- `--bg-surface` / `--bg-primary`: primary surfaces, cards, work areas.
- `--bg-secondary` / `--bg-tertiary`: elevated controls and secondary containers.
- `--text-primary`: key metrics, labels, table values.
- `--text-secondary`: helper text, sublabels, metadata.
- `--accent-primary`: active nav state, focused controls, primary CTA emphasis.
- Status colors only for semantic meaning:
  - Green = success/ready
  - Amber = warning/late/needs attention
  - Red = error/risk/rejected
  - Blue = informational/active context

### Color behavior patterns already in use
- Status badges use low-alpha fills + stronger text/border contrast.
- Borders are subtle and cool-toned; contrast comes from text hierarchy, not heavy shadows.
- Shadows are intentionally minimal (`--shadow-sm/md/lg: none`) to keep a flat, operational look.

## 3) Spacing and Density System

### Spacing scale
```css
--spacing-xs: 4px;
--spacing-sm: 8px;
--spacing-md: 12px;
--spacing-lg: 16px;
--spacing-xl: 20px;
```

### Layout density principles
- Use 4/8/12/16/20 spacing only (avoid arbitrary values unless required for a special component).
- Keep forms and tables compact (most control paddings are 6-10px vertical range).
- Prefer small, consistent gaps over large whitespace.

### Typical spacing patterns from current UI
- Card interior padding: `12-20px`
- Button padding: `6px 12px` (compact actions), occasionally `8px 12px`
- Row/item gaps: `8-12px`
- Section gaps: `12-16px`

## 4) Shape, Borders, and Elevation

### Radius scale
```css
--radius-sm: 4px;
--radius-md: 6px;
--radius-lg: 6px;
--radius-xl: 8px;
```

### Geometry rules
- Most interactive controls: `4-6px` radius.
- Cards/panels: `6-8px` radius.
- Pills/chips/badges: full-pill radius (`999px`).

### Elevation
- Minimal box-shadow usage.
- Separation is created primarily by:
  - background tier changes
  - border contrast
  - spacing and grouping

## 5) Typography

### Font stack
- Primary UI font: `Inter` (weights 400/500/600/700 in use)
- Monospace for IDs/technical values: `ui-monospace, SFMono-Regular, Consolas, monospace`

### Type hierarchy pattern
- Body default: `14px`
- Table/body microcopy: `10-12px`
- Section labels/meta: uppercase + letter spacing for quick scan
- Dense dashboard numbers: larger (18-24px+) while labels stay compact

### Copy style
- Operational and direct.
- Short labels and concise helper text.
- Use uppercase sparingly for chips, tabs, and status labels.

## 6) Component Guidelines

### Navigation and shell
- Left sidebar as persistent primary nav on desktop.
- Active nav item uses accent fill (`--accent-primary`) with white text.
- Collapsible sidebar supported; mobile defaults to icon-led compact nav.

### Buttons
- `btn-primary`: accent-driven action (main CTA).
- `btn-secondary`: secondary action with subdued fill.
- `btn-ghost`: tertiary action with transparent/low-emphasis styling.

Rule: keep one visually dominant action per action cluster.

### Cards/panels
- Use card wrappers to segment workflows (`upload`, `scope`, `optimization`, `review`).
- Card styling should remain restrained and consistent; avoid decorative gradients unless context-specific.

### Tables
- Tables are a core UI primitive; optimize readability and scanning speed.
- Keep row density high, with strong text contrast and subtle row boundaries.
- Use badges/chips inline for status instead of adding verbose columns.

### Status badges and pills
- Keep shape/padding compact.
- Always map badge color to semantic state (never decorative-only status colors).

## 7) Layout and Grid Behavior

### Desktop patterns in current app
- App shell: fixed sidebar + fluid main pane.
- Hero/workbench rows often use 2-column grids for control + summary.
- Data-heavy pages use nested cards + table containers with controlled overflow.

### Responsive behavior
- Existing breakpoints commonly used around:
  - `900px`
  - `980px`
  - `1100px`
  - additional page-specific breakpoints (e.g., 760, 1180, 1200)
- On smaller viewports:
  - collapse multi-column grids to single column
  - keep controls full-width where needed
  - preserve touch-friendly hit area and readability

## 8) Data Visualization and Feedback Patterns

- Progress and utilization bars use status color bands (green/amber/red, occasionally blue for informational).
- KPI cards use compact labels + bold value + optional delta pill.
- Feedback states (saving/success/error) should be immediate and color-coded with text labels.

## 9) Accessibility and Usability Guardrails

- Maintain high contrast between text and dark surfaces.
- Preserve keyboard focus visibility (focus states should be obvious).
- Ensure status is not color-only: include text labels/icons where practical.
- Keep interaction targets large enough for frequent operational use.

## 10) Claude Build Instructions (Visual Contract)
Use these constraints when implementing ProGrade screens:
1. Keep the same dark-first palette structure and token naming style.
2. Reuse the 4/8/12/16/20 spacing scale and compact control density.
3. Preserve button hierarchy: one primary action, secondary and ghost alternatives.
4. Keep card/table-heavy layout optimized for planning workflows.
5. Use semantic status colors only; no decorative remapping of success/warning/error.
6. Preserve Inter typography and compact uppercase meta-label pattern.
7. Build mobile behavior by collapsing grid complexity, not by removing critical data.
8. Minimize decorative effects; prioritize speed, clarity, and scanability.

## 11) Optional Starter Token Block for ProGrade
```css
:root {
  --bg-primary: #1e293b;
  --bg-secondary: #334155;
  --bg-tertiary: #475569;
  --bg-surface: #0f172a;

  --border-default: #334155;
  --border-subtle: #475569;

  --text-primary: #f8fafc;
  --text-secondary: #94a3b8;
  --text-tertiary: #64748b;

  --accent-primary: #3b82f6;
  --accent-hover: #2563eb;

  --status-success: #22c55e;
  --status-warning: #eab308;
  --status-error: #ef4444;
  --status-info: #3b82f6;

  --spacing-xs: 4px;
  --spacing-sm: 8px;
  --spacing-md: 12px;
  --spacing-lg: 16px;
  --spacing-xl: 20px;

  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 6px;
  --radius-xl: 8px;

  --font-family: "Inter", sans-serif;
  --font-mono: ui-monospace, "SFMono-Regular", "Consolas", monospace;
}
```

---
Prepared: 2026-03-25
Intended audience: Claude Code implementation for ProGrade spinoff UI
