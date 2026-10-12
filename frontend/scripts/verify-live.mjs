import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';

const reportPath = new URL(`../../${process.argv[2] || 'evals/fixes-live.json'}`, import.meta.url);
const report = JSON.parse(await fs.readFile(reportPath, 'utf8'));
const detail = await (await fetch(`http://127.0.0.1:8000/api/sessions/${report.session_id}`)).json();
if (detail.artifacts.length !== 2) throw new Error('Expected two real persisted artifacts');
const results = [];
const browser = await chromium.launch();
try {
  for (const [name, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport, permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    const openSession = async () => {
      if (name === 'mobile') await page.getByRole('button', { name: 'Open navigation' }).click();
      await page.locator(`[data-session-id="${report.session_id}"]`).click();
      await expect(page.locator('.artifact-card')).toHaveCount(2);
    };
    await page.goto('http://127.0.0.1:5174');
    await openSession();
    for (const artifact of detail.artifacts) {
      const label = artifact.type === 'html' ? 'HTML' : 'Markdown';
      await page.getByRole('button', { name: new RegExp(`${label} artifact`) }).click();
      if (artifact.type === 'html') {
        await expect(page.locator('iframe')).toHaveAttribute('sandbox', '');
        await expect(page.frameLocator('iframe').locator('h1')).toContainText(artifact.title);
      } else await expect(page.getByRole('tabpanel').locator('h1')).toContainText(artifact.title);
      await page.screenshot({ path: `../docs/screenshots/fixes-${name}-${artifact.type}.png`, fullPage: true });
      if (name === 'desktop') {
        await page.getByRole('button', { name: 'Expand artifact' }).click();
        await expect(page.locator('.artifact-pane')).toHaveClass(/expanded/);
        await page.getByRole('button', { name: 'Collapse artifact' }).click();
      }
      await page.getByRole('tab', { name: label, exact: true }).click();
      expect(await page.getByRole('tabpanel').locator('pre').textContent()).toBe(artifact.content);
      await page.getByRole('button', { name: 'Copy artifact' }).click();
      const copied = await page.evaluate(() => navigator.clipboard.readText());
      expect(copied.replaceAll('\r\n', '\n')).toBe(artifact.content.replaceAll('\r\n', '\n'));
      const download = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Download artifact' }).click();
      const file = await download;
      expect(await fs.readFile(await file.path(), 'utf8')).toBe(artifact.content);
      expect(file.suggestedFilename()).toMatch(artifact.type === 'html' ? /\.html$/ : /\.md$/);
      await page.getByRole('button', { name: 'Close artifact' }).click();
      results.push({ viewport: name, artifact_id: artifact.id, type: artifact.type, preview_source_copy_download: true, expanded: name === 'desktop' });
    }
    await page.reload();
    await openSession();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await context.close();
  }
} finally { await browser.close(); }
await fs.writeFile(new URL('../../evals/fixes-browser.json', import.meta.url), JSON.stringify({ session_id: report.session_id, scope: report.scope || 'Long-form corrective walkthrough', real_api: true, reload_persistence: true, results }, null, 2));
console.log(JSON.stringify({ checks: results.length, passed: true }));
