from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings

from financiacion_educativa.models import (
    ProcesoAutomatizacionEducativa, SolicitudFinanciacionEducativa,
    VersionTerminosFinanciacion,
)
from financiacion_educativa.services.cancelacion_sandbox import cancelar_solicitud_sandbox
from financiacion_educativa.services.terminos import (
    aceptar_terminos_solicitud, publicar_version_terminos,
)
from financiacion_educativa.tests.factories import crear_solicitud


@skipUnless(connection.vendor == 'postgresql', 'Requiere bloqueos PostgreSQL reales.')
@override_settings(DEPLOYMENT_ENVIRONMENT='test', FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True)
class GuardrailsConcurrenciaPostgreSQLTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            'guardrails-admin', 'guardrails@example.test', 'synthetic-test-only',
        )
        self.solicitud = crear_solicitud(usuario=self.actor, referencia='QA-GUARDRAILS')

    def _dos_operaciones(self, operacion):
        barrera = Barrier(2)

        def ejecutar():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                solicitud = SolicitudFinanciacionEducativa.objects.get(pk=self.solicitud.pk)
                barrera.wait(timeout=10)
                return operacion(solicitud, actor)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            tareas = [executor.submit(ejecutar) for _ in range(2)]
            return [tarea.result(timeout=30) for tarea in tareas]

    def test_dos_cancelaciones_conservan_un_solo_historial(self):
        self.solicitud.estado = 'PENDING_MANUAL_REVIEW'
        self.solicitud.save(update_fields=['estado'])
        proceso = ProcesoAutomatizacionEducativa.objects.create(
            solicitud=self.solicitud, version_expediente=1,
            estado='QUEUED', etapa_actual='DECISION',
        )
        resultados = self._dos_operaciones(lambda solicitud, actor: cancelar_solicitud_sandbox(
            application_id=solicitud.pk, actor=actor,
            motivo='Caso sintetico que se cancela en Sandbox.',
        ))
        self.solicitud.refresh_from_db()
        proceso.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'CANCELLED')
        self.assertEqual(proceso.estado, 'MANUAL_EXCEPTION')
        self.assertEqual(sum(r.procesos_automatizacion_cerrados for r in resultados), 1)
        self.assertEqual(self.solicitud.historial_estados.filter(estado_nuevo='CANCELLED').count(), 1)

    def test_dos_reaceptaciones_crean_un_consentimiento_y_una_cola_nueva(self):
        self.solicitud.estado = 'PENDING_TERMS'
        self.solicitud.save(update_fields=['estado'])
        primera = publicar_version_terminos(version=VersionTerminosFinanciacion.objects.create(
            tipo='TERMS', version='guardrails-v1', titulo='Texto sintetico',
            contenido='Primera version sintetica para pruebas.', obligatorio=True,
        ))
        aceptar_terminos_solicitud(
            solicitud=self.solicitud, usuario=self.actor, versiones=[primera],
        )
        self.solicitud.estado = 'PENDING_PROMISSORY_NOTE'
        self.solicitud.save(update_fields=['estado'])
        anterior = ProcesoAutomatizacionEducativa.objects.create(
            solicitud=self.solicitud, version_expediente=1,
            estado='QUEUED', etapa_actual='SIGNATURE_SEND',
        )
        segunda = publicar_version_terminos(version=VersionTerminosFinanciacion.objects.create(
            tipo='TERMS', version='guardrails-v2', titulo='Texto sintetico nuevo',
            contenido='Segunda version sintetica para pruebas.', obligatorio=True,
        ))
        resultados = self._dos_operaciones(lambda solicitud, actor: aceptar_terminos_solicitud(
            solicitud=solicitud, usuario=actor, versiones=[segunda],
        ))
        anterior.refresh_from_db()
        self.assertEqual(sum(not r.repetida for r in resultados), 1)
        self.assertEqual(anterior.estado, 'MANUAL_EXCEPTION')
        self.assertEqual(self.solicitud.consentimientos.filter(version_texto=segunda.version).count(), 1)
        self.assertEqual(self.solicitud.procesos_automatizacion.filter(
            estado='QUEUED', etapa_actual='CONTRACT_GENERATION',
        ).count(), 1)
