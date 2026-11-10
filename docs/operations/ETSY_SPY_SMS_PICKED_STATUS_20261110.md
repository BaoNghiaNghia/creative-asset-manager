# CAM Etsy Spy Picked - SMS Idea Inbox delivery status (2026-11-10)

## Contract
Windows SMS worker holds authoritative outbound receipts in `data/sms-ideas-push.sqlite`.
CAM receives only tenant-scoped status projections from the existing authenticated
Windows Etsy Spy agent. Client permissions remain `etsy_spy.read` for viewing;
browser clients cannot mutate SMS status. No SMS token, full capture payload,
Etsy screenshot or worker log is uploaded by this status endpoint.

`POST /api/v1/etsy-spy/agents/{agent_id}/sms-status` takes at most 2,500
status records per request, with a 1 MB streamed request cap. Each record
contains `listingId`, `status`, `updatedAt`, and optional
`smsId`, `smsCode`, `sentAt`. Only a `sent` state with a receipt and
a sent timestamp can display *Đã có trên SMS*. Unpicked IDs are ignored.
Previously confirmed sends cannot be downgraded by older pending states.
PostgreSQL table `etsy_spy_sms_statuses` keyed by tenant + Etsy Listing ID
retains status across Unpick/Re-Pick and independent of Scout source sessions.

The Picked table displays the status under the product title, not in a new
column, and refreshes only its visible page every 30 seconds while active.
Unknown projection is displayed as **SMS chưa đồng bộ**, not false success.

## Release order and rollback
1. Back up production PostgreSQL and verify the rollback point.
2. Apply additive migration `0206_etsy_spy_sms_status`, then deploy CAM API,
   verify authentication restrictions and tenant-scoped projection.
3. Deploy CAM frontend from committed built assets, preserve current + one rollback.
4. Only after CAM endpoint returns 200 with authenticated status batch,
   update supervised Windows SMS worker. It uses the existing CAM agent bearer
   secret, never opens Chrome, and never blocks SMS capture when CAM sync fails.
5. Confirm statuses on the Picked table match Windows SMS receipts.
Rollback frontend/backend to prior release if needed; leave the additive
status table in place to preserve verified receipts. Schema downgrade requires
backup and intentionally drops the projection table (not recommended).
No dependency or credential changes.
