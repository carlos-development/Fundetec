'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

test('Reportes institucionales: filtros, grafica y respaldo sin JavaScript', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const base = process.env.EDU_REPORT_BASE;
    const context = await browser.newContext({ javaScriptEnabled: false, locale: 'es-CO', reducedMotion: 'reduce' });
    await context.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_REPORT_SESSION, url: base }]);
    const page = await context.newPage();
    async function cifras(expected) {
        for (const [codigo, total] of Object.entries(expected)) {
            assert.equal(await page.locator(`[data-indicador="${codigo}"] strong`).innerText(), String(total));
        }
    }
    async function capture(name) {
        for (const width of [375, 390, 1440]) {
            await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
            await page.evaluate(() => window.scrollTo(0, 0));
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name}: overflow ${width}`);
            assert.equal(await page.locator('.edu-navbar-logo img').evaluate(img => img.complete && img.naturalWidth > 0), true);
            await page.screenshot({ path: path.join(process.env.EDU_REPORT_SCREENSHOTS, `${name}-${width}.png`), fullPage: true });
        }
    }
    async function comprobarListado(url, total) {
        assert.equal((await page.goto(new URL(url, base).href)).status(), 200);
        assert.equal((await page.locator('#applications-result-title').innerText()).split(' ')[0], String(total));
        assert.equal(await page.locator('.edu-institution-results tbody tr').count(), total);
        assert.equal(await page.getByRole('link', { name: 'Volver a reportes', exact: true }).count(), 1);
    }
    await page.goto(base + process.env.EDU_REPORT_HOME);
    await page.getByRole('link', { name: 'Reportes', exact: true }).click();
    assert.equal(new URL(page.url()).pathname, process.env.EDU_REPORT_PATH);
    assert.equal((await page.goto(base + process.env.EDU_REPORT_URL)).status(), 200);
    await cifras({ recibidas: 8, aprobadas: 5, autorizadas: 3, pendientes: 2 });
    const links = await page.locator('[data-indicador]').evaluateAll(items => items.map(a => ({
        url: a.getAttribute('href'), total: Number(a.querySelector('strong').textContent),
    })));
    const monthly = await page.locator('.edu-reports-bar').first().getAttribute('href');
    assert.equal(await page.locator('.edu-reports-month').count(), 3);
    await capture('01-reporte');
    await page.getByText('Ver cifras por mes', { exact: true }).click();
    await capture('02-cifras-mensuales');
    for (const link of links) await comprobarListado(link.url, link.total);
    await comprobarListado(monthly, 1);
    await page.goto(base + process.env.EDU_REPORT_URL);
    await page.locator('#id_desde').fill('2026-09-01');
    await page.locator('#id_hasta').fill('2026-09-30');
    await page.locator('#id_programa').selectOption(process.env.EDU_REPORT_COURSE);
    await page.getByRole('button', { name: 'Consultar', exact: true }).click();
    await cifras({ recibidas: 5, aprobadas: 2, autorizadas: 1, pendientes: 1 });
    await capture('03-curso-filtrado');
    await page.locator('[data-indicador="pendientes"]').click();
    assert.equal(await page.locator('.edu-institution-results tbody tr').count(), 1);
    assert.equal(await page.locator('td[data-label="Curso autorizado"] .edu-institution-status').innerText(), 'No');
    await capture('04-listado-respaldo');
    await page.goto(base + process.env.EDU_REPORT_PATH + '?desde=2030-01-01&hasta=2030-01-31');
    await cifras({ recibidas: 0, aprobadas: 0, autorizadas: 0, pendientes: 0 });
    await page.getByRole('heading', { name: 'Sin radicaciones en este rango', exact: true }).waitFor();
    await capture('05-vacio');
    assert.equal((await page.goto(base + process.env.EDU_REPORT_PATH + '?desde=invalid&hasta=2026-09-30')).status(), 400);
    assert.equal(await page.locator('[data-indicador]').count(), 0);
    assert.equal(await page.getByRole('alert').count() > 0, true);
    await capture('06-filtro-invalido');
    assert.equal((await context.request.get(base + process.env.EDU_REPORT_OTHER_APPLICATION)).status(), 404);
    assert.equal((await context.request.get(base + process.env.EDU_REPORT_BANK)).status(), 403);
    const other = await browser.newContext({ javaScriptEnabled: false });
    t.after(() => other.close());
    await other.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
    await other.addCookies([{ name: 'sessionid', value: process.env.EDU_REPORT_OTHER_SESSION, url: base }]);
    const otherPage = await other.newPage();
    await otherPage.goto(base + process.env.EDU_REPORT_URL);
    for (const [code, value] of Object.entries({ recibidas: 2, aprobadas: 1, autorizadas: 1, pendientes: 0 })) {
        assert.equal(await otherPage.locator(`[data-indicador="${code}"] strong`).innerText(), String(value));
    }
    assert.equal((await otherPage.content()).includes('Curso Gestion'), false);
});
