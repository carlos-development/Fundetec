from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoEvidenciaMatricula,
    EstadoSolicitudFinanciacion,
    MotivoRechazoDocumento,
    OrigenCapturaDocumento,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
    TipoConsentimiento,
)
from financiacion_educativa.models import (
    Consentimiento,
    HistorialEstadoSolicitud,
    VersionTerminosFinanciacion,
)
from financiacion_educativa.services.documentos import (
    registrar_documento,
    revisar_documento,
)
from financiacion_educativa.services.matricula import (
    registrar_o_actualizar_evidencia_matricula,
    revisar_evidencia_matricula,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.services.requisitos_documentales import (
    calcular_requisitos_documentales,
    completar_fase_documental,
    fase_documental_completa,
)
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.scan_helpers import (
    conceder_permisos_documentales,
    registrar_resultado_escaneo,
)


def pdf(marca):
    return SimpleUploadedFile(
        f'{marca}.pdf',
        b'%PDF-1.7\n' + marca.encode('ascii') + b'\n%%EOF',
        content_type='application/pdf',
    )


def jpeg(marca):
    return SimpleUploadedFile(
        f'{marca}.jpg',
        b'\xff\xd8\xff' + marca.encode('ascii') + b'\xff\xd9',
        content_type='image/jpeg',
    )


class RequisitosDocumentalesFase4Tests(TestCase):
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
            username='requisitos@example.com',
            email='requisitos@example.com',
            password='Clave-2026',
        )
        self.revisor = User.objects.create_user(
            username='revisor-requisitos@example.com',
            email='revisor-requisitos@example.com',
            password='Clave-2026',
            is_staff=True,
        )
        conceder_permisos_documentales(self.revisor)
        self.solicitud = crear_solicitud()
        self.solicitud.usuario = self.usuario
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['usuario', 'estado'])
        self.institucion_original_id = self.solicitud.institucion_id
        version = VersionTerminosFinanciacion.objects.create(
            tipo=TipoConsentimiento.TERMS,
            version='test-requisitos-v1',
            titulo='Terminos de prueba',
            contenido='Contenido de prueba.',
            obligatorio=True,
            estado='PUBLISHED',
            publicada_en=timezone.now(),
            vigente_desde=timezone.now(),
        )
        Consentimiento.objects.create(
            solicitud=self.solicitud,
            usuario=self.usuario,
            tipo=version.tipo,
            version_texto=version.version,
            evidencia_hash='0' * 64,
        )

    def _participante(self, nacimiento=date(1990, 1, 1), roles=None):
        return registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='Estudiante',
                apellidos='Prueba',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='1000200030',
                fecha_nacimiento=nacimiento,
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles=roles or {
                RolParticipante.STUDENT,
                RolParticipante.PRINCIPAL_DEBTOR,
            },
        )

    def _aceptar_documento(self, tipo, archivo, participante=None):
        tipos_camara = {
            TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            TipoDocumentoFinanciacion.STUDENT_ID_BACK,
            TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
            TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
        }
        documento = registrar_documento(
            solicitud=self.solicitud,
            participante=participante,
            tipo=tipo,
            origen_captura=(
                OrigenCapturaDocumento.CAMERA
                if tipo in tipos_camara
                else OrigenCapturaDocumento.USER_UPLOAD
            ),
            archivo=archivo,
            actor=self.usuario,
        )
        registrar_resultado_escaneo(
            documento=documento,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
            referencia_escaneo=f'scanner-{tipo}',
        )
        revisar_documento(documento=documento, actor=self.revisor, aceptar=True)
        documento.refresh_from_db()
        return documento

    def _documentos_adulto(self, estudiante, *, aceptar=False):
        registrar = self._aceptar_documento if aceptar else registrar_documento
        documentos = (
            (TipoDocumentoFinanciacion.STUDENT_ID_FRONT, jpeg('frente')),
            (TipoDocumentoFinanciacion.STUDENT_ID_BACK, jpeg('reverso')),
            (TipoDocumentoFinanciacion.INCOME_CERTIFICATE, pdf('ingresos')),
        )
        for tipo, archivo in documentos:
            if aceptar:
                registrar(tipo, archivo, estudiante)
            else:
                registrar(
                    solicitud=self.solicitud,
                    participante=estudiante,
                    tipo=tipo,
                    origen_captura=(
                        OrigenCapturaDocumento.CAMERA
                        if tipo
                        in {
                            TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                            TipoDocumentoFinanciacion.STUDENT_ID_BACK,
                        }
                        else OrigenCapturaDocumento.USER_UPLOAD
                    ),
                    archivo=archivo,
                    actor=self.usuario,
                )

    def _matricula_aceptada(self):
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.usuario,
            institucion_declarada='Institucion declarada',
            programa_curso='Tecnologia',
            periodo_academico='2026-2',
            referencia_matricula='MAT-001',
            archivo=pdf('matricula'),
        )
        registrar_resultado_escaneo(
            documento=evidencia.documento_soporte,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
            referencia_escaneo='scanner-matricula',
        )
        revisar_documento(
            documento=evidencia.documento_soporte,
            actor=self.revisor,
            aceptar=True,
        )
        return revisar_evidencia_matricula(
            evidencia=evidencia,
            actor=self.revisor,
            aceptar=True,
        )

    def test_carga_matricula_no_verifica_ni_cambia_institucion_originadora(self):
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.usuario,
            institucion_declarada='Otra sede declarada',
            programa_curso='Curso',
            periodo_academico='2026-2',
            archivo=pdf('matricula-pendiente'),
        )
        self.solicitud.refresh_from_db()

        self.assertEqual(evidencia.estado, EstadoEvidenciaMatricula.PENDING)
        self.assertEqual(self.solicitud.institucion_id, self.institucion_original_id)
        with self.assertRaises(ValidationError):
            revisar_evidencia_matricula(
                evidencia=evidencia,
                actor=self.usuario,
                aceptar=True,
            )

    def test_revision_matricula_registra_actor_fecha_y_resultado(self):
        evidencia = self._matricula_aceptada()

        self.assertEqual(evidencia.estado, EstadoEvidenciaMatricula.ACCEPTED)
        self.assertEqual(evidencia.revisado_por, self.revisor)
        self.assertIsNotNone(evidencia.revisado_en)

    def test_rechazo_matricula_exige_motivo_controlado(self):
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.usuario,
            institucion_declarada='Institucion',
            programa_curso='Curso',
            periodo_academico='2026-2',
            archivo=pdf('matricula-rechazo'),
        )
        with self.assertRaises(ValidationError):
            revisar_evidencia_matricula(
                evidencia=evidencia,
                actor=self.revisor,
                aceptar=False,
                motivo_rechazo='LIBRE',
            )
        rechazada = revisar_evidencia_matricula(
            evidencia=evidencia,
            actor=self.revisor,
            aceptar=False,
            motivo_rechazo=MotivoRechazoDocumento.INCOMPLETE,
        )
        self.assertEqual(rechazada.estado, EstadoEvidenciaMatricula.REJECTED)

    def test_soporte_matricula_aportado_debe_resolverse_antes_de_revision(self):
        estudiante = self._participante()
        self._documentos_adulto(estudiante)
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.usuario,
            institucion_declarada='Institucion declarada',
            programa_curso='Tecnologia',
            periodo_academico='2026-2',
            referencia_matricula='MAT-002',
            archivo=pdf('matricula-pendiente-envio'),
        )

        self.assertFalse(fase_documental_completa(self.solicitud))
        registrar_resultado_escaneo(
            documento=evidencia.documento_soporte,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
            referencia_escaneo='scanner-matricula-envio',
        )
        revisar_documento(
            documento=evidencia.documento_soporte,
            actor=self.revisor,
            aceptar=True,
        )
        revisar_evidencia_matricula(
            evidencia=evidencia,
            actor=self.revisor,
            aceptar=True,
        )
        self.assertTrue(fase_documental_completa(self.solicitud))
        completar_fase_documental(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        self.solicitud.refresh_from_db()
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )

    def test_documento_bloqueado_impide_envio_a_revision(self):
        estudiante = self._participante()
        documento = registrar_documento(
            solicitud=self.solicitud,
            participante=estudiante,
            tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            origen_captura=OrigenCapturaDocumento.CAMERA,
            archivo=jpeg('identidad-bloqueada'),
            actor=self.usuario,
        )
        registrar_documento(
            solicitud=self.solicitud,
            participante=estudiante,
            tipo=TipoDocumentoFinanciacion.STUDENT_ID_BACK,
            origen_captura=OrigenCapturaDocumento.CAMERA,
            archivo=jpeg('identidad-reverso'),
            actor=self.usuario,
        )
        registrar_documento(
            solicitud=self.solicitud,
            participante=estudiante,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=pdf('ingresos'),
            actor=self.usuario,
        )
        registrar_resultado_escaneo(
            documento=documento,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.BLOCKED,
            referencia_escaneo='scanner-bloqueado',
        )
        self._matricula_aceptada()

        requisitos = {
            requisito.codigo: requisito
            for requisito in calcular_requisitos_documentales(self.solicitud)
        }
        self.assertIn('STUDENT_ID_FRONT', requisitos, requisitos.keys())
        self.assertFalse(requisitos['STUDENT_ID_FRONT'].cumplido)
        with self.assertRaises(ValidationError):
            completar_fase_documental(
                solicitud=self.solicitud,
                actor=self.usuario,
            )

    def test_no_completa_sin_terminos_aceptados(self):
        Consentimiento.objects.filter(solicitud=self.solicitud).delete()

        requisitos = {
            requisito.codigo: requisito
            for requisito in calcular_requisitos_documentales(self.solicitud)
        }

        self.assertFalse(requisitos['TERMS'].cumplido)
        with self.assertRaises(ValidationError):
            completar_fase_documental(
                solicitud=self.solicitud,
                actor=self.usuario,
            )

    def test_requisitos_de_adulto_no_incluyen_tutor(self):
        requisitos = {
            requisito.codigo: requisito
            for requisito in calcular_requisitos_documentales(self.solicitud)
        }

        self.assertNotIn('GUARDIAN', requisitos)

    def test_completa_con_servicio_central_y_reintento_no_duplica_historial(self):
        estudiante = self._participante()
        self._documentos_adulto(estudiante, aceptar=True)
        self._matricula_aceptada()

        requisitos = calcular_requisitos_documentales(self.solicitud)
        self.assertTrue(all(requisito.cumplido for requisito in requisitos))
        completar_fase_documental(solicitud=self.solicitud, actor=self.usuario)
        completar_fase_documental(solicitud=self.solicitud, actor=self.usuario)
        self.solicitud.refresh_from_db()

        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )
        self.assertEqual(
            HistorialEstadoSolicitud.objects.filter(
                solicitud=self.solicitud,
                estado_nuevo=EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
            ).count(),
            1,
        )

    def test_menor_no_completa_sin_tutor(self):
        self._participante(
            nacimiento=date(2012, 1, 1),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )
        requisitos = {
            requisito.codigo: requisito
            for requisito in calcular_requisitos_documentales(self.solicitud)
        }
        self.assertFalse(requisitos['GUARDIAN'].cumplido)

    def test_reemplazar_soporte_reinicia_revision_de_matricula(self):
        evidencia = self._matricula_aceptada()
        actualizada = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.usuario,
            institucion_declarada=evidencia.institucion_declarada,
            programa_curso=evidencia.programa_curso,
            periodo_academico=evidencia.periodo_academico,
            referencia_matricula=evidencia.referencia_matricula,
            archivo=pdf('matricula-reemplazo'),
        )

        self.assertEqual(actualizada.estado, EstadoEvidenciaMatricula.PENDING)
        self.assertEqual(
            actualizada.documento_soporte.estado_escaneo,
            EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
        )
        self.assertIsNotNone(actualizada.documento_soporte.reemplaza_a_id)
