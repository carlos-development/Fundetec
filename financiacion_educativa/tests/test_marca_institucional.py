import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from financiacion_educativa.choices import CodigoMensajeCorreoEducativo as Codigo
from financiacion_educativa.models import DestinatarioNotificacionInstitucionalEducativa
from financiacion_educativa.services.correos import construir_correo_expediente_recibido
from financiacion_educativa.services.marca_institucional import logos_correo_de_solicitud
from financiacion_educativa.services.orquestacion import programar_invitacion_inicial
from financiacion_educativa.services.outbox_correos import (
    _construir_mensaje, _entregar, procesar_siguiente_correo,
)
from financiacion_educativa.tests.factories import crear_solicitud


@override_settings(
    DEBUG=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BRAND_PUBLIC_BASE_URL='https://aprobado.com.co',
    EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=['auditoria@example.test'],
)
class MarcaCorreoTests(TestCase):
    def setUp(self):
        self.solicitud = crear_solicitud(referencia='DEMO-EDU-001', correo='alumna@example.test')
        self.config = self.solicitud.institucion.configuracion_anexo_matricula

    def cambiar_perfil(self, perfil):
        self.config.perfil = perfil
        self.config.save(update_fields=['perfil'])

    def verificar_logos(self, mensaje, jowalth):
        html = mensaje.alternatives[0].content
        for nombre in ('Fundetec', 'Loopiter'):
            url = f'https://aprobado.com.co/static/images/simulador/{nombre}_logo.png'
            self.assertEqual(html.count(f'src="{url}"'), int(jowalth))
            self.assertNotIn(url, mensaje.body)
            if jowalth:
                self.assertIn(f'alt="{nombre.upper() if nombre == "Fundetec" else nombre}"', html)
        if jowalth:
            self.assertLess(html.index('alt="Aprobado"'), html.index('<h1'))
            self.assertLess(html.index('<h1'), html.index('alt="FUNDETEC"'))
            self.assertLess(html.index('alt="Loopiter"'), html.index('Mensaje enviado por'))
        self.assertIn('Aprobado', html)

    def test_cada_aviso_conserva_datos_y_solo_cambia_la_presentacion_html(self):
        aplicacion = SimpleNamespace(capital_aplicado=150, interes_aplicado=10, cuota=SimpleNamespace(numero=1))
        ingreso = SimpleNamespace(monto_recibido=160, aplicacion=aplicacion)
        aviso = SimpleNamespace(
            solicitud=self.solicitud, solicitud_id=self.solicitud.pk, destinatarios=[self.solicitud.correo],
            decision=SimpleNamespace(tipo='APPROVED', mensaje_solicitante='Decisión de prueba.', solicitud=self.solicitud),
            reporte_recaudo=SimpleNamespace(solicitud=self.solicitud, monto_reportado=160, conciliacion=ingreso),
            contexto={}, message_id='<demo@example.test>',
        )
        for codigo in (Codigo.DOSSIER_RECEIVED, Codigo.REVIEW_DECISION, Codigo.AUTOMATIC_CORRECTION,
                       Codigo.AUTOMATIC_CONTINUATION, Codigo.CONTRACTUAL_PLAN_AVAILABLE, Codigo.BREB_REPORT_RECEIVED,
                       Codigo.BREB_INCOME_CONFIRMED, Codigo.BREB_PAYMENT_APPLIED, Codigo.NEW_APPLICATION_INTERNAL):
            with self.subTest(codigo=codigo):
                aviso.codigo_mensaje = codigo
                self.cambiar_perfil('FUNDETEC_FO_AD_005')
                generico = _construir_mensaje(aviso, None)
                self.cambiar_perfil('JOWALTH_ACADEMY')
                marcado = _construir_mensaje(aviso, None)
                self.verificar_logos(generico, False)
                self.verificar_logos(marcado, codigo != Codigo.NEW_APPLICATION_INTERNAL)
                for campo in ('body', 'subject', 'to', 'cc', 'bcc', 'from_email', 'extra_headers'):
                    self.assertEqual(getattr(generico, campo), getattr(marcado, campo))
                if codigo == Codigo.DOSSIER_RECEIVED and os.environ.get('EDU_JOWALTH_EMAIL_PREVIEWS'):
                    carpeta = Path(os.environ['EDU_JOWALTH_EMAIL_PREVIEWS'])
                    carpeta.mkdir(parents=True, exist_ok=True)
                    for nombre, mensaje in [('jowalth', marcado), ('generico', generico)]:
                        (carpeta / f'{nombre}.html').write_text(mensaje.alternatives[0].content, encoding='utf-8')
                        (carpeta / f'{nombre}.txt').write_text(mensaje.body, encoding='utf-8')

    def test_invitacion_real_y_copias_se_entregan_con_marcas_separadas(self):
        self.cambiar_perfil('JOWALTH_ACADEMY')
        DestinatarioNotificacionInstitucionalEducativa.objects.create(
            institucion=self.solicitud.institucion, correo='institucion@example.test')
        programar_invitacion_inicial(solicitud=self.solicitud)
        for esperado in [True, False, False]:
            self.assertEqual(procesar_siguiente_correo().estado, 'SENT')
            self.verificar_logos(mail.outbox[-1], esperado)
        self.assertEqual(mail.outbox[0].to, [self.solicitud.correo])
        self.assertEqual({m.to[0] for m in mail.outbox[1:]}, {'auditoria@example.test', 'institucion@example.test'})

    def test_recordatorio_y_captura_reciben_el_perfil_del_evento(self):
        aviso = SimpleNamespace(solicitud=self.solicitud, solicitud_id=self.solicitud.pk,
            pk=1, correo_origen_id=None, destinatarios=[self.solicitud.correo], message_id='<demo@example.test>')
        self.cambiar_perfil('JOWALTH_ACADEMY')
        with patch('financiacion_educativa.services.outbox_correos._preparar_enlace_personal',
                   return_value=('https://example.test/continuar', timezone.now())):
            for codigo, evento in [(Codigo.INVITATION, 'CONTINUATION_REMINDER_1H'),
                                   (Codigo.MOBILE_CAPTURE, 'MOBILE_CAPTURE_LINK')]:
                aviso.codigo_mensaje, aviso.tipo_evento = codigo, evento
                _entregar(aviso, None)
                self.verificar_logos(mail.outbox[-1], True)

    def test_sin_institucion_o_sin_origen_https(self):
        generico = construir_correo_expediente_recibido(recipient=self.solicitud.correo,
            referencia_externa='DEMO', program_name='Jowalth Academy')
        self.verificar_logos(generico, False)
        self.cambiar_perfil('JOWALTH_ACADEMY')
        for configuracion in [{'BRAND_PUBLIC_BASE_URL': 'http://example.test'}, {'STATIC_URL': 'http://cdn.example.test/'}]:
            with self.subTest(configuracion=configuracion), override_settings(**configuracion):
                self.assertEqual(logos_correo_de_solicitud(self.solicitud), ())

