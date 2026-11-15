"""Narrow cancellation of one unused registration; never cancellation of a contract."""
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, OperationalError, transaction
from django.db.models import Q
from django.utils import timezone

from financiacion_educativa.models import (
    EntregaInvitacionContinuacion,
    InvitacionContinuacionSolicitud, OutboxCorreoEducativo, SolicitudFinanciacionEducativa,
)
from .estados import transicionar_solicitud
from .invitaciones import revocar_invitacion_continuacion


MOTIVO_CONTROLADO = 'DUPLICATE_REGISTRATION_CANCELLED'
RELACIONES_INICIALES = frozenset({
    'invitaciones_continuacion', 'entregas_invitacion', 'correos_outbox',
    'registros_idempotencia', 'historial_estados', 'eventos_seguridad', 'resultado_sandbox',
})
EVENTOS_INICIALES = frozenset({
    'INITIAL_INVITATION', 'INVITATION_REISSUE', 'CONTINUATION_REMINDER_1H',
    'CONTINUATION_REMINDER_6H', 'CONTINUATION_REMINDER_24H', 'CONTINUATION_REMINDER_48H',
    'NEW_APPLICATION_INTERNAL', 'AUDIT_COPY', 'INSTITUTIONAL_INITIAL_NOTIFICATION',
})


@dataclass(frozen=True)
class ResultadoCancelacionDuplicado:
    solicitud_id: object
    repetida: bool
    invitaciones_revocadas: int = 0
    entregas_detenidas: int = 0
    correos_detenidos: int = 0


def es_superadministrador(usuario):
    return bool(usuario and usuario.is_active and usuario.is_staff and usuario.is_superuser)


def _bloqueo(codigo, mensaje):
    raise ValidationError(mensaje, code=codigo)


def verificar_sin_avance(solicitud):
    if solicitud.estado != 'PENDING_USER_REGISTRATION':
        _bloqueo('DUPLICATE_STATE_ADVANCED', 'Solo se admite una solicitud pendiente de registro.')
    if solicitud.usuario_id is not None:
        _bloqueo('DUPLICATE_ACCOUNT_LINKED', 'La solicitud ya tiene una cuenta vinculada.')
    if solicitud.fecha_matricula is not None:
        _bloqueo('DUPLICATE_PROGRESS_FOUND', 'Existe una fecha de matricula efectiva; esta accion no aplica.')
    if solicitud.historial_estados.exclude(estado_nuevo='PENDING_USER_REGISTRATION').exists() or (
        solicitud.historial_estados.exclude(estado_anterior__isnull=True).exclude(
            estado_anterior__in=['', 'PENDING_USER_REGISTRATION']).exists()
    ):
        _bloqueo('DUPLICATE_PROGRESS_FOUND', 'El historial muestra avance previo; esta accion no aplica.')
    if solicitud.invitaciones_continuacion.filter(
        Q(estado='CONSUMED') | Q(consumida_en__isnull=False) | Q(consumida_por__isnull=False)
    ).exists():
        _bloqueo('DUPLICATE_INVITATION_CONSUMED', 'Existe una invitacion consumida; requiere revision fuera de esta accion.')
    if solicitud.entregas_invitacion.filter(estado='SENDING').exists() or solicitud.correos_outbox.filter(
        estado__in=['SENDING', 'AMBIGUOUS']).exists():
        _bloqueo('DUPLICATE_DELIVERY_IN_PROGRESS', 'Hay un envio en curso o ambiguo; debe resolverse antes de cancelar.')
    if solicitud.correos_outbox.exclude(tipo_evento__in=EVENTOS_INICIALES).exists():
        _bloqueo('DUPLICATE_PROGRESS_FOUND', 'Existen comunicaciones de una etapa posterior al registro.')
    if hasattr(solicitud, 'resultado_sandbox') and solicitud.resultado_sandbox.activo:
        _bloqueo('DUPLICATE_SIMULATION_ACTIVE', 'Existe un resultado Sandbox activo; no se modifica desde esta accion.')
    # Unknown future relations fail closed when they contain data. Initial API
    # fields, delivery history and idempotency records are not application progress.
    for relacion in solicitud._meta.related_objects:
        if relacion.get_accessor_name() in RELACIONES_INICIALES:
            continue
        if relacion.related_model._base_manager.filter(**{relacion.field.name: solicitud.pk}).exists():
            _bloqueo('DUPLICATE_PROGRESS_FOUND',
                f'Existe evidencia de avance ({relacion.related_model._meta.verbose_name}). No se cancela desde esta accion.')


def _lock(consulta):
    # Other services have existing invitation/outbox -> application lock orders.
    # NOWAIT rejects contention rather than waiting in an inverted lock chain.
    return consulta.select_for_update(nowait=True) if connection.features.has_select_for_update_nowait else consulta.select_for_update()


@transaction.atomic
def cancelar_duplicado_sin_registro(*, solicitud_id, actor, referencia, motivo, confirmar=False):
    actual = get_user_model().objects.filter(pk=getattr(actor, 'pk', None),
        is_active=True, is_staff=True, is_superuser=True).first()
    if not actual:
        raise PermissionDenied('Solo un superadministrador activo puede cancelar este duplicado.')
    motivo = str(motivo or '').strip()
    if confirmar is not True or not 12 <= len(motivo) <= 1000:
        _bloqueo('DUPLICATE_CONFIRMATION_REQUIRED', 'Confirma la accion e indica un motivo de 12 a 1000 caracteres.')
    try:
        solicitud = _lock(SolicitudFinanciacionEducativa.objects.all()).get(pk=solicitud_id)
        if referencia != solicitud.referencia_externa:
            _bloqueo('DUPLICATE_REFERENCE_MISMATCH', 'La referencia debe coincidir exactamente, incluyendo mayusculas y espacios.')
        if solicitud.estado == 'CANCELLED':
            anterior = solicitud.historial_estados.filter(estado_nuevo='CANCELLED',
                metadata__reason_code=MOTIVO_CONTROLADO, actor=actual, motivo=motivo).first()
            if anterior:
                return ResultadoCancelacionDuplicado(solicitud_id=solicitud.pk, repetida=True)
        invitaciones = list(_lock(InvitacionContinuacionSolicitud.objects.filter(solicitud=solicitud)).order_by('pk'))
        entregas = list(_lock(EntregaInvitacionContinuacion.objects.filter(solicitud=solicitud)).order_by('pk'))
        correos = list(_lock(OutboxCorreoEducativo.objects.filter(solicitud=solicitud)).order_by('pk'))
        verificar_sin_avance(solicitud)
        ahora = timezone.now()
        revocadas = 0
        for invitacion in invitaciones:
            if invitacion.estado == 'ACTIVE':
                revocar_invitacion_continuacion(invitacion=invitacion, actor=actual, motivo=MOTIVO_CONTROLADO)
                revocadas += 1
        entregas_ids = [e.pk for e in entregas if e.estado == 'PENDING']
        correos_ids = [c.pk for c in correos if c.estado in ('PENDING', 'RETRYING')]
        EntregaInvitacionContinuacion.objects.filter(pk__in=entregas_ids).update(
            estado='CANCELLED', cancelada_en=ahora, actualizada_en=ahora)
        OutboxCorreoEducativo.objects.filter(pk__in=correos_ids).update(
            estado='SUPERSEDED', codigo_ultimo_error=MOTIVO_CONTROLADO, actualizada_en=ahora)
        transicionar_solicitud(solicitud=solicitud, nuevo_estado='CANCELLED', actor=actual,
            motivo=motivo, metadata={'reason_code': MOTIVO_CONTROLADO, 'source': 'ADMIN_SINGLE_APPLICATION',
                'invitations_revoked': revocadas, 'deliveries_stopped': len(entregas_ids), 'outbox_stopped': len(correos_ids)})
        return ResultadoCancelacionDuplicado(solicitud_id=solicitud.pk, repetida=False,
            invitaciones_revocadas=revocadas, entregas_detenidas=len(entregas_ids), correos_detenidos=len(correos_ids))
    except OperationalError as error:
        origen = error.__cause__
        if getattr(origen, 'sqlstate', None) == '55P03' or getattr(origen, 'pgcode', None) == '55P03':
            _bloqueo('DUPLICATE_APPLICATION_BUSY', 'La solicitud o sus entregas estan en uso. Recarga y vuelve a revisar; no se cancelo nada.')
        raise
