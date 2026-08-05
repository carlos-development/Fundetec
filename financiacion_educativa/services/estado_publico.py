from dataclasses import dataclass

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo,
    EstadoProcesoFirmaEducativa,
    EstadoPublicoSolicitud,
    EstadoSolicitudFinanciacion,
    TipoDecisionRevisionEducativa,
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


def obtener_resultado_publico(solicitud):
    estado = MAPA_ESTADO_PUBLICO.get(
        solicitud.estado,
        EstadoPublicoSolicitud.UNDER_REVIEW,
    )
    decision = solicitud.decisiones_revision.order_by(
        '-creada_en',
        '-id',
    ).first()
    firma = solicitud.procesos_firma.select_related(
        'artefacto__fotografia_financiera'
    ).filter(
        estado=EstadoProcesoFirmaEducativa.SIGNED,
        artefacto__vigente=True,
        artefacto__estado=EstadoArtefactoContractualEducativo.SIGNED,
        artefacto__fotografia_financiera__activa=True,
        artefacto__fotografia_financiera__bloqueada=True,
        artefacto__fotografia_financiera__es_legado=False,
    ).order_by('-firmado_en').first()
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
    )
