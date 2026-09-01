from copy import deepcopy
from datetime import date
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

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
    CondicionesFinancieras,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.documentos import (
    registrar_documento,
    revisar_documento,
)
from financiacion_educativa.services.matricula import (
    registrar_o_actualizar_evidencia_matricula,
    revisar_evidencia_matricula,
)
from financiacion_educativa.services.outbox_correos import procesar_siguiente_correo
from financiacion_educativa.services.terminos import (
    publicar_version_terminos,
)
from financiacion_educativa.tests.delivery_backends import (
    RecordingInvitationDeliveryBackend,
)
from financiacion_educativa.tests.factories import crear_configuracion_financiera
from financiacion_educativa.tests.scan_helpers import (
    conceder_permisos_documentales,
    registrar_resultado_escaneo,
)
from financiacion_educativa.models import VersionTerminosFinanciacion
from instituciones.models import Institucion
from instituciones.services.credenciales import crear_credencial_api


BACKEND = (
    'financiacion_educativa.tests.delivery_backends.'
    'RecordingInvitationDeliveryBackend'
)
PAYLOAD = {
    'external_reference': 'INTEGRAL-2026-001',
    'first_names': 'CAMILA ANDREA',
    'last_names': 'ROJAS DIAZ',
    'phone': '3001234567',
    'email': 'camila@example.com',
    'address': 'Calle 10 # 20-30',
    'document_type': 'CC',
    'document_number': '0012345678',
    'birth_date': '2000-08-15',
    'enrollment_code': 'A2D-2026-00123',
    'academic_period': '2026-2',
    'campus': 'Sede Centro',
    'schedule': 'Nocturna',
    'program_name': 'INGLÉS BÁSICO A2 DIAMANTE',
    'enrollment_date': None,
    'plan_value': '2500000.00',
    'term': 6,
}


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


@override_settings(
    BRAND_PUBLIC_BASE_URL='https://credito.example.com',
    FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND,
    EDUCATIONAL_OPERATIONS_NOTIFICATION_EMAILS=[],
)
class FlujoIntegralFinanciacionEducativaTests(APITestCase):
    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.override = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.private_root.cleanup)
        RecordingInvitationDeliveryBackend.reset()

        self.institucion = Institucion.objects.create(
            nombre_comercial='Institucion Integral',
            razon_social='Institucion Integral SAS',
            numero_identificacion_tributaria='901700001',
        )
        self.credencial = crear_credencial_api(
            institucion=self.institucion,
            nombre='Prueba integral',
        )
        self.revisor = get_user_model().objects.create_user(
            username='revisor-integral@example.com',
            email='revisor-integral@example.com',
            password='Clave-2026',
            is_staff=True,
        )
        conceder_permisos_documentales(self.revisor)
        self.version = VersionTerminosFinanciacion.objects.create(
            tipo=TipoConsentimiento.TERMS,
            version='integral-v1',
            titulo='Terminos integrales',
            contenido='Contenido contractual de prueba.',
            obligatorio=True,
        )
        publicar_version_terminos(version=self.version)
        crear_configuracion_financiera()
        self.url_api = reverse(
            'financiacion_educativa_api:solicitud-crear'
        )

    def _headers(self):
        return {
            'HTTP_AUTHORIZATION': f'ApiKey {self.credencial.token}',
            'HTTP_IDEMPOTENCY_KEY': 'integral-idem-001',
        }

    def test_recorrido_completo_y_consulta_institucional_final(self):
        with self.captureOnCommitCallbacks(execute=True):
            creacion = self.client.post(
                self.url_api,
                deepcopy(PAYLOAD),
                format='json',
                **self._headers(),
            )
        procesar_siguiente_correo()
        enlace = RecordingInvitationDeliveryBackend.deliveries[0][
            'continuation_url'
        ]
        self.client.get(urlparse(enlace).path)
        self.client.post(
            reverse('financiacion_educativa_web:registro'),
            {
                'email': PAYLOAD['email'],
                'first_name': 'Camila',
                'last_name': 'Rojas',
                'password1': 'ClaveEducativa-2026',
                'password2': 'ClaveEducativa-2026',
            },
        )
        self.client.post(reverse('financiacion_educativa_web:confirmar'))
        solicitud = SolicitudFinanciacionEducativa.objects.get(
            pk=creacion.data['application_id']
        )
        url_terminos = reverse(
            'financiacion_educativa_web:terminos',
            kwargs={'solicitud_id': solicitud.pk},
        )
        self.client.get(url_terminos)
        self.client.post(
            url_terminos,
            {'accepted_versions': [str(self.version.pk)]},
        )
        solicitud.refresh_from_db()
        estudiante = solicitud.roles_participantes.get(
            rol=RolParticipante.STUDENT
        ).participante

        identidad = registrar_documento(
            solicitud=solicitud,
            participante=estudiante,
            tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            origen_captura=OrigenCapturaDocumento.CAMERA,
            archivo=jpeg('identidad-integral-frente'),
            actor=solicitud.usuario,
        )
        registrar_documento(
            solicitud=solicitud,
            participante=estudiante,
            tipo=TipoDocumentoFinanciacion.STUDENT_ID_BACK,
            origen_captura=OrigenCapturaDocumento.CAMERA,
            archivo=jpeg('identidad-integral-reverso'),
            actor=solicitud.usuario,
        )
        registrar_documento(
            solicitud=solicitud,
            participante=estudiante,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=pdf('ingresos-integral'),
            actor=solicitud.usuario,
        )
        previsualizacion = self.client.get(
            reverse(
                'financiacion_educativa_web:documento-previsualizar',
                kwargs={
                    'solicitud_id': solicitud.pk,
                    'documento_id': identidad.pk,
                },
            )
        )
        registrar_resultado_escaneo(
            documento=identidad,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
            referencia_escaneo='scanner-identidad-integral',
        )
        revisar_documento(
            documento=identidad,
            actor=self.revisor,
            aceptar=True,
        )
        evidencia = registrar_o_actualizar_evidencia_matricula(
            solicitud=solicitud,
            actor=solicitud.usuario,
            institucion_declarada=self.institucion.nombre_comercial,
            programa_curso=solicitud.nombre_curso,
            periodo_academico=solicitud.periodo_academico,
            referencia_matricula=solicitud.codigo_matricula,
            archivo=pdf('matricula-integral'),
        )
        registrar_resultado_escaneo(
            documento=evidencia.documento_soporte,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
            referencia_escaneo='scanner-matricula-integral',
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

        finanzas = self.client.get(
            reverse(
                'financiacion_educativa_web:finanzas',
                kwargs={'solicitud_id': solicitud.pk},
            ),
        )
        envio = self.client.post(
            reverse(
                'financiacion_educativa_web:documentacion-completar',
                kwargs={'solicitud_id': solicitud.pk},
            ),
            follow=True,
        )
        detalle = self.client.get(
            reverse(
                'financiacion_educativa_api:solicitud-detalle',
                kwargs={'application_id': solicitud.pk},
            ),
            HTTP_AUTHORIZATION=f'ApiKey {self.credencial.token}',
        )
        with self.captureOnCommitCallbacks(execute=True):
            replay = self.client.post(
                self.url_api,
                deepcopy(PAYLOAD),
                format='json',
                **self._headers(),
            )

        solicitud.refresh_from_db()
        self.assertEqual(creacion.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(finanzas.status_code, status.HTTP_200_OK)
        self.assertContains(finanzas, 'Condiciones definitivas pendientes')
        self.assertEqual(previsualizacion.status_code, status.HTTP_200_OK)
        self.assertEqual(previsualizacion['X-Frame-Options'], 'SAMEORIGIN')
        previsualizacion.close()
        self.assertFalse(
            CondicionesFinancieras.objects.filter(solicitud=solicitud).exists()
        )
        self.assertFalse(
            solicitud.roles_participantes.filter(
                rol=RolParticipante.GUARDIAN
            ).exists()
        )
        self.assertContains(envio, 'Expediente enviado')
        self.assertEqual(
            solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )
        self.assertEqual(detalle.status_code, status.HTTP_200_OK)
        self.assertEqual(
            detalle.data['status'],
            'UNDER_REVIEW',
        )
        self.assertEqual(detalle.data['document_number'], '0012345678')
        self.assertIsNone(detalle.data['enrollment_date'])
        self.assertEqual(replay.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(replay['Idempotent-Replayed'], 'true')
        self.assertEqual(SolicitudFinanciacionEducativa.objects.count(), 1)
        self.assertEqual(len(RecordingInvitationDeliveryBackend.deliveries), 1)

    def test_recorrido_completo_de_estudiante_menor_con_tutor_separado(self):
        payload = {
            **deepcopy(PAYLOAD),
            'external_reference': 'INTEGRAL-MENOR-2026-001',
            'email': 'estudiante-menor@example.com',
            'document_type': TipoDocumentoIdentidad.TI,
            'document_number': '0011223344',
            'birth_date': '2012-08-15',
        }
        headers = {
            'HTTP_AUTHORIZATION': f'ApiKey {self.credencial.token}',
            'HTTP_IDEMPOTENCY_KEY': 'integral-menor-idem-001',
        }
        with self.captureOnCommitCallbacks(execute=True):
            creacion = self.client.post(
                self.url_api,
                payload,
                format='json',
                **headers,
            )
        procesar_siguiente_correo()
        enlace = RecordingInvitationDeliveryBackend.deliveries[0][
            'continuation_url'
        ]
        self.client.get(urlparse(enlace).path)
        self.client.post(
            reverse('financiacion_educativa_web:registro'),
            {
                'email': payload['email'],
                'first_name': 'Estudiante',
                'last_name': 'Menor',
                'password1': 'ClaveEducativa-2026',
                'password2': 'ClaveEducativa-2026',
            },
        )
        self.client.post(reverse('financiacion_educativa_web:confirmar'))
        solicitud = SolicitudFinanciacionEducativa.objects.get(
            pk=creacion.data['application_id']
        )
        self.client.get(
            reverse(
                'financiacion_educativa_web:terminos',
                kwargs={'solicitud_id': solicitud.pk},
            )
        )
        self.client.post(
            reverse(
                'financiacion_educativa_web:terminos',
                kwargs={'solicitud_id': solicitud.pk},
            ),
            {'accepted_versions': [str(self.version.pk)]},
        )
        solicitud.refresh_from_db()
        estudiante = solicitud.roles_participantes.get(
            rol=RolParticipante.STUDENT
        ).participante
        self.assertFalse(
            solicitud.roles_participantes.filter(
                rol=RolParticipante.PRINCIPAL_DEBTOR
            ).exists()
        )

        tutor_response = self.client.post(
            (
                reverse(
                    'financiacion_educativa_web:participante-nuevo',
                    kwargs={'solicitud_id': solicitud.pk},
                )
                + '?tipo=tutor'
            ),
            {
                'tipo_persona': 'tutor',
                'nombres': 'TUTOR',
                'apellidos': 'RESPONSABLE',
                'tipo_documento': TipoDocumentoIdentidad.CC,
                'numero_documento': '9000999988',
                'pais_expedicion': 'CO',
                'fecha_nacimiento': '1980-01-01',
                'correo': 'tutor-integral@example.com',
                'telefono': '3011234567',
                'relacion_estudiante': RelacionEstudiante.LEGAL_GUARDIAN,
            },
        )
        self.assertEqual(tutor_response.status_code, status.HTTP_302_FOUND)
        tutor = solicitud.roles_participantes.get(
            rol=RolParticipante.GUARDIAN
        ).participante
        self.assertNotEqual(estudiante.pk, tutor.pk)
        self.assertEqual(
            solicitud.roles_participantes.get(
                rol=RolParticipante.PRINCIPAL_DEBTOR
            ).participante_id,
            tutor.pk,
        )

        identidad_estudiante = registrar_documento(
            solicitud=solicitud,
            participante=estudiante,
            tipo=TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            origen_captura=OrigenCapturaDocumento.CAMERA,
            archivo=jpeg('identidad-estudiante-menor-frente'),
            actor=solicitud.usuario,
        )
        registrar_documento(
            solicitud=solicitud,
            participante=estudiante,
            tipo=TipoDocumentoFinanciacion.STUDENT_ID_BACK,
            origen_captura=OrigenCapturaDocumento.CAMERA,
            archivo=jpeg('identidad-estudiante-menor-reverso'),
            actor=solicitud.usuario,
        )
        registrar_documento(
            solicitud=solicitud,
            participante=tutor,
            tipo=TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
            origen_captura=OrigenCapturaDocumento.CAMERA,
            archivo=jpeg('identidad-tutor-frente'),
            actor=solicitud.usuario,
        )
        registrar_documento(
            solicitud=solicitud,
            participante=tutor,
            tipo=TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
            origen_captura=OrigenCapturaDocumento.CAMERA,
            archivo=jpeg('identidad-tutor-reverso'),
            actor=solicitud.usuario,
        )
        registrar_documento(
            solicitud=solicitud,
            participante=tutor,
            tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=pdf('ingresos-tutor'),
            actor=solicitud.usuario,
        )
        evidencia_matricula = registrar_o_actualizar_evidencia_matricula(
            solicitud=solicitud,
            actor=solicitud.usuario,
            institucion_declarada=self.institucion.nombre_comercial,
            programa_curso=solicitud.nombre_curso,
            periodo_academico=solicitud.periodo_academico,
            referencia_matricula=solicitud.codigo_matricula,
            archivo=pdf('matricula-estudiante-menor'),
        )
        registrar_resultado_escaneo(
            documento=evidencia_matricula.documento_soporte,
            actor=self.revisor,
            estado=EstadoEscaneoDocumento.SAFE,
        )
        revisar_documento(
            documento=evidencia_matricula.documento_soporte,
            actor=self.revisor,
            aceptar=True,
        )
        revisar_evidencia_matricula(
            evidencia=evidencia_matricula,
            actor=self.revisor,
            aceptar=True,
        )

        previsualizacion = self.client.get(
            reverse(
                'financiacion_educativa_web:documento-previsualizar',
                kwargs={
                    'solicitud_id': solicitud.pk,
                    'documento_id': identidad_estudiante.pk,
                },
            )
        )
        finanzas = self.client.get(
            reverse(
                'financiacion_educativa_web:finanzas',
                kwargs={'solicitud_id': solicitud.pk},
            ),
        )
        envio = self.client.post(
            reverse(
                'financiacion_educativa_web:documentacion-completar',
                kwargs={'solicitud_id': solicitud.pk},
            ),
            follow=True,
        )
        detalle = self.client.get(
            reverse(
                'financiacion_educativa_api:solicitud-detalle',
                kwargs={'application_id': solicitud.pk},
            ),
            HTTP_AUTHORIZATION=f'ApiKey {self.credencial.token}',
        )

        solicitud.refresh_from_db()
        self.assertEqual(creacion.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(previsualizacion.status_code, status.HTTP_200_OK)
        previsualizacion.close()
        self.assertContains(finanzas, 'Condiciones definitivas pendientes')
        self.assertEqual(
            CondicionesFinancieras.objects.filter(
                solicitud=solicitud,
                activa=True,
            ).count(),
            0,
        )
        self.assertContains(envio, 'Expediente enviado')
        self.assertEqual(
            solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        )
        self.assertEqual(
            detalle.data['status'],
            'UNDER_REVIEW',
        )
