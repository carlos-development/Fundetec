import base64
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from financiacion_educativa.services.firma_zapsign import (
    FirmaEducativaEnvioAmbiguo,
    FirmaEducativaRechazoPermanente,
    FirmaEducativaRespuestaInvalida,
    ZapSignEducationalSignatureBackend,
)


class FirmantePrueba:
    nombre_completo = 'FIRMANTE DE PRUEBA'
    correo = 'firmante@example.com'
    tipo_documento = 'CC'
    numero_documento = '100200300'


@override_settings(
    FINANCIACION_EDUCATIVA_ZAPSIGN_BASE_URL=(
        'https://sandbox.api.zapsign.com.br/api/v1'
    ),
    FINANCIACION_EDUCATIVA_ZAPSIGN_API_TOKEN='api-test-token',
    FINANCIACION_EDUCATIVA_ZAPSIGN_TIMEOUT_SECONDS=17,
    FINANCIACION_EDUCATIVA_ZAPSIGN_AUTH_MODE='assinaturaTela-tokenEmail',
    FINANCIACION_EDUCATIVA_ZAPSIGN_SEND_AUTOMATIC_EMAIL=True,
    FINANCIACION_EDUCATIVA_ZAPSIGN_REQUIRE_SELFIE=True,
    FINANCIACION_EDUCATIVA_ZAPSIGN_SELFIE_VALIDATION_TYPE='identity-verification',
)
class ZapSignEducationalSignatureBackendTests(SimpleTestCase):
    @patch('requests.post')
    def test_timeout_de_creacion_se_clasifica_como_envio_ambiguo(self, post):
        import requests

        post.side_effect = requests.Timeout('timeout de prueba')

        with self.assertRaises(FirmaEducativaEnvioAmbiguo):
            ZapSignEducationalSignatureBackend().enviar(
                pdf=b'%PDF-1.4\nprivate\n%%EOF',
                nombre_documento='Pagare educativo',
                external_id='edu-ambiguous-id',
                firmante=FirmantePrueba(),
            )

    @patch('requests.post')
    def test_envia_base64_sin_exponer_url_privada(self, post):
        respuesta = Mock()
        respuesta.json.return_value = {'token': 'document-token', 'status': 'pending'}
        respuesta.raise_for_status.return_value = None
        post.return_value = respuesta
        backend = ZapSignEducationalSignatureBackend()

        resultado = backend.enviar(
            pdf=b'%PDF-1.4\nprivate\n%%EOF',
            nombre_documento='Pagare educativo',
            external_id='edu-artifact-id',
            firmante=FirmantePrueba(),
        )

        payload = post.call_args.kwargs['json']
        self.assertNotIn('url_pdf', payload)
        self.assertEqual(
            base64.b64decode(payload['base64_pdf']),
            b'%PDF-1.4\nprivate\n%%EOF',
        )
        self.assertEqual(payload['external_id'], 'edu-artifact-id')
        self.assertNotIn('require_document_data', payload['signers'][0])
        self.assertNotIn('require_document', payload['signers'][0])
        self.assertEqual(
            payload['signers'][0]['selfie_validation_type'],
            'identity-verification',
        )
        self.assertEqual(post.call_args.kwargs['timeout'], 17)
        self.assertEqual(resultado.token_documento, 'document-token')

    @patch('requests.post')
    def test_rechazo_http_conocido_no_se_clasifica_como_envio_ambiguo(self, post):
        import requests

        respuesta = Mock(status_code=400)
        error = requests.HTTPError(response=respuesta)
        post.side_effect = error

        with self.assertRaises(FirmaEducativaRechazoPermanente) as contexto:
            ZapSignEducationalSignatureBackend().enviar(
                pdf=b'%PDF-1.4\nprivate\n%%EOF',
                nombre_documento='Pagare educativo',
                external_id='edu-invalid-request',
                firmante=FirmantePrueba(),
            )
        self.assertEqual(contexto.exception.codigo, 'SIGNATURE_HTTP_400')

    @patch('requests.get')
    def test_descarga_firmado_solo_desde_host_permitido(self, get):
        detalle = Mock()
        detalle.json.return_value = {
            'status': 'signed',
            'signed_file': 'https://evil.example.com/file.pdf',
        }
        detalle.raise_for_status.return_value = None
        get.return_value = detalle

        with self.assertRaises(FirmaEducativaRespuestaInvalida):
            ZapSignEducationalSignatureBackend().descargar_firmado(
                token_documento='document-token'
            )
        self.assertEqual(get.call_count, 1)

    @patch('requests.get')
    def test_descarga_pdf_firmado_con_timeout(self, get):
        detalle = Mock()
        detalle.json.return_value = {
            'status': 'signed',
            'signed_file': 'https://files.s3.amazonaws.com/signed.pdf',
        }
        detalle.raise_for_status.return_value = None
        archivo = Mock()
        contenido = b'%PDF-1.4\nsigned\n%%EOF'
        archivo.iter_content.return_value = [contenido]
        archivo.raise_for_status.return_value = None
        get.side_effect = [detalle, archivo]

        pdf = ZapSignEducationalSignatureBackend().descargar_firmado(
            token_documento='document-token'
        )

        self.assertEqual(pdf, contenido)
        self.assertEqual(get.call_args_list[0].kwargs['timeout'], 17)
        self.assertEqual(get.call_args_list[1].kwargs['timeout'], 17)
        self.assertTrue(get.call_args_list[1].kwargs['stream'])
        self.assertFalse(get.call_args_list[1].kwargs['allow_redirects'])

    @patch('financiacion_educativa.services.firma_zapsign.MAX_SIGNED_PDF_BYTES', 10)
    @patch('requests.get')
    def test_descarga_firmada_aplica_limite_durante_streaming(self, get):
        detalle = Mock()
        detalle.json.return_value = {
            'status': 'signed',
            'signed_file': 'https://files.s3.amazonaws.com/signed.pdf',
        }
        detalle.raise_for_status.return_value = None
        archivo = Mock()
        archivo.iter_content.return_value = [b'%PDF-', b'123456']
        archivo.raise_for_status.return_value = None
        get.side_effect = [detalle, archivo]

        with self.assertRaisesMessage(
            FirmaEducativaRespuestaInvalida,
            'El archivo firmado supera el tamano permitido.',
        ):
            ZapSignEducationalSignatureBackend().descargar_firmado(
                token_documento='document-token'
            )
