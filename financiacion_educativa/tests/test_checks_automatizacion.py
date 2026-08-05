from django.test import SimpleTestCase, override_settings

from financiacion_educativa.checks import (
    DISABLED_DOCUMENT_AI_BACKEND,
    DISABLED_EDUCATIONAL_SIGNATURE_BACKEND,
    check_educational_automation_configuration,
)


class AutomatizacionEducativaChecksTests(SimpleTestCase):
    def ids(self):
        return {
            error.id
            for error in check_educational_automation_configuration(None)
        }

    @override_settings(FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=False)
    def test_deshabilitada_no_exige_integraciones(self):
        self.assertEqual(self.ids(), set())

    @override_settings(FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED='yes')
    def test_interruptor_debe_ser_booleano(self):
        self.assertEqual(self.ids(), {'financiacion_educativa.E060'})

    @override_settings(
        FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True,
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=DISABLED_DOCUMENT_AI_BACKEND,
        FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND=(
            DISABLED_EDUCATIONAL_SIGNATURE_BACKEND
        ),
        FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL='',
    )
    def test_habilitada_exige_ia_firma_y_acreedor(self):
        self.assertEqual(
            self.ids(),
            {
                'financiacion_educativa.E061',
                'financiacion_educativa.E062',
                'financiacion_educativa.E063',
            },
        )
