import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from financiacion_educativa.tests.test_plan_contractual_pdf import AISLAR_PLAN, DatosPlanContractual


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local para revisar movil.')
@override_settings(**AISLAR_PLAN)
class PlanContractualPdfBrowserTests(DatosPlanContractual, StaticLiveServerTestCase):
    def test_descarga_desde_portal_movil_y_escritorio_sin_javascript(self):
        titular, ajeno = Client(), Client()
        titular.force_login(self.pagador)
        ajeno.force_login(self.actor)
        directorio = Path(os.environ['EDU_PLAN_SCREENSHOTS'])
        directorio.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'EDU_PLAN_BASE': self.live_server_url,
            'EDU_PLAN_SESSION': titular.cookies['sessionid'].value,
            'EDU_PLAN_OTHER_SESSION': ajeno.cookies['sessionid'].value,
            'EDU_PLAN_FINANCE': reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk]),
            'EDU_PLAN_DOWNLOAD': self.ruta_plan(), 'EDU_PLAN_TEMP': self.temporal.name}
        resultado = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_plan_contractual_pdf_browser.js')],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=150)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        print('Plan contractual: descarga real sin JavaScript a 375/390/1440 px y cuenta ajena bloqueada OK.')
