import os
from pathlib import Path
import subprocess
from tempfile import mkdtemp
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from financiacion_educativa.tests.test_dashboard_obligaciones import DatosObligacionesMixin


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local mediante EDU_PLAYWRIGHT_MODULE.')
@override_settings(
    DEPLOYMENT_ENVIRONMENT='test', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_LIVE_DELIVERY_ENABLED=False, FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=False,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=False, FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=False,
)
class DashboardObligacionesBrowserTests(DatosObligacionesMixin, StaticLiveServerTestCase):
    def test_consulta_responsive_y_tabla_desplazable(self):
        self.preparar_datos()
        solicitud, _, _, _ = self.contrato('CONTRATO-SINTETICO-MOVIL')
        self.client.force_login(self.actor)
        muestras = mkdtemp(prefix='fundetec-obligaciones-')
        env = {
            **os.environ, 'EDU_DASHBOARD_URL': self.live_server_url,
            'EDU_DASHBOARD_DETAIL': self.detalle_url(solicitud),
            'EDU_DASHBOARD_SESSION': self.client.cookies['sessionid'].value,
            'EDU_DASHBOARD_SCREENSHOTS': muestras,
        }
        script = Path(__file__).parent / 'js' / 'test_dashboard_obligaciones_browser.js'
        resultado = subprocess.run(
            ['node', '--test', str(script)], env=env, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=120,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        print(f'Capturas sinteticas del dashboard: {muestras}')
