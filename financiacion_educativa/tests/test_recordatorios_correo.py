from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from io import StringIO
import threading
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEntregaInvitacion,
    EstadoInvitacionContinuacion,
    EstadoOutboxCorreoEducativo,
    EstadoSolicitudFinanciacion,
    TipoEventoCorreoEducativo,
)
from financiacion_educativa.models import (
    EntregaInvitacionContinuacion,
    InvitacionContinuacionSolicitud,
    OutboxCorreoEducativo,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.orquestacion import (
    crear_solicitud_institucional_orquestada,
    programar_invitacion_inicial,
)
from financiacion_educativa.services.outbox_correos import (
    procesar_siguiente_correo,
    programar_notificacion_nueva_solicitud_interna,
)
from financiacion_educativa.services.recordatorios_solicitudes import (
    programar_recordatorios_solicitudes,
)
from financiacion_educativa.services.solicitudes import (
    DatosSolicitudFinanciacion,
)
from financiacion_educativa.tests.delivery_backends import (
    RecordingInvitationDeliveryBackend,
)
from financiacion_educativa.tests.factories import (
    crear_institucion,
    crear_solicitud,
)


BACKEND_REGISTRO = (
    'financiacion_educativa.tests.delivery_backends.'
    'RecordingInvitationDeliveryBackend'
)
BACKEND_DJANGO = (
    'financiacion_educativa.services.entrega_invitaciones.'
    'DjangoEmailInvitationDeliveryBackend'
)


@override_settings(
    DEBUG=True,
    BRAND_PUBLIC_BASE_URL='https://educacion.example.test',
    FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_REGISTRO,
    FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS=0,
    FINANCIACION_EDUCATIVA_CONTINUATION_MAX_MESSAGES=4,
)
class RecordatoriosSolicitudesEducativasTests(TestCase):
    def setUp(self):
        RecordingInvitationDeliveryBackend.reset()

    def _solicitud_con_invitacion_enviada(self, referencia='REM-001'):
        solicitud = crear_solicitud(referencia=referencia)
        programar_invitacion_inicial(solicitud=solicitud)
        resultado = procesar_siguiente_correo()
        self.assertEqual(resultado.estado, EstadoOutboxCorreoEducativo.SENT)
        solicitud.refresh_from_db()
        return solicitud

    def _envejecer(self, solicitud, horas):
        creada_en = timezone.now() - timedelta(hours=horas)
        SolicitudFinanciacionEducativa.objects.filter(pk=solicitud.pk).update(
            creada_en=creada_en
        )
        solicitud.creada_en = creada_en
        return creada_en

    def _evento_programado(self):
        return OutboxCorreoEducativo.objects.order_by('-creada_en').first().tipo_evento

    def test_cadencia_1_hora(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-1H')
        base = self._envejecer(solicitud, 1)

        resultado = programar_recordatorios_solicitudes(
            ahora=base + timedelta(hours=1)
        )

        self.assertEqual(resultado.programadas, 1)
        self.assertEqual(
            self._evento_programado(),
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_1H,
        )

    def test_cadencia_6_horas(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-6H')
        base = self._envejecer(solicitud, 6)
        programar_recordatorios_solicitudes(ahora=base + timedelta(hours=6))
        self.assertEqual(
            self._evento_programado(),
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_6H,
        )

    def test_cadencia_24_horas(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-24H')
        base = self._envejecer(solicitud, 24)
        programar_recordatorios_solicitudes(ahora=base + timedelta(hours=24))
        self.assertEqual(
            self._evento_programado(),
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_24H,
        )

    @override_settings(FINANCIACION_EDUCATIVA_CONTINUATION_MAX_MESSAGES=5)
    def test_cadencia_48_horas_disponible_si_maximo_autoriza_cinco(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-48H')
        base = self._envejecer(solicitud, 48)
        programar_recordatorios_solicitudes(ahora=base + timedelta(hours=48))
        self.assertEqual(
            self._evento_programado(),
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_48H,
        )

    def test_antes_de_tiempo_no_programa(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-EARLY')
        base = self._envejecer(solicitud, 1)
        resultado = programar_recordatorios_solicitudes(
            ahora=base + timedelta(minutes=59)
        )
        self.assertEqual(resultado.evaluadas, 0)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)

    def test_maximo_incluye_invitacion_inicial(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-MAX')
        base = self._envejecer(solicitud, 50)
        for horas in (1, 6, 24):
            resultado = programar_recordatorios_solicitudes(
                ahora=base + timedelta(hours=horas)
            )
            self.assertEqual(resultado.programadas, 1)
            procesar_siguiente_correo()

        final = programar_recordatorios_solicitudes(
            ahora=base + timedelta(hours=48)
        )

        self.assertEqual(final.programadas, 0)
        self.assertEqual(EntregaInvitacionContinuacion.objects.count(), 4)
        self.assertFalse(
            OutboxCorreoEducativo.objects.filter(
                tipo_evento=TipoEventoCorreoEducativo.CONTINUATION_REMINDER_48H
            ).exists()
        )

    def test_maximo_seguro_no_habilita_48_horas_tras_inactividad(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-MAX-DOWNTIME')
        base = self._envejecer(solicitud, 50)

        resultado = programar_recordatorios_solicitudes(
            ahora=base + timedelta(hours=50)
        )

        self.assertEqual(resultado.programadas, 1)
        self.assertEqual(
            self._evento_programado(),
            TipoEventoCorreoEducativo.CONTINUATION_REMINDER_24H,
        )
        self.assertFalse(
            OutboxCorreoEducativo.objects.filter(
                tipo_evento=(
                    TipoEventoCorreoEducativo.CONTINUATION_REMINDER_48H
                )
            ).exists()
        )

    def test_estado_cambiado_omite_recordatorio(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-STATE')
        self._envejecer(solicitud, 2)
        SolicitudFinanciacionEducativa.objects.filter(pk=solicitud.pk).update(
            estado=EstadoSolicitudFinanciacion.PENDING_TERMS
        )
        resultado = programar_recordatorios_solicitudes()
        self.assertEqual(resultado.programadas, 0)

    def test_invitacion_revocada_omite_recordatorio(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-REVOKED')
        self._envejecer(solicitud, 2)
        InvitacionContinuacionSolicitud.objects.filter(solicitud=solicitud).update(
            estado=EstadoInvitacionContinuacion.REVOKED
        )
        resultado = programar_recordatorios_solicitudes()
        self.assertEqual(resultado.programadas, 0)

    def test_invitacion_consumida_omite_recordatorio(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-CONSUMED')
        self._envejecer(solicitud, 2)
        usuario = get_user_model().objects.create_user(
            username='reminder-consumed',
            email='consumed@example.com',
            password='Clave-segura-2026',
        )
        InvitacionContinuacionSolicitud.objects.filter(solicitud=solicitud).update(
            estado=EstadoInvitacionContinuacion.CONSUMED,
            consumida_en=timezone.now(),
            consumida_por=usuario,
        )
        resultado = programar_recordatorios_solicitudes()
        self.assertEqual(resultado.programadas, 0)

    def test_invitacion_vencida_se_rota_sin_reutilizar_token(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-EXPIRED')
        self._envejecer(solicitud, 2)
        anterior = InvitacionContinuacionSolicitud.objects.get(
            solicitud=solicitud,
            estado=EstadoInvitacionContinuacion.ACTIVE,
        )
        InvitacionContinuacionSolicitud.objects.filter(pk=anterior.pk).update(
            vence_en=timezone.now() - timedelta(minutes=1)
        )

        resultado = programar_recordatorios_solicitudes()
        procesar_siguiente_correo()

        self.assertEqual(resultado.programadas, 1)
        anterior.refresh_from_db()
        self.assertEqual(anterior.estado, EstadoInvitacionContinuacion.REVOKED)
        self.assertEqual(
            InvitacionContinuacionSolicitud.objects.filter(
                solicitud=solicitud,
                estado=EstadoInvitacionContinuacion.ACTIVE,
            ).count(),
            1,
        )

    def test_dry_run_no_crea_entrega_ni_outbox(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-DRY')
        self._envejecer(solicitud, 2)
        resultado = programar_recordatorios_solicitudes(dry_run=True)
        self.assertEqual(resultado.programadas, 1)
        self.assertEqual(EntregaInvitacionContinuacion.objects.count(), 1)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)

    def test_dos_ejecuciones_no_duplican_evento_logico(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-IDEM')
        self._envejecer(solicitud, 2)
        primera = programar_recordatorios_solicitudes()
        procesar_siguiente_correo()
        segunda = programar_recordatorios_solicitudes()
        self.assertEqual(primera.programadas, 1)
        self.assertEqual(segunda.programadas, 0)
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                tipo_evento=TipoEventoCorreoEducativo.CONTINUATION_REMINDER_1H
            ).count(),
            1,
        )

    def test_fallo_de_entrega_no_crea_otra_secuencia(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-FAIL')
        self._envejecer(solicitud, 2)
        programar_recordatorios_solicitudes()
        with override_settings(
            FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=(
                'financiacion_educativa.tests.delivery_backends.'
                'FailingInvitationDeliveryBackend'
            )
        ):
            procesar_siguiente_correo()

        segundo = programar_recordatorios_solicitudes()

        self.assertEqual(segundo.programadas, 0)
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                tipo_evento=TipoEventoCorreoEducativo.CONTINUATION_REMINDER_1H
            ).count(),
            1,
        )

    @override_settings(
        DEBUG=True,
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_DJANGO,
    )
    def test_recordatorio_24_horas_es_ultimo_aviso_con_maximo_cuatro(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-24H-FINAL')
        mail.outbox.clear()
        base = self._envejecer(solicitud, 24)
        programar_recordatorios_solicitudes(ahora=base + timedelta(hours=24))
        procesar_siguiente_correo()

        mensaje = mail.outbox[0]
        self.assertIn('Ultimo recordatorio', mensaje.subject)
        self.assertIn('ultimo recordatorio automatico', mensaje.body.lower())
        self.assertIn(
            'no enviaremos m&aacute;s recordatorios autom&aacute;ticos',
            mensaje.alternatives[0].content.lower(),
        )

    @override_settings(
        DEBUG=True,
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_DJANGO,
        FINANCIACION_EDUCATIVA_CONTINUATION_MAX_MESSAGES=5,
    )
    def test_recordatorio_final_identifica_ultimo_aviso_en_html_y_texto(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-FINAL-TEMPLATE')
        mail.outbox.clear()
        base = self._envejecer(solicitud, 48)
        programar_recordatorios_solicitudes(ahora=base + timedelta(hours=48))
        procesar_siguiente_correo()

        mensaje = mail.outbox[0]
        self.assertIn('Ultimo recordatorio', mensaje.subject)
        self.assertIn('ultimo recordatorio automatico', mensaje.body.lower())
        self.assertIn(
            'recordatorios autom',
            mensaje.alternatives[0].content.lower(),
        )

    @override_settings(
        DEBUG=True,
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_DJANGO,
        FINANCIACION_EDUCATIVA_CONTINUATION_MAX_MESSAGES=5,
    )
    def test_recordatorio_24_horas_no_es_final_si_maximo_es_cinco(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-24H-NOT-FINAL')
        mail.outbox.clear()
        base = self._envejecer(solicitud, 24)
        programar_recordatorios_solicitudes(ahora=base + timedelta(hours=24))
        procesar_siguiente_correo()

        mensaje = mail.outbox[0]
        self.assertNotIn('Ultimo recordatorio', mensaje.subject)
        self.assertNotIn('ultimo recordatorio automatico', mensaje.body.lower())
        self.assertNotIn(
            'no enviaremos m&aacute;s recordatorios autom&aacute;ticos',
            mensaje.alternatives[0].content.lower(),
        )

    def test_cambio_de_estado_antes_del_worker_cancela_entrega(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-RACE')
        self._envejecer(solicitud, 2)
        programar_recordatorios_solicitudes()
        RecordingInvitationDeliveryBackend.reset()
        SolicitudFinanciacionEducativa.objects.filter(pk=solicitud.pk).update(
            estado=EstadoSolicitudFinanciacion.PENDING_TERMS
        )

        resultado = procesar_siguiente_correo()

        self.assertEqual(resultado.estado, EstadoOutboxCorreoEducativo.FAILED)
        self.assertEqual(resultado.codigo, 'INVITATION_NO_LONGER_ELIGIBLE')
        self.assertEqual(RecordingInvitationDeliveryBackend.deliveries, [])

    def test_comando_informa_conteos_y_no_entrega_smtp(self):
        solicitud = self._solicitud_con_invitacion_enviada('REM-CMD')
        self._envejecer(solicitud, 2)
        RecordingInvitationDeliveryBackend.reset()
        salida = StringIO()
        call_command(
            'programar_recordatorios_solicitudes_educativas',
            '--dry-run',
            stdout=salida,
        )
        self.assertIn('programadas: 1', salida.getvalue())
        self.assertEqual(RecordingInvitationDeliveryBackend.deliveries, [])


@override_settings(
    DEBUG=True,
    BRAND_PUBLIC_BASE_URL='https://educacion.example.test',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_DJANGO,
)
class PlantillasYNotificacionInternaTests(TestCase):
    @override_settings(
        EDUCATIONAL_OPERATIONS_NOTIFICATION_EMAILS=['ops@example.com']
    )
    def test_creacion_institucional_programa_una_notificacion_sin_duplicarla(self):
        institucion = crear_institucion('notify')
        datos = DatosSolicitudFinanciacion(
            referencia_externa='INTERNAL-ORCHESTRATED',
            nombres='ANA MARIA',
            apellidos='PEREZ LOPEZ',
            celular='3001234567',
            correo='applicant@example.com',
            direccion='Calle 10 # 20-30',
            valor_plan=Decimal('1000000.00'),
            plazo_meses=6,
            nombre_curso='Ingles A2',
            tipo_curso='INGLES',
            correlation_id='internal-orchestrated',
        )

        primero = crear_solicitud_institucional_orquestada(
            institucion=institucion,
            clave_idempotencia='internal-notification-key',
            datos=datos,
        )
        segundo = crear_solicitud_institucional_orquestada(
            institucion=institucion,
            clave_idempotencia='internal-notification-key',
            datos=datos,
        )

        self.assertFalse(primero.repetida)
        self.assertTrue(segundo.repetida)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 2)
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                tipo_evento=TipoEventoCorreoEducativo.NEW_APPLICATION_INTERNAL
            ).count(),
            1,
        )

    def test_sin_destinatarios_omite_y_registra_motivo(self):
        solicitud = crear_solicitud(referencia='INTERNAL-NONE')
        with self.assertLogs(
            'financiacion_educativa.services.outbox_correos', level='INFO'
        ) as logs:
            with transaction.atomic():
                outbox, creado = programar_notificacion_nueva_solicitud_interna(
                    solicitud=solicitud
                )
        self.assertIsNone(outbox)
        self.assertFalse(creado)
        self.assertIn('NO_INTERNAL_RECIPIENTS', ' '.join(logs.output))
        self.assertFalse(OutboxCorreoEducativo.objects.exists())

    @override_settings(
        EDUCATIONAL_OPERATIONS_NOTIFICATION_EMAILS=['ops@example.com']
    )
    def test_notificacion_interna_es_idempotente_enmascarada_y_no_copia_estudiante(self):
        solicitud = crear_solicitud(referencia='INTERNAL-ONE')
        SolicitudFinanciacionEducativa.objects.filter(pk=solicitud.pk).update(
            tipo_documento_estudiante='CC',
            numero_documento_estudiante='1234567890',
        )
        solicitud.refresh_from_db()
        with transaction.atomic():
            primero, creado_primero = programar_notificacion_nueva_solicitud_interna(
                solicitud=solicitud
            )
            segundo, creado_segundo = programar_notificacion_nueva_solicitud_interna(
                solicitud=solicitud
            )

        self.assertTrue(creado_primero)
        self.assertFalse(creado_segundo)
        self.assertEqual(primero.pk, segundo.pk)
        procesar_siguiente_correo()
        mensaje = mail.outbox[0]
        contenido = mensaje.body + mensaje.alternatives[0].content
        self.assertEqual(mensaje.to, ['ops@example.com'])
        self.assertNotIn(solicitud.correo, mensaje.to)
        self.assertIn('******7890', contenido)
        self.assertNotIn('1234567890', contenido)
        self.assertIn('Nueva solicitud', contenido)
        self.assertIn('Estado:', mensaje.body)
        self.assertIn('Notificaci&oacute;n interna', mensaje.alternatives[0].content)
        self.assertNotIn('data:image', contenido)
        self.assertNotIn('href="#"', contenido)
        self.assertNotIn('token', repr(primero.contexto).lower())

    def test_invitacion_html_y_texto_son_equivalentes_y_sin_dominios_ajenos(self):
        solicitud = crear_solicitud(referencia='TEMPLATE-INV')
        programar_invitacion_inicial(solicitud=solicitud)
        procesar_siguiente_correo()
        mensaje = mail.outbox[0]
        texto = mensaje.body.lower()
        html = mensaje.alternatives[0].content.lower()
        for esperado in ('programa', 'curso', 'referencia', 'continuar mi solicitud'):
            self.assertIn(esperado, texto)
            self.assertIn(esperado, html)
        for prohibido in ('libranza', 'desembolso', 'datacredito', 'transunion'):
            self.assertNotIn(prohibido, texto)
            self.assertNotIn(prohibido, html)
        self.assertIn('role="presentation"', html)
        self.assertIn('solicitud recibida', html)
        self.assertIn('si el boton no funciona', html)
        self.assertNotIn('data:image', html)
        self.assertNotIn('href="#"', html)
        self.assertNotIn('<script', html)


@override_settings(
    DEBUG=True,
    BRAND_PUBLIC_BASE_URL='https://educacion.example.test',
    FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND=BACKEND_REGISTRO,
    FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS=0,
)
class ConcurrenciaRecordatoriosPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def _programar(self, barrera, errores):
        close_old_connections()
        try:
            barrera.wait(timeout=5)
            return programar_recordatorios_solicitudes(limite=10)
        except Exception as error:  # pragma: no cover - diagnostico de hilo
            errores.append(error)
            return None
        finally:
            connection.close()

    @skipUnless(
        connection.vendor == 'postgresql',
        'La concurrencia del programador requiere PostgreSQL real.',
    )
    def test_dos_programadores_generan_un_solo_recordatorio(self):
        solicitud = crear_solicitud(referencia='REM-PG-CONCURRENCY')
        programar_invitacion_inicial(solicitud=solicitud)
        procesar_siguiente_correo()
        SolicitudFinanciacionEducativa.objects.filter(pk=solicitud.pk).update(
            creada_en=timezone.now() - timedelta(hours=2)
        )
        barrera = threading.Barrier(2)
        errores = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = [
                executor.submit(self._programar, barrera, errores),
                executor.submit(self._programar, barrera, errores),
            ]
            resultados = [futuro.result() for futuro in futuros]

        self.assertEqual(errores, [])
        self.assertEqual(sum(r.programadas for r in resultados), 1)
        self.assertEqual(
            OutboxCorreoEducativo.objects.filter(
                tipo_evento=TipoEventoCorreoEducativo.CONTINUATION_REMINDER_1H
            ).count(),
            1,
        )
        self.assertEqual(
            EntregaInvitacionContinuacion.objects.filter(
                solicitud=solicitud,
                secuencia=2,
            ).count(),
            1,
        )
