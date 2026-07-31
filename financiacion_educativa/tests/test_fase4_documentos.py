from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    MotivoRechazoDocumento,
    OrigenCapturaDocumento,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.services.documentos import (
    registrar_documento,
    registrar_resultado_escaneo,
    reemplazar_documento,
    revisar_documento,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.tests.factories import crear_solicitud


def archivo_pdf(nombre='soporte.pdf', marca=b'contenido'):
    return SimpleUploadedFile(
        nombre,
        b'%PDF-1.7\n' + marca + b'\n%%EOF',
        content_type='text/plain',
    )


def archivo_jpeg(nombre='captura.jpg', marca=b'contenido'):
    return SimpleUploadedFile(
        nombre,
        b'\xff\xd8\xff' + marca + b'\xff\xd9',
        content_type='application/octet-stream',
    )


def archivo_png(nombre='captura.png', marca=b'contenido'):
    return SimpleUploadedFile(
        nombre,
        b'\x89PNG\r\n\x1a\n' + marca + b'IEND',
        content_type='application/octet-stream',
    )


class DocumentosPrivadosFase4Tests(TestCase):
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
            username='documentos@example.com',
            email='documentos@example.com',
            password='Clave-2026',
        )
        self.otro = User.objects.create_user(
            username='documentos-otro@example.com',
            email='documentos-otro@example.com',
            password='Clave-2026',
        )
        self.revisor = User.objects.create_user(
            username='revisor@example.com',
            email='revisor@example.com',
            password='Clave-2026',
            is_staff=True,
        )
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['estado'])
        self.participante = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='Ana',
                apellidos='Perez',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='1000123456',
                fecha_nacimiento=date(1990, 1, 1),
                correo='documentos@example.com',
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )

    def _registrar(self, archivo=None):
        return registrar_documento(
            solicitud=self.solicitud,
            participante=self.participante,
            tipo=TipoDocumentoFinanciacion.STUDENT_IDENTIFICATION,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=archivo or archivo_pdf(),
            actor=self.usuario,
        )

    def test_nombre_y_ruta_son_aleatorios_y_no_tienen_datos_personales(self):
        documento = self._registrar(
            archivo_pdf('../../1000123456-documentos@example.com.pdf')
        )

        self.assertRegex(documento.nombre_seguro, r'^[0-9a-f]{32}\.pdf$')
        self.assertNotIn('1000123456', documento.archivo.name)
        self.assertNotIn('documentos@example.com', documento.archivo.name)
        self.assertNotIn('..', documento.archivo.name)
        self.assertEqual(documento.nombre_original, 'documento.pdf')
        with self.assertRaises(ValueError):
            documento.archivo.url

    def test_no_confia_en_content_type_y_calcula_hash_en_servidor(self):
        documento = self._registrar()

        self.assertEqual(documento.content_type, 'application/pdf')
        self.assertEqual(len(documento.sha256), 64)
        self.assertEqual(documento.estado_escaneo, EstadoEscaneoDocumento.PENDING_SECURITY_SCAN)
        self.assertEqual(documento.estado_validacion, EstadoValidacionDocumento.PENDING)

    def test_rechaza_formato_real_y_extension_incorrectos(self):
        with self.assertRaises(ValidationError):
            self._registrar(
                SimpleUploadedFile('falso.pdf', b'no-es-pdf', content_type='application/pdf')
            )
        with self.assertRaises(ValidationError):
            self._registrar(
                SimpleUploadedFile(
                    'falso.png',
                    b'%PDF-1.7\ncontenido\n%%EOF',
                    content_type='image/png',
                )
            )

    @override_settings(FINANCIACION_EDUCATIVA_DOCUMENT_MAX_BYTES=12)
    def test_rechaza_archivo_mayor_al_limite(self):
        with self.assertRaises(ValidationError):
            self._registrar(archivo_pdf(marca=b'contenido-demasiado-largo'))

    def test_reemplazo_conserva_trazabilidad_y_reinicia_estados(self):
        anterior = self._registrar()
        registrar_resultado_escaneo(
            documento=anterior,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
            referencia_escaneo='scanner-real-001',
        )
        revisar_documento(documento=anterior, actor=self.revisor, aceptar=True)

        nuevo = reemplazar_documento(
            documento=anterior,
            archivo=archivo_pdf(marca=b'nueva-version'),
            actor=self.usuario,
        )
        anterior.refresh_from_db()

        self.assertFalse(anterior.activo)
        self.assertEqual(nuevo.reemplaza_a, anterior)
        self.assertTrue(nuevo.activo)
        self.assertEqual(nuevo.estado_escaneo, EstadoEscaneoDocumento.PENDING_SECURITY_SCAN)
        self.assertEqual(nuevo.estado_validacion, EstadoValidacionDocumento.PENDING)

    def test_escaneo_y_revision_son_estados_separados(self):
        documento = self._registrar()
        with self.assertRaises(ValidationError):
            revisar_documento(documento=documento, actor=self.revisor, aceptar=True)
        with self.assertRaises(ValidationError):
            revisar_documento(documento=documento, actor=self.usuario, aceptar=True)

        registrar_resultado_escaneo(
            documento=documento,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
            referencia_escaneo='scanner-real-002',
        )
        revisado = revisar_documento(
            documento=documento,
            actor=self.revisor,
            aceptar=True,
        )

        self.assertEqual(revisado.estado_validacion, EstadoValidacionDocumento.APPROVED)
        self.assertEqual(revisado.revisado_por, self.revisor)
        self.assertIsNotNone(revisado.revisado_en)

    def test_rechazo_exige_motivo_controlado(self):
        documento = self._registrar()
        with self.assertRaises(ValidationError):
            revisar_documento(
                documento=documento,
                actor=self.revisor,
                aceptar=False,
                motivo_rechazo='TEXTO_LIBRE',
            )
        rechazado = revisar_documento(
            documento=documento,
            actor=self.revisor,
            aceptar=False,
            motivo_rechazo=MotivoRechazoDocumento.UNREADABLE,
        )
        self.assertEqual(rechazado.estado_validacion, EstadoValidacionDocumento.REJECTED)

    def test_descarga_requiere_sesion_y_propiedad_y_usa_headers_defensivos(self):
        documento = self._registrar()
        url = reverse(
            'financiacion_educativa_web:documento-descargar',
            kwargs={
                'solicitud_id': self.solicitud.pk,
                'documento_id': documento.pk,
            },
        )

        anonima = self.client.get(url)
        self.assertEqual(anonima.status_code, 302)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('attachment;', respuesta['Content-Disposition'])
        self.assertEqual(respuesta['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertNotIn(self.private_root.name, str(respuesta.headers))
        respuesta.close()

    def test_previsualizacion_es_inline_privada_y_solo_mismo_origen(self):
        documento = self._registrar()
        url = reverse(
            'financiacion_educativa_web:documento-previsualizar',
            kwargs={
                'solicitud_id': self.solicitud.pk,
                'documento_id': documento.pk,
            },
        )

        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.usuario)
        respuesta = self.client.get(url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertIn('inline;', respuesta['Content-Disposition'])
        self.assertEqual(respuesta['X-Frame-Options'], 'SAMEORIGIN')
        self.assertEqual(
            respuesta['Cross-Origin-Resource-Policy'],
            'same-origin',
        )
        self.assertIn("frame-ancestors 'self'", respuesta['Content-Security-Policy'])
        self.assertIn("object-src 'none'", respuesta['Content-Security-Policy'])
        self.assertNotIn('sandbox', respuesta['Content-Security-Policy'])
        self.assertIn('no-store', respuesta['Cache-Control'])
        self.assertNotIn(self.private_root.name, str(respuesta.headers))
        respuesta.close()

    def test_previsualizacion_privada_admite_pdf_jpeg_y_png_reales(self):
        casos = (
            (TipoDocumentoFinanciacion.OTHER_EDUCATIONAL, archivo_pdf(), 'application/pdf'),
            (TipoDocumentoFinanciacion.OTHER, archivo_jpeg(), 'image/jpeg'),
            (TipoDocumentoFinanciacion.INCOME_CERTIFICATE, archivo_png(), 'image/png'),
        )
        self.client.force_login(self.usuario)

        for indice, (tipo, archivo, mime) in enumerate(casos):
            with self.subTest(mime=mime):
                documento = registrar_documento(
                    solicitud=self.solicitud,
                    participante=(
                        self.participante
                        if tipo == TipoDocumentoFinanciacion.INCOME_CERTIFICATE
                        else None
                    ),
                    tipo=tipo,
                    origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
                    archivo=archivo,
                    actor=self.usuario,
                )
                url = reverse(
                    'financiacion_educativa_web:documento-previsualizar',
                    kwargs={
                        'solicitud_id': self.solicitud.pk,
                        'documento_id': documento.pk,
                    },
                )
                respuesta = self.client.get(url)
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(respuesta['Content-Type'], mime)
                self.assertIn('inline;', respuesta['Content-Disposition'])
                respuesta.close()

    def test_previsualizacion_rechaza_mime_no_permitido(self):
        documento = self._registrar()
        documento.content_type = 'text/html'
        documento.save(update_fields=['content_type'])
        self.client.force_login(self.usuario)
        url = reverse(
            'financiacion_educativa_web:documento-previsualizar',
            kwargs={
                'solicitud_id': self.solicitud.pk,
                'documento_id': documento.pk,
            },
        )

        self.assertEqual(self.client.get(url).status_code, 404)
