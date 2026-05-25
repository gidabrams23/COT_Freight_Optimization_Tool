# ProGrade Load Building Tool Transition Document (Draft)

Date: 2026-05-20  
Audience: Non-technical operations leaders, fulfillment teams, sales teams, shipping teams

## Executive Quick Start (One-Page Version)

### Strategic Purpose
The ProGrade load building tool formalizes brand-specific load engineering knowledge into a repeatable operating workflow.  
It enables teams to design, validate, and refine trailer loads without relying on a single shipping expert to interpret fit logic.

### Why This Matters to the Business
- Reduces key-person dependency in daily shipping and fulfillment decisions.
- Improves trailer utilization through standardized stacking rules and real-time fit validation.
- Accelerates cycle time for load planning, customer response, and internal approvals.
- Creates a durable training platform for future planners, fulfillment coordinators, and sales teams.

### Brand Paths, Users, and Value

| Brand Path | Primary User Group | Core Business Value |
|---|---|---|
| PJ | Sales representatives | Build customer-ready load scenarios in real time and identify incremental units that improve freight economics and customer margin. |
| Big Tex | Order fulfillment team | Independently validate non-standard load mixes, reducing approval bottlenecks with shipping leadership. |
| B-Wise | User group being finalized | Extend the same operating model to improve utilization consistency and reduce dependency risk as adoption scales. |

### Process At A Glance

`[1. Select Brand + Session] -> [2. Build Draft Load] -> [3. Validate Fit in Real Time] -> [4. Close Remaining Gap with Inventory Finder] -> [5. Final Review + Export]`

### Operating Guardrails
- Treat the tool as decision support; final shipping release still requires carrier and regulatory judgment.
- Confirm truck profile early (`53_step_deck`, `53_flatbed`, `ground_pull`) to avoid late-stage redesign.
- Maintain SKU dimensions and stack assumptions as controlled operational data.

---

## Full Transition Guide

## 1) Purpose and Intended Users

This document captures and transfers the operational load-building logic used across PJ, Big Tex, and B-Wise so planning quality does not depend on institutional memory held by a limited number of individuals.

This document is for operational users and business leaders who need to:
- Understand what the ProGrade load building tool does and why it exists.
- Use the core workflows confidently.
- Maintain assumptions (SKU and stacking settings) that keep recommendations accurate.

## 2) Business Value and Expected Outcomes

### Primary Outcomes
- Reduce single-person dependency in load design and release decisions.
- Reduce time spent by shipping directors/load engineers on routine fit validation.
- Improve trailer utilization and load quality through standardized configuration logic.
- Accelerate training readiness for future planners and support teams.

### Additional Outcomes
- Faster customer-facing decisions (particularly in PJ sales workflows).
- Faster inventory movement by recommending feasible fill candidates that are currently available.
- Stronger cross-functional alignment, with sales, fulfillment, and shipping working from one logic standard.

### Example KPI Ideas (Optional)
- `% of loads built without shipping-manager intervention`
- `Average planning time per load`
- `Average load utilization % by brand`
- `% of loads where inventory-gap suggestions were used`

## 3) Combined Workflow With Brand-Specific Notes

### Step A: Initialize Session and Operating Context
1. Open the ProGrade load building tool and choose the relevant brand path (PJ, Big Tex, B-Wise).
2. Start a new draft session or continue an existing one.
3. Confirm truck profile before heavy editing (for example: `53_step_deck`, `53_flatbed`, `ground_pull`).

Brand notes:
- PJ commonly uses step deck flow patterns.
- Big Tex commonly uses flatbed and ground pull patterns.
- B-Wise follows the same workflow pattern; assumption quality is especially important while user process is still stabilizing.

### Step B: Build and Refine the Load
1. Add units from SKU list (category -> model -> item).
2. Drag and drop to desired stack positions.
3. Use real-time schematic and capacity meters to validate how full the load is.
4. Adjust orientation/placement as needed to resolve fit conflicts.

Outcome of this step:
- Users can confirm operational viability visually before escalating for shipping leadership review.

### Step C: Close Remaining Capacity with Inventory Gap Finder
1. Open the Inventory Gap Finder panel in the load builder.
2. Select warehouse filter (when available).
3. Review gap feet and candidate items.
4. Add suggested units that fit stack constraints and available inventory.

Improved wording of the value proposition:
The ProGrade load building tool's Inventory Gap Finder matches open trailer space to real SKU geometry and current on-hand inventory, then recommends units that can complete a load more efficiently.

Why it matters:
- Improves utilization with items likely to fit physically and operationally.
- Helps move available inventory faster.
- Supports practical upsell conversations (for example, "you are already paying freight; adding one more unit may improve delivered economics").

## 4) Use Cases by Brand

### At-a-Glance Table

| Brand | Typical User Group | Typical Trigger | Business Benefit |
|---|---|---|---|
| PJ | Sales reps | Customer asks for quote/load feasibility | Faster customer response; improved freight absorption recommendations; reduced engineer bottleneck. |
| Big Tex | Order fulfillment team | Non-standard or mixed load request | Faster internal decisioning without waiting on manager for each scenario. |
| B-Wise | To be finalized | Early-stage adoption/support use | Reuse proven workflow to reduce dependency risk and improve consistency. |

### Practical Narrative
- Big Tex: fulfillment can sketch and validate more loads directly, reducing repeated approval dependency on a small number of experts.
- PJ: sales can pre-validate fit and suggest incremental units that improve customer economics.
- Both: inventory-aware suggestions help reduce time inventory sits at facilities.

## 5) Settings and Assumption Management

Settings are the operational knowledge base behind the ProGrade load building tool. They encode brand-specific stacking behavior and space-consumption assumptions.

### What Is Configured
- SKU catalog by brand and category.
- Key dimensions by SKU:
  - deck length
  - tongue length
  - stack heights / height envelopes
- Stacking behavior and space-consumption assumptions.
- Trailer assumptions (for example `53_step_deck`, `53_flatbed`, `ground_pull`).
- Tongue profile conventions (including bumper-pull vs gooseneck behavior where applicable).

### Add or Edit SKU Process (In-Tool)
1. Open `Settings` in the ProGrade load building tool.
2. Select brand tab (`PJ Standards`, `Big Tex Standards`, or `B-Wise Standards`).
3. Search existing SKU first (avoid duplicates).
4. Add new SKU or edit existing fields (dimensions/stack assumptions).
5. Save settings.
6. Re-open or refresh affected session and re-validate load fit before final export.

Quality rule:
If dimensions are wrong, fit recommendations are wrong. Treat SKU edits as operationally important changes.

### Where Inventory Gap Logic Fits
Inventory Gap Finder is not a separate rule system; it depends on:
- Current load geometry (remaining space and stack constraints),
- SKU assumptions from settings,
- Available inventory snapshot data (when uploaded).

## 6) Operational Guardrails and Common Mistakes

### Guardrails
- Use the ProGrade load building tool as a decision-support platform, not as a legal/compliance authority.
- Final shipping decisions should include carrier and regulatory constraints (state height limits, overhang, route-specific limits).
- Validate truck profile early; switching late can change fit outcomes materially.

### Common Mistakes To Avoid
- Building on outdated SKU assumptions.
- Forgetting to verify brand path before building (PJ vs Big Tex vs B-Wise standards differ).
- Treating "fits on schematic" as automatic "ready to ship."
- Overlooking warehouse filter context in inventory-gap recommendations.

## 7) Handoff Checklist, Ownership, and Support Model (Example Draft)

### A) Ownership Example
| Area | Recommended Owner | Backup Owner | Cadence |
|---|---|---|---|
| SKU dimensions and stack assumptions | Brand shipping lead | Operations analyst | Weekly review + ad hoc updates |
| Inventory upload/process quality | Fulfillment operations | Planning support | Daily |
| User enablement/training | Ops excellence or PM | Brand coordinator | Monthly |
| Tool admin/support triage | Internal app owner | IT support | Ongoing |

### B) Change Governance Example
1. User submits SKU/assumption update request.
2. Brand owner reviews and approves.
3. Update is made in `Settings`.
4. One representative load is re-validated.
5. Change note is logged (what changed, why, by whom, date).

### C) Support Escalation Example
1. Workflow/usage question: team super-user.
2. Data/assumption issue: brand shipping lead.
3. Tool behavior issue/bug: app owner + dev support.

### D) Transition Completion Checklist
- Document approved by operations leadership.
- Named owners assigned for each brand path.
- Initial training delivered to target users.
- First KPI baseline captured (before/after period).
- Review date scheduled (30 days after go-live use).

## 8) Suggested Screenshot Pack for Word Version

Insert these screenshots in the Word version (one per section):
1. ProGrade load building tool session start / all sessions page.
2. Load builder with drag-and-drop canvas and Truck selector visible.
3. Capacity/utilization meters in schematic view.
4. Inventory Gap Finder panel with candidate rows and stack-fit columns.
5. Settings page tabs (`Universal`, `PJ`, `Big Tex`, `B-Wise`).
6. SKU add/edit area showing key fields (deck, tongue, heights).

Suggested caption format:
- "What you are seeing"
- "Why it matters"
- "What decision to make on this screen"

---

## Appendix: Short Talking Points for Leadership
- "This tool converts expert tribal knowledge into a repeatable operating system."
- "It reduces bottlenecks around a few individuals and raises team throughput."
- "It improves freight economics through better utilization and inventory-aware recommendations."


