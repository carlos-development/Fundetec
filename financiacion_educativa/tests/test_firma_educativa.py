import hashlib
import json
from datetime import date
from io import StringIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoArtefactoContractualEducativo,
    EstadoEventoWebhookFirmaEducativa,
    EstadoProcesoFirmaEducativa,
    EstadoSolicitudFinanciacion,
    MotivoDecisionRevisionEducativa,
    RelacionEstudiante,
    RolParticipante,
    TipoDocumentoIdentidad,
    TipoDecisionRevisionEducativa,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    DecisionRevisionEducativa,
    EventoWebhookFirmaEducativa,
    ProcesoAutomatizacionEducativa,
    ProcesoFirmaEducativa,
)
from financiacion_educativa.services.cola_automatizacion import (
    encolar_proceso_automatizacion,
)
from financiacion_educativa.services.artefactos_contractuales import (
    generar_artefactos_contractuales,
)
from financiacion_educativa.services.estado_publico import (
    obtener_resultado_publico,
)
from financiacion_educativa.services.firma_zapsign import (
    enviar_pagare_educativo,
)
from financiacion_educativa.services.participantes import (
    DatosParticipante,
    registrar_estudiante_menor_con_tutor,
    registrar_o_actualizar_participante,
)
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.tests.factories import (
    crear_configuracion_financiera,
    crear_solicitud,
)
from financiacion_educativa.tests.signature_backends import (
    RecordingEducationalSignatureBackend,
)


BACKEND_PRUEBA = (
    'financiacion_educativa.tests.signature_backends.'
    'RecordingEducationalSignatureBackend'
)


@override_settings(
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
    FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND=BACKEND_PRUEBA,
    FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS=True,
    FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET='webhook-test-secret',
    FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_HEADER=(
        'X-Educational-Signature-Secret'
    ),
    FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY='recipient-test-key',
)
class FirmaEducativaTests(TestCase):
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
        self.usuario = User.objects.create_user(
            username='firma-educativa@example.com',
            email='firma-educativa@example.com',
            password='Clave-2026',
        )
        self.solicitud = crear_solicitud(usuario=self.usuario)
        self.solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
        self.solicitud.plazo_meses = 3
        self.solicitud.save(update_fields=['estado', 'plazo_meses'])
        registrar_o_actualizar_participante(
            solicitud=self.solicitud,
            actor=self.usuario,
            datos=DatosParticipante(
                nombres='FIRMANTE',
                apellidos='EDUCATIVO',
                tipo_documento=TipoDocumentoIdentidad.CC,
                numero_documento='100200300',
                fecha_nacimiento=date(1990, 1, 1),
                correo='firmante@example.com',
                telefono='3000000000',
                relacion_estudiante=RelacionEstudiante.SELF,
            ),
            roles={
                RolParticipante.STUDENT,
                RolParticipante.PRINCIPAL_DEBTOR,
            },
        )
        crear_configuracion_financiera()
        fotografia = crear_fotografia_condiciones_financieras(
            self.solicitud,
            fecha_inicio_plan=date(2026, 8, 4),
            actor=self.usuario,
            bloquear=True,
        )
        decision = DecisionRevisionEducativa(
            solicitud=self.solicitud,
            tipo=TipoDecisionRevisionEducativa.APPROVED,
            motivo=MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED,
            mensaje_solicitante='Expediente aprobado para continuar a firma.',
            fotografia_financiera=fotografia,
            responsable=self.usuario,
        )
        decision.full_clean()
        decision.save()
        self.solicitud.estado = (
            EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE
        )
        self.solicitud.save(update_fields=['estado'])
        self.artefactos = generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        )
        self.proceso = ProcesoFirmaEducativa.objects.get(
            artefacto=self.artefactos.pagare
        )
        self.webhook_url = reverse(
            'financiacion_educativa_api:zapsign-webhook'
        )

    def _enviar(self):
        return enviar_pagare_educativo(proceso=self.proceso)

    def _payload(self, **cambios):
        payload = {
            'event_type': 'doc_signed',
            'token': self.proceso.token_documento_externo,
            'external_id': self.proceso.external_id,
            'status': 'signed',
            'signers': [{'status': 'signed', 'signed_at': '2026-08-04'}],
        }
        payload.update(cambios)
        return payload

    def _post_webhook(self, payload, *, secreto='webhook-test-secret'):
        return self.client.post(
            self.webhook_url,
            data=json.dumps(payload, separators=(',', ':')),
            content_type='application/json',
            HTTP_X_EDUCATIONAL_SIGNATURE_SECRET=secreto,
        )

    def test_envio_usa_pdf_privado_y_es_idempotente(self):
        primero = self._enviar()
        segundo = enviar_pagare_educativo(proceso=primero)

        self.solicitud.refresh_from_db()
        self.artefactos.pagare.refresh_from_db()
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 1)
        envio = RecordingEducationalSignatureBackend.submissions[0]
        self.assertTrue(envio['pdf'].startswith(b'%PDF'))
        self.assertEqual(envio['external_id'], self.proceso.external_id)
        self.assertEqual(envio['firmante'].nombre_completo, 'FIRMANTE EDUCATIVO')
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(primero.estado, EstadoProcesoFirmaEducativa.SENT)
        self.assertEqual(
            self.artefactos.pagare.estado,
            EstadoArtefactoContractualEducativo.SENT_FOR_SIGNATURE,
        )
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_SIGNATURE,
        )
        self.assertFalse(hasattr(primero, 'sign_url'))
        self.assertNotIn('firmante@example.com', str(primero.__dict__))

    @override_settings(FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True)
    def test_webhook_firmado_autoriza_curso_y_es_idempotente(self):
        self._enviar()
        self.proceso.refresh_from_db()
        proceso_automatico, creado = encolar_proceso_automatizacion(
            solicitud_id=self.solicitud.pk
        )
        payload = self._payload()

        primera = self._post_webhook(payload)
        segunda = self._post_webhook(payload)

        self.solicitud.refresh_from_db()
        self.proceso.refresh_from_db()
        self.artefactos.pagare.refresh_from_db()
        resultado = obtener_resultado_publico(self.solicitud)
        self.assertEqual(primera.status_code, 200)
        self.assertTrue(creado)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda.json()['status'], 'replayed')
        self.assertEqual(EventoWebhookFirmaEducativa.objects.count(), 1)
        self.assertEqual(self.proceso.estado, EstadoProcesoFirmaEducativa.SIGNED)
        self.assertEqual(
            self.artefactos.pagare.estado,
            EstadoArtefactoContractualEducativo.SIGNED,
        )
        self.assertTrue(self.artefactos.pagare.archivo_firmado)
        with self.artefactos.pagare.archivo_firmado.open('rb') as archivo:
            firmado = archivo.read()
        self.assertEqual(
            self.artefactos.pagare.hash_firmado_sha256,
            hashlib.sha256(firmado).hexdigest(),
        )
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.APPROVED,
        )
        self.assertEqual(self.solicitud.fecha_matricula, timezone.localdate())
        self.assertTrue(resultado.curso_autorizado)
        self.assertEqual(resultado.estado, 'APPROVED')
        self.assertEqual(resultado.condiciones_financieras['currency'], 'COP')
        proceso_automatico.refresh_from_db()
        self.assertEqual(proceso_automatico.estado, 'COMPLETED')
        self.assertEqual(ProcesoAutomatizacionEducativa.objects.count(), 1)

    def test_webhook_equivalente_con_serializacion_distinta_es_replay(self):
        self._enviar()
        self.proceso.refresh_from_db()
        payload = self._payload()

        primera = self._post_webhook(payload)
        segunda = self.client.post(
            self.webhook_url,
            data=json.dumps(payload, sort_keys=True, indent=2),
            content_type='application/json',
            HTTP_X_EDUCATIONAL_SIGNATURE_SECRET='webhook-test-secret',
        )

        self.assertEqual(primera.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda.json()['status'], 'replayed')
        self.assertEqual(EventoWebhookFirmaEducativa.objects.count(), 1)

    def test_webhook_rechaza_secreto_invalido_sin_registrar_payload(self):
        self._enviar()
        self.proceso.refresh_from_db()

        respuesta = self._post_webhook(
            self._payload(),
            secreto='incorrecto',
        )

        self.assertEqual(respuesta.status_code, 401)
        self.assertFalse(EventoWebhookFirmaEducativa.objects.exists())

    def test_estado_pending_no_confirma_firma(self):
        self._enviar()
        self.proceso.refresh_from_db()

        respuesta = self._post_webhook(self._payload(status='pending'))

        self.proceso.refresh_from_db()
        self.solicitud.refresh_from_db()
        evento = EventoWebhookFirmaEducativa.objects.get()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(evento.estado, EstadoEventoWebhookFirmaEducativa.IGNORED)
        self.assertEqual(self.proceso.estado, EstadoProcesoFirmaEducativa.SENT)
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_SIGNATURE,
        )

    def test_webhook_no_firma_pagare_cancelado_o_reemplazado(self):
        self._enviar()
        self.proceso.refresh_from_db()
        self.artefactos.pagare.refresh_from_db()
        self.artefactos.pagare.estado = EstadoArtefactoContractualEducativo.CANCELLED
        self.artefactos.pagare.vigente = False
        self.artefactos.pagare.save(
            update_fields=['estado', 'vigente', 'actualizado_en']
        )

        respuesta = self._post_webhook(self._payload())

        self.solicitud.refresh_from_db()
        self.proceso.refresh_from_db()
        resultado = obtener_resultado_publico(self.solicitud)
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(self.proceso.estado, EstadoProcesoFirmaEducativa.SENT)
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_SIGNATURE,
        )
        self.assertFalse(resultado.curso_autorizado)
        self.assertIsNone(resultado.condiciones_financieras)

    def test_webhook_external_id_ajeno_no_autoriza(self):
        self._enviar()
        self.proceso.refresh_from_db()

        respuesta = self._post_webhook(
            self._payload(external_id='edu-otra-solicitud')
        )

        self.solicitud.refresh_from_db()
        self.proceso.refresh_from_db()
        resultado = obtener_resultado_publico(self.solicitud)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.proceso.estado, EstadoProcesoFirmaEducativa.SENT)
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_SIGNATURE,
        )
        self.assertFalse(resultado.curso_autorizado)
        self.assertIsNone(resultado.condiciones_financieras)

    def test_fallo_descarga_es_reintentable_con_el_mismo_evento(self):
        self._enviar()
        self.proceso.refresh_from_db()
        payload = self._payload()
        RecordingEducationalSignatureBackend.fail_download = True

        fallida = self._post_webhook(payload)
        evento = EventoWebhookFirmaEducativa.objects.get()
        RecordingEducationalSignatureBackend.fail_download = False
        recuperada = self._post_webhook(payload)

        evento.refresh_from_db()
        self.proceso.refresh_from_db()
        self.assertEqual(fallida.status_code, 503)
        self.assertEqual(recuperada.status_code, 200)
        self.assertEqual(EventoWebhookFirmaEducativa.objects.count(), 1)
        self.assertEqual(
            evento.estado,
            EstadoEventoWebhookFirmaEducativa.PROCESSED,
        )
        self.assertEqual(self.proceso.estado, EstadoProcesoFirmaEducativa.SIGNED)

    def test_rechazo_revoca_pagare_y_permite_nueva_version(self):
        self._enviar()
        self.proceso.refresh_from_db()
        respuesta = self._post_webhook(
            self._payload(event_type='doc_refused', status='refused')
        )

        self.solicitud.refresh_from_db()
        self.proceso.refresh_from_db()
        self.artefactos.pagare.refresh_from_db()
        nuevos = generar_artefactos_contractuales(
            solicitud=self.solicitud,
            actor=self.usuario,
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.proceso.estado, EstadoProcesoFirmaEducativa.REFUSED)
        self.assertFalse(self.artefactos.pagare.vigente)
        self.assertEqual(
            self.solicitud.estado,
            EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
        )
        self.assertNotEqual(nuevos.pagare.pk, self.artefactos.pagare.pk)
        self.assertEqual(nuevos.pagare.numero_version, 2)
        self.assertEqual(
            ArtefactoContractualEducativo.objects.filter(
                solicitud=self.solicitud,
                vigente=True,
            ).count(),
            2,
        )

    def test_comando_envia_solo_solicitud_indicada(self):
        salida = StringIO()
        call_command(
            'enviar_pagares_educativos',
            solicitud_id=str(self.solicitud.pk),
            stdout=salida,
        )
        self.proceso.refresh_from_db()
        self.assertEqual(self.proceso.estado, EstadoProcesoFirmaEducativa.SENT)
        self.assertIn('Enviados: 1', salida.getvalue())

    def test_rechazo_permanente_exige_confirmacion_operativa_para_reintentar(self):
        self.proceso.estado = EstadoProcesoFirmaEducativa.FAILED
        self.proceso.codigo_ultimo_error = 'SIGNATURE_HTTP_400'
        self.proceso.save(update_fields=['estado', 'codigo_ultimo_error'])

        with self.assertRaisesMessage(
            ValidationError,
            'El rechazo permanente requiere correccion y confirmacion operativa.',
        ):
            enviar_pagare_educativo(proceso=self.proceso)
        self.assertEqual(RecordingEducationalSignatureBackend.submissions, [])

        enviar_pagare_educativo(
            proceso=self.proceso,
            permitir_reintento_permanente=True,
        )
        self.proceso.refresh_from_db()
        self.assertEqual(self.proceso.estado, EstadoProcesoFirmaEducativa.SENT)
        self.assertEqual(len(RecordingEducationalSignatureBackend.submissions), 1)


@override_settings(
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
    FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND=BACKEND_PRUEBA,
    FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS=True,
    FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY='recipient-test-key',
)
class FirmaEducativaMenorTests(TestCase):
    def test_tutor_es_el_unico_firmante_del_pagare_del_menor(self):
        RecordingEducationalSignatureBackend.reset()
        private_root = TemporaryDirectory()
        self.addCleanup(private_root.cleanup)
        with override_settings(
            FINANCIACION_EDUCATIVA_PRIVATE_ROOT=private_root.name,
        ):
            usuario = get_user_model().objects.create_user(
                username='menor-firma@example.com',
                email='menor-firma@example.com',
                password='Clave-2026',
            )
            solicitud = crear_solicitud(usuario=usuario)
            solicitud.estado = EstadoSolicitudFinanciacion.PENDING_DOCUMENT
            solicitud.plazo_meses = 3
            solicitud.save(update_fields=['estado', 'plazo_meses'])
            estudiante, tutor = registrar_estudiante_menor_con_tutor(
                solicitud=solicitud,
                estudiante=DatosParticipante(
                    nombres='ESTUDIANTE',
                    apellidos='MENOR',
                    tipo_documento=TipoDocumentoIdentidad.TI,
                    numero_documento='100001111',
                    fecha_nacimiento=date(2012, 1, 1),
                    fecha_nacimiento_confirmada=True,
                    correo='menor@example.com',
                    telefono='3000000000',
                    relacion_estudiante=RelacionEstudiante.SELF,
                ),
                tutor=DatosParticipante(
                    nombres='TUTOR',
                    apellidos='LEGAL',
                    tipo_documento=TipoDocumentoIdentidad.CC,
                    numero_documento='900002222',
                    fecha_nacimiento=date(1980, 1, 1),
                    fecha_nacimiento_confirmada=True,
                    correo='tutor@example.com',
                    telefono='3010000000',
                    relacion_estudiante=(
                        RelacionEstudiante.LEGAL_GUARDIAN
                    ),
                ),
            )
            crear_configuracion_financiera()
            crear_fotografia_condiciones_financieras(
                solicitud,
                fecha_inicio_plan=date(2026, 8, 4),
                actor=usuario,
                bloquear=True,
            )
            solicitud.estado = (
                EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE
            )
            solicitud.save(update_fields=['estado'])
            artefactos = generar_artefactos_contractuales(
                solicitud=solicitud,
                actor=usuario,
            )
            proceso = ProcesoFirmaEducativa.objects.get(
                artefacto=artefactos.pagare
            )
            enviar_pagare_educativo(proceso=proceso)

        envio = RecordingEducationalSignatureBackend.submissions[0]
        self.assertEqual(envio['firmante'].pk, tutor.pk)
        self.assertNotEqual(envio['firmante'].pk, estudiante.pk)
