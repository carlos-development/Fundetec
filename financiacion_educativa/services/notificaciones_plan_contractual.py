"""One notice at the new authorization transition; never a historical backfill."""
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import CodigoMensajeCorreoEducativo as Codigo
from financiacion_educativa.dashboards.operaciones.cartera import obligaciones_autorizadas
from financiacion_educativa.models import CondicionesFinancieras, SolicitudFinanciacionEducativa
from .autorizacion import usuario_es_propietario_solicitud
from .notificaciones_pagos import destinatario_verificado
from .participantes import solicitud_requiere_tutor
from .plan_contractual_pdf import datos_plan_contractual


CODIGO_PLAN = Codigo.CONTRACTUAL_PLAN_AVAILABLE


class AvisoPlanDeshabilitado(Exception):
    codigo = 'PLAN_NOTIFICATIONS_DISABLED'


class AvisoPlanSustituido(Exception):
    codigo = 'PLAN_NOTICE_SUPERSEDED'


class DestinatarioPlanNoElegible(Exception):
    def __init__(self, codigo):
        self.codigo = codigo


class LeasePlanPerdido(Exception):
    codigo = 'DELIVERY_NOT_STARTED'


def _destinatario(solicitud):
    usuario = solicitud.usuario
    if not usuario or not usuario.is_active or not usuario_es_propietario_solicitud(usuario, solicitud):
        return None, None, 'PLAN_RECIPIENT_NO_ACCESS'
    if solicitud_requiere_tutor(solicitud):
        responsables = list(solicitud.participantes.filter(responsable_contractual=True,
            roles__rol='GUARDIAN', roles__solicitud_id=solicitud.pk))
        if len(responsables) != 1:
            return None, None, 'PLAN_GUARDIAN_UNDEFINED'
        if responsables[0].usuario_id not in (None, usuario.pk):
            return None, None, 'PLAN_RECIPIENT_NO_ACCESS'
    correo = destinatario_verificado(solicitud)
    if not correo or not EmailAddress.objects.filter(user=usuario, email__iexact=correo, verified=True).exists():
        return None, None, 'PLAN_RECIPIENT_UNVERIFIED'
    if usuario.email.strip().casefold() != correo:
        return None, None, 'PLAN_RECIPIENT_NO_ACCESS'
    return usuario, correo, ''


def _plan_disponible(solicitud, fotografia):
    if not obligaciones_autorizadas().filter(pk=fotografia.pk, solicitud_id=solicitud.pk).exists():
        return False
    try:
        datos_plan_contractual(fotografia)
    except ValidationError:
        return False
    return True


def registrar_disponibilidad_plan(*, solicitud_id, fotografia_id):
    from .outbox_correos import crear_intencion_correo
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('El aviso debe registrarse en la transaccion de autorizacion.')
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(pk=solicitud_id)
    fotografia = CondicionesFinancieras.objects.get(pk=fotografia_id, solicitud_id=solicitud.pk)
    usuario, correo, motivo = _destinatario(solicitud)
    if not _plan_disponible(solicitud, fotografia):
        motivo = 'PLAN_VERSION_UNAVAILABLE'
    outbox, creado = crear_intencion_correo(solicitud=solicitud, tipo_evento=CODIGO_PLAN,
        codigo_mensaje=CODIGO_PLAN, clave_idempotencia=f'plan-available:{solicitud.pk}:{fotografia.pk}',
        destinatarios=[correo] if correo else [], plan_contractual=fotografia, plan_destinatario_usuario=usuario)
    if creado and motivo:
        outbox.estado = 'FAILED'
        outbox.codigo_ultimo_error = motivo
        outbox.save(update_fields=['estado', 'codigo_ultimo_error', 'actualizada_en'])
    return outbox, creado


@transaction.atomic
def entregar_aviso_plan(outbox, lease_id, enviar):
    # Version changes use the same application lock; do not lock nullable joins.
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(pk=outbox.solicitud_id)
    fotografia = CondicionesFinancieras.objects.select_for_update().get(pk=outbox.plan_contractual_id)
    if not settings.FINANCIACION_EDUCATIVA_CONTRACTUAL_PLAN_EMAILS_ENABLED:
        raise AvisoPlanDeshabilitado()
    if not _plan_disponible(solicitud, fotografia):
        raise AvisoPlanSustituido()
    # Lock the persisted recipient and verification rows before re-reading eligibility.
    if outbox.plan_destinatario_usuario_id:
        get_user_model().objects.select_for_update().get(pk=outbox.plan_destinatario_usuario_id)
        list(EmailAddress.objects.select_for_update().filter(user_id=outbox.plan_destinatario_usuario_id))
    solicitud = SolicitudFinanciacionEducativa.objects.get(pk=solicitud.pk)
    usuario, correo, motivo = _destinatario(solicitud)
    actual = type(outbox).objects.select_for_update().get(pk=outbox.pk)
    if (actual.estado != 'SENDING' or actual.lease_id != lease_id
            or not actual.lease_vence_en or actual.lease_vence_en <= timezone.now()):
        raise LeasePlanPerdido()
    if motivo:
        raise DestinatarioPlanNoElegible(motivo)
    if actual.plan_destinatario_usuario_id != usuario.pk or actual.destinatarios != [correo]:
        raise DestinatarioPlanNoElegible('PLAN_RECIPIENT_CHANGED')
    if actual.plan_contractual_id != fotografia.pk or fotografia.solicitud_id != solicitud.pk:
        raise AvisoPlanSustituido()
    outbox.destinatarios = actual.destinatarios
    enviar()
