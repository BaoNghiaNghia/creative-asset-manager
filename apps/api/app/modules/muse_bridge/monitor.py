"""Read-only operator view. No job writes, credential exposure, or alert delivery."""
from datetime import datetime, timezone

def derive_alerts(report, now=None):
    """Return stable active incident IDs; historical errors remain in the timeline."""
    now = now or datetime.now(timezone.utc)
    alerts = []
    def add(key, severity, message):
        alerts.append({"id": key, "severity": severity, "message": message})
    recovery = report.get("queueRecovery", {})
    if recovery.get("enabled") and recovery.get("status") in ("STALE", "LAST_SWEEP_FAILED", "NOT_OBSERVED"):
        add("queue-recovery", "error", "Queue recovery is not confirmed: " + recovery["status"])
    if not recovery.get("enabled"):
        add("queue-recovery-disabled", "warning", "Automatic queue recovery is disabled.")
    for worker in report.get("workers", []):
        wid = worker["workerId"]
        ready = worker.get("executionReadiness", {}).get("ready")
        status = worker.get("status")
        cool = worker.get("cooldown", {})
        if ready is not False and status in ("NO_RECENT_SIGNAL", "NOT_OBSERVED"):
            add(wid+":signal", "error" if status=="NO_RECENT_SIGNAL" else "warning", wid+" has no recent runner signal.")
        elif status == "API_ACTIVE_HEARTBEAT_UNCONFIRMED":
            if worker.get("apiSignalKind") == "config_only":
                add(wid+":heartbeat", "warning", wid+" only reads configuration; no recent operational API signal or V2 heartbeat.")
            else:
                add(wid+":heartbeat", "warning", wid+" calls CAM but independent V2 heartbeat is unconfirmed.")
        if ready is False:
            add(wid+":standby", "info", wid+" is not cleared for production rendering.")
        if cool.get("cooldownActive"):
            add(wid+":cooldown", "info", wid+" is in provider cooldown; honor the existing retry schedule.")
        last = worker.get("lastError")
        if isinstance(last, dict):
            try:
                stamp = datetime.fromisoformat(last["occurredAtUtc"].replace("Z","+00:00"))
                if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
                age = (now-stamp).total_seconds()
            except (TypeError,ValueError,AttributeError,KeyError):
                age = None
            if age is not None and 0 <= age <= 900:
                code = (last.get("payload") or {}).get("errorCode") or last.get("eventType")
                add(wid+":recent-error", "warning", wid+" reported a recent error: "+str(code))
    circuit = report.get("rendererCircuit", {})
    if circuit.get("state") == "open":
        add("renderer-circuit", "warning", "Video claims paused by the renderer circuit; existing leases may finish.")
    if circuit.get("state") == "half_open":
        add("renderer-probe", "info", "Renderer circuit is waiting for a real recovery probe.")
    for family in ("commands", "jobs"):
        counts = report.get(family, {})
        expired=counts.get("garmentExpiredClaims",counts.get("expiredClaims",0))
        if expired:
            add(family+":expired", "warning", str(expired)+" expired garment "+family+" claim(s); no manual lease release.")
        if family=="commands" and counts.get("genericExpiredClaims"):
            add("generic:expired", "info", str(counts["genericExpiredClaims"])+" expired non-video task claim(s); separate from garment video recovery.")
        if counts.get("missingLeaseExpiry"):
            add(family+":missing-expiry", "error", str(counts["missingLeaseExpiry"])+" "+family+" claim(s) without an expiry; operator review required.")
    return alerts


def monitor_report(response, principal, db):
    """Require the CAM permission dependency in the public route, and scope to the configured tenant."""
    from fastapi import HTTPException
    from app.modules.muse_bridge import router as muse
    _, configured_tenant = muse._configured()
    tenant = principal.active_tenant_id
    if tenant != configured_tenant:
        raise HTTPException(404, "Muse monitor is not configured for this tenant")
    result = muse.muse_automation_diagnostics(response, tenant=tenant, db=db)
    result["alerts"] = derive_alerts(result)
    result["videoAspectPhase"] = muse._video_aspect_phase()
    result["readOnly"] = True
    from .alerting import summary
    result["watchdog"] = summary(db, tenant)
    result["externalAlertDelivery"] = result["watchdog"]["telegram"]["status"]
    return result