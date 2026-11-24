from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from requests import ConnectionError

from financiacion_educativa.checks import ZAPSIGN_EDUCATIONAL_BACKEND


COMANDO = 'diagnosticar_zapsign_educativo'
GET = f'financiacion_educativa.management.commands.{COMANDO}.requests.get'


@override_settings(
    DEPLOYMENT_ENVIRONMENT='staging',
    FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND=ZAPSIGN_EDUCATIONAL_BACKEND,
    FINANCIACION_EDUCATIVA_ZAPSIGN_BASE_URL='https://sandbox.api.zapsign.com.br/api/v1',
    FINANCIACION_EDUCATIVA_ZAPSIGN_API_TOKEN='token-sintetico',
    FINANCIACION_EDUCATIVA_ZAPSIGN_TIMEOUT_SECONDS=10,
)
class DiagnosticarZapSignEducativoTests(SimpleTestCase):
    @patch(GET)
    def test_consulta_solo_el_entorno_esperado_y_no_lee_el_cuerpo(self, get):
        escenarios = (
            ('staging', 'sandbox.api.zapsign.com.br'),
            ('production', 'api.zapsign.com.br'),
            ('production', 'br.api.zapsign.com.br'),
        )
        respuesta = get.return_value.__enter__.return_value
        respuesta.status_code = 200
        for entorno, host in escenarios:
            base_url = f'https://{host}/api/v1'
            with self.subTest(entorno=entorno, host=host), override_settings(
                DEPLOYMENT_ENVIRONMENT=entorno,
                FINANCIACION_EDUCATIVA_ZAPSIGN_BASE_URL=base_url,
            ):
                salida = StringIO()
                get.reset_mock()
                call_command(COMANDO, entorno=entorno, stdout=salida)
                self.assertEqual(salida.getvalue().strip(), f'ZapSign {entorno}: HTTP 200.')
                get.assert_called_once_with(
                    f'{base_url}/docs/',
                    params={'page': 1},
                    headers={'Authorization': 'Bearer token-sintetico'},
                    timeout=10,
                    allow_redirects=False,
                    stream=True,
                )
                respuesta.json.assert_not_called()
                respuesta.iter_content.assert_not_called()

    @patch(GET)
    def test_rechaza_cruce_de_entorno_y_url_ajena_antes_de_enviar(self, get):
        with self.assertRaisesMessage(CommandError, 'El entorno efectivo'):
            call_command(COMANDO, entorno='production')
        for base_url in (
            'https://api.zapsign.com.br/api/v1',
            'https://sandbox.api.zapsign.com.br.ejemplo.test/api/v1',
        ):
            with self.subTest(base_url=base_url), override_settings(
                FINANCIACION_EDUCATIVA_ZAPSIGN_BASE_URL=base_url,
            ):
                with self.assertRaisesMessage(CommandError, 'La URL de ZapSign'):
                    call_command(COMANDO, entorno='staging')
        get.assert_not_called()

    @patch(GET)
    def test_error_http_o_de_red_solo_reporta_datos_operativos(self, get):
        for estado in (302, 401, 403, 503):
            get.return_value.__enter__.return_value.status_code = estado
            with self.subTest(estado=estado):
                with self.assertRaisesMessage(CommandError, f'ZapSign staging: HTTP {estado}.'):
                    call_command(COMANDO, entorno='staging')
        get.side_effect = ConnectionError('detalle privado que no debe imprimirse')
        with self.assertRaises(CommandError) as error:
            call_command(COMANDO, entorno='staging')
        self.assertEqual(str(error.exception), 'ZapSign staging: error de conexión.')
