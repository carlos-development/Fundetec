from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    EstadoValidacionIADocumento,
    OrigenCapturaDocumento,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import ValidacionIADocumento
from financiacion_educativa.services.documentos import registrar_documento
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.services.validacion_documental_ia import (
    DisabledDocumentAIValidationBackend,
    ErrorValidacionDocumentalIA,
    OpenAIDocumentAIValidationBackend,
    normalizar_resultado_validacion,
    procesar_validacion_documental_ia,
)
from financiacion_educativa.tests.ai_validation_backends import (
    BackendIABajaConfianza,
    BackendIAConcluyente,
    BackendIAError,
    BackendIAInconsistente,
)
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.scan_helpers import (
    conceder_permisos_documentales,
    registrar_resultado_escaneo,
)


TEST_AI_BACKEND = (
    'financiacion_educativa.tests.ai_validation_backends.BackendIAConcluyente'
)


def imagen_jpeg(nombre='documento.jpg', marca=b'documento-prueba'):
    return SimpleUploadedFile(
        nombre,
        b'\xff\xd8\xff' + marca + b'\xff\xd9',
        content_type='image/jpeg',
    )


class ValidacionDocumentalIATests(TestCase):
    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.override = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name,
            FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_CONFIDENCE='0.90',
            FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_QUALITY='0.80',
            FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_LEGIBILITY='0.80',
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.private_root.cleanup)

        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='ia-owner@example.com',
            email='ia-owner@example.com',
            password='Clave-2026',
        )
        self.operador = User.objects.create_user(
            username='ia-operator@example.com',
            email='ia-operator@example.com',
            password='Clave-2026',
            is_staff=True,
        )
        conceder_permisos_documentales(self.operador)
        permiso_ia = Permission.objects.get(
            content_type__app_label='financiacion_educativa',
            codename='procesar_validacion_ia_documento',
        )
        self.operador.user_permissions.add(permiso_ia)
        self.operador.__dict__.pop('_perm_cache', None)
        self.sin_permiso = User.objects.create_user(
            username='ia-no-permission@example.com',
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
                correo='ia-owner@example.com',
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )

    def documento(self, marca=b'documento-prueba'):
        return registrar_documento(
            solicitud=self.solicitud,
            participante=self.participante,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=imagen_jpeg(marca=marca),
            actor=self.usuario,
        )

    def documento_seguro(self, marca=b'documento-prueba'):
        documento = self.documento(marca)
        registrar_resultado_escaneo(
            documento=documento,
            actor=self.operador,
            estado=EstadoEscaneoDocumento.SAFE,
        )
        return documento

    def test_solo_documentos_safe_y_operadores_autorizados(self):
        documento = self.documento()

        with self.assertRaises(PermissionDenied):
            procesar_validacion_documental_ia(
                documento=documento,
                actor=self.sin_permiso,
                backend=BackendIAConcluyente(),
            )
        resultado = procesar_validacion_documental_ia(
            documento=documento,
            actor=self.operador,
            backend=BackendIAConcluyente(),
        )

        self.assertEqual(resultado.estado, 'SECURITY_SCAN_REQUIRED')
        self.assertFalse(ValidacionIADocumento.objects.exists())

    def test_resultado_concluyente_acepta_documento_y_conserva_escaneo(self):
        documento = self.documento_seguro()
        scan_summary = dict(documento.resultado_procesamiento)

        resultado = procesar_validacion_documental_ia(
            documento=documento,
            actor=self.operador,
            backend=BackendIAConcluyente(),
        )

        documento.refresh_from_db()
        validacion = documento.validaciones_ia.get()
        self.assertEqual(resultado.estado, EstadoValidacionIADocumento.AUTO_APPROVED)
        self.assertEqual(
            documento.estado_validacion,
            EstadoValidacionDocumento.APPROVED,
        )
        self.assertEqual(documento.estado_escaneo, EstadoEscaneoDocumento.SAFE)
        self.assertIsNone(documento.revisado_por)
        self.assertEqual(documento.nivel_confianza, validacion.confianza)
        self.assertEqual(validacion.calidad, Decimal('0.9800'))
        self.assertEqual(validacion.estado, EstadoValidacionIADocumento.AUTO_APPROVED)
        for key, value in scan_summary.items():
            self.assertEqual(documento.resultado_procesamiento[key], value)
        serialized = str(documento.resultado_procesamiento)
        self.assertNotIn('1000123456', serialized)
        self.assertNotIn('ia-owner@example.com', serialized)
        self.assertNotIn(documento.archivo.name, serialized)

    def test_baja_confianza_o_inconsistencia_siempre_deriva_a_revision(self):
        for index, backend in enumerate(
            (BackendIABajaConfianza(), BackendIAInconsistente()),
            start=1,
        ):
            with self.subTest(backend=type(backend).__name__):
                documento = self.documento_seguro(f'imagen-{index}'.encode())
                resultado = procesar_validacion_documental_ia(
                    documento=documento,
                    actor=self.operador,
                    backend=backend,
                )
                documento.refresh_from_db()
                self.assertEqual(
                    resultado.estado,
                    EstadoValidacionIADocumento.MANUAL_REVIEW,
                )
                self.assertEqual(
                    documento.estado_validacion,
                    EstadoValidacionDocumento.PENDING,
                )
                self.assertNotEqual(
                    documento.estado_validacion,
                    EstadoValidacionDocumento.REJECTED,
                )
                documento.activo = False
                documento.save(update_fields=['activo', 'actualizado_en'])

    def test_fallo_tecnico_es_cerrado_y_no_rechaza(self):
        documento = self.documento_seguro()

        resultado = procesar_validacion_documental_ia(
            documento=documento,
            actor=self.operador,
            backend=BackendIAError(),
        )

        documento.refresh_from_db()
        validacion = documento.validaciones_ia.get()
        self.assertEqual(resultado.estado, EstadoValidacionIADocumento.ERROR)
        self.assertEqual(resultado.codigo_error, 'PROVIDER_TIMEOUT')
        self.assertEqual(validacion.codigo_error, 'PROVIDER_TIMEOUT')
        self.assertEqual(
            documento.estado_validacion,
            EstadoValidacionDocumento.PENDING,
        )
        self.assertNotIn('documento-prueba', str(documento.resultado_procesamiento))

    def test_backend_deshabilitado_no_consume_intentos(self):
        documento = self.documento_seguro()

        resultado = procesar_validacion_documental_ia(
            documento=documento,
            actor=self.operador,
            backend=DisabledDocumentAIValidationBackend(),
        )

        self.assertEqual(resultado.estado, 'DISABLED')
        self.assertFalse(documento.validaciones_ia.exists())

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_MAX_ATTEMPTS=3,
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_STALE_SECONDS=1,
    )
    def test_intento_abandonado_se_recupera_sin_duplicado_activo(self):
        documento = self.documento_seguro()
        abandonado = ValidacionIADocumento.objects.create(
            documento=documento,
            numero=1,
            origen='COMMAND',
        )
        ValidacionIADocumento.objects.filter(pk=abandonado.pk).update(
            iniciado_en=timezone.now() - timedelta(seconds=5)
        )

        resultado = procesar_validacion_documental_ia(
            documento=documento,
            actor=self.operador,
            backend=BackendIAConcluyente(),
        )

        abandonado.refresh_from_db()
        self.assertEqual(abandonado.estado, EstadoValidacionIADocumento.ERROR)
        self.assertEqual(abandonado.codigo_error, 'STALE_ATTEMPT')
        self.assertEqual(resultado.estado, EstadoValidacionIADocumento.AUTO_APPROVED)
        self.assertEqual(documento.validaciones_ia.count(), 2)
        self.assertFalse(
            documento.validaciones_ia.filter(
                estado=EstadoValidacionIADocumento.STARTED
            ).exists()
        )

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=TEST_AI_BACKEND,
        FINANCIACION_EDUCATIVA_ALLOW_TEST_AI_BACKENDS=True,
    )
    def test_comando_filtra_estrictamente_por_solicitud(self):
        primero = self.documento_seguro(b'primero')
        otra = crear_solicitud(
            institucion=self.solicitud.institucion,
            referencia='REF-IA-2',
            usuario=self.usuario,
        )
        otra.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        otra.save(update_fields=['estado'])
        segundo = registrar_documento(
            solicitud=otra,
            tipo=TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=imagen_jpeg('otro.jpg', b'segundo'),
            actor=self.usuario,
        )
        registrar_resultado_escaneo(
            documento=segundo,
            actor=self.operador,
            estado=EstadoEscaneoDocumento.SAFE,
        )

        call_command(
            'procesar_validaciones_ia_documentales',
            solicitud_id=self.solicitud.pk,
            stdout=StringIO(),
        )

        primero.refresh_from_db()
        segundo.refresh_from_db()
        self.assertEqual(primero.estado_validacion, EstadoValidacionDocumento.APPROVED)
        self.assertEqual(segundo.estado_validacion, EstadoValidacionDocumento.PENDING)
        self.assertFalse(segundo.validaciones_ia.exists())

    def test_comando_deshabilitado_falla_sin_crear_intentos(self):
        documento = self.documento_seguro()
        with self.assertRaises(CommandError):
            call_command(
                'procesar_validaciones_ia_documentales',
                documento_id=documento.pk,
                stdout=StringIO(),
            )
        self.assertFalse(documento.validaciones_ia.exists())


class AdaptadorOpenAIValidacionDocumentalTests(TestCase):
    payload = {
        'quality_score': 0.98,
        'legibility_score': 0.97,
        'confidence': 0.99,
        'document_type_match': True,
        'appears_real': True,
        'data_consistent': True,
        'finding_codes': [],
    }

    def test_normalizacion_exige_esquema_cerrado_y_puntajes_validos(self):
        resultado = normalizar_resultado_validacion(
            self.payload,
            proveedor='proveedor-controlado',
            modelo='modelo-controlado',
        )
        self.assertEqual(resultado.confianza, Decimal('0.9900'))

        with self.assertRaises(ErrorValidacionDocumentalIA):
            normalizar_resultado_validacion(
                {**self.payload, 'campo_inesperado': 'dato'},
            )
        with self.assertRaises(ErrorValidacionDocumentalIA):
            normalizar_resultado_validacion(
                {**self.payload, 'confidence': 1.1},
            )

    @override_settings(
        OPENAI_API_KEY='test-key-not-real',
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_MODEL='test-model',
        FINANCIACION_EDUCATIVA_DOCUMENT_AI_TIMEOUT_SECONDS=7,
    )
    def test_adaptador_usa_respuesta_estructurada_y_timeout(self):
        response = SimpleNamespace(output_text=json.dumps(self.payload))
        with patch('openai.OpenAI') as openai_class:
            openai_class.return_value.responses.create.return_value = response
            resultado = OpenAIDocumentAIValidationBackend().validar(
                contenido=b'contenido-imagen',
                content_type='image/jpeg',
                tipo_esperado='Identificacion - frente',
                contexto={'numero_documento': '1000123456'},
            )

        self.assertEqual(resultado.confianza, Decimal('0.9900'))
        openai_class.assert_called_once_with(
            api_key='test-key-not-real',
            timeout=7,
        )
        llamada = openai_class.return_value.responses.create.call_args.kwargs
        self.assertEqual(llamada['model'], 'test-model')
        self.assertEqual(llamada['text']['format']['type'], 'json_schema')
        self.assertTrue(llamada['text']['format']['strict'])
        self.assertFalse(llamada['store'])
