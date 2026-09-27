'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const baseURL = process.env.EDU_SIMULATOR_TEST_URL;

test('simulador real: responsive, recalculo, fallback y respuestas tardias', {
    skip: !baseURL || !process.env.EDU_PLAYWRIGHT_MODULE,
}, async (t) => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: process.env.EDU_BROWSER_CHANNEL || 'msedge' });
    t.after(() => browser.close());
    const context = await browser.newContext();
    await context.route('**/*', route => {
        const url = new URL(route.request().url());
        return url.origin === new URL(baseURL).origin ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', e => pageErrors.push(e.message));

    for (const [width, height] of [[360, 640], [375, 667], [390, 844], [412, 915], [844, 390], [1280, 800]]) {
        await t.test(`${width}x${height}: 6 -> 4, cuota nueva y sin overflow`, async () => {
            await page.setViewportSize({ width, height });
            await page.goto(baseURL);
            assert.equal(await page.locator('[data-simulator-plan] tr').count(), 6);
            const anterior = await page.locator('[data-simulator-result="cuota_informativa"]').textContent();
            await page.locator('[name="plazo_meses"]').fill('4');
            await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
            assert.equal(await page.locator('[data-simulator-plan] tr').count(), 4);
            assert.notEqual(await page.locator('[data-simulator-result="cuota_informativa"]').textContent(), anterior);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            await page.getByRole('button', { name: 'Actualizar simulación' }).click();
            await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
            assert.equal(await page.locator('[data-simulator-plan] tr').count(), 4);
        });
    }

    await t.test('input invalido oculta resultado previo y cambio recupera', async () => {
        await page.locator('[name="plazo_meses"]').fill('0');
        await page.waitForFunction(() => !document.querySelector('[data-simulator-error]').hidden);
        assert.equal(await page.locator('.edu-simulator-plan').isVisible(), false);
        assert.equal(await page.locator('.edu-simulator-results').isVisible(), false);
        await page.locator('[name="plazo_meses"]').fill('3');
        await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
        assert.equal(await page.locator('[data-simulator-plan] tr').count(), 3);
    });

    await t.test('error de red no conserva cifras antiguas', async () => {
        await page.route('**/simulador/calcular/', r => r.abort());
        await page.locator('[name="plazo_meses"]').fill('4');
        await page.waitForFunction(() => !document.querySelector('[data-simulator-error]').hidden);
        assert.equal(await page.locator('.edu-simulator-plan').isVisible(), false);
        await page.unroute('**/simulador/calcular/');
    });

    await t.test('respuesta tardia no sobrescribe parametros nuevos', async () => {
        let liberar;
        let iniciada;
        const esperando = new Promise(resolve => { iniciada = resolve; });
        const retenida = new Promise(resolve => { liberar = resolve; });
        await page.route('**/simulador/calcular/', async route => {
            if (route.request().postData().includes('name="plazo_meses"\r\n\r\n6')) {
                const respuesta = await route.fetch();
                iniciada();
                await retenida;
                await route.fulfill({ response: respuesta }).catch(() => {});
            } else await route.continue();
        });
        await page.locator('[name="plazo_meses"]').fill('6');
        await esperando;
        await page.locator('[name="plazo_meses"]').fill('4');
        await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
        liberar();
        await page.unrouteAll({ behavior: 'wait' });
        assert.equal(await page.locator('[data-simulator-plan] tr').count(), 4);
    });

    await t.test('sin JavaScript: POST HTML real y cuatro cuotas', async () => {
        const sinJS = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 375, height: 667 } });
        await sinJS.route('**/*', r => new URL(r.request().url()).origin === new URL(baseURL).origin ? r.continue() : r.abort());
        const pagina = await sinJS.newPage();
        await pagina.goto(baseURL);
        await pagina.locator('[name="plazo_meses"]').fill('4');
        assert.deepEqual(await pagina.locator('[data-simulator-form] input:invalid').evaluateAll(
            inputs => inputs.map(input => ({ name: input.name, value: input.value, message: input.validationMessage }))
        ), []);
        await Promise.all([pagina.waitForNavigation(), pagina.getByRole('button', { name: 'Actualizar simulación' }).click()]);
        assert.equal(await pagina.locator('[data-simulator-plan] tr').count(), 4);
        await sinJS.close();
    });
    assert.deepEqual(pageErrors, []);
});
