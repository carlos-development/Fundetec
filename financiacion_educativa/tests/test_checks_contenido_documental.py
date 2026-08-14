from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from financiacion_educativa.checks import check_document_content_configuration


DISABLED = (
    'financiacion_educativa.services.clasificacion_contenido_documental.'
    'DisabledContentDocumentClassificationBackend'
)
TEST_BACKEND = (
    'financiacion_educativa.tests.content_validation_backends.'
    'BackendContenidoConcluyente'
)


class ConfiguracionContenidoDocumentalChecksTests(SimpleTestCase):
    def ids(self):
        return {error.id for error in check_document_content_configuration(None)}

    def test_configuracion_deshabilitada_predeterminada_es_valida(self):
        self.assertEqual(self.ids(), set())

    @override_settings(FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND='')
    def test_backend_vacio_es_invalido(self):
        self.assertIn('financiacion_educativa.E105', self.ids())

    @override_settings(
        FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND=TEST_BACKEND,
        FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS=False,
    )
    def test_backend_de_prueba_exige_habilitacion(self):
        self.assertIn('financiacion_educativa.E108', self.ids())

    def test_limites_y_umbrales_invalidos_se_rechazan(self):
        casos = {
            'FINANCIACION_EDUCATIVA_PDF_MAX_BYTES': 'E090',
            'FINANCIACION_EDUCATIVA_PDF_MAX_PAGES': 'E091',
            'FINANCIACION_EDUCATIVA_PDF_MAX_PIXELS_PER_PAGE': 'E093',
            'FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS': 'E097',
            'FINANCIACION_EDUCATIVA_CONTENT_MIN_CONFIDENCE': 'E098',
            'FINANCIACION_EDUCATIVA_CONTENT_MIN_LEGIBILITY': 'E099',
            'FINANCIACION_EDUCATIVA_CONTENT_MIN_COMPLETENESS': 'E100',
        }
        for nombre, codigo in casos.items():
            with self.subTest(nombre=nombre), override_settings(**{nombre: -1}):
                self.assertIn(f'financiacion_educativa.{codigo}', self.ids())

    @override_settings(
        FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=True,
        FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND=TEST_BACKEND,
        FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS=True,
        FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY='',
    )
    def test_habilitado_exige_clave_hmac(self):
        self.assertIn('financiacion_educativa.E111', self.ids())

    @override_settings(
        DEBUG=False,
        FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=True,
        FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS=False,
        FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND=TEST_BACKEND,
        FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS=True,
        FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY='test-key',
    )
    def test_produccion_exige_aislamiento_por_subproceso(self):
        self.assertIn('financiacion_educativa.E127', self.ids())

    def test_limites_relacionados_y_consumo_excesivo_se_rechazan(self):
        casos = (
            (
                {
                    'FINANCIACION_EDUCATIVA_PDF_MAX_BYTES': 100,
                    'FINANCIACION_EDUCATIVA_PDF_MAX_OBJECT_BYTES': 101,
                },
                'E122',
            ),
            ({'FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS': 11}, 'E123'),
            ({'FINANCIACION_EDUCATIVA_PDF_MAX_EXTRACTED_CHARACTERS': 1_000_001}, 'E124'),
            ({'FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS': 121}, 'E125'),
            ({'FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS': 'false'}, 'E126'),
        )
        for valores, codigo in casos:
            with self.subTest(codigo=codigo), override_settings(**valores):
                self.assertIn(f'financiacion_educativa.{codigo}', self.ids())

    @override_settings(
        FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=True,
        FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND=TEST_BACKEND,
        FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS=True,
        FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY='test-key',
    )
    @patch('builtins.__import__', side_effect=ImportError('missing'))
    def test_habilitado_sin_dependencias_falla_check(self, _import):
        self.assertIn('financiacion_educativa.E109', self.ids())
