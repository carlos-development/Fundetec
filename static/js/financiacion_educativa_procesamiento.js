(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.EducationProcessing = api;
    if (root && root.document) {
        root.document.addEventListener('DOMContentLoaded', function () {
            api.initialize(root.document, root);
        });
    }
}(typeof window !== 'undefined' ? window : null, function () {
    'use strict';

    const DEFAULT_DELAYS = [1200, 2000, 3500, 5500, 8000, 12000];
    const PENDING_SIGNATURE_DELAY = 45000;
    const PENDING_SIGNATURE_MAX_DURATION = 4 * 60 * 60 * 1000;
    const STOP_STATUSES = new Set([
        'CORRECTION_REQUIRED',
        'MANUAL_EXCEPTION',
        'FAILED',
        'COMPLETED',
        'CLOSED',
        'NOT_STARTED'
    ]);
    const ACTIVE_STATUSES = new Set([
        'QUEUED',
        'RUNNING',
        'RETRYING',
        'PENDING_SIGNATURE'
    ]);
    const instances = typeof WeakMap === 'function' ? new WeakMap() : null;

    function safeSameOriginUrl(value, locationObject) {
        if (!value || !locationObject) return null;
        try {
            const parsed = new URL(value, locationObject.origin);
            if (parsed.origin !== locationObject.origin) return null;
            if (!['http:', 'https:'].includes(parsed.protocol)) return null;
            return parsed.pathname + parsed.search + parsed.hash;
        } catch (_error) {
            return null;
        }
    }

    function delayForAttempt(attempt, retryAfterSeconds) {
        if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
            return Math.min(retryAfterSeconds * 1000, 60000);
        }
        const index = Math.min(Math.max(attempt, 0), DEFAULT_DELAYS.length - 1);
        return DEFAULT_DELAYS[index];
    }

    function titleForStatus(status) {
        return {
            CORRECTION_REQUIRED: 'Necesitamos una corrección',
            MANUAL_EXCEPTION: 'Verificación adicional',
            FAILED: 'No pudimos terminar la verificación',
            PENDING_SIGNATURE: 'Tu pagaré está listo para firmar',
            COMPLETED: 'Proceso completado',
            CLOSED: 'Consulta el resultado de tu solicitud',
            NOT_STARTED: 'Continúa tu solicitud'
        }[status] || 'Estamos procesando tu expediente';
    }

    function createElement(documentObject, tag, text) {
        const element = documentObject.createElement(tag);
        if (text !== undefined) element.textContent = text;
        return element;
    }

    function createDomRenderer(container, windowObject) {
        const documentObject = container.ownerDocument;
        const message = container.querySelector('[data-processing-message]');
        const title = container.querySelector('[data-processing-title]');
        const steps = container.querySelector('[data-processing-steps]');
        const correctionSection = container.querySelector(
            '[data-processing-corrections-section]'
        );
        const correctionList = container.querySelector(
            '[data-processing-corrections]'
        );
        const action = container.querySelector('[data-processing-action]');
        const retry = container.querySelector('[data-processing-retry]');
        const timeHelp = container.querySelector('[data-processing-time]');
        const financial = container.querySelector('[data-processing-financial]');
        const money = new Intl.NumberFormat('es-CO', {
            style: 'currency',
            currency: 'COP',
            maximumFractionDigits: 0
        });

        function renderSteps(items) {
            steps.replaceChildren();
            (Array.isArray(items) ? items : []).forEach(function (item) {
                const li = createElement(documentObject, 'li');
                const state = ['complete', 'current', 'action', 'pending'].includes(
                    item.state
                ) ? item.state : 'pending';
                li.className = 'is-' + state;
                const marker = createElement(documentObject, 'span');
                marker.className = 'edu-processing-step-marker';
                marker.setAttribute('aria-hidden', 'true');
                const icon = createElement(documentObject, 'i');
                icon.className = 'bi ' + (
                    state === 'complete'
                        ? 'bi-check-lg'
                        : state === 'action'
                            ? 'bi-exclamation-lg'
                            : 'bi-circle-fill'
                );
                marker.appendChild(icon);
                li.appendChild(marker);
                li.appendChild(createElement(documentObject, 'span', item.label || ''));
                steps.appendChild(li);
            });
        }

        function renderCorrections(items) {
            correctionList.replaceChildren();
            (Array.isArray(items) ? items : []).forEach(function (item) {
                const li = createElement(documentObject, 'li');
                li.appendChild(createElement(documentObject, 'span', item.message || ''));
                const safeUrl = safeSameOriginUrl(
                    item.action && item.action.url,
                    windowObject.location
                );
                if (safeUrl) {
                    const link = createElement(
                        documentObject,
                        'a',
                        item.action.label || 'Corregir'
                    );
                    link.href = safeUrl;
                    li.appendChild(link);
                }
                correctionList.appendChild(li);
            });
            correctionSection.hidden = !items || !items.length;
        }

        function renderFinancial(terms) {
            if (!terms) {
                financial.hidden = true;
                return;
            }
            const values = {
                '[data-financial-requested]': terms.requested_amount,
                '[data-financial-financed]': terms.financed_amount,
                '[data-financial-installment]': terms.estimated_installment
            };
            Object.keys(values).forEach(function (selector) {
                const value = Number(values[selector]);
                financial.querySelector(selector).textContent = (
                    Number.isFinite(value) ? money.format(value) : ''
                );
            });
            financial.querySelector('[data-financial-term]').textContent =
                String(terms.term_months || '') + ' meses';
            financial.hidden = false;
        }

        function render(data) {
            const status = data.status || 'FAILED';
            container.classList.toggle('is-terminal', STOP_STATUSES.has(status));
            container.classList.toggle('is-complete', status === 'COMPLETED');
            container.classList.remove('is-paused');
            title.textContent = titleForStatus(status);
            message.textContent = data.message || 'Consulta el estado de tu expediente.';
            renderSteps(data.steps);
            renderCorrections(data.correction_requirements);
            renderFinancial(status === 'COMPLETED' ? data.financial_terms : null);
            const safeAction = safeSameOriginUrl(
                data.action && data.action.url,
                windowObject.location
            );
            action.hidden = !safeAction;
            if (safeAction) {
                action.href = safeAction;
                action.textContent = data.action.label || 'Continuar';
            } else {
                action.removeAttribute('href');
                action.textContent = '';
            }
            retry.hidden = status !== 'PENDING_SIGNATURE';
            timeHelp.hidden = STOP_STATUSES.has(status);
        }

        function showConnectionIssue(text, canRetry) {
            container.classList.add('is-paused');
            message.textContent = text;
            retry.hidden = !canRetry;
        }

        return { render, showConnectionIssue, retry };
    }

    function createPollingController(options) {
        const fetchFunction = options.fetchFunction;
        const renderer = options.renderer;
        const schedule = options.schedule || setTimeout;
        const cancelSchedule = options.cancelSchedule || clearTimeout;
        const now = options.now || Date.now;
        const maxVisualDuration = options.maxVisualDuration || 300000;
        const pendingSignatureDelay = (
            options.pendingSignatureDelay || PENDING_SIGNATURE_DELAY
        );
        const pendingSignatureMaxDuration = (
            options.pendingSignatureMaxDuration
            || PENDING_SIGNATURE_MAX_DURATION
        );
        let running = false;
        let visible = true;
        let timer = null;
        let requestController = null;
        let refreshWhenIdle = false;
        let attempt = 0;
        let startedAt = null;
        let statusStartedAt = null;
        let lastStatus = options.initialStatus || '';

        function clearTimer() {
            if (timer !== null) cancelSchedule(timer);
            timer = null;
        }

        function stop() {
            running = false;
            refreshWhenIdle = false;
            clearTimer();
            if (requestController) requestController.abort();
            requestController = null;
        }

        function scheduleNext(delay) {
            if (!running || !visible) return;
            clearTimer();
            timer = schedule(poll, delay);
        }

        function delayForStatus(retryAfterSeconds) {
            const normalDelay = delayForAttempt(attempt++, retryAfterSeconds);
            if (lastStatus === 'PENDING_SIGNATURE') {
                return Math.max(pendingSignatureDelay, normalDelay);
            }
            return normalDelay;
        }

        function durationLimit() {
            return lastStatus === 'PENDING_SIGNATURE'
                ? pendingSignatureMaxDuration
                : maxVisualDuration;
        }

        function parseRetryAfter(response) {
            const raw = response.headers && response.headers.get
                ? Number(response.headers.get('Retry-After'))
                : NaN;
            return Number.isFinite(raw) ? raw : null;
        }

        async function poll() {
            if (!running || requestController) return;
            clearTimer();
            if (now() - (statusStartedAt || startedAt) >= durationLimit()) {
                running = false;
                renderer.showConnectionIssue(
                    'El proceso continúa en segundo plano. Consulta nuevamente para actualizar el estado.',
                    true
                );
                return;
            }
            requestController = typeof AbortController === 'function'
                ? new AbortController()
                : { signal: undefined, abort: function () {} };
            try {
                const response = await fetchFunction(options.url, {
                    method: 'GET',
                    credentials: 'same-origin',
                    cache: 'no-store',
                    headers: { Accept: 'application/json' },
                    signal: requestController.signal
                });
                if ([401, 403, 404].includes(response.status)) {
                    running = false;
                    renderer.showConnectionIssue(
                        'No fue posible consultar este expediente con la sesión actual.',
                        false
                    );
                    return;
                }
                if (response.status === 429) {
                    renderer.showConnectionIssue(
                        'La consulta está tardando más de lo habitual. Seguiremos intentando.',
                        false
                    );
                    scheduleNext(delayForStatus(parseRetryAfter(response)));
                    return;
                }
                if (response.status >= 500) {
                    renderer.showConnectionIssue(
                        'La conexión se interrumpió temporalmente. Seguiremos intentando.',
                        true
                    );
                    scheduleNext(delayForStatus());
                    return;
                }
                if (!response.ok) throw new Error('UNEXPECTED_RESPONSE');
                const data = await response.json();
                if (data.status !== lastStatus) {
                    const previousStatus = lastStatus;
                    lastStatus = data.status;
                    statusStartedAt = now();
                    if (
                        data.status === 'PENDING_SIGNATURE'
                        && previousStatus !== 'PENDING_SIGNATURE'
                    ) {
                        attempt = 0;
                    }
                }
                renderer.render(data);
                if (STOP_STATUSES.has(data.status) || data.should_poll === false) {
                    running = false;
                    return;
                }
                if (!ACTIVE_STATUSES.has(data.status)) {
                    running = false;
                    return;
                }
                scheduleNext(delayForStatus());
            } catch (error) {
                if (!running || (error && error.name === 'AbortError')) return;
                renderer.showConnectionIssue(
                    'No hay conexión en este momento. Tu proceso sigue guardado y volveremos a consultar.',
                    true
                );
                scheduleNext(delayForStatus());
            } finally {
                requestController = null;
                if (refreshWhenIdle && running && visible) {
                    refreshWhenIdle = false;
                    scheduleNext(0);
                }
            }
        }

        function start(immediate) {
            if (running) return false;
            running = true;
            attempt = 0;
            startedAt = now();
            statusStartedAt = startedAt;
            scheduleNext(
                immediate
                    ? 0
                    : (
                        lastStatus === 'PENDING_SIGNATURE'
                            ? pendingSignatureDelay
                            : DEFAULT_DELAYS[0]
                    )
            );
            return true;
        }

        function pollNow() {
            if (requestController || !visible) return false;
            if (!running) return start(true);
            attempt = 0;
            statusStartedAt = now();
            scheduleNext(0);
            return true;
        }

        function setVisible(isVisible) {
            const nextVisible = Boolean(isVisible);
            if (visible === nextVisible) return false;
            visible = nextVisible;
            if (!visible) {
                clearTimer();
                return true;
            }
            if (!running) return true;
            if (requestController) {
                refreshWhenIdle = true;
            } else {
                scheduleNext(0);
            }
            return true;
        }

        return {
            start,
            stop,
            pollNow,
            setVisible,
            isRunning: function () { return running; }
        };
    }

    function protectSubmit(form) {
        if (!form || form.dataset.submitProtectionBound === 'true') return false;
        form.dataset.submitProtectionBound = 'true';
        form.addEventListener('submit', function (event) {
            if (form.dataset.submitting === 'true') {
                event.preventDefault();
                return;
            }
            if (!form.checkValidity()) return;
            form.dataset.submitting = 'true';
            form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(
                function (button) { button.disabled = true; }
            );
        });
        return true;
    }

    function initialize(documentObject, windowObject) {
        documentObject.querySelectorAll('[data-submit-once]').forEach(protectSubmit);
        documentObject.querySelectorAll('[data-education-processing]').forEach(
            function (container) {
                if (instances && instances.has(container)) return;
                if (
                    windowObject.matchMedia
                    && windowObject.matchMedia('(prefers-reduced-motion: reduce)').matches
                ) {
                    container.classList.add('prefers-reduced-motion');
                }
                const renderer = createDomRenderer(container, windowObject);
                const controller = createPollingController({
                    url: container.dataset.statusUrl,
                    fetchFunction: windowObject.fetch.bind(windowObject),
                    renderer: renderer,
                    initialStatus: container.dataset.initialStatus
                });
                if (instances) instances.set(container, controller);
                renderer.retry.addEventListener('click', function () {
                    controller.pollNow();
                });
                const handleVisibility = function () {
                    controller.setVisible(documentObject.visibilityState !== 'hidden');
                };
                const stop = function () {
                    documentObject.removeEventListener(
                        'visibilitychange',
                        handleVisibility
                    );
                    controller.stop();
                };
                documentObject.addEventListener(
                    'visibilitychange',
                    handleVisibility
                );
                controller.setVisible(documentObject.visibilityState !== 'hidden');
                windowObject.addEventListener('pagehide', stop, { once: true });
                const initial = container.dataset.initialStatus;
                if (!STOP_STATUSES.has(initial) && !['CLOSED', 'NOT_STARTED'].includes(initial)) {
                    controller.start(false);
                }
            }
        );
    }

    return {
        ACTIVE_STATUSES,
        PENDING_SIGNATURE_DELAY,
        PENDING_SIGNATURE_MAX_DURATION,
        STOP_STATUSES,
        createPollingController,
        delayForAttempt,
        initialize,
        protectSubmit,
        safeSameOriginUrl,
        titleForStatus
    };
}));
