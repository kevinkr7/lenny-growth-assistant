import json
from types import SimpleNamespace

import pytest

from app.agent import ABSTENTION, bounded_history, retrieval_query, route, run_agent
from app.artifacts import citations, normalize_citations, sanitize_html, validate, word_count
from app.errors import AppError
from app.ingest import chunk_text, load_demo_manifest, parse_transcript
from app.retrieval import focus_query


@pytest.fixture(autouse=True)
def isolate_structural_tests_from_semantic_model(monkeypatch):
    # Semantic judgments are tested separately in test_behavior.py; these tests
    # isolate structural repair/security contracts and must never call a live model.
    async def review(*args):
        return {"verdict": "supported", "issues": [], "method": "test_fixture"}

    monkeypatch.setattr("app.agent.review_grounding", review)


def evidence():
    return [
        {
            "citation": "S1",
            "source_id": "one",
            "chunk_id": "chunk",
            "guest": "Test guest",
            "episode_title": "Testing PMF",
            "excerpt": "Survey the people who depend on your product and focus on a narrow segment.",
        }
    ]


def test_artifact_retrieval_keeps_topic_without_output_instructions():
    assert (
        focus_query(
            "Write a Ship 30 essay for founders about Rahul Vohra's approach to product-market fit.", ["Rahul Vohra"]
        )
        == "product-market fit."
    )
    assert "retention" in focus_query("Compare Rahul Vohra and Sean Ellis on retention", ["Rahul Vohra", "Sean Ellis"])


def test_metadata_and_chunk_bounds():
    metadata, text = parse_transcript(
        "---\nguest: Test Guest\npublish_date: 2025-03-23\n---\n# Title\n\n## Transcript\n"
        + "A real transcript sentence. " * 300
    )
    assert metadata["publish_date"] == "2025-03-23"
    chunks = chunk_text(text)
    assert len(chunks) > 2 and all(len(c.split()) <= 220 for c in chunks)
    assert chunks[0].split()[-40:] == chunks[1].split()[:40]
    assert chunks == chunk_text(text)


def test_no_silent_metadata_guess():
    with pytest.raises(ValueError):
        parse_transcript("An invalid unstructured source")


def test_public_demo_manifest_has_provenance_but_no_transcript_content():
    manifest = load_demo_manifest()
    assert manifest["schema_version"] == 1
    assert manifest["repository"] == "https://github.com/thechangelog/transcripts"
    assert manifest["license"]["spdx_id"] == "CC-BY-SA-4.0"
    assert manifest["sources"]
    for source in manifest["sources"]:
        assert {"path", "guest", "episode_title", "episode_url"} <= source.keys()
        assert not {"transcript", "excerpt", "content", "text"} & source.keys()


def test_demo_manifest_rejects_embedded_transcript_text(tmp_path):
    manifest = load_demo_manifest()
    manifest["sources"][0]["excerpt"] = "This must not be shipped inside a source manifest."
    path = tmp_path / "unsafe-demo-sources.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="metadata only"):
        load_demo_manifest(path)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("How should I prioritize?", "answer"),
        ("Turn this into a Ship 30 essay.", "ship30"),
        ("Build a landing page.", "html"),
        ("Create a Markdown playbook.", "markdown"),
    ],
)
def test_routing(question, expected):
    assert route(question) == expected
    assert route(question, "answer") == "answer"


def test_context_is_bounded_and_failed_turns_excluded():
    history = [{"role": "user", "content": "x" * 10000} for _ in range(30)]
    history.append({"role": "assistant", "content": "secret failure", "metadata": {"status": "failed"}})
    bounded = bounded_history(history)
    assert sum(len(m["content"]) for m in bounded) <= 8000
    assert "secret failure" not in str(bounded)
    assert "product-market fit" in retrieval_query(
        "How does that change for B2B?", "answer", [{"role": "user", "content": "How to find product-market fit?"}]
    )


def test_artifact_defense_in_depth():
    html = sanitize_html(
        """<html><head><style>@import url(https://evil.test); body {color:red;background:url(https://evil.test/a)} .safe {display:grid;gap:20px}</style></head><body onload="alert(1)"><script>parent.alert(1)</script><iframe src="https://evil.test"></iframe><form action="https://evil.test"><input name="x"></form><a href="javascript:alert(1)">bad</a><img src=x onerror="alert(1)"><div style="color:blue;background-image:url(https://evil.test)">Safe [S1]</div></body></html>"""
    )
    assert "<script" not in html and "<iframe" not in html and "<form" not in html
    assert "onload=" not in html and "onerror=" not in html and "javascript:" not in html
    assert "evil.test" not in html
    assert "Content-Security-Policy" in html and "script-src 'none'" in html
    assert "display:grid" in html and "color:blue" in html and "[S1]" in html


@pytest.mark.parametrize(
    "payload",
    [
        "<svg><script>alert(1)</script></svg><p>[S1]</p>",
        '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=1 onerror=alert(1)>">',
        '<div style="background:u\\72l(https://evil.test)">text</div>',
        '<meta http-equiv="refresh" content="0;url=https://evil.test"><base href="https://evil.test">',
    ],
)
def test_sanitizer_adversarial(payload):
    html = sanitize_html(payload)
    assert "onerror=" not in html and "<script" not in html and "evil.test" not in html


def test_validation_rejects_fabricated_citations_and_short_essay():
    assert validate("Good advice [S9]", "answer", evidence())
    assert validate("# A short essay\n## Idea\nToo short [S1]", "ship30", evidence())
    assert not validate("Focus on your strongest users. [S1]", "answer", evidence())
    assert word_count("A useful claim. [S1]") == 3
    assert citations("A [S1], B [S2], C [S1]") == {"S1", "S2"}


def test_citation_groups_and_percentage_measure_are_checked():
    assert normalize_citations("A claim [S1, S2]") == "A claim [S1][S2]"
    assert citations("A claim [S1, S2]") == {"S1", "S2"}
    source = [{**evidence()[0], "excerpt": "People ask whether reaching 40% is sufficient. Check retention as well."}]
    assert any("40% adoption" in p for p in validate("Reach a 40% adoption rate. [S1]", "answer", source))
    assert validate("For example, 60% of users might leave.", "answer", source)
    assert validate("For example, 60% of users might leave. [S1]", "answer", source)
    assert not validate("People ask whether reaching 40% is sufficient. [S1]", "answer", source)


def test_citation_validation_handles_bare_known_ids_without_accepting_empty_drafts():
    source = evidence()
    assert normalize_citations("A supported claim S1.", {"S1"}) == "A supported claim [S1]."
    assert not validate("A supported claim [S1].", "answer", source)
    assert validate("A supported claim [S9].", "answer", source)
    assert validate("A supported claim with no citation.", "answer", source)
    assert validate("[S1]", "answer", source)
    assert validate("S1", "answer", source)


def test_html_percentage_validation_ignores_layout_but_checks_visible_claims():
    assert not validate(
        '<html><head><style>main {width:100%}</style></head><main style="height:50%">Focus on users. [S1]</main></html>',
        "html",
        evidence(),
    )
    assert validate('<html><main style="width:100%">60% of users left. [S1]</main></html>', "html", evidence())


async def test_abstention_does_not_call_provider(monkeypatch):
    async def search(_):
        return [], 1.5

    async def forbidden(*args):
        raise AssertionError("Provider should not be called")

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", forbidden)
    events = [e async for e in run_agent("Quantum spell", "ollama", "answer", [])]
    assert events[-1][1]["content"] == ABSTENTION
    assert events[-1][1]["sources"] == []


async def test_agent_repairs_once_and_retains_verified_sources(monkeypatch):

    async def search(_):
        return evidence(), 2

    calls = []

    async def fake(provider, system, prompt):
        calls.append((provider, prompt))
        yield "Invented [S99]" if len(calls) == 1 else "Focus on a narrow segment. [S1]"

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", fake)
    events = [e async for e in run_agent("Find PMF", "gemini", "answer", [])]
    assert len(calls) == 2 and calls[0][0] == "gemini"
    assert any(e[0] == "reset" for e in events)
    assert events[-1][1]["sources"] == evidence()


async def test_repeated_invalid_generation_stops(monkeypatch):
    async def search(_):
        return evidence(), 2

    calls = []

    async def fake(*args):
        calls.append(1)
        yield "No citations"

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", fake)
    with pytest.raises(AppError, match="citation IDs"):
        _ = [e async for e in run_agent("Find PMF", "ollama", "answer", [])]
    assert len(calls) == 2


async def test_html_generation_is_sanitized(monkeypatch):
    async def search(_):
        return evidence(), 2

    async def fake(*args):
        yield "<html><title>PMF</title><body><main><h1>Focus [S1]</h1><script>alert(1)</script></main></body></html>"

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", fake)
    events = [e async for e in run_agent("Make a page", "ollama", "html", [])]
    artifact = events[-1][1]["artifact"]
    assert artifact["title"] == "PMF" and artifact["source_ids"] == ["one"]
    assert "<script" not in artifact["content"]


@pytest.mark.parametrize("include_list", [True, False])
async def test_short_valid_essay_gets_one_bounded_expansion(monkeypatch, include_list, tmp_path):
    async def search(_):
        return evidence(), 1

    draft = (
        "# A focused essay\n\n"
        + "\n\n".join(f"## Lesson {i}\n\n" + "Explain the supported idea clearly. " * 60 + "[S1]" for i in range(3))
        + ("\n\n- **Next step:** talk to users. [S1]" if include_list else "\n\nTalk to users. [S1]")
    )
    calls = []
    configured_guide = tmp_path / "custom-essay-guide.md"
    configured_guide.write_text("CUSTOM_CONFIGURED_ESSAY_GUIDE", encoding="utf-8")
    monkeypatch.setattr(
        "app.agent.settings",
        lambda: SimpleNamespace(
            ship30_guide_file=str(configured_guide), context_char_limit=8000, context_messages=8
        ),
    )

    async def fake(provider, system, prompt):
        if not calls:
            assert "CUSTOM_CONFIGURED_ESSAY_GUIDE" in system
        calls.append(prompt)
        yield (
            draft
            if len(calls) == 1
            else "## Suggested application\n\n- **Next step:** " + "Consider one specific next action. " * 60 + "[S1]"
        )

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", fake)
    events = [event async for event in run_agent("Write an essay", "ollama", "ship30", [])]
    artifact = events[-1][1]["artifact"]
    assert len(calls) == 2 and "ONLY one additional" in calls[1]
    assert 1125 <= artifact["word_count"] <= 1375
    assert artifact["content"].startswith("# A focused essay")
