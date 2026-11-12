import os
from pathlib import Path
import subprocess
from unittest import skipUnless
from urllib.parse import urlencode

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO
from financiacion_educativa.tests.test_reportes_institucionales import DatosReportesInstitucionales


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local para capturas.')
@override_settings(**{**AISLAMIENTO, 'TIME_ZONE': 'America/Bogota'})
class ReportesInstitucionalesBrowserTests(DatosReportesInstitucionales, StaticLiveServerTestCase):
    def setUp(self):
        self.preparar_reportes()

    def test_reportes_sin_javascript_y_listados_reconciliados_en_dos_instituciones(self):
        cliente = Client()
        cliente.force_login(self.usuario)
        ajeno = Client()
        ajeno.force_login(self.usuario_ajeno)
        banco = self.evidencia()
        output = Path(os.environ['EDU_REPORT_SCREENSHOTS'])
        output.mkdir(parents=True, exist_ok=True)
        ruta = self.ruta('reportes')
        env = {**os.environ, 'EDU_REPORT_BASE': self.live_server_url,
            'EDU_REPORT_SESSION': cliente.cookies['sessionid'].value,
            'EDU_REPORT_OTHER_SESSION': ajeno.cookies['sessionid'].value,
            'EDU_REPORT_HOME': self.ruta('inicio'), 'EDU_REPORT_PATH': ruta,
            'EDU_REPORT_URL': ruta + '?' + urlencode({'desde': '2026-08-01', 'hasta': '2026-10-31'}),
            'EDU_REPORT_COURSE': self.curso,
            'EDU_REPORT_OTHER_APPLICATION': self.ruta('solicitud-detalle', self.ajena.pk),
            'EDU_REPORT_BANK': reverse('financiacion_educativa_web:operaciones:evidencia-propia', args=[banco.pk]),
        }
        result = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_reportes_institucionales_browser.js')],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print('Reportes: navegador sin JavaScript, cifras/listados e instituciones aisladas; 375/390/1440 px OK.')
