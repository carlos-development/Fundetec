from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import close_old_connections, connection, connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from financiacion_educativa.checks import check_signature_send_pause
from financiacion_educativa.models import CondicionesFinancieras, ProcesoFirmaEducativa
from financiacion_educativa.services.cola_automatizacion import (
    encolar_proceso_automatizacion, procesar_siguiente_trabajo,
)
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.services.firma_zapsign import enviar_pagare_educativo
from financiacion_educativa.services.pausa_firma import firma_pausada
from financiacion_educativa.tests.signature_backends import RecordingEducationalSignatureBackend
from financiacion_educativa.tests import test_orquestacion_automatica as fixtures


class ConfiguracionPausaTests(SimpleTestCase):
    def test_pausa_solo_staging_test_y_falla_cerrada_en_produccion(self):
        for ambiente in ('staging', 'test', 'production', 'local'):
            with self.subTest(ambiente=ambiente), override_settings(
                DEPLOYMENT_ENVIRONMENT=ambiente,
                FINANCIACION_EDUCATIVA_SIGNATURE_SEND_PAUSED=True,
            ):
                if ambiente in {'staging', 'test'}:
                    self.assertEqual(check_signature_send_pause(None), [])
                    self.assertTrue(firma_pausada())
                else:
                    self.assertEqual(check_signature_send_pause(None)[0].id, 'financiacion_educativa.E090')
                    with self.assertRaises(ImproperlyConfigured):
                        firma_pausada()

    @override_settings(DEPLOYMENT_ENVIRONMENT='production', FINANCIACION_EDUCATIVA_SIGNATURE_SEND_PAUSED=False)
    def test_desactivada_no_altera_produccion(self):
        self.assertFalse(firma_pausada())
        self.assertEqual(check_signature_send_pause(None), [])


@override_settings(**{
    **fixtures.OrquestacionAutomaticaTests._overridden_settings,
    'DEPLOYMENT_ENVIRONMENT': 'test',
    'FINANCIACION_EDUCATIVA_SIGNATURE_SEND_PAUSED': True,
})
class PausaFirmaTests(TransactionTestCase):
    # Reuse synthetic E2E fixtures without inheriting or duplicating their tests.
    setUp = fixtures.OrquestacionAutomaticaTests.setUp
    _solicitud_base = fixtures.OrquestacionAutomaticaTests._solicitud_base
    _participante = fixtures.OrquestacionAutomaticaTests._participante
    _documento = fixtures.OrquestacionAutomaticaTests._documento
    _adulto_listo = fixtures.OrquestacionAutomaticaTests._adulto_listo

    def _pausar(self):
        solicitud, _ = self._adulto_listo()
        proceso, _ = encolar_proceso_automatizacion(solicitud_id=solicitud.pk)
        with (
            patch('financiacion_educativa.services.firma_zapsign._backend') as backend,
            patch('financiacion_educativa.services.artefactos_contractuales._renderizar_pdf',
                  return_value=b'%PDF-1.7\nsynthetic-pause-test\n%%EOF'),
        ):
            for _ in range(10):
                if not procesar_siguiente_trabajo().procesado:
                    break
            else:
                self.fail('La cola pausada no debe consumir intentos continuamente.')
            with self.assertRaises(ValidationError) as error:
                enviar_pagare_educativo(proceso=None)
            self.assertEqual(error.exception.code, 'SIGNATURE_SEND_PAUSED')
            backend.assert_not_called()
        proceso.refresh_from_db()
        solicitud.refresh_from_db()
        self.assertEqual(proceso.estado, 'QUEUED')
        self.assertEqual(proceso.etapa_actual, 'SIGNATURE_SEND')
        self.assertEqual(proceso.intento_actual, 0)
        self.assertEqual(solicitud.estado, 'PENDING_PROMISSORY_NOTE')
        self.assertTrue(proceso.etapas.filter(metadata_publica__signature_send_paused=True).exists())
        self.assertFalse(solicitud.documentos.filter(activo=True).exclude(estado_validacion='APPROVED').exists())
        publico = obtener_resultado_publico(solicitud)
        self.assertFalse(publico.curso_autorizado)
        self.assertIsNone(publico.condiciones_financieras)
        self.assertEqual(RecordingEducationalSignatureBackend.submissions, [])
        return solicitud, proceso

    def _comprobar_envio_unico(self, solicitud, proceso):
        solicitud.refresh_from_db()
        proceso.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PENDING_SIGNATURE')
        self.assertEqual(proceso.estado, 'PENDING_SIGNATURE')
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 1)
        self.assertEqual(ProcesoFirmaEducativa.objects.filter(solicitud=solicitud).count(), 1)
        self.assertEqual(CondicionesFinancieras.objects.filter(solicitud=solicitud, activa=True).count(), 1)
        self.assertFalse(procesar_siguiente_trabajo().procesado)
        self.assertFalse(obtener_resultado_publico(solicitud).curso_autorizado)

    def test_pausa_y_reanudacion_sin_invocar_backend_antes_de_autorizacion(self):
        solicitud, proceso = self._pausar()
        with override_settings(FINANCIACION_EDUCATIVA_SIGNATURE_SEND_PAUSED=False):
            self.assertTrue(procesar_siguiente_trabajo().procesado)
            self._comprobar_envio_unico(solicitud, proceso)

    @skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL real.')
    def test_dos_workers_reanudan_un_solo_envio(self):
        solicitud, proceso = self._pausar()
        barrera = Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrera.wait(timeout=15)
                return procesar_siguiente_trabajo().procesado
            finally:
                connections['default'].close()

        with override_settings(FINANCIACION_EDUCATIVA_SIGNATURE_SEND_PAUSED=False):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futuros = [executor.submit(worker) for _ in range(2)]
                resultados = [futuro.result(timeout=30) for futuro in futuros]
            self.assertEqual(sum(resultados), 1)
            self._comprobar_envio_unico(solicitud, proceso)
