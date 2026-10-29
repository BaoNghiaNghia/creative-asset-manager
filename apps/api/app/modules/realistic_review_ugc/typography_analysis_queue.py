"""G3 durable Style DNA queue storage/lease primitives.

No public routes, background workers or AI provider calls are registered here.
All writes require explicit feature+tenant-canary gates from a trusted caller,
and the caller must commit the transaction before any external provider work.
PostgreSQL claims are serialized per tenant with a transaction advisory lock,
then use SKIP LOCKED; SQLite supports deterministic single-writer unit tests.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.typography_analysis_policy import (
    BACKOFF_SECONDS, MAX_ATTEMPTS, MAX_CONCURRENCY, analysis_decision,
)
from app.modules.realistic_review_ugc.typography_dna_contract import (
    TypographyDnaContractError, validate_typography_dna,
)
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel as Reference,
    RrugcTypographyStyleDnaModel as Dna,
    RrugcTypographyAnalysisJobModel as Job,
)

VERIFIED_RIGHTS = frozenset({"owned", "licensed", "permitted_reference"})
VERSION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
LEASE_SECONDS = 120


def _clock(now: datetime | None) -> datetime:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return clock.astimezone(timezone.utc)


def _aware(value: datetime) -> datetime:
    # SQLite strips timezone information; persisted timestamps are UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class TypographyAnalysisQueue:
    def __init__(self, session: Session):
        self.session = session

    def _tenant_lock(self, tenant_id: str) -> None:
        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(722343, hashtext(:tenant))"),
                {"tenant": tenant_id},
            )

    def enqueue(
        self, *, tenant_id: str, reference_id: str, user_id: str,
        analyzer_version: str, enabled: bool = False, tenant_allowed: bool = False,
        manage: bool = False, now: datetime | None = None,
    ) -> tuple[Job | None, str]:
        """Idempotently queue an authorized, rights-verified reference.

        Locks the parent reference to serialize concurrent PostgreSQL enqueues.
        The unique DB key is a second defense and enforces SQLite/other writers.
        """
        if not enabled or not tenant_allowed:
            return None, "feature_disabled"
        if not VERSION.fullmatch(analyzer_version):
            raise ValueError("Invalid analyzer version")
        if not tenant_id or not reference_id or not user_id:
            raise ValueError("Tenant, reference and actor required")
        visibility = (
            True if manage else (
                (Reference.privacy == "workspace") |
                (Reference.created_by_user_id == user_id)
            )
        )
        ref = self.session.scalar(
            select(Reference).where(
                Reference.tenant_id == tenant_id,
                Reference.id == reference_id,
                visibility,
            ).with_for_update()
        )
        if ref is None:
            return None, "reference_unavailable"
        decision = analysis_decision(
            enabled=True, tenant_allowed=True, rights=ref.source_rights_status,
            editorial_status=ref.editorial_status, status="pending",
            attempts=0, active_jobs=0,
        )
        if decision.action != "dispatch":
            return None, decision.reason
        cached = self.session.scalar(
            select(Dna.id).where(
                Dna.tenant_id == tenant_id,
                Dna.reference_id == reference_id,
                Dna.analyzer_version == analyzer_version,
                Dna.image_hash == ref.content_hash,
            )
        )
        if cached is not None:
            return None, "cache_hit"
        existing = self.session.scalar(
            select(Job).where(
                Job.tenant_id == tenant_id,
                Job.reference_id == reference_id,
                Job.analyzer_version == analyzer_version,
                Job.image_hash == ref.content_hash,
            )
        )
        if existing is not None:
            return existing, "existing"
        job = Job(
            tenant_id=tenant_id, reference_id=reference_id,
            analyzer_version=analyzer_version, image_hash=ref.content_hash,
            status="queued", attempt_count=0, next_attempt_at=_clock(now),
        )
        self.session.add(job)
        self.session.flush()
        return job, "created"

    def _recover_expired(self, tenant_id: str, now: datetime, *, limit: int = 20) -> None:
        """Bounded lease recovery; caller already holds tenant advisory lock."""
        expired = self.session.scalars(
            select(Job).where(
                Job.tenant_id == tenant_id,
                Job.status == "running",
                Job.lease_expires_at <= now,
            ).order_by(Job.lease_expires_at, Job.id)
            .limit(limit).with_for_update(skip_locked=True)
        ).all()
        for job in expired:
            exhausted = job.attempt_count >= MAX_ATTEMPTS
            job.status = "failed" if exhausted else "queued"
            job.next_attempt_at = (
                None if exhausted else
                now + timedelta(seconds=BACKOFF_SECONDS[min(job.attempt_count - 1, len(BACKOFF_SECONDS) - 1)])
            )
            job.last_error_code = "lease_expired"
            job.lease_token = None
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            ref = self.session.scalar(
                select(Reference).where(
                    Reference.tenant_id == tenant_id,
                    Reference.id == job.reference_id,
                )
            )
            if ref is not None and ref.content_hash == job.image_hash and ref.analysis_status == "running":
                other_running = self.session.scalar(
                    select(func.count()).select_from(Job).where(
                        Job.tenant_id == tenant_id,
                        Job.reference_id == job.reference_id,
                        Job.id != job.id,
                        Job.status == "running",
                        Job.lease_expires_at > now,
                    )
                ) or 0
                if not other_running:
                    ref.analysis_status = "failed" if exhausted else "pending"

    def claim(
        self, *, tenant_id: str, owner: str,
        enabled: bool = False, tenant_allowed: bool = False,
        concurrency: int = 1, now: datetime | None = None,
    ) -> Job | None:
        """Claim at most one eligible job; no provider execution in the transaction.

        Per-tenant PostgreSQL advisory lock serializes the capacity count and claim.
        Worker heartbeats and completions additionally use lease-token CAS.
        """
        if not enabled or not tenant_allowed:
            return None
        if not (1 <= concurrency <= MAX_CONCURRENCY):
            raise ValueError("Concurrency out of bounds")
        if not owner or len(owner) > 128 or not tenant_id:
            raise ValueError("Invalid worker identity or tenant")
        clock = _clock(now)
        self._tenant_lock(tenant_id)
        self._recover_expired(tenant_id, clock)
        running = self.session.scalar(
            select(func.count()).select_from(Job).where(
                Job.tenant_id == tenant_id,
                Job.status == "running",
                Job.lease_expires_at > clock,
            )
        ) or 0
        if running >= concurrency:
            return None

        # Process a bounded number of stale cached rows without any AI calls.
        for _ in range(12):
            job = self.session.scalar(
                select(Job).join(
                    Reference,
                    (Reference.tenant_id == Job.tenant_id) &
                    (Reference.id == Job.reference_id),
                ).where(
                    Job.tenant_id == tenant_id,
                    Job.status == "queued",
                    Job.attempt_count < MAX_ATTEMPTS,
                    Job.next_attempt_at <= clock,
                    Reference.source_rights_status.in_(VERIFIED_RIGHTS),
                    Reference.editorial_status.notin_(("archived", "rejected")),
                    Reference.content_hash == Job.image_hash,
                ).order_by(Job.next_attempt_at, Job.created_at, Job.id)
                .limit(1).with_for_update(of=Job, skip_locked=True)
            )
            if job is None:
                return None
            cached = self.session.scalar(
                select(Dna.id).where(
                    Dna.tenant_id == tenant_id,
                    Dna.reference_id == job.reference_id,
                    Dna.analyzer_version == job.analyzer_version,
                    Dna.image_hash == job.image_hash,
                )
            )
            if cached:
                job.status = "completed"
                job.completed_at = clock
                job.next_attempt_at = None
                ref = self.session.scalar(select(Reference).where(
                    Reference.tenant_id == tenant_id,
                    Reference.id == job.reference_id,
                ))
                if ref is not None and ref.analysis_status != "running":
                    ref.analysis_status = "ready"
                self.session.flush()
                continue

            job.status = "running"
            job.attempt_count += 1
            job.started_at = clock
            job.lease_token = str(uuid4())
            job.lease_owner = owner
            job.lease_expires_at = clock + timedelta(seconds=LEASE_SECONDS)
            job.heartbeat_at = clock
            job.next_attempt_at = None
            job.last_error_code = None
            ref = self.session.scalar(select(Reference).where(
                Reference.tenant_id == tenant_id, Reference.id == job.reference_id,
            ))
            if ref is not None:
                ref.analysis_status = "running"
            self.session.flush()
            return job
        return None

    def heartbeat(
        self, *, tenant_id: str, job_id: str, token: str,
        enabled: bool = False, tenant_allowed: bool = False,
        now: datetime | None = None,
    ) -> bool:
        """CAS prevents a dead/replaced worker from renewing someone else's lease."""
        if not enabled or not tenant_allowed or not token:
            return False
        clock = _clock(now)
        # Serialize lease renewal with tenant capacity claims to avoid
        # counting an uncommitted renewal as an expired worker.
        self._tenant_lock(tenant_id)
        authorized_reference = select(Reference.id).where(
            Reference.tenant_id == Job.tenant_id,
            Reference.id == Job.reference_id,
            Reference.content_hash == Job.image_hash,
            Reference.source_rights_status.in_(VERIFIED_RIGHTS),
            Reference.editorial_status.notin_(("archived", "rejected")),
        ).exists()
        result = self.session.execute(
            update(Job).where(
                Job.tenant_id == tenant_id, Job.id == job_id,
                Job.status == "running", Job.lease_token == token,
                Job.lease_expires_at > clock, authorized_reference,
            ).values(
                heartbeat_at=clock,
                lease_expires_at=clock + timedelta(seconds=LEASE_SECONDS),
                updated_at=clock,
            ).execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    def _owned(self, tenant_id: str, job_id: str, token: str, now: datetime) -> Job | None:
        if not token:
            return None
        job = self.session.scalar(
            select(Job).where(
                Job.tenant_id == tenant_id,
                Job.id == job_id,
                Job.status == "running",
                Job.lease_token == token,
            ).with_for_update()
        )
        if job is None or job.lease_expires_at is None or _aware(job.lease_expires_at) <= now:
            return None
        return job

    @staticmethod
    def _clear_lease(job: Job) -> None:
        job.lease_token = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None

    def fail(
        self, *, tenant_id: str, job_id: str, token: str,
        code: str, retryable: bool = True,
        now: datetime | None = None,
    ) -> str:
        if not ERROR_CODE.fullmatch(code):
            raise ValueError("Error code must be a bounded machine code")
        clock = _clock(now)
        job = self._owned(tenant_id, job_id, token, clock)
        if job is None:
            return "stale_lease"
        can_retry = retryable and job.attempt_count < MAX_ATTEMPTS
        job.status = "queued" if can_retry else ("failed" if retryable else "needs_review")
        job.next_attempt_at = (
            clock + timedelta(seconds=BACKOFF_SECONDS[min(job.attempt_count - 1, len(BACKOFF_SECONDS) - 1)])
            if can_retry else None
        )
        job.last_error_code = code
        self._clear_lease(job)
        ref = self.session.scalar(
            select(Reference).where(
                Reference.tenant_id == tenant_id, Reference.id == job.reference_id,
            ).with_for_update()
        )
        if ref and ref.content_hash == job.image_hash:
            ref.analysis_status = "pending" if can_retry else (
                "failed" if retryable else "needs_review"
            )
        self.session.flush()
        return job.status

    def complete(
        self, *, tenant_id: str, job_id: str, token: str,
        payload: dict, enabled: bool = False, tenant_allowed: bool = False,
        now: datetime | None = None,
    ) -> str:
        """Atomic DNA commit only for an enabled, rights-verified active lease."""
        if not enabled or not tenant_allowed:
            return "feature_disabled"
        clock = _clock(now)
        job = self._owned(tenant_id, job_id, token, clock)
        if job is None:
            return "stale_lease"
        ref = self.session.scalar(
            select(Reference).where(
                Reference.tenant_id == tenant_id, Reference.id == job.reference_id,
            ).with_for_update()
        )
        if (
            ref is None or ref.content_hash != job.image_hash
            or ref.source_rights_status not in VERIFIED_RIGHTS
            or ref.editorial_status in {"archived", "rejected"}
        ):
            return self.fail(
                tenant_id=tenant_id, job_id=job_id, token=token,
                code="reference_changed", retryable=False, now=clock,
            )
        # Reject untrusted AI output; no unsafe provider text is persisted.
        try:
            dna = validate_typography_dna(payload, source_sha256=job.image_hash)
            if dna["analyzer_version"] != job.analyzer_version:
                raise TypographyDnaContractError("Analyzer version mismatch")
        except TypographyDnaContractError:
            return self.fail(
                tenant_id=tenant_id, job_id=job_id, token=token,
                code="invalid_style_dna", retryable=False, now=clock,
            )
        cached = self.session.scalar(
            select(Dna.id).where(
                Dna.tenant_id == tenant_id, Dna.reference_id == job.reference_id,
                Dna.analyzer_version == job.analyzer_version,
                Dna.image_hash == job.image_hash,
            )
        )
        if cached is None:
            self.session.add(Dna(
                tenant_id=tenant_id, reference_id=job.reference_id,
                analyzer_version=job.analyzer_version, image_hash=job.image_hash,
                schema_version=dna["schema_version"],
                typography_region_json=dna["typography_region"],
                style_family=dna["style_family"],
                font_mood_json=dna["font_mood"],
                layout_graph_json=dna["layout_graph"],
                overlap_rules_json=dna["overlap_rules"],
                confidence=dna["confidence"],
            ))
        job.status = "completed"
        job.completed_at = clock
        job.next_attempt_at = None
        job.last_error_code = None
        self._clear_lease(job)
        ref.analysis_status = "ready"
        self.session.flush()
        return "completed"
