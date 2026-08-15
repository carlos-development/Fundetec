from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoSolicitudFinanciacion,
    OrigenCapturaDocumento,
    RelacionEstudiante,
    RolParticipante,
    TipoConsentimiento,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import (
    Consentimiento,
    HistorialEstadoSolicitud,
    ProcesoAutomatizacionEducativa,
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
    sincronizar_estudiante_desde_solicitud,
)
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.scan_helpers import (
    conceder_permisos_documentales,
    registrar_resultado_escaneo,
)


def pdf(nombre):
    return SimpleUploadedFile(
        f'{nombre}.pdf',
        b'%PDF-1.7\n' + nombre.encode('ascii') + b'\n%%EOF',
        content_type='application/pdf',
    )


def jpeg(nombre):
    return SimpleUploadedFile(
        f'{nombre}.jpg',
        b'\xff\xd8\xff' + nombre.encode('ascii') + b'\xff\xd9',
        content_type='image/jpeg',
    )


class ExpedienteVerificableIteracionTests(TestCase):
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
            username='expediente@example.com',
            email='expediente@example.com',
            password='Clave-2026',
        )
        self.otro = User.objects.create_user(
            username='expediente-otro@example.com',
            email='expediente-otro@example.com',
            password='Clave-2026',
        )
        self.revisor = User.objects.create_user(
            username='expediente-revisor@example.com',
            email='expediente-revisor@example.com',
            password='Clave-2026',
            is_staff=True,
        )
        conceder_permisos_documentales(self.revisor)
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.tipo_documento_estudiante = TipoDocumentoIdentidad.CC
        self.solicitud.numero_documento_estudiante = '0012345678'
        self.solicitud.fecha_nacimiento_estudiante = date(2000, 5, 10)
        self.solicitud.codigo_matricula = 'MAT-001'
        self.solicitud.periodo_academico = '2026-2'
        self.solicitud.sede = 'Sede Centro'
        self.solicitud.jornada = 'Nocturna'
        self.solicitud.save()

        version = VersionTerminosFinanciacion.objects.create(
            tipo=TipoConsentimiento.TERMS,
            version='expediente-v1',
            titulo='Terminos expediente',
            contenido='Contenido contractual de prueba.',
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
            evidencia_hash='1' * 64,
        )

    def _url(self, nombre, **kwargs):
        return reverse(
            f'financiacion_educativa_web:{nombre}',
            kwargs={'solicitud_id': self.solicitud.pk, **kwargs},
        )

    def _aceptar_documento(self, participante):
        documentos = []
        for tipo, archivo, origen in (
            (
                TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                jpeg('identidad-frente'),
                OrigenCapturaDocumento.CAMERA,
            ),
            (
                TipoDocumentoFinanciacion.STUDENT_ID_BACK,
                jpeg('identidad-reverso'),
                OrigenCapturaDocumento.CAMERA,
            ),
            (
                TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
                pdf('ingresos'),
                OrigenCapturaDocumento.USER_UPLOAD,
            ),
        ):
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
                estado=EstadoEscaneoDocumento.SAFE,
                referencia_escaneo=f'scanner-{tipo}',
            )
            documentos.append(
                revisar_documento(
                    documento=documento,
                    actor=self.revisor,
                    aceptar=True,
                )
            )
        return documentos

    def _aceptar_matricula(self):
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=self.solicitud,
            actor=self.usuario,
            institucion_declarada=self.solicitud.institucion.nombre_comercial,
            programa_curso=self.solicitud.nombre_curso,
            periodo_academico=self.solicitud.periodo_academico,
            referencia_matricula=self.solicitud.codigo_matricula,
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

    def test_adulto_usa_identidad_institucional_y_no_habilita_tutor(self):
        estudiante = sincronizar_estudiante_desde_solicitud(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        self.client.force_login(self.usuario)

        pagina = self.client.get(self._url('documentacion'))
        tutor = self.client.get(
            f'{self._url("participante-nuevo")}?tipo=tutor'
        )

        self.assertEqual(tutor.status_code, 404)
        self.assertContains(pagina, 'No se requiere tutor')
        self.assertContains(pagina, '******5678')
        self.assertNotContains(pagina, 'Editar identificaci&oacute;n')
        self.assertEqual(
            set(estudiante.roles.values_list('rol', flat=True)),
            {RolParticipante.STUDENT, RolParticipante.PRINCIPAL_DEBTOR},
        )

    def test_menor_habilita_tutor_y_rechaza_un_tutor_menor(self):
        self.solicitud.tipo_documento_estudiante = TipoDocumentoIdentidad.TI
        self.solicitud.fecha_nacimiento_estudiante = date(2012, 5, 10)
        self.solicitud.save()
        sincronizar_estudiante_desde_solicitud(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        self.client.force_login(self.usuario)

        pagina = self.client.get(
            f'{self._url("participante-nuevo")}?tipo=tutor'
        )
        self.assertEqual(pagina.status_code, 200)

        with self.assertRaises(ValidationError):
            registrar_o_actualizar_participante(
                solicitud=self.solicitud,
                actor=self.usuario,
                datos=DatosParticipante(
                    nombres='Tutor',
                    apellidos='Menor',
                    tipo_documento=TipoDocumentoIdentidad.TI,
                    numero_documento='99887766',
                    fecha_nacimiento=date(2013, 1, 1),
                    relacion_estudiante=RelacionEstudiante.LEGAL_GUARDIAN,
                ),
                roles={
                    RolParticipante.GUARDIAN,
                    RolParticipante.PRINCIPAL_DEBTOR,
                },
            )

    def test_envio_incompleto_muestra_pendientes_y_no_cambia_estado(self):
        sincronizar_estudiante_desde_solicitud(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        self.client.force_login(self.usuario)

        respuesta = self.client.post(
            self._url('documentacion-completar'),
            follow=True,
        )

        self.solicitud.refresh_from_db()
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        )
        self.assertContains(respuesta, 'No fue posible enviar el expediente')
        self.assertContains(respuesta, 'Abrir c&aacute;mara')
        self.assertContains(respuesta, 'Cargar certificado')
        self.assertNotContains(respuesta, 'Completar evidencia')
        self.assertContains(
            respuesta,
            'Esto no impide enviar el expediente',
        )

    @override_settings(FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True)
    def test_envio_completo_transiciona_y_reintento_es_idempotente(self):
        estudiante = sincronizar_estudiante_desde_solicitud(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        self._aceptar_documento(estudiante)
        self._aceptar_matricula()
        self.client.force_login(self.usuario)

        with self.captureOnCommitCallbacks(execute=True):
            primera = self.client.post(
                self._url('documentacion-completar'),
                follow=True,
            )
        with self.captureOnCommitCallbacks(execute=True):
            segunda = self.client.post(
                self._url('documentacion-completar'),
                follow=True,
            )

        self.solicitud.refresh_from_db()
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )
        self.assertContains(primera, 'Estamos procesando tu expediente')
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(
            ProcesoAutomatizacionEducativa.objects.filter(
                solicitud=self.solicitud,
            ).count(),
            1,
        )
        self.assertEqual(
            HistorialEstadoSolicitud.objects.filter(
                solicitud=self.solicitud,
                estado_nuevo=EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
            ).count(),
            1,
        )

    def test_accion_rechaza_get_csrf_e_idor(self):
        self.client.force_login(self.usuario)
        self.assertEqual(
            self.client.get(self._url('documentacion-completar')).status_code,
            405,
        )

        otro = Client()
        otro.force_login(self.otro)
        self.assertEqual(
            otro.post(self._url('documentacion-completar')).status_code,
            404,
        )

        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.usuario)
        self.assertEqual(
            csrf.post(self._url('documentacion-completar')).status_code,
            403,
        )
