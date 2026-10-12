import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import run_agent
from app.config import settings
from app.db import SessionLocal, database, engine
from app.errors import AppError
from app.evidence import saved_evidence
from app.models import Artifact, Chunk, Conversation, Message, Source, User
from app.providers import model_name, provider_status
from app.schemas import ChatRequest, SessionCreate

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("lenny")
# SDK/HTTP debug logs are deliberately disabled; they may contain model input.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app):
    yield
    await engine.dispose()


app = FastAPI(title="The Lenny Growth Assistant", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().cors_origins.split(","),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Request-ID"],
)


def error_response(code, message, status, request_id=None):
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message, "request_id": request_id}}
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(AppError)
async def app_error(request, exc):
    return error_response(exc.code, exc.message, exc.status, request.state.request_id)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return error_response("invalid_request", "Check the request fields and try again.", 422, request.state.request_id)


@app.exception_handler(SQLAlchemyError)
async def database_error(request, exc):
    log.error(
        json.dumps({"event": "request_failed", "request_id": request.state.request_id, "error_category": "database"})
    )
    return error_response(
        "database_unavailable",
        "The conversation store is unavailable. Check PostgreSQL and retry.",
        503,
        request.state.request_id,
    )


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    log.error(
        json.dumps(
            {"event": "request_failed", "request_id": request.state.request_id, "error_category": type(exc).__name__}
        )
    )
    return error_response(
        "service_unavailable",
        "A required service is unavailable. Check workspace status and retry.",
        503,
        request.state.request_id,
    )


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/readyz")
async def ready():
    checks = {"database": False, "vector": False, "corpus": False}
    counts = {"sources": 0, "chunks": 0}
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
            checks["database"] = True
            checks["vector"] = bool(await db.scalar(text("SELECT 1 FROM pg_extension WHERE extname='vector'")))
            counts = {
                "sources": await db.scalar(select(func.count()).select_from(Source)),
                "chunks": await db.scalar(select(func.count()).select_from(Chunk)),
            }
            checks["corpus"] = counts["chunks"] > 0
    except Exception:
        pass
    providers = await provider_status()
    ok = all(checks.values()) and any(p["available"] for p in providers)
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ready" if ok else "degraded",
            "checks": checks,
            "library": counts,
            "providers": providers,
            "default_provider": settings().default_provider,
            "guidance": "Run migrations and ingestion, then start Ollama or configure Gemini." if not ok else None,
        },
    )


@app.get("/api/providers")
async def providers():
    return {"default": settings().default_provider, "providers": await provider_status()}


def session_json(s):
    return {
        "id": str(s.id),
        "title": s.title,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def message_json(m):
    return {
        "id": str(m.id),
        "role": m.role,
        "content": m.content,
        "provider": m.provider,
        "model": m.model,
        "metadata": m.details,
        "created_at": m.created_at.isoformat(),
    }


def artifact_json(a):
    return {
        "id": str(a.id),
        "session_id": str(a.session_id),
        "message_id": str(a.message_id),
        "type": a.type,
        "title": a.title,
        "content": a.content,
        "source_ids": a.source_ids,
        "evidence": a.evidence,
        "word_count": a.word_count,
        "created_at": a.created_at.isoformat(),
    }


async def get_session(db, session_id):
    session = await db.get(Conversation, session_id)
    if not session:
        raise AppError("session_not_found", "This conversation no longer exists. Start a new chat.", 404)
    return session


@app.post("/api/sessions", status_code=201)
async def create_session(body: SessionCreate, db: AsyncSession = Depends(database)):
    user = User(details={"kind": "local_anonymous", **body.user_metadata})
    db.add(user)
    await db.flush()
    session = Conversation(title=body.title, user_id=user.id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session_json(session)


@app.get("/api/sessions")
async def sessions(db: AsyncSession = Depends(database)):
    return [
        session_json(s)
        for s in (await db.scalars(select(Conversation).order_by(Conversation.updated_at.desc()).limit(100))).all()
    ]


@app.get("/api/sessions/{session_id}")
async def session_detail(session_id: uuid.UUID, db: AsyncSession = Depends(database)):
    session = await get_session(db, session_id)
    messages = (
        await db.scalars(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at, Message.id)
        )
    ).all()
    artifacts = (
        await db.scalars(select(Artifact).where(Artifact.session_id == session_id).order_by(Artifact.created_at))
    ).all()
    return {
        **session_json(session),
        "messages": [message_json(m) for m in messages],
        "artifacts": [artifact_json(a) for a in artifacts],
    }


@app.get("/api/sessions/{session_id}/messages")
async def messages(session_id: uuid.UUID, db: AsyncSession = Depends(database)):
    return (await session_detail(session_id, db))["messages"]


@app.delete("/api/sessions/{session_id}", status_code=204)
async def remove_session(session_id: uuid.UUID, db: AsyncSession = Depends(database)):
    session = await get_session(db, session_id)
    # A transaction-scoped advisory lock conflicts with the stream's session lock.
    if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key(session_id)}):
        raise AppError("session_busy", "Stop the current response before deleting this conversation.", 409)
    await db.execute(delete(Conversation).where(Conversation.id == session_id))
    await db.execute(delete(User).where(User.id == session.user_id))
    await db.commit()


@app.get("/api/artifacts/{artifact_id}")
async def artifact_detail(artifact_id: uuid.UUID, db: AsyncSession = Depends(database)):
    artifact = await db.get(Artifact, artifact_id)
    if not artifact:
        raise AppError("artifact_not_found", "This artifact no longer exists.", 404)
    return artifact_json(artifact)


@app.get("/api/sources/{source_id}")
async def source_detail(source_id: uuid.UUID, db: AsyncSession = Depends(database)):
    source = await db.get(Source, source_id)
    if not source:
        raise AppError("source_not_found", "This source is no longer indexed.", 404)
    return {
        "id": str(source.id),
        "title": source.episode_title,
        "guest": source.guest,
        "source_url": source.source_url,
        "repository": source.repository,
        "revision": source.revision,
        "path": source.path,
        "publish_date": source.publish_date,
        "description": source.description,
        "content_hash": source.content_hash,
        "ingested_at": source.ingested_at.isoformat(),
        "metadata": source.details,
    }


def lock_key(session_id):
    return int.from_bytes(session_id.bytes[:8], "big", signed=True)


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
async def chat(body: ChatRequest, request: Request, db: AsyncSession = Depends(database)):
    await get_session(db, body.session_id)
    await db.rollback()  # Release the read transaction before starting a long response.

    async def events():
        started = time.perf_counter()
        metrics = {
            "event": "chat_completed",
            "request_id": request.state.request_id,
            "session_id": str(body.session_id),
            "provider": body.provider,
            "model": model_name(body.provider),
            "retrieval_count": 0,
            "retrieval_latency_ms": 0,
            "generation_latency_ms": 0,
            "artifact_created": False,
            "error_category": None,
        }
        user_saved = False
        async with engine.connect() as connection:
            locked = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key(body.session_id)}
            )
            await connection.commit()  # Session advisory locks survive a commit.
            if not locked:
                yield sse(
                    "error", {"code": "session_busy", "message": "A response is already running in this conversation."}
                )
                return
            try:
                async with SessionLocal() as store:
                    session = await get_session(store, body.session_id)
                    history = (
                        await store.scalars(
                            select(Message)
                            .where(Message.session_id == body.session_id)
                            .order_by(Message.created_at.desc(), Message.id.desc())
                            .limit(settings().context_messages)
                        )
                    ).all()
                    history = [message_json(m) for m in reversed(history)]
                    source_metadata = (
                        await store.scalars(
                            select(Message.details)
                            .where(Message.session_id == body.session_id, Message.role == "assistant")
                            .order_by(Message.created_at, Message.id)
                        )
                    ).all()
                    registry = saved_evidence([{"metadata": details} for details in source_metadata])
                    user = Message(
                        session_id=body.session_id,
                        role="user",
                        content=body.message,
                        provider=body.provider,
                        model=model_name(body.provider),
                        details={"mode": body.mode},
                    )
                    store.add(user)
                    if session.title == "New conversation":
                        session.title = body.message[:80]
                    session.updated_at = datetime.now(timezone.utc)
                    await store.commit()
                    user_saved = True
                    yield sse("accepted", {"message_id": str(user.id), "session": session_json(session)})
                async with asyncio.timeout(settings().model_timeout_seconds * 4 + 90):
                    async for event, data in run_agent(
                        body.message, body.provider, body.mode, history, evidence_registry=registry
                    ):
                        if await request.is_disconnected():
                            raise asyncio.CancelledError()
                        if event == "metrics":
                            metrics.update(data)
                        elif event == "result":
                            async with SessionLocal() as store:
                                assistant = Message(
                                    session_id=body.session_id,
                                    role="assistant",
                                    content=data["content"],
                                    provider=None if data.get("response_kind") == "source_lookup" else body.provider,
                                    model=None
                                    if data.get("response_kind") == "source_lookup"
                                    else model_name(body.provider),
                                    details={
                                        "status": "complete",
                                        "sources": data["sources"],
                                        "abstained": data["abstained"],
                                        "response_kind": data.get("response_kind", "knowledge"),
                                        "grounding": data.get("grounding"),
                                    },
                                )
                                store.add(assistant)
                                await store.flush()
                                artifacts = []
                                for payload in data.get(
                                    "artifacts", [data["artifact"]] if data.get("artifact") else []
                                ):
                                    artifact = Artifact(session_id=body.session_id, message_id=assistant.id, **payload)
                                    store.add(artifact)
                                    await store.flush()
                                    artifacts.append(artifact)
                                if artifacts:
                                    assistant.details = {
                                        **assistant.details,
                                        "artifact_id": str(artifacts[0].id),
                                        "artifact_ids": [str(a.id) for a in artifacts],
                                    }
                                await store.commit()
                                yield sse("sources", {"sources": data["sources"]})
                                for artifact in artifacts:
                                    metrics["artifact_created"] = True
                                    yield sse("artifact", artifact_json(artifact))
                                yield sse("done", {"message": message_json(assistant)})
                        else:
                            yield sse(event, data)
            except asyncio.CancelledError:
                metrics["error_category"] = "cancelled"
                if user_saved:
                    with anyio.CancelScope(shield=True):
                        await save_failure(body, "Response stopped. You can try again.", "cancelled", user.id)
                raise
            except Exception as exc:
                if isinstance(exc, AppError):
                    code, message = exc.code, exc.message
                elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                    code, message = "model_timeout", "The request took too long. Please try again."
                elif isinstance(exc, SQLAlchemyError):
                    code, message = (
                        "database_unavailable",
                        "The conversation store is unavailable. Check PostgreSQL and retry.",
                    )
                else:
                    code, message = (
                        "generation_failed",
                        "The request could not be completed. Check service readiness and retry.",
                    )
                metrics["error_category"] = code
                if user_saved:
                    await save_failure(body, message, "failed", user.id)
                yield sse("error", {"code": code, "message": message, "request_id": request.state.request_id})
            finally:
                with anyio.CancelScope(shield=True):
                    try:
                        await connection.execute(
                            text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key(body.session_id)}
                        )
                        await connection.commit()
                    except Exception:
                        await connection.invalidate()  # Closing a native PG connection releases its session locks.
                metrics["total_latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                metrics["generation_latency_ms"] = max(0, metrics["total_latency_ms"] - metrics["retrieval_latency_ms"])
                log.info(json.dumps(metrics))

    async def guarded_events():
        # Connection acquisition/locking can fail before the inner stream handler starts.
        try:
            async for event in events():
                yield event
        except Exception:
            log.error(json.dumps({"event": "stream_setup_failed", "request_id": request.state.request_id}))
            yield sse(
                "error",
                {
                    "code": "database_unavailable",
                    "message": "The conversation store is unavailable. Check PostgreSQL and retry.",
                    "request_id": request.state.request_id,
                },
            )

    return StreamingResponse(
        guarded_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def save_failure(body, message, status, user_message_id):
    try:
        async with SessionLocal() as store:
            failed_input = await store.get(Message, user_message_id)
            if failed_input:
                failed_input.details = {**failed_input.details, "status": status}
            store.add(
                Message(
                    session_id=body.session_id,
                    role="assistant",
                    content=message,
                    provider=body.provider,
                    model=model_name(body.provider),
                    details={"status": status, "sources": []},
                )
            )
            await store.commit()
    except Exception:
        pass  # Database outage cannot be recorded in the database; structured log remains.
