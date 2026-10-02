from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo,
    EstadoProcesoAutomatizacionEducativa,
    EstadoProcesoFirmaEducativa,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    ProcesoAutomatizacionEducativa,
    ProcesoFirmaEducativa,
    SolicitudFinanciacionEducativa,
)


@dataclass(frozen=True)
class ResultadoInvalidacionContractual:
    procesos_automatizacion_cerrados: int
    procesos_firma_cancelados: int
    artefactos_cancelados: int


@transaction.atomic
def invalidar_paquete_pendiente_por_nuevos_terminos(*, solicitud):
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
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
            'La automatizacion esta ejecutandose; intenta nuevamente en unos segundos.'
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
                'Existe una firma enviada o potencialmente enviada; '
                'requiere conciliacion antes de actualizar terminos.'
            )

    ahora = timezone.now()
    procesos_cerrados = ProcesoAutomatizacionEducativa.objects.filter(
        solicitud=solicitud,
        estado__in={
            EstadoProcesoAutomatizacionEducativa.QUEUED,
            EstadoProcesoAutomatizacionEducativa.RUNNING,
            EstadoProcesoAutomatizacionEducativa.RETRYING,
            EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE,
        },
    ).update(
        estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
        codigo_razon='CURRENT_TERMS_REQUIRED',
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
        },
    ).update(
        estado=EstadoProcesoFirmaEducativa.CANCELLED,
        codigo_ultimo_error='TERMS_VERSION_CHANGED',
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
    return ResultadoInvalidacionContractual(
        procesos_automatizacion_cerrados=procesos_cerrados,
        procesos_firma_cancelados=firmas_canceladas,
        artefactos_cancelados=artefactos_cancelados,
    )
