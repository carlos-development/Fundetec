document.addEventListener('DOMContentLoaded', function () {
    const menuToggle = document.querySelector('[data-menu-toggle]');
    const mobileMenu = document.querySelector('[data-mobile-menu]');
    if (menuToggle && mobileMenu) {
        menuToggle.addEventListener('click', function () {
            const isOpen = mobileMenu.classList.toggle('is-open');
            menuToggle.setAttribute('aria-expanded', String(isOpen));
            menuToggle.setAttribute(
                'aria-label',
                isOpen ? 'Cerrar menu' : 'Abrir menu'
            );
        });
    }

    const userTrigger = document.querySelector('[data-user-menu-trigger]');
    const userMenu = userTrigger && userTrigger.closest('.edu-user-menu');
    if (userTrigger && userMenu) {
        const closeUserMenu = function () {
            userMenu.classList.remove('is-open');
            userTrigger.setAttribute('aria-expanded', 'false');
        };

        userTrigger.addEventListener('click', function () {
            const isOpen = userMenu.classList.toggle('is-open');
            userTrigger.setAttribute('aria-expanded', String(isOpen));
        });

        document.addEventListener('click', function (event) {
            if (!userMenu.contains(event.target)) closeUserMenu();
        });

        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                closeUserMenu();
                userTrigger.focus();
            }
        });
    }

    document.querySelectorAll('[data-summary-toggle]').forEach(function (button) {
        button.addEventListener('click', function () {
            const summary = button.closest('.edu-summary');
            if (!summary) return;
            const isOpen = summary.classList.toggle('is-open');
            button.setAttribute('aria-expanded', String(isOpen));
        });
    });

    document.querySelectorAll('.edu-dropzone input[type="file"]').forEach(function (input) {
        const dropzone = input.closest('.edu-dropzone');

        input.addEventListener('change', function () {
            const target = dropzone.querySelector('[data-file-name]');
            if (!target) return;
            target.textContent = input.files && input.files.length
                ? input.files[0].name
                : 'Selecciona o arrastra tu archivo';
        });

        ['dragenter', 'dragover'].forEach(function (eventName) {
            dropzone.addEventListener(eventName, function (event) {
                event.preventDefault();
                dropzone.classList.add('is-dragging');
            });
        });

        ['dragleave', 'drop'].forEach(function (eventName) {
            dropzone.addEventListener(eventName, function (event) {
                event.preventDefault();
                dropzone.classList.remove('is-dragging');
            });
        });

        dropzone.addEventListener('drop', function (event) {
            if (!event.dataTransfer || !event.dataTransfer.files.length) return;
            input.files = event.dataTransfer.files;
            input.dispatchEvent(new Event('change', { bubbles: true }));
        });
    });

    document.querySelectorAll('form.edu-form').forEach(function (form) {
        form.addEventListener('submit', function () {
            const button = form.querySelector('button[type="submit"]');
            if (!button || button.disabled || !form.checkValidity()) return;
            button.disabled = true;
            button.textContent = 'Procesando...';
        });
    });

    document.querySelectorAll('[data-toast-close]').forEach(function (button) {
        button.addEventListener('click', function () {
            const toast = button.closest('[data-toast]');
            if (toast) toast.remove();
        });
    });

    document.querySelectorAll('.edu-toast-success[data-toast]').forEach(function (toast) {
        window.setTimeout(function () {
            toast.remove();
        }, 8000);
    });

    const documentDialog = document.querySelector('[data-document-dialog]');
    if (documentDialog) {
        const image = documentDialog.querySelector('[data-document-image]');
        const pdf = documentDialog.querySelector('[data-document-pdf]');
        const title = documentDialog.querySelector('[data-document-title]');
        const download = documentDialog.querySelector('[data-document-download]');

        const resetDocumentViewer = function () {
            image.hidden = true;
            image.removeAttribute('src');
            image.alt = '';
            pdf.hidden = true;
            pdf.removeAttribute('src');
        };

        document.querySelectorAll('[data-document-preview]').forEach(function (button) {
            button.addEventListener('click', function () {
                resetDocumentViewer();
                const previewUrl = button.dataset.previewUrl;
                const contentType = button.dataset.previewType;
                title.textContent = button.dataset.previewTitle || 'Documento';
                download.href = button.dataset.downloadUrl;
                if (contentType === 'application/pdf') {
                    pdf.src = previewUrl;
                    pdf.hidden = false;
                } else {
                    image.src = previewUrl;
                    image.alt = title.textContent;
                    image.hidden = false;
                }
                documentDialog.showModal();
            });
        });

        documentDialog.querySelector('[data-document-close]').addEventListener(
            'click',
            function () {
                documentDialog.close();
            }
        );
        documentDialog.addEventListener('close', resetDocumentViewer);
        documentDialog.addEventListener('click', function (event) {
            if (event.target === documentDialog) documentDialog.close();
        });
    }

    const cameraRoot = document.querySelector('[data-camera-capture]');
    if (cameraRoot) {
        const video = cameraRoot.querySelector('[data-camera-video]');
        const canvas = cameraRoot.querySelector('[data-camera-canvas]');
        const preview = cameraRoot.querySelector('[data-camera-preview]');
        const placeholder = cameraRoot.querySelector('[data-camera-placeholder]');
        const message = cameraRoot.querySelector('[data-camera-message]');
        const startButton = cameraRoot.querySelector('[data-camera-start]');
        const shootButton = cameraRoot.querySelector('[data-camera-shoot]');
        const repeatButton = cameraRoot.querySelector('[data-camera-repeat]');
        const confirmButton = cameraRoot.querySelector('[data-camera-confirm]');
        const title = cameraRoot.querySelector('[data-camera-side-title]');
        const help = cameraRoot.querySelector('[data-camera-side-help]');
        const csrf = cameraRoot.querySelector(
            '[name="csrfmiddlewaretoken"]'
        ).value;
        let stream = null;
        let captureBlob = null;
        let previewUrl = '';
        let side = cameraRoot.dataset.initialSide || 'frente';
        const requiresBack = cameraRoot.dataset.requiresBack === 'true';
        let replacementSide = '';

        const setSide = function (newSide) {
            side = newSide;
            const front = side === 'frente';
            title.textContent = front ? 'Parte frontal' : 'Parte posterior';
            help.textContent = front
                ? 'Ubica el frente completo dentro del encuadre, con buena luz y sin reflejos.'
                : 'Gira el documento y ubica el reverso completo dentro del encuadre.';
        };

        const stopCamera = function () {
            if (stream) {
                stream.getTracks().forEach(function (track) {
                    track.stop();
                });
            }
            stream = null;
            video.srcObject = null;
            video.hidden = true;
        };

        const clearPreview = function () {
            captureBlob = null;
            if (previewUrl) URL.revokeObjectURL(previewUrl);
            previewUrl = '';
            preview.removeAttribute('src');
            preview.hidden = true;
        };

        const showMessage = function (text) {
            message.textContent = text;
            message.hidden = false;
        };

        const hideMessage = function () {
            message.textContent = '';
            message.hidden = true;
        };

        const startCamera = async function () {
            hideMessage();
            clearPreview();
            if (!window.isSecureContext) {
                showMessage('La camara requiere HTTPS o un entorno local seguro.');
                return;
            }
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                showMessage(
                    'Este dispositivo o navegador no ofrece una camara compatible.'
                );
                return;
            }
            try {
                const rearCamera = {
                    video: {
                        facingMode: { exact: 'environment' },
                        width: { ideal: 1920 },
                        height: { ideal: 1080 }
                    },
                    audio: false
                };
                try {
                    stream = await navigator.mediaDevices.getUserMedia(rearCamera);
                } catch (cameraError) {
                    if (
                        !cameraError
                        || !['OverconstrainedError', 'ConstraintNotSatisfiedError']
                            .includes(cameraError.name)
                    ) throw cameraError;
                    stream = await navigator.mediaDevices.getUserMedia({
                        video: {
                            facingMode: { ideal: 'environment' },
                            width: { ideal: 1920 },
                            height: { ideal: 1080 }
                        },
                        audio: false
                    });
                }
                video.srcObject = stream;
                video.hidden = false;
                placeholder.hidden = true;
                startButton.hidden = true;
                shootButton.hidden = false;
            } catch (error) {
                if (error && error.name === 'NotAllowedError') {
                    showMessage(
                        'El permiso de camara fue rechazado. Habilitalo en el navegador para continuar.'
                    );
                } else if (
                    error
                    && ['NotFoundError', 'DevicesNotFoundError'].includes(error.name)
                ) {
                    showMessage('No se encontro una camara disponible.');
                } else {
                    showMessage(
                        'No fue posible iniciar la camara. Revisa permisos y disponibilidad.'
                    );
                }
                stopCamera();
                placeholder.hidden = false;
            }
        };

        startButton.addEventListener('click', startCamera);
        shootButton.addEventListener('click', function () {
            if (!stream || !video.videoWidth || !video.videoHeight) {
                showMessage('Espera a que la vista de la camara este lista.');
                return;
            }
            hideMessage();
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            canvas.getContext('2d').drawImage(
                video,
                0,
                0,
                canvas.width,
                canvas.height
            );
            canvas.toBlob(function (blob) {
                if (!blob) {
                    showMessage('No fue posible generar la captura.');
                    return;
                }
                captureBlob = blob;
                previewUrl = URL.createObjectURL(blob);
                preview.src = previewUrl;
                preview.hidden = false;
                video.hidden = true;
                shootButton.hidden = true;
                repeatButton.hidden = false;
                confirmButton.hidden = false;
            }, 'image/jpeg', 0.9);
        });

        repeatButton.addEventListener('click', function () {
            hideMessage();
            clearPreview();
            video.hidden = false;
            shootButton.hidden = false;
            repeatButton.hidden = true;
            confirmButton.hidden = true;
        });

        confirmButton.addEventListener('click', async function () {
            if (!captureBlob) return;
            hideMessage();
            confirmButton.disabled = true;
            const savedSide = side;
            const data = new FormData();
            data.append('csrfmiddlewaretoken', csrf);
            data.append('lado', savedSide);
            data.append('captura', captureBlob, savedSide + '.jpg');
            if (replacementSide === savedSide) {
                data.append('confirmar_reemplazo', '1');
            }
            try {
                const response = await fetch(cameraRoot.dataset.uploadUrl, {
                    method: 'POST',
                    body: data,
                    credentials: 'same-origin',
                    headers: { 'X-CSRFToken': csrf }
                });
                const result = await response.json();
                if (!response.ok || !result.ok) {
                    throw new Error(result.error || 'La captura fue rechazada.');
                }
                const status = document.querySelector(
                    '[data-side-status="' + savedSide + '"]'
                );
                status.classList.add('is-complete');
                status.querySelector('span').textContent =
                    'Capturada; pendiente de revision';
                stopCamera();
                clearPreview();
                repeatButton.hidden = true;
                confirmButton.hidden = true;
                startButton.hidden = false;
                placeholder.hidden = false;
                if (savedSide === 'frente' && requiresBack) setSide('reverso');
                replacementSide = '';
                showMessage(
                    'Captura guardada de forma privada. Continua con '
                    + (
                        savedSide === 'reverso' || !requiresBack
                            ? 'el siguiente requisito.'
                            : 'el reverso.'
                    )
                );
            } catch (error) {
                showMessage(error.message || 'No fue posible guardar la captura.');
            } finally {
                confirmButton.disabled = false;
            }
        });

        document.querySelectorAll('[data-camera-cancel]').forEach(function (link) {
            link.addEventListener('click', stopCamera);
        });
        document.querySelectorAll('[data-camera-replace]').forEach(function (button) {
            button.addEventListener('click', function () {
                const selectedSide = button.dataset.cameraReplace;
                if (!window.confirm('Esta accion reemplazara la captura existente.')) {
                    return;
                }
                replacementSide = selectedSide;
                setSide(selectedSide);
                startCamera();
            });
        });
        window.addEventListener('pagehide', stopCamera);
        window.addEventListener('beforeunload', stopCamera);
        setSide(side);
    }

    const simulator = document.querySelector('[data-education-simulator]');
    if (simulator) {
        const form = simulator.querySelector('[data-simulator-form]');
        const amount = form.querySelector('[name="monto_solicitado"]');
        const term = form.querySelector('[name="plazo_meses"]');
        const error = simulator.querySelector('[data-simulator-error]');
        const status = simulator.querySelector('[data-simulator-status]');
        const results = simulator.querySelector('.edu-simulator-results');
        const version = simulator.querySelector('[data-simulator-version]');
        const csrf = form.querySelector('[name="csrfmiddlewaretoken"]').value;
        const cop = new Intl.NumberFormat('es-CO', {
            style: 'currency',
            currency: 'COP',
            maximumFractionDigits: 0
        });
        let timer = null;
        let activeRequest = null;

        function percent(value) {
            return String(value).replace(/\.0+$/, '').replace('.', ',');
        }

        function showSimulation(data) {
            simulator.querySelectorAll('[data-simulator-result]').forEach(function (node) {
                const value = data[node.dataset.simulatorResult];
                node.textContent = cop.format(Number(value));
            });
            simulator.querySelectorAll('[data-simulator-rate]').forEach(function (node) {
                const key = node.dataset.simulatorRate;
                const suffix = key === 'tasa_interes_mensual' ? ' % mensual' : ' %';
                node.textContent = '(' + percent(data[key]) + suffix + ')';
            });
            simulator.querySelectorAll('[data-simulator-provider]').forEach(function (node) {
                node.textContent = data[node.dataset.simulatorProvider];
            });
            const method = simulator.querySelector('[data-simulator-method]');
            if (method) method.textContent = data.metodo_calculo_nombre;
            const plan = document.querySelector('[data-simulator-plan]');
            if (plan) {
                plan.replaceChildren();
                data.plan.forEach(function (payment) {
                    const row = document.createElement('tr');
                    const date = new Date(payment.fecha_vencimiento + 'T00:00:00');
                    [
                        payment.numero,
                        date.toLocaleDateString('es-CO'),
                        cop.format(Number(payment.saldo_inicial)),
                        cop.format(Number(payment.interes)),
                        cop.format(Number(payment.capital)),
                        cop.format(Number(payment.valor_cuota)),
                        cop.format(Number(payment.saldo_final))
                    ].forEach(function (value) {
                        const cell = document.createElement('td');
                        cell.textContent = value;
                        row.appendChild(cell);
                    });
                    plan.appendChild(row);
                });
            }
            version.textContent = data.codigo_configuracion + ' v' + data.version_configuracion;
        }

        async function calculate() {
            if (!amount.checkValidity() || !term.checkValidity()) {
                error.textContent = 'Revisa el monto y el plazo indicados.';
                error.hidden = false;
                return;
            }
            if (activeRequest) activeRequest.abort();
            activeRequest = new AbortController();
            results.setAttribute('aria-busy', 'true');
            status.textContent = 'Actualizando resultado...';
            error.hidden = true;
            const data = new FormData(form);
            try {
                const response = await fetch(simulator.dataset.calculateUrl, {
                    method: 'POST',
                    body: data,
                    credentials: 'same-origin',
                    headers: {'X-CSRFToken': csrf},
                    signal: activeRequest.signal
                });
                const payload = await response.json();
                if (!response.ok || !payload.ok) {
                    throw new Error(payload.error || 'No fue posible actualizar la simulacion.');
                }
                showSimulation(payload.simulation);
                status.textContent = 'Resultado actualizado.';
            } catch (requestError) {
                if (requestError.name === 'AbortError') return;
                error.textContent = requestError.message || 'No fue posible actualizar la simulacion.';
                error.hidden = false;
                status.textContent = '';
            } finally {
                results.setAttribute('aria-busy', 'false');
            }
        }

        function scheduleCalculation() {
            window.clearTimeout(timer);
            timer = window.setTimeout(calculate, 300);
        }

        amount.addEventListener('input', scheduleCalculation);
        term.addEventListener('input', scheduleCalculation);
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            calculate();
        });
    }

    const scrollTarget = document.querySelector('[data-scroll-on-load]');
    if (scrollTarget) {
        window.requestAnimationFrame(function () {
            scrollTarget.scrollIntoView({ block: 'start' });
        });
    }

    if (window.location.hash) {
        window.addEventListener('load', function () {
            const target = document.getElementById(
                decodeURIComponent(window.location.hash.slice(1))
            );
            if (target) target.scrollIntoView({ block: 'center' });
        });
    }
});
