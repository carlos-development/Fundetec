import threading
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings

from financiacion_educativa.choices import (
    EstadoIntentoEscaneoDocumento,
    EstadoSolicitudFinanciacion,
    OrigenCapturaDocumento,
    OrigenIntentoEscaneoDocumento,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import IntentoEscaneoDocumento
from financiacion_educativa.services.documentos import (
    registrar_documento,
    reemplazar_documento,
)
from financiacion_educativa.services.escaneo_documentos import (
    ResultadoAntivirus,
    VeredictoAntivirus,
    procesar_escaneo_documento,
)
from financiacion_educativa.tests.factories import crear_solicitud


class BackendConcurrenteControlado:
    proveedor = 'postgres-test-double'

    def __init__(self):
        self.iniciado = threading.Event()
        self.liberar = threading.Event()
        self.invocaciones = 0
        self.lock = threading.Lock()

    def escanear(self, archivo):
        archivo.read()
        with self.lock:
            self.invocaciones += 1
        self.iniciado.set()
        if not self.liberar.wait(5):
            raise RuntimeError('La prueba no libero el backend.')
        return ResultadoAntivirus(VeredictoAntivirus.CLEAN, self.proveedor)


@skipUnless(
    connection.vendor == 'postgresql',
    'Requiere PostgreSQL para validar bloqueo entre conexiones.',
)
class ConcurrenciaEscaneoPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.override = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.private_root.cleanup)
        self.usuario = get_user_model().objects.create_user(
            username='pg-scan-owner@example.com',
            password='Clave-2026',
        )
        solicitud = crear_solicitud(
            usuario=self.usuario,
            referencia='PG-SCAN-001',
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        solicitud.save(update_fields=['estado'])
        self.documento = registrar_documento(
            solicitud=solicitud,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=SimpleUploadedFile(
                'pg.pdf',
                b'%PDF-1.7\npg\n%%EOF',
                content_type='application/pdf',
            ),
            actor=self.usuario,
        )

    @override_settings(FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=False)
    def test_reemplazo_bloquea_solo_documento_y_no_el_join_nullable(self):
        nuevo = reemplazar_documento(
            documento=self.documento,
            archivo=SimpleUploadedFile(
                'pg-reemplazo.pdf',
                b'%PDF-1.7\npg-reemplazo\n%%EOF',
                content_type='application/pdf',
            ),
            actor=self.usuario,
        )

        self.documento.refresh_from_db()
        self.assertFalse(self.documento.activo)
        self.assertTrue(nuevo.activo)
        self.assertEqual(nuevo.reemplaza_a_id, self.documento.pk)

    def test_dos_conexiones_generan_un_solo_intento_y_un_escaneo(self):
        backend = BackendConcurrenteControlado()
        resultados = []
        errores = []

        def ejecutar():
            close_old_connections()
            try:
                documento = type(self.documento).objects.get(pk=self.documento.pk)
                resultados.append(
                    procesar_escaneo_documento(
                        documento=documento,
                        origen=OrigenIntentoEscaneoDocumento.COMMAND,
                        backend=backend,
                    )
                )
            except Exception as error:  # pragma: no cover - diagnostico de hilo
                errores.append(error)
            finally:
                close_old_connections()

        primero = threading.Thread(target=ejecutar)
        primero.start()
        self.assertTrue(backend.iniciado.wait(5))
        self.assertEqual(
            self.documento.intentos_escaneo.filter(
                estado=EstadoIntentoEscaneoDocumento.STARTED
            ).count(),
            1,
        )
        segundo = threading.Thread(target=ejecutar)
        segundo.start()
        try:
            segundo.join(5)
            self.assertFalse(segundo.is_alive())
            self.assertEqual(errores, [])
            self.assertEqual(backend.invocaciones, 1)
            self.assertEqual(
                self.documento.intentos_escaneo.filter(
                    estado=EstadoIntentoEscaneoDocumento.STARTED
                ).count(),
                1,
            )
            self.assertEqual(self.documento.intentos_escaneo.count(), 1)
        finally:
            backend.liberar.set()
        primero.join(5)

        self.assertFalse(primero.is_alive())
        self.assertEqual(errores, [])
        self.assertEqual(backend.invocaciones, 1)
        self.assertEqual(self.documento.intentos_escaneo.count(), 1)
        self.assertEqual(
            self.documento.intentos_escaneo.filter(
                estado=EstadoIntentoEscaneoDocumento.CLEAN
            ).count(),
            1,
        )
        self.assertEqual(
            {resultado.estado for resultado in resultados},
            {'IN_PROGRESS', 'SAFE'},
        )

    def test_restriccion_parcial_impide_dos_intentos_activos(self):
        IntentoEscaneoDocumento.objects.create(
            documento=self.documento,
            numero=1,
            origen=OrigenIntentoEscaneoDocumento.COMMAND,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            IntentoEscaneoDocumento.objects.create(
                documento=self.documento,
                numero=2,
                origen=OrigenIntentoEscaneoDocumento.COMMAND,
                estado=EstadoIntentoEscaneoDocumento.STARTED,
            )
