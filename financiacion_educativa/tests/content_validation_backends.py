from decimal import Decimal

from financiacion_educativa.choices import (
    CategoriaContenidoDocumento,
    EstadoProcesamientoContenidoDocumento,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.services.clasificacion_contenido_documental import (
    ErrorClasificacionContenido,
    ResultadoClasificacionContenido,
)


def resultado_concluyente(*, tipo_esperado, contexto, **overrides):
    matricula = tipo_esperado == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE
    valores = {
        'categoria': (
            CategoriaContenidoDocumento.ENROLLMENT_EVIDENCE
            if matricula else CategoriaContenidoDocumento.INCOME_CERTIFICATE
        ),
        'confianza_categoria': Decimal('0.95'),
        'legibilidad': Decimal('0.95'),
        'completitud_extraccion': Decimal('0.95'),
        'coincidencia_titular': 'MATCH',
        'coincidencia_emisor': 'MATCH' if matricula else 'NOT_APPLICABLE',
        'fecha_periodo_presente': True,
        'contenido_requerido_presente': True,
        'senales_manipulacion_visible': False,
        'codigos_razon': (),
        'campos_extraidos': {
            'holder_name': contexto['holder_name'],
            'holder_document_number': contexto['holder_document_number'],
            'issuer_name': 'EMISOR SINTETICO',
            'institution_name': contexto['institution_name'] if matricula else '',
            'program_name': contexto['program_name'] if matricula else '',
            'date_or_period': contexto.get('academic_period') or '2026',
            'evidence_kind': 'MATRICULA' if matricula else 'INGRESOS',
            'enrollment_reference': (
                contexto.get('enrollment_reference', '') if matricula else ''
            ),
            'financial_values_present': not matricula,
        },
        'paginas_analizadas': (1,),
        'resultado_general': EstadoProcesamientoContenidoDocumento.ACCEPTED,
        'proveedor': 'fake',
        'modelo': 'content-test-v1',
    }
    valores.update(overrides)
    return ResultadoClasificacionContenido(**valores)


class BackendContenidoConcluyente:
    enabled = True

    def clasificar(self, *, tipo_esperado, contexto, **kwargs):
        return resultado_concluyente(
            tipo_esperado=tipo_esperado,
            contexto=contexto,
        )


class BackendContenidoContradictorio:
    enabled = True

    def clasificar(self, *, tipo_esperado, contexto, **kwargs):
        return resultado_concluyente(
            tipo_esperado=tipo_esperado,
            contexto=contexto,
            campos_extraidos={
                'holder_name': 'PERSONA DISTINTA',
                'holder_document_number': '999999999',
                'issuer_name': 'EMISOR SINTETICO',
                'institution_name': 'INSTITUCION DISTINTA',
                'program_name': 'CURSO DISTINTO',
                'date_or_period': '2026',
                'evidence_kind': 'OTRO',
                'enrollment_reference': 'REFERENCIA-DISTINTA',
                'financial_values_present': True,
            },
        )


class BackendContenidoAmbiguo:
    enabled = True

    def clasificar(self, *, tipo_esperado, contexto, **kwargs):
        return resultado_concluyente(
            tipo_esperado=tipo_esperado,
            contexto=contexto,
            confianza_categoria=Decimal('0.60'),
            coincidencia_titular='INCONCLUSIVE',
            senales_manipulacion_visible=None,
            resultado_general=EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION,
        )


class BackendContenidoTemporal:
    enabled = True

    def clasificar(self, **kwargs):
        raise ErrorClasificacionContenido('PROVIDER_ERROR', temporal=True)


class BackendContenidoPermanente:
    enabled = True

    def clasificar(self, **kwargs):
        raise ErrorClasificacionContenido('PROVIDER_ERROR', temporal=False)
