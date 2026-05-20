"""Render a ResearchResult into a markdown dossier."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from slugify import slugify

from app.config import get_settings
from app.services.research_runner import ResearchResult

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(disabled_extensions=("md", "j2")),
    trim_blocks=False,
    lstrip_blocks=False,
)


def render_dossier(result: ResearchResult) -> str:
    settings = get_settings()

    eupry_set = {r["keyword"].lower() for r in result.eupry_rankings if r.get("keyword")}
    eupry_position_map = {
        r["keyword"].lower(): r.get("position")
        for r in result.eupry_rankings
        if r.get("keyword")
    }

    total_volume = sum((kw.get("volume") or 0) for kw in result.keywords)
    kd_values = [kw["kd"] for kw in result.keywords if kw.get("kd") is not None]
    avg_kd = round(sum(kd_values) / len(kd_values)) if kd_values else 0

    eupry_ranked_count = sum(1 for kw in result.keywords if kw["keyword"].lower() in eupry_set)
    eupry_ranked_pct = (
        round(eupry_ranked_count / len(result.keywords) * 100) if result.keywords else 0
    )

    top_opportunity = _find_top_opportunity(result.keywords, eupry_set)
    cannibalization_risks = _detect_cannibalization(result.eupry_rankings)
    gaps = _content_gaps(result, eupry_set)

    keywords_with_lower = []
    for kw in result.keywords:
        new = dict(kw)
        new["keyword"] = kw["keyword"]
        keywords_with_lower.append(new)

    tmpl = _env.get_template("dossier.md.j2")
    return tmpl.render(
        topic=result.topic,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        location=result.location,
        language=result.language,
        seeds=result.seeds,
        keywords=keywords_with_lower,
        serps=result.serps,
        eupry_rankings=result.eupry_rankings,
        eupry_ranked_set=eupry_set,
        eupry_position_map=eupry_position_map,
        eupry_ranked_count=eupry_ranked_count,
        eupry_ranked_pct=eupry_ranked_pct,
        total_volume=total_volume,
        avg_kd=avg_kd,
        top_opportunity=top_opportunity,
        cannibalization_risks=cannibalization_risks,
        gaps=gaps,
        competitors=result.competitors,
        competitor_rankings=result.competitor_rankings,
        cost_usd=result.cost_usd,
        eupry_domain=settings.eupry_domain,
        ai=result.ai,
        strategy=result.strategy,
    )


def write_dossier(result: ResearchResult, run_id: str) -> str:
    settings = get_settings()
    Path(settings.dossier_dir).mkdir(parents=True, exist_ok=True)
    filename = f"research-{slugify(result.topic)}-{run_id[:8]}.md"
    path = Path(settings.dossier_dir) / filename
    path.write_text(render_dossier(result), encoding="utf-8")
    return str(path)


# --------------------------------------------------------------- analysis helpers
def _find_top_opportunity(keywords: list[dict], eupry_set: set[str]) -> dict | None:
    candidates = [
        kw for kw in keywords
        if kw.get("volume") and kw["keyword"].lower() not in eupry_set and (kw.get("kd") or 100) < 50
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda kw: kw["volume"])


def _detect_cannibalization(rankings: list[dict]) -> list[dict]:
    """Find keywords where Eupry has 2+ URLs ranking."""
    by_keyword: dict[str, list[str]] = {}
    for r in rankings:
        kw = (r.get("keyword") or "").lower()
        url = r.get("url")
        if not kw or not url:
            continue
        by_keyword.setdefault(kw, []).append(url)
    return [
        {"keyword": kw, "urls": urls}
        for kw, urls in by_keyword.items()
        if len(urls) > 1
    ]


def _content_gaps(result: ResearchResult, eupry_set: set[str]) -> list[dict]:
    """Volume >= 200, Eupry doesn't rank, >= 1 competitor ranks top 10."""
    competitor_top10: dict[str, list[str]] = {}
    for comp_name, rankings in result.competitor_rankings.items():
        for r in rankings:
            kw = (r.get("keyword") or "").lower()
            pos = r.get("position") or 999
            if pos <= 10:
                competitor_top10.setdefault(kw, []).append(comp_name)

    gaps = []
    for kw in result.keywords:
        kw_lower = kw["keyword"].lower()
        if (kw.get("volume") or 0) < 200:
            continue
        if kw_lower in eupry_set:
            continue
        comps = competitor_top10.get(kw_lower, [])
        if not comps:
            continue
        gaps.append({
            "keyword": kw["keyword"],
            "volume": kw["volume"],
            "kd": kw.get("kd"),
            "intent": kw.get("intent", "unknown"),
            "competitors": comps,
        })
    gaps.sort(key=lambda g: g["volume"] or 0, reverse=True)
    return gaps[:50]
