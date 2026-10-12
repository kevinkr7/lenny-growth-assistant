import { expect, test, type Page } from "@playwright/test";

const session = {
  id: "polish",
  title: "A thoughtful product question",
  created_at: "2026-10-11",
  updated_at: "2026-10-11",
};
const readiness = {
  status: "ready",
  checks: { database: true, vector: true, corpus: true },
  library: { sources: 20, chunks: 2266 },
  default_provider: "ollama",
  providers: [
    {
      id: "ollama",
      name: "Ollama",
      model: "qwen3:4b-instruct",
      available: true,
      verified: true,
      message: "Local server reachable and model installed",
    },
  ],
};
async function setup(page: Page) {
  await page.route("**/readyz", (route) => route.fulfill({ json: readiness }));
  await page.route("**/api/sessions", (route) =>
    route.fulfill({ json: route.request().method() === "POST" ? session : [] }),
  );
  await page.route("**/api/chat/stream", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: `event: done\ndata: ${JSON.stringify({ message: { id: `answer-${Date.now()}`, role: "assistant", content: "## A useful starting point\n\nStart with one customer segment.\n\n- State the problem.\n- Test the riskiest assumption.\n\n> Keep the next step small.\n\n```text\nA readable code example\n```", model: "UI fixture", metadata: { status: "complete" } } })}\n\n`,
    }),
  );
}

test("editorial layout keeps each message role aligned across four viewports", async ({
  page,
}, info) => {
  await setup(page);
  for (const [width, height] of [
    [1440, 900],
    [1280, 800],
    [768, 1024],
    [390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await expect(page.locator(".startup-screen")).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: /The Lenny/ }),
    ).toBeVisible();
    expect(
      await page
        .locator(".chat-scroll")
        .evaluate((el) => el.scrollHeight <= el.clientHeight + 2),
    ).toBe(true);
    await page.screenshot({
      path: `test-results/${info.project.name}-polish-home-${width}.png`,
      animations: "disabled",
    });
    for (const message of [
      "A short question",
      "Please help me reason through this launch. ".repeat(18) +
        "verylongunbrokentext".repeat(20),
    ]) {
      await page
        .getByRole("textbox", { name: "Ask Lenny a question" })
        .fill(message);
      await page.getByRole("button", { name: "Send message" }).click();
      await expect(page.locator(".message.assistant").last()).toContainText(
        "Start with one customer segment.",
      );
      const user = await page.locator(".message.user").last().boundingBox();
      const assistant = await page
        .locator(".message.assistant")
        .last()
        .boundingBox();
      const container = await page.locator(".messages").boundingBox();
      expect(user!.x).toBeGreaterThan(assistant!.x);
      expect(user!.x + user!.width).toBeGreaterThan(
        assistant!.x + assistant!.width,
      );
      expect(user!.x + user!.width).toBeLessThanOrEqual(
        container!.x + container!.width,
      );
      expect(
        await page
          .locator(".chat-scroll")
          .evaluate((el) => el.scrollWidth <= el.clientWidth),
      ).toBe(true);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      if (message === "A short question")
        await page.screenshot({
          path: `test-results/${info.project.name}-polish-short-chat-${width}.png`,
          animations: "disabled",
        });
    }
    await page.screenshot({
      path: `test-results/${info.project.name}-polish-chat-${width}.png`,
      animations: "disabled",
    });
  }
});

test("startup waits for initialization, leaves on failure, and never replays for a new conversation", async ({
  page,
}, info) => {
  await setup(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/readyz", async (route) => {
    await gate;
    await route.abort();
  });
  await page.goto("/");
  await expect(page.getByRole("status")).toContainText(
    "Opening your workspace",
  );
  await expect(page.locator(".app-shell")).toHaveAttribute("inert", "");
  await page.screenshot({
    path: `test-results/${info.project.name}-startup.png`,
    animations: "disabled",
  });
  release();
  await expect(page.locator(".startup-screen")).toHaveCount(0);
  await expect(page.locator(".app-shell")).toHaveCSS(
    "animation-name",
    "workspace-enter",
  );
  await expect(page.locator(".app-shell")).toHaveCSS("opacity", "1");
  await expect(page.getByRole("alert")).toContainText("Cannot reach");
  await page.route("**/readyz", (route) => route.fulfill({ json: readiness }));
  await page
    .getByRole("button", { name: "Retry connection", exact: true })
    .click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  if (await page.getByRole("button", { name: "Open navigation" }).isVisible())
    await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: /New conversation/ }).click();
  await expect(page.locator(".startup-screen")).toHaveCount(0);
});

test("mobile navigation contains focus, dismisses, and respects reduced motion", async ({
  page,
}) => {
  await setup(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "Open navigation" });
  await toggle.click();
  const drawer = page.getByRole("dialog", { name: "Workspace navigation" });
  await expect(drawer).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Close navigation", exact: true }).last(),
  ).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(
    page.getByRole("button", { name: /Personal workspace/ }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(toggle).toBeFocused();
  expect(
    await page
      .locator(".app-shell")
      .evaluate((el) => getComputedStyle(el).animationName),
  ).toBe("none");
  await page.getByRole("button", { name: /Local · Ollama/ }).click();
  await expect(
    page.getByRole("group", { name: "Choose your model" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("group", { name: "Choose your model" }),
  ).toHaveCount(0);
});

test("a stalled initialization recovers within the frontend deadline", async ({
  page,
}) => {
  await setup(page);
  await page.route("**/readyz", () => {});
  await page.goto("/");
  await expect(page.locator(".startup-screen")).toBeVisible();
  await expect(page.locator(".startup-screen")).toHaveCount(0, {
    timeout: 13000,
  });
  await expect(page.getByRole("alert")).toContainText("Cannot reach");
  await expect(
    page.getByRole("button", { name: "Retry connection", exact: true }),
  ).toBeEnabled();
});

test("streaming respects reading position and supports stop, retry, and terminal errors", async ({
  page,
}) => {
  await setup(page);
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = async (input, init) => {
      if (input !== "/api/chat/stream") return original(input, init);
      const body = new ReadableStream({
        start(controller) {
          (window as any).emitChat = (event: string, data: unknown) => {
            controller.enqueue(
              new TextEncoder().encode(
                `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
              ),
            );
            if (event === "error" || event === "done") controller.close();
          };
          init?.signal?.addEventListener(
            "abort",
            () => controller.error(new DOMException("Stopped", "AbortError")),
            { once: true },
          );
        },
      });
      return new Response(body, {
        headers: { "Content-Type": "text/event-stream" },
      });
    };
  });
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "Ask Lenny a question" })
    .fill("Explain this in detail.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByRole("button", { name: "Stop response" }),
  ).toBeVisible();
  await page.evaluate(() =>
    (window as any).emitChat("delta", {
      text: "A streamed paragraph.\n\n".repeat(70),
    }),
  );
  await expect(page.locator(".message.assistant")).toContainText(
    "A streamed paragraph.",
  );
  await expect
    .poll(() => page.locator(".chat-scroll").evaluate((el) => el.scrollTop))
    .toBeGreaterThan(100);
  await page.locator(".chat-scroll").evaluate((el) => {
    el.scrollTop = 0;
    el.dispatchEvent(new Event("scroll"));
  });
  await page.evaluate(() =>
    (window as any).emitChat("delta", { text: "An additional thought." }),
  );
  await expect(page.locator(".message.assistant")).toContainText(
    "An additional thought.",
  );
  expect(
    await page.locator(".chat-scroll").evaluate((el) => el.scrollTop),
  ).toBeLessThan(2);
  await page.getByRole("button", { name: "Stop response" }).click();
  await expect(page.getByRole("alert")).toContainText("Response stopped");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("button", { name: "Stop response" }),
  ).toBeVisible();
  await page.evaluate(() =>
    (window as any).emitChat("error", {
      message: "The test provider is unavailable. Retry shortly.",
    }),
  );
  await expect(page.getByRole("alert")).toContainText(
    "test provider is unavailable",
  );
  await expect(page.getByRole("button", { name: "Stop response" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("button", { name: "Try again" })).toBeEnabled();
});
