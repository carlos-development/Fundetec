import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from financiacion_educativa.tests.test_ingreso_operaciones import DatosIngresoOperaciones
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO, Infectado, Fallo
from financiacion_educativa.tests.factories import imagen_jpeg_prueba
from financiacion_educativa.services import recaudos


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local para capturas.')
@override_settings(**AISLAMIENTO)
class PortalOperacionesRecaudosBrowserTests(DatosIngresoOperaciones, StaticLiveServerTestCase):
    def test_recorrido_sintetico_sin_javascript(self):
        for backend in (Infectado(), Fallo()):
            archivo = recaudos.registrar_evidencia_bancaria(archivo=imagen_jpeg_prueba(), actor=self.actor)
            self.escanear(archivo.pk, backend)
        pendiente, _ = self.reportar()
        cliente = Client()
        cliente.force_login(self.actor)
        pagador = Client()
        pagador.force_login(self.pagador)
        output = Path(os.environ['EDU_OPS_SCREENSHOTS'])
        output.mkdir(parents=True, exist_ok=True)
        archivo = Path(self.temporal.name) / 'upload-synthetic.jpg'
        archivo.write_bytes(imagen_jpeg_prueba().read())
        env = {**os.environ, 'EDU_OPS_BASE': self.live_server_url,
            'EDU_OPS_SESSION': cliente.cookies['sessionid'].value,
            'EDU_OPS_PAYER_SESSION': pagador.cookies['sessionid'].value,
            'EDU_OPS_OBLIGATION': self.detalle_url(self.solicitud),
            'EDU_OPS_INBOX': reverse('financiacion_educativa_web:operaciones:recaudos'),
            'EDU_OPS_ACCOUNT': str(self.receptor.cuenta_id), 'EDU_OPS_EVIDENCE': str(self.banco.pk),
            'EDU_OPS_AMOUNT': str(self.cuota.valor_cuota), 'EDU_OPS_DATE': self.datos['fecha'].isoformat(),
            'EDU_OPS_UPLOAD': str(archivo),
        }
        result = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_portal_operaciones_recaudos_browser.js')],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print('Operaciones: recorrido sin JavaScript y capturas 375/390/1440 px OK.')
