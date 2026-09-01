from dataclasses import dataclass
from datetime import timedelta
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEntregaInvitacion,
    EstadoInvitacionContinuacion,
    EstadoSolicitudFinanciacion,
    OrigenEntregaInvitacion,
    TipoEventoCorreoEducativo,
)
from financiacion_educativa.models import (
    EntregaInvitacionContinuacion,
    InvitacionContinuacionSolicitud,
    OutboxCorreoEducativo,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.invitaciones import (
    emitir_invitacion_continuacion,
)
from financiacion_educativa.services.orquestacion import _crear_entrega
from financiacion_educativa.services.correos import (
    ConfiguracionSMTPInvalida,
    normalizar_destinatario,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HitoRecordatorio:
    tipo_evento: str
    horas: int


@dataclass(frozen=True)
class ResultadoProgramacionRecordatorios:
    evaluadas: int = 0
    programadas: int = 0
    omitidas: int = 0
    errores: int = 0


def _entero_positivo(nombre, predeterminado):
    try:
        valor = int(getattr(settings, nombre, predeterminado))
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(f'{nombre} debe ser un entero positivo.') from exc
    if valor <= 0:
        raise ImproperlyConfigured(f'{nombre} debe ser un entero positivo.')
    return valor


def hitos_recordatorio():
    hitos = (
        HitoRecordatorio(
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_1H,
            _entero_positivo(
                'FINANCIACION_EDUCATIVA_CONTINUATION_REMINDER_1_HOURS', 1
            ),
        ),
        HitoRecordatorio(
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_6H,
            _entero_positivo(
                'FINANCIACION_EDUCATIVA_CONTINUATION_REMINDER_2_HOURS', 6
            ),
        ),
        HitoRecordatorio(
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_24H,
            _entero_positivo(
                'FINANCIACION_EDUCATIVA_CONTINUATION_REMINDER_3_HOURS', 24
            ),
        ),
        HitoRecordatorio(
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_48H,
            _entero_positivo(
                'FINANCIACION_EDUCATIVA_CONTINUATION_REMINDER_FINAL_HOURS', 48
            ),
        ),
    )
    if [hito.horas for hito in hitos] != sorted({hito.horas for hito in hitos}):
        raise ImproperlyConfigured(
            'Las horas de recordatorios educativos deben ser unicas y ascendentes.'
        )
    return hitos


def _hito_elegible(solicitud, ahora, *, maximo_mensajes):
    hitos = hitos_recordatorio()[:max(maximo_mensajes - 1, 0)]
    if not hitos:
        return None
    tipos_existentes = set(
        OutboxCorreoEducativo.objects.filter(
            solicitud=solicitud,
            tipo_evento__in=[hito.tipo_evento for hito in hitos],
        ).values_list('tipo_evento', flat=True)
    )
    vencidos = [
        hito
        for hito in hitos
        if solicitud.creada_en + timedelta(hours=hito.horas) <= ahora
    ]
    if not vencidos:
        return None
    ultimo_vencido = vencidos[-1]
    return (
        None
        if ultimo_vencido.tipo_evento in tipos_existentes
        else ultimo_vencido
    )


def _evaluar_solicitud_bloqueada(solicitud, *, ahora, dry_run):
    if (
        solicitud.estado != EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION
        or solicitud.usuario_id
    ):
        return False
    try:
        normalizar_destinatario(solicitud.correo)
    except ConfiguracionSMTPInvalida:
        return False
    maximo = _entero_positivo(
        'FINANCIACION_EDUCATIVA_CONTINUATION_MAX_MESSAGES', 4
    )
    if EntregaInvitacionContinuacion.objects.filter(solicitud=solicitud).count() >= maximo:
        return False
    hito = _hito_elegible(
        solicitud,
        ahora,
        maximo_mensajes=maximo,
    )
    if not hito:
        return False
    invitaciones = InvitacionContinuacionSolicitud.objects.filter(
        solicitud=solicitud,
        estado=EstadoInvitacionContinuacion.ACTIVE,
    )
    if not dry_run:
        invitaciones = invitaciones.select_for_update()
    invitacion = invitaciones.order_by('-creada_en').first()
    if not invitacion:
        return False
    entregas = EntregaInvitacionContinuacion.objects.filter(solicitud=solicitud)
    if not dry_run:
        entregas = entregas.select_for_update()
    anterior = entregas.order_by('-secuencia').first()
    if not anterior or anterior.estado != EstadoEntregaInvitacion.SENT:
        return False
    if dry_run:
        return True

    emitida = emitir_invitacion_continuacion(solicitud=solicitud)
    anterior.estado = EstadoEntregaInvitacion.SUPERSEDED
    anterior.cancelada_en = ahora
    anterior.save(update_fields=['estado', 'cancelada_en', 'actualizada_en'])
    _crear_entrega(
        solicitud=solicitud,
        emitida=emitida,
        origen=OrigenEntregaInvitacion.SCHEDULED_REMINDER,
        reemplaza_a=anterior,
        tipo_evento_correo=hito.tipo_evento,
        clave_idempotencia_correo=(
            f'continuation-reminder:{solicitud.pk}:{hito.tipo_evento}'
        ),
    )
    return True


@transaction.atomic
def _procesar_candidata(solicitud_id, *, ahora, dry_run):
    queryset = SolicitudFinanciacionEducativa.objects.filter(pk=solicitud_id)
    if not dry_run:
        queryset = queryset.select_for_update()
    solicitud = queryset.get()
    return _evaluar_solicitud_bloqueada(
        solicitud,
        ahora=ahora,
        dry_run=dry_run,
    )


def programar_recordatorios_solicitudes(*, limite=None, dry_run=False, ahora=None):
    ahora = ahora or timezone.now()
    if limite is None:
        limite = _entero_positivo(
            'FINANCIACION_EDUCATIVA_CONTINUATION_REMINDER_BATCH_SIZE', 100
        )
    if limite <= 0:
        raise ImproperlyConfigured('El lote de recordatorios debe ser positivo.')
    primer_hito = hitos_recordatorio()[0]
    maximo = _entero_positivo(
        'FINANCIACION_EDUCATIVA_CONTINUATION_MAX_MESSAGES', 4
    )
    candidatas = list(
        SolicitudFinanciacionEducativa.objects.filter(
            estado=EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION,
            usuario__isnull=True,
            creada_en__lte=ahora - timedelta(hours=primer_hito.horas),
            invitaciones_continuacion__estado=(
                EstadoInvitacionContinuacion.ACTIVE
            ),
        )
        .annotate(cantidad_entregas=Count('entregas_invitacion', distinct=True))
        .filter(cantidad_entregas__lt=maximo)
        .order_by('creada_en', 'pk')
        .values_list('pk', flat=True)[:limite]
    )
    programadas = omitidas = errores = 0
    for solicitud_id in candidatas:
        try:
            creada = _procesar_candidata(
                solicitud_id,
                ahora=ahora,
                dry_run=dry_run,
            )
        except Exception as error:
            errores += 1
            logger.warning(
                'No fue posible programar recordatorio educativo: '
                'solicitud_id=%s clase=%s',
                solicitud_id,
                error.__class__.__name__,
            )
        else:
            if creada:
                programadas += 1
            else:
                omitidas += 1
    return ResultadoProgramacionRecordatorios(
        evaluadas=len(candidatas),
        programadas=programadas,
        omitidas=omitidas,
        errores=errores,
    )
