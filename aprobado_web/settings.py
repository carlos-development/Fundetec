from pathlib import Path, PurePosixPath
import importlib.util
import os
import socket
import sys
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

try:
    import dj_database_url
except ModuleNotFoundError:
    dj_database_url = None

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

# Cargar variables de entorno desde .env solo cuando se habilite expresamente.
# En servicios administrados se recomienda inyectar el entorno desde systemd.
if (
    load_dotenv is not None
    and os.environ.get('DJANGO_LOAD_DOTENV', 'true').strip().lower()
    in ('1', 'true', 'yes', 'on', 'si', 'sí')
):
    load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ========================
# Seguridad y Debug
# ========================

DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'
RUNNING_TESTS = 'test' in sys.argv
DEPLOYMENT_ENVIRONMENT = os.environ.get(
    'DEPLOYMENT_ENVIRONMENT',
    'local',
).strip().lower()
if DEPLOYMENT_ENVIRONMENT not in {'local', 'staging', 'production'}:
    raise ImproperlyConfigured(
        'DEPLOYMENT_ENVIRONMENT debe ser local, staging o production.'
    )

_external_secret_key = os.environ.get('SECRET_KEY', '').strip()
if not DEBUG and (
    len(_external_secret_key) < 32
    or _external_secret_key in {'dev-secret-key', 'changeme', 'replace-me'}
):
    raise ImproperlyConfigured(
        'SECRET_KEY debe definirse externamente con al menos 32 caracteres.'
    )
SECRET_KEY = _external_secret_key or 'dev-secret-key'


def _module_available(module_name):
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


ALLAUTH_AVAILABLE = _module_available('allauth')
ALLAUTH_ACCOUNT_AVAILABLE = _module_available('allauth.account')
ALLAUTH_SOCIALACCOUNT_AVAILABLE = _module_available('allauth.socialaccount')
ALLAUTH_GOOGLE_AVAILABLE = _module_available('allauth.socialaccount.providers.google')
DJANGO_CELERY_BEAT_AVAILABLE = _module_available('django_celery_beat')
WHITENOISE_AVAILABLE = _module_available('whitenoise')

def env_bool(name, default=False):
    value = os.environ.get(name, str(default))
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'si', 'sí')

def _split_env_list(name, default=''):
    value = os.environ.get(name, default)
    return [item.strip() for item in value.split(',') if item.strip()]


def _require_nonempty_setting(name, value):
    if not str(value or '').strip():
        raise ImproperlyConfigured(f'{name} es obligatorio en este entorno.')


def _is_local_redis_url(redis_url):
    if not redis_url:
        return False
    parsed = urlparse(redis_url)
    return (parsed.hostname or '').lower() in {'localhost', '127.0.0.1'}


def _can_open_redis_socket(redis_url, timeout=0.15):
    if not redis_url:
        return False
    parsed = urlparse(redis_url)
    host = parsed.hostname
    port = parsed.port or 6379
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

PRIMARY_DOMAIN_HOST = os.environ.get('PRIMARY_DOMAIN_HOST', 'aprobado.com.co')
EMPRENDER_SUBDOMAIN_HOST = os.environ.get('EMPRENDER_SUBDOMAIN_HOST', 'emprender.aprobado.com.co')
MARKET_SUBDOMAIN_HOST = os.environ.get('MARKET_SUBDOMAIN_HOST', 'market.aprobado.com.co')
CONTRACTORS_PORTAL_HOST = os.environ.get('CONTRACTORS_PORTAL_HOST', f'contratistas.{PRIMARY_DOMAIN_HOST}')
LEGACY_DISABLED_HOSTS = (
    EMPRENDER_SUBDOMAIN_HOST,
    MARKET_SUBDOMAIN_HOST,
    CONTRACTORS_PORTAL_HOST,
    f'contratistas.{PRIMARY_DOMAIN_HOST}',
    'emprender.aprobado.com.co',
    'market.aprobado.com.co',
    'contratistas.aprobado.com.co',
)

# ========================
# Marca blanca
# ========================

BRAND_NAME = os.environ.get('BRAND_NAME', 'FUNDETEC')
BRAND_LEGAL_NAME = os.environ.get('BRAND_LEGAL_NAME', 'FUNDETEC')
BRAND_PRIMARY_COLOR = os.environ.get('BRAND_PRIMARY_COLOR', '#0B4EA2')
BRAND_SECONDARY_COLOR = os.environ.get('BRAND_SECONDARY_COLOR', '#FFC400')
BRAND_ACCENT_COLOR = os.environ.get('BRAND_ACCENT_COLOR', '#E7191A')
BRAND_DARK_COLOR = os.environ.get('BRAND_DARK_COLOR', '#083B7A')
BRAND_LOGO = os.environ.get('BRAND_LOGO', 'images/fundetec-logo.png')
BRAND_LOGO_DARK = os.environ.get('BRAND_LOGO_DARK', BRAND_LOGO)
BRAND_FAVICON = os.environ.get('BRAND_FAVICON', BRAND_LOGO)
BRAND_PUBLIC_BASE_URL = os.environ.get('BRAND_PUBLIC_BASE_URL', f'https://{PRIMARY_DOMAIN_HOST}')

# Identidad propia del producto educativo. No hereda la marca blanca historica.
EDUCATION_BRAND_NAME = 'Aprobado'
EDUCATION_BRAND_LOGO = 'images/logo-dark.png'
EDUCATION_BRAND_LOGO_INVERSE = 'images/logo.png'
EDUCATION_BRAND_FAVICON = 'images/favicon.png'
EDUCATION_EMAIL_LOGO_URL = os.environ.get(
    'EDUCATION_EMAIL_LOGO_URL',
    'https://aprobado.com.co/static/images/logo-dark.png',
).strip()

CONTACT_EMAIL = os.environ.get(
    'CONTACT_EMAIL',
    'Info@aprobado.com.co'
)
EMAIL_QA_MODE = env_bool('EMAIL_QA_MODE', False)
EMAIL_LIVE_DELIVERY_ENABLED = env_bool(
    'EMAIL_LIVE_DELIVERY_ENABLED',
    False,
)
EMAIL_QA_REDIRECT_TO = os.environ.get('EMAIL_QA_REDIRECT_TO', '').strip()
EMAIL_QA_SUBJECT_PREFIX = os.environ.get('EMAIL_QA_SUBJECT_PREFIX', '[QA]')
FINANCIACION_EDUCATIVA_REVIEW_NOTIFICATION_EMAILS = _split_env_list(
    'FINANCIACION_EDUCATIVA_REVIEW_NOTIFICATION_EMAILS',
    '',
)

# Correos internos para alertas operativas del flujo de credito.
# Formato esperado en .env:
# CREDIT_INTERNAL_NOTIFICATION_EMAILS=correo1@dominio.com,correo2@dominio.com
CREDIT_INTERNAL_NOTIFICATION_EMAILS = _split_env_list(
    'CREDIT_INTERNAL_NOTIFICATION_EMAILS',
    CONTACT_EMAIL
)

# Tasas mensuales parametrizables por linea de credito.
# Se usan en simuladores, pagare y activacion financiera.
LIBRANZA_TASA_MENSUAL = os.environ.get('LIBRANZA_TASA_MENSUAL', '1.9')
EMPRENDIMIENTO_TASA_MENSUAL = os.environ.get('EMPRENDIMIENTO_TASA_MENSUAL', '3.5')
ADELANTO_NOMINA_TASA_MENSUAL = os.environ.get('ADELANTO_NOMINA_TASA_MENSUAL', '1.9')
ADELANTO_NOMINA_COMISION_PERCENT = os.environ.get('ADELANTO_NOMINA_COMISION_PERCENT', '10')
ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE = os.environ.get('ADELANTO_NOMINA_CAPACIDAD_PORCENTAJE', '25')

FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL = os.environ.get(
    'FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL',
    '',
)
FINANCIACION_EDUCATIVA_ACREEDOR_NIT = os.environ.get(
    'FINANCIACION_EDUCATIVA_ACREEDOR_NIT',
    '',
).strip()
FINANCIACION_EDUCATIVA_ACREEDOR_REPRESENTANTE_LEGAL = os.environ.get(
    'FINANCIACION_EDUCATIVA_ACREEDOR_REPRESENTANTE_LEGAL',
    '',
).strip()
FINANCIACION_EDUCATIVA_ACREEDOR_DOMICILIO = os.environ.get(
    'FINANCIACION_EDUCATIVA_ACREEDOR_DOMICILIO',
    '',
).strip()
FINANCIACION_EDUCATIVA_PAGARE_VERSION_JURIDICA = os.environ.get(
    'FINANCIACION_EDUCATIVA_PAGARE_VERSION_JURIDICA',
    '',
).strip()
FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION = os.environ.get(
    'FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION',
    '',
).strip()
FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_CARTA_INSTRUCCIONES = os.environ.get(
    'FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_CARTA_INSTRUCCIONES',
    '',
).strip()
FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_INCUMPLIMIENTO = os.environ.get(
    'FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_INCUMPLIMIENTO',
    '',
).strip()
FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MIN_AMOUNT = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MIN_AMOUNT', '100000')
)
FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MAX_AMOUNT = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MAX_AMOUNT', '2000000')
)
FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_AMOUNT = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_AMOUNT', '1000000')
)
FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MIN_TERM_MONTHS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MIN_TERM_MONTHS', '1')
)
FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MAX_TERM_MONTHS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_MAX_TERM_MONTHS', '6')
)
FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_TERM_MONTHS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_TERM_MONTHS', '6')
)
FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_RATE_LIMIT_REQUESTS = int(
    os.environ.get(
        'FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_RATE_LIMIT_REQUESTS',
        '30',
    )
)
FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_RATE_LIMIT_WINDOW_SECONDS = int(
    os.environ.get(
        'FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_RATE_LIMIT_WINDOW_SECONDS',
        '60',
    )
)
FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_TRUSTED_PROXY_IPS = _split_env_list(
    'FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_TRUSTED_PROXY_IPS',
    '127.0.0.1,::1',
)
FINANCIACION_EDUCATIVA_INVITACION_TTL_HOURS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_INVITACION_TTL_HOURS', '72')
)
FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND = os.environ.get(
    'FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND',
    (
        'financiacion_educativa.services.entrega_invitaciones.'
        'DjangoEmailInvitationDeliveryBackend'
    ),
)
FINANCIACION_EDUCATIVA_INVITATION_REISSUE_LIMIT = int(
    os.environ.get('FINANCIACION_EDUCATIVA_INVITATION_REISSUE_LIMIT', '5')
)
FINANCIACION_EDUCATIVA_INVITATION_REISSUE_WINDOW_HOURS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_INVITATION_REISSUE_WINDOW_HOURS', '24')
)
FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS', '300')
)
FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_TIMEOUT_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_TIMEOUT_SECONDS', '10')
)
FINANCIACION_EDUCATIVA_INVITATION_RECOVERY_STALE_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_INVITATION_RECOVERY_STALE_SECONDS', '300')
)
FINANCIACION_EDUCATIVA_INVITATION_RECIPIENT_HMAC_KEY = os.environ.get(
    'FINANCIACION_EDUCATIVA_INVITATION_RECIPIENT_HMAC_KEY',
    SECRET_KEY if DEBUG else '',
)
FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TTL_MINUTES = int(
    os.environ.get('FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TTL_MINUTES', '30')
)
FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_COOLDOWN_SECONDS = int(
    os.environ.get(
        'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_COOLDOWN_SECONDS',
        '120',
    )
)
FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_REISSUE_LIMIT = int(
    os.environ.get('FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_REISSUE_LIMIT', '5')
)
FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_REISSUE_WINDOW_HOURS = int(
    os.environ.get(
        'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_REISSUE_WINDOW_HOURS',
        '1',
    )
)
FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TOKEN_HMAC_KEY = os.environ.get(
    'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TOKEN_HMAC_KEY',
    SECRET_KEY if DEBUG else '',
)
FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_DELIVERY_BACKEND = os.environ.get(
    'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_DELIVERY_BACKEND',
    (
        'financiacion_educativa.services.captura_movil.'
        'DjangoEmailMobileCaptureDeliveryBackend'
    ),
)
FINANCIACION_EDUCATIVA_MAYORIA_EDAD = int(
    os.environ.get('FINANCIACION_EDUCATIVA_MAYORIA_EDAD', '18')
)
FINANCIACION_EDUCATIVA_DOCUMENT_MAX_BYTES = int(
    os.environ.get('FINANCIACION_EDUCATIVA_DOCUMENT_MAX_BYTES', str(10 * 1024 * 1024))
)
FINANCIACION_EDUCATIVA_DOCUMENT_MAX_COUNT = int(
    os.environ.get('FINANCIACION_EDUCATIVA_DOCUMENT_MAX_COUNT', '20')
)
FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND = os.environ.get(
    'FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND',
    'financiacion_educativa.services.escaneo_documentos.ClamAVDocumentScanBackend',
)
FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET = os.environ.get(
    'FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET',
    '/run/clamav/clamd.ctl',
)
FINANCIACION_EDUCATIVA_CLAMAV_HOST = os.environ.get(
    'FINANCIACION_EDUCATIVA_CLAMAV_HOST',
    '',
)
FINANCIACION_EDUCATIVA_CLAMAV_PORT = int(
    os.environ.get('FINANCIACION_EDUCATIVA_CLAMAV_PORT', '3310')
)
FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS = float(
    os.environ.get(
        'FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS',
        '3',
    )
)
FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS = float(
    os.environ.get(
        'FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS',
        '30',
    )
)
FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS', '3')
)
FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS', '300')
)
FINANCIACION_EDUCATIVA_SCAN_MAX_REOPENINGS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_SCAN_MAX_REOPENINGS', '1')
)
FINANCIACION_EDUCATIVA_SCAN_REOPEN_EXTRA_ATTEMPTS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_SCAN_REOPEN_EXTRA_ATTEMPTS', '1')
)
FINANCIACION_EDUCATIVA_ALLOW_TEST_SCAN_BACKENDS = False
FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED = env_bool(
    'FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED',
    False,
)
FINANCIACION_EDUCATIVA_WORKER_LEASE_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_WORKER_LEASE_SECONDS', '180')
)
FINANCIACION_EDUCATIVA_WORKER_MAX_ATTEMPTS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_WORKER_MAX_ATTEMPTS', '3')
)
FINANCIACION_EDUCATIVA_WORKER_BACKOFF_BASE_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_WORKER_BACKOFF_BASE_SECONDS', '15')
)
FINANCIACION_EDUCATIVA_WORKER_BACKOFF_MAX_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_WORKER_BACKOFF_MAX_SECONDS', '300')
)
FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_LEASE_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_LEASE_SECONDS', '120')
)
FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_MAX_ATTEMPTS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_MAX_ATTEMPTS', '3')
)
FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_BASE_SECONDS = int(
    os.environ.get(
        'FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_BASE_SECONDS', '30'
    )
)
FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_MAX_SECONDS = int(
    os.environ.get(
        'FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_MAX_SECONDS', '600'
    )
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED = env_bool(
    'FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED',
    False,
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND = os.environ.get(
    'FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND',
    (
        'financiacion_educativa.services.validacion_documental_ia.'
        'DisabledDocumentAIValidationBackend'
    ),
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_MODEL = os.environ.get(
    'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MODEL',
    '',
).strip()
FINANCIACION_EDUCATIVA_DOCUMENT_AI_TIMEOUT_SECONDS = float(
    os.environ.get('FINANCIACION_EDUCATIVA_DOCUMENT_AI_TIMEOUT_SECONDS', '30')
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_MAX_ATTEMPTS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_DOCUMENT_AI_MAX_ATTEMPTS', '3')
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_STALE_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_DOCUMENT_AI_STALE_SECONDS', '300')
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_CONFIDENCE = os.environ.get(
    'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_CONFIDENCE',
    '0.85',
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_QUALITY = os.environ.get(
    'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_QUALITY',
    '0.70',
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_LEGIBILITY = os.environ.get(
    'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_LEGIBILITY',
    '0.80',
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_DIMENSION_CONFIDENCE = os.environ.get(
    'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_DIMENSION_CONFIDENCE',
    '0.80',
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_WIDTH = int(
    os.environ.get('FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_WIDTH', '800')
)
FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_HEIGHT = int(
    os.environ.get('FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_HEIGHT', '500')
)
FINANCIACION_EDUCATIVA_ALLOW_TEST_AI_BACKENDS = False
FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED = env_bool(
    'FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED',
    False,
)
FINANCIACION_EDUCATIVA_PDF_MAX_BYTES = int(
    os.environ.get(
        'FINANCIACION_EDUCATIVA_PDF_MAX_BYTES',
        str(10 * 1024 * 1024),
    )
)
FINANCIACION_EDUCATIVA_PDF_MAX_PAGES = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PDF_MAX_PAGES', '12')
)
FINANCIACION_EDUCATIVA_PDF_MAX_OBJECTS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PDF_MAX_OBJECTS', '10000')
)
FINANCIACION_EDUCATIVA_PDF_MAX_OBJECT_BYTES = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PDF_MAX_OBJECT_BYTES', '8388608')
)
FINANCIACION_EDUCATIVA_PDF_MAX_PIXELS_PER_PAGE = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PDF_MAX_PIXELS_PER_PAGE', '8000000')
)
FINANCIACION_EDUCATIVA_PDF_MAX_AI_PAGES = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PDF_MAX_AI_PAGES', '3')
)
FINANCIACION_EDUCATIVA_PDF_MAX_EXTRACTED_CHARACTERS = int(
    os.environ.get(
        'FINANCIACION_EDUCATIVA_PDF_MAX_EXTRACTED_CHARACTERS',
        '40000',
    )
)
FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS = float(
    os.environ.get('FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS', '30')
)
FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS = env_bool(
    'FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS', True
)
FINANCIACION_EDUCATIVA_PDF_MAX_MEMORY_MB = int(
    os.environ.get('FINANCIACION_EDUCATIVA_PDF_MAX_MEMORY_MB', '512')
)
FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND = os.environ.get(
    'FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND',
    (
        'financiacion_educativa.services.clasificacion_contenido_documental.'
        'DisabledContentDocumentClassificationBackend'
    ),
)
FINANCIACION_EDUCATIVA_CONTENT_MIN_CONFIDENCE = os.environ.get(
    'FINANCIACION_EDUCATIVA_CONTENT_MIN_CONFIDENCE', '0.90'
)
FINANCIACION_EDUCATIVA_CONTENT_MIN_LEGIBILITY = os.environ.get(
    'FINANCIACION_EDUCATIVA_CONTENT_MIN_LEGIBILITY', '0.80'
)
FINANCIACION_EDUCATIVA_CONTENT_MIN_COMPLETENESS = os.environ.get(
    'FINANCIACION_EDUCATIVA_CONTENT_MIN_COMPLETENESS', '0.80'
)
FINANCIACION_EDUCATIVA_CONTENT_STALE_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_CONTENT_STALE_SECONDS', '300')
)
FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS', '3')
)
FINANCIACION_EDUCATIVA_CONTENT_PROCESSOR_VERSION = os.environ.get(
    'FINANCIACION_EDUCATIVA_CONTENT_PROCESSOR_VERSION', 'PDF_CONTENT_V1'
).strip()
FINANCIACION_EDUCATIVA_CONTENT_SCHEMA_VERSION = os.environ.get(
    'FINANCIACION_EDUCATIVA_CONTENT_SCHEMA_VERSION', 'CONTENT_V1'
).strip()
FINANCIACION_EDUCATIVA_CONTENT_POLICY_VERSION = os.environ.get(
    'FINANCIACION_EDUCATIVA_CONTENT_POLICY_VERSION', 'EDU_CONTENT_V1'
).strip()
FINANCIACION_EDUCATIVA_CALIBRATION_OPENAI_ENABLED = env_bool(
    'FINANCIACION_EDUCATIVA_CALIBRATION_OPENAI_ENABLED',
    False,
)
FINANCIACION_EDUCATIVA_CALIBRATION_IDENTITY_BACKEND = os.environ.get(
    'FINANCIACION_EDUCATIVA_CALIBRATION_IDENTITY_BACKEND',
    (
        'financiacion_educativa.services.validacion_documental_ia.'
        'OpenAIDocumentAIValidationBackend'
    ),
).strip()
FINANCIACION_EDUCATIVA_CALIBRATION_CONTENT_BACKEND = os.environ.get(
    'FINANCIACION_EDUCATIVA_CALIBRATION_CONTENT_BACKEND',
    (
        'financiacion_educativa.services.clasificacion_contenido_documental.'
        'OpenAIContentDocumentClassificationBackend'
    ),
).strip()
FINANCIACION_EDUCATIVA_CALIBRATION_ALLOW_TEST_BACKENDS = False
FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY = os.environ.get(
    'FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY',
    SECRET_KEY if DEBUG else '',
)
FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS = False
FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND = os.environ.get(
    'FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND',
    (
        'financiacion_educativa.services.firma_zapsign.'
        'DisabledEducationalSignatureBackend'
    ),
)
FINANCIACION_EDUCATIVA_ZAPSIGN_BASE_URL = os.environ.get(
    'FINANCIACION_EDUCATIVA_ZAPSIGN_BASE_URL',
    'https://sandbox.api.zapsign.com.br/api/v1',
).rstrip('/')
FINANCIACION_EDUCATIVA_ZAPSIGN_API_TOKEN = os.environ.get(
    'FINANCIACION_EDUCATIVA_ZAPSIGN_API_TOKEN',
    '',
).strip()
FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET = os.environ.get(
    'FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET',
    '',
).strip()
FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_HEADER = os.environ.get(
    'FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_HEADER',
    'X-Educational-Signature-Secret',
).strip()
FINANCIACION_EDUCATIVA_ZAPSIGN_TIMEOUT_SECONDS = float(
    os.environ.get('FINANCIACION_EDUCATIVA_ZAPSIGN_TIMEOUT_SECONDS', '30')
)
FINANCIACION_EDUCATIVA_ZAPSIGN_MAX_ATTEMPTS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_ZAPSIGN_MAX_ATTEMPTS', '3')
)
FINANCIACION_EDUCATIVA_ZAPSIGN_STALE_SECONDS = int(
    os.environ.get('FINANCIACION_EDUCATIVA_ZAPSIGN_STALE_SECONDS', '300')
)
FINANCIACION_EDUCATIVA_ZAPSIGN_SEND_AUTOMATIC_EMAIL = env_bool(
    'FINANCIACION_EDUCATIVA_ZAPSIGN_SEND_AUTOMATIC_EMAIL',
    True,
)
FINANCIACION_EDUCATIVA_ZAPSIGN_AUTH_MODE = os.environ.get(
    'FINANCIACION_EDUCATIVA_ZAPSIGN_AUTH_MODE',
    'assinaturaTela-tokenEmail',
).strip()
FINANCIACION_EDUCATIVA_ZAPSIGN_REQUIRE_SELFIE = env_bool(
    'FINANCIACION_EDUCATIVA_ZAPSIGN_REQUIRE_SELFIE',
    True,
)
FINANCIACION_EDUCATIVA_ZAPSIGN_SELFIE_VALIDATION_TYPE = os.environ.get(
    'FINANCIACION_EDUCATIVA_ZAPSIGN_SELFIE_VALIDATION_TYPE',
    'identity-verification',
).strip()
FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY = os.environ.get(
    'FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY',
    SECRET_KEY if DEBUG else '',
)
FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS = False
FINANCIACION_EDUCATIVA_ALLOWED_DOCUMENT_MIME_TYPES = (
    'application/pdf',
    'image/jpeg',
    'image/png',
)
FINANCIACION_EDUCATIVA_PRIVATE_ROOT = os.environ.get(
    'FINANCIACION_EDUCATIVA_PRIVATE_ROOT',
    os.path.join(BASE_DIR, 'private_uploads', 'financiacion_educativa'),
)
FINANCIACION_EDUCATIVA_PRIVATE_STORAGE_BACKEND = os.environ.get(
    'FINANCIACION_EDUCATIVA_PRIVATE_STORAGE_BACKEND',
    'financiacion_educativa.storage.PrivateFileSystemStorage',
)
ALLOW_MULTIPLE_LIBRANZA_ACTIVE_CREDITS_FOR_TESTING = env_bool(
    'ALLOW_MULTIPLE_LIBRANZA_ACTIVE_CREDITS_FOR_TESTING',
    False
)
LIBRANZA_AUTO_MARK_MORA_ENABLED = env_bool('LIBRANZA_AUTO_MARK_MORA_ENABLED', True)
LIBRANZA_PAYMENT_REMINDERS_ENABLED = env_bool('LIBRANZA_PAYMENT_REMINDERS_ENABLED', False)
LIBRANZA_MORA_ALERTS_ENABLED = env_bool('LIBRANZA_MORA_ALERTS_ENABLED', False)
PAGADOR_MONTHLY_PENDING_NOTIFICATIONS_ENABLED = env_bool('PAGADOR_MONTHLY_PENDING_NOTIFICATIONS_ENABLED', True)
WORKER_PENDING_PAYMENT_ALERTS_ENABLED = env_bool('WORKER_PENDING_PAYMENT_ALERTS_ENABLED', True)
LIBRANZA_PRORATED_INTEREST_ENABLED = env_bool('LIBRANZA_PRORATED_INTEREST_ENABLED', False)
PAGARE_TEMPLATE_VERSION = os.environ.get('PAGARE_TEMPLATE_VERSION', '1.0')
WHATSAPP_SUPPORT_NUMBER = os.environ.get('WHATSAPP_SUPPORT_NUMBER', '573132477352')
WHATSAPP_DEFAULT_MESSAGE = os.environ.get(
    'WHATSAPP_DEFAULT_MESSAGE',
    f'Hola, necesito ayuda con {BRAND_NAME}.'
)
WHATSAPP_FLOATING_ENABLED = env_bool('WHATSAPP_FLOATING_ENABLED', True)
ZAPSIGN_SEND_LOCAL_EMAIL = env_bool('ZAPSIGN_SEND_LOCAL_EMAIL', False)
ZAPSIGN_SEND_COPY_EMAILS = env_bool('ZAPSIGN_SEND_COPY_EMAILS', False)

ALLOWED_HOSTS = _split_env_list(
    'ALLOWED_HOSTS',
    '' if not DEBUG else (
        f'{PRIMARY_DOMAIN_HOST},'
        f'www.{PRIMARY_DOMAIN_HOST},'
        f'.{PRIMARY_DOMAIN_HOST},'
        f'{EMPRENDER_SUBDOMAIN_HOST},'
        f'{MARKET_SUBDOMAIN_HOST},'
        f'{CONTRACTORS_PORTAL_HOST},'
        '127.0.0.1,localhost,.localhost,contratistas.localhost,'
        '.onrender.com,aprobado-proj.onrender.com'
    )
)

CSRF_TRUSTED_ORIGINS = _split_env_list('CSRF_TRUSTED_ORIGINS')

if not DEBUG:
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured(
            'ALLOWED_HOSTS debe definirse explicitamente cuando DEBUG=False.'
        )
    if not CSRF_TRUSTED_ORIGINS:
        raise ImproperlyConfigured(
            'CSRF_TRUSTED_ORIGINS debe definirse cuando DEBUG=False.'
        )
    if any(not origin.startswith('https://') for origin in CSRF_TRUSTED_ORIGINS):
        raise ImproperlyConfigured(
            'CSRF_TRUSTED_ORIGINS solo puede contener origenes HTTPS fuera de desarrollo.'
        )
    _require_nonempty_setting(
        'FINANCIACION_EDUCATIVA_INVITATION_RECIPIENT_HMAC_KEY',
        FINANCIACION_EDUCATIVA_INVITATION_RECIPIENT_HMAC_KEY,
    )
    _require_nonempty_setting(
        'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TOKEN_HMAC_KEY',
        FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TOKEN_HMAC_KEY,
    )

USE_X_FORWARDED_HOST = True

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
CONTRACTORS_CONTRACT_AI_ENABLED = os.environ.get('CONTRACTORS_CONTRACT_AI_ENABLED', 'False').lower() == 'true'
CONTRACTORS_CONTRACT_AI_MODEL = os.environ.get('CONTRACTORS_CONTRACT_AI_MODEL', 'gpt-4o-mini')
CONTRACTORS_DATACREDITO_ENABLED = os.environ.get('CONTRACTORS_DATACREDITO_ENABLED', 'False').lower() == 'true'
CONTRACTORS_DATACREDITO_PROVIDER = os.environ.get('CONTRACTORS_DATACREDITO_PROVIDER', 'mock')
CONTRACTORS_DATACREDITO_TIMEOUT_SECONDS = int(os.environ.get('CONTRACTORS_DATACREDITO_TIMEOUT_SECONDS', '10'))
CONTRACTORS_DATACREDITO_MOCK_SCENARIO = os.environ.get('CONTRACTORS_DATACREDITO_MOCK_SCENARIO', 'bueno')
MANUAL_PAYMENT_AUTH_KEY = os.environ.get('MANUAL_PAYMENT_AUTH_KEY', '')
WHATSAPP_INTERNAL_API_KEY = os.environ.get('WHATSAPP_INTERNAL_API_KEY', '')
WHATSAPP_INTERNAL_API_RATE_LIMIT = int(os.environ.get('WHATSAPP_INTERNAL_API_RATE_LIMIT', '120'))
WHATSAPP_CREDIT_TASA_MENSUAL = os.environ.get('WHATSAPP_CREDIT_TASA_MENSUAL', '3.5')
WHATSAPP_CREDIT_ORIGINATION_RATE = os.environ.get('WHATSAPP_CREDIT_ORIGINATION_RATE', '10')
WHATSAPP_CREDIT_VAT_RATE = os.environ.get('WHATSAPP_CREDIT_VAT_RATE', '19')
WHATSAPP_SIMULATION_VALID_DAYS = int(os.environ.get('WHATSAPP_SIMULATION_VALID_DAYS', '7'))



# ========================
# Aplicaciones
# ========================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django.contrib.humanize',
    'rest_framework',
    'drf_spectacular',
    'usuarios',
    'configuraciones',
    'gestion_creditos',
    'contractors',
    'instituciones',
    'financiacion_educativa',
    'usuariocreditos',
]

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Aprobado - API de financiacion educativa',
    'DESCRIPTION': (
        'API institucional versionada para originar y consultar solicitudes '
        'de financiacion educativa.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

if ALLAUTH_AVAILABLE:
    INSTALLED_APPS.append('allauth')
if ALLAUTH_ACCOUNT_AVAILABLE:
    INSTALLED_APPS.append('allauth.account')
if ALLAUTH_SOCIALACCOUNT_AVAILABLE:
    INSTALLED_APPS.append('allauth.socialaccount')
if ALLAUTH_GOOGLE_AVAILABLE:
    INSTALLED_APPS.append('allauth.socialaccount.providers.google')
if DJANGO_CELERY_BEAT_AVAILABLE:
    INSTALLED_APPS.append('django_celery_beat')

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]

if ALLAUTH_ACCOUNT_AVAILABLE:
    AUTHENTICATION_BACKENDS.append(
        'allauth.account.auth_backends.AuthenticationBackend'
    )

SITE_ID = 1

# ========================================
# Django Allauth - Autenticación
# ========================================
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/'
SOCIALACCOUNT_AUTO_SIGNUP = True
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'optional'

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    }
}

ACCOUNT_ADAPTER = 'usuarios.adapter.AccountAdapter'
SOCIALACCOUNT_ADAPTER = 'usuarios.adapter.CustomSocialAccountAdapter'
ACCOUNT_UNIQUE_EMAIL = True

if DEBUG:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'aprobado_web.middleware.RetiredLegacySurfaceMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if ALLAUTH_ACCOUNT_AVAILABLE:
    MIDDLEWARE.append('allauth.account.middleware.AccountMiddleware')
if WHITENOISE_AVAILABLE:
    MIDDLEWARE.append('whitenoise.middleware.WhiteNoiseMiddleware')

ROOT_URLCONF = 'aprobado_web.urls_main'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'aprobado_web.context_processors.public_whatsapp_processor',
                'aprobado_web.context_processors.brand_processor',
            ],
        },
    },
]

WSGI_APPLICATION = 'aprobado_web.wsgi.application'

# ========================
# Bases de datos
# ========================

USE_SQLITE = os.environ.get('USE_SQLITE', '').lower() == 'true'
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()

if not DEBUG:
    if USE_SQLITE:
        raise ImproperlyConfigured(
            'SQLite no esta permitido cuando DEBUG=False.'
        )
    _require_nonempty_setting('DATABASE_URL', DATABASE_URL)
    if dj_database_url is None:
        raise ImproperlyConfigured(
            'dj-database-url es obligatorio para configurar PostgreSQL.'
        )

if USE_SQLITE or not DATABASE_URL:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    if dj_database_url is None:
        raise ImproperlyConfigured(
            'DATABASE_URL esta definida, pero dj-database-url no esta instalado.'
        )
    DATABASES = {
        'default': dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=60,
            conn_health_checks=True,
        )
    }

if not DEBUG and DATABASES['default']['ENGINE'] not in {
    'django.db.backends.postgresql',
    'django.db.backends.postgresql_psycopg2',
}:
    raise ImproperlyConfigured(
        'DATABASE_URL debe apuntar a PostgreSQL cuando DEBUG=False.'
    )

# ========================
# Validación de contraseñas
# ========================

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ========================
# Internacionalización
# ========================

LANGUAGE_CODE = 'es-CO'
TIME_ZONE = 'America/Bogota'
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

# ========================
# Archivos estáticos
# ========================

STATIC_URL = 'static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]
_static_root_value = os.environ.get(
    'STATIC_ROOT',
    str(BASE_DIR / 'staticfiles'),
)
STATIC_ROOT = Path(_static_root_value).expanduser()
if WHITENOISE_AVAILABLE:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
_media_root_value = os.environ.get('MEDIA_ROOT', str(BASE_DIR / 'media'))
MEDIA_ROOT = Path(_media_root_value).expanduser()

_private_root_value = str(FINANCIACION_EDUCATIVA_PRIVATE_ROOT)
FINANCIACION_EDUCATIVA_PRIVATE_ROOT = Path(
    FINANCIACION_EDUCATIVA_PRIVATE_ROOT
).expanduser()

if not DEBUG:
    for _path_setting_name in (
        'STATIC_ROOT',
        'MEDIA_ROOT',
        'FINANCIACION_EDUCATIVA_PRIVATE_ROOT',
    ):
        _require_nonempty_setting(
            _path_setting_name,
            os.environ.get(_path_setting_name, ''),
        )
    _storage_roots = {
        'STATIC_ROOT': STATIC_ROOT,
        'MEDIA_ROOT': MEDIA_ROOT,
        'FINANCIACION_EDUCATIVA_PRIVATE_ROOT': (
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT
        ),
    }
    _raw_storage_roots = {
        'STATIC_ROOT': _static_root_value,
        'MEDIA_ROOT': _media_root_value,
        'FINANCIACION_EDUCATIVA_PRIVATE_ROOT': _private_root_value,
    }
    for _root_name, _root_path in _storage_roots.items():
        _raw_root = _raw_storage_roots[_root_name].replace('\\', '/')
        if not (
            _root_path.is_absolute()
            or PurePosixPath(_raw_root).is_absolute()
        ):
            raise ImproperlyConfigured(
                f'{_root_name} debe ser una ruta absoluta fuera de desarrollo.'
            )
    if len({str(path.resolve()) for path in _storage_roots.values()}) != 3:
        raise ImproperlyConfigured(
            'STATIC_ROOT, MEDIA_ROOT y el almacenamiento privado deben ser distintos.'
        )
    if DEPLOYMENT_ENVIRONMENT == 'staging':
        _protected_root = Path('/var/www/aprobado')
        if any(
            path.resolve().is_relative_to(_protected_root)
            for path in _storage_roots.values()
        ):
            raise ImproperlyConfigured(
                'Staging no puede reutilizar rutas del proyecto Aprobado existente.'
            )

# ========================
# Seguridad
# ========================

SECURE_SSL_REDIRECT = not DEBUG and not RUNNING_TESTS
SESSION_COOKIE_SECURE = not DEBUG and not RUNNING_TESTS
CSRF_COOKIE_SECURE = not DEBUG and not RUNNING_TESTS
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(
    'SECURE_HSTS_INCLUDE_SUBDOMAINS',
    False,
)
SECURE_HSTS_PRELOAD = env_bool('SECURE_HSTS_PRELOAD', False)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ========================
# Configuración de Email (Gmail SMTP)
# ========================

# Backend de email - usando SMTP de Gmail
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.smtp.EmailBackend',
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com').strip()
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', True)
EMAIL_USE_SSL = env_bool('EMAIL_USE_SSL', False)
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '').strip()
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_TIMEOUT = int(os.environ.get('EMAIL_TIMEOUT', '10'))
DEFAULT_FROM_EMAIL = os.environ.get(
    'DEFAULT_FROM_EMAIL',
    'Aprobado <noreply@aprobado.com.co>',
).strip()
SERVER_EMAIL = os.environ.get('SERVER_EMAIL', EMAIL_HOST_USER)

SAFE_ROUTING_EMAIL_BACKEND = (
    'aprobado_web.email_backends.SafeRoutingEmailBackend'
)
if EMAIL_QA_MODE and not EMAIL_QA_REDIRECT_TO:
    raise ImproperlyConfigured(
        'EMAIL_QA_REDIRECT_TO es obligatorio cuando EMAIL_QA_MODE=True.'
    )
if EMAIL_QA_MODE and EMAIL_LIVE_DELIVERY_ENABLED:
    raise ImproperlyConfigured(
        'EMAIL_QA_MODE y EMAIL_LIVE_DELIVERY_ENABLED no pueden estar activos '
        'al mismo tiempo.'
    )
if DEPLOYMENT_ENVIRONMENT == 'staging':
    if DEBUG:
        raise ImproperlyConfigured('Staging requiere DEBUG=False.')
    if EMAIL_BACKEND != SAFE_ROUTING_EMAIL_BACKEND:
        raise ImproperlyConfigured(
            'Staging requiere SafeRoutingEmailBackend.'
        )
    if not EMAIL_QA_MODE and not EMAIL_LIVE_DELIVERY_ENABLED:
        raise ImproperlyConfigured(
            'Staging requiere EMAIL_QA_MODE=True o la habilitacion explicita '
            'EMAIL_LIVE_DELIVERY_ENABLED=True.'
        )
    if (
        EMAIL_LIVE_DELIVERY_ENABLED
        and not FINANCIACION_EDUCATIVA_REVIEW_NOTIFICATION_EMAILS
    ):
        raise ImproperlyConfigured(
            'FINANCIACION_EDUCATIVA_REVIEW_NOTIFICATION_EMAILS es obligatorio '
            'para la entrega real en staging.'
        )
    for _email_setting_name, _email_setting_value in {
        'EMAIL_HOST': EMAIL_HOST,
        'EMAIL_HOST_USER': EMAIL_HOST_USER,
        'EMAIL_HOST_PASSWORD': EMAIL_HOST_PASSWORD,
        'DEFAULT_FROM_EMAIL': DEFAULT_FROM_EMAIL,
    }.items():
        _require_nonempty_setting(_email_setting_name, _email_setting_value)

# ========================
# Configuración de WOMPI (Pasarela de Pagos)
# ========================
WOMPI_PUBLIC_KEY = os.environ.get('WOMPI_PUBLIC_KEY', '')
WOMPI_PRIVATE_KEY = os.environ.get('WOMPI_PRIVATE_KEY', '')
WOMPI_INTEGRITY_KEY = os.environ.get('WOMPI_INTEGRITY_KEY', '')
WOMPI_EVENTS_SECRET = os.environ.get('WOMPI_EVENTS_SECRET', '')
WOMPI_ENVIRONMENT = os.environ.get('WOMPI_ENVIRONMENT', 'sandbox')  # 'sandbox' o 'production'

# URL base se calcula automáticamente según el ambiente
WOMPI_API_BASE_URL = (
    'https://sandbox.wompi.co/v1'
    if WOMPI_ENVIRONMENT == 'sandbox'
    else 'https://production.wompi.co/v1'
)

# Controles de duplicidad y rate limiting para WOMPI
WOMPI_DUPLICATE_COOLDOWN_SECONDS = int(os.environ.get('WOMPI_DUPLICATE_COOLDOWN_SECONDS', '300'))
WOMPI_DUPLICATE_WINDOW_MINUTES = int(os.environ.get('WOMPI_DUPLICATE_WINDOW_MINUTES', '10'))
WOMPI_RATE_LIMIT_ATTEMPTS = int(os.environ.get('WOMPI_RATE_LIMIT_ATTEMPTS', '3'))
WOMPI_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get('WOMPI_RATE_LIMIT_WINDOW_SECONDS', '60'))

# ========================
# Seguridad
# ========================
# Permite previsualizar PDFs internos en iframes (mismo origen)
X_FRAME_OPTIONS = 'SAMEORIGIN'

# Cache (usar Redis si esta disponible)
REDIS_URL = os.environ.get('REDIS_URL', '')
ALLOW_LOCAL_REDIS_FALLBACK = env_bool('ALLOW_LOCAL_REDIS_FALLBACK', True)
USE_LOCAL_REDIS_FALLBACK = ALLOW_LOCAL_REDIS_FALLBACK and _is_local_redis_url(REDIS_URL) and not _can_open_redis_socket(REDIS_URL)
if REDIS_URL and not USE_LOCAL_REDIS_FALLBACK:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'aprobado-cache',
        }
    }

# ========================
# Configuración de ZapSign (Firma Electrónica de Pagarés)
# ========================
ZAPSIGN_API_TOKEN = os.environ.get('ZAPSIGN_API_TOKEN', '')
ZAPSIGN_WEBHOOK_SECRET = os.environ.get('ZAPSIGN_WEBHOOK_SECRET', '')
ZAPSIGN_WEBHOOK_HEADER = os.environ.get('ZAPSIGN_WEBHOOK_HEADER', 'X-ZapSign-Secret')
ZAPSIGN_ENVIRONMENT = os.environ.get('ZAPSIGN_ENVIRONMENT', 'sandbox')  # 'sandbox' o 'production'
ZAPSIGN_AUTH_MODE = os.environ.get('ZAPSIGN_AUTH_MODE', 'assinaturaTela')
ZAPSIGN_SEND_AUTOMATIC_EMAIL = env_bool('ZAPSIGN_SEND_AUTOMATIC_EMAIL', True)

# Para desactivar funciones avanzadas
ZAPSIGN_ENABLE_SELFIE_VALIDATION = env_bool('ZAPSIGN_ENABLE_SELFIE_VALIDATION', False)
ZAPSIGN_SELFIE_VALIDATION_TYPE = os.environ.get('ZAPSIGN_SELFIE_VALIDATION_TYPE', 'identity-verification')


# Configuración del dominio público para URLs de descarga de PDFs
SITE_DOMAIN = os.environ.get('SITE_DOMAIN', 'localhost:8000')
SITE_HTTPS = os.environ.get('SITE_HTTPS', 'False').lower() == 'true'

# ========================
# Configuración de Email con Gmail API (COMENTADO - Para uso futuro)
# ========================
# Para implementar Gmail API en el futuro, consulta: GMAIL_API_SETUP.md
#
# GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
#     'GOOGLE_SERVICE_ACCOUNT_FILE',
#     os.path.join(BASE_DIR, 'config', 'google-service-account.json')
# )
# DEFAULT_FROM_EMAIL = 'Aprobado <aprobado-email-service@aprobado-web.iam.gserviceaccount.com>'
# SERVER_EMAIL = DEFAULT_FROM_EMAIL
# GMAIL_DELEGATED_USER = os.environ.get('GMAIL_DELEGATED_USER', 'tu-email@tudominio.com')

# ========================
# Configuración de Celery
# ========================

# URL de Redis (usar Redis como broker y backend)
CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# Configuración adicional de Celery
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'America/Bogota'
CELERY_ENABLE_UTC = False

# Configuración de Celery Beat (tareas programadas)
CELERY_BEAT_SCHEDULE = {}

APROBADO_INSTITUTION_API_KEY = os.environ.get('APROBADO_INSTITUTION_API_KEY', '')
