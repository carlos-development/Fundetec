from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from financiacion_educativa.choices import (
    EstadoInvitacionContinuacion,
    EstadoSolicitudFinanciacion as Estado,
)
from financiacion_educativa.services.asociacion import (
    asociar_usuario_mediante_invitacion,
)
from financiacion_educativa.services.invitaciones import (
    emitir_invitacion_continuacion,
)
from financiacion_educativa.services.reanudacion import (
    MAPA_REANUDACION,
    resolver_url_reanudacion,
)
from financiacion_educativa.tests.factories import crear_institucion, crear_solicitud


class ReanudacionSolicitudesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='reanudar@example.com',
            email='reanudar@example.com',
            password='Clave-Reanudar-2026',
        )
        self.otro_usuario = User.objects.create_user(
            username='otro-reanudar@example.com',
            email='otro-reanudar@example.com',
            password='Clave-Reanudar-2026',
        )
        self.institucion = crear_institucion('91')
        self.url = reverse('financiacion_educativa_web:reanudar-solicitudes')

    def _solicitud(self, estado, *, referencia='REF-REANUDAR', usuario=None):
        solicitud = crear_solicitud(
            institucion=self.institucion,
            referencia=referencia,
            usuario=usuario or self.usuario,
        )
        solicitud.estado = estado
        solicitud.save(update_fields=['estado'])
        return solicitud

    def test_mapa_cubre_y_redirige_cada_estado_real(self):
        self.assertEqual(set(MAPA_REANUDACION), set(Estado.values))
        self.client.force_login(self.usuario)
        for index, estado in enumerate(Estado.values):
            with self.subTest(estado=estado):
                solicitud = self._solicitud(
                    estado,
                    referencia=f'REF-ESTADO-{index}',
                )
                response = self.client.get(self.url)
                self.assertRedirects(
                    response,
                    resolver_url_reanudacion(solicitud),
                )
                solicitud.delete()

    def test_usuario_sin_solicitudes_recibe_pantalla_informativa(self):
        self.client.force_login(self.usuario)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No tienes solicitudes asociadas')

    def test_reanudador_anonimo_envia_al_login_con_next_local(self):
        response = self.client.get(self.url)

        self.assertRedirects(
            response,
            f'{reverse("account_login")}?next={self.url}',
            fetch_redirect_response=False,
        )

    def test_varias_solicitudes_activas_muestran_eleccion_segura(self):
        primera = self._solicitud(Estado.PENDING_TERMS, referencia='REF-UNO')
        segunda = self._solicitud(Estado.PENDING_DOCUMENT, referencia='REF-DOS')
        ajena = self._solicitud(
            Estado.PENDING_DOCUMENT,
            referencia='REFERENCIA-SECRETA',
            usuario=self.otro_usuario,
        )
        ajena.nombre_curso = 'CURSO SECRETO AJENO'
        ajena.save(update_fields=['nombre_curso'])
        self.client.force_login(self.usuario)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, resolver_url_reanudacion(primera))
        self.assertContains(response, resolver_url_reanudacion(segunda))
        self.assertNotContains(response, 'CURSO SECRETO AJENO')
        self.assertNotContains(response, 'REFERENCIA-SECRETA')

    def test_correo_coincidente_sin_asociacion_no_es_suficiente(self):
        crear_solicitud(
            referencia='REF-SIN-ASOCIAR',
            correo=self.usuario.email,
        )
        self.client.force_login(self.usuario)

        response = self.client.get(self.url)

        self.assertContains(response, 'No tienes solicitudes asociadas')

    def test_invitacion_consumida_no_impide_reanudar_desde_la_cuenta(self):
        solicitud = crear_solicitud(
            referencia='REF-INVITACION-CONSUMIDA',
            correo=self.usuario.email,
        )
        emitida = emitir_invitacion_continuacion(solicitud=solicitud)
        asociar_usuario_mediante_invitacion(
            invitacion_id=emitida.invitacion.pk,
            usuario=self.usuario,
        )
        emitida.invitacion.refresh_from_db()
        solicitud.refresh_from_db()
        self.client.force_login(self.usuario)

        response = self.client.get(self.url)

        self.assertEqual(
            emitida.invitacion.estado,
            EstadoInvitacionContinuacion.CONSUMED,
        )
        self.assertRedirects(
            response,
            resolver_url_reanudacion(solicitud),
            fetch_redirect_response=False,
        )

    def test_vista_de_estado_rechaza_idor(self):
        solicitud = self._solicitud(
            Estado.PENDING_MANUAL_REVIEW,
            usuario=self.otro_usuario,
        )
        self.client.force_login(self.usuario)

        response = self.client.get(resolver_url_reanudacion(solicitud))

        self.assertEqual(response.status_code, 404)
