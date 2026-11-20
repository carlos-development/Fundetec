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


    const simulator = document.querySelector('[data-education-simulator]');
    if (simulator && window.fetch && window.AbortController) {
        const form = simulator.querySelector('[data-simulator-form]');
        const amount = form.querySelector('[name="monto_solicitado"]');
        const term = form.querySelector('[name="plazo_meses"]');
        const error = simulator.querySelector('[data-simulator-error]');
        const status = simulator.querySelector('[data-simulator-status]');
        const results = simulator.querySelector('.edu-simulator-results');
        const version = simulator.querySelector('[data-simulator-version]');
        const retry = form.querySelector('[data-simulator-retry]');
        const manual = form.querySelector('[data-simulator-manual]');
        const heading = simulator.querySelector('[data-estimate-title]');
        const scenario = simulator.querySelector('[data-estimate-for]');
        const placeholder = simulator.querySelector('[data-simulator-placeholder]');
        const csrf = form.querySelector('[name="csrfmiddlewaretoken"]').value;
        const cop = new Intl.NumberFormat('es-CO', {
            style: 'currency',
            currency: 'COP',
            maximumFractionDigits: 0
        });
        const amountFormat = new Intl.NumberFormat('es-CO', {
            style: 'currency', currency: 'COP', minimumFractionDigits: 0, maximumFractionDigits: 2
        });
        const planSection = simulator.querySelector('.edu-simulator-plan');
        const detailScenario = planSection.querySelector('[data-detail-scenario]');
        let timer;
        let request;
        let gesture = null;
        let lastSample = 0;
        let selected = selection();
        let shown = results.hidden ? null : selected;

        function selection() {
            return {amount: amount.value, term: term.value};
        }

        function same(a, b) {
            return a && b && a.amount === b.amount && a.term === b.term;
        }

        function labelAmount(value) {
            return amountFormat.format(Number(value));
        }

        function labelTerm(value) {
            return value + (Number(value) === 1 ? ' mes' : ' meses');
        }

        function describeEstimate() {
            if (!shown) return;
            const current = same(selected, shown);
            heading.textContent = current ? 'Resultado informativo' : 'Última estimación';
            scenario.textContent = 'Para ' + labelAmount(shown.amount) + ' a ' + labelTerm(shown.term);
            detailScenario.textContent = heading.textContent + ' · ' + scenario.textContent;
        }

        function cancelRequest() {
            if (request) request.controller.abort();
            request = null;
            results.setAttribute('aria-busy', 'false');
        }

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
            window.clearTimeout(timer);
            timer = null;
            if (same(request?.values, selected)) return;
            cancelRequest();
            if (!amount.checkValidity() || !term.checkValidity()) {
                error.textContent = 'Revisa el monto y el plazo indicados.';
                error.hidden = false;
                status.textContent = '';
                return;
            }
            if (same(shown, selected)) {
                status.textContent = 'Resultado actualizado.';
                return;
            }
            const pending = {values: selected, gesture: gesture, controller: new AbortController()};
            request = pending;
            lastSample = performance.now();
            results.setAttribute('aria-busy', 'true');
            status.textContent = 'Calculando…';
            retry.hidden = true;
            error.hidden = true;
            try {
                const response = await fetch(simulator.dataset.calculateUrl, {
                    method: 'POST', body: new FormData(form), credentials: 'same-origin',
                    headers: {'X-CSRFToken': csrf}, signal: pending.controller.signal
                });
                const payload = await response.json();
                if (request !== pending) return;
                const current = same(selected, pending.values);
                // During one drag a completed sample may be shown with its own inputs.
                // A release or a newer request retires that sample.
                if (!current && (!gesture || gesture !== pending.gesture || same(shown, selected))) return;
                if (!response.ok || !payload.ok) {
                    throw new Error(payload.error || 'No fue posible actualizar la simulación.');
                }
                showSimulation(payload.simulation);
                shown = pending.values;
                describeEstimate();
                results.hidden = false;
                placeholder.hidden = true;
                planSection.hidden = false;
                status.textContent = current ? 'Resultado actualizado.' : 'Calculando…';
            } catch (requestError) {
                if (request !== pending || !same(selected, pending.values)) return;
                error.textContent = requestError instanceof TypeError || requestError instanceof SyntaxError
                    ? 'No se pudo consultar la cuota. Revisa tu conexión y vuelve a intentar.'
                    : requestError.message;
                retry.hidden = false;
                error.hidden = false;
                status.textContent = '';
            } finally {
                if (request === pending) {
                    request = null;
                    results.setAttribute('aria-busy', 'false');
                    if (gesture && !same(selected, pending.values)) queueSample();
                }
            }
        }

        function queueSample() {
            if (request || timer != null) return;
            // Allow a slow intermediate request to finish. Release flushes the final selection.
            const delay = lastSample ? Math.max(0, 2200 - (performance.now() - lastSample)) : 300;
            timer = window.setTimeout(calculate, delay);
        }

        function showSelection(input) {
            if (input.type !== 'range') return;
            const value = input === amount ? labelAmount(input.value) : labelTerm(input.value);
            form.querySelector('[data-selection="' + input.name + '"]').textContent = value;
            input.setAttribute('aria-valuetext', value);
            const span = Number(input.max) - Number(input.min);
            const progress = span ? 100 * (Number(input.value) - Number(input.min)) / span : 0;
            input.style.setProperty('--range-progress', progress + '%');
        }

        function changed(input) {
            showSelection(input);
            const values = selection();
            if (same(values, selected)) return;
            selected = values;
            describeEstimate();
            error.hidden = true;
            retry.hidden = true;
            status.textContent = 'Calculando…';
            if (gesture) {
                queueSample();
            } else {
                cancelRequest();
                window.clearTimeout(timer);
                timer = window.setTimeout(calculate, 300);
            }
        }

        function beginGesture(input) {
            if (gesture?.input === input) return;
            window.clearTimeout(timer);
            timer = null;
            cancelRequest();
            gesture = {input: input};
            lastSample = 0;
        }

        function finishGesture() {
            if (!gesture) return;
            gesture = null;
            calculate();
        }

        // Enhance the bound Django inputs in place. Keep both fields when an amount cap is absent.
        const useRanges = [amount, term].every(input => input.min && input.max && input.validity.valid);
        const rangeKeys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'];
        [amount, term].forEach(function (input) {
            if (useRanges) {
                input.type = 'range';
                input.classList.add('edu-simulator-range');
                form.querySelector('[data-selection="' + input.name + '"]').hidden = false;
                showSelection(input);
                input.addEventListener('pointerdown', () => beginGesture(input));
                input.addEventListener('keydown', event => {
                    if (rangeKeys.includes(event.key)) beginGesture(input);
                });
                input.addEventListener('keyup', event => {
                    if (rangeKeys.includes(event.key)) finishGesture();
                });
                input.addEventListener('blur', finishGesture);
            }
            input.addEventListener('input', () => changed(input));
            input.addEventListener('change', () => {
                changed(input);
                if (!gesture) calculate();
            });
        });
        window.addEventListener('pointerup', finishGesture);
        window.addEventListener('pointercancel', finishGesture);
        describeEstimate();
        retry.addEventListener('click', calculate);
        if (results.hidden && !error.hidden && form.checkValidity()) retry.hidden = false;
        manual.hidden = true;
        window.addEventListener('pagehide', function () {
            window.clearTimeout(timer);
            cancelRequest();
            gesture = null;
        });
        window.addEventListener('pageshow', event => {
            if (event.persisted && !same(shown, selected)) calculate();
        });
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
