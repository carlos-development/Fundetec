from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    OrigenCapturaDocumento,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
    TipoEventoSeguridadFinanciacion,
)
from financiacion_educativa.models import EventoSeguridadFinanciacion
from financiacion_educativa.services.documentos import registrar_documento
from financiacion_educativa.services.invitaciones import (
    emitir_invitacion_continuacion,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.tests.factories import crear_solicitud


def archivo_jpeg():
    return SimpleUploadedFile(
        'soporte.jpg',
        b'\xff\xd8\xffseguro\xff\xd9',
        content_type='image/jpeg',
    )


class AutorizacionWebEducativaTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.propietario = User.objects.create_user(
            username='ana@example.com',
            email='ana@example.com',
            password='Clave-2026',
        )
        self.ajeno = User.objects.create_user(
            username='ajeno@example.com',
            email='ajeno@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud(usuario=self.propietario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['estado'])
        self.participante = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.propietario,
            datos=DatosParticipante(
                nombres='Ana',
                apellidos='Perez',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='100200300',
                fecha_nacimiento=date(1990, 1, 1),
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles={
                RolParticipante.STUDENT,
                RolParticipante.PRINCIPAL_DEBTOR,
            },
        )
        self.documento = registrar_documento(
            solicitud=self.solicitud,
            participante=self.participante,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=archivo_jpeg(),
            actor=self.propietario,
        )

    def _url(self, nombre, **kwargs):
        return reverse(
            f'financiacion_educativa_web:{nombre}',
            kwargs={'solicitud_id': self.solicitud.pk, **kwargs},
        )

    def test_propietario_correcto_consulta_expediente(self):
        self.client.force_login(self.propietario)
        respuesta = self.client.get(self._url('documentacion'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, self.solicitud.nombre_curso)

    def test_usuario_ajeno_con_uuid_conocido_recibe_404_en_get_y_post(self):
        self.client.force_login(self.ajeno)
        solicitudes = [
            ('get', self._url('terminos'), {}),
            ('get', self._url('siguiente'), {}),
            ('get', self._url('documentacion'), {}),
            ('get', self._url('participante-nuevo'), {}),
            (
                'get',
                self._url(
                    'participante-editar',
                    participante_id=self.participante.pk,
                ),
                {},
            ),
            ('get', self._url('documento-cargar'), {}),
            (
                'get',
                self._url('capturar-identidad', persona='estudiante'),
                {},
            ),
            (
                'post',
                self._url('captura-movil-enviar', persona='estudiante'),
                {},
            ),
            (
                'get',
                self._url(
                    'documento-reemplazar',
                    documento_id=self.documento.pk,
                ),
                {},
            ),
            (
                'get',
                self._url(
                    'documento-previsualizar',
                    documento_id=self.documento.pk,
                ),
                {},
            ),
            (
                'get',
                self._url(
                    'documento-descargar',
                    documento_id=self.documento.pk,
                ),
                {},
            ),
            ('get', self._url('matricula'), {}),
            ('get', self._url('ficha-matricula'), {}),
            ('post', self._url('documentacion-completar'), {}),
            ('get', self._url('finanzas'), {}),
            ('post', self._url('proyectar-abono'), {}),
            ('post', self._url('proyectar-pago-total'), {}),
        ]

        for metodo, url, datos in solicitudes:
            with self.subTest(metodo=metodo, url=url):
                respuesta = getattr(self.client, metodo)(url, datos)
                self.assertEqual(respuesta.status_code, 404)
                contenido = respuesta.content.decode(errors='ignore')
                self.assertNotIn(self.solicitud.nombres, contenido)
                self.assertNotIn(self.solicitud.correo, contenido)
                self.assertNotIn(self.solicitud.nombre_curso, contenido)

        self.assertTrue(
            EventoSeguridadFinanciacion.objects.filter(
                solicitud=self.solicitud,
                actor=self.ajeno,
                tipo=(
                    TipoEventoSeguridadFinanciacion
                    .UNAUTHORIZED_APPLICATION_ACCESS
                ),
            ).exists()
        )

    def test_anonimo_no_recibe_datos_personales(self):
        respuesta = self.client.get(self._url('documentacion'))

        self.assertEqual(respuesta.status_code, 302)
        self.assertNotIn(self.solicitud.nombres, respuesta.url)
        self.assertNotIn(self.solicitud.correo, respuesta.url)
        self.assertNotIn(self.solicitud.nombre_curso, respuesta.url)

    def test_invitacion_abierta_con_cuenta_incorrecta_no_filtra_datos(self):
        solicitud = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia='INV-AJENA',
            correo='destinatario@example.com',
        )
        emitida = emitir_invitacion_continuacion(solicitud=solicitud)
        self.client.force_login(self.ajeno)
        respuesta = self.client.get(
            reverse(
                'financiacion_educativa_web:continuar-invitacion',
                kwargs={'token': emitida.token},
            )
        )
        contenido = respuesta.content.decode(errors='ignore')

        self.assertEqual(respuesta.status_code, 404)
        for sensible in (
            solicitud.nombres,
            solicitud.apellidos,
            solicitud.correo,
            solicitud.celular,
            solicitud.numero_documento_estudiante,
            solicitud.nombre_curso,
        ):
            if sensible:
                self.assertNotIn(sensible, contenido)

    def test_solicitud_ya_asociada_no_admite_reasociacion(self):
        solicitud = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia='INV-ASOCIADA',
            correo='compartido@example.com',
        )
        emitida = emitir_invitacion_continuacion(solicitud=solicitud)
        propietario = get_user_model().objects.create_user(
            username='propietario-real@example.com',
            email='compartido@example.com',
            password='Clave-2026',
        )
        atacante = get_user_model().objects.create_user(
            username='cuenta-duplicada@example.com',
            email='compartido@example.com',
            password='Clave-2026',
        )
        solicitud.usuario = propietario
        solicitud.save(update_fields=['usuario'])
        self.client.force_login(atacante)

        respuesta = self.client.get(
            reverse(
                'financiacion_educativa_web:continuar-invitacion',
                kwargs={'token': emitida.token},
            )
        )

        self.assertEqual(respuesta.status_code, 404)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.usuario, propietario)
        self.assertNotContains(respuesta, solicitud.nombre_curso, status_code=404)
