from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings

from financiacion_educativa.choices import (
    EtapaAutomatizacionEducativa,
    EstadoProcesoAutomatizacionEducativa,
    EstadoSolicitudFinanciacion,
)
from financiacion_educativa.models import ProcesoAutomatizacionEducativa
from financiacion_educativa.services.cancelacion_sandbox import (
    cancelar_solicitud_sandbox,
)
from financiacion_educativa.tests.factories import crear_solicitud


@override_settings(DEPLOYMENT_ENVIRONMENT='test')
class CancelacionSandboxTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            'sandbox-cancel-admin',
            'sandbox-cancel@example.invalid',
            'synthetic-only',
        )
        self.solicitud = crear_solicitud(referencia='JOW-REAL-EN-QA')
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        self.solicitud.save(update_fields=['estado'])
        self.proceso = ProcesoAutomatizacionEducativa.objects.create(
            solicitud=self.solicitud,
            version_expediente=1,
            estado=EstadoProcesoAutomatizacionEducativa.QUEUED,
            etapa_actual=EtapaAutomatizacionEducativa.DECISION,
        )

    def test_cancela_sin_borrar_y_cierra_automatizacion(self):
        resultado = cancelar_solicitud_sandbox(
            application_id=self.solicitud.pk,
            actor=self.actor,
            motivo='Solicitud real creada por error con credencial Sandbox.',
        )

        self.solicitud.refresh_from_db()
        self.proceso.refresh_from_db()
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.CANCELLED,
        )
        self.assertEqual(
            self.proceso.estado,
            EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
        )
        self.assertEqual(
            self.proceso.codigo_razon,
            'SANDBOX_APPLICATION_CANCELLED',
        )
        self.assertEqual(resultado.procesos_automatizacion_cerrados, 1)
        self.assertTrue(
            self.solicitud.historial_estados.filter(
                estado_nuevo=EstadoSolicitudFinanciacion.CANCELLED,
                actor=self.actor,
            ).exists()
        )

    def test_idempotente_si_ya_esta_cancelada(self):
        datos = dict(
            application_id=self.solicitud.pk,
            actor=self.actor,
            motivo='Solicitud real creada por error con credencial Sandbox.',
        )
        cancelar_solicitud_sandbox(**datos)
        segundo = cancelar_solicitud_sandbox(**datos)

        self.assertEqual(segundo.procesos_automatizacion_cerrados, 0)
        self.assertEqual(
            self.solicitud.historial_estados.filter(
                estado_nuevo=EstadoSolicitudFinanciacion.CANCELLED
            ).count(),
            1,
        )

    def test_comando_exige_referencia_y_confirmacion(self):
        opciones = {
            'application_id': self.solicitud.pk,
            'actor_id': self.actor.pk,
            'confirm_reference': self.solicitud.referencia_externa,
            'reason': 'Solicitud real creada por error con credencial Sandbox.',
        }
        with self.assertRaises(CommandError):
            call_command('cancelar_solicitud_sandbox', **opciones)
        with self.assertRaises(CommandError):
            call_command(
                'cancelar_solicitud_sandbox',
                confirm=True,
                **{**opciones, 'confirm_reference': 'OTRA'},
            )

        salida = StringIO()
        call_command(
            'cancelar_solicitud_sandbox',
            confirm=True,
            stdout=salida,
            **opciones,
        )
        self.assertIn('SANDBOX_CANCELLED', salida.getvalue())
        self.assertNotIn(self.solicitud.correo, salida.getvalue())

    def test_production_esta_prohibido(self):
        with override_settings(DEPLOYMENT_ENVIRONMENT='production'):
            with self.assertRaises(PermissionDenied):
                cancelar_solicitud_sandbox(
                    application_id=self.solicitud.pk,
                    actor=self.actor,
                    motivo='Solicitud real creada por error con credencial Sandbox.',
                )
