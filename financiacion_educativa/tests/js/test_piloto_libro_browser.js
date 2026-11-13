'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const test = require('node:test');

test('Piloto nativo: dos operadores, revision, aprobacion, libro, compensacion y apagado', async t => {
    const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser = await chromium.launch({ headless: true, channel: 'msedge' });
    t.after(() => browser.close());
    const base = process.env.EDU_PILOT_BASE;
    async function sesion(valor) {
        const context = await browser.newContext({ javaScriptEnabled: false, locale: 'es-CO' });
        await context.route('**/*', r => new URL(r.request().url()).origin === base ? r.continue() : r.abort());
        await context.addCookies([{ name: 'sessionid', value: valor, url: base }]);
        return context.newPage();
    }
    const operador = await sesion(process.env.EDU_PILOT_OPERATOR_SESSION);
    const aprobador = await sesion(process.env.EDU_PILOT_APPROVER_SESSION);
    fs.mkdirSync(process.env.EDU_PILOT_SCREENSHOTS, { recursive: true });
    async function captura(pagina, nombre) {
        for (const width of [375, 390, 1440]) {
            await pagina.setViewportSize({ width, height: width > 700 ? 1000 : 844 });
            await pagina.evaluate(() => scrollTo(0, 0));
            assert.equal(await pagina.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${nombre}: ${width}`);
            await pagina.screenshot({ path: path.join(process.env.EDU_PILOT_SCREENSHOTS, `${nombre}-${width}.png`), fullPage: true });
        }
    }
    assert.equal((await aprobador.goto(base + process.env.EDU_PILOT_PROPOSAL)).status(), 200);
    await captura(aprobador, '01-propuesta');
    await aprobador.locator('#id_accion').selectOption('REVIEW');
    await aprobador.locator('#id_respaldo').fill('Revision independiente sintetica para ensayo');
    await aprobador.locator('#id_confirmar').check();
    await aprobador.getByRole('button', { name: 'Registrar decision', exact: true }).click();
    await aprobador.getByRole('heading', { name: 'Revisada', exact: true }).waitFor();
    await aprobador.locator('#id_accion').selectOption('APPROVE');
    await aprobador.locator('#id_reemplaza').selectOption(process.env.EDU_PILOT_OLD_POLICY);
    await aprobador.locator('#id_respaldo').fill('Aprobacion de prueba, sin aprobacion comercial');
    await aprobador.locator('#id_confirmar').check();
    await aprobador.getByRole('button', { name: 'Registrar decision', exact: true }).click();
    await aprobador.getByRole('heading', { name: 'Aprobada', exact: true }).waitFor();
    await captura(aprobador, '02-aprobacion-sintetica');
    await operador.goto(base + process.env.EDU_PILOT_PREVIEW);
    await operador.locator('#id_modo').selectOption('PARTIAL');
    const opcion = operador.locator('#id_politica option').filter({ hasText: 'PILOTO_VISUAL_V2' });
    const politica = await opcion.getAttribute('value');
    await operador.locator('#id_politica').selectOption(politica);
    await operador.getByRole('button', { name: 'Previsualizar', exact: true }).click();
    await captura(operador, '03-previa-parcial');
    await operador.locator('#id_motivo').fill('Finalidad y distribucion sinteticas verificadas');
    await operador.locator('#id_confirmar').check();
    await operador.getByRole('button', { name: 'Contabilizar con politica aprobada', exact: true }).click();
    await operador.getByRole('heading', { name: 'Libro del ingreso', exact: true }).waitFor();
    await captura(operador, '04-contabilizado');
    await aprobador.goto(base + process.env.EDU_PILOT_LEDGER);
    await aprobador.getByRole('link', { name: 'Revisar compensacion independiente del ultimo movimiento', exact: true }).click();
    await captura(aprobador, '05-compensacion-independiente');
    await aprobador.locator('#id_motivo').fill('Compensacion completa de ensayo autorizada independientemente');
    await aprobador.locator('#id_confirmar').check();
    await aprobador.getByRole('button', { name: 'Confirmar compensacion', exact: true }).click();
    await aprobador.getByRole('heading', { name: 'Libro del ingreso', exact: true }).waitFor();
    await captura(aprobador, '06-compensado');
    await aprobador.goto(base + process.env.EDU_PILOT_CONTROL);
    await aprobador.locator('#id_institucion').selectOption(process.env.EDU_PILOT_INSTITUTION);
    await aprobador.locator('#id_activo').selectOption('OFF');
    await aprobador.locator('#id_motivo').fill('Ensayo de apagado seguro del piloto');
    await aprobador.locator('#id_confirmar').check();
    await aprobador.getByRole('button', { name: 'Confirmar control', exact: true }).click();
    await captura(aprobador, '07-piloto-detenido');
    await operador.goto(base + process.env.EDU_PILOT_PREVIEW + '?modo=PARTIAL&politica=' + politica);
    assert.equal(await operador.getByRole('button', { name: 'Contabilizar con politica aprobada' }).count(), 0);
    await captura(operador, '08-bloqueo-escrituras');
    assert.equal((await operador.goto(base + process.env.EDU_PILOT_LEDGER)).status(), 200);
});
