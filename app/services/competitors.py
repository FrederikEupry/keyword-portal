"""Load competitor list from a Google Sheet.

Columns are matched by header name (case-insensitive) so the sheet can have
extra columns or be reordered without breaking the loader.

Required headers:
  - "Companies" (or "Company" / "Name") — competitor name
  - "Domain" — primary domain, e.g. vaisala.com

Optional:
  - "Primary?" — if present, only rows where this column has a truthy
    value (x, X, yes, true, 1, ✓) are included. If the column doesn't
    exist, all rows with a name + domain are included.

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
    if not rows:
        log.warning("Sheet is empty")
        return []

    header_row = [c.strip().lower() for c in rows[0]]
    log.info("Header row normalized: %r", header_row)

    name_idx = _find_column(header_row, ["companies", "company", "name", "competitor"])
    domain_idx = _find_column(header_row, ["domain", "url", "website"])
    primary_idx = _find_column(header_row, ["primary?", "primary", "is primary", "core"])

    if name_idx is None or domain_idx is None:
        log.error(
            "Sheet missing required columns. Found headers=%r. "
            "Need at least 'Companies' and 'Domain'.",
            header_row,
        )
        return []

    log.info(
        "Columns mapped: name=col%d domain=col%d primary=%s",
        name_idx, domain_idx,
        f"col{primary_idx}" if primary_idx is not None else "none (loading all rows)",
    )

    truthy = {"x", "yes", "y", "true", "1", "✓", "primary"}
    out: list[dict] = []
    skipped_non_primary = 0
    for row in rows[1:]:
        if len(row) <= max(name_idx, domain_idx):
            continue
        name = row[name_idx].strip()
        domain = row[domain_idx].strip().lower()
        if not name or not domain:
            continue

        if primary_idx is not None and primary_idx < len(row):
            if row[primary_idx].strip().lower() not in truthy:
                skipped_non_primary += 1
                continue

        domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
        out.append({"name": name, "domain": domain})

    log.info(
        "Competitors loaded: %d (skipped %d non-primary)",
        len(out), skipped_non_primary,
    )
    return out


def _find_column(headers: list[str], candidates: list[str]) -> int | None:
    """Find the first header that matches any of the candidate names."""
    for cand in candidates:
        cand_lower = cand.lower()
        for idx, h in enumerate(headers):
            if h == cand_lower:
                return idx
    return None


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
