import { expect, test } from "@playwright/test";

test("question → evidence → artifact, model choice, and responsive layout", async ({
  page,
}, info) => {
  const source = {
    citation: "S1",
    source_id: "source",
    chunk_id: "chunk",
    guest: "Fixture guest",
    episode_title: "Product-market fit · UI test fixture",
    excerpt:
      "A deterministic test excerpt about focusing on a customer segment.",
    source_url: "https://example.org/episode",
    publish_date: "2025-01-01",
    revision: "fixture123",
    path: "episodes/fixture/transcript.md",
    repository: "https://example.org",
  };
  const session = {
    id: "session",
    title: "New conversation",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  const artifact = {
    id: "artifact",
    title: "A focused product brief",
    type: "markdown",
    content:
      "# A focused product brief\n\n## Start with a segment\n\nMake the next decision explicit. [S1]",
    word_count: 18,
    source_ids: ["source"],
    evidence: [source],
  };
  const htmlArtifact = {
    ...artifact,
    id: "html-artifact",
    type: "html",
    content:
      "<!doctype html><html><head><title>A focused product brief</title></head><body><h1>Separate HTML document</h1><p>A useful idea [S1]</p></body></html>",
  };
  let created = false;
  const messages: any[] = [],
    artifacts: any[] = [];
  await page.route("**/readyz", (route) =>
    route.fulfill({
      json: {
        status: "ready",
        checks: { database: true, vector: true, corpus: true },
        library: { sources: 20, chunks: 1400 },
        providers: [
          {
            id: "ollama",
            name: "Ollama",
            model: "qwen2.5:3b",
            available: true,
            message: "Runs on this machine",
          },
          {
            id: "gemini",
            name: "Gemini",
            model: "gemini-2.5-flash-lite",
            available: true,
            verified: false,
            message:
              "Credentials and model access checked; generation is checked when used.",
          },
        ],
      },
    }),
  );
  await page.route("**/api/sessions", (route) => {
    if (route.request().method() === "POST") {
      created = true;
      return route.fulfill({ status: 201, json: session });
    }
    return route.fulfill({ json: created ? [session] : [] });
  });
  await page.route("**/api/sessions/session", (route) =>
    route.fulfill({ json: { ...session, messages, artifacts } }),
  );
  await page.route("**/api/chat/stream", (route) => {
    const input = route.request().postDataJSON();
    const isArtifact = input.message.includes("Markdown and HTML");
    const response = {
      id: `assistant-${messages.length}`,
      role: "assistant",
      content: isArtifact
        ? "Created your brief."
        : "Focus on a specific customer segment. [S1]",
      model: "qwen2.5:3b",
      metadata: {
        status: "complete",
        sources: [source],
        ...(isArtifact ? { artifact_ids: ["artifact", "html-artifact"] } : {}),
      },
    };
    messages.push(
      {
        id: `user-${messages.length}`,
        role: "user",
        content: input.message,
        metadata: {},
      },
      response,
    );
    if (isArtifact) artifacts.push(artifact, htmlArtifact);
    const events = [
      ["status", { message: "Searching Lenny’s library" }],
      ["delta", { text: response.content }],
      ...(isArtifact
        ? [
            ["artifact", artifact],
            ["artifact", htmlArtifact],
          ]
        : []),
      ["done", { message: response }],
    ];
    return route.fulfill({
      contentType: "text/event-stream",
      body: events
        .map(
          ([event, data]) =>
            `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
        )
        .join(""),
    });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(
    "The Lenny",
  );
  await expect(
    page.getByRole("button", { name: "Send message" }),
  ).toBeDisabled();
  await page.screenshot({
    path: `test-results/${info.project.name}-empty.png`,
    fullPage: true,
  });
  await page
    .getByRole("textbox", { name: "Ask Lenny a question" })
    .fill("How should I find product-market fit?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText("Focus on a specific customer segment."),
  ).toBeVisible();
  await page.getByRole("button", { name: /Grounded in 1 source/ }).click();
  await expect(
    page.getByRole("complementary", { name: "Source evidence" }),
  ).toBeVisible();
  await expect(page.getByText(source.excerpt)).toBeVisible();
  await page.screenshot({
    path: `test-results/${info.project.name}-sources.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "Close sources" }).click();
  await page
    .getByRole("textbox", { name: "Ask Lenny a question" })
    .fill("Create Markdown and HTML about this topic.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByRole("complementary", { name: "Artifact viewer" }),
  ).toBeVisible();
  await expect(page.locator("iframe")).toHaveAttribute("sandbox", "");
  await expect(
    page
      .frameLocator("iframe")
      .getByRole("heading", { name: "Separate HTML document" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "HTML", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("<!doctype html>");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download artifact" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.html$/);
  await page.getByRole("button", { name: "Close artifact" }).click();
  await page.getByRole("button", { name: /Markdown artifact/ }).click();
  await page.getByRole("tab", { name: "Markdown" }).click();
  await expect(page.getByRole("tabpanel")).toContainText(
    "# A focused product brief",
  );
  await page.getByRole("tab", { name: "Preview" }).click();
  await page.screenshot({
    path: `test-results/${info.project.name}-artifact.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "Close artifact" }).click();
  await page.reload();
  if (info.project.name === "mobile")
    await page.getByRole("button", { name: "Open navigation" }).click();
  await page.locator('[data-session-id="session"]').click();
  await expect(
    page.getByRole("button", { name: /Markdown artifact/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /HTML artifact/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /HTML artifact/ }).click();
  await expect(
    page
      .frameLocator("iframe")
      .getByRole("heading", { name: "Separate HTML document" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close artifact" }).click();
  await page.getByRole("button", { name: "Local · Ollama" }).click();
  await page.getByRole("button", { name: /Gemini · Cloud/ }).click();
  await expect(
    page.getByRole("button", { name: "Cloud · Gemini" }),
  ).toBeVisible();
  await expect(
    page.locator(".provider-button .connection-dot"),
  ).not.toHaveClass(/online/);
  await page.getByRole("button", { name: "Cloud · Gemini" }).click();
  await page.getByRole("button", { name: /Ollama · Local/ }).click();
  await expect(
    page.getByRole("button", { name: /Markdown artifact/ }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("only supported providers are listed and a missing Gemini key is disabled", async ({
  page,
}) => {
  await page.route("**/api/sessions", (route) => route.fulfill({ json: [] }));
  await page.route("**/readyz", (route) =>
    route.fulfill({
      json: {
        status: "ready",
        checks: { database: true, vector: true, corpus: true },
        library: { sources: 20, chunks: 2266 },
        providers: [
          {
            id: "ollama",
            model: "local-model",
            available: true,
            verified: true,
          },
          {
            id: "gemini",
            model: "gemini-model",
            available: false,
            verified: false,
            state: "unconfigured",
            message: "Set GEMINI_API_KEY in .env and restart the API.",
          },
        ],
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Local · Ollama" }).click();
  await expect(
    page.getByRole("button", { name: /Gemini · Cloud/ }),
  ).toBeDisabled();
  await expect(page.getByRole("button", { name: /Claude/i })).toHaveCount(0);
  await expect(
    page.getByText("Set GEMINI_API_KEY in .env and restart the API."),
  ).toBeVisible();
});

test("restores the active session and its memory after a browser reload", async ({
  page,
}) => {
  const session = {
    id: "memory-session",
    title: "Elena income question",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  const messages: any[] = [];
  const requestSessionIds: string[] = [];
  await page.route("**/readyz", (route) =>
    route.fulfill({
      json: {
        status: "ready",
        checks: { database: true, vector: true, corpus: true },
        library: { sources: 20, chunks: 2266 },
        providers: [
          { id: "ollama", name: "Ollama", model: "fixture", available: true },
        ],
        default_provider: "ollama",
      },
    }),
  );
  await page.route("**/api/sessions", (route) => {
    if (route.request().method() === "POST")
      return route.fulfill({ status: 201, json: session });
    return route.fulfill({ json: [session] });
  });
  await page.route("**/api/sessions/memory-session", (route) =>
    route.fulfill({ json: { ...session, messages, artifacts: [] } }),
  );
  await page.route("**/api/chat/stream", (route) => {
    const request = route.request().postDataJSON();
    requestSessionIds.push(request.session_id);
    const response = {
      id: `assistant-${messages.length}`,
      role: "assistant",
      content: request.message.includes("What conclusion")
        ? "The earlier conclusion is preserved in this session."
        : "The amount was not established.",
      model: "fixture",
      metadata: { status: "complete", sources: [] },
    };
    messages.push(
      {
        id: `user-${messages.length}`,
        role: "user",
        content: request.message,
        metadata: {},
      },
      response,
    );
    return route.fulfill({
      contentType: "text/event-stream",
      body: [
        `event: delta\ndata: ${JSON.stringify({ text: response.content })}\n\n`,
        `event: done\ndata: ${JSON.stringify({ message: response })}\n\n`,
      ].join(""),
    });
  });

  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "Ask Lenny a question" });
  await composer.fill(
    "According to Lenny's Podcast transcripts, what was Elena Verna's exact personal income in March 2025?",
  );
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText("The amount was not established.")).toBeVisible();

  await page.addInitScript(() => {
    const state = window as typeof window & {
      __outerOverflowSeen?: boolean;
      __outerOverflowSamples?: number;
    };
    const sample = () => {
      const root = document.documentElement;
      if (root.scrollHeight > root.clientHeight + 1) {
        state.__outerOverflowSeen = true;
      }
      state.__outerOverflowSamples = (state.__outerOverflowSamples ?? 0) + 1;
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  await page.reload();
  await expect(page.getByText("The amount was not established.")).toBeVisible();
  await page.waitForFunction(() => (window as any).__outerOverflowSamples >= 5);
  expect(
    await page.evaluate(() => (window as any).__outerOverflowSeen ?? false),
  ).toBe(false);
  await composer.fill(
    "What conclusion did we reach about Elena Verna's exact personal income in March 2025?",
  );
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText("The earlier conclusion is preserved in this session."),
  ).toBeVisible();
  expect(requestSessionIds).toEqual(["memory-session", "memory-session"]);
});

test("service errors are actionable", async ({ page }) => {
  await page.route("**/api/sessions", (route) =>
    route.fulfill({
      status: 503,
      json: {
        error: {
          message:
            "The conversation store is unavailable. Check PostgreSQL and retry.",
        },
      },
    }),
  );
  await page.route("**/readyz", (route) =>
    route.fulfill({
      status: 503,
      json: {
        status: "degraded",
        checks: { database: false },
        library: { sources: 0, chunks: 0 },
        providers: [],
      },
    }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Check PostgreSQL");
  await expect(
    page.getByRole("button", { name: "Retry connection" }),
  ).toBeVisible();
});
