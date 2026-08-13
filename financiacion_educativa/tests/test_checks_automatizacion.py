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
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=False,
        FINANCIACION_EDUCATIVA_ACREEDOR_NIT='',
        FINANCIACION_EDUCATIVA_ACREEDOR_REPRESENTANTE_LEGAL='',
        FINANCIACION_EDUCATIVA_ACREEDOR_DOMICILIO='',
        FINANCIACION_EDUCATIVA_PAGARE_VERSION_JURIDICA='',
        FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION='',
        FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_CARTA_INSTRUCCIONES='',
        FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_INCUMPLIMIENTO='',
    )
    def test_habilitada_exige_ia_firma_y_acreedor(self):
        self.assertEqual(
            self.ids(),
            {
                'financiacion_educativa.E061',
                'financiacion_educativa.E062',
                'financiacion_educativa.E063',
                'financiacion_educativa.E064',
                'financiacion_educativa.E065',
                'financiacion_educativa.E066',
                'financiacion_educativa.E067',
                'financiacion_educativa.E068',
                'financiacion_educativa.E069',
                'financiacion_educativa.E070',
                'financiacion_educativa.E071',
            },
        )

    @override_settings(
        FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True,
        FINANCIACION_EDUCATIVA_WORKER_LEASE_SECONDS=0,
        FINANCIACION_EDUCATIVA_WORKER_MAX_ATTEMPTS=-1,
        FINANCIACION_EDUCATIVA_WORKER_BACKOFF_BASE_SECONDS=60,
        FINANCIACION_EDUCATIVA_WORKER_BACKOFF_MAX_SECONDS=30,
    )
    def test_parametros_del_worker_deben_ser_positivos_y_acotados(self):
        self.assertTrue(
            {
                'financiacion_educativa.E072',
                'financiacion_educativa.E073',
                'financiacion_educativa.E076',
            }.issubset(self.ids())
        )
