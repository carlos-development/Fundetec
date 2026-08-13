import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.mail import get_connection
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.module_loading import import_string

from financiacion_educativa.choices import (
    EstadoEnlaceCapturaMovil,
    EstadoEntregaCapturaMovil,
    EstadoSolicitudFinanciacion,
    RolParticipante,
    TipoEventoEnlaceCapturaMovil,
)
from financiacion_educativa.models import (
    EnlaceCapturaMovil,
    EventoEnlaceCapturaMovil,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.correos import (
    ConfiguracionSMTPInvalida,
    SMTP_BACKENDS,
    construir_correo_captura_movil,
    normalizar_destinatario,
    validar_configuracion_smtp,
)
from financiacion_educativa.services.autorizacion import (
    usuario_es_propietario_solicitud,
)


CODIGO_ERROR_ENTREGA = 'DELIVERY_BACKEND_ERROR'
PERSONA_A_ROL = {
    'estudiante': RolParticipante.STUDENT,
    'tutor': RolParticipante.GUARDIAN,
}
ROL_A_PERSONA = {valor: clave for clave, valor in PERSONA_A_ROL.items()}


class PuertoEntregaCapturaMovil(Protocol):
    def deliver(self, *, recipient, continuation_url, expires_at):
        ...


@dataclass(frozen=True, repr=False)
class EnlaceCapturaEmitido:
    enlace: EnlaceCapturaMovil
    url: str


def _clave_hmac():
    clave = str(
        getattr(
            settings,
            'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TOKEN_HMAC_KEY',
            settings.SECRET_KEY,
        )
    ).encode('utf-8')
    if not clave:
        raise ImproperlyConfigured(
            'La clave HMAC de captura movil no puede estar vacia.'
        )
    return clave


def calcular_hash_token(token):
    return hmac.new(
        _clave_hmac(),
        b'financiacion-educativa:captura-movil:' + token.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def calcular_hmac_destinatario(correo):
    return hmac.new(
        _clave_hmac(),
        (
            b'financiacion-educativa:captura-movil:destinatario:'
            + (correo or '').strip().lower().encode('utf-8')
        ),
        hashlib.sha256,
    ).hexdigest()


def _duracion():
    minutos = int(
        getattr(
            settings,
            'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TTL_MINUTES',
            30,
        )
    )
    if minutos <= 0:
        raise ImproperlyConfigured(
            'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TTL_MINUTES debe ser positivo.'
        )
    return timedelta(minutes=minutos)


def _construir_url(token):
    base = str(getattr(settings, 'BRAND_PUBLIC_BASE_URL', '')).rstrip('/')
    partes = urlsplit(base)
    if partes.scheme not in {'http', 'https'} or not partes.netloc:
        raise ImproperlyConfigured('BRAND_PUBLIC_BASE_URL no es una URL valida.')
    if partes.scheme != 'https' and not settings.DEBUG:
        raise ImproperlyConfigured(
            'BRAND_PUBLIC_BASE_URL debe usar HTTPS fuera de desarrollo.'
        )
    ruta = reverse(
        'financiacion_educativa_web:captura-movil-token',
    )
    # El fragmento no se envia al servidor ni aparece en access logs.
    return f'{base}{ruta}#{quote(token, safe="")}'


def _registrar_evento(enlace, tipo, *, actor=None, metadata=None):
    return EventoEnlaceCapturaMovil.objects.create(
        enlace=enlace,
        tipo=tipo,
        actor=actor,
        metadata=metadata or {},
    )


def _validar_persona(solicitud, persona):
    rol = PERSONA_A_ROL.get(persona)
    if not rol:
        raise ValidationError({'persona': 'La persona indicada no es valida.'})
    if not solicitud.roles_participantes.filter(rol=rol).exists():
        raise ValidationError({
            'persona': 'La persona no esta registrada en esta solicitud.',
        })
    return rol


@transaction.atomic
def emitir_enlace_captura_movil(*, solicitud, persona, actor):
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if (
        not usuario_es_propietario_solicitud(actor, solicitud)
        or solicitud.estado
        not in {
            EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
            EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
        }
    ):
        raise ValidationError(
            'La solicitud no admite continuacion documental movil.'
        )
    try:
        destinatario = normalizar_destinatario(solicitud.correo)
    except ImproperlyConfigured as error:
        raise ValidationError(
            'El correo registrado no permite enviar el enlace.'
        ) from error
    rol = _validar_persona(solicitud, persona)
    ahora = timezone.now()
    cooldown = max(
        0,
        int(
            getattr(
                settings,
                'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_COOLDOWN_SECONDS',
                120,
            )
        ),
    )
    ventana = max(
        1,
        int(
            getattr(
                settings,
                'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_REISSUE_WINDOW_HOURS',
                1,
            )
        ),
    )
    limite = max(
        1,
        int(
            getattr(
                settings,
                'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_REISSUE_LIMIT',
                5,
            )
        ),
    )
    recientes = EnlaceCapturaMovil.objects.filter(
        solicitud=solicitud,
        creada_en__gte=ahora - timedelta(hours=ventana),
    )
    ultimo = recientes.order_by('-creada_en').first()
    if ultimo and ultimo.creada_en > ahora - timedelta(seconds=cooldown):
        raise ValidationError(
            'Espera unos minutos antes de solicitar un nuevo enlace.'
        )
    if recientes.count() >= limite:
        raise ValidationError(
            'Se alcanzo el limite temporal de enlaces. Intenta mas tarde.'
        )

    anteriores = list(
        EnlaceCapturaMovil.objects.select_for_update().filter(
            solicitud=solicitud,
            estado=EstadoEnlaceCapturaMovil.ACTIVE,
        )
    )
    for anterior in anteriores:
        anterior.estado = EstadoEnlaceCapturaMovil.REVOKED
        anterior.revocada_en = ahora
        anterior.save(
            update_fields=['estado', 'revocada_en', 'actualizada_en']
        )
        _registrar_evento(
            anterior,
            TipoEventoEnlaceCapturaMovil.REVOKED,
            actor=actor,
            metadata={'reason': 'REPLACED'},
        )

    token = secrets.token_urlsafe(48)
    enlace = EnlaceCapturaMovil.objects.create(
        solicitud=solicitud,
        persona=rol,
        token_hash=calcular_hash_token(token),
        destinatario_hmac=calcular_hmac_destinatario(destinatario),
        vence_en=ahora + _duracion(),
        creada_por=actor,
    )
    _registrar_evento(
        enlace,
        TipoEventoEnlaceCapturaMovil.ISSUED,
        actor=actor,
        metadata={'expires_at': enlace.vence_en.isoformat()},
    )
    url = _construir_url(token)
    from financiacion_educativa.services.outbox_correos import crear_correo_captura

    crear_correo_captura(enlace=enlace)
    return EnlaceCapturaEmitido(enlace=enlace, url=url)


class DjangoEmailMobileCaptureDeliveryBackend:
    def deliver(
        self, *, recipient, continuation_url, expires_at, message_id=None
    ):
        if settings.EMAIL_BACKEND in SMTP_BACKENDS:
            validar_configuracion_smtp()
        elif not settings.DEBUG:
            raise ConfiguracionSMTPInvalida(
                'El envio movil requiere SMTP fuera de desarrollo.'
            )
        timeout = int(getattr(settings, 'EMAIL_TIMEOUT', 10))
        connection = get_connection(timeout=max(1, timeout))
        message = construir_correo_captura_movil(
            recipient=recipient,
            continuation_url=continuation_url,
            expires_at=expires_at,
            connection=connection,
        )
        if message_id:
            message.extra_headers['Message-ID'] = message_id
        if message.send(fail_silently=False) != 1:
            raise RuntimeError('No fue posible confirmar la entrega.')


def _delivery_backend():
    ruta = getattr(
        settings,
        'FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_DELIVERY_BACKEND',
        '',
    )
    if not ruta:
        raise ImproperlyConfigured(
            'Configura el backend de entrega para captura movil.'
        )
    backend = import_string(ruta)()
    if not callable(getattr(backend, 'deliver', None)):
        raise ImproperlyConfigured(
            'El backend de captura movil no implementa el puerto de entrega.'
        )
    return backend


@transaction.atomic
def _iniciar_entrega(enlace_id):
    enlace = (
        EnlaceCapturaMovil.objects.select_for_update()
        .select_related('solicitud')
        .get(pk=enlace_id)
    )
    if (
        enlace.estado != EstadoEnlaceCapturaMovil.ACTIVE
        or enlace.estado_entrega != EstadoEntregaCapturaMovil.PENDING
    ):
        return None
    enlace.estado_entrega = EstadoEntregaCapturaMovil.SENDING
    enlace.intentos_entrega += 1
    enlace.entrega_iniciada_en = timezone.now()
    enlace.codigo_ultimo_error = ''
    enlace.save(
        update_fields=[
            'estado_entrega',
            'intentos_entrega',
            'entrega_iniciada_en',
            'codigo_ultimo_error',
            'actualizada_en',
        ]
    )
    _registrar_evento(
        enlace,
        TipoEventoEnlaceCapturaMovil.DELIVERY_STARTED,
    )
    return enlace


@transaction.atomic
def _marcar_entrega(enlace_id, *, enviada, codigo_error=''):
    enlace = EnlaceCapturaMovil.objects.select_for_update().get(pk=enlace_id)
    if enlace.estado_entrega != EstadoEntregaCapturaMovil.SENDING:
        return enlace
    ahora = timezone.now()
    if enviada:
        enlace.estado_entrega = EstadoEntregaCapturaMovil.SENT
        enlace.enviada_en = ahora
        enlace.codigo_ultimo_error = ''
        tipo = TipoEventoEnlaceCapturaMovil.DELIVERY_SENT
        metadata = {}
    else:
        enlace.estado_entrega = EstadoEntregaCapturaMovil.FAILED
        enlace.fallida_en = ahora
        enlace.codigo_ultimo_error = (
            codigo_error or CODIGO_ERROR_ENTREGA
        )[:60]
        if enlace.estado == EstadoEnlaceCapturaMovil.ACTIVE:
            enlace.estado = EstadoEnlaceCapturaMovil.REVOKED
            enlace.revocada_en = ahora
        tipo = TipoEventoEnlaceCapturaMovil.DELIVERY_FAILED
        metadata = {'error_code': enlace.codigo_ultimo_error}
    enlace.save(
        update_fields=[
            'estado_entrega',
            'enviada_en',
            'fallida_en',
            'codigo_ultimo_error',
            'estado',
            'revocada_en',
            'actualizada_en',
        ]
    )
    _registrar_evento(enlace, tipo, metadata=metadata)
    if not enviada and enlace.estado == EstadoEnlaceCapturaMovil.REVOKED:
        _registrar_evento(
            enlace,
            TipoEventoEnlaceCapturaMovil.REVOKED,
            metadata={'reason': enlace.codigo_ultimo_error},
        )
    return enlace


def ejecutar_callback_entrega(*, enlace_id, continuation_url):
    raise RuntimeError(
        'La entrega directa fue retirada; procesa el outbox educativo.'
    )


def obtener_enlace_vigente_por_token(token):
    if not token:
        return None
    return (
        EnlaceCapturaMovil.objects.select_related('solicitud')
        .filter(
            token_hash=calcular_hash_token(token),
            estado=EstadoEnlaceCapturaMovil.ACTIVE,
            vence_en__gt=timezone.now(),
        )
        .first()
    )


@transaction.atomic
def consumir_enlace_captura_movil(*, enlace_id, usuario):
    enlace = (
        EnlaceCapturaMovil.objects.select_for_update()
        .select_related('solicitud')
        .filter(pk=enlace_id)
        .first()
    )
    if not enlace or enlace.estado != EstadoEnlaceCapturaMovil.ACTIVE:
        return None
    ahora = timezone.now()
    if enlace.vence_en <= ahora:
        enlace.estado = EstadoEnlaceCapturaMovil.REVOKED
        enlace.revocada_en = ahora
        enlace.save(
            update_fields=['estado', 'revocada_en', 'actualizada_en']
        )
        _registrar_evento(
            enlace,
            TipoEventoEnlaceCapturaMovil.REVOKED,
            metadata={'reason': 'EXPIRED'},
        )
        return None
    if (
        not usuario_es_propietario_solicitud(usuario, enlace.solicitud)
        or enlace.solicitud.estado
        not in {
            EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
            EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
        }
    ):
        return None
    persona = ROL_A_PERSONA.get(enlace.persona)
    if not persona or not enlace.solicitud.roles_participantes.filter(
        rol=enlace.persona
    ).exists():
        return None
    enlace.estado = EstadoEnlaceCapturaMovil.CONSUMED
    enlace.consumida_en = ahora
    enlace.consumida_por = usuario
    enlace.save(
        update_fields=[
            'estado',
            'consumida_en',
            'consumida_por',
            'actualizada_en',
        ]
    )
    _registrar_evento(
        enlace,
        TipoEventoEnlaceCapturaMovil.CONSUMED,
        actor=usuario,
    )
    return enlace, persona
