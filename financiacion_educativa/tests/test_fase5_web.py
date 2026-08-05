from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from financiacion_educativa.models import CondicionesFinancieras
from financiacion_educativa.models import ConfiguracionFinancieraEducativa
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_solicitud,
)


class InterfazFinancieraEducativaTests(TestCase):
    def setUp(self):
        crear_configuracion_financiera()
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='finanzas@example.com',
            email='finanzas@example.com',
            password='Clave-2026',
        )
        self.otro = User.objects.create_user(
            username='finanzas-otro@example.com',
            email='finanzas-otro@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.solicitud.plazo_meses = 3
        self.solicitud.save(update_fields=['plazo_meses'])
        self.url = reverse(
            'financiacion_educativa_web:finanzas',
            kwargs={'solicitud_id': self.solicitud.pk},
        )

    def _crear_fotografia(self):
        return crear_fotografia_condiciones_financieras(
            self.solicitud,
            fecha_inicio_plan=date(2026, 7, 31),
            actor=self.usuario,
        )

    def test_resumen_requiere_sesion_y_propiedad(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_usuario_ve_fotografia_contractual_existente_y_plan_cop(self):
        self._crear_fotografia()
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '$1.142.711')
        self.assertContains(respuesta, '$388.547')
        self.assertContains(respuesta, '2026-08-31')
        self.assertContains(respuesta, '10 %')
        self.assertContains(respuesta, '19 %')
        self.assertContains(respuesta, '2 %')
        self.assertContains(respuesta, '0,3711 %')
        self.assertNotContains(respuesta, '10,000000 %')
        self.assertEqual(CondicionesFinancieras.objects.filter(activa=True).count(), 1)

    def test_usuario_no_puede_crear_condiciones_desde_la_pantalla(self):
        ConfiguracionFinancieraEducativa.objects.all().delete()
        self.client.force_login(self.usuario)

        respuesta = self.client.get(self.url)
        post = self.client.post(
            self.url,
            {'fecha_inicio_plan': '2026-07-31'},
        )

        self.assertContains(respuesta, 'Condiciones definitivas pendientes')
        self.assertEqual(post.status_code, 405)
        self.assertFalse(CondicionesFinancieras.objects.exists())

    def test_comando_activa_politica_visible_sin_reiniciar_servidor(self):
        ConfiguracionFinancieraEducativa.objects.all().delete()
        self.client.force_login(self.usuario)
        simulator_url = reverse(
            'financiacion_educativa_web:simulador',
            kwargs={'solicitud_id': self.solicitud.pk},
        )
        sin_politica = self.client.get(simulator_url)
        self.assertContains(sin_politica, 'No hay una politica financiera educativa activa')

        call_command(
            'configurar_politica_financiera_educativa',
            vigente_desde='2026-01-01',
            activate=True,
            stdout=StringIO(),
        )
        con_politica = self.client.get(simulator_url)

        self.assertNotContains(
            con_politica,
            'No hay una politica financiera educativa activa',
        )
        self.assertFalse(CondicionesFinancieras.objects.exists())

    def test_proyeccion_web_no_registra_pago(self):
        self.client.force_login(self.usuario)
        self._crear_fotografia()
        fotografia = CondicionesFinancieras.objects.get(activa=True)
        url = reverse(
            'financiacion_educativa_web:proyectar-abono',
            kwargs={'solicitud_id': self.solicitud.pk},
        )
        respuesta = self.client.post(
            url,
            {
                'valor_pago': '500000',
                'fecha_efectiva': '2026-08-15',
            },
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Cuotas pendientes despu')
        self.assertContains(respuesta, 'Esta es una simulaci')
        self.assertContains(respuesta, 'Aplicado directamente a capital')
        self.assertContains(respuesta, 'Proyeccion calculada')
        self.assertContains(respuesta, 'no registra ningun pago')
        self.assertEqual(CondicionesFinancieras.objects.count(), 1)
        self.assertEqual(fotografia.cuotas.count(), 3)

    def test_resumen_financiero_no_admite_post(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.post(
            self.url,
            {'fecha_inicio_plan': '2026-07-31'},
        )
        self.assertEqual(respuesta.status_code, 405)

    def test_proyecciones_rechazan_get_csrf_e_idor(self):
        self.client.force_login(self.usuario)
        self._crear_fotografia()
        url = reverse(
            'financiacion_educativa_web:proyectar-abono',
            kwargs={'solicitud_id': self.solicitud.pk},
        )
        self.assertEqual(self.client.get(url).status_code, 405)

        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.usuario)
        self.assertEqual(
            csrf.post(
                url,
                {
                    'valor_pago': '500000',
                    'fecha_efectiva': '2026-08-15',
                },
            ).status_code,
            403,
        )

        ajeno = Client()
        ajeno.force_login(self.otro)
        self.assertEqual(
            ajeno.post(
                url,
                {
                    'valor_pago': '500000',
                    'fecha_efectiva': '2026-08-15',
                },
            ).status_code,
            404,
        )

    def test_proyeccion_web_rechaza_valor_sin_abono_real_a_capital(self):
        self.client.force_login(self.usuario)
        self._crear_fotografia()
        respuesta = self.client.post(
            reverse(
                'financiacion_educativa_web:proyectar-abono',
                kwargs={'solicitud_id': self.solicitud.pk},
            ),
            {
                'valor_pago': '1000',
                'fecha_efectiva': '2026-08-15',
            },
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(
            respuesta,
            'debe ser superior a los intereses y conceptos causados',
        )

    def test_respuestas_no_permiten_cache(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        self.assertIn('no-store', respuesta['Cache-Control'])
