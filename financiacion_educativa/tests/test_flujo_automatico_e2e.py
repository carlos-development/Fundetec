import json
from copy import deepcopy
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo,
    EstadoEscaneoDocumento,
    EstadoProcesoAutomatizacionEducativa,
    EstadoProcesoFirmaEducativa,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    OrigenCapturaDocumento,
    RolParticipante,
    TipoArtefactoContractualEducativo,
    TipoConsentimiento,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    CondicionesFinancieras,
    EventoWebhookFirmaEducativa,
    OutboxCorreoEducativo,
    ProcesoFirmaEducativa,
    SolicitudFinanciacionEducativa,
    VersionTerminosFinanciacion,
)
from financiacion_educativa.services.documentos import registrar_documento
from financiacion_educativa.services.cola_automatizacion import (
    procesar_siguiente_trabajo,
)
from financiacion_educativa.services.outbox_correos import procesar_siguiente_correo
from financiacion_educativa.services.terminos import publicar_version_terminos
from financiacion_educativa.tests.delivery_backends import (
    RecordingInvitationDeliveryBackend,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    imagen_jpeg_prueba,
)
from financiacion_educativa.tests.signature_backends import (
    RecordingEducationalSignatureBackend,
)
from instituciones.models import Institucion
from instituciones.services.credenciales import crear_credencial_api


INVITATION_BACKEND = (
    'financiacion_educativa.tests.delivery_backends.'
    'RecordingInvitationDeliveryBackend'
)
SCAN_BACKEND = 'financiacion_educativa.tests.scan_backends.BackendLimpio'
AI_BACKEND = (
    'financiacion_educativa.tests.ai_validation_backends.BackendIAConcluyente'
)
SIGNATURE_BACKEND = (
    'financiacion_educativa.tests.signature_backends.'
    'RecordingEducationalSignatureBackend'
)
PAYLOAD = {
    'external_reference': 'E2E-AUTOMATICO-001',
    'first_names': 'CAMILA ANDREA',
    'last_names': 'ROJAS DIAZ',
    'phone': '3001234567',
    'email': 'e2e-automatico@example.com',
    'address': 'Calle 10 # 20-30',
    'document_type': 'CC',
    'document_number': '1000123456',
    'birth_date': '2000-08-15',
    'enrollment_code': 'MAT-E2E-001',
    'academic_period': '2026-2',
    'campus': 'Sede Centro',
    'schedule': 'Nocturna',
    'program_name': 'INGLES BASICO A2',
    'enrollment_date': None,
    'plan_value': '2000000.00',
    'term': 6,
}


@override_settings(
    DEBUG=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BRAND_PUBLIC_BASE_URL='https://credito.example.com',
    FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=INVITATION_BACKEND,
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True,
    FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=SCAN_BACKEND,
    FINANCIACION_EDUCATIVA_ALLOW_TEST_SCAN_BACKENDS=True,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND=AI_BACKEND,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=True,
    FINANCIACION_EDUCATIVA_ALLOW_TEST_AI_BACKENDS=True,
    FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND=SIGNATURE_BACKEND,
    FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS=True,
    FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET='e2e-webhook-secret',
    FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY='e2e-hmac-key',
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
class FlujoAutomaticoE2ETests(APITestCase):
    def setUp(self):
        self.private_root = TemporaryDirectory()
        self.override_private = override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.private_root.name,
        )
        self.override_private.enable()
        self.addCleanup(self.override_private.disable)
        self.addCleanup(self.private_root.cleanup)
        RecordingInvitationDeliveryBackend.reset()
        RecordingEducationalSignatureBackend.reset()

        self.institucion = Institucion.objects.create(
            nombre_comercial='Institucion E2E',
            razon_social='Institucion E2E SAS',
            numero_identificacion_tributaria='901700002',
        )
        self.credencial = crear_credencial_api(
            institucion=self.institucion,
            nombre='Prueba E2E automatica',
        )
        version = VersionTerminosFinanciacion.objects.create(
            tipo=TipoConsentimiento.TERMS,
            version='e2e-automatico-v1',
            titulo='Terminos E2E',
            contenido='Contenido contractual exclusivo de prueba.',
            obligatorio=True,
        )
        publicar_version_terminos(version=version)
        self.version_terminos = version
        crear_configuracion_financiera()

    def _api_headers(self):
        return {
            'HTTP_AUTHORIZATION': f'ApiKey {self.credencial.token}',
            'HTTP_IDEMPOTENCY_KEY': 'e2e-automatico-idempotency',
        }

    def _registrar_documento(self, *, solicitud, participante, tipo, nombre):
        with self.captureOnCommitCallbacks(execute=True):
            documento = registrar_documento(
                solicitud=solicitud,
                participante=participante,
                tipo=tipo,
                origen_captura=(
                    OrigenCapturaDocumento.CAMERA
                    if tipo in {
                        TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                        TipoDocumentoFinanciacion.STUDENT_ID_BACK,
                    }
                    else OrigenCapturaDocumento.USER_UPLOAD
                ),
                archivo=imagen_jpeg_prueba(f'{nombre}.jpg', nombre),
                actor=solicitud.usuario,
            )
        documento.refresh_from_db()
        self.assertEqual(
            documento.estado_escaneo,
            EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
        )
        self.assertEqual(
            documento.estado_validacion,
            EstadoValidacionDocumento.PENDING,
        )
        return documento

    def test_api_hasta_aprobacion_firmada_sin_pasos_administrativos(self):
        with self.captureOnCommitCallbacks(execute=True):
            creacion = self.client.post(
                reverse('financiacion_educativa_api:solicitud-crear'),
                deepcopy(PAYLOAD),
                format='json',
                **self._api_headers(),
            )
        self.assertEqual(creacion.status_code, status.HTTP_202_ACCEPTED)
        resultado_correo = procesar_siguiente_correo()
        self.assertTrue(resultado_correo.procesado)
        self.assertEqual(len(RecordingInvitationDeliveryBackend.deliveries), 1)

        enlace = RecordingInvitationDeliveryBackend.deliveries[0]['continuation_url']
        self.client.get(urlparse(enlace).path)
        registro = self.client.post(
            reverse('financiacion_educativa_web:registro'),
            {
                'email': PAYLOAD['email'],
                'first_name': 'Camila',
                'last_name': 'Rojas',
                'password1': 'ClaveEducativa-2026',
                'password2': 'ClaveEducativa-2026',
            },
        )
        self.assertEqual(registro.status_code, status.HTTP_302_FOUND)
        confirmacion = self.client.post(
            reverse('financiacion_educativa_web:confirmar')
        )
        self.assertEqual(confirmacion.status_code, status.HTTP_302_FOUND)

        solicitud = SolicitudFinanciacionEducativa.objects.get(
            pk=creacion.data['application_id']
        )
        self.assertIsInstance(solicitud.usuario, get_user_model())
        terminos_url = reverse(
            'financiacion_educativa_web:terminos',
            kwargs={'solicitud_id': solicitud.pk},
        )
        self.client.get(terminos_url)
        terminos = self.client.post(
            terminos_url,
            {'accepted_versions': [str(self.version_terminos.pk)]},
        )
        self.assertEqual(terminos.status_code, status.HTTP_302_FOUND)
        solicitud.refresh_from_db()
        estudiante = solicitud.roles_participantes.get(
            rol=RolParticipante.STUDENT
        ).participante

        for tipo, nombre in (
            (TipoDocumentoFinanciacion.STUDENT_ID_FRONT, 'e2e-frente'),
            (TipoDocumentoFinanciacion.STUDENT_ID_BACK, 'e2e-reverso'),
            (TipoDocumentoFinanciacion.INCOME_CERTIFICATE, 'e2e-ingresos'),
        ):
            self._registrar_documento(
                solicitud=solicitud,
                participante=estudiante,
                tipo=tipo,
                nombre=nombre,
            )

        with self.captureOnCommitCallbacks(execute=True):
            completar = self.client.post(
                reverse(
                    'financiacion_educativa_web:documentacion-completar',
                    kwargs={'solicitud_id': solicitud.pk},
                )
        )
        self.assertEqual(completar.status_code, status.HTTP_302_FOUND)
        resultado_correo = procesar_siguiente_correo()
        self.assertTrue(resultado_correo.procesado)

        for _ in range(10):
            resultado_worker = procesar_siguiente_trabajo()
            self.assertTrue(resultado_worker.procesado)
            if (
                resultado_worker.estado
                == EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE
            ):
                break
        else:
            self.fail('El worker no alcanzo PENDING_SIGNATURE.')

        solicitud.refresh_from_db()
        proceso = ProcesoFirmaEducativa.objects.get(solicitud=solicitud)
        self.assertEqual(
            solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_SIGNATURE,
        )
        self.assertEqual(proceso.estado, EstadoProcesoFirmaEducativa.SENT)
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 1)
        resultado_correo = procesar_siguiente_correo()
        self.assertTrue(resultado_correo.procesado)
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                solicitud=solicitud,
                estado='SENT',
            ).count(),
            3,
        )
        fotografia = CondicionesFinancieras.objects.get(
            solicitud=solicitud,
            activa=True,
        )
        self.assertTrue(fotografia.bloqueada)
        self.assertEqual(
            ArtefactoContractualEducativo.objects.filter(
                solicitud=solicitud,
                vigente=True,
            ).count(),
            2,
        )

        detalle_previo = self.client.get(
            reverse(
                'financiacion_educativa_api:solicitud-detalle',
                kwargs={'application_id': solicitud.pk},
            ),
            HTTP_AUTHORIZATION=f'ApiKey {self.credencial.token}',
        )
        self.assertEqual(detalle_previo.status_code, status.HTTP_200_OK)
        self.assertIsNone(detalle_previo.data['financial_terms'])
        self.assertFalse(detalle_previo.data['course_authorized'])

        payload_webhook = {
            'event_type': 'doc_signed',
            'token': proceso.token_documento_externo,
            'external_id': proceso.external_id,
            'status': 'signed',
        }
        webhook_url = reverse('financiacion_educativa_api:zapsign-webhook')
        webhook = self.client.post(
            webhook_url,
            data=json.dumps(payload_webhook),
            content_type='application/json',
            HTTP_X_EDUCATIONAL_SIGNATURE_SECRET='e2e-webhook-secret',
        )
        repetido = self.client.post(
            webhook_url,
            data=json.dumps(payload_webhook),
            content_type='application/json',
            HTTP_X_EDUCATIONAL_SIGNATURE_SECRET='e2e-webhook-secret',
        )
        self.assertEqual(webhook.status_code, status.HTTP_200_OK)
        self.assertEqual(repetido.status_code, status.HTTP_200_OK)

        solicitud.refresh_from_db()
        proceso.refresh_from_db()
        pagare = ArtefactoContractualEducativo.objects.get(
            solicitud=solicitud,
            tipo=TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
            vigente=True,
        )
        self.assertEqual(solicitud.estado, EstadoSolicitudFinanciacion.APPROVED)
        self.assertEqual(proceso.estado, EstadoProcesoFirmaEducativa.SIGNED)
        self.assertEqual(
            pagare.estado,
            EstadoArtefactoContractualEducativo.SIGNED,
        )
        self.assertTrue(pagare.archivo_firmado)
        self.assertEqual(EventoWebhookFirmaEducativa.objects.count(), 1)
        estudiante.refresh_from_db()
        self.assertFalse(estudiante.identidad_verificada)
        self.assertFalse(estudiante.relacion_verificada)

        detalle_final = self.client.get(
            reverse(
                'financiacion_educativa_api:solicitud-detalle',
                kwargs={'application_id': solicitud.pk},
            ),
            HTTP_AUTHORIZATION=f'ApiKey {self.credencial.token}',
        )
        self.assertEqual(detalle_final.status_code, status.HTTP_200_OK)
        self.assertEqual(detalle_final.data['status'], 'APPROVED')
        self.assertTrue(detalle_final.data['course_authorized'])
        self.assertIsNotNone(detalle_final.data['financial_terms'])
