# Summary

Describe the user-visible changes, affected routes/workspaces, and any impact on authorization, state or data. Link the issue/request.

## Multi-device acceptance — required for ANY frontend UI change

See docs/operations/CAM_MULTI_DEVICE_STANDARD.md and CAM_MULTI_DEVICE_QA.md. This is mandatory for new features even when the request does not mention responsive. For backend-only/no-visible-UI changes mark N/A with a short reason.

| Device class | Tested viewport / browser | PASS / FAIL / NOT_RUN | Evidence or issue |
| --- | --- | --- | --- |
| Phone (390x844 reference, 320 when needed) | | | |
| iPad / tablet (768x1024, landscape when needed) | | | |
| Desktop (1440x900 reference) | | | |
| High-DPI media / DPR 3 (if applicable) | | | |
| Real iOS / Android / iPad (milestone releases) | | | |

- [ ] Essential actions work without hover; touch targets and focus/keyboard are usable
- [ ] Body does not overflow; modals, tables and navigation adapt at affected widths
- [ ] Loading, empty, error, selected and long-text states handled
- [ ] Search/sort/selection/retry/role behavior preserved
- [ ] Relevant feature fixtures, source tests/typecheck/build and Browser QA completed
- [ ] Console/network issues, test omissions (NOT_RUN) and risks documented

## Verification

Provide the exact local/staging commands that ran, test results and screenshot/QA paths. Do not claim unexecuted or unavailable tests passed.

## Release and rollback

Identify changed dependencies, feature flags or migrations and the rollback path if applicable. This PR does not authorize production deployment or visual-baseline acceptance.
