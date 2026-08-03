from financiacion_educativa.services.escaneo_documentos import (
    ErrorEscaneoDocumento,
    ResultadoAntivirus,
    VeredictoAntivirus,
)


class BackendLimpio:
    proveedor = 'test-double'

    def escanear(self, archivo):
        archivo.read()
        return ResultadoAntivirus(VeredictoAntivirus.CLEAN, 'test-double')


class BackendInfectado:
    proveedor = 'test-double'

    def escanear(self, archivo):
        archivo.read()
        return ResultadoAntivirus(
            VeredictoAntivirus.INFECTED,
            'test-double',
            'Test.Signature',
        )


class BackendNoDisponible:
    proveedor = 'test-double'

    def escanear(self, archivo):
        raise ErrorEscaneoDocumento('SCANNER_UNAVAILABLE')


class BackendTimeout:
    proveedor = 'test-double'

    def escanear(self, archivo):
        raise ErrorEscaneoDocumento('SCANNER_TIMEOUT')


class BackendRespuestaInvalida:
    proveedor = 'test-double'

    def escanear(self, archivo):
        return object()
