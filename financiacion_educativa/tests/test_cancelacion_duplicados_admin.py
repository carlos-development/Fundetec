from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import EstadoSolicitudFinanciacion
from financiacion_educativa.models import (
    EntregaInvitacionContinuacion, HistorialEstadoSolicitud,
    InvitacionContinuacionSolicitud, OutboxCorreoEducativo,
    ParticipanteFinanciacion, SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.asociacion import asociar_usuario_mediante_invitacion
from financiacion_educativa.services.cancelacion_duplicados import (
    MOTIVO_CONTROLADO, cancelar_duplicado_sin_registro,
)
from financiacion_educativa.services.estado_publico import obtener_resultado_publico
from financiacion_educativa.services.estados import transicionar_solicitud
from financiacion_educativa.services.invitaciones import (
    InvitacionNoValida, emitir_invitacion_continuacion,
)
from financiacion_educativa.services.orquestacion import crear_solicitud_institucional_orquestada, programar_invitacion_inicial
from financiacion_educativa.services.outbox_correos import (
    _crear_copia_post_envio, crear_correo_expediente_recibido,
    procesar_siguiente_correo, reclamar_correo_pendiente,
)
from financiacion_educativa.services.solicitudes import DatosSolicitudFinanciacion
from financiacion_educativa.tests.factories import crear_configuracion_financiera, crear_institucion, crear_solicitud
from financiacion_educativa.tests.test_dashboard_obligaciones import DatosObligacionesMixin


AISLAMIENTO = dict(
    DEBUG=True, DEPLOYMENT_ENVIRONMENT='test', SECURE_SSL_REDIRECT=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_QA_MODE=False, EMAIL_LIVE_DELIVERY_ENABLED=False,
    EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=[],
    EDUCATIONAL_OPERATIONS_NOTIFICATION_EMAILS=[],
    FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=False,
    FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=False,
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=False,
    FINANCIACION_EDUCATIVA_CONTRACTUAL_PLAN_EMAILS_ENABLED=False,
    FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED=False,
    BRAND_PUBLIC_BASE_URL='https://portal.example.test',
)
MOTIVO = 'Registro duplicado confirmado por Operaciones.'


class DatosDuplicado:
    def preparar(self):
        self.actor = get_user_model().objects.create_superuser('super', 'super@example.test', 'test-only')
        self.institucion = crear_institucion('cancel-1')
        self.solicitud = crear_solicitud(institucion=self.institucion, referencia='DUP-SYNTHETIC',
            correo='student@example.test')
        self.entrega = programar_invitacion_inicial(solicitud=self.solicitud, actor=self.actor).entrega
        self.invitacion = self.entrega.invitacion
        self.correo = self.entrega.correo_outbox

    def cancelar(self, **kwargs):
        return cancelar_duplicado_sin_registro(**{
            'solicitud_id': self.solicitud.pk, 'actor': self.actor,
            'referencia': self.solicitud.referencia_externa, 'motivo': MOTIVO, 'confirmar': True, **kwargs})

    def comprobar_sin_cambio(self):
        self.solicitud.refresh_from_db()
        self.invitacion.refresh_from_db()
        self.correo.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'PENDING_USER_REGISTRATION')
        self.assertEqual(self.invitacion.estado, 'ACTIVE')
        self.assertEqual(self.correo.estado, 'PENDING')
        self.assertFalse(self.solicitud.historial_estados.filter(estado_nuevo='CANCELLED').exists())


@override_settings(**AISLAMIENTO)
class CancelacionDuplicadoAdminTests(DatosDuplicado, TestCase):
    def setUp(self):
        self.preparar()
        self.url = reverse('admin:financiacion_educativa_solicitud_cancelar_duplicado', args=[self.solicitud.pk])
        self.client.force_login(self.actor)

    def test_cancelacion_auditada_conserva_historial_y_revoca_invitacion(self):
        historiales = self.solicitud.historial_estados.count()
        resultado = self.cancelar()
        self.solicitud.refresh_from_db()
        self.invitacion.refresh_from_db()
        self.entrega.refresh_from_db()
        self.correo.refresh_from_db()
        self.assertFalse(resultado.repetida)
        self.assertEqual(self.solicitud.estado, 'CANCELLED')
        self.assertIsNone(self.solicitud.usuario_id)
        self.assertEqual(self.invitacion.estado, 'REVOKED')
        self.assertEqual(self.entrega.estado, 'CANCELLED')
        self.assertEqual(self.correo.estado, 'SUPERSEDED')
        self.assertEqual(self.correo.codigo_ultimo_error, MOTIVO_CONTROLADO)
        self.assertEqual(self.solicitud.historial_estados.count(), historiales + 1)
        historia = self.solicitud.historial_estados.get(estado_nuevo='CANCELLED')
        self.assertEqual(historia.actor_id, self.actor.pk)
        self.assertEqual(historia.motivo, MOTIVO)
        self.assertEqual(historia.metadata['reason_code'], MOTIVO_CONTROLADO)
        self.assertEqual(self.invitacion.eventos.filter(tipo='REVOKED').count(), 1)

    def test_reintento_identico_es_idempotente(self):
        self.cancelar()
        self.assertTrue(self.cancelar().repetida)
        self.assertEqual(self.solicitud.historial_estados.filter(estado_nuevo='CANCELLED').count(), 1)
        self.assertEqual(self.invitacion.eventos.filter(tipo='REVOKED').count(), 1)
        with self.assertRaises(ValidationError):
            self.cancelar(motivo='Otro motivo diferente suficientemente largo.')

    def test_replay_institucional_no_reabre_ni_reenvia_cancelada(self):
        datos = DatosSolicitudFinanciacion(referencia_externa='IDEM-SYNTHETIC',
            nombres='PERSONA', apellidos='SINTETICA', celular='3000000000',
            correo='student@example.test', direccion='Direccion sintetica',
            valor_plan=Decimal('1000000'), plazo_meses=12, nombre_curso='Curso sintetico')
        argumentos = dict(institucion=self.institucion, clave_idempotencia='synthetic-only-key', datos=datos)
        primera = crear_solicitud_institucional_orquestada(**argumentos)
        self.cancelar(solicitud_id=primera.solicitud.pk, referencia=datos.referencia_externa)
        cantidades = (SolicitudFinanciacionEducativa.objects.count(), InvitacionContinuacionSolicitud.objects.count(),
            EntregaInvitacionContinuacion.objects.count(), OutboxCorreoEducativo.objects.count())
        replay = crear_solicitud_institucional_orquestada(**argumentos)
        self.assertTrue(replay.repetida)
        self.assertEqual(replay.solicitud.pk, primera.solicitud.pk)
        replay.solicitud.refresh_from_db()
        self.assertEqual(replay.solicitud.estado, 'CANCELLED')
        self.assertEqual(cantidades, (SolicitudFinanciacionEducativa.objects.count(),
            InvitacionContinuacionSolicitud.objects.count(), EntregaInvitacionContinuacion.objects.count(),
            OutboxCorreoEducativo.objects.count()))

    def test_referencia_exacta_confirmacion_y_motivo_sin_efectos_parciales(self):
        for cambio in ({'referencia': 'otra'}, {'referencia': 'DUP-SYNTHETIC '},
                {'referencia': 'dup-synthetic'}, {'confirmar': False}, {'motivo': 'corto'}):
            with self.subTest(cambio=cambio), self.assertRaises(ValidationError):
                self.cancelar(**cambio)
            self.comprobar_sin_cambio()

    def test_solo_superadministrador_activo_y_permiso_revalidado(self):
        for campos in ({'is_superuser': False}, {'is_staff': False}, {'is_active': False}):
            get_user_model().objects.filter(pk=self.actor.pk).update(**campos)
            with self.subTest(campos=campos), self.assertRaises(PermissionDenied):
                self.cancelar()
            get_user_model().objects.filter(pk=self.actor.pk).update(is_superuser=True, is_staff=True, is_active=True)
        self.comprobar_sin_cambio()

    def test_todos_los_estados_avanzados_bloquean(self):
        for estado in EstadoSolicitudFinanciacion.values:
            if estado == 'PENDING_USER_REGISTRATION':
                continue
            SolicitudFinanciacionEducativa.objects.filter(pk=self.solicitud.pk).update(estado=estado)
            with self.subTest(estado=estado), self.assertRaises(ValidationError):
                self.cancelar()
        SolicitudFinanciacionEducativa.objects.filter(pk=self.solicitud.pk).update(estado='PENDING_USER_REGISTRATION')
        self.comprobar_sin_cambio()

    def test_cuenta_vinculada_impide_cancelar(self):
        SolicitudFinanciacionEducativa.objects.filter(pk=self.solicitud.pk).update(usuario=self.actor)
        with self.assertRaises(ValidationError) as error:
            self.cancelar()
        self.assertEqual(error.exception.code, 'DUPLICATE_ACCOUNT_LINKED')

    def test_fecha_matricula_efectiva_impide_cancelar(self):
        SolicitudFinanciacionEducativa.objects.filter(pk=self.solicitud.pk).update(fecha_matricula=date(2026, 1, 1))
        with self.assertRaises(ValidationError) as error:
            self.cancelar()
        self.assertEqual(error.exception.code, 'DUPLICATE_PROGRESS_FOUND')

    def test_solicitud_sin_invitacion_tambien_puede_cancelarse(self):
        otra = crear_solicitud(institucion=self.institucion, referencia='UNUSED-SYNTHETIC')
        resultado = self.cancelar(solicitud_id=otra.pk, referencia=otra.referencia_externa)
        self.assertEqual(resultado.invitaciones_revocadas, 0)
        self.assertEqual(resultado.correos_detenidos, 0)
        otra.refresh_from_db()
        self.assertEqual(otra.estado, 'CANCELLED')
        self.comprobar_sin_cambio()

    def test_avance_historico_impide_cancelar_aunque_estado_fue_restablecido(self):
        transicionar_solicitud(solicitud=self.solicitud, nuevo_estado='PENDING_TERMS')
        SolicitudFinanciacionEducativa.objects.filter(pk=self.solicitud.pk).update(estado='PENDING_USER_REGISTRATION')
        with self.assertRaises(ValidationError) as error:
            self.cancelar()
        self.assertEqual(error.exception.code, 'DUPLICATE_PROGRESS_FOUND')

    def test_invitacion_consumida_impide_cancelar(self):
        InvitacionContinuacionSolicitud.objects.filter(pk=self.invitacion.pk).update(
            estado='CONSUMED', consumida_en=timezone.now(), consumida_por=self.actor)
        with self.assertRaises(ValidationError):
            self.cancelar()

    def test_evidencia_de_participante_impide_cancelar(self):
        ParticipanteFinanciacion.objects.create(solicitud=self.solicitud, nombres='PERSONA',
            apellidos='SINTETICA', tipo_documento='CC', numero_documento='SYNTHETIC001')
        with self.assertRaises(ValidationError) as error:
            self.cancelar()
        self.assertEqual(error.exception.code, 'DUPLICATE_PROGRESS_FOUND')

    def test_comunicacion_posterior_impide_cancelar(self):
        OutboxCorreoEducativo.objects.filter(pk=self.correo.pk).update(tipo_evento='DOSSIER_RECEIVED')
        with self.assertRaises(ValidationError):
            self.cancelar()

    def test_envio_reclamado_o_ambiguo_impide_cancelar(self):
        reclamado = reclamar_correo_pendiente()
        self.assertEqual(reclamado.pk, self.correo.pk)
        with self.assertRaises(ValidationError) as error:
            self.cancelar()
        self.assertEqual(error.exception.code, 'DUPLICATE_DELIVERY_IN_PROGRESS')
        OutboxCorreoEducativo.objects.filter(pk=reclamado.pk).update(lease_vence_en=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(ValidationError):
            self.cancelar()
        OutboxCorreoEducativo.objects.filter(pk=reclamado.pk).update(
            estado='AMBIGUOUS', lease_id=None, lease_vence_en=None)
        with self.assertRaises(ValidationError):
            self.cancelar()

    def test_entrega_en_curso_impide_cancelar(self):
        EntregaInvitacionContinuacion.objects.filter(pk=self.entrega.pk).update(estado='SENDING')
        with self.assertRaises(ValidationError):
            self.cancelar()

    def test_correo_enviado_e_historial_de_entrega_no_se_reescriben(self):
        ahora = timezone.now()
        OutboxCorreoEducativo.objects.filter(pk=self.correo.pk).update(estado='SENT', enviada_en=ahora)
        EntregaInvitacionContinuacion.objects.filter(pk=self.entrega.pk).update(estado='SENT', enviada_en=ahora)
        previo = OutboxCorreoEducativo.objects.values().get(pk=self.correo.pk)
        entrega_previa = EntregaInvitacionContinuacion.objects.values().get(pk=self.entrega.pk)
        self.cancelar()
        self.assertEqual(OutboxCorreoEducativo.objects.values().get(pk=self.correo.pk), previo)
        self.assertEqual(EntregaInvitacionContinuacion.objects.values().get(pk=self.entrega.pk), entrega_previa)

    def test_correo_reintentable_se_detiene_sin_borrar(self):
        OutboxCorreoEducativo.objects.filter(pk=self.correo.pk).update(estado='RETRYING')
        self.cancelar()
        self.correo.refresh_from_db()
        self.assertEqual(self.correo.estado, 'SUPERSEDED')
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)

    def test_invitacion_activa_vencida_tambien_se_revoca(self):
        InvitacionContinuacionSolicitud.objects.filter(pk=self.invitacion.pk).update(vence_en=timezone.now() - timedelta(days=1))
        self.cancelar()
        self.invitacion.refresh_from_db()
        self.assertEqual(self.invitacion.estado, 'REVOKED')

    def test_cancelada_no_puede_asociarse_ni_emitir_nueva_invitacion(self):
        self.cancelar()
        usuario = get_user_model().objects.create_user('student', 'student@example.test', 'test-only')
        with self.assertRaises(InvitacionNoValida):
            asociar_usuario_mediante_invitacion(invitacion_id=self.invitacion.pk, usuario=usuario)
        self.solicitud.refresh_from_db()
        with self.assertRaises(ValidationError):
            emitir_invitacion_continuacion(solicitud=self.solicitud)
        resultado = obtener_resultado_publico(self.solicitud)
        self.assertEqual(resultado.estado, 'CANCELLED')
        self.assertFalse(resultado.curso_autorizado)
        self.assertIsNone(resultado.condiciones_financieras)

    @patch('financiacion_educativa.services.outbox_correos._enviar_mensaje_smtp')
    def test_reintento_forzado_no_envia_correo(self, smtp):
        self.cancelar()
        OutboxCorreoEducativo.objects.filter(pk=self.correo.pk).update(estado='PENDING')
        resultado = procesar_siguiente_correo()
        self.assertEqual(resultado.estado, 'SUPERSEDED')
        self.correo.refresh_from_db()
        self.assertEqual(self.correo.codigo_ultimo_error, 'APPLICATION_CANCELLED')
        smtp.assert_not_called()

    @patch('financiacion_educativa.services.outbox_correos._enviar_mensaje_smtp')
    def test_copia_tardia_de_original_sent_no_se_envia(self, smtp):
        OutboxCorreoEducativo.objects.filter(pk=self.correo.pk).update(estado='SENT', enviada_en=timezone.now())
        self.cancelar()
        self.correo.refresh_from_db()
        with transaction.atomic():
            copia, creada = _crear_copia_post_envio(original=self.correo,
                codigo_mensaje='AUDIT_COPY', destinatarios=['audit@example.test'])
        self.assertTrue(creada)
        self.assertEqual(procesar_siguiente_correo().estado, 'SUPERSEDED')
        copia.refresh_from_db()
        self.correo.refresh_from_db()
        self.assertEqual(copia.codigo_ultimo_error, 'APPLICATION_CANCELLED')
        self.assertEqual(self.correo.estado, 'SENT')
        smtp.assert_not_called()

    def test_otra_institucion_y_otro_uuid_no_se_modifican(self):
        institucion = crear_institucion('cancel-2')
        otra = crear_solicitud(institucion=institucion, referencia=self.solicitud.referencia_externa,
            correo=self.solicitud.correo)
        entrega = programar_invitacion_inicial(solicitud=otra).entrega
        previa = SolicitudFinanciacionEducativa.objects.values().get(pk=otra.pk)
        correo_previo = OutboxCorreoEducativo.objects.values().get(entrega_invitacion=entrega)
        self.cancelar()
        self.assertEqual(SolicitudFinanciacionEducativa.objects.values().get(pk=otra.pk), previa)
        self.assertEqual(OutboxCorreoEducativo.objects.values().get(entrega_invitacion=entrega), correo_previo)

    def test_get_no_modifica_y_post_exige_csrf(self):
        self.assertContains(self.client.get(self.url), 'Referencia exacta')
        self.comprobar_sin_cambio()
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.actor)
        self.assertEqual(cliente.post(self.url, {'referencia': 'DUP-SYNTHETIC', 'motivo': MOTIVO, 'confirmar': 'on'}).status_code, 403)
        self.comprobar_sin_cambio()

    def test_admin_registra_una_sola_accion_y_replay(self):
        datos = {'referencia': 'DUP-SYNTHETIC', 'motivo': MOTIVO, 'confirmar': 'on'}
        self.assertEqual(self.client.post(self.url, datos).status_code, 302)
        self.assertEqual(self.client.post(self.url, datos).status_code, 302)
        self.assertEqual(LogEntry.objects.filter(object_id=str(self.solicitud.pk)).count(), 1)

    def test_staff_sin_superusuario_no_accede_ni_ve_accion(self):
        from django.contrib.auth.models import Permission
        staff = get_user_model().objects.create_user('staff', 'staff@example.test', 'test-only', is_staff=True)
        staff.user_permissions.set(Permission.objects.filter(content_type__app_label='financiacion_educativa'))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {}).status_code, 403)
        cambio = reverse('admin:financiacion_educativa_solicitudfinanciacioneducativa_change', args=[self.solicitud.pk])
        self.assertNotContains(self.client.get(cambio), 'cancelar-duplicado/')
        self.comprobar_sin_cambio()

    def test_admin_revalida_despues_de_abrir_formulario(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        SolicitudFinanciacionEducativa.objects.filter(pk=self.solicitud.pk).update(usuario=self.actor)
        respuesta = self.client.post(self.url, {'referencia': 'DUP-SYNTHETIC', 'motivo': MOTIVO, 'confirmar': 'on'})
        self.assertContains(respuesta, 'cuenta vinculada')
        self.assertEqual(self.solicitud.historial_estados.filter(estado_nuevo='CANCELLED').count(), 0)

    def test_admin_error_conserva_datos_y_no_hay_accion_masiva(self):
        from django.contrib import admin
        from django.test import RequestFactory
        respuesta = self.client.post(self.url, {'referencia': 'incorrecta', 'motivo': MOTIVO, 'confirmar': 'on'})
        self.assertContains(respuesta, MOTIVO)
        self.assertContains(respuesta, 'incorrecta')
        peticion = RequestFactory().get('/')
        peticion.user = self.actor
        acciones = admin.site._registry[SolicitudFinanciacionEducativa].get_actions(peticion)
        self.assertFalse(any('cancelar_duplicado' in nombre for nombre in acciones))
        self.comprobar_sin_cambio()

    def test_error_transicion_revierte_revocacion_y_correos(self):
        with patch('financiacion_educativa.services.cancelacion_duplicados.transicionar_solicitud', side_effect=ValidationError('fallo sintetico')):
            with self.assertRaises(ValidationError):
                self.cancelar()
        self.comprobar_sin_cambio()

    def test_otra_solicitud_misma_persona_pendiente_firma_permanece_intacta(self):
        datos = DatosObligacionesMixin()
        datos.configuracion = crear_configuracion_financiera()
        datos.institucion = self.institucion
        otra, foto, pagare, firma = datos.contrato('OTHER-SYNTHETIC', estado='PENDING_SIGNATURE')
        otra.correo = self.solicitud.correo
        otra.usuario = self.actor
        otra.save(update_fields=['correo', 'usuario'])
        for solicitud in (self.solicitud, otra):
            solicitud.tipo_documento_estudiante = 'CC'
            solicitud.numero_documento_estudiante = '199900001'
            solicitud.fecha_nacimiento_estudiante = date(1990, 1, 1)
            solicitud.save(update_fields=['tipo_documento_estudiante', 'numero_documento_estudiante', 'fecha_nacimiento_estudiante'])
        pagare.estado = 'SENT_FOR_SIGNATURE'
        pagare.archivo_firmado = ''
        pagare.hash_firmado_sha256 = ''
        pagare.tamano_firmado_bytes = None
        pagare.firmado_en = None
        pagare.save(update_fields=['estado', 'archivo_firmado', 'hash_firmado_sha256', 'tamano_firmado_bytes', 'firmado_en'])
        firma.estado = 'SENT'
        firma.firmado_en = None
        firma.save(update_fields=['estado', 'firmado_en'])
        correo_otro, _ = crear_correo_expediente_recibido(solicitud=otra)
        correo_previo = OutboxCorreoEducativo.objects.values().get(pk=correo_otro.pk)
        filas = [(modelo, objeto.pk, modelo.objects.values().get(pk=objeto.pk)) for modelo, objeto in (
            (SolicitudFinanciacionEducativa, otra), (type(foto), foto), (type(pagare), pagare), (type(firma), firma))]
        cuotas = list(foto.cuotas.values())
        self.cancelar()
        for modelo, pk, previo in filas:
            self.assertEqual(modelo.objects.values().get(pk=pk), previo)
        self.assertEqual(list(foto.cuotas.values()), cuotas)
        self.assertEqual(OutboxCorreoEducativo.objects.values().get(pk=correo_otro.pk), correo_previo)

    @override_settings(FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=True)
    def test_cancelada_no_admite_automatizacion_ni_recordatorios(self):
        from financiacion_educativa.services.cola_automatizacion import encolar_proceso_automatizacion
        from financiacion_educativa.services.recordatorios_solicitudes import programar_recordatorios_solicitudes
        self.cancelar()
        self.assertEqual(encolar_proceso_automatizacion(solicitud_id=self.solicitud.pk), (None, False))
        programar_recordatorios_solicitudes()
        self.assertEqual(self.solicitud.correos_outbox.count(), 1)
        self.assertFalse(self.solicitud.historial_estados.exclude(
            estado_nuevo__in=['PENDING_USER_REGISTRATION', 'CANCELLED']).exists())


@override_settings(**AISLAMIENTO)
class CancelacionDuplicadoPostgreSQLTests(DatosDuplicado, TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requiere PostgreSQL real para locks NOWAIT y conexiones independientes.')
        self.preparar()

    def ejecutar_hilo(self, funcion):
        close_old_connections()
        try:
            return funcion()
        finally:
            connections['default'].close()

    def comprobar_conexion_principal(self, conexion):
        self.assertIs(connection.connection, conexion)
        self.assertTrue(connection.is_usable())
        self.assertTrue(SolicitudFinanciacionEducativa.objects.filter(pk=self.solicitud.pk).exists())

    def test_dos_cancelaciones_generan_una_sola_transicion(self):
        inicio = Barrier(2)
        conexion = connection.connection

        def cancelar():
            inicio.wait(timeout=10)
            try:
                return self.cancelar().repetida
            except ValidationError as error:
                self.assertEqual(error.code, 'DUPLICATE_APPLICATION_BUSY')
                return 'BUSY'

        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(self.ejecutar_hilo, cancelar) for _ in range(2)]
            resultados = [f.result(timeout=20) for f in futuros]
        self.assertIn(False, resultados)
        self.assertEqual(self.solicitud.historial_estados.filter(estado_nuevo='CANCELLED').count(), 1)
        self.assertEqual(self.invitacion.eventos.filter(tipo='REVOKED').count(), 1)
        self.comprobar_conexion_principal(conexion)

    def test_asociacion_con_lock_invitacion_no_provoca_deadlock_ni_cierre_parcial(self):
        tomada, liberar = Event(), Event()
        usuario = get_user_model().objects.create_user('student', 'student@example.test', 'test-only')
        conexion = connection.connection

        def asociar():
            with transaction.atomic():
                InvitacionContinuacionSolicitud.objects.select_for_update().get(pk=self.invitacion.pk)
                tomada.set()
                if not liberar.wait(10):
                    raise RuntimeError('No se libero la barrera de asociacion.')
                return asociar_usuario_mediante_invitacion(invitacion_id=self.invitacion.pk, usuario=usuario).repetida

        with ThreadPoolExecutor(max_workers=1) as pool:
            futuro = pool.submit(self.ejecutar_hilo, asociar)
            try:
                self.assertTrue(tomada.wait(10))
                with self.assertRaises(ValidationError) as error:
                    self.cancelar()
                self.assertEqual(error.exception.code, 'DUPLICATE_APPLICATION_BUSY')
            finally:
                liberar.set()
            self.assertFalse(futuro.result(timeout=20))
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'PENDING_TERMS')
        self.assertEqual(self.solicitud.usuario_id, usuario.pk)
        self.assertFalse(self.solicitud.historial_estados.filter(estado_nuevo='CANCELLED').exists())
        self.comprobar_conexion_principal(conexion)

    def test_envio_en_reclamacion_bloquea_cancelacion_sin_tocar_smtp(self):
        tomada, liberar = Event(), Event()
        conexion = connection.connection

        def reclamar():
            with transaction.atomic():
                OutboxCorreoEducativo.objects.select_for_update().get(pk=self.correo.pk)
                tomada.set()
                if not liberar.wait(10):
                    raise RuntimeError('No se libero la barrera de correo.')
                return reclamar_correo_pendiente().pk

        with ThreadPoolExecutor(max_workers=1) as pool:
            futuro = pool.submit(self.ejecutar_hilo, reclamar)
            try:
                self.assertTrue(tomada.wait(10))
                with self.assertRaises(ValidationError) as error:
                    self.cancelar()
                self.assertEqual(error.exception.code, 'DUPLICATE_APPLICATION_BUSY')
            finally:
                liberar.set()
            self.assertEqual(futuro.result(timeout=20), self.correo.pk)
        with self.assertRaises(ValidationError) as error:
            self.cancelar()
        self.assertEqual(error.exception.code, 'DUPLICATE_DELIVERY_IN_PROGRESS')
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'PENDING_USER_REGISTRATION')
        self.comprobar_conexion_principal(conexion)

    def test_cancelacion_confirmada_no_es_reclamable_por_worker(self):
        self.cancelar()
        conexion = connection.connection
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertIsNone(pool.submit(self.ejecutar_hilo, reclamar_correo_pendiente).result(timeout=20))
        self.comprobar_conexion_principal(conexion)
