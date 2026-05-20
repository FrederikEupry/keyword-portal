from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.db import upsert_user

router = APIRouter()
oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/auth/login")
async def login(request: Request):
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(500, "GOOGLE_CLIENT_ID not configured")

    oauth.google.client_id = settings.google_client_id
    oauth.google.client_secret = settings.google_client_secret

    redirect_uri = f"{settings.app_base_url}/auth/callback"
    return await oauth.google.authorize_redirect(
        request,
        redirect_uri,
        hd=settings.allowed_email_domain,
    )


@router.get("/auth/callback")
async def callback(request: Request):
    settings = get_settings()
    oauth.google.client_id = settings.google_client_id
    oauth.google.client_secret = settings.google_client_secret

    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(401, f"OAuth failed: {exc.error}") from exc

    user_info = token.get("userinfo") or {}
    email = (user_info.get("email") or "").lower()
    name = user_info.get("name", "")
    email_verified = user_info.get("email_verified", False)
    hd = user_info.get("hd", "")

    if not email_verified:
        raise HTTPException(403, "Email not verified by Google")

    allowed = settings.allowed_email_domain.lower()
    if hd.lower() != allowed and not email.endswith(f"@{allowed}"):
        raise HTTPException(
            403,
            f"Access restricted to @{allowed} accounts. You signed in as {email}.",
        )

    await upsert_user(email, name)
    request.session["user"] = {"email": email, "name": name}
    return RedirectResponse(url="/", status_code=303)


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


def current_user(request: Request) -> dict | None:
    return request.session.get("user")


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user
