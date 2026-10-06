'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

test('obligaciones: filtros, calendario y ancho movil', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const base = process.env.EDU_DASHBOARD_URL;
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const context = await browser.newContext();
    await context.route('**/*', route => new URL(route.request().url()).origin === base
        ? route.continue() : route.abort());
    await context.addCookies([{ name: 'sessionid', value: process.env.EDU_DASHBOARD_SESSION, url: base }]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const width of [375, 390, 768, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        for (const [nombre, ruta] of [
            ['inicio', '/financiacion-educativa/operaciones/'],
            ['listado', '/financiacion-educativa/operaciones/obligaciones/'],
            ['detalle', process.env.EDU_DASHBOARD_DETAIL],
        ]) {
            const respuesta = await page.goto(base + ruta);
            assert.equal(respuesta.status(), 200);
            await page.screenshot({ path: path.join(process.env.EDU_DASHBOARD_SCREENSHOTS,
                `${nombre}-${width}.png`), fullPage: true });
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true,
                `overflow de pagina: ${nombre} ${width}`);
            if (nombre === 'listado') {
                assert.equal(await page.getByRole('button', { name: 'Consultar', exact: true }).isVisible(), true);
                for (const campo of ['ambito', 'institucion', 'estado', 'desde', 'hasta']) {
                    const rect = await page.locator(`[name="${campo}"]`).boundingBox();
                    assert.ok(rect.width > 100 && rect.width <= width);
                }
            }
            if (nombre !== 'inicio') {
                assert.equal(await page.getByText('No disponible', { exact: true }).count(), 5);
                const region = page.locator('.edu-contract-table-wrap');
                assert.equal(await region.evaluate(el => el.scrollWidth >= el.clientWidth), true);
                if (width < 640) {
                    assert.equal(await region.evaluate(el => el.scrollWidth > el.clientWidth), true);
                    await region.evaluate(el => { el.scrollLeft = 300; });
                    assert.ok(await region.evaluate(el => el.scrollLeft) > 0);
                }
            }
        }
    }
    await page.goto(base + '/financiacion-educativa/operaciones/obligaciones/');
    await page.locator('[name="estado"]').selectOption('UNDER_REVIEW');
    await Promise.all([
        page.waitForURL('**/*estado=UNDER_REVIEW*'),
        page.getByRole('button', { name: 'Consultar', exact: true }).click(),
    ]);
    assert.equal(await page.getByRole('heading', { name: 'Sin obligaciones autorizadas', exact: true }).count(), 1);
    assert.deepEqual(errors, []);
});
