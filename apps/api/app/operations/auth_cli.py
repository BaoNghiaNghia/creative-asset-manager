from __future__ import annotations
import argparse
import json

from app.core.database import SessionLocal
from app.modules.auth_persistence.model import AuthAuditEventModel, TenantModel, UserModel
from app.modules.auth_persistence.service import cipher_from_settings
from app.modules.auth_persistence.repository import AuthPersistenceRepository
from app.modules.auth_persistence.tenant_membership import TenantMembershipService, normalize_slug
from app.modules.authorization.seed import seed_tenant_rbac
from app.operations.auth_migration import (
    backfill_legacy_auth_page,
    bootstrap_identity_access,
    grant_identity_platform_admin,
)

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

def bootstrap_tenant(*, user_id: str, name: str, slug: str, tenant_id: str | None, reason: str, dry_run: bool, confirmed: bool):
    if not dry_run and not confirmed:
        raise ValueError("bootstrap requires --confirm unless --dry-run is used")
    with SessionLocal() as session:
        user = session.get(UserModel, user_id)
        if user is None or user.status != "active":
            raise LookupError("active application user not found")
        service = TenantMembershipService(session)
        normalized_slug = normalize_slug(slug)
        tenant = session.get(TenantModel, tenant_id) if tenant_id else None
        if tenant is None:
            tenant = next((item[1] for item in service.list_user_tenants(user_id, include_inactive=True) if item[1].slug == normalized_slug), None)
        created = tenant is None
        if tenant is None:
            tenant = service.create_tenant(name=name, slug=normalized_slug, tenant_id=tenant_id)
        elif tenant.slug != normalized_slug:
            raise ValueError("selected tenant does not match requested slug")
        membership = service.add_member(tenant_id=tenant.id, user_id=user.id, status="active")
        session.add(AuthAuditEventModel(
            tenant_id=tenant.id,
            actor_id=user.id,
            action="tenant_bootstrapped",
            detail_json={"reason": reason[:500], "dry_run": dry_run, "tenant_created": created},
        ))
        result = {"tenant_id": tenant.id, "membership_id": membership.id, "created": created, "dry_run": dry_run}
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return result


def seed_rbac(*, tenant_id: str, reason: str, dry_run: bool, confirmed: bool):
    if not dry_run and not confirmed:
        raise ValueError("RBAC seed requires --confirm unless --dry-run is used")
    with SessionLocal() as session:
        result = seed_tenant_rbac(session, tenant_id)
        session.add(AuthAuditEventModel(
            tenant_id=tenant_id,
            actor_id="operator",
            action="tenant_rbac_seeded",
            detail_json={"reason": reason[:500], "dry_run": dry_run, **result},
        ))
        result = {"tenant_id": tenant_id, "dry_run": dry_run, **result}
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return result


def bootstrap_access(
    *,
    provider: str,
    subject: str,
    tenant_id: str | None,
    tenant_name: str | None,
    tenant_slug: str | None,
    reason: str,
    dry_run: bool,
    confirmed: bool,
):
    if not dry_run and not confirmed:
        raise ValueError("bootstrap access requires --confirm unless --dry-run is used")
    with SessionLocal() as session:
        result = bootstrap_identity_access(
            session,
            provider=provider,
            subject=subject,
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            tenant_slug=tenant_slug,
            reason=reason,
        )
        result["dry_run"] = dry_run
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return result


def grant_platform_admin(
    *,
    provider: str,
    subject: str,
    granted_by_user_id: str | None,
    reason: str,
    dry_run: bool,
    confirmed: bool,
):
    if not dry_run and not confirmed:
        raise ValueError("platform admin grant requires --confirm unless --dry-run is used")
    with SessionLocal() as session:
        result = grant_identity_platform_admin(
            session,
            provider=provider,
            subject=subject,
            granted_by_user_id=granted_by_user_id,
            reason=reason,
        )
        result["dry_run"] = dry_run
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return result


def backfill_legacy_auth(
    *,
    page_size: int,
    after_id: str | None,
    max_pages: int | None,
    actor_id: str | None,
    reason: str,
    dry_run: bool,
    confirmed: bool,
):
    if not dry_run and not confirmed:
        raise ValueError("legacy auth backfill requires --confirm unless --dry-run is used")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be positive")
    totals = {
        "processed": 0,
        "identities_created": 0,
        "memberships_created": 0,
        "sessions_updated": 0,
        "unresolved_count": 0,
        "unresolved": [],
        "pages": 0,
        "cursor": after_id,
        "dry_run": dry_run,
    }
    cursor = after_id
    while max_pages is None or totals["pages"] < max_pages:
        with SessionLocal() as session:
            page = backfill_legacy_auth_page(
                session,
                after_id=cursor,
                page_size=page_size,
                actor_id=actor_id,
                reason=reason,
            )
            if dry_run:
                session.rollback()
            else:
                session.commit()
        totals["pages"] += 1
        for key in (
            "processed",
            "identities_created",
            "memberships_created",
            "sessions_updated",
            "unresolved_count",
        ):
            totals[key] += getattr(page, key)
        totals["unresolved"].extend(
            page.unresolved[: max(0, 100 - len(totals["unresolved"]))]
        )
        totals["cursor"] = page.next_cursor or cursor
        if page.processed < page_size or page.next_cursor is None:
            break
        cursor = page.next_cursor
    return totals


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
    bootstrap=commands.add_parser("bootstrap-tenant")
    bootstrap.add_argument("--user-id",required=True); bootstrap.add_argument("--name",required=True)
    bootstrap.add_argument("--slug",required=True); bootstrap.add_argument("--tenant-id")
    bootstrap.add_argument("--reason",required=True); bootstrap.add_argument("--dry-run",action="store_true")
    bootstrap.add_argument("--confirm",action="store_true")
    rbac=commands.add_parser("seed-rbac")
    rbac.add_argument("--tenant",required=True)
    rbac.add_argument("--reason",required=True); rbac.add_argument("--dry-run",action="store_true")
    rbac.add_argument("--confirm",action="store_true")
    access=commands.add_parser("bootstrap-access")
    access.add_argument("--provider",choices=["google","microsoft"],required=True)
    access.add_argument("--subject",required=True); access.add_argument("--tenant-id")
    access.add_argument("--tenant-name"); access.add_argument("--tenant-slug")
    access.add_argument("--reason",required=True); access.add_argument("--dry-run",action="store_true")
    access.add_argument("--confirm",action="store_true")
    platform=commands.add_parser("grant-platform-admin")
    platform.add_argument("--provider",choices=["google","microsoft"],required=True)
    platform.add_argument("--subject",required=True); platform.add_argument("--granted-by-user-id")
    platform.add_argument("--reason",required=True); platform.add_argument("--dry-run",action="store_true")
    platform.add_argument("--confirm",action="store_true")
    backfill=commands.add_parser("backfill-legacy-auth")
    backfill.add_argument("--page-size",type=int,default=100); backfill.add_argument("--after-id")
    backfill.add_argument("--max-pages",type=int); backfill.add_argument("--actor-user-id")
    backfill.add_argument("--reason",required=True); backfill.add_argument("--dry-run",action="store_true")
    backfill.add_argument("--confirm",action="store_true")
    args=parser.parse_args(argv)
    if args.command=="rotate-keys":
        result=rotate_keys(page_size=args.page_size,after_id=args.after_id,max_pages=args.max_pages,dry_run=args.dry_run)
    elif args.command=="cleanup-expired":
        result=cleanup_expired_auth()
    elif args.command=="bootstrap-tenant":
        result=bootstrap_tenant(user_id=args.user_id,name=args.name,slug=args.slug,tenant_id=args.tenant_id,reason=args.reason,dry_run=args.dry_run,confirmed=args.confirm)
    elif args.command=="seed-rbac":
        result=seed_rbac(tenant_id=args.tenant,reason=args.reason,dry_run=args.dry_run,confirmed=args.confirm)
    elif args.command=="bootstrap-access":
        result=bootstrap_access(
            provider=args.provider,subject=args.subject,tenant_id=args.tenant_id,
            tenant_name=args.tenant_name,tenant_slug=args.tenant_slug,reason=args.reason,
            dry_run=args.dry_run,confirmed=args.confirm,
        )
    elif args.command=="grant-platform-admin":
        result=grant_platform_admin(
            provider=args.provider,subject=args.subject,
            granted_by_user_id=args.granted_by_user_id,reason=args.reason,
            dry_run=args.dry_run,confirmed=args.confirm,
        )
    elif args.command=="backfill-legacy-auth":
        result=backfill_legacy_auth(
            page_size=args.page_size,after_id=args.after_id,max_pages=args.max_pages,
            actor_id=args.actor_user_id,reason=args.reason,
            dry_run=args.dry_run,confirmed=args.confirm,
        )
    else:
        with SessionLocal() as session:
            changed=AuthPersistenceRepository(session,cipher_from_settings()).revoke_connection(tenant_id=args.tenant,provider=args.provider,provider_account_id=args.account,actor_id="operator",reason=args.reason)
            session.commit(); result={"revoked":changed}
    print(json.dumps(result,sort_keys=True))
    return 0

if __name__=="__main__": raise SystemExit(main())
