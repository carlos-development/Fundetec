from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoInvitacionContinuacion,
    EstadoSolicitudFinanciacion,
    TipoEventoInvitacion,
)
from financiacion_educativa.models import (
    EventoInvitacionContinuacion,
    InvitacionContinuacionSolicitud,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.estados import transicionar_solicitud
from financiacion_educativa.services.invitaciones import InvitacionNoValida
from financiacion_educativa.services.autorizacion import (
    usuario_coincide_con_correo,
)


@dataclass(frozen=True)
class ResultadoAsociacion:
    solicitud: SolicitudFinanciacionEducativa
    repetida: bool


@transaction.atomic
def asociar_usuario_mediante_invitacion(*, invitacion_id, usuario):
    if not usuario or not usuario.is_authenticated:
        raise InvitacionNoValida()

    invitacion = InvitacionContinuacionSolicitud.objects.select_for_update().filter(
        pk=invitacion_id
    ).first()
    if not invitacion:
        raise InvitacionNoValida()

    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=invitacion.solicitud_id
    )
    if (
        invitacion.estado == EstadoInvitacionContinuacion.CONSUMED
        and invitacion.consumida_por_id == usuario.pk
        and solicitud.usuario_id == usuario.pk
        and usuario_coincide_con_correo(usuario, solicitud.correo)
    ):
        return ResultadoAsociacion(solicitud=solicitud, repetida=True)

    if not invitacion.esta_vigente:
        raise InvitacionNoValida()
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION:
        raise InvitacionNoValida()
    if solicitud.usuario_id and solicitud.usuario_id != usuario.pk:
        raise InvitacionNoValida()
    if not usuario_coincide_con_correo(usuario, solicitud.correo):
        raise InvitacionNoValida()

    solicitud.usuario = usuario
    solicitud.save(update_fields=['usuario', 'actualizada_en'])
    solicitud = transicionar_solicitud(
        solicitud=solicitud,
        nuevo_estado=EstadoSolicitudFinanciacion.PENDING_TERMS,
        actor=usuario,
        motivo='Cuenta autenticada asociada mediante invitacion de continuacion.',
        metadata={'event': 'USER_ASSOCIATED'},
    )

    invitacion.estado = EstadoInvitacionContinuacion.CONSUMED
    invitacion.consumida_en = timezone.now()
    invitacion.consumida_por = usuario
    invitacion.save(
        update_fields=[
            'estado',
            'consumida_en',
            'consumida_por',
            'actualizada_en',
        ]
    )
    EventoInvitacionContinuacion.objects.create(
        invitacion=invitacion,
        tipo=TipoEventoInvitacion.CONSUMED,
        actor=usuario,
        metadata={'application_status': solicitud.estado},
    )
    return ResultadoAsociacion(solicitud=solicitud, repetida=False)
