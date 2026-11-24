import os
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import subprocess
from unittest import skipUnless
from urllib.parse import urlencode

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings

from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO
from financiacion_educativa.tests.test_tablero_recaudos import DatosTableroRecaudos
from financiacion_educativa.templatetags.recaudo_format import cop_recaudo


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local para capturas.')
@override_settings(**AISLAMIENTO)
class TableroRecaudosBrowserTests(DatosTableroRecaudos, StaticLiveServerTestCase):
    def test_tablero_y_respaldo_sin_javascript(self):
        self.preparar_tablero()
        convenio = self.crear_convenio()
        intereses = [ingreso.aplicacion.interes_aplicado for ingreso in (self.aplicado, self.aplicado_segundo)]
        aprobado = [(interes * convenio.porcentaje_aprobado / 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                    for interes in intereses]
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
            'EDU_COLLECTIONS_APROBADO_PERIODO': cop_recaudo(sum(aprobado)),
            'EDU_COLLECTIONS_INSTITUCION_PERIODO': cop_recaudo(sum(intereses) - sum(aprobado)),
            'EDU_COLLECTIONS_APROBADO_MOVIMIENTO': cop_recaudo(aprobado[-1]),
            'EDU_COLLECTIONS_INSTITUCION_MOVIMIENTO': cop_recaudo(intereses[-1] - aprobado[-1]),
            'EDU_COLLECTIONS_APPLIED_INTEREST': cop_recaudo(sum(
                ingreso.aplicacion.interes_aplicado
                for ingreso in (self.aplicado, self.aplicado_segundo, self.aplicado_ajeno)
            )),
        }
        result = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_tablero_recaudos_browser.js')],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print('Tablero: filtros, cuadre de reparto y capturas en 320/375/390/1440 px sin JavaScript OK.')
