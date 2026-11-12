import os
from pathlib import Path
import subprocess
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.services import aplicacion_cuotas
from financiacion_educativa.tests import test_recaudos_breb as base
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.test_ingreso_operaciones import DatosIngresoOperaciones


@skipUnless(os.environ.get('EDU_PLAYWRIGHT_MODULE'), 'Requiere Playwright local para capturas.')
@override_settings(**base.AISLAMIENTO)
class PortalEstudianteBrowserTests(DatosIngresoOperaciones, StaticLiveServerTestCase):
    def setUp(self):
        hoy = timezone.localdate()
        with patch('django.utils.timezone.localdate', return_value=hoy - timedelta(days=90)):
            super().setUp()
        self.datos['fecha'] = hoy

    def test_recorrido_real_sin_javascript_movil_y_escritorio(self):
        pendiente = crear_solicitud(institucion=self.institucion, usuario=self.pagador, referencia='PREVIEW-STUDENT-PENDING')
        pendiente.nombre_curso = 'Preparacion Pruebas Saber'
        pendiente.save(update_fields=['nombre_curso'])
        ingreso, _ = self.registrar(referencia='PREVIEW-APPLIED')
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        aplicacion_cuotas.aplicar_cuota(reporte_id=ingreso.reporte_id, cuota_id=cuota.pk,
            actor=self.actor, vista_previa=token, confirmar_cuota=True)
        confirmado, _ = self.registrar(clave=uuid4(), referencia='PREVIEW-CONFIRMED')
        cliente = Client()
        cliente.force_login(self.pagador)
        operador = Client()
        operador.force_login(self.actor)
        output = Path(os.environ['EDU_PORTAL_SCREENSHOTS'])
        output.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            'EDU_PORTAL_BASE': self.live_server_url,
            'EDU_PORTAL_SESSION': cliente.cookies['sessionid'].value,
            'EDU_PORTAL_OTHER_SESSION': operador.cookies['sessionid'].value,
            'EDU_PORTAL_LIST': reverse('financiacion_educativa_web:reanudar-solicitudes'),
            'EDU_PORTAL_FINANCE': reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk]),
            'EDU_PORTAL_BANK': reverse('financiacion_educativa_web:operaciones:recaudo-soporte', args=[confirmado.reporte_id]) + '?banco=1',
            'EDU_PORTAL_OPERATOR': self.actor.username,
            'EDU_PORTAL_BANK_REFERENCE': confirmado.referencia_verificada,
        }
        result = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_portal_estudiante_browser.js')],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print('Portal estudiante: navegador sin JavaScript OK; 375/390/1440 px.')
