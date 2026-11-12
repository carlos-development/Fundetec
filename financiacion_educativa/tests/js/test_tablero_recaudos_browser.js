'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

test('Tablero operativo: importes y respaldos bancarios sin JavaScript', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const base = process.env.EDU_COLLECTIONS_BASE;
    const context = await browser.newContext({ javaScriptEnabled: false, locale: 'es-CO', reducedMotion: 'reduce' });
    await context.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_COLLECTIONS_SESSION, url: base }]);
    const page = await context.newPage();
    async function capture(name) {
        for (const width of [375, 390, 1440]) {
            await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
            await page.evaluate(() => window.scrollTo(0, 0));
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name}: overflow ${width}`);
            assert.equal(await page.locator('.edu-navbar-logo img').evaluate(img => img.complete && img.naturalWidth > 0), true);
            await page.screenshot({ path: path.join(process.env.EDU_COLLECTIONS_SCREENSHOTS, `${name}-${width}.png`), fullPage: true });
        }
    }
    assert.equal((await page.goto(base + process.env.EDU_COLLECTIONS_URL)).status(), 200);
    assert.equal(await page.locator('#movements-total').innerText().then(s => s.startsWith('5 movimientos')), true);
    const indicators = await page.locator('[data-indicador]').evaluateAll(items => items.map(a => ({
        url: a.getAttribute('href'), amount: a.querySelector('strong').textContent,
        count: Number(a.querySelector('small').textContent.trim().split(' ')[0]),
    })));
    await capture('01-tablero');
    for (const item of indicators) {
        await page.goto(new URL(item.url, base).href);
        const total = await page.locator('#movements-total').innerText();
        assert.equal(total.includes(item.amount), true);
        assert.equal(total.startsWith(`${item.count} movimiento`), true);
        assert.equal(await page.locator('#movimientos tbody tr').count(), item.count);
    }
    await capture('02-aplicados');
    await page.locator('#id_institucion').selectOption(process.env.EDU_COLLECTIONS_INSTITUTION);
    await page.locator('#id_estado').selectOption('TO_APPLY');
    await page.getByRole('button', { name: 'Consultar', exact: true }).click();
    assert.equal(await page.locator('#movimientos tbody tr').count(), 1);
    assert.equal((await page.locator('#movements-total').innerText()).includes('$40.000'), true);
    assert.equal((await page.content()).includes('OTRA-INSTITUCION-SINTETICA'), false);
    await capture('03-por-aplicar-institucion');
    await page.getByText('Ver importes por mes bancario', { exact: true }).click();
    await capture('04-mes-bancario');
    await page.goto(base + process.env.EDU_COLLECTIONS_PATH + '?desde=2020-01-01&hasta=2020-01-31');
    assert.equal(await page.locator('#movements-total').innerText().then(s => s.startsWith('0 movimientos')), true);
    await capture('05-vacio');
    assert.equal((await page.goto(base + process.env.EDU_COLLECTIONS_PATH + '?desde=invalida')).status(), 400);
    assert.equal(await page.locator('[data-indicador]').count(), 0);
    await page.getByRole('alert').waitFor();
    await capture('06-error-filtro');
    const payer = await browser.newContext({ javaScriptEnabled: false });
    t.after(() => payer.close());
    await payer.addCookies([{ name: 'sessionid', value: process.env.EDU_COLLECTIONS_PAYER, url: base }]);
    assert.equal((await payer.request.get(base + process.env.EDU_COLLECTIONS_URL)).status(), 403);
});
