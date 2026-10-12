import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import {
  createAgentSession,
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";

const HOST = process.env.PI_AGENT_HOST || "127.0.0.1";
const PORT = Number.parseInt(process.env.PI_AGENT_PORT || "8001", 10);
const MAX_BODY_BYTES = 2_000_000;
const MAX_TIMEOUT_MS = Math.min(
  600_000,
  Math.max(5_000, Number.parseInt(process.env.PI_AGENT_MAX_TIMEOUT_MS || "360000", 10)),
);
const runtimes = new Map();

export async function disposePiRuntimes() {
  const directories = [...runtimes.values()].map(({ directory }) => directory);
  runtimes.clear();
  await Promise.all(directories.map((directory) => rm(directory, { recursive: true, force: true })));
}

class BridgeError extends Error {
  constructor(code, status = 502) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

function safeProviderError(error, provider) {
  const status = Number(error?.status ?? error?.statusCode ?? error?.code);
  const errorName = String(error?.name || "").toLowerCase();
  const message = String(error?.message || "").toLowerCase();
  if (errorName.includes("abort") || message.includes("timeout") || message.includes("timed out")) {
    return new BridgeError("provider_timeout", 504);
  }
  if (provider === "gemini") {
    if (status === 401 || status === 403 || /api.?key|auth|permission/.test(message)) {
      return new BridgeError("gemini_authentication", 401);
    }
    if (status === 429 || /quota|rate.?limit|resource exhausted/.test(message)) {
      return new BridgeError("gemini_rate_limit", 429);
    }
    if (status === 404 || /not found|model.*missing/.test(message)) {
      return new BridgeError("model_missing", 404);
    }
    if (Number.isFinite(status) && status >= 500) return new BridgeError("gemini_service", 503);
  }
  if (provider === "ollama") return new BridgeError("ollama_unavailable", 503);
  return new BridgeError("provider_error", 502);
}

async function runtimeFor(input, sdk = { ModelRuntime }) {
  const { provider, model, ollama_base_url, max_tokens, context_tokens } = input;
  const runtimeProvider = provider === "gemini" ? "google" : "ollama";
  const baseUrl = provider === "ollama" ? ollama_base_url : "";
  const cacheable = provider === "ollama";
  const cacheKey = JSON.stringify([runtimeProvider, model, baseUrl, max_tokens, context_tokens]);
  const cached = cacheable ? runtimes.get(cacheKey) : undefined;
  if (cached) return cached;

  const directory = await mkdtemp(join(tmpdir(), "lenny-pi-"));
  const modelsPath = join(directory, "models.json");
  {
    const normalizedBaseUrl = String(baseUrl || "").replace(/\/+$/u, "");
    if (provider === "ollama" && !/^https?:\/\//u.test(normalizedBaseUrl)) {
      await rm(directory, { recursive: true, force: true });
      throw new BridgeError("provider_configuration", 400);
    }
    const providerConfig = {
      models: [{ id: model, maxTokens: Math.max(256, Math.min(8000, Number(max_tokens) || 3500)) }],
    };
    if (provider === "ollama") {
      providerConfig.baseUrl = `${normalizedBaseUrl}/v1`;
      providerConfig.api = "openai-completions";
      providerConfig.apiKey = "ollama";
      providerConfig.models[0].contextWindow = Math.max(8192, Math.min(32768, Number(context_tokens) || 8192));
      providerConfig.models[0].reasoning = false;
      providerConfig.models[0].input = ["text"];
      providerConfig.models[0].cost = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
    }
    await writeFile(
      modelsPath,
      JSON.stringify({
        providers: {
          [runtimeProvider]: providerConfig,
        },
      }),
      { mode: 0o600 },
    );
  }

  try {
    const runtime = await sdk.ModelRuntime.create({
      authPath: join(directory, "auth.json"),
      modelsPath,
      refreshOnCreate: false,
    });
    const entry = { runtime, directory, provider: runtimeProvider, model };
    if (cacheable) runtimes.set(cacheKey, entry);
    return entry;
  } catch (error) {
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
}

export async function createPiSession(input, sdk = { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager }) {
  const entry = await runtimeFor(input, sdk);
  const model = entry.runtime.getModel(entry.provider, entry.model);
  if (!model) {
    if (input.provider === "gemini") await rm(entry.directory, { recursive: true, force: true });
    throw new BridgeError("model_missing", 404);
  }

  if (input.provider === "gemini") {
    if (!input.api_key) {
      await rm(entry.directory, { recursive: true, force: true });
      throw new BridgeError("gemini_not_configured", 503);
    }
    await entry.runtime.setRuntimeApiKey("google", input.api_key);
  }

  try {
    const resourceLoader = new sdk.DefaultResourceLoader({
      cwd: entry.directory,
      agentDir: entry.directory,
      noExtensions: true,
      noSkills: true,
      noPromptTemplates: true,
      noThemes: true,
      noContextFiles: true,
      systemPrompt: input.system,
      appendSystemPrompt: [],
    });
    await resourceLoader.reload();
    const { session } = await sdk.createAgentSession({
      cwd: entry.directory,
      agentDir: entry.directory,
      modelRuntime: entry.runtime,
      model,
      thinkingLevel: "off",
      resourceLoader,
      sessionManager: sdk.SessionManager.inMemory(entry.directory),
      settingsManager: sdk.SettingsManager.inMemory({
        retry: { enabled: false, maxRetries: 0, provider: { maxRetries: 0 } },
      }),
      tools: [],
      noTools: "all",
    });
    return {
      session,
      cleanup: async () => {
        if (input.provider === "gemini") {
          await entry.runtime.removeRuntimeApiKey("google").catch(() => {});
          await rm(entry.directory, { recursive: true, force: true });
        }
      },
    };
  } catch (error) {
    if (input.provider === "gemini") {
      await entry.runtime.removeRuntimeApiKey("google").catch(() => {});
      await rm(entry.directory, { recursive: true, force: true });
    }
    throw error;
  }
}

export async function runPiGeneration(input, { emit, signal, createSession = createPiSession, timeoutMs = MAX_TIMEOUT_MS, trace = () => {} }) {
  if (!input || !["ollama", "gemini"].includes(input.provider)) throw new BridgeError("provider_unknown", 400);
  if (typeof input.model !== "string" || !input.model || typeof input.system !== "string" || typeof input.prompt !== "string") {
    throw new BridgeError("invalid_request", 400);
  }
  if (input.provider === "gemini" && !input.api_key) throw new BridgeError("gemini_not_configured", 503);

  const budget = Math.max(1_000, Math.min(timeoutMs, MAX_TIMEOUT_MS));
  let session;
  let unsubscribe;
  let timedOut = false;
  let cancelled = false;
  let streamed = false;
  let deltaCount = 0;
  let outputCharacters = 0;
  let rejectStop;
  const stop = new Promise((_, reject) => { rejectStop = reject; });
  const abortSession = () => {
    cancelled = true;
    void Promise.resolve(session?.abort?.()).catch(() => {});
    rejectStop(new BridgeError("request_cancelled", 499));
  };
  const timer = setTimeout(() => {
    timedOut = true;
    void Promise.resolve(session?.abort?.()).catch(() => {});
    rejectStop(new BridgeError("provider_timeout", 504));
  }, budget);
  if (signal?.aborted) abortSession();
  else signal?.addEventListener("abort", abortSession, { once: true });
  let cleanup;

  try {
    const creation = Promise.resolve(createSession(input)).then((created) => {
      if (cancelled || timedOut) {
        const lateSession = created?.session || created;
        void Promise.resolve(lateSession?.dispose?.()).catch(() => {});
        void Promise.resolve(created?.cleanup?.()).catch(() => {});
        throw new BridgeError(cancelled ? "request_cancelled" : "provider_timeout", cancelled ? 499 : 504);
      }
      if (created?.session) {
        cleanup = created.cleanup;
        return created.session;
      }
      return created;
    });
    session = await Promise.race([creation, stop]);
    if (cancelled || timedOut) throw new BridgeError(cancelled ? "request_cancelled" : "provider_timeout", cancelled ? 499 : 504);
    trace({ event: "pi_session_created" });
    const streamCompletion = new Promise((resolve, reject) => {
      unsubscribe = session.subscribe((event) => {
        if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
          const text = event.assistantMessageEvent.delta;
          if (text) {
            streamed = true;
            deltaCount += 1;
            outputCharacters += text.length;
            trace({ event: "pi_text_delta_received", delta_count: deltaCount, delta_characters: text.length });
            emit({ type: "delta", text });
          }
        }
      });
      trace({ event: "pi_prompt_invoked" });
      Promise.resolve(session.prompt(input.prompt)).then(resolve, reject);
    });
    await Promise.race([streamCompletion, stop]);
    if (!streamed) throw new BridgeError("provider_empty", 502);
    emit({ type: "done" });
    trace({ event: "pi_generation_completed", delta_count: deltaCount, output_characters: outputCharacters });
  } catch (error) {
    if (cancelled) throw new BridgeError("request_cancelled", 499);
    if (timedOut) throw new BridgeError("provider_timeout", 504);
    if (error instanceof BridgeError) throw error;
    throw safeProviderError(error, input.provider);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abortSession);
    unsubscribe?.();
    await Promise.resolve(session?.dispose?.()).catch(() => {});
    await Promise.resolve(cleanup?.()).catch(() => {});
  }
}

async function readJson(request) {
  let body = "";
  for await (const part of request) {
    body += part;
    if (Buffer.byteLength(body) > MAX_BODY_BYTES) throw new BridgeError("request_too_large", 413);
  }
  try {
    return JSON.parse(body);
  } catch {
    throw new BridgeError("invalid_request", 400);
  }
}

function sendFrame(response, frame) {
  if (!response.destroyed && !response.writableEnded) response.write(`${JSON.stringify(frame)}\n`);
}

export function createPiServer(options = {}) {
  const createSession = options.createSession || createPiSession;
  const defaultTimeoutMs = options.timeoutMs || MAX_TIMEOUT_MS;
  const logger = options.logger || ((entry) => process.stdout.write(`${JSON.stringify(entry)}\n`));
  const server = createServer(async (request, response) => {
    if (request.method === "GET" && request.url === "/healthz") {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ status: "ok", agent: "pi-coding-agent-sdk" }));
      return;
    }
    if (request.method !== "POST" || request.url !== "/generate") {
      response.writeHead(404).end();
      return;
    }

    const controller = new AbortController();
    response.on("close", () => {
      if (!response.writableEnded) controller.abort();
    });
    let requestTrace;
    try {
      const input = await readJson(request);
      const traceId = randomUUID();
      requestTrace = (entry) => logger({ trace_id: traceId, provider: input.provider, model: input.model, ...entry });
      requestTrace({ event: "pi_request_received" });
      response.writeHead(200, {
        "content-type": "application/x-ndjson; charset=utf-8",
        "cache-control": "no-cache, no-transform",
        "x-accel-buffering": "no",
      });
      await runPiGeneration(input, {
        emit: (frame) => sendFrame(response, frame),
        signal: controller.signal,
        createSession,
        timeoutMs: Math.min(defaultTimeoutMs, Number(input.timeout_ms) || defaultTimeoutMs),
        trace: requestTrace,
      });
      response.end();
    } catch (error) {
      requestTrace?.({ event: "pi_generation_failed", error_category: error?.code || "provider_error" });
      if (response.headersSent) {
        sendFrame(response, { type: "error", code: error?.code || "provider_error" });
        response.end();
      } else {
        const status = error instanceof BridgeError ? error.status : 500;
        response.writeHead(status, { "content-type": "application/json" });
        response.end(JSON.stringify({ code: error?.code || "provider_error" }));
      }
    }
  });
  server.on("close", () => {
    void disposePiRuntimes();
  });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = createPiServer();
  server.listen(PORT, HOST, () => process.stdout.write(`Pi bridge listening on ${HOST}:${PORT}\n`));
  const shutdown = () => server.close(() => process.exit(0));
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}
