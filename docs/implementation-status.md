# Implementation status

## Public-submission safety check — 12 October 2026

Replaced the transcript-derived evidence in `backend/tests/fixtures/elena-evidence.json` with synthetic test data while retaining its schema and the fixture consumers' retrieval, validation, and persistence checks. The fixture-consuming run passed **40 tests** (the complete `test_response_modes.py` module plus the SQL-backed evidence serialization test). The full backend suite was not rerun after this fixture-only change; its last recorded result remains **125 passed, 1 skipped**. No live Gemini or Ollama inference was run, and Docker/Compose remains unverified.

The original `evals/demo-guests.json` and `skills/ship30/SKILL.md` remain unchanged and are excluded by `.gitignore`; this repository contains no redistribution grant for them. Demo ingestion now uses the metadata-only, configurable `evals/demo-sources.json` manifest, which points to a Changelog transcript covered by the upstream CC BY-SA 4.0 license and records attribution. Essay generation loads the independently authored `skills/growth-essay/ESSAY_GUIDE.md` by default, configurable with `SHIP30_GUIDE_FILE`. Verification on 2026-10-12: focused core tests **25 passed**, isolated PGlite demo-ingestion integration test **1 passed**, targeted Ruff **passed**. The full backend suite was not rerun. No source transcript was copied into the repository, and no live model request was run.

## Latest UI pass — 11 October 2026

Editorial UI polish is implemented. The final frontend run passed **6 component tests**, **18 browser tests**, TypeScript/Vite production build and Prettier checks. Additional focused startup assertions passed **2/2**; layout recapture passed **4/4**. User-right/assistant-left alignment was verified at four viewport sizes. Startup, reduced motion, persistence, streaming/stop/retry, evidence and artifact contracts were exercised with mocked APIs. HTML remains script-free. No backend changes or model benchmarks were made in this pass. Generated screenshots and detailed UI QA records are excluded from the public file set.

## Earlier application and benchmark status

Updated 2026-10-11. The application is running locally. Corrective work fixes routing, stable evidence references, metadata validation, separate Markdown/HTML artifacts and provider status. Live model quality and external verification limits are recorded explicitly below.

The 2026-10-11 benchmark was recorded as 17/20 Ollama and 18.5/20 Gemini, but only Tests 5, 10–15, and 18 were rerun live against the corrected API; other live ratings were carried forward from baseline. Test 14 is partial because the HTML sanitizer strips scripts. That pass recorded 74 focused tests and a separate backend run of 126 passed / 15 skipped before the final unverified routing tweak. The later full-suite record below is 125 passed / 1 skipped. These are test-suite results, not live model inference. The `context_declaration` tweak is not tested or score-credited. Detailed benchmark reports and snapshots are excluded from the public repository.

The latest response-validation and QA-remediation patches are verified locally. They cover exact-output grammar, fresh metadata/raw-evidence lookup, no-search artifacts, citation association, explicit abstention precedence and fail-closed grounded responses. Model generation now runs through the official Pi Coding Agent SDK service for the explicitly selected Gemini or Ollama model; the existing Google GenAI SDK is retained only for Gemini model-access status. Live model requests remain a separate verification boundary.

The Pi execution-path implementation was verified on 2026-10-12: focused provider tests **12/12**, Pi bridge tests **9/9**, and targeted Ruff **passed**. Follow-up verification fixed the stale conversation-memory integration assertion to check the public `response_kind` event payload while retaining its history and retrieval-isolation checks. The full backend suite now passes **125 tests with 1 skipped**. The reload layout fix also passes the frontend tests/build and **18 desktop/mobile browser tests**. No real Gemini or Ollama generation was run. Docker was unavailable, so Compose validation/startup remains unverified. Local implementation transcripts are excluded from the public repository.

- [x] Inspect empty workspace and read complete assignment, including hyperlink targets.
- [x] Confirm transcript source and Ship 30 guide.
- [x] Record discovery brief and scope decisions before implementation.
- [x] Foundation implementation: FastAPI, PostgreSQL schema, migration, health, Compose.
- [x] Knowledge implementation: real corpus, provenance, idempotent ingestion, local embeddings, hybrid retrieval.
- [x] Provider implementation: Google GenAI Gemini, Ollama, bounded history, streaming, citations, abstention.
- [x] Explicit conversation-history recall routes through the session-scoped conversation path; transcript-grounded questions retain normal retrieval.
- [x] The browser restores its active persisted session after reload, so follow-up requests keep the same session ID and history.
- [x] Product implementation: responsive React UI, sessions, evidence inspector, provider switch.
- [x] Artifact implementation: configurable growth-essay guide, validation/retry, Markdown, sanitized isolated HTML/CSS.
- [x] Verification: backend, integration, frontend, browser contracts, retrieval and abstention evaluations.
- [x] Complete the five-case live artifact evaluation (structural/persistence checks).
- [x] Handoff: synchronized docs, test evidence, demo script, final acceptance checklist.
- [x] Add a source-support review, exact quote checks and fail-closed handling for unsupported drafts; disclose model-review limitations.
- [ ] External deliverables: public repository and camera-enabled YouTube demo.

Environment discovery: native PostgreSQL 18 service exists. Docker and Ollama were not on PATH. No pgvector extension was found in PostgreSQL 18. Do not alter the user's existing database.

## Corrective regression verification

- Backend: **84 passed, 1 skipped** on the separate verification PostgreSQL instance (5.64 seconds); Ruff passed. The skip requires independent native PostgreSQL connections. An earlier attempt hit Windows temporary-folder permissions; the successful run used a fresh workspace temporary directory.
- Latest corrective verification: **123 backend tests passed, 1 skipped**; focused response tests passed; Ruff passed; frontend **6 tests passed**; frontend production build passed. Live `/readyz` is ready with 20 sources, 2,266 chunks, and Ollama model verification. The local API now uses explicit IPv4 loopback for Ollama on Windows.
- QA-remediation verification on 2026-10-11: **124 backend tests passed, 1 skipped**, Ruff passed, frontend **6 tests passed**, production build passed, and Playwright browser contracts **6 passed**. The grounded competitor response now satisfies its two-sentence requirement; explicit unsupported-information responses override stale artifact mode. HTML remains intentionally script-free and permissionless per the PRD.
- Frontend: **5 component tests and 6 desktop/mobile browser contracts passed**; production build passed. Browser contracts cover separate Markdown/HTML records, independent opening, HTML download, reload persistence, provider switching, missing-key disabling and service failures.
- Metadata: audited all **20 sources**, detected **1 conflict**, applied generic validation, preserved all source IDs and **2,266 passage IDs**, and confirmed **20/20 unchanged** on repeat ingestion. The generated audit snapshot is excluded from the public repository.
- Live support evaluation on Qwen3 4B: **9/10 labelled cases passed**. The added multi-speaker paraphrase was wrongly rejected; a separate Qwen2.5 comparison scored **7/10**. The cases include real-transcript paraphrase/quotation, contradiction, wrong speaker, invented number/guarantee, fabricated quote and invalid ID. These tuned diagnostics are not proof of general reliability.
- The sixth live API regression run passed **7/9** contracts: greetings, exact output, no-search acknowledgement, stable source quote/metadata, an actual sourced follow-up and unsupported abstention. The three-insight answer was over-rejected, and the essay retained quotation/length defects. **No long-form artifact was created.** Detailed run snapshots are excluded from the public repository.
- A separate short-artifact attempt reached semantic review after correcting a false quotation rejection for an explicitly proposed interview question. It was still withheld when Qwen3 misattributed Teresa's words to Lenny. Live dual-artifact verification remains pending; mocked persistence/viewer tests do not substitute for it.

## Earlier verification (before the corrective audit)

- Backend: **37 unit/API/provider/security/integration tests passed, 1 skipped**; Ruff passed. The native-concurrency scenario explicitly skips WASM PostgreSQL.
- Frontend: four component tests passed; TypeScript/Vite production build passed; four browser contract scenarios passed across desktop/mobile. UI tests use fixtures, not live inference.
- Local setup: official Ollama 0.40.1 portable runtime checksum verified; current model **qwen3:4b-instruct-2507-q4_K_M**, 8K context and 360-second limit per attempt. Long essays take minutes on this machine's 4GB GPU. The UI is running at `http://127.0.0.1:5174`, API at port 8000, verification database at 55432, separate test database at 55442, and Ollama at 11434.
- Data: 20 real episodes / 2,266 passages indexed at `be8ab89a890a833cbba2c892178f823fff178c65`. Refresh completed, including the upstream Gibson Biddle metadata warning and immutable transcript links. Repeat ingestion: 0 updated, 20 unchanged.
- Retrieval: 20/20 grounded questions hit a designated relevant episode in the top eight; this measures episode retrieval, not claim entailment.
- Immutable migration: downgrade to base, upgrade to head, and integration rerun succeeded on the disposable PGlite instance.
- Unsupported generation: **5/5** actual local-model cases abstained in the recorded run.
- Live walkthrough: answers passed in **15.96s / 19.57s**, first visible deltas **2.69s / 3.34s**. HTML passed in **87.98s** and Markdown in **49.79s**. Essay failed after one repair and was withheld. These are recorded local results, not a current rerun.
- Revised artifact evaluation: **5/5 API structural/persistence checks passed**. The two essays contain **1,221 / 1,256 words** and completed in **158.31s / 158.28s**. Positioning brief: **657 words, 66.47s**; HTML page: **389 words, 202.62s**; prioritization playbook: **924 words, 112.86s**. These are not factual-accuracy scores; detailed output snapshots and screenshots are excluded.
- Live browser: the revised 1,221-word essay, HTML page and positioning brief passed preview/source/copy/download and persistence checks at desktop and phone sizes (six artifact/viewport checks); desktop expansion also passed. The generated screenshots and reports are excluded from the public repository.
- Final runtime: restarted the API with the last validation correction; readiness confirms database, vector, 20-source corpus and local Ollama availability. Frontend build, four component tests and four browser contracts passed after the final UI wording change.

## Corrections and quality limits

Earlier Qwen2.5 and Qwen3 generation failures remain in the evaluation reports. The hybrid Qwen3 variant returned planning text despite the thinking switch, so the dedicated instruct model is used with a guard for recognizable planning prefixes. Failed/cancelled turns remain visible but are excluded from future history.

A valid-length essay omitted bullets. Length/bullet/emphasis-only repairs now add one small cited section where space permits, then validate the full essay. Unsupported percentages, including uncited numerical examples, are rejected. Other defects allow one full revision.

The percentage validator excludes HTML/CSS layout values and checks visible text. That final correction is covered by the backend regression suite; the recorded five-case model run preceded this layout-only correction.

Source review caught a structurally valid essay misusing an unrelated time-management discussion as product-market-fit advice. Named-guest requests now scope retrieval to the specified interview(s), remove artifact-format wording from the search query, and admit more relevant passages from a single requested interview. The hybrid SQL now applies cosine thresholds to lexical-only candidates rather than discarding them. Separate integration tests cover both corrections. The original flawed essay remains in `evals/artifact-initial-smoke.json` with a failed human-review note.

Small-model output can still blend related advice, overstate an inference or attribute an interviewer's wording to the guest. Citation allowlisting and a narrow percentage check do not prove semantic entailment. Recorded source reviews found concrete issues in generated essays; those drafts are not approved as fully source-faithful. Inspect and edit generated advice before relying on it. Detailed source-review notes are excluded from the public repository.

## Explicit constraints

- Live Gemini verification remains pending; mocked provider contract tests do not prove live connectivity.
- Docker is unavailable on this host. Compose and native PostgreSQL concurrency still require a supported host/CI run; PGlite is explicitly a verification fallback.
- Public GitHub publication and the candidate's camera-enabled YouTube video are not done.

Original assessment materials, detailed evaluation records, screenshots and local implementation transcripts are excluded from the public repository.
