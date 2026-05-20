import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from app.db import create_run, get_run, update_run_status
from app.routes.auth import require_user
from app.services.markdown_gen import write_dossier
from app.services.research_runner import run_research

router = APIRouter()
log = logging.getLogger(__name__)

VALID_MARKETS = {"us", "de"}
MAX_SEEDS = 20


@router.post("/research")
async def submit(
    request: Request,
    topic: str = Form(...),
    seeds: str = Form(...),
    market: str = Form("us"),
    user: dict = Depends(require_user),
):
    if market not in VALID_MARKETS:
        raise HTTPException(400, f"Invalid market. Use one of: {sorted(VALID_MARKETS)}")
    seed_list = _parse_seeds(seeds)
    if not seed_list:
        raise HTTPException(400, "At least one seed keyword required")
    if len(seed_list) > MAX_SEEDS:
        raise HTTPException(400, f"Too many seeds (max {MAX_SEEDS}). You submitted {len(seed_list)}.")
    if not topic.strip():
        raise HTTPException(400, "Topic name required")

    run_id = uuid.uuid4().hex
    await create_run(
        run_id=run_id,
        user_email=user["email"],
        topic=topic.strip(),
        seeds=seed_list,
        location=market,
        language="en" if market == "us" else "de",
    )
    asyncio.create_task(_run_in_background(run_id, topic.strip(), seed_list, market))

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"run_id": run_id, "status": "queued"})
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@router.get("/research/{run_id}/status")
async def status(run_id: str, user: dict = Depends(require_user)):
    run = await get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["user_email"] != user["email"]:
        raise HTTPException(403, "Not your run")
    return {
        "id": run["id"],
        "status": run["status"],
        "error": run["error"],
        "cost_usd": run["cost_usd"],
        "dossier_ready": bool(run["dossier_path"]),
    }


@router.get("/research/{run_id}/download")
async def download(run_id: str, user: dict = Depends(require_user)):
    run = await get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["user_email"] != user["email"]:
        raise HTTPException(403, "Not your run")
    if not run["dossier_path"] or not Path(run["dossier_path"]).is_file():
        raise HTTPException(404, "Dossier not ready")
    return FileResponse(
        run["dossier_path"],
        media_type="text/markdown",
        filename=Path(run["dossier_path"]).name,
    )


def _parse_seeds(raw: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


async def _run_in_background(run_id: str, topic: str, seeds: list[str], market: str) -> None:
    try:
        await update_run_status(run_id, "running")
        result = await run_research(topic=topic, seeds=seeds, market=market)
        path = write_dossier(result, run_id)
        await update_run_status(
            run_id, "complete", cost_usd=result.cost_usd, dossier_path=path
        )
    except Exception as exc:
        log.exception("Run %s failed", run_id)
        await update_run_status(run_id, "failed", error=str(exc))
