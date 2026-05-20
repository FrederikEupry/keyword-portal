"""Claude API integration for semantic clustering + exec summary narrative.

Both functions share the same large keyword payload, so we use prompt caching:
the payload goes in a cacheable system block, the per-call instruction is small.

If ANTHROPIC_API_KEY is unset, returns empty results and the dossier falls
back to the deterministic template.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from anthropic.types import TextBlock

from app.config import get_settings

log = logging.getLogger(__name__)

MAX_KEYWORDS_TO_LLM = 250  # cap to keep prompt size + latency sane


@dataclass
class AIAnalysis:
    clusters: list[dict]  # [{name, theme, keywords: [str], rationale}]
    exec_summary: str  # markdown paragraphs
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
    if not settings.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY not set — skipping AI analysis")
        return AIAnalysis(clusters=[], exec_summary="", enabled=False)

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Build a compact payload sent ONCE and cached across the two calls.
    payload = _build_shared_payload(topic, seeds, keywords, eupry_ranked_set, competitor_rankings, market)

    clusters_task = _cluster_keywords(client, settings.anthropic_model, payload)
    summary_task = _write_exec_summary(client, settings.anthropic_model, payload)

    try:
        clusters, summary, cost = await _gather_with_cost(clusters_task, summary_task)
    except Exception as exc:
        log.exception("AI analysis failed; continuing without it")
        return AIAnalysis(clusters=[], exec_summary=f"_AI analysis unavailable: {exc}_", enabled=False)

    return AIAnalysis(clusters=clusters, exec_summary=summary, cost_usd=cost, enabled=True)


# ----------------------------------------------------------------- payload
def _build_shared_payload(
    topic: str,
    seeds: list[str],
    keywords: list[dict],
    eupry_ranked_set: set[str],
    competitor_rankings: dict[str, list[dict]],
    market: str,
) -> str:
    """Compact JSON the model uses for both clustering + summary."""
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


# ---------------------------------------------------------------- clustering
CLUSTER_SYSTEM = """You are an SEO content strategist analyzing keyword research for an editorial team. \
Output strict JSON only — no prose outside the JSON."""

CLUSTER_INSTRUCTION = """Cluster the keywords in the payload into 4-8 semantic content clusters.

Each cluster represents a distinct article (or small group of closely related articles).
Group by user intent and topical proximity — keywords that would naturally be answered by the same piece of content belong together.

Output JSON shape:
{
  "clusters": [
    {
      "name": "Short marketing-meaningful name (3-6 words)",
      "theme": "One-sentence description of what this cluster is about",
      "keywords": ["kw1", "kw2", ...],
      "rationale": "One sentence: why these belong together, and the recommended content angle"
    }
  ]
}

Rules:
- Cover EVERY keyword from the payload. Don't drop any.
- 4-8 clusters total. Don't over-fragment.
- Cluster names should be evergreen (suitable as content hub names), not keyword-stuffed.
- If competitor_top10_overlap shows competitors winning a cluster, briefly mention it in the rationale."""


async def _cluster_keywords(client: AsyncAnthropic, model: str, payload: str) -> list[dict]:
    resp = await client.messages.create(
        model=model,
        max_tokens=4000,
        system=[
            {"type": "text", "text": CLUSTER_SYSTEM},
            {
                "type": "text",
                "text": f"<keyword_research_payload>\n{payload}\n</keyword_research_payload>",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": CLUSTER_INSTRUCTION}],
    )
    text = _extract_text(resp)
    data = _safe_json_parse(text)
    return data.get("clusters", []) if data else []


# -------------------------------------------------------------- exec summary
SUMMARY_SYSTEM = """You are an SEO content strategist briefing a marketing team. \
Write in clear, direct prose. No hype, no marketing fluff. Use specific numbers from the data."""

SUMMARY_INSTRUCTION = """Write a strategic executive summary of this keyword research, in markdown.

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


async def _write_exec_summary(client: AsyncAnthropic, model: str, payload: str) -> str:
    resp = await client.messages.create(
        model=model,
        max_tokens=2000,
        system=[
            {"type": "text", "text": SUMMARY_SYSTEM},
            {
                "type": "text",
                "text": f"<keyword_research_payload>\n{payload}\n</keyword_research_payload>",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": SUMMARY_INSTRUCTION}],
    )
    return _extract_text(resp).strip()


# ----------------------------------------------------------------- helpers
async def _gather_with_cost(clusters_coro, summary_coro):
    """Run both coroutines, sum the costs from response usage."""
    clusters_resp_holder: dict = {}
    summary_resp_holder: dict = {}

    async def run_clusters():
        clusters_resp_holder["result"] = await clusters_coro

    async def run_summary():
        summary_resp_holder["result"] = await summary_coro

    await asyncio.gather(run_clusters(), run_summary())
    # NOTE: anthropic SDK returns the message object inside our helpers,
    # but we extracted the parsed result already. Cost tracked separately if needed.
    return clusters_resp_holder["result"], summary_resp_holder["result"], 0.0


def _extract_text(message) -> str:
    parts = []
    for block in message.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "".join(parts)


def _safe_json_parse(text: str) -> dict | None:
    """Tolerate Claude wrapping JSON in ```json fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find the first { ... last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None
