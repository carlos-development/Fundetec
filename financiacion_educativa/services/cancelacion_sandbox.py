from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo,
    EstadoProcesoAutomatizacionEducativa,
    EstadoProcesoFirmaEducativa,
    EstadoSolicitudFinanciacion,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    ProcesoAutomatizacionEducativa,
    ProcesoFirmaEducativa,
    ResultadoPublicoSandboxSolicitud,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.estados import transicionar_solicitud


@dataclass(frozen=True)
class ResultadoCancelacionSandbox:
    solicitud_id: object
    referencia_externa: str
    procesos_automatizacion_cerrados: int
    procesos_firma_cancelados: int
    artefactos_cancelados: int


def validar_ambiente_cancelacion_sandbox():
    if settings.DEPLOYMENT_ENVIRONMENT not in {'staging', 'test'}:
        raise PermissionDenied(
            'La cancelacion operativa Sandbox esta prohibida fuera de staging/test.'
        )


@transaction.atomic
def cancelar_solicitud_sandbox(*, application_id, actor, motivo):
    validar_ambiente_cancelacion_sandbox()
    if not actor or not actor.is_active or not actor.is_superuser:
        raise PermissionDenied('Se requiere un administrador activo.')
    motivo = str(motivo or '').strip()
    if len(motivo) < 12:
        raise ValidationError('Registra un motivo de auditoria suficientemente claro.')

    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=application_id
    )
    if solicitud.estado == EstadoSolicitudFinanciacion.CANCELLED:
        return ResultadoCancelacionSandbox(
            solicitud_id=solicitud.pk,
            referencia_externa=solicitud.referencia_externa,
            procesos_automatizacion_cerrados=0,
            procesos_firma_cancelados=0,
            artefactos_cancelados=0,
        )
    if solicitud.estado in {
        EstadoSolicitudFinanciacion.APPROVED,
        EstadoSolicitudFinanciacion.ACTIVE,
        EstadoSolicitudFinanciacion.PAYMENT_REPORTED,
        EstadoSolicitudFinanciacion.PAYMENT_UNDER_REVIEW,
        EstadoSolicitudFinanciacion.PAID,
        EstadoSolicitudFinanciacion.REJECTED,
    }:
        raise ValidationError('La solicitud ya tiene un estado terminal o financiero.')

    procesos_automatizacion = list(
        ProcesoAutomatizacionEducativa.objects.select_for_update().filter(
            solicitud=solicitud
        )
    )
    if any(
        proceso.estado == EstadoProcesoAutomatizacionEducativa.RUNNING
        for proceso in procesos_automatizacion
    ):
        raise ValidationError(
            'La automatizacion esta ejecutandose; detenga el worker y reintente.'
        )

    procesos_firma = list(
        ProcesoFirmaEducativa.objects.select_for_update().filter(
            solicitud=solicitud
        )
    )
    for proceso in procesos_firma:
        if proceso.estado in {
            EstadoProcesoFirmaEducativa.SENDING,
            EstadoProcesoFirmaEducativa.SENT,
            EstadoProcesoFirmaEducativa.SIGNED,
        } or proceso.token_documento_externo or proceso.enviado_en or proceso.intentos_envio:
            raise ValidationError(
                'Existe una firma enviada o potencialmente enviada; conciliala antes de cancelar.'
            )

    ahora = timezone.now()
    activos = {
        EstadoProcesoAutomatizacionEducativa.QUEUED,
        EstadoProcesoAutomatizacionEducativa.RUNNING,
        EstadoProcesoAutomatizacionEducativa.RETRYING,
        EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE,
    }
    procesos_cerrados = ProcesoAutomatizacionEducativa.objects.filter(
        solicitud=solicitud,
        estado__in=activos,
    ).update(
        estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
        codigo_razon='SANDBOX_APPLICATION_CANCELLED',
        finalizada_en=ahora,
        lease_id=None,
        lease_vence_en=None,
        actualizada_en=ahora,
    )
    firmas_canceladas = ProcesoFirmaEducativa.objects.filter(
        solicitud=solicitud,
        estado__in={
            EstadoProcesoFirmaEducativa.PENDING,
            EstadoProcesoFirmaEducativa.FAILED,
            EstadoProcesoFirmaEducativa.CANCELLED,
        },
    ).exclude(
        estado=EstadoProcesoFirmaEducativa.CANCELLED
    ).update(
        estado=EstadoProcesoFirmaEducativa.CANCELLED,
        codigo_ultimo_error='SANDBOX_APPLICATION_CANCELLED',
        actualizado_en=ahora,
    )
    artefactos_cancelados = ArtefactoContractualEducativo.objects.filter(
        solicitud=solicitud,
        vigente=True,
        estado=EstadoArtefactoContractualEducativo.GENERATED,
    ).update(
        estado=EstadoArtefactoContractualEducativo.CANCELLED,
        vigente=False,
        actualizado_en=ahora,
    )
    ResultadoPublicoSandboxSolicitud.objects.filter(
        solicitud=solicitud,
        activo=True,
    ).update(
        activo=False,
        actualizado_por=actor,
        actualizada_en=ahora,
    )
    solicitud = transicionar_solicitud(
        solicitud=solicitud,
        nuevo_estado=EstadoSolicitudFinanciacion.CANCELLED,
        actor=actor,
        motivo=motivo,
        metadata={
            'environment': settings.DEPLOYMENT_ENVIRONMENT,
            'reason_code': 'SANDBOX_APPLICATION_CANCELLED',
            'automation_processes_closed': procesos_cerrados,
            'signature_processes_cancelled': firmas_canceladas,
            'artifacts_cancelled': artefactos_cancelados,
        },
    )
    return ResultadoCancelacionSandbox(
        solicitud_id=solicitud.pk,
        referencia_externa=solicitud.referencia_externa,
        procesos_automatizacion_cerrados=procesos_cerrados,
        procesos_firma_cancelados=firmas_canceladas,
        artefactos_cancelados=artefactos_cancelados,
    )
