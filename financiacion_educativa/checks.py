import ipaddress
import math
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.checks import Error, register
from django.utils.module_loading import import_string


CLAMAV_BACKEND = (
    'financiacion_educativa.services.escaneo_documentos.'
    'ClamAVDocumentScanBackend'
)
OPENAI_DOCUMENT_AI_BACKEND = (
    'financiacion_educativa.services.validacion_documental_ia.'
    'OpenAIDocumentAIValidationBackend'
)
ZAPSIGN_EDUCATIONAL_BACKEND = (
    'financiacion_educativa.services.firma_zapsign.'
    'ZapSignEducationalSignatureBackend'
)
DISABLED_DOCUMENT_AI_BACKEND = (
    'financiacion_educativa.services.validacion_documental_ia.'
    'DisabledDocumentAIValidationBackend'
)
DISABLED_EDUCATIONAL_SIGNATURE_BACKEND = (
    'financiacion_educativa.services.firma_zapsign.'
    'DisabledEducationalSignatureBackend'
)
DISABLED_CONTENT_BACKEND = (
    'financiacion_educativa.services.clasificacion_contenido_documental.'
    'DisabledContentDocumentClassificationBackend'
)
OPENAI_CONTENT_BACKEND = (
    'financiacion_educativa.services.clasificacion_contenido_documental.'
    'OpenAIContentDocumentClassificationBackend'
)


def _error(message, identifier):
    return Error(message, id=f'financiacion_educativa.{identifier}')


@register()
def check_public_simulator_configuration(app_configs, **kwargs):
    errors = []
    values = {}
    for name, identifier in (
        ('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MIN_AMOUNT', 'E070'),
        ('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MAX_AMOUNT', 'E071'),
        ('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_AMOUNT', 'E072'),
        ('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MIN_TERM_MONTHS', 'E073'),
        ('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MAX_TERM_MONTHS', 'E074'),
        ('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_TERM_MONTHS', 'E075'),
        ('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_RATE_LIMIT_REQUESTS', 'E076'),
        (
            'FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_RATE_LIMIT_WINDOW_SECONDS',
            'E077',
        ),
    ):
        value = getattr(settings, name, None)
        values[name] = value
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            errors.append(_error(f'{name} debe ser un entero positivo.', identifier))

    if not errors:
        if values['FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MIN_AMOUNT'] > values[
            'FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MAX_AMOUNT'
        ]:
            errors.append(_error('El rango de monto del simulador no es valido.', 'E078'))
        if not (
            values['FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MIN_AMOUNT']
            <= values['FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_AMOUNT']
            <= values['FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MAX_AMOUNT']
        ):
            errors.append(_error('El monto inicial debe estar dentro del rango.', 'E079'))
        if values['FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MIN_TERM_MONTHS'] > values[
            'FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MAX_TERM_MONTHS'
        ]:
            errors.append(_error('El rango de plazo del simulador no es valido.', 'E080'))
        if not (
            values['FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MIN_TERM_MONTHS']
            <= values['FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_TERM_MONTHS']
            <= values['FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MAX_TERM_MONTHS']
        ):
            errors.append(_error('El plazo inicial debe estar dentro del rango.', 'E081'))

    proxies = getattr(
        settings,
        'FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_TRUSTED_PROXY_IPS',
        (),
    )
    if not isinstance(proxies, (list, tuple)):
        errors.append(_error('Los proxies del simulador deben ser una lista.', 'E082'))
    else:
        for proxy in proxies:
            try:
                ipaddress.ip_address(proxy)
            except ValueError:
                errors.append(_error('Existe un proxy del simulador invalido.', 'E083'))
                break
    return errors


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


@register()
def check_document_ai_configuration(app_configs, **kwargs):
    errors = []
    backend_path = str(
        getattr(settings, 'FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND', '') or ''
    ).strip()
    if not backend_path:
        errors.append(_error('El backend de IA documental no puede estar vacio.', 'E020'))
    else:
        try:
            backend_class = import_string(backend_path)
        except (ImportError, AttributeError, ValueError):
            errors.append(_error('El backend de IA documental no puede importarse.', 'E021'))
        else:
            if not callable(getattr(backend_class, 'validar', None)):
                errors.append(
                    _error('El backend de IA documental no implementa validar().', 'E022')
                )
            if (
                backend_path.startswith('financiacion_educativa.tests.')
                and not getattr(
                    settings,
                    'FINANCIACION_EDUCATIVA_ALLOW_TEST_AI_BACKENDS',
                    False,
                )
            ):
                errors.append(
                    _error(
                        'Los backends IA de prueba requieren habilitacion explicita.',
                        'E023',
                    )
                )

    for nombre, identifier in (
        ('FINANCIACION_EDUCATIVA_DOCUMENT_AI_MAX_ATTEMPTS', 'E024'),
        ('FINANCIACION_EDUCATIVA_DOCUMENT_AI_STALE_SECONDS', 'E025'),
        ('FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_WIDTH', 'E032'),
        ('FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_HEIGHT', 'E033'),
    ):
        valor = getattr(settings, nombre, None)
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            errors.append(_error(f'{nombre} debe ser un entero positivo.', identifier))

    timeout = getattr(
        settings,
        'FINANCIACION_EDUCATIVA_DOCUMENT_AI_TIMEOUT_SECONDS',
        None,
    )
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        errors.append(_error('El timeout de IA debe ser positivo y finito.', 'E026'))

    for nombre, identifier in (
        ('FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_CONFIDENCE', 'E027'),
        ('FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_QUALITY', 'E028'),
        ('FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_LEGIBILITY', 'E029'),
        (
            'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_DIMENSION_CONFIDENCE',
            'E128',
        ),
    ):
        try:
            valor = Decimal(str(getattr(settings, nombre, '')))
        except (InvalidOperation, TypeError, ValueError):
            valor = None
        if valor is None or not valor.is_finite() or not 0 <= valor <= 1:
            errors.append(_error(f'{nombre} debe estar entre 0 y 1.', identifier))

    if backend_path == OPENAI_DOCUMENT_AI_BACKEND:
        if not getattr(
            settings,
            'FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED',
            False,
        ):
            errors.append(_error('La validacion IA debe habilitarse explicitamente.', 'E034'))
        if not str(
            getattr(settings, 'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MODEL', '') or ''
        ).strip():
            errors.append(_error('El modelo de IA documental es obligatorio.', 'E030'))
        if not str(getattr(settings, 'OPENAI_API_KEY', '') or '').strip():
            errors.append(_error('La credencial del proveedor IA es obligatoria.', 'E031'))
    return errors


@register()
def check_document_content_configuration(app_configs, **kwargs):
    errors = []
    limites = (
        ('FINANCIACION_EDUCATIVA_PDF_MAX_BYTES', 'E090'),
        ('FINANCIACION_EDUCATIVA_PDF_MAX_PAGES', 'E091'),
        ('FINANCIACION_EDUCATIVA_PDF_MAX_OBJECTS', 'E092'),
        ('FINANCIACION_EDUCATIVA_PDF_MAX_OBJECT_BYTES', 'E115'),
        ('FINANCIACION_EDUCATIVA_PDF_MAX_PIXELS_PER_PAGE', 'E093'),
        ('FINANCIACION_EDUCATIVA_PDF_MAX_AI_PAGES', 'E094'),
        ('FINANCIACION_EDUCATIVA_PDF_MAX_EXTRACTED_CHARACTERS', 'E095'),
        ('FINANCIACION_EDUCATIVA_CONTENT_STALE_SECONDS', 'E096'),
        ('FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS', 'E121'),
        ('FINANCIACION_EDUCATIVA_PDF_MAX_MEMORY_MB', 'E116'),
    )
    valores = {}
    for nombre, identificador in limites:
        valor = getattr(settings, nombre, None)
        valores[nombre] = valor
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            errors.append(_error(f'{nombre} debe ser un entero positivo.', identificador))
    timeout = getattr(
        settings, 'FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS', None
    )
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        errors.append(_error('El timeout de procesamiento PDF no es valido.', 'E097'))
    for nombre, identificador in (
        ('FINANCIACION_EDUCATIVA_CONTENT_MIN_CONFIDENCE', 'E098'),
        ('FINANCIACION_EDUCATIVA_CONTENT_MIN_LEGIBILITY', 'E099'),
        ('FINANCIACION_EDUCATIVA_CONTENT_MIN_COMPLETENESS', 'E100'),
    ):
        try:
            valor = Decimal(str(getattr(settings, nombre, '')))
        except (InvalidOperation, TypeError, ValueError):
            valor = None
        if valor is None or not valor.is_finite() or not 0 <= valor <= 1:
            errors.append(_error(f'{nombre} debe estar entre 0 y 1.', identificador))
    if not errors:
        if valores['FINANCIACION_EDUCATIVA_PDF_MAX_BYTES'] > getattr(
            settings, 'FINANCIACION_EDUCATIVA_DOCUMENT_MAX_BYTES', 0
        ):
            errors.append(_error('El limite PDF no puede superar el limite documental.', 'E101'))
        if valores['FINANCIACION_EDUCATIVA_PDF_MAX_AI_PAGES'] > valores[
            'FINANCIACION_EDUCATIVA_PDF_MAX_PAGES'
        ]:
            errors.append(_error('Las paginas IA no pueden superar el maximo PDF.', 'E102'))
        if valores['FINANCIACION_EDUCATIVA_PDF_MAX_PAGES'] > 100:
            errors.append(_error('El limite de paginas PDF es excesivo.', 'E103'))
        if valores['FINANCIACION_EDUCATIVA_PDF_MAX_PIXELS_PER_PAGE'] > 40_000_000:
            errors.append(_error('El limite de pixeles PDF es excesivo.', 'E104'))
        if valores['FINANCIACION_EDUCATIVA_PDF_MAX_MEMORY_MB'] > 2048:
            errors.append(_error('El limite de memoria PDF es excesivo.', 'E117'))
        if valores['FINANCIACION_EDUCATIVA_PDF_MAX_OBJECT_BYTES'] > valores[
            'FINANCIACION_EDUCATIVA_PDF_MAX_BYTES'
        ]:
            errors.append(_error(
                'El limite por objeto PDF no puede superar el limite del archivo.',
                'E122',
            ))
        if valores['FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS'] > 10:
            errors.append(_error(
                'El numero de intentos de contenido es excesivo.',
                'E123',
            ))
        if valores[
            'FINANCIACION_EDUCATIVA_PDF_MAX_EXTRACTED_CHARACTERS'
        ] > 1_000_000:
            errors.append(_error(
                'El limite de texto extraido es excesivo.',
                'E124',
            ))
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
        if math.isfinite(timeout) and timeout > 120:
            errors.append(_error(
                'El timeout de procesamiento PDF es excesivo.',
                'E125',
            ))

    usar_subproceso = getattr(
        settings, 'FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS', None
    )
    if not isinstance(usar_subproceso, bool):
        errors.append(_error(
            'FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS debe ser booleano.',
            'E126',
        ))

    backend_path = str(
        getattr(settings, 'FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND', '') or ''
    ).strip()
    if not backend_path:
        errors.append(_error('El backend de contenido no puede estar vacio.', 'E105'))
    else:
        try:
            backend_class = import_string(backend_path)
        except (ImportError, AttributeError, ValueError):
            errors.append(_error('El backend de contenido no puede importarse.', 'E106'))
        else:
            if not callable(getattr(backend_class, 'clasificar', None)):
                errors.append(_error('El backend de contenido no implementa clasificar().', 'E107'))
            if (
                backend_path.startswith('financiacion_educativa.tests.')
                and not getattr(
                    settings, 'FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS', False
                )
            ):
                errors.append(_error('El backend de prueba requiere habilitacion.', 'E108'))

    if getattr(settings, 'FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED', False):
        try:
            import pypdf  # noqa: F401
            import pypdfium2  # noqa: F401
        except ImportError:
            errors.append(_error('Faltan dependencias para procesar PDF.', 'E109'))
        if backend_path == DISABLED_CONTENT_BACKEND:
            errors.append(_error('El procesamiento PDF requiere clasificador habilitado.', 'E110'))
        if not str(
            getattr(settings, 'FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY', '') or ''
        ):
            errors.append(_error('La clave HMAC de contenido es obligatoria.', 'E111'))
        if not getattr(settings, 'DEBUG', False) and usar_subproceso is not True:
            errors.append(_error(
                'El procesamiento PDF requiere aislamiento por subproceso fuera de DEBUG.',
                'E127',
            ))
    if backend_path == OPENAI_CONTENT_BACKEND:
        if not getattr(settings, 'FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED', False):
            errors.append(_error('La IA documental debe habilitarse para contenido.', 'E118'))
        if not str(
            getattr(settings, 'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MODEL', '') or ''
        ).strip():
            errors.append(_error('El modelo IA de contenido es obligatorio.', 'E119'))
        if not str(getattr(settings, 'OPENAI_API_KEY', '') or '').strip():
            errors.append(_error('La credencial IA de contenido es obligatoria.', 'E120'))
    for nombre, identificador in (
        ('FINANCIACION_EDUCATIVA_CONTENT_PROCESSOR_VERSION', 'E112'),
        ('FINANCIACION_EDUCATIVA_CONTENT_SCHEMA_VERSION', 'E113'),
        ('FINANCIACION_EDUCATIVA_CONTENT_POLICY_VERSION', 'E114'),
    ):
        if not str(getattr(settings, nombre, '') or '').strip():
            errors.append(_error(f'{nombre} no puede estar vacio.', identificador))
    return errors


@register()
def check_educational_signature_configuration(app_configs, **kwargs):
    errors = []
    backend_path = str(
        getattr(settings, 'FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND', '') or ''
    ).strip()
    if not backend_path:
        errors.append(_error('El backend de firma educativa no puede estar vacio.', 'E040'))
    else:
        try:
            backend_class = import_string(backend_path)
        except (ImportError, AttributeError, ValueError):
            errors.append(_error('El backend de firma educativa no puede importarse.', 'E041'))
        else:
            if not callable(getattr(backend_class, 'enviar', None)):
                errors.append(_error('El backend de firma no implementa enviar().', 'E042'))
            if not callable(getattr(backend_class, 'descargar_firmado', None)):
                errors.append(
                    _error(
                        'El backend de firma no implementa descargar_firmado().',
                        'E043',
                    )
                )
            if (
                backend_path.startswith('financiacion_educativa.tests.')
                and not getattr(
                    settings,
                    'FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS',
                    False,
                )
            ):
                errors.append(
                    _error(
                        'Los backends de firma de prueba requieren habilitacion.',
                        'E044',
                    )
                )

    timeout = getattr(
        settings,
        'FINANCIACION_EDUCATIVA_ZAPSIGN_TIMEOUT_SECONDS',
        None,
    )
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        errors.append(_error('El timeout de firma debe ser positivo y finito.', 'E045'))
    for nombre, identifier in (
        ('FINANCIACION_EDUCATIVA_ZAPSIGN_MAX_ATTEMPTS', 'E046'),
        ('FINANCIACION_EDUCATIVA_ZAPSIGN_STALE_SECONDS', 'E047'),
    ):
        valor = getattr(settings, nombre, None)
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            errors.append(_error(f'{nombre} debe ser un entero positivo.', identifier))

    header = str(
        getattr(settings, 'FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_HEADER', '')
        or ''
    ).strip()
    if not re.fullmatch(r'[A-Za-z0-9-]{1,80}', header):
        errors.append(_error('El header del webhook de firma no es valido.', 'E048'))
    if not str(
        getattr(
            settings,
            'FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY',
            '',
        )
        or ''
    ):
        errors.append(_error('La clave HMAC de firma es obligatoria.', 'E049'))

    if backend_path == ZAPSIGN_EDUCATIONAL_BACKEND:
        for nombre, identifier, mensaje in (
            (
                'FINANCIACION_EDUCATIVA_ZAPSIGN_API_TOKEN',
                'E050',
                'La credencial ZapSign educativa es obligatoria.',
            ),
            (
                'FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET',
                'E051',
                'El secreto del webhook educativo es obligatorio.',
            ),
        ):
            if not str(getattr(settings, nombre, '') or '').strip():
                errors.append(_error(mensaje, identifier))
        base_url = str(
            getattr(settings, 'FINANCIACION_EDUCATIVA_ZAPSIGN_BASE_URL', '') or ''
        ).strip()
        if not base_url.startswith('https://'):
            errors.append(_error('ZapSign educativo requiere una URL HTTPS.', 'E052'))
        if not getattr(
            settings,
            'FINANCIACION_EDUCATIVA_ZAPSIGN_SEND_AUTOMATIC_EMAIL',
            False,
        ):
            errors.append(
                _error(
                    'Habilita el correo automatico: no se almacenan enlaces de firma.',
                    'E053',
                )
            )
        if (
            getattr(settings, 'FINANCIACION_EDUCATIVA_ZAPSIGN_REQUIRE_SELFIE', False)
            and not str(
                getattr(
                    settings,
                    'FINANCIACION_EDUCATIVA_ZAPSIGN_SELFIE_VALIDATION_TYPE',
                    '',
                )
                or ''
            ).strip()
        ):
            errors.append(
                _error(
                    'La validacion de identidad ZapSign debe configurarse.',
                    'E054',
                )
            )
    return errors


@register()
def check_educational_automation_configuration(app_configs, **kwargs):
    enabled = getattr(
        settings,
        'FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED',
        False,
    )
    if not isinstance(enabled, bool):
        return [_error('El interruptor de automatizacion debe ser booleano.', 'E060')]
    if not enabled:
        return []

    errors = []
    ai_backend = str(
        getattr(settings, 'FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND', '') or ''
    ).strip()
    signature_backend = str(
        getattr(settings, 'FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND', '') or ''
    ).strip()
    if ai_backend == DISABLED_DOCUMENT_AI_BACKEND:
        errors.append(
            _error(
                'La automatizacion requiere un backend de IA documental activo.',
                'E061',
            )
        )
    if not getattr(
        settings,
        'FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED',
        False,
    ):
        errors.append(
            _error(
                'La automatizacion requiere habilitar la IA documental.',
                'E064',
            )
        )
    if signature_backend == DISABLED_EDUCATIONAL_SIGNATURE_BACKEND:
        errors.append(
            _error(
                'La automatizacion requiere un backend de firma activo.',
                'E062',
            )
        )
    if not str(
        getattr(
            settings,
            'FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL',
            '',
        )
        or ''
    ).strip():
        errors.append(
            _error(
                'La automatizacion requiere la razon social del acreedor.',
                'E063',
            )
        )
    for nombre, identifier in (
        ('FINANCIACION_EDUCATIVA_ACREEDOR_NIT', 'E065'),
        ('FINANCIACION_EDUCATIVA_ACREEDOR_REPRESENTANTE_LEGAL', 'E066'),
        ('FINANCIACION_EDUCATIVA_ACREEDOR_DOMICILIO', 'E067'),
        ('FINANCIACION_EDUCATIVA_PAGARE_VERSION_JURIDICA', 'E068'),
        ('FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION', 'E069'),
        (
            'FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_CARTA_INSTRUCCIONES',
            'E070',
        ),
        ('FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_INCUMPLIMIENTO', 'E071'),
    ):
        if not str(getattr(settings, nombre, '') or '').strip():
            errors.append(
                _error(
                    f'La automatizacion requiere {nombre}.',
                    identifier,
                )
            )
    parametros_worker = (
        ('FINANCIACION_EDUCATIVA_WORKER_LEASE_SECONDS', 'E072'),
        ('FINANCIACION_EDUCATIVA_WORKER_MAX_ATTEMPTS', 'E073'),
        ('FINANCIACION_EDUCATIVA_WORKER_BACKOFF_BASE_SECONDS', 'E074'),
        ('FINANCIACION_EDUCATIVA_WORKER_BACKOFF_MAX_SECONDS', 'E075'),
    )
    for nombre, identifier in parametros_worker:
        valor = getattr(settings, nombre, 0)
        if not isinstance(valor, int) or isinstance(valor, bool) or valor <= 0:
            errors.append(
                _error(
                    f'{nombre} debe ser un entero positivo.',
                    identifier,
                )
            )
    if (
        getattr(
            settings,
            'FINANCIACION_EDUCATIVA_WORKER_BACKOFF_BASE_SECONDS',
            0,
        )
        > getattr(
            settings,
            'FINANCIACION_EDUCATIVA_WORKER_BACKOFF_MAX_SECONDS',
            0,
        )
    ):
        errors.append(
            _error(
                'El backoff base no puede superar el backoff maximo.',
                'E076',
            )
        )
    if (
        getattr(settings, 'DEPLOYMENT_ENVIRONMENT', 'local')
        in {'staging', 'production'}
        and 'postgresql' not in str(
            settings.DATABASES.get('default', {}).get('ENGINE', '')
        )
    ):
        errors.append(
            _error(
                'La cola educativa requiere PostgreSQL fuera del entorno local.',
                'E077',
            )
        )
    return errors


@register()
def check_educational_email_outbox_configuration(app_configs, **kwargs):
    errors = []
    parametros = (
        ('FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_LEASE_SECONDS', 'E084'),
        ('FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_MAX_ATTEMPTS', 'E085'),
        ('FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_BASE_SECONDS', 'E086'),
        ('FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_MAX_SECONDS', 'E087'),
    )
    for nombre, identifier in parametros:
        valor = getattr(settings, nombre, 0)
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            errors.append(
                _error(f'{nombre} debe ser un entero positivo.', identifier)
            )
    base = getattr(
        settings,
        'FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_BASE_SECONDS',
        0,
    )
    maximo = getattr(
        settings,
        'FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_MAX_SECONDS',
        0,
    )
    if isinstance(base, int) and isinstance(maximo, int) and base > maximo:
        errors.append(_error('El backoff de correo no es valido.', 'E088'))
    lease = getattr(
        settings,
        'FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_LEASE_SECONDS',
        0,
    )
    timeout = getattr(settings, 'EMAIL_TIMEOUT', 0)
    if (
        isinstance(lease, int)
        and isinstance(timeout, (int, float))
        and lease <= timeout
    ):
        errors.append(
            _error(
                'El lease del outbox debe superar el timeout SMTP.',
                'E089',
            )
        )
    return errors
