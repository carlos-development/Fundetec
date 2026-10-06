'use strict';
(() => {
    const button = document.getElementById('copy-breb');
    const key = document.getElementById('breb-key');
    const result = document.getElementById('copy-result');
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
