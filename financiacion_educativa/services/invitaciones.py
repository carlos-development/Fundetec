import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoInvitacionContinuacion,
    EstadoSolicitudFinanciacion,
    PropositoInvitacionContinuacion,
    TipoEventoInvitacion,
)
from financiacion_educativa.models import (
    EventoInvitacionContinuacion,
    InvitacionContinuacionSolicitud,
    SolicitudFinanciacionEducativa,
)


class InvitacionNoValida(Exception):
    pass


@dataclass(frozen=True, repr=False)
class InvitacionEmitida:
    invitacion: InvitacionContinuacionSolicitud
    token: str
    url: str


def calcular_hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def duracion_invitacion():
    horas = int(
        getattr(settings, 'FINANCIACION_EDUCATIVA_INVITACION_TTL_HOURS', 72)
    )
    if horas <= 0:
        raise ImproperlyConfigured(
            'FINANCIACION_EDUCATIVA_INVITACION_TTL_HOURS debe ser positivo.'
        )
    return timedelta(hours=horas)


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
        'financiacion_educativa_web:continuar-invitacion',
        kwargs={'token': token},
    )
    return f'{base}{ruta}'


def _registrar_evento(invitacion, tipo, actor=None, metadata=None):
    return EventoInvitacionContinuacion.objects.create(
        invitacion=invitacion,
        tipo=tipo,
        actor=actor,
        metadata=metadata or {},
    )


@transaction.atomic
def emitir_invitacion_continuacion(*, solicitud, actor=None):
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION:
        raise ValidationError({
            'estado': 'La solicitud no admite una invitacion de continuacion.',
        })
    if solicitud.usuario_id:
        raise ValidationError({
            'usuario': 'La solicitud ya tiene una cuenta asociada.',
        })

    ahora = timezone.now()
    anteriores = list(
        InvitacionContinuacionSolicitud.objects.select_for_update().filter(
            solicitud=solicitud,
            estado=EstadoInvitacionContinuacion.ACTIVE,
        )
    )
    for anterior in anteriores:
        anterior.estado = EstadoInvitacionContinuacion.REVOKED
        anterior.save(update_fields=['estado', 'actualizada_en'])
        _registrar_evento(
            anterior,
            TipoEventoInvitacion.REVOKED,
            actor=actor,
            metadata={'reason': 'REPLACED'},
        )

    token = secrets.token_urlsafe(48)
    invitacion = InvitacionContinuacionSolicitud.objects.create(
        solicitud=solicitud,
        token_hash=calcular_hash_token(token),
        proposito=PropositoInvitacionContinuacion.CONTINUE_APPLICATION,
        estado=EstadoInvitacionContinuacion.ACTIVE,
        vence_en=ahora + duracion_invitacion(),
    )
    _registrar_evento(
        invitacion,
        TipoEventoInvitacion.ISSUED,
        actor=actor,
        metadata={'expires_at': invitacion.vence_en.isoformat()},
    )
    return InvitacionEmitida(
        invitacion=invitacion,
        token=token,
        url=_construir_url(token),
    )


def obtener_invitacion_vigente_por_token(token):
    if not token:
        return None
    token_hash = calcular_hash_token(token)
    invitacion = InvitacionContinuacionSolicitud.objects.select_related(
        'solicitud'
    ).filter(token_hash=token_hash).first()
    if not invitacion or not invitacion.esta_vigente:
        return None
    if (
        invitacion.solicitud.estado
        != EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION
    ):
        return None
    return invitacion


def obtener_invitacion_vigente_por_id(invitacion_id):
    if not invitacion_id:
        return None
    invitacion = InvitacionContinuacionSolicitud.objects.select_related(
        'solicitud'
    ).filter(pk=invitacion_id).first()
    if not invitacion or not invitacion.esta_vigente:
        return None
    if (
        invitacion.solicitud.estado
        != EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION
    ):
        return None
    return invitacion


@transaction.atomic
def revocar_invitacion_continuacion(*, invitacion, actor=None, motivo='MANUAL'):
    invitacion = InvitacionContinuacionSolicitud.objects.select_for_update().get(
        pk=invitacion.pk
    )
    if invitacion.estado != EstadoInvitacionContinuacion.ACTIVE:
        return invitacion
    invitacion.estado = EstadoInvitacionContinuacion.REVOKED
    invitacion.save(update_fields=['estado', 'actualizada_en'])
    _registrar_evento(
        invitacion,
        TipoEventoInvitacion.REVOKED,
        actor=actor,
        metadata={'reason': motivo},
    )
    return invitacion
