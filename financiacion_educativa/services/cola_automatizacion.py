import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Max, Q
from django.utils import timezone

from financiacion_educativa.choices import (
    CodigoRazonAutomatizacionEducativa,
    EtapaAutomatizacionEducativa,
    EstadoProcesoAutomatizacionEducativa,
    EstadoSolicitudFinanciacion,
)
from financiacion_educativa.models import (
    EtapaProcesoAutomatizacionEducativa,
    ProcesoAutomatizacionEducativa,
    SolicitudFinanciacionEducativa,
)


logger = logging.getLogger(__name__)
ESTADOS_RECLAMABLES = {
    EstadoProcesoAutomatizacionEducativa.QUEUED,
    EstadoProcesoAutomatizacionEducativa.RETRYING,
}
ESTADOS_ACTIVOS = {
    *ESTADOS_RECLAMABLES,
    EstadoProcesoAutomatizacionEducativa.RUNNING,
    EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE,
}
CODIGOS_TEMPORALES = {
    'SCANNER_TIMEOUT',
    'SCANNER_UNAVAILABLE',
    'PROVIDER_TIMEOUT',
    'PROVIDER_ERROR',
    'DOCUMENT_CONTENT_TEMPORARY_ERROR',
    'SIGNATURE_SEND_RETRY_REQUIRED',
    'SIGNED_FILE_RECOVERY_FAILED',
}
CODIGOS_AMBIGUOS = {
    'SIGNATURE_SEND_AMBIGUOUS',
    'SIGNATURE_SEND_NOT_CONFIRMED',
}
CODIGOS_CONTROLADOS = frozenset(CodigoRazonAutomatizacionEducativa.values)


def _codigo_controlado(codigo):
    codigo = str(codigo or '').strip()
    if codigo in CODIGOS_CONTROLADOS:
        return codigo
    return CodigoRazonAutomatizacionEducativa.INTERNAL_ERROR


@dataclass(frozen=True)
class ResultadoEjecucionCola:
    procesado: bool
    proceso_id: object = None
    estado: str = ''
    codigo: str = ''


def _etapa_inicial(solicitud):
    if solicitud.estado == EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW:
        return EtapaAutomatizacionEducativa.SECURITY_SCAN
    if solicitud.estado == EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE:
        return EtapaAutomatizacionEducativa.CONTRACT_GENERATION
    if solicitud.estado == EstadoSolicitudFinanciacion.PENDING_SIGNATURE:
        return EtapaAutomatizacionEducativa.WAITING_SIGNATURE
    return None


@transaction.atomic
def encolar_proceso_automatizacion(*, solicitud_id):
    if not settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED:
        return None, False
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud_id
    )
    existente = solicitud.procesos_automatizacion.filter(
        estado__in=ESTADOS_ACTIVOS
    ).first()
    if existente:
        return existente, False
    etapa = _etapa_inicial(solicitud)
    if not etapa:
        return None, False
    version = (
        solicitud.procesos_automatizacion.aggregate(maxima=Max('version_expediente'))[
            'maxima'
        ]
        or 0
    ) + 1
    try:
        with transaction.atomic():
            proceso = ProcesoAutomatizacionEducativa.objects.create(
                solicitud=solicitud,
                version_expediente=version,
                estado=(
                    EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE
                    if etapa == EtapaAutomatizacionEducativa.WAITING_SIGNATURE
                    else EstadoProcesoAutomatizacionEducativa.QUEUED
                ),
                etapa_actual=etapa,
                maximo_intentos=(
                    settings.FINANCIACION_EDUCATIVA_WORKER_MAX_ATTEMPTS
                ),
            )
    except IntegrityError:
        proceso = solicitud.procesos_automatizacion.filter(
            estado__in=ESTADOS_ACTIVOS
        ).get()
        return proceso, False
    return proceso, True


def _query_reclamable(ahora):
    return Q(
        estado__in=ESTADOS_RECLAMABLES,
        proxima_ejecucion_en__lte=ahora,
    ) | Q(
        estado=EstadoProcesoAutomatizacionEducativa.RUNNING,
        lease_vence_en__lte=ahora,
    )


@transaction.atomic
def reclamar_siguiente_proceso():
    ahora = timezone.now()
    queryset = ProcesoAutomatizacionEducativa.objects.filter(
        _query_reclamable(ahora)
    ).order_by('proxima_ejecucion_en', 'creada_en')
    if connection.features.has_select_for_update_skip_locked:
        queryset = queryset.select_for_update(skip_locked=True)
    else:
        queryset = queryset.select_for_update()
    proceso = queryset.first()
    if not proceso:
        return None
    intento = proceso.intento_actual + 1
    if intento > proceso.maximo_intentos:
        proceso.estado = EstadoProcesoAutomatizacionEducativa.FAILED
        proceso.codigo_razon = 'MAX_ATTEMPTS_EXCEEDED'
        proceso.finalizada_en = ahora
        proceso.lease_id = None
        proceso.lease_vence_en = None
        proceso.save()
        return None
    proceso.estado = EstadoProcesoAutomatizacionEducativa.RUNNING
    proceso.intento_actual = intento
    proceso.lease_id = uuid.uuid4()
    proceso.lease_vence_en = ahora + timedelta(
        seconds=settings.FINANCIACION_EDUCATIVA_WORKER_LEASE_SECONDS
    )
    proceso.iniciada_en = proceso.iniciada_en or ahora
    proceso.codigo_razon = ''
    proceso.save()
    return proceso


def _backoff(intento):
    base = settings.FINANCIACION_EDUCATIVA_WORKER_BACKOFF_BASE_SECONDS
    maximo = settings.FINANCIACION_EDUCATIVA_WORKER_BACKOFF_MAX_SECONDS
    return min(base * (2 ** max(intento - 1, 0)), maximo)


@transaction.atomic
def _finalizar_etapa(*, proceso_id, lease_id, salida, iniciada_en):
    proceso = ProcesoAutomatizacionEducativa.objects.select_for_update().get(
        pk=proceso_id
    )
    if (
        proceso.estado != EstadoProcesoAutomatizacionEducativa.RUNNING
        or proceso.lease_id != lease_id
    ):
        return proceso
    ahora = timezone.now()
    estado = salida.estado
    codigo_original = str(salida.codigo or '').strip()
    codigo = _codigo_controlado(codigo_original)
    if codigo != codigo_original:
        estado = EstadoProcesoAutomatizacionEducativa.FAILED
    EtapaProcesoAutomatizacionEducativa.objects.create(
        proceso=proceso,
        etapa=proceso.etapa_actual,
        estado=estado,
        intento=proceso.intento_actual,
        codigo_razon=codigo,
        metadata_publica={
            'requires_correction': bool(salida.requisitos_correccion),
        },
        iniciada_en=iniciada_en,
    )
    proceso.estado = estado
    proceso.codigo_razon = codigo
    proceso.requisitos_correccion = list(salida.requisitos_correccion)
    proceso.lease_id = None
    proceso.lease_vence_en = None
    if estado == EstadoProcesoAutomatizacionEducativa.QUEUED:
        proceso.etapa_actual = salida.siguiente_etapa
        proceso.intento_actual = 0
        proceso.proxima_ejecucion_en = ahora
    elif estado == EstadoProcesoAutomatizacionEducativa.RETRYING:
        if proceso.intento_actual >= proceso.maximo_intentos:
            proceso.estado = EstadoProcesoAutomatizacionEducativa.FAILED
            proceso.codigo_razon = 'MAX_ATTEMPTS_EXCEEDED'
            proceso.finalizada_en = ahora
        else:
            proceso.proxima_ejecucion_en = ahora + timedelta(
                seconds=_backoff(proceso.intento_actual)
            )
    elif estado == EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE:
        proceso.etapa_actual = EtapaAutomatizacionEducativa.WAITING_SIGNATURE
    elif estado in {
        EstadoProcesoAutomatizacionEducativa.CORRECTION_REQUIRED,
        EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
        EstadoProcesoAutomatizacionEducativa.COMPLETED,
        EstadoProcesoAutomatizacionEducativa.FAILED,
    }:
        proceso.finalizada_en = ahora
    proceso.save()
    return proceso


def procesar_siguiente_trabajo():
    proceso = reclamar_siguiente_proceso()
    if not proceso:
        return ResultadoEjecucionCola(procesado=False)
    lease_id = proceso.lease_id
    iniciada_en = timezone.now()
    try:
        from financiacion_educativa.services.orquestacion_automatica import (
            ejecutar_etapa_persistente,
        )

        salida = ejecutar_etapa_persistente(
            solicitud_id=proceso.solicitud_id,
            etapa=proceso.etapa_actual,
        )
    except Exception as error:
        codigo_original = (
            getattr(error, 'codigo', '') or type(error).__name__.upper()
        )
        codigo = _codigo_controlado(codigo_original)
        logger.error(
            'Fallo controlado del worker educativo: proceso=%s etapa=%s tipo=%s',
            proceso.pk,
            proceso.etapa_actual,
            type(error).__name__,
        )
        from financiacion_educativa.services.orquestacion_automatica import (
            SalidaEtapaPersistente,
        )

        if codigo_original in CODIGOS_AMBIGUOS:
            estado = EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION
        elif codigo_original in CODIGOS_TEMPORALES:
            estado = EstadoProcesoAutomatizacionEducativa.RETRYING
        else:
            estado = EstadoProcesoAutomatizacionEducativa.FAILED
        salida = SalidaEtapaPersistente(estado=estado, codigo=codigo)
    actualizado = _finalizar_etapa(
        proceso_id=proceso.pk,
        lease_id=lease_id,
        salida=salida,
        iniciada_en=iniciada_en,
    )
    return ResultadoEjecucionCola(
        procesado=True,
        proceso_id=actualizado.pk,
        estado=actualizado.estado,
        codigo=actualizado.codigo_razon,
    )


@transaction.atomic
def recuperar_leases_vencidos():
    ahora = timezone.now()
    procesos = list(
        ProcesoAutomatizacionEducativa.objects.select_for_update().filter(
            estado=EstadoProcesoAutomatizacionEducativa.RUNNING,
            lease_vence_en__lte=ahora,
        )
    )
    for proceso in procesos:
        proceso.estado = EstadoProcesoAutomatizacionEducativa.RETRYING
        proceso.codigo_razon = CodigoRazonAutomatizacionEducativa.LEASE_EXPIRED
        proceso.lease_id = None
        proceso.lease_vence_en = None
        proceso.proxima_ejecucion_en = ahora
        proceso.save()
    return len(procesos)


@transaction.atomic
def completar_proceso_por_firma(*, solicitud_id):
    proceso = ProcesoAutomatizacionEducativa.objects.select_for_update().filter(
        solicitud_id=solicitud_id,
        estado=EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE,
    ).first()
    if not proceso:
        return None
    ahora = timezone.now()
    EtapaProcesoAutomatizacionEducativa.objects.create(
        proceso=proceso,
        etapa=EtapaAutomatizacionEducativa.COMPLETED,
        estado=EstadoProcesoAutomatizacionEducativa.COMPLETED,
        intento=1,
        codigo_razon=(
            CodigoRazonAutomatizacionEducativa.SIGNED_WEBHOOK_CONFIRMED
        ),
        metadata_publica={},
        iniciada_en=ahora,
    )
    proceso.estado = EstadoProcesoAutomatizacionEducativa.COMPLETED
    proceso.etapa_actual = EtapaAutomatizacionEducativa.COMPLETED
    proceso.codigo_razon = (
        CodigoRazonAutomatizacionEducativa.SIGNED_WEBHOOK_CONFIRMED
    )
    proceso.finalizada_en = ahora
    proceso.save()
    return proceso


@transaction.atomic
def cerrar_proceso_de_firma_interrumpido(*, solicitud_id, codigo):
    proceso = ProcesoAutomatizacionEducativa.objects.select_for_update().filter(
        solicitud_id=solicitud_id,
        estado=EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE,
    ).first()
    if not proceso:
        return None
    ahora = timezone.now()
    proceso.estado = EstadoProcesoAutomatizacionEducativa.CORRECTION_REQUIRED
    proceso.codigo_razon = _codigo_controlado(codigo)
    proceso.finalizada_en = ahora
    proceso.save()
    return proceso


def ejecutar_worker(*, limite=None, intervalo=2, una_vez=False):
    procesados = 0
    while limite is None or procesados < limite:
        resultado = procesar_siguiente_trabajo()
        if resultado.procesado:
            procesados += 1
            continue
        if una_vez:
            break
        time.sleep(intervalo)
    return procesados
