from dataclasses import dataclass

from django.urls import reverse

from financiacion_educativa.choices import EstadoSolicitudFinanciacion as Estado


@dataclass(frozen=True)
class DestinoReanudacion:
    ruta: str
    titulo: str
    accion: str
    inconclusa: bool


MAPA_REANUDACION = {
    Estado.PENDING_USER_REGISTRATION: DestinoReanudacion(
        'financiacion_educativa_web:estado-solicitud',
        'Asociacion pendiente',
        'Consultar estado',
        True,
    ),
    Estado.PENDING_TERMS: DestinoReanudacion(
        'financiacion_educativa_web:terminos',
        'Terminos pendientes',
        'Revisar terminos',
        True,
    ),
    Estado.PENDING_DOCUMENT: DestinoReanudacion(
        'financiacion_educativa_web:documentacion',
        'Documentacion pendiente',
        'Continuar expediente',
        True,
    ),
    Estado.PENDING_GUARDIAN: DestinoReanudacion(
        'financiacion_educativa_web:documentacion',
        'Tutor pendiente',
        'Registrar tutor',
        True,
    ),
    Estado.PENDING_MANUAL_REVIEW: DestinoReanudacion(
        'financiacion_educativa_web:procesamiento',
        'Expediente en revision',
        'Consultar estado',
        True,
    ),
    Estado.CORRECTION_REQUIRED: DestinoReanudacion(
        'financiacion_educativa_web:documentacion',
        'Correcciones pendientes',
        'Corregir expediente',
        True,
    ),
    Estado.PENDING_PROMISSORY_NOTE: DestinoReanudacion(
        'financiacion_educativa_web:procesamiento',
        'Documentos contractuales en preparacion',
        'Consultar estado',
        True,
    ),
    Estado.PENDING_SIGNATURE: DestinoReanudacion(
        'financiacion_educativa_web:procesamiento',
        'Firma pendiente',
        'Consultar firma',
        True,
    ),
    Estado.APPROVED: DestinoReanudacion(
        'financiacion_educativa_web:finanzas',
        'Financiacion aprobada',
        'Consultar financiacion',
        False,
    ),
    Estado.REJECTED: DestinoReanudacion(
        'financiacion_educativa_web:estado-solicitud',
        'Solicitud no aprobada',
        'Consultar resultado',
        False,
    ),
    Estado.ACTIVE: DestinoReanudacion(
        'financiacion_educativa_web:finanzas',
        'Financiacion activa',
        'Consultar financiacion',
        False,
    ),
    Estado.PAYMENT_REPORTED: DestinoReanudacion(
        'financiacion_educativa_web:finanzas',
        'Pago reportado',
        'Consultar financiacion',
        False,
    ),
    Estado.PAYMENT_UNDER_REVIEW: DestinoReanudacion(
        'financiacion_educativa_web:finanzas',
        'Pago en revision',
        'Consultar financiacion',
        False,
    ),
    Estado.PAID: DestinoReanudacion(
        'financiacion_educativa_web:finanzas',
        'Financiacion pagada',
        'Consultar financiacion',
        False,
    ),
    Estado.CANCELLED: DestinoReanudacion(
        'financiacion_educativa_web:estado-solicitud',
        'Solicitud cancelada',
        'Consultar resultado',
        False,
    ),
}


if set(MAPA_REANUDACION) != set(Estado.values):
    raise RuntimeError('El mapa de reanudacion no cubre todos los estados reales.')


def resolver_destino_reanudacion(solicitud):
    return MAPA_REANUDACION[solicitud.estado]


def resolver_url_reanudacion(solicitud):
    destino = resolver_destino_reanudacion(solicitud)
    return reverse(destino.ruta, kwargs={'solicitud_id': solicitud.pk})
