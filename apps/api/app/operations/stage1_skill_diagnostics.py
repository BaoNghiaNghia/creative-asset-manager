"""Read-only Stage 1 image generation policy diagnostics without secrets or prompt text."""
from collections import Counter
from pathlib import Path
import json
import re
from sqlalchemy import select, func
from app.core.database import SessionLocal
from app.core.config import get_settings
from app.modules.realistic_review_ugc.model import (
    RrugcStageSkillDefaultModel, RrugcStage2SkillRegistryModel,
    RrugcKeywordImageJobModel, RrugcImageOutputVersionModel,
)
from app.modules.processing.model import ProcessingJobModel
from app.providers.ai.codex_image import list_codex_skill_manifests

def main():
    settings=get_settings()
    with SessionLocal() as session:
        defaults=list(session.execute(
            select(RrugcStage2SkillRegistryModel.skill_name)
            .join(RrugcStageSkillDefaultModel,
                RrugcStageSkillDefaultModel.registry_id==RrugcStage2SkillRegistryModel.id)
            .where(RrugcStageSkillDefaultModel.stage=="stage1")
        ))
        jobs=list(session.scalars(select(RrugcKeywordImageJobModel).order_by(
            RrugcKeywordImageJobModel.updated_at.desc()).limit(24)))
        skill_names={name for (name,) in defaults} | {job.skill_name for job in jobs} | {"gatorhats-keyword-embroidery"}
        skill_details={}
        for name in sorted(skill_names):
            path=(Path(settings.CODEX_IMAGE_HOME)/"skills"/name/"SKILL.md")
            matches=[]
            if path.is_file():
                for line in path.read_text(encoding="utf-8",errors="replace").splitlines():
                    if re.search(r"(?i)(six|\b6\b|ten|\b10\b|hundred|\b100\b|image count|concept|design|output|iteration|variant)", line):
                        matches.append(line.strip()[:180])
            skill_details[name]={"skill_installed":path.is_file(),"count_instructions":matches[:25]}
        results=[]
        for job in jobs:
            count=int(session.scalar(select(func.count()).select_from(RrugcImageOutputVersionModel).where(
                RrugcImageOutputVersionModel.stage=="stage1",
                RrugcImageOutputVersionModel.job_id==job.id,
                RrugcImageOutputVersionModel.tenant_id==job.tenant_id
            )) or 0)
            p=session.get(ProcessingJobModel,job.processing_job_id) if job.processing_job_id else None
            results.append({"skill":job.skill_name,"status":job.status,"versions":count,
                            "processing_status":p.status if p else None})
        manifests=list_codex_skill_manifests(settings.CODEX_IMAGE_HOME)
        print(json.dumps({"installed_skill_names":[{"name":m.skill_name,"workflows":m.workflows} for m in manifests],
                          "default_skills":[name for (name,) in defaults],
                          "skills":skill_details,"recent_jobs":results},indent=2,ensure_ascii=False))

if __name__=="__main__": main()
