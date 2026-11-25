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
    const shares = page.locator('#movimientos .collections-installment-shares');
    const distribution = page.locator('.collections-distribution dl');
    const summaryShares = [
        process.env.EDU_COLLECTIONS_APROBADO_PERIODO,
        process.env.EDU_COLLECTIONS_INSTITUCION_PERIODO,
    ];
    async function assertColumns(list) {
        const [aprobado, institucion] = await list.locator(':scope > div').evaluateAll(parts => (
            parts.map(part => ({ x: part.offsetLeft, y: part.offsetTop }))
        ));
        assert.ok(aprobado.x < institucion.x);
        assert.equal(aprobado.y, institucion.y);
    }
    async function capture(name) {
        for (const width of [320, 375, 390, 1440]) {
            await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
            await page.evaluate(() => window.scrollTo(0, 0));
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name}: overflow ${width}`);
            assert.equal(await page.locator('.edu-navbar-logo img').evaluate(img => img.complete && img.naturalWidth > 0), true);
            if (name === '01-tablero') {
                assert.deepEqual(await distribution.locator('dd').allTextContents(), summaryShares);
                await assertColumns(shares.first());
            }
            await page.screenshot({ path: path.join(process.env.EDU_COLLECTIONS_SCREENSHOTS, `${name}-${width}.png`), fullPage: true });
        }
    }
    assert.equal((await page.goto(base + process.env.EDU_COLLECTIONS_URL)).status(), 200);
    assert.equal(await page.locator('#movements-total').innerText().then(s => s.startsWith('5 movimientos')), true);
    assert.equal(await shares.count(), 3);
    assert.deepEqual(await distribution.locator('dd').allTextContents(), summaryShares);
    assert.deepEqual(await shares.first().locator('dd').allTextContents(), [
        process.env.EDU_COLLECTIONS_APROBADO_MOVIMIENTO,
        process.env.EDU_COLLECTIONS_INSTITUCION_MOVIMIENTO,
    ]);
    assert.equal(await shares.nth(1).getByText('Sin política aprobada', { exact: true }).count(), 2);
    assert.equal(
        await page.locator('#interes-aplicado-periodo').innerText(),
        process.env.EDU_COLLECTIONS_APPLIED_INTEREST
    );
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
    assert.equal(await shares.count(), 0);
    assert.equal((await page.locator('#movements-total').innerText()).includes('$40.000'), true);
    assert.equal((await page.content()).includes('OTRA-INSTITUCION-SINTETICA'), false);
    await capture('03-por-aplicar-institucion');
    await page.getByText('Ver importes por mes bancario', { exact: true }).click();
    assert.deepEqual(await page.locator('.collections-history .collections-installment-shares dd').allTextContents(), summaryShares);
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
