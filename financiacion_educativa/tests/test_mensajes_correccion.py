from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from financiacion_educativa.services.correos import (
    construir_correo_copia_educativa, construir_correo_correccion_automatica,
)
from financiacion_educativa.services.mensajes_correccion import (
    MENSAJES_RAZON, TITULO_CORRECCION, resolver_mensaje_correccion,
)


@override_settings(BRAND_PUBLIC_BASE_URL='https://example.invalid')
class MensajesCorreccionTests(SimpleTestCase):
    def test_original_html_txt_asunto_y_cta_no_muestran_codigos(self):
        for razon, texto in MENSAJES_RAZON.items():
            with self.subTest(razon=razon):
                correo = construir_correo_correccion_automatica(
                    recipient='estudiante@example.invalid', requisitos=['INCOME_CERTIFICATE'],
                    razones={'INCOME_CERTIFICATE': [razon]},
                )
                self.assertEqual(correo.subject, TITULO_CORRECCION)
                for contenido in (correo.body, correo.alternatives[0].content):
                    self.assertIn(texto, contenido)
                    self.assertIn('Revisar mi solicitud', contenido)
                    self.assertIn('no necesitas comenzar nuevamente', contenido)
                    self.assertNotIn(razon, contenido)
                    self.assertNotIn('INCOME_CERTIFICATE', contenido)
                    self.assertIn('https://example.invalid/financiacion-educativa/mis-solicitudes/continuar/', contenido)

    def test_codigos_desconocidos_nunca_se_interpolan(self):
        self.assertNotIn('TOKEN-SECRETO', resolver_mensaje_correccion('TOKEN-SECRETO', ['TOKEN-SECRETO']))

    def test_copia_auditoria_sin_documentos_enlaces_personales_ni_datos_titular(self):
        copia = construir_correo_copia_educativa(
            recipients=['auditoria@example.invalid'], clase='AUDIT',
            comunicacion='Solicitud de corrección', asunto_original=TITULO_CORRECCION,
            referencia_externa='REF-SINTETICA', institucion='INSTITUCION SINTETICA',
            programa='CURSO', curso='CURSO', destinatario_original='estudiante@example.invalid',
            enviada_en=timezone.now(),
        )
        self.assertEqual(copia.attachments, [])
        for contenido in (copia.body, copia.alternatives[0].content, copia.subject):
            for secreto in ('estudiante@example.invalid', 'INCOME_CERTIFICATE', 'DATA_MISMATCH', '/continuar/', '10000001'):
                self.assertNotIn(secreto, contenido)
