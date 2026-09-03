'use strict';

const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { pathToFileURL } = require('node:url');

const modulePath = path.resolve(
    __dirname,
    '../../../static/js/financiacion_educativa_camara.js'
);
const cameraModule = require(modulePath);
const { CameraFlowController, STATES } = cameraModule;

function fakeView() {
    return {
        renders: [],
        previews: [],
        releases: 0,
        completed: [],
        results: [],
        disposed: 0,
        render(snapshot) { this.renders.push({ ...snapshot }); },
        showPreview(blob, photo) { this.previews.push({ blob, photo }); },
        releasePreview() { this.releases += 1; },
        markComplete(side) { this.completed.push(side); },
        handleServerResult(result) { this.results.push(result); },
        dispose() { this.disposed += 1; }
    };
}

function fakeCamera(options) {
    options = options || {};
    const calls = {
        requests: [], stops: 0, zooms: [], captures: 0, files: 0
    };
    const devices = options.devices || [
        { kind: 'videoinput', deviceId: 'rear' }
    ];
    const camera = {
        calls,
        isSupported: () => options.supported !== false,
        open: async (request) => {
            calls.requests.push(request);
            if (options.openError) throw options.openError;
            const deviceId = request.video.deviceId
                ? request.video.deviceId.exact
                : 'rear';
            return {
                stream: { id: deviceId },
                track: { getSettings: () => ({ deviceId }) }
            };
        },
        stop: () => { calls.stops += 1; },
        listVideoInputs: async () => {
            if (options.listError) throw options.listError;
            return devices;
        },
        enableZoom: async () => {
            if (options.zoomError) throw options.zoomError;
            return options.zoom || null;
        },
        setZoom: async (_track, value) => { calls.zooms.push(value); },
        capture: async () => {
            calls.captures += 1;
            if (options.captureError) throw options.captureError;
            return {
                blob: { id: calls.captures },
                width: 1600,
                height: 1000,
                method: 'image-capture'
            };
        },
        prepareFile: async (file) => {
            calls.files += 1;
            return { blob: file, width: 1200, height: 800, method: 'file' };
        }
    };
    return camera;
}

function controllerHarness(options) {
    options = options || {};
    const view = fakeView();
    const camera = options.camera || fakeCamera(options.cameraOptions);
    const uploads = [];
    const uploader = options.uploader || {
        upload: async (payload) => {
            uploads.push(payload);
            return { ok: true };
        }
    };
    const controller = new CameraFlowController({
        camera,
        uploader,
        view,
        secureContext: options.secureContext !== false,
        requiresBack: options.requiresBack !== false,
        initialSide: options.initialSide || 'frente'
    });
    return { controller, camera, view, uploads };
}

test('inicia en INTRO sin solicitar permiso', () => {
    const instance = controllerHarness();
    assert.equal(instance.controller.state, STATES.INTRO);
    assert.equal(instance.camera.calls.requests.length, 0);
});

test('solicita camara trasera, sin audio y enumera despues del permiso', async () => {
    const instance = controllerHarness();
    assert.equal(await instance.controller.start(), true);
    assert.equal(instance.controller.state, STATES.LIVE_CAMERA);
    assert.deepEqual(instance.camera.calls.requests[0], {
        audio: false,
        video: {
            facingMode: { ideal: 'environment' },
            width: { ideal: 1920 },
            height: { ideal: 1080 }
        }
    });
    assert.equal(instance.controller.devices.length, 1);
});

test('enumeracion y zoom opcionales no bloquean una camara abierta', async () => {
    const instance = controllerHarness({
        cameraOptions: {
            listError: new Error('enumeration unavailable'),
            zoomError: new Error('capabilities unavailable')
        }
    });
    assert.equal(await instance.controller.start(), true);
    assert.equal(instance.controller.state, STATES.LIVE_CAMERA);
    assert.deepEqual(instance.controller.devices, []);
    assert.equal(instance.controller.zoom, null);
});

test('distingue permiso denegado y camara inexistente', async () => {
    const denied = controllerHarness({
        cameraOptions: { openError: { name: 'NotAllowedError' } }
    });
    await denied.controller.start();
    assert.equal(denied.controller.state, STATES.PERMISSION_DENIED);

    const missing = controllerHarness({
        cameraOptions: { openError: { name: 'NotFoundError' } }
    });
    await missing.controller.start();
    assert.equal(missing.controller.state, STATES.CAMERA_UNAVAILABLE);
});

test('contexto inseguro y navegador sin getUserMedia fallan de forma controlada', async () => {
    const insecure = controllerHarness({ secureContext: false });
    await insecure.controller.start();
    assert.equal(insecure.controller.state, STATES.CAMERA_UNAVAILABLE);

    const unsupported = controllerHarness({ cameraOptions: { supported: false } });
    await unsupported.controller.start();
    assert.equal(unsupported.controller.state, STATES.CAMERA_UNAVAILABLE);
});

test('cambio de camara reinicia el stream con el siguiente deviceId', async () => {
    const instance = controllerHarness({
        cameraOptions: {
            devices: [
                { kind: 'videoinput', deviceId: 'rear' },
                { kind: 'videoinput', deviceId: 'front' }
            ]
        }
    });
    await instance.controller.start();
    assert.equal(await instance.controller.switchCamera(), true);
    assert.deepEqual(instance.camera.calls.requests[1].video.deviceId, {
        exact: 'front'
    });
    assert.ok(instance.camera.calls.stops >= 1);
});

test('error al cambiar de camara queda identificado y libera el stream', async () => {
    const instance = controllerHarness({
        cameraOptions: {
            devices: [
                { kind: 'videoinput', deviceId: 'rear' },
                { kind: 'videoinput', deviceId: 'front' }
            ]
        }
    });
    await instance.controller.start();
    instance.camera.open = async () => {
        throw Object.assign(new Error('switch failed'), { name: 'NotReadableError' });
    };
    assert.equal(await instance.controller.switchCamera(), false);
    assert.equal(instance.controller.state, STATES.CAMERA_UNAVAILABLE);
    assert.match(instance.controller.errorMessage, /cambiar de camara/);
    assert.ok(instance.camera.calls.stops >= 1);
});

test('zoom solo se aplica cuando existe capacidad real', async () => {
    const enabled = controllerHarness({
        cameraOptions: { zoom: { min: 1, max: 3, step: 0.5, value: 1 } }
    });
    await enabled.controller.start();
    assert.equal(await enabled.controller.setZoom(2), true);
    assert.deepEqual(enabled.camera.calls.zooms, [2]);

    const disabled = controllerHarness();
    await disabled.controller.start();
    assert.equal(await disabled.controller.setZoom(2), false);
});

test('zoomRange valida rango y ajustes efectivos', () => {
    const track = {
        getCapabilities: () => ({ zoom: { min: 1, max: 4, step: 0.25 } }),
        getSettings: () => ({ zoom: 2.5 })
    };
    assert.deepEqual(cameraModule.zoomRange(track), {
        min: 1, max: 4, step: 0.25, value: 2.5
    });
    assert.equal(cameraModule.zoomRange({ getCapabilities: () => ({}) }), null);
});

test('captura, repite y detiene el stream anterior', async () => {
    const instance = controllerHarness();
    await instance.controller.start();
    assert.equal(await instance.controller.capture(), true);
    assert.equal(instance.controller.state, STATES.CAPTURED_REVIEW);
    assert.equal(instance.view.previews.length, 1);
    assert.ok(instance.camera.calls.stops >= 1);
    assert.equal(await instance.controller.repeat(), true);
    assert.equal(instance.controller.state, STATES.LIVE_CAMERA);
    assert.ok(instance.view.releases >= 2);
});

test('confirma una sola carga y conserva la foto ante error para reintentar', async () => {
    let resolveUpload;
    let calls = 0;
    const uploader = {
        upload: () => {
            calls += 1;
            return new Promise((resolve) => { resolveUpload = resolve; });
        }
    };
    const instance = controllerHarness({ uploader });
    await instance.controller.start();
    await instance.controller.capture();
    const first = instance.controller.upload();
    assert.equal(await instance.controller.upload(), false);
    assert.equal(calls, 1);
    resolveUpload({ ok: true });
    assert.equal(await first, true);
    assert.equal(instance.view.results.length, 1);
    assert.equal(instance.controller.state, STATES.COMPLETED);

    let attempt = 0;
    const retry = controllerHarness({
        uploader: {
            upload: async () => {
                attempt += 1;
                if (attempt === 1) throw new Error('Fallo temporal');
                return { ok: true };
            }
        }
    });
    await retry.controller.start();
    await retry.controller.capture();
    assert.equal(await retry.controller.upload(), false);
    assert.equal(retry.controller.state, STATES.UPLOAD_ERROR);
    assert.ok(retry.controller.photo);
    assert.equal(await retry.controller.upload(), true);
    assert.equal(attempt, 2);
});

test('frente y reverso se cargan por separado sin sobrescribir el lado', async () => {
    const instance = controllerHarness({ requiresBack: true });
    await instance.controller.start();
    await instance.controller.capture();
    await instance.controller.upload();
    assert.equal(instance.uploads[0].side, 'frente');
    assert.equal(instance.uploads[0].captureMethod, 'webrtc');
    assert.equal(instance.controller.next(), true);
    assert.equal(instance.controller.side, 'reverso');
    await instance.controller.start();
    await instance.controller.capture();
    await instance.controller.upload();
    assert.equal(instance.uploads[1].side, 'reverso');
    assert.deepEqual(instance.view.completed, ['frente', 'reverso']);
});

test('control nativo entra a revision con origen propio sin afirmar tiempo real', async () => {
    const instance = controllerHarness();
    const file = { type: 'image/jpeg' };
    assert.equal(await instance.controller.selectFile(file), true);
    assert.equal(instance.controller.state, STATES.CAPTURED_REVIEW);
    assert.equal(instance.camera.calls.files, 1);
    await instance.controller.upload();
    assert.equal(instance.uploads[0].captureMethod, 'native');
});

test('dispose detiene pistas y libera la vista previa', async () => {
    const instance = controllerHarness();
    await instance.controller.start();
    await instance.controller.capture();
    instance.controller.dispose();
    assert.equal(instance.view.disposed, 1);
    assert.ok(instance.view.releases >= 2);
});

test('ocultar libera stream o preview y volver restaura una revision pendiente', async () => {
    const live = controllerHarness();
    await live.controller.start();
    assert.equal(live.controller.pause(), true);
    assert.equal(live.controller.state, STATES.INTRO);
    assert.ok(live.camera.calls.stops >= 1);

    const review = controllerHarness();
    await review.controller.start();
    await review.controller.capture();
    const previewsBefore = review.view.previews.length;
    assert.equal(review.controller.pause(), true);
    assert.equal(review.controller.state, STATES.CAPTURED_REVIEW);
    assert.equal(review.controller.previewSuspended, true);
    assert.equal(review.controller.resume(), true);
    assert.equal(review.view.previews.length, previewsBefore + 1);
    assert.equal(review.controller.previewSuspended, false);
});

test('prefiere ImageCapture y usa canvas como fallback', async () => {
    let takePhotoCalls = 0;
    let drawCalls = 0;
    const track = { stop() {}, getSettings: () => ({ deviceId: 'rear' }) };
    const stream = {
        getVideoTracks: () => [track],
        getTracks: () => [track]
    };
    const video = {
        srcObject: null,
        videoWidth: 1600,
        videoHeight: 1000,
        play: async () => {},
        pause: () => {}
    };
    const canvas = {
        width: 0,
        height: 0,
        getContext: () => ({ drawImage: () => { drawCalls += 1; } }),
        toBlob: (callback) => callback({ type: 'image/jpeg' })
    };
    const browserWindow = {
        navigator: { mediaDevices: {
            getUserMedia: async () => stream,
            enumerateDevices: async () => []
        } },
        ImageCapture: class {
            async takePhoto() {
                takePhotoCalls += 1;
                return { type: 'image/jpeg' };
            }
        }
    };
    const nativeCamera = cameraModule.createBrowserCamera(video, canvas, browserWindow);
    const session = await nativeCamera.open(cameraModule.buildVideoRequest());
    const nativePhoto = await nativeCamera.capture(session);
    assert.equal(nativePhoto.method, 'image-capture');
    assert.equal(takePhotoCalls, 1);
    assert.equal(drawCalls, 0);

    browserWindow.ImageCapture = null;
    const canvasCamera = cameraModule.createBrowserCamera(video, canvas, browserWindow);
    const canvasPhoto = await canvasCamera.capture(session);
    assert.equal(canvasPhoto.method, 'canvas');
    assert.equal(drawCalls, 1);
});

test('detiene el stream si el video no puede iniciar', async () => {
    let stops = 0;
    const track = { stop: () => { stops += 1; } };
    const stream = {
        getVideoTracks: () => [track],
        getTracks: () => [track]
    };
    const video = {
        srcObject: null,
        play: async () => { throw new Error('play failed'); },
        pause: () => {}
    };
    const camera = cameraModule.createBrowserCamera(video, {}, {
        navigator: { mediaDevices: { getUserMedia: async () => stream } },
        ImageCapture: null
    });
    await assert.rejects(camera.open(cameraModule.buildVideoRequest()), /play failed/);
    assert.equal(stops, 1);
    assert.equal(video.srcObject, null);
});

test('revoca cada object URL al repetir, reemplazar o salir', () => {
    const created = [];
    const revoked = [];
    const image = {
        src: '',
        removeAttribute(name) { if (name === 'src') this.src = ''; }
    };
    const manager = cameraModule.createPreviewManager({
        createObjectURL: (blob) => {
            const url = 'blob:' + blob.id;
            created.push(url);
            return url;
        },
        revokeObjectURL: (url) => revoked.push(url)
    }, image);
    manager.show({ id: 1 });
    manager.show({ id: 2 });
    manager.release();
    assert.deepEqual(created, ['blob:1', 'blob:2']);
    assert.deepEqual(revoked, ['blob:1', 'blob:2']);
    assert.equal(image.src, '');
});

test('no contiene linterna, biometria, almacenamiento local ni zoom simulado', () => {
    const source = fs.readFileSync(modulePath, 'utf8');
    assert.equal(/torch|linterna/i.test(source), false);
    assert.equal(/selfie|biometr|facial|liveness/i.test(source), false);
    assert.equal(/localStorage|sessionStorage|indexedDB/i.test(source), false);
    assert.equal(/transform:\s*scale|style\.zoom/i.test(source), false);
    assert.match(source, /getCapabilities/);
    assert.match(source, /applyConstraints/);
    assert.match(source, /takePhoto/);
    assert.match(source, /querySelectorAll\('\[data-camera-repeat\]'\)/);
});

test('estilos mantienen controles dentro del viewport movil pequeno', () => {
    const cssPath = path.resolve(
        __dirname,
        '../../../static/css/financiacion_educativa.css'
    );
    const source = fs.readFileSync(cssPath, 'utf8');
    assert.match(source, /height:\s*100dvh/);
    assert.match(source, /env\(safe-area-inset-top\)/);
    assert.match(source, /env\(safe-area-inset-bottom\)/);
    assert.match(source, /orientation:\s*portrait[^}]*max-height:\s*700px/s);
    assert.match(source, /\.edu-camera-shutter-bar\s*\{[^}]*min-height:/s);
    assert.match(source, /\.edu-camera-live,[\s\S]*?overflow:\s*hidden/);
});

test('layout real mantiene obturador y controles dentro de viewports moviles bajos', (t) => {
    const candidatos = process.platform === 'win32'
        ? [
            'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
            'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
            'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
        ]
        : ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
    const navegador = candidatos.find((candidato) => fs.existsSync(candidato));
    if (!navegador) {
        t.skip('Chrome/Chromium no esta disponible para validar layout real.');
        return;
    }
    const css = fs.readFileSync(
        path.resolve(__dirname, '../../../static/css/financiacion_educativa.css'),
        'utf8'
    );
    const temporal = fs.mkdtempSync(path.join(os.tmpdir(), 'fundetec-camera-layout-'));
    try {
        const html = path.join(temporal, 'camera.html');
        fs.writeFileSync(html, `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head>
<body class="edu-camera-page"><section class="edu-camera-app"><div class="edu-camera-panel edu-camera-live">
<header class="edu-camera-overlay-header"><button class="edu-camera-icon-action is-dark">X</button><div><strong>Frente</strong><span>Encuadra el documento</span></div><button class="edu-camera-icon-action is-dark">C</button></header>
<div class="edu-camera-viewport"><div class="edu-camera-id-frame"></div></div>
<div class="edu-camera-zoom"><label>Zoom</label><input type="range"></div>
<div class="edu-camera-shutter-bar"><button class="edu-camera-shutter"><span></span></button></div>
</div></section><script>
const viewport = { width: innerWidth, height: innerHeight };
const names = ['.edu-camera-app', '.edu-camera-overlay-header', '.edu-camera-shutter-bar', '.edu-camera-shutter'];
const boxes = Object.fromEntries(names.map((name) => { const r = document.querySelector(name).getBoundingClientRect(); return [name, {top:r.top,right:r.right,bottom:r.bottom,left:r.left,width:r.width,height:r.height}]; }));
document.documentElement.dataset.layoutResult = encodeURIComponent(JSON.stringify({viewport, boxes, scrollWidth:document.documentElement.scrollWidth, scrollHeight:document.documentElement.scrollHeight}));
</script></body></html>`, 'utf8');
        for (const [width, height] of [[360, 640], [640, 360]]) {
            const perfil = path.join(temporal, `profile-${width}-${height}`);
            const result = childProcess.spawnSync(navegador, [
                '--headless=new', '--disable-gpu', '--no-sandbox',
                `--user-data-dir=${perfil}`, `--window-size=${width},${height}`,
                '--dump-dom', pathToFileURL(html).href
            ], { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 });
            assert.equal(result.status, 0, result.stderr);
            const match = result.stdout.match(/data-layout-result="([^"]+)"/);
            assert.ok(match, 'Chrome debe devolver las mediciones del layout.');
            const layout = JSON.parse(decodeURIComponent(match[1]));
            for (const [selector, box] of Object.entries(layout.boxes)) {
                assert.ok(box.top >= -0.5, `${selector} inicia fuera del viewport ${width}x${height}`);
                assert.ok(box.left >= -0.5, `${selector} inicia fuera del viewport ${width}x${height}`);
                assert.ok(box.right <= layout.viewport.width + 0.5, `${selector} desborda horizontalmente ${width}x${height}`);
                assert.ok(box.bottom <= layout.viewport.height + 0.5, `${selector} desborda verticalmente ${width}x${height}`);
            }
            assert.ok(layout.scrollWidth <= layout.viewport.width);
            assert.ok(layout.scrollHeight <= layout.viewport.height);
        }
    } finally {
        fs.rmSync(temporal, { recursive: true, force: true });
    }
});

test('recupera iPad con apariencia macOS sin clasificar portatil tactil generico', () => {
    assert.equal(cameraModule.isAppleTouchDesktop({
        platform: 'MacIntel',
        userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)',
        maxTouchPoints: 5
    }), true);
    assert.equal(cameraModule.isAppleTouchDesktop({
        platform: 'Win32',
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        maxTouchPoints: 10
    }), false);
    assert.equal(cameraModule.isAppleTouchDesktop({
        platform: 'MacIntel',
        userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)',
        maxTouchPoints: 1
    }), false);
});
