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
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['estado'])

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
        self.assertEqual(self.client.get(self._url('ficha-matricula')).status_code, 404)

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
        self.assertEqual(participante.nombres, self.solicitud.nombres)
        self.assertEqual(participante.apellidos, self.solicitud.apellidos)
        self.assertEqual(participante.correo, self.solicitud.correo)
        self.assertEqual(participante.telefono, self.solicitud.celular)
        self.assertEqual(
            set(participante.roles.values_list('rol', flat=True)),
            {RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )
        self.assertIsNone(participante.usuario)

    def test_formulario_estudiante_no_repite_datos_recibidos(self):
        self.client.force_login(self.usuario)

        respuesta = self.client.get(
            f'{self._url("participante-nuevo")}?tipo=estudiante'
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, self.solicitud.nombres)
        self.assertContains(respuesta, self.solicitud.apellidos)
        self.assertNotContains(respuesta, 'name="nombres"')
        self.assertNotContains(respuesta, 'name="apellidos"')
        self.assertNotContains(respuesta, 'name="correo"')
        self.assertNotContains(respuesta, 'name="telefono"')

    def test_tutor_es_adicional_y_no_reemplaza_datos_del_estudiante(self):
        registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='Persona',
                apellidos='Menor',
                tipo_documento=TipoDocumentoIdentidad.TI,
                numero_documento='1000999988',
                fecha_nacimiento=date(2012, 1, 1),
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles={RolParticipante.STUDENT},
        )
        self.client.force_login(self.usuario)

        respuesta = self.client.post(
            f'{self._url("participante-nuevo")}?tipo=tutor',
            {
                'tipo_persona': 'tutor',
                'nombres': 'Tutor',
                'apellidos': 'Responsable',
                'tipo_documento': TipoDocumentoIdentidad.CC,
                'numero_documento': '9000999988',
                'pais_expedicion': 'CO',
                'fecha_nacimiento': '1980-01-01',
                'correo': 'tutor@example.com',
                'telefono': '3011234567',
                'relacion_estudiante': RelacionEstudiante.LEGAL_GUARDIAN,
            },
        )

        self.assertRedirects(respuesta, self._url('documentacion'))
        estudiante = self.solicitud.roles_participantes.get(
            rol=RolParticipante.STUDENT
        ).participante
        tutor = self.solicitud.roles_participantes.get(
            rol=RolParticipante.GUARDIAN
        ).participante
        self.assertEqual(estudiante.nombres, 'Persona')
        self.assertEqual(tutor.nombres, 'Tutor')
        self.assertNotEqual(estudiante.pk, tutor.pk)

    def test_ficha_matricula_es_privada_y_no_infiere_datos_faltantes(self):
        self._crear_estudiante()
        self.client.force_login(self.usuario)

        respuesta = self.client.get(self._url('ficha-matricula'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Referrer-Policy'], 'no-referrer')
        self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertContains(respuesta, self.solicitud.nombre_curso)
        self.assertContains(respuesta, self.solicitud.nombres)
        self.assertContains(respuesta, 'Fecha oficial de matricula')
        self.assertContains(
            respuesta,
            'La fecha de creaci&oacute;n de la solicitud tampoco se interpreta',
        )

    def test_evidencia_no_solicita_datos_academicos_ya_recibidos(self):
        self.client.force_login(self.usuario)

        respuesta = self.client.get(self._url('matricula'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, self.solicitud.nombre_curso)
        self.assertContains(
            respuesta,
            self.solicitud.institucion.nombre_comercial,
        )
        self.assertNotContains(respuesta, 'name="institucion_declarada"')
        self.assertNotContains(respuesta, 'name="programa_curso"')

    def test_carga_multipart_es_privada_y_no_acepta_aprobacion_del_usuario(self):
        participante = self._crear_estudiante()
        self.client.force_login(self.usuario)
        respuesta = self.client.post(
            self._url('documento-cargar'),
            {
                'tipo': TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
                'participante': str(participante.pk),
                'archivo': SimpleUploadedFile(
                    'ingresos.pdf',
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
        resumen = self.client.get(self._url('documentacion'))
        self.assertContains(resumen, 'Previsualizar')
        self.assertContains(resumen, 'data-document-preview')
        self.assertNotContains(
            resumen,
            'Informaci&oacute;n recibida de la instituci&oacute;n',
        )

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
