from django.test import SimpleTestCase, override_settings

from financiacion_educativa.checks import check_document_scan_configuration


CLAMAV = (
    'financiacion_educativa.services.escaneo_documentos.'
    'ClamAVDocumentScanBackend'
)


class ConfiguracionEscaneoChecksTests(SimpleTestCase):
    def _ids(self):
        return {error.id for error in check_document_scan_configuration(None)}

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=CLAMAV,
        FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET='/run/clamav/clamd.ctl',
        FINANCIACION_EDUCATIVA_CLAMAV_HOST='',
    )
    def test_configuracion_unix_valida(self):
        self.assertEqual(self._ids(), set())

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=CLAMAV,
        FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET='',
        FINANCIACION_EDUCATIVA_CLAMAV_HOST='127.0.0.1',
        FINANCIACION_EDUCATIVA_CLAMAV_PORT=3310,
    )
    def test_configuracion_tcp_valida(self):
        self.assertEqual(self._ids(), set())

    def test_backend_vacio_y_desconocido(self):
        with override_settings(FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=''):
            self.assertIn('financiacion_educativa.E001', self._ids())
        with override_settings(
            FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND='no.existe.Backend'
        ):
            self.assertIn('financiacion_educativa.E002', self._ids())

    def test_valores_numericos_invalidos(self):
        casos = {
            'FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS': 'E005',
            'FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS': 'E006',
            'FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS': 'E007',
            'FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS': 'E008',
            'FINANCIACION_EDUCATIVA_SCAN_MAX_REOPENINGS': 'E009',
            'FINANCIACION_EDUCATIVA_SCAN_REOPEN_EXTRA_ATTEMPTS': 'E010',
        }
        for nombre, codigo in casos.items():
            invalidos = (0, -1, 'no-numerico', None, True, False)
            if codigo in {'E005', 'E006', 'E009', 'E010'}:
                invalidos += (1.5,)
            else:
                invalidos += (float('nan'), float('inf'))
            for valor in invalidos:
                with (
                    self.subTest(nombre=nombre, valor=valor),
                    override_settings(**{nombre: valor}),
                ):
                    self.assertIn(
                        f'financiacion_educativa.{codigo}',
                        self._ids(),
                    )
        for puerto in (0, -1, 65536, 'no-numerico', None, True, 3310.0):
            with self.subTest(puerto=puerto), override_settings(
                FINANCIACION_EDUCATIVA_CLAMAV_PORT=puerto
            ):
                self.assertIn('financiacion_educativa.E011', self._ids())

    @override_settings(
        FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS=3,
        FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS=300,
        FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS=3,
        FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS=30.5,
        FINANCIACION_EDUCATIVA_SCAN_MAX_REOPENINGS=1,
        FINANCIACION_EDUCATIVA_SCAN_REOPEN_EXTRA_ATTEMPTS=1,
        FINANCIACION_EDUCATIVA_CLAMAV_PORT=3310,
    )
    def test_tipos_y_rangos_numericos_validos(self):
        self.assertEqual(self._ids(), set())

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=CLAMAV,
    )
    def test_clamav_exige_un_unico_destino(self):
        with override_settings(
            FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET='',
            FINANCIACION_EDUCATIVA_CLAMAV_HOST='',
        ):
            self.assertIn('financiacion_educativa.E012', self._ids())
        with override_settings(
            FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET='/run/clamav/clamd.ctl',
            FINANCIACION_EDUCATIVA_CLAMAV_HOST='127.0.0.1',
        ):
            self.assertIn('financiacion_educativa.E012', self._ids())

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=(
            'financiacion_educativa.tests.scan_backends.BackendLimpio'
        ),
        FINANCIACION_EDUCATIVA_ALLOW_TEST_SCAN_BACKENDS=False,
    )
    def test_backend_falso_requiere_habilitacion_explicita(self):
        self.assertIn('financiacion_educativa.E004', self._ids())
        with override_settings(
            FINANCIACION_EDUCATIVA_ALLOW_TEST_SCAN_BACKENDS=True
        ):
            self.assertNotIn('financiacion_educativa.E004', self._ids())
