'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('Aviso de plan: logo oficial, CTA autenticado y sin red externa', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const context = await browser.newContext({ javaScriptEnabled: false });
    const logo = path.resolve(__dirname, '../../../static/images/logo-dark.png');
    await context.route('**/*', route => route.request().url() === 'https://portal.example.test/static/images/logo-dark.png'
        ? route.fulfill({ path: logo, contentType: 'image/png' }) : route.abort());
    const page = await context.newPage();
    const directory = process.env.EDU_PLAN_EMAIL_PREVIEWS;
    await page.setContent(fs.readFileSync(path.join(directory, 'plan-contractual-disponible.html'), 'utf8'));
    const image = page.getByRole('img', { name: 'Aprobado', exact: true });
    await image.waitFor();
    assert.equal(await image.evaluate(img => img.complete && img.naturalWidth > 0), true);
    const cta = page.getByRole('link', { name: 'Ver mi plan de pagos', exact: true });
    assert.match(await cta.getAttribute('href'), /^https:\/\/portal\.example\.test\/financiacion-educativa\/solicitudes\/.+\/finanzas\/$/);
    for (const width of [375, 600]) {
        await page.setViewportSize({ width, height: 844 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        await page.screenshot({ path: path.join(directory, `plan-contractual-disponible-${width}.png`), fullPage: true });
    }
});
