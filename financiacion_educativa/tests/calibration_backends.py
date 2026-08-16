from decimal import Decimal

from financiacion_educativa.services.clasificacion_contenido_documental import (
    ErrorClasificacionContenido,
)
from financiacion_educativa.services.validacion_documental_ia import (
    ErrorValidacionDocumentalIA,
    ResultadoValidacionDocumentalIA,
)
from financiacion_educativa.tests.content_validation_backends import (
    resultado_concluyente,
)


def identity_result(**overrides):
    values = {
        'calidad': Decimal('0.9600'),
        'legibilidad': Decimal('0.9700'),
        'confianza': Decimal('0.9800'),
        'corresponde_tipo': True,
        'indicios_imagen_real': True,
        'datos_consistentes': True,
        'proveedor': 'calibration-fake',
        'modelo': 'calibration-v3',
        'decision': 'ACCEPTED',
        'es_documento_identidad': True,
        'es_documento_colombiano': True,
        'lado_correcto': True,
        'campos_visibles': True,
        'borrosa': False,
        'oscura': False,
        'reflejos': False,
        'recortada': False,
        'obstruida': False,
        'version_esquema': '3',
        'captura_documento_fisico': True,
        'senales_manipulacion_visible': False,
        'integridad_visual': True,
        'confianza_tipo_documental': Decimal('0.9800'),
        'confianza_lado': Decimal('0.9800'),
        'confianza_legibilidad': Decimal('0.9800'),
        'confianza_integridad_visual': Decimal('0.9800'),
        'confianza_datos': Decimal('0.9800'),
        'confianza_captura_fisica': Decimal('0.9800'),
        'confianza_manipulacion': Decimal('0.9800'),
        'metricas_uso': {
            'input_tokens': 100,
            'output_tokens': 20,
            'total_tokens': 120,
        },
    }
    values.update(overrides)
    return ResultadoValidacionDocumentalIA(**values)


class CalibrationIdentityConclusiveBackend:
    calls = 0
    enabled = True

    @classmethod
    def reset(cls):
        cls.calls = 0

    def validar(self, **kwargs):
        type(self).calls += 1
        return identity_result()


class CalibrationIdentityTemporaryBackend:
    calls = 0
    enabled = True

    @classmethod
    def reset(cls):
        cls.calls = 0

    def validar(self, **kwargs):
        type(self).calls += 1
        raise ErrorValidacionDocumentalIA('PROVIDER_TIMEOUT')


class CalibrationIdentityPermanentBackend:
    calls = 0
    enabled = True

    @classmethod
    def reset(cls):
        cls.calls = 0

    def validar(self, **kwargs):
        type(self).calls += 1
        raise ErrorValidacionDocumentalIA('CONFIGURATION_ERROR')


class CalibrationIdentityMalformedBackend:
    calls = 0
    enabled = True

    @classmethod
    def reset(cls):
        cls.calls = 0

    def validar(self, **kwargs):
        type(self).calls += 1
        return {'unexpected': True}


class CalibrationContentConclusiveBackend:
    calls = 0
    enabled = True

    @classmethod
    def reset(cls):
        cls.calls = 0

    def clasificar(self, *, tipo_esperado, contexto, **kwargs):
        type(self).calls += 1
        return resultado_concluyente(
            tipo_esperado=tipo_esperado,
            contexto=contexto,
            metricas_uso={
                'input_tokens': 200,
                'output_tokens': 30,
                'total_tokens': 230,
            },
        )


class CalibrationContentTemporaryBackend:
    calls = 0
    enabled = True

    @classmethod
    def reset(cls):
        cls.calls = 0

    def clasificar(self, **kwargs):
        type(self).calls += 1
        raise ErrorClasificacionContenido('PROVIDER_ERROR', temporal=True)


class CalibrationContentPermanentBackend:
    calls = 0
    enabled = True

    @classmethod
    def reset(cls):
        cls.calls = 0

    def clasificar(self, **kwargs):
        type(self).calls += 1
        raise ErrorClasificacionContenido('CONFIGURATION_ERROR')


class CalibrationContentMalformedBackend:
    calls = 0
    enabled = True

    @classmethod
    def reset(cls):
        cls.calls = 0

    def clasificar(self, **kwargs):
        type(self).calls += 1
        return {'unexpected': True}
