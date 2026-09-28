from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from financiacion_educativa.choices import MotivoDecisionRevisionEducativa
from financiacion_educativa.models import ResultadoPublicoSandboxSolicitud, SolicitudFinanciacionEducativa
from financiacion_educativa.services.simulacion import simular_financiacion_educativa


def validar_ambiente_sandbox():
    if settings.DEPLOYMENT_ENVIRONMENT not in {'staging', 'test'}:
        raise PermissionDenied('SANDBOX_ENVIRONMENT_FORBIDDEN: solo staging o test.')


@transaction.atomic
def simular_resultado_sandbox(*, application_id, actor, status=None, decision_reason='', clear=False):
    validar_ambiente_sandbox()
    if not actor.is_active or not actor.is_staff or not (
        actor.is_superuser or actor.has_perm('financiacion_educativa.change_resultadopublicosandboxsolicitud')
    ):
        raise PermissionDenied('Se requiere un administrador autorizado.')
    if clear:
        if status or decision_reason:
            raise ValidationError('--clear no admite estado ni motivo.')
    elif status not in {'APPROVED', 'REJECTED'} or (
        status == 'APPROVED' and decision_reason
    ) or (status == 'REJECTED' and decision_reason not in {
        item.value for item in MotivoDecisionRevisionEducativa
        if item != MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED
    }):
        raise ValidationError('Estado o motivo publico incoherente.')
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(pk=application_id)
    fila = ResultadoPublicoSandboxSolicitud.objects.filter(solicitud=solicitud).first()
    from financiacion_educativa.services.estado_publico import obtener_resultado_publico
    anterior = obtener_resultado_publico(solicitud).estado
    if clear:
        if fila and fila.activo:
            fila.activo = False
            fila.actualizado_por = actor
            fila.save(update_fields=['activo', 'actualizado_por', 'actualizada_en'])
    elif not (fila and fila.activo and fila.estado_publico == status and fila.decision_reason == decision_reason):
        if fila is None:
            fila = ResultadoPublicoSandboxSolicitud(solicitud=solicitud, creado_por=actor)
        fila.estado_publico = status
        fila.course_authorized = status == 'APPROVED'
        fila.decision_reason = decision_reason
        fila.activo = True
        fila.actualizado_por = actor
        fila.condiciones_financieras = {}
        fila.configuracion_financiera = {}
        if fila.course_authorized:
            simulacion = simular_financiacion_educativa(
                monto_solicitado=solicitud.valor_plan, plazo_meses=solicitud.plazo_meses,
            )
            r = simulacion.resultado
            fila.condiciones_financieras = {
                'currency': 'COP', 'requested_amount': format(r.monto_solicitado, '.2f'),
                'financed_amount': format(r.capital_total_financiado, '.2f'),
                'term_months': r.plazo_meses, 'estimated_installment': format(r.cuota_informativa, '.2f'),
            }
            fila.configuracion_financiera = {
                'id': str(simulacion.configuracion.pk), 'codigo': simulacion.configuracion.codigo,
                'version': simulacion.configuracion.version,
            }
        fila.save()
    return anterior, obtener_resultado_publico(solicitud)
