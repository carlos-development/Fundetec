from datetime import date

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
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import (
    Consentimiento,
    DecisionRevisionEducativa,
    EntregaCorreoEstadoSolicitud,
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
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.services.revision import decidir_solicitud
from financiacion_educativa.services.requisitos_documentales import (
    calcular_requisitos_documentales,
    completar_fase_documental,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_solicitud,
)
from financiacion_educativa.tests.scan_helpers import registrar_resultado_escaneo
from instituciones.services.credenciales import crear_credencial_api


def jpeg(nombre, marca):
    return SimpleUploadedFile(
        nombre,
        b'\xff\xd8\xff' + marca + b'\xff\xd9',
        content_type='image/jpeg',
    )


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class RevisionOperativaTests(TestCase):
    def setUp(self):
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
        self.fotografia = crear_fotografia_condiciones_financieras(
            self.solicitud,
            fecha_inicio_plan=date(2026, 8, 1),
            actor=self.propietario,
        )
        self.solicitud.estado = (
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        )
        self.solicitud.save(update_fields=['estado'])

    def _decidir(self, tipo, motivo, mensaje=''):
        requisitos = (
            [RequisitoCorreccionEducativa.STUDENT_ID_FRONT]
            if tipo == TipoDecisionRevisionEducativa.CORRECTION_REQUESTED
            else []
        )
        with self.captureOnCommitCallbacks(execute=True):
            return decidir_solicitud(
                solicitud=self.solicitud,
                actor=self.revisor,
                tipo=tipo,
                motivo=motivo,
                mensaje_solicitante=mensaje,
                observacion_interna='Nota interna que no debe salir por API.',
                requisitos_pendientes=requisitos,
            )

    def test_aprobacion_bloquea_fotografia_y_autoriza_curso(self):
        decision = self._decidir(
            TipoDecisionRevisionEducativa.APPROVED,
            MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED,
        )

        self.solicitud.refresh_from_db()
        self.fotografia.refresh_from_db()
        entrega = EntregaCorreoEstadoSolicitud.objects.get(decision=decision)
        resultado = obtener_resultado_publico(self.solicitud)

        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.APPROVED,
        )
        self.assertTrue(self.fotografia.bloqueada)
        self.assertEqual(decision.fotografia_financiera, self.fotografia)
        self.assertEqual(entrega.estado, EstadoEntregaCorreoSolicitud.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(resultado.curso_autorizado)
        self.assertEqual(resultado.estado, 'APPROVED')
        self.assertEqual(
            resultado.condiciones_financieras['currency'],
            'COP',
        )
        decision.mensaje_solicitante = 'Cambio no permitido'
        with self.assertRaises(ValidationError):
            decision.save()

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
            EstadoSolicitudFinanciacion.APPROVED,
        )
        self.assertEqual(entrega.estado, EstadoEntregaCorreoSolicitud.FAILED)
        self.assertEqual(
            entrega.codigo_ultimo_error,
            'DELIVERY_BACKEND_ERROR',
        )

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

    def test_api_expone_resultado_reducido_que_autoriza_curso(self):
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
        self.assertEqual(datos['status'], 'APPROVED')
        self.assertTrue(datos['course_authorized'])
        self.assertIsNotNone(datos['authorization_effective_at'])
        self.assertEqual(datos['financial_terms']['currency'], 'COP')
        self.assertNotIn('observacion_interna', str(datos))
        self.assertNotIn('Nota interna', str(datos))
