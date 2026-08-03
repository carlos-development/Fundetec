from io import BytesIO, StringIO
import socket
import threading
import time
import uuid
from datetime import timedelta
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import F
from django.test import TestCase, override_settings
from django.utils import timezone
from unittest.mock import patch

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoIntentoEscaneoDocumento,
    EstadoSolicitudFinanciacion,
    OrigenCapturaDocumento,
    OrigenIntentoEscaneoDocumento,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.services.documentos import (
    registrar_documento,
    revisar_documento,
)
from financiacion_educativa.services.escaneo_documentos import (
    ClamAVDocumentScanBackend,
    ErrorEscaneoDocumento,
    VeredictoAntivirus,
    procesar_escaneo_documento,
    reabrir_escaneo_documento,
)
from financiacion_educativa.models import (
    DocumentoFinanciacion,
    DocumentoFinanciacionManager,
    DocumentoFinanciacionQuerySet,
    IntentoEscaneoDocumento,
)
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.scan_backends import (
    BackendInfectado,
    BackendLimpio,
    BackendNoDisponible,
    BackendRespuestaInvalida,
    BackendTimeout,
)
from financiacion_educativa.tests.scan_helpers import (
    conceder_permisos_documentales,
    forzar_seguridad_documento_historico,
)


def pdf(nombre='documento'):
    return SimpleUploadedFile(
        f'{nombre}.pdf',
        b'%PDF-1.7\n' + nombre.encode('ascii') + b'\n%%EOF',
        content_type='application/pdf',
    )


def servir_clamav(listener, *, respuesta, demora=0):
    conexion, _ = listener.accept()
    with conexion:
        conexion.settimeout(2)
        recibido = b''
        while not recibido.endswith(b'\0\0\0\0'):
            bloque = conexion.recv(65536)
            if not bloque:
                break
            recibido += bloque
        if demora:
            time.sleep(demora)
        if respuesta is not None:
            conexion.sendall(respuesta)


class EscaneoAntivirusTests(TestCase):
    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.override = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.private_root.cleanup)
        User = get_user_model()
        self.propietario = User.objects.create_user(
            username='scan-owner@example.com',
            email='scan-owner@example.com',
            password='Clave-2026',
        )
        self.operador = User.objects.create_user(
            username='scan-operator@example.com',
            password='Clave-2026',
            is_staff=True,
        )
        conceder_permisos_documentales(self.operador)
        self.sin_permiso = User.objects.create_user(
            username='scan-no-permission@example.com',
            password='Clave-2026',
            is_staff=True,
        )
        self.solicitud = crear_solicitud(usuario=self.propietario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['estado'])

    def documento(self, nombre='documento'):
        return registrar_documento(
            solicitud=self.solicitud,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=pdf(nombre),
            actor=self.propietario,
        )

    def assert_safe_invalido_en_clean_y_save(self, documento):
        with self.assertRaises(ValidationError):
            documento.full_clean()
        with self.assertRaises(ValidationError):
            documento.save()

    def test_archivo_limpio_queda_seguro_y_registra_intento(self):
        documento = self.documento()
        resultado = procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendLimpio(),
        )
        documento.refresh_from_db()
        intento = documento.intentos_escaneo.get()

        self.assertTrue(resultado.procesado)
        self.assertEqual(documento.estado_escaneo, EstadoEscaneoDocumento.SAFE)
        self.assertEqual(intento.estado, EstadoIntentoEscaneoDocumento.CLEAN)
        self.assertEqual(intento.proveedor, 'test-double')
        self.assertNotIn(documento.archivo.name, str(documento.resultado_procesamiento))

    def test_adaptador_clamav_interpreta_solo_respuestas_concluyentes(self):
        backend = ClamAVDocumentScanBackend()

        limpio = backend._interpretar_respuesta(b'stream: OK\0')
        infectado = backend._interpretar_respuesta(
            b'stream: Eicar-Test-Signature FOUND\0'
        )

        self.assertEqual(limpio.veredicto, VeredictoAntivirus.CLEAN)
        self.assertEqual(infectado.veredicto, VeredictoAntivirus.INFECTED)
        self.assertEqual(infectado.firma_amenaza, 'Eicar-Test-Signature')
        with self.assertRaises(ErrorEscaneoDocumento) as error:
            backend._interpretar_respuesta(b'stream: UNKNOWN\0')
        self.assertEqual(error.exception.codigo, 'INVALID_RESPONSE')
        with self.assertRaises(ErrorEscaneoDocumento):
            backend._interpretar_respuesta(b'prefix stream: OK\0')

    @override_settings(FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET='')
    def test_adaptador_clamav_clasifica_indisponibilidad_y_timeout(self):
        backend = ClamAVDocumentScanBackend()
        with patch(
            'financiacion_educativa.services.escaneo_documentos.socket.create_connection',
            side_effect=OSError,
        ):
            with self.assertRaises(ErrorEscaneoDocumento) as indisponible:
                backend._conectar()
        self.assertEqual(indisponible.exception.codigo, 'SCANNER_UNAVAILABLE')

        with patch(
            'financiacion_educativa.services.escaneo_documentos.socket.create_connection',
            side_effect=TimeoutError,
        ):
            with self.assertRaises(ErrorEscaneoDocumento) as timeout:
                backend._conectar()
        self.assertEqual(timeout.exception.codigo, 'SCANNER_TIMEOUT')

    @override_settings(
        FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET='',
        FINANCIACION_EDUCATIVA_CLAMAV_HOST='127.0.0.1',
        FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS=1,
        FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS=1,
    )
    def test_adaptador_clamav_tcp_envia_instream_y_recibe_veredicto(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        with override_settings(
            FINANCIACION_EDUCATIVA_CLAMAV_PORT=listener.getsockname()[1]
        ):
            hilo = threading.Thread(
                target=servir_clamav,
                kwargs={'listener': listener, 'respuesta': b'stream: OK\0'},
                daemon=True,
            )
            hilo.start()
            resultado = ClamAVDocumentScanBackend().escanear(BytesIO(b'contenido'))
            hilo.join(2)
        self.assertEqual(resultado.veredicto, VeredictoAntivirus.CLEAN)
        self.assertFalse(hilo.is_alive())

    @override_settings(
        FINANCIACION_EDUCATIVA_CLAMAV_HOST='',
        FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS=1,
        FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS=1,
    )
    def test_adaptador_clamav_socket_unix_envia_instream(self):
        if not hasattr(socket, 'AF_UNIX'):
            self.skipTest('El sistema no ofrece sockets Unix.')
        with TemporaryDirectory() as temporal:
            ruta = f'{temporal}/clamd.sock'
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(listener.close)
            try:
                listener.bind(ruta)
            except OSError as error:
                self.skipTest(f'No fue posible crear el socket Unix: {error}')
            listener.listen(1)
            with override_settings(
                FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET=ruta
            ):
                hilo = threading.Thread(
                    target=servir_clamav,
                    kwargs={'listener': listener, 'respuesta': b'stream: OK\0'},
                    daemon=True,
                )
                hilo.start()
                resultado = ClamAVDocumentScanBackend().escanear(
                    BytesIO(b'contenido')
                )
                hilo.join(2)
        self.assertEqual(resultado.veredicto, VeredictoAntivirus.CLEAN)
        self.assertFalse(hilo.is_alive())

    @override_settings(
        FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET='',
        FINANCIACION_EDUCATIVA_CLAMAV_HOST='127.0.0.1',
        FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS=1,
        FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS=0.05,
    )
    def test_adaptador_clamav_aplica_timeout_real_de_lectura(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        with override_settings(
            FINANCIACION_EDUCATIVA_CLAMAV_PORT=listener.getsockname()[1]
        ):
            hilo = threading.Thread(
                target=servir_clamav,
                kwargs={
                    'listener': listener,
                    'respuesta': b'stream: OK\0',
                    'demora': 0.2,
                },
                daemon=True,
            )
            hilo.start()
            with self.assertRaises(ErrorEscaneoDocumento) as error:
                ClamAVDocumentScanBackend().escanear(BytesIO(b'contenido'))
            hilo.join(1)
        self.assertEqual(error.exception.codigo, 'SCANNER_TIMEOUT')

    def test_adaptador_rechaza_respuesta_incompleta(self):
        conexion = patch(
            'financiacion_educativa.services.escaneo_documentos.'
            'ClamAVDocumentScanBackend._conectar'
        )
        with conexion as conectar:
            conectar.return_value.recv.side_effect = [b'stream: OK', b'']
            with self.assertRaises(ErrorEscaneoDocumento) as error:
                ClamAVDocumentScanBackend().escanear(BytesIO(b'contenido'))
        self.assertEqual(error.exception.codigo, 'INVALID_RESPONSE')

    def test_archivo_infectado_queda_bloqueado(self):
        documento = self.documento()
        procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendInfectado(),
        )
        documento.refresh_from_db()

        self.assertEqual(
            documento.estado_escaneo,
            EstadoEscaneoDocumento.BLOCKED,
        )
        self.assertEqual(
            documento.intentos_escaneo.get().estado,
            EstadoIntentoEscaneoDocumento.INFECTED,
        )

    def test_fallos_operativos_mantienen_estado_pendiente(self):
        for backend, codigo in (
            (BackendNoDisponible(), 'SCANNER_UNAVAILABLE'),
            (BackendTimeout(), 'SCANNER_TIMEOUT'),
            (BackendRespuestaInvalida(), 'INVALID_RESPONSE'),
        ):
            documento = self.documento()
            resultado = procesar_escaneo_documento(
                documento=documento,
                actor=self.operador,
                backend=backend,
            )
            documento.refresh_from_db()

            self.assertEqual(resultado.codigo_error, codigo)
            self.assertEqual(
                documento.estado_escaneo,
                EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
            )
            self.assertEqual(
                documento.intentos_escaneo.order_by('-numero').first().estado,
                EstadoIntentoEscaneoDocumento.ERROR,
            )

    def test_documento_resuelto_no_se_reprocesa(self):
        documento = self.documento()
        procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendLimpio(),
        )
        segundo = procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendInfectado(),
        )

        documento.refresh_from_db()
        self.assertFalse(segundo.procesado)
        self.assertEqual(segundo.estado, 'ALREADY_RESOLVED')
        self.assertEqual(documento.intentos_escaneo.count(), 1)
        self.assertEqual(documento.estado_escaneo, EstadoEscaneoDocumento.SAFE)

    @override_settings(FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS=2)
    def test_reintentos_son_controlados_e_idempotentes(self):
        documento = self.documento()
        for _ in range(2):
            procesar_escaneo_documento(
                documento=documento,
                actor=self.operador,
                backend=BackendNoDisponible(),
            )
        tercero = procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendLimpio(),
        )

        self.assertFalse(tercero.procesado)
        self.assertEqual(tercero.estado, 'MAX_ATTEMPTS')
        self.assertEqual(documento.intentos_escaneo.count(), 2)

    def test_pending_a_safe_requiere_intento_limpio(self):
        documento = self.documento()
        documento.estado_escaneo = EstadoEscaneoDocumento.SAFE
        with self.assertRaises(ValidationError):
            documento.full_clean()
        with self.assertRaises(ValidationError):
            documento.save()

    def test_intento_terminal_no_puede_fabricarse_con_save(self):
        documento = self.documento()
        with self.assertRaises(ValidationError):
            IntentoEscaneoDocumento.objects.create(
                documento=documento,
                numero=1,
                estado=EstadoIntentoEscaneoDocumento.CLEAN,
                origen=OrigenIntentoEscaneoDocumento.ADMIN,
                solicitado_por=self.operador,
                veredicto=EstadoIntentoEscaneoDocumento.CLEAN,
                finalizado_en=timezone.now(),
            )

    def test_estados_no_limpios_y_otro_documento_no_autorizan_safe(self):
        for indice, estado in enumerate((
            EstadoIntentoEscaneoDocumento.STARTED,
            EstadoIntentoEscaneoDocumento.ERROR,
            EstadoIntentoEscaneoDocumento.INFECTED,
        )):
            documento = self.documento(f'estado-{indice}')
            intento = IntentoEscaneoDocumento.objects.create(
                documento=documento,
                numero=1,
                origen=OrigenIntentoEscaneoDocumento.ADMIN,
                solicitado_por=self.operador,
            )
            if estado != EstadoIntentoEscaneoDocumento.STARTED:
                IntentoEscaneoDocumento.objects.filter(pk=intento.pk).update(
                    estado=estado,
                    finalizado_en=timezone.now(),
                )
            documento.estado_escaneo = EstadoEscaneoDocumento.SAFE
            with self.assertRaises(ValidationError):
                documento.save()
            type(documento).objects.filter(pk=documento.pk).update(activo=False)

        documento = self.documento('principal')
        type(documento).objects.filter(pk=documento.pk).update(activo=False)
        otro = self.documento('otro')
        procesar_escaneo_documento(
            documento=otro,
            actor=self.operador,
            backend=BackendLimpio(),
        )
        documento.estado_escaneo = EstadoEscaneoDocumento.SAFE
        with self.assertRaises(ValidationError):
            documento.save()

    def test_documento_historico_safe_admite_actualizar_otros_campos(self):
        documento = self.documento()
        forzar_seguridad_documento_historico(
            documento=documento,
            estado_escaneo=EstadoEscaneoDocumento.SAFE,
        )
        documento.refresh_from_db()
        documento.observacion_revision = 'Registro historico preservado'
        documento.full_clean()
        documento.save(update_fields=['observacion_revision', 'actualizado_en'])

    def test_safe_rechaza_puntero_de_otro_documento(self):
        documento = self.documento('principal-safe')
        procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendLimpio(),
        )
        type(documento).objects.filter(pk=documento.pk).update(activo=False)
        otro = self.documento('otro-safe')
        procesar_escaneo_documento(
            documento=otro,
            actor=self.operador,
            backend=BackendLimpio(),
        )
        otro.refresh_from_db()
        documento.refresh_from_db()
        documento.ultimo_intento_limpio = otro.ultimo_intento_limpio

        self.assert_safe_invalido_en_clean_y_save(documento)

    def test_safe_rechaza_sustituir_puntero_por_intento_no_limpio(self):
        estados = (
            EstadoIntentoEscaneoDocumento.STARTED,
            EstadoIntentoEscaneoDocumento.ERROR,
            EstadoIntentoEscaneoDocumento.INFECTED,
        )
        for indice, estado in enumerate(estados, start=1):
            with self.subTest(estado=estado):
                documento = self.documento(f'safe-{indice}')
                procesar_escaneo_documento(
                    documento=documento,
                    actor=self.operador,
                    backend=BackendLimpio(),
                )
                intento = IntentoEscaneoDocumento.objects.create(
                    documento=documento,
                    numero=2,
                    origen=OrigenIntentoEscaneoDocumento.ADMIN,
                    solicitado_por=self.operador,
                )
                if estado != EstadoIntentoEscaneoDocumento.STARTED:
                    IntentoEscaneoDocumento.objects.filter(pk=intento.pk).update(
                        estado=estado,
                        finalizado_en=timezone.now(),
                    )
                documento.refresh_from_db()
                documento.ultimo_intento_limpio_id = intento.pk

                self.assert_safe_invalido_en_clean_y_save(documento)
                type(documento).objects.filter(pk=documento.pk).update(
                    activo=False
                )

    def test_safe_rechaza_borrar_puntero_limpio(self):
        documento = self.documento('safe-con-puntero')
        procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendLimpio(),
        )
        documento.refresh_from_db()
        documento.ultimo_intento_limpio = None

        self.assert_safe_invalido_en_clean_y_save(documento)

    def test_safe_rechaza_intento_limpio_anterior_al_marcador(self):
        documento = self.documento('safe-intento-antiguo')
        procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendLimpio(),
        )
        intento = documento.intentos_escaneo.get()
        forzar_seguridad_documento_historico(
            documento=documento,
            estado_escaneo=EstadoEscaneoDocumento.SAFE,
            ultimo_intento_limpio_id=intento.pk,
            escaneo_requerido_desde=(
                intento.finalizado_en + timedelta(seconds=1)
            ),
        )
        documento.refresh_from_db()

        self.assert_safe_invalido_en_clean_y_save(documento)

    def test_safe_conserva_puntero_y_admite_actualizar_otro_campo(self):
        documento = self.documento('safe-sin-cambios-seguridad')
        procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendLimpio(),
        )
        documento.refresh_from_db()
        intento_id = documento.ultimo_intento_limpio_id
        documento.observacion_revision = 'Actualizacion legitima'

        documento.full_clean()
        documento.save(update_fields=['observacion_revision', 'actualizado_en'])
        documento.refresh_from_db()

        self.assertEqual(documento.ultimo_intento_limpio_id, intento_id)
        self.assertEqual(documento.observacion_revision, 'Actualizacion legitima')

    def test_update_rechaza_todos_los_campos_de_seguridad(self):
        documento = self.documento('update-protegido')
        intento = IntentoEscaneoDocumento.objects.create(
            documento=documento,
            numero=1,
            origen=OrigenIntentoEscaneoDocumento.ADMIN,
            solicitado_por=self.operador,
        )
        casos = (
            {'estado_escaneo': EstadoEscaneoDocumento.SAFE},
            {'ultimo_intento_limpio_id': intento.pk},
            {'ultimo_intento_limpio': intento},
            {'escaneo_requerido_desde': timezone.now()},
            {'estado_escaneo': F('estado_escaneo')},
        )
        for valores in casos:
            with self.subTest(campos=tuple(valores)), self.assertRaises(
                ValidationError
            ):
                DocumentoFinanciacion.objects.filter(pk=documento.pk).update(
                    **valores
                )
        with self.assertRaises(ValidationError):
            self.solicitud.documentos.filter(pk=documento.pk).update(
                estado_escaneo=EstadoEscaneoDocumento.SAFE
            )

    def test_managers_base_default_y_relacionado_estan_protegidos(self):
        self.assertIsInstance(
            DocumentoFinanciacion._default_manager,
            DocumentoFinanciacionManager,
        )
        self.assertIsInstance(
            DocumentoFinanciacion._base_manager,
            DocumentoFinanciacionManager,
        )
        self.assertIsInstance(
            DocumentoFinanciacion._default_manager.get_queryset(),
            DocumentoFinanciacionQuerySet,
        )
        self.assertIsInstance(
            DocumentoFinanciacion._base_manager.get_queryset(),
            DocumentoFinanciacionQuerySet,
        )
        self.assertIsInstance(
            self.solicitud.documentos,
            DocumentoFinanciacionManager,
        )
        self.assertIsInstance(
            self.solicitud.documentos.all(),
            DocumentoFinanciacionQuerySet,
        )
        self.assertEqual(
            [manager.name for manager in DocumentoFinanciacion._meta.managers],
            ['objects'],
        )

    def test_base_manager_bloquea_update_y_bulk_update_protegidos(self):
        documento = self.documento('base-manager')
        intento = IntentoEscaneoDocumento.objects.create(
            documento=documento,
            numero=1,
            origen=OrigenIntentoEscaneoDocumento.ADMIN,
        )
        casos_update = (
            {'estado_escaneo': EstadoEscaneoDocumento.SAFE},
            {'ultimo_intento_limpio': intento},
            {'ultimo_intento_limpio_id': intento.pk},
            {'escaneo_requerido_desde': timezone.now()},
        )
        for valores in casos_update:
            with self.subTest(update=tuple(valores)), self.assertRaises(
                ValidationError
            ):
                DocumentoFinanciacion._base_manager.filter(
                    pk=documento.pk
                ).update(**valores)

        casos_bulk = (
            'estado_escaneo',
            'ultimo_intento_limpio',
            'ultimo_intento_limpio_id',
            'escaneo_requerido_desde',
        )
        for campo in casos_bulk:
            with self.subTest(bulk_update=campo), self.assertRaises(
                ValidationError
            ):
                DocumentoFinanciacion._base_manager.bulk_update(
                    [documento],
                    [campo],
                )

        actualizados = DocumentoFinanciacion._base_manager.filter(
            pk=documento.pk
        ).update(observacion_revision='Base manager protegido')
        self.assertEqual(actualizados, 1)
        documento.refresh_from_db()
        self.assertEqual(
            documento.observacion_revision,
            'Base manager protegido',
        )

    def test_update_combinado_falla_sin_cambios_parciales(self):
        documento = self.documento('update-combinado')
        with self.assertRaises(ValidationError):
            DocumentoFinanciacion.objects.filter(pk=documento.pk).update(
                observacion_revision='No debe persistir',
                estado_escaneo=EstadoEscaneoDocumento.SAFE,
            )
        documento.refresh_from_db()
        self.assertEqual(documento.observacion_revision, '')
        self.assertEqual(
            documento.estado_escaneo,
            EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
        )

    def test_actualizaciones_masivas_de_campos_normales_funcionan(self):
        documento = self.documento('update-normal')
        actualizados = DocumentoFinanciacion.objects.filter(
            pk=documento.pk
        ).update(observacion_revision='Actualizado por queryset')
        self.assertEqual(actualizados, 1)
        documento.refresh_from_db()
        self.assertEqual(
            documento.observacion_revision,
            'Actualizado por queryset',
        )
        actualizados = DocumentoFinanciacion.objects.filter(
            pk=documento.pk
        ).update(observacion_revision=F('observacion_revision'))
        self.assertEqual(actualizados, 1)

        documento.observacion_revision = 'Actualizado por bulk'
        DocumentoFinanciacion.objects.bulk_update(
            [documento],
            ['observacion_revision'],
        )
        documento.refresh_from_db()
        self.assertEqual(documento.observacion_revision, 'Actualizado por bulk')

    def test_bulk_update_rechaza_campos_de_seguridad(self):
        for campo in (
            'estado_escaneo',
            'ultimo_intento_limpio',
            'escaneo_requerido_desde',
        ):
            with self.subTest(campo=campo):
                documento = self.documento(f'bulk-{campo}')
                if campo == 'estado_escaneo':
                    documento.estado_escaneo = EstadoEscaneoDocumento.SAFE
                elif campo == 'ultimo_intento_limpio':
                    intento = IntentoEscaneoDocumento.objects.create(
                        documento=documento,
                        numero=1,
                        origen=OrigenIntentoEscaneoDocumento.ADMIN,
                    )
                    documento.ultimo_intento_limpio = intento
                else:
                    documento.escaneo_requerido_desde = timezone.now()
                with self.assertRaises(ValidationError):
                    DocumentoFinanciacion.objects.bulk_update(
                        [documento],
                        [campo],
                    )
                DocumentoFinanciacion.objects.filter(pk=documento.pk).update(
                    activo=False
                )

    def test_bulk_create_no_permite_precargar_autorizacion_safe(self):
        inseguro = DocumentoFinanciacion(
            solicitud=self.solicitud,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            referencia_almacenamiento='historico/no-autorizado.pdf',
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            estado_escaneo=EstadoEscaneoDocumento.SAFE,
        )
        with self.assertRaises(ValidationError):
            DocumentoFinanciacion.objects.bulk_create([inseguro])

        normal = DocumentoFinanciacion(
            solicitud=self.solicitud,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            referencia_almacenamiento='historico/pendiente.pdf',
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
        )
        creados = DocumentoFinanciacion.objects.bulk_create([normal])
        self.assertEqual(creados, [normal])
        self.assertTrue(
            DocumentoFinanciacion.objects.filter(pk=normal.pk).exists()
        )

    def test_bulk_create_upsert_rechaza_campos_protegidos_antes_de_sql(self):
        documento = self.documento('upsert-protegido')
        campos = (
            'estado_escaneo',
            'ultimo_intento_limpio',
            'ultimo_intento_limpio_id',
            'escaneo_requerido_desde',
        )
        for campo in campos:
            with self.subTest(campo=campo):
                candidato = DocumentoFinanciacion.objects.get(pk=documento.pk)
                candidato.observacion_revision = 'No debe persistir'
                with self.assertRaises(ValidationError):
                    DocumentoFinanciacion.objects.bulk_create(
                        [candidato],
                        update_conflicts=True,
                        update_fields=[campo],
                        unique_fields=['pk'],
                    )
                documento.refresh_from_db()
                self.assertEqual(documento.observacion_revision, '')
                self.assertEqual(
                    documento.estado_escaneo,
                    EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
                )

        candidato = DocumentoFinanciacion.objects.get(pk=documento.pk)
        candidato.observacion_revision = 'Tampoco debe persistir'
        with self.assertRaises(ValidationError):
            DocumentoFinanciacion.objects.bulk_create(
                [candidato],
                update_conflicts=True,
                update_fields=['observacion_revision', 'estado_escaneo'],
                unique_fields=['pk'],
            )
        documento.refresh_from_db()
        self.assertEqual(documento.observacion_revision, '')

    def test_bulk_create_upsert_protegido_rechaza_aun_sin_conflicto(self):
        candidato = DocumentoFinanciacion(
            solicitud=self.solicitud,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            referencia_almacenamiento='upsert/sin-conflicto.pdf',
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
        )
        with self.assertRaises(ValidationError):
            DocumentoFinanciacion.objects.bulk_create(
                [candidato],
                update_conflicts=True,
                update_fields=['estado_escaneo'],
                unique_fields=['pk'],
            )
        self.assertFalse(
            DocumentoFinanciacion.objects.filter(pk=candidato.pk).exists()
        )

    def test_bulk_create_upsert_normal_actualiza_e_inserta(self):
        existente = self.documento('upsert-normal')
        candidato = DocumentoFinanciacion.objects.get(pk=existente.pk)
        candidato.observacion_revision = 'Actualizado por upsert'
        DocumentoFinanciacion.objects.bulk_create(
            [candidato],
            update_conflicts=True,
            update_fields=['observacion_revision'],
            unique_fields=['pk'],
        )
        existente.refresh_from_db()
        self.assertEqual(
            existente.observacion_revision,
            'Actualizado por upsert',
        )

        solicitud_nueva = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia='UPSERT-INSERT-001',
        )
        nuevo = DocumentoFinanciacion(
            solicitud=solicitud_nueva,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            referencia_almacenamiento='upsert/nuevo.pdf',
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            observacion_revision='Insertado por upsert',
        )
        DocumentoFinanciacion.objects.bulk_create(
            [nuevo],
            update_conflicts=True,
            update_fields=['observacion_revision'],
            unique_fields=['pk'],
        )
        self.assertTrue(
            DocumentoFinanciacion.objects.filter(
                pk=nuevo.pk,
                observacion_revision='Insertado por upsert',
            ).exists()
        )

    def test_bulk_create_ignore_conflicts_y_lista_mixta(self):
        existente = self.documento('ignore-conflicts')
        candidato = DocumentoFinanciacion.objects.get(pk=existente.pk)
        candidato.observacion_revision = 'No debe actualizarse'
        DocumentoFinanciacion.objects.bulk_create(
            [candidato],
            ignore_conflicts=True,
        )
        existente.refresh_from_db()
        self.assertEqual(existente.observacion_revision, '')

        normal = DocumentoFinanciacion(
            solicitud=self.solicitud,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            referencia_almacenamiento='bulk/normal.pdf',
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
        )
        inseguro = DocumentoFinanciacion(
            solicitud=self.solicitud,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            referencia_almacenamiento='bulk/inseguro.pdf',
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            estado_escaneo=EstadoEscaneoDocumento.SAFE,
        )
        with self.assertRaises(ValidationError):
            DocumentoFinanciacion.objects.bulk_create([normal, inseguro])
        self.assertFalse(
            DocumentoFinanciacion.objects.filter(
                pk__in=[normal.pk, inseguro.pk]
            ).exists()
        )

    def test_get_or_create_y_update_or_create_respetan_save(self):
        with self.assertRaises(ValidationError):
            DocumentoFinanciacion.objects.get_or_create(
                pk=uuid.uuid4(),
                defaults={
                    'solicitud': self.solicitud,
                    'tipo': TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
                    'referencia_almacenamiento': 'get-or-create.pdf',
                    'origen_captura': OrigenCapturaDocumento.USER_UPLOAD,
                    'estado_escaneo': EstadoEscaneoDocumento.SAFE,
                },
            )

        documento = self.documento('update-or-create')
        with self.assertRaises(ValidationError):
            DocumentoFinanciacion.objects.update_or_create(
                pk=documento.pk,
                defaults={'estado_escaneo': EstadoEscaneoDocumento.SAFE},
            )
        documento.refresh_from_db()
        self.assertEqual(
            documento.estado_escaneo,
            EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
        )

    def test_salir_de_safe_exige_nuevo_veredicto_para_regresar(self):
        documento = self.documento()
        procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendLimpio(),
        )
        documento.refresh_from_db()
        documento.estado_escaneo = EstadoEscaneoDocumento.PENDING_SECURITY_SCAN
        documento.save(update_fields=['estado_escaneo', 'actualizado_en'])
        documento.estado_escaneo = EstadoEscaneoDocumento.SAFE
        with self.assertRaises(ValidationError):
            documento.save()

    @override_settings(
        FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS=1,
        FINANCIACION_EDUCATIVA_SCAN_MAX_REOPENINGS=1,
        FINANCIACION_EDUCATIVA_SCAN_REOPEN_EXTRA_ATTEMPTS=1,
    )
    def test_reapertura_es_privilegiada_auditada_y_limitada(self):
        documento = self.documento()
        procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendNoDisponible(),
        )
        with self.assertRaises(PermissionDenied):
            reabrir_escaneo_documento(
                documento=documento,
                actor=self.sin_permiso,
                motivo='Incidente resuelto',
            )
        with self.assertRaises(ValidationError):
            reabrir_escaneo_documento(
                documento=documento,
                actor=self.operador,
                motivo='   ',
            )

        reapertura = reabrir_escaneo_documento(
            documento=documento,
            actor=self.operador,
            motivo='Servicio antivirus restablecido',
        )
        self.assertEqual(reapertura.autorizado_por, self.operador)
        self.assertEqual(reapertura.intentos_adicionales, 1)
        self.assertEqual(reapertura.motivo, 'Servicio antivirus restablecido')

        procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendNoDisponible(),
        )
        agotado = procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendLimpio(),
        )
        self.assertEqual(agotado.estado, 'MAX_ATTEMPTS')
        with self.assertRaises(ValidationError):
            reabrir_escaneo_documento(
                documento=documento,
                actor=self.operador,
                motivo='Segundo intento de reapertura',
            )

    def test_reapertura_rechaza_intento_activo(self):
        documento = self.documento()
        IntentoEscaneoDocumento.objects.create(
            documento=documento,
            numero=1,
            origen=OrigenIntentoEscaneoDocumento.ADMIN,
            solicitado_por=self.operador,
        )
        with self.assertRaises(ValidationError):
            reabrir_escaneo_documento(
                documento=documento,
                actor=self.operador,
                motivo='Intento aparentemente detenido',
            )

    @override_settings(
        FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS=2,
        FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS=1,
    )
    def test_intento_abandonado_se_cierra_y_recupera_dentro_del_presupuesto(self):
        documento = self.documento()
        intento = IntentoEscaneoDocumento.objects.create(
            documento=documento,
            numero=1,
            origen=OrigenIntentoEscaneoDocumento.COMMAND,
        )
        IntentoEscaneoDocumento.objects.filter(pk=intento.pk).update(
            iniciado_en=timezone.now() - timedelta(seconds=2)
        )

        resultado = procesar_escaneo_documento(
            documento=documento,
            origen=OrigenIntentoEscaneoDocumento.COMMAND,
            backend=BackendLimpio(),
        )
        intento.refresh_from_db()
        documento.refresh_from_db()
        self.assertEqual(intento.estado, EstadoIntentoEscaneoDocumento.ERROR)
        self.assertEqual(intento.codigo_error, 'STALE_ATTEMPT')
        self.assertEqual(resultado.estado, EstadoEscaneoDocumento.SAFE)
        self.assertEqual(documento.intentos_escaneo.count(), 2)

    def test_permisos_de_escaneo_y_revision_son_especificos(self):
        documento = self.documento()
        with self.assertRaises(PermissionDenied):
            procesar_escaneo_documento(
                documento=documento,
                actor=self.sin_permiso,
                backend=BackendLimpio(),
            )
        with self.assertRaises(ValidationError):
            revisar_documento(
                documento=documento,
                actor=self.sin_permiso,
                aceptar=False,
            )

    def test_no_se_puede_aceptar_antes_de_safe(self):
        with self.assertRaises(ValidationError):
            revisar_documento(
                documento=self.documento(),
                actor=self.operador,
                aceptar=True,
            )

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=(
            'financiacion_educativa.tests.scan_backends.BackendLimpio'
        )
    )
    def test_comando_procesa_pendientes_por_el_puerto(self):
        documento = self.documento()
        call_command(
            'procesar_escaneos_documentales',
            documento_id=str(documento.pk),
            verbosity=0,
        )
        documento.refresh_from_db()

        self.assertEqual(documento.estado_escaneo, EstadoEscaneoDocumento.SAFE)
        self.assertEqual(documento.intentos_escaneo.count(), 1)

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=(
            'financiacion_educativa.tests.scan_backends.BackendLimpio'
        )
    )
    def test_comando_solicitud_id_no_procesa_otros_expedientes(self):
        primero = self.documento()
        otra_solicitud = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia='REF-SCAN-OTRA',
            usuario=self.propietario,
        )
        otra_solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        otra_solicitud.save(update_fields=['estado'])
        segundo = registrar_documento(
            solicitud=otra_solicitud,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=pdf(),
            actor=self.propietario,
        )

        call_command(
            'procesar_escaneos_documentales',
            solicitud_id=str(self.solicitud.pk),
            verbosity=0,
        )
        primero.refresh_from_db()
        segundo.refresh_from_db()
        self.assertEqual(primero.estado_escaneo, EstadoEscaneoDocumento.SAFE)
        self.assertEqual(
            segundo.estado_escaneo,
            EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
        )

    @override_settings(
        FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS=1,
        FINANCIACION_EDUCATIVA_SCAN_MAX_REOPENINGS=1,
        FINANCIACION_EDUCATIVA_SCAN_REOPEN_EXTRA_ATTEMPTS=1,
    )
    def test_comando_reapertura_exige_actor_y_registra_auditoria(self):
        documento = self.documento()
        procesar_escaneo_documento(
            documento=documento,
            actor=self.operador,
            backend=BackendNoDisponible(),
        )
        salida = StringIO()
        call_command(
            'reabrir_escaneo_documental',
            documento_id=str(documento.pk),
            actor_id=str(self.operador.pk),
            motivo='ClamAV fue restablecido',
            stdout=salida,
        )
        self.assertEqual(documento.reaperturas_escaneo.count(), 1)
        with self.assertRaises(CommandError):
            call_command(
                'reabrir_escaneo_documental',
                documento_id=str(documento.pk),
                actor_id=str(self.operador.pk),
                motivo='No debe superar el limite',
                stdout=StringIO(),
            )
