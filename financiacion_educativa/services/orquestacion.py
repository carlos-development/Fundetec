from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEntregaInvitacion,
    EstadoInvitacionContinuacion,
    EstadoSolicitudFinanciacion,
    OrigenEntregaInvitacion,
    TipoEventoInvitacion,
)
from financiacion_educativa.models import (
    EntregaInvitacionContinuacion,
    InvitacionContinuacionSolicitud,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.entrega_invitaciones import (
    calcular_hmac_destinatario,
)
from financiacion_educativa.services.outbox_correos import (
    crear_correo_invitacion,
    programar_notificacion_nueva_solicitud_interna,
)
from financiacion_educativa.services.idempotencia import (
    crear_solicitud_idempotente,
)
from financiacion_educativa.services.invitaciones import (
    emitir_invitacion_continuacion,
    registrar_evento_invitacion,
    revocar_invitacion_continuacion,
)


@dataclass(frozen=True)
class ResultadoProgramacionInvitacion:
    entrega: EntregaInvitacionContinuacion
    creada: bool


def _entero_configuracion(nombre, predeterminado, *, minimo=0):
    valor = int(getattr(settings, nombre, predeterminado))
    if valor < minimo:
        raise ImproperlyConfigured(f'{nombre} debe ser mayor o igual a {minimo}.')
    return valor


def _siguiente_secuencia(solicitud):
    return (
        EntregaInvitacionContinuacion.objects.filter(
            solicitud=solicitud
        ).aggregate(maximo=Max('secuencia'))['maximo']
        or 0
    ) + 1


def _crear_entrega(
    *,
    solicitud,
    emitida,
    origen,
    actor=None,
    reemplaza_a=None,
    tipo_evento_correo=None,
    clave_idempotencia_correo=None,
):
    entrega = EntregaInvitacionContinuacion(
        solicitud=solicitud,
        invitacion=emitida.invitacion,
        secuencia=_siguiente_secuencia(solicitud),
        origen=origen,
        estado=EstadoEntregaInvitacion.PENDING,
        destinatario_hmac=calcular_hmac_destinatario(solicitud.correo),
        reemplaza_a=reemplaza_a,
        creada_por=actor,
    )
    entrega.full_clean()
    entrega.save()
    registrar_evento_invitacion(
        emitida.invitacion,
        TipoEventoInvitacion.DELIVERY_SCHEDULED,
        actor=actor,
        metadata={'delivery_id': str(entrega.pk), 'channel': entrega.canal},
    )
    crear_correo_invitacion(
        entrega=entrega,
        tipo_evento=tipo_evento_correo,
        clave_idempotencia=clave_idempotencia_correo,
    )
    return entrega


@transaction.atomic
def programar_invitacion_inicial(*, solicitud, actor=None):
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    existente = EntregaInvitacionContinuacion.objects.filter(
        solicitud=solicitud,
        origen=OrigenEntregaInvitacion.INITIAL,
    ).first()
    if existente:
        return ResultadoProgramacionInvitacion(entrega=existente, creada=False)
    emitida = emitir_invitacion_continuacion(
        solicitud=solicitud,
        actor=actor,
    )
    entrega = _crear_entrega(
        solicitud=solicitud,
        emitida=emitida,
        origen=OrigenEntregaInvitacion.INITIAL,
        actor=actor,
    )
    return ResultadoProgramacionInvitacion(entrega=entrega, creada=True)


@transaction.atomic
def crear_solicitud_institucional_orquestada(
    *,
    institucion,
    clave_idempotencia,
    datos,
):
    resultado = crear_solicitud_idempotente(
        institucion=institucion,
        clave_idempotencia=clave_idempotencia,
        datos=datos,
    )
    if not resultado.repetida:
        programar_invitacion_inicial(solicitud=resultado.solicitud)
        programar_notificacion_nueva_solicitud_interna(
            solicitud=resultado.solicitud,
        )
    return resultado


def _validar_actor_manual(actor):
    if not actor or not actor.is_authenticated or not actor.is_staff:
        raise ValidationError('La reemision requiere un usuario administrativo.')


def _validar_limites_reemision(solicitud):
    ahora = timezone.now()
    ventana_horas = _entero_configuracion(
        'FINANCIACION_EDUCATIVA_INVITATION_REISSUE_WINDOW_HOURS',
        24,
        minimo=1,
    )
    limite = _entero_configuracion(
        'FINANCIACION_EDUCATIVA_INVITATION_REISSUE_LIMIT',
        5,
        minimo=1,
    )
    cooldown = _entero_configuracion(
        'FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS',
        300,
    )
    inicio_ventana = ahora - timedelta(hours=ventana_horas)
    reemisiones = EntregaInvitacionContinuacion.objects.filter(
        solicitud=solicitud,
        origen__in=[
            OrigenEntregaInvitacion.AUTOMATIC_RETRY,
            OrigenEntregaInvitacion.MANUAL_REISSUE,
        ],
        creada_en__gte=inicio_ventana,
    )
    if reemisiones.count() >= limite:
        raise ValidationError('Se alcanzo el limite de reemisiones permitido.')
    ultima = EntregaInvitacionContinuacion.objects.filter(
        solicitud=solicitud
    ).order_by('-creada_en').first()
    if ultima and ultima.creada_en > ahora - timedelta(seconds=cooldown):
        raise ValidationError('La reemision se encuentra temporalmente limitada.')


@transaction.atomic
def reemitir_invitacion_orquestada(
    *,
    solicitud,
    origen,
    actor=None,
):
    if origen not in {
        OrigenEntregaInvitacion.AUTOMATIC_RETRY,
        OrigenEntregaInvitacion.MANUAL_REISSUE,
    }:
        raise ValidationError({'origen': 'El origen de reemision no es valido.'})
    if origen == OrigenEntregaInvitacion.MANUAL_REISSUE:
        _validar_actor_manual(actor)

    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if (
        solicitud.estado
        != EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION
        or solicitud.usuario_id
    ):
        raise ValidationError('La solicitud no admite reemisiones.')
    _validar_limites_reemision(solicitud)

    anterior = (
        EntregaInvitacionContinuacion.objects.select_for_update()
        .filter(solicitud=solicitud)
        .order_by('-secuencia')
        .first()
    )
    emitida = emitir_invitacion_continuacion(
        solicitud=solicitud,
        actor=actor,
    )
    if anterior and anterior.estado not in {
        EstadoEntregaInvitacion.CANCELLED,
        EstadoEntregaInvitacion.SUPERSEDED,
    }:
        anterior.estado = EstadoEntregaInvitacion.SUPERSEDED
        anterior.cancelada_en = timezone.now()
        anterior.save(
            update_fields=['estado', 'cancelada_en', 'actualizada_en']
        )
    entrega = _crear_entrega(
        solicitud=solicitud,
        emitida=emitida,
        origen=origen,
        actor=actor,
        reemplaza_a=anterior,
    )
    return entrega


@transaction.atomic
def revocar_invitacion_orquestada(*, solicitud, actor):
    _validar_actor_manual(actor)
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    invitacion = (
        InvitacionContinuacionSolicitud.objects.select_for_update()
        .filter(
            solicitud=solicitud,
            estado=EstadoInvitacionContinuacion.ACTIVE,
        )
        .order_by('-creada_en')
        .first()
    )
    if not invitacion:
        return None
    revocar_invitacion_continuacion(
        invitacion=invitacion,
        actor=actor,
        motivo='MANUAL',
    )
    entrega = EntregaInvitacionContinuacion.objects.select_for_update().filter(
        invitacion=invitacion
    ).first()
    if entrega and entrega.estado not in {
        EstadoEntregaInvitacion.CANCELLED,
        EstadoEntregaInvitacion.SUPERSEDED,
    }:
        entrega.estado = EstadoEntregaInvitacion.CANCELLED
        entrega.cancelada_en = timezone.now()
        entrega.save(
            update_fields=['estado', 'cancelada_en', 'actualizada_en']
        )
    return invitacion
