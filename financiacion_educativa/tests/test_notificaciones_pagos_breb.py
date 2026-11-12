from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
import os
from pathlib import Path
import subprocess
from threading import Barrier, Event
from time import monotonic
from unittest import mock, skipUnless
from uuid import uuid4

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import close_old_connections, connection, transaction, IntegrityError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from financiacion_educativa.choices import EstadoOutboxCorreoEducativo as Estado
from financiacion_educativa.models import OutboxCorreoEducativo, DestinatarioNotificacionInstitucionalEducativa
from financiacion_educativa.models_recaudos import ReporteRecaudoEducativo, ConciliacionRecaudoEducativo, AplicacionCuotaEducativa
from financiacion_educativa.services import recaudos, aplicacion_cuotas, outbox_correos as outbox
from financiacion_educativa.services.notificaciones_pagos import LeasePagoPerdido, NotificacionesPagoDeshabilitadas
from financiacion_educativa.services.participantes import DatosParticipante, registrar_estudiante_menor_con_tutor
from financiacion_educativa.tests.test_ingreso_operaciones import DatosIngresoOperaciones
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


AISLAR_PAGOS = {**AISLAMIENTO, 'DEBUG': True,
    'FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED': True,
    'EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS': [], 'EDUCATION_EMAIL_LOGO_URL': '',
    'FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_MAX_ATTEMPTS': 3,
    'FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_BASE_SECONDS': 1,
    'FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_MAX_SECONDS': 2}


class DatosNotificacionesPagos(DatosIngresoOperaciones):
    def setUp(self):
        super().setUp()
        self.solicitud.fecha_nacimiento_estudiante = date(1995, 1, 1)
        self.solicitud.save(update_fields=['fecha_nacimiento_estudiante'])
        self.email = EmailAddress.objects.create(user=self.pagador, email=self.pagador.email, verified=True, primary=True)

    def reporte_cuota(self, **cambios):
        return self.reportar(monto=self.cuota.valor_cuota, **cambios)

    def confirmar(self, reporte):
        self.escanear(reporte.comprobante_id)
        return recaudos.confirmar_recaudo(**self.datos_confirmacion(reporte))

    def aplicar_ingreso(self, ingreso):
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        return aplicacion_cuotas.aplicar_cuota(reporte_id=ingreso.reporte_id, cuota_id=cuota.pk,
            actor=self.actor, vista_previa=token, confirmar_cuota=True)

    def correo(self, evento):
        return OutboxCorreoEducativo.objects.get(codigo_mensaje=evento)

    def menor(self, verificado=True):
        tutor = get_user_model().objects.create_user('tutor-sintetico', 'tutor@example.test', 'test-only')
        EmailAddress.objects.create(user=tutor, email=tutor.email, verified=verificado)
        _, participante = registrar_estudiante_menor_con_tutor(solicitud=self.solicitud,
            estudiante=DatosParticipante(nombres='Estudiante', apellidos='Sintetico', tipo_documento='TI',
                numero_documento='SYNTH-STUDENT', fecha_nacimiento=date(2014, 1, 1), fecha_nacimiento_confirmada=True,
                correo=self.pagador.email, usuario=self.pagador),
            tutor=DatosParticipante(nombres='Tutor', apellidos='Sintetico', tipo_documento='CC',
                numero_documento='SYNTH-GUARDIAN', fecha_nacimiento=date(1980, 1, 1), fecha_nacimiento_confirmada=True,
                correo=tutor.email, usuario=tutor))
        return tutor, participante


@override_settings(**AISLAR_PAGOS)
class NotificacionesPagosBrebTests(DatosNotificacionesPagos, TestCase):
    def test_tres_eventos_con_contenido_preciso_y_sin_datos_privados(self):
        reporte, _ = self.reporte_cuota()
        self.assertEqual(self.correo('BREB_REPORT_RECEIVED').reporte_recaudo_id, reporte.pk)
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SENT)
        self.assertIn('pendiente de revisi\u00f3n', mail.outbox[-1].body)
        ingreso, _ = self.confirmar(reporte)
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SENT)
        self.assertIn('Todav\u00eda no se ha aplicado a una cuota', mail.outbox[-1].body)
        aplicacion = self.aplicar_ingreso(ingreso)
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SENT)
        self.assertIn(f'Cuota: {aplicacion.cuota.numero}', mail.outbox[-1].body)
        self.assertIn('Importe aplicado:', mail.outbox[-1].body)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 3)
        for mensaje in mail.outbox:
            self.assertEqual(mensaje.to, [self.pagador.email])
            self.assertEqual(mensaje.cc, [])
            self.assertEqual(mensaje.bcc, [])
            self.assertEqual(mensaje.attachments, [])
            contenido = mensaje.body + str(mensaje.alternatives[0].content)
            for privado in (self.banco.archivo.name, str(self.banco.pk), ingreso.referencia_verificada,
                            self.actor.username, '/operaciones/', '/soporte/', 'http'):
                self.assertNotIn(privado, contenido)

    def test_operaciones_no_simula_reporte_del_estudiante(self):
        ingreso, _ = self.registrar()
        self.assertEqual(ingreso.reporte.origen, 'OPERATIONS')
        self.assertFalse(OutboxCorreoEducativo.objects.filter(codigo_mensaje='BREB_REPORT_RECEIVED').exists())
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SENT)
        self.assertNotIn('Recibimos tu reporte', mail.outbox[0].body)
        self.aplicar_ingreso(ingreso)
        outbox.procesar_siguiente_correo()
        self.assertEqual(len(mail.outbox), 2)

    def test_replays_no_duplican_eventos_ni_reenvian_historicos(self):
        clave = uuid4()
        reporte, _ = self.reporte_cuota(clave=clave)
        repetido, replay = self.reporte_cuota(clave=clave)
        self.assertTrue(replay)
        self.assertEqual(reporte.pk, repetido.pk)
        self.escanear(reporte.comprobante_id)
        datos = self.datos_confirmacion(reporte)
        ingreso, _ = recaudos.confirmar_recaudo(**datos)
        self.assertTrue(recaudos.confirmar_recaudo(**datos)[1])
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        parametros = dict(reporte_id=reporte.pk, cuota_id=cuota.pk, actor=self.actor, vista_previa=token, confirmar_cuota=True)
        primera = aplicacion_cuotas.aplicar_cuota(**parametros)
        self.assertEqual(aplicacion_cuotas.aplicar_cuota(**parametros).pk, primera.pk)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 3)

    def test_registro_operativo_duplicado_por_nueva_clave_no_duplica_aviso(self):
        ingreso, _ = self.registrar()
        repetido, replay = self.registrar(clave=uuid4())
        self.assertTrue(replay)
        self.assertEqual(ingreso.pk, repetido.pk)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)

    def test_rollback_descarta_reporte_y_aviso(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.reporte_cuota()
            self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)
            raise RuntimeError('synthetic rollback')
        self.assertFalse(ReporteRecaudoEducativo.objects.exists())
        self.assertFalse(OutboxCorreoEducativo.objects.exists())

    def test_rollback_descarta_conciliacion_y_su_aviso(self):
        reporte, _ = self.reporte_cuota()
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.confirmar(reporte)
            raise RuntimeError('synthetic rollback')
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())
        self.assertFalse(OutboxCorreoEducativo.objects.filter(codigo_mensaje='BREB_INCOME_CONFIRMED').exists())
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)

    def test_rollback_descarta_aplicacion_y_su_aviso(self):
        ingreso, _ = self.registrar()
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.aplicar_ingreso(ingreso)
            raise RuntimeError('synthetic rollback')
        self.assertFalse(AplicacionCuotaEducativa.objects.exists())
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)

    def test_fallo_al_encolar_revierte_transicion_no_solo_correo(self):
        with mock.patch('financiacion_educativa.services.notificaciones_pagos.registrar_aviso_pago', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.registrar()
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())
        self.assertFalse(ReporteRecaudoEducativo.objects.exists())

    def test_avisos_superados_no_se_envian_y_no_se_recuperan_como_failed(self):
        reporte, _ = self.reporte_cuota()
        ingreso, _ = self.confirmar(reporte)
        self.aplicar_ingreso(ingreso)
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SUPERSEDED)
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SUPERSEDED)
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SENT)
        self.assertFalse(outbox.procesar_siguiente_correo().procesado)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(outbox.reintentar_fallidos(solicitud_id=self.solicitud.pk), 0)
        self.assertIsNone(self.correo('BREB_INCOME_CONFIRMED').enviada_en)

    def test_reporte_rechazado_sustituye_aviso_pendiente(self):
        reporte, _ = self.reporte_cuota()
        recaudos.rechazar_reporte(reporte_id=reporte.pk, actor=self.actor, motivo='Synthetic unconfirmed receipt')
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SUPERSEDED)
        self.assertEqual(len(mail.outbox), 0)

    def test_flag_false_no_reclama_pagos_pero_conserva_outbox_anterior(self):
        self.registrar()
        with self.settings(FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED=False):
            self.assertFalse(outbox.procesar_siguiente_correo().procesado)
            self.assertEqual(self.correo('BREB_INCOME_CONFIRMED').intentos, 0)
            with transaction.atomic():
                legado, _ = outbox.crear_correo_expediente_recibido(solicitud=self.solicitud)
            self.assertEqual(outbox.procesar_siguiente_correo().outbox_id, legado.pk)
        self.assertEqual(len(mail.outbox), 1)

    def test_desactivar_despues_de_reclamar_impide_smtp(self):
        self.registrar()
        correo = outbox.reclamar_correo_pendiente()
        with self.settings(FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED=False):
            with self.assertRaises(NotificacionesPagoDeshabilitadas):
                outbox._entregar(correo, correo.lease_id)
        self.assertEqual(len(mail.outbox), 0)

    def test_lease_ajeno_o_vencido_no_envia(self):
        self.registrar()
        correo = outbox.reclamar_correo_pendiente()
        with self.assertRaises(LeasePagoPerdido):
            outbox._entregar(correo, uuid4())
        OutboxCorreoEducativo.objects.filter(pk=correo.pk).update(lease_vence_en=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(LeasePagoPerdido):
            outbox._entregar(correo, correo.lease_id)
        self.assertEqual(len(mail.outbox), 0)

    def test_error_transitorio_reintenta_mismo_evento_y_message_id(self):
        self.registrar()
        original = self.correo('BREB_INCOME_CONFIRMED')
        with mock.patch('django.core.mail.message.EmailMultiAlternatives.send', side_effect=ConnectionRefusedError):
            self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.RETRYING)
        OutboxCorreoEducativo.objects.filter(pk=original.pk).update(proxima_ejecucion_en=timezone.now())
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SENT)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)
        self.assertEqual(mail.outbox[0].extra_headers['Message-ID'], original.message_id)

    def test_resultado_smtp_ambiguo_no_reenvia(self):
        self.registrar()
        with mock.patch('django.core.mail.message.EmailMultiAlternatives.send', side_effect=TimeoutError):
            self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.AMBIGUOUS)
        self.assertFalse(outbox.procesar_siguiente_correo().procesado)

    def test_menor_notifica_solo_tutor_verificado(self):
        tutor, _ = self.menor()
        reporte, _ = self.reporte_cuota()
        outbox.procesar_siguiente_correo()
        ingreso, _ = self.confirmar(reporte)
        outbox.procesar_siguiente_correo()
        self.aplicar_ingreso(ingreso)
        outbox.procesar_siguiente_correo()
        self.assertEqual([m.to for m in mail.outbox], [[tutor.email]] * 3)
        self.assertNotEqual(tutor.email, self.pagador.email)

    def test_tutor_sin_verificacion_no_recae_en_estudiante(self):
        self.menor(verificado=False)
        ingreso, _ = self.registrar()
        correo = self.correo('BREB_INCOME_CONFIRMED')
        self.assertEqual(correo.estado, Estado.FAILED)
        self.assertEqual(correo.codigo_ultimo_error, 'PAYMENT_RECIPIENT_UNVERIFIED')
        self.assertEqual(correo.destinatarios, [])
        self.assertFalse(outbox.procesar_siguiente_correo().procesado)
        self.assertTrue(ConciliacionRecaudoEducativo.objects.filter(pk=ingreso.pk).exists())

    def test_adulto_no_verificado_no_bloquea_pago_y_recuperacion_es_explicita(self):
        self.email.verified = False
        self.email.save(update_fields=['verified'])
        ingreso, _ = self.registrar()
        self.aplicar_ingreso(ingreso)
        self.assertEqual(OutboxCorreoEducativo.objects.filter(estado=Estado.FAILED).count(), 2)
        self.email.verified = True
        self.email.save(update_fields=['verified'])
        self.assertFalse(outbox.procesar_siguiente_correo().procesado)
        self.assertEqual(outbox.reintentar_fallidos(solicitud_id=self.solicitud.pk), 2)
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SUPERSEDED)
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SENT)
        self.assertEqual(len(mail.outbox), 1)

    def test_recuperacion_fija_destinatario_antes_de_un_smtp_ambiguo(self):
        self.email.verified = False
        self.email.save(update_fields=['verified'])
        self.registrar()
        self.assertEqual(outbox.reintentar_fallidos(solicitud_id=self.solicitud.pk), 0)
        self.email.verified = True
        self.email.save(update_fields=['verified'])
        self.assertEqual(outbox.reintentar_fallidos(solicitud_id=self.solicitud.pk), 1)
        with mock.patch('django.core.mail.message.EmailMultiAlternatives.send', side_effect=TimeoutError):
            self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.AMBIGUOUS)
        self.assertEqual(self.correo('BREB_INCOME_CONFIRMED').destinatarios, [self.pagador.email])

    def test_snapshot_no_se_redirige_si_cambia_la_cuenta(self):
        self.registrar()
        ajeno = get_user_model().objects.create_user('otro', 'other@example.test', 'test-only')
        EmailAddress.objects.create(user=ajeno, email=ajeno.email, verified=True)
        self.solicitud.usuario, self.solicitud.correo = ajeno, ajeno.email
        self.solicitud.save(update_fields=['usuario', 'correo'])
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.FAILED)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.correo('BREB_INCOME_CONFIRMED').destinatarios, [self.pagador.email])

    @override_settings(EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=['audit@example.test'])
    def test_copia_auditoria_sin_copia_institucional_y_flag_la_bloquea(self):
        DestinatarioNotificacionInstitucionalEducativa.objects.create(institucion=self.institucion, correo='institution@example.test')
        self.registrar()
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SENT)
        copia = OutboxCorreoEducativo.objects.get(codigo_mensaje='AUDIT_COPY')
        self.assertEqual(copia.contexto, {})
        self.assertFalse(OutboxCorreoEducativo.objects.filter(codigo_mensaje='INSTITUTIONAL_INITIAL_NOTIFICATION').exists())
        with self.settings(FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED=False):
            self.assertFalse(outbox.procesar_siguiente_correo().procesado)
        self.assertEqual(outbox.procesar_siguiente_correo().estado, Estado.SENT)
        self.assertEqual(mail.outbox[1].to, ['audit@example.test'])
        self.assertNotIn(self.pagador.email, mail.outbox[1].body)

    def test_eventos_no_necesitan_smtp_en_transicion(self):
        with mock.patch('django.core.mail.message.EmailMultiAlternatives.send', side_effect=AssertionError('SMTP during transition')):
            ingreso, _ = self.registrar()
            self.aplicar_ingreso(ingreso)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 2)

    @override_settings(EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=['audit@example.test'])
    def test_tres_eventos_tienen_una_copia_auditoria_cada_uno(self):
        reporte, _ = self.reporte_cuota()
        outbox.procesar_siguiente_correo()
        outbox.procesar_siguiente_correo()
        ingreso, _ = self.confirmar(reporte)
        outbox.procesar_siguiente_correo()
        outbox.procesar_siguiente_correo()
        self.aplicar_ingreso(ingreso)
        outbox.procesar_siguiente_correo()
        outbox.procesar_siguiente_correo()
        self.assertEqual(len(mail.outbox), 6)
        self.assertEqual(OutboxCorreoEducativo.objects.filter(codigo_mensaje='AUDIT_COPY').count(), 3)
        self.assertFalse(OutboxCorreoEducativo.objects.filter(codigo_mensaje='INSTITUTIONAL_INITIAL_NOTIFICATION').exists())

    def test_rollback_registro_operativo_descarta_evento(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.registrar()
            raise RuntimeError('synthetic rollback')
        self.assertFalse(OutboxCorreoEducativo.objects.exists())
        self.assertFalse(ConciliacionRecaudoEducativo.objects.exists())

    def test_cuenta_inactiva_correo_no_coincidente_y_edad_desconocida_bloquean_solo_aviso(self):
        for campo, valor in (('correo', 'unlinked@example.test'), ('fecha_nacimiento_estudiante', None)):
            with self.subTest(campo=campo):
                original = getattr(self.solicitud, campo)
                setattr(self.solicitud, campo, valor)
                self.solicitud.save(update_fields=[campo])
                ingreso, _ = self.registrar(clave=uuid4(), referencia='BANK-' + uuid4().hex)
                self.assertEqual(ingreso.reporte.correos_outbox.get().estado, Estado.FAILED)
                setattr(self.solicitud, campo, original)
                self.solicitud.save(update_fields=[campo])
        self.pagador.is_active = False
        self.pagador.save(update_fields=['is_active'])
        ingreso, _ = self.registrar(clave=uuid4(), referencia='BANK-' + uuid4().hex)
        self.assertEqual(ingreso.reporte.correos_outbox.get().estado, Estado.FAILED)

    def test_tutor_de_otra_solicitud_no_se_usa_como_destinatario(self):
        from financiacion_educativa.tests.factories import crear_solicitud
        tutor, participante = self.menor()
        otra = crear_solicitud(institucion=self.otra, referencia='OTRA-SINTETICA')
        self.solicitud.fecha_nacimiento_estudiante = date(2014, 1, 1)
        self.solicitud.save(update_fields=['fecha_nacimiento_estudiante'])
        participante.solicitud = otra
        # Controlled fixture inconsistency must fail closed, not leak to another institution.
        participante.save(update_fields=['solicitud'])
        ingreso, _ = self.registrar()
        self.assertEqual(ingreso.reporte.correos_outbox.get().estado, Estado.FAILED)
        self.assertFalse(outbox.procesar_siguiente_correo().procesado)

    def test_constraint_impide_evento_sin_reporte_o_tipo_incoherente(self):
        self.registrar()
        correo = self.correo('BREB_INCOME_CONFIRMED')
        for cambio in ({'reporte_recaudo': None}, {'tipo_evento': 'BREB_PAYMENT_APPLIED'},
                       {'contexto': {'bank_reference': 'not-permitted'}}, {'destinatarios_copia': ['someone@example.test']}):
            with self.subTest(cambio=cambio), self.assertRaises(IntegrityError), transaction.atomic():
                OutboxCorreoEducativo.objects.filter(pk=correo.pk).update(**cambio)

    @skipUnless(os.environ.get('EDU_BREB_EMAIL_PREVIEWS'), 'Muestras requieren directorio local explicito.')
    def test_muestras_sinteticas_html_txt_y_render_sin_red(self):
        reporte, _ = self.reporte_cuota()
        outbox.procesar_siguiente_correo()
        ingreso, _ = self.confirmar(reporte)
        outbox.procesar_siguiente_correo()
        self.aplicar_ingreso(ingreso)
        outbox.procesar_siguiente_correo()
        ruta = Path(os.environ['EDU_BREB_EMAIL_PREVIEWS'])
        ruta.mkdir(parents=True, exist_ok=True)
        nombres = ['01-reporte-recibido', '02-ingreso-confirmado', '03-pago-aplicado']
        for nombre, mensaje in zip(nombres, mail.outbox):
            html = '\n'.join(line.rstrip() for line in mensaje.alternatives[0].content.splitlines()) + '\n'
            for privado in (self.pagador.email, self.actor.email, str(self.banco.pk), self.banco.archivo.name, self.actor.username, 'href='):
                self.assertNotIn(privado, html + mensaje.body)
            (ruta / (nombre + '.html')).write_text(html, encoding='utf-8')
            (ruta / (nombre + '.txt')).write_text(mensaje.body, encoding='utf-8')
        resultado = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_correos_pagos_breb.js')],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120, env=os.environ)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)


@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL real para concurrencia.')
@override_settings(**AISLAR_PAGOS)
class NotificacionesPagosConcurrentesTests(DatosNotificacionesPagos, TransactionTestCase):
    def ejecutar_threads(self, funcion):
        barrera = Barrier(2)
        def worker():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                return funcion()
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(worker) for _ in range(2)]
            return [f.result(timeout=20) for f in futuros]

    def test_dos_reportes_replay_crean_un_evento(self):
        clave = uuid4()
        resultados = self.ejecutar_threads(lambda: self.reporte_cuota(clave=clave))
        self.assertEqual(sum(not replay for _, replay in resultados), 1)
        self.assertEqual(OutboxCorreoEducativo.objects.count(), 1)

    def test_dos_conciliaciones_crean_un_evento_confirmado(self):
        reporte, _ = self.reporte_cuota()
        self.escanear(reporte.comprobante_id)
        datos = self.datos_confirmacion(reporte)
        resultados = self.ejecutar_threads(lambda: recaudos.confirmar_recaudo(**datos))
        self.assertEqual(sum(not replay for _, replay in resultados), 1)
        self.assertEqual(OutboxCorreoEducativo.objects.filter(codigo_mensaje='BREB_INCOME_CONFIRMED').count(), 1)

    def test_dos_aplicaciones_crean_un_evento_aplicado(self):
        ingreso, _ = self.registrar()
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        def aplicar():
            return aplicacion_cuotas.aplicar_cuota(reporte_id=ingreso.reporte_id, cuota_id=cuota.pk,
                actor=self.actor, vista_previa=token, confirmar_cuota=True).pk
        resultados = self.ejecutar_threads(aplicar)
        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)
        self.assertEqual(OutboxCorreoEducativo.objects.filter(codigo_mensaje='BREB_PAYMENT_APPLIED').count(), 1)

    def test_dos_workers_envian_un_solo_correo(self):
        self.registrar()
        resultados = self.ejecutar_threads(outbox.procesar_siguiente_correo)
        self.assertEqual(sum(r.procesado for r in resultados), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.correo('BREB_INCOME_CONFIRMED').estado, Estado.SENT)
        self.assertTrue(ConciliacionRecaudoEducativo.objects.exists())

    def test_reclamacion_skip_locked_no_espera_otro_aviso(self):
        primero, _ = self.registrar()
        self.registrar(clave=uuid4(), referencia='OPS-SECOND')
        reclamado, liberar = Event(), Event()
        ids = []
        def bloquear():
            close_old_connections()
            try:
                with transaction.atomic():
                    ids.append(OutboxCorreoEducativo.objects.select_for_update().get(reporte_recaudo=primero.reporte).pk)
                    reclamado.set()
                    if not liberar.wait(timeout=10):
                        raise AssertionError('Lock sintetico no liberado')
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            futuro = pool.submit(bloquear)
            try:
                self.assertTrue(reclamado.wait(timeout=10))
                otro = outbox.reclamar_correo_pendiente()
                self.assertIsNotNone(otro)
                self.assertNotEqual(otro.pk, ids[0])
            finally:
                liberar.set()
            futuro.result(timeout=10)

    def test_aplicacion_y_envio_serializados_por_reporte_sin_aviso_tardio(self):
        ingreso, _ = self.registrar()
        cuota, token = aplicacion_cuotas.preparar_vista_previa(reporte=ingreso.reporte, actor=self.actor)
        enviando, liberar, iniciado = Event(), Event(), Event()
        pid = []
        def smtp_simulado(*args, **kwargs):
            enviando.set()
            if not liberar.wait(timeout=15):
                raise AssertionError('SMTP sintetico no fue liberado')
        def enviar():
            close_old_connections()
            try:
                return outbox.procesar_siguiente_correo()
            finally:
                connection.close()
        def aplicar():
            close_old_connections()
            try:
                connection.ensure_connection()
                pid.append(connection.connection.get_backend_pid())
                iniciado.set()
                return aplicacion_cuotas.aplicar_cuota(reporte_id=ingreso.reporte_id, cuota_id=cuota.pk,
                    actor=self.actor, vista_previa=token, confirmar_cuota=True)
            finally:
                connection.close()
        with mock.patch('financiacion_educativa.services.outbox_correos._enviar_mensaje_smtp', side_effect=smtp_simulado):
            with ThreadPoolExecutor(max_workers=2) as pool:
                envio = pool.submit(enviar)
                try:
                    self.assertTrue(enviando.wait(timeout=10))
                    aplicacion = pool.submit(aplicar)
                    self.assertTrue(iniciado.wait(timeout=10))
                    bloqueada, limite = False, monotonic() + 5
                    while monotonic() < limite:
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT cardinality(pg_blocking_pids(%s))', [pid[0]])
                            bloqueada = cursor.fetchone()[0] > 0
                        if bloqueada:
                            break
                    self.assertTrue(bloqueada, 'La aplicacion debe esperar el lock del aviso en envio.')
                    self.assertFalse(AplicacionCuotaEducativa.objects.exists())
                finally:
                    liberar.set()
                self.assertEqual(envio.result(timeout=10).estado, Estado.SENT)
                self.assertIsNotNone(aplicacion.result(timeout=10).pk)
        self.assertEqual(OutboxCorreoEducativo.objects.filter(codigo_mensaje='BREB_PAYMENT_APPLIED').count(), 1)
        self.assertTrue(connection.is_usable())
        self.assertEqual(AplicacionCuotaEducativa.objects.count(), 1)
