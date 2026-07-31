from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import EstadoSolicitudFinanciacion
from financiacion_educativa.services.entrega_invitaciones import (
    DjangoEmailInvitationDeliveryBackend,
)
from financiacion_educativa.tests.factories import crear_solicitud


class PublicEducationBrandTests(TestCase):
    def test_landing_uses_aprobado_brand_and_optimized_hero(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Impulsamos tus sue&ntilde;os.')
        self.assertContains(response, 'Apoyamos tu futuro.')
        self.assertContains(response, 'Continuar mi solicitud')
        self.assertContains(response, 'aprobado-estudiantes-campus.webp')
        self.assertContains(response, 'aprobado-estudiantes-campus-mobile.webp')
        self.assertContains(response, 'images/logo.png')
        self.assertContains(response, 'edu-navbar')
        for removed_text in (
            'Tu proceso, con claridad',
            'Datos preparados',
            'Tres momentos para continuar',
            'Recibe tu invitaci&oacute;n',
        ):
            with self.subTest(removed_text=removed_text):
                self.assertNotContains(response, removed_text)
        self.assertNotContains(response, 'FUNDETEC')
        self.assertNotContains(response, 'Libranza')

    def test_login_preserves_referrer_policy_and_aprobado_brand(self):
        response = self.client.get(reverse('account_login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<meta name="referrer" content="same-origin">',
            html=True,
        )
        self.assertContains(response, 'Contin&uacute;a tu financiaci&oacute;n')
        self.assertContains(response, 'edu-auth-page')
        self.assertContains(response, 'css/financiacion_educativa.css')
        self.assertNotContains(response, 'FUNDETEC')
        self.assertNotContains(response, 'Libranza')

    def test_logout_action_is_only_visible_for_authenticated_users(self):
        anonymous_response = self.client.get(reverse('home'))
        self.assertNotContains(anonymous_response, 'Cerrar sesi&oacute;n')

        user = get_user_model().objects.create_user(
            username='navbar@example.com',
            email='navbar@example.com',
            password='ClaveEducativa-2026',
        )
        self.client.force_login(user)
        authenticated_response = self.client.get(reverse('home'))

        self.assertContains(authenticated_response, 'Cerrar sesi&oacute;n', count=2)
        self.assertContains(
            authenticated_response,
            f'action="{reverse("account_logout")}"',
        )

    def test_logout_uses_post_and_returns_to_public_landing(self):
        user = get_user_model().objects.create_user(
            username='logout@example.com',
            email='logout@example.com',
            password='ClaveEducativa-2026',
        )
        self.client.force_login(user)

        get_response = self.client.get(reverse('account_logout'))
        self.assertTrue(get_response.wsgi_request.user.is_authenticated)

        post_response = self.client.post(reverse('account_logout'), follow=True)

        self.assertRedirects(post_response, reverse('home'))
        self.assertFalse(post_response.wsgi_request.user.is_authenticated)
        self.assertNotContains(post_response, 'Cerrar sesi&oacute;n')

    def test_public_legal_pages_remain_in_education_context(self):
        for route_name in ('politica_privacidad', 'terminos_condiciones'):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Aprobado')
                self.assertNotContains(response, 'FUNDETEC')
                self.assertNotContains(response, 'libranza')

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='no-reply@example.com',
        EDUCATION_BRAND_NAME='Aprobado',
    )
    def test_invitation_email_uses_education_brand(self):
        DjangoEmailInvitationDeliveryBackend().deliver(
            recipient='solicitante@example.com',
            continuation_url='https://example.com/continuar/token-seguro/',
            expires_at=timezone.now(),
        )

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn('Aprobado', message.subject)
        self.assertNotIn('FUNDETEC', message.subject)
        self.assertNotIn('FUNDETEC', message.body)
        self.assertNotIn('FUNDETEC', message.alternatives[0].content)


class AssociatedApplicationSummaryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='resumen@example.com',
            email='resumen@example.com',
            password='ClaveEducativa-2026',
        )
        self.solicitud = crear_solicitud(usuario=self.user)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_TERMS
        self.solicitud.save(update_fields=['estado'])
        self.client.force_login(self.user)

    def test_terms_shows_api_data_only_after_secure_association(self):
        response = self.client.get(
            reverse(
                'financiacion_educativa_web:terminos',
                kwargs={'solicitud_id': self.solicitud.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        for value in (
            self.solicitud.nombres,
            self.solicitud.apellidos,
            self.solicitud.correo,
            self.solicitud.celular,
            self.solicitud.direccion,
            self.solicitud.nombre_curso,
            self.solicitud.institucion.nombre_comercial,
            self.solicitud.referencia_externa,
            '12 meses',
            'Recibido de la instituci&oacute;n',
        ):
            with self.subTest(value=value):
                self.assertContains(response, value)
        self.assertNotContains(response, 'FUNDETEC')
        self.assertNotContains(response, 'Libranza')
