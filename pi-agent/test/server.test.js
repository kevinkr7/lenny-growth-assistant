import assert from "node:assert/strict";
import { once } from "node:events";
import test from "node:test";
import { createPiSession, createPiServer, disposePiRuntimes, runPiGeneration } from "../src/server.js";

const input = {
  provider: "ollama",
  model: "qwen-test",
  ollama_base_url: "http://ollama.local:11434",
  system: "Be concise.",
  prompt: "Say PASS.",
};

function fakeSession(text = "PASS") {
  let listener;
  return {
    prompts: [],
    subscribe(callback) {
      listener = callback;
      return () => { listener = undefined; };
    },
    async prompt(prompt) {
      this.prompts.push(prompt);
      listener?.({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: text } });
    },
    async abort() { this.aborted = true; },
    async dispose() { this.disposed = true; },
  };
}

test("SDK session creation selects the requested model and disables all tools", async () => {
  const calls = {};
  const runtime = {
    getModel(provider, model) {
      calls.selectedModel = [provider, model];
      return { provider, id: model };
    },
  };
  class FakeResourceLoader {
    constructor(options) { calls.loaderOptions = options; }
    async reload() {}
  }
  const session = fakeSession();
  const sdk = {
    ModelRuntime: {
      async create(options) {
        calls.runtimeOptions = options;
        return runtime;
      },
    },
    DefaultResourceLoader: FakeResourceLoader,
    SessionManager: { inMemory: () => ({}) },
    SettingsManager: { inMemory: () => ({}) },
    async createAgentSession(options) {
      calls.sessionOptions = options;
      return { session };
    },
  };
  const created = await createPiSession(input, sdk);
  await created.session.prompt(input.prompt);
  assert.deepEqual(calls.selectedModel, ["ollama", "qwen-test"]);
  assert.equal(calls.sessionOptions.model.id, "qwen-test");
  assert.deepEqual(calls.sessionOptions.tools, []);
  assert.equal(calls.sessionOptions.noTools, "all");
  assert.equal(calls.loaderOptions.systemPrompt, input.system);
  assert.deepEqual(session.prompts, [input.prompt]);
  await created.session.dispose();
  await created.cleanup();
});

test("official Pi SDK creates tool-free sessions for the configured Ollama and Gemini models", async (t) => {
  const ollama = await createPiSession({
    ...input,
    max_tokens: 3500,
    context_tokens: 8192,
  });
  const gemini = await createPiSession({
    ...input,
    provider: "gemini",
    model: "gemini-3.1-flash-lite",
    api_key: "mocked-key-used-only-to-configure-the-runtime",
    max_tokens: 3500,
  });
  t.after(async () => {
    await ollama.session.dispose();
    await gemini.session.dispose();
    await ollama.cleanup();
    await gemini.cleanup();
    await disposePiRuntimes();
  });
  assert.equal(ollama.session.model.provider, "ollama");
  assert.equal(ollama.session.model.id, input.model);
  assert.equal(gemini.session.model.provider, "google");
  assert.equal(gemini.session.model.id, "gemini-3.1-flash-lite");
  assert.equal(ollama.session.model.maxTokens, 3500);
  assert.equal(gemini.session.model.maxTokens, 3500);
  assert.deepEqual(ollama.session.getActiveToolNames(), []);
  assert.deepEqual(gemini.session.getActiveToolNames(), []);
});

test("the bridge HTTP request creates and prompts the Pi session and forwards deltas", async (t) => {
  const session = fakeSession("Pi streamed answer");
  const calls = [];
  const trace = [];
  const server = createPiServer({
    timeoutMs: 5_000,
    logger: (entry) => trace.push(entry),
    createSession: async (request) => {
      calls.push({ provider: request.provider, model: request.model });
      return session;
    },
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => server.close());
  const address = server.address();
  const response = await fetch(`http://127.0.0.1:${address.port}/generate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  const frames = (await response.text()).trim().split("\n").map((line) => JSON.parse(line));
  assert.deepEqual(calls, [{ provider: "ollama", model: "qwen-test" }]);
  assert.deepEqual(session.prompts, [input.prompt]);
  assert.deepEqual(frames, [{ type: "delta", text: "Pi streamed answer" }, { type: "done" }]);
  assert.deepEqual(trace.map(({ event }) => event), [
    "pi_request_received",
    "pi_session_created",
    "pi_prompt_invoked",
    "pi_text_delta_received",
    "pi_generation_completed",
  ]);
  assert.equal(trace[3].delta_characters, "Pi streamed answer".length);
  assert.equal(trace[4].output_characters, "Pi streamed answer".length);
  assert.equal(JSON.stringify(trace).includes(input.prompt), false);
  assert.equal(JSON.stringify(trace).includes("Pi streamed answer"), false);
  assert.equal(session.disposed, true);
});

test("Gemini selection reaches Pi with its configured model and runtime credential", async () => {
  const geminiInput = { ...input, provider: "gemini", model: "gemini-test", api_key: "secret-test-key" };
  const calls = {};
  const runtime = {
    getModel(provider, model) {
      calls.selectedModel = [provider, model];
      return { provider, id: model };
    },
    async setRuntimeApiKey(provider, key) { calls.apiKey = [provider, key]; },
    async removeRuntimeApiKey(provider) { calls.removedCredential = provider; },
  };
  class FakeResourceLoader { async reload() {} }
  const session = fakeSession();
  const sdk = {
    ModelRuntime: { async create() { return runtime; } },
    DefaultResourceLoader: FakeResourceLoader,
    SessionManager: { inMemory: () => ({}) },
    SettingsManager: { inMemory: () => ({}) },
    async createAgentSession(options) { calls.sessionOptions = options; return { session }; },
  };
  const created = await createPiSession(geminiInput, sdk);
  assert.deepEqual(calls.selectedModel, ["google", "gemini-test"]);
  assert.deepEqual(calls.apiKey, ["google", "secret-test-key"]);
  assert.equal(calls.sessionOptions.model.id, "gemini-test");
  await created.cleanup();
  assert.equal(calls.removedCredential, "google");
});

test("provider errors are sanitized and terminate the request", async () => {
  const session = {
    subscribe() { return () => {}; },
    async prompt() { const error = new Error("secret-key and private prompt contents"); error.status = 429; throw error; },
    async dispose() {},
  };
  const frames = [];
  await assert.rejects(
    runPiGeneration({ ...input, provider: "gemini", api_key: "secret-key" }, {
      createSession: async () => session,
      emit: (frame) => frames.push(frame),
      timeoutMs: 5_000,
    }),
    (error) => error.code === "gemini_rate_limit",
  );
  assert.equal(JSON.stringify(frames).includes("secret-key"), false);
  assert.equal(JSON.stringify(frames).includes("private prompt"), false);
});

test("provider errors are sent as a terminal NDJSON frame", async (t) => {
  const session = {
    subscribe() { return () => {}; },
    async prompt() { throw new Error("private prompt and provider response"); },
    async dispose() {},
  };
  const server = createPiServer({ timeoutMs: 5_000, createSession: async () => session });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => server.close());
  const address = server.address();
  const response = await fetch(`http://127.0.0.1:${address.port}/generate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  const frames = (await response.text()).trim().split("\n").map((line) => JSON.parse(line));
  assert.deepEqual(frames, [{ type: "error", code: "ollama_unavailable" }]);
  assert.equal(JSON.stringify(frames).includes("private prompt"), false);
});

test("closing the HTTP stream propagates cancellation to the active Pi session", async (t) => {
  let markPromptStarted;
  const promptStarted = new Promise((resolve) => { markPromptStarted = resolve; });
  let markAborted;
  const sessionAborted = new Promise((resolve) => { markAborted = resolve; });
  const session = {
    subscribe() { return () => {}; },
    prompt() { markPromptStarted(); return new Promise(() => {}); },
    async abort() { this.aborted = true; markAborted(); },
    async dispose() { this.disposed = true; },
  };
  const server = createPiServer({ timeoutMs: 5_000, createSession: async () => session });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => server.close());
  const address = server.address();
  const response = await fetch(`http://127.0.0.1:${address.port}/generate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  await promptStarted;
  await response.body.cancel();
  await sessionAborted;
  assert.equal(session.aborted, true);
  assert.equal(session.disposed, true);
});

test("Pi session timeout aborts and disposes the active session", async () => {
  const session = {
    subscribe() { return () => {}; },
    prompt() { return new Promise(() => {}); },
    async abort() { this.aborted = true; },
    async dispose() { this.disposed = true; },
  };
  await assert.rejects(
    runPiGeneration(input, { createSession: async () => session, emit: () => {}, timeoutMs: 1_000 }),
    (error) => error.code === "provider_timeout",
  );
  assert.equal(session.aborted, true);
  assert.equal(session.disposed, true);
});

test("client cancellation aborts the Pi session", async () => {
  const session = {
    subscribe() { return () => {}; },
    prompt() { return new Promise(() => {}); },
    async abort() { this.aborted = true; },
    async dispose() { this.disposed = true; },
  };
  const controller = new AbortController();
  const generation = runPiGeneration(input, {
    createSession: async () => session,
    emit: () => {},
    signal: controller.signal,
    timeoutMs: 5_000,
  });
  await new Promise((resolve) => setTimeout(resolve, 10));
  controller.abort();
  await assert.rejects(generation, (error) => error.code === "request_cancelled");
  assert.equal(session.aborted, true);
  assert.equal(session.disposed, true);
});
