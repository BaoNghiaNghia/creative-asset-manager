from __future__ import annotations
import argparse
import json

from app.core.database import SessionLocal
from app.modules.auth_persistence.service import cipher_from_settings
from app.modules.auth_persistence.repository import AuthPersistenceRepository

def rotate_keys(*, page_size: int = 100, after_id: str | None = None, dry_run: bool = False, max_pages: int | None = None):
    if page_size < 1 or page_size > 1000: raise ValueError("page_size must be between 1 and 1000")
    cursor=after_id; processed=0; pages=0
    while max_pages is None or pages < max_pages:
        with SessionLocal() as session:
            repository=AuthPersistenceRepository(session,cipher_from_settings())
            count,next_cursor=repository.rotate_page(after_id=cursor,limit=page_size,dry_run=dry_run)
            if not dry_run: session.commit()
            else: session.rollback()
        processed+=count; pages+=1
        if not count or not next_cursor: break
        cursor=next_cursor
    return {"processed":processed,"pages":pages,"cursor":cursor,"dry_run":dry_run}

def cleanup_expired_auth():
    with SessionLocal() as session:
        result=AuthPersistenceRepository(session,cipher_from_settings()).cleanup_expired()
        session.commit(); return result

def main(argv=None):
    parser=argparse.ArgumentParser(description="OAuth persistence operations")
    commands=parser.add_subparsers(dest="command",required=True)
    rotate=commands.add_parser("rotate-keys")
    rotate.add_argument("--page-size",type=int,default=100); rotate.add_argument("--after-id")
    rotate.add_argument("--max-pages",type=int); rotate.add_argument("--dry-run",action="store_true")
    commands.add_parser("cleanup-expired")
    revoke=commands.add_parser("revoke-connection")
    revoke.add_argument("--tenant",required=True); revoke.add_argument("--provider",choices=["google","microsoft"],required=True)
    revoke.add_argument("--account",required=True); revoke.add_argument("--reason",default="Operator revocation")
    args=parser.parse_args(argv)
    if args.command=="rotate-keys":
        result=rotate_keys(page_size=args.page_size,after_id=args.after_id,max_pages=args.max_pages,dry_run=args.dry_run)
    elif args.command=="cleanup-expired":
        result=cleanup_expired_auth()
    else:
        with SessionLocal() as session:
            changed=AuthPersistenceRepository(session,cipher_from_settings()).revoke_connection(tenant_id=args.tenant,provider=args.provider,provider_account_id=args.account,actor_id="operator",reason=args.reason)
            session.commit(); result={"revoked":changed}
    print(json.dumps(result,sort_keys=True))
    return 0

if __name__=="__main__": raise SystemExit(main())
