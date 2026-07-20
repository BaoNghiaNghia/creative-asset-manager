from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.ai_governance.pilot import PilotSelection, PilotService

def parser():
    value=argparse.ArgumentParser(description="AI pilot evaluation operations")
    value.add_argument("command",choices=["ai:pilot-create","ai:pilot-cancel","ai:pilot-resume","ai:pilot-report"])
    value.add_argument("--tenant-id",required=True)
    value.add_argument("--run-id")
    value.add_argument("--profile-id")
    value.add_argument("--asset-id",action="append",default=[])
    value.add_argument("--source-id")
    value.add_argument("--folder-path")
    value.add_argument("--created-from")
    value.add_argument("--created-to")
    value.add_argument("--maximum-items",type=int,default=100)
    value.add_argument("--sample-seed",default="0")
    value.add_argument("--golden-query",action="append",default=[])
    value.add_argument("--actor-id",default="operator-cli")
    value.add_argument("--force",action="store_true")
    value.add_argument("--format",choices=["json","csv"],default="json")
    value.add_argument("--output")
    return value

def _date(value):
    return datetime.fromisoformat(value.replace("Z","+00:00")) if value else None

def execute(args):
    with SessionLocal() as session:
        service=PilotService(session,get_settings())
        if args.command=="ai:pilot-create":
            if not args.profile_id: raise ValueError("--profile-id is required")
            run=service.create(tenant_id=args.tenant_id,metadata_profile_id=args.profile_id,
                selection=PilotSelection(tuple(args.asset_id),args.source_id,args.folder_path,
                    _date(args.created_from),_date(args.created_to),args.maximum_items,
                    args.sample_seed,tuple(args.golden_query)),
                created_by=args.actor_id,force=args.force)
            result={"pilot_run_id":run.id,"status":run.status,
                    "estimated_max_cost_micros":run.estimated_max_cost_micros}
        else:
            if not args.run_id: raise ValueError("--run-id is required")
            if args.command=="ai:pilot-cancel":
                run=service.cancel(args.tenant_id,args.run_id,args.actor_id)
                result={"pilot_run_id":run.id,"status":run.status}
            elif args.command=="ai:pilot-resume":
                run=service.resume(args.tenant_id,args.run_id,args.actor_id)
                result={"pilot_run_id":run.id,"status":run.status}
            else:
                result=service.report(args.tenant_id,args.run_id)
        session.commit()
    output=PilotService.report_csv(result) if args.format=="csv" else json.dumps(result,sort_keys=True,default=str,indent=2)
    if args.output: Path(args.output).write_text(output)
    return output

def main():
    print(execute(parser().parse_args()))
    return 0

if __name__=="__main__": raise SystemExit(main())
