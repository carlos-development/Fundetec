import ipaddress
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.core.signing import salted_hmac


logger = logging.getLogger(__name__)


def _ip_cliente(request):
    remote = (request.META.get('REMOTE_ADDR') or '').strip()
    candidate = remote
    if remote in settings.FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_TRUSTED_PROXY_IPS:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            candidate = forwarded.split(',', 1)[0].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return 'invalid'


def limite_simulador_publico_excedido(request):
    """Aplica una ventana fija por IP sin conservar la direccion en cache."""
    limit = settings.FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_RATE_LIMIT_REQUESTS
    window = settings.FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_RATE_LIMIT_WINDOW_SECONDS
    bucket = int(time.time()) // window
    digest = salted_hmac(
        'financiacion_educativa.simulador_publico',
        f'{_ip_cliente(request)}:{bucket}',
    ).hexdigest()
    key = f'edu-public-simulator:{digest}'
    try:
        if cache.add(key, 1, timeout=window + 1):
            return False
        return cache.incr(key) > limit
    except Exception:
        logger.exception('No fue posible aplicar el limite del simulador publico.')
        return False
