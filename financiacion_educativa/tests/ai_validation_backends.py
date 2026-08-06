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
            decision='ACCEPTED',
            es_documento_identidad=True,
            es_documento_colombiano=True,
            lado_correcto=True,
            campos_visibles=True,
            borrosa=False,
            oscura=False,
            reflejos=False,
            recortada=False,
            obstruida=False,
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


class BackendIANoEsDocumento(BackendIAConcluyente):
    def validar(self, **kwargs):
        return ResultadoValidacionDocumentalIA(
            calidad=Decimal('0.9900'),
            legibilidad=Decimal('0.9900'),
            confianza=Decimal('0.9900'),
            corresponde_tipo=False,
            indicios_imagen_real=False,
            datos_consistentes=False,
            hallazgos=('NOT_IDENTITY_DOCUMENT',),
            proveedor=self.proveedor,
            modelo='test-model',
            decision='REJECTED',
            es_documento_identidad=False,
            razones=('NOT_IDENTITY_DOCUMENT',),
        )


class BackendIALadoIncorrecto(BackendIAConcluyente):
    def validar(self, **kwargs):
        return ResultadoValidacionDocumentalIA(
            calidad=Decimal('0.9900'),
            legibilidad=Decimal('0.9900'),
            confianza=Decimal('0.9900'),
            corresponde_tipo=False,
            indicios_imagen_real=True,
            datos_consistentes=True,
            hallazgos=('SIDE_MISMATCH',),
            proveedor=self.proveedor,
            modelo='test-model',
            decision='REJECTED',
            es_documento_identidad=True,
            es_documento_colombiano=True,
            lado_correcto=False,
            razones=('SIDE_MISMATCH',),
        )


class BackendIAIlegible(BackendIAConcluyente):
    def validar(self, **kwargs):
        return ResultadoValidacionDocumentalIA(
            calidad=Decimal('0.3000'),
            legibilidad=Decimal('0.2000'),
            confianza=Decimal('0.6000'),
            corresponde_tipo=None,
            indicios_imagen_real=None,
            datos_consistentes=None,
            hallazgos=('LOW_LEGIBILITY', 'INCONCLUSIVE'),
            proveedor=self.proveedor,
            modelo='test-model',
            decision='MANUAL_REVIEW',
            borrosa=True,
            razones=('LOW_LEGIBILITY', 'INCONCLUSIVE'),
        )


class BackendIAPasaporteConcluyente(BackendIAConcluyente):
    def validar(self, **kwargs):
        resultado = super().validar(**kwargs)
        return ResultadoValidacionDocumentalIA(
            **{
                **resultado.__dict__,
                'es_documento_colombiano': False,
            }
        )


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
