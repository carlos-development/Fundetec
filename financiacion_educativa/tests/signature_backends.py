from financiacion_educativa.services.firma_zapsign import (
    FirmaEducativaEnvioAmbiguo,
    FirmaEducativaError,
    ResultadoEnvioFirma,
)


class AmbiguousEducationalSignatureBackend:
    attempts = 0

    @classmethod
    def reset(cls):
        cls.attempts = 0

    def enviar(self, **kwargs):
        type(self).attempts += 1
        raise FirmaEducativaEnvioAmbiguo('Envio ambiguo controlado.')

    def descargar_firmado(self, **kwargs):
        raise AssertionError('No debe descargar un envio ambiguo.')


class RecordingEducationalSignatureBackend:
    submissions = []
    downloads = []
    fail_send = False
    fail_download = False

    @classmethod
    def reset(cls):
        cls.submissions = []
        cls.downloads = []
        cls.fail_send = False
        cls.fail_download = False

    def enviar(self, **kwargs):
        if self.fail_send:
            raise FirmaEducativaError('Fallo controlado de envio.')
        self.submissions.append(kwargs)
        return ResultadoEnvioFirma(
            token_documento=f'zapsign-test-{len(self.submissions)}',
            estado_proveedor='pending',
        )

    def descargar_firmado(self, *, token_documento):
        if self.fail_download:
            raise FirmaEducativaError('Fallo controlado de descarga.')
        self.downloads.append(token_documento)
        return b'%PDF-1.4\n% educational signed test\n%%EOF'
