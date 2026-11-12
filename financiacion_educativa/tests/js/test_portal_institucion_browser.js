'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

test('Institucion: seguimiento, aprobacion y autorizacion efectiva sin JavaScript', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const base = process.env.EDU_INST_BASE;
    const context = await browser.newContext({ javaScriptEnabled: false, locale: 'es-CO', reducedMotion: 'reduce' });
    await context.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_INST_SESSION, url: base }]);
    const page = await context.newPage();
    async function capture(name) {
        for (const width of [375, 390, 1440]) {
            await page.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name}: overflow ${width}`);
            assert.equal(await page.locator('.edu-navbar-logo img').evaluate(img => img.complete && img.naturalWidth > 0), true);
            await page.screenshot({ path: path.join(process.env.EDU_INST_SCREENSHOTS, `${name}-${width}.png`), fullPage: true });
        }
    }
    assert.equal((await page.goto(base + process.env.EDU_INST_TRACKING)).status(), 200);
    const pending = page.getByRole('row').filter({ hasText: process.env.EDU_INST_PENDING_REFERENCE });
    assert.equal(await pending.locator('td[data-label="Solicitud aprobada"] .edu-institution-status').innerText(), 'S\u00ed');
    assert.equal(await pending.locator('td[data-label="Curso autorizado"] .edu-institution-status').innerText(), 'No');
    const authorized = page.getByRole('row').filter({ hasText: process.env.EDU_INST_AUTHORIZED_REFERENCE });
    assert.equal(await authorized.locator('td[data-label="Curso autorizado"] .edu-institution-status').innerText(), 'S\u00ed');
    assert.equal((await page.locator('main').innerText()).includes(process.env.EDU_INST_OTHER_REFERENCE), false);
    assert.equal(await page.locator('.edu-dashboard-advanced-filters[open]').count(), 0);
    await capture('01-seguimiento');
    await pending.getByRole('link', { name: 'Ver solicitud ' + process.env.EDU_INST_PENDING_REFERENCE, exact: true }).click();
    assert.deepEqual(await page.locator('.edu-institution-decisions .edu-institution-status').allInnerTexts(), ['S\u00ed', 'No']);
    await page.getByText('Historial de seguimiento', { exact: true }).click();
    assert.equal((await page.locator('.edu-dashboard-timeline').innerText()).includes('Curso autorizado'), false);
    await page.getByText('Historial de seguimiento', { exact: true }).click();
    await capture('02-aprobada-sin-autorizacion');
    await page.getByRole('link', { name: 'Ir a seguimiento', exact: true }).click();
    await page.getByRole('link', { name: 'Ver solicitud ' + process.env.EDU_INST_AUTHORIZED_REFERENCE, exact: true }).click();
    assert.deepEqual(await page.locator('.edu-institution-decisions .edu-institution-status').allInnerTexts(), ['S\u00ed', 'S\u00ed']);
    assert.equal(await page.locator('.edu-institution-decisions time[datetime]').count(), 1);
    assert.equal(await page.locator('main a[href*="operaciones/"]').count(), 0);
    await capture('03-autorizacion-efectiva');
    await page.getByRole('link', { name: 'Ir a seguimiento', exact: true }).click();
    await page.locator('#id_q').fill('SYNTHETIC-NO-MATCH');
    await page.getByRole('button', { name: 'Consultar', exact: true }).click();
    await page.getByRole('heading', { name: 'Sin resultados', exact: true }).waitFor();
    await capture('04-sin-resultados');
    assert.equal((await page.goto(base + process.env.EDU_INST_TRACKING + '?desde=invalid-date')).status(), 400);
    assert.equal(await page.getByRole('alert').count() > 0, true);
    await capture('05-filtro-invalido');
    assert.equal((await context.request.get(base + process.env.EDU_INST_OTHER)).status(), 404);
    assert.equal((await context.request.get(base + process.env.EDU_INST_BANK)).status(), 403);
    const other = await browser.newContext({ javaScriptEnabled: false });
    t.after(() => other.close());
    await other.addCookies([{ name: 'sessionid', value: process.env.EDU_INST_OTHER_SESSION, url: base }]);
    assert.equal((await other.request.get(base + process.env.EDU_INST_AUTHORIZED)).status(), 404);
    const otherList = await other.request.get(base + process.env.EDU_INST_TRACKING);
    assert.equal(otherList.status(), 200);
    assert.equal((await otherList.text()).includes(process.env.EDU_INST_AUTHORIZED_REFERENCE), false);
    assert.equal((await otherList.text()).includes(process.env.EDU_INST_OTHER_REFERENCE), true);
});
