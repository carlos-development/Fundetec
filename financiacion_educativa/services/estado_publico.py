from dataclasses import dataclass

from django.db.models import F

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo,
    EstadoProcesoFirmaEducativa,
    EstadoPublicoSolicitud,
    EstadoSolicitudFinanciacion,
    TipoDecisionRevisionEducativa,
    TipoArtefactoContractualEducativo,
)


MAPA_ESTADO_PUBLICO = {
    EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION: (
        EstadoPublicoSolicitud.RECEIVED
    ),
    EstadoSolicitudFinanciacion.PENDING_TERMS: (
        EstadoPublicoSolicitud.ACTION_REQUIRED
    ),
    EstadoSolicitudFinanciacion.PENDING_DOCUMENT: (
        EstadoPublicoSolicitud.ACTION_REQUIRED
    ),
    EstadoSolicitudFinanciacion.PENDING_GUARDIAN: (
        EstadoPublicoSolicitud.ACTION_REQUIRED
    ),
    EstadoSolicitudFinanciacion.CORRECTION_REQUIRED: (
        EstadoPublicoSolicitud.ACTION_REQUIRED
    ),
    EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW: (
        EstadoPublicoSolicitud.UNDER_REVIEW
    ),
    EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE: (
        EstadoPublicoSolicitud.UNDER_REVIEW
    ),
    EstadoSolicitudFinanciacion.PENDING_SIGNATURE: (
        EstadoPublicoSolicitud.UNDER_REVIEW
    ),
    EstadoSolicitudFinanciacion.APPROVED: EstadoPublicoSolicitud.APPROVED,
    EstadoSolicitudFinanciacion.REJECTED: EstadoPublicoSolicitud.REJECTED,
    EstadoSolicitudFinanciacion.CANCELLED: EstadoPublicoSolicitud.CANCELLED,
    EstadoSolicitudFinanciacion.ACTIVE: EstadoPublicoSolicitud.UNDER_REVIEW,
    EstadoSolicitudFinanciacion.PAYMENT_REPORTED: (
        EstadoPublicoSolicitud.UNDER_REVIEW
    ),
    EstadoSolicitudFinanciacion.PAYMENT_UNDER_REVIEW: (
        EstadoPublicoSolicitud.UNDER_REVIEW
    ),
    EstadoSolicitudFinanciacion.PAID: EstadoPublicoSolicitud.UNDER_REVIEW,
}


@dataclass(frozen=True)
class ResultadoPublicoSolicitud:
    estado: str
    curso_autorizado: bool
    autorizacion_efectiva_en: object = None
    motivo_decision: str = ''
    condiciones_financieras: dict | None = None
    actualizada_en: object = None


def firmas_contractuales_vigentes():
    """Evidencia de firma coherente; no consulta simulaciones ni proveedores."""
    from financiacion_educativa.models import ProcesoFirmaEducativa

    return ProcesoFirmaEducativa.objects.select_related(
        'artefacto__fotografia_financiera',
    ).filter(
        estado=EstadoProcesoFirmaEducativa.SIGNED,
        firmado_en__isnull=False,
        artefacto__solicitud_id=F('solicitud_id'),
        artefacto__tipo=TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
        artefacto__vigente=True,
        artefacto__estado=EstadoArtefactoContractualEducativo.SIGNED,
        artefacto__firmado_en=F('firmado_en'),
        artefacto__tamano_firmado_bytes__gt=0,
        artefacto__fotografia_financiera__solicitud_id=F('solicitud_id'),
        artefacto__fotografia_financiera__activa=True,
        artefacto__fotografia_financiera__bloqueada=True,
        artefacto__fotografia_financiera__es_legado=False,
    ).exclude(artefacto__archivo_firmado='').exclude(
        artefacto__archivo_firmado__isnull=True,
    ).exclude(artefacto__hash_firmado_sha256='')


def firmas_autorizadas_reales():
    return firmas_contractuales_vigentes().filter(
        solicitud__estado=EstadoSolicitudFinanciacion.APPROVED,
    )


def obtener_resultado_publico(solicitud, *, aplicar_sandbox=True):
    from financiacion_educativa.models import ResultadoPublicoSandboxSolicitud
    from financiacion_educativa.services.resultado_sandbox import validar_ambiente_sandbox
    simulacion = ResultadoPublicoSandboxSolicitud.objects.filter(solicitud_id=solicitud.pk).first()
    if simulacion:
        validar_ambiente_sandbox()
        if aplicar_sandbox and simulacion.activo:
            return ResultadoPublicoSolicitud(
                estado=simulacion.estado_publico, curso_autorizado=simulacion.course_authorized,
                autorizacion_efectiva_en=simulacion.actualizada_en if simulacion.course_authorized else None,
                motivo_decision=simulacion.decision_reason,
                condiciones_financieras=simulacion.condiciones_financieras or None,
                actualizada_en=max(solicitud.actualizada_en, simulacion.actualizada_en),
            )
    estado = MAPA_ESTADO_PUBLICO.get(
        solicitud.estado,
        EstadoPublicoSolicitud.UNDER_REVIEW,
    )
    decision = solicitud.decisiones_revision.order_by(
        '-creada_en',
        '-id',
    ).first()
    firma = firmas_autorizadas_reales().filter(
        solicitud_id=solicitud.pk,
    ).order_by('-firmado_en', '-pk').first()
    aprobada = bool(
        estado == EstadoPublicoSolicitud.APPROVED
        and firma
        and firma.firmado_en
    )
    condiciones = None
    if aprobada:
        fotografia = firma.artefacto.fotografia_financiera
        condiciones = {
            'currency': fotografia.moneda,
            'requested_amount': format(fotografia.valor_financiado, '.2f'),
            'financed_amount': format(fotografia.capital_financiado, '.2f'),
            'term_months': fotografia.plazo_meses,
            'estimated_installment': format(
                fotografia.valor_cuota_estimada,
                '.2f',
            ),
        }
    return ResultadoPublicoSolicitud(
        estado=estado,
        curso_autorizado=aprobada,
        autorizacion_efectiva_en=firma.firmado_en if aprobada else None,
        motivo_decision=(
            decision.motivo
            if decision
            and decision.tipo
            in {
                TipoDecisionRevisionEducativa.REJECTED,
                TipoDecisionRevisionEducativa.CORRECTION_REQUESTED,
            }
            else ''
        ),
        condiciones_financieras=condiciones,
        actualizada_en=max(solicitud.actualizada_en, simulacion.actualizada_en) if simulacion and aplicar_sandbox else solicitud.actualizada_en,
    )
