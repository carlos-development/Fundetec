'use strict';
(() => {
    const button = document.getElementById('copy-breb');
    const key = document.getElementById('breb-key');
    const result = document.getElementById('copy-result');
    const reportLink = document.querySelector('a[href="#formulario-reporte"]');
    const reportForm = document.getElementById('formulario-reporte');
    if (reportLink && reportForm) reportLink.addEventListener('click', () => reportForm.focus());

    const difference = document.getElementById('diferencia-monto');
    if (difference) {
        const form = difference.closest('form');
        const amount = form.elements.monto;
        const reason = form.elements.justificacion_diferencia;
        const hasErrors = Boolean(difference.querySelector('.errorlist'));
        // Compare decimal strings without rounding financial values in JavaScript.
        const normalize = value => {
            if (!/^\d+(\.\d{1,2})?$/.test(value)) return null;
            const [whole, fraction = ''] = value.split('.');
            return whole.replace(/^0+(?=\d)/, '') + '.' + fraction.padEnd(2, '0');
        };
        const update = () => {
            const value = normalize(amount.value);
            const differs = value !== null && value !== normalize(difference.dataset.montoReportado);
            difference.open = differs || Boolean(reason.value.trim()) || hasErrors;
            reason.required = differs;
        };
        amount.addEventListener('input', update);
        update();
    }
    if (!button || !key || !result) return;
    button.hidden = false;
    button.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(key.value);
            result.textContent = 'Llave copiada.';
        } catch (_) {
            key.focus();
            key.select();
            result.textContent = 'Selecciona y copia la llave del campo.';
        }
    });
})();
