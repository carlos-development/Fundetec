import os
from pathlib import Path
import subprocess
from tempfile import mkdtemp
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from financiacion_educativa.tests.factories import imagen_jpeg_prueba
from financiacion_educativa.tests.test_recaudos_breb import DatosRecaudosMixin, AISLAMIENTO


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local.')
@override_settings(**AISLAMIENTO)
class RecaudosBrowserTests(DatosRecaudosMixin, StaticLiveServerTestCase):
    def test_reportar_desde_movil_y_consulta_operativa_sin_overflow(self):
        self.preparar_recaudos()
        reporte, _ = self.reportar()
        estudiante = Client()
        estudiante.force_login(self.pagador)
        operador = Client()
        operador.force_login(self.actor)
        muestras = mkdtemp(prefix='fundetec-breb-movil-')
        soporte = Path(self.temporal.name) / 'comprobante-sintetico.jpg'
        soporte.write_bytes(imagen_jpeg_prueba().read())
        env = {**os.environ,
            'EDU_BREB_BASE': self.live_server_url,
            'EDU_BREB_SESSION': estudiante.cookies['sessionid'].value,
            'EDU_BREB_OPERATOR_SESSION': operador.cookies['sessionid'].value,
            'EDU_BREB_FORM': reverse('financiacion_educativa_web:pagos', args=[self.solicitud.pk]),
            'EDU_BREB_STATE': reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, reporte.pk]),
            'EDU_BREB_DETAIL': reverse('financiacion_educativa_web:operaciones:recaudo-detalle', args=[reporte.pk]),
            'EDU_BREB_RECEIPT': str(soporte), 'EDU_BREB_SCREENSHOTS': muestras,
        }
        script = Path(__file__).parent / 'js' / 'test_recaudos_breb_browser.js'
        resultado = subprocess.run(['node', '--test', str(script)], env=env, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=120)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        print(f'Capturas sinteticas BRE-B: {muestras}')
