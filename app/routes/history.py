import json

from fastapi import APIRouter, Depends

from app.db import list_runs
from app.routes.auth import require_user

router = APIRouter()


@router.get("/api/history")
async def history(user: dict = Depends(require_user)):
    runs = await list_runs(user["email"])
    return [
        {
            "id": r["id"],
            "topic": r["topic"],
            "seeds": json.loads(r["seeds_json"]),
            "market": r["location"],
            "status": r["status"],
            "cost_usd": r["cost_usd"],
            "created_at": r["created_at"],
            "dossier_ready": bool(r["dossier_path"]),
        }
        for r in runs
    ]
