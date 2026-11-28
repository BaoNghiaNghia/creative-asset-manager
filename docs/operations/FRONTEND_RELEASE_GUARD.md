# CAM frontend release guard — Color Mix V5 recovery (2026-11-28)

The November 27 visual source and the November 28 SMS Sync panel were developed in a different checkout from the November 28 Eligible/Inventory release. A valid commit and a valid Vite bundle were not sufficient: the later release lost the earlier UI work.

- Frontend-only activation: use `scripts/deploy-cam-frontend.sh` from the integrated release checkout. Do not copy a prebuilt bundle from a worker/backend branch or deploy the backend just to change the UI.
- Before a normal activation, the candidate must descend from the **currently served** frontend build commit; unknown/divergent live commits fail closed. Merge the live frontend source first.
- Candidate dist must carry the Color Mix V5 contract and local Inter/Be Vietnam Pro fonts, V5 navigation CSS and consolidated Demand CSS. Original navigation SVGs are covered by the frontend V5 regression test.
- The script serializes guarded frontend deploys and retains two releases by default. Explicit rollback is a separate operator action.
- After activation compare the public build commit to the candidate, not merely HTTP status.
- Future branches must start from or integrate the active frontend lineage and run the guard. Do not invoke older copied versions of the deploy script; those copies do not implement this check. This repository guard is not an OS-level restriction on arbitrary privileged operations.

The restoration changes no production data, backend service, or worker configuration.
