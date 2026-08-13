import hashlib
import inspect
import logging
import secrets
import smtplib
import socket
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.mail import get_connection
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from financiacion_educativa.choices import (
    CodigoMensajeCorreoEducativo,
    EstadoEnlaceCapturaMovil,
    EstadoEntregaCapturaMovil,
    EstadoEntregaInvitacion,
    EstadoInvitacionContinuacion,
    EstadoOutboxCorreoEducativo,
    TipoEventoCorreoEducativo,
    TipoEventoEnlaceCapturaMovil,
    TipoEventoInvitacion,
)
from financiacion_educativa.models import (
    EnlaceCapturaMovil,
    EntregaCorreoEstadoSolicitud,
    EntregaInvitacionContinuacion,
    OutboxCorreoEducativo,
)
from financiacion_educativa.services.correos import (
    ConfiguracionSMTPInvalida,
    SMTP_BACKENDS,
    construir_correo_correccion_automatica,
    construir_correo_continuacion_automatica,
    construir_correo_decision_educativa,
    construir_correo_expediente_recibido,
    normalizar_destinatario,
    validar_configuracion_smtp,
)


logger = logging.getLogger(__name__)
ESTADOS_RECLAMABLES = {
    EstadoOutboxCorreoEducativo.PENDING,
    EstadoOutboxCorreoEducativo.RETRYING,
}
CODIGOS_TEMPORALES = {
    'SMTP_CONNECT_ERROR',
    'SMTP_TEMPORARY_ERROR',
    'DELIVERY_NOT_STARTED',
}
CODIGOS_PERMANENTES = {
    'SMTP_CONFIGURATION_ERROR',
    'SMTP_AUTHENTICATION_ERROR',
    'SMTP_RECIPIENT_REFUSED',
    'MESSAGE_CONTEXT_INVALID',
}


class EntregaCorreoNoIniciada(Exception):
    codigo = 'DELIVERY_NOT_STARTED'


class EntregaCorreoAmbigua(Exception):
    codigo = 'SMTP_DELIVERY_AMBIGUOUS'


@dataclass(frozen=True)
class ResultadoProcesoOutbox:
    procesado: bool
    outbox_id: object = None
    estado: str = ''
    codigo: str = ''


def _hash_evento(clave):
    return hashlib.sha256(clave.encode('utf-8')).hexdigest()


def _message_id(clave):
    dominio = str(getattr(settings, 'PRIMARY_DOMAIN_HOST', 'aprobado.com.co'))
    dominio = ''.join(c for c in dominio.lower() if c.isalnum() or c in '.-')
    if not dominio:
        dominio = 'aprobado.com.co'
    return f'<edu-{_hash_evento(clave)}@{dominio}>'


def _normalizar_lista(correos):
    resultado = []
    for correo in correos or []:
        normalizado = normalizar_destinatario(correo)
        if normalizado not in resultado:
            resultado.append(normalizado)
    if not resultado:
        raise ValidationError('El correo educativo requiere destinatario.')
    return resultado


def crear_intencion_correo(
    *,
    solicitud,
    tipo_evento,
    clave_idempotencia,
    codigo_mensaje,
    destinatarios,
    destinatarios_copia=(),
    contexto=None,
    entrega_invitacion=None,
    enlace_captura=None,
    decision=None,
):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('La intencion de correo debe crearse atomicamente.')
    destinatarios = _normalizar_lista(destinatarios)
    copias = (
        [
            correo
            for correo in _normalizar_lista(destinatarios_copia)
            if correo not in destinatarios
        ]
        if destinatarios_copia
        else []
    )
    clave = str(clave_idempotencia).strip()
    if not clave or len(clave) > 180:
        raise ValidationError('La clave idempotente del correo no es valida.')
    defaults = {
        'solicitud': solicitud,
        'tipo_evento': tipo_evento,
        'evento_logico': _hash_evento(clave),
        'destinatarios': destinatarios,
        'destinatarios_copia': copias,
        'codigo_mensaje': codigo_mensaje,
        'contexto': contexto or {},
        'maximo_intentos': settings.FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_MAX_ATTEMPTS,
        'message_id': _message_id(clave),
        'entrega_invitacion': entrega_invitacion,
        'enlace_captura': enlace_captura,
        'decision': decision,
    }
    try:
        outbox, creado = OutboxCorreoEducativo.objects.get_or_create(
            clave_idempotencia=clave,
            defaults=defaults,
        )
    except IntegrityError:
        outbox = OutboxCorreoEducativo.objects.get(clave_idempotencia=clave)
        creado = False
    if outbox.solicitud_id != solicitud.pk or outbox.codigo_mensaje != codigo_mensaje:
        raise ValidationError('La clave idempotente pertenece a otro correo.')
    return outbox, creado


def crear_correo_expediente_recibido(*, solicitud):
    return crear_intencion_correo(
        solicitud=solicitud,
        tipo_evento=TipoEventoCorreoEducativo.DOSSIER_RECEIVED,
        clave_idempotencia=f'dossier-received:{solicitud.pk}',
        codigo_mensaje=CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED,
        destinatarios=[solicitud.correo],
        destinatarios_copia=(
            settings.FINANCIACION_EDUCATIVA_REVIEW_NOTIFICATION_EMAILS
        ),
        contexto={},
    )


def crear_correo_decision(*, solicitud, decision, entrega_legacy=None):
    return crear_intencion_correo(
        solicitud=solicitud,
        tipo_evento=TipoEventoCorreoEducativo.REVIEW_DECISION,
        clave_idempotencia=f'review-decision:{decision.pk}',
        codigo_mensaje=CodigoMensajeCorreoEducativo.REVIEW_DECISION,
        destinatarios=[solicitud.correo],
        decision=decision,
        contexto={},
    )


def crear_correo_correccion_automatica(*, solicitud, proceso_id, requisitos):
    return crear_intencion_correo(
        solicitud=solicitud,
        tipo_evento=TipoEventoCorreoEducativo.AUTOMATIC_CORRECTION,
        clave_idempotencia=f'automatic-correction:{proceso_id}',
        codigo_mensaje=CodigoMensajeCorreoEducativo.AUTOMATIC_CORRECTION,
        destinatarios=[solicitud.correo],
        contexto={'requirements': sorted(set(requisitos))},
    )


def crear_correo_continuacion_automatica(*, solicitud, fotografia_id):
    return crear_intencion_correo(
        solicitud=solicitud,
        tipo_evento=TipoEventoCorreoEducativo.AUTOMATIC_CONTINUATION,
        clave_idempotencia=f'automatic-continuation:{fotografia_id}',
        codigo_mensaje=CodigoMensajeCorreoEducativo.AUTOMATIC_CONTINUATION,
        destinatarios=[solicitud.correo],
        contexto={},
    )


def crear_correo_invitacion(*, entrega):
    tipo = (
        TipoEventoCorreoEducativo.INITIAL_INVITATION
        if entrega.origen == 'INITIAL'
        else TipoEventoCorreoEducativo.INVITATION_REISSUE
    )
    return crear_intencion_correo(
        solicitud=entrega.solicitud,
        tipo_evento=tipo,
        clave_idempotencia=f'invitation-delivery:{entrega.pk}',
        codigo_mensaje=CodigoMensajeCorreoEducativo.INVITATION,
        destinatarios=[entrega.solicitud.correo],
        entrega_invitacion=entrega,
    )


def crear_correo_captura(*, enlace):
    return crear_intencion_correo(
        solicitud=enlace.solicitud,
        tipo_evento=TipoEventoCorreoEducativo.MOBILE_CAPTURE_LINK,
        clave_idempotencia=f'mobile-capture:{enlace.pk}',
        codigo_mensaje=CodigoMensajeCorreoEducativo.MOBILE_CAPTURE,
        destinatarios=[enlace.solicitud.correo],
        enlace_captura=enlace,
    )


def _lease_seconds():
    return settings.FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_LEASE_SECONDS


def _backoff(intentos):
    base = settings.FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_BASE_SECONDS
    maximo = settings.FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_MAX_SECONDS
    return min(base * (2 ** max(intentos - 1, 0)), maximo)


@transaction.atomic
def reclamar_correo_pendiente():
    ahora = timezone.now()
    queryset = OutboxCorreoEducativo.objects.filter(
        estado__in=ESTADOS_RECLAMABLES,
        proxima_ejecucion_en__lte=ahora,
    ).order_by('proxima_ejecucion_en', 'creada_en')
    queryset = (
        queryset.select_for_update(skip_locked=True)
        if connection.features.has_select_for_update_skip_locked
        else queryset.select_for_update()
    )
    outbox = queryset.first()
    if not outbox:
        return None
    if outbox.intentos >= outbox.maximo_intentos:
        outbox.estado = EstadoOutboxCorreoEducativo.FAILED
        outbox.codigo_ultimo_error = 'MAX_ATTEMPTS_EXCEEDED'
        outbox.save(update_fields=['estado', 'codigo_ultimo_error', 'actualizada_en'])
        return None
    outbox.estado = EstadoOutboxCorreoEducativo.SENDING
    outbox.intentos += 1
    outbox.ultimo_intento_en = ahora
    outbox.lease_id = uuid.uuid4()
    outbox.lease_vence_en = ahora + timedelta(seconds=_lease_seconds())
    outbox.codigo_ultimo_error = ''
    outbox.save()
    return outbox


def _invocar_backend(backend, *, message_id, **kwargs):
    firma = inspect.signature(backend.deliver)
    if 'message_id' in firma.parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in firma.parameters.values()
    ):
        kwargs['message_id'] = message_id
    return backend.deliver(**kwargs)


@transaction.atomic
def _preparar_enlace_personal(*, outbox_id, lease_id):
    outbox = OutboxCorreoEducativo.objects.select_for_update().select_related(
        'entrega_invitacion__invitacion',
        'entrega_invitacion__solicitud',
        'enlace_captura__solicitud',
    ).get(pk=outbox_id)
    if outbox.estado != EstadoOutboxCorreoEducativo.SENDING or outbox.lease_id != lease_id:
        raise EntregaCorreoNoIniciada()
    if outbox.codigo_mensaje == CodigoMensajeCorreoEducativo.INVITATION:
        from financiacion_educativa.services.invitaciones import (
            emitir_invitacion_continuacion,
        )
        entrega = EntregaInvitacionContinuacion.objects.select_for_update().get(
            pk=outbox.entrega_invitacion_id
        )
        if entrega.estado in {
            EstadoEntregaInvitacion.SENT,
            EstadoEntregaInvitacion.CANCELLED,
            EstadoEntregaInvitacion.SUPERSEDED,
        } or EntregaInvitacionContinuacion.objects.filter(
            reemplaza_a=entrega,
        ).exists():
            raise ValidationError(
                'La entrega de invitacion ya no es utilizable.'
            )
        emitida = emitir_invitacion_continuacion(
            solicitud=entrega.solicitud,
            actor=entrega.creada_por,
        )
        entrega.invitacion = emitida.invitacion
        entrega.estado = EstadoEntregaInvitacion.SENDING
        entrega.intentos += 1
        entrega.iniciada_en = timezone.now()
        entrega.codigo_ultimo_error = ''
        entrega.save()
        from financiacion_educativa.services.invitaciones import (
            registrar_evento_invitacion,
        )
        metadata = {'delivery_id': str(entrega.pk), 'channel': entrega.canal}
        registrar_evento_invitacion(
            emitida.invitacion,
            TipoEventoInvitacion.DELIVERY_SCHEDULED,
            actor=entrega.creada_por,
            metadata=metadata,
        )
        registrar_evento_invitacion(
            emitida.invitacion,
            TipoEventoInvitacion.DELIVERY_STARTED,
            actor=entrega.creada_por,
            metadata={'delivery_id': str(entrega.pk)},
        )
        return emitida.url, emitida.invitacion.vence_en
    if outbox.codigo_mensaje == CodigoMensajeCorreoEducativo.MOBILE_CAPTURE:
        from financiacion_educativa.services.captura_movil import (
            _construir_url,
            _duracion,
            _registrar_evento,
            calcular_hash_token,
        )
        enlace = EnlaceCapturaMovil.objects.select_for_update().get(
            pk=outbox.enlace_captura_id
        )
        if enlace.estado == EstadoEnlaceCapturaMovil.CONSUMED or (
            EnlaceCapturaMovil.objects.filter(
                solicitud_id=enlace.solicitud_id,
                persona=enlace.persona,
                creada_en__gt=enlace.creada_en,
            ).exists()
        ):
            raise ValidationError(
                'El enlace de captura ya no es utilizable.'
            )
        token = secrets.token_urlsafe(48)
        enlace.token_hash = calcular_hash_token(token)
        enlace.vence_en = timezone.now() + _duracion()
        enlace.estado = EstadoEnlaceCapturaMovil.ACTIVE
        enlace.estado_entrega = EstadoEntregaCapturaMovil.SENDING
        enlace.intentos_entrega += 1
        enlace.entrega_iniciada_en = timezone.now()
        enlace.codigo_ultimo_error = ''
        enlace.save()
        _registrar_evento(enlace, TipoEventoEnlaceCapturaMovil.DELIVERY_STARTED)
        return _construir_url(token), enlace.vence_en
    return None, None


def _construir_mensaje(outbox, connection_mail):
    if outbox.codigo_mensaje == CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED:
        mensaje = construir_correo_expediente_recibido(
            recipient=outbox.destinatarios[0],
            referencia_externa=outbox.solicitud.referencia_externa,
            cc=outbox.destinatarios_copia,
            connection=connection_mail,
        )
    elif outbox.codigo_mensaje == CodigoMensajeCorreoEducativo.REVIEW_DECISION:
        mensaje = construir_correo_decision_educativa(
            recipient=outbox.destinatarios[0],
            decision=outbox.decision,
            connection=connection_mail,
        )
    elif outbox.codigo_mensaje == CodigoMensajeCorreoEducativo.AUTOMATIC_CORRECTION:
        mensaje = construir_correo_correccion_automatica(
            recipient=outbox.destinatarios[0],
            requisitos=outbox.contexto.get('requirements', []),
            connection=connection_mail,
        )
    elif outbox.codigo_mensaje == CodigoMensajeCorreoEducativo.AUTOMATIC_CONTINUATION:
        mensaje = construir_correo_continuacion_automatica(
            recipient=outbox.destinatarios[0],
            connection=connection_mail,
        )
    else:
        raise ValidationError('El mensaje requiere un enlace personal.')
    mensaje.extra_headers['Message-ID'] = outbox.message_id
    return mensaje


def _entregar(outbox, lease_id):
    if outbox.codigo_mensaje in {
        CodigoMensajeCorreoEducativo.INVITATION,
        CodigoMensajeCorreoEducativo.MOBILE_CAPTURE,
    }:
        url, vence_en = _preparar_enlace_personal(
            outbox_id=outbox.pk,
            lease_id=lease_id,
        )
        if outbox.codigo_mensaje == CodigoMensajeCorreoEducativo.INVITATION:
            from financiacion_educativa.services.entrega_invitaciones import _delivery_backend
        else:
            from financiacion_educativa.services.captura_movil import _delivery_backend
        return _invocar_backend(
            _delivery_backend(),
            recipient=outbox.destinatarios[0],
            continuation_url=url,
            expires_at=vence_en,
            message_id=outbox.message_id,
        )
    if settings.EMAIL_BACKEND in SMTP_BACKENDS:
        validar_configuracion_smtp()
    elif not settings.DEBUG:
        raise ConfiguracionSMTPInvalida('El outbox educativo requiere SMTP.')
    mail_connection = get_connection(timeout=max(1, int(settings.EMAIL_TIMEOUT)))
    mensaje = _construir_mensaje(outbox, mail_connection)
    if mensaje.send(fail_silently=False) != 1:
        raise EntregaCorreoAmbigua()


def _clasificar_error(error):
    codigo = getattr(error, 'codigo', '')
    if codigo:
        return codigo, (
            EstadoOutboxCorreoEducativo.RETRYING
            if codigo in CODIGOS_TEMPORALES
            else EstadoOutboxCorreoEducativo.AMBIGUOUS
        )
    if isinstance(error, ConfiguracionSMTPInvalida):
        return 'SMTP_CONFIGURATION_ERROR', EstadoOutboxCorreoEducativo.FAILED
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return 'SMTP_AUTHENTICATION_ERROR', EstadoOutboxCorreoEducativo.FAILED
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        codigos = [
            int(detalle[0])
            for detalle in error.recipients.values()
            if isinstance(detalle, (tuple, list)) and detalle
        ]
        if codigos and all(400 <= codigo < 500 for codigo in codigos):
            return 'SMTP_TEMPORARY_ERROR', EstadoOutboxCorreoEducativo.RETRYING
        if codigos and all(500 <= codigo < 600 for codigo in codigos):
            return 'SMTP_RECIPIENT_REFUSED', EstadoOutboxCorreoEducativo.FAILED
        return 'SMTP_DELIVERY_AMBIGUOUS', EstadoOutboxCorreoEducativo.AMBIGUOUS
    if isinstance(error, smtplib.SMTPResponseException):
        if 400 <= int(error.smtp_code) < 500:
            return 'SMTP_TEMPORARY_ERROR', EstadoOutboxCorreoEducativo.RETRYING
        if 500 <= int(error.smtp_code) < 600:
            return 'SMTP_PERMANENT_ERROR', EstadoOutboxCorreoEducativo.FAILED
        return 'SMTP_DELIVERY_AMBIGUOUS', EstadoOutboxCorreoEducativo.AMBIGUOUS
    if isinstance(error, smtplib.SMTPConnectError):
        return 'SMTP_CONNECT_ERROR', EstadoOutboxCorreoEducativo.RETRYING
    if isinstance(error, (socket.timeout, TimeoutError, smtplib.SMTPServerDisconnected)):
        return 'SMTP_DELIVERY_AMBIGUOUS', EstadoOutboxCorreoEducativo.AMBIGUOUS
    if isinstance(error, (ValidationError, ImproperlyConfigured)):
        return 'MESSAGE_CONTEXT_INVALID', EstadoOutboxCorreoEducativo.FAILED
    if isinstance(error, (ConnectionRefusedError, ConnectionError)):
        return 'SMTP_CONNECT_ERROR', EstadoOutboxCorreoEducativo.RETRYING
    return 'SMTP_DELIVERY_AMBIGUOUS', EstadoOutboxCorreoEducativo.AMBIGUOUS


@transaction.atomic
def _finalizar(*, outbox_id, lease_id, estado, codigo=''):
    outbox = OutboxCorreoEducativo.objects.select_for_update().get(pk=outbox_id)
    if outbox.estado != EstadoOutboxCorreoEducativo.SENDING or outbox.lease_id != lease_id:
        return outbox
    ahora = timezone.now()
    if estado == EstadoOutboxCorreoEducativo.RETRYING and outbox.intentos >= outbox.maximo_intentos:
        estado = EstadoOutboxCorreoEducativo.FAILED
        codigo = 'MAX_ATTEMPTS_EXCEEDED'
    outbox.estado = estado
    outbox.codigo_ultimo_error = str(codigo or '')[:60]
    outbox.lease_id = None
    outbox.lease_vence_en = None
    if estado == EstadoOutboxCorreoEducativo.SENT:
        outbox.enviada_en = ahora
    elif estado == EstadoOutboxCorreoEducativo.RETRYING:
        outbox.proxima_ejecucion_en = ahora + timedelta(seconds=_backoff(outbox.intentos))
    outbox.save()
    _sincronizar_legado(outbox)
    return outbox


def _sincronizar_legado(outbox):
    if outbox.entrega_invitacion_id:
        entrega = outbox.entrega_invitacion
        if entrega.estado in {
            EstadoEntregaInvitacion.CANCELLED,
            EstadoEntregaInvitacion.SUPERSEDED,
        }:
            return
        if outbox.estado == EstadoOutboxCorreoEducativo.SENT:
            entrega.estado = EstadoEntregaInvitacion.SENT
            entrega.enviada_en = outbox.enviada_en
            tipo = TipoEventoInvitacion.DELIVERY_SENT
        elif outbox.estado in {EstadoOutboxCorreoEducativo.FAILED, EstadoOutboxCorreoEducativo.AMBIGUOUS}:
            entrega.estado = EstadoEntregaInvitacion.FAILED
            entrega.fallida_en = timezone.now()
            entrega.invitacion.estado = EstadoInvitacionContinuacion.REVOKED
            entrega.invitacion.save(update_fields=['estado', 'actualizada_en'])
            tipo = TipoEventoInvitacion.DELIVERY_FAILED
        else:
            return
        entrega.codigo_ultimo_error = outbox.codigo_ultimo_error
        entrega.save()
        from financiacion_educativa.services.invitaciones import registrar_evento_invitacion
        registrar_evento_invitacion(
            entrega.invitacion,
            tipo,
            metadata={'delivery_id': str(entrega.pk), 'error_code': outbox.codigo_ultimo_error},
        )
    if outbox.enlace_captura_id:
        enlace = outbox.enlace_captura
        if outbox.estado == EstadoOutboxCorreoEducativo.SENT:
            enlace.estado_entrega = EstadoEntregaCapturaMovil.SENT
            enlace.enviada_en = outbox.enviada_en
            tipo = TipoEventoEnlaceCapturaMovil.DELIVERY_SENT
        elif outbox.estado in {EstadoOutboxCorreoEducativo.FAILED, EstadoOutboxCorreoEducativo.AMBIGUOUS}:
            enlace.estado_entrega = EstadoEntregaCapturaMovil.FAILED
            enlace.estado = EstadoEnlaceCapturaMovil.REVOKED
            enlace.revocada_en = timezone.now()
            enlace.fallida_en = timezone.now()
            tipo = TipoEventoEnlaceCapturaMovil.DELIVERY_FAILED
        else:
            return
        enlace.codigo_ultimo_error = outbox.codigo_ultimo_error
        enlace.save()
        from financiacion_educativa.services.captura_movil import _registrar_evento
        _registrar_evento(enlace, tipo, metadata={'error_code': outbox.codigo_ultimo_error})
    if outbox.decision_id:
        EntregaCorreoEstadoSolicitud.objects.filter(decision_id=outbox.decision_id).update(
            estado=(
                'SENT' if outbox.estado == EstadoOutboxCorreoEducativo.SENT else 'FAILED'
            ),
            intentos=outbox.intentos,
            enviada_en=outbox.enviada_en,
            fallida_en=(
                timezone.now()
                if outbox.estado in {EstadoOutboxCorreoEducativo.FAILED, EstadoOutboxCorreoEducativo.AMBIGUOUS}
                else None
            ),
            codigo_ultimo_error=outbox.codigo_ultimo_error,
        )


def procesar_siguiente_correo():
    outbox = reclamar_correo_pendiente()
    if not outbox:
        return ResultadoProcesoOutbox(procesado=False)
    lease_id = outbox.lease_id
    try:
        _entregar(outbox, lease_id)
        estado = EstadoOutboxCorreoEducativo.SENT
        codigo = ''
    except Exception as error:
        codigo, estado = _clasificar_error(error)
        logger.warning(
            'Fallo controlado del outbox educativo: outbox_id=%s tipo=%s codigo=%s',
            outbox.pk,
            outbox.tipo_evento,
            codigo,
        )
    actualizado = _finalizar(
        outbox_id=outbox.pk,
        lease_id=lease_id,
        estado=estado,
        codigo=codigo,
    )
    return ResultadoProcesoOutbox(
        procesado=True,
        outbox_id=actualizado.pk,
        estado=actualizado.estado,
        codigo=actualizado.codigo_ultimo_error,
    )


@transaction.atomic
def recuperar_leases_outbox(*, dry_run=False, solicitud_id=None, outbox_id=None):
    queryset = OutboxCorreoEducativo.objects.select_for_update().filter(
        estado=EstadoOutboxCorreoEducativo.SENDING,
        lease_vence_en__lte=timezone.now(),
    )
    if solicitud_id:
        queryset = queryset.filter(solicitud_id=solicitud_id)
    if outbox_id:
        queryset = queryset.filter(pk=outbox_id)
    registros = list(queryset)
    if dry_run:
        transaction.set_rollback(True)
        return len(registros)
    for outbox in registros:
        lease_id = outbox.lease_id
        _finalizar(
            outbox_id=outbox.pk,
            lease_id=lease_id,
            estado=EstadoOutboxCorreoEducativo.AMBIGUOUS,
            codigo='LEASE_EXPIRED_DELIVERY_AMBIGUOUS',
        )
    return len(registros)


@transaction.atomic
def reintentar_fallidos(*, dry_run=False, solicitud_id=None, outbox_id=None):
    queryset = OutboxCorreoEducativo.objects.select_for_update().filter(
        estado=EstadoOutboxCorreoEducativo.FAILED,
    )
    if solicitud_id:
        queryset = queryset.filter(solicitud_id=solicitud_id)
    if outbox_id:
        queryset = queryset.filter(pk=outbox_id)
    registros = list(queryset)
    if dry_run:
        transaction.set_rollback(True)
        return len(registros)
    for outbox in registros:
        outbox.estado = EstadoOutboxCorreoEducativo.RETRYING
        outbox.intentos = 0
        outbox.proxima_ejecucion_en = timezone.now()
        outbox.codigo_ultimo_error = ''
        outbox.save()
    return len(registros)


@transaction.atomic
def resolver_ambiguos(*, resolucion, dry_run=False, solicitud_id=None, outbox_id=None):
    if resolucion not in {'SENT', 'FAILED', 'RETRYING'}:
        raise ValidationError('La resolucion ambigua no es valida.')
    queryset = OutboxCorreoEducativo.objects.select_for_update().filter(
        estado=EstadoOutboxCorreoEducativo.AMBIGUOUS,
    )
    if solicitud_id:
        queryset = queryset.filter(solicitud_id=solicitud_id)
    if outbox_id:
        queryset = queryset.filter(pk=outbox_id)
    registros = list(queryset)
    if dry_run:
        transaction.set_rollback(True)
        return len(registros)
    for outbox in registros:
        outbox.estado = resolucion
        outbox.codigo_ultimo_error = f'AMBIGUOUS_RESOLVED_{resolucion}'
        outbox.lease_id = None
        outbox.lease_vence_en = None
        if resolucion == 'SENT':
            outbox.enviada_en = timezone.now()
        elif resolucion == 'RETRYING':
            outbox.intentos = 0
            outbox.proxima_ejecucion_en = timezone.now()
        outbox.save()
        _sincronizar_legado(outbox)
    return len(registros)


def conteos_outbox(*, solicitud_id=None, outbox_id=None):
    queryset = OutboxCorreoEducativo.objects.all()
    if solicitud_id:
        queryset = queryset.filter(solicitud_id=solicitud_id)
    if outbox_id:
        queryset = queryset.filter(pk=outbox_id)
    return dict(queryset.values_list('estado').annotate(total=Count('id')))
