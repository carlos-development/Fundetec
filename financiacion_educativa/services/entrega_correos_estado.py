from django.conf import settings
from django.core.mail import get_connection
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import EstadoEntregaCorreoSolicitud
from financiacion_educativa.models import EntregaCorreoEstadoSolicitud
from financiacion_educativa.services.correos import (
    clasificar_error_entrega,
    construir_correo_decision_educativa,
)


@transaction.atomic
def _iniciar_entrega(entrega_id):
    entrega = (
        EntregaCorreoEstadoSolicitud.objects.select_for_update()
        .select_related('solicitud', 'decision')
        .get(pk=entrega_id)
    )
    if entrega.estado != EstadoEntregaCorreoSolicitud.PENDING:
        return None
    entrega.estado = EstadoEntregaCorreoSolicitud.SENDING
    entrega.intentos += 1
    entrega.iniciada_en = timezone.now()
    entrega.codigo_ultimo_error = ''
    entrega.save(
        update_fields=[
            'estado',
            'intentos',
            'iniciada_en',
            'codigo_ultimo_error',
            'actualizada_en',
        ]
    )
    return entrega


@transaction.atomic
def _finalizar_entrega(entrega_id, *, enviada, codigo_error=''):
    entrega = EntregaCorreoEstadoSolicitud.objects.select_for_update().get(
        pk=entrega_id
    )
    if entrega.estado != EstadoEntregaCorreoSolicitud.SENDING:
        return entrega
    ahora = timezone.now()
    if enviada:
        entrega.estado = EstadoEntregaCorreoSolicitud.SENT
        entrega.enviada_en = ahora
        entrega.codigo_ultimo_error = ''
    else:
        entrega.estado = EstadoEntregaCorreoSolicitud.FAILED
        entrega.fallida_en = ahora
        entrega.codigo_ultimo_error = (
            codigo_error or 'DELIVERY_BACKEND_ERROR'
        )[:60]
    entrega.save(
        update_fields=[
            'estado',
            'enviada_en',
            'fallida_en',
            'codigo_ultimo_error',
            'actualizada_en',
        ]
    )
    return entrega


def ejecutar_entrega_correo_estado(*, entrega_id):
    try:
        entrega = _iniciar_entrega(entrega_id)
        if entrega is None:
            return
        connection = get_connection(
            timeout=max(1, int(getattr(settings, 'EMAIL_TIMEOUT', 10)))
        )
        mensaje = construir_correo_decision_educativa(
            recipient=entrega.solicitud.correo,
            decision=entrega.decision,
            connection=connection,
        )
        if mensaje.send(fail_silently=False) != 1:
            raise RuntimeError('No fue posible confirmar la entrega.')
        _finalizar_entrega(entrega_id, enviada=True)
    except Exception as error:
        try:
            _finalizar_entrega(
                entrega_id,
                enviada=False,
                codigo_error=clasificar_error_entrega(error),
            )
        except Exception:
            pass
