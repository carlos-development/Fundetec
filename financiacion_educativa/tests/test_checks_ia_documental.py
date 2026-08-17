from django.test import SimpleTestCase, override_settings

from financiacion_educativa.checks import check_document_ai_configuration


DISABLED = (
    'financiacion_educativa.services.validacion_documental_ia.'
    'DisabledDocumentAIValidationBackend'
)
OPENAI = (
    'financiacion_educativa.services.validacion_documental_ia.'
    'OpenAIDocumentAIValidationBackend'
)
TEST_BACKEND = (
    'financiacion_educativa.tests.ai_validation_backends.BackendIAConcluyente'
)


class ConfiguracionIADocumentalChecksTests(SimpleTestCase):
    def ids(self):
        return {error.id for error in check_document_ai_configuration(None)}

    @override_settings(FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND='')
    def test_backend_vacio_es_invalido(self):
        self.assertIn('financiacion_educativa.E020', self.ids())

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=TEST_BACKEND,
        FINANCIACION_EDUCATIVA_ALLOW_TEST_AI_BACKENDS=False,
    )
    def test_backend_de_prueba_exige_habilitacion(self):
        self.assertIn('financiacion_educativa.E023', self.ids())

    def test_limites_invalidos_se_rechazan(self):
        casos = {
            'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MAX_ATTEMPTS': 'E024',
            'FINANCIACION_EDUCATIVA_DOCUMENT_AI_STALE_SECONDS': 'E025',
            'FINANCIACION_EDUCATIVA_DOCUMENT_AI_TIMEOUT_SECONDS': 'E026',
            'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_CONFIDENCE': 'E027',
            'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_QUALITY': 'E028',
            'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_LEGIBILITY': 'E029',
            'FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_DIMENSION_CONFIDENCE': 'E128',
        }
        for setting, code in casos.items():
            with self.subTest(setting=setting), override_settings(**{setting: -1}):
                self.assertIn(f'financiacion_educativa.{code}', self.ids())

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=OPENAI,
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_MODEL='',
        OPENAI_API_KEY='',
    )
    def test_openai_exige_modelo_y_credencial(self):
        self.assertIn('financiacion_educativa.E030', self.ids())
        self.assertIn('financiacion_educativa.E031', self.ids())

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=DISABLED,
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_MAX_ATTEMPTS=3,
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_STALE_SECONDS=300,
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_TIMEOUT_SECONDS=30,
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_CONFIDENCE='0.85',
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_QUALITY='0.70',
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_LEGIBILITY='0.80',
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_DIMENSION_CONFIDENCE='0.80',
    )
    def test_configuracion_deshabilitada_predeterminada_es_valida(self):
        self.assertEqual(self.ids(), set())
