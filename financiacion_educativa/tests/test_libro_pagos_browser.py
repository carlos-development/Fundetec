import os
from pathlib import Path
import subprocess
from unittest import skipUnless
from unittest.mock import patch
from datetime import date

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from financiacion_educativa.tests.test_libro_pagos import DatosLibro
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local para capturas.')
@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=True)
class LibroBrowserTests(DatosLibro, StaticLiveServerTestCase):
    def test_recorrido_libro_y_previews_sin_javascript(self):
        ingreso = self.nuevo_ingreso(self.cuota.capital // 2)
        from financiacion_educativa.models_libro import PoliticaLibroEducativo
        from financiacion_educativa.services.libro_pagos import REGLAS_SINTETICAS
        from financiacion_educativa.tests.piloto_libro_helpers import politica_sintetica
        self.politica = politica_sintetica(self.institucion, self.actor, self.aprobador,
            'SYNTHETIC_CAPITAL_FIRST', date(2026, 7, 1),
            reglas={**REGLAS_SINTETICAS, 'imputacion': 'CAPITAL_FIRST'}, reemplaza_id=self.politica.pk)
        movimiento = self.registrar(ingreso)
        completa = self.nuevo_ingreso(self.cuota.valor_cuota - movimiento.importe)
        capital = self.nuevo_ingreso('400000')
        total = self.nuevo_ingreso('2000000')
        cliente = Client()
        cliente.force_login(self.actor)
        prefijo = 'financiacion_educativa_web:operaciones:'
        rutas = {
            '01-libro': reverse(prefijo + 'libro-ingreso', args=[ingreso.pk]),
            '02-cuota-completa': reverse(prefijo + 'libro-preview', args=[completa.pk]) + '?modo=FULL',
            '03-parcial': reverse(prefijo + 'libro-preview', args=[capital.pk]) + '?modo=PARTIAL',
            '04-abono-capital': reverse(prefijo + 'libro-preview', args=[capital.pk]) + '?modo=PRINCIPAL',
            '05-pago-total': reverse(prefijo + 'libro-preview', args=[total.pk]) + '?modo=PAYOFF',
            '06-tablero': reverse(prefijo + 'tablero-recaudos') + '?desde=2026-07-01&hasta=2026-07-31',
            '07-seguimiento': reverse(prefijo + 'seguimiento-detalle', args=[self.solicitud.pk]),
        }
        with patch('django.utils.timezone.localdate', return_value=date(2026, 7, 1)):
            self.solicitud, self.fotografia, _, _ = self.contrato('LIBRO-PROYECCION-SINTETICA')
        self.solicitud.usuario, self.solicitud.correo = self.pagador, self.pagador.email
        self.solicitud.save(update_fields=['usuario', 'correo'])
        self.registrar(self.nuevo_ingreso('400000'), 'PRINCIPAL')
        rutas['08-proyeccion-contabilizada'] = reverse(prefijo + 'seguimiento-detalle', args=[self.solicitud.pk])
        import json
        env = {**os.environ, 'EDU_LEDGER_BASE': self.live_server_url,
            'EDU_LEDGER_SESSION': cliente.cookies['sessionid'].value, 'EDU_LEDGER_PATHS': json.dumps(rutas)}
        resultado = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_libro_pagos_browser.js')],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
