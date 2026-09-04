import smtplib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from financiacion_educativa.choices import EstadoOutboxCorreoEducativo
from financiacion_educativa.models import OutboxCorreoEducativo
from financiacion_educativa.services.outbox_correos import (
    EntregaCorreoAmbigua,
    EntregaCorreoNoIniciada,
    _finalizar,
    crear_correo_expediente_recibido,
    procesar_siguiente_correo,
    reclamar_correo_pendiente,
    recuperar_leases_outbox,
    reintentar_fallidos,
    resolver_ambiguos,
)
from financiacion_educativa.tests.factories import crear_solicitud


@override_settings(
    DEBUG=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    FINANCIACION_EDUCATIVA_REVIEW_NOTIFICATION_EMAILS=[
        'soporte@aprobado.com.co',
    ],
    FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_LEASE_SECONDS=60,
    FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_MAX_ATTEMPTS=3,
    FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_BASE_SECONDS=10,
    FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_MAX_SECONDS=30,
)
class OutboxCorreoEducativoTests(TestCase):
    def setUp(self):
        self.solicitud = crear_solicitud(referencia='OUTBOX-001')

    def _crear(self):
        with transaction.atomic():
            outbox, creado = crear_correo_expediente_recibido(
                solicitud=self.solicitud,
            )
        self.assertTrue(creado)
        return outbox

    def test_intencion_es_idempotente(self):
        primero = self._crear()
        with transaction.atomic():
            segundo, creado = crear_correo_expediente_recibido(
                solicitud=self.solicitud,
            )
        self.assertFalse(creado)
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)

    def test_intencion_exige_transaccion_explicita(self):
        conexion = mock.Mock(in_atomic_block=False)
        with mock.patch(
            'financiacion_educativa.services.outbox_correos.'
            'transaction.get_connection',
            return_value=conexion,
        ):
            with self.assertRaises(RuntimeError):
                crear_correo_expediente_recibido(solicitud=self.solicitud)

    def test_rollback_descarta_la_intencion(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                crear_correo_expediente_recibido(solicitud=self.solicitud)
                raise RuntimeError('rollback controlado')
        self.assertFalse(OutboxCorreoEducativo.objects.exists())

    def test_entrega_exitosa_con_cc_y_message_id_determinista(self):
        outbox = self._crear()
        resultado = procesar_siguiente_correo()

        outbox.refresh_from_db()
        self.assertEqual(resultado.estado, EstadoOutboxCorreoEducativo.SENT)
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.solicitud.correo])
        self.assertEqual(mail.outbox[0].cc, ['soporte@aprobado.com.co'])
        self.assertEqual(
            mail.outbox[0].extra_headers['Message-ID'],
            outbox.message_id,
        )
        self.assertEqual(outbox.message_id, self._message_id_recreado())

    def _message_id_recreado(self):
        outbox = OutboxCorreoEducativo.objects.get()
        return outbox.message_id

    @mock.patch('financiacion_educativa.services.outbox_correos._entregar')
    def test_error_temporal_aplica_backoff(self, entregar):
        entregar.side_effect = smtplib.SMTPDataError(451, b'temporary')
        outbox = self._crear()
        antes = timezone.now()

        resultado = procesar_siguiente_correo()

        outbox.refresh_from_db()
        self.assertEqual(resultado.estado, EstadoOutboxCorreoEducativo.RETRYING)
        self.assertEqual(outbox.codigo_ultimo_error, 'SMTP_TEMPORARY_ERROR')
        self.assertGreaterEqual(
            outbox.proxima_ejecucion_en,
            antes + timedelta(seconds=10),
        )

    @mock.patch('financiacion_educativa.services.outbox_correos._entregar')
    def test_error_permanente_termina_failed(self, entregar):
        entregar.side_effect = smtplib.SMTPDataError(550, b'rejected')
        outbox = self._crear()

        procesar_siguiente_correo()

        outbox.refresh_from_db()
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.FAILED)
        self.assertEqual(outbox.codigo_ultimo_error, 'SMTP_PERMANENT_ERROR')

    @mock.patch('financiacion_educativa.services.outbox_correos._entregar')
    def test_caida_antes_de_smtp_se_reintenta(self, entregar):
        entregar.side_effect = EntregaCorreoNoIniciada()
        outbox = self._crear()

        procesar_siguiente_correo()

        outbox.refresh_from_db()
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.RETRYING)
        self.assertEqual(outbox.codigo_ultimo_error, 'DELIVERY_NOT_STARTED')

    @mock.patch('financiacion_educativa.services.outbox_correos._entregar')
    def test_caida_despues_de_aceptacion_es_ambigua_y_no_reintenta(self, entregar):
        entregar.side_effect = EntregaCorreoAmbigua()
        outbox = self._crear()

        primer_resultado = procesar_siguiente_correo()
        segundo_resultado = procesar_siguiente_correo()

        outbox.refresh_from_db()
        self.assertEqual(
            primer_resultado.estado,
            EstadoOutboxCorreoEducativo.AMBIGUOUS,
        )
        self.assertFalse(segundo_resultado.procesado)
        self.assertEqual(entregar.call_count, 1)

    def test_solo_dueno_del_lease_puede_finalizar(self):
        outbox = self._crear()
        reclamado = reclamar_correo_pendiente()
        lease_valido = reclamado.lease_id

        _finalizar(
            outbox_id=outbox.pk,
            lease_id=uuid.uuid4(),
            estado=EstadoOutboxCorreoEducativo.SENT,
        )
        outbox.refresh_from_db()
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.SENDING)

        _finalizar(
            outbox_id=outbox.pk,
            lease_id=lease_valido,
            estado=EstadoOutboxCorreoEducativo.SENT,
        )
        outbox.refresh_from_db()
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.SENT)

    def test_lease_vencido_se_recupera_como_ambiguo(self):
        outbox = self._crear()
        reclamar_correo_pendiente()
        OutboxCorreoEducativo.objects.filter(pk=outbox.pk).update(
            lease_vence_en=timezone.now() - timedelta(seconds=1),
        )

        self.assertEqual(recuperar_leases_outbox(dry_run=True), 1)
        outbox.refresh_from_db()
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.SENDING)
        self.assertEqual(recuperar_leases_outbox(), 1)
        outbox.refresh_from_db()
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.AMBIGUOUS)

    def test_recuperacion_failed_y_ambiguo_es_explicita(self):
        outbox = self._crear()
        OutboxCorreoEducativo.objects.filter(pk=outbox.pk).update(
            estado=EstadoOutboxCorreoEducativo.FAILED,
        )
        self.assertEqual(reintentar_fallidos(dry_run=True), 1)
        outbox.refresh_from_db()
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.FAILED)
        self.assertEqual(reintentar_fallidos(), 1)
        outbox.refresh_from_db()
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.RETRYING)

        OutboxCorreoEducativo.objects.filter(pk=outbox.pk).update(
            estado=EstadoOutboxCorreoEducativo.AMBIGUOUS,
        )
        self.assertEqual(
            resolver_ambiguos(resolucion='FAILED', dry_run=True),
            1,
        )
        self.assertEqual(resolver_ambiguos(resolucion='FAILED'), 1)
        outbox.refresh_from_db()
        self.assertEqual(outbox.estado, EstadoOutboxCorreoEducativo.FAILED)

    def test_diagnostico_no_muestra_destinatarios_ni_contexto(self):
        self._crear()
        salida = StringIO()

        call_command('diagnosticar_outbox_educativo', stdout=salida)

        texto = salida.getvalue()
        self.assertNotIn(self.solicitud.correo, texto)
        self.assertNotIn(self.solicitud.referencia_externa, texto)
        self.assertIn('PENDING: 1', texto)

    @mock.patch('financiacion_educativa.services.outbox_correos._entregar')
    def test_log_de_fallo_no_expone_destinatario_ni_contexto(self, entregar):
        entregar.side_effect = EntregaCorreoAmbigua()
        self._crear()

        with self.assertLogs(
            'financiacion_educativa.services.outbox_correos',
            level='WARNING',
        ) as capturados:
            procesar_siguiente_correo()

        texto = ' '.join(capturados.output)
        self.assertNotIn(self.solicitud.correo, texto)
        self.assertNotIn(self.solicitud.referencia_externa, texto)


class ConcurrenciaOutboxPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def test_dos_workers_no_reclaman_el_mismo_correo(self):
        if connection.vendor != 'postgresql':
            self.skipTest('SKIPPED: requiere PostgreSQL real con skip_locked.')
        self.assertTrue(connection.features.has_select_for_update_skip_locked)
        solicitud = crear_solicitud(referencia='OUTBOX-PG-CONCURRENCY')
        with transaction.atomic():
            crear_correo_expediente_recibido(solicitud=solicitud)

        def reclamar():
            close_old_connections()
            try:
                registro = reclamar_correo_pendiente()
                return str(registro.pk) if registro else None
            except Exception as error:
                return error
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: reclamar(), range(2)))

        errores = [resultado for resultado in resultados if isinstance(resultado, Exception)]
        self.assertEqual(errores, [])
        self.assertEqual(sum(resultado is not None for resultado in resultados), 1)
