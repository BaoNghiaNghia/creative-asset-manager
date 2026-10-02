# Cursor UI design workflow

This document defines the Cursor-specific screenshot-driven design lane for Creative Asset Manager.

The project-wide source of truth for automatic UI work is `docs/operations/UI_AUTOMATION_WORKFLOW.md`. This document only adds the optional Cursor/My Machines lane.

It complements `docs/operations/CURSOR_AGENT_STAGING.md`:

```text
User screenshot / visual requirement
        |
        v
Cursor Agent on cam-vps-staging
        |
        +--> inspect existing component + CSS + behavior
        +--> implement scoped change on task branch
        +--> run staging gate
        +--> Browser QA on local/staging
        |      - default
        |      - hover
        |      - selected
        |      - selected + hover
        |      - focus
        |      - desktop/tablet/mobile where relevant
        |      - console + failed requests
        |
        v
ready handoff
        |
        v
CodeLocal review -> explicit user production deploy
```

Cursor remains a development/staging design agent. CodeLocal remains the production and Windows release gate.

## Recommended design task prompt

Use this structure when giving Cursor a UI task:

```text
Area:
Asset Explorer / Shared / Review Board / Realistic Review UGC / Desktop shell

Reference:
<attach screenshot or describe current vs desired UI>

Requirements:
- ...
- ...
- ...

Behavior that must remain unchanged:
- multi-select / drag-out / double-click / context menu / playback / search / etc.

Acceptance:
- visual match at normal state
- hover state checked
- selected state checked
- selected + hover checked
- long title / overflow checked
- responsive checked at relevant widths
- no new console errors
- no relevant failed network requests

Delivery:
1. inspect existing implementation first
2. make the smallest scoped change
3. run bash scripts/cam-cursor-staging-gate.sh
4. verify with Browser on local/staging
5. report branch/commit, screenshots/Browser evidence, tests, risks
6. do not deploy production
```

## Visual acceptance matrix

For card/grid work, Browser should check at least:

| State | What to verify |
| --- | --- |
| Default | spacing, title position, borders, controls |
| Hover | no layout shift, hover-only controls behave correctly |
| Selected | selected outline/border is unmistakable |
| Selected + hover | selected styling remains visible |
| Focus | keyboard focus remains visible and does not break layout |
| Long title | wrapping/clamping is intentional and does not touch card edges |
| Loading | skeleton does not jump the final layout |
| Mobile/touch | essential actions do not depend on hover |

For media cards also verify play controls, thumbnail crop, hover reveal, and playback affordance. For folders verify title wrapping, share/menu actions, and double-click/open behavior.

## Responsive widths

Use the nearest available Browser viewport to these targets when the page supports them:

- desktop: 1440 x 900;
- compact desktop: 1280 x 800;
- tablet landscape: 1024 x 768;
- tablet portrait: 768 x 1024;
- mobile: approximately 390 x 844.

The goal is not pixel-perfect identity across different viewport sizes. The goal is stable hierarchy, readable sizing, preserved interaction, and no clipping/overflow.

## Screenshot comparison discipline

When a user provides a screenshot:

1. identify the exact changed region;
2. note geometry before editing;
3. avoid restyling neighboring areas without a requirement;
4. verify the same region after editing;
5. check hover/selected/focus separately because screenshots often show only one state;
6. keep typography and card density consistent with the rest of the product.

If the screenshot conflicts with an accessibility or security invariant, preserve the invariant and report the conflict instead of silently weakening it.

## Browser safety

Cursor Browser may use:
- loopback development URLs;
- an explicitly designated staging URL;
- staging/test authentication.

Cursor Browser must not:
- mutate production data;
- use production secrets for convenience;
- approve destructive actions;
- deploy or roll back production.

## Handoff report

A ready UI-design handoff should include:

- task branch + commit;
- files changed;
- exact visual behavior changed;
- tests/typecheck/build result;
- Browser URL/environment;
- viewport(s) and states checked;
- screenshots captured, if any;
- console/network issues;
- migration/dependency/secret impact;
- remaining visual risk;
- rollback note.

Production is a separate CodeLocal/operator step after explicit user approval.
