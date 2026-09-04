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
    TipoEventoCorreoEducativo,
    TipoEventoInvitacion,
)
from financiacion_educativa.models import EntregaInvitacionContinuacion
from financiacion_educativa.services.invitaciones import (
    registrar_evento_invitacion,
)
from financiacion_educativa.services.correos import (
    normalizar_destinatario,
    obtener_email_logo_url,
)


CODIGO_ERROR_ENTREGA = 'DELIVERY_BACKEND_ERROR'


class PuertoEntregaInvitacion(Protocol):
    def deliver(self, *, recipient, continuation_url, expires_at):
        ...


def contenido_correo_invitacion(tipo_evento):
    brand_name = getattr(settings, 'EDUCATION_BRAND_NAME', 'Aprobado')
    mensajes = {
        TipoEventoCorreoEducativo.INITIAL_INVITATION: {
            'subject': f'Continua tu solicitud educativa con {brand_name}',
            'title': 'Recibimos tu solicitud educativa',
            'intro': 'Completa tu registro para continuar con el proceso.',
            'step': 'El siguiente paso es confirmar tu cuenta y revisar los terminos.',
            'badge_text': 'Solicitud recibida',
            'badge_background': '#EAF2FE',
            'badge_color': '#2D7FE0',
            'final_notice': False,
        },
        TipoEventoCorreoEducativo.CONTINUATION_REMINDER_1H: {
            'subject': 'Tu solicitud educativa te esta esperando',
            'title': 'Tu solicitud educativa te esta esperando',
            'intro': 'Puedes continuar desde el punto donde la dejaste.',
            'step': 'Confirma tu cuenta para avanzar al siguiente paso.',
            'badge_text': 'Solicitud pendiente',
            'badge_background': '#FFF3D6',
            'badge_color': '#8A6A00',
            'final_notice': False,
        },
        TipoEventoCorreoEducativo.CONTINUATION_REMINDER_6H: {
            'subject': 'Continua donde dejaste tu solicitud educativa',
            'title': 'Estas a un paso de continuar tu proceso educativo',
            'intro': 'Retoma tu solicitud desde el punto donde la dejaste.',
            'step': 'Usa el enlace seguro para retomar el proceso.',
            'badge_text': 'Continua donde la dejaste',
            'badge_background': '#EAF7EE',
            'badge_color': '#237A43',
            'final_notice': False,
        },
        TipoEventoCorreoEducativo.CONTINUATION_REMINDER_24H: {
            'subject': 'Completa el siguiente paso de tu financiacion educativa',
            'title': 'Completa el siguiente paso',
            'intro': 'Aun puedes continuar con tu solicitud educativa.',
            'step': 'Ingresa con el enlace seguro y completa el registro pendiente.',
            'badge_text': 'Solicitud pendiente',
            'badge_background': '#FFF3D6',
            'badge_color': '#8A6A00',
            'final_notice': False,
        },
        TipoEventoCorreoEducativo.CONTINUATION_REMINDER_48H: {
            'subject': 'Ultimo recordatorio automatico de tu solicitud educativa',
            'title': 'Ultimo recordatorio automatico',
            'intro': 'Este es el ultimo aviso automatico sobre esta solicitud.',
            'step': 'Si deseas continuar, utiliza el enlace seguro antes de su vencimiento.',
            'badge_text': 'Ultimo recordatorio automatico',
            'badge_background': '#FFF3D6',
            'badge_color': '#8A6A00',
            'final_notice': True,
        },
    }
    contenido = dict(mensajes.get(
        tipo_evento,
        mensajes[TipoEventoCorreoEducativo.INITIAL_INVITATION],
    ))
    eventos_recordatorio = (
        TipoEventoCorreoEducativo.CONTINUATION_REMINDER_1H,
        TipoEventoCorreoEducativo.CONTINUATION_REMINDER_6H,
        TipoEventoCorreoEducativo.CONTINUATION_REMINDER_24H,
        TipoEventoCorreoEducativo.CONTINUATION_REMINDER_48H,
    )
    maximo_mensajes = int(getattr(
        settings,
        'FINANCIACION_EDUCATIVA_CONTINUATION_MAX_MESSAGES',
        4,
    ))
    cantidad_recordatorios = min(
        max(maximo_mensajes - 1, 0),
        len(eventos_recordatorio),
    )
    evento_final = (
        eventos_recordatorio[cantidad_recordatorios - 1]
        if cantidad_recordatorios
        else None
    )
    if tipo_evento == evento_final:
        contenido.update({
            'subject': 'Ultimo recordatorio automatico de tu solicitud educativa',
            'title': 'Ultimo recordatorio automatico',
            'intro': 'Este es el ultimo aviso automatico sobre esta solicitud.',
            'step': (
                'Si deseas continuar, utiliza el enlace seguro antes de su '
                'vencimiento.'
            ),
            'badge_text': 'Ultimo recordatorio automatico',
            'badge_background': '#FFF3D6',
            'badge_color': '#8A6A00',
            'final_notice': True,
        })
    return contenido


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
        self,
        *,
        recipient,
        continuation_url,
        expires_at,
        message_id=None,
        email_context=None,
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
        email_context = email_context or {}
        tipo_evento = email_context.get(
            'event_type',
            TipoEventoCorreoEducativo.INITIAL_INVITATION,
        )
        contenido = contenido_correo_invitacion(tipo_evento)
        context = {
            'brand_name': brand_name,
            'continuation_url': continuation_url,
            'expires_at': expires_at,
            'email_logo_url': obtener_email_logo_url(),
            **email_context,
            **contenido,
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
            subject=contenido['subject'],
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
