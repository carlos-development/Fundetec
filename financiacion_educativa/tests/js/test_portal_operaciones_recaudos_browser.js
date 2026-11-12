'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

test('Operaciones: obligacion, evidencia, ingreso, aplicacion y resultado sin JavaScript', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const base = process.env.EDU_OPS_BASE;
    const context = await browser.newContext({ javaScriptEnabled: false, locale: 'es-CO', reducedMotion: 'reduce' });
    await context.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_OPS_SESSION, url: base }]);
    const page = await context.newPage();
    async function capture(name) {
        for (const width of [375, 390, 1440]) {
            await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name}: overflow ${width}`);
            await page.screenshot({ path: path.join(process.env.EDU_OPS_SCREENSHOTS, `${name}-${width}.png`), fullPage: true });
        }
    }
    assert.equal((await page.goto(base + process.env.EDU_OPS_INBOX)).status(), 200);
    await capture('01-por-verificar');
    await page.goto(base + process.env.EDU_OPS_OBLIGATION);
    await page.getByRole('link', { name: 'Registrar pago recibido', exact: true }).click();
    await page.locator('#id_cuenta_id').selectOption(process.env.EDU_OPS_ACCOUNT);
    await page.locator('#id_monto').fill(process.env.EDU_OPS_AMOUNT);
    await page.locator('#id_fecha').fill(process.env.EDU_OPS_DATE);
    await page.locator('#id_referencia').fill('SYNTHETIC-BROWSER-OPERATIONS');
    await page.locator('#id_archivo').setInputFiles(process.env.EDU_OPS_UPLOAD);
    await page.getByRole('button', { name: 'Registrar evidencia', exact: true }).click();
    assert.equal(await page.locator('#id_referencia').inputValue(), 'SYNTHETIC-BROWSER-OPERATIONS');
    assert.equal(await page.locator('#id_monto').inputValue(), process.env.EDU_OPS_AMOUNT);
    await page.getByText('Revisión pendiente', { exact: true }).waitFor();
    await page.getByText('Lista para verificar', { exact: true }).waitFor();
    await page.getByText('Rechazada por seguridad', { exact: true }).waitFor();
    await page.getByText('Error técnico de verificación', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Consultar estado', exact: true }).click();
    assert.equal(await page.locator('#id_referencia').inputValue(), 'SYNTHETIC-BROWSER-OPERATIONS');
    await capture('02-evidencia-y-datos');
    const [preview] = await Promise.all([
        context.waitForEvent('page'), page.getByRole('link', { name: /Revisar evidencia/ }).click(),
    ]);
    await preview.waitForLoadState();
    assert.equal(await page.locator('#id_referencia').inputValue(), 'SYNTHETIC-BROWSER-OPERATIONS');
    await preview.close();
    await page.locator('#id_evidencia_id').selectOption(process.env.EDU_OPS_EVIDENCE);
    await page.getByRole('button', { name: 'Registrar ingreso bancario', exact: true }).click();
    const detail = page.url();
    await page.reload();
    await page.getByRole('heading', { name: /Aplicar a cuota 1/ }).waitFor();
    await capture('03-aplicar-a-cuota');
    await page.goto(base + process.env.EDU_OPS_INBOX + '?estado=TO_APPLY');
    await capture('04-por-aplicar');
    await page.goto(detail);
    await page.locator('#id_confirmar_cuota').check();
    await page.getByRole('button', { name: /Confirmar aplicación de/ }).click();
    await page.reload();
    await page.getByRole('heading', { name: 'Cuota 1 pagada', exact: true }).waitFor();
    await capture('05-resultado');
    await page.goto(base + process.env.EDU_OPS_INBOX + '?estado=APPLIED');
    await capture('06-aplicados');
    const student = await browser.newContext({ javaScriptEnabled: false });
    t.after(() => student.close());
    await student.addCookies([{ name: 'sessionid', value: process.env.EDU_OPS_PAYER_SESSION, url: base }]);
    assert.equal((await student.request.get(base + process.env.EDU_OPS_INBOX)).status(), 403);
    const evidence = base + process.env.EDU_OPS_INBOX + 'evidencias/' + process.env.EDU_OPS_EVIDENCE + '/';
    assert.equal((await student.request.get(evidence)).status(), 403);
});
