from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoInvitacionContinuacion,
    EstadoSolicitudFinanciacion,
    TipoConsentimiento,
)
from financiacion_educativa.models import (
    InvitacionContinuacionSolicitud,
    ParticipanteFinanciacion,
    SolicitudFinanciacionEducativa,
    VersionTerminosFinanciacion,
)
from financiacion_educativa.services.invitaciones import (
    emitir_invitacion_continuacion,
    revocar_invitacion_continuacion,
)
from financiacion_educativa.services.terminos import publicar_version_terminos
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.web.views import SESSION_INVITACION_ID


DATOS_REGISTRO = {
    'email': 'nuevo@example.com',
    'first_name': 'Nuevo',
    'last_name': 'Usuario',
    'password1': 'ClaveEducativa-2026',
    'password2': 'ClaveEducativa-2026',
}


@override_settings(BRAND_PUBLIC_BASE_URL='https://credito.example.com')
class FlujoWebContinuacionTests(TestCase):
    def setUp(self):
        self.solicitud = crear_solicitud()
        self.emitida = emitir_invitacion_continuacion(solicitud=self.solicitud)
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='existente@example.com',
            email='existente@example.com',
            password='ClaveExistente-2026',
        )

    def _url_token(self, token=None):
        return reverse(
            'financiacion_educativa_web:continuar-invitacion',
            kwargs={'token': token or self.emitida.token},
        )

    def _abrir(self):
        return self.client.get(self._url_token())

    def test_enlace_valido_inicia_sin_asociar_y_sin_exponer_datos(self):
        respuesta = self.client.get(self._url_token(), follow=True)
        contenido = respuesta.content.decode()

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            respuesta.redirect_chain,
            [(reverse('financiacion_educativa_web:inicio'), 302)],
        )
        self.assertEqual(respuesta['Cache-Control'], 'max-age=0, no-cache, no-store, must-revalidate, private')
        self.assertEqual(
            self.client.session[SESSION_INVITACION_ID],
            str(self.emitida.invitacion.pk),
        )
        self.assertNotIn(
            self.emitida.token,
            str(dict(self.client.session.items())),
        )
        self.solicitud.refresh_from_db()
        self.assertIsNone(self.solicitud.usuario)
        for sensible in (
            self.solicitud.nombres,
            self.solicitud.apellidos,
            self.solicitud.correo,
            self.solicitud.direccion,
            str(self.solicitud.valor_plan),
            self.solicitud.nombre_curso,
            self.solicitud.institucion.nombre_comercial,
            self.emitida.token,
        ):
            self.assertNotIn(sensible, contenido)

    def test_respuesta_del_token_impide_enviar_referer(self):
        respuesta = self._abrir()

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta['Referrer-Policy'], 'no-referrer')

    def test_tokens_invalidos_muestran_misma_respuesta_generica(self):
        respuestas = [
            self.client.get(self._url_token('token-inexistente')),
            self.client.get(self._url_token(f'{self.emitida.token}x')),
            self.client.get(self._url_token(str(self.solicitud.pk))),
        ]

        self.assertTrue(all(respuesta.status_code == 410 for respuesta in respuestas))
        contenidos = {respuesta.content for respuesta in respuestas}
        self.assertEqual(len(contenidos), 1)

    def test_vencido_y_revocado_son_rechazados(self):
        InvitacionContinuacionSolicitud.objects.filter(
            pk=self.emitida.invitacion.pk
        ).update(vence_en=timezone.now() - timedelta(seconds=1))
        vencida = self.client.get(self._url_token())

        self.emitida = emitir_invitacion_continuacion(solicitud=self.solicitud)
        revocar_invitacion_continuacion(invitacion=self.emitida.invitacion)
        revocada = self.client.get(self._url_token())

        self.assertEqual(vencida.status_code, 410)
        self.assertEqual(revocada.status_code, 410)
        self.assertEqual(vencida.content, revocada.content)

    def test_usuario_existente_inicia_sesion_y_asocia_solicitud(self):
        self._abrir()
        respuesta_login = self.client.post(
            reverse('financiacion_educativa_web:acceso'),
            {
                'username': 'existente@example.com',
                'password': 'ClaveExistente-2026',
            },
        )
        self.assertRedirects(
            respuesta_login,
            reverse('financiacion_educativa_web:confirmar'),
        )

        respuesta_confirmar = self.client.post(
            reverse('financiacion_educativa_web:confirmar')
        )
        self.solicitud.refresh_from_db()
        self.emitida.invitacion.refresh_from_db()

        self.assertEqual(respuesta_confirmar.status_code, 302)
        self.assertEqual(self.solicitud.usuario, self.usuario)
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_TERMS,
        )
        self.assertEqual(
            self.emitida.invitacion.estado,
            EstadoInvitacionContinuacion.CONSUMED,
        )
        self.assertNotIn(SESSION_INVITACION_ID, self.client.session)

    def test_usuario_nuevo_se_registra_sin_crear_duplicados(self):
        self._abrir()
        respuesta = self.client.post(
            reverse('financiacion_educativa_web:registro'),
            DATOS_REGISTRO,
        )

        self.assertRedirects(
            respuesta,
            reverse('financiacion_educativa_web:confirmar'),
        )
        User = get_user_model()
        usuario = User.objects.get(email='nuevo@example.com')
        self.assertTrue(usuario.check_password('ClaveEducativa-2026'))

        otra_solicitud = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia='REF-DUPLICADO',
        )
        otra_emitida = emitir_invitacion_continuacion(solicitud=otra_solicitud)
        self.client.logout()
        self.client.get(self._url_token(otra_emitida.token))
        duplicado = self.client.post(
            reverse('financiacion_educativa_web:registro'),
            DATOS_REGISTRO,
        )

        self.assertEqual(duplicado.status_code, 200)
        self.assertContains(
            duplicado,
            'No fue posible crear la cuenta',
        )
        self.assertEqual(
            User.objects.filter(email__iexact='nuevo@example.com').count(),
            1,
        )

    def test_user_id_enviado_no_cambia_usuario_de_sesion(self):
        self._abrir()
        self.client.force_login(self.usuario)
        otro = get_user_model().objects.create_user(
            username='inyectado@example.com',
            email='inyectado@example.com',
            password='ClaveInyectada-2026',
        )

        self.client.post(
            reverse('financiacion_educativa_web:confirmar'),
            {'user_id': otro.pk},
        )

        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.usuario, self.usuario)

    def test_no_hay_open_redirect_por_next(self):
        self._abrir()
        respuesta = self.client.post(
            (
                f"{reverse('financiacion_educativa_web:acceso')}"
                '?next=https://evil.example/'
            ),
            {
                'username': 'existente@example.com',
                'password': 'ClaveExistente-2026',
                'next': 'https://evil.example/',
            },
        )

        self.assertRedirects(
            respuesta,
            reverse('financiacion_educativa_web:confirmar'),
        )

    def test_vistas_sensibles_exigen_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.get(self._url_token())
        cliente.force_login(self.usuario)
        cliente.get(reverse('financiacion_educativa_web:confirmar'))

        respuesta = cliente.post(
            reverse('financiacion_educativa_web:confirmar')
        )

        self.assertEqual(respuesta.status_code, 403)

    def test_usuario_no_consulta_solicitud_ajena(self):
        self.solicitud.usuario = self.usuario
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_TERMS
        self.solicitud.save(update_fields=['usuario', 'estado'])
        otro = get_user_model().objects.create_user(
            username='otro@example.com',
            email='otro@example.com',
            password='ClaveOtro-2026',
        )
        self.client.force_login(otro)

        respuesta = self.client.get(
            reverse(
                'financiacion_educativa_web:terminos',
                kwargs={'solicitud_id': self.solicitud.pk},
            )
        )

        self.assertEqual(respuesta.status_code, 404)

    def test_aceptacion_web_avanza_al_siguiente_paso(self):
        self.solicitud.usuario = self.usuario
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_TERMS
        self.solicitud.save(update_fields=['usuario', 'estado'])
        version = VersionTerminosFinanciacion.objects.create(
            tipo=TipoConsentimiento.TERMS,
            version='web-fixture-v1',
            titulo='Terminos web fixture',
            contenido='FIXTURE WEB SIN VALIDEZ LEGAL.',
            obligatorio=True,
        )
        publicar_version_terminos(version=version)
        self.client.force_login(self.usuario)
        url = reverse(
            'financiacion_educativa_web:terminos',
            kwargs={'solicitud_id': self.solicitud.pk},
        )
        self.client.get(url)

        respuesta = self.client.post(
            url,
            {'accepted_versions': [str(version.pk)]},
        )

        self.solicitud.refresh_from_db()
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        )
        self.assertRedirects(
            respuesta,
            reverse(
                'financiacion_educativa_web:siguiente',
                kwargs={'solicitud_id': self.solicitud.pk},
            ),
        )
        self.assertFalse(ParticipanteFinanciacion.objects.exists())
