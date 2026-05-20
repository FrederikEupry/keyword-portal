import logging

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.db import upsert_user

log = logging.getLogger(__name__)

router = APIRouter()
_oauth: OAuth | None = None


def _get_oauth() -> OAuth:
    """Lazy-register the Google OAuth client with credentials from settings.

    Authlib needs client_id + client_secret at register() time to fetch the JWKs
    for ID token verification. Registering once (vs. setting attributes per
    request) avoids race conditions and gives proper id_token claim parsing.
    """
    global _oauth
    if _oauth is not None:
        return _oauth
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(500, "GOOGLE_CLIENT_ID not configured")
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    _oauth = oauth
    return oauth


@router.get("/auth/login")
async def login(request: Request):
    settings = get_settings()
    oauth = _get_oauth()
    redirect_uri = f"{settings.app_base_url}/auth/callback"
    log.info(
        "OAuth login starting. session_keys_before=%s redirect_uri=%s",
        list(request.session.keys()),
        redirect_uri,
    )
    response = await oauth.google.authorize_redirect(
        request,
        redirect_uri,
        hd=settings.allowed_email_domain,
    )
    log.info(
        "OAuth login redirect built. session_keys_after=%s",
        list(request.session.keys()),
    )
    return response


@router.get("/auth/callback")
async def callback(request: Request):
    settings = get_settings()
    oauth = _get_oauth()
    log.info(
        "OAuth callback received. query_state=%s session_keys=%s",
        request.query_params.get("state"),
        list(request.session.keys()),
    )

    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        log.exception("OAuth token exchange failed")
        raise HTTPException(401, f"OAuth failed: {exc.error} — {exc.description}") from exc
    except Exception as exc:
        log.exception("Unexpected error during OAuth callback")
        raise HTTPException(401, f"OAuth callback error: {type(exc).__name__}: {exc}") from exc

    # token['userinfo'] is the parsed ID token claims. If Authlib couldn't parse
    # the ID token (wrong issuer/audience/nonce), fall back to the /userinfo endpoint.
    user_info = token.get("userinfo")
    if not user_info:
        try:
            resp = await oauth.google.userinfo(token=token)
            user_info = dict(resp) if resp else {}
        except Exception as exc:
            log.exception("userinfo endpoint failed")
            raise HTTPException(401, f"Could not fetch user info: {exc}") from exc

    email = (user_info.get("email") or "").lower()
    name = user_info.get("name", "")
    email_verified = user_info.get("email_verified", False)
    hd = user_info.get("hd", "")

    log.info("OAuth callback: email=%s verified=%s hd=%s", email, email_verified, hd)

    if not email:
        raise HTTPException(403, "Google did not return an email address")
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
