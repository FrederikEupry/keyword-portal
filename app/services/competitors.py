"""Load competitor list from a Google Sheet.

Sheet structure assumed (confirm with marketing):
  Column A: Competitor name
  Column B: Primary domain (e.g. vaisala.com)

If GOOGLE_SERVICE_ACCOUNT_JSON or COMPETITORS_SHEET_ID is unset, returns an
empty list and the dossier sections that depend on competitors are skipped.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from app.config import get_settings

log = logging.getLogger(__name__)

_CACHE: dict = {"ts": 0.0, "data": []}
_TTL_SECONDS = 3600


async def get_competitors() -> list[dict]:
    """Return [{name, domain}]. Cached for 1 hour."""
    if time.time() - _CACHE["ts"] < _TTL_SECONDS and _CACHE["data"]:
        return _CACHE["data"]
    data = await asyncio.to_thread(_load_sync)
    _CACHE["ts"] = time.time()
    _CACHE["data"] = data
    return data


def _load_sync() -> list[dict]:
    s = get_settings()
    log.info(
        "Loading competitors. sheet_id_set=%s sa_json_set=%s sa_json_len=%d",
        bool(s.competitors_sheet_id),
        bool(s.google_service_account_json),
        len(s.google_service_account_json or ""),
    )
    if not s.competitors_sheet_id:
        log.warning("Competitors disabled: COMPETITORS_SHEET_ID is empty")
        return []
    if not s.google_service_account_json:
        log.warning("Competitors disabled: GOOGLE_SERVICE_ACCOUNT_JSON is empty")
        return []

    info = _service_account_info(s.google_service_account_json)
    if not info:
        log.error(
            "Competitors disabled: could not parse GOOGLE_SERVICE_ACCOUNT_JSON "
            "(first 80 chars: %r)",
            (s.google_service_account_json or "")[:80],
        )
        return []
    log.info("Service account parsed OK. client_email=%s", info.get("client_email"))

    try:
        creds = Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(s.competitors_sheet_id)
    except Exception as exc:
        log.exception("Failed to authorize / open sheet (%s): %s", s.competitors_sheet_id, exc)
        return []

    try:
        ws = sh.sheet1
        rows = ws.get_all_values()
    except Exception as exc:
        log.exception("Failed to read sheet1 values: %s", exc)
        return []

    log.info("Sheet opened. raw_rows=%d", len(rows))
    if rows:
        log.info("First row preview (header): %r", rows[0][:3])
        if len(rows) > 1:
            log.info("Second row preview: %r", rows[1][:3])

    out: list[dict] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        name, domain = row[0].strip(), row[1].strip().lower()
        if not name or not domain:
            continue
        domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
        out.append({"name": name, "domain": domain})

    log.info("Competitors loaded: %d", len(out))
    return out


def _service_account_info(value: str) -> dict | None:
    """Accept either a path to JSON or the JSON contents itself.

    The Path(value).is_file() check has to be guarded — when value is the
    full JSON blob (~2KB), Path treats it as a filename and stat() errors
    out with [Errno 36] File name too long.
    """
    if not value:
        return None

    # If it looks like JSON, parse it directly. Otherwise try as a file path.
    stripped = value.lstrip()
    if stripped.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    # Treat as file path. Guard against pathologically long strings.
    if len(value) < 4096:
        try:
            p = Path(value)
            if p.is_file():
                return json.loads(p.read_text())
        except (OSError, ValueError):
            pass
    return None
