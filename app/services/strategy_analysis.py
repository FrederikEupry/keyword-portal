"""Three post-clustering AI analyses that run in parallel against OpenRouter:

  1. Page-type consensus (#3) — for each cluster, looks at the SERP top-10 we
     already fetched and identifies which page format Google rewards.

  2. Persona scoring (#4) — derives 3-5 personas from SERP signals and the
     keyword mix, scores each cluster's fit per persona on 4 dimensions.

  3. Competitor citation-readiness grading (#1) — for each cluster picks the
     top-3 competitor URLs, fetches the page HTML, strips to readable text,
     and grades AI-citability on a 0-100 scale with breakdown.

All three are best-effort: if any fails, the dossier still renders (the
section just shows a soft "_analysis unavailable_" line).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

import httpx
from openai import AsyncOpenAI

from app.config import get_settings

log = logging.getLogger(__name__)

MAX_COMPETITOR_PAGES_PER_CLUSTER = 3
MAX_PAGE_BYTES = 600_000  # ~150KB of text after strip; ignore mega-pages
PAGE_FETCH_TIMEOUT = 15.0
PAGE_TEXT_CAP_CHARS = 8000  # what we send to the LLM per page
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass
class StrategyAnalysis:
    page_types: dict[str, dict] = field(default_factory=dict)        # cluster_name -> {dominant, consensus_pct, your_format_match, rationale}
    personas: list[dict] = field(default_factory=list)                # [{name, role, motivation, clusters_relevant: [..]}, ...]
    cluster_persona_fit: dict[str, list[dict]] = field(default_factory=dict)  # cluster -> [{persona, score, gap_note}]
    citation_grades: dict[str, list[dict]] = field(default_factory=dict)      # cluster -> [{url, domain, score, breakdown, top_gap}]
    enabled: bool = True


async def analyze_strategy(
    clusters: list[dict],
    serps: dict[str, list[dict]],
    seeds: list[str],
    market: str,
) -> StrategyAnalysis:
    """Run the three post-cluster analyses. Each is best-effort."""
    settings = get_settings()
    if not settings.openrouter_api_key or not clusters:
        return StrategyAnalysis(enabled=False)

    client = AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
    )

    page_type_task = _analyze_page_types(client, settings.openrouter_model, clusters, serps)
    personas_task = _derive_personas(client, settings.openrouter_model, clusters, serps, seeds, market)
    citation_task = _grade_competitor_citations(client, settings.openrouter_model, clusters, serps)

    page_types, personas_out, citation_grades = await asyncio.gather(
        page_type_task, personas_task, citation_task, return_exceptions=True
    )

    result = StrategyAnalysis(enabled=True)
    if isinstance(page_types, Exception):
        log.warning("Page-type analysis failed: %s", page_types)
    else:
        result.page_types = page_types

    if isinstance(personas_out, Exception):
        log.warning("Persona analysis failed: %s", personas_out)
    else:
        result.personas = personas_out.get("personas", [])
        result.cluster_persona_fit = personas_out.get("cluster_fit", {})

    if isinstance(citation_grades, Exception):
        log.warning("Citation grading failed: %s", citation_grades)
    else:
        result.citation_grades = citation_grades

    return result


# =============================================================== #3 page-type
PAGE_TYPE_SYSTEM = """You are an SEO analyst inferring the dominant page format that Google rewards. \
Output strict JSON only."""

PAGE_TYPE_INSTRUCTION = """For each cluster, look at the SERP top-10 results provided and classify which page TYPE Google is rewarding.

Page types to choose from:
- "ultimate-guide" (long-form educational, "everything you need to know")
- "listicle" ("top N best X", "10 ways to Y")
- "comparison" ("X vs Y", "alternatives to X")
- "how-to" (step-by-step tutorial)
- "product-page" (commercial landing page)
- "definition" (short "what is X" answer page)
- "case-study" (specific real-world example)
- "tool" (calculator, interactive widget)
- "video" (YouTube/Vimeo dominates)
- "forum" (Reddit/Quora/community)
- "news" (recent article with date)
- "mixed" (no clear consensus)

Output JSON:
{
  "clusters": [
    {
      "name": "<cluster name verbatim>",
      "dominant_type": "<one of the types above>",
      "consensus_pct": 70,  // % of top 10 that match dominant_type
      "rationale": "One sentence: what signals you used to decide (titles, URL patterns, domains)"
    }
  ]
}"""


async def _analyze_page_types(
    client: AsyncOpenAI,
    model: str,
    clusters: list[dict],
    serps: dict[str, list[dict]],
) -> dict[str, dict]:
    """One LLM call processes all clusters at once."""
    payload = []
    for c in clusters:
        # Pick the highest-volume seed keyword that's actually in this cluster's keyword list,
        # then use the SERP for that seed as the representative SERP.
        rep_serp = _representative_serp(c, serps)
        payload.append({
            "name": c.get("name"),
            "theme": c.get("theme"),
            "serp_top10": rep_serp,
        })

    resp = await client.chat.completions.create(
        model=model,
        max_tokens=3000,
        temperature=0.2,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "page_type_analysis",
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
                                "required": ["name", "dominant_type", "consensus_pct", "rationale"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "dominant_type": {"type": "string"},
                                    "consensus_pct": {"type": "integer"},
                                    "rationale": {"type": "string"},
                                },
                            },
                        }
                    },
                },
            },
        },
        messages=[
            {"role": "system", "content": PAGE_TYPE_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": f"<clusters_and_serps>\n{json.dumps(payload, ensure_ascii=False)}\n</clusters_and_serps>"},
                {"type": "text", "text": PAGE_TYPE_INSTRUCTION},
            ]},
        ],
    )
    data = _safe_json_parse(resp.choices[0].message.content or "")
    out: dict[str, dict] = {}
    for item in (data or {}).get("clusters", []):
        out[item["name"]] = {
            "dominant": item.get("dominant_type"),
            "consensus_pct": item.get("consensus_pct"),
            "rationale": item.get("rationale"),
        }
    return out


# =============================================================== #4 personas
PERSONAS_SYSTEM = """You are a content strategist deriving user personas from search intent signals. \
Output strict JSON only."""

PERSONAS_INSTRUCTION = """Derive 3-5 personas for this keyword research, then score each cluster's fit per persona.

A persona = a specific role + situation (e.g. "Regulatory affairs lead at a CDMO preparing for an FDA inspection").
Each persona should have a clear motivation that's visible in the keyword mix.

For each cluster x persona combination, score on 4 dimensions (each 0-25):
- relevance: how directly the cluster speaks to this persona's job
- clarity: how unambiguous the intent is (vs informational fluff)
- trust: do the SERP winners suggest the audience values authority signals (regulatory, peer-reviewed)?
- action: how close is this cluster to a buying / decision moment?

Output JSON:
{
  "personas": [
    {
      "name": "<short label, e.g. 'GMP Compliance Lead'>",
      "role": "<role + company-type>",
      "motivation": "<what they're trying to accomplish>"
    }
  ],
  "cluster_fit": {
    "<cluster name verbatim>": [
      {"persona": "<persona name>", "score": 78, "gap_note": "<one sentence: what's missing for this persona, or why this cluster fits well>"}
    ]
  }
}

Score = sum of 4 dimensions, 0-100. Show only personas with score >= 40 per cluster. If a cluster has zero personas >= 40, return an empty list for that cluster."""


async def _derive_personas(
    client: AsyncOpenAI,
    model: str,
    clusters: list[dict],
    serps: dict[str, list[dict]],
    seeds: list[str],
    market: str,
) -> dict:
    payload = {
        "market": market,
        "seeds": seeds,
        "clusters": [
            {
                "name": c.get("name"),
                "theme": c.get("theme"),
                "keywords_sample": (c.get("keywords") or [])[:15],
                "rationale": c.get("rationale"),
                "serp_titles_sample": [s.get("title") for s in _representative_serp(c, serps)][:5],
            }
            for c in clusters
        ],
    }

    resp = await client.chat.completions.create(
        model=model,
        max_tokens=4000,
        temperature=0.4,
        messages=[
            {"role": "system", "content": PERSONAS_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": f"<payload>\n{json.dumps(payload, ensure_ascii=False)}\n</payload>"},
                {"type": "text", "text": PERSONAS_INSTRUCTION},
            ]},
        ],
    )
    return _safe_json_parse(resp.choices[0].message.content or "") or {"personas": [], "cluster_fit": {}}


# ================================================== #1 competitor citation grading
async def _grade_competitor_citations(
    client: AsyncOpenAI,
    model: str,
    clusters: list[dict],
    serps: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """Per cluster: fetch top 3 competitor URLs and grade citation-readiness."""
    out: dict[str, list[dict]] = {}

    async with httpx.AsyncClient(
        timeout=PAGE_FETCH_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; KeywordPortalBot/0.3)"},
    ) as http_client:
        for cluster in clusters:
            urls = _pick_competitor_urls(cluster, serps, MAX_COMPETITOR_PAGES_PER_CLUSTER)
            if not urls:
                continue

            fetch_results = await asyncio.gather(
                *[_fetch_and_extract(http_client, url) for url in urls],
                return_exceptions=True,
            )

            grades: list[dict] = []
            grade_tasks = []
            paired_urls: list[tuple[str, str]] = []
            for url, fetch_result in zip(urls, fetch_results, strict=True):
                if isinstance(fetch_result, Exception) or not fetch_result:
                    continue
                domain, text = fetch_result
                paired_urls.append((url, domain))
                grade_tasks.append(_grade_one_page(client, model, cluster.get("name"), url, text))

            if not grade_tasks:
                continue

            grade_results = await asyncio.gather(*grade_tasks, return_exceptions=True)
            for (url, domain), grade in zip(paired_urls, grade_results, strict=True):
                if isinstance(grade, Exception) or not grade:
                    continue
                grades.append({
                    "url": url,
                    "domain": domain,
                    "score": grade.get("score"),
                    "breakdown": grade.get("breakdown", {}),
                    "top_gap": grade.get("top_gap"),
                    "strongest": grade.get("strongest"),
                })

            if grades:
                out[cluster.get("name")] = sorted(grades, key=lambda g: g.get("score") or 0, reverse=True)

    return out


GRADE_SYSTEM = """You are an AI-search optimization analyst grading how citable a webpage is for ChatGPT, \
Perplexity, and Google AI Overviews. Output strict JSON only."""

GRADE_INSTRUCTION = """Grade this competitor page on AI citation readiness, 0-100.

Scoring rubric (each 0-25):
- passage_length: does the page contain self-contained passages of ~134-167 words that answer a clear question?
- definitions: does it open key sections with "X is..." clear definitions in the first 60 words?
- specificity: does it use specific numbers, names, dates, quotes vs vague generalities?
- structure: heading hierarchy, FAQ patterns, Q&A formatting, lists for steps

Output JSON:
{
  "score": <int 0-100>,
  "breakdown": {
    "passage_length": <int 0-25>,
    "definitions": <int 0-25>,
    "specificity": <int 0-25>,
    "structure": <int 0-25>
  },
  "strongest": "<one sentence: what this page does well that makes it citable>",
  "top_gap": "<one sentence: the single biggest improvement opportunity if you were rewriting>"
}"""


async def _grade_one_page(
    client: AsyncOpenAI,
    model: str,
    cluster_name: str,
    url: str,
    text: str,
) -> dict | None:
    text_capped = text[:PAGE_TEXT_CAP_CHARS]
    user_block = (
        f"<cluster>{cluster_name}</cluster>\n"
        f"<url>{url}</url>\n"
        f"<page_text>\n{text_capped}\n</page_text>"
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=800,
            temperature=0.2,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "citation_grade",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["score", "breakdown", "strongest", "top_gap"],
                        "properties": {
                            "score": {"type": "integer"},
                            "breakdown": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["passage_length", "definitions", "specificity", "structure"],
                                "properties": {
                                    "passage_length": {"type": "integer"},
                                    "definitions": {"type": "integer"},
                                    "specificity": {"type": "integer"},
                                    "structure": {"type": "integer"},
                                },
                            },
                            "strongest": {"type": "string"},
                            "top_gap": {"type": "string"},
                        },
                    },
                },
            },
            messages=[
                {"role": "system", "content": GRADE_SYSTEM},
                {"role": "user", "content": user_block + "\n\n" + GRADE_INSTRUCTION},
            ],
        )
        return _safe_json_parse(resp.choices[0].message.content or "")
    except Exception as exc:
        log.warning("Grade failed for %s: %s", url, exc)
        return None


# ----------------------------------------------------------------- helpers
def _representative_serp(cluster: dict, serps: dict[str, list[dict]]) -> list[dict]:
    """Pick the SERP whose seed has the highest overlap with this cluster's keywords."""
    cluster_kws = {k.lower() for k in (cluster.get("keywords") or [])}
    best_seed, best_overlap = None, -1
    for seed in serps.keys():
        overlap = 1 if seed.lower() in cluster_kws else 0
        if overlap > best_overlap:
            best_overlap = overlap
            best_seed = seed
    if best_seed is None and serps:
        best_seed = next(iter(serps))
    return serps.get(best_seed or "", [])


def _pick_competitor_urls(cluster: dict, serps: dict[str, list[dict]], n: int) -> list[str]:
    """Top-N unique URLs from the cluster's representative SERP."""
    serp = _representative_serp(cluster, serps)
    seen: set[str] = set()
    urls: list[str] = []
    for item in serp:
        url = item.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= n:
            break
    return urls


async def _fetch_and_extract(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """Returns (domain, readable_text) or None on failure."""
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        if len(resp.content) > MAX_PAGE_BYTES:
            return None
        text = _strip_html(resp.text)
        if len(text) < 500:
            return None
        domain = httpx.URL(url).host or url
        return domain, text
    except Exception as exc:
        log.info("Fetch failed for %s: %s", url, exc)
        return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def _strip_html(html: str) -> str:
    """Cheap-and-cheerful HTML to text. Good enough for citation grading."""
    no_scripts = _SCRIPT_STYLE_RE.sub("", html)
    no_tags = _TAG_RE.sub(" ", no_scripts)
    return _WS_RE.sub(" ", no_tags).strip()


def _safe_json_parse(text: str) -> dict | None:
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
