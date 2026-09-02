'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

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
