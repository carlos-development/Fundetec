from decimal import Decimal

from financiacion_educativa.services.validacion_documental_ia import (
    ErrorValidacionDocumentalIA,
    ResultadoValidacionDocumentalIA,
)


class BackendIAConcluyente:
    enabled = True
    proveedor = 'test-ai'

    def validar(self, **kwargs):
        return ResultadoValidacionDocumentalIA(
            calidad=Decimal('0.9800'),
            legibilidad=Decimal('0.9700'),
            confianza=Decimal('0.9900'),
            corresponde_tipo=True,
            indicios_imagen_real=True,
            datos_consistentes=True,
            proveedor=self.proveedor,
            modelo='test-model',
        )


class BackendIABajaConfianza:
    enabled = True
    proveedor = 'test-ai'

    def validar(self, **kwargs):
        return ResultadoValidacionDocumentalIA(
            calidad=Decimal('0.9500'),
            legibilidad=Decimal('0.9600'),
            confianza=Decimal('0.6000'),
            corresponde_tipo=True,
            indicios_imagen_real=True,
            datos_consistentes=True,
            proveedor=self.proveedor,
            modelo='test-model',
        )


class BackendIAInconsistente:
    enabled = True
    proveedor = 'test-ai'

    def validar(self, **kwargs):
        return ResultadoValidacionDocumentalIA(
            calidad=Decimal('0.9800'),
            legibilidad=Decimal('0.9700'),
            confianza=Decimal('0.9900'),
            corresponde_tipo=True,
            indicios_imagen_real=True,
            datos_consistentes=False,
            hallazgos=('DATA_MISMATCH',),
            proveedor=self.proveedor,
            modelo='test-model',
        )


class BackendIAImagenNoReal:
    enabled = True
    proveedor = 'test-ai'

    def validar(self, **kwargs):
        return ResultadoValidacionDocumentalIA(
            calidad=Decimal('0.9800'),
            legibilidad=Decimal('0.9700'),
            confianza=Decimal('0.9900'),
            corresponde_tipo=True,
            indicios_imagen_real=False,
            datos_consistentes=True,
            hallazgos=('POSSIBLY_NOT_REAL',),
            proveedor=self.proveedor,
            modelo='test-model',
        )


class BackendIAError:
    enabled = True
    proveedor = 'test-ai'

    def validar(self, **kwargs):
        raise ErrorValidacionDocumentalIA('PROVIDER_TIMEOUT')


class BackendIAFallaUnaVez(BackendIAConcluyente):
    intentos = 0

    @classmethod
    def reset(cls):
        cls.intentos = 0

    def validar(self, **kwargs):
        type(self).intentos += 1
        if type(self).intentos == 1:
            raise ErrorValidacionDocumentalIA('PROVIDER_TIMEOUT')
        return super().validar(**kwargs)
