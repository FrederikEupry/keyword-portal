"""Filter keyword expansions for industry relevance via OpenRouter.

DataForSEO's keyword_ideas is purely lexical — searching "temperature mapping"
returns "chicken temperature", "phoenix temperature", "definition of temperature",
etc. This module sends the universe to Claude with the topic + seeds as context
and drops keywords flagged as noise.

Runs in batches so prompt size stays reasonable. Best-effort: any failure
returns the input list unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging

from openai import AsyncOpenAI

from app.config import get_settings

log = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
BATCH_SIZE = 150
# Seeds are always kept regardless of model judgement.

FILTER_SYSTEM = """You are an SEO analyst grading keyword relevance for a B2B research dossier.
Output strict JSON only."""

FILTER_INSTRUCTION = """Given a topic, the seed keywords, and a list of expanded keyword candidates, label each candidate as:
- "relevant" — clearly about the same industry / use case as the seeds. Includes adjacent technical concepts (e.g. "iso 7 cleanroom" is relevant to "cdmo environmental monitoring").
- "borderline" — tangentially related; could be a longtail with some value but not the main intent. KEEP THESE.
- "noise" — unrelated to the industry. Common patterns: cooking ("chicken temperature"), weather ("phoenix temperature"), general definitions ("definition of temperature"), consumer goods, navigational searches for unrelated brands.

Output JSON:
{
  "verdicts": [
    {"k": "<keyword verbatim>", "v": "relevant" | "borderline" | "noise"}
  ]
}

Rules:
- Return one verdict per input keyword — same string, same order.
- When uncertain, prefer "borderline" over "noise". We'd rather keep weak signals than drop legitimate longtails.
- Generic terms that could apply across many industries (e.g. "monitoring system") are "borderline", not "noise", because they may capture our audience.
- Industry-specific noise (recipes, weather, navigational brand searches) is "noise"."""


async def filter_keywords(
    keywords: list[dict],
    topic: str,
    seeds: list[str],
    market: str,
) -> list[dict]:
    """Return only keywords flagged relevant or borderline.

    Seeds are always preserved (they're the user's input, by definition relevant).
    On any failure, returns the input list unchanged.
    """
    settings = get_settings()
    if not settings.openrouter_api_key or not keywords:
        return keywords

    seed_set = {s.lower() for s in seeds}
    # Only filter NON-seed keywords; seeds always stay.
    to_check = [kw for kw in keywords if kw["keyword"].lower() not in seed_set]
    seed_keywords = [kw for kw in keywords if kw["keyword"].lower() in seed_set]

    if not to_check:
        return keywords

    client = AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
    )

    # Batch to keep prompts manageable
    batches = [to_check[i:i + BATCH_SIZE] for i in range(0, len(to_check), BATCH_SIZE)]
    log.info(
        "Relevance filter: %d keywords (excluding %d seeds) in %d batch(es)",
        len(to_check), len(seed_keywords), len(batches),
    )

    try:
        verdict_lists = await asyncio.gather(*[
            _grade_batch(client, settings.openrouter_model, topic, seeds, market, batch)
            for batch in batches
        ])
    except Exception as exc:
        log.warning("Relevance filter failed; passing through all keywords: %s", exc)
        return keywords

    # Merge verdicts back. Keep relevant + borderline, drop noise.
    verdict_map: dict[str, str] = {}
    for verdict_list in verdict_lists:
        for v in verdict_list:
            verdict_map[v["k"].lower()] = v.get("v", "relevant")

    kept: list[dict] = list(seed_keywords)
    dropped = 0
    for kw in to_check:
        v = verdict_map.get(kw["keyword"].lower(), "relevant")
        if v == "noise":
            dropped += 1
            continue
        kept.append(kw)

    log.info(
        "Relevance filter: kept %d / dropped %d / total %d",
        len(kept), dropped, len(keywords),
    )
    return kept


async def _grade_batch(
    client: AsyncOpenAI,
    model: str,
    topic: str,
    seeds: list[str],
    market: str,
    batch: list[dict],
) -> list[dict]:
    payload = {
        "topic": topic,
        "market": market,
        "seeds": seeds,
        "candidates": [kw["keyword"] for kw in batch],
    }

    resp = await client.chat.completions.create(
        model=model,
        max_tokens=8000,
        temperature=0.1,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "relevance_verdicts",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["verdicts"],
                    "properties": {
                        "verdicts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["k", "v"],
                                "properties": {
                                    "k": {"type": "string"},
                                    "v": {"type": "string", "enum": ["relevant", "borderline", "noise"]},
                                },
                            },
                        }
                    },
                },
            },
        },
        messages=[
            {"role": "system", "content": FILTER_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": f"<context>\n{json.dumps(payload, ensure_ascii=False)}\n</context>"},
                {"type": "text", "text": FILTER_INSTRUCTION},
            ]},
        ],
    )
    raw = resp.choices[0].message.content or ""
    try:
        data = json.loads(raw)
        return data.get("verdicts", [])
    except json.JSONDecodeError:
        log.warning("Relevance filter returned invalid JSON; passing batch through")
        return [{"k": kw["keyword"], "v": "relevant"} for kw in batch]
