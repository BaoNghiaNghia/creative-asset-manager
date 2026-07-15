import asyncio
import logging
from datetime import datetime, timezone

from app.modules.explorer.schema import IndexRequest, IndexStatus, SearchRequest
from app.modules.explorer.service import ExplorerService

logger = logging.getLogger(__name__)
_jobs: dict[str, IndexStatus] = {}
_tasks: dict[str, asyncio.Task] = {}


def get_index_status(account_id: str) -> IndexStatus:
    return _jobs.get(account_id, IndexStatus())


def start_index_job(
    account_id: str,
    access_token: str | None,
    body: IndexRequest,
) -> IndexStatus:
    current_task = _tasks.get(account_id)
    if current_task and not current_task.done():
        return get_index_status(account_id)

    job = IndexStatus(
        state="running",
        status="Preparing Google Drive metadata index",
        progress=1,
        started_at=datetime.now(timezone.utc),
    )
    _jobs[account_id] = job

    async def report(event: dict):
        job.status = str(event.get("status") or job.status)
        job.progress = max(job.progress, min(99, int(event.get("progress") or 0)))
        job.indexed_count = int(event.get("indexed_count") or job.indexed_count)
        job.processed_folders = int(event.get("processed_folders") or 0)
        job.pending_folders = int(event.get("pending_folders") or 0)
        job.skipped_folders = int(event.get("skipped_folders") or job.skipped_folders)

    async def run():
        try:
            result = await ExplorerService().search_subtree(
                SearchRequest(
                    query="__metadata_index_only__",
                    root_id=body.root_id,
                    ancestor_ids=body.ancestor_ids,
                    ancestor_names=body.ancestor_names,
                    limit=1,
                ),
                access_token,
                account_id,
                progress=report,
            )
            job.state = "completed"
            job.skipped_folders = result.skipped_folders
            job.status = (
                f"Metadata ready; skipped {result.skipped_folders} inaccessible folders"
                if result.skipped_folders
                else "Google Drive metadata is ready"
            )
            job.progress = 100
            job.indexed_count = result.indexed_count
            job.pending_folders = 0
            job.completed_at = datetime.now(timezone.utc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Google Drive metadata indexing failed for the current account")
            job.state = "failed"
            job.status = "Metadata indexing failed"
            job.error = str(exc) or type(exc).__name__
            job.pending_folders = 0
            job.completed_at = datetime.now(timezone.utc)

    _tasks[account_id] = asyncio.create_task(run())
    return job
