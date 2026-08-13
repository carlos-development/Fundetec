from datetime import date
from tempfile import TemporaryDirectory
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEnlaceCapturaMovil,
    EstadoEntregaCorreoSolicitud,
    EstadoOutboxCorreoEducativo,
    EstadoEscaneoDocumento,
    EstadoEvidenciaMatricula,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    MotivoDecisionRevisionEducativa,
    OrigenCapturaDocumento,
    RequisitoCorreccionEducativa,
    RelacionEstudiante,
    RolParticipante,
    TipoDecisionRevisionEducativa,
    TipoConsentimiento,
    TipoArtefactoContractualEducativo,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    CondicionesFinancieras,
    ConfiguracionFinancieraEducativa,
    Consentimiento,
    DecisionRevisionEducativa,
    EntregaCorreoEstadoSolicitud,
    OutboxCorreoEducativo,
    ProcesoFirmaEducativa,
    VersionTerminosFinanciacion,
)
from financiacion_educativa.services.documentos import (
    reemplazar_documento,
    registrar_documento,
    revisar_documento,
)
from financiacion_educativa.services.estado_publico import (
    obtener_resultado_publico,
)
from financiacion_educativa.services.matricula import (
    registrar_o_actualizar_evidencia_matricula,
    revisar_evidencia_matricula,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.services.revision import decidir_solicitud
from financiacion_educativa.services.outbox_correos import procesar_siguiente_correo
from financiacion_educativa.services.firma_zapsign import (
    enviar_pagare_educativo,
)
from financiacion_educativa.services.requisitos_documentales import (
    calcular_requisitos_documentales,
    completar_fase_documental,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_solicitud,
)
from financiacion_educativa.tests.scan_helpers import registrar_resultado_escaneo
from financiacion_educativa.tests.signature_backends import (
    RecordingEducationalSignatureBackend,
)
from instituciones.services.credenciales import crear_credencial_api


def jpeg(nombre, marca):
    return SimpleUploadedFile(
        nombre,
        b'\xff\xd8\xff' + marca + b'\xff\xd9',
        content_type='image/jpeg',
    )


@override_settings(
    DEBUG=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL=(
        'APROBADO SOLUCIONES DIGITALES S.A.S.'
    ),
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
    FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND=(
        'financiacion_educativa.tests.signature_backends.'
        'RecordingEducationalSignatureBackend'
    ),
    FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS=True,
    FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET='revision-webhook-secret',
)
class RevisionOperativaTests(TestCase):
    def setUp(self):
        RecordingEducationalSignatureBackend.reset()
        self.private_root = TemporaryDirectory()
        self.addCleanup(self.private_root.cleanup)
        self.private_override = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name,
        )
        self.private_override.enable()
        self.addCleanup(self.private_override.disable)
        User = get_user_model()
        self.propietario = User.objects.create_user(
            username='revision@example.com',
            email='revision@example.com',
            password='Clave-2026',
        )
        self.revisor = User.objects.create_superuser(
            username='revisor@example.com',
            email='revisor@example.com',
            password='Clave-2026',
        )
        self.sin_permiso = User.objects.create_user(
            username='staff@example.com',
            email='staff@example.com',
            password='Clave-2026',
            is_staff=True,
        )
        self.solicitud = crear_solicitud(usuario=self.propietario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.plazo_meses = 3
        self.solicitud.save(update_fields=['estado', 'plazo_meses'])
        version = VersionTerminosFinanciacion.objects.create(
            tipo=TipoConsentimiento.TERMS,
            version='revision-operativa-v1',
            titulo='Terminos de prueba',
            contenido='Contenido de prueba.',
            obligatorio=True,
            estado='PUBLISHED',
            publicada_en=timezone.now(),
            vigente_desde=timezone.now(),
        )
        Consentimiento.objects.create(
            solicitud=self.solicitud,
            usuario=self.propietario,
            tipo=version.tipo,
            version_texto=version.version,
            evidencia_hash='0' * 64,
        )
        self.estudiante = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.propietario,
            datos=DatosParticipante(
                nombres='Persona',
                apellidos='Revision',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='100200300',
                fecha_nacimiento=date(1990, 1, 1),
                correo='revision@example.com',
                telefono='3001234567',
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles={
                RolParticipante.STUDENT,
                RolParticipante.PRINCIPAL_DEBTOR,
            },
        )
        self.documentos = [
            registrar_documento(
                solicitud=self.solicitud,
                participante=self.estudiante,
                tipo=tipo,
                origen_captura=origen,
                archivo=jpeg(nombre, marca),
                actor=self.propietario,
            )
            for tipo, origen, nombre, marca in (
                (
                    TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                    OrigenCapturaDocumento.CAMERA,
                    'frente.jpg',
                    b'frente',
                ),
                (
                    TipoDocumentoFinanciacion.STUDENT_ID_BACK,
                    OrigenCapturaDocumento.CAMERA,
                    'reverso.jpg',
                    b'reverso',
                ),
                (
                    TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
                    OrigenCapturaDocumento.USER_UPLOAD,
                    'ingresos.jpg',
                    b'ingresos',
                ),
            )
        ]
        self.evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.propietario,
            institucion_declarada='Institucion de prueba',
            programa_curso=self.solicitud.nombre_curso,
            periodo_academico='2026-2',
            referencia_matricula='MAT-DEMO',
            archivo=jpeg('matricula.jpg', b'matricula'),
        )
        self.documentos.append(self.evidencia.documento_soporte)
        for indice, documento in enumerate(self.documentos, start=1):
            registrar_resultado_escaneo(
                documento=documento,
                actor=self.revisor,
                estado=EstadoEscaneoDocumento.SAFE,
                referencia_escaneo=f'scan-demo-{indice}',
            )
            revisar_documento(
                documento=documento,
                actor=self.revisor,
                aceptar=True,
            )
        revisar_evidencia_matricula(
            evidencia=self.evidencia,
            actor=self.revisor,
            aceptar=True,
        )
        crear_configuracion_financiera()
        self.solicitud.estado = (
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        )
        self.solicitud.save(update_fields=['estado'])

    def _decidir(self, tipo, motivo, mensaje='', *, procesar_correo=True):
        requisitos = (
            [RequisitoCorreccionEducativa.STUDENT_ID_FRONT]
            if tipo == TipoDecisionRevisionEducativa.CORRECTION_REQUESTED
            else []
        )
        decision = decidir_solicitud(
            solicitud=self.solicitud,
            actor=self.revisor,
            tipo=tipo,
            motivo=motivo,
            mensaje_solicitante=mensaje,
            observacion_interna='Nota interna que no debe salir por API.',
            requisitos_pendientes=requisitos,
        )
        if procesar_correo:
            procesar_siguiente_correo()
        return decision

    def test_aprobacion_bloquea_fotografia_y_genera_contratos(self):
        self.assertFalse(
            CondicionesFinancieras.objects.filter(
                solicitud=self.solicitud
            ).exists()
        )
        decision = self._decidir(
            TipoDecisionRevisionEducativa.APPROVED,
            MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED,
        )

        self.solicitud.refresh_from_db()
        self.fotografia = CondicionesFinancieras.objects.get(
            solicitud=self.solicitud,
            activa=True,
        )
        self.fotografia.refresh_from_db()
        entrega = EntregaCorreoEstadoSolicitud.objects.get(decision=decision)
        resultado = obtener_resultado_publico(self.solicitud)

        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
        )
        self.assertTrue(self.fotografia.bloqueada)
        self.assertEqual(self.fotografia.fecha_inicio_plan, timezone.localdate())
        self.assertEqual(decision.fotografia_financiera, self.fotografia)
        self.assertEqual(entrega.estado, EstadoEntregaCorreoSolicitud.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(resultado.curso_autorizado)
        self.assertEqual(resultado.estado, 'UNDER_REVIEW')
        self.assertIsNone(resultado.condiciones_financieras)
        self.assertSetEqual(
            set(
                ArtefactoContractualEducativo.objects.filter(
                    solicitud=self.solicitud,
                ).values_list('tipo', flat=True)
            ),
            {
                TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
                TipoArtefactoContractualEducativo.ENROLLMENT_FORM,
            },
        )
        decision.mensaje_solicitante = 'Cambio no permitido'
        with self.assertRaises(ValidationError):
            decision.save()

    def test_aprobacion_sin_politica_revierte_decision_y_estado(self):
        ConfiguracionFinancieraEducativa.objects.all().delete()

        with self.assertRaises(ValidationError):
            self._decidir(
                TipoDecisionRevisionEducativa.APPROVED,
                MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED,
            )

        self.solicitud.refresh_from_db()
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )
        self.assertFalse(DecisionRevisionEducativa.objects.exists())
        self.assertFalse(CondicionesFinancieras.objects.exists())

    def test_aprobacion_sin_correo_del_firmante_revierte_todo(self):
        self.estudiante.correo = ''
        self.estudiante.save(update_fields=['correo', 'actualizado_en'])

        with self.assertRaises(ValidationError):
            self._decidir(
                TipoDecisionRevisionEducativa.APPROVED,
                MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED,
            )

        self.solicitud.refresh_from_db()
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )
        self.assertFalse(DecisionRevisionEducativa.objects.exists())
        self.assertFalse(CondicionesFinancieras.objects.exists())
        self.assertFalse(ArtefactoContractualEducativo.objects.exists())

    @override_settings(
        EMAIL_BACKEND=(
            'financiacion_educativa.tests.delivery_backends.'
            'FailingDjangoEmailBackend'
        ),
    )
    def test_fallo_de_correo_no_revierte_la_aprobacion(self):
        decision = self._decidir(
            TipoDecisionRevisionEducativa.APPROVED,
            MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED,
        )

        self.solicitud.refresh_from_db()
        entrega = EntregaCorreoEstadoSolicitud.objects.get(decision=decision)

        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
        )
        self.assertEqual(entrega.estado, EstadoEntregaCorreoSolicitud.FAILED)
        self.assertEqual(
            entrega.codigo_ultimo_error,
            'SMTP_DELIVERY_AMBIGUOUS',
        )
        self.assertEqual(
            OutboxCorreoEducativo.objects.get(decision=decision).estado,
            EstadoOutboxCorreoEducativo.AMBIGUOUS,
        )

    def test_fallo_al_crear_outbox_revierte_decision_y_transicion(self):
        with mock.patch(
            'financiacion_educativa.services.revision.crear_correo_decision',
            side_effect=RuntimeError('fallo controlado'),
        ):
            with self.assertRaises(RuntimeError):
                self._decidir(
                    TipoDecisionRevisionEducativa.CORRECTION_REQUESTED,
                    MotivoDecisionRevisionEducativa.UNREADABLE_DOCUMENT,
                    'Repite la captura frontal.',
                    procesar_correo=False,
                )

        self.solicitud.refresh_from_db()
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )
        self.assertFalse(DecisionRevisionEducativa.objects.exists())
        self.assertFalse(EntregaCorreoEstadoSolicitud.objects.exists())
        self.assertFalse(OutboxCorreoEducativo.objects.exists())

    def test_correccion_reabre_expediente_sin_exponer_nota_interna(self):
        decision = self._decidir(
            TipoDecisionRevisionEducativa.CORRECTION_REQUESTED,
            MotivoDecisionRevisionEducativa.UNREADABLE_DOCUMENT,
            'Repite la captura frontal con mejor iluminacion.',
        )
        self.solicitud.refresh_from_db()
        self.client.force_login(self.propietario)

        pagina = self.client.get(
            reverse(
                'financiacion_educativa_web:documentacion',
                kwargs={'solicitud_id': self.solicitud.pk},
            )
        )
        resultado = obtener_resultado_publico(self.solicitud)

        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
        )
        self.assertContains(pagina, decision.mensaje_solicitante)
        self.assertNotContains(pagina, decision.observacion_interna)
        self.assertEqual(resultado.estado, 'ACTION_REQUIRED')
        self.assertFalse(resultado.curso_autorizado)
        self.assertEqual(len(mail.outbox), 1)
        requisitos = {
            requisito.codigo: requisito.cumplido
            for requisito in calcular_requisitos_documentales(self.solicitud)
        }
        self.assertFalse(requisitos['STUDENT_ID_FRONT'])

    def test_correccion_exige_reemplazo_posterior_antes_de_reenviar(self):
        self._decidir(
            TipoDecisionRevisionEducativa.CORRECTION_REQUESTED,
            MotivoDecisionRevisionEducativa.UNREADABLE_DOCUMENT,
            'Repite la captura frontal con mejor iluminacion.',
        )
        self.solicitud.refresh_from_db()

        with self.assertRaises(ValidationError):
            completar_fase_documental(
                solicitud=self.solicitud,
                actor=self.propietario,
            )

        reemplazar_documento(
            documento=self.documentos[0],
            archivo=jpeg('frente-nuevo.jpg', b'frente-nuevo'),
            actor=self.propietario,
            origen_captura=OrigenCapturaDocumento.CAMERA,
        )
        solicitud = completar_fase_documental(
            solicitud=self.solicitud,
            actor=self.propietario,
        )

        self.assertEqual(
            solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )

    def test_rechazo_es_final_y_no_autoriza_curso(self):
        self._decidir(
            TipoDecisionRevisionEducativa.REJECTED,
            MotivoDecisionRevisionEducativa.IDENTITY_MISMATCH,
            'No fue posible validar la informacion aportada.',
        )
        self.solicitud.refresh_from_db()
        resultado = obtener_resultado_publico(self.solicitud)

        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.REJECTED,
        )
        self.assertEqual(resultado.estado, 'REJECTED')
        self.assertFalse(resultado.curso_autorizado)
        with self.assertRaises(ValidationError):
            decidir_solicitud(
                solicitud=self.solicitud,
                actor=self.revisor,
                tipo=TipoDecisionRevisionEducativa.REJECTED,
                motivo=MotivoDecisionRevisionEducativa.OTHER,
                mensaje_solicitante='Segundo rechazo.',
            )

    def test_usuario_staff_sin_permiso_no_puede_decidir(self):
        with self.assertRaises(ValidationError):
            decidir_solicitud(
                solicitud=self.solicitud,
                actor=self.sin_permiso,
                tipo=TipoDecisionRevisionEducativa.REJECTED,
                motivo=MotivoDecisionRevisionEducativa.OTHER,
                mensaje_solicitante='No autorizado.',
            )
        self.client.force_login(self.sin_permiso)
        respuesta = self.client.get(
            reverse(
                'admin:financiacion_educativa_solicitud_revision',
                args=[self.solicitud.pk],
            )
        )
        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(DecisionRevisionEducativa.objects.exists())

    def test_admin_muestra_expediente_y_registra_correccion(self):
        self.client.force_login(self.revisor)
        url = reverse(
            'admin:financiacion_educativa_solicitud_revision',
            args=[self.solicitud.pk],
        )
        pagina = self.client.get(url)
        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(
                url,
                {
                    'tipo': (
                        TipoDecisionRevisionEducativa.CORRECTION_REQUESTED
                    ),
                    'motivo': (
                        MotivoDecisionRevisionEducativa.INCOMPLETE_INFORMATION
                    ),
                    'mensaje_solicitante': (
                        'Completa el soporte academico solicitado.'
                    ),
                    'observacion_interna': 'Control interno.',
                    'requisitos_pendientes': [
                        RequisitoCorreccionEducativa.ENROLLMENT_EVIDENCE,
                    ],
                },
            )

        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, 'Revision de solicitud educativa')
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(DecisionRevisionEducativa.objects.exists())

    def test_api_mantiene_curso_bloqueado_hasta_la_firma(self):
        self._decidir(
            TipoDecisionRevisionEducativa.APPROVED,
            MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED,
        )
        credencial = crear_credencial_api(
            institucion=self.solicitud.institucion,
            nombre='Consulta revision',
        )
        respuesta = self.client.get(
            reverse(
                'financiacion_educativa_api:solicitud-detalle',
                kwargs={'application_id': self.solicitud.pk},
            ),
            HTTP_AUTHORIZATION=f'ApiKey {credencial.token}',
        )

        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        self.assertEqual(datos['status'], 'UNDER_REVIEW')
        self.assertFalse(datos['course_authorized'])
        self.assertIsNone(datos['authorization_effective_at'])
        self.assertIsNone(datos['financial_terms'])
        self.assertNotIn('observacion_interna', str(datos))
        self.assertNotIn('Nota interna', str(datos))

    def test_recorrido_manual_hasta_firma_autoriza_api(self):
        self._decidir(
            TipoDecisionRevisionEducativa.APPROVED,
            MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED,
        )
        proceso = ProcesoFirmaEducativa.objects.get(solicitud=self.solicitud)
        enviar_pagare_educativo(proceso=proceso)
        proceso.refresh_from_db()
        webhook = self.client.post(
            reverse('financiacion_educativa_api:zapsign-webhook'),
            {
                'event_type': 'doc_signed',
                'token': proceso.token_documento_externo,
                'external_id': proceso.external_id,
                'status': 'signed',
                'signers': [
                    {'status': 'signed', 'signed_at': '2026-08-04'}
                ],
            },
            content_type='application/json',
            HTTP_X_EDUCATIONAL_SIGNATURE_SECRET='revision-webhook-secret',
        )
        credencial = crear_credencial_api(
            institucion=self.solicitud.institucion,
            nombre='Consulta posterior a firma',
        )
        respuesta = self.client.get(
            reverse(
                'financiacion_educativa_api:solicitud-detalle',
                kwargs={'application_id': self.solicitud.pk},
            ),
            HTTP_AUTHORIZATION=f'ApiKey {credencial.token}',
        )

        self.solicitud.refresh_from_db()
        self.assertEqual(webhook.status_code, 200)
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.APPROVED,
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['status'], 'APPROVED')
        self.assertTrue(respuesta.data['course_authorized'])
        self.assertIsNotNone(respuesta.data['financial_terms'])
        self.assertIsNotNone(respuesta.data['enrollment_date'])
