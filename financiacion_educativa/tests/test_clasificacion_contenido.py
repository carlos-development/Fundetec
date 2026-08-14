from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from financiacion_educativa.choices import (
    CategoriaContenidoDocumento,
    EstadoProcesamientoContenidoDocumento,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import (
    ProcesamientoContenidoDocumento,
    ProcesamientoContenidoDocumentoManager,
)
from financiacion_educativa.services.clasificacion_contenido_documental import (
    ErrorClasificacionContenido,
    decidir_politica_contenido,
    esquema_clasificacion_contenido,
    normalizar_clasificacion,
    procesar_contenido_documental,
)
from financiacion_educativa.services.documentos import (
    registrar_documento,
    reemplazar_documento,
)
from financiacion_educativa.services.escaneo_documentos import (
    procesar_escaneo_documento,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.tests.content_validation_backends import (
    BackendContenidoAmbiguo,
    BackendContenidoConcluyente,
    BackendContenidoContradictorio,
    BackendContenidoPermanente,
    BackendContenidoTemporal,
    resultado_concluyente,
)
from financiacion_educativa.tests.factories import crear_solicitud, imagen_jpeg_prueba
from financiacion_educativa.tests.scan_backends import BackendLimpio
from financiacion_educativa.tests.test_procesamiento_pdf import pdf_sintetico


CONTENT_BACKEND = (
    'financiacion_educativa.tests.content_validation_backends.'
    'BackendContenidoConcluyente'
)


@override_settings(
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=True,
    FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND=CONTENT_BACKEND,
    FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS=True,
    FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY='content-test-hmac-key',
    FINANCIACION_EDUCATIVA_PDF_MAX_BYTES=1024 * 1024,
    FINANCIACION_EDUCATIVA_PDF_MAX_PAGES=5,
    FINANCIACION_EDUCATIVA_PDF_MAX_OBJECTS=2000,
    FINANCIACION_EDUCATIVA_PDF_MAX_OBJECT_BYTES=512 * 1024,
    FINANCIACION_EDUCATIVA_PDF_MAX_PIXELS_PER_PAGE=1_000_000,
    FINANCIACION_EDUCATIVA_PDF_MAX_AI_PAGES=3,
    FINANCIACION_EDUCATIVA_PDF_MAX_EXTRACTED_CHARACTERS=10000,
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS=10,
    FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS=False,
)
class ClasificacionContenidoDocumentalTests(TestCase):
    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.override = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.private_root.cleanup)
        self.usuario = get_user_model().objects.create_user(
            username='contenido@example.com',
            email='contenido@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud(
            usuario=self.usuario,
            referencia='CONTENT-001',
        )
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.periodo_academico = '2026-2'
        self.solicitud.save(update_fields=['estado', 'periodo_academico'])
        self.deudor = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='ANA MARIA',
                apellidos='PEREZ LOPEZ',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='10000001',
                fecha_nacimiento=date(1990, 1, 1),
                correo='ana@example.com',
                telefono='3001234567',
                relacion_estudiante=RelacionEstudiante.SELF,
                pais_expedicion='CO',
            ),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )

    def _archivo_pdf(self, nombre='ingresos.pdf', texto='CERTIFICADO DE INGRESOS'):
        return SimpleUploadedFile(
            nombre,
            pdf_sintetico(textos=(f'{texto} ANA MARIA PEREZ LOPEZ PERIODO 2026 VALORES',)),
            content_type='application/pdf',
        )

    def _documento(self, *, tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE):
        documento = registrar_documento(
            solicitud=self.solicitud,
            participante=(
                None if tipo == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE
                else self.deudor
            ),
            tipo=tipo,
            origen_captura='USER_UPLOAD',
            archivo=self._archivo_pdf(
                nombre='matricula.pdf' if tipo == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE else 'ingresos.pdf',
                texto='SOPORTE DE MATRICULA' if tipo == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE else 'CERTIFICADO DE INGRESOS',
            ),
            actor=self.usuario,
        )
        procesar_escaneo_documento(
            documento=documento,
            origen='AUTOMATIC',
            backend=BackendLimpio(),
        )
        documento.refresh_from_db()
        return documento

    def test_pdf_valido_se_acepta_y_guarda_traza_minima(self):
        documento = self._documento()

        resultado = procesar_contenido_documental(documento=documento)

        documento.refresh_from_db()
        traza = documento.procesamientos_contenido.get()
        self.assertEqual(resultado.estado, EstadoProcesamientoContenidoDocumento.ACCEPTED)
        self.assertEqual(documento.estado_validacion, EstadoValidacionDocumento.APPROVED)
        self.assertEqual(traza.clasificacion, CategoriaContenidoDocumento.INCOME_CERTIFICATE)
        self.assertEqual(traza.metodo_extraccion, 'PDF_HYBRID')
        self.assertNotIn('holder_document_number', traza.campos_estructurados)
        self.assertIn('holder_document_hash', traza.campos_estructurados)

    def test_clasificacion_no_inicia_antes_de_clamav_safe(self):
        documento = registrar_documento(
            solicitud=self.solicitud,
            participante=self.deudor,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura='USER_UPLOAD',
            archivo=self._archivo_pdf(),
            actor=self.usuario,
        )

        resultado = procesar_contenido_documental(documento=documento)

        self.assertEqual(resultado.codigo, 'SECURITY_SCAN_REQUIRED')
        self.assertFalse(documento.procesamientos_contenido.exists())

    def test_repeticion_no_crea_otro_procesamiento(self):
        documento = self._documento()
        primero = procesar_contenido_documental(documento=documento)

        segundo = procesar_contenido_documental(documento=documento)

        self.assertEqual(primero.procesamiento_id, segundo.procesamiento_id)
        self.assertEqual(documento.procesamientos_contenido.count(), 1)

    def test_traza_finalizada_es_inmutable_por_save_y_update(self):
        documento = self._documento()
        procesar_contenido_documental(documento=documento)
        traza = documento.procesamientos_contenido.get()
        traza.codigos_razon = ['ALTERED']

        with self.assertRaises(ValidationError):
            traza.save()
        with self.assertRaises(ValidationError):
            documento.procesamientos_contenido.update(codigos_razon=['ALTERED'])

    def test_manager_base_y_bulk_create_no_permiten_saltar_inmutabilidad(self):
        documento = self._documento()
        procesar_contenido_documental(documento=documento)
        traza = documento.procesamientos_contenido.get()

        self.assertIsInstance(
            ProcesamientoContenidoDocumento._base_manager,
            ProcesamientoContenidoDocumentoManager,
        )
        with self.assertRaises(ValidationError):
            ProcesamientoContenidoDocumento._base_manager.update(
                codigos_razon=['ALTERED'],
            )
        with self.assertRaises(ValidationError):
            ProcesamientoContenidoDocumento.objects.bulk_create([
                ProcesamientoContenidoDocumento(
                    documento=documento,
                    numero=traza.numero + 1,
                    hash_original=traza.hash_original,
                    content_type=traza.content_type,
                    tamano_bytes=traza.tamano_bytes,
                    estado=EstadoProcesamientoContenidoDocumento.ACCEPTED,
                )
            ])

    def test_pdf_cifrado_exige_correccion_y_deja_traza(self):
        documento = registrar_documento(
            solicitud=self.solicitud,
            participante=self.deudor,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura='USER_UPLOAD',
            archivo=SimpleUploadedFile(
                'cifrado.pdf',
                pdf_sintetico(cifrado=True),
                content_type='application/pdf',
            ),
            actor=self.usuario,
        )
        procesar_escaneo_documento(
            documento=documento,
            origen='AUTOMATIC',
            backend=BackendLimpio(),
        )
        documento.refresh_from_db()

        resultado = procesar_contenido_documental(documento=documento)

        traza = documento.procesamientos_contenido.get()
        self.assertEqual(resultado.estado, EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED)
        self.assertTrue(traza.pdf_cifrado)
        self.assertEqual(traza.codigos_razon, ['PDF_ENCRYPTED'])

    def test_titular_con_tildes_orden_y_segundo_apellido_no_se_rechaza(self):
        documento = self._documento()
        backend = BackendContenidoConcluyente()
        original = backend.clasificar

        def clasificar(**kwargs):
            resultado = original(**kwargs)
            campos = dict(resultado.campos_extraidos)
            campos['holder_name'] = 'PEREZ ANA MARIA'
            return resultado.__class__(**{
                **resultado.__dict__,
                'campos_extraidos': campos,
            })

        backend.clasificar = clasificar
        resultado = procesar_contenido_documental(documento=documento, backend=backend)

        self.assertEqual(resultado.estado, EstadoProcesamientoContenidoDocumento.ACCEPTED)

    def test_identificacion_contradictoria_exige_correccion(self):
        documento = self._documento()

        resultado = procesar_contenido_documental(
            documento=documento,
            backend=BackendContenidoContradictorio(),
        )

        documento.refresh_from_db()
        self.assertEqual(resultado.estado, EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED)
        self.assertEqual(documento.estado_validacion, EstadoValidacionDocumento.REJECTED)

    def test_resultado_ambiguo_no_se_acepta_ni_rechaza(self):
        documento = self._documento()

        resultados = [
            procesar_contenido_documental(
                documento=documento,
                backend=BackendContenidoAmbiguo(),
            )
            for _ in range(3)
        ]

        documento.refresh_from_db()
        self.assertEqual(
            [resultado.estado for resultado in resultados],
            ['RETRYING', 'RETRYING', 'MANUAL_EXCEPTION'],
        )
        self.assertEqual(documento.estado_validacion, EstadoValidacionDocumento.PENDING)
        self.assertEqual(documento.procesamientos_contenido.count(), 3)

    def test_error_temporal_queda_reintentable(self):
        documento = self._documento()

        resultado = procesar_contenido_documental(
            documento=documento,
            backend=BackendContenidoTemporal(),
        )

        self.assertEqual(resultado.estado, EstadoProcesamientoContenidoDocumento.RETRYING)

    def test_error_permanente_falla_cerrado(self):
        documento = self._documento()

        resultado = procesar_contenido_documental(
            documento=documento,
            backend=BackendContenidoPermanente(),
        )

        self.assertEqual(resultado.estado, EstadoProcesamientoContenidoDocumento.FAILED)
        documento.refresh_from_db()
        self.assertEqual(documento.estado_validacion, EstadoValidacionDocumento.PENDING)

    def test_intento_abandonado_se_versiona_y_recupera(self):
        documento = self._documento()
        ProcesamientoContenidoDocumento.objects.create(
            documento=documento,
            numero=1,
            hash_original=documento.sha256,
            content_type=documento.content_type,
            tamano_bytes=documento.tamano_bytes,
            iniciado_en=timezone.now() - timedelta(hours=1),
        )

        resultado = procesar_contenido_documental(documento=documento)

        self.assertEqual(resultado.estado, EstadoProcesamientoContenidoDocumento.ACCEPTED)
        trazas = list(documento.procesamientos_contenido.order_by('numero'))
        self.assertEqual(len(trazas), 2)
        self.assertEqual(trazas[0].estado, EstadoProcesamientoContenidoDocumento.RETRYING)
        self.assertEqual(trazas[1].estado, EstadoProcesamientoContenidoDocumento.ACCEPTED)

    def test_version_reemplazada_invalida_resultado_tardio(self):
        documento = self._documento()

        class BackendReemplaza:
            enabled = True

            def clasificar(_self, *, tipo_esperado, contexto, **kwargs):
                reemplazar_documento(
                    documento=documento,
                    archivo=self._archivo_pdf('nuevo.pdf', 'CERTIFICADO LABORAL'),
                    actor=self.usuario,
                )
                return resultado_concluyente(
                    tipo_esperado=tipo_esperado,
                    contexto=contexto,
                )

        resultado = procesar_contenido_documental(
            documento=documento,
            backend=BackendReemplaza(),
        )

        documento.refresh_from_db()
        self.assertEqual(resultado.estado, EstadoProcesamientoContenidoDocumento.OBSOLETE)
        self.assertFalse(documento.activo)

    def test_soporte_matricula_valido_se_clasifica_por_contenido(self):
        documento = self._documento(
            tipo=TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE
        )

        resultado = procesar_contenido_documental(documento=documento)

        self.assertEqual(resultado.estado, EstadoProcesamientoContenidoDocumento.ACCEPTED)

    def test_soporte_matricula_jpeg_valido_se_clasifica_por_contenido(self):
        documento = registrar_documento(
            solicitud=self.solicitud,
            participante=None,
            tipo=TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE,
            origen_captura='USER_UPLOAD',
            archivo=imagen_jpeg_prueba('matricula.jpg', 'matricula-sintetica'),
            actor=self.usuario,
        )
        procesar_escaneo_documento(
            documento=documento,
            origen='AUTOMATIC',
            backend=BackendLimpio(),
        )
        documento.refresh_from_db()

        resultado = procesar_contenido_documental(documento=documento)

        self.assertEqual(resultado.estado, EstadoProcesamientoContenidoDocumento.ACCEPTED)
        self.assertEqual(
            documento.procesamientos_contenido.get().metodo_extraccion,
            'IMAGE',
        )

    def test_matricula_con_estudiante_institucion_o_curso_contradictorio_corrige(self):
        documento = self._documento(
            tipo=TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE
        )

        resultado = procesar_contenido_documental(
            documento=documento,
            backend=BackendContenidoContradictorio(),
        )

        self.assertEqual(
            resultado.estado,
            EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED,
        )
        traza = documento.procesamientos_contenido.get()
        self.assertIn('INSTITUTION_MISMATCH', traza.codigos_razon)

    def test_manipulacion_visible_nunca_se_acepta_automaticamente(self):
        base = resultado_concluyente(
            tipo_esperado=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            contexto={
                'holder_name': 'ANA MARIA PEREZ LOPEZ',
                'holder_document_number': '10000001',
                'institution_name': 'INSTITUCION',
                'program_name': 'PROGRAMA',
                'academic_period': '2026-2',
                'enrollment_reference': '',
            },
        )

        estado, razones = decidir_politica_contenido(
            replace(base, senales_manipulacion_visible=True),
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
        )

        self.assertEqual(estado, EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION)
        self.assertIn('TAMPERING_SIGNALS', razones)

    def test_matriz_admite_cinco_categorias_de_ingresos_sin_evaluar_monto(self):
        contexto = {
            'holder_name': 'ANA MARIA PEREZ',
            'holder_document_number': '10000001',
            'institution_name': 'INSTITUCION',
            'program_name': 'CURSO',
            'academic_period': '2026',
        }
        for categoria in (
            CategoriaContenidoDocumento.EMPLOYMENT_CERTIFICATE,
            CategoriaContenidoDocumento.INCOME_CERTIFICATE,
            CategoriaContenidoDocumento.INCOME_AND_WITHHOLDING_CERTIFICATE,
            CategoriaContenidoDocumento.BANK_STATEMENT,
            CategoriaContenidoDocumento.PAYSLIP,
        ):
            with self.subTest(categoria=categoria):
                resultado = resultado_concluyente(
                    tipo_esperado=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
                    contexto=contexto,
                    categoria=categoria,
                )
                estado, _ = decidir_politica_contenido(
                    resultado,
                    tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
                )
                self.assertEqual(estado, EstadoProcesamientoContenidoDocumento.ACCEPTED)

    def test_categoria_ajena_exige_correccion(self):
        contexto = {
            'holder_name': 'ANA MARIA PEREZ',
            'holder_document_number': '10000001',
            'institution_name': 'INSTITUCION',
            'program_name': 'CURSO',
            'academic_period': '2026',
        }
        resultado = resultado_concluyente(
            tipo_esperado=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            contexto=contexto,
            categoria=CategoriaContenidoDocumento.UNRELATED,
        )

        estado, razones = decidir_politica_contenido(
            resultado,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
        )

        self.assertEqual(estado, EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED)
        self.assertIn('CATEGORY_MISMATCH', razones)

    def test_esquema_ia_es_cerrado_y_sin_keywords_incompatibles(self):
        esquema = esquema_clasificacion_contenido()
        serializado = str(esquema)

        self.assertFalse(esquema['additionalProperties'])
        self.assertFalse(
            esquema['properties']['extracted_fields']['additionalProperties']
        )
        self.assertNotIn('uniqueItems', serializado)
        self.assertNotIn("'minimum'", serializado)
        self.assertNotIn("'maximum'", serializado)

    def test_respuesta_ia_con_propiedad_extra_se_rechaza_localmente(self):
        payload = {
            'document_category': 'INCOME_CERTIFICATE',
            'category_confidence': 0.95,
            'legibility': 0.95,
            'extraction_completeness': 0.95,
            'holder_match': 'MATCH',
            'institution_or_issuer_match': 'NOT_APPLICABLE',
            'date_or_period_present': True,
            'required_content_present': True,
            'visible_tampering_signals': False,
            'reason_codes': [],
            'extracted_fields': {
                'holder_name': 'PERSONA PRUEBA',
                'holder_document_number': None,
                'issuer_name': 'EMISOR',
                'institution_name': None,
                'program_name': None,
                'date_or_period': '2026',
                'evidence_kind': 'CERTIFICADO',
                'enrollment_reference': None,
                'financial_values_present': True,
            },
            'analyzed_pages': [1],
            'overall_outcome': 'ACCEPTED',
            'unexpected': 'not-allowed',
        }

        with self.assertRaises(ErrorClasificacionContenido):
            normalizar_clasificacion(payload)
