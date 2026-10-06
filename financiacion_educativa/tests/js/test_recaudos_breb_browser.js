'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

test('BRE-B movil: QR, llave, reporte, estado y bandeja', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const base = process.env.EDU_BREB_BASE;
    const context = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'] });
    await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_BREB_SESSION, url: base }]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));
    for (const viewport of [{ width: 375, height: 667 }, { width: 390, height: 844 }]) {
        await page.setViewportSize(viewport);
        for (const [name, url] of [['reportar', process.env.EDU_BREB_FORM], ['estado', process.env.EDU_BREB_STATE]]) {
            assert.equal((await page.goto(base + url)).status(), 200);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name);
            if (name === 'reportar') {
                assert.equal(await page.locator('.edu-recaudos-qr').evaluate(img => img.complete && img.naturalWidth > 0), true);
                await page.getByRole('button', { name: 'Copiar llave' }).click();
                await page.getByRole('status').filter({ hasText: 'Llave copiada.' }).waitFor();
                assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'LLAVE-SINTETICA');
                assert.ok(await page.getByRole('link', { name: 'Descargar QR' }).getAttribute('href'));
            }
            await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, `${name}-${viewport.width}.png`), fullPage: true });
        }
    }
    await page.goto(base + process.env.EDU_BREB_FORM);
    await page.locator('[name="proposito"]').selectOption('INSTALLMENT');
    await page.locator('[name="monto"]').fill('125000');
    await page.locator('[name="archivo"]').setInputFiles(process.env.EDU_BREB_RECEIPT);
    assert.match(await page.locator('[name="fecha"]').inputValue(), /^\d{4}-\d{2}-\d{2}$/);
    await Promise.all([page.waitForURL('**/pagos/*/'), page.getByRole('button', { name: 'Enviar reporte' }).click()]);
    assert.equal(await page.getByRole('heading', { name: 'Revision pendiente' }).count(), 1);
    await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, 'enviado-390.png'), fullPage: true });
    // Evita que el scroll suave desplace el toque automatizado con JS deshabilitado.
    const nojs = await browser.newContext({ javaScriptEnabled: false, reducedMotion: 'reduce', isMobile: true, hasTouch: true, viewport: { width: 375, height: 667 } });
    await nojs.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
    await nojs.addCookies([{ name: 'sessionid', value: process.env.EDU_BREB_SESSION, url: base }]);
    const basic = await nojs.newPage();
    assert.equal((await basic.goto(base + process.env.EDU_BREB_FORM)).status(), 200);
    assert.equal(await basic.locator('#breb-key').inputValue(), 'LLAVE-SINTETICA');
    await basic.locator('[name="monto"]').fill('90000');
    await basic.locator('[name="proposito"]').selectOption('PRINCIPAL');
    await basic.locator('[name="archivo"]').setInputFiles(process.env.EDU_BREB_RECEIPT);
    assert.deepEqual(await basic.locator('.edu-recaudos-form').evaluate(form => Array.from(form.elements)
        .filter(field => field.willValidate && !field.validity.valid).map(field => field.name)), []);
    const [postResponse] = await Promise.all([
        basic.waitForResponse(response => response.request().method() === 'POST'),
        basic.getByRole('button', { name: 'Enviar reporte' }).tap(),
    ]);
    assert.equal(postResponse.status(), 302);
    await basic.waitForURL('**/pagos/*/');
    assert.equal(await basic.getByRole('heading', { name: 'Revision pendiente' }).count(), 1);
    await basic.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, 'sin-js-375.png'), fullPage: true });
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_BREB_OPERATOR_SESSION, url: base }]);
    for (const viewport of [{ width: 375, height: 667 }, { width: 390, height: 844 }]) {
        await page.setViewportSize(viewport);
        for (const [name, url] of [['bandeja', '/financiacion-educativa/operaciones/recaudos/'], ['detalle', process.env.EDU_BREB_DETAIL]]) {
            assert.equal((await page.goto(base + url)).status(), 200);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name);
            await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, `${name}-${viewport.width}.png`), fullPage: true });
        }
    }
    assert.deepEqual(errors, []);
});
