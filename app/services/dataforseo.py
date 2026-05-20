"""Async DataForSEO client wrapping the endpoints used by the portal.

All methods return normalized Python dicts/lists, not raw DataForSEO envelopes.
Costs are tracked per-call so the runner can enforce a budget.
"""
from __future__ import annotations

import base64
from typing import Any

import httpx

from app.config import get_settings

BASE_URL = "https://api.dataforseo.com"

LOCATION_CODES = {
    "us": 2840,
    "de": 2276,
}
LANGUAGE_CODES = {
    "us": "en",
    "de": "de",
}


class DataForSEOError(RuntimeError):
    pass


class DataForSEOClient:
    def __init__(self) -> None:
        s = get_settings()
        if not s.dataforseo_login or not s.dataforseo_password:
            raise DataForSEOError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not configured")
        creds = f"{s.dataforseo_login}:{s.dataforseo_password}".encode()
        self._auth = "Basic " + base64.b64encode(creds).decode()
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": self._auth, "Content-Type": "application/json"},
            timeout=httpx.Timeout(60.0, connect=15.0),
        )
        self.total_cost_usd = 0.0

    async def __aenter__(self) -> "DataForSEOClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: list[dict]) -> dict:
        resp = await self._client.post(path, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status_code") not in (20000, 20100):
            raise DataForSEOError(f"{path}: {body.get('status_message')}")
        self.total_cost_usd += float(body.get("cost", 0))
        return body

    # ------------------------------------------------------------------ ideas
    async def keyword_ideas(
        self,
        seed: str,
        location_code: int,
        language_code: str,
        limit: int = 50,
    ) -> list[dict]:
        body = await self._post(
            "/v3/dataforseo_labs/google/keyword_ideas/live",
            [{
                "keywords": [seed],
                "location_code": location_code,
                "language_code": language_code,
                "limit": limit,
                "order_by": ["keyword_info.search_volume,desc"],
            }],
        )
        items = _first_result(body).get("items", []) or []
        return [
            {
                "keyword": it.get("keyword"),
                "volume": _ki(it, "search_volume"),
                "cpc": _ki(it, "cpc"),
                "competition": _ki(it, "competition_level"),
            }
            for it in items
        ]

    # ----------------------------------------------------------------- volume
    async def search_volume(
        self,
        keywords: list[str],
        location_code: int,
        language_code: str,
    ) -> dict[str, dict]:
        body = await self._post(
            "/v3/keywords_data/google_ads/search_volume/live",
            [{
                "keywords": keywords[:1000],
                "location_code": location_code,
                "language_code": language_code,
            }],
        )
        items = _first_result(body).get("items") or _first_result(body).get("result", [])
        return {
            it["keyword"]: {
                "volume": it.get("search_volume"),
                "cpc": it.get("cpc"),
                "competition": it.get("competition"),
            }
            for it in items
            if it.get("keyword")
        }

    # ------------------------------------------------------------- difficulty
    async def keyword_difficulty(
        self,
        keywords: list[str],
        location_code: int,
        language_code: str,
    ) -> dict[str, int | None]:
        body = await self._post(
            "/v3/dataforseo_labs/google/bulk_keyword_difficulty/live",
            [{
                "keywords": keywords[:1000],
                "location_code": location_code,
                "language_code": language_code,
            }],
        )
        items = _first_result(body).get("items", []) or []
        return {it["keyword"]: it.get("keyword_difficulty") for it in items}

    # ----------------------------------------------------------------- intent
    async def search_intent(
        self,
        keywords: list[str],
        language_code: str,
    ) -> dict[str, str]:
        body = await self._post(
            "/v3/dataforseo_labs/google/search_intent/live",
            [{
                "keywords": keywords[:1000],
                "language_code": language_code,
            }],
        )
        items = _first_result(body).get("items", []) or []
        out: dict[str, str] = {}
        for it in items:
            intent = (it.get("keyword_intent") or {}).get("label")
            out[it["keyword"]] = intent or "unknown"
        return out

    # ------------------------------------------------------------------- SERP
    async def serp_top10(
        self,
        keyword: str,
        location_code: int,
        language_code: str,
    ) -> list[dict]:
        body = await self._post(
            "/v3/serp/google/organic/live/advanced",
            [{
                "keyword": keyword,
                "location_code": location_code,
                "language_code": language_code,
                "depth": 10,
            }],
        )
        items = _first_result(body).get("items", []) or []
        out: list[dict] = []
        for it in items:
            if it.get("type") != "organic":
                continue
            out.append({
                "rank": it.get("rank_absolute"),
                "domain": it.get("domain"),
                "url": it.get("url"),
                "title": it.get("title"),
            })
            if len(out) >= 10:
                break
        return out

    # -------------------------------------------------- domain ranked_keywords
    async def domain_ranked_keywords(
        self,
        domain: str,
        location_code: int,
        language_code: str,
        limit: int = 1000,
    ) -> list[dict]:
        body = await self._post(
            "/v3/dataforseo_labs/google/ranked_keywords/live",
            [{
                "target": domain,
                "location_code": location_code,
                "language_code": language_code,
                "limit": limit,
                "filters": [["ranked_serp_element.serp_item.rank_absolute", "<=", 100]],
                "order_by": ["ranked_serp_element.serp_item.rank_absolute,asc"],
            }],
        )
        items = _first_result(body).get("items", []) or []
        out: list[dict] = []
        for it in items:
            serp = (it.get("ranked_serp_element") or {}).get("serp_item") or {}
            kw_info = (it.get("keyword_data") or {}).get("keyword_info") or {}
            out.append({
                "keyword": (it.get("keyword_data") or {}).get("keyword"),
                "position": serp.get("rank_absolute"),
                "url": serp.get("url"),
                "volume": kw_info.get("search_volume"),
            })
        return out


def _first_result(body: dict) -> dict:
    tasks = body.get("tasks") or []
    if not tasks:
        return {}
    results = tasks[0].get("result") or []
    return results[0] if results else {}


def _ki(item: dict, field: str):
    return (item.get("keyword_info") or {}).get(field)
