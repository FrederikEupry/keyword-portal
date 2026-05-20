"""Minimal smoke tests — verify imports and template rendering work."""
from app.services.ai_analysis import AIAnalysis
from app.services.markdown_gen import render_dossier
from app.services.research_runner import ResearchResult


def _basic_result(ai: AIAnalysis | None = None) -> ResearchResult:
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
    )


def test_renders_without_ai():
    md = render_dossier(_basic_result())
    assert "Keyword Research: Smoke test" in md
    assert "data logger calibration" in md
    assert "## 1. Executive summary" in md
    # AI-only sections should be absent
    assert "## 2. Content clusters" not in md
    assert "Strategic read (AI-generated)" not in md
    # Downstream sections still numbered
    assert "## 3. Cannibalization check" in md


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


def test_parse_seeds_dedupes_and_strips():
    from app.routes.research import _parse_seeds
    raw = "  foo  \nBar\nfoo\n# comment\n\nbaz"
    assert _parse_seeds(raw) == ["foo", "Bar", "baz"]


def test_safe_json_parse_handles_code_fences():
    from app.services.ai_analysis import _safe_json_parse
    assert _safe_json_parse('```json\n{"a": 1}\n```') == {"a": 1}
    assert _safe_json_parse('prefix {"a": 1} suffix') == {"a": 1}
    assert _safe_json_parse("not json") is None
