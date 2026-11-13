import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from financiacion_educativa.models_libro import MovimientoLibroEducativo
from financiacion_educativa.models_gobernanza_libro import ControlPilotoLibro
from financiacion_educativa.tests.test_gobernanza_libro import DatosGobierno
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local y Edge headless.')
@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=True)
class PilotoLibroBrowserTests(DatosGobierno, StaticLiveServerTestCase):
    def test_politica_contabilizacion_compensacion_apagado_y_lectura(self):
        propuesta = self.propuesta('PILOTO_VISUAL_V2')
        ingreso = self.nuevo_ingreso()
        operador, aprobador = Client(), Client()
        operador.force_login(self.actor)
        aprobador.force_login(self.aprobador)
        prefijo = 'financiacion_educativa_web:operaciones:'
        env = {**os.environ, 'EDU_PILOT_BASE': self.live_server_url,
            'EDU_PILOT_OPERATOR_SESSION': operador.cookies['sessionid'].value,
            'EDU_PILOT_APPROVER_SESSION': aprobador.cookies['sessionid'].value,
            'EDU_PILOT_PROPOSAL': reverse(prefijo + 'politica-detalle', args=[propuesta.pk]),
            'EDU_PILOT_PREVIEW': reverse(prefijo + 'libro-preview', args=[ingreso.pk]),
            'EDU_PILOT_LEDGER': reverse(prefijo + 'libro-ingreso', args=[ingreso.pk]),
            'EDU_PILOT_CONTROL': reverse(prefijo + 'control-piloto-libro'),
            'EDU_PILOT_INSTITUTION': str(self.institucion.pk), 'EDU_PILOT_OLD_POLICY': str(self.politica.pk)}
        resultado = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_piloto_libro_browser.js')],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        self.assertFalse(ControlPilotoLibro.objects.get(pk=1).activo)
        self.assertEqual(MovimientoLibroEducativo.objects.count(), 2)
