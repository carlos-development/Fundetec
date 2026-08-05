from django.test import SimpleTestCase, override_settings

from financiacion_educativa.checks import (
    ZAPSIGN_EDUCATIONAL_BACKEND,
    check_educational_signature_configuration,
)


class EducationalSignatureChecksTests(SimpleTestCase):
    def _ids(self):
        return {
            error.id
            for error in check_educational_signature_configuration(None)
        }

    @override_settings(FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND='')
    def test_rechaza_backend_vacio(self):
        self.assertIn('financiacion_educativa.E040', self._ids())

    @override_settings(
        FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND=(
            'financiacion_educativa.tests.signature_backends.'
            'RecordingEducationalSignatureBackend'
        ),
        FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS=False,
    )
    def test_backend_prueba_exige_habilitacion(self):
        self.assertIn('financiacion_educativa.E044', self._ids())

    @override_settings(
        FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND=ZAPSIGN_EDUCATIONAL_BACKEND,
        FINANCIACION_EDUCATIVA_ZAPSIGN_API_TOKEN='',
        FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET='',
        FINANCIACION_EDUCATIVA_ZAPSIGN_SEND_AUTOMATIC_EMAIL=False,
    )
    def test_backend_real_exige_secretos_y_canal_de_entrega(self):
        self.assertTrue({
            'financiacion_educativa.E050',
            'financiacion_educativa.E051',
            'financiacion_educativa.E053',
        }.issubset(self._ids()))

    @override_settings(
        FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_HEADER='invalid header',
    )
    def test_rechaza_header_invalido(self):
        self.assertIn('financiacion_educativa.E048', self._ids())
