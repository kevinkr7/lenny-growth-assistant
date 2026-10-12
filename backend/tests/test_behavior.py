import json

import pytest

from app.agent import needs_history, run_agent
from app.evidence import contextual_passage_metadata, metadata_integrity, passage_metadata, stable_citations
from app.grounding import quotation_problems, review_grounding
from app.retrieval import focus_query
from app.routing import conversation_recall, plan_turn


def source(ref="S3"):
    return {
        "citation": ref,
        "source_id": "episode-a",
        "chunk_id": "chunk-a",
        "revision": "abc123",
        "guest": "Guest Example",
        "episode_title": "A precise episode title",
        "publish_date": "2025-03-23",
        "source_url": "https://example.org/transcript",
        "speakers": ["Guest Example"],
        "timestamp_start": "00:12:30",
        "excerpt": "Guest Example (00:12:30): Interview customers every week. Repeated contact helps the team discover unmet needs.",
    }


async def supported(*args):
    return {"verdict": "supported", "issues": [], "method": "test_fixture"}


@pytest.mark.parametrize(
    ("prompt_text", "response"),
    [
        (
            "Hi Lenny, greet me in one sentence.",
            "Hi, I can help you explore Lenny's podcast and create useful documents.",
        ),
        ("Reply with exactly: `OLLAMA_TEST_OK`.", "OLLAMA_TEST_OK"),
        ("Acknowledge `ORCHID-472` without searching.", "Acknowledged: ORCHID-472."),
        ("Reply with exactly: `A different arbitrary literal 936`.", "A different arbitrary literal 936"),
        ('Reply with exactly: "  preserve spacing  ".', "  preserve spacing  "),
        (
            "Please greet me in one sentence and briefly explain what you can help me with. Don't search the knowledge base.",
            "Hello, I help you explore product insights and create sourced documents.",
        ),
    ],
)
async def test_conversational_constraints_never_trigger_retrieval(monkeypatch, prompt_text, response):
    async def forbidden(*args):
        raise AssertionError("Conversation must not retrieve")

    async def direct(provider, system, prompt):
        assert provider == "ollama" and prompt_text in json.loads(prompt)["current_request"]
        assert "3–5 actionable" not in system
        yield response

    monkeypatch.setattr("app.agent.search_knowledge_base", forbidden)
    monkeypatch.setattr("app.agent.generate", direct)
    history = [
        {"role": "assistant", "content": "Old unrelated growth advice [S3]", "metadata": {"sources": [source()]}}
    ]
    events = [event async for event in run_agent(prompt_text, "ollama", "auto", history)]
    result = events[-1][1]
    assert result["content"] == response and result["sources"] == [] and result["artifact"] is None
    assert not any(event == "status" and data.get("phase") == "retrieval" for event, data in events)


async def test_exact_output_is_validated_and_repaired_without_extra_prose(monkeypatch):
    outputs = iter(["Sure: blue sky", "blue sky"])

    async def direct(*args):
        yield next(outputs)

    monkeypatch.setattr("app.agent.generate", direct)
    results = [event async for event in run_agent('Reply with exactly: "blue sky".', "ollama", "auto", [])]
    assert results[-1][1]["content"] == "blue sky"


async def test_explicit_history_recall_uses_session_history_without_retrieval(monkeypatch):
    request = (
        "Earlier, I asked you about Elena Verna's exact personal income in March 2025. "
        "What conclusion did you reach about that question? Answer based on our conversation history, "
        "not the podcast transcripts."
    )
    prior = [
        {"role": "user", "content": "According to Lenny's Podcast transcripts, what was Elena Verna's exact personal income in March 2025?"},
        {"role": "assistant", "content": "I couldn't find enough support in Lenny's material to answer that reliably."},
    ]

    async def forbidden(*args):
        raise AssertionError("Conversation-memory recall must not retrieve transcript evidence")

    async def respond(provider, system, prompt):
        payload = json.loads(prompt)
        assert payload["history"] == prior
        assert "session-scoped conversation history" in system
        yield "I concluded that the transcripts did not establish the amount."

    monkeypatch.setattr("app.agent.search_knowledge_base", forbidden)
    monkeypatch.setattr("app.agent.generate", respond)
    events = [event async for event in run_agent(request, "ollama", "auto", prior)]
    result = events[-1][1]
    assert result["response_kind"] == "conversation"
    assert result["content"] == "I concluded that the transcripts did not establish the amount."
    assert result["sources"] == []
    assert events[0][1]["route"] == "conversation"
    assert events[0][1]["retrieval_requested"] is False


async def test_history_recall_cannot_leak_across_sessions(monkeypatch):
    request = "Earlier, what conclusion did you reach? Answer based on our conversation history, not the podcast transcripts."

    async def respond(provider, system, prompt):
        assert json.loads(prompt)["history"] == []
        yield "I don't have that earlier exchange in this conversation."

    monkeypatch.setattr("app.agent.generate", respond)
    events = [event async for event in run_agent(request, "ollama", "auto", [])]
    result = events[-1][1]
    assert result["response_kind"] == "conversation"
    assert result["sources"] == []
    assert not result["abstained"]


def test_history_recall_routing_is_explicit_and_transcript_questions_still_search():
    recall = "Earlier, what conclusion did you reach? Answer based on our conversation history, not the podcast transcripts."
    assert conversation_recall(recall)
    assert plan_turn(recall).intent == "conversation"
    assert not plan_turn(recall).search
    assert plan_turn("What does Lenny discuss about pricing in the podcast interviews?").search
    assert not conversation_recall("What does Lenny discuss about pricing in the podcast interviews?")
    assert plan_turn("What conclusion did we reach about Elena Verna's exact personal income in March 2025?").intent == "conversation"


def test_benchmark_memory_and_metadata_prompts_route_deterministically():
    token_recall = "What exact test token did I ask you to remember? Reply with the token only."
    cross_session = "What exact test token did I ask you to remember in my other conversation? Reply with the token only. If this session does not contain that information, say you don't know."
    metadata = "From the retrieved source metadata, report the episode title, guest, publication date, and transcript URL for the Elena Verna episode. Include the source ID if available."
    assert plan_turn(token_recall).intent == "conversation"
    assert not plan_turn(token_recall).search
    assert plan_turn(cross_session).intent == "conversation"
    assert not plan_turn(cross_session).search
    metadata_plan = plan_turn(metadata)
    assert metadata_plan.intent == "metadata" and metadata_plan.search


async def test_original_artifact_uses_only_relevant_session_context(monkeypatch):
    request = (
        "Create a concise Markdown growth experiment brief using the product and budget details I gave you. "
        "Include one hypothesis, one experiment, one success metric, and a budget line. "
        "Do not invent additional product details."
    )
    history = [{"role": "user", "content": "The product is a podcast app for early-career product managers. The budget is $500."}]

    async def forbidden(*args):
        raise AssertionError("Original writing from session context must not retrieve podcast evidence")

    async def draft(provider, system, prompt):
        assert history[0]["content"] in prompt
        assert "do not invent missing product, audience, budget, or performance facts" in system.lower()
        assert "do not invent product names" in system.lower()
        yield "# Growth Experiment Brief\n\nHypothesis: Early-career product managers will value concise podcast takeaways.\n\nExperiment: Test a short takeaway format.\n\nSuccess metric: Measure saves per listener.\n\nBudget: $500."

    monkeypatch.setattr("app.agent.search_knowledge_base", forbidden)
    monkeypatch.setattr("app.agent.generate", draft)
    events = [event async for event in run_agent(request, "ollama", "auto", history)]
    result = events[-1][1]
    assert result["response_kind"] == "writing" and result["artifact"] is not None


@pytest.mark.parametrize(
    "question",
    [
        "What lesson does S3 discuss? Quote a supporting passage verbatim.",
        "Give the title, author, and publication date of S3.",
    ],
)
async def test_saved_quote_and_metadata_are_deterministic(monkeypatch, question):
    async def forbidden(*args):
        raise AssertionError("Saved source inspection needs neither retrieval nor generation")

    monkeypatch.setattr("app.agent.search_knowledge_base", forbidden)
    monkeypatch.setattr("app.agent.generate", forbidden)
    history = [{"role": "assistant", "content": "A claim [S3]", "metadata": {"sources": [source()]}}]
    result = [event async for event in run_agent(question, "ollama", "auto", history)][-1][1]
    assert result["sources"] == [source()] and "A precise episode title" in result["content"]
    if "Quote" in question:
        assert source()["excerpt"] in result["content"]
    else:
        assert "Author: Unavailable" in result["content"] and "2025-03-23" in result["content"]
    assert result["response_kind"] == "source_lookup"


async def test_unknown_source_reference_abstains_without_guessing():
    result = [event async for event in run_agent("Quote S3 verbatim", "ollama", "auto", [])][-1][1]
    assert result["abstained"] and result["sources"] == [] and "S3" in result["content"]


async def test_source_followup_keeps_saved_identity(monkeypatch):
    async def forbidden(*args):
        raise AssertionError("Do not substitute a new search for an explicit saved source")

    async def respond(provider, system, prompt):
        assert source()["excerpt"] in prompt
        yield "The passage recommends regular customer interviews. [S3]"

    monkeypatch.setattr("app.agent.search_knowledge_base", forbidden)
    monkeypatch.setattr("app.agent.generate", respond)
    monkeypatch.setattr("app.agent.review_grounding", supported)
    result = [event async for event in run_agent("Explain the recommendation in S3", "ollama", "auto", [], [source()])][
        -1
    ][1]
    assert result["sources"] == [source()]
    other = {**source(), "chunk_id": "different", "citation": "S1"}
    assert stable_citations([other], [source()])[0]["citation"] == "S4"
    assert stable_citations([source()], [source()])[0]["citation"] == "S3"


async def test_new_topic_searches_without_old_topic_injection(monkeypatch):
    async def search(query):
        assert "pricing" in query and "onboarding" not in query
        return [], 1

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    history = [{"role": "user", "content": "How should I improve onboarding?"}]
    result = [event async for event in run_agent("What does Lenny discuss about pricing?", "ollama", "auto", history)][
        -1
    ][1]
    assert result["abstained"]


async def test_both_formats_create_real_distinct_safe_artifacts(monkeypatch):
    async def search(_):
        return [source("S1")], 1

    async def respond(*args):
        yield "# A useful brief\n\nInterview customers weekly. [S1]\n\n<script>alert('bad')</script>"

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", respond)
    monkeypatch.setattr("app.agent.review_grounding", supported)
    result = [
        event async for event in run_agent("Create Markdown and HTML about customer interviews.", "ollama", "auto", [])
    ][-1][1]
    markdown, html = result["artifacts"]
    assert markdown["type"] == "markdown" and html["type"] == "html"
    assert markdown["content"].startswith("# A useful brief") and html["content"].startswith("<!doctype html>")
    assert "<h1>A useful brief</h1>" in html["content"] and "<script>" not in html["content"]
    assert markdown["source_ids"] == html["source_ids"] and "[S1]" in html["content"]


async def test_writing_invokes_skill(monkeypatch):
    async def search(_):
        return [source("S1")], 1

    async def respond(provider, system, prompt):
        assert "Ship 30 writing process" in system and "1,250" in system
        yield "ABSTAIN"

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", respond)
    result = [event async for event in run_agent("Write a long-form essay about discovery.", "ollama", "auto", [])][-1][
        1
    ]
    assert result["abstained"]


def test_generic_metadata_conflicts_and_timestamps():
    body = "Maya Chen (00:01:00): Hello.\n\nMaya Chen (00:02:30): A useful explanation."
    metadata = {"guest": "Maya Chen", "title": "Design | James North", "publish_date": "2025-01-01"}
    check = metadata_integrity(metadata, body)
    assert check["metadata_status"] == "conflicting" and check["original_metadata"] == metadata
    assert metadata_integrity({"guest": "Someone Else", "title": "An interview"}, body)["metadata_warning"]
    assert not metadata_integrity({"guest": "Maya Chen", "title": "Design | Maya Chen"}, body)["metadata_warning"]
    assert passage_metadata(body) == {
        "speakers": ["Maya Chen"],
        "timestamp_start": "00:01:00",
        "timestamp_end": "00:02:30",
    }


def test_speaker_context_carries_over_chunk_boundary_without_rewriting_text():
    transcript = "Teresa Torres (14:17):\n\nCollect rich customer stories.\n\n(14:43):\nAnswers can miss context.\n\nLenny (15:01):\nWhat happens next?"
    chunks = ["Collect rich customer stories.", "Answers can miss context. Lenny (15:01): What happens next?"]
    metadata = contextual_passage_metadata(chunks, transcript)
    assert metadata[0]["opening_speaker"] == "Teresa Torres"
    assert metadata[1]["opening_speaker"] == "Teresa Torres"
    assert metadata[1]["speakers"] == ["Teresa Torres", "Lenny"]
    assert chunks[0] == "Collect rich customer stories."


def test_quotation_integrity_rejects_paraphrase_as_direct_quote():
    assert not quotation_problems("“Repeated contact helps the team discover unmet needs.” [S3]", [source()])
    assert not quotation_problems(
        "“Repeated contact helps the team discover unmet needs,” the speaker says. [S3]", [source()]
    )
    assert quotation_problems("“Repeated interviews always guarantee perfect product market fit.” [S3]", [source()])
    assert not quotation_problems(
        'Suggested interview question: "What happened the last time you tried this?"', [source()]
    )


async def test_claim_support_review_goes_beyond_valid_ids(monkeypatch):
    async def reviewer(provider, system, prompt):
        assert "entailed" in system and source()["excerpt"] in prompt
        yield json.dumps(
            {
                "verdict": "unsupported",
                "issues": [
                    {
                        "claim": "Interviews guarantee growth",
                        "citation": "S3",
                        "reason": "The excerpt makes no guarantee.",
                    }
                ],
            }
        )

    monkeypatch.setattr("app.grounding.generate", reviewer)
    review = await review_grounding(
        "Interviews guarantee growth. [S3]", [source()], "ollama", "What do interviews achieve?"
    )
    assert review["verdict"] == "unsupported" and review["issues"]


def test_proposed_questions_are_not_mistaken_for_source_quotations():
    question = '"Tell me about a time when you faced a challenge with a product you used"'
    proposed = "## Proposed Application\n\nTry open-ended prompts, such as " + question + "."
    assert not quotation_problems(proposed, [source()])
    assert not quotation_problems("**Proposed application:** Ask " + question + ".", [source()])
    assert quotation_problems("The guest's question is " + question + ".", [source()])
    assert quotation_problems("## Proposed Application\n\nThe guest says to ask " + question + ".", [source()])
    assert quotation_problems(
        '## Proposed Application\n\nUse the assertion "Interviewing guarantees that all customers will buy".',
        [source()],
    )


async def test_inconclusive_grounding_review_fails_closed(monkeypatch):
    async def invalid(*args):
        yield "This seems fine"

    monkeypatch.setattr("app.grounding.generate", invalid)
    assert (await review_grounding("Interview customers weekly. [S3]", [source()], "ollama", "How often?"))[
        "verdict"
    ] == "uncertain"


async def test_support_review_receives_only_cited_passages(monkeypatch):
    async def reviewer(provider, system, prompt):
        packet = json.loads(prompt)["evidence"]
        assert [item["citation"] for item in packet] == ["S3"]
        assert packet[0]["opening_speaker"] == "Maya Chen"
        assert "UNRELATED_TOPIC" not in prompt
        yield '{"verdict":"supported","issues":[]}'

    monkeypatch.setattr("app.grounding.generate", reviewer)
    evidence = [{**source(), "opening_speaker": "Maya Chen"}, {**source("S4"), "excerpt": "UNRELATED_TOPIC"}]
    assert (await review_grounding("Interview customers weekly. [S3]", evidence, "ollama", "How often?"))[
        "verdict"
    ] == "supported"


async def test_unsupported_claim_is_withheld_after_one_repair(monkeypatch):
    async def search(_):
        return [source("S1")], 1

    calls = []

    async def respond(*args):
        calls.append(1)
        yield "Interviews guarantee revenue growth. [S1]"

    async def reject(*args):
        return {"verdict": "unsupported", "issues": ["No revenue guarantee in the excerpt"], "method": "test_fixture"}

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", respond)
    monkeypatch.setattr("app.agent.review_grounding", reject)
    result = [event async for event in run_agent("What do interviews achieve?", "ollama", "auto", [])][-1][1]
    assert result["abstained"] and result["artifact"] is None and len(calls) == 2


def test_no_search_is_an_intent_constraint_not_a_literal_exception():
    assert not plan_turn("Please explain this supplied text without using the knowledge base.").search
    assert plan_turn("What does Sean Ellis say about retention?").search


def test_reference_routing_preserves_requested_formats():
    plan = plan_turn("Turn S3 into Markdown and HTML.")
    assert plan.intent == "writing" and plan.output == "both" and plan.references == ("S3",) and not plan.search
    assert plan_turn("Quote S3 and give its title and author").intent == "quote_metadata"
    assert plan_turn("Quote s3 and S3").references == ("S3",)


def test_artifact_retrieval_uses_topic_not_appended_style_instructions():
    request = "Write a 1,250-word essay about customer stories, grounded in Teresa Torres's interview. Clearly label proposed applications and preserve evidence limits. Create both Markdown and HTML artifacts."
    query = focus_query(request, ["Teresa Torres"])
    assert query == "customer stories"
    assert not needs_history(request)
    assert needs_history("Turn that into an essay.")
    assert "Compare interviewing with observation" in focus_query(
        "Write an essay about customer stories. Compare interviewing with observation. Create both Markdown and HTML artifacts.",
        [],
    )


async def test_standalone_writing_does_not_inherit_source_inspection_chatter(monkeypatch):
    async def search(_):
        return [source("S1")], 1

    async def generate(provider, system, prompt):
        assert "OLD_METADATA_CHATTER" not in prompt
        yield "ABSTAIN"

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", generate)
    history = [{"role": "assistant", "content": "OLD_METADATA_CHATTER"}]
    _ = [e async for e in run_agent("Write an essay about customer stories", "ollama", "auto", history)]


async def test_failed_grounding_draft_is_never_visible(monkeypatch):
    async def search(_):
        return [source("S1")], 1

    async def generate(*args):
        yield "Customer interviews guarantee revenue growth. [S1]"

    async def reject(*args):
        return {"verdict": "unsupported", "issues": ["No guarantee"], "method": "fixture"}

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", generate)
    monkeypatch.setattr("app.agent.review_grounding", reject)
    events = [event async for event in run_agent("What do interviews achieve?", "ollama", "auto", [])]
    assert not any(event == "delta" for event, _ in events)
    assert events[-1][1]["abstained"]


async def test_grounded_one_sentence_constraint_is_repaired(monkeypatch):
    responses = iter(["Collect stories. Interview customers. [S3]", "Interview customers to collect stories. [S3]"])

    async def generate(*args):
        yield next(responses)

    monkeypatch.setattr("app.agent.generate", generate)
    monkeypatch.setattr("app.agent.review_grounding", supported)
    events = [event async for event in run_agent("Explain S3 in one sentence.", "ollama", "auto", [], [source()])]
    assert events[-1][1]["content"] == "Interview customers to collect stories. [S3]"


async def test_short_essay_with_invalid_quotes_gets_full_repair_not_expansion(monkeypatch):
    calls = []

    async def search(_):
        return [source("S1")], 1

    async def generate(provider, system, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            yield "# A title\n\n## A heading\n\n“Interviews guarantee revenue for every team.” [S1]"
        else:
            assert "complete revised result" in prompt and "quotation is not an exact excerpt" in prompt
            assert "ONLY one additional" not in prompt
            yield "ABSTAIN"

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", generate)
    events = [event async for event in run_agent("Write a Ship 30 essay about interviews", "ollama", "auto", [])]
    assert events[-1][1]["abstained"] and len(calls) == 2
