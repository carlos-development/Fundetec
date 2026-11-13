import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from financiacion_educativa.tests.test_seguimiento_cartera import AISLAR, DatosSeguimiento


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local para capturas.')
@override_settings(**AISLAR)
class SeguimientoCarteraBrowserTests(DatosSeguimiento, StaticLiveServerTestCase):
    def test_recorrido_cartera_y_portal_sin_javascript(self):
        self.datos_visuales()
        operador, pagador = Client(), Client()
        operador.force_login(self.actor)
        pagador.force_login(self.pagador)
        carpeta = Path(os.environ['EDU_FOLLOWUP_SCREENSHOTS'])
        carpeta.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'EDU_FOLLOWUP_BASE': self.live_server_url,
            'EDU_FOLLOWUP_SESSION': operador.cookies['sessionid'].value,
            'EDU_FOLLOWUP_PAYER_SESSION': pagador.cookies['sessionid'].value,
            'EDU_FOLLOWUP_PATH': self.ruta(), 'EDU_FOLLOWUP_INSTITUTION': str(self.institucion.pk),
            'EDU_FOLLOWUP_FINANCE': reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk]),
            'EDU_FOLLOWUP_REFERENCE': self.solicitud.referencia_externa,
            'EDU_FOLLOWUP_BANK': self.ingreso_abierto.referencia_verificada,
            'EDU_FOLLOWUP_PRIVATE': self.banco.archivo.name}
        resultado = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_seguimiento_cartera_browser.js')],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        print('Seguimiento: indicadores/listado/detalle/recaudo y portal sin JS; 375/390/1440 px OK.')
