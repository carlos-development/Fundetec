from django.conf import settings

from contractors.datacredito.dto import (
    FUENTE_NO_CONFIGURADO,
    FUENTE_PROVEEDOR_REAL,
    NIVEL_RIESGO_NO_DISPONIBLE,
    EntradaConsultaDatacreditoPrestador,
    ResultadoDatacreditoPrestador,
)
from contractors.datacredito.mock import ESCENARIO_BUENO, consultar_datacredito_mock
from contractors.datacredito.normalizador import construir_metadata_segura


PROVEEDOR_MOCK = 'mock'
PROVEEDOR_REAL = 'real'


def consultar_datacredito_prestador(entrada: EntradaConsultaDatacreditoPrestador):
    if not getattr(settings, 'CONTRACTORS_DATACREDITO_ENABLED', False):
        return _resultado_no_configurado(entrada)

    proveedor = str(getattr(settings, 'CONTRACTORS_DATACREDITO_PROVIDER', PROVEEDOR_MOCK) or '').lower()
    if proveedor == PROVEEDOR_MOCK:
        escenario = getattr(settings, 'CONTRACTORS_DATACREDITO_MOCK_SCENARIO', ESCENARIO_BUENO)
        return consultar_datacredito_mock(entrada, escenario=escenario)

    if proveedor == PROVEEDOR_REAL:
        return ResultadoDatacreditoPrestador(
            disponible=False,
            fuente=FUENTE_PROVEEDOR_REAL,
            nivel_riesgo=NIVEL_RIESGO_NO_DISPONIBLE,
            requiere_revision_manual=True,
            error_tipo='proveedor_real_no_implementado',
            metadata_segura=construir_metadata_segura(
                entrada,
                fuente=FUENTE_PROVEEDOR_REAL,
                proveedor=PROVEEDOR_REAL,
            ),
        )

    return ResultadoDatacreditoPrestador(
        disponible=False,
        fuente=FUENTE_NO_CONFIGURADO,
        nivel_riesgo=NIVEL_RIESGO_NO_DISPONIBLE,
        requiere_revision_manual=True,
        error_tipo='proveedor_datacredito_no_valido',
        metadata_segura=construir_metadata_segura(entrada, fuente=FUENTE_NO_CONFIGURADO, proveedor=proveedor),
    )


def _resultado_no_configurado(entrada):
    return ResultadoDatacreditoPrestador(
        disponible=False,
        fuente=FUENTE_NO_CONFIGURADO,
        nivel_riesgo=NIVEL_RIESGO_NO_DISPONIBLE,
        requiere_revision_manual=True,
        error_tipo='datacredito_no_configurado',
        metadata_segura=construir_metadata_segura(entrada, fuente=FUENTE_NO_CONFIGURADO),
    )
