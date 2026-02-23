# Tutorial Media Workflow

This folder documents how to maintain the in-app Tutorial module.

## Content Source Of Truth
- Manifest: `docs/tutorial/tutorial_manifest.json`
- Media root: `static/tutorial/<module-slug>/`

## Media Standards
- Data policy: sanitized demo data only.
- Screenshot format: `.webp` preferred (SVG placeholders are acceptable until captures are ready).
- Clip format: `.mp4` (H.264), silent, 8-25 seconds target, <=1080p.
- Poster image: provide a static poster for each clip.

## Naming Convention
- Screenshots: `01-open-page.webp`
- Clips: `02-run-action.mp4`
- Clip posters: `02-run-action-poster.webp`

## Capture Checklist
1. Verify no sensitive customer/order data is visible.
2. Keep only the relevant UI section in frame.
3. Add clear callout overlays for click targets.
4. Keep one action per step (short and focused).
5. Confirm media path in manifest exactly matches file path in `static/`.

## Manifest Validation Rules
- Module required fields: `slug`, `title`, `summary`, `steps`.
- Step required fields: `id`, `title`, `instruction`, `media`.
- Media required fields: `type` (`image` or `video`), `src`.
- Invalid modules/steps are skipped at runtime with warning logs.
