"""Minimal smoke tests — verify imports and template rendering work."""
from app.services.ai_analysis import AIAnalysis
from app.services.markdown_gen import render_dossier
from app.services.research_runner import ResearchResult
from app.services.strategy_analysis import StrategyAnalysis


def _basic_result(
    ai: AIAnalysis | None = None,
    strategy: StrategyAnalysis | None = None,
) -> ResearchResult:
    return ResearchResult(
        topic="Smoke test",
        location="US",
        language="en",
        seeds=["data logger calibration"],
        keywords=[{
            "keyword": "data logger calibration",
            "volume": 320,
            "cpc": 4.20,
            "competition": "MEDIUM",
            "kd": 32,
            "intent": "commercial",
            "seed_parent": "data logger calibration",
        }],
        ai=ai,
        strategy=strategy,
    )


def test_renders_without_ai():
    md = render_dossier(_basic_result())
    assert "Keyword Research: Smoke test" in md
    assert "data logger calibration" in md
    assert "## 1. Executive summary" in md
    # AI-only sections should be absent
    assert "## 2. Content clusters" not in md
    assert "Strategic read (AI-generated)" not in md
    # Downstream sections still rendered (numbering is for the full template;
    # individual sections still appear when their data is present)
    assert "Cannibalization check" in md
    assert "Keyword universe" in md
    assert "Methodology" in md


def test_renders_with_ai():
    ai = AIAnalysis(
        clusters=[
            {
                "name": "Calibration fundamentals",
                "theme": "How data logger calibration works in regulated environments",
                "keywords": ["data logger calibration"],
                "rationale": "Foundational article; covers the basics every other piece links to.",
            }
        ],
        exec_summary="### Where the volume is\nFoo bar.\n\n### What to prioritize\n- Do X",
        enabled=True,
    )
    md = render_dossier(_basic_result(ai=ai))
    assert "## 2. Content clusters" in md
    assert "Calibration fundamentals" in md
    assert "Strategic read (AI-generated)" in md
    assert "Foo bar." in md
    # Strategy sections absent when strategy=None
    assert "## 3. Personas" not in md
    assert "## 4. Competitor citation-readiness" not in md


def test_renders_with_strategy():
    ai = AIAnalysis(
        clusters=[
            {
                "name": "Calibration fundamentals",
                "theme": "Theme",
                "keywords": ["data logger calibration"],
                "rationale": "Rationale",
            }
        ],
        exec_summary="### A\nx",
        enabled=True,
    )
    strategy = StrategyAnalysis(
        page_types={
            "Calibration fundamentals": {
                "dominant": "ultimate-guide",
                "consensus_pct": 70,
                "rationale": "Long-form titles dominate the SERP.",
            }
        },
        personas=[
            {"name": "GMP Compliance Lead", "role": "Regulatory affairs at a CDMO", "motivation": "Pass audits"},
        ],
        cluster_persona_fit={
            "Calibration fundamentals": [
                {"persona": "GMP Compliance Lead", "score": 78, "gap_note": "Strong fit for audit prep content."},
            ],
        },
        citation_grades={
            "Calibration fundamentals": [
                {
                    "url": "https://example.com/x",
                    "domain": "example.com",
                    "score": 72,
                    "breakdown": {"passage_length": 20, "definitions": 18, "specificity": 17, "structure": 17},
                    "strongest": "Clear definitions in the opening paragraph.",
                    "top_gap": "Add a structured FAQ block.",
                }
            ],
        },
        enabled=True,
    )
    md = render_dossier(_basic_result(ai=ai, strategy=strategy))
    assert "Google rewards:" in md
    assert "ultimate-guide" in md
    assert "## 3. Personas" in md
    assert "GMP Compliance Lead" in md
    assert "Cluster ↔ persona fit" in md
    assert "## 4. Competitor citation-readiness" in md
    assert "**72/100**" in md
    assert "Add a structured FAQ block." in md
    # Renumbered downstream
    assert "## 5. Cannibalization check" in md
    assert "## 10. Methodology" in md


def test_parse_seeds_dedupes_and_strips():
    from app.routes.research import _parse_seeds
    raw = "  foo  \nBar\nfoo\n# comment\n\nbaz"
    assert _parse_seeds(raw) == ["foo", "Bar", "baz"]


def test_safe_json_parse_handles_code_fences():
    from app.services.ai_analysis import _safe_json_parse
    assert _safe_json_parse('```json\n{"a": 1}\n```') == {"a": 1}
    assert _safe_json_parse('prefix {"a": 1} suffix') == {"a": 1}
    assert _safe_json_parse("not json") is None
