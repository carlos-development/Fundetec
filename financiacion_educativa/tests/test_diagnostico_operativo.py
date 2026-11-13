from datetime import timedelta
from io import StringIO
import json
import subprocess
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command, CommandError
from django.db import connection, DatabaseError
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from financiacion_educativa.models import EventoWebhookFirmaEducativa, OutboxCorreoEducativo, ProcesoFirmaEducativa
from financiacion_educativa.services import diagnostico_operativo as monitor
from financiacion_educativa.tests.factories import crear_solicitud
from financiacion_educativa.tests.test_dashboard_obligaciones import DatosObligacionesMixin
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


def crear_aviso(solicitud, **cambios):
    return OutboxCorreoEducativo.objects.create(solicitud=solicitud, tipo_evento='DOSSIER_RECEIVED',
        codigo_mensaje='DOSSIER_RECEIVED', clave_idempotencia=uuid4().hex, evento_logico='a' * 64,
        destinatarios=['destino@example.test'], contexto={'private': 'payload-sensible-de-prueba'},
        message_id=uuid4().hex + '@example.test', **cambios)


class ServiciosOperativosTests(SimpleTestCase):
    def test_servicio_activo_y_timer_activo_son_saludables(self):
        salida = subprocess.CompletedProcess([], 0, 'LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\n', '')
        with patch.object(monitor.subprocess, 'run', return_value=salida) as ejecutar:
            servicios, alertas = monitor.comprobar_servicios(['fundetec-staging.service', 'fundetec-educational-monitor.timer'])
        self.assertEqual(alertas, [])
        self.assertTrue(all(s['activa'] for s in servicios))
        self.assertEqual(ejecutar.call_count, 2)
        self.assertEqual(ejecutar.call_args.kwargs['timeout'], 5)
        self.assertNotIn('shell', ejecutar.call_args.kwargs)

    def test_caido_no_encontrado_timeout_y_respuesta_vacia_no_son_ok(self):
        respuestas = [subprocess.CompletedProcess([], 0, 'LoadState=loaded\nActiveState=failed\n', ''),
            subprocess.CompletedProcess([], 0, 'LoadState=not-found\nActiveState=inactive\n', 'dato privado'),
            subprocess.TimeoutExpired('systemctl', 5), subprocess.CompletedProcess([], 0, '', '')]
        for respuesta in respuestas:
            with self.subTest(respuesta=type(respuesta).__name__):
                kwargs = {'side_effect': respuesta} if isinstance(respuesta, Exception) else {'return_value': respuesta}
                with patch.object(monitor.subprocess, 'run', **kwargs):
                    _, alertas = monitor.comprobar_servicios(['fundetec-staging.service'])
                self.assertEqual(len(alertas), 1)
                self.assertEqual(alertas[0]['severidad'], 'CRITICAL')
                self.assertNotIn('dato privado', json.dumps(alertas))

    def test_sin_unidades_no_certifica_servicios_y_rechaza_inyeccion(self):
        self.assertEqual(monitor.comprobar_servicios([])[1][0]['codigo'], 'SERVICE_SCOPE_MISSING')
        for unidad in ('--all', '/etc/secret.service', 'unidad;rm.service', 'archivo.env'):
            with self.assertRaises(ValueError), patch.object(monitor.subprocess, 'run') as ejecutar:
                monitor.comprobar_servicios([unidad])
            ejecutar.assert_not_called()

    def test_plantillas_sin_recuperacion_con_entorno_y_protecciones(self):
        from pathlib import Path
        raiz = Path(__file__).resolve().parents[2] / 'deploy/systemd'
        servicio = (raiz / 'fundetec-educational-monitor.service.template').read_text()
        for requerido in ('Type=oneshot', 'User=@@SERVICE_USER@@', 'Group=@@SERVICE_GROUP@@',
                'WorkingDirectory=@@APP_DIR@@', 'EnvironmentFile=@@ENV_FILE@@',
                'manage.py diagnosticar_operacion_educativa', 'NoNewPrivileges=true',
                'PrivateTmp=true', 'ProtectSystem=strict', 'ProtectHome=true'):
            self.assertIn(requerido, servicio)
        self.assertNotIn('Restart=', servicio)
        timer = (raiz / 'fundetec-educational-monitor.timer').read_text()
        self.assertIn('OnUnitActiveSec=5min', timer)
        self.assertIn('Unit=fundetec-educational-monitor.service', timer)


@override_settings(**AISLAMIENTO, FINANCIACION_EDUCATIVA_LIBRO_ENABLED=False,
    FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED=False)
class ObservacionesOperativasTests(DatosObligacionesMixin, TestCase):
    def setUp(self):
        self.preparar_datos()
        self.solicitud, _, self.artefacto, self.firma = self.contrato()
        self.ahora = timezone.now()

    def codigos(self):
        return {a['codigo']: a for a in monitor.observar_base(ahora=self.ahora)['alertas']}

    def test_umbral_pendiente_inclusive_y_recordatorio_futuro_no_alerta(self):
        aviso = crear_aviso(self.solicitud, proxima_ejecucion_en=self.ahora - timedelta(minutes=15))
        crear_aviso(self.solicitud, proxima_ejecucion_en=self.ahora + timedelta(hours=1))
        alertas = self.codigos()
        self.assertEqual(alertas['OUTBOX_OVERDUE']['cantidad'], 1)
        self.assertEqual(alertas['OUTBOX_OVERDUE']['antiguedad_maxima_segundos'], 900)
        OutboxCorreoEducativo.objects.filter(pk=aviso.pk).update(proxima_ejecucion_en=self.ahora - timedelta(seconds=899))
        self.assertNotIn('OUTBOX_OVERDUE', self.codigos())

    def test_reintento_vencido_se_observa_sin_ejecutarlo(self):
        aviso = crear_aviso(self.solicitud, estado='RETRYING', proxima_ejecucion_en=self.ahora - timedelta(hours=1), intentos=1)
        previo = OutboxCorreoEducativo.objects.values().get(pk=aviso.pk)
        with patch('requests.sessions.Session.request') as http, patch('smtplib.SMTP') as smtp:
            self.assertIn('OUTBOX_OVERDUE', self.codigos())
        http.assert_not_called()
        smtp.assert_not_called()
        self.assertEqual(OutboxCorreoEducativo.objects.values().get(pk=aviso.pk), previo)

    def test_failed_y_ambiguous_separados_sin_imprimir_payload(self):
        for estado in ('FAILED', 'AMBIGUOUS'):
            crear_aviso(self.solicitud, estado=estado, codigo_ultimo_error='texto-privado-no-imprimir')
        datos = monitor.observar_base(ahora=self.ahora)
        self.assertEqual(set(self.codigos()), {'OUTBOX_FAILED', 'OUTBOX_AMBIGUOUS'})
        for privado in ('destino@example.test', 'payload-sensible-de-prueba', 'texto-privado-no-imprimir', self.solicitud.correo):
            self.assertNotIn(privado, json.dumps(datos))

    def test_leases_vencidos_pero_no_lease_vigente(self):
        for vence in (self.ahora - timedelta(seconds=1), self.ahora, self.ahora + timedelta(seconds=1)):
            crear_aviso(self.solicitud, estado='SENDING', lease_vence_en=vence, lease_id=uuid4())
        self.assertEqual(self.codigos()['OUTBOX_LEASE_EXPIRED']['cantidad'], 2)

    def test_firma_pendiente_y_envio_atascado_con_fecha_fallback(self):
        ProcesoFirmaEducativa.objects.filter(pk=self.firma.pk).update(estado='PENDING', creado_en=self.ahora - timedelta(minutes=15))
        self.assertIn('SIGNATURE_QUEUE_OVERDUE', self.codigos())
        ProcesoFirmaEducativa.objects.filter(pk=self.firma.pk).update(estado='SENDING', envio_iniciado_en=None,
            actualizado_en=self.ahora - timedelta(minutes=10))
        self.assertIn('SIGNATURE_SEND_STALLED', self.codigos())

    def test_firma_ambigua_y_fallida_no_se_confunden_ni_reintentan(self):
        ProcesoFirmaEducativa.objects.filter(pk=self.firma.pk).update(estado='FAILED', codigo_ultimo_error='SIGNATURE_SEND_AMBIGUOUS')
        self.assertEqual(set(self.codigos()), {'SIGNATURE_AMBIGUOUS'})
        ProcesoFirmaEducativa.objects.filter(pk=self.firma.pk).update(codigo_ultimo_error='SIGNATURE_HTTP_400')
        self.assertEqual(set(self.codigos()), {'SIGNATURE_FAILED'})

    def test_espera_firmante_72h_no_implica_error_de_proveedor(self):
        ProcesoFirmaEducativa.objects.filter(pk=self.firma.pk).update(estado='SENT', enviado_en=self.ahora - timedelta(hours=72))
        self.assertEqual(set(self.codigos()), {'SIGNATURE_WAIT_OVERDUE'})
        ProcesoFirmaEducativa.objects.filter(pk=self.firma.pk).update(enviado_en=self.ahora - timedelta(hours=71))
        self.assertEqual(self.codigos(), {})

    def test_webhook_pendiente_o_error_supera_15min_sin_reprocesar(self):
        evento = EventoWebhookFirmaEducativa.objects.create(payload_hash='d' * 64,
            tipo_evento='doc_signed', proceso=self.firma, estado='RETRYABLE_ERROR')
        EventoWebhookFirmaEducativa.objects.filter(pk=evento.pk).update(recibido_en=self.ahora - timedelta(minutes=15))
        self.assertEqual(set(self.codigos()), {'SIGNATURE_WEBHOOK_OVERDUE'})
        previo = EventoWebhookFirmaEducativa.objects.values().get(pk=evento.pk)
        self.codigos()
        self.assertEqual(EventoWebhookFirmaEducativa.objects.values().get(pk=evento.pk), previo)
        EventoWebhookFirmaEducativa.objects.filter(pk=evento.pk).update(estado='PROCESSED')
        self.assertEqual(self.codigos(), {})

    def test_historico_no_vigente_y_firmas_terminadas_no_alertan(self):
        for estado in ('SIGNED', 'REFUSED', 'CANCELLED', 'EXPIRED'):
            ProcesoFirmaEducativa.objects.filter(pk=self.firma.pk).update(estado=estado, actualizado_en=self.ahora - timedelta(days=10))
            self.assertEqual(self.codigos(), {})
        self.artefacto.vigente = False
        self.artefacto.save(update_fields=['vigente'])
        ProcesoFirmaEducativa.objects.filter(pk=self.firma.pk).update(estado='FAILED')
        self.assertEqual(self.codigos(), {})

    def test_sent_y_superseded_no_son_pendientes_y_flags_solo_se_leen(self):
        for estado in ('SENT', 'SUPERSEDED'):
            crear_aviso(self.solicitud, estado=estado, proxima_ejecucion_en=self.ahora - timedelta(days=30))
        datos = monitor.observar_base(ahora=self.ahora)
        self.assertEqual(datos['alertas'], [])
        self.assertEqual(datos['flags_observados'], {'libro': False, 'avisos_breb': False})
        self.assertTrue(datos['sent_no_acredita_entrega_en_buzon_ni_primer_envio_real'])


@override_settings(**AISLAMIENTO)
class ComandoDiagnosticoTests(TransactionTestCase):
    def test_comando_lectura_postgresql_no_escribe_ni_recupera(self):
        aviso = crear_aviso(crear_solicitud(), estado='AMBIGUOUS')
        previo = OutboxCorreoEducativo.objects.values().get(pk=aviso.pk)
        consultas = []
        def inspeccionar(execute, sql, params, many, context):
            consultas.append(sql)
            self.assertNotIn(sql.lstrip().split(' ')[0].upper(), ['INSERT', 'UPDATE', 'DELETE'])
            return execute(sql, params, many, context)
        salida = StringIO()
        with connection.execute_wrapper(inspeccionar), patch.object(monitor.subprocess, 'run', return_value=
                subprocess.CompletedProcess([], 0, 'LoadState=loaded\nActiveState=active\n', '')):
            with self.assertRaises(CommandError) as error:
                call_command('diagnosticar_operacion_educativa', servicio=['fundetec-staging.service'], stdout=salida)
        self.assertEqual(error.exception.returncode, 2)
        self.assertEqual(json.loads(salida.getvalue())['resultado'], 'ALERT')
        if connection.vendor == 'postgresql':
            self.assertTrue(any('REPEATABLE READ, READ ONLY' in c for c in consultas))
        self.assertEqual(OutboxCorreoEducativo.objects.values().get(pk=aviso.pk), previo)

    def test_sin_alertas_sale_correctamente_con_servicios_comprobados(self):
        salida = StringIO()
        with patch.object(monitor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0,
                'LoadState=loaded\nActiveState=active\n', '')):
            call_command('diagnosticar_operacion_educativa', servicio=['fundetec-staging.service'], stdout=salida)
        self.assertEqual(json.loads(salida.getvalue())['resultado'], 'OK')

    def test_fallo_bd_sale_tres_sin_revelar_error(self):
        salida = StringIO()
        with patch('financiacion_educativa.management.commands.diagnosticar_operacion_educativa.observar_base',
                side_effect=DatabaseError('credencial-no-imprimir')):
            with self.assertRaises(CommandError) as error:
                call_command('diagnosticar_operacion_educativa', stdout=salida)
        self.assertEqual(error.exception.returncode, 3)
        self.assertNotIn('credencial-no-imprimir', salida.getvalue())
        self.assertTrue(json.loads(salida.getvalue())['diagnostico_incompleto'])

    def test_rechaza_umbral_invalido_antes_de_consultar_servicios(self):
        with patch.object(monitor.subprocess, 'run') as ejecutar, self.assertRaises(CommandError):
            call_command('diagnosticar_operacion_educativa', aviso_minutos=0)
        ejecutar.assert_not_called()
