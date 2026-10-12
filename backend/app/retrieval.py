import re
import time

from sqlalchemy import select, text

from app.config import settings
from app.db import SessionLocal
from app.embeddings import embed
from app.models import Source


def focus_query(query: str, guests: list[str]) -> str:
    """Remove output-format wording and already-applied guest names, keeping the topic."""
    focused = re.sub(
        r"^(?:write|create|draft|generate)\b[^\n]{0,140}?\b(?:about|explaining|grounded in)\s+",
        "",
        query,
        flags=re.I,
    )
    if focused != query:
        # Remove output instructions, while retaining substantive secondary topics.
        # Guest scoping has already used the complete request.
        sentences = re.split(r"(?<=[.!?])\s+", focused)
        topic = []
        for sentence in sentences:
            if re.search(
                r"^(?:(?:please|clearly)\s+)?(?:create|return|format|use|include|label)\b.*\b(?:markdown|html|artifacts?|headings?|bullet|proposed applications)\b",
                sentence,
                re.I,
            ):
                continue
            topic.append(
                re.sub(
                    r",?\s+(?:grounded in|based on)\b.*?\b(?:interview|podcast|material|discussion)\b.*$",
                    "",
                    sentence,
                    flags=re.I,
                )
            )
        focused = " ".join(topic)
    for guest in guests:
        focused = re.sub(rf"\b{re.escape(guest)}(?:['’]s)?\b", "", focused, flags=re.I)
    focused = re.sub(r"\b(?:approach to|advice on|based on|workshop)\b", " ", focused, flags=re.I)
    return re.sub(r"\s+", " ", focused).strip() or query


async def search_knowledge_base(query: str) -> tuple[list[dict], float]:
    start = time.perf_counter()
    async with SessionLocal() as db:
        sources = (await db.execute(select(Source.id, Source.guest))).all()
    named_sources = [
        source for source in sources if source.guest and re.search(rf"\b{re.escape(source.guest)}\b", query, re.I)
    ]
    scope = [source.id for source in named_sources]
    query = focus_query(query, list({source.guest for source in named_sources}))
    vector = (await embed(["Represent this sentence for searching relevant passages: " + query[:3000]]))[0]
    sql = text("""
    WITH semantic AS (
      SELECT id,
             row_number() OVER (ORDER BY embedding <=> CAST(:embedding AS vector)) AS rank
      FROM chunks WHERE (:unscoped OR source_id = ANY(CAST(:scope AS uuid[])))
      ORDER BY embedding <=> CAST(:embedding AS vector) LIMIT 40
    ), lexical AS (
      SELECT id, row_number() OVER (ORDER BY ts_rank_cd(search_vector, websearch_to_tsquery('english', :query)) DESC) AS rank
      FROM chunks WHERE search_vector @@ websearch_to_tsquery('english', :query)
      AND (:unscoped OR source_id = ANY(CAST(:scope AS uuid[])))
      ORDER BY ts_rank_cd(search_vector, websearch_to_tsquery('english', :query)) DESC LIMIT 40
    ), fused AS (
      SELECT coalesce(s.id,l.id) AS id,
             coalesce(1.0/(60+s.rank),0)+coalesce(1.0/(60+l.rank),0) AS score
      FROM semantic s FULL OUTER JOIN lexical l ON s.id=l.id
    )
    SELECT c.id AS chunk_id, c.content, c.chunk_index, s.id AS source_id, s.episode_title, s.guest,
           s.source_url, s.publish_date, s.revision, s.path, s.repository,
           s.metadata->'original_metadata' AS original_metadata,
           s.metadata->>'author' AS author, c.metadata AS passage_metadata,
           s.metadata->>'metadata_warning' AS metadata_warning, f.score,
           1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity
    FROM fused f JOIN chunks c ON c.id=f.id JOIN sources s ON s.id=c.source_id
    WHERE 1 - (c.embedding <=> CAST(:embedding AS vector)) >= :minimum
    ORDER BY f.score DESC LIMIT 32
    """)
    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    sql,
                    {
                        "embedding": str(vector),
                        "query": query[:3000],
                        "minimum": settings().rag_min_score,
                        "unscoped": not scope,
                        "scope": scope,
                    },
                )
            )
            .mappings()
            .all()
        )
    evidence, per_source = [], {}
    source_limit = settings().rag_top_k if len(scope) == 1 else 2
    for row in rows:
        source_id = str(row["source_id"])
        if per_source.get(source_id, 0) >= source_limit:
            continue
        per_source[source_id] = per_source.get(source_id, 0) + 1
        evidence.append(
            {
                **dict(row),
                **(row["passage_metadata"] or {}),
                "source_id": source_id,
                "chunk_id": str(row["chunk_id"]),
                "citation": f"S{len(evidence) + 1}",
                "score": float(row["score"]),
                "similarity": float(row["similarity"]),
                "excerpt": row["content"],
            }
        )
        if len(evidence) >= settings().rag_top_k:
            break
    return evidence, round((time.perf_counter() - start) * 1000, 2)
