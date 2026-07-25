from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from financiacion_educativa.models import CondicionesFinancieras
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

    def test_resumen_requiere_sesion_y_propiedad(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_usuario_crea_fotografia_explicita_y_ve_plan_cop(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.post(
            self.url,
            {'fecha_inicio_plan': '2026-07-31'},
            follow=True,
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '$1.142.711')
        self.assertContains(respuesta, '$388.547')
        self.assertContains(respuesta, '2026-08-31')
        self.assertEqual(CondicionesFinancieras.objects.filter(activa=True).count(), 1)

    def test_proyeccion_web_no_registra_pago(self):
        self.client.force_login(self.usuario)
        self.client.post(self.url, {'fecha_inicio_plan': '2026-07-31'})
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
                'cuotas_cubiertas': '0',
                'participante_pagante': '',
            },
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Nuevas cuotas proyectadas')
        self.assertEqual(CondicionesFinancieras.objects.count(), 1)
        self.assertEqual(fotografia.cuotas.count(), 3)

    def test_formularios_financieros_exigen_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.usuario)
        respuesta = cliente.post(
            self.url,
            {'fecha_inicio_plan': date(2026, 7, 31).isoformat()},
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_respuestas_no_permiten_cache(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self.url)
        self.assertIn('no-store', respuesta['Cache-Control'])
