// Inspect persisted real API/model results. No API interception or model fixtures.
import { chromium, expect } from "@playwright/test";
import { readFile, mkdir, writeFile } from "node:fs/promises";
const report = JSON.parse(await readFile("../evals/live-smoke.json", "utf8"));
if (process.argv[2]) report.session_id = process.argv[2];
const reviewName = process.argv[3] || "live-browser-results";
const detail = await (
  await fetch(`http://127.0.0.1:8000/api/sessions/${report.session_id}`)
).json();
if (!detail.artifacts?.length)
  throw new Error("No persisted live artifacts to inspect");
await mkdir("../docs/screenshots", { recursive: true });
const browser = await chromium.launch({ headless: true });
const checks = [];
try {
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
    await page
      .context()
      .grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto("http://127.0.0.1:5174");
    if (viewport.width < 600)
      await page.getByRole("button", { name: "Open navigation" }).click();
    await page.locator(`[data-session-id="${report.session_id}"]`).click();
    await expect(page.locator(".artifact-card")).toHaveCount(
      detail.artifacts.length,
    );
    await page.locator(".grounding-strip").first().click();
    await expect(page.locator(".source-card").first()).toBeVisible();
    await expect(page.locator(".source-card a").first()).toHaveAttribute(
      "href",
      /github\.com\/ChatPRD\/lennys-podcast-transcripts\/blob\//,
    );
    await page.getByRole("button", { name: "Close sources" }).click();
    const name = viewport.width < 600 ? "mobile" : "desktop";
    for (const artifact of detail.artifacts) {
      await page
        .locator(".artifact-card")
        .filter({ hasText: artifact.title })
        .click();
      await expect(
        page.getByRole("complementary", { name: "Artifact viewer" }),
      ).toBeVisible();
      if (artifact.type === "html") {
        await expect(page.locator("iframe")).toHaveAttribute("sandbox", "");
        await expect(page.locator("iframe")).toHaveAttribute(
          "srcdoc",
          /Content-Security-Policy/,
        );
      } else {
        await expect(page.locator(".artifact-body h1")).toBeVisible();
      }
      await page.screenshot({
        path: `../docs/screenshots/${name}-${artifact.type === "markdown" && artifact.word_count >= 1125 ? "essay" : artifact.type}.png`,
      });
      await page
        .getByRole("tab", {
          name: artifact.type === "html" ? "HTML" : "Markdown",
          exact: true,
        })
        .click();
      await expect(page.locator(".artifact-body pre")).toContainText(
        artifact.content.slice(0, 50),
      );
      const downloadEvent = page.waitForEvent("download");
      await page.getByRole("button", { name: "Download artifact" }).click();
      const download = await downloadEvent;
      expect(download.suggestedFilename()).toMatch(
        artifact.type === "html" ? /\.html$/ : /\.md$/,
      );
      expect(await readFile(await download.path(), "utf8")).toBe(
        artifact.content,
      );
      await page.getByRole("button", { name: "Copy artifact" }).click();
      await expect
        .poll(async () =>
          (await page.evaluate(() => navigator.clipboard.readText())).replace(
            /\r\n/g,
            "\n",
          ),
        )
        .toBe(artifact.content.replace(/\r\n/g, "\n"));
      if (viewport.width >= 600) {
        await page.getByRole("button", { name: "Expand artifact" }).click();
        await expect(page.locator(".artifact-pane")).toHaveClass(/expanded/);
        await page.getByRole("button", { name: "Collapse artifact" }).click();
      }
      checks.push({
        viewport: name,
        artifact: artifact.id,
        type: artifact.type,
        words: artifact.word_count,
        preview: true,
        source: true,
        download: true,
        copy: true,
        expand: viewport.width >= 600 ? true : "not shown on phone",
      });
      await page
        .getByRole("button", { name: "Close artifact", exact: true })
        .click();
    }
    await page.reload();
    if (viewport.width < 600)
      await page.getByRole("button", { name: "Open navigation" }).click();
    await page.locator(`[data-session-id="${report.session_id}"]`).click();
    await expect(page.locator(".artifact-card")).toHaveCount(
      detail.artifacts.length,
    );
    await page.close();
  }
  await writeFile(
    `../evals/${reviewName}.json`,
    JSON.stringify(
      {
        timestamp: new Date().toISOString(),
        session_id: report.session_id,
        checks,
        persistence_after_reload: true,
      },
      null,
      2,
    ),
  );
  console.log(
    JSON.stringify({ checks: checks.length, persistence_after_reload: true }),
  );
} finally {
  await browser.close();
}
