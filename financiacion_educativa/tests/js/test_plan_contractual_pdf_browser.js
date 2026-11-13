'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('Descarga privada del plan contractual desde movil sin JavaScript', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const base = process.env.EDU_PLAN_BASE;
    const context = await browser.newContext({ javaScriptEnabled: false, locale: 'es-CO', reducedMotion: 'reduce' });
    await context.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_PLAN_SESSION, url: base }]);
    const page = await context.newPage();
    for (const width of [375, 390, 1440]) {
        await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
        assert.equal((await page.goto(base + process.env.EDU_PLAN_FINANCE)).status(), 200);
        const button = page.getByRole('link', { name: 'Descargar plan contractual en PDF', exact: true });
        await button.waitFor();
        assert.equal(await button.getAttribute('href'), process.env.EDU_PLAN_DOWNLOAD);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        assert.equal(await page.locator('.edu-navbar-logo img').evaluate(img => img.complete && img.naturalWidth > 0), true);
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: path.join(process.env.EDU_PLAN_SCREENSHOTS, `portal-${width}.png`), fullPage: true });
        const pending = page.waitForEvent('download');
        await button.click();
        const download = await pending;
        assert.equal(download.suggestedFilename(), 'plan-contractual-v1.pdf');
        assert.equal(await download.failure(), null);
        const destination = path.join(process.env.EDU_PLAN_TEMP, `download-${width}.pdf`);
        await download.saveAs(destination);
        assert.equal(fs.readFileSync(destination).subarray(0, 5).toString(), '%PDF-');
    }
    const other = await browser.newContext({ javaScriptEnabled: false });
    t.after(() => other.close());
    await other.addCookies([{ name: 'sessionid', value: process.env.EDU_PLAN_OTHER_SESSION, url: base }]);
    assert.equal((await other.request.get(base + process.env.EDU_PLAN_DOWNLOAD)).status(), 404);
});
