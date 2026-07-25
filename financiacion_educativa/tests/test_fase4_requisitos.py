from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

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
)
from financiacion_educativa.models import HistorialEstadoSolicitud
from financiacion_educativa.services.documentos import (
    registrar_documento,
    registrar_resultado_escaneo,
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


def pdf(marca):
    return SimpleUploadedFile(
        f'{marca}.pdf',
        b'%PDF-1.7\n' + marca.encode('ascii') + b'\n%%EOF',
        content_type='application/pdf',
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
        self.solicitud = crear_solicitud()
        self.solicitud.usuario = self.usuario
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['usuario', 'estado'])
        self.institucion_original_id = self.solicitud.institucion_id

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
        documento = registrar_documento(
            solicitud=self.solicitud,
            participante=participante,
            tipo=tipo,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
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

    def test_requisitos_no_completan_con_documentos_pendientes(self):
        estudiante = self._participante()
        registrar_documento(
            solicitud=self.solicitud,
            participante=estudiante,
            tipo=TipoDocumentoFinanciacion.STUDENT_IDENTIFICATION,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=pdf('identidad-pendiente'),
            actor=self.usuario,
        )
        self._matricula_aceptada()

        self.assertFalse(fase_documental_completa(self.solicitud))
        with self.assertRaises(ValidationError):
            completar_fase_documental(
                solicitud=self.solicitud,
                actor=self.usuario,
            )

    def test_completa_con_servicio_central_y_reintento_no_duplica_historial(self):
        estudiante = self._participante()
        self._aceptar_documento(
            TipoDocumentoFinanciacion.STUDENT_IDENTIFICATION,
            pdf('identidad-aceptada'),
            estudiante,
        )
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
