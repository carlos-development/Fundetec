'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const test = require('node:test');

test('Libro y cuatro vistas previas, sin JavaScript y sin contabilizacion implicita', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const base = process.env.EDU_LEDGER_BASE;
    const context = await browser.newContext({ javaScriptEnabled: false, locale: 'es-CO' });
    await context.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_LEDGER_SESSION, url: base }]);
    const page = await context.newPage();
    fs.mkdirSync(process.env.EDU_LEDGER_SCREENSHOTS, { recursive: true });
    for (const [name, url] of Object.entries(JSON.parse(process.env.EDU_LEDGER_PATHS))) {
        assert.equal((await page.goto(base + url)).status(), 200, name);
        if (name.match(/^0[2-5]/)) {
            assert.equal(await page.getByText('SIMULACION_NO_APROBADA', { exact: true }).count(), 1,
                name + ': ' + await page.locator('[role="alert"]').allTextContents());
            assert.equal(await page.getByRole('button', { name: 'Contabilizar con politica aprobada' }).count(), 0);
            await page.locator('details').first().evaluate(e => { e.open = true; });
        }
        for (const width of [375, 390, 1440]) {
            await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
            await page.evaluate(() => window.scrollTo(0, 0));
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name} ${width}`);
            await page.screenshot({ path: path.join(process.env.EDU_LEDGER_SCREENSHOTS, `${name}-${width}.png`), fullPage: true });
        }
    }
});
