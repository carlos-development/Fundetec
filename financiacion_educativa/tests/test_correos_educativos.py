import re
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.template.loader import get_template
from django.utils import timezone

from financiacion_educativa.choices import TipoDecisionRevisionEducativa
from financiacion_educativa.models import (
    DocumentoFinanciacion,
    EnlaceCapturaMovil,
    EventoEnlaceCapturaMovil,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.correos import (
    ASUNTO_CAPTURA_MOVIL,
    ASUNTO_EXPEDIENTE_RECIBIDO,
    ConfiguracionSMTPInvalida,
    URL_MUESTRA_INERTE,
    construir_correo_captura_movil,
    construir_correo_decision_educativa,
    construir_correo_expediente_recibido,
    construir_correos_prueba,
    validar_configuracion_smtp,
)


CONFIGURACION_SMTP_VALIDA = {
    'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
    'EMAIL_HOST': 'smtp.example.test',
    'EMAIL_PORT': 587,
    'EMAIL_USE_TLS': True,
    'EMAIL_USE_SSL': False,
    'EMAIL_HOST_USER': 'noreply@aprobado.com.co',
    'EMAIL_HOST_PASSWORD': 'secreto-solo-prueba',
    'DEFAULT_FROM_EMAIL': 'Aprobado <noreply@aprobado.com.co>',
    'EMAIL_TIMEOUT': 10,
}


@override_settings(**CONFIGURACION_SMTP_VALIDA)
class ConfiguracionSMTPTests(SimpleTestCase):
    def test_configuracion_smtp_valida(self):
        self.assertIsNone(validar_configuracion_smtp())

    @override_settings(
        EMAIL_BACKEND='aprobado_web.email_backends.SafeRoutingEmailBackend'
    )
    def test_backend_seguro_de_qa_es_compatible_con_smtp(self):
        self.assertIsNone(validar_configuracion_smtp())

    @override_settings(EMAIL_USE_SSL=True)
    def test_tls_y_ssl_simultaneos_fallan_sin_revelar_secretos(self):
        with self.assertRaises(ConfiguracionSMTPInvalida) as contexto:
            validar_configuracion_smtp()

        mensaje = str(contexto.exception)
        self.assertIn('EMAIL_USE_TLS/EMAIL_USE_SSL', mensaje)
        self.assertNotIn('secreto-solo-prueba', mensaje)
        self.assertNotIn('smtp.example.test', mensaje)
        self.assertNotIn('noreply@aprobado.com.co', mensaje)

    @override_settings(EMAIL_HOST_PASSWORD='', EMAIL_TIMEOUT=0)
    def test_variables_obligatorias_faltantes_fallan_controladamente(self):
        with self.assertRaises(ConfiguracionSMTPInvalida) as contexto:
            validar_configuracion_smtp()

        self.assertIn('EMAIL_HOST_PASSWORD', str(contexto.exception))
        self.assertIn('EMAIL_TIMEOUT', str(contexto.exception))

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'
    )
    def test_backend_de_consola_no_se_acepta_para_envio_real(self):
        with self.assertRaises(ConfiguracionSMTPInvalida):
            validar_configuracion_smtp()


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='Aprobado <noreply@aprobado.com.co>',
    EMAIL_TIMEOUT=10,
    EDUCATION_EMAIL_LOGO_URL=(
        'https://aprobado.com.co/static/images/logo-dark.png'
    ),
)
class PlantillasCorreoEducativoTests(TestCase):
    def setUp(self):
        mail.outbox.clear()

    def test_correo_funcional_de_captura_tiene_html_y_texto(self):
        mensaje = construir_correo_captura_movil(
            recipient='persona@example.com',
            continuation_url='https://credito.example.test/continuar#secreto',
            expires_at=timezone.now(),
        )

        self.assertEqual(mensaje.subject, ASUNTO_CAPTURA_MOVIL)
        self.assertEqual(mensaje.to, ['persona@example.com'])
        self.assertEqual(
            mensaje.from_email,
            'Aprobado <noreply@aprobado.com.co>',
        )
        self.assertEqual(len(mensaje.alternatives), 1)
        html = mensaje.alternatives[0].content
        self.assertIn('Continuar desde mi celular', html)
        self.assertIn('documento solicitado', html)
        self.assertNotIn('frente y el reverso', html)
        self.assertIn('vence en 30 minutos', html)
        self.assertIn('nunca te pedir&aacute; tu contrase&ntilde;a', html)
        self.assertIn('https://credito.example.test/continuar#secreto', html)
        self.assertIn('https://credito.example.test/continuar#secreto', mensaje.body)
        self.assertIn(
            'https://aprobado.com.co/static/images/logo-dark.png',
            html,
        )
        self.assertNotIn('data:image', html)
        self.assertNotIn('href="#"', html)

    def test_confirmacion_expediente_copia_soporte_sin_enlaces_secretos(self):
        mensaje = construir_correo_expediente_recibido(
            recipient='persona@example.com',
            referencia_externa='EDU-OPERACION-001',
            program_name='INGLES',
            course_name='Ingles Basico A2',
            cc=[
                'soporte@aprobado.com.co',
                'SOPORTE@APROBADO.COM.CO',
                'persona@example.com',
            ],
        )

        self.assertEqual(mensaje.subject, ASUNTO_EXPEDIENTE_RECIBIDO)
        self.assertEqual(mensaje.to, ['persona@example.com'])
        self.assertEqual(mensaje.cc, ['soporte@aprobado.com.co'])
        contenido = mensaje.body + mensaje.alternatives[0].content
        self.assertIn('EDU-OPERACION-001', contenido)
        self.assertIn('INGLES', contenido)
        self.assertIn('Ingles Basico A2', contenido)
        self.assertIn('Disponible para validaci', contenido)
        self.assertNotIn('/captura-movil/', contenido)
        self.assertNotIn('/continuar/', contenido)

    @override_settings(EDUCATION_EMAIL_LOGO_URL='')
    def test_logo_ausente_muestra_marca_textual_sin_impedir_render(self):
        mensaje = construir_correo_expediente_recibido(
            recipient='persona@example.com',
            referencia_externa='EDU-SIN-LOGO-001',
        )

        html = mensaje.alternatives[0].content
        self.assertIn('>APROBADO</strong>', html)
        self.assertNotIn('<img', html)
        self.assertIn('EDU-SIN-LOGO-001', mensaje.body)

    @override_settings(
        EDUCATION_EMAIL_LOGO_URL='http://assets.example.test/logo.png'
    )
    def test_logo_no_https_se_descarta_de_forma_segura(self):
        mensaje = construir_correo_expediente_recibido(
            recipient='persona@example.com',
            referencia_externa='EDU-LOGO-INSEGURO-001',
        )

        html = mensaje.alternatives[0].content
        self.assertIn('>APROBADO</strong>', html)
        self.assertNotIn('http://assets.example.test', html)

    def test_decision_usa_presentacion_contextual_sin_notas_tecnicas(self):
        mensaje = construir_correo_decision_educativa(
            recipient='persona@example.com',
            decision=SimpleNamespace(
                tipo=TipoDecisionRevisionEducativa.CORRECTION_REQUESTED,
                mensaje_solicitante='Revisa la legibilidad del documento.',
            ),
        )

        contenido = mensaje.body + mensaje.alternatives[0].content
        self.assertIn('Accion requerida', contenido)
        self.assertIn('Siguiente paso', contenido)
        self.assertNotIn('prompt', contenido.lower())
        self.assertNotIn('confidence', contenido.lower())

    def test_datos_variables_se_escapan_en_html(self):
        mensaje = construir_correo_expediente_recibido(
            recipient='persona@example.com',
            referencia_externa='<script>referencia</script>',
            program_name='<b>programa</b>',
            course_name='Curso & nivel',
        )

        html = mensaje.alternatives[0].content
        self.assertNotIn('<script>referencia</script>', html)
        self.assertNotIn('<b>programa</b>', html)
        self.assertIn('&lt;script&gt;referencia&lt;/script&gt;', html)
        self.assertIn('&lt;b&gt;programa&lt;/b&gt;', html)
        self.assertIn('Curso &amp; nivel', html)

    def test_plantillas_productivas_no_incorporan_preview_inseguro(self):
        nombres = (
            'emails/financiacion_educativa/_base.html',
            'emails/financiacion_educativa/invitacion_continuacion.html',
            'emails/financiacion_educativa/captura_movil.html',
            'emails/financiacion_educativa/expediente_recibido.html',
            'emails/financiacion_educativa/decision_estado.html',
            'emails/financiacion_educativa/nueva_solicitud_interna.html',
        )
        for nombre in nombres:
            fuente = get_template(nombre).template.source.lower()
            self.assertNotIn('data:image', fuente, nombre)
            self.assertNotIn('href="#"', fuente, nombre)
            self.assertNotIn('example.invalid', fuente, nombre)
            self.assertNotIn('<script', fuente, nombre)

    def test_nueve_muestras_son_inertes_y_no_cambian_el_dominio(self):
        conteos_antes = {
            'solicitudes': SolicitudFinanciacionEducativa.objects.count(),
            'enlaces': EnlaceCapturaMovil.objects.count(),
            'eventos': EventoEnlaceCapturaMovil.objects.count(),
            'documentos': DocumentoFinanciacion.objects.count(),
        }

        mensajes = construir_correos_prueba(
            destinatario='bandeja-autorizada@example.com'
        )
        for mensaje in mensajes:
            self.assertEqual(mensaje.send(), 1)

        self.assertEqual(len(mensajes), 9)
        self.assertEqual(len(mail.outbox), 9)
        patron_uuid = re.compile(
            r'\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-'
            r'[89ab][0-9a-f]{3}-[0-9a-f]{12}\b',
            re.IGNORECASE,
        )
        for mensaje in mensajes:
            self.assertTrue(mensaje.subject.startswith('[PRUEBA] '))
            self.assertEqual(
                mensaje.from_email,
                'Aprobado <noreply@aprobado.com.co>',
            )
            self.assertEqual(
                mensaje.to,
                ['bandeja-autorizada@example.com'],
            )
            self.assertEqual(len(mensaje.alternatives), 1)
            html = mensaje.alternatives[0].content
            contenido = f'{mensaje.subject}\n{mensaje.body}\n{html}'
            self.assertIn(URL_MUESTRA_INERTE, contenido)
            self.assertIsNone(patron_uuid.search(contenido))
            self.assertNotIn('/documentos/', contenido)
            self.assertNotIn('/captura-movil/token/', contenido)
            self.assertNotIn('secreto-solo-prueba', contenido)

        conteos_despues = {
            'solicitudes': SolicitudFinanciacionEducativa.objects.count(),
            'enlaces': EnlaceCapturaMovil.objects.count(),
            'eventos': EventoEnlaceCapturaMovil.objects.count(),
            'documentos': DocumentoFinanciacion.objects.count(),
        }
        self.assertEqual(conteos_despues, conteos_antes)

    def test_comando_exige_confirmacion_y_destinatario_valido(self):
        with self.assertRaises(CommandError):
            call_command(
                'enviar_correos_prueba_educacion',
                destinatario='autorizado@example.com',
            )
        with self.assertRaises(CommandError):
            call_command(
                'enviar_correos_prueba_educacion',
                destinatario='direccion-invalida',
                confirmar=True,
            )
        self.assertEqual(len(mail.outbox), 0)

    @mock.patch(
        'financiacion_educativa.management.commands.'
        'enviar_correos_prueba_educacion.validar_configuracion_smtp'
    )
    def test_comando_controlado_envia_solo_nueve_muestras(
        self,
        validar_mock,
    ):
        salida = StringIO()
        call_command(
            'enviar_correos_prueba_educacion',
            destinatario='autorizado@example.com',
            confirmar=True,
            stdout=salida,
        )

        validar_mock.assert_called_once_with()
        self.assertEqual(len(mail.outbox), 9)
        self.assertIn('ACEPTADOS 9/9', salida.getvalue())
