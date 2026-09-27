import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from financiacion_educativa.tests.factories import crear_configuracion_financiera


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local mediante EDU_PLAYWRIGHT_MODULE.')
@override_settings(
    FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_INITIAL_TERM_MONTHS=6,
    FINANCIACION_EDUCATIVA_PUBLIC_SIMULATOR_RATE_LIMIT_REQUESTS=200,
)
class SimuladorBrowserTests(StaticLiveServerTestCase):
    def test_navegador_real_sin_proveedores_externos(self):
        crear_configuracion_financiera()
        script = Path(__file__).parent / 'js' / 'test_simulador_browser.js'
        env = {**os.environ, 'EDU_SIMULATOR_TEST_URL': f'{self.live_server_url}/financiacion-educativa/simulador/'}
        resultado = subprocess.run(
            ['node', '--test', '--test-reporter=tap', str(script)], env=env, text=True,
            encoding='utf-8', errors='replace', capture_output=True, timeout=150,
        )
        print(resultado.stdout)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
