"""Audit every indexed source; optionally update metadata without re-embedding unchanged text."""

import argparse
import asyncio
import json
from pathlib import Path

from app.db import SessionLocal, engine
from app.evidence import metadata_integrity
from app.ingest import ingest, parse_transcript
from app.models import Chunk, Source
from sqlalchemy import select


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=Path("data/transcripts"))
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    try:
        async with SessionLocal() as db:
            sources = (await db.scalars(select(Source).order_by(Source.path))).all()
            before = {
                str(value) for value in (await db.scalars(select(Chunk.id))).all()
            }
        rows = []
        for source in sources:
            metadata, body = parse_transcript(
                (args.path / source.path).read_text(encoding="utf-8")
            )
            rows.append(
                {
                    "source_id": str(source.id),
                    "path": source.path,
                    **metadata_integrity(metadata, body),
                }
            )
        report = {
            "sources_checked": len(rows),
            "conflicts": sum(r["metadata_status"] == "conflicting" for r in rows),
            "sources": rows,
        }
        if args.update:
            report["ingestion"] = await ingest(args.path, profile="demo")
            async with SessionLocal() as db:
                after = {
                    str(value) for value in (await db.scalars(select(Chunk.id))).all()
                }
                after_sources = {
                    str(value) for value in (await db.scalars(select(Source.id))).all()
                }
            report["chunk_ids_preserved"] = before == after
            report["source_ids_preserved"] = {
                str(source.id) for source in sources
            } == after_sources
            report["idempotence"] = await ingest(args.path, profile="demo")
        Path("evals/metadata-audit.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {key: value for key, value in report.items() if key != "sources"}
            ),
            flush=True,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
