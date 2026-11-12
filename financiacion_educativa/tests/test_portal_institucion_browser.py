import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from financiacion_educativa.tests.test_portal_institucion import DatosPortalInstitucion
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local para capturas.')
@override_settings(**AISLAMIENTO)
class PortalInstitucionBrowserTests(DatosPortalInstitucion, StaticLiveServerTestCase):
    def setUp(self):
        self.preparar_portal()

    def test_consulta_sin_javascript_aislamiento_y_resultados_separados(self):
        cliente = Client()
        cliente.force_login(self.usuario)
        ajeno = Client()
        ajeno.force_login(self.usuario_ajeno)
        banco = self.evidencia()
        output = Path(os.environ['EDU_INST_SCREENSHOTS'])
        output.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'EDU_INST_BASE': self.live_server_url,
            'EDU_INST_SESSION': cliente.cookies['sessionid'].value,
            'EDU_INST_OTHER_SESSION': ajeno.cookies['sessionid'].value,
            'EDU_INST_TRACKING': self.ruta('seguimiento'),
            'EDU_INST_APPROVED_PENDING': self.ruta('solicitud-detalle', self.sin_autorizacion.pk),
            'EDU_INST_AUTHORIZED': self.ruta('solicitud-detalle', self.autorizada.pk),
            'EDU_INST_OTHER': self.ruta('solicitud-detalle', self.ajena.pk),
            'EDU_INST_PENDING_REFERENCE': self.sin_autorizacion.referencia_externa,
            'EDU_INST_AUTHORIZED_REFERENCE': self.autorizada.referencia_externa,
            'EDU_INST_OTHER_REFERENCE': self.ajena.referencia_externa,
            'EDU_INST_BANK': reverse('financiacion_educativa_web:operaciones:evidencia-propia', args=[banco.pk]),
        }
        resultado = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_portal_institucion_browser.js')],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        print('Institucion: navegador sin JavaScript y capturas 375/390/1440 px OK.')
