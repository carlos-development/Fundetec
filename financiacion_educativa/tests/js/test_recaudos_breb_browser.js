'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

test('BRE-B movil: QR, llave, reporte, estado y bandeja', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const base = process.env.EDU_BREB_BASE;
    const context = await browser.newContext({ locale: 'es-CO', reducedMotion: 'reduce', permissions: ['clipboard-read', 'clipboard-write'] });
    await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_BREB_SESSION, url: base }]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));
    const viewports = [{ width: 375, height: 667 }, { width: 390, height: 844 }, { width: 1280, height: 900 }];
    for (const viewport of viewports) {
        await page.setViewportSize(viewport);
        for (const [name, url] of [['finanzas', process.env.EDU_BREB_FINANCE], ['reportar', process.env.EDU_BREB_FORM], ['estado', process.env.EDU_BREB_STATE]]) {
            assert.equal((await page.goto(base + url)).status(), 200);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name);
            if (name === 'finanzas') {
                assert.equal(await page.getByRole('link', { name: 'Pagar y reportar transferencia' }).getAttribute('href'), process.env.EDU_BREB_FORM);
            }
            if (name === 'reportar') {
                assert.equal(await page.locator('.edu-recaudos-qr').isVisible(), true);
                assert.equal(await page.locator('.edu-recaudos-qr').evaluate(img => img.complete && img.naturalWidth > 0), true);
                await page.getByRole('button', { name: 'Copiar llave' }).click();
                await page.getByRole('status').filter({ hasText: 'Llave copiada.' }).waitFor();
                assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'LLAVE-SINTETICA');
                assert.equal(await page.getByRole('link', { name: 'Descargar QR' }).isVisible(), viewport.width > 700);
                await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, `qr-${viewport.width}.png`), fullPage: true });
                const cta = page.getByRole('link', { name: 'Pagar con BRE-B', exact: true });
                assert.equal(await cta.evaluate(el => getComputedStyle(el).color), 'rgb(255, 255, 255)');
                await cta.click();
                assert.equal(await page.evaluate(() => document.activeElement.id), 'pagar-breb');
                assert.equal(await page.locator('[name="monto"]').isVisible(), false);
            }
            await page.evaluate(() => scrollTo(0, 0));
            await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, `${name}-${viewport.width}.png`), fullPage: true });
            if (name === 'reportar') {
                await page.locator('#reportar-transferencia > summary').click();
                await page.waitForFunction(() => document.activeElement.id === 'formulario-reporte');
                assert.equal(await page.locator('[name="proposito"]').isVisible(), true);
                assert.equal(await page.locator('[name="referencia"]').isVisible(), true);
                await page.evaluate(() => scrollTo(0, 0));
                await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, `formulario-${viewport.width}.png`), fullPage: true });
            }
        }
    }
    await page.goto(base + process.env.EDU_BREB_FORM);
    await page.locator('#reportar-transferencia > summary').click();
    assert.equal(await page.locator('[name="proposito"]').inputValue(), 'INSTALLMENT');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('[name="monto"]').fill('-1');
    await page.locator('[name="archivo"]').setInputFiles(process.env.EDU_BREB_RECEIPT);
    // Exercise server errors independently of the browser's native negative-amount guard.
    await page.locator('#formulario-reporte').evaluate(form => { form.noValidate = true; });
    const [invalidStudent] = await Promise.all([
        page.waitForResponse(response => response.request().method() === 'POST'),
        page.getByRole('button', { name: 'Enviar reporte' }).click(),
    ]);
    assert.equal(invalidStudent.status(), 200);
    assert.equal(await page.locator('#reportar-transferencia').evaluate(el => el.open), true);
    assert.equal(await page.locator('[name="monto"]').inputValue(), '-1');
    await page.getByRole('alert').waitFor();
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, 'error-estudiante-390.png'), fullPage: true });
    await page.locator('[name="monto"]').fill('125000.25');
    await page.locator('[name="archivo"]').setInputFiles(process.env.EDU_BREB_RECEIPT);
    assert.match(await page.locator('[name="fecha"]').inputValue(), /^\d{4}-\d{2}-\d{2}$/);
    const [posted] = await Promise.all([page.waitForRequest(request => request.method() === 'POST'), page.getByRole('button', { name: 'Enviar reporte' }).click()]);
    assert.match(posted.postData(), /name="monto"\r?\n\r?\n125000\.25/);
    await page.waitForURL('**/pagos/*/');
    assert.equal(await page.getByRole('heading', { name: 'Revision pendiente' }).count(), 1);
    await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, 'enviado-390.png'), fullPage: true });
    // Evita que el scroll suave desplace el toque automatizado con JS deshabilitado.
    const nojs = await browser.newContext({ locale: 'es-CO', javaScriptEnabled: false, reducedMotion: 'reduce', isMobile: true, hasTouch: true, viewport: { width: 375, height: 667 } });
    t.after(() => nojs.close());
    await nojs.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
    await nojs.addCookies([{ name: 'sessionid', value: process.env.EDU_BREB_SESSION, url: base }]);
    const basic = await nojs.newPage();
    assert.equal((await basic.goto(base + process.env.EDU_BREB_FORM)).status(), 200);
    assert.equal(await basic.locator('#breb-key').inputValue(), 'LLAVE-SINTETICA');
    await basic.locator('#reportar-transferencia > summary').tap();
    assert.equal(await basic.locator('[name="monto"]').isVisible(), true);
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
    // HTTP redirects may preserve the fragment from the accessible form anchor.
    await basic.waitForURL(url => /\/pagos\/[0-9a-f-]+\/$/.test(url.pathname));
    assert.equal(await basic.getByRole('heading', { name: 'Revision pendiente' }).count(), 1);
    await basic.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, 'sin-js-375.png'), fullPage: true });
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_BREB_OPERATOR_SESSION, url: base }]);
    for (const viewport of viewports) {
        await page.setViewportSize(viewport);
        for (const [name, url] of [['bandeja', '/financiacion-educativa/operaciones/recaudos/'], ['detalle', process.env.EDU_BREB_DETAIL]]) {
            assert.equal((await page.goto(base + url)).status(), 200);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name);
            if (name === 'bandeja') {
                assert.equal(await page.locator('.edu-recaudos-table td').first().evaluate(td => getComputedStyle(td).display), 'table-cell');
                assert.equal(await page.locator('.edu-recaudos-table thead').evaluate(head => getComputedStyle(head).position), 'static');
            }
            if (name === 'detalle') {
                assert.equal(await page.locator('#diferencia-monto').evaluate(el => el.open), false);
                await page.locator('[name="monto"]').fill('90000');
                assert.equal(await page.locator('#diferencia-monto').evaluate(el => el.open), true);
                assert.equal(await page.locator('[name="justificacion_diferencia"]').getAttribute('required'), '');
                await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, `diferencia-${viewport.width}.png`), fullPage: true });
                await page.locator('[name="monto"]').fill('100000.00');
                assert.equal(await page.locator('#diferencia-monto').evaluate(el => el.open), false);
            }
            await page.evaluate(() => scrollTo(0, 0));
            await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, `${name}-${viewport.width}.png`), fullPage: true });
        }
    }
    await nojs.addCookies([{ name: 'sessionid', value: process.env.EDU_BREB_OPERATOR_SESSION, url: base }]);
    await basic.goto(base + process.env.EDU_BREB_DETAIL);
    const confirmation = basic.locator('form[action$="/confirmar/"]');
    await confirmation.locator('[name="referencia"]').fill('REFERENCIA CON ESPACIO');
    await confirmation.locator('[name="evidencia_id"]').selectOption(process.env.EDU_BREB_EVIDENCE);
    await basic.locator('#observacion-adicional summary').click();
    await confirmation.locator('[name="observacion"]').fill('Verificacion sintetica para probar errores.');
    const [invalidPost] = await Promise.all([
        basic.waitForResponse(response => response.request().method() === 'POST'),
        confirmation.getByRole('button', { name: 'Confirmar ingreso bancario' }).tap(),
    ]);
    assert.equal(invalidPost.status(), 200);
    await basic.getByRole('alert').waitFor();
    assert.equal(await basic.locator('[name="referencia"]').inputValue(), 'REFERENCIA CON ESPACIO');
    assert.equal(await basic.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await basic.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, 'error-operador-sin-js-375.png'), fullPage: true });
    // Sin JS el servidor abre la justificacion cuando detecta una diferencia.
    await basic.locator('[name="monto"]').fill('90000');
    await basic.locator('[name="referencia"]').fill('BANCO-BROWSER-UX');
    await Promise.all([
        basic.waitForResponse(response => response.request().method() === 'POST'),
        basic.getByRole('button', { name: 'Confirmar ingreso bancario' }).tap(),
    ]);
    assert.equal(await basic.locator('#diferencia-monto').getAttribute('open'), '');
    assert.equal(await basic.locator('[name="monto"]').inputValue(), '90000');
    await basic.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, 'diferencia-sin-js-375.png'), fullPage: true });
    const retry = basic.locator('form[action$="/confirmar/"]');
    assert.equal(await retry.locator('[name="evidencia_id"]').inputValue(), process.env.EDU_BREB_EVIDENCE);
    assert.equal(await retry.locator('[name="observacion"]').getAttribute('required'), null);
    await retry.locator('[name="referencia"]').fill('BANCO-BROWSER-UX');
    await retry.locator('[name="monto"]').fill('100000');
    await retry.locator('[name="observacion"]').fill('');
    const [confirmed] = await Promise.all([
        basic.waitForResponse(response => response.request().method() === 'POST'),
        retry.getByRole('button', { name: 'Confirmar ingreso bancario' }).tap(),
    ]);
    assert.equal(confirmed.status(), 302);
    await basic.waitForURL(base + process.env.EDU_BREB_DETAIL);
    await basic.getByRole('heading', { name: /Ingreso confirmado pendiente/ }).waitFor();
    await basic.getByText('Sin observacion adicional registrada.', { exact: true }).waitFor();
    await basic.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, 'confirmado-sin-js-375.png'), fullPage: true });
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_BREB_SESSION, url: base }]);
    for (const viewport of viewports) {
        await page.setViewportSize(viewport);
        await page.goto(base + process.env.EDU_BREB_STATE);
        await page.getByRole('heading', { name: 'Transferencia confirmada', exact: true }).waitFor();
        await page.getByText('La aplicaci\u00f3n a tu cuota est\u00e1 pendiente', { exact: true }).waitFor();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        await page.screenshot({ path: path.join(process.env.EDU_BREB_SCREENSHOTS, `ingreso-confirmado-estudiante-${viewport.width}.png`), fullPage: true });
    }
    assert.deepEqual(errors, []);
});
