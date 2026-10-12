"""Opt in: TEST_DATABASE_URL must point to a disposable, migrated PostgreSQL database.

Every test uses a fresh schema. No existing application records are deleted.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import connection_options
from app.main import app
from app.models import Base, Chunk, Source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="Set TEST_DATABASE_URL to a test PostgreSQL instance"
    ),
]


@pytest_asyncio.fixture
async def store(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    admin = create_async_engine(url, connect_args=connection_options())
    schema = "test_" + uuid.uuid4().hex
    async with admin.begin() as c:
        await c.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_async_engine(
        url,
        connect_args=connection_options(),
        execution_options={"schema_translate_map": {None: schema}},
    )

    @event.listens_for(engine.sync_engine, "begin")
    def use_schema(connection):
        connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}, public")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)

    async def dependency():
        async with factory() as db:
            yield db

    from app.db import database

    app.dependency_overrides[database] = dependency
    monkeypatch.setattr("app.main.SessionLocal", factory)
    monkeypatch.setattr("app.main.engine", engine)
    monkeypatch.setattr("app.retrieval.SessionLocal", factory)
    monkeypatch.setattr("app.ingest.SessionLocal", factory)
    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await admin.dispose()


async def seed(store):
    async with store() as db:
        source = Source(
            path="episodes/test/transcript.md",
            source_url="https://example.org/test",
            repository="https://example.org/repo",
            revision="a" * 40,
            episode_title="Product-market fit",
            guest="Fixture guest",
            content_hash="b" * 64,
            details={},
        )
        db.add(source)
        await db.flush()
        chunk = Chunk(
            source_id=source.id,
            chunk_index=0,
            content="Product market fit comes from narrowing your customer segment. Survey customers to learn who depends on your product.",
            embedding=[1.0] + [0.0] * 383,
            content_hash="c" * 64,
            details={},
        )
        db.add(chunk)
        await db.flush()
        await db.execute(text("UPDATE chunks SET search_vector=to_tsvector('english', content)"))
        await db.commit()
        return source.id


async def test_persistent_session_isolation_and_artifact(store, monkeypatch):
    seen = []

    async def fake_agent(message, provider, mode, history, **kwargs):
        seen.append(history)
        yield "status", {"message": "Preparing grounded answer"}
        yield "delta", {"text": "A grounded answer [S1]"}
        yield (
            "result",
            {
                "content": "A grounded answer [S1]",
                "sources": [],
                "abstained": False,
                "artifact": {
                    "type": "markdown",
                    "title": "Fixture artifact",
                    "content": "# Title\nFixture content",
                    "word_count": 3,
                    "source_ids": [],
                    "evidence": [],
                }
                if mode == "markdown"
                else None,
            },
        )

    monkeypatch.setattr("app.main.run_agent", fake_agent)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        one = (await client.post("/api/sessions", json={"user_metadata": {"display_name": "Evaluator"}})).json()["id"]
        two = (await client.post("/api/sessions", json={})).json()["id"]
        for session_id, question, mode in [
            (one, "Session one confidential context", "answer"),
            (two, "Session two question", "answer"),
            (one, "Turn this into a document", "markdown"),
        ]:
            response = await client.post(
                "/api/chat/stream",
                json={"session_id": session_id, "message": question, "provider": "ollama", "mode": mode},
            )
            assert "event: done" in response.text, response.text
        assert seen[1] == []
        assert any(m["content"] == "Session one confidential context" for m in seen[2])
        assert not any("Session two question" in m["content"] for m in seen[2])
        detail = (await client.get(f"/api/sessions/{one}")).json()
        assert len(detail["messages"]) == 4 and len(detail["artifacts"]) == 1
        artifact_id = detail["artifacts"][0]["id"]
        assert (await client.get(f"/api/artifacts/{artifact_id}")).json()["title"] == "Fixture artifact"
        assert (await client.delete(f"/api/sessions/{one}")).status_code == 204
        assert (await client.get(f"/api/artifacts/{artifact_id}")).status_code == 404
        assert len((await client.get(f"/api/sessions/{two}/messages")).json()) == 2


async def test_conversation_memory_survives_session_reload_and_stays_isolated(store, monkeypatch):
    first_question = "According to Lenny's Podcast transcripts, what was Elena Verna's exact personal income in March 2025?"
    recall_question = "What conclusion did we reach about Elena Verna's exact personal income in March 2025?"
    generated_histories = []
    search_calls = 0

    async def search(_):
        nonlocal search_calls
        search_calls += 1
        # The first turn is an unsupported knowledge question. A later recall
        # must not call retrieval at all.
        return [], 1

    async def generate(provider, system, prompt):
        payload = json.loads(prompt)
        generated_histories.append(payload["history"])
        yield "The earlier conclusion is available in this session history."

    monkeypatch.setattr("app.agent.search_knowledge_base", search)
    monkeypatch.setattr("app.agent.generate", generate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.post("/api/sessions", json={})).json()["id"]
        second = (await client.post("/api/sessions", json={})).json()["id"]

        first_response = await client.post(
            "/api/chat/stream",
            json={"session_id": first, "message": first_question, "provider": "ollama"},
        )
        assert '"response_kind": "source_lookup"' in first_response.text
        persisted = (await client.get(f"/api/sessions/{first}")).json()
        assert any(message["content"] == first_question for message in persisted["messages"])

        # This request represents a browser reload: the next request uses the
        # same persisted session ID, and the API reconstructs history from SQL.
        recalled = await client.post(
            "/api/chat/stream",
            json={"session_id": first, "message": recall_question, "provider": "ollama"},
        )
        # Route intent is internal metrics; the public SSE message contract uses response_kind.
        assert '"response_kind": "conversation"' in recalled.text
        assert generated_histories[-1] and first_question in json.dumps(generated_histories[-1])

        isolated = await client.post(
            "/api/chat/stream",
            json={"session_id": second, "message": recall_question, "provider": "ollama"},
        )
        assert '"response_kind": "conversation"' in isolated.text
        assert generated_histories[-1] == []
        assert search_calls == 1


async def test_pgvector_fts_retrieval_and_provenance(store, monkeypatch):
    source_id = await seed(store)

    async def embedding(_):
        return [[1.0] + [0.0] * 383]

    monkeypatch.setattr("app.retrieval.embed", embedding)
    from app.retrieval import search_knowledge_base

    evidence, latency = await search_knowledge_base("product market fit")
    assert len(evidence) == 1 and evidence[0]["source_id"] == str(source_id)
    assert evidence[0]["revision"] == "a" * 40 and evidence[0]["path"] == "episodes/test/transcript.md"
    assert evidence[0]["score"] > 0 and latency > 0

    async def unrelated(_):
        return [[0.0, 1.0] + [0.0] * 382]

    monkeypatch.setattr("app.retrieval.embed", unrelated)
    assert (await search_knowledge_base("quantum physics"))[0] == []


async def test_failed_turn_is_persisted_but_excluded_from_followup(store, monkeypatch):
    from app.agent import bounded_history
    from app.errors import AppError

    async def fail(*args, **kwargs):
        raise AppError("provider_error", "Fixture provider failure")
        yield  # async generator contract

    monkeypatch.setattr("app.main.run_agent", fail)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session = (await client.post("/api/sessions", json={})).json()["id"]
        response = await client.post(
            "/api/chat/stream", json={"session_id": session, "message": "Unfinished question", "provider": "ollama"}
        )
        assert "event: error" in response.text and "Fixture provider failure" in response.text
        history = (await client.get(f"/api/sessions/{session}/messages")).json()
        assert len(history) == 2 and all(m["metadata"]["status"] == "failed" for m in history)
        assert bounded_history(history) == []


async def test_named_guest_retrieval_excludes_other_interviews(store, monkeypatch):
    source_id = await seed(store)
    async with store() as db:
        other = Source(
            path="episodes/other/transcript.md",
            source_url="https://example.org/other",
            repository="https://example.org/repo",
            revision="a" * 40,
            episode_title="Another interview",
            guest="Other guest",
            content_hash="d" * 64,
        )
        db.add(other)
        await db.flush()
        other_id = other.id
        db.add(
            Chunk(
                source_id=other_id,
                chunk_index=0,
                content="Product market fit advice.",
                embedding=[1.0] + [0.0] * 383,
                content_hash="e" * 64,
            )
        )
        await db.commit()

    async def embedding(_):
        return [[1.0] + [0.0] * 383]

    monkeypatch.setattr("app.retrieval.embed", embedding)
    from app.retrieval import search_knowledge_base

    for guest, expected in [("Fixture guest", source_id), ("Other guest", other_id)]:
        results, _ = await search_knowledge_base(f"What does {guest} say about product market fit?")
        assert results and {row["source_id"] for row in results} == {str(expected)}


async def test_lexical_candidate_outside_semantic_top_40_remains_eligible(store, monkeypatch):
    source_id = await seed(store)
    async with store() as db:
        for i in range(1, 42):
            db.add(
                Chunk(
                    source_id=source_id,
                    chunk_index=i,
                    content="General product strategy.",
                    embedding=[1.0] + [0.0] * 383,
                    content_hash=str(i),
                )
            )
        db.add(
            Chunk(
                source_id=source_id,
                chunk_index=42,
                content="Onboarding needs thoughtful attention.",
                embedding=[0.6, 0.8] + [0.0] * 382,
                content_hash="target",
            )
        )
        await db.flush()
        await db.execute(text("UPDATE chunks SET search_vector=to_tsvector('english', content)"))
        await db.commit()

    async def embedding(_):
        return [[1.0] + [0.0] * 383]

    monkeypatch.setattr("app.retrieval.embed", embedding)
    from app.retrieval import search_knowledge_base

    results, _ = await search_knowledge_base("onboarding")
    assert any(row["chunk_index"] == 42 and row["similarity"] > 0.5 for row in results)


async def test_ingestion_is_idempotent_and_refreshes_atomically(store, monkeypatch, tmp_path):
    from app.ingest import ingest

    file = tmp_path / "episodes" / "test" / "transcript.md"
    file.parent.mkdir(parents=True)
    file.write_text(
        "---\nguest: Test\ntitle: Test episode\n---\n## Transcript\n"
        + "Product market fit matters to customers. " * 50,
        encoding="utf-8",
    )
    monkeypatch.setattr("app.ingest.subprocess.check_output", lambda *a, **kw: "a" * 40)
    calls = []

    async def embedding(texts):
        calls.append(len(texts))
        return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr("app.ingest.embed", embedding)
    assert (await ingest(tmp_path))["updated"] == 1
    assert (await ingest(tmp_path))["unchanged"] == 1
    assert len(calls) == 1
    with file.open("a", encoding="utf-8") as f:
        f.write("\n\n" + "New grounded material about prioritization. " * 30)
    assert (await ingest(tmp_path))["updated"] == 1
    async with store() as db:
        assert await db.scalar(select(func.count()).select_from(Source)) == 1
        assert await db.scalar(select(func.count()).select_from(Chunk)) > 1


async def test_native_postgres_rejects_overlapping_session_turns(store, monkeypatch):
    async with store() as probe:
        version = await probe.scalar(text("SELECT version()"))
        if "wasm" in version.lower():
            pytest.skip("Requires independent native PostgreSQL backends; PGlite multiplexes one")
    async with store() as one, store() as two:
        first_pid = await one.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await two.scalar(text("SELECT pg_backend_pid()"))
        if first_pid == second_pid:
            pytest.skip("Requires independent native PostgreSQL backends; PGlite multiplexes one")
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting_agent(*args, **kwargs):
        entered.set()
        await release.wait()
        yield "result", {"content": "Fixture", "sources": [], "artifact": None, "abstained": True}

    monkeypatch.setattr("app.main.run_agent", waiting_agent)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session = (await client.post("/api/sessions", json={})).json()["id"]
        body = {"session_id": session, "message": "Concurrent question", "provider": "ollama"}
        first = asyncio.create_task(client.post("/api/chat/stream", json=body))
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            second = await client.post("/api/chat/stream", json=body)
            assert '"code": "session_busy"' in second.text
            assert (await client.delete(f"/api/sessions/{session}")).status_code == 409
        finally:
            release.set()
            await first


async def test_two_artifacts_and_saved_evidence_survive_reload_in_correct_session(store, monkeypatch):
    from app.evidence import inspect_source

    evidence = {
        "citation": "S3",
        "chunk_id": "chunk-a",
        "source_id": "source-a",
        "revision": "abc",
        "episode_title": "Preserved title",
        "excerpt": "The exact preserved passage.",
    }
    seen = []

    async def agent(message, provider, mode, history, evidence_registry):
        seen.append(evidence_registry)
        if mode == "both":
            payloads = [
                {
                    "type": kind,
                    "title": "Two formats",
                    "content": content,
                    "word_count": 4,
                    "source_ids": ["source-a"],
                    "evidence": [evidence],
                }
                for kind, content in [
                    ("markdown", "# Two formats\nA claim [S3]"),
                    ("html", "<!doctype html><html><body><h1>Two formats</h1><p>A claim [S3]</p></body></html>"),
                ]
            ]
            yield (
                "result",
                {"content": "Two files created", "sources": [evidence], "abstained": False, "artifacts": payloads},
            )
        else:
            yield (
                "result",
                {
                    "content": inspect_source(evidence_registry[0]) if evidence_registry else "No saved S3",
                    "sources": evidence_registry,
                    "abstained": not evidence_registry,
                    "response_kind": "source_lookup",
                },
            )

    monkeypatch.setattr("app.main.run_agent", agent)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.post("/api/sessions", json={})).json()["id"]
        second = (await client.post("/api/sessions", json={})).json()["id"]
        for sid, mode, provider in [
            (first, "both", "ollama"),
            (second, "answer", "gemini"),
            (first, "answer", "ollama"),
        ]:
            response = await client.post(
                "/api/chat/stream",
                json={"session_id": sid, "message": "Inspect S3", "mode": mode, "provider": provider},
            )
            assert "event: done" in response.text
        assert seen == [[], [], [evidence]]
        detail = (await client.get(f"/api/sessions/{first}")).json()
        artifacts = detail["artifacts"]
        assert len(artifacts) == 2 and len({a["id"] for a in artifacts}) == 2
        assert {a["type"] for a in artifacts} == {"html", "markdown"}
        assert len({a["message_id"] for a in artifacts}) == 1
        assert set(detail["messages"][1]["metadata"]["artifact_ids"]) == {a["id"] for a in artifacts}
        assert detail["messages"][-1]["model"] is None and detail["messages"][-1]["provider"] is None
        for artifact in artifacts:
            assert (await client.get(f"/api/artifacts/{artifact['id']}")).json()["content"] == artifact["content"]


async def test_metadata_upgrade_preserves_ids_and_vectors(store, monkeypatch, tmp_path):
    from app.ingest import ingest

    file = tmp_path / "episodes" / "test" / "transcript.md"
    file.parent.mkdir(parents=True)
    file.write_text(
        "---\nguest: Maya Chen\ntitle: Product | James North\npublish_date: 2024-01-01\n---\n## Transcript\n"
        + "Maya Chen (00:01:00): Customers need a clear reason to use a product.\n\n" * 40,
        encoding="utf-8",
    )
    monkeypatch.setattr("app.ingest.subprocess.check_output", lambda *a, **kw: "a" * 40)

    async def embed(texts):
        return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr("app.ingest.embed", embed)
    await ingest(tmp_path)
    async with store() as db:
        source = await db.scalar(select(Source))
        source_id = source.id
        source.details = {**source.details, "metadata_version": 0}
        before = set((await db.scalars(select(Chunk.id))).all())
        await db.commit()

    async def forbidden(*args):
        raise AssertionError("Metadata updates must not re-embed unchanged chunks")

    monkeypatch.setattr("app.ingest.embed", forbidden)
    assert (await ingest(tmp_path))["updated"] == 1
    assert (await ingest(tmp_path))["unchanged"] == 1
    async with store() as db:
        source = await db.scalar(select(Source))
        assert source.id == source_id and source.episode_title == "Product | James North"
        assert source.publish_date is None and source.details["original_metadata"]["publish_date"] == "2024-01-01"
        assert source.details["metadata_status"] == "conflicting"
        assert set((await db.scalars(select(Chunk.id))).all()) == before


async def test_demo_manifest_ingests_only_declared_licensed_source(store, monkeypatch, tmp_path):
    from app.ingest import ingest

    source_root = tmp_path / "public-transcripts"
    source_file = source_root / "founderstalk" / "demo.md"
    source_file.parent.mkdir(parents=True)
    source_text = (
        "Synthetic fixture passage about a founder reviewing customer feedback and making a product decision. "
        * 12
    )
    source_file.write_text(source_text, encoding="utf-8")
    unrelated = source_root / "episodes" / "not-in-demo" / "transcript.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("This deliberately invalid file must not be read.", encoding="utf-8")

    manifest_path = tmp_path / "demo-sources.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "https://github.com/example/public-transcripts",
                "revision": "public-demo-ref",
                "license": {
                    "spdx_id": "CC-BY-SA-4.0",
                    "url": "https://creativecommons.org/licenses/by-sa/4.0/",
                    "attribution": "Example rights holder",
                },
                "sources": [
                    {
                        "path": "founderstalk/demo.md",
                        "guest": "Synthetic Guest",
                        "episode_title": "Synthetic Product Discussion",
                        "publish_date": "2025-02-03",
                        "episode_url": "https://example.org/episodes/demo",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.ingest.settings",
        lambda: SimpleNamespace(demo_sources_file=str(manifest_path), embedding_model="fixture-embedding"),
    )
    monkeypatch.setattr("app.ingest.subprocess.check_output", lambda *args, **kwargs: "a" * 40)

    async def embed(texts):
        return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr("app.ingest.embed", embed)
    result = await ingest(source_root, profile="demo")
    assert result["updated"] == 1 and result["chunks"] > 0
    async with store() as db:
        sources = (await db.scalars(select(Source))).all()
        assert len(sources) == 1
        source = sources[0]
        assert source.path == "founderstalk/demo.md"
        assert source.repository == "https://github.com/example/public-transcripts"
        assert source.source_url == (
            "https://github.com/example/public-transcripts/blob/" + "a" * 40 + "/founderstalk/demo.md"
        )
        assert source.guest == "Synthetic Guest" and source.episode_title == "Synthetic Product Discussion"
        assert source.details["source_license"] == "CC-BY-SA-4.0"
        assert source.details["license_url"] == "https://creativecommons.org/licenses/by-sa/4.0/"
        assert source.details["attribution"] == "Example rights holder"
        assert source.details["episode_url"] == "https://example.org/episodes/demo"
        chunks = (await db.scalars(select(Chunk).where(Chunk.source_id == source.id))).all()
        assert chunks and all("Synthetic fixture passage" in chunk.content for chunk in chunks)


async def test_removed_provider_is_rejected_and_ollama_preserves_session(store, monkeypatch):
    async def direct(*args):
        yield "Hello again."

    monkeypatch.setattr("app.agent.generate", direct)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid = (await client.post("/api/sessions", json={})).json()["id"]
        invalid = await client.post("/api/chat/stream", json={"session_id": sid, "message": "Hello", "provider": "retired-provider"})
        assert invalid.status_code == 422
        ok = await client.post("/api/chat/stream", json={"session_id": sid, "message": "Hello", "provider": "ollama"})
        assert "event: done" in ok.text
        messages = (await client.get(f"/api/sessions/{sid}/messages")).json()
        assert len(messages) == 2
        assert messages[-1]["provider"] == "ollama" and messages[-1]["content"] == "Hello again."


async def test_retrieval_database_error_has_actionable_stream_failure(store, monkeypatch):
    from sqlalchemy.exc import OperationalError

    async def unavailable(*args):
        raise OperationalError("private statement", {}, Exception("private connection string"))

    monkeypatch.setattr("app.agent.search_knowledge_base", unavailable)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid = (await client.post("/api/sessions", json={})).json()["id"]
        response = await client.post(
            "/api/chat/stream",
            json={"session_id": sid, "message": "What does Lenny say about retention?", "provider": "ollama"},
        )
        assert "database_unavailable" in response.text and "Check PostgreSQL" in response.text
        assert "private" not in response.text and "event: done" not in response.text
        history = (await client.get(f"/api/sessions/{sid}/messages")).json()
        assert history[-1]["metadata"]["status"] == "failed"


async def test_stream_connection_acquisition_failure_is_reported(store, monkeypatch):
    from contextlib import asynccontextmanager

    from sqlalchemy.exc import OperationalError

    class OfflineEngine:
        @asynccontextmanager
        async def connect(self):
            raise OperationalError("private", {}, Exception("private"))
            yield

    monkeypatch.setattr("app.main.engine", OfflineEngine())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid = (await client.post("/api/sessions", json={})).json()["id"]
        response = await client.post(
            "/api/chat/stream", json={"session_id": sid, "message": "Hello", "provider": "ollama"}
        )
        assert "database_unavailable" in response.text and "private" not in response.text


async def test_real_agent_persists_original_artifact_pair_and_history(store, monkeypatch):
    """Real API/agent/validation/database; only inference is a deterministic fixture."""
    content = "# Research checklist\n\n- [ ] Identify the user problem.\n- [ ] Test your hypothesis."
    seen = []

    async def generate(provider, system, prompt):
        seen.append(prompt)
        yield content if "OUTPUT REQUIREMENTS" in prompt else "You asked for a research checklist."

    async def forbidden(*args):
        raise AssertionError("Original artifacts and no-search follow-ups need no RAG")

    monkeypatch.setattr("app.agent.generate", generate)
    monkeypatch.setattr("app.agent.search_knowledge_base", forbidden)
    monkeypatch.setattr("app.agent.review_grounding", forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.post("/api/sessions", json={})).json()["id"]
        second = (await client.post("/api/sessions", json={})).json()["id"]
        question = "Create Markdown and HTML artifacts for a research checklist. Do not search the knowledge base."
        response = await client.post("/api/chat/stream", json={"session_id": first, "message": question})
        assert "event: done" in response.text and response.text.count("event: artifact") == 2
        detail = (await client.get(f"/api/sessions/{first}")).json()
        assert detail["messages"][1]["metadata"]["sources"] == []
        assert len(detail["artifacts"]) == 2
        assert {a["type"] for a in detail["artifacts"]} == {"markdown", "html"}
        assert len({a["id"] for a in detail["artifacts"]}) == 2
        for artifact in detail["artifacts"]:
            assert (await client.get(f"/api/artifacts/{artifact['id']}")).json()["content"] == artifact["content"]
        for sid in [first, second]:
            followup = await client.post("/api/chat/stream", json={
                "session_id": sid, "message": "What did I just ask? Do not search the knowledge base.",
            })
            assert "event: done" in followup.text
        assert question in json.dumps(json.loads(seen[1])["history"])
        assert json.loads(seen[2])["history"] == []


async def test_retrieved_evidence_is_identical_in_review_sse_and_saved_message(store, monkeypatch):
    """Real SQL retrieval and serialization; embeddings and model verdict are fixtures."""
    fixture = json.loads((Path(__file__).parent / "fixtures/elena-evidence.json").read_text(encoding="utf-8"))
    async with store() as db:
        source = Source(
            path=fixture["path"], source_url=fixture["source_url"], repository=fixture["repository"],
            revision=fixture["revision"], episode_title=fixture["episode_title"], guest=fixture["guest"],
            publish_date=fixture["publish_date"], content_hash="a" * 64, details={},
        )
        db.add(source)
        await db.flush()
        db.add(Chunk(
            source_id=source.id, chunk_index=0, content=fixture["excerpt"],
            embedding=[1.0] + [0.0] * 383, content_hash="b" * 64,
            details={"opening_speaker": fixture["opening_speaker"], "speakers": fixture["speakers"]},
        ))
        await db.flush()
        await db.execute(text("UPDATE chunks SET search_vector=to_tsvector('english', content)"))
        await db.commit()

    reviewed = []

    async def embed(texts):
        return [[1.0] + [0.0] * 383 for _ in texts]

    async def generate(provider, system, prompt):
        assert fixture["excerpt"] in prompt and "[S1]" in prompt
        yield "The team used which onboarding label volunteers understood more quickly to select its next small usability test. [S1]"

    async def review(content, evidence, provider, question):
        reviewed.extend(evidence)
        return {"verdict": "supported", "issues": [], "method": "fixture"}

    monkeypatch.setattr("app.retrieval.embed", embed)
    monkeypatch.setattr("app.agent.generate", generate)
    monkeypatch.setattr("app.agent.review_grounding", review)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        sid = (await client.post("/api/sessions", json={})).json()["id"]
        response = await client.post("/api/chat/stream", json={
            "session_id": sid, "message": "How did the fictional team choose its next usability test?",
        })
        assert "event: done" in response.text
        events = {block.splitlines()[0][7:]: json.loads(block.splitlines()[1][6:])
                  for block in response.text.strip().split("\n\n")}
        detail = (await client.get(f"/api/sessions/{sid}")).json()
        assert reviewed == events["sources"]["sources"] == events["done"]["message"]["metadata"]["sources"]
        assert reviewed == detail["messages"][-1]["metadata"]["sources"]
        assert reviewed[0]["excerpt"] == fixture["excerpt"] and reviewed[0]["citation"] == "S1"
