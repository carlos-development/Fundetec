'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs/promises');
const path = require('node:path');
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
    const screenshots = process.env.EDU_SIMULATOR_SCREENSHOTS;
    async function capture(page, name) {
        if (!screenshots) return;
        await fs.mkdir(screenshots, {recursive: true});
        await page.screenshot({path: path.join(screenshots, name + '.png'), fullPage: true});
    }
    async function selectTerm(page, value) {
        await page.locator('[name="plazo_meses"]').evaluate((input, term) => {
            input.value = term;
            input.dispatchEvent(new Event('input', {bubbles: true}));
        }, value);
    }
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', e => pageErrors.push(e.message));

    for (const [width, height] of [[360, 640], [375, 667], [390, 844], [412, 915], [844, 390], [1280, 800]]) {
        await t.test(`${width}x${height}: 6 -> 4, cuota nueva y sin overflow`, async () => {
            await page.setViewportSize({ width, height });
            await page.goto(baseURL);
            assert.equal(await page.locator('[data-simulator-plan] tr').count(), 6);
            const anterior = await page.locator('[data-simulator-result="cuota_informativa"]').textContent();
            await selectTerm(page, '4');
            await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
            assert.equal(await page.locator('[data-simulator-plan] tr').count(), 4);
            assert.notEqual(await page.locator('[data-simulator-result="cuota_informativa"]').textContent(), anterior);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            assert.equal(await page.locator('[data-simulator-manual]').isVisible(), false);
            await page.getByText('Ver detalle de los costos', {exact: true}).click();
            assert.equal(await page.locator('[data-simulator-plan] tr').first().isVisible(), true);
        });
    }

    await t.test('cambios rápidos del monto producen una sola consulta con el último valor', async () => {
        let calls = 0;
        function count(request) {
            if (request.url().endsWith('/simulador/calcular/')) calls++;
        }
        page.on('request', count);
        await page.locator('[name="monto_solicitado"]').evaluate(input => {
            for (const value of ['100000', '500000', '1100000']) {
                input.value = value;
                input.dispatchEvent(new Event('input', {bubbles: true}));
            }
        });
        await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
        page.off('request', count);
        assert.equal(calls, 1);
        const value = await page.locator('[data-simulator-result="monto_solicitado"]').textContent();
        assert.equal(value.replace(/\D/g, ''), '1100000');
    });

    await t.test('error de red identifica la estimación anterior y permite reintentar', async () => {
        await page.goto(baseURL);
        await page.route('**/simulador/calcular/', r => r.abort());
        await selectTerm(page, '4');
        await page.waitForFunction(() => !document.querySelector('[data-simulator-error]').hidden);
        assert.match(await page.locator('[data-estimate-title]').innerText(), /Última estimación/i);
        assert.match(await page.locator('[data-estimate-for]').innerText(), /6 meses/);
        await page.unroute('**/simulador/calcular/');
        await page.getByRole('button', {name: 'Reintentar'}).click();
        await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
        assert.equal(await page.locator('[data-simulator-plan] tr').count(), 4);
        assert.equal(await page.locator('[data-simulator-error]').isVisible(), false);
    });

    await t.test('respuesta tardia no sobrescribe parametros nuevos', async () => {
        await page.goto(baseURL);
        // Force an uncancellable transport so the revision guard is exercised.
        await page.evaluate(() => {
            const fetchOriginal = window.fetch;
            window.fetch = (url, options) => fetchOriginal(url, {...options, signal: undefined});
        });
        let liberar;
        const esperando = page.waitForRequest(r => r.url().endsWith('/simulador/calcular/'), {timeout: 5000});
        const retenida = new Promise(resolve => { liberar = resolve; });
        await page.route('**/simulador/calcular/', async route => {
            if (route.request().postData().includes('name="plazo_meses"\r\n\r\n5')) {
                const respuesta = await route.fetch();
                await retenida;
                await route.fulfill({ response: respuesta }).catch(() => {});
            } else await route.continue();
        });
        await selectTerm(page, '5');
        await esperando;
        await selectTerm(page, '4');
        await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
        liberar();
        await page.unrouteAll({ behavior: 'wait' });
        assert.equal(await page.locator('[data-simulator-plan] tr').count(), 4);
    });

    await t.test('una respuesta durante el debounce no restaura la cuota anterior', async () => {
        // Resolve an old request in the same browser task as the next input event.
        await page.evaluate(() => {
            const realFetch = window.fetch;
            window.fetch = async (...args) => {
                const response = await realFetch(...args);
                return {ok: response.ok, json: async () => {
                    const payload = await response.json();
                    window.fetch = realFetch;
                    const term = document.querySelector('[name="plazo_meses"]');
                    term.value = '3';
                    term.dispatchEvent(new Event('input', {bubbles: true}));
                    requestAnimationFrame(() => {
                        window.staleResultVisible = document.querySelectorAll('[data-simulator-plan] tr').length === 5;
                    });
                    return payload;
                }};
            };
        });
        await selectTerm(page, '5');
        await page.waitForFunction(() => typeof window.staleResultVisible === 'boolean');
        assert.equal(await page.evaluate(() => window.staleResultVisible), false);
        await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
        assert.equal(await page.locator('[data-simulator-plan] tr').count(), 3);
    });

    await t.test('Jowalth y entrada pública conservan la cabecera y el alcance de las marcas', async () => {
        const entries = [
            ['generica', baseURL], ['jowalth-publica', process.env.EDU_SIMULATOR_PUBLIC_JOWALTH],
            ['jowalth', process.env.EDU_SIMULATOR_JOWALTH],
        ];
        for (const [entry, url] of entries) {
            if (entry === 'jowalth') {
                await context.addCookies([{name: 'sessionid', value: process.env.EDU_SIMULATOR_SESSION, url: new URL(baseURL).origin}]);
            }
            for (const width of [390, 1440]) {
                await page.setViewportSize({width, height: width === 390 ? 844 : 1000});
                assert.equal((await page.goto(url)).status(), 200);
                const brands = page.locator('.edu-simulator-brands[aria-label] img');
                assert.equal(page.url(), url);
                assert.equal(await brands.count(), entry === 'generica' ? 0 : 2);
                assert.equal(await brands.evaluateAll(imgs => imgs.every(img => img.complete && img.naturalWidth > 0)), true);
                if (entry === 'generica') {
                    const partner = page.getByRole('link', {name: /Abrir simulador de Jowalth/});
                    assert.equal(new URL(await partner.getAttribute('href'), url).href, process.env.EDU_SIMULATOR_PUBLIC_JOWALTH);
                    const partnerLogos = partner.locator('img');
                    assert.equal(await partnerLogos.count(), 2);
                    assert.equal(await partnerLogos.evaluateAll(imgs => imgs.every(img => img.complete && img.naturalWidth > 0)), true);
                    await partner.click();
                    assert.equal(page.url(), process.env.EDU_SIMULATOR_PUBLIC_JOWALTH);
                    assert.equal(await brands.count(), 2);
                    await page.goBack();
                } else {
                    const title = await page.locator('.edu-simulator-heading h1').boundingBox();
                    const logos = await brands.first().boundingBox();
                    assert.ok(width === 390 ? logos.y >= title.y + title.height : logos.x >= title.x + title.width);
                }
                const rect = await page.locator('[data-simulator-result="cuota_informativa"]').boundingBox();
                assert.ok(rect.y + rect.height < page.viewportSize().height);
                assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
                await capture(page, entry + '-' + width);
                if (entry === 'jowalth-publica') {
                    const menu = page.locator(width === 390 ? '.edu-mobile-menu' : '.edu-navbar-links');
                    if (width === 390) await page.getByRole('button', {name: 'Abrir menú'}).click();
                    const navSimulator = menu.getByRole('link', {name: 'Simulador', exact: true});
                    assert.equal(new URL(await navSimulator.getAttribute('href'), url).href, url);
                    if (width === 390) await page.locator('[data-menu-toggle]').click();
                    const footerSimulator = page.locator('.edu-site-footer').getByRole('link', {name: 'Simulador educativo', exact: true});
                    assert.equal(new URL(await footerSimulator.getAttribute('href'), url).href, url);
                    assert.equal((await context.cookies()).some(c => c.name === 'sessionid'), false);
                    await selectTerm(page, '3');
                    await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
                    assert.equal(await page.locator('[data-simulator-plan] tr').count(), 3);
                }
                if (entry === 'jowalth' && width === 1440) {
                    await page.getByText('Ver detalle de los costos', {exact: true}).click();
                    await capture(page, entry + '-costos');
                }
            }
        }
    });

    await t.test('expediente sin tope de monto conserva ambos campos y la última estimación ante un error', async () => {
        await page.goto(process.env.EDU_SIMULATOR_JOWALTH);
        assert.equal(await page.getByRole('spinbutton').count(), 2);
        assert.equal(await page.getByRole('slider').count(), 0);
        await page.locator('[name="plazo_meses"]').fill('0');
        await page.waitForFunction(() => !document.querySelector('[data-simulator-error]').hidden);
        assert.equal(await page.locator('.edu-simulator-plan').isVisible(), true);
        assert.equal(await page.locator('.edu-simulator-results').isVisible(), true);
        await page.locator('[name="plazo_meses"]').fill('3');
        await page.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
        assert.equal(await page.locator('[data-simulator-plan] tr').count(), 3);
    });

    await t.test('deslizador con tecla y toque, ambos vinculados al plazo de Django', async () => {
        const touch = await browser.newContext({hasTouch: true, viewport: {width: 390, height: 844}});
        await touch.route('**/*', r => new URL(r.request().url()).origin === new URL(baseURL).origin ? r.continue() : r.abort());
        const mobile = await touch.newPage();
        await mobile.goto(baseURL);
        const slider = mobile.getByRole('slider', {name: 'Plazo en meses'});
        const term = mobile.locator('[name="plazo_meses"]');
        assert.equal(await mobile.getByRole('slider').count(), 2);
        assert.equal(await mobile.getByRole('spinbutton').count(), 0);
        const amount = mobile.getByRole('slider', {name: 'Monto a financiar'});
        const exact = mobile.locator('[name="monto_solicitado"]');
        await amount.press('Home');
        await amount.press('ArrowRight');
        assert.equal(await exact.inputValue(), '150000');
        assert.match(await amount.getAttribute('aria-valuetext'), /150.000/);
        await mobile.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
        assert.match(await mobile.locator('[data-estimate-for]').innerText(), /150.000 a 6 meses/);
        await amount.press('End');
        assert.equal(await exact.inputValue(), '2000000');
        await mobile.getByRole('button', {name: '$1.000.000', exact: true}).tap();
        assert.equal(await exact.inputValue(), '1000000');
        await mobile.getByRole('button', {name: 'Escribir un monto exacto'}).tap();
        assert.equal(await amount.isVisible(), false);
        await exact.fill('1000000.01');
        await mobile.waitForFunction(() => document.querySelector('[data-estimate-for]').textContent.includes('1.000.000,01'));
        await capture(mobile, 'monto-exacto-390');
        await mobile.getByRole('button', {name: 'Volver al deslizador'}).tap();
        assert.equal(await exact.isVisible(), false);
        assert.equal(await exact.inputValue(), '1000000.01');
        assert.equal(await amount.isVisible(), true);
        await slider.press('Home');
        await slider.press('ArrowRight');
        assert.equal(Number(await term.inputValue()), Number(await term.getAttribute('min')) + 1);
        const sliderBox = await slider.boundingBox();
        await slider.tap({position: {x: sliderBox.width - 3, y: sliderBox.height / 2}});
        assert.equal(await term.inputValue(), await term.getAttribute('max'));
        await mobile.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
        assert.equal(await mobile.locator('[data-simulator-plan] tr').count(), Number(await term.inputValue()));
        await touch.close();
    });

    for (const mobile of [false, true]) {
        await t.test('arrastre sostenido ' + (mobile ? 'táctil' : 'con mouse'), async () => {
            const viewport = mobile ? {width: 390, height: 844} : {width: 1200, height: 760};
            const videos = process.env.EDU_SIMULATOR_VIDEOS;
            if (videos) await fs.mkdir(videos, {recursive: true});
            const ctx = await browser.newContext({
                viewport, hasTouch: mobile, isMobile: mobile, locale: 'es-CO',
                ...(videos ? {recordVideo: {dir: videos, size: viewport}} : {}),
            });
            try {
                await ctx.route('**/*', r => new URL(r.request().url()).origin === new URL(baseURL).origin ? r.continue() : r.abort());
                const tab = await ctx.newPage();
                await tab.goto(process.env.EDU_SIMULATOR_PUBLIC_JOWALTH);
                const slider = tab.getByRole('slider', {name: 'Monto a financiar'});
                const quote = tab.locator('[data-simulator-result="cuota_informativa"]');
                const total = tab.locator('[data-simulator-result="total_proyectado"]');
                const box = await slider.boundingBox();
                const quoteBox = await quote.boundingBox();
                const totalBox = await total.boundingBox();
                assert.ok(mobile ? totalBox.y + totalBox.height <= viewport.height : quoteBox.x > box.x + box.width);
                const y = box.y + box.height / 2;
                const x = portion => box.x + 10 + portion * (box.width - 20);
                const inputs = [];
                const samples = new Set();
                tab.on('request', req => {
                    if (req.url().endsWith('/calcular/')) inputs.push(Date.now());
                });
                const cdp = await ctx.newCDPSession(tab);
                async function touch(type, pos) {
                    await cdp.send('Input.dispatchTouchEvent', {
                        type, touchPoints: type === 'touchEnd' ? [] : [{x: pos, y}],
                    });
                }
                if (mobile) await touch('touchStart', x(.12));
                else { await tab.mouse.move(x(.12), y); await tab.mouse.down(); }
                for (let i = 1; i <= 37; i++) {
                    const pos = x(.12 + .78 * i / 37);
                    if (mobile) await touch('touchMove', pos);
                    else await tab.mouse.move(pos, y);
                    assert.ok(await quote.isVisible() && await total.isVisible());
                    samples.add(await quote.textContent());
                    await tab.waitForTimeout(100);
                }
                assert.ok(inputs.length >= 2 && inputs.length <= 4, 'consultas durante el arrastre: ' + inputs.length);
                assert.ok(samples.size >= 2, 'la cuota debe actualizarse antes de soltar');
                if (mobile) await touch('touchEnd');
                else await tab.mouse.up();
                await tab.waitForFunction(() => document.querySelector('[data-simulator-status]').textContent === 'Resultado actualizado.');
                const amount = await tab.locator('[name="monto_solicitado"]').inputValue();
                assert.equal(Number(amount) % 50000, 0);
                const term = await tab.locator('[name="plazo_meses"]').inputValue();
                const displayed = await tab.locator('[data-estimate-for]').innerText();
                assert.ok(displayed.includes(await slider.getAttribute('aria-valuetext')));
                assert.match(displayed, new RegExp(term + ' meses'));
                const endpoint = await tab.locator('[data-education-simulator]').getAttribute('data-calculate-url');
                const resp = await ctx.request.post(new URL(endpoint, baseURL).href, {form: {
                    monto_solicitado: amount, plazo_meses: term,
                    csrfmiddlewaretoken: await tab.locator('[name="csrfmiddlewaretoken"]').inputValue(),
                }});
                assert.equal(resp.status(), 200);
                const {simulation} = await resp.json();
                assert.equal((await quote.innerText()).replace(/\D/g, ''), simulation.cuota_informativa);
                assert.equal((await total.innerText()).replace(/\D/g, ''), simulation.total_proyectado);
                await tab.waitForTimeout(500);
                if (videos) {
                    const video = tab.video();
                    await tab.close();
                    await video.saveAs(path.join(videos, mobile ? 'arrastre-movil.webm' : 'arrastre-escritorio.webm'));
                    await video.delete();
                }
                t.diagnostic('Muestras antes de soltar: ' + samples.size + '. Monto final: ' + amount);
            } finally { await ctx.close(); }
        });
    }

    await t.test('sin JavaScript: POST HTML real y cuatro cuotas', async () => {
        const sinJS = await browser.newContext({ javaScriptEnabled: false, reducedMotion: 'reduce', viewport: { width: 375, height: 667 } });
        await sinJS.route('**/*', r => new URL(r.request().url()).origin === new URL(baseURL).origin ? r.continue() : r.abort());
        const pagina = await sinJS.newPage();
        await pagina.bringToFront();
        for (const [url, expectedLogos] of [[baseURL, 0], [process.env.EDU_SIMULATOR_PUBLIC_JOWALTH, 2]]) {
            await pagina.goto(url);
            await pagina.locator('[name="plazo_meses"]').fill('4');
            assert.deepEqual(await pagina.locator('[data-simulator-form] input:invalid').evaluateAll(
                inputs => inputs.map(input => ({ name: input.name, value: input.value, message: input.validationMessage }))
            ), []);
            const manual = pagina.locator('[data-simulator-manual]');
            assert.equal(await manual.isVisible(), true);
            const point = await manual.evaluate(button => {
                button.scrollIntoView({block: 'center', behavior: 'instant'});
                const bounds = button.getBoundingClientRect();
                const x = bounds.x + bounds.width / 2;
                const y = bounds.y + bounds.height / 2;
                return {x, y, reachable: button.contains(document.elementFromPoint(x, y))};
            });
            assert.equal(point.reachable, true);
            const [posted] = await Promise.all([
                pagina.waitForNavigation({waitUntil: 'domcontentloaded'}),
                pagina.mouse.click(point.x, point.y),
            ]);
            assert.equal(posted.request().method(), 'POST');
            assert.equal(posted.url(), url);
            assert.equal(posted.status(), 200);
            assert.equal(await pagina.locator('[data-simulator-plan] tr').count(), 4);
            assert.equal(await pagina.locator('.edu-simulator-brands[aria-label] img').count(), expectedLogos);
            await capture(pagina, expectedLogos ? 'jowalth-publica-sin-js' : 'respaldo-sin-js');
        }
        await sinJS.close();
    });
    assert.deepEqual(pageErrors, []);
});
