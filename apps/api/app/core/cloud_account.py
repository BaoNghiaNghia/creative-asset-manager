from fastapi import Request

from app.modules.explorer.schema import Provider
from app.providers.google.auth import get_session as get_google_session
from app.providers.microsoft.auth import get_session as get_microsoft_session


def cloud_account_id(request: Request, provider: Provider) -> str:
    session = (
        get_microsoft_session(request)
        if provider == "sharepoint"
        else get_google_session(request)
    )
    if not session:
        return f"{provider}:developer"
    return str(
        session.user.get("id")
        or session.user.get("email")
        or f"{provider}-user"
    )


def cloud_tenant_id(request: Request, provider: Provider) -> str:
    """Use the authenticated workspace tenant for pipeline/status lookups."""
    session = (
        get_microsoft_session(request)
        if provider == "sharepoint"
        else get_google_session(request)
    )
    if session and session.active_tenant_id:
        return str(session.active_tenant_id)
    return cloud_account_id(request, provider)
