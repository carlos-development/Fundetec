from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    MotivoRechazoDocumento,
    OrigenCapturaDocumento,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
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
from financiacion_educativa.services.politica_documental import (
    CARAS_IDENTIFICACION_POR_TIPO,
    caras_identificacion_requeridas,
    construir_politica_documental,
    requisito_listo_para_aprobacion,
    requisito_listo_para_envio,
)
from financiacion_educativa.services.revision import _validar_aprobacion
from financiacion_educativa.services.requisitos_documentales import (
    calcular_requisitos_documentales,
)
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.scan_helpers import (
    conceder_permisos_documentales,
    registrar_resultado_escaneo,
)
from financiacion_educativa.tests.scan_backends import BackendInfectado
from financiacion_educativa.services.escaneo_documentos import (
    procesar_escaneo_documento,
)


def jpeg(nombre):
    return SimpleUploadedFile(
        f'{nombre}.jpg',
        b'\xff\xd8\xff' + nombre.encode('ascii') + b'\xff\xd9',
        content_type='image/jpeg',
    )


def pdf(nombre):
    return SimpleUploadedFile(
        f'{nombre}.pdf',
        b'%PDF-1.7\n' + nombre.encode('ascii') + b'\n%%EOF',
        content_type='application/pdf',
    )


class PoliticaDocumentalTests(TestCase):
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
            username='policy-owner@example.com',
            email='policy-owner@example.com',
            password='Clave-2026',
        )
        self.revisor = User.objects.create_user(
            username='policy-reviewer@example.com',
            password='Clave-2026',
            is_staff=True,
        )
        conceder_permisos_documentales(self.revisor)
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.save(update_fields=['estado'])
        self.estudiante = registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='Laura',
                apellidos='Diaz',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='1000100010',
                fecha_nacimiento=date(1990, 1, 1),
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles={RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )

    def _cargar_y_aceptar(self, tipo, archivo, origen, participante=None):
        documento = registrar_documento(
            solicitud=self.solicitud,
            participante=participante,
            tipo=tipo,
            origen_captura=origen,
            archivo=archivo,
            actor=self.usuario,
        )
        registrar_resultado_escaneo(
            documento=documento,
            actor=self.revisor,
            estado='SAFE',
        )
        revisar_documento(
            documento=documento,
            actor=self.revisor,
            aceptar=True,
        )
        return documento

    def _aceptar_obligatorios(self):
        for tipo, archivo, origen in (
            (
                TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                jpeg('policy-front'),
                OrigenCapturaDocumento.CAMERA,
            ),
            (
                TipoDocumentoFinanciacion.STUDENT_ID_BACK,
                jpeg('policy-back'),
                OrigenCapturaDocumento.CAMERA,
            ),
            (
                TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
                pdf('policy-income'),
                OrigenCapturaDocumento.USER_UPLOAD,
            ),
        ):
            self._cargar_y_aceptar(
                tipo,
                archivo,
                origen,
                self.estudiante,
            )

    def test_matriz_explicita_para_todos_los_tipos_admitidos(self):
        esperadas = {
            TipoDocumentoIdentidad.CC: ('frente', 'reverso'),
            TipoDocumentoIdentidad.TI: ('frente', 'reverso'),
            TipoDocumentoIdentidad.PASSPORT: ('frente',),
            TipoDocumentoIdentidad.CE: ('frente',),
            TipoDocumentoIdentidad.RC: ('frente',),
            TipoDocumentoIdentidad.OTHER: ('frente',),
        }
        self.assertEqual(CARAS_IDENTIFICACION_POR_TIPO, esperadas)
        for tipo, caras in esperadas.items():
            self.assertEqual(caras_identificacion_requeridas(tipo), caras)

    def test_certificado_ingresos_es_obligatorio_con_o_sin_documento(self):
        politica_nueva = construir_politica_documental(self.solicitud)
        requisito_nuevo = next(
            item for item in politica_nueva if item.codigo == 'INCOME_CERTIFICATE'
        )
        self.assertTrue(requisito_nuevo.obligatorio)
        self.assertIsNone(requisito_nuevo.documento)

        documento = self._cargar_y_aceptar(
            TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            pdf('existing-income'),
            OrigenCapturaDocumento.USER_UPLOAD,
            self.estudiante,
        )
        requisito_existente = next(
            item
            for item in construir_politica_documental(self.solicitud)
            if item.codigo == 'INCOME_CERTIFICATE'
        )
        self.assertTrue(requisito_existente.obligatorio)
        self.assertEqual(requisito_existente.documento, documento)

    def test_datos_matricula_sin_soporte_no_crean_pendiente_ni_bloquean(self):
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.usuario,
            institucion_declarada='Institucion aliada',
            programa_curso=self.solicitud.nombre_curso,
            periodo_academico='2026-2',
        )
        self._aceptar_obligatorios()
        politica = construir_politica_documental(self.solicitud)

        self.assertIsNone(evidencia.documento_soporte)
        self.assertNotIn('ENROLLMENT_EVIDENCE', {item.codigo for item in politica})
        self.assertNotIn(
            'ENROLLMENT_EVIDENCE',
            {
                item.codigo
                for item in calcular_requisitos_documentales(self.solicitud)
            },
        )
        self.assertTrue(all(requisito_listo_para_envio(item) for item in politica))
        _validar_aprobacion(self.solicitud)

    def test_soporte_opcional_aportado_debe_resolverse(self):
        self._aceptar_obligatorios()
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.usuario,
            institucion_declarada='Institucion aliada',
            programa_curso=self.solicitud.nombre_curso,
            periodo_academico='2026-2',
            archivo=pdf('optional-enrollment'),
        )
        requisito = next(
            item
            for item in construir_politica_documental(self.solicitud)
            if item.codigo == 'ENROLLMENT_EVIDENCE'
        )

        self.assertFalse(requisito.obligatorio)
        self.assertFalse(requisito_listo_para_envio(requisito))
        self.assertFalse(requisito_listo_para_aprobacion(requisito))
        requisito_envio = next(
            item
            for item in calcular_requisitos_documentales(self.solicitud)
            if item.codigo == 'ENROLLMENT_EVIDENCE'
        )
        self.assertFalse(requisito_envio.cumplido)
        with self.assertRaises(ValidationError):
            _validar_aprobacion(self.solicitud)

        registrar_resultado_escaneo(
            documento=evidencia.documento_soporte,
            actor=self.revisor,
            estado='SAFE',
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
        requisito = next(
            item
            for item in construir_politica_documental(self.solicitud)
            if item.codigo == 'ENROLLMENT_EVIDENCE'
        )
        self.assertTrue(requisito_listo_para_envio(requisito))
        self.assertTrue(requisito_listo_para_aprobacion(requisito))
        requisito_envio = next(
            item
            for item in calcular_requisitos_documentales(self.solicitud)
            if item.codigo == 'ENROLLMENT_EVIDENCE'
        )
        self.assertTrue(requisito_envio.cumplido)
        _validar_aprobacion(self.solicitud)

    def test_soporte_opcional_bloqueado_no_se_ignora(self):
        self._aceptar_obligatorios()
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.usuario,
            institucion_declarada='Institucion aliada',
            programa_curso=self.solicitud.nombre_curso,
            periodo_academico='2026-2',
            archivo=pdf('infected-enrollment'),
        )
        procesar_escaneo_documento(
            documento=evidencia.documento_soporte,
            actor=self.revisor,
            backend=BackendInfectado(),
        )
        requisito = next(
            item
            for item in construir_politica_documental(self.solicitud)
            if item.codigo == 'ENROLLMENT_EVIDENCE'
        )

        self.assertFalse(requisito_listo_para_envio(requisito))
        self.assertFalse(requisito_listo_para_aprobacion(requisito))
        requisito_envio = next(
            item
            for item in calcular_requisitos_documentales(self.solicitud)
            if item.codigo == 'ENROLLMENT_EVIDENCE'
        )
        self.assertFalse(requisito_envio.cumplido)
        with self.assertRaises(ValidationError):
            _validar_aprobacion(self.solicitud)

    def test_soporte_opcional_rechazado_bloquea_envio_y_aprobacion(self):
        self._aceptar_obligatorios()
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.usuario,
            institucion_declarada='Institucion aliada',
            programa_curso=self.solicitud.nombre_curso,
            periodo_academico='2026-2',
            archivo=pdf('rejected-enrollment'),
        )
        registrar_resultado_escaneo(
            documento=evidencia.documento_soporte,
            actor=self.revisor,
            estado='SAFE',
        )
        revisar_documento(
            documento=evidencia.documento_soporte,
            actor=self.revisor,
            aceptar=True,
        )
        revisar_evidencia_matricula(
            evidencia=evidencia,
            actor=self.revisor,
            aceptar=False,
            motivo_rechazo=MotivoRechazoDocumento.WRONG_DOCUMENT,
        )
        requisito = next(
            item
            for item in construir_politica_documental(self.solicitud)
            if item.codigo == 'ENROLLMENT_EVIDENCE'
        )
        self.assertFalse(requisito_listo_para_envio(requisito))
        self.assertFalse(requisito_listo_para_aprobacion(requisito))
        requisito_envio = next(
            item
            for item in calcular_requisitos_documentales(self.solicitud)
            if item.codigo == 'ENROLLMENT_EVIDENCE'
        )
        self.assertFalse(requisito_envio.cumplido)
        with self.assertRaises(ValidationError):
            _validar_aprobacion(self.solicitud)

    def test_envio_y_aprobacion_comparten_la_misma_politica(self):
        self._aceptar_obligatorios()
        politica = construir_politica_documental(self.solicitud)

        self.assertTrue(all(requisito_listo_para_envio(item) for item in politica))
        self.assertTrue(
            all(requisito_listo_para_aprobacion(item) for item in politica)
        )
        _validar_aprobacion(self.solicitud)
