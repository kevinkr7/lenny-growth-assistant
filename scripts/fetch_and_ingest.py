"""Fetch the configured corpus source for a profile and run idempotent ingestion."""

import argparse
import asyncio
import subprocess
from pathlib import Path

from app.db import engine
from app.ingest import ingest, ingestion_source


async def run_git(command: list[str]) -> None:
    await asyncio.to_thread(subprocess.run, command, check=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=Path("data/transcripts"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--profile", choices=["demo", "all"], default="demo")
    args = parser.parse_args()
    repository, revision = ingestion_source(args.profile)
    if not (args.path / ".git").exists():
        args.path.parent.mkdir(parents=True, exist_ok=True)
        await run_git(["git", "clone", "--depth", "1", repository, str(args.path)])
    await run_git(
        [
            "git",
            "-C",
            str(args.path),
            "fetch",
            "--depth",
            "1",
            repository,
            revision,
        ],
    )
    await run_git(["git", "-C", str(args.path), "checkout", "--detach", "FETCH_HEAD"])
    try:
        await ingest(args.path, args.limit, profile=args.profile)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
