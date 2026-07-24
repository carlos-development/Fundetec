from django.core.exceptions import ValidationError
from django.db import transaction

from financiacion_educativa.choices import EstadoSolicitudFinanciacion as Estado
from financiacion_educativa.models import (
    HistorialEstadoSolicitud,
    SolicitudFinanciacionEducativa,
)


TRANSICIONES_PERMITIDAS = {
    Estado.PENDING_USER_REGISTRATION: {Estado.PENDING_TERMS, Estado.CANCELLED},
    Estado.PENDING_TERMS: {Estado.PENDING_DOCUMENT, Estado.CANCELLED},
    Estado.PENDING_DOCUMENT: {
        Estado.PENDING_GUARDIAN,
        Estado.PENDING_MANUAL_REVIEW,
        Estado.CANCELLED,
    },
    Estado.PENDING_GUARDIAN: {Estado.PENDING_DOCUMENT, Estado.CANCELLED},
    Estado.PENDING_MANUAL_REVIEW: {
        Estado.PENDING_DOCUMENT,
        Estado.PENDING_PROMISSORY_NOTE,
        Estado.CANCELLED,
    },
    Estado.PENDING_PROMISSORY_NOTE: {Estado.PENDING_SIGNATURE, Estado.CANCELLED},
    Estado.PENDING_SIGNATURE: {
        Estado.PENDING_PROMISSORY_NOTE,
        Estado.ACTIVE,
        Estado.CANCELLED,
    },
    Estado.ACTIVE: {Estado.PAYMENT_REPORTED, Estado.PAID},
    Estado.PAYMENT_REPORTED: {Estado.PAYMENT_UNDER_REVIEW},
    Estado.PAYMENT_UNDER_REVIEW: {Estado.ACTIVE, Estado.PAID},
    Estado.PAID: set(),
    Estado.CANCELLED: set(),
}


def transicion_es_valida(estado_anterior, estado_nuevo):
    return estado_nuevo in TRANSICIONES_PERMITIDAS.get(estado_anterior, set())


@transaction.atomic
def transicionar_solicitud(
    *,
    solicitud,
    nuevo_estado,
    actor=None,
    motivo='',
    metadata=None,
):
    if not solicitud or not solicitud.pk:
        raise ValidationError({'solicitud': 'La solicitud es obligatoria.'})

    solicitud_bloqueada = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    estado_anterior = solicitud_bloqueada.estado

    if not transicion_es_valida(estado_anterior, nuevo_estado):
        raise ValidationError({
            'estado': f'Transicion invalida: {estado_anterior} -> {nuevo_estado}.',
        })

    solicitud_bloqueada.estado = nuevo_estado
    solicitud_bloqueada.save(update_fields=['estado', 'actualizada_en'])
    HistorialEstadoSolicitud.objects.create(
        solicitud=solicitud_bloqueada,
        estado_anterior=estado_anterior,
        estado_nuevo=nuevo_estado,
        actor=actor,
        motivo=(motivo or '').strip(),
        metadata=metadata or {},
    )
    return solicitud_bloqueada
