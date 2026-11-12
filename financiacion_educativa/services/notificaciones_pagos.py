"""Transactional BRE-B notices, verified recipients and stale-message suppression."""

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import CodigoMensajeCorreoEducativo as Codigo
from financiacion_educativa.models_recaudos import ReporteRecaudoEducativo
from financiacion_educativa.services.autorizacion import usuario_es_propietario_solicitud
from financiacion_educativa.services.correos import ConfiguracionSMTPInvalida, normalizar_destinatario
from financiacion_educativa.services.participantes import solicitud_requiere_tutor


CODIGOS_PAGO = frozenset({Codigo.BREB_REPORT_RECEIVED, Codigo.BREB_INCOME_CONFIRMED, Codigo.BREB_PAYMENT_APPLIED})


class DestinatarioPagoNoVerificado(Exception):
    codigo = 'PAYMENT_RECIPIENT_UNVERIFIED'


class AvisoPagoSustituido(Exception):
    codigo = 'PAYMENT_NOTICE_SUPERSEDED'


class NotificacionesPagoDeshabilitadas(Exception):
    codigo = 'PAYMENT_NOTIFICATIONS_DISABLED'


class LeasePagoPerdido(Exception):
    codigo = 'DELIVERY_NOT_STARTED'


def destinatario_verificado(solicitud):
    if solicitud_requiere_tutor(solicitud):
        responsables = list(solicitud.participantes.filter(
            responsable_contractual=True, roles__rol='GUARDIAN', roles__solicitud_id=solicitud.pk,
        ).select_related('usuario'))
        if len(responsables) != 1:
            return None
        tutor = responsables[0]
        if not tutor.roles.filter(rol='PRINCIPAL_DEBTOR', solicitud_id=solicitud.pk).exists():
            return None
        usuario = tutor.usuario
        if not usuario and solicitud.usuario and solicitud.usuario.email.casefold() == tutor.correo.casefold():
            usuario = solicitud.usuario
        correo = tutor.correo
    else:
        # Unknown age is not evidence that the addressee is an adult.
        estudiante = solicitud.roles_participantes.filter(rol='STUDENT').select_related('participante').first()
        if not (estudiante.participante.fecha_nacimiento if estudiante else solicitud.fecha_nacimiento_estudiante):
            return None
        usuario, correo = solicitud.usuario, solicitud.correo
        if not usuario_es_propietario_solicitud(usuario, solicitud):
            return None
    if not usuario or not usuario.is_active:
        return None
    try:
        correo = normalizar_destinatario(correo)
    except ConfiguracionSMTPInvalida:
        return None
    if not EmailAddress.objects.filter(user=usuario, email__iexact=correo, verified=True).exists():
        return None
    return correo


def registrar_aviso_pago(*, reporte, evento):
    from financiacion_educativa.services.outbox_correos import crear_intencion_correo

    if evento not in CODIGOS_PAGO:
        raise ValueError('Evento de pago no permitido.')
    correo = destinatario_verificado(reporte.solicitud)
    return crear_intencion_correo(
        solicitud=reporte.solicitud, tipo_evento=evento, codigo_mensaje=evento,
        clave_idempotencia=f'breb:{evento}:{reporte.pk}',
        destinatarios=[correo] if correo else [], reporte_recaudo=reporte,
    )


def validar_aviso_actual(outbox, reporte):
    if not settings.FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED:
        raise NotificacionesPagoDeshabilitadas()
    if reporte.solicitud_id != outbox.solicitud_id:
        raise ValidationError('Referencia de aviso de pago no valida.')
    ingreso = getattr(reporte, 'conciliacion', None)
    aplicado = ingreso is not None and hasattr(ingreso, 'aplicacion')
    codigo = outbox.codigo_mensaje
    if codigo == Codigo.BREB_REPORT_RECEIVED:
        if reporte.origen != 'PAYER' or ingreso or hasattr(reporte, 'rechazo'):
            raise AvisoPagoSustituido()
    elif codigo == Codigo.BREB_INCOME_CONFIRMED:
        if aplicado:
            raise AvisoPagoSustituido()
        if ingreso is None:
            raise ValidationError('El aviso requiere una conciliacion persistida.')
    elif codigo == Codigo.BREB_PAYMENT_APPLIED and not aplicado:
        raise ValidationError('El aviso requiere una aplicacion persistida.')
    correo = destinatario_verificado(reporte.solicitud)
    if not correo or outbox.destinatarios != [correo]:
        raise DestinatarioPagoNoVerificado()


@transaction.atomic
def entregar_aviso_pago(outbox, lease_id, enviar):
    # Application and reconciliation lock this same base row before writing.
    # Keep it through SMTP: a concurrent application cannot make this notice stale.
    reporte = ReporteRecaudoEducativo.objects.select_for_update().get(pk=outbox.reporte_recaudo_id)
    actual = type(outbox).objects.select_for_update().get(pk=outbox.pk)
    if (actual.estado != 'SENDING' or actual.lease_id != lease_id
            or actual.lease_vence_en <= timezone.now()):
        raise LeasePagoPerdido()
    validar_aviso_actual(actual, reporte)
    outbox.destinatarios = actual.destinatarios
    enviar(reporte)
