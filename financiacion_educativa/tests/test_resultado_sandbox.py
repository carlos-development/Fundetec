from copy import deepcopy
from io import StringIO
import threading
from unittest import skipUnless

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command, CommandError
from django.db import connection, close_old_connections, IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings, RequestFactory
from django.urls import reverse
from rest_framework.test import APIClient

from financiacion_educativa import models
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.services.resultado_sandbox import simular_resultado_sandbox
from financiacion_educativa.tests.factories import crear_solicitud, crear_configuracion_financiera, crear_institucion
from financiacion_educativa.tests.test_api_institucional import PAYLOAD_VALIDO
from instituciones.services.credenciales import crear_credencial_api


@override_settings(DEPLOYMENT_ENVIRONMENT='test', EDUCATIONAL_OPERATIONS_NOTIFICATION_EMAILS=[])
class ResultadoSandboxTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser('sandbox-admin', 'admin@example.invalid', 'synthetic-only')
        self.solicitud = crear_solicitud()
        crear_configuracion_financiera()

    def simular(self, **kwargs):
        return simular_resultado_sandbox(application_id=self.solicitud.pk, actor=self.actor, **kwargs)

    def test_approved_snapshot_sin_efectos_contractuales(self):
        antes = models.SolicitudFinanciacionEducativa.objects.values().get(pk=self.solicitud.pk)
        efectos = [models.ArtefactoContractualEducativo, models.ProcesoFirmaEducativa,
                   models.DecisionRevisionEducativa, models.CondicionesFinancieras,
                   models.OutboxCorreoEducativo, models.HistorialEstadoSolicitud]
        cantidades = [m.objects.count() for m in efectos]
        anterior, r = self.simular(status='APPROVED')
        self.assertEqual(anterior, 'RECEIVED')
        self.assertTrue(r.curso_autorizado)
        self.assertEqual(r.estado, 'APPROVED')
        self.assertEqual(r.condiciones_financieras['currency'], 'COP')
        self.assertEqual(r.condiciones_financieras['requested_amount'], '1000000.00')
        self.assertIsNotNone(r.autorizacion_efectiva_en)
        self.assertEqual(cantidades, [m.objects.count() for m in efectos])
        self.assertEqual(antes, models.SolicitudFinanciacionEducativa.objects.values().get(pk=self.solicitud.pk))
        self.assertEqual(obtener_resultado_publico(self.solicitud, aplicar_sandbox=False).estado, 'RECEIVED')

    def test_rejected_y_solicitud_sin_simulacion(self):
        _, r = self.simular(status='REJECTED', decision_reason='OTHER')
        self.assertEqual((r.estado, r.curso_autorizado, r.motivo_decision), ('REJECTED', False, 'OTHER'))
        self.assertIsNone(r.condiciones_financieras)
        self.assertIsNone(r.autorizacion_efectiva_en)
        otra = crear_solicitud(institucion=self.solicitud.institucion, referencia='OTRA')
        self.assertEqual(obtener_resultado_publico(otra).estado, 'RECEIVED')

    def test_idempotencia_conserva_snapshot_y_fecha(self):
        self.simular(status='APPROVED')
        antes = models.ResultadoPublicoSandboxSolicitud.objects.values().get()
        self.simular(status='APPROVED')
        self.assertEqual(antes, models.ResultadoPublicoSandboxSolicitud.objects.values().get())

    def test_clear_conserva_auditoria_y_actualizacion_sin_tocar_solicitud(self):
        self.simular(status='APPROVED')
        _, resultado = self.simular(clear=True)
        fila = models.ResultadoPublicoSandboxSolicitud.objects.get()
        self.assertFalse(fila.activo)
        self.assertEqual(resultado.estado, 'RECEIVED')
        self.assertEqual(resultado.actualizada_en, fila.actualizada_en)
        self.assertEqual(fila.creado_por_id, self.actor.pk)
        self.simular(clear=True)
        self.assertEqual(fila.actualizada_en, models.ResultadoPublicoSandboxSolicitud.objects.get().actualizada_en)

    def test_production_falla_con_y_sin_fila_incluso_inactiva(self):
        with override_settings(DEPLOYMENT_ENVIRONMENT='production'):
            with self.assertRaises(PermissionDenied):
                self.simular(status='APPROVED')
        self.assertFalse(models.ResultadoPublicoSandboxSolicitud.objects.exists())
        self.simular(status='APPROVED')
        for activo in (True, False):
            models.ResultadoPublicoSandboxSolicitud.objects.update(activo=activo)
            with override_settings(DEPLOYMENT_ENVIRONMENT='production'):
                with self.assertRaises(PermissionDenied):
                    obtener_resultado_publico(self.solicitud)
                with self.assertRaises(PermissionDenied):
                    self.simular(clear=True)

    def test_local_y_ambiente_desconocido_no_permitidos(self):
        for ambiente in ('local', '', 'sandbox'):
            with override_settings(DEPLOYMENT_ENVIRONMENT=ambiente), self.assertRaises(PermissionDenied):
                self.simular(status='APPROVED')

    @override_settings(DEPLOYMENT_ENVIRONMENT='staging')
    def test_staging_admite_simulacion(self):
        self.assertEqual(self.simular(status='APPROVED')[1].estado, 'APPROVED')

    def test_estado_motivos_y_clear_incoherentes_fallan(self):
        for datos in ({'status': 'RECEIVED'}, {'status': 'REJECTED'},
                      {'status': 'APPROVED', 'decision_reason': 'OTHER'},
                      {'status': 'REJECTED', 'decision_reason': 'REQUIREMENTS_VERIFIED'},
                      {'clear': True, 'status': 'APPROVED'}):
            with self.subTest(datos=datos), self.assertRaises(ValidationError):
                self.simular(**datos)
        self.assertFalse(models.ResultadoPublicoSandboxSolicitud.objects.exists())

    def test_constraints_sql_impiden_coherencia_invalida(self):
        self.simular(status='APPROVED')
        for datos in ({'course_authorized': False}, {'estado_publico': 'RECEIVED'},
                      {'decision_reason': 'OTHER'}, {'condiciones_financieras': {}}):
            with self.subTest(datos=datos), self.assertRaises(IntegrityError), transaction.atomic():
                models.ResultadoPublicoSandboxSolicitud.objects.update(**datos)
        self.simular(status='REJECTED', decision_reason='OTHER')
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.ResultadoPublicoSandboxSolicitud.objects.update(decision_reason='INVENTADO')

    def test_comando_exige_confirmacion_y_administrador(self):
        opciones = dict(application_id=self.solicitud.pk, actor_id=self.actor.pk, status='APPROVED')
        with self.assertRaises(CommandError):
            call_command('simular_resultado_api_sandbox', **opciones)
        self.actor.is_staff = False
        self.actor.save(update_fields=['is_staff'])
        with self.assertRaises(CommandError):
            call_command('simular_resultado_api_sandbox', confirm=True, **opciones)
        self.assertFalse(models.ResultadoPublicoSandboxSolicitud.objects.exists())

    def test_comando_muestra_solo_resultados_y_clear(self):
        salida = StringIO()
        opciones = dict(application_id=self.solicitud.pk, actor_id=self.actor.pk, confirm=True, stdout=salida)
        call_command('simular_resultado_api_sandbox', status='REJECTED', decision_reason='OTHER', **opciones)
        self.assertIn('anterior=RECEIVED final=REJECTED', salida.getvalue())
        self.assertNotIn(self.solicitud.correo, salida.getvalue())
        call_command('simular_resultado_api_sandbox', clear=True, **opciones)
        self.assertIn('final=RECEIVED', salida.getvalue())

    def test_admin_solo_lectura(self):
        instancia = admin.site._registry[models.ResultadoPublicoSandboxSolicitud]
        request = RequestFactory().get('/')
        request.user = self.actor
        self.assertFalse(instancia.has_add_permission(request))
        self.assertFalse(instancia.has_change_permission(request))
        self.assertFalse(instancia.has_delete_permission(request))
        self.assertTrue(instancia.has_view_permission(request))

    def test_staff_sin_permiso_no_puede_simular(self):
        self.actor.is_superuser = False
        self.actor.save(update_fields=['is_superuser'])
        with self.assertRaises(PermissionDenied):
            self.simular(status='APPROVED')

    def test_clear_sin_fila_no_crea_efectos(self):
        self.simular(clear=True)
        self.assertFalse(models.ResultadoPublicoSandboxSolicitud.objects.exists())

    def test_limpieza_qa_reconoce_y_preserva_auditoria_sandbox(self):
        from financiacion_educativa.services.limpieza_solicitudes import (
            construir_plan_limpieza, ejecutar_limpieza_solicitudes, ErrorLimpiezaSolicitudes,
        )
        self.simular(status='APPROVED')
        self.simular(clear=True)
        plan = construir_plan_limpieza(self.solicitud.institucion)
        self.assertEqual(dict(plan.conteos)['ResultadoPublicoSandboxSolicitud'], 1)
        self.assertTrue(any('ResultadoPublicoSandboxSolicitud' in r for r in plan.relaciones_protegidas))
        with self.assertRaisesMessage(ErrorLimpiezaSolicitudes, 'relacion protegida'):
            ejecutar_limpieza_solicitudes(institucion_id=self.solicitud.institucion_id, expected_count=1)
        self.assertTrue(models.SolicitudFinanciacionEducativa.objects.filter(pk=self.solicitud.pk).exists())
        self.assertEqual(models.ResultadoPublicoSandboxSolicitud.objects.count(), 1)

    def test_motor_sin_configuracion_no_crea_simulacion(self):
        models.ConfiguracionFinancieraEducativa.objects.all().delete()
        with self.assertRaises(ValidationError):
            self.simular(status='APPROVED')
        self.assertFalse(models.ResultadoPublicoSandboxSolicitud.objects.exists())

    def test_snapshot_invalido_no_puede_guardarse(self):
        self.simular(status='APPROVED')
        fila = models.ResultadoPublicoSandboxSolicitud.objects.get()
        for datos in ({'currency': 'COP'}, {'bad': 'value'}, []):
            fila.condiciones_financieras = datos
            with self.assertRaises(ValidationError):
                fila.save()

    def test_get_aislado_y_post_replay_sin_simulacion(self):
        cliente = APIClient()
        token = crear_credencial_api(institucion=self.solicitud.institucion, nombre='Sandbox').token
        cliente.credentials(HTTP_AUTHORIZATION=f'ApiKey {token}')
        url = reverse('financiacion_educativa_api:solicitud-crear')
        original = cliente.post(url, deepcopy(PAYLOAD_VALIDO), format='json', HTTP_IDEMPOTENCY_KEY='sandbox-replay')
        self.assertEqual(original.status_code, 202)
        pk = original.data['application_id']
        simular_resultado_sandbox(application_id=pk, actor=self.actor, status='APPROVED')
        cantidad = models.SolicitudFinanciacionEducativa.objects.count()
        invitaciones = models.InvitacionContinuacionSolicitud.objects.count()
        outbox = models.OutboxCorreoEducativo.objects.count()
        replay = cliente.post(url, deepcopy(PAYLOAD_VALIDO), format='json', HTTP_IDEMPOTENCY_KEY='sandbox-replay')
        self.assertEqual(replay.data['status'], 'RECEIVED')
        self.assertEqual(models.SolicitudFinanciacionEducativa.objects.count(), cantidad)
        self.assertEqual(models.InvitacionContinuacionSolicitud.objects.count(), invitaciones)
        self.assertEqual(models.OutboxCorreoEducativo.objects.count(), outbox)
        detalle = reverse('financiacion_educativa_api:solicitud-detalle', kwargs={'application_id': pk})
        respuesta = cliente.get(detalle)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['status'], 'APPROVED')
        fila = models.ResultadoPublicoSandboxSolicitud.objects.get(solicitud_id=pk)
        self.assertEqual(respuesta.data['updated_at'], fila.actualizada_en)
        ajena = crear_credencial_api(institucion=crear_institucion('2'), nombre='Ajena').token
        cliente.credentials(HTTP_AUTHORIZATION=f'ApiKey {ajena}')
        self.assertEqual(cliente.get(detalle).status_code, 404)


@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL real.')
@override_settings(DEPLOYMENT_ENVIRONMENT='test')
class ConcurrenciaSandboxTests(TransactionTestCase):
    def test_dos_operadores_generan_un_snapshot(self):
        actor = get_user_model().objects.create_superuser('pg-sandbox', 'pg@example.invalid', 'synthetic-only')
        solicitud = crear_solicitud()
        crear_configuracion_financiera()
        barrera = threading.Barrier(2)
        errores, resultados = [], []

        def ejecutar():
            close_old_connections()
            try:
                administrador = get_user_model().objects.get(pk=actor.pk)
                barrera.wait(timeout=10)
                resultados.append(simular_resultado_sandbox(
                    application_id=solicitud.pk, actor=administrador, status='APPROVED',
                )[1])
            except Exception as error:
                errores.append(error)
            finally:
                connection.close()

        hilos = [threading.Thread(target=ejecutar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=20)
        self.assertFalse(any(hilo.is_alive() for hilo in hilos))
        self.assertEqual(errores, [])
        self.assertEqual(len(resultados), 2)
        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(models.ResultadoPublicoSandboxSolicitud.objects.count(), 1)
        self.assertTrue(connection.is_usable())
