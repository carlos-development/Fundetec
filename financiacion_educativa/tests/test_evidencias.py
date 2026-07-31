from datetime import date
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from financiacion_educativa.choices import (
    OrigenCapturaDocumento,
    TipoConsentimiento,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.services.consentimientos import registrar_consentimiento
from financiacion_educativa.services.documentos import registrar_documento
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_adulto_como_estudiante_y_deudor,
)
from financiacion_educativa.tests.factories import crear_solicitud


PNG_VALIDO = b'\x89PNG\r\n\x1a\ncontenido-prueba-IEND-00000000'


class EvidenciasFinanciacionTests(TestCase):
    def setUp(self):
        self.solicitud = crear_solicitud()
        self.participante = registrar_adulto_como_estudiante_y_deudor(
            solicitud=self.solicitud,
            datos=DatosParticipante(
                nombres='ANA',
                apellidos='PEREZ',
                tipo_documento='CC',
                numero_documento='10001234',
                fecha_nacimiento=date(1990, 1, 1),
                fecha_nacimiento_confirmada=True,
            ),
        )

    def test_consentimiento_guarda_version_y_evidencia_inmutable(self):
        consentimiento = registrar_consentimiento(
            solicitud=self.solicitud,
            participante=self.participante,
            tipo=TipoConsentimiento.TERMS,
            version_texto='2026-01',
            texto='Texto legal versionado.',
            ip_address='127.0.0.1',
            user_agent='Navegador de prueba',
        )

        self.assertEqual(consentimiento.version_texto, '2026-01')
        self.assertEqual(len(consentimiento.evidencia_hash), 64)
        consentimiento.version_texto = 'alterada'
        with self.assertRaises(ValidationError):
            consentimiento.save()

    def test_documento_se_registra_sin_ocr_ni_ia(self):
        with TemporaryDirectory() as media_root:
            with override_settings(FINANCIACION_EDUCATIVA_PRIVATE_ROOT=media_root):
                documento = registrar_documento(
                    solicitud=self.solicitud,
                    participante=self.participante,
                    tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                    origen_captura=OrigenCapturaDocumento.CAMERA,
                    archivo=SimpleUploadedFile(
                        'documento.png',
                        PNG_VALIDO,
                        content_type='image/png',
                    ),
                )

        self.assertEqual(documento.resultado_procesamiento, {})
        self.assertIsNone(documento.nivel_confianza)
        self.assertEqual(len(documento.sha256), 64)

    def test_detecta_documento_duplicado_por_hash_en_solicitud(self):
        with TemporaryDirectory() as media_root:
            with override_settings(FINANCIACION_EDUCATIVA_PRIVATE_ROOT=media_root):
                registrar_documento(
                    solicitud=self.solicitud,
                    participante=self.participante,
                    tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                    origen_captura=OrigenCapturaDocumento.CAMERA,
                    archivo=SimpleUploadedFile('uno.png', PNG_VALIDO, content_type='image/png'),
                )
                with self.assertRaises(ValidationError):
                    registrar_documento(
                        solicitud=self.solicitud,
                        participante=self.participante,
                        tipo=TipoDocumentoFinanciacion.OTHER,
                        origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
                        archivo=SimpleUploadedFile('dos.png', PNG_VALIDO, content_type='image/png'),
                    )
