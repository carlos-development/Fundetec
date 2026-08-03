import math

from django.conf import settings
from django.core.checks import Error, register
from django.utils.module_loading import import_string


CLAMAV_BACKEND = (
    'financiacion_educativa.services.escaneo_documentos.'
    'ClamAVDocumentScanBackend'
)


def _error(message, identifier):
    return Error(message, id=f'financiacion_educativa.{identifier}')


@register()
def check_document_scan_configuration(app_configs, **kwargs):
    errors = []
    backend_path = str(
        getattr(settings, 'FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND', '')
        or ''
    ).strip()
    if not backend_path:
        errors.append(_error('El backend documental no puede estar vacio.', 'E001'))
    else:
        try:
            backend_class = import_string(backend_path)
        except (ImportError, AttributeError, ValueError):
            errors.append(_error('El backend documental no puede importarse.', 'E002'))
        else:
            if not callable(getattr(backend_class, 'escanear', None)):
                errors.append(
                    _error('El backend documental no implementa escanear().', 'E003')
                )
            if (
                backend_path.startswith('financiacion_educativa.tests.')
                and not getattr(
                    settings,
                    'FINANCIACION_EDUCATIVA_ALLOW_TEST_SCAN_BACKENDS',
                    False,
                )
            ):
                errors.append(
                    _error(
                        'Los backends de prueba requieren habilitacion explicita.',
                        'E004',
                    )
                )

    enteros_positivos = (
        ('FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS', 'E005'),
        ('FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS', 'E006'),
        ('FINANCIACION_EDUCATIVA_SCAN_MAX_REOPENINGS', 'E009'),
        ('FINANCIACION_EDUCATIVA_SCAN_REOPEN_EXTRA_ATTEMPTS', 'E010'),
    )
    for nombre, identifier in enteros_positivos:
        valor = getattr(settings, nombre, None)
        if isinstance(valor, bool) or not isinstance(valor, int):
            errors.append(
                _error(f'{nombre} debe ser un entero positivo.', identifier)
            )
        elif valor <= 0:
            errors.append(_error(f'{nombre} debe ser positivo.', identifier))

    numeros_positivos = (
        ('FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS', 'E007'),
        ('FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS', 'E008'),
    )
    for nombre, identifier in numeros_positivos:
        valor = getattr(settings, nombre, None)
        if (
            isinstance(valor, bool)
            or not isinstance(valor, (int, float))
            or not math.isfinite(valor)
        ):
            errors.append(
                _error(f'{nombre} debe ser un numero positivo finito.', identifier)
            )
        elif valor <= 0:
            errors.append(_error(f'{nombre} debe ser positivo.', identifier))

    puerto = getattr(settings, 'FINANCIACION_EDUCATIVA_CLAMAV_PORT', 0)
    if (
        isinstance(puerto, bool)
        or not isinstance(puerto, int)
        or not 1 <= puerto <= 65535
    ):
        errors.append(_error('El puerto TCP de ClamAV no es valido.', 'E011'))

    if backend_path == CLAMAV_BACKEND:
        unix_socket = str(
            getattr(settings, 'FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET', '')
            or ''
        ).strip()
        host = str(
            getattr(settings, 'FINANCIACION_EDUCATIVA_CLAMAV_HOST', '') or ''
        ).strip()
        if bool(unix_socket) == bool(host):
            errors.append(
                _error(
                    'Configura exactamente un destino ClamAV: socket Unix o TCP.',
                    'E012',
                )
            )
    return errors
