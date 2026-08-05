from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from financiacion_educativa.models import (
    CondicionesFinancieras,
    ConfiguracionFinancieraEducativa,
    CuotaAmortizacionEducativa,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_solicitud,
)


class SimuladorFinanciacionEducativaTests(TestCase):
    def setUp(self):
        crear_configuracion_financiera()
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='simulador@example.com',
            email='simulador@example.com',
            password='Clave-2026',
        )
        self.otro_usuario = User.objects.create_user(
            username='otro-simulador@example.com',
            email='otro-simulador@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.pagina_url = reverse(
            'financiacion_educativa_web:simulador',
            kwargs={'solicitud_id': self.solicitud.pk},
        )
        self.calcular_url = reverse(
            'financiacion_educativa_web:simulador-calcular',
            kwargs={'solicitud_id': self.solicitud.pk},
        )

    def assert_sin_persistencia_financiera(self):
        self.assertFalse(CondicionesFinancieras.objects.exists())
        self.assertFalse(CuotaAmortizacionEducativa.objects.exists())

    def test_pagina_requiere_autenticacion_y_propiedad(self):
        self.assertEqual(self.client.get(self.pagina_url).status_code, 302)

        self.client.force_login(self.otro_usuario)
        self.assertEqual(self.client.get(self.pagina_url).status_code, 404)

    def test_pagina_muestra_todos_los_componentes_sin_persistir(self):
        self.client.force_login(self.usuario)

        response = self.client.get(self.pagina_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Simulador de cr&eacute;dito educativo')
        self.assertContains(response, 'Originaci&oacute;n')
        self.assertContains(response, 'IVA sobre originaci&oacute;n')
        self.assertContains(response, 'Figarantias')
        self.assertContains(response, 'SURA')
        self.assertContains(response, 'Capital financiado')
        self.assertContains(response, 'Intereses proyectados')
        self.assertContains(response, 'Cuota mensual estimada')
        self.assertContains(response, 'Total proyectado')
        self.assertContains(response, '1 % mensual')
        self.assertIn('no-store', response['Cache-Control'])
        self.assert_sin_persistencia_financiera()

    def test_recalculo_devuelve_resultado_del_motor_y_no_muta_solicitud(self):
        self.client.force_login(self.usuario)

        response = self.client.post(
            self.calcular_url,
            {'monto_solicitado': '1000000', 'plazo_meses': '3'},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()['simulation']
        self.assertEqual(data['monto_solicitado'], '1000000')
        self.assertEqual(data['valor_originacion'], '100000')
        self.assertEqual(data['valor_iva_originacion'], '19000')
        self.assertEqual(data['valor_fondo_garantias'], '20000')
        self.assertEqual(data['valor_seguro_vida'], '3711')
        self.assertEqual(data['capital_total_financiado'], '1142711')
        self.assertEqual(data['cuota_informativa'], '388547')
        self.assertEqual(data['intereses_totales'], '22930')
        self.assertEqual(data['total_proyectado'], '1165641')
        self.assertEqual(data['tasa_interes_mensual'], '1.000000')
        self.assertEqual(data['metodo_calculo'], 'FRENCH_AMORTIZATION')

        self.solicitud.refresh_from_db()
        self.assertEqual(str(self.solicitud.valor_plan), '1000000.00')
        self.assertEqual(self.solicitud.plazo_meses, 12)
        self.assert_sin_persistencia_financiera()

    def test_recalculo_rechaza_datos_invalidos_sin_cambios_parciales(self):
        self.client.force_login(self.usuario)

        amount_response = self.client.post(
            self.calcular_url,
            {'monto_solicitado': '0', 'plazo_meses': '12'},
        )
        term_response = self.client.post(
            self.calcular_url,
            {'monto_solicitado': '1000000', 'plazo_meses': '121'},
        )

        self.assertEqual(amount_response.status_code, 400)
        self.assertIn('monto_solicitado', amount_response.json()['fields'])
        self.assertEqual(term_response.status_code, 400)
        self.assertIn('plazo_meses', term_response.json()['fields'])
        self.assert_sin_persistencia_financiera()

    def test_recalculo_exige_post_csrf_y_propiedad(self):
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(self.calcular_url).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.usuario)
        csrf_response = csrf_client.post(
            self.calcular_url,
            {'monto_solicitado': '1000000', 'plazo_meses': '3'},
        )
        self.assertEqual(csrf_response.status_code, 403)

        other_client = Client()
        other_client.force_login(self.otro_usuario)
        idor_response = other_client.post(
            self.calcular_url,
            {'monto_solicitado': '1000000', 'plazo_meses': '3'},
        )
        self.assertEqual(idor_response.status_code, 404)
        self.assert_sin_persistencia_financiera()

    def test_politica_no_disponible_falla_de_forma_controlada(self):
        ConfiguracionFinancieraEducativa.objects.all().delete()
        self.client.force_login(self.usuario)

        page_response = self.client.get(self.pagina_url)
        api_response = self.client.post(
            self.calcular_url,
            {'monto_solicitado': '1000000', 'plazo_meses': '3'},
        )

        self.assertContains(
            page_response,
            'No hay una politica financiera educativa activa.',
        )
        self.assertEqual(api_response.status_code, 503)
        self.assertNotIn('fields', api_response.json())
        self.assert_sin_persistencia_financiera()

    def test_plazo_institucional_fuera_del_rango_no_dispara_calculo(self):
        self.solicitud.plazo_meses = 121
        self.solicitud.save(update_fields=['plazo_meses'])
        self.client.force_login(self.usuario)

        response = self.client.get(self.pagina_url)

        self.assertContains(
            response,
            'Los datos institucionales no estan dentro del rango del simulador.',
        )
        self.assert_sin_persistencia_financiera()
