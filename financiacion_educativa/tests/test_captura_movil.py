from datetime import date, timedelta
from unittest import mock
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEnlaceCapturaMovil,
    EstadoEntregaCapturaMovil,
    EstadoOutboxCorreoEducativo,
    EstadoSolicitudFinanciacion,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import EnlaceCapturaMovil, OutboxCorreoEducativo
from financiacion_educativa.services.captura_movil import (
    emitir_enlace_captura_movil,
)
from financiacion_educativa.services.outbox_correos import procesar_siguiente_correo
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.tests.delivery_backends import (
    RecordingMobileCaptureDeliveryBackend,
)
from financiacion_educativa.tests.factories import crear_solicitud


BACKEND_GRABADOR = (
    'financiacion_educativa.tests.delivery_backends.'
    'RecordingMobileCaptureDeliveryBackend'
)
BACKEND_FALLIDO = (
    'financiacion_educativa.tests.delivery_backends.'
    'FailingMobileCaptureDeliveryBackend'
)
BACKEND_DJANGO = (
    'financiacion_educativa.services.captura_movil.'
    'DjangoEmailMobileCaptureDeliveryBackend'
)
MOBILE_UA = (
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) '
    'AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36'
)
IPHONE_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) '
    'AppleWebKit/605.1.15 Version/18.5 Mobile/15E148 Safari/604.1'
)
IPAD_DESKTOP_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) '
    'AppleWebKit/605.1.15 Version/18.5 Safari/605.1.15'
)


def jpeg(nombre='captura.jpg', marca=b'movil'):
    return SimpleUploadedFile(
        nombre,
        b'\xff\xd8\xff' + marca + b'\xff\xd9',
        content_type='image/jpeg',
    )


@override_settings(
    BRAND_PUBLIC_BASE_URL='https://credito.example.com',
    FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_DELIVERY_BACKEND=BACKEND_GRABADOR,
    FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_COOLDOWN_SECONDS=0,
)
class CapturaMovilTests(TestCase):
    def setUp(self):
        RecordingMobileCaptureDeliveryBackend.reset()
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='movil@example.com',
            email='movil@example.com',
            password='Clave-2026',
        )
        self.otro = User.objects.create_user(
            username='otro-movil@example.com',
            email='otro-movil@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['estado'])
        self.estudiante = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='Persona',
                apellidos='Movil',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='1000200030',
                fecha_nacimiento=date(1990, 1, 1),
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles={
                RolParticipante.STUDENT,
                RolParticipante.PRINCIPAL_DEBTOR,
            },
        )
        self.url_envio = reverse(
            'financiacion_educativa_web:captura-movil-enviar',
            kwargs={
                'solicitud_id': self.solicitud.pk,
                'persona': 'estudiante',
            },
        )

    def _emitir_desde_web(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.post(self.url_envio)
        procesar_siguiente_correo()
        return respuesta

    def _token_y_ruta(self, indice=-1):
        entrega = RecordingMobileCaptureDeliveryBackend.deliveries[-1]
        partes = urlparse(entrega['continuation_url'])
        return partes.fragment, partes.path

    def test_emite_enlace_sin_persistir_token_url_o_correo(self):
        respuesta = self._emitir_desde_web()

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(len(RecordingMobileCaptureDeliveryBackend.deliveries), 1)
        entrega = RecordingMobileCaptureDeliveryBackend.deliveries[0]
        self.assertEqual(entrega['recipient'], self.solicitud.correo)
        enlace = EnlaceCapturaMovil.objects.get()
        partes = urlparse(entrega['continuation_url'])
        token = partes.fragment
        self.assertNotIn(str(self.solicitud.pk), entrega['continuation_url'])
        self.assertNotIn(token, partes.path)
        self.assertNotEqual(enlace.token_hash, token)
        self.assertNotIn(token, repr(enlace))
        self.assertNotEqual(enlace.destinatario_hmac, self.solicitud.correo)
        self.assertEqual(enlace.estado_entrega, EstadoEntregaCapturaMovil.SENT)
        self.assertNotIn(
            self.solicitud.correo,
            str(list(enlace.eventos.values_list('metadata', flat=True))),
        )

    def test_enlace_exige_login_se_consume_una_vez_y_abre_camara(self):
        self._emitir_desde_web()
        token, ruta = self._token_y_ruta()
        movil = Client(HTTP_USER_AGENT=MOBILE_UA)

        handoff = movil.get(ruta)
        self.assertEqual(handoff.status_code, 200)
        self.assertNotContains(handoff, token)
        entrada = movil.post(ruta, {'token': token})
        self.assertEqual(entrada.status_code, 302)
        self.assertEqual(
            entrada.wsgi_request.sensitive_post_parameters,
            ('token',),
        )
        self.assertTrue(entrada.url.startswith('/accounts/login/'))
        self.assertNotIn(ruta, entrada.url)

        movil.force_login(self.usuario)
        continuar = movil.get(
            reverse('financiacion_educativa_web:captura-movil-continuar')
        )
        self.assertEqual(continuar.status_code, 302)
        self.assertEqual(
            continuar.url,
            reverse(
                'financiacion_educativa_web:capturar-identidad',
                kwargs={
                    'solicitud_id': self.solicitud.pk,
                    'persona': 'estudiante',
                },
            ),
        )
        enlace = EnlaceCapturaMovil.objects.get()
        self.assertEqual(enlace.estado, EstadoEnlaceCapturaMovil.CONSUMED)
        self.assertEqual(enlace.consumida_por, self.usuario)
        self.assertEqual(
            movil.post(ruta, {'token': token}).status_code,
            410,
        )

    def test_senales_mobiles_explicitas_entran_al_handoff(self):
        escenarios = (
            {'HTTP_USER_AGENT': IPHONE_UA},
            {'HTTP_USER_AGENT': MOBILE_UA},
            {
                'HTTP_USER_AGENT': (
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
                ),
                'HTTP_SEC_CH_UA_MOBILE': '?1',
            },
            {
                'HTTP_USER_AGENT': IPHONE_UA,
                'HTTP_SEC_CH_UA_MOBILE': '?0',
            },
        )
        for indice, headers in enumerate(escenarios):
            with self.subTest(indice=indice):
                self._emitir_desde_web()
                token, ruta = self._token_y_ruta()
                cliente = Client(**headers)
                cliente.get(ruta)

                respuesta = cliente.post(ruta, {'token': token})

                self.assertEqual(respuesta.status_code, 302)
                self.assertTrue(
                    respuesta.url.startswith('/accounts/login/')
                )

    def test_bootstrap_apple_tactil_es_firmado_temporal_y_conserva_grant(self):
        self._emitir_desde_web()
        token, ruta = self._token_y_ruta()
        ipad = Client(HTTP_USER_AGENT=IPAD_DESKTOP_UA)
        handoff = ipad.get(ruta)
        marcador = handoff.context['mobile_context_bootstrap']

        self.assertEqual(
            ipad.post(ruta, {'token': token}).status_code,
            400,
        )
        self.assertEqual(
            ipad.post(
                ruta,
                {
                    'token': token,
                    'mobile_context_kind': 'apple-touch',
                    'mobile_context_bootstrap': f'{marcador}alterado',
                },
            ).status_code,
            400,
        )
        with mock.patch(
            'financiacion_educativa.web.views.timezone.now',
            return_value=timezone.now() + timedelta(minutes=6),
        ):
            expirada = ipad.post(
                ruta,
                {
                    'token': token,
                    'mobile_context_kind': 'apple-touch',
                    'mobile_context_bootstrap': marcador,
                },
            )
        self.assertEqual(expirada.status_code, 400)

        entrada = ipad.post(
            ruta,
            {
                'token': token,
                'mobile_context_kind': 'apple-touch',
                'mobile_context_bootstrap': marcador,
            },
        )
        self.assertEqual(entrada.status_code, 302)
        ipad.force_login(self.usuario)
        continuar = ipad.get(
            reverse('financiacion_educativa_web:captura-movil-continuar')
        )
        self.assertEqual(continuar.status_code, 302)
        captura = ipad.get(continuar.url)
        self.assertContains(captura, 'data-identity-camera')

    def test_token_alterado_vencido_y_usuario_incorrecto_no_dan_acceso(self):
        self._emitir_desde_web()
        token, ruta = self._token_y_ruta()
        movil = Client(HTTP_USER_AGENT=MOBILE_UA)
        self.assertEqual(
            movil.post(ruta, {'token': f'{token}x'}).status_code,
            410,
        )

        enlace = EnlaceCapturaMovil.objects.get()
        enlace.vence_en = timezone.now() - timedelta(seconds=1)
        enlace.save(update_fields=['vence_en', 'actualizada_en'])
        self.assertEqual(
            movil.post(ruta, {'token': token}).status_code,
            410,
        )

        enlace.vence_en = timezone.now() + timedelta(minutes=5)
        enlace.save(update_fields=['vence_en', 'actualizada_en'])
        ajeno = Client(HTTP_USER_AGENT=MOBILE_UA)
        self.assertEqual(
            ajeno.post(ruta, {'token': token}).status_code,
            302,
        )
        ajeno.force_login(self.otro)
        self.assertEqual(
            ajeno.get(
                reverse('financiacion_educativa_web:captura-movil-continuar')
            ).status_code,
            410,
        )
        enlace.refresh_from_db()
        self.assertEqual(enlace.estado, EstadoEnlaceCapturaMovil.ACTIVE)

    def test_grant_movil_no_autoriza_otra_solicitud_del_mismo_usuario(self):
        self._emitir_desde_web()
        token, ruta = self._token_y_ruta()
        movil = Client(HTTP_USER_AGENT=MOBILE_UA)
        self.assertEqual(movil.post(ruta, {'token': token}).status_code, 302)
        movil.force_login(self.usuario)
        self.assertEqual(
            movil.get(
                reverse('financiacion_educativa_web:captura-movil-continuar')
            ).status_code,
            302,
        )

        otra = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia='CAPTURA-OTRA-SOLICITUD',
            usuario=self.usuario,
        )
        otra.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        otra.save(update_fields=['estado'])
        registrar_o_actualizar_participante(
            solicitud=otra,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='Otra',
                apellidos='Persona',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='1000200099',
                fecha_nacimiento=date(1990, 1, 1),
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles={
                RolParticipante.STUDENT,
                RolParticipante.PRINCIPAL_DEBTOR,
            },
        )
        url_otra = reverse(
            'financiacion_educativa_web:capturar-identidad',
            kwargs={
                'solicitud_id': otra.pk,
                'persona': 'estudiante',
            },
        )

        respuesta = movil.post(
            url_otra,
            {'lado': 'frente', 'captura': jpeg(marca=b'otra')},
        )

        self.assertEqual(respuesta.status_code, 404)
        self.assertFalse(otra.documentos.exists())

    def test_reemision_revoca_enlace_anterior_y_deja_solo_uno_activo(self):
        with self.captureOnCommitCallbacks(execute=True):
            primero = emitir_enlace_captura_movil(
                solicitud=self.solicitud,
                persona='estudiante',
                actor=self.usuario,
            )
        with self.captureOnCommitCallbacks(execute=True):
            segundo = emitir_enlace_captura_movil(
                solicitud=self.solicitud,
                persona='estudiante',
                actor=self.usuario,
            )

        primero.enlace.refresh_from_db()
        self.assertEqual(primero.enlace.estado, EstadoEnlaceCapturaMovil.REVOKED)
        self.assertEqual(
            EnlaceCapturaMovil.objects.filter(
                estado=EstadoEnlaceCapturaMovil.ACTIVE
            ).count(),
            1,
        )
        ruta = urlparse(primero.url).path
        self.assertEqual(
            self.client.post(
                ruta,
                {'token': urlparse(primero.url).fragment},
                HTTP_USER_AGENT=MOBILE_UA,
            ).status_code,
            410,
        )
        self.assertEqual(
            self.client.post(
                ruta,
                {'token': urlparse(segundo.url).fragment},
                HTTP_USER_AGENT=MOBILE_UA,
            ).status_code,
            302,
        )

    @override_settings(
        FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_DELIVERY_BACKEND=BACKEND_FALLIDO
    )
    def test_fallo_de_correo_no_convierte_post_en_error(self):
        estado_inicial = self.solicitud.estado
        self.client.force_login(self.usuario)
        respuesta = self.client.post(self.url_envio)
        self.assertEqual(respuesta.status_code, 302)
        procesar_siguiente_correo()

        enlace = EnlaceCapturaMovil.objects.get()
        self.assertEqual(enlace.estado_entrega, EstadoEntregaCapturaMovil.FAILED)
        self.assertEqual(enlace.estado, EstadoEnlaceCapturaMovil.REVOKED)
        self.assertEqual(
            enlace.codigo_ultimo_error,
            'SMTP_DELIVERY_AMBIGUOUS',
        )
        self.assertFalse(
            EnlaceCapturaMovil.objects.filter(
                estado=EstadoEnlaceCapturaMovil.ACTIVE
            ).exists()
        )
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, estado_inicial)
        textos = [str(mensaje) for mensaje in get_messages(respuesta.wsgi_request)]
        self.assertTrue(any('Programamos el envio' in texto for texto in textos))

    def test_post_solo_programa_entrega_persistente(self):
        estado_inicial = self.solicitud.estado
        self.client.force_login(self.usuario)

        respuesta = self.client.post(self.url_envio)

        enlace = EnlaceCapturaMovil.objects.get()
        self.assertEqual(enlace.estado_entrega, EstadoEntregaCapturaMovil.PENDING)
        self.assertEqual(
            OutboxCorreoEducativo.objects.get().estado,
            EstadoOutboxCorreoEducativo.PENDING,
        )
        textos = [str(mensaje) for mensaje in get_messages(respuesta.wsgi_request)]
        self.assertTrue(any('Programamos el envio' in texto for texto in textos))
        self.assertFalse(RecordingMobileCaptureDeliveryBackend.deliveries)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, estado_inicial)

    def test_envio_es_post_csrf_y_propiedad_sin_idor(self):
        anonimo = Client()
        self.assertEqual(anonimo.post(self.url_envio).status_code, 302)
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(self.url_envio).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.usuario)
        self.assertEqual(csrf.post(self.url_envio).status_code, 403)
        ajeno = Client()
        ajeno.force_login(self.otro)
        self.assertEqual(ajeno.post(self.url_envio).status_code, 404)

        self._emitir_desde_web()
        self.assertEqual(
            RecordingMobileCaptureDeliveryBackend.deliveries[-1]['recipient'],
            self.solicitud.correo,
        )
        token, ruta = self._token_y_ruta()
        handoff_csrf = Client(
            enforce_csrf_checks=True,
            HTTP_USER_AGENT=MOBILE_UA,
        )
        handoff_csrf.get(ruta)
        self.assertEqual(
            handoff_csrf.post(ruta, {'token': token}).status_code,
            403,
        )
        csrf_token = handoff_csrf.cookies['csrftoken'].value
        self.assertEqual(
            handoff_csrf.post(
                ruta,
                {'token': token},
                HTTP_X_CSRFTOKEN=csrf_token,
            ).status_code,
            302,
        )

    def test_no_admite_destinatario_arbitrario_y_muestra_el_boton_correcto(self):
        self.client.force_login(self.usuario)
        captura_url = reverse(
            'financiacion_educativa_web:capturar-identidad',
            kwargs={
                'solicitud_id': self.solicitud.pk,
                'persona': 'estudiante',
            },
        )
        pagina = self.client.get(captura_url)
        self.assertContains(pagina, 'Enviar enlace a mi correo')
        self.assertNotContains(pagina, 'data-camera-capture')
        self.assertNotContains(pagina, 'Activar c&aacute;mara')

        self.client.post(
            self.url_envio,
            {'destinatario': 'atacante@example.com'},
        )
        procesar_siguiente_correo()
        self.assertEqual(
            RecordingMobileCaptureDeliveryBackend.deliveries[-1]['recipient'],
            self.solicitud.correo,
        )

    @override_settings(
        FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_REISSUE_LIMIT=2,
        FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_REISSUE_WINDOW_HOURS=1,
    )
    def test_respeta_limite_de_emisiones(self):
        for _indice in range(2):
            with self.captureOnCommitCallbacks(execute=True):
                emitir_enlace_captura_movil(
                    solicitud=self.solicitud,
                    persona='estudiante',
                    actor=self.usuario,
                )

        with self.assertRaises(ValidationError):
            emitir_enlace_captura_movil(
                solicitud=self.solicitud,
                persona='estudiante',
                actor=self.usuario,
            )

    def test_captura_movil_actualiza_el_estado_visible_en_computador(self):
        self._emitir_desde_web()
        movil = Client(HTTP_USER_AGENT=MOBILE_UA)
        token, ruta = self._token_y_ruta()
        movil.get(ruta)
        movil.post(ruta, {'token': token})
        movil.force_login(self.usuario)
        continuar = movil.get(
            reverse('financiacion_educativa_web:captura-movil-continuar')
        )
        captura_url = continuar.url
        captura = movil.post(
            captura_url,
            {
                'lado': 'frente',
                'captura': jpeg(),
                'metodo_captura': 'webrtc',
            },
        )

        self.assertEqual(captura.status_code, 200)
        self.assertTrue(
            self.solicitud.documentos.filter(
                tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                activo=True,
            ).exists()
        )
        computador = Client()
        computador.force_login(self.usuario)
        actualizada = computador.get(captura_url)
        self.assertContains(actualizada, 'Capturada; pendiente de revisi')

    def test_escritorio_no_puede_capturar_por_url_directa(self):
        self.client.force_login(self.usuario)
        captura_url = reverse(
            'financiacion_educativa_web:capturar-identidad',
            kwargs={
                'solicitud_id': self.solicitud.pk,
                'persona': 'estudiante',
            },
        )

        pagina = self.client.get(captura_url)
        post = self.client.post(
            captura_url,
            {'lado': 'frente', 'captura': jpeg()},
        )

        self.assertEqual(pagina.status_code, 200)
        self.assertNotContains(pagina, 'data-camera-capture')
        self.assertEqual(post.status_code, 404)
        self.assertFalse(self.solicitud.documentos.exists())

    def test_reemplazo_de_captura_requiere_confirmacion_explicita(self):
        self._emitir_desde_web()
        token, ruta = self._token_y_ruta()
        movil = Client(HTTP_USER_AGENT=MOBILE_UA)
        movil.post(ruta, {'token': token})
        movil.force_login(self.usuario)
        continuar = movil.get(
            reverse('financiacion_educativa_web:captura-movil-continuar')
        )
        captura_url = continuar.url

        primera = movil.post(
            captura_url,
            {
                'lado': 'frente',
                'captura': jpeg('primera.jpg', b'primera'),
                'metodo_captura': 'webrtc',
            },
        )
        sin_confirmar = movil.post(
            captura_url,
            {
                'lado': 'frente',
                'captura': jpeg('segunda.jpg', b'segunda'),
                'metodo_captura': 'webrtc',
            },
        )
        confirmada = movil.post(
            captura_url,
            {
                'lado': 'frente',
                'captura': jpeg('segunda.jpg', b'segunda'),
                'metodo_captura': 'webrtc',
                'confirmar_reemplazo': '1',
            },
        )

        self.assertEqual(primera.status_code, 200)
        self.assertEqual(sin_confirmar.status_code, 409)
        self.assertEqual(confirmada.status_code, 200)
        self.assertEqual(
            self.solicitud.documentos.filter(
                tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                activo=True,
            ).count(),
            1,
        )

    def test_token_abierto_en_escritorio_no_se_consume(self):
        self._emitir_desde_web()
        token, ruta = self._token_y_ruta()

        respuesta = self.client.post(ruta, {'token': token})

        self.assertEqual(respuesta.status_code, 400)
        enlace = EnlaceCapturaMovil.objects.get()
        self.assertEqual(enlace.estado, EstadoEnlaceCapturaMovil.ACTIVE)

    @override_settings(
        FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_DELIVERY_BACKEND=BACKEND_DJANGO,
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='',
        EMAIL_HOST_USER='',
        EMAIL_HOST_PASSWORD='',
    )
    def test_configuracion_smtp_incompleta_revoca_enlace(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.post(self.url_envio)
        procesar_siguiente_correo()

        self.assertEqual(respuesta.status_code, 302)
        enlace = EnlaceCapturaMovil.objects.get()
        self.assertEqual(
            enlace.codigo_ultimo_error,
            'SMTP_CONFIGURATION_ERROR',
        )
        self.assertEqual(enlace.estado, EstadoEnlaceCapturaMovil.REVOKED)
        self.assertEqual(enlace.estado_entrega, EstadoEntregaCapturaMovil.FAILED)

    @override_settings(
        DEBUG=True,
        FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_DELIVERY_BACKEND=BACKEND_DJANGO,
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    )
    def test_backend_local_en_memoria_confirma_entrega(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.post(self.url_envio)
        procesar_siguiente_correo()

        enlace = EnlaceCapturaMovil.objects.get()
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(enlace.estado, EstadoEnlaceCapturaMovil.ACTIVE)
        self.assertEqual(enlace.estado_entrega, EstadoEntregaCapturaMovil.SENT)

    @override_settings(
        DEBUG=False,
        FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_DELIVERY_BACKEND=BACKEND_DJANGO,
        EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend',
    )
    def test_backend_no_smtp_falla_cerrado_fuera_de_desarrollo(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.post(self.url_envio)
        procesar_siguiente_correo()

        enlace = EnlaceCapturaMovil.objects.get()
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(enlace.estado, EstadoEnlaceCapturaMovil.REVOKED)
        self.assertEqual(enlace.estado_entrega, EstadoEntregaCapturaMovil.FAILED)
        self.assertEqual(
            enlace.codigo_ultimo_error,
            'SMTP_CONFIGURATION_ERROR',
        )
