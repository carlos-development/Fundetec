'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const modulePath = path.resolve(
    __dirname,
    '../../../static/js/financiacion_educativa_procesamiento.js'
);
const processing = require(modulePath);

function response(status, data, headers) {
    return {
        status,
        ok: status >= 200 && status < 300,
        headers: { get: (name) => (headers || {})[name] || null },
        json: async () => data
    };
}

function harness(responses, options) {
    options = options || {};
    const scheduled = [];
    const rendered = [];
    const issues = [];
    const renderer = {
        render: (data) => rendered.push(data),
        showConnectionIssue: (message, retry) => issues.push({ message, retry })
    };
    const fetchFunction = options.fetchFunction || (async () => {
        const item = responses.shift();
        if (item instanceof Error) throw item;
        return item;
    });
    const controller = processing.createPollingController({
        url: '/financiacion-educativa/solicitudes/demo/procesamiento/estado/',
        fetchFunction,
        renderer,
        schedule: (callback, delay) => {
            const timer = { callback, delay, cancelled: false };
            scheduled.push(timer);
            return timer;
        },
        cancelSchedule: (timer) => { timer.cancelled = true; },
        now: options.now || (() => 1000),
        maxVisualDuration: options.maxVisualDuration || 300000,
        pendingSignatureDelay: options.pendingSignatureDelay || 45000,
        pendingSignatureMaxDuration: (
            options.pendingSignatureMaxDuration || 14400000
        ),
        initialStatus: options.initialStatus || ''
    });
    return { controller, scheduled, rendered, issues };
}

async function runNext(instance) {
    let timer = instance.scheduled.shift();
    while (timer && timer.cancelled) timer = instance.scheduled.shift();
    assert.ok(timer, 'Debe existir una consulta programada');
    assert.equal(timer.cancelled, false);
    await timer.callback();
    return timer;
}

test('inicia una sola instancia, aplica backoff y se detiene al corregir', async () => {
    const instance = harness([
        response(200, { status: 'QUEUED', should_poll: true }),
        response(200, { status: 'RUNNING', should_poll: true }),
        response(200, { status: 'RETRYING', should_poll: true }),
        response(200, { status: 'CORRECTION_REQUIRED', should_poll: false })
    ]);

    assert.equal(instance.controller.start(false), true);
    assert.equal(instance.controller.start(false), false);
    assert.equal((await runNext(instance)).delay, 1200);
    assert.equal((await runNext(instance)).delay, 1200);
    assert.equal((await runNext(instance)).delay, 2000);
    assert.equal((await runNext(instance)).delay, 3500);
    assert.equal(instance.controller.isRunning(), false);
    assert.deepEqual(
        instance.rendered.map((item) => item.status),
        ['QUEUED', 'RUNNING', 'RETRYING', 'CORRECTION_REQUIRED']
    );
});

test('recupera una perdida de red sin crear otro controlador', async () => {
    const instance = harness([
        new Error('offline'),
        response(200, { status: 'COMPLETED', should_poll: false })
    ]);
    instance.controller.start(true);

    await runNext(instance);
    assert.match(instance.issues[0].message, /conexión/i);
    assert.equal(instance.controller.isRunning(), true);
    assert.equal(instance.controller.pollNow(), true);
    assert.equal(
        instance.scheduled.find((timer) => !timer.cancelled).delay,
        0
    );
    await runNext(instance);
    assert.equal(instance.controller.isRunning(), false);
    assert.equal(instance.rendered[0].status, 'COMPLETED');
});

test('PENDING_SIGNATURE conserva un polling lento y acotado', async () => {
    const instance = harness([
        response(200, { status: 'PENDING_SIGNATURE', should_poll: true })
    ], { initialStatus: 'PENDING_SIGNATURE' });

    instance.controller.start(false);
    assert.equal((await runNext(instance)).delay, 45000);
    assert.equal(instance.controller.isRunning(), true);
    assert.equal(
        instance.scheduled.find((timer) => !timer.cancelled).delay,
        45000
    );
});

test('una pestana oculta pausa y al volver consulta inmediatamente', () => {
    const instance = harness([], { initialStatus: 'PENDING_SIGNATURE' });
    instance.controller.start(false);
    const timerLento = instance.scheduled[0];

    assert.equal(instance.controller.setVisible(false), true);
    assert.equal(timerLento.cancelled, true);
    assert.equal(
        instance.scheduled.filter((timer) => !timer.cancelled).length,
        0
    );

    assert.equal(instance.controller.setVisible(true), true);
    const timerVisible = instance.scheduled.find((timer) => !timer.cancelled);
    assert.equal(timerVisible.delay, 0);
});

test('COMPLETED detiene definitivamente el polling', async () => {
    const instance = harness([
        response(200, { status: 'COMPLETED', should_poll: false })
    ]);
    instance.controller.start(true);

    await runNext(instance);

    assert.equal(instance.controller.isRunning(), false);
    assert.equal(
        instance.scheduled.filter((timer) => !timer.cancelled).length,
        0
    );
});

test('no crea dos timers ni dos fetch concurrentes', async () => {
    let resolveFetch;
    let fetchCount = 0;
    const fetchPromise = new Promise((resolve) => { resolveFetch = resolve; });
    const instance = harness([], {
        fetchFunction: async () => {
            fetchCount += 1;
            return fetchPromise;
        }
    });
    instance.controller.start(true);
    const timer = instance.scheduled.shift();
    const inFlight = timer.callback();
    await Promise.resolve();

    assert.equal(instance.controller.pollNow(), false);
    assert.equal(fetchCount, 1);
    assert.equal(
        instance.scheduled.filter((item) => !item.cancelled).length,
        0
    );

    resolveFetch(response(200, {
        status: 'PENDING_SIGNATURE',
        should_poll: true
    }));
    await inFlight;
    assert.equal(fetchCount, 1);
    assert.equal(
        instance.scheduled.filter((item) => !item.cancelled).length,
        1
    );
});

test('el boton manual adelanta la consulta lenta sin duplicarla', () => {
    const instance = harness([], { initialStatus: 'PENDING_SIGNATURE' });
    instance.controller.start(false);
    const timerLento = instance.scheduled[0];

    assert.equal(instance.controller.pollNow(), true);

    assert.equal(timerLento.cancelled, true);
    const activos = instance.scheduled.filter((timer) => !timer.cancelled);
    assert.equal(activos.length, 1);
    assert.equal(activos[0].delay, 0);
});

for (const status of [401, 403, 404]) {
    test('detiene la consulta ante HTTP ' + status, async () => {
        const instance = harness([response(status)]);
        instance.controller.start(true);
        await runNext(instance);
        assert.equal(instance.controller.isRunning(), false);
        assert.equal(instance.issues[0].retry, false);
    });
}

test('respeta Retry-After acotado para HTTP 429', async () => {
    const instance = harness([
        response(429, null, { 'Retry-After': '20' }),
        response(200, { status: 'COMPLETED', should_poll: false })
    ]);
    instance.controller.start(true);
    await runNext(instance);
    assert.equal(instance.scheduled[0].delay, 20000);
    await runNext(instance);
    assert.equal(instance.controller.isRunning(), false);
});

test('un 5xx mantiene el proceso recuperable', async () => {
    const instance = harness([
        response(503),
        response(200, { status: 'MANUAL_EXCEPTION', should_poll: false })
    ]);
    instance.controller.start(true);
    await runNext(instance);
    assert.equal(instance.issues[0].retry, true);
    assert.equal(instance.controller.isRunning(), true);
    await runNext(instance);
    assert.equal(instance.controller.isRunning(), false);
});

test('stop cancela el timer al abandonar la pagina', () => {
    const instance = harness([]);
    instance.controller.start(false);
    const timer = instance.scheduled[0];
    instance.controller.stop();
    assert.equal(timer.cancelled, true);
    assert.equal(instance.controller.isRunning(), false);
});

test('el timeout visual no cancela el proceso de backend', async () => {
    let currentTime = 0;
    const instance = harness([], {
        now: () => currentTime,
        maxVisualDuration: 1000
    });
    instance.controller.start(false);
    currentTime = 1500;
    await runNext(instance);
    assert.equal(instance.controller.isRunning(), false);
    assert.equal(instance.issues[0].retry, true);
    assert.match(instance.issues[0].message, /segundo plano/i);
});

test('clasifica los resultados visibles y no acepta redirects externos', () => {
    const location = { origin: 'https://credito.example.com' };
    assert.equal(
        processing.safeSameOriginUrl('/ruta-segura/', location),
        '/ruta-segura/'
    );
    assert.equal(
        processing.safeSameOriginUrl('https://evil.example/ruta/', location),
        null
    );
    assert.match(processing.titleForStatus('CORRECTION_REQUIRED'), /corrección/i);
    assert.match(processing.titleForStatus('MANUAL_EXCEPTION'), /adicional/i);
    assert.match(processing.titleForStatus('PENDING_SIGNATURE'), /firmar/i);
    assert.match(processing.titleForStatus('COMPLETED'), /completado/i);
});

test('la proteccion de submit impide el segundo envio', () => {
    let listener;
    let disabled = false;
    const form = {
        dataset: {},
        addEventListener: (_name, callback) => { listener = callback; },
        checkValidity: () => true,
        querySelectorAll: () => [{
            get disabled() { return disabled; },
            set disabled(value) { disabled = value; }
        }]
    };
    assert.equal(processing.protectSubmit(form), true);
    listener({ preventDefault: () => assert.fail('Primer envio bloqueado') });
    assert.equal(disabled, true);
    let prevented = false;
    listener({ preventDefault: () => { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(processing.protectSubmit(form), false);
});

test('el cliente no inserta HTML recibido y contempla movimiento reducido', () => {
    const source = fs.readFileSync(modulePath, 'utf8');
    assert.equal(source.includes('.innerHTML'), false);
    assert.match(source, /prefers-reduced-motion/);
    assert.match(source, /textContent/);
});
