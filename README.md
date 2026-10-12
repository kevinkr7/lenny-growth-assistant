# The Lenny Growth Assistant

A local product and growth workspace: ask a question, inspect podcast evidence, then turn the answer into a document or a rendered HTML page.

**Start with [implementation status](docs/implementation-status.md) for the exact verification state and remaining handoff items.** Targets and implemented features are not a claim that every environment has been tested.

The latest fixture cleanup verification (12 October 2026) passed **40 tests**, including the SQL-backed evidence serialization test. The last recorded full backend run passed **125 tests with 1 skipped** before this fixture-only cleanup; the full suite was not rerun afterward. Provider and frontend checks recorded elsewhere use mocked or contract fixtures where applicable. **Live Gemini and Ollama inference remain unverified, and Docker/Compose was not run.** See [implementation status](docs/implementation-status.md) for the verification boundaries and remaining blockers. Original no-search artifacts use format checks without borrowed podcast sources; indexed metadata/raw-evidence requests return stored fields directly.

## What it does

- FastAPI streams progress over SSE; ordinary conversation bypasses retrieval and grounded answers appear after source checks.
- PostgreSQL stores independent sessions, messages, user metadata, sources, chunks and artifacts.
- Local CPU embeddings, pgvector cosine search and PostgreSQL full-text search retrieve a small evidence packet.
- Ollama runs local inference and Gemini is an optional Google cloud provider. Provider failures never trigger a silent switch.
- A configurable, independently authored growth-essay guide shapes long-form essays targeting 1,250 words; deterministic checks allow one repair attempt.
- Requesting Markdown and HTML produces two separate saved artifacts beside chat. HTML is sanitized twice and rendered in a permissionless sandbox with a restrictive CSP.

## Quick start with Docker Compose

Prerequisites: Git, Docker Engine/Desktop with Compose v2, network access for first downloads, several GB of free disk space, and adequate RAM for a 4B model. CPU works but can be slow. Native Ollama on a GPU can be faster than the default CPU container.

```cmd
copy .env.example .env
docker compose up --build -d
docker compose logs -f model-init ingest
```

Open **http://localhost:3000**. The first start downloads the model, the configured demo transcript source, and the embedding model, then indexes sources selected by `evals/demo-sources.json`. Readiness stays degraded until prerequisites are ready. Subsequent ingestion skips unchanged sources. A first cold start is substantially slower than a warm restart.

```sh
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
docker compose logs -f api
docker compose down
```

`down` preserves data volumes. Do not add `-v` unless you intend to erase conversations, downloaded models and indexed material. Services bind to loopback. This is an unauthenticated internal evaluation app; add access controls before exposing it outside a trusted machine.

The Docker path is included for evaluator reproducibility; consult the status document for whether it was actually executed in the implementation environment.

## Native development

Prerequisites: Python 3.12, Node.js 22.19 or newer, PostgreSQL 17+ with pgvector, Git, and Ollama. Create a dedicated database and user; do not point migrations at unrelated application data. The DB user needs permission to install `vector`, or a database administrator must install it first.

From the repository root:

```cmd
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -e "backend[dev]"
copy .env.example .env
```

Set `DATABASE_URL` to your dedicated PostgreSQL database. The example uses port 5433 to avoid a common local PostgreSQL conflict. Then:

```cmd
cd backend
..\.venv\Scripts\alembic.exe upgrade head
cd ..
.venv\Scripts\python.exe scripts\fetch_and_ingest.py --profile demo
```

Start Ollama in its own CMD window with `ollama serve`. In another window, run `ollama pull qwen3:4b-instruct-2507-q4_K_M`.

In another CMD window, start the Pi Coding Agent SDK bridge from the repository root:

```cmd
cd pi-agent
npm ci
npm start
```

In another CMD window, start FastAPI:

```cmd
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a final CMD window, start the frontend:

```cmd
cd frontend
npm ci
npm run dev
```

Open the URL Vite prints (normally http://localhost:5173). The Pi service listens only on loopback during native development; Compose keeps it on the private service network with no published host port. The API sends the selected provider/model to Pi, and sends the Gemini key only in the internal generation request when Gemini is selected. Neither service logs prompts or credentials.

## Models and configuration

Copy `.env.example`; keep real secrets in `.env` or the process environment. Never commit them. Changes to backend environment values require a restart.

| Variable | Default / purpose |
| --- | --- |
| `DATABASE_URL` | Async SQLAlchemy URL, `postgresql+asyncpg://...` |
| `POSTGRES_PASSWORD` | Local Compose password; change for a shared environment |
| `DEFAULT_PROVIDER` | `ollama`; initial provider preference |
| `OLLAMA_BASE_URL` | `http://localhost:11434`; inside Compose, `http://ollama:11434` |
| `OLLAMA_MODEL` | `qwen3:4b-instruct-2507-q4_K_M`; use an instruct model and pull replacements before choosing them |
| `PI_AGENT_URL` | `http://localhost:8001` for native development; Compose uses its private `pi-agent` service |
| `PI_AGENT_MAX_TIMEOUT_MS` | `360000`; hard maximum per SDK generation request in the Pi service |
| `GEMINI_API_KEY` | Optional; required for real Gemini requests; keep it only in local `.env` |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite`; configurable Google GenAI model |
| `GEMINI_TIMEOUT_SECONDS` | 90-second overall Gemini request budget, including bounded retries |
| `MODEL_TIMEOUT_SECONDS` | 360 per model call; at most two drafts and two source reviews |
| `MODEL_MAX_TOKENS` | 3500 for Ollama; cloud output additionally has a 45,000-character cap |
| `OLLAMA_CONTEXT_TOKENS` | 8192; increase together with larger context/output budgets if hardware permits |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5`, 384 dimensions |
| `EMBEDDING_CACHE_DIR` | Workspace `data/embeddings`; Compose `/data/embeddings` |
| `RAG_TOP_K` / `RAG_MIN_SCORE` | 8 chunks / 0.45 minimum cosine similarity |
| `CONTEXT_MESSAGES` / `CONTEXT_CHAR_LIMIT` | 8 messages / 8,000 characters; at most 4,000 per historical message |
| `CORS_ORIGINS` | Explicit allowed development web origins |
| `TRANSCRIPT_REPOSITORY` / `TRANSCRIPT_REVISION` | Repository and Git ref for the full `all` profile |
| `DEMO_SOURCES_FILE` | `evals/demo-sources.json`; configurable metadata-only manifest used by the `demo` profile |
| `SHIP30_GUIDE_FILE` | `skills/growth-essay/ESSAY_GUIDE.md`; configurable long-form essay instructions |

**Ollama:** selecting Local never falls back to cloud. The UI shows the configured model and whether it is installed. With native Ollama and Docker for the API, override `OLLAMA_BASE_URL` for the `api` service to `http://host.docker.internal:11434` and ensure Ollama accepts that interface; review network exposure before changing its binding.

The verified configuration uses the dedicated Qwen3 **instruct** tag above; the hybrid `qwen3:4b` tag returned planning text in this runtime. Thinking is disabled in requests and the provider ignores separate thinking fields; a prefix guard rejects recognizable planning output. On a small NVIDIA GPU, the implementation run used `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` in the Ollama server environment to reduce memory use. Long essays can still take minutes; preserve the validation check and use saved real results for a short recorded demo.

**Gemini:** set `GEMINI_API_KEY` in the local `.env`, optionally change `GEMINI_MODEL`, restart the API, and choose Gemini in the header. The Pi bridge uses the official Pi Coding Agent SDK with Google's provider; the existing Python GenAI model-access status check remains separate from generation. Missing keys leave Gemini unavailable; authentication, quota/rate-limit, timeout, model, and network failures are reported without exposing provider messages or credentials. Automated tests mock model responses and do not establish live Gemini connectivity.

Greetings, acknowledgements and exact-output requests do not search. Refer to a saved citation (for example, “Quote S3 verbatim” or “Give the title, author and publication date of S3”) to inspect that same passage. Missing fields are explicitly unavailable. A new topic retrieves new evidence. Factual drafts pass citation/quote checks and a separate model-based support review; unsupported or inconclusive drafts are repaired once, then withheld. This review can still make mistakes, so original excerpts remain inspectable.

## Data ingestion and provenance

The full `all` profile uses the configured `TRANSCRIPT_REPOSITORY` and `TRANSCRIPT_REVISION`; the example points to [ChatPRD/lennys-podcast-transcripts](https://github.com/ChatPRD/lennys-podcast-transcripts), which describes personal and educational use. Its transcripts are fetched at runtime and are not redistributed in this codebase. The public-safe `demo` profile instead follows the metadata-only manifest at `DEMO_SOURCES_FILE`. Its Changelog transcript is outside the repository's specially excluded `bigtent` folder and is marked CC BY-SA 4.0 by the upstream project. The manifest records attribution and license metadata without embedding excerpts. Reuse must retain attribution and follow the license's share-alike terms. The episode page identifies Sahil Lavingia, the title, and publication date. [Transcript repository license](https://github.com/thechangelog/transcripts#license--copyright), [episode page](https://changelog.com/founderstalk/66), [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

```sh
# Fetch and index the source selected by the demo manifest:
python scripts/fetch_and_ingest.py --profile demo
# Download/update and index the full archive:
python scripts/fetch_and_ingest.py --profile all
# Index an existing checkout without fetching:
python -m app.ingest --path data/transcripts --profile all
# Force recomputation (e.g. after changing embedding/chunking behavior):
python -m app.ingest --path data/transcripts --profile all --refresh
```

Compose equivalents:

```sh
docker compose run --rm ingest python /app/scripts/fetch_and_ingest.py --path /data/transcripts --profile all
docker compose run --rm ingest python -m app.ingest --path /data/transcripts --profile all --refresh
```

Source hashes and pipeline versions prevent repeated embedding work. A changed source replaces its chunks atomically. Each source preserves the repository, revision, path, URL, guest, title, date, description, source hash, original metadata and ingestion timestamp. Deleting a source from the upstream repository does not silently remove historical evidence; automatic pruning is intentionally absent. Old answers/artifacts keep evidence snapshots even after a refresh.

Source integrity checks compare declared guests and transcript speaker labels against episode titles. They cannot authenticate video contents or independently verify publication dates. Unknown fields remain unknown, and original metadata is retained for inspection.

Chunks target 220 words with up to 40 words of overlap, respecting paragraph boundaries where possible. Oversized turns are split. The demo profile is listed in the configurable `evals/demo-sources.json` manifest. The full archive takes longer and may shift retrieval rankings. Changing to a model with another embedding dimension requires a schema migration and complete reindex; a 384-dimensional replacement still requires `--refresh`.

## Tests and evaluations

```sh
python -m pytest backend/tests -q
ruff check backend
cd frontend
npm ci
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

The PostgreSQL integration tests are opt-in, use uniquely named schemas, and need a migrated **test** database:

```cmd
set TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:5432/lenny_test
python -m pytest backend/tests/test_integration.py -q
```

Browser contract tests use deterministic API fixtures and run at desktop and phone sizes. They validate UI state transitions, not model quality. The CI workflow includes native pgvector/PostgreSQL service tests. A checked-in workflow is not evidence that a remote CI run succeeded.

Automated tests use deterministic fixtures and do not establish live model quality or live Gemini/Ollama connectivity. Live evaluations make real provider requests and can be slow; their generated reports and screenshots are local QA artifacts and are excluded from this repository.

## API

Interactive OpenAPI: http://localhost:8000/docs. Main endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz`, `/readyz` | Process liveness; database, vector, library and provider readiness |
| GET | `/api/providers` | Model names and availability/recovery guidance |
| POST / GET | `/api/sessions` | Create / list conversations |
| GET / DELETE | `/api/sessions/{id}` | Read messages/artifacts / delete conversation |
| GET | `/api/sessions/{id}/messages` | Persisted messages |
| POST | `/api/chat/stream` | `{session_id,message,provider,mode}`; SSE response |
| GET | `/api/artifacts/{id}`, `/api/sources/{id}` | Saved artifact / source provenance |

Modes: `auto`, `answer`, `ship30`, `markdown`, `html`, `both`. SSE events: `accepted`, `status`, `delta`, `reset`, `sources`, `artifact`, `done`, `error`. Grounded drafts are withheld during validation; `done.message` is the saved response. A request can emit multiple `artifact` events, each with its own ID. Error bodies have a stable code, human-readable message and request ID.

## Troubleshooting

| Symptom | Recovery |
| --- | --- |
| UI cannot reach backend | Start FastAPI; check port 8000 and Vite/nginx proxy. Use Retry connection. |
| PostgreSQL unavailable | Check `DATABASE_URL`, service health, credentials, dedicated database, and migration logs. |
| `extension vector is not available` | Use the pgvector Docker image or install pgvector for the exact native PostgreSQL version. |
| Library empty | Run ingestion; inspect its logs and embedding download access. |
| Ollama unavailable / model missing | Start Ollama, run `ollama pull <model>`, check URL, then Retry connection. |
| Gemini not configured / unavailable | Set `GEMINI_API_KEY` locally, confirm `GEMINI_MODEL`, restart the API, and use Retry connection. Quota and rate-limit errors require waiting or selecting Ollama. |
| Timeout | Warm the local model, narrow the topic, use a faster/smaller model, or increase the configured timeout within its 600-second cap. |
| Draft fails validation twice | Narrow the evidence/topic or choose a more capable model. Invalid drafts are not saved as artifacts. |
| Empty / stripped HTML preview | Unsupported active content is removed deliberately. Use semantic HTML and CSS without external assets. |
| Answer abstains | Ask a more specific question, inspect indexed coverage, or ingest the full corpus. |
| First request slow | The embedding model and LLM load on first use. Subsequent requests should reuse their caches. |
| Windows pytest temporary-directory permission error | Use a fresh test directory: `python -m pytest backend/tests -q --basetemp .runtime/pytest-local -o cache_dir=.runtime/pytest-cache-local`. Do not reuse a temp directory created by another Windows account. |

Logs are JSON for chat events with request/session/provider/model identifiers, counts, latencies, artifact flag and error category. User prompts, retrieved passages, API keys and SDK stderr are not logged by the application. Reverse proxy and runtime logs may have their own formats.

## Handoff

- [Design](design.md): layout, interactions, responsive behavior and accessibility.
- [Architecture](architecture.md): schema, retrieval, providers, trust boundaries and operations.
- [Implementation status](docs/implementation-status.md): recorded test results and verification limits.
- [Verification database harness](scripts/verification-db/README.md): local PostgreSQL-compatible test support.

Original assessment material, local work logs, detailed evaluation snapshots, and generated screenshots are intentionally omitted from the public file set. The verification harness is a PGlite PostgreSQL WASM instance with pgvector, used when Docker/native pgvector is absent. It is a local verification fallback, not the production persistence topology; native PostgreSQL CI is the authority for multi-connection behavior.
