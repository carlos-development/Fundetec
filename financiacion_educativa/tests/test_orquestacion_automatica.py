import json
from datetime import date
from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from financiacion_educativa.choices import (
    EtapaAutomatizacionEducativa,
    EstadoArtefactoContractualEducativo,
    EstadoEvidenciaMatricula,
    EstadoOutboxCorreoEducativo,
    EstadoEscaneoDocumento,
    EstadoProcesoAutomatizacionEducativa,
    EstadoProcesoFirmaEducativa,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    EstadoValidacionIADocumento,
    RelacionEstudiante,
    RolParticipante,
    TipoArtefactoContractualEducativo,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    CondicionesFinancieras,
    EvidenciaMatricula,
    EventoWebhookFirmaEducativa,
    OutboxCorreoEducativo,
    ProcesoFirmaEducativa,
    ValidacionIADocumento,
)
from financiacion_educativa.services.documentos import (
    registrar_documento,
    reemplazar_documento,
)
from financiacion_educativa.services.cola_automatizacion import (
    encolar_proceso_automatizacion,
    procesar_siguiente_trabajo,
)
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.services.escaneo_documentos import (
    procesar_escaneo_documento,
)
from financiacion_educativa.services.matricula import (
    registrar_o_actualizar_evidencia_matricula,
)
from financiacion_educativa.services.orquestacion_automatica import (
    ejecutar_etapa_persistente,
    ejecutar_orquestacion_automatica,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.services.requisitos_documentales import (
    RequisitoDocumental,
    completar_fase_documental,
)
from financiacion_educativa.tests.ai_validation_backends import (
    BackendIAFallaUnaVez,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_solicitud,
    imagen_jpeg_prueba,
)
from financiacion_educativa.tests.signature_backends import (
    AmbiguousEducationalSignatureBackend,
    RecordingEducationalSignatureBackend,
)
from financiacion_educativa.tests.scan_backends import BackendLimpio
from financiacion_educativa.tests.test_procesamiento_pdf import pdf_sintetico


SCAN_CLEAN = 'financiacion_educativa.tests.scan_backends.BackendLimpio'
SCAN_INFECTED = 'financiacion_educativa.tests.scan_backends.BackendInfectado'
SCAN_UNAVAILABLE = (
    'financiacion_educativa.tests.scan_backends.BackendNoDisponible'
)
AI_CONCLUSIVE = (
    'financiacion_educativa.tests.ai_validation_backends.BackendIAConcluyente'
)
AI_LOW_CONFIDENCE = (
    'financiacion_educativa.tests.ai_validation_backends.BackendIABajaConfianza'
)
AI_NOT_REAL = (
    'financiacion_educativa.tests.ai_validation_backends.BackendIAImagenNoReal'
)
AI_NOT_IDENTITY = (
    'financiacion_educativa.tests.ai_validation_backends.BackendIANoEsDocumento'
)
AI_FLAKY = (
    'financiacion_educativa.tests.ai_validation_backends.BackendIAFallaUnaVez'
)
SIGNATURE = (
    'financiacion_educativa.tests.signature_backends.'
    'RecordingEducationalSignatureBackend'
)
SIGNATURE_AMBIGUOUS = (
    'financiacion_educativa.tests.signature_backends.'
    'AmbiguousEducationalSignatureBackend'
)
CONTENT_CONCLUSIVE = (
    'financiacion_educativa.tests.content_validation_backends.'
    'BackendContenidoConcluyente'
)
CONTENT_CONTRADICTORY = (
    'financiacion_educativa.tests.content_validation_backends.'
    'BackendContenidoContradictorio'
)


def imagen(nombre):
    return imagen_jpeg_prueba(f'{nombre}.jpg', nombre)


def pdf(nombre):
    return SimpleUploadedFile(
        f'{nombre}.pdf',
        b'%PDF-1.7\n' + nombre.encode('ascii') + b'\n%%EOF',
        content_type='application/pdf',
    )


@override_settings(
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True,
    FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=SCAN_CLEAN,
    FINANCIACION_EDUCATIVA_ALLOW_TEST_SCAN_BACKENDS=True,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=AI_CONCLUSIVE,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=True,
    FINANCIACION_EDUCATIVA_ALLOW_TEST_AI_BACKENDS=True,
    FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND=SIGNATURE,
    FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS=True,
    FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET='automatic-webhook-secret',
    FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY='automatic-hmac-key',
    FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL='ACREEDOR EDUCATIVO SAS',
    FINANCIACION_EDUCATIVA_ACREEDOR_NIT='900000000-1',
    FINANCIACION_EDUCATIVA_ACREEDOR_REPRESENTANTE_LEGAL='REPRESENTANTE PRUEBA',
    FINANCIACION_EDUCATIVA_ACREEDOR_DOMICILIO='Bogota D.C.',
    FINANCIACION_EDUCATIVA_PAGARE_VERSION_JURIDICA='1',
    FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION='OBLIGACION DE PRUEBA.',
    FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_CARTA_INSTRUCCIONES=(
        'CARTA DE INSTRUCCIONES DE PRUEBA.'
    ),
    FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_INCUMPLIMIENTO=(
        'INCUMPLIMIENTO DE PRUEBA.'
    ),
)
class OrquestacionAutomaticaTests(TestCase):
    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.override = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.private_root.cleanup)
        RecordingEducationalSignatureBackend.reset()
        AmbiguousEducationalSignatureBackend.reset()
        BackendIAFallaUnaVez.reset()
        crear_configuracion_financiera()
        self.usuario = get_user_model().objects.create_user(
            username='automatico@example.com',
            email='automatico@example.com',
            password='Clave-2026',
        )

    def _solicitud_base(self, referencia):
        solicitud = crear_solicitud(
            usuario=self.usuario,
            referencia=referencia,
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        solicitud.save(update_fields=['estado'])
        return solicitud

    def _participante(self, solicitud, *, tutor=False, menor=False):
        return registrar_o_actualizar_participante(
            solicitud=solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='TUTOR' if tutor else 'ESTUDIANTE',
                apellidos='AUTOMATICO',
                tipo_documento=(
                    TipoDocumentoIdentidad.CC
                    if tutor or not menor
                    else TipoDocumentoIdentidad.TI
                ),
                numero_documento=('80000002' if tutor else '10000001'),
                fecha_nacimiento=(
                    date(1980, 1, 1)
                    if tutor or not menor
                    else date(2012, 1, 1)
                ),
                correo=(
                    'tutor@example.com' if tutor else 'estudiante@example.com'
                ),
                telefono='3001234567',
                relacion_estudiante=(
                    RelacionEstudiante.LEGAL_GUARDIAN
                    if tutor
                    else RelacionEstudiante.SELF
                ),
                pais_expedicion='CO',
            ),
            roles=(
                {RolParticipante.GUARDIAN, RolParticipante.PRINCIPAL_DEBTOR}
                if tutor
                else (
                    {RolParticipante.STUDENT}
                    if menor
                    else {RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR}
                )
            ),
        )

    def _documento(self, solicitud, participante, tipo, sufijo):
        return registrar_documento(
            solicitud=solicitud,
            participante=participante,
            tipo=tipo,
            origen_captura=(
                'CAMERA'
                if tipo in {
                    TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                    TipoDocumentoFinanciacion.STUDENT_ID_BACK,
                    TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
                    TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
                }
                else 'USER_UPLOAD'
            ),
            archivo=imagen(sufijo),
            actor=self.usuario,
        )

    def _adulto_listo(self, referencia='AUTO-ADULTO'):
        solicitud = self._solicitud_base(referencia)
        adulto = self._participante(solicitud)
        self._documento(
            solicitud,
            adulto,
            TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            f'{referencia}-frente',
        )
        self._documento(
            solicitud,
            adulto,
            TipoDocumentoFinanciacion.STUDENT_ID_BACK,
            f'{referencia}-reverso',
        )
        self._documento(
            solicitud,
            adulto,
            TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            f'{referencia}-ingresos',
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        solicitud.save(update_fields=['estado'])
        return solicitud, adulto

    def _menor_listo(self):
        solicitud = self._solicitud_base('AUTO-MENOR')
        estudiante = self._participante(solicitud, menor=True)
        tutor = self._participante(solicitud, tutor=True)
        for participante, tipo, sufijo in (
            (estudiante, TipoDocumentoFinanciacion.STUDENT_ID_FRONT, 'menor-frente'),
            (estudiante, TipoDocumentoFinanciacion.STUDENT_ID_BACK, 'menor-reverso'),
            (tutor, TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT, 'tutor-frente'),
            (tutor, TipoDocumentoFinanciacion.GUARDIAN_ID_BACK, 'tutor-reverso'),
            (tutor, TipoDocumentoFinanciacion.INCOME_CERTIFICATE, 'tutor-ingresos'),
        ):
            self._documento(solicitud, participante, tipo, sufijo)
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        solicitud.save(update_fields=['estado'])
        return solicitud, estudiante, tutor

    def _firmar(self, proceso):
        payload = {
            'event_type': 'doc_signed',
            'token': proceso.token_documento_externo,
            'external_id': proceso.external_id,
            'status': 'signed',
        }
        return self.client.post(
            reverse('financiacion_educativa_api:zapsign-webhook'),
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_EDUCATIONAL_SIGNATURE_SECRET='automatic-webhook-secret',
        )

    def test_adulto_aprobado_automaticamente_solo_despues_de_firma(self):
        solicitud, _ = self._adulto_listo()

        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        solicitud.refresh_from_db()
        proceso = ProcesoFirmaEducativa.objects.get(solicitud=solicitud)
        publico_previo = obtener_resultado_publico(solicitud)

        self.assertEqual(resultado.codigo, 'PENDING_SIGNATURE')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_SIGNATURE)
        self.assertFalse(publico_previo.curso_autorizado)
        self.assertIsNone(publico_previo.condiciones_financieras)
        self.assertEqual(CondicionesFinancieras.objects.filter(activa=True).count(), 1)
        self.assertTrue(CondicionesFinancieras.objects.get().bloqueada)
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 1)
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(solicitud=solicitud).count(),
            1,
        )

        repeticion = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        self.assertEqual(repeticion.codigo, 'ALREADY_PENDING_SIGNATURE')
        self.assertEqual(CondicionesFinancieras.objects.filter(activa=True).count(), 1)
        self.assertEqual(ProcesoFirmaEducativa.objects.count(), 1)
        self.assertEqual(
            ArtefactoContractualEducativo.objects.filter(vigente=True).count(),
            2,
        )
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 1)
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(solicitud=solicitud).count(),
            1,
        )

        primera = self._firmar(proceso)
        segunda = self._firmar(proceso)
        solicitud.refresh_from_db()
        publico_final = obtener_resultado_publico(solicitud)
        self.assertEqual(primera.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.APPROVED)
        self.assertTrue(publico_final.curso_autorizado)
        self.assertIsNotNone(publico_final.condiciones_financieras)
        self.assertEqual(EventoWebhookFirmaEducativa.objects.count(), 1)

    @override_settings(
        FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=True,
        FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND=CONTENT_CONCLUSIVE,
        FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS=True,
        FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY='orchestration-content-key',
        FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS=False,
    )
    def test_e2e_adulto_con_certificado_pdf_avanza_a_firma(self):
        solicitud = self._solicitud_base('AUTO-ADULTO-PDF-V2')
        adulto = self._participante(solicitud)
        self._documento(
            solicitud, adulto, TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            'adulto-pdf-frente',
        )
        self._documento(
            solicitud, adulto, TipoDocumentoFinanciacion.STUDENT_ID_BACK,
            'adulto-pdf-reverso',
        )
        ingresos = registrar_documento(
            solicitud=solicitud,
            participante=adulto,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura='USER_UPLOAD',
            archivo=SimpleUploadedFile(
                'ingresos.pdf',
                pdf_sintetico(textos=(
                    'CERTIFICADO DE INGRESOS ADULTO AUTOMATICO PERIODO 2026 VALORES',
                )),
                content_type='application/pdf',
            ),
            actor=self.usuario,
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        solicitud.save(update_fields=['estado'])

        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        ingresos.refresh_from_db()
        self.assertEqual(resultado.codigo, 'PENDING_SIGNATURE')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_SIGNATURE)
        self.assertEqual(ingresos.estado_validacion, EstadoValidacionDocumento.APPROVED)
        self.assertEqual(
            ingresos.procesamientos_contenido.get().estado,
            'ACCEPTED',
        )
        self.assertIsNone(obtener_resultado_publico(solicitud).condiciones_financieras)

    @override_settings(
        FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=True,
        FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND=CONTENT_CONCLUSIVE,
        FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS=True,
        FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY='orchestration-content-key',
        FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS=False,
    )
    def test_e2e_menor_con_certificado_pdf_del_tutor_avanza_a_firma(self):
        solicitud = self._solicitud_base('AUTO-MENOR-PDF-V2')
        estudiante = self._participante(solicitud, menor=True)
        tutor = self._participante(solicitud, tutor=True)
        for participante, tipo, sufijo in (
            (estudiante, TipoDocumentoFinanciacion.STUDENT_ID_FRONT, 'menor-pdf-frente'),
            (estudiante, TipoDocumentoFinanciacion.STUDENT_ID_BACK, 'menor-pdf-reverso'),
            (tutor, TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT, 'tutor-pdf-frente'),
            (tutor, TipoDocumentoFinanciacion.GUARDIAN_ID_BACK, 'tutor-pdf-reverso'),
        ):
            self._documento(solicitud, participante, tipo, sufijo)
        registrar_documento(
            solicitud=solicitud,
            participante=tutor,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura='USER_UPLOAD',
            archivo=SimpleUploadedFile(
                'ingresos-tutor.pdf',
                pdf_sintetico(textos=(
                    'CERTIFICADO DE INGRESOS TUTOR AUTOMATICO PERIODO 2026 VALORES',
                )),
                content_type='application/pdf',
            ),
            actor=self.usuario,
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        solicitud.save(update_fields=['estado'])

        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        self.assertEqual(resultado.codigo, 'PENDING_SIGNATURE')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_SIGNATURE)
        self.assertEqual(
            RecordingEducationalSignatureBackend.submissions[0]['firmante'].pk,
            tutor.pk,
        )

    @override_settings(
        FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=True,
        FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS=False,
        FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND=CONTENT_CONTRADICTORY,
        FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS=True,
        FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY='orchestration-content-key',
    )
    def test_correccion_pdf_es_consolidada_y_no_duplica_outbox(self):
        solicitud = self._solicitud_base('AUTO-PDF-CORRECCION')
        adulto = self._participante(solicitud)
        self._documento(
            solicitud, adulto, TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            'correccion-pdf-frente',
        )
        self._documento(
            solicitud, adulto, TipoDocumentoFinanciacion.STUDENT_ID_BACK,
            'correccion-pdf-reverso',
        )
        registrar_documento(
            solicitud=solicitud,
            participante=adulto,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura='USER_UPLOAD',
            archivo=SimpleUploadedFile(
                'ingresos-incorrectos.pdf',
                pdf_sintetico(textos=('DOCUMENTO AJENO PERSONA DISTINTA 2026',)),
                content_type='application/pdf',
            ),
            actor=self.usuario,
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        solicitud.save(update_fields=['estado'])

        primero = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        segundo = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        self.assertEqual(primero.codigo, 'DOCUMENT_CORRECTION_REQUIRED')
        self.assertEqual(segundo.codigo, 'STATE_NOT_AUTOMATABLE')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.CORRECTION_REQUIRED)
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                solicitud=solicitud,
                codigo_mensaje='AUTOMATIC_CORRECTION',
            ).count(),
            1,
        )

        anterior = solicitud.documentos.get(
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            activo=True,
        )
        reemplazo = reemplazar_documento(
            documento=anterior,
            archivo=SimpleUploadedFile(
                'ingresos-corregidos.pdf',
                pdf_sintetico(textos=(
                    'CERTIFICADO INGRESOS ADULTO AUTOMATICO PERIODO 2026 VALORES',
                )),
                content_type='application/pdf',
            ),
            actor=self.usuario,
        )
        procesar_escaneo_documento(
            documento=reemplazo,
            origen='AUTOMATIC',
            backend=BackendLimpio(),
        )
        solicitud.refresh_from_db()
        with patch(
            'financiacion_educativa.services.requisitos_documentales.'
            'terminos_obligatorios_aceptados',
            return_value=True,
        ):
            completar_fase_documental(solicitud=solicitud, actor=self.usuario)
        with override_settings(
            FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND=CONTENT_CONCLUSIVE
        ):
            for _ in range(7):
                procesar_siguiente_trabajo()

        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_SIGNATURE)
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                solicitud=solicitud,
                codigo_mensaje='AUTOMATIC_CORRECTION',
            ).count(),
            1,
        )

    def test_menor_envia_como_unico_firmante_al_tutor(self):
        solicitud, estudiante, tutor = self._menor_listo()

        ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        envio = RecordingEducationalSignatureBackend.submissions[0]
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_SIGNATURE)
        self.assertEqual(envio['firmante'].pk, tutor.pk)
        self.assertNotEqual(envio['firmante'].pk, estudiante.pk)
        self.assertEqual(ProcesoFirmaEducativa.objects.count(), 1)

    def test_soporte_matricula_adjunto_se_valida_y_acepta_automaticamente(self):
        solicitud, _ = self._adulto_listo('AUTO-MATRICULA')
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        solicitud.save(update_fields=['estado'])
        registrar_o_actualizar_evidencia_matricula(
            solicitud=solicitud,
            actor=self.usuario,
            institucion_declarada='Institucion educativa',
            programa_curso=solicitud.nombre_curso,
            periodo_academico='2026-2',
            referencia_matricula='MAT-AUTO-001',
            archivo=imagen('soporte-matricula'),
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        solicitud.save(update_fields=['estado'])

        ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        evidencia = EvidenciaMatricula.objects.get(solicitud=solicitud)
        self.assertEqual(evidencia.estado, EstadoEvidenciaMatricula.ACCEPTED)
        self.assertEqual(evidencia.programa_curso, solicitud.nombre_curso)
        self.assertEqual(evidencia.referencia_matricula, 'MAT-AUTO-001')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_SIGNATURE)

    def test_soporte_matricula_pdf_no_se_autoacepta_por_metadatos(self):
        solicitud, _ = self._adulto_listo('AUTO-MATRICULA-PDF')
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        solicitud.save(update_fields=['estado'])
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=solicitud,
            actor=self.usuario,
            institucion_declarada='Institucion educativa',
            programa_curso=solicitud.nombre_curso,
            periodo_academico='2026-2',
            referencia_matricula='MAT-PDF-001',
            archivo=pdf('soporte-matricula'),
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        solicitud.save(update_fields=['estado'])

        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        evidencia.refresh_from_db()
        documento = evidencia.documento_soporte
        documento.refresh_from_db()
        decision = documento.resultado_procesamiento['automatic_document_policy']
        self.assertEqual(resultado.codigo, 'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(
            solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )
        self.assertEqual(documento.estado_escaneo, EstadoEscaneoDocumento.SAFE)
        self.assertEqual(
            documento.estado_validacion,
            EstadoValidacionDocumento.PENDING,
        )
        self.assertEqual(evidencia.estado, EstadoEvidenciaMatricula.PENDING)
        self.assertEqual(decision['decision'], 'MANUAL_EXCEPTION')
        self.assertEqual(
            decision['reason'],
            'PDF_CONTENT_PROCESSING_REQUIRED',
        )
        self.assertFalse(documento.validaciones_ia.exists())
        self.assertFalse(CondicionesFinancieras.objects.exists())

    @override_settings(FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=False)
    def test_pdf_deshabilitado_cierra_en_excepcion_manual(self):
        solicitud = self._solicitud_base('AUTO-INGRESOS-PDF')
        adulto = self._participante(solicitud)
        self._documento(
            solicitud,
            adulto,
            TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            'pdf-ingresos-frente',
        )
        self._documento(
            solicitud,
            adulto,
            TipoDocumentoFinanciacion.STUDENT_ID_BACK,
            'pdf-ingresos-reverso',
        )
        ingresos = registrar_documento(
            solicitud=solicitud,
            participante=adulto,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura='USER_UPLOAD',
            archivo=SimpleUploadedFile(
                'certificado-ingresos.pdf',
                pdf_sintetico(textos=(
                    'CERTIFICADO INGRESOS ADULTO AUTOMATICO PERIODO 2026 VALORES',
                )),
                content_type='application/pdf',
            ),
            actor=self.usuario,
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        solicitud.save(update_fields=['estado'])

        proceso, creado = encolar_proceso_automatizacion(
            solicitud_id=solicitud.pk
        )
        self.assertTrue(creado)
        for _ in range(3):
            resultado = procesar_siguiente_trabajo()
            self.assertTrue(resultado.procesado)

        solicitud.refresh_from_db()
        proceso.refresh_from_db()
        ingresos.refresh_from_db()
        decision = ingresos.resultado_procesamiento['automatic_document_policy']
        self.assertEqual(
            proceso.estado,
            EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
        )
        self.assertEqual(proceso.codigo_razon, 'DOCUMENT_RESULT_NOT_CONCLUSIVE')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW)
        self.assertEqual(ingresos.estado_escaneo, EstadoEscaneoDocumento.SAFE)
        self.assertEqual(ingresos.estado_validacion, EstadoValidacionDocumento.PENDING)
        self.assertEqual(decision['decision'], 'MANUAL_REVIEW')
        self.assertEqual(
            decision['reason'],
            'CONTENT_VALIDATION_UNSUPPORTED_MEDIA_TYPE',
        )
        self.assertFalse(ingresos.validaciones_ia.exists())
        self.assertFalse(CondicionesFinancieras.objects.exists())

    @override_settings(FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=AI_LOW_CONFIDENCE)
    def test_baja_confianza_conserva_revision_manual_sin_rechazar(self):
        solicitud, _ = self._adulto_listo('AUTO-BAJA')

        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        repeticion = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        self.assertEqual(resultado.codigo, 'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(repeticion.codigo, 'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW)
        self.assertFalse(CondicionesFinancieras.objects.exists())
        self.assertFalse(ProcesoFirmaEducativa.objects.exists())
        self.assertEqual(ValidacionIADocumento.objects.count(), 3)
        self.assertFalse(
            solicitud.documentos.filter(
                estado_validacion=EstadoValidacionDocumento.REJECTED
            ).exists()
        )

    @override_settings(FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=AI_NOT_REAL)
    def test_imagen_posiblemente_no_real_conserva_revision_manual(self):
        solicitud, _ = self._adulto_listo('AUTO-NO-REAL')

        ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW)
        validaciones = ValidacionIADocumento.objects.filter(
            estado=EstadoValidacionIADocumento.MANUAL_REVIEW,
        )
        self.assertTrue(
            any(
                'POSSIBLY_NOT_REAL' in validacion.hallazgos
                for validacion in validaciones
            )
        )
        self.assertFalse(CondicionesFinancieras.objects.exists())

    @override_settings(FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=AI_NOT_IDENTITY)
    def test_objeto_ajeno_a_identidad_exige_nueva_captura(self):
        solicitud, _ = self._adulto_listo('AUTO-NO-IDENTIDAD')

        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        repeticion = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        self.assertEqual(resultado.codigo, 'DOCUMENT_CORRECTION_REQUIRED')
        self.assertEqual(
            solicitud.estado,
            EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
        )
        self.assertTrue(
            solicitud.documentos.filter(
                tipo__in={
                    TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                    TipoDocumentoFinanciacion.STUDENT_ID_BACK,
                },
                estado_validacion=EstadoValidacionDocumento.REJECTED,
            ).exists()
        )
        self.assertTrue(
            ValidacionIADocumento.objects.filter(
                estado=EstadoValidacionIADocumento.AUTO_REJECTED,
            ).exists()
        )
        self.assertFalse(CondicionesFinancieras.objects.exists())
        self.assertEqual(repeticion.codigo, 'STATE_NOT_AUTOMATABLE')
        outbox = OutboxCorreoEducativo.objects.get(solicitud=solicitud)
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.PENDING)

    @override_settings(FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=SCAN_INFECTED)
    def test_malware_bloquea_el_documento_y_no_avanza(self):
        solicitud, _ = self._adulto_listo('AUTO-MALWARE')

        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        self.assertEqual(resultado.codigo, 'SECURITY_REVIEW_REQUIRED')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW)
        self.assertTrue(
            solicitud.documentos.filter(
                estado_escaneo=EstadoEscaneoDocumento.BLOCKED
            ).exists()
        )
        self.assertFalse(ValidacionIADocumento.objects.exists())
        self.assertFalse(CondicionesFinancieras.objects.exists())

    @override_settings(
        FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=SCAN_UNAVAILABLE
    )
    def test_fallo_clamav_conserva_estado_de_revision_y_no_avanza(self):
        solicitud, _ = self._adulto_listo('AUTO-SCAN-ERROR')

        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        self.assertEqual(resultado.codigo, 'SECURITY_REVIEW_REQUIRED')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW)
        self.assertTrue(
            solicitud.documentos.filter(
                estado_escaneo=EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
            ).exists()
        )
        self.assertFalse(ValidacionIADocumento.objects.exists())
        self.assertFalse(CondicionesFinancieras.objects.exists())

    @override_settings(FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=AI_FLAKY)
    def test_fallo_temporal_ia_se_reintenta_sin_duplicar_recursos(self):
        solicitud, _ = self._adulto_listo('AUTO-IA-RETRY')

        primero = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        segundo = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        self.assertEqual(primero.codigo, 'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(segundo.codigo, 'PENDING_SIGNATURE')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_SIGNATURE)
        self.assertEqual(CondicionesFinancieras.objects.filter(activa=True).count(), 1)
        self.assertEqual(
            ArtefactoContractualEducativo.objects.filter(vigente=True).count(),
            2,
        )
        self.assertEqual(ProcesoFirmaEducativa.objects.count(), 1)
        self.assertEqual(
            ValidacionIADocumento.objects.filter(
                estado=EstadoValidacionIADocumento.ERROR
            ).count(),
            1,
        )

    def test_fallo_zapsign_se_reintenta_sobre_el_mismo_proceso(self):
        solicitud, _ = self._adulto_listo('AUTO-SIGN-RETRY')
        RecordingEducationalSignatureBackend.fail_send = True

        primero = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        proceso_inicial = ProcesoFirmaEducativa.objects.get(solicitud=solicitud)
        RecordingEducationalSignatureBackend.fail_send = False
        salida = StringIO()
        call_command(
            'procesar_orquestacion_educativa',
            solicitud_id=solicitud.pk,
            limit=1,
            stdout=salida,
        )

        solicitud.refresh_from_db()
        proceso_final = ProcesoFirmaEducativa.objects.get(solicitud=solicitud)
        self.assertEqual(primero.codigo, 'SIGNATURE_SEND_RETRY_REQUIRED')
        self.assertIn('PENDING_SIGNATURE: 1', salida.getvalue())
        self.assertEqual(proceso_inicial.pk, proceso_final.pk)
        self.assertEqual(proceso_final.intentos_envio, 2)
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 1)
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_SIGNATURE)

    def test_recuperacion_despues_del_envio_no_reenvia_a_zapsign(self):
        solicitud, _ = self._adulto_listo('AUTO-SEND-RECOVERY')
        ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 1)

        salida = ejecutar_etapa_persistente(
            solicitud_id=solicitud.pk,
            etapa=EtapaAutomatizacionEducativa.SIGNATURE_SEND,
        )

        self.assertEqual(salida.estado, 'PENDING_SIGNATURE')
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 1)

    def test_caida_con_envio_sending_exige_conciliacion_y_no_reenvia(self):
        solicitud, _ = self._adulto_listo('AUTO-SEND-CRASH')
        ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        proceso = ProcesoFirmaEducativa.objects.get(solicitud=solicitud)
        proceso.estado = EstadoProcesoFirmaEducativa.SENDING
        proceso.token_documento_externo = ''
        proceso.save(
            update_fields=['estado', 'token_documento_externo', 'actualizado_en']
        )
        solicitud.estado = EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE
        solicitud.save(update_fields=['estado'])

        salida = ejecutar_etapa_persistente(
            solicitud_id=solicitud.pk,
            etapa=EtapaAutomatizacionEducativa.SIGNATURE_SEND,
        )

        proceso.refresh_from_db()
        self.assertEqual(salida.estado, 'MANUAL_EXCEPTION')
        self.assertEqual(salida.codigo, 'SIGNATURE_SEND_AMBIGUOUS')
        self.assertEqual(proceso.estado, EstadoProcesoFirmaEducativa.FAILED)
        self.assertEqual(
            proceso.codigo_ultimo_error,
            'SIGNATURE_SEND_AMBIGUOUS',
        )
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 1)

    @override_settings(FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND=SIGNATURE_AMBIGUOUS)
    def test_envio_ambiguo_no_se_repite_automaticamente(self):
        solicitud, _ = self._adulto_listo('AUTO-SIGN-AMBIGUOUS')

        primero = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        salida = StringIO()
        call_command(
            'procesar_orquestacion_educativa',
            solicitud_id=solicitud.pk,
            limit=1,
            stdout=salida,
        )

        proceso = ProcesoFirmaEducativa.objects.get(solicitud=solicitud)
        self.assertEqual(primero.codigo, 'SIGNATURE_SEND_RETRY_REQUIRED')
        self.assertIn('SIGNATURE_SEND_RETRY_REQUIRED: 1', salida.getvalue())
        self.assertEqual(proceso.intentos_envio, 1)
        self.assertEqual(proceso.codigo_ultimo_error, 'SIGNATURE_SEND_AMBIGUOUS')
        self.assertEqual(AmbiguousEducationalSignatureBackend.attempts, 1)

    @override_settings(FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=False)
    def test_automatizacion_deshabilitada_no_modifica_solicitud(self):
        solicitud, _ = self._adulto_listo('AUTO-DISABLED')

        resultado = ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)

        solicitud.refresh_from_db()
        self.assertEqual(resultado.codigo, 'AUTOMATION_DISABLED')
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW)
        self.assertFalse(
            solicitud.documentos.exclude(
                estado_escaneo=EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
            ).exists()
        )
        self.assertFalse(ValidacionIADocumento.objects.exists())
        self.assertFalse(CondicionesFinancieras.objects.exists())

    def test_rechazo_regenera_y_reenvia_un_solo_pagare_vigente(self):
        solicitud, _ = self._adulto_listo('AUTO-REFUSAL')
        ejecutar_orquestacion_automatica(solicitud_id=solicitud.pk)
        proceso = ProcesoFirmaEducativa.objects.get(solicitud=solicitud)
        payload = {
            'event_type': 'doc_refused',
            'token': proceso.token_documento_externo,
            'external_id': proceso.external_id,
            'status': 'refused',
        }
        datos = json.dumps(payload)

        primera = self.client.post(
            reverse('financiacion_educativa_api:zapsign-webhook'),
            data=datos,
            content_type='application/json',
            HTTP_X_EDUCATIONAL_SIGNATURE_SECRET='automatic-webhook-secret',
        )
        segunda = self.client.post(
            reverse('financiacion_educativa_api:zapsign-webhook'),
            data=datos,
            content_type='application/json',
            HTTP_X_EDUCATIONAL_SIGNATURE_SECRET='automatic-webhook-secret',
        )
        primera_etapa = procesar_siguiente_trabajo()
        segunda_etapa = procesar_siguiente_trabajo()

        solicitud.refresh_from_db()
        self.assertEqual(primera.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertTrue(primera_etapa.procesado)
        self.assertTrue(segunda_etapa.procesado)
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.PENDING_SIGNATURE)
        self.assertEqual(ProcesoFirmaEducativa.objects.count(), 2)
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 2)
        self.assertEqual(
            ArtefactoContractualEducativo.objects.filter(
                tipo=TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
                vigente=True,
            ).count(),
            1,
        )
        self.assertEqual(
            ArtefactoContractualEducativo.objects.filter(
                tipo=TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
                estado=EstadoArtefactoContractualEducativo.CANCELLED,
            ).count(),
            1,
        )

    @patch(
        'financiacion_educativa.services.requisitos_documentales.'
        'calcular_requisitos_documentales',
        return_value=[RequisitoDocumental('READY', 'Listo', True)],
    )
    @patch(
        'financiacion_educativa.services.requisitos_documentales.'
        'programar_orquestacion_automatica'
    )
    def test_completar_documentos_programa_la_orquestacion(
        self,
        programar,
        _calcular,
    ):
        solicitud = self._solicitud_base('AUTO-DISPATCH')

        resultado = completar_fase_documental(
            solicitud=solicitud,
            actor=self.usuario,
        )

        self.assertEqual(
            resultado.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )
        programar.assert_called_once_with(solicitud_id=solicitud.pk)
