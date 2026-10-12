"""python -m app.ingest --path ../data/transcripts [--limit 12] [--refresh]"""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import yaml
from sqlalchemy import delete, select

from app.config import rooted_path, settings
from app.db import SessionLocal, engine
from app.embeddings import embed
from app.evidence import contextual_passage_metadata, metadata_integrity
from app.models import Chunk, Source

PIPELINE_VERSION = "speaker220-overlap40-v2"
METADATA_VERSION = 4


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_transcript(raw: str) -> tuple[dict, str]:
    raw = raw.replace("\r\n", "\n").lstrip("\ufeff")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", raw, re.S)
    if not match:
        raise ValueError("Transcript lacks YAML metadata")
    meta = yaml.safe_load(match[1]) or {}
    # Remove introductory description where the archive marks the transcript.
    body = re.split(r"^##?\s+(?:Transcript|Full Transcript)\s*$", match[2], maxsplit=1, flags=re.M | re.I)[-1]
    return json.loads(json.dumps(meta, default=str)), body.strip()


def load_demo_manifest(manifest_path: str | Path | None = None) -> dict:
    """Load the small, provenance-bearing public-source manifest for demo ingestion."""
    configured = manifest_path if manifest_path is not None else settings().demo_sources_file
    path = rooted_path(configured)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read demo source manifest: {path}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Demo source manifest must be an object with schema_version 1")
    if set(manifest) - {"schema_version", "repository", "revision", "license", "sources"}:
        raise ValueError("Demo source manifest contains unsupported fields")

    repository = manifest.get("repository")
    repo_url = urlsplit(repository) if isinstance(repository, str) else None
    repository_parts = repo_url.path.strip("/").removesuffix(".git").split("/") if repo_url else []
    if (
        not repo_url
        or repo_url.scheme != "https"
        or repo_url.hostname != "github.com"
        or len(repository_parts) != 2
        or any(not part for part in repository_parts)
        or repo_url.query
        or repo_url.fragment
    ):
        raise ValueError("Demo source repository must be an HTTPS GitHub URL")
    if not manifest.get("revision") or not isinstance(manifest.get("revision"), str):
        raise ValueError("Demo source manifest must specify a repository revision")

    license_info = manifest.get("license")
    if not isinstance(license_info, dict) or not all(
        isinstance(license_info.get(key), str) and license_info[key].strip()
        for key in ("spdx_id", "url", "attribution")
    ):
        raise ValueError("Demo source manifest must include license ID, license URL, and attribution")
    if set(license_info) - {"spdx_id", "url", "attribution"}:
        raise ValueError("Demo source license metadata contains unsupported fields")
    license_url = urlsplit(license_info["url"])
    if license_url.scheme != "https":
        raise ValueError("Demo source license URL must use HTTPS")

    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Demo source manifest must contain at least one source")
    seen_paths = set()
    for item in sources:
        if not isinstance(item, dict):
            raise ValueError("Each demo source must be an object")
        # An allowlist keeps the manifest descriptive and prevents transcript excerpts
        # or content from being checked into this configuration file.
        if set(item) - {"path", "guest", "episode_title", "publish_date", "episode_url"}:
            raise ValueError("Demo source entries may contain metadata only, not transcript content")
        relative = item.get("path")
        source_path = PurePosixPath(relative) if isinstance(relative, str) else None
        if (
            not source_path
            or source_path.is_absolute()
            or "\\" in relative
            or ":" in relative
            or ".." in source_path.parts
            or source_path.suffix.lower() != ".md"
        ):
            raise ValueError("Demo source path must be a repository-relative Markdown file")
        if relative in seen_paths:
            raise ValueError("Demo source paths must be unique")
        seen_paths.add(relative)
        for key in ("guest", "episode_title", "episode_url"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"Demo source must specify {key}")
        episode_url = urlsplit(item["episode_url"])
        if episode_url.scheme != "https":
            raise ValueError("Demo episode URL must use HTTPS")
        if item.get("publish_date") is not None and not isinstance(item["publish_date"], str):
            raise ValueError("Demo publish_date must be a string when supplied")
    return manifest


def ingestion_source(profile: str) -> tuple[str, str]:
    """Return the configured repository and ref used by the selected corpus profile."""
    if profile == "demo":
        manifest = load_demo_manifest()
        return manifest["repository"].rstrip("/"), manifest["revision"]
    if profile == "all":
        config = settings()
        return config.transcript_repository.rstrip("/"), config.transcript_revision
    raise ValueError(f"Unknown ingestion profile: {profile}")


def chunk_text(text: str, target: int = 220, overlap: int = 40) -> list[str]:
    """Keep paragraphs/speaker turns intact where possible; bound oversized turns."""
    if not 0 <= overlap < target:
        raise ValueError("overlap must be smaller than target")
    blocks = re.split(r"\n\s*\n", text.strip())
    chunks, current = [], []
    for block in blocks:
        words = block.split()
        while words:
            room = target - len(current)
            if len(words) <= room:
                current.extend(words)
                words = []
            elif current and len(words) <= target:
                chunks.append(" ".join(current))
                current = current[-overlap:] if overlap else []
                if len(current) + len(words) > target:
                    current = []
            else:
                current.extend(words[:room])
                words = words[room:]
                chunks.append(" ".join(current))
                current = current[-overlap:] if overlap else []
    if current and (not chunks or " ".join(current) != chunks[-1]):
        chunks.append(" ".join(current))
    return [c for c in chunks if len(c.split()) >= 15]


async def ingest(path: Path, limit: int | None = None, refresh: bool = False, profile: str = "all"):
    revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    source_entries = []
    if profile == "demo":
        manifest = load_demo_manifest()
        repository = manifest["repository"].rstrip("/")
        license_info = manifest["license"]
        for item in manifest["sources"]:
            file = path.joinpath(*PurePosixPath(item["path"]).parts)
            if not file.resolve().is_relative_to(path.resolve()):
                raise ValueError(f"Demo source resolves outside the repository: {item['path']}")
            source_entries.append((file, item, repository, license_info))
        missing = [item["path"] for file, item, _, _ in source_entries if not file.is_file()]
        if missing:
            raise ValueError(f"Missing demo sources: {missing}")
    elif profile == "all":
        source_entries = [(file, None, settings().transcript_repository.rstrip("/"), None)
                          for file in sorted(path.glob("episodes/*/transcript.md"))]
    else:
        raise ValueError(f"Unknown ingestion profile: {profile}")
    if not source_entries:
        raise ValueError("No sources found for the selected ingestion profile")
    counts = {"updated": 0, "unchanged": 0, "chunks": 0, "revision": revision}
    for file, demo_metadata, repository, license_info in source_entries[:limit]:
        raw = file.read_text(encoding="utf-8")
        relative = file.relative_to(path).as_posix()
        if demo_metadata is None:
            metadata, body = parse_transcript(raw)
        else:
            metadata = {
                "title": demo_metadata["episode_title"],
                "guest": demo_metadata["guest"],
                "publish_date": demo_metadata.get("publish_date"),
                "description": "",
                "episode_url": demo_metadata["episode_url"],
                "source_license": license_info["spdx_id"],
                "license_url": license_info["url"],
                "attribution": license_info["attribution"],
            }
            body = raw.strip()
        async with SessionLocal() as db:
            source = await db.scalar(select(Source).where(Source.path == relative))
            pipeline = f"{PIPELINE_VERSION}:{settings().embedding_model}"
            reuse_chunks = bool(
                source
                and source.content_hash == digest(raw)
                and source.details.get("pipeline") == pipeline
                and not refresh
            )
            if reuse_chunks and source.details.get("metadata_version") == METADATA_VERSION:
                counts["unchanged"] += 1
                continue
        chunks = chunk_text(body)
        if not chunks:
            raise ValueError(f"Empty transcript: {relative}")
        chunk_metadata = contextual_passage_metadata(chunks, body)
        # CPU/model work must not hold an open database transaction.
        vectors = None if reuse_chunks else await embed(chunks)
        async with SessionLocal() as db:
            source = await db.scalar(select(Source).where(Source.path == relative))
            if source is None:
                source = Source(path=relative)
                db.add(source)
            elif not reuse_chunks:
                await db.execute(delete(Chunk).where(Chunk.source_id == source.id))
            source.repository = repository
            source.revision = revision
            repo_url = f"{source.repository}/blob/{revision}/{quote(relative)}"
            # The immutable transcript is the evidence of record. Archive video metadata
            # can be wrong; retain it in metadata rather than silently mislinking citations.
            source.source_url = repo_url
            source.episode_title = str(metadata.get("title") or file.parent.name)
            source.guest = str(metadata.get("guest") or file.parent.name)
            source.publish_date = str(metadata["publish_date"]) if metadata.get("publish_date") else None
            source.description = str(metadata.get("description") or "")
            source.content_hash = digest(raw)
            integrity = metadata_integrity(metadata, body)
            source.details = {
                **metadata,
                **integrity,
                "pipeline": pipeline,
                "repository_url": repo_url,
                "metadata_version": METADATA_VERSION,
            }
            if integrity["metadata_warning"]:
                source.publish_date = None
            source.ingested_at = datetime.now(timezone.utc)
            await db.flush()
            if reuse_chunks:
                for chunk in (await db.scalars(select(Chunk).where(Chunk.source_id == source.id))).all():
                    chunk.details = {**chunk.details, **chunk_metadata[chunk.chunk_index]}
            for index, (content, vector) in enumerate(zip(chunks, vectors or [], strict=not reuse_chunks)):
                db.add(
                    Chunk(
                        source_id=source.id,
                        chunk_index=index,
                        content=content,
                        embedding=vector,
                        content_hash=digest(content),
                        details={"pipeline": pipeline, **chunk_metadata[index]},
                    )
                )
            await db.commit()
        counts["updated"] += 1
        counts["chunks"] += len(chunks)
        print(json.dumps({"event": "source_ingested", "path": relative, "chunks": len(chunks)}), flush=True)
    print(json.dumps(counts), flush=True)
    return counts


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--profile", choices=["demo", "all"], default="all")
    args = parser.parse_args()
    try:
        await ingest(args.path, args.limit, args.refresh, args.profile)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
