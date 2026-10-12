"""Reported manual failures, exercised with deterministic model/retrieval fixtures.

These tests establish routing/data contracts, not live Ollama answer quality.
"""

import hashlib
import json
from pathlib import Path

import pytest

from app.agent import run_agent
from app.artifacts import requested_constraints, unprovided_numbers, validate
from app.config import ROOT
from app.errors import AppError
from app.grounding import quotation_problems
from app.routing import abstention_text, plan_turn

CASES = {item["id"]: item["prompt"] for item in json.loads(
    (ROOT / "evals/response-regressions.json").read_text(encoding="utf-8")
)["cases"]}
SOURCE = json.loads((Path(__file__).parent / "fixtures/elena-evidence.json").read_text(encoding="utf-8"))
CHECKLIST = '# Competitor Research Checklist\n\n' + '\n'.join([
    '- [ ] Define the customer problem before comparing features.',
    '- [ ] Record the context behind each competing approach.',
    '- [ ] Interview users about their needs.',
    '- [ ] Develop an independent design hypothesis.',
    '- [ ] Test the hypothesis before adopting it.',
])


async def forbidden(*args, **kwargs):
    raise AssertionError("This response mode must not call this dependency")


@pytest.mark.parametrize(("case", "intent", "output", "search"), [
    (1, "conversation", "answer", False), (2, "conversation", "answer", False),
    (3, "conversation", "answer", False), (4, "quote", "answer", False),
    (5, "metadata", "answer", True), (6, "knowledge", "answer", True),
    (7, "evidence", "answer", True), (8, "knowledge", "answer", True),
    (9, "conversation", "answer", False), (10, "conversation", "answer", False),
    (11, "knowledge", "answer", True), (12, "writing", "markdown", False),
    (13, "conversation", "answer", False), (14, "conversation", "answer", False),
])
def test_all_reported_prompts_select_applicable_validation(case, intent, output, search):
    plan = plan_turn(CASES[case])
    assert (plan.intent, plan.output, plan.search) == (intent, output, search)


@pytest.mark.parametrize(("case", "answer"), [
    (1, "OLLAMA_TEST_OK"), (14, "PASS-123"), (2, "Acknowledged: ORCHID-472."),
    (3, "Hello!"),
    (13, "1. Study the customer problem.\n2. Compare approaches in context.\n3. Test your own hypothesis."),
])
async def test_direct_outputs_never_reach_source_validation(monkeypatch, case, answer):
    async def generate(provider, system, prompt):
        assert json.loads(prompt)["current_request"] == CASES[case]
        yield answer

    monkeypatch.setattr("app.agent.generate", generate)
    monkeypatch.setattr("app.agent.search_knowledge_base", forbidden)
    monkeypatch.setattr("app.agent.validate", forbidden)
    monkeypatch.setattr("app.agent.review_grounding", forbidden)
    events = [event async for event in run_agent(CASES[case], "ollama", "auto", [], [SOURCE])]
    result = events[-1][1]
    assert result["content"] == answer and not result["sources"] and not result["artifacts"]
    assert events[0][1]["grounding_required"] is False


def test_exact_parser_is_general_and_not_a_count_instruction():
    assert plan_turn("Return exactly the following string, without explanation:\n\nAZURE-981").exact == "AZURE-981"
    assert plan_turn('Reply with exactly this text and nothing else: "  keep space  "').exact == "  keep space  "
    assert plan_turn("Return exactly three tips: explain customer interviews.").exact is None
    assert plan_turn("Reply with exactly PASS. Do not call any tools.").exact == "PASS"


@pytest.mark.parametrize("case", [9, 10])
async def test_no_search_followup_keeps_recent_history(monkeypatch, case):
    history = [{"role": "user", "content": CASES[8]}]

    async def generate(provider, system, prompt):
        assert json.loads(prompt)["history"] == history
        yield "You asked why a growth team should not simply copy competitors."

    monkeypatch.setattr("app.agent.generate", generate)
    monkeypatch.setattr("app.agent.search_knowledge_base", forbidden)
    result = [event async for event in run_agent(CASES[case], "ollama", "auto", history)][-1][1]
    assert not result["abstained"] and not result["sources"]


def test_user_context_artifacts_reject_unprovided_numeric_details():
    request = "Create a brief using the product and budget details I gave you. Include one hypothesis and one metric."
    history = [{"role": "user", "content": "Product: podcast app. Budget: $500."}]
    assert unprovided_numbers("Hypothesis: test a short format. Budget: $500.", request, history) == []
    assert unprovided_numbers(
        "<html><head><style>body{margin:0;padding:32px}</style></head><body><p>Budget: $500.</p></body></html>",
        request,
        history,
    ) == []
    problems = unprovided_numbers(
        "Run a 5-episode campaign for users aged 22–30 over 14 days with a 20% target. Budget: $500.",
        request,
        history,
    )
    assert problems and all(value in problems[0] for value in ["5", "14", "20", "22", "30"])


@pytest.mark.parametrize("case", [5, 7])
async def test_retrieved_metadata_and_raw_evidence_are_not_model_claims(monkeypatch, case):
    async def search(query):
        assert SOURCE["guest"] in query
        return [SOURCE], 2

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", forbidden)
    monkeypatch.setattr("app.agent.review_grounding", forbidden)
    question = CASES[case].replace("Elena Verna", SOURCE["guest"])
    result = [event async for event in run_agent(question, "ollama", "auto", [])][-1][1]
    assert SOURCE["episode_title"] in result["content"] and SOURCE["publish_date"] in result["content"]
    assert "Author: Unavailable" in result["content"] and result["response_kind"] == "source_lookup"
    assert result["sources"][0]["excerpt"] == SOURCE["excerpt"] and not result["abstained"]
    if case == 7:
        assert SOURCE["excerpt"] in result["content"]


async def test_missing_metadata_stays_unavailable(monkeypatch):
    async def search(query):
        return [{**SOURCE, "publish_date": None, "episode_title": None}], 0

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", forbidden)
    question = CASES[5].replace("Elena Verna", SOURCE["guest"])
    result = [event async for event in run_agent(question, "ollama", "auto", [])][-1][1]
    assert "Title: Unavailable" in result["content"] and "Publication date: Unavailable" in result["content"]


async def test_missing_label_is_not_reassigned_to_arbitrary_library_passage(monkeypatch):
    monkeypatch.setattr("app.agent.search_knowledge_base", forbidden)
    monkeypatch.setattr("app.agent.generate", forbidden)
    result = [event async for event in run_agent(CASES[4], "ollama", "auto", [])][-1][1]
    assert result["abstained"] and "not global library records" in result["content"]


async def test_model_validator_and_result_share_exact_evidence(monkeypatch):
    captured = {}
    calls = []
    question = "How did the fictional team choose its next usability test?"
    answer = "It used which onboarding label volunteers understood more quickly to choose its next small usability test. [S3]"

    async def search(query):
        return [SOURCE], 1

    async def generate(provider, system, prompt):
        calls.append(prompt)
        assert SOURCE["excerpt"] in prompt and "[S3]" in prompt
        yield answer

    original_validate = validate

    def check(content, mode, evidence):
        captured["validator"] = evidence
        return original_validate(content, mode, evidence)

    async def review(content, evidence, provider, question):
        assert content == answer and question == "How did the fictional team choose its next usability test?"
        captured["reviewer"] = evidence
        return {"verdict": "supported", "issues": [], "method": "fixture"}

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", generate)
    monkeypatch.setattr("app.agent.validate", check)
    monkeypatch.setattr("app.agent.review_grounding", review)
    events = [event async for event in run_agent(question, "ollama", "auto", [], [SOURCE])]
    result = events[-1][1]
    assert result["content"] == answer and not result["abstained"] and len(calls) == 1
    assert captured["validator"] == captured["reviewer"] == result["sources"] == [SOURCE]
    manifest = next(data["evidence_manifest"] for event, data in events if "evidence_manifest" in data)
    assert manifest == [{"citation": "S3", "chunk_id": SOURCE["chunk_id"],
                         "excerpt_sha256": hashlib.sha256(SOURCE["excerpt"].encode()).hexdigest()}]


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("case", [6, 11])
async def test_absence_response_is_not_forced_to_have_a_citation(monkeypatch, empty, case):
    async def search(query):
        return ([] if empty else [SOURCE]), 1

    async def generate(*args):
        yield abstention_text(CASES[case])

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", forbidden if empty else generate)
    monkeypatch.setattr("app.agent.validate", forbidden)
    monkeypatch.setattr("app.agent.review_grounding", forbidden)
    result = [event async for event in run_agent(CASES[case], "ollama", "auto", [])][-1][1]
    assert result["abstained"] and result["content"] == abstention_text(CASES[case])
    assert not result["sources"] and result["artifact"] is None


async def test_absence_response_overrides_stale_artifact_mode(monkeypatch):
    async def search(_query):
        return [], 0

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    result = [event async for event in run_agent(CASES[11], "ollama", "ship30", [])][-1][1]
    assert result["abstained"] and result["content"] == abstention_text(CASES[11])
    assert result["artifact"] is None


@pytest.mark.parametrize("both", [False, True])
async def test_original_artifact_does_not_borrow_saved_sources(monkeypatch, both):
    question = CASES[12] + (" Also create a separate HTML artifact." if both else "")

    async def generate(provider, system, prompt):
        assert SOURCE["excerpt"] not in prompt and "Answer strictly" not in system
        assert "No podcast evidence" in system
        yield CHECKLIST

    monkeypatch.setattr("app.agent.generate", generate)
    monkeypatch.setattr("app.agent.search_knowledge_base", forbidden)
    monkeypatch.setattr("app.agent.review_grounding", forbidden)
    result = [event async for event in run_agent(question, "ollama", "auto", [], [SOURCE])][-1][1]
    assert not result["abstained"] and not result["sources"]
    assert len(result["artifacts"]) == (2 if both else 1)
    assert result["artifacts"][0]["content"] == CHECKLIST
    assert all(not item["evidence"] and not item["source_ids"] for item in result["artifacts"])
    if both:
        assert result["artifacts"][1]["type"] == "html"
        assert "script-src 'none'" in result["artifacts"][1]["content"]


def test_format_validation_and_citation_rules_remain_separate():
    assert not validate(CHECKLIST, "markdown", [], grounded=False)
    assert validate(CHECKLIST, "markdown", [])  # Grounded writing still requires citations.
    assert validate(CHECKLIST + " [S3]", "markdown", [], grounded=False)
    assert not requested_constraints(CHECKLIST, CASES[12])
    assert requested_constraints(CHECKLIST.rsplit("\n", 1)[0], CASES[12])
    assert requested_constraints("1. One tip.\n2. Another tip.", CASES[13])


def test_quote_must_match_its_cited_passage_not_another_retrieved_chunk():
    source = {"citation": "S3", "excerpt": "Research the customer before copying the competitor."}
    unrelated = {"citation": "S4", "excerpt": "A different discussion about pricing."}
    assert not quotation_problems('"Research the customer before copying the competitor." [S3]', [source, unrelated])
    assert quotation_problems('"Research the customer before copying the competitor." [S4]', [source, unrelated])


async def test_invalid_draft_repair_retains_original_task_and_sources(monkeypatch):
    calls = []
    question = "How did the fictional team choose its next usability test?"

    async def generate(provider, system, prompt):
        calls.append(prompt)
        assert question in prompt and SOURCE["excerpt"] in prompt and "[S3]" in prompt
        yield "A claim with no citation." if len(calls) == 1 else "The team used which onboarding label volunteers understood more quickly to choose its next test. [S3]"

    async def search(query):
        return [SOURCE], 0

    async def review(*args):
        return {"verdict": "supported", "issues": [], "method": "fixture"}

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", generate)
    monkeypatch.setattr("app.agent.review_grounding", review)
    result = [event async for event in run_agent(question, "ollama", "auto", [], [SOURCE])][-1][1]
    assert len(calls) == 2 and calls[0] != calls[1] and not result["abstained"]
    assert all(SOURCE["excerpt"] in prompt and "[S3]" in prompt for prompt in calls)
    assert result["content"] == "The team used which onboarding label volunteers understood more quickly to choose its next test. [S3]"


async def test_single_word_grounded_draft_is_not_mistaken_for_a_valid_citation(monkeypatch):
    calls = []
    question = "What did the fictional team show to volunteer users?"

    async def search(_):
        return [SOURCE], 0

    async def generate(provider, system, prompt):
        calls.append(prompt)
        yield "No"

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", generate)
    with pytest.raises(AppError, match="citation IDs"):
        _ = [event async for event in run_agent(question, "ollama", "auto", [])]
    assert len(calls) == 2
