from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections, IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from financiacion_educativa.models import (
    CondicionesFinancieras, EventoWebhookFirmaEducativa, OutboxCorreoEducativo,
    ProcesoFirmaEducativa, SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services import outbox_correos as outbox
from financiacion_educativa.services.correo_plan_contractual import construir_correo_plan
from financiacion_educativa.services.firma_zapsign import _finalizar_firma
from financiacion_educativa.services.notificaciones_plan_contractual import CODIGO_PLAN, registrar_disponibilidad_plan
from financiacion_educativa.services.participantes import DatosParticipante, registrar_estudiante_menor_con_tutor
from financiacion_educativa.tests.signature_backends import RecordingEducationalSignatureBackend
from financiacion_educativa.tests.test_dashboard_obligaciones import DatosObligacionesMixin
from financiacion_educativa.tests.test_recaudos_breb import AISLAMIENTO


AISLAR_PLAN = {**AISLAMIENTO, 'DEBUG': True,
    'FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED': False,
    'FINANCIACION_EDUCATIVA_CONTRACTUAL_PLAN_EMAILS_ENABLED': True,
    'EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS': [],
    'EDUCATION_EMAIL_LOGO_URL': 'https://portal.example.test/static/images/logo-dark.png',
    'BRAND_PUBLIC_BASE_URL': 'https://portal.example.test',
    'FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND': 'financiacion_educativa.tests.signature_backends.RecordingEducationalSignatureBackend',
    'FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS': True,
    'FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET': 'synthetic-plan-webhook',
    'FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_HEADER': 'X-Educational-Signature-Secret'}


class DatosAvisoPlan(DatosObligacionesMixin):
    def setUp(self):
        from tempfile import TemporaryDirectory
        self.privados = TemporaryDirectory(prefix='fundetec-aviso-plan-')
        self.addCleanup(self.privados.cleanup)
        temporal = override_settings(FINANCIACION_EDUCATIVA_PRIVATE_ROOT=self.privados.name)
        temporal.enable()
        self.addCleanup(temporal.disable)
        self.preparar_datos()
        self.titular = get_user_model().objects.create_user('titular-plan', 'titular-plan@example.test', 'synthetic-only')
        self.verificacion = EmailAddress.objects.create(user=self.titular, email=self.titular.email, verified=True)
        self.solicitud, self.foto, self.pagare, self.firma = self.contrato('PLAN-SINTETICO')
        self.solicitud.usuario = self.titular
        self.solicitud.correo = self.titular.email
        self.solicitud.fecha_nacimiento_estudiante = date(1990, 1, 1)
        self.solicitud.estado = 'PENDING_SIGNATURE'
        self.solicitud.save()
        self.pagare.estado = 'SENT_FOR_SIGNATURE'
        self.pagare.archivo_firmado = ''
        self.pagare.hash_firmado_sha256 = ''
        self.pagare.tamano_firmado_bytes = None
        self.pagare.firmado_en = None
        self.pagare.save()
        self.firma.estado = 'SENT'
        self.firma.firmado_en = None
        self.firma.token_documento_externo = 'synthetic-plan-' + uuid4().hex
        self.firma.save()
        RecordingEducationalSignatureBackend.reset()

    def firmar(self):
        evento = EventoWebhookFirmaEducativa.objects.create(payload_hash=uuid4().hex * 2, tipo_evento='doc_signed')
        _finalizar_firma(evento=evento, proceso=self.firma, pdf_firmado=b'%PDF-1.4\nsynthetic-plan-signature\n%%EOF')
        self.solicitud.refresh_from_db()
        return OutboxCorreoEducativo.objects.get(solicitud=self.solicitud, codigo_mensaje=CODIGO_PLAN)

    def webhook(self):
        return self.client.post(reverse('financiacion_educativa_api:zapsign-webhook'),
            data=json.dumps({'event_type': 'doc_signed', 'status': 'signed',
                'external_id': self.firma.external_id, 'token': self.firma.token_documento_externo}),
            content_type='application/json', HTTP_X_EDUCATIONAL_SIGNATURE_SECRET='synthetic-plan-webhook')

    def tutor(self, acceso=True, verificado=True):
        tutor = get_user_model().objects.create_user('tutor-plan', 'tutor-plan@example.test', 'synthetic-only')
        EmailAddress.objects.create(user=tutor, email=tutor.email, verified=verificado)
        registrar_estudiante_menor_con_tutor(solicitud=self.solicitud,
            estudiante=DatosParticipante(nombres='Estudiante', apellidos='Sintetico', tipo_documento='TI',
                numero_documento='STUDENT-SYNTHETIC', fecha_nacimiento=date(2014, 1, 1), fecha_nacimiento_confirmada=True),
            tutor=DatosParticipante(nombres='Tutor', apellidos='Sintetico', tipo_documento='CC',
                numero_documento='GUARDIAN-SYNTHETIC', fecha_nacimiento=date(1980, 1, 1), fecha_nacimiento_confirmada=True,
                correo=tutor.email, usuario=tutor))
        if acceso:
            self.solicitud.usuario = tutor
            self.solicitud.correo = tutor.email
            self.solicitud.save(update_fields=['usuario', 'correo'])
        return tutor


@override_settings(**AISLAR_PLAN)
class AvisoPlanContractualTests(DatosAvisoPlan, TestCase):
    def test_adulto_encola_y_envia_sin_cifras_adjuntos_ni_pdf_publico(self):
        aviso = self.firmar()
        self.assertEqual(aviso.plan_contractual_id, self.foto.pk)
        self.assertEqual(aviso.plan_destinatario_usuario_id, self.titular.pk)
        self.assertEqual(aviso.estado, 'PENDING')
        self.assertEqual(outbox.procesar_siguiente_correo().estado, 'SENT')
        mensaje = mail.outbox[-1]
        html = mensaje.alternatives[0].content
        self.assertEqual(mensaje.to, [self.titular.email])
        self.assertEqual((mensaje.cc, mensaje.bcc, mensaje.attachments), ([], [], []))
        self.assertIn('Ver mi plan de pagos', html)
        self.assertIn('calendario pactado', html)
        self.assertIn('/static/images/logo-dark.png', html)
        self.assertIn('https://portal.example.test' + reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk]), html)
        for dato in (self.solicitud.referencia_externa, self.titular.email, self.pagare.archivo.name,
                str(self.foto.capital_financiado), str(self.foto.valor_cuota_estimada), '.pdf', '/operaciones/'):
            self.assertNotIn(dato, html + mensaje.body)

    def test_tutor_verificado_con_acceso_es_principal_y_estudiante_no_recibe(self):
        tutor = self.tutor()
        aviso = self.firmar()
        self.assertEqual(aviso.destinatarios, [tutor.email])
        self.assertEqual(outbox.procesar_siguiente_correo().estado, 'SENT')
        self.assertEqual(mail.outbox[-1].to, [tutor.email])
        self.client.force_login(tutor)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:plan-contractual-pdf',
            args=[self.solicitud.pk, self.foto.pk])).status_code, 200)

    def test_tutor_verificado_sin_acceso_no_recibe_ni_se_asocia(self):
        tutor = self.tutor(acceso=False)
        aviso = self.firmar()
        self.assertEqual((aviso.estado, aviso.codigo_ultimo_error), ('FAILED', 'PLAN_RECIPIENT_NO_ACCESS'))
        self.assertEqual(self.solicitud.estado, 'APPROVED')
        self.assertEqual(self.solicitud.usuario_id, self.titular.pk)
        self.client.force_login(tutor)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:plan-contractual-pdf',
            args=[self.solicitud.pk, self.foto.pk])).status_code, 404)

    def test_adulto_sin_verificar_registra_motivo_sin_bloquear_contrato(self):
        self.verificacion.verified = False
        self.verificacion.save()
        aviso = self.firmar()
        self.assertEqual((aviso.estado, aviso.codigo_ultimo_error), ('FAILED', 'PLAN_RECIPIENT_UNVERIFIED'))
        self.assertEqual(self.solicitud.estado, 'APPROVED')
        self.assertEqual(aviso.destinatarios, [])
        self.assertFalse(outbox.procesar_siguiente_correo().procesado)

    def test_tutor_sin_verificar_bloquea_solo_aviso(self):
        self.tutor(verificado=False)
        aviso = self.firmar()
        self.assertEqual(aviso.codigo_ultimo_error, 'PLAN_RECIPIENT_UNVERIFIED')
        self.assertEqual(self.solicitud.estado, 'APPROVED')

    def test_sin_cuenta_no_se_infiere_acceso(self):
        self.solicitud.usuario = None
        self.solicitud.save(update_fields=['usuario'])
        aviso = self.firmar()
        self.assertEqual(aviso.codigo_ultimo_error, 'PLAN_RECIPIENT_NO_ACCESS')

    def test_edad_desconocida_no_se_considera_adulto_verificado(self):
        self.solicitud.fecha_nacimiento_estudiante = None
        self.solicitud.save(update_fields=['fecha_nacimiento_estudiante'])
        self.assertEqual(self.firmar().codigo_ultimo_error, 'PLAN_RECIPIENT_UNVERIFIED')

    def test_flag_independiente_excluye_reclamacion_y_no_consume_intentos(self):
        aviso = self.firmar()
        with override_settings(FINANCIACION_EDUCATIVA_CONTRACTUAL_PLAN_EMAILS_ENABLED=False,
                FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED=True):
            self.assertFalse(outbox.procesar_siguiente_correo().procesado)
        aviso.refresh_from_db()
        self.assertEqual(aviso.intentos, 0)
        self.assertEqual(aviso.estado, 'PENDING')

    def test_apagado_despues_de_reclamar_no_envia(self):
        self.firmar()
        reclamado = outbox.reclamar_correo_pendiente()
        with override_settings(FINANCIACION_EDUCATIVA_CONTRACTUAL_PLAN_EMAILS_ENABLED=False):
            with self.assertRaises(Exception) as error:
                outbox._entregar(reclamado, reclamado.lease_id)
        self.assertEqual(error.exception.codigo, 'PLAN_NOTIFICATIONS_DISABLED')
        self.assertEqual(len(mail.outbox), 0)

    def test_webhook_repetido_y_descarga_no_generan_otro_aviso(self):
        self.assertEqual(self.webhook().status_code, 200)
        self.assertEqual(self.webhook().status_code, 200)
        self.assertEqual(OutboxCorreoEducativo.objects.filter(codigo_mensaje=CODIGO_PLAN).count(), 1)
        self.client.force_login(self.titular)
        ruta = reverse('financiacion_educativa_web:plan-contractual-pdf', args=[self.solicitud.pk, self.foto.pk])
        self.assertEqual(self.client.get(ruta).status_code, 200)
        self.assertEqual(self.client.get(ruta).status_code, 200)
        self.assertEqual(OutboxCorreoEducativo.objects.filter(codigo_mensaje=CODIGO_PLAN).count(), 1)

    def test_reversion_de_transaccion_no_deja_aviso_ni_autorizacion(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.firmar()
                raise RuntimeError('Rollback sintetico')
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'PENDING_SIGNATURE')
        self.assertFalse(OutboxCorreoEducativo.objects.filter(codigo_mensaje=CODIGO_PLAN).exists())

    def test_fallo_de_registro_revierte_transicion_contractual(self):
        with patch('financiacion_educativa.services.notificaciones_plan_contractual.registrar_disponibilidad_plan',
                side_effect=RuntimeError('Fallo sintetico del outbox')):
            with self.assertRaises(RuntimeError):
                self.firmar()
        self.solicitud.refresh_from_db()
        self.firma.refresh_from_db()
        self.assertEqual((self.solicitud.estado, self.firma.estado), ('PENDING_SIGNATURE', 'SENT'))

    def test_version_reemplazada_no_se_envia(self):
        aviso = self.firmar()
        self.pagare.vigente = False
        self.pagare.save(update_fields=['vigente'])
        resultado = outbox.procesar_siguiente_correo()
        self.assertEqual((resultado.estado, resultado.codigo), ('SUPERSEDED', 'PLAN_NOTICE_SUPERSEDED'))
        self.assertEqual(len(mail.outbox), 0)
        aviso.refresh_from_db()
        self.assertEqual(aviso.plan_contractual_id, self.foto.pk)

    def test_revalida_correo_verificado_antes_de_enviar(self):
        self.firmar()
        self.verificacion.verified = False
        self.verificacion.save()
        resultado = outbox.procesar_siguiente_correo()
        self.assertEqual((resultado.estado, resultado.codigo), ('FAILED', 'PLAN_RECIPIENT_UNVERIFIED'))
        self.assertEqual(len(mail.outbox), 0)

    def test_revalida_cuenta_sin_reenrutar_a_otra(self):
        self.firmar()
        self.titular.is_active = False
        self.titular.save(update_fields=['is_active'])
        resultado = outbox.procesar_siguiente_correo()
        self.assertEqual((resultado.estado, resultado.codigo), ('FAILED', 'PLAN_RECIPIENT_NO_ACCESS'))
        self.assertEqual(len(mail.outbox), 0)

    def test_cambio_de_cuenta_elegible_no_reenruta_el_aviso_original(self):
        aviso = self.firmar()
        EmailAddress.objects.create(user=self.actor, email=self.actor.email, verified=True)
        self.solicitud.usuario = self.actor
        self.solicitud.correo = self.actor.email
        self.solicitud.save(update_fields=['usuario', 'correo'])
        resultado = outbox.procesar_siguiente_correo()
        self.assertEqual((resultado.estado, resultado.codigo), ('FAILED', 'PLAN_RECIPIENT_CHANGED'))
        aviso.refresh_from_db()
        self.assertEqual(aviso.destinatarios, [self.titular.email])
        self.assertEqual(len(mail.outbox), 0)

    def test_otra_cuenta_no_puede_abrir_plan_ni_portal_por_url(self):
        self.firmar()
        self.client.force_login(self.actor)
        for ruta in (reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk]),
                reverse('financiacion_educativa_web:plan-contractual-pdf', args=[self.solicitud.pk, self.foto.pk])):
            self.assertEqual(self.client.get(ruta).status_code, 404)

    def test_enlace_del_correo_exige_login_y_no_da_acceso_publico(self):
        self.firmar()
        ruta = reverse('financiacion_educativa_web:finanzas', args=[self.solicitud.pk])
        respuesta = self.client.get(ruta)
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(respuesta['Location'].startswith('/financiacion-educativa/acceso/'))
        self.assertIn('next=', respuesta['Location'])

    def test_contrato_historico_no_se_encola_al_descargar_o_iniciar_app(self):
        # Explicit historical fixture: the authorization predates the notification hook.
        self.pagare.estado, self.pagare.archivo_firmado = 'SIGNED', 'synthetic/historical.pdf'
        self.pagare.hash_firmado_sha256, self.pagare.tamano_firmado_bytes = 'b' * 64, 100
        from django.utils import timezone
        self.pagare.firmado_en = timezone.now()
        self.pagare.save()
        self.firma.estado, self.firma.firmado_en = 'SIGNED', self.pagare.firmado_en
        self.firma.save()
        self.solicitud.estado = 'APPROVED'
        self.solicitud.save(update_fields=['estado'])
        self.client.force_login(self.titular)
        self.assertEqual(self.client.get(reverse('financiacion_educativa_web:plan-contractual-pdf',
            args=[self.solicitud.pk, self.foto.pk])).status_code, 200)
        from django.apps import apps
        apps.get_app_config('financiacion_educativa').ready()
        self.assertFalse(OutboxCorreoEducativo.objects.filter(codigo_mensaje=CODIGO_PLAN).exists())

    def test_constraint_impide_duplicar_version_aunque_la_clave_sea_distinta(self):
        aviso = self.firmar()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OutboxCorreoEducativo.objects.create(solicitud=self.solicitud, plan_contractual=self.foto,
                plan_destinatario_usuario=self.titular, codigo_mensaje=CODIGO_PLAN, tipo_evento=CODIGO_PLAN,
                destinatarios=[self.titular.email], clave_idempotencia=uuid4().hex, evento_logico='a' * 64, message_id=uuid4().hex)
        self.assertTrue(OutboxCorreoEducativo.objects.filter(pk=aviso.pk).exists())

    def test_sin_logo_o_portal_https_falla_sin_enviar(self):
        self.firmar()
        with override_settings(EDUCATION_EMAIL_LOGO_URL=''):
            resultado = outbox.procesar_siguiente_correo()
        self.assertEqual(resultado.estado, 'FAILED')
        self.assertEqual(len(mail.outbox), 0)

    @skipUnless(os.environ.get('EDU_PLAN_EMAIL_PREVIEWS'), 'Muestra requiere carpeta local explicita.')
    def test_muestra_html_txt_y_render_local_con_logo_oficial(self):
        aviso = self.firmar()
        mensaje = construir_correo_plan(outbox=aviso)
        directorio = Path(os.environ['EDU_PLAN_EMAIL_PREVIEWS'])
        directorio.mkdir(parents=True, exist_ok=True)
        (directorio / 'plan-contractual-disponible.html').write_text(mensaje.alternatives[0].content, encoding='utf-8')
        (directorio / 'plan-contractual-disponible.txt').write_text(mensaje.body, encoding='utf-8')
        resultado = subprocess.run(['node', '--test', str(Path(__file__).parent / 'js/test_correo_plan_contractual.js')],
            env=os.environ, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)


@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL real para concurrencia.')
@override_settings(**AISLAR_PLAN)
class AvisoPlanConcurrenteTests(DatosAvisoPlan, TransactionTestCase):
    def paralelos(self, funcion):
        barrera = Barrier(2)
        def ejecutar():
            close_old_connections()
            propia = connections['default']
            try:
                barrera.wait(timeout=10)
                return funcion()
            finally:
                propia.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(ejecutar) for _ in range(2)]
            return [f.result(timeout=30) for f in futuros]

    def test_registro_concurrente_una_intencion_por_solicitud_y_version(self):
        aviso = self.firmar()
        def registrar():
            with transaction.atomic():
                return registrar_disponibilidad_plan(solicitud_id=self.solicitud.pk, fotografia_id=self.foto.pk)[0].pk
        self.assertEqual(self.paralelos(registrar), [aviso.pk, aviso.pk])
        self.assertEqual(OutboxCorreoEducativo.objects.filter(codigo_mensaje=CODIGO_PLAN).count(), 1)
        self.assertTrue(connection.is_usable())

    def test_dos_workers_envian_una_sola_vez(self):
        self.firmar()
        resultados = self.paralelos(outbox.procesar_siguiente_correo)
        self.assertEqual(sum(r.procesado for r in resultados), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(OutboxCorreoEducativo.objects.get(codigo_mensaje=CODIGO_PLAN).estado, 'SENT')
        self.assertTrue(connection.is_usable())

    def test_dos_webhooks_simultaneos_crean_un_aviso_y_una_firma(self):
        from django.test import Client
        def confirmar():
            return Client().post(reverse('financiacion_educativa_api:zapsign-webhook'),
                data=json.dumps({'event_type': 'doc_signed', 'status': 'signed',
                    'external_id': self.firma.external_id, 'token': self.firma.token_documento_externo}),
                content_type='application/json', HTTP_X_EDUCATIONAL_SIGNATURE_SECRET='synthetic-plan-webhook').status_code
        self.assertEqual(self.paralelos(confirmar), [200, 200])
        self.assertEqual(OutboxCorreoEducativo.objects.filter(codigo_mensaje=CODIGO_PLAN).count(), 1)
        self.assertEqual(ProcesoFirmaEducativa.objects.filter(solicitud=self.solicitud, estado='SIGNED').count(), 1)
        self.assertTrue(connection.is_usable())
