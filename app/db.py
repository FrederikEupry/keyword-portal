import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    topic TEXT NOT NULL,
    seeds_json TEXT NOT NULL,
    location TEXT NOT NULL,
    language TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    cost_usd REAL DEFAULT 0,
    dossier_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_email) REFERENCES users (email)
);

CREATE INDEX IF NOT EXISTS idx_runs_user ON runs (user_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status);
"""


async def init_db() -> None:
    settings = get_settings()
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.dossier_dir).mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def upsert_user(email: str, name: str) -> None:
    async with aiosqlite.connect(get_settings().db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (email, name, created_at) VALUES (?, ?, ?)",
            (email, name, _now()),
        )
        await db.commit()


async def create_run(
    run_id: str,
    user_email: str,
    topic: str,
    seeds: list[str],
    location: str,
    language: str,
) -> None:
    now = _now()
    async with aiosqlite.connect(get_settings().db_path) as db:
        await db.execute(
            """INSERT INTO runs
            (id, user_email, topic, seeds_json, location, language, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
            (run_id, user_email, topic, json.dumps(seeds), location, language, now, now),
        )
        await db.commit()


async def update_run_status(
    run_id: str,
    status: str,
    *,
    error: str | None = None,
    cost_usd: float | None = None,
    dossier_path: str | None = None,
) -> None:
    fields = ["status = ?", "updated_at = ?"]
    values: list = [status, _now()]
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    if cost_usd is not None:
        fields.append("cost_usd = ?")
        values.append(cost_usd)
    if dossier_path is not None:
        fields.append("dossier_path = ?")
        values.append(dossier_path)
    values.append(run_id)
    async with aiosqlite.connect(get_settings().db_path) as db:
        await db.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()


async def get_run(run_id: str) -> dict | None:
    async with aiosqlite.connect(get_settings().db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def list_runs(user_email: str, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(get_settings().db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM runs WHERE user_email = ? ORDER BY created_at DESC LIMIT ?",
            (user_email, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
