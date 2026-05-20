import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import init_db
from app.routes import auth, history, pages, research

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Eupry Keyword Portal", lifespan=lifespan)

    # Set the Secure cookie flag only when the app is served over HTTPS.
    # Browsers refuse to send Secure cookies over http://localhost, which breaks
    # the OAuth CSRF-state round-trip in local dev.
    is_https = settings.app_base_url.startswith("https://")

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="keyword_portal_session",
        same_site="lax",
        https_only=is_https,
        max_age=14 * 24 * 60 * 60,
    )

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(auth.router)
    app.include_router(research.router)
    app.include_router(history.router)
    app.include_router(pages.router)

    return app


app = create_app()
