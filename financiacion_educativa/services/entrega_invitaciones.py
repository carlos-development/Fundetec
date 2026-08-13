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
from financiacion_educativa.services.correos import normalizar_destinatario


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
    def deliver(
        self, *, recipient, continuation_url, expires_at, message_id=None
    ):
        recipient = normalizar_destinatario(recipient)
        timeout = int(
            getattr(
                settings,
                'FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_TIMEOUT_SECONDS',
                10,
            )
        )
        connection = get_connection(timeout=max(1, timeout))
        brand_name = getattr(settings, 'EDUCATION_BRAND_NAME', 'Aprobado')
        context = {
            'brand_name': brand_name,
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
            subject=f'Continua tu solicitud educativa con {brand_name}',
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            connection=connection,
        )
        if message_id:
            message.extra_headers['Message-ID'] = message_id
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
def _marcar_entrega_fallida(entrega_id, *, codigo_error=''):
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
    entrega.codigo_ultimo_error = (
        codigo_error or CODIGO_ERROR_ENTREGA
    )[:60]
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
            'error_code': entrega.codigo_ultimo_error,
        },
    )
    return entrega


def ejecutar_callback_entrega(*, entrega_id, continuation_url):
    raise RuntimeError(
        'La entrega directa fue retirada; procesa el outbox educativo.'
    )
