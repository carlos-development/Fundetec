from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from financiacion_educativa.admin import SolicitudFinanciacionEducativaAdmin
from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.tests.factories import crear_solicitud


class AccionesInvitacionAdminTests(TestCase):
    def setUp(self):
        self.solicitud = crear_solicitud()
        self.request = SimpleNamespace(user=SimpleNamespace())
        self.admin = SolicitudFinanciacionEducativaAdmin(
            SolicitudFinanciacionEducativa,
            AdminSite(),
        )
        self.usuario_admin = get_user_model().objects.create_user(
            username='admin-acciones@example.com',
            password='Clave-2026',
            is_staff=True,
        )
        content_type = ContentType.objects.get_for_model(
            SolicitudFinanciacionEducativa
        )
        self.usuario_admin.user_permissions.add(
            *Permission.objects.filter(
                content_type=content_type,
                codename__in=(
                    'view_solicitudfinanciacioneducativa',
                    'change_solicitudfinanciacioneducativa',
                ),
            )
        )
        self.client.force_login(self.usuario_admin)
        self.changelist_url = reverse(
            'admin:financiacion_educativa_'
            'solicitudfinanciacioneducativa_changelist'
        )

    def _assert_error_controlado(self, metodo, servicio):
        with (
            patch(servicio, side_effect=ValidationError('Fallo controlado')),
            patch.object(self.admin, 'message_user') as message_user,
        ):
            metodo(self.request, [self.solicitud])

        mensajes = [str(llamada.args[1]) for llamada in message_user.call_args_list]
        self.assertTrue(any(str(self.solicitud.pk) in mensaje for mensaje in mensajes))
        self.assertTrue(any('Fallo controlado' in mensaje for mensaje in mensajes))

    def test_programar_invitacion_informa_validation_error_sin_name_error(self):
        self._assert_error_controlado(
            self.admin.programar_invitacion_inicial_seleccionadas,
            'financiacion_educativa.admin.programar_invitacion_inicial',
        )

    def test_reemitir_invitacion_informa_validation_error_sin_name_error(self):
        self._assert_error_controlado(
            self.admin.reemitir_invitacion_seleccionadas,
            'financiacion_educativa.admin.reemitir_invitacion_orquestada',
        )

    def _assert_error_controlado_por_http(self, accion, servicio):
        with patch(servicio, side_effect=ValidationError('Fallo controlado')) as mock:
            response = self.client.post(
                self.changelist_url,
                {
                    'action': accion,
                    '_selected_action': [str(self.solicitud.pk)],
                    'index': '0',
                },
            )

        self.assertEqual(response.status_code, 302)
        mensajes = [str(mensaje) for mensaje in get_messages(response.wsgi_request)]
        self.assertTrue(any(str(self.solicitud.pk) in mensaje for mensaje in mensajes))
        self.assertTrue(any('Fallo controlado' in mensaje for mensaje in mensajes))
        self.assertFalse(any('NameError' in mensaje for mensaje in mensajes))
        mock.assert_called_once()
        solicitud_enviada = mock.call_args.kwargs['solicitud']
        self.assertEqual(solicitud_enviada.pk, self.solicitud.pk)

    def test_programar_invitacion_controla_validation_error_desde_admin_http(self):
        self._assert_error_controlado_por_http(
            'programar_invitacion_inicial_seleccionadas',
            'financiacion_educativa.admin.programar_invitacion_inicial',
        )

    def test_reemitir_invitacion_controla_validation_error_desde_admin_http(self):
        self._assert_error_controlado_por_http(
            'reemitir_invitacion_seleccionadas',
            'financiacion_educativa.admin.reemitir_invitacion_orquestada',
        )
