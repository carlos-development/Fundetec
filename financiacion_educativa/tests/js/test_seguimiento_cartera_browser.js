'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

test('Seguimiento operativo y estudiante con navegacion nativa', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const base = process.env.EDU_FOLLOWUP_BASE;
    const context = await browser.newContext({ javaScriptEnabled: false, locale: 'es-CO', reducedMotion: 'reduce' });
    await context.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_FOLLOWUP_SESSION, url: base }]);
    const page = await context.newPage();
    async function capture(name, current = page) {
        for (const width of [375, 390, 1440]) {
            await current.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
            await current.evaluate(() => window.scrollTo(0, 0));
            assert.equal(await current.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name}: overflow ${width}`);
            assert.equal(await current.locator('.edu-navbar-logo img').evaluate(img => img.complete && img.naturalWidth > 0), true);
            await current.screenshot({ path: path.join(process.env.EDU_FOLLOWUP_SCREENSHOTS, `${name}-${width}.png`), fullPage: true });
        }
    }
    assert.equal((await page.goto(base + process.env.EDU_FOLLOWUP_PATH)).status(), 200);
    await page.getByRole('heading', { name: 'Seguimiento de cartera', exact: true }).waitFor();
    const indicators = await page.locator('[data-indicador]').evaluateAll(items => items.map(a => ({
        url: a.getAttribute('href'), count: a.querySelector('strong').textContent,
    })));
    await capture('01-resumen');
    for (const item of indicators) {
        assert.equal((await page.goto(new URL(item.url, base).href)).status(), 200);
        assert.equal((await page.locator('#followup-total').innerText()).startsWith(item.count), true);
    }
    await page.goto(base + process.env.EDU_FOLLOWUP_PATH);
    await page.locator('#id_institucion').selectOption(process.env.EDU_FOLLOWUP_INSTITUTION);
    await page.locator('#id_estado_operativo').selectOption('INCOME');
    await page.getByRole('button', { name: 'Consultar', exact: true }).click();
    await page.getByRole('link', { name: 'Por obligaci\u00f3n', exact: true }).click();
    assert.equal(await page.locator('.edu-ops-inbox tbody tr').count(), 1);
    await capture('02-bandeja-filtrada');
    await page.getByRole('link', { name: 'Abrir caso', exact: true }).click();
    await page.getByRole('heading', { name: process.env.EDU_FOLLOWUP_REFERENCE, exact: true }).waitFor();
    assert.equal(await page.getByText('Requiere revisi\u00f3n', { exact: true }).count(), 1);
    await capture('03-detalle-obligacion');
    const movement = await page.getByRole('link', { name: 'Ver ingreso por aplicar', exact: true }).getAttribute('href');
    await page.goto(new URL(movement, base).href);
    await page.getByRole('link', { name: 'Volver al seguimiento filtrado', exact: true }).click();
    assert.equal(new URL(page.url()).searchParams.get('estado_operativo'), 'INCOME');
    assert.equal(new URL(page.url()).searchParams.get('vista'), 'obligaciones');
    await page.goto(base + process.env.EDU_FOLLOWUP_PATH + '?q=NO-RESULTS');
    assert.equal((await page.locator('#followup-total').innerText()).startsWith('0 cuotas'), true);
    await capture('05-vacio');
    assert.equal((await page.goto(base + process.env.EDU_FOLLOWUP_PATH + '?desde=invalid')).status(), 400);
    assert.equal(await page.locator('[data-indicador]').count(), 0);
    await capture('06-filtro-invalido');
    const student = await browser.newContext({ javaScriptEnabled: false, locale: 'es-CO' });
    t.after(() => student.close());
    await student.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
    await student.addCookies([{ name: 'sessionid', value: process.env.EDU_FOLLOWUP_PAYER_SESSION, url: base }]);
    const studentPage = await student.newPage();
    await studentPage.goto(base + process.env.EDU_FOLLOWUP_FINANCE);
    assert.equal(await studentPage.getByText('Vencida', { exact: true }).count(), 1);
    assert.equal(await studentPage.getByRole('link', { name: 'Pagar y reportar transferencia', exact: true }).count(), 0);
    assert.equal(await studentPage.getByRole('link', { name: 'Descargar plan contractual en PDF', exact: true }).count(), 1);
    for (const forbidden of [process.env.EDU_FOLLOWUP_BANK, process.env.EDU_FOLLOWUP_PRIVATE, 'Seguimiento de cartera']) {
        assert.equal((await studentPage.content()).includes(forbidden), false);
    }
    assert.equal((await student.request.get(base + process.env.EDU_FOLLOWUP_PATH)).status(), 403);
    await capture('04-estudiante', studentPage);
});
