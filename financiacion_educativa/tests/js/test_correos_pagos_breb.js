'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('Correos BRE-B: muestras sinteticas completas sin recursos externos', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const context = await browser.newContext({ javaScriptEnabled: false });
    await context.route('**/*', r => r.abort());
    const page = await context.newPage();
    for (const name of ['01-reporte-recibido', '02-ingreso-confirmado', '03-pago-aplicado']) {
        const html = fs.readFileSync(path.join(process.env.EDU_BREB_EMAIL_PREVIEWS, `${name}.html`), 'utf8');
        await page.setContent(html);
        assert.equal(await page.locator('h1').count(), 1);
        for (const width of [375, 600]) {
            await page.setViewportSize({ width, height: 844 });
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            await page.screenshot({ path: path.join(process.env.EDU_BREB_EMAIL_PREVIEWS, `${name}-${width}.png`), fullPage: true });
        }
    }
});
