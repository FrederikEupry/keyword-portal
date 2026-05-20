"""LLM analysis via OpenRouter (OpenAI-compatible API).

Two calls, run sequentially so the second one hits Anthropic's prompt cache:
  1. cluster_keywords — group the keyword universe into named clusters
  2. write_exec_summary — strategic narrative for the marketing lead

Both share the same large JSON payload. The first call writes it to cache
(1.25x cost). The second reads from cache (0.1x cost). Serializing the calls
trades ~5s of latency for ~23% cost savings.

Default model: anthropic/claude-sonnet-4.6 (confirmed slug with dots).
Override with OPENROUTER_MODEL — any chat model on OpenRouter works.

If OPENROUTER_API_KEY is unset, returns empty results and the dossier falls
back to the deterministic template.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import get_settings

log = logging.getLogger(__name__)

MAX_KEYWORDS_TO_LLM = 250
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass
class AIAnalysis:
    clusters: list[dict]
    exec_summary: str
    cost_usd: float = 0.0
    enabled: bool = True


async def analyze(
    topic: str,
    seeds: list[str],
    keywords: list[dict],
    eupry_ranked_set: set[str],
    competitor_rankings: dict[str, list[dict]],
    market: str,
) -> AIAnalysis:
    settings = get_settings()
    if not settings.openrouter_api_key:
        log.info("OPENROUTER_API_KEY not set — skipping AI analysis")
        return AIAnalysis(clusters=[], exec_summary="", enabled=False)

    headers: dict[str, str] = {}
    if settings.openrouter_app_url:
        headers["HTTP-Referer"] = settings.openrouter_app_url
    if settings.openrouter_app_title:
        headers["X-Title"] = settings.openrouter_app_title

    client = AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers=headers or None,
    )

    payload = _build_shared_payload(topic, seeds, keywords, eupry_ranked_set, competitor_rankings, market)

    try:
        # Serialized: first call writes the cache, second call reads it.
        # Parallel calls would produce two cache writes — ~23% more expensive.
        clusters = await _cluster_keywords(client, settings.openrouter_model, payload)
        summary = await _write_exec_summary(client, settings.openrouter_model, payload)
    except Exception as exc:
        log.exception("AI analysis failed; continuing without it")
        return AIAnalysis(clusters=[], exec_summary=f"_AI analysis unavailable: {exc}_", enabled=False)

    return AIAnalysis(clusters=clusters, exec_summary=summary, enabled=True)


# ----------------------------------------------------------------- payload
def _build_shared_payload(
    topic: str,
    seeds: list[str],
    keywords: list[dict],
    eupry_ranked_set: set[str],
    competitor_rankings: dict[str, list[dict]],
    market: str,
) -> str:
    sorted_kws = sorted(
        keywords,
        key=lambda k: (k.get("volume") or 0),
        reverse=True,
    )[:MAX_KEYWORDS_TO_LLM]

    kw_rows = [
        {
            "k": kw["keyword"],
            "v": kw.get("volume"),
            "kd": kw.get("kd"),
            "intent": kw.get("intent"),
            "eupry": kw["keyword"].lower() in eupry_ranked_set,
        }
        for kw in sorted_kws
    ]

    competitor_top10: dict[str, list[str]] = {}
    for name, rankings in competitor_rankings.items():
        winners = [r["keyword"] for r in rankings if (r.get("position") or 999) <= 10]
        if winners:
            competitor_top10[name] = winners[:50]

    return json.dumps({
        "topic": topic,
        "market": market,
        "seeds": seeds,
        "keywords": kw_rows,
        "competitor_top10_overlap": competitor_top10,
    }, ensure_ascii=False)


def _payload_user_message(payload: str, instruction: str) -> list[dict]:
    """Build a user message with the cacheable payload block first, then the instruction.

    Anthropic prompt caching (via OpenRouter) requires `cache_control` on a content
    block. We mark the large JSON payload as ephemeral so the second call reads from
    cache at 0.1x cost.
    """
    return [
        {
            "type": "text",
            "text": f"<keyword_research_payload>\n{payload}\n</keyword_research_payload>",
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": instruction},
    ]


# ---------------------------------------------------------------- clustering
CLUSTER_SYSTEM = """You are an SEO content strategist analyzing keyword research for an editorial team. \
Output strict JSON only — no prose outside the JSON."""

CLUSTER_INSTRUCTION = """Cluster the keywords in the payload above into 4-8 semantic content clusters.

Each cluster represents a distinct article (or small group of closely related articles).
Group by user intent and topical proximity — keywords that would naturally be answered by the same piece of content belong together.

Rules:
- Cover EVERY keyword from the payload. Don't drop any.
- 4-8 clusters total. Don't over-fragment.
- Cluster names should be evergreen (suitable as content hub names), not keyword-stuffed.
- If competitor_top10_overlap shows competitors winning a cluster, briefly mention it in the rationale."""


# Schema kept minimal: minItems/maxItems on arrays aren't supported by all
# providers (e.g. Bedrock rejects values other than 0/1). The 4-8 cluster count
# is enforced via the prompt instead.
CLUSTER_JSON_SCHEMA = {
    "name": "keyword_clusters",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["clusters"],
        "properties": {
            "clusters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "theme", "keywords", "rationale"],
                    "properties": {
                        "name": {"type": "string"},
                        "theme": {"type": "string"},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": "string"},
                    },
                },
            }
        },
    },
}


async def _cluster_keywords(client: AsyncOpenAI, model: str, payload: str) -> list[dict]:
    resp = await client.chat.completions.create(
        model=model,
        max_tokens=4000,
        temperature=0.3,
        response_format={"type": "json_schema", "json_schema": CLUSTER_JSON_SCHEMA},
        messages=[
            {"role": "system", "content": CLUSTER_SYSTEM},
            {"role": "user", "content": _payload_user_message(payload, CLUSTER_INSTRUCTION)},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    data = _safe_json_parse(text)
    return data.get("clusters", []) if data else []


# -------------------------------------------------------------- exec summary
SUMMARY_SYSTEM = """You are an SEO content strategist briefing a marketing team. \
Write in clear, direct prose. No hype, no marketing fluff. Use specific numbers from the data."""

SUMMARY_INSTRUCTION = """Write a strategic executive summary of the keyword research above, in markdown.

Structure (use these headings):

### Where the volume is
2-3 sentences identifying the highest-volume themes and where genuine demand exists.

### What Eupry already covers
2-3 sentences. Cite specific keywords from the data. Be honest if coverage is weak or strong.

### Where competitors are winning
2-3 sentences. Name specific competitors and the keywords/clusters they dominate.

### What to prioritize
3-5 bullet points, ordered. Each is a concrete next action ("Write a guide on X targeting keyword Y, volume Z").

Rules:
- Cite real numbers from the payload (volumes, KD scores, positions).
- Don't invent keywords or competitors not in the payload.
- Total length: 250-400 words.
- No emojis. No "Here's a summary" preamble. Start with the first heading."""


async def _write_exec_summary(client: AsyncOpenAI, model: str, payload: str) -> str:
    resp = await client.chat.completions.create(
        model=model,
        max_tokens=2000,
        temperature=0.4,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": _payload_user_message(payload, SUMMARY_INSTRUCTION)},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


# ----------------------------------------------------------------- helpers
def _safe_json_parse(text: str) -> dict | None:
    """Belt-and-braces JSON parsing. With json_schema response_format, the model
    should always emit valid JSON, but this handles any edge case (e.g. provider
    fallback that doesn't honor json_schema)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None
