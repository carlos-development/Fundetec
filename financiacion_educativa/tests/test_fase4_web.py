from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import DocumentoFinanciacion
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.tests.factories import crear_solicitud


class FlujoWebDocumentalFase4Tests(TestCase):
    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.override = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.private_root.cleanup)

        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='web-f4@example.com',
            email='web-f4@example.com',
            password='Clave-2026',
        )
        self.otro = User.objects.create_user(
            username='web-f4-otro@example.com',
            email='web-f4-otro@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud()
        self.solicitud.usuario = self.usuario
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['usuario', 'estado'])

    def _url(self, nombre, **kwargs):
        return reverse(
            f'financiacion_educativa_web:{nombre}',
            kwargs={'solicitud_id': self.solicitud.pk, **kwargs},
        )

    def _crear_estudiante(self):
        return registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='Persona',
                apellidos='Web',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='1000999988',
                fecha_nacimiento=date(1990, 1, 1),
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )

    def test_vistas_documentales_requieren_sesion(self):
        urls = [
            self._url('documentacion'),
            self._url('participante-nuevo'),
            self._url('documento-cargar'),
            self._url('matricula'),
        ]
        self.assertTrue(all(self.client.get(url).status_code == 302 for url in urls))

    def test_usuario_no_accede_a_solicitud_ajena(self):
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(self._url('documentacion')).status_code, 404)
        self.assertEqual(self.client.get(self._url('participante-nuevo')).status_code, 404)

    def test_formulario_participante_crea_roles_y_resumen_enmascara_documento(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.post(
            self._url('participante-nuevo'),
            {
                'nombres': 'Persona',
                'apellidos': 'Web',
                'tipo_documento': TipoDocumentoIdentidad.CC,
                'numero_documento': '1000999988',
                'pais_expedicion': 'CO',
                'fecha_nacimiento': '1990-01-01',
                'correo': '',
                'telefono': '',
                'relacion_estudiante': RelacionEstudiante.SELF,
                'roles': [
                    RolParticipante.STUDENT,
                    RolParticipante.PRINCIPAL_DEBTOR,
                ],
            },
        )
        self.assertRedirects(respuesta, self._url('documentacion'))
        resumen = self.client.get(self._url('documentacion'))

        self.assertNotContains(resumen, '1000999988')
        self.assertContains(resumen, '******9988')
        participante = self.solicitud.participantes.get()
        self.assertIsNone(participante.usuario)

    def test_carga_multipart_es_privada_y_no_acepta_aprobacion_del_usuario(self):
        participante = self._crear_estudiante()
        self.client.force_login(self.usuario)
        respuesta = self.client.post(
            self._url('documento-cargar'),
            {
                'tipo': TipoDocumentoFinanciacion.STUDENT_IDENTIFICATION,
                'participante': str(participante.pk),
                'archivo': SimpleUploadedFile(
                    'identidad.pdf',
                    b'%PDF-1.7\nweb\n%%EOF',
                    content_type='application/octet-stream',
                ),
                'estado_validacion': 'APPROVED',
            },
        )

        self.assertRedirects(respuesta, self._url('documentacion'))
        documento = DocumentoFinanciacion.objects.get()
        self.assertEqual(documento.estado_validacion, 'PENDING')
        with self.assertRaises(ValueError):
            documento.archivo.url

    def test_post_documental_exige_csrf(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.usuario)
        respuesta = cliente.post(
            self._url('participante-nuevo'),
            {
                'nombres': 'Sin',
                'apellidos': 'Csrf',
                'tipo_documento': TipoDocumentoIdentidad.CC,
                'numero_documento': '12345678',
                'fecha_nacimiento': '1990-01-01',
                'roles': [RolParticipante.STUDENT],
            },
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_resumen_sensible_no_permite_cache(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(self._url('documentacion'))
        self.assertIn('no-store', respuesta['Cache-Control'])
