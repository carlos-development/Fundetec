import os
from io import BytesIO
from pathlib import Path
import subprocess
from tempfile import mkdtemp
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from financiacion_educativa.tests.factories import imagen_jpeg_prueba
from financiacion_educativa.tests.test_recaudos_breb import DatosRecaudosMixin, AISLAMIENTO
from financiacion_educativa.services import recaudos as servicios


def qr_sintetico_preview():
    # QR generated with qrcode 8.2; payload is SYNTHETIC-NO-TRANSFERIR, not bank data.
    # Fixed test matrix avoids adding an application/runtime dependency.
    matriz = '''000000000000000000000000000000000
000000000000000000000000000000000
000000000000000000000000000000000
000000000000000000000000000000000
000011111110111111110011111110000
000010000010000000010010000010000
000010111010111101000010111010000
000010111010001110010010111010000
000010111010001100011010111010000
000010000010111101101010000010000
000011111110101010101011111110000
000000000000001001010000000000000
000010100011010101000001001010000
000001111101001010001001000100000
000001011111111101111100111010000
000010110100010100101011111000000
000011010111010101110010011100000
000000010001110001110011000010000
000011111110111100010011110000000
000000011001010001010001011000000
000011111011101111011111110110000
000000000000101010001000101100000
000011111110100011101010101000000
000010000010011110111000110110000
000010111010010001111111101100000
000010111010011001110110001000000
000010111010101100001111010110000
000010000010010001011001010010000
000011111110110111001100111110000
000000000000000000000000000000000
000000000000000000000000000000000
000000000000000000000000000000000
000000000000000000000000000000000'''.splitlines()
    imagen = Image.frombytes('L', (33, 33), bytes(0 if c == '1' else 255 for row in matriz for c in row))
    salida = BytesIO()
    imagen.resize((330, 330), Image.Resampling.NEAREST).save(salida, format='PNG')
    return SimpleUploadedFile('qr-sintetico-no-transferir.png', salida.getvalue(), content_type='image/png')


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local.')
@override_settings(**AISLAMIENTO)
class RecaudosBrowserTests(DatosRecaudosMixin, StaticLiveServerTestCase):
    def test_reportar_desde_movil_y_consulta_operativa_sin_overflow(self):
        self.preparar_recaudos()
        self.receptor = servicios.crear_receptor(entidad='BANCO-SINTETICO', identificador_cuenta='CUENTA-SINTETICA',
            titular='RECEPTOR SINTETICO - NO TRANSFERIR', llave='LLAVE-SINTETICA',
            qr=qr_sintetico_preview(), actor=self.actor)
        self.escanear(self.receptor.qr_id)
        servicios.activar_receptor(receptor_id=self.receptor.pk, actor=self.actor, motivo='Preview sintetico local')
        reporte, _ = self.reportar()
        self.escanear(reporte.comprobante_id)
        evidencia = self.evidencia()
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
            'EDU_BREB_FINANCE': reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk]),
            'EDU_BREB_STATE': reverse('financiacion_educativa_web:reporte-pago', args=[self.solicitud.pk, reporte.pk]),
            'EDU_BREB_DETAIL': reverse('financiacion_educativa_web:operaciones:recaudo-detalle', args=[reporte.pk]),
            'EDU_BREB_EVIDENCE': str(evidencia.pk),
            'EDU_BREB_RECEIPT': str(soporte), 'EDU_BREB_SCREENSHOTS': muestras,
        }
        script = Path(__file__).parent / 'js' / 'test_recaudos_breb_browser.js'
        resultado = subprocess.run(['node', '--test', str(script)], env=env, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=120)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        print(f'Capturas sinteticas BRE-B: {muestras}')
