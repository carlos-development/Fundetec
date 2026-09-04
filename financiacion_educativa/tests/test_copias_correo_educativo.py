import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from urllib.parse import urlsplit
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from aprobado_web import settings as project_settings
from financiacion_educativa.admin import (
    DestinatarioNotificacionInstitucionalEducativaAdmin,
)
from financiacion_educativa.choices import (
    CodigoMensajeCorreoEducativo,
    EstadoEntregaInvitacion,
    EstadoInvitacionContinuacion,
    EstadoOutboxCorreoEducativo,
    OrigenEntregaInvitacion,
    TipoEventoCorreoEducativo,
)
from financiacion_educativa.models import (
    DestinatarioNotificacionInstitucionalEducativa,
    EntregaInvitacionContinuacion,
    InvitacionContinuacionSolicitud,
    OutboxCorreoEducativo,
    RegistroIdempotenciaSolicitud,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.outbox_correos import (
    EVENTOS_AUDITABLES_ESTUDIANTE,
    _finalizar,
    crear_correo_expediente_recibido,
    crear_intencion_correo,
    procesar_siguiente_correo,
)
from financiacion_educativa.services.orquestacion import (
    crear_solicitud_institucional_orquestada,
    programar_invitacion_inicial,
    reemitir_invitacion_orquestada,
)
from financiacion_educativa.services.limpieza_solicitudes import (
    ejecutar_limpieza_solicitudes,
)
from financiacion_educativa.services.invitaciones import (
    obtener_invitacion_vigente_por_token,
)
from financiacion_educativa.services.solicitudes import (
    DatosSolicitudFinanciacion,
)
from financiacion_educativa.tests.factories import (
    crear_institucion,
    crear_solicitud,
)


@override_settings(
    DEBUG=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FINANCIACION_EDUCATIVA_REVIEW_NOTIFICATION_EMAILS=[],
    EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=['audit@example.test'],
    FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_LEASE_SECONDS=60,
    FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_MAX_ATTEMPTS=3,
    FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_BASE_SECONDS=1,
    FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_MAX_SECONDS=2,
)
class CopiasCorreoEducativoTests(TestCase):
    def setUp(self):
        self.institucion = crear_institucion('801')
        self.solicitud = crear_solicitud(
            institucion=self.institucion,
            referencia='COPY-001',
            correo='student@example.test',
        )

    def _crear_original(self, tipo_evento, codigo_mensaje, *, clave=None):
        with transaction.atomic():
            original, _ = crear_intencion_correo(
                solicitud=self.solicitud,
                tipo_evento=tipo_evento,
                clave_idempotencia=clave or f'original:{tipo_evento}',
                codigo_mensaje=codigo_mensaje,
                destinatarios=[self.solicitud.correo],
                contexto={},
            )
        lease_id = uuid.uuid4()
        OutboxCorreoEducativo.objects.filter(pk=original.pk).update(
            estado=EstadoOutboxCorreoEducativo.SENDING,
            lease_id=lease_id,
            lease_vence_en=timezone.now(),
        )
        return _finalizar(
            outbox_id=original.pk,
            lease_id=lease_id,
            estado=EstadoOutboxCorreoEducativo.SENT,
        )

    def _confirmar_invitacion_inicial(self):
        resultado = programar_invitacion_inicial(solicitud=self.solicitud)
        original = resultado.entrega.correo_outbox
        lease_id = uuid.uuid4()
        OutboxCorreoEducativo.objects.filter(pk=original.pk).update(
            estado=EstadoOutboxCorreoEducativo.SENDING,
            lease_id=lease_id,
            lease_vence_en=timezone.now(),
        )
        return _finalizar(
            outbox_id=original.pk,
            lease_id=lease_id,
            estado=EstadoOutboxCorreoEducativo.SENT,
        )

    def test_allowlist_cerrada_incluye_solo_eventos_estudiantiles_existentes(self):
        self.assertSetEqual(
            set(EVENTOS_AUDITABLES_ESTUDIANTE),
            {
                TipoEventoCorreoEducativo.INITIAL_INVITATION,
                TipoEventoCorreoEducativo.INVITATION_REISSUE,
                TipoEventoCorreoEducativo.CONTINUATION_REMINDER_1H,
                TipoEventoCorreoEducativo.CONTINUATION_REMINDER_6H,
                TipoEventoCorreoEducativo.CONTINUATION_REMINDER_24H,
                TipoEventoCorreoEducativo.CONTINUATION_REMINDER_48H,
                TipoEventoCorreoEducativo.MOBILE_CAPTURE_LINK,
                TipoEventoCorreoEducativo.DOSSIER_RECEIVED,
                TipoEventoCorreoEducativo.REVIEW_DECISION,
                TipoEventoCorreoEducativo.AUTOMATIC_CORRECTION,
                TipoEventoCorreoEducativo.AUTOMATIC_CONTINUATION,
            },
        )
        self.assertNotIn(
            TipoEventoCorreoEducativo.NEW_APPLICATION_INTERNAL,
            EVENTOS_AUDITABLES_ESTUDIANTE,
        )

    def test_cada_evento_permitido_programa_exactamente_una_copia_auditoria(self):
        codigos = {
            TipoEventoCorreoEducativo.INITIAL_INVITATION: CodigoMensajeCorreoEducativo.INVITATION,
            TipoEventoCorreoEducativo.INVITATION_REISSUE: CodigoMensajeCorreoEducativo.INVITATION,
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_1H: CodigoMensajeCorreoEducativo.INVITATION,
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_6H: CodigoMensajeCorreoEducativo.INVITATION,
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_24H: CodigoMensajeCorreoEducativo.INVITATION,
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_48H: CodigoMensajeCorreoEducativo.INVITATION,
            TipoEventoCorreoEducativo.MOBILE_CAPTURE_LINK: CodigoMensajeCorreoEducativo.MOBILE_CAPTURE,
            TipoEventoCorreoEducativo.DOSSIER_RECEIVED: CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED,
            TipoEventoCorreoEducativo.REVIEW_DECISION: CodigoMensajeCorreoEducativo.REVIEW_DECISION,
            TipoEventoCorreoEducativo.AUTOMATIC_CORRECTION: CodigoMensajeCorreoEducativo.AUTOMATIC_CORRECTION,
            TipoEventoCorreoEducativo.AUTOMATIC_CONTINUATION: CodigoMensajeCorreoEducativo.AUTOMATIC_CONTINUATION,
        }
        for indice, (evento, codigo) in enumerate(codigos.items()):
            self._crear_original(evento, codigo, clave=f'allowlist:{indice}')

        copias = OutboxCorreoEducativo.objects.filter(
            codigo_mensaje=CodigoMensajeCorreoEducativo.AUDIT_COPY,
        )
        self.assertEqual(copias.count(), len(EVENTOS_AUDITABLES_ESTUDIANTE))
        self.assertEqual(
            copias.values('correo_origen_id').distinct().count(),
            len(EVENTOS_AUDITABLES_ESTUDIANTE),
        )

    def test_mensaje_interno_no_genera_copias(self):
        self._crear_original(
            TipoEventoCorreoEducativo.NEW_APPLICATION_INTERNAL,
            CodigoMensajeCorreoEducativo.NEW_APPLICATION_INTERNAL,
        )
        self.assertFalse(
            OutboxCorreoEducativo.objects.filter(
                correo_origen__isnull=False,
            ).exists()
        )

    @override_settings(EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=[])
    def test_configuracion_auditoria_vacia_omite_sin_fallar(self):
        with self.assertLogs(
            'financiacion_educativa.services.outbox_correos',
            level='INFO',
        ) as logs:
            original = self._crear_original(
                TipoEventoCorreoEducativo.DOSSIER_RECEIVED,
                CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED,
            )
        self.assertEqual(original.estado, EstadoOutboxCorreoEducativo.SENT)
        self.assertFalse(original.copias_secundarias.exists())
        self.assertIn('codigo=NO_AUDIT_RECIPIENTS', ' '.join(logs.output))
        self.assertNotIn(self.solicitud.correo, ' '.join(logs.output))

    def test_invitacion_inicial_genera_auditoria_e_institucional(self):
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.institucion,
            correo='fundetec@example.test',
        )
        original = self._confirmar_invitacion_inicial()
        copias = original.copias_secundarias.order_by('codigo_mensaje')
        self.assertEqual(copias.count(), 2)
        self.assertSetEqual(
            set(copias.values_list('codigo_mensaje', flat=True)),
            {
                CodigoMensajeCorreoEducativo.AUDIT_COPY,
                CodigoMensajeCorreoEducativo.INSTITUTIONAL_INITIAL_NOTIFICATION,
            },
        )

    @override_settings(
        BRAND_PUBLIC_BASE_URL='https://education.example.test',
        FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=(
            'financiacion_educativa.services.entrega_invitaciones.'
            'DjangoEmailInvitationDeliveryBackend'
        ),
    )
    def test_invitacion_y_copias_se_entregan_como_tres_mensajes_independientes(self):
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.institucion,
            correo='fundetec@example.test',
        )
        programar_invitacion_inicial(solicitud=self.solicitud)

        original = procesar_siguiente_correo()
        auditoria = procesar_siguiente_correo()
        institucional = procesar_siguiente_correo()

        self.assertEqual(original.estado, EstadoOutboxCorreoEducativo.SENT)
        self.assertEqual(auditoria.estado, EstadoOutboxCorreoEducativo.SENT)
        self.assertEqual(institucional.estado, EstadoOutboxCorreoEducativo.SENT)
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(mail.outbox[0].to, [self.solicitud.correo])
        self.assertEqual(mail.outbox[0].cc, [])
        self.assertEqual(mail.outbox[1].to, ['audit@example.test'])
        self.assertEqual(mail.outbox[2].to, ['fundetec@example.test'])
        self.assertTrue(mail.outbox[1].subject.startswith('[COPIA AUDITORÍA]'))
        self.assertTrue(
            mail.outbox[2].subject.startswith('[COPIA INSTITUCIONAL]')
        )
        ids = {
            mensaje.extra_headers['Message-ID'] for mensaje in mail.outbox
        }
        self.assertEqual(len(ids), 3)
        for copia in mail.outbox[1:]:
            contenido = copia.body + ''.join(
                alternativa[0] for alternativa in copia.alternatives
            )
            self.assertNotIn('https://education.example.test/', contenido)
            self.assertNotIn(self.solicitud.correo, contenido)
            self.assertIn('Enlace personal omitido', contenido)

    def test_reemision_no_genera_notificacion_institucional(self):
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.institucion,
            correo='fundetec@example.test',
        )
        original = self._crear_original(
            TipoEventoCorreoEducativo.INVITATION_REISSUE,
            CodigoMensajeCorreoEducativo.INVITATION,
        )
        self.assertEqual(original.copias_secundarias.count(), 1)
        self.assertEqual(
            original.copias_secundarias.get().codigo_mensaje,
            CodigoMensajeCorreoEducativo.AUDIT_COPY,
        )

    def test_eventos_no_iniciales_nunca_generan_notificacion_institucional(self):
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.institucion,
            correo='fundetec@example.test',
        )
        eventos = {
            TipoEventoCorreoEducativo.INVITATION_REISSUE: (
                CodigoMensajeCorreoEducativo.INVITATION
            ),
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_1H: (
                CodigoMensajeCorreoEducativo.INVITATION
            ),
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_6H: (
                CodigoMensajeCorreoEducativo.INVITATION
            ),
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_24H: (
                CodigoMensajeCorreoEducativo.INVITATION
            ),
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_48H: (
                CodigoMensajeCorreoEducativo.INVITATION
            ),
            TipoEventoCorreoEducativo.MOBILE_CAPTURE_LINK: (
                CodigoMensajeCorreoEducativo.MOBILE_CAPTURE
            ),
            TipoEventoCorreoEducativo.DOSSIER_RECEIVED: (
                CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED
            ),
            TipoEventoCorreoEducativo.REVIEW_DECISION: (
                CodigoMensajeCorreoEducativo.REVIEW_DECISION
            ),
            TipoEventoCorreoEducativo.AUTOMATIC_CORRECTION: (
                CodigoMensajeCorreoEducativo.AUTOMATIC_CORRECTION
            ),
            TipoEventoCorreoEducativo.AUTOMATIC_CONTINUATION: (
                CodigoMensajeCorreoEducativo.AUTOMATIC_CONTINUATION
            ),
        }
        for indice, (evento, codigo) in enumerate(eventos.items()):
            self._crear_original(
                evento,
                codigo,
                clave=f'non-institutional:{indice}',
            )

        self.assertFalse(
            OutboxCorreoEducativo.objects.filter(
                codigo_mensaje=(
                    CodigoMensajeCorreoEducativo.INSTITUTIONAL_INITIAL_NOTIFICATION
                ),
            ).exists()
        )

    @override_settings(EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=[])
    def test_destinatarios_inactivos_y_otra_institucion_quedan_aislados(self):
        otra = crear_institucion('802')
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.institucion,
            correo='inactive@example.test',
            activo=False,
        )
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=otra,
            correo='other@example.test',
        )
        with self.assertLogs(
            'financiacion_educativa.services.outbox_correos',
            level='INFO',
        ) as logs:
            original = self._confirmar_invitacion_inicial()
        self.assertFalse(original.copias_secundarias.exists())
        self.assertIn('codigo=NO_INSTITUTION_RECIPIENTS', ' '.join(logs.output))
        self.assertNotIn('inactive@example.test', ' '.join(logs.output))
        self.assertNotIn('other@example.test', ' '.join(logs.output))

    @override_settings(EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=[])
    def test_preicfes_e_ingles_de_fundetec_usan_configuracion_institucional_unica(self):
        self.institucion.nombre_comercial = 'FUNDETEC'
        self.institucion.save(update_fields=['nombre_comercial', 'actualizada_en'])
        self.solicitud.nombre_curso = 'PREICFES'
        self.solicitud.tipo_curso = 'PREICFES'
        self.solicitud.save(update_fields=['nombre_curso', 'tipo_curso', 'actualizada_en'])
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.institucion,
            correo='fundetec@example.test',
        )
        segunda = crear_solicitud(
            institucion=self.institucion,
            referencia='COPY-INGLES',
            correo='other-student@example.test',
        )
        segunda.nombre_curso = 'INGLES'
        segunda.tipo_curso = 'INGLES'
        segunda.save(update_fields=['nombre_curso', 'tipo_curso', 'actualizada_en'])
        self.assertEqual(self.solicitud.institucion_id, self.institucion.pk)
        self.assertEqual(segunda.institucion_id, self.institucion.pk)
        self._confirmar_invitacion_inicial()
        self.solicitud = segunda
        self._confirmar_invitacion_inicial()
        institucionales = OutboxCorreoEducativo.objects.filter(
            codigo_mensaje=(
                CodigoMensajeCorreoEducativo.INSTITUTIONAL_INITIAL_NOTIFICATION
            )
        )
        self.assertEqual(institucionales.count(), 2)
        self.assertTrue(all(
            fila.destinatarios == ['fundetec@example.test']
            for fila in institucionales
        ))
        self.assertSetEqual(
            set(institucionales.values_list('solicitud__institucion_id', flat=True)),
            {self.institucion.pk},
        )

    @override_settings(
        EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=['audit@example.test'],
        EDUCATIONAL_OPERATIONS_NOTIFICATION_EMAILS=[],
    )
    def test_replay_idempotente_no_duplica_solicitud_invitacion_ni_copias(self):
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.institucion,
            correo='fundetec@example.test',
        )
        datos = DatosSolicitudFinanciacion(
            referencia_externa='PAYMENT-REPLAY-001',
            nombres='ESTUDIANTE',
            apellidos='PRUEBA',
            celular='3000000000',
            correo='replay-student@example.test',
            direccion='Direccion sintetica',
            valor_plan=Decimal('450000.00'),
            plazo_meses=6,
            nombre_curso='PREICFES',
            tipo_curso='PREICFES',
        )
        primera = crear_solicitud_institucional_orquestada(
            institucion=self.institucion,
            clave_idempotencia='payment-replay-key',
            datos=datos,
        )
        for _ in range(3):
            procesar_siguiente_correo()

        conteos_antes = {
            'solicitudes': SolicitudFinanciacionEducativa.objects.filter(
                referencia_externa=datos.referencia_externa,
                institucion=self.institucion,
            ).count(),
            'registros_idempotencia': RegistroIdempotenciaSolicitud.objects.filter(
                solicitud=primera.solicitud,
            ).count(),
            'entregas': EntregaInvitacionContinuacion.objects.filter(
                solicitud=primera.solicitud,
            ).count(),
            'correos': OutboxCorreoEducativo.objects.filter(
                solicitud=primera.solicitud,
            ).count(),
            'institucionales': OutboxCorreoEducativo.objects.filter(
                solicitud=primera.solicitud,
                codigo_mensaje=(
                    CodigoMensajeCorreoEducativo.INSTITUTIONAL_INITIAL_NOTIFICATION
                ),
            ).count(),
            'auditorias': OutboxCorreoEducativo.objects.filter(
                solicitud=primera.solicitud,
                codigo_mensaje=CodigoMensajeCorreoEducativo.AUDIT_COPY,
            ).count(),
        }

        segunda = crear_solicitud_institucional_orquestada(
            institucion=self.institucion,
            clave_idempotencia='payment-replay-key',
            datos=datos,
        )

        self.assertFalse(primera.repetida)
        self.assertTrue(segunda.repetida)
        self.assertEqual(segunda.solicitud.pk, primera.solicitud.pk)
        self.assertEqual(
            conteos_antes,
            {
                'solicitudes': 1,
                'registros_idempotencia': 1,
                'entregas': 1,
                'correos': 3,
                'institucionales': 1,
                'auditorias': 1,
            },
        )
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                solicitud=primera.solicitud,
            ).count(),
            conteos_antes['correos'],
        )
        self.assertEqual(
            EntregaInvitacionContinuacion.objects.filter(
                solicitud=primera.solicitud,
            ).count(),
            conteos_antes['entregas'],
        )

    @override_settings(
        BRAND_PUBLIC_BASE_URL='https://education.example.test',
        EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=['audit@example.test'],
        FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS=0,
    )
    def test_reemision_real_reutiliza_solicitud_revoca_enlace_y_no_renotifica_institucion(self):
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.institucion,
            correo='fundetec@example.test',
        )
        inicial = programar_invitacion_inicial(solicitud=self.solicitud)
        invitacion_anterior = inicial.entrega.invitacion
        hash_anterior = invitacion_anterior.token_hash

        procesar_siguiente_correo()
        mensaje_inicial = mail.outbox[0]
        url_inicial = next(
            linea.strip()
            for linea in mensaje_inicial.body.splitlines()
            if linea.strip().startswith('https://')
        )
        token_inicial = urlsplit(url_inicial).path.rstrip('/').split('/')[-1]
        self.assertIsNotNone(
            obtener_invitacion_vigente_por_token(token_inicial)
        )
        procesar_siguiente_correo()
        procesar_siguiente_correo()

        with self.assertRaises(ValidationError):
            reemitir_invitacion_orquestada(
                solicitud=self.solicitud,
                origen=OrigenEntregaInvitacion.MANUAL_REISSUE,
            )
        actor = get_user_model().objects.create_user(
            username='reissue-admin@example.test',
            password='test-only-password',
            is_staff=True,
        )
        reemision = reemitir_invitacion_orquestada(
            solicitud=self.solicitud,
            origen=OrigenEntregaInvitacion.MANUAL_REISSUE,
            actor=actor,
        )
        self.assertEqual(reemision.solicitud_id, self.solicitud.pk)
        self.assertNotEqual(reemision.invitacion.token_hash, hash_anterior)
        invitacion_anterior.refresh_from_db()
        inicial.entrega.refresh_from_db()
        self.assertEqual(
            invitacion_anterior.estado,
            EstadoInvitacionContinuacion.REVOKED,
        )
        self.assertEqual(
            inicial.entrega.estado,
            EstadoEntregaInvitacion.SUPERSEDED,
        )
        self.assertIsNone(obtener_invitacion_vigente_por_token(token_inicial))

        procesar_siguiente_correo()
        mensajes_estudiante = [
            mensaje
            for mensaje in mail.outbox
            if mensaje.to == [self.solicitud.correo]
        ]
        self.assertEqual(len(mensajes_estudiante), 2)
        url_reemision = next(
            linea.strip()
            for linea in mensajes_estudiante[-1].body.splitlines()
            if linea.strip().startswith('https://')
        )
        token_reemision = (
            urlsplit(url_reemision).path.rstrip('/').split('/')[-1]
        )
        self.assertNotEqual(url_reemision, url_inicial)
        self.assertIsNotNone(
            obtener_invitacion_vigente_por_token(token_reemision)
        )
        procesar_siguiente_correo()

        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                solicitud=self.solicitud,
                tipo_evento=TipoEventoCorreoEducativo.INVITATION_REISSUE,
                correo_origen__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                solicitud=self.solicitud,
                codigo_mensaje=CodigoMensajeCorreoEducativo.AUDIT_COPY,
                correo_origen__tipo_evento=(
                    TipoEventoCorreoEducativo.INVITATION_REISSUE
                ),
            ).count(),
            1,
        )
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                solicitud=self.solicitud,
                codigo_mensaje=(
                    CodigoMensajeCorreoEducativo.INSTITUTIONAL_INITIAL_NOTIFICATION
                ),
            ).count(),
            1,
        )
        self.assertEqual(
            InvitacionContinuacionSolicitud.objects.filter(
                solicitud=self.solicitud,
                estado=EstadoInvitacionContinuacion.ACTIVE,
            ).count(),
            1,
        )
        self.assertEqual(
            EntregaInvitacionContinuacion.objects.filter(
                solicitud=self.solicitud,
            ).count(),
            2,
        )

    def test_copia_no_se_crea_antes_de_sent_y_no_admite_contexto(self):
        with transaction.atomic():
            original, _ = crear_correo_expediente_recibido(
                solicitud=self.solicitud,
            )
            with self.assertRaises(ValidationError):
                crear_intencion_correo(
                    solicitud=self.solicitud,
                    tipo_evento=TipoEventoCorreoEducativo.AUDIT_COPY,
                    clave_idempotencia='early-copy',
                    codigo_mensaje=CodigoMensajeCorreoEducativo.AUDIT_COPY,
                    destinatarios=['audit@example.test'],
                    correo_origen=original,
                )
        self.assertFalse(original.copias_secundarias.exists())

    def test_copia_no_persiste_contexto_cc_ni_enlace_personal(self):
        secreto = 'token-super-secreto-no-persistir'
        with transaction.atomic():
            original, _ = crear_intencion_correo(
                solicitud=self.solicitud,
                tipo_evento=TipoEventoCorreoEducativo.DOSSIER_RECEIVED,
                clave_idempotencia='privacy-original',
                codigo_mensaje=CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED,
                destinatarios=[self.solicitud.correo],
                contexto={'token': secreto, 'url': f'https://example.test/{secreto}'},
            )
        lease = uuid.uuid4()
        OutboxCorreoEducativo.objects.filter(pk=original.pk).update(
            estado=EstadoOutboxCorreoEducativo.SENDING,
            lease_id=lease,
            lease_vence_en=timezone.now(),
        )
        _finalizar(
            outbox_id=original.pk,
            lease_id=lease,
            estado=EstadoOutboxCorreoEducativo.SENT,
        )
        copia = original.copias_secundarias.get(
            codigo_mensaje=CodigoMensajeCorreoEducativo.AUDIT_COPY,
        )
        self.assertEqual(copia.contexto, {})
        self.assertEqual(copia.destinatarios_copia, [])

        procesar_siguiente_correo()
        mensaje = mail.outbox[-1]
        contenido = mensaje.body + ''.join(
            alternativa[0] for alternativa in mensaje.alternatives
        )
        self.assertNotIn(secreto, contenido)
        self.assertNotIn('https://example.test/', contenido)
        self.assertNotIn(self.solicitud.correo, contenido)
        self.assertIn('s***@example.test', contenido)
        self.assertIn('Enlace personal omitido', contenido)
        self.assertTrue(mensaje.subject.startswith('[COPIA AUDITORÍA]'))

    def test_estudiante_no_ve_destinatarios_secundarios(self):
        with transaction.atomic():
            crear_correo_expediente_recibido(solicitud=self.solicitud)
        procesar_siguiente_correo()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.solicitud.correo])
        self.assertEqual(mail.outbox[0].cc, [])
        self.assertNotIn('audit@example.test', mail.outbox[0].recipients())

    def test_reintento_de_copia_no_reenvia_original(self):
        with transaction.atomic():
            original, _ = crear_correo_expediente_recibido(
                solicitud=self.solicitud,
            )
        procesar_siguiente_correo()
        self.assertEqual(len(mail.outbox), 1)
        with mock.patch(
            'financiacion_educativa.services.outbox_correos._entregar',
            side_effect=ConnectionRefusedError(),
        ):
            resultado = procesar_siguiente_correo()
        original.refresh_from_db()
        self.assertEqual(original.estado, EstadoOutboxCorreoEducativo.SENT)
        self.assertEqual(resultado.estado, EstadoOutboxCorreoEducativo.RETRYING)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=[])
    def test_fallo_institucional_es_independiente_del_original(self):
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.institucion,
            correo='fundetec@example.test',
        )
        original = self._confirmar_invitacion_inicial()
        with mock.patch(
            'financiacion_educativa.services.outbox_correos._entregar',
            side_effect=ConnectionRefusedError(),
        ):
            resultado = procesar_siguiente_correo()
        original.refresh_from_db()
        self.assertEqual(original.estado, EstadoOutboxCorreoEducativo.SENT)
        self.assertEqual(resultado.estado, EstadoOutboxCorreoEducativo.RETRYING)

    @override_settings(
        EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=['direccion-invalida'],
    )
    def test_destinatario_auditoria_invalido_no_revierte_sent(self):
        with self.assertLogs(
            'financiacion_educativa.services.outbox_correos',
            level='ERROR',
        ) as logs:
            original = self._crear_original(
                TipoEventoCorreoEducativo.DOSSIER_RECEIVED,
                CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED,
            )
        self.assertEqual(original.estado, EstadoOutboxCorreoEducativo.SENT)
        self.assertFalse(original.copias_secundarias.exists())
        self.assertIn('codigo=AUDIT_COPY_CREATE_FAILED', ' '.join(logs.output))

    def test_copias_tienen_message_id_independiente_y_no_generan_recursion(self):
        original = self._crear_original(
            TipoEventoCorreoEducativo.DOSSIER_RECEIVED,
            CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED,
        )
        copia = original.copias_secundarias.get()
        self.assertNotEqual(original.message_id, copia.message_id)
        total_antes = OutboxCorreoEducativo.objects.count()
        procesar_siguiente_correo()
        copia.refresh_from_db()
        self.assertEqual(copia.estado, EstadoOutboxCorreoEducativo.SENT)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), total_antes)

    def test_repetir_finalizacion_no_duplica_copias(self):
        original = self._crear_original(
            TipoEventoCorreoEducativo.DOSSIER_RECEIVED,
            CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED,
        )
        _finalizar(
            outbox_id=original.pk,
            lease_id=uuid.uuid4(),
            estado=EstadoOutboxCorreoEducativo.SENT,
        )
        self.assertEqual(original.copias_secundarias.count(), 1)

    def test_registro_historico_sent_no_se_reprocesa(self):
        OutboxCorreoEducativo.objects.create(
            solicitud=self.solicitud,
            tipo_evento=TipoEventoCorreoEducativo.DOSSIER_RECEIVED,
            clave_idempotencia='historic-sent',
            evento_logico='a' * 64,
            destinatarios=[self.solicitud.correo],
            codigo_mensaje=CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED,
            estado=EstadoOutboxCorreoEducativo.SENT,
            message_id='<historic@example.test>',
            enviada_en=timezone.now(),
        )
        self.assertFalse(
            OutboxCorreoEducativo.objects.filter(
                correo_origen__isnull=False,
            ).exists()
        )

    def test_limpieza_controlada_elimina_copias_antes_del_original(self):
        self._crear_original(
            TipoEventoCorreoEducativo.DOSSIER_RECEIVED,
            CodigoMensajeCorreoEducativo.DOSSIER_RECEIVED,
        )
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 2)

        ejecutar_limpieza_solicitudes(
            institucion_id=self.institucion.pk,
            expected_count=1,
        )

        self.assertFalse(OutboxCorreoEducativo.objects.exists())


class ConfiguracionDestinatariosInstitucionalesTests(TestCase):
    def setUp(self):
        self.institucion = crear_institucion('811')

    def test_normaliza_valida_y_evitar_duplicado_sin_distinguir_mayusculas(self):
        destinatario = DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.institucion,
            correo='  Notice@Example.Test  ',
        )
        self.assertEqual(destinatario.correo, 'notice@example.test')
        self.assertNotIn('notice@example.test', str(destinatario))
        with self.assertRaises(ValidationError):
            DestinatarioNotificacionInstitucionalEducativa.objects.create(
                institucion=self.institucion,
                correo='NOTICE@example.test',
            )

    def test_rechaza_correo_invalido(self):
        with self.assertRaises(ValidationError):
            DestinatarioNotificacionInstitucionalEducativa.objects.create(
                institucion=self.institucion,
                correo='direccion-invalida',
            )

    def test_admin_no_expone_acciones_masivas_ni_borrado(self):
        model_admin = DestinatarioNotificacionInstitucionalEducativaAdmin(
            DestinatarioNotificacionInstitucionalEducativa,
            admin.site,
        )
        self.assertIsNone(model_admin.actions)
        self.assertFalse(model_admin.has_delete_permission(mock.Mock()))
        self.assertIn('correo', model_admin.search_fields)
        self.assertIn('activo', model_admin.list_filter)

    def test_lista_env_normaliza_deduplica_y_rechaza_invalidos(self):
        with mock.patch.dict(
            os.environ,
            {'TEST_EMAIL_LIST': 'Audit@Example.Test,audit@example.test'},
        ):
            self.assertEqual(
                project_settings._validated_email_env_list('TEST_EMAIL_LIST'),
                ['audit@example.test'],
            )
        with mock.patch.dict(
            os.environ,
            {'TEST_EMAIL_LIST': 'direccion-invalida'},
        ):
            with self.assertRaises(ImproperlyConfigured):
                project_settings._validated_email_env_list('TEST_EMAIL_LIST')


@override_settings(
    DEBUG=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FINANCIACION_EDUCATIVA_REVIEW_NOTIFICATION_EMAILS=[],
    EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=['audit@example.test'],
    FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_LEASE_SECONDS=60,
    FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_MAX_ATTEMPTS=3,
)
class ConcurrenciaCopiasCorreoPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def test_dos_workers_crean_una_sola_copia(self):
        if connection.vendor != 'postgresql':
            self.skipTest('SKIPPED: requiere PostgreSQL real para bloqueos por fila.')
        solicitud = crear_solicitud(referencia='COPY-PG-CONCURRENCY')
        with transaction.atomic():
            original, _ = crear_correo_expediente_recibido(solicitud=solicitud)
        lease_id = uuid.uuid4()
        OutboxCorreoEducativo.objects.filter(pk=original.pk).update(
            estado=EstadoOutboxCorreoEducativo.SENDING,
            lease_id=lease_id,
            lease_vence_en=timezone.now(),
        )

        def finalizar():
            close_old_connections()
            try:
                resultado = _finalizar(
                    outbox_id=original.pk,
                    lease_id=lease_id,
                    estado=EstadoOutboxCorreoEducativo.SENT,
                )
                return str(resultado.pk), None
            except Exception as error:
                return None, error
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(lambda _: finalizar(), range(2)))

        errores = [error for _, error in resultados if error is not None]
        self.assertEqual(errores, [])
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                correo_origen=original,
                codigo_mensaje=CodigoMensajeCorreoEducativo.AUDIT_COPY,
            ).count(),
            1,
        )
