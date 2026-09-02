(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root && root.document) {
        root.EducationalIdentityCamera = api;
        const start = function () { api.bootstrap(root.document, root); };
        if (root.document.readyState === 'loading') {
            root.document.addEventListener('DOMContentLoaded', start, { once: true });
        } else {
            start();
        }
    }
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const STATES = Object.freeze({
        INTRO: 'INTRO',
        REQUESTING_PERMISSION: 'REQUESTING_PERMISSION',
        LIVE_CAMERA: 'LIVE_CAMERA',
        CAPTURED_REVIEW: 'CAPTURED_REVIEW',
        UPLOADING: 'UPLOADING',
        COMPLETED: 'COMPLETED',
        PERMISSION_DENIED: 'PERMISSION_DENIED',
        CAMERA_UNAVAILABLE: 'CAMERA_UNAVAILABLE',
        UPLOAD_ERROR: 'UPLOAD_ERROR'
    });

    function buildVideoRequest(deviceId) {
        const video = {
            facingMode: { ideal: 'environment' },
            width: { ideal: 1920 },
            height: { ideal: 1080 }
        };
        if (deviceId) {
            delete video.facingMode;
            video.deviceId = { exact: deviceId };
        }
        return { audio: false, video: video };
    }

    function classifyCameraError(error, secureContext) {
        if (!secureContext) {
            return {
                state: STATES.CAMERA_UNAVAILABLE,
                message: 'La camara requiere una conexion HTTPS segura.'
            };
        }
        const name = error && error.name;
        if (['NotAllowedError', 'PermissionDeniedError', 'SecurityError'].includes(name)) {
            return {
                state: STATES.PERMISSION_DENIED,
                message: 'El permiso de camara esta bloqueado. Habilitalo en Safari o Chrome y vuelve a intentar.'
            };
        }
        if (['NotFoundError', 'DevicesNotFoundError'].includes(name)) {
            return {
                state: STATES.CAMERA_UNAVAILABLE,
                message: 'No encontramos una camara disponible en este telefono.'
            };
        }
        if (['NotReadableError', 'TrackStartError'].includes(name)) {
            return {
                state: STATES.CAMERA_UNAVAILABLE,
                message: 'Otra aplicacion puede estar usando la camara. Cierrala y vuelve a intentar.'
            };
        }
        return {
            state: STATES.CAMERA_UNAVAILABLE,
            message: 'No fue posible iniciar la camara. Abre el enlace en Safari o Chrome, o abre la camara del telefono.'
        };
    }

    function stopStream(stream) {
        if (!stream || typeof stream.getTracks !== 'function') return;
        stream.getTracks().forEach(function (track) { track.stop(); });
    }

    function isAppleTouchDesktop(navigatorObject) {
        if (!navigatorObject) return false;
        const userAgent = String(navigatorObject.userAgent || '').toLowerCase();
        const platform = String(navigatorObject.platform || '').toLowerCase();
        const touchPoints = Number(navigatorObject.maxTouchPoints || 0);
        const appleDesktopSignature = (
            userAgent.includes('macintosh')
            || userAgent.includes('mac os x')
            || platform === 'macintel'
        );
        return appleDesktopSignature && touchPoints > 1;
    }

    function zoomRange(track) {
        if (!track || typeof track.getCapabilities !== 'function') return null;
        const capabilities = track.getCapabilities() || {};
        const zoom = capabilities.zoom;
        if (!zoom) return null;
        const min = Number(zoom.min);
        const max = Number(zoom.max);
        const step = Number(zoom.step || 0.1);
        if (![min, max, step].every(Number.isFinite) || max <= min || step <= 0) {
            return null;
        }
        const settings = typeof track.getSettings === 'function'
            ? (track.getSettings() || {})
            : {};
        const current = Number.isFinite(Number(settings.zoom))
            ? Math.min(max, Math.max(min, Number(settings.zoom)))
            : min;
        return { min: min, max: max, step: step, value: current };
    }

    function safeSameOriginUrl(value, locationObject) {
        if (!value) return null;
        try {
            const url = new URL(value, locationObject.origin);
            return url.origin === locationObject.origin
                ? url.pathname + url.search + url.hash
                : null;
        } catch (_error) {
            return null;
        }
    }

    function createPreviewManager(urlApi, image) {
        let currentUrl = '';
        return {
            show: function (blob) {
                if (currentUrl) urlApi.revokeObjectURL(currentUrl);
                currentUrl = urlApi.createObjectURL(blob);
                image.src = currentUrl;
                return currentUrl;
            },
            release: function () {
                if (currentUrl) urlApi.revokeObjectURL(currentUrl);
                currentUrl = '';
                image.removeAttribute('src');
            },
            current: function () { return currentUrl; }
        };
    }

    class CameraFlowController {
        constructor(options) {
            this.camera = options.camera;
            this.uploader = options.uploader;
            this.view = options.view;
            this.secureContext = options.secureContext !== false;
            this.requiresBack = Boolean(options.requiresBack);
            this.side = options.initialSide === 'reverso' ? 'reverso' : 'frente';
            this.replacementSide = '';
            this.state = STATES.INTRO;
            this.session = null;
            this.photo = null;
            this.devices = [];
            this.zoom = null;
            this.busy = false;
            this.disposed = false;
            this.errorMessage = '';
            this.view.render(this.snapshot());
        }

        snapshot() {
            return {
                state: this.state,
                side: this.side,
                devices: this.devices.slice(),
                canSwitch: this.devices.length > 1,
                zoom: this.zoom,
                errorMessage: this.errorMessage,
                hasPhoto: Boolean(this.photo),
                nextSide: this.side === 'frente' && this.requiresBack
                    ? 'reverso'
                    : '',
                replacementSide: this.replacementSide
            };
        }

        transition(state, message) {
            this.state = state;
            this.errorMessage = message || '';
            this.view.render(this.snapshot());
        }

        releasePhoto() {
            this.photo = null;
            this.view.releasePreview();
        }

        stopCamera() {
            if (this.session) this.camera.stop(this.session);
            this.session = null;
        }

        async start(deviceId) {
            if (this.busy || this.disposed) return false;
            this.busy = true;
            this.stopCamera();
            this.releasePhoto();
            this.transition(STATES.REQUESTING_PERMISSION);
            try {
                if (!this.secureContext || !this.camera.isSupported()) {
                    throw Object.assign(new Error('camera unavailable'), {
                        name: this.secureContext ? 'NotSupportedError' : 'SecurityError'
                    });
                }
                this.session = await this.camera.open(buildVideoRequest(deviceId));
                try {
                    this.devices = await this.camera.listVideoInputs();
                } catch (_error) {
                    this.devices = [];
                }
                try {
                    this.zoom = await this.camera.enableZoom(this.session.track);
                } catch (_error) {
                    this.zoom = null;
                }
                this.transition(STATES.LIVE_CAMERA);
                return true;
            } catch (error) {
                this.stopCamera();
                this.devices = [];
                this.zoom = null;
                const outcome = classifyCameraError(error, this.secureContext);
                this.transition(outcome.state, outcome.message);
                return false;
            } finally {
                this.busy = false;
            }
        }

        async switchCamera() {
            if (this.state !== STATES.LIVE_CAMERA || this.devices.length < 2) {
                return false;
            }
            const settings = this.session && this.session.track
                && typeof this.session.track.getSettings === 'function'
                ? this.session.track.getSettings()
                : {};
            const current = this.devices.findIndex(function (device) {
                return device.deviceId === settings.deviceId;
            });
            const next = this.devices[(current + 1 + this.devices.length) % this.devices.length];
            const changed = await this.start(next.deviceId);
            if (!changed && this.state === STATES.CAMERA_UNAVAILABLE) {
                this.transition(
                    STATES.CAMERA_UNAVAILABLE,
                    'No fue posible cambiar de camara. Vuelve a intentarlo o abre la camara del telefono.'
                );
            }
            return changed;
        }

        async setZoom(value) {
            if (this.state !== STATES.LIVE_CAMERA || !this.zoom || !this.session) {
                return false;
            }
            try {
                await this.camera.setZoom(this.session.track, Number(value));
                this.zoom.value = Number(value);
                this.view.render(this.snapshot());
                return true;
            } catch (_error) {
                this.zoom = null;
                this.view.render(this.snapshot());
                return false;
            }
        }

        async capture() {
            if (this.state !== STATES.LIVE_CAMERA || this.busy || !this.session) {
                return false;
            }
            this.busy = true;
            try {
                const photo = await this.camera.capture(this.session);
                this.stopCamera();
                this.photo = photo;
                this.view.showPreview(photo.blob, photo);
                this.transition(STATES.CAPTURED_REVIEW);
                return true;
            } catch (_error) {
                this.stopCamera();
                this.transition(
                    STATES.CAMERA_UNAVAILABLE,
                    'No fue posible tomar la foto. Vuelve a intentarlo o abre la camara del telefono.'
                );
                return false;
            } finally {
                this.busy = false;
            }
        }

        async selectFile(file) {
            if (!file || this.busy || this.disposed) return false;
            this.busy = true;
            this.stopCamera();
            this.releasePhoto();
            try {
                this.photo = await this.camera.prepareFile(file);
                this.view.showPreview(this.photo.blob, this.photo);
                this.transition(STATES.CAPTURED_REVIEW);
                return true;
            } catch (_error) {
                this.transition(
                    STATES.CAMERA_UNAVAILABLE,
                    'El navegador no pudo preparar la imagen seleccionada.'
                );
                return false;
            } finally {
                this.busy = false;
            }
        }

        async repeat() {
            if (![STATES.CAPTURED_REVIEW, STATES.UPLOAD_ERROR].includes(this.state)) {
                return false;
            }
            this.releasePhoto();
            return this.start();
        }

        async upload() {
            if (
                ![STATES.CAPTURED_REVIEW, STATES.UPLOAD_ERROR].includes(this.state)
                || !this.photo
                || this.busy
            ) return false;
            this.busy = true;
            this.transition(STATES.UPLOADING);
            try {
                const result = await this.uploader.upload({
                    blob: this.photo.blob,
                    side: this.side,
                    captureMethod: this.photo.method === 'file'
                        ? 'native'
                        : 'webrtc',
                    replace: this.replacementSide === this.side
                });
                this.view.markComplete(this.side);
                this.releasePhoto();
                this.replacementSide = '';
                this.view.handleServerResult(result);
                this.transition(STATES.COMPLETED);
                return true;
            } catch (error) {
                this.transition(
                    STATES.UPLOAD_ERROR,
                    error && error.message
                        ? error.message
                        : 'No fue posible guardar la fotografia.'
                );
                return false;
            } finally {
                this.busy = false;
            }
        }

        next() {
            if (this.state !== STATES.COMPLETED) return false;
            if (this.side === 'frente' && this.requiresBack) {
                this.side = 'reverso';
                this.transition(STATES.INTRO);
                return true;
            }
            return false;
        }

        replace(side) {
            if (!['frente', 'reverso'].includes(side)) return false;
            this.stopCamera();
            this.releasePhoto();
            this.side = side;
            this.replacementSide = side;
            this.transition(STATES.INTRO);
            return true;
        }

        pause() {
            if (this.state !== STATES.LIVE_CAMERA) return false;
            this.stopCamera();
            this.transition(
                STATES.INTRO,
                'La camara se pauso al salir de la pagina. Activa la camara para continuar.'
            );
            return true;
        }

        dispose() {
            if (this.disposed) return;
            this.disposed = true;
            this.stopCamera();
            this.releasePhoto();
            this.view.dispose();
        }
    }

    function createBrowserCamera(video, canvas, browserWindow) {
        const mediaDevices = browserWindow.navigator.mediaDevices;
        const ImageCaptureClass = browserWindow.ImageCapture;

        async function dimensions(blob) {
            if (typeof browserWindow.createImageBitmap !== 'function') {
                return { width: 0, height: 0 };
            }
            const bitmap = await browserWindow.createImageBitmap(blob);
            const result = { width: bitmap.width, height: bitmap.height };
            if (typeof bitmap.close === 'function') bitmap.close();
            return result;
        }

        return {
            isSupported: function () {
                return Boolean(mediaDevices && mediaDevices.getUserMedia);
            },
            open: async function (request) {
                const stream = await mediaDevices.getUserMedia(request);
                try {
                    const track = stream.getVideoTracks()[0];
                    if (!track) {
                        throw Object.assign(new Error('No video track'), {
                            name: 'NotFoundError'
                        });
                    }
                    video.srcObject = stream;
                    await video.play();
                    return { stream: stream, track: track };
                } catch (error) {
                    stopStream(stream);
                    video.srcObject = null;
                    throw error;
                }
            },
            stop: function (session) {
                stopStream(session && session.stream);
                video.pause();
                video.srcObject = null;
            },
            listVideoInputs: async function () {
                if (!mediaDevices || typeof mediaDevices.enumerateDevices !== 'function') {
                    return [];
                }
                const devices = await mediaDevices.enumerateDevices();
                return devices.filter(function (device) {
                    return device.kind === 'videoinput' && device.deviceId;
                });
            },
            enableZoom: async function (track) {
                const range = zoomRange(track);
                if (!range || typeof track.applyConstraints !== 'function') return null;
                try {
                    await track.applyConstraints({ advanced: [{ zoom: range.value }] });
                    return range;
                } catch (_error) {
                    return null;
                }
            },
            setZoom: function (track, value) {
                return track.applyConstraints({ advanced: [{ zoom: value }] });
            },
            capture: async function (session) {
                if (ImageCaptureClass) {
                    try {
                        const blob = await new ImageCaptureClass(session.track).takePhoto();
                        const size = await dimensions(blob);
                        return { blob: blob, width: size.width, height: size.height, method: 'image-capture' };
                    } catch (_error) {
                        // Safari and older Chromium variants fall through to canvas.
                    }
                }
                if (!video.videoWidth || !video.videoHeight) {
                    throw new Error('Video not ready');
                }
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
                canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
                const blob = await new Promise(function (resolve, reject) {
                    canvas.toBlob(function (value) {
                        if (value) resolve(value);
                        else reject(new Error('Canvas capture failed'));
                    }, 'image/jpeg', 0.94);
                });
                return { blob: blob, width: canvas.width, height: canvas.height, method: 'canvas' };
            },
            prepareFile: async function (file) {
                const size = await dimensions(file);
                return { blob: file, width: size.width, height: size.height, method: 'file' };
            }
        };
    }

    function createDomView(root, browserWindow) {
        const panels = Array.from(root.querySelectorAll('[data-camera-states]'));
        const status = root.querySelector('[data-camera-status]');
        const messages = root.querySelectorAll('[data-camera-side-copy]');
        const preview = root.querySelector('[data-camera-preview]');
        const switchButton = root.querySelector('[data-camera-switch]');
        const zoomControl = root.querySelector('[data-camera-zoom-control]');
        const zoomInput = root.querySelector('[data-camera-zoom]');
        const completeTitle = root.querySelector('[data-camera-complete-title]');
        const completeCopy = root.querySelector('[data-camera-complete-copy]');
        const nextButton = root.querySelector('[data-camera-next]');
        const finishLink = root.querySelector('[data-camera-finish]');
        const quality = root.querySelector('[data-camera-quality]');
        const errorDetails = root.querySelectorAll('[data-camera-error-detail]');
        const previewManager = createPreviewManager(browserWindow.URL, preview);

        function sideCopy(side) {
            return side === 'frente'
                ? {
                    title: 'Fotografia el frente de tu documento',
                    help: 'Incluye los cuatro bordes, evita reflejos y manten el documento enfocado.'
                }
                : {
                    title: 'Ahora fotografia el reverso',
                    help: 'Gira el documento e incluye los cuatro bordes dentro del marco.'
                };
        }

        return {
            render: function (snapshot) {
                root.dataset.cameraState = snapshot.state;
                root.classList.toggle(
                    'is-immersive',
                    snapshot.state !== STATES.INTRO && snapshot.state !== STATES.COMPLETED
                );
                panels.forEach(function (panel) {
                    panel.hidden = !panel.dataset.cameraStates.split(',').includes(snapshot.state);
                });
                const copy = sideCopy(snapshot.side);
                messages.forEach(function (container) {
                    const title = container.querySelector('[data-camera-side-title]');
                    const help = container.querySelector('[data-camera-side-help]');
                    if (title) title.textContent = copy.title;
                    if (help) help.textContent = copy.help;
                });
                if (status) status.textContent = snapshot.errorMessage || '';
                errorDetails.forEach(function (detail) {
                    if (snapshot.errorMessage) detail.textContent = snapshot.errorMessage;
                });
                if (switchButton) switchButton.hidden = !snapshot.canSwitch;
                if (zoomControl) zoomControl.hidden = !snapshot.zoom;
                if (snapshot.zoom && zoomInput) {
                    zoomInput.min = String(snapshot.zoom.min);
                    zoomInput.max = String(snapshot.zoom.max);
                    zoomInput.step = String(snapshot.zoom.step);
                    zoomInput.value = String(snapshot.zoom.value);
                }
                if (snapshot.state === STATES.COMPLETED) {
                    const hasNext = snapshot.nextSide === 'reverso';
                    completeTitle.textContent = hasNext
                        ? 'Frente guardado de forma segura'
                        : 'Documento capturado';
                    completeCopy.textContent = hasNext
                        ? 'Continua con el reverso para completar la identificacion.'
                        : 'Las fotografias quedaron disponibles para la validacion del servidor.';
                    nextButton.hidden = !hasNext;
                    finishLink.hidden = hasNext;
                }
            },
            showPreview: function (blob, photo) {
                previewManager.show(blob);
                const lowResolution = photo.width && photo.height
                    && (photo.width < Number(root.dataset.minWidth || 800)
                        || photo.height < Number(root.dataset.minHeight || 500));
                quality.textContent = lowResolution
                    ? 'La resolucion parece baja. Revisa que el texto sea legible antes de usar la foto.'
                    : 'Revisa que el documento completo y sus textos sean legibles.';
            },
            releasePreview: function () {
                previewManager.release();
            },
            markComplete: function (side) {
                const item = root.querySelector('[data-side-status="' + side + '"]');
                if (!item) return;
                item.classList.add('is-complete');
                const label = item.querySelector('span');
                if (label) label.textContent = 'Capturada; pendiente de revision';
            },
            handleServerResult: function (result) {
                const safe = safeSameOriginUrl(result && result.processing_url, browserWindow.location);
                if (safe) browserWindow.location.assign(safe);
            },
            dispose: function () {
                previewManager.release();
                root.classList.remove('is-immersive');
                browserWindow.document.body.classList.remove('edu-camera-active');
            }
        };
    }

    function createUploader(root, browserWindow) {
        const csrf = root.querySelector('[name="csrfmiddlewaretoken"]').value;
        return {
            upload: async function (payload) {
                const data = new browserWindow.FormData();
                const extension = payload.blob.type === 'image/png' ? 'png' : 'jpg';
                data.append('csrfmiddlewaretoken', csrf);
                data.append('lado', payload.side);
                data.append('metodo_captura', payload.captureMethod);
                data.append('captura', payload.blob, payload.side + '.' + extension);
                if (payload.replace) data.append('confirmar_reemplazo', '1');
                const response = await browserWindow.fetch(root.dataset.uploadUrl, {
                    method: 'POST',
                    body: data,
                    credentials: 'same-origin',
                    headers: { 'X-CSRFToken': csrf }
                });
                let result = {};
                try { result = await response.json(); } catch (_error) { /* controlled below */ }
                if (!response.ok || !result.ok) {
                    throw new Error(result.error || 'No fue posible guardar la fotografia.');
                }
                return result;
            }
        };
    }

    function bootstrap(documentObject, browserWindow) {
        const root = documentObject.querySelector('[data-identity-camera]');
        if (!root || root.dataset.cameraInitialized === 'true') return null;
        root.dataset.cameraInitialized = 'true';
        const video = root.querySelector('[data-camera-video]');
        const canvas = root.querySelector('[data-camera-canvas]');
        const view = createDomView(root, browserWindow);
        const controller = new CameraFlowController({
            camera: createBrowserCamera(video, canvas, browserWindow),
            uploader: createUploader(root, browserWindow),
            view: view,
            secureContext: browserWindow.isSecureContext,
            initialSide: root.dataset.initialSide,
            requiresBack: root.dataset.requiresBack === 'true'
        });
        browserWindow.document.body.classList.add('edu-camera-active');

        root.querySelector('[data-camera-start]').addEventListener('click', function () {
            controller.start();
        });
        root.querySelector('[data-camera-shoot]').addEventListener('click', function () {
            controller.capture();
        });
        root.querySelectorAll('[data-camera-repeat]').forEach(function (button) {
            button.addEventListener('click', function () { controller.repeat(); });
        });
        root.querySelector('[data-camera-use]').addEventListener('click', function () {
            controller.upload();
        });
        root.querySelector('[data-camera-upload-retry]').addEventListener('click', function () {
            controller.upload();
        });
        root.querySelector('[data-camera-switch]').addEventListener('click', function () {
            controller.switchCamera();
        });
        root.querySelector('[data-camera-zoom]').addEventListener('input', function (event) {
            controller.setZoom(event.target.value);
        });
        root.querySelector('[data-camera-next]').addEventListener('click', function () {
            controller.next();
        });
        root.querySelectorAll('[data-camera-retry]').forEach(function (button) {
            button.addEventListener('click', function () { controller.start(); });
        });
        root.querySelectorAll('[data-camera-replace]').forEach(function (button) {
            button.addEventListener('click', function () {
                if (browserWindow.confirm('Esta accion reemplazara la captura existente.')) {
                    controller.replace(button.dataset.cameraReplace);
                }
            });
        });
        root.querySelectorAll('[data-camera-file]').forEach(function (input) {
            input.addEventListener('change', function () {
                const file = input.files && input.files[0];
                if (file) controller.selectFile(file);
                input.value = '';
            });
        });
        root.querySelectorAll('[data-camera-exit]').forEach(function (link) {
            link.addEventListener('click', function () { controller.dispose(); });
        });
        documentObject.addEventListener('visibilitychange', function () {
            if (documentObject.visibilityState === 'hidden') controller.pause();
        });
        browserWindow.addEventListener('pagehide', function () { controller.dispose(); });
        browserWindow.addEventListener('beforeunload', function () { controller.dispose(); });
        return controller;
    }

    return {
        STATES: STATES,
        CameraFlowController: CameraFlowController,
        buildVideoRequest: buildVideoRequest,
        classifyCameraError: classifyCameraError,
        createBrowserCamera: createBrowserCamera,
        createPreviewManager: createPreviewManager,
        isAppleTouchDesktop: isAppleTouchDesktop,
        safeSameOriginUrl: safeSameOriginUrl,
        stopStream: stopStream,
        zoomRange: zoomRange,
        bootstrap: bootstrap
    };
}));
