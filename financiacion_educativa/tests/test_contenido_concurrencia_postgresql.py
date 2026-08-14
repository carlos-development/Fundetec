import threading
from datetime import date
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.services.clasificacion_contenido_documental import (
    procesar_contenido_documental,
)
from financiacion_educativa.services.documentos import registrar_documento
from financiacion_educativa.services.escaneo_documentos import procesar_escaneo_documento
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.tests.content_validation_backends import (
    resultado_concluyente,
)
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.scan_backends import BackendLimpio
from financiacion_educativa.tests.test_procesamiento_pdf import pdf_sintetico


@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL real.')
@override_settings(
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=True,
    FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS=False,
    FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY='postgres-content-key',
)
class ConcurrenciaContenidoPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.override = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.private_root.cleanup)
        usuario = get_user_model().objects.create_user(
            username='pg-content@example.com',
            email='pg-content@example.com',
        )
        solicitud = crear_solicitud(usuario=usuario, referencia='PG-CONTENT')
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        solicitud.save(update_fields=['estado'])
        participante = registrar_o_actualizar_participante(
            solicitud=solicitud,
            actor=usuario,
            datos=DatosParticipante(
                nombres='PERSONA', apellidos='PRUEBA',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='10000001',
                fecha_nacimiento=date(1990, 1, 1),
                correo='persona@example.com', telefono='3001234567',
                relacion_estudiante=RelacionEstudiante.SELF,
                pais_expedicion='CO',
            ),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )
        self.documento = registrar_documento(
            solicitud=solicitud,
            participante=participante,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura='USER_UPLOAD',
            archivo=SimpleUploadedFile(
                'ingresos.pdf',
                pdf_sintetico(textos=('CERTIFICADO INGRESOS PERSONA PRUEBA 2026',)),
                content_type='application/pdf',
            ),
            actor=usuario,
        )
        procesar_escaneo_documento(
            documento=self.documento,
            origen='AUTOMATIC',
            backend=BackendLimpio(),
        )
        self.documento.refresh_from_db()

    def test_dos_workers_no_clasifican_la_misma_version_dos_veces(self):
        iniciado = threading.Event()
        liberar = threading.Event()
        llamadas = []
        resultados = []

        class BackendLento:
            enabled = True

            def clasificar(_self, *, tipo_esperado, contexto, **kwargs):
                llamadas.append(1)
                iniciado.set()
                liberar.wait(timeout=10)
                return resultado_concluyente(
                    tipo_esperado=tipo_esperado,
                    contexto=contexto,
                )

        def procesar():
            close_old_connections()
            try:
                resultados.append(procesar_contenido_documental(
                    documento=self.documento,
                    backend=BackendLento(),
                ).estado)
            finally:
                close_old_connections()

        primero = threading.Thread(target=procesar)
        segundo = threading.Thread(target=procesar)
        primero.start()
        self.assertTrue(iniciado.wait(timeout=10))
        segundo.start()
        segundo.join(timeout=10)
        liberar.set()
        primero.join(timeout=10)

        self.assertFalse(primero.is_alive())
        self.assertFalse(segundo.is_alive())
        self.assertEqual(llamadas, [1])
        self.assertCountEqual(resultados, ['IN_PROGRESS', 'ACCEPTED'])
        self.assertEqual(self.documento.procesamientos_contenido.count(), 1)
