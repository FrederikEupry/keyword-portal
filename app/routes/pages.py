import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db import get_run, list_runs
from app.routes.auth import current_user, require_user

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = current_user(request)
    if not user:
        return templates.TemplateResponse(request, "login.html")
    runs = await list_runs(user["email"], limit=10)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "runs": runs, "parse_seeds": json.loads},
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(
    run_id: str,
    request: Request,
    user: dict = Depends(require_user),
):
    run = await get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["user_email"] != user["email"]:
        raise HTTPException(403, "Not your run")
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {"user": user, "run": run, "seeds": json.loads(run["seeds_json"])},
    )
