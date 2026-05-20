"""Orchestrates a full keyword research run.

Stages:
  1. Expand each seed via keyword_ideas
  2. Dedup all keywords (seeds + expansions)
  3. Enrich with volume, difficulty, intent in batches
  4. Fetch SERP top 10 per seed
  5. Cannibalization: cross-ref against eupry.com ranked_keywords
  6. Competitor coverage: cross-ref against each competitor's ranked_keywords
  7. Hand off to markdown_gen
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.config import get_settings
from app.services.ai_analysis import AIAnalysis, analyze
from app.services.competitors import get_competitors
from app.services.dataforseo import (
    LANGUAGE_CODES,
    LOCATION_CODES,
    DataForSEOClient,
    DataForSEOError,
)


@dataclass
class ResearchResult:
    topic: str
    location: str
    language: str
    seeds: list[str]
    keywords: list[dict] = field(default_factory=list)  # [{keyword, volume, kd, intent, cpc, seed_parent}]
    serps: dict[str, list[dict]] = field(default_factory=dict)  # seed -> [{rank, domain, url, title}]
    eupry_rankings: list[dict] = field(default_factory=list)  # rankings overlapping our keyword universe
    competitor_rankings: dict[str, list[dict]] = field(default_factory=dict)  # name -> [...]
    competitors: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    ai: AIAnalysis | None = None


async def run_research(
    topic: str,
    seeds: list[str],
    market: str,  # "us" or "de"
    expansions_per_seed: int = 30,
) -> ResearchResult:
    settings = get_settings()
    location_code = LOCATION_CODES[market]
    language_code = LANGUAGE_CODES[market]

    result = ResearchResult(
        topic=topic,
        location=market.upper(),
        language=language_code,
        seeds=seeds,
    )

    async with DataForSEOClient() as df:
        # ---- 1. expand seeds in parallel
        expansions = await asyncio.gather(
            *[df.keyword_ideas(s, location_code, language_code, limit=expansions_per_seed) for s in seeds],
            return_exceptions=True,
        )

        keyword_to_seed: dict[str, str] = {s: s for s in seeds}
        all_keywords: set[str] = set(seeds)
        for seed, ideas in zip(seeds, expansions, strict=True):
            if isinstance(ideas, Exception):
                continue
            for item in ideas:
                kw = (item.get("keyword") or "").strip().lower()
                if not kw or kw in all_keywords:
                    continue
                all_keywords.add(kw)
                keyword_to_seed[kw] = seed

        if df.total_cost_usd > settings.max_cost_per_run_usd:
            raise DataForSEOError(
                f"Cost cap ${settings.max_cost_per_run_usd} hit after expansion (${df.total_cost_usd:.2f})"
            )

        # ---- 2. enrich (volume + difficulty + intent run in parallel)
        kw_list = list(all_keywords)
        volume_task = df.search_volume(kw_list, location_code, language_code)
        kd_task = df.keyword_difficulty(kw_list, location_code, language_code)
        intent_task = df.search_intent(kw_list, language_code)
        volume_map, kd_map, intent_map = await asyncio.gather(volume_task, kd_task, intent_task)

        for kw in kw_list:
            v = volume_map.get(kw, {})
            result.keywords.append({
                "keyword": kw,
                "volume": v.get("volume"),
                "cpc": v.get("cpc"),
                "competition": v.get("competition"),
                "kd": kd_map.get(kw),
                "intent": intent_map.get(kw, "unknown"),
                "seed_parent": keyword_to_seed.get(kw, kw),
            })

        # ---- 3. SERP top 10 per seed (parallel)
        serp_results = await asyncio.gather(
            *[df.serp_top10(s, location_code, language_code) for s in seeds],
            return_exceptions=True,
        )
        for seed, serp in zip(seeds, serp_results, strict=True):
            result.serps[seed] = [] if isinstance(serp, Exception) else serp

        # ---- 4. cannibalization (eupry domain ranked_keywords) + competitor coverage
        competitors = await get_competitors()
        result.competitors = competitors

        domain_targets = [settings.eupry_domain] + [c["domain"] for c in competitors]
        domain_tasks = [
            df.domain_ranked_keywords(d, location_code, language_code, limit=1000) for d in domain_targets
        ]
        domain_results = await asyncio.gather(*domain_tasks, return_exceptions=True)

        keyword_set = set(kw_list)
        for domain, rankings in zip(domain_targets, domain_results, strict=True):
            relevant = []
            if isinstance(rankings, Exception):
                rankings = []
            for r in rankings:
                if (r.get("keyword") or "").lower() in keyword_set:
                    relevant.append(r)
            if domain == settings.eupry_domain:
                result.eupry_rankings = relevant
            else:
                competitor_name = next((c["name"] for c in competitors if c["domain"] == domain), domain)
                result.competitor_rankings[competitor_name] = relevant

        result.cost_usd = round(df.total_cost_usd, 4)

    # AI analysis runs after data collection so it can see the full picture.
    eupry_set = {r["keyword"].lower() for r in result.eupry_rankings if r.get("keyword")}
    result.ai = await analyze(
        topic=topic,
        seeds=seeds,
        keywords=result.keywords,
        eupry_ranked_set=eupry_set,
        competitor_rankings=result.competitor_rankings,
        market=market,
    )

    return result
