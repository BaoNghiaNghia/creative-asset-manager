"""Durable sequential GatorHats Products Stage 2 Run all (1 -> 7).

Run-all only queues the current step. Three scene shots and thirteen color
shots may run concurrently *within their own slot*. Next slot starts only once
all outputs in the previous slot have been saved. Manual single-slot Run,
completed historical jobs and retry limits remain untouched.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.colorways import (
    COLORS, ColorwayError, ColorwayService, effective_status as color_effective_status,
    PRODUCTS_STAGE1_SELECTED_ROOT,
)
from app.modules.realistic_review_ugc.ecommerce_pipeline import (
    EcommerceError, EcommerceService, SCENE_GROUPS,
    _effective_status as ecommerce_effective_status,
)
from app.modules.realistic_review_ugc.model import (
    RrugcProductsRunAllModel, RrugcSourcePlanModel, RrugcEcommerceJobModel,
    RrugcColorwayJobModel, RrugcStageSkillDefaultModel, RrugcStage2SkillRegistryModel,
)

logger = logging.getLogger(__name__)
SLOT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("prod_s2_ec1",),
    ("prod_s2_ec2",),
    ("prod_s2_ec3",),
    ("prod_s2_ec4",),
    SCENE_GROUPS["prod_s2_mh"],
    SCENE_GROUPS["prod_s2_ugc"],
    tuple(color for color, _label in COLORS),
)
SLOT_LABELS = (
    "Ecommerce · Image 01", "Ecommerce · Image 02",
    "Ecommerce · Image 03", "Ecommerce · Image 04",
    "Multi-hat", "UGC", "13 Hat Colors",
)


def _run_query(tenant_id: str, source_plan_id: str, source_revision: str, color_key: str):
    return select(RrugcProductsRunAllModel).where(
        RrugcProductsRunAllModel.tenant_id == tenant_id,
        RrugcProductsRunAllModel.source_plan_id == source_plan_id,
        RrugcProductsRunAllModel.source_revision == source_revision,
        RrugcProductsRunAllModel.color_key == color_key,
    )


def _jobs_for_slot(session: Session, run: RrugcProductsRunAllModel, position: int):
    if position == 7:
        rows = session.scalars(select(RrugcColorwayJobModel).where(
            RrugcColorwayJobModel.tenant_id == run.tenant_id,
            RrugcColorwayJobModel.source_plan_id == run.source_plan_id,
            RrugcColorwayJobModel.source_revision == run.source_revision,
            RrugcColorwayJobModel.color_key.in_(SLOT_GROUPS[6]),
        )).all()
        # Return all colorway rows, regardless of selected Ecommerce cap color.
        return {row.color_key: (row, color_effective_status(
            row, session.get(ProcessingJobModel, row.processing_job_id)
            if row.processing_job_id else None,
        )) for row in rows}
    rows = session.scalars(select(RrugcEcommerceJobModel).where(
        RrugcEcommerceJobModel.tenant_id == run.tenant_id,
        RrugcEcommerceJobModel.source_plan_id == run.source_plan_id,
        RrugcEcommerceJobModel.source_revision == run.source_revision,
        RrugcEcommerceJobModel.color_key == run.color_key,
        RrugcEcommerceJobModel.stage.in_(SLOT_GROUPS[position - 1]),
    )).all()
    return {row.stage: (row, ecommerce_effective_status(
        row, session.get(ProcessingJobModel, row.processing_job_id)
        if row.processing_job_id else None,
    )) for row in rows}


def _slot_state(session: Session, run: RrugcProductsRunAllModel, position: int) -> tuple[str, str | None]:
    jobs = _jobs_for_slot(session, run, position)
    required = SLOT_GROUPS[position - 1]
    if all(key in jobs and jobs[key][1] == "completed"
           and bool(jobs[key][0].output_remote_file_id) for key in required):
        return "completed", None
    for key in required:
        if key in jobs and jobs[key][1] == "failed":
            return "failed", f"Output {key} failed. Retry the failed job manually, then press Run all to resume."
        if key in jobs and jobs[key][1] == "completed" and not jobs[key][0].output_remote_file_id:
            return "failed", f"Output {key} is marked completed without a saved image."
    # Recover a partially enqueued three-scene / thirteen-color batch after a
    # crash. Missing siblings must be queued before this slot can be considered
    # ready; existing completed and in-progress siblings are left untouched.
    if any(key not in jobs for key in required):
        return "missing", None
    if any(jobs[key][1] in ("queued", "running") for key in required):
        return "running", None
    return "missing", None


def _status(session: Session, run: RrugcProductsRunAllModel) -> dict:
    steps = []
    for position, label in enumerate(SLOT_LABELS, 1):
        result, error = _slot_state(session, run, position)
        if result == "completed":
            status = "completed"
        elif position < run.current_slot:
            status = "completed"  # previous step already audited by the coordinator
        elif run.status == "blocked" and position == run.current_slot:
            status = "blocked"
        elif position > run.current_slot:
            status = "pending"
        elif result == "running":
            status = "running"
        else:
            status = "pending"
        steps.append({
            "stage": SLOT_GROUPS[position - 1][0] if position < 7 else "prod_s2_col",
            "slot": position, "name": label, "status": status,
            "completed_outputs": sum(
                1 for key, (job, state) in _jobs_for_slot(session, run, position).items()
                if state == "completed" and job.output_remote_file_id
            ),
            "total_outputs": len(SLOT_GROUPS[position - 1]),
            "reason": error if status == "blocked" else None,
        })
    return {
        "source_plan_id": run.source_plan_id,
        "source_revision": run.source_revision,
        "color_key": run.color_key,
        "run_id": run.id,
        "status": run.status,
        "current_slot": run.current_slot,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "steps": steps,
    }


def _compact(run: RrugcProductsRunAllModel) -> dict:
    """Worker/scheduler result without expensive full seven-slot status queries."""
    return {"run_id": run.id, "status": run.status, "current_slot": run.current_slot}


class ProductsRunAllService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def status(self, *, tenant_id: str, source_plan_id: str,
               color_key: str = "natural-black") -> dict | None:
        plan = self.session.scalar(select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == tenant_id,
            RrugcSourcePlanModel.id == source_plan_id,
            RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
        ))
        if not plan or plan.status == "missing":
            return None
        run = self.session.scalar(_run_query(tenant_id, source_plan_id, plan.source_revision, color_key))
        return _status(self.session, run) if run else None

    def start(self, *, tenant_id: str, user_id: str, source_plan_id: str,
              color_key: str = "natural-black", colorway_skill: dict | None = None) -> dict:
        """Explicit opt-in; never backfill across unrelated designs or cap colors."""
        if not all((
            self.settings.PROCESSING_JOBS_ENABLED, self.settings.IMAGE_GENERATION_ENABLED,
            self.settings.MANAGED_ASSET_STORAGE_ENABLED, self.settings.CODEX_IMAGE_GENERATION_ENABLED,
        )):
            raise EcommerceError("run_all_disabled", "Image worker is disabled.", 503)
        plan = self.session.scalar(select(RrugcSourcePlanModel).where(
            RrugcSourcePlanModel.tenant_id == tenant_id,
            RrugcSourcePlanModel.id == source_plan_id,
            RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
        ).with_for_update())
        if not plan or plan.status == "missing":
            raise EcommerceError("run_all_source_missing", "Embroidery source not found.", 404)
        if color_key not in dict(COLORS):
            raise EcommerceError("run_all_color_invalid", "Invalid selected cap color.", 422)
        run = self.session.scalar(_run_query(tenant_id, plan.id, plan.source_revision, color_key).with_for_update())
        if run is None:
            selected = colorway_skill or self._default_colorway_skill(tenant_id)
            run = RrugcProductsRunAllModel(
                tenant_id=tenant_id, source_plan_id=source_plan_id,
                source_revision=plan.source_revision, color_key=color_key,
                started_by_user_id=user_id, colorway_skill_json=selected,
                status="running", current_slot=1,
            )
            self.session.add(run)
            self.session.flush()
        elif run.status == "blocked":
            # Re-click is an explicit resume; failed individual jobs must first
            # be retried manually, never silently re-generated by Run all.
            run.status = "running"
            run.error_code = run.error_message = None
        elif run.status == "completed":
            return _status(self.session, run)
        # Old Run all stored simultaneous EC2+EC3 follow-ups on Front payload.
        # Remove this obsolete fanout only when a sequential run is requested.
        self._remove_legacy_front_fanout(run)
        run_id = run.id
        self.session.commit()
        self.advance(run_id)
        return self.status(tenant_id=tenant_id, source_plan_id=source_plan_id, color_key=color_key)

    def _default_colorway_skill(self, tenant_id: str) -> dict:
        selection = self.session.get(RrugcStageSkillDefaultModel, (tenant_id, "prod_s2_col"))
        row = self.session.get(RrugcStage2SkillRegistryModel, selection.registry_id) if selection and selection.registry_id else None
        if not row or not row.enabled or row.deleted_at is not None or row.validation_status != "valid":
            raise EcommerceError("run_all_color_skill_missing", "Assign a valid Slot 7 Skill first.")
        return {"source": row.source, "skill_id": row.skill_id, "skill_name": row.skill_name,
                "skill_version": row.synced_version or row.default_version}

    def _remove_legacy_front_fanout(self, run: RrugcProductsRunAllModel) -> None:
        front = _jobs_for_slot(self.session, run, 1).get("prod_s2_ec1")
        job = front[0] if front else None
        processing = self.session.get(ProcessingJobModel, job.processing_job_id) if job and job.processing_job_id else None
        if processing and (processing.payload_json or {}).get("run_all_followups"):
            payload = dict(processing.payload_json)
            for field in ("run_all_followups", "run_all_user_id", "run_all_color_key"):
                payload.pop(field, None)
            processing.payload_json = payload

    def advance(self, run_id: str) -> dict | None:
        """One locked advance; safe to retry after crashes or concurrent callbacks."""
        run = self.session.scalar(select(RrugcProductsRunAllModel).where(
            RrugcProductsRunAllModel.id == run_id,
        ).with_for_update())
        if not run or run.status != "running":
            return _compact(run) if run else None
        try:
            plan = self.session.get(RrugcSourcePlanModel, run.source_plan_id)
            if (not plan or plan.tenant_id != run.tenant_id
                    or plan.root_folder_id != PRODUCTS_STAGE1_SELECTED_ROOT
                    or plan.status == "missing" or plan.source_revision != run.source_revision):
                return self._block(
                    run, "run_all_source_changed",
                    "Embroidery source changed during Run all. Existing outputs were preserved; start a new revision explicitly.",
                )
            # Completed intermediate steps may be skipped without generating.
            for position in range(run.current_slot, 8):
                result, error = _slot_state(self.session, run, position)
                if result == "completed":
                    run.current_slot = min(position + 1, 7)
                    self.session.flush()
                    if position == 7:
                        run.status = "completed"
                        run.completed_at = datetime.now(timezone.utc)
                        self.session.commit()
                        return _compact(run)
                    continue
                run.current_slot = position
                if result == "failed":
                    return self._block(run, "run_all_slot_failed", error or "A previous output failed.")
                if result == "running":
                    self.session.commit()
                    return _compact(run)
                self.session.flush()
                if position <= 4:
                    EcommerceService(self.session, self.settings).queue(
                        tenant_id=run.tenant_id, user_id=run.started_by_user_id,
                        source_plan_id=run.source_plan_id,
                        stage=SLOT_GROUPS[position - 1][0], color_key=run.color_key,
                    )
                elif position in (5, 6):
                    slot = "prod_s2_mh" if position == 5 else "prod_s2_ugc"
                    result = EcommerceService(self.session, self.settings).queue_scene(
                        tenant_id=run.tenant_id, user_id=run.started_by_user_id,
                        source_plan_id=run.source_plan_id, slot=slot, color_key=run.color_key,
                    )
                    errors = [step.get("reason", "Scene job blocked") for step in result["steps"]
                              if step.get("status") == "blocked"]
                    if errors:
                        return self._block(run, "run_all_scene_blocked", "; ".join(errors))
                else:
                    selection = dict(run.colorway_skill_json or {})
                    if not selection.get("skill_name"):
                        return self._block(run, "run_all_color_skill_missing",
                                           "No selected Slot 7 Skill was recorded.")
                    ColorwayService(self.session, self.settings).queue(
                        tenant_id=run.tenant_id, user_id=run.started_by_user_id,
                        source_plan_ids=[run.source_plan_id], workflow="products",
                        skill_source=selection.get("source"), skill_id=selection.get("skill_id"),
                        skill_name=selection.get("skill_name"), skill_version=selection.get("skill_version"),
                    )
                self.session.commit()
                return _compact(run)
        except (EcommerceError, ColorwayError) as exc:
            self.session.rollback()
            locked = self.session.get(RrugcProductsRunAllModel, run_id)
            if locked:
                return self._block(locked, exc.code, exc.message)
            return None
        except Exception:
            self.session.rollback()
            logger.exception("products_run_all_advance_failed", extra={"run_id": run_id})
            # Keep running for the background reconciler to retry after transient
            # service/DB errors instead of destroying a committed upstream output.
            return None
        return _compact(run)

    def _block(self, run: RrugcProductsRunAllModel, code: str, detail: str) -> dict:
        run.status = "blocked"
        run.error_code = code[:100]
        run.error_message = detail[:500]
        self.session.commit()
        return _compact(run)


def advance_after_output(session_factory: Callable[[], Session], *,
                         tenant_id: str, source_plan_id: str, source_revision: str,
                         color_key: str | None) -> None:
    """Worker completion hook; no provider retry on scheduling error.

    For Ecommerce, advance the exact cap color. Colorway outputs are shared
    across cap colors, so only look up explicitly running Slot 7 sequences.
    """
    with session_factory() as session:
        if color_key is None:
            ids = list(session.scalars(select(RrugcProductsRunAllModel.id).where(
                RrugcProductsRunAllModel.tenant_id == tenant_id,
                RrugcProductsRunAllModel.source_plan_id == source_plan_id,
                RrugcProductsRunAllModel.source_revision == source_revision,
                RrugcProductsRunAllModel.status == "running",
                RrugcProductsRunAllModel.current_slot == 7,
            ).limit(16)))
        else:
            run = session.scalar(_run_query(tenant_id, source_plan_id, source_revision, color_key))
            ids = [run.id] if run and run.status == "running" else []
    for run_id in ids:
        with session_factory() as session:
            ProductsRunAllService(session).advance(run_id)


class ProductsRunAllScheduler:
    """Lightweight recovery for crashes between an output commit and handoff.

    Checks only explicitly active runs in indexed batches; never scans every
    image or reactivates blocked/completed runs.
    """
    def __init__(self, session_factory: Callable[[], Session],
                 settings: Settings, *, interval_seconds: int = 30):
        self.session_factory = session_factory
        self.settings = settings
        self.interval_seconds = max(15, interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.settings.PROCESSING_JOBS_ENABLED or self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="rrugc-products-run-all",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("products_run_all_reconcile_failed")
            self._stop.wait(self.interval_seconds)

    def tick(self) -> int:
        if not self.settings.PROCESSING_JOBS_ENABLED:
            return 0
        with self.session_factory() as session:
            ids = list(session.scalars(select(RrugcProductsRunAllModel.id).where(
                RrugcProductsRunAllModel.status == "running",
            ).order_by(RrugcProductsRunAllModel.updated_at).limit(50)))
        for run_id in ids:
            try:
                with self.session_factory() as session:
                    ProductsRunAllService(session, self.settings).advance(run_id)
            except Exception:
                logger.exception("products_run_all_tick_failed", extra={"run_id": run_id})
        return len(ids)