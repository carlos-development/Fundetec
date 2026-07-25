import hashlib
import hmac
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.module_loading import import_string

from financiacion_educativa.choices import (
    EstadoEntregaInvitacion,
    TipoEventoInvitacion,
)
from financiacion_educativa.models import EntregaInvitacionContinuacion
from financiacion_educativa.services.invitaciones import (
    registrar_evento_invitacion,
)


CODIGO_ERROR_ENTREGA = 'DELIVERY_BACKEND_ERROR'


class PuertoEntregaInvitacion(Protocol):
    def deliver(self, *, recipient, continuation_url, expires_at):
        ...


def calcular_hmac_destinatario(correo):
    clave = str(
        getattr(
            settings,
            'FINANCIACION_EDUCATIVA_INVITATION_RECIPIENT_HMAC_KEY',
            settings.SECRET_KEY,
        )
    ).encode('utf-8')
    if not clave:
        raise ImproperlyConfigured(
            'La clave HMAC de entregas educativas no puede estar vacia.'
        )
    correo_normalizado = (correo or '').strip().lower().encode('utf-8')
    return hmac.new(
        clave,
        b'financiacion-educativa:invitacion:' + correo_normalizado,
        hashlib.sha256,
    ).hexdigest()


class DjangoEmailInvitationDeliveryBackend:
    def deliver(self, *, recipient, continuation_url, expires_at):
        timeout = int(
            getattr(
                settings,
                'FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_TIMEOUT_SECONDS',
                10,
            )
        )
        connection = get_connection(timeout=max(1, timeout))
        context = {
            'brand_name': settings.BRAND_NAME,
            'continuation_url': continuation_url,
            'expires_at': expires_at,
        }
        text_body = render_to_string(
            'emails/financiacion_educativa/invitacion_continuacion.txt',
            context,
        )
        html_body = render_to_string(
            'emails/financiacion_educativa/invitacion_continuacion.html',
            context,
        )
        message = EmailMultiAlternatives(
            subject=f'Continua tu solicitud educativa con {settings.BRAND_NAME}',
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            connection=connection,
        )
        message.attach_alternative(html_body, 'text/html')
        if message.send(fail_silently=False) != 1:
            raise RuntimeError('No fue posible confirmar la entrega.')


def _delivery_backend() -> PuertoEntregaInvitacion:
    ruta = getattr(
        settings,
        'FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND',
        '',
    )
    if not ruta:
        raise ImproperlyConfigured(
            'Configura el backend de entrega de invitaciones educativas.'
        )
    backend = import_string(ruta)()
    if not callable(getattr(backend, 'deliver', None)):
        raise ImproperlyConfigured(
            'El backend educativo no implementa el puerto de entrega.'
        )
    return backend


@transaction.atomic
def _iniciar_entrega(entrega_id):
    entrega = (
        EntregaInvitacionContinuacion.objects.select_for_update()
        .select_related('solicitud', 'invitacion')
        .get(pk=entrega_id)
    )
    if entrega.estado != EstadoEntregaInvitacion.PENDING:
        return None
    entrega.estado = EstadoEntregaInvitacion.SENDING
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
    registrar_evento_invitacion(
        entrega.invitacion,
        TipoEventoInvitacion.DELIVERY_STARTED,
        metadata={'delivery_id': str(entrega.pk)},
    )
    return entrega


@transaction.atomic
def _marcar_entrega_enviada(entrega_id):
    entrega = EntregaInvitacionContinuacion.objects.select_for_update().get(
        pk=entrega_id
    )
    if entrega.estado != EstadoEntregaInvitacion.SENDING:
        return entrega
    entrega.estado = EstadoEntregaInvitacion.SENT
    entrega.enviada_en = timezone.now()
    entrega.fallida_en = None
    entrega.codigo_ultimo_error = ''
    entrega.save(
        update_fields=[
            'estado',
            'enviada_en',
            'fallida_en',
            'codigo_ultimo_error',
            'actualizada_en',
        ]
    )
    registrar_evento_invitacion(
        entrega.invitacion,
        TipoEventoInvitacion.DELIVERY_SENT,
        metadata={'delivery_id': str(entrega.pk)},
    )
    return entrega


@transaction.atomic
def _marcar_entrega_fallida(entrega_id):
    entrega = EntregaInvitacionContinuacion.objects.select_for_update().get(
        pk=entrega_id
    )
    if entrega.estado not in {
        EstadoEntregaInvitacion.PENDING,
        EstadoEntregaInvitacion.SENDING,
    }:
        return entrega
    entrega.estado = EstadoEntregaInvitacion.FAILED
    entrega.fallida_en = timezone.now()
    entrega.codigo_ultimo_error = CODIGO_ERROR_ENTREGA
    entrega.save(
        update_fields=[
            'estado',
            'fallida_en',
            'codigo_ultimo_error',
            'actualizada_en',
        ]
    )
    registrar_evento_invitacion(
        entrega.invitacion,
        TipoEventoInvitacion.DELIVERY_FAILED,
        metadata={
            'delivery_id': str(entrega.pk),
            'error_code': CODIGO_ERROR_ENTREGA,
        },
    )
    return entrega


def ejecutar_callback_entrega(*, entrega_id, continuation_url):
    """Runs after commit and deliberately never propagates delivery failures."""
    try:
        entrega = _iniciar_entrega(entrega_id)
        if entrega is None:
            return
        _delivery_backend().deliver(
            recipient=entrega.solicitud.correo,
            continuation_url=continuation_url,
            expires_at=entrega.invitacion.vence_en,
        )
        _marcar_entrega_enviada(entrega_id)
    except Exception:
        try:
            _marcar_entrega_fallida(entrega_id)
        except Exception:
            pass
