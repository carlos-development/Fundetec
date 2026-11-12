import os
from pathlib import Path
import subprocess
from unittest import skipUnless
from urllib.parse import urlencode

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings

from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO
from financiacion_educativa.tests.test_tablero_recaudos import DatosTableroRecaudos


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local para capturas.')
@override_settings(**AISLAMIENTO)
class TableroRecaudosBrowserTests(DatosTableroRecaudos, StaticLiveServerTestCase):
    def test_tablero_y_respaldo_sin_javascript(self):
        self.preparar_tablero()
        cliente, pagador = Client(), Client()
        cliente.force_login(self.actor)
        pagador.force_login(self.pagador)
        output = Path(os.environ['EDU_COLLECTIONS_SCREENSHOTS'])
        output.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'EDU_COLLECTIONS_BASE': self.live_server_url,
            'EDU_COLLECTIONS_SESSION': cliente.cookies['sessionid'].value,
            'EDU_COLLECTIONS_PAYER': pagador.cookies['sessionid'].value,
            'EDU_COLLECTIONS_PATH': self.ruta_tablero(),
            'EDU_COLLECTIONS_URL': self.ruta_tablero() + '?' + urlencode(self.rango),
            'EDU_COLLECTIONS_INSTITUTION': str(self.institucion.pk),
        }
        result = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_tablero_recaudos_browser.js')],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print('Tablero: filtros, importes/listados y capturas 375/390/1440 px sin JavaScript OK.')
