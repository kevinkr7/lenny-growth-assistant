# Optional local verification database

This harness exists for machines without Docker or native pgvector. PGlite runs PostgreSQL compiled to WASM with pgvector, persisted under `data/verification-db-v2`. It multiplexes a single backend: it is not the production topology and cannot verify native PostgreSQL concurrency. The intended deployment remains the root Compose file.

After installing root Python/frontend prerequisites, start this in its own terminal:

```powershell
cd scripts/verification-db
npm ci
npm start
```

The loopback proxy listens on 55432, forwarding to 55433. It supplies EOF on PostgreSQL Terminate because the socket adapter otherwise leaves asyncpg waiting during disposal. No authentication boundary is claimed; keep both ports on loopback.

In the root `.env`, use the dedicated harness URL:

```dotenv
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/postgres?ssl=disable
```

From a second terminal, activate the Python environment, run `cd backend; alembic upgrade head`, return to root and run `python scripts/fetch_and_ingest.py --profile demo`. Start native Ollama and the API/frontend as described in the root README. Do not put the harness on the existing native PostgreSQL port.

On the implementation machine, the portable Ollama executable is in ignored `.runtime/ollama/ollama.exe`. To reuse the downloaded models in a PowerShell terminal at the repository root:

```powershell
$env:OLLAMA_MODELS = Join-Path (Get-Location) 'data/ollama-models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_FLASH_ATTENTION = '1'
$env:OLLAMA_KV_CACHE_TYPE = 'q8_0'
.runtime/ollama/ollama.exe serve
```

For integration tests, launch a **separate** instance in another terminal from this directory:

```powershell
$env:VERIFICATION_PORT = '55442'
$env:VERIFICATION_DB_DIR = '../../data/integration-db'
npm start
```

Migrate that URL, set `TEST_DATABASE_URL` to port 55442, then run the backend suite from root. Tests create and remove their own schemas. Native concurrency tests explicitly skip WASM PostgreSQL. Never run concurrent ingestion and schema-changing tests against the demo database.
