from dataclasses import dataclass

from financiacion_educativa.choices import (
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
    aprobada = bool(
        estado == EstadoPublicoSolicitud.APPROVED
        and decision
        and decision.tipo == TipoDecisionRevisionEducativa.APPROVED
        and decision.fotografia_financiera_id
    )
    condiciones = None
    if aprobada:
        fotografia = decision.fotografia_financiera
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
        autorizacion_efectiva_en=decision.creada_en if aprobada else None,
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
