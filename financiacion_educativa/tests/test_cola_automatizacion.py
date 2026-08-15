import threading
import uuid
from datetime import timedelta
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EtapaAutomatizacionEducativa,
    EstadoProcesoAutomatizacionEducativa,
    EstadoSolicitudFinanciacion,
)
from financiacion_educativa.models import ProcesoAutomatizacionEducativa
from financiacion_educativa.services.cola_automatizacion import (
    _finalizar_etapa,
    encolar_proceso_automatizacion,
    procesar_siguiente_trabajo,
    reclamar_siguiente_proceso,
    recuperar_leases_vencidos,
)
from financiacion_educativa.services.orquestacion_automatica import (
    SalidaEtapaPersistente,
    programar_orquestacion_automatica,
)
from financiacion_educativa.services.validacion_documental_ia import (
    ErrorValidacionDocumentalIA,
)
from financiacion_educativa.tests.factories import crear_solicitud


@override_settings(
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True,
    FINANCIACION_EDUCATIVA_WORKER_LEASE_SECONDS=60,
    FINANCIACION_EDUCATIVA_WORKER_MAX_ATTEMPTS=3,
    FINANCIACION_EDUCATIVA_WORKER_BACKOFF_BASE_SECONDS=10,
    FINANCIACION_EDUCATIVA_WORKER_BACKOFF_MAX_SECONDS=30,
)
class ColaAutomatizacionEducativaTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username='cola@example.com',
            email='cola@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud(
            usuario=self.usuario,
            referencia='COLA-001',
        )
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        self.solicitud.save(update_fields=['estado'])

    def test_encolado_duplicado_reutiliza_un_proceso_activo(self):
        primero, creado = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )
        segundo, creado_segundo = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )

        self.assertTrue(creado)
        self.assertFalse(creado_segundo)
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(ProcesoAutomatizacionEducativa.objects.count(), 1)

    def test_correccion_crea_nueva_version_sin_reutilizar_etapas_anteriores(self):
        primero, _ = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )
        primero.estado = EstadoProcesoAutomatizacionEducativa.CORRECTION_REQUIRED
        primero.codigo_razon = 'DOCUMENT_CORRECTION_REQUIRED'
        primero.finalizada_en = timezone.now()
        primero.save()
        self.solicitud.estado = EstadoSolicitudFinanciacion.CORRECTION_REQUIRED
        self.solicitud.save(update_fields=['estado'])
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        self.solicitud.save(update_fields=['estado'])

        segundo, creado = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )

        self.assertTrue(creado)
        self.assertEqual(primero.version_expediente, 1)
        self.assertEqual(segundo.version_expediente, 2)
        self.assertNotEqual(primero.pk, segundo.pk)
        self.assertEqual(segundo.etapas.count(), 0)

    def test_solicitud_terminal_no_puede_encolar_nuevo_proceso(self):
        self.solicitud.estado = EstadoSolicitudFinanciacion.APPROVED
        self.solicitud.save(update_fields=['estado'])

        proceso, creado = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )

        self.assertIsNone(proceso)
        self.assertFalse(creado)

    @patch(
        'financiacion_educativa.services.orquestacion_automatica.'
        'ejecutar_etapa_persistente'
    )
    def test_programacion_desde_request_solo_persiste_trabajo(self, ejecutar):
        programado = programar_orquestacion_automatica(
            solicitud_id=self.solicitud.pk
        )

        self.assertTrue(programado)
        ejecutar.assert_not_called()
        self.assertTrue(
            ProcesoAutomatizacionEducativa.objects.filter(
                solicitud=self.solicitud,
                estado=EstadoProcesoAutomatizacionEducativa.QUEUED,
            ).exists()
        )

    @patch(
        'financiacion_educativa.services.orquestacion_automatica.'
        'ejecutar_etapa_persistente'
    )
    def test_ejecuta_una_etapa_y_persiste_reanudacion(self, ejecutar):
        ejecutar.return_value = SalidaEtapaPersistente(
            estado=EstadoProcesoAutomatizacionEducativa.QUEUED,
            codigo='SECURITY_SCAN_COMPLETED',
            siguiente_etapa=EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION,
        )
        proceso, _ = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )

        resultado = procesar_siguiente_trabajo()

        proceso.refresh_from_db()
        self.assertTrue(resultado.procesado)
        self.assertEqual(proceso.estado, EstadoProcesoAutomatizacionEducativa.QUEUED)
        self.assertEqual(
            proceso.etapa_actual,
            EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION,
        )
        self.assertEqual(proceso.intento_actual, 0)
        self.assertEqual(proceso.etapas.count(), 1)
        ejecutar.assert_called_once_with(
            solicitud_id=self.solicitud.pk,
            etapa=EtapaAutomatizacionEducativa.SECURITY_SCAN,
        )

    @patch(
        'financiacion_educativa.services.orquestacion_automatica.'
        'ejecutar_etapa_persistente'
    )
    def test_reanuda_desde_la_etapa_persistida_sin_repetir_la_anterior(
        self,
        ejecutar,
    ):
        ejecutar.side_effect = [
            SalidaEtapaPersistente(
                estado=EstadoProcesoAutomatizacionEducativa.QUEUED,
                codigo='SECURITY_SCAN_COMPLETED',
                siguiente_etapa=EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION,
            ),
            SalidaEtapaPersistente(
                estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
                codigo='DOCUMENT_VALIDATION_INCONCLUSIVE',
            ),
        ]
        proceso, _ = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )

        procesar_siguiente_trabajo()
        procesar_siguiente_trabajo()

        proceso.refresh_from_db()
        self.assertEqual(
            [llamada.kwargs['etapa'] for llamada in ejecutar.call_args_list],
            [
                EtapaAutomatizacionEducativa.SECURITY_SCAN,
                EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION,
            ],
        )
        self.assertEqual(proceso.etapas.count(), 2)
        self.assertEqual(
            proceso.estado,
            EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
        )

    @patch(
        'financiacion_educativa.services.orquestacion_automatica.'
        'ejecutar_etapa_persistente',
        side_effect=ErrorValidacionDocumentalIA('PROVIDER_ERROR'),
    )
    def test_error_temporal_programa_backoff_acotado(self, _ejecutar):
        proceso, _ = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )
        antes = timezone.now()

        procesar_siguiente_trabajo()

        proceso.refresh_from_db()
        self.assertEqual(
            proceso.estado,
            EstadoProcesoAutomatizacionEducativa.RETRYING,
        )
        self.assertEqual(proceso.codigo_razon, 'PROVIDER_ERROR')
        self.assertGreaterEqual(
            proceso.proxima_ejecucion_en,
            antes + timedelta(seconds=10),
        )
        self.assertLessEqual(
            proceso.proxima_ejecucion_en,
            timezone.now() + timedelta(seconds=30),
        )

    @patch(
        'financiacion_educativa.services.orquestacion_automatica.'
        'ejecutar_etapa_persistente',
        side_effect=ValueError('detalle no publico'),
    )
    def test_error_permanente_falla_sin_persistir_detalle(self, _ejecutar):
        proceso, _ = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )

        procesar_siguiente_trabajo()

        proceso.refresh_from_db()
        self.assertEqual(proceso.estado, EstadoProcesoAutomatizacionEducativa.FAILED)
        self.assertEqual(proceso.codigo_razon, 'INTERNAL_ERROR')
        self.assertNotIn('detalle', proceso.codigo_razon.lower())

    def test_lease_vencido_es_recuperable(self):
        proceso, _ = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )
        proceso.estado = EstadoProcesoAutomatizacionEducativa.RUNNING
        proceso.lease_id = uuid.uuid4()
        proceso.lease_vence_en = timezone.now() - timedelta(seconds=1)
        proceso.save()

        recuperados = recuperar_leases_vencidos()

        proceso.refresh_from_db()
        self.assertEqual(recuperados, 1)
        self.assertEqual(proceso.estado, EstadoProcesoAutomatizacionEducativa.RETRYING)
        self.assertEqual(proceso.codigo_razon, 'LEASE_EXPIRED')
        self.assertIsNone(proceso.lease_id)

    def test_worker_con_lease_obsoleto_no_sobrescribe_al_nuevo_dueno(self):
        proceso, _ = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )
        primer_reclamo = reclamar_siguiente_proceso()
        lease_obsoleto = primer_reclamo.lease_id
        proceso.refresh_from_db()
        proceso.lease_vence_en = timezone.now() - timedelta(seconds=1)
        proceso.save(update_fields=['lease_vence_en'])
        recuperar_leases_vencidos()
        segundo_reclamo = reclamar_siguiente_proceso()

        _finalizar_etapa(
            proceso_id=proceso.pk,
            lease_id=lease_obsoleto,
            salida=SalidaEtapaPersistente(
                estado=EstadoProcesoAutomatizacionEducativa.FAILED,
                codigo='INTERNAL_ERROR',
            ),
            iniciada_en=timezone.now(),
        )

        proceso.refresh_from_db()
        self.assertEqual(proceso.estado, EstadoProcesoAutomatizacionEducativa.RUNNING)
        self.assertEqual(proceso.lease_id, segundo_reclamo.lease_id)
        self.assertNotEqual(proceso.lease_id, lease_obsoleto)

    @override_settings(FINANCIACION_EDUCATIVA_WORKER_MAX_ATTEMPTS=1)
    @patch(
        'financiacion_educativa.services.orquestacion_automatica.'
        'ejecutar_etapa_persistente',
        side_effect=ErrorValidacionDocumentalIA('PROVIDER_ERROR'),
    )
    def test_agotamiento_de_reintentos_termina_en_failed(self, _ejecutar):
        proceso, _ = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )

        procesar_siguiente_trabajo()

        proceso.refresh_from_db()
        self.assertEqual(proceso.estado, EstadoProcesoAutomatizacionEducativa.FAILED)
        self.assertEqual(proceso.codigo_razon, 'MAX_ATTEMPTS_EXCEEDED')

    def test_endpoint_progreso_es_privado_persistente_y_sin_detalles_internos(self):
        proceso, _ = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )
        proceso.codigo_razon = 'PROVIDER_ERROR'
        proceso.save(update_fields=['codigo_razon', 'actualizada_en'])
        url = reverse(
            'financiacion_educativa_web:estado-procesamiento',
            kwargs={'solicitud_id': self.solicitud.pk},
        )

        anonima = self.client.get(url)
        self.client.force_login(self.usuario)
        propia = self.client.get(url)

        self.assertEqual(anonima.status_code, 302)
        self.assertEqual(propia.status_code, 200)
        datos = propia.json()
        self.assertEqual(datos['status'], 'QUEUED')
        self.assertEqual(datos['public_stage'], 'SEGURIDAD_DOCUMENTAL')
        self.assertNotIn('PROVIDER_ERROR', str(datos))
        self.assertNotIn('codigo_razon', datos)

        otro = get_user_model().objects.create_user(
            username='otro-cola@example.com',
            password='Clave-2026',
        )
        self.client.force_login(otro)
        self.assertEqual(self.client.get(url).status_code, 404)
        inexistente = reverse(
            'financiacion_educativa_web:estado-procesamiento',
            kwargs={'solicitud_id': uuid.uuid4()},
        )
        self.assertEqual(self.client.get(inexistente).status_code, 404)
        self.assertSetEqual(
            set(datos),
            {
                'status',
                'public_stage',
                'message',
                'steps',
                'requires_correction',
                'correction_requirements',
                'can_resume',
                'action',
                'should_poll',
                'is_terminal',
                'financial_terms',
                'updated_at',
            },
        )

    def test_endpoint_sin_proceso_representa_estados_finales(self):
        url = reverse(
            'financiacion_educativa_web:estado-procesamiento',
            kwargs={'solicitud_id': self.solicitud.pk},
        )
        self.client.force_login(self.usuario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.APPROVED
        self.solicitud.save(update_fields=['estado'])

        respuesta = self.client.get(url)

        self.assertEqual(respuesta.json()['status'], 'MANUAL_EXCEPTION')
        self.assertIsNone(respuesta.json()['financial_terms'])

    def test_comandos_diagnostican_y_procesan_sin_exponer_solicitud(self):
        encolar_proceso_automatizacion(solicitud_id=self.solicitud.pk)
        diagnostico = StringIO()

        call_command('diagnosticar_cola_educativa', stdout=diagnostico)

        salida = diagnostico.getvalue()
        self.assertIn('QUEUED: 1', salida)
        self.assertNotIn('COLA-001', salida)
        with patch(
            'financiacion_educativa.services.orquestacion_automatica.'
            'ejecutar_etapa_persistente',
            return_value=SalidaEtapaPersistente(
                estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
                codigo='DOCUMENT_VALIDATION_INCONCLUSIVE',
            ),
        ):
            ejecucion = StringIO()
            call_command(
                'procesar_cola_educativa',
                once=True,
                stdout=ejecucion,
            )
        self.assertIn('Procesos ejecutados: 1.', ejecucion.getvalue())

    @override_settings(FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=False)
    def test_comando_worker_no_arranca_si_automatizacion_esta_deshabilitada(self):
        with self.assertRaises(CommandError):
            call_command('procesar_cola_educativa', once=True)


@skipUnless(
    connection.vendor == 'postgresql',
    'Requiere PostgreSQL para validar skip_locked entre conexiones.',
)
@override_settings(
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True,
    FINANCIACION_EDUCATIVA_WORKER_LEASE_SECONDS=60,
    FINANCIACION_EDUCATIVA_WORKER_MAX_ATTEMPTS=3,
)
class ConcurrenciaColaPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def test_dos_workers_no_reclaman_el_mismo_proceso(self):
        usuario = get_user_model().objects.create_user(
            username='cola-pg@example.com',
            password='Clave-2026',
        )
        solicitud = crear_solicitud(usuario=usuario, referencia='COLA-PG-001')
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        solicitud.save(update_fields=['estado'])
        proceso, _ = encolar_proceso_automatizacion(solicitud_id=solicitud.pk)
        barrera = threading.Barrier(2)
        reclamados = []
        errores = []

        def reclamar():
            close_old_connections()
            try:
                barrera.wait(timeout=5)
                reclamado = reclamar_siguiente_proceso()
                reclamados.append(getattr(reclamado, 'pk', None))
            except Exception as error:  # pragma: no cover - diagnostico de hilo
                errores.append(error)
            finally:
                close_old_connections()

        hilos = [threading.Thread(target=reclamar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(10)

        self.assertEqual(errores, [])
        self.assertEqual(reclamados.count(proceso.pk), 1)
        self.assertEqual(reclamados.count(None), 1)
