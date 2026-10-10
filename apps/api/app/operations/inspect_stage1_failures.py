"""Read-only Stage 1 production failure and queue diagnostics, with no prompts or credentials."""
from __future__ import annotations
import json
from collections import Counter
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.config import get_settings
from app.modules.realistic_review_ugc.model import RrugcKeywordImageJobModel
from app.modules.processing.model import ProcessingJobModel
from app.providers.ai.codex_execution_log import read_codex_execution_log

def main():
    settings=get_settings()
    with SessionLocal() as session:
        rows=list(session.scalars(select(RrugcKeywordImageJobModel).order_by(
            RrugcKeywordImageJobModel.updated_at.desc()).limit(85)))
        allstates=Counter((r.status, r.last_error_code or "") for r in rows)
        result=[]
        for r in rows[:40]:
            process=session.get(ProcessingJobModel,r.processing_job_id) if r.processing_job_id else None
            log=read_codex_execution_log(settings.IMAGE_GENERATION_STAGING_ROOT,process.id) if process else None
            result.append({"keyword":r.keyword_text,"job_id":r.id,"skill":r.skill_name,
              "state":r.status,"error_code":r.last_error_code,
              "error_message":(r.last_error_message or "")[:160],
              "processing_id":process.id if process else None,
              "processing_state":process.status if process else None,
              "attempts":process.attempt_count if process else None,
              "log":{k:log[k] for k in ("state","event_count","last_event","stdout_bytes","stderr_bytes") if k in log} if log else None})
    print(json.dumps({"recent_status_counts":[{"status":k[0],"error":k[1],"count":v} for k,v in allstates.items()],
      "latest_jobs":result},indent=2,ensure_ascii=False))

if __name__=="__main__": main()
