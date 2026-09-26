from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def firma_pausada():
    if not settings.FINANCIACION_EDUCATIVA_SIGNATURE_SEND_PAUSED:
        return False
    if settings.DEPLOYMENT_ENVIRONMENT not in {'staging', 'test'}:
        raise ImproperlyConfigured('La pausa de firma solo esta permitida en staging/test.')
    return True
