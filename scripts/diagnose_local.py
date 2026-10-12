"""Local-only draft diagnostics. Output stays in ignored .runtime; never log prompts in production."""

import argparse
import asyncio
import json
from pathlib import Path

from app import agent
from app.db import engine
from app.errors import AppError

original = agent.validate
original_generate = agent.generate
drafts = []


def inspect_draft(content, mode, evidence):
    problems = original(content, mode, evidence)
    drafts.append(
        {
            "mode": mode,
            "content": content,
            "problems": problems,
            "allowed": [e["citation"] for e in evidence],
        }
    )
    Path(".runtime").mkdir(exist_ok=True)
    Path(".runtime/local-drafts.json").write_text(
        json.dumps(drafts, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "mode": mode,
                "problems": problems,
                "words": agent.word_count(content),
                "preview": content[:250],
            }
        ),
        flush=True,
    )
    return problems


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", default="answer", choices=["answer", "ship30", "markdown", "html"]
    )
    parser.add_argument(
        "--question", default="How should a startup approach product-market fit?"
    )
    parser.add_argument("--history-file", type=Path)
    args = parser.parse_args()
    history = (
        json.loads(args.history_file.read_text(encoding="utf-8"))
        if args.history_file
        else []
    )
    agent.validate = inspect_draft

    async def capture(provider, system, prompt):
        output = ""
        async for token in original_generate(provider, system, prompt):
            output += token
            yield token
        print(json.dumps({"generated_characters": len(output)}), flush=True)

    agent.generate = capture
    try:
        async for event, data in agent.run_agent(
            args.question, "ollama", args.mode, history
        ):
            if event == "result":
                print(
                    json.dumps(
                        {
                            "abstained": data["abstained"],
                            "sources": len(data["sources"]),
                        }
                    )
                )
    except AppError as error:
        print(type(error).__name__, str(error))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
