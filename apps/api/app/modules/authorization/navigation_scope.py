"""Canonical tenant navbar grants. Explicit grants replace legacy role rights.

None = pre-existing role-based access (backward compatible); [] = no modules.
Platform/tenant administrators cannot be scoped by this mechanism.
"""
from __future__ import annotations

import re

from app.modules.inventory.permissions import INVENTORY_PERMISSION_DEFINITIONS

NAVIGATION_MODULES: dict[str, frozenset[str]] = {
    "assets": frozenset({
        "assets.read", "assets.upload", "assets.delete", "assets.manage",
        "assets.generate", "search.read", "search.rebuild", "search.index.activate",
    }),
    "inventory": frozenset(INVENTORY_PERMISSION_DEFINITIONS) | frozenset({"assets.read"}),
    "operations": frozenset({
        "ai_operations.read", "ai_analysis.run", "ai_analysis.force",
        "ai_provider.configure", "ai_jobs.retry", "ai_jobs.cancel",
        "ai_budget.read", "ai_budget.update", "ai_emergency_stop",
        "application_logs.manage",
    }),
    "branding-dna": frozenset({"ai_operations.read", "ai_provider.configure"}),
    "realistic-review-ugc": frozenset({
        "realistic_review_ugc.read", "realistic_review_ugc.run",
        "realistic_review_ugc.configure", "assets.read", "assets.generate",
    }),
    # TikTokShop and GatorHats use the same operation-level permission keys,
    # but a *different* explicit navigation scope and server-owned source root.
    "tiktokshop": frozenset({
        "realistic_review_ugc.read", "realistic_review_ugc.run",
        "realistic_review_ugc.configure", "assets.read", "assets.generate",
    }),
    "queue": frozenset({
        "ai_operations.read", "ai_jobs.retry", "ai_jobs.cancel",
    }),
    "generation": frozenset({"assets.read", "assets.generate"}),
    "review-board": frozenset({
        "public_review.read", "public_review.resolve", "public_review.manage",
    }),
    "access": frozenset({
        "tenant_members.read", "tenant_members.manage", "tenant_roles.manage",
        "audit.read",
    }),
}

NAVIGATION_LABELS = {
    "assets": "Asset Explorer",
    "inventory": "Inventory",
    "operations": "AI Operations",
    "branding-dna": "Branding DNA",
    "realistic-review-ugc": "Realistic Review UGC",
    "tiktokshop": "TikTokShop · Scout & UGC",
    "queue": "Job Queue",
    "generation": "Video Generation",
    "review-board": "Review Board",
    "access": "Access Management",
}


def permissions_for_modules(modules: set[str] | frozenset[str]) -> frozenset[str]:
    if not modules.issubset(NAVIGATION_MODULES):
        raise ValueError("Unknown navigation module")
    return frozenset(key for module in modules for key in NAVIGATION_MODULES[module])


# Only routes with a verified workspace-root filter can be dispatched under
# the separate TikTokShop grant. A query parameter alone cannot re-label an
# unrelated GatorHats endpoint as TikTokShop.
_TIKTOKSHOP_WORKSPACE_ROUTES = tuple(re.compile(expr) for expr in (
    r"/source-plans",
    r"/source-plans/folders",
    r"/source-plans/sync",
    r"/source-plans/[^/]+/image",
    r"/source-plans/[^/]+/stage2-jobs",
    r"/source-plans/[^/]+/stage2-jobs/cancel",
    r"/stage2-jobs",
    r"/stage2-jobs/[^/]+/(?:output|reference-image|source-image)",
    r"/stage3/review-groups",
    r"/stage3/review-groups/analyze",
    r"/stage3/reviews/export-loox",
    r"/stage3/reviews/[^/]+",
    r"/stage4/jobs/[^/]+/(?:retry|regenerate)",
    r"/stage4/(?:completions|active-jobs)",
    r"/scout-agents",
))


def navigation_for_api_path(path: str, workspace: str | None = None) -> str | None:
    """Distinguish shared permission keys by API domain, not client-side hiding.

    The check applies only to endpoints using the authenticated principal. Public
    shares, worker callbacks and OAuth routes are deliberately not captured.
    """
    if path == "/api/v1/admin/ai-operations/jobs" or path.startswith("/api/v1/admin/ai-operations/jobs/"):
        return "queue"
    rrugc_prefix = "/api/v1/realistic-review-ugc"
    if workspace == "tiktokshop" and path.startswith(rrugc_prefix + "/"):
        suffix = path[len(rrugc_prefix):]
        if any(pattern.fullmatch(suffix) for pattern in _TIKTOKSHOP_WORKSPACE_ROUTES):
            return "tiktokshop"
    prefixes = (
        ("/api/v1/branding-dna", "branding-dna"),
        ("/api/v1/admin/ai-operations", "operations"),
        ("/api/v1/ai-operations", "operations"),
        ("/api/v1/admin/ai-governance", "operations"),
        ("/api/v1/admin/processing-policies", "operations"),
        ("/api/v1/admin/asset-analyses", "operations"),
        ("/api/v1/admin/ai", "operations"),
        ("/api/v1/admin/visual-search", "operations"),
        ("/api/v1/creative-pipeline", "generation"),
        ("/api/v1/image-generations", "generation"),
        ("/api/inventory", "inventory"),
        ("/api/v1/inventory", "inventory"),
        ("/api/v1/realistic-review-ugc", "realistic-review-ugc"),
        ("/api/v1/public-review/board", "review-board"),
        ("/api/v1/public-review/shares", "review-board"),
        ("/api/v1/video-generations", "generation"),
        ("/api/explorer", "assets"),
        ("/explorer", "assets"),
        ("/api/sources", "assets"),
        ("/api/v1/asset-ingestions", "assets"),
        ("/metadata", "assets"),
        ("/api/v1/search", "assets"),
        ("/api/v1/admin/search", "assets"),
    )
    for prefix, module in prefixes:
        if path == prefix or path.startswith(prefix + "/"):
            return module
    if path.startswith("/api/v1/tenants/"):
        pieces = path.split("/")
        if len(pieces) >= 6 and pieces[5] in {"members", "roles", "permissions", "navigation-modules"}:
            return "access"
    return None
