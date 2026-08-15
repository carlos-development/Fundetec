import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EtapaAutomatizacionEducativa,
    EstadoProcesoAutomatizacionEducativa,
    EstadoSolicitudFinanciacion,
    RequisitoCorreccionEducativa,
)
from financiacion_educativa.models import ProcesoAutomatizacionEducativa
from financiacion_educativa.tests.factories import (
    crear_institucion,
    crear_solicitud,
)


class ProgresoProcesamientoWebTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username='progreso@example.com',
            email='progreso@example.com',
            password='Clave-Progreso-2026',
        )
        self.otro = get_user_model().objects.create_user(
            username='progreso-otro@example.com',
            password='Clave-Progreso-2026',
        )
        self.institucion = crear_institucion('82')
        self.solicitud = crear_solicitud(
            institucion=self.institucion,
            referencia='PROGRESO-001',
            usuario=self.usuario,
        )
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        self.solicitud.save(update_fields=['estado'])
        self.estado_url = reverse(
            'financiacion_educativa_web:estado-procesamiento',
            kwargs={'solicitud_id': self.solicitud.pk},
        )
        self.pagina_url = reverse(
            'financiacion_educativa_web:procesamiento',
            kwargs={'solicitud_id': self.solicitud.pk},
        )

    def _proceso(self, estado='QUEUED', etapa='SECURITY_SCAN', **extra):
        datos = {
            'solicitud': self.solicitud,
            'version_expediente': 1,
            'estado': estado,
            'etapa_actual': etapa,
        }
        datos.update(extra)
        if estado == EstadoProcesoAutomatizacionEducativa.RUNNING:
            datos.setdefault('lease_id', uuid.uuid4())
            datos.setdefault('lease_vence_en', timezone.now())
        return ProcesoAutomatizacionEducativa.objects.create(**datos)

    def test_pagina_es_privada_reanudable_y_no_crea_procesos(self):
        anonima = self.client.get(self.pagina_url)
        self.client.force_login(self.usuario)
        propia = self.client.get(self.pagina_url)

        self.assertEqual(anonima.status_code, 302)
        self.assertEqual(propia.status_code, 200)
        self.assertContains(propia, self.estado_url)
        self.assertContains(propia, 'data-education-processing')
        self.assertIn('private', propia['Cache-Control'])
        self.assertIn('no-store', propia['Cache-Control'])
        self.assertEqual(self.solicitud.procesos_automatizacion.count(), 0)

        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(self.pagina_url).status_code, 404)

    def test_endpoint_traduce_estados_activos_sin_detalles_internos(self):
        proceso = self._proceso()
        self.client.force_login(self.usuario)
        casos = (
            ('QUEUED', 'SECURITY_SCAN', True),
            ('RUNNING', 'DOCUMENT_VALIDATION', True),
            ('RETRYING', 'DECISION', True),
            ('MANUAL_EXCEPTION', 'DECISION', False),
            ('FAILED', 'DOCUMENT_VALIDATION', False),
        )
        for estado, etapa, consulta in casos:
            with self.subTest(estado=estado):
                proceso.estado = estado
                proceso.etapa_actual = etapa
                proceso.codigo_razon = 'INTERNAL_ERROR'
                if estado == 'RUNNING':
                    proceso.lease_id = uuid.uuid4()
                    proceso.lease_vence_en = timezone.now()
                else:
                    proceso.lease_id = None
                    proceso.lease_vence_en = None
                proceso.save()

                respuesta = self.client.get(self.estado_url)
                datos = respuesta.json()

                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(datos['status'], estado)
                self.assertEqual(datos['should_poll'], consulta)
                self.assertNotIn('INTERNAL_ERROR', str(datos))
                self.assertNotIn('codigo_razon', datos)
                self.assertTrue(datos['steps'])

    def test_endpoint_controla_cache_y_aislamiento(self):
        self._proceso()
        self.client.force_login(self.usuario)

        respuesta = self.client.get(self.estado_url)

        self.assertIn('private', respuesta['Cache-Control'])
        self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertEqual(respuesta['Pragma'], 'no-cache')
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(self.estado_url).status_code, 404)

    def test_correcciones_son_consolidadas_y_no_exponen_codigos(self):
        proceso = self._proceso(
            estado=EstadoProcesoAutomatizacionEducativa.CORRECTION_REQUIRED,
            etapa=EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION,
            requisitos_correccion=[
                RequisitoCorreccionEducativa.STUDENT_ID_FRONT,
                RequisitoCorreccionEducativa.STUDENT_ID_FRONT,
                RequisitoCorreccionEducativa.INCOME_CERTIFICATE,
                'CODIGO_INTERNO_DESCONOCIDO',
            ],
        )
        self.solicitud.estado = EstadoSolicitudFinanciacion.CORRECTION_REQUIRED
        self.solicitud.save(update_fields=['estado'])
        self.client.force_login(self.usuario)

        datos = self.client.get(self.estado_url).json()

        self.assertEqual(datos['status'], 'CORRECTION_REQUIRED')
        self.assertFalse(datos['should_poll'])
        self.assertEqual(len(datos['correction_requirements']), 2)
        self.assertNotIn('STUDENT_ID_FRONT', str(datos))
        self.assertNotIn('CODIGO_INTERNO_DESCONOCIDO', str(datos))
        self.assertTrue(all(
            item['action']['url'].startswith('/financiacion-educativa/')
            for item in datos['correction_requirements']
        ))
        proceso.refresh_from_db()

    def test_version_obsoleta_no_se_presenta_como_correccion_actual(self):
        self._proceso(
            estado=EstadoProcesoAutomatizacionEducativa.CORRECTION_REQUIRED,
            etapa=EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION,
            requisitos_correccion=[RequisitoCorreccionEducativa.STUDENT_ID_FRONT],
        )
        self.client.force_login(self.usuario)

        datos = self.client.get(self.estado_url).json()

        self.assertEqual(datos['status'], 'MANUAL_EXCEPTION')
        self.assertFalse(datos['requires_correction'])
        self.assertEqual(datos['correction_requirements'], [])

    def test_firma_pendiente_nunca_se_presenta_como_aprobacion(self):
        self._proceso(
            estado=EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE,
            etapa=EtapaAutomatizacionEducativa.WAITING_SIGNATURE,
        )
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_SIGNATURE
        self.solicitud.save(update_fields=['estado'])
        self.client.force_login(self.usuario)

        datos = self.client.get(self.estado_url).json()

        self.assertEqual(datos['status'], 'PENDING_SIGNATURE')
        self.assertTrue(datos['should_poll'])
        self.assertNotIn('aprob', datos['message'].lower())
        self.assertIsNone(datos['financial_terms'])

    @patch(
        'financiacion_educativa.services.progreso_publico.'
        'obtener_resultado_publico'
    )
    def test_completado_solo_publica_condiciones_autorizadas(self, resultado):
        condiciones = {
            'currency': 'COP',
            'requested_amount': '1000000.00',
            'financed_amount': '1142711.00',
            'term_months': 6,
            'estimated_installment': '197173.00',
        }
        resultado.return_value = SimpleNamespace(
            curso_autorizado=True,
            condiciones_financieras=condiciones,
        )
        self._proceso(
            estado=EstadoProcesoAutomatizacionEducativa.COMPLETED,
            etapa=EtapaAutomatizacionEducativa.COMPLETED,
        )
        self.solicitud.estado = EstadoSolicitudFinanciacion.APPROVED
        self.solicitud.save(update_fields=['estado'])
        self.client.force_login(self.usuario)

        datos = self.client.get(self.estado_url).json()

        self.assertEqual(datos['status'], 'COMPLETED')
        self.assertTrue(datos['is_terminal'])
        self.assertEqual(datos['financial_terms'], condiciones)

    def test_aprobacion_inconsistente_sin_firma_exige_verificacion(self):
        self.solicitud.estado = EstadoSolicitudFinanciacion.APPROVED
        self.solicitud.save(update_fields=['estado'])
        self.client.force_login(self.usuario)

        datos = self.client.get(self.estado_url).json()

        self.assertEqual(datos['status'], 'MANUAL_EXCEPTION')
        self.assertIsNone(datos['financial_terms'])
