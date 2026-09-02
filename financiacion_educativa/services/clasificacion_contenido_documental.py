import base64
import hashlib
import hmac
import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.module_loading import import_string
from PIL import Image, UnidentifiedImageError

from financiacion_educativa.choices import (
    CategoriaContenidoDocumento,
    EstadoEscaneoDocumento,
    EstadoProcesamientoContenidoDocumento,
    EstadoValidacionDocumento,
    MetodoExtraccionContenido,
    MotivoRechazoDocumento,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import (
    DocumentoFinanciacion,
    ProcesamientoContenidoDocumento,
)
from financiacion_educativa.services.procesamiento_pdf import (
    ErrorProcesamientoPDF,
    PaginaExtraida,
    procesar_pdf_seguro,
)
from financiacion_educativa.services.metricas_openai import extraer_metricas_uso


CATEGORIAS_INGRESOS = frozenset({
    CategoriaContenidoDocumento.EMPLOYMENT_CERTIFICATE,
    CategoriaContenidoDocumento.INCOME_CERTIFICATE,
    CategoriaContenidoDocumento.INCOME_AND_WITHHOLDING_CERTIFICATE,
    CategoriaContenidoDocumento.BANK_STATEMENT,
    CategoriaContenidoDocumento.PAYSLIP,
})
PUNTAJES_PERMITIDOS = [indice / 100 for indice in range(101)]
CATEGORIAS_PERMITIDAS = frozenset({
    *CATEGORIAS_INGRESOS,
    CategoriaContenidoDocumento.ENROLLMENT_EVIDENCE,
    CategoriaContenidoDocumento.UNRELATED,
    CategoriaContenidoDocumento.INCONCLUSIVE,
})
RESULTADOS_PERMITIDOS = frozenset({
    EstadoProcesamientoContenidoDocumento.ACCEPTED,
    EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED,
    EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION,
})
MATCHES = frozenset({'MATCH', 'MISMATCH', 'INCONCLUSIVE', 'NOT_APPLICABLE'})
CODIGOS_RAZON = frozenset({
    'ACCEPTED',
    'CATEGORY_MISMATCH',
    'CONTENT_INSUFFICIENT',
    'DATA_MISMATCH',
    'DATE_OR_PERIOD_MISSING',
    'DOCUMENT_UNREADABLE',
    'INCONCLUSIVE',
    'INSTITUTION_MISMATCH',
    'LOW_CONFIDENCE',
    'REQUIRED_CONTENT_MISSING',
    'TAMPERING_SIGNALS',
    'PDF_ACTIVE_CONTENT',
    'PDF_ADDITIONAL_ACTION',
    'PDF_CORRUPT',
    'PDF_EMBEDDED_FILE',
    'PDF_ENCRYPTED',
    'PDF_INVALID_SIGNATURE',
    'PDF_JAVASCRIPT',
    'PDF_LAUNCH_ACTION',
    'PDF_NO_PAGES',
    'PDF_OBJECT_TOO_LARGE',
    'PDF_OPEN_ACTION',
    'PDF_PIXEL_LIMIT_EXCEEDED',
    'PDF_PROCESSING_TIMEOUT',
    'PDF_RENDER_ERROR',
    'PDF_RENDERER_UNAVAILABLE',
    'PDF_TEXT_EXTRACTION_ERROR',
    'PDF_TEXT_LIMIT_EXCEEDED',
    'PDF_TOO_LARGE',
    'PDF_TOO_MANY_OBJECTS',
    'PDF_TOO_MANY_PAGES',
    'PDF_RICH_MEDIA',
    'PDF_SUBMIT_FORM',
    'PDF_XFA_ACTIVE_CONTENT',
    'PROVIDER_ERROR',
    'INVALID_RESPONSE',
    'IMAGE_CORRUPT',
    'IMAGE_TOO_LARGE',
})

RAZONES_SEGURIDAD_PDF = frozenset({
    'PDF_ACTIVE_CONTENT',
    'PDF_ADDITIONAL_ACTION',
    'PDF_EMBEDDED_FILE',
    'PDF_JAVASCRIPT',
    'PDF_LAUNCH_ACTION',
    'PDF_OPEN_ACTION',
    'PDF_RICH_MEDIA',
    'PDF_SUBMIT_FORM',
    'PDF_XFA_ACTIVE_CONTENT',
})
OBSERVACION_POR_RAZON = {
    'CATEGORY_MISMATCH': (
        MotivoRechazoDocumento.WRONG_DOCUMENT,
        'El contenido no corresponde al tipo documental solicitado.',
    ),
    'DATA_MISMATCH': (
        MotivoRechazoDocumento.DATA_MISMATCH,
        'Los datos del documento no coinciden con la solicitud.',
    ),
    'INSTITUTION_MISMATCH': (
        MotivoRechazoDocumento.DATA_MISMATCH,
        'La institucion o el curso del documento no coincide con la solicitud.',
    ),
    'DOCUMENT_UNREADABLE': (
        MotivoRechazoDocumento.UNREADABLE,
        'El contenido del documento no es legible.',
    ),
    'PDF_CORRUPT': (
        MotivoRechazoDocumento.UNREADABLE,
        'El PDF esta corrupto o no puede leerse de forma segura.',
    ),
    'PDF_RENDER_ERROR': (
        MotivoRechazoDocumento.UNREADABLE,
        'El PDF no pudo representarse de forma segura.',
    ),
    'REQUIRED_CONTENT_MISSING': (
        MotivoRechazoDocumento.INCOMPLETE,
        'El documento no contiene toda la informacion requerida.',
    ),
    'CONTENT_INSUFFICIENT': (
        MotivoRechazoDocumento.INCOMPLETE,
        'El contenido aportado es insuficiente para continuar.',
    ),
    'PDF_EMBEDDED_FILE': (
        MotivoRechazoDocumento.OTHER,
        'El PDF contiene archivos incrustados. Carga una version sin adjuntos.',
    ),
    'PDF_JAVASCRIPT': (
        MotivoRechazoDocumento.OTHER,
        'El PDF contiene JavaScript. Carga una version sin contenido activo.',
    ),
    'PDF_OPEN_ACTION': (
        MotivoRechazoDocumento.OTHER,
        'El PDF contiene una accion automatica de apertura.',
    ),
    'PDF_ADDITIONAL_ACTION': (
        MotivoRechazoDocumento.OTHER,
        'El PDF contiene acciones automaticas adicionales.',
    ),
    'PDF_LAUNCH_ACTION': (
        MotivoRechazoDocumento.OTHER,
        'El PDF contiene una accion de lanzamiento no permitida.',
    ),
    'PDF_XFA_ACTIVE_CONTENT': (
        MotivoRechazoDocumento.OTHER,
        'El PDF contiene un formulario XFA activo no permitido.',
    ),
    'PDF_RICH_MEDIA': (
        MotivoRechazoDocumento.OTHER,
        'El PDF contiene contenido multimedia activo no permitido.',
    ),
    'PDF_SUBMIT_FORM': (
        MotivoRechazoDocumento.OTHER,
        'El PDF contiene una accion de envio de formulario no permitida.',
    ),
    'PDF_ACTIVE_CONTENT': (
        MotivoRechazoDocumento.OTHER,
        'El PDF contiene una caracteristica activa no permitida.',
    ),
    'PDF_ENCRYPTED': (
        MotivoRechazoDocumento.OTHER,
        'El PDF esta protegido con contrasena y no puede validarse.',
    ),
    'PDF_INVALID_SIGNATURE': (
        MotivoRechazoDocumento.UNREADABLE,
        'El archivo no tiene una estructura PDF valida.',
    ),
    'PDF_NO_PAGES': (
        MotivoRechazoDocumento.UNREADABLE,
        'El PDF no contiene paginas que puedan validarse.',
    ),
    'PDF_OBJECT_TOO_LARGE': (
        MotivoRechazoDocumento.OTHER,
        'El PDF contiene un objeto que supera el limite permitido.',
    ),
    'PDF_PIXEL_LIMIT_EXCEEDED': (
        MotivoRechazoDocumento.OTHER,
        'Una pagina del PDF supera el limite de resolucion permitido.',
    ),
    'PDF_TEXT_LIMIT_EXCEEDED': (
        MotivoRechazoDocumento.OTHER,
        'El texto del PDF supera el limite permitido.',
    ),
    'PDF_TOO_LARGE': (
        MotivoRechazoDocumento.OTHER,
        'El PDF supera el tamano permitido.',
    ),
    'PDF_TOO_MANY_OBJECTS': (
        MotivoRechazoDocumento.OTHER,
        'La estructura del PDF supera el limite permitido.',
    ),
    'PDF_TOO_MANY_PAGES': (
        MotivoRechazoDocumento.OTHER,
        'El PDF supera la cantidad de paginas permitida.',
    ),
}
PRIORIDAD_RAZONES_CORRECCION = (
    *sorted(RAZONES_SEGURIDAD_PDF),
    'PDF_ENCRYPTED',
    'DATA_MISMATCH',
    'INSTITUTION_MISMATCH',
    'CATEGORY_MISMATCH',
    'DOCUMENT_UNREADABLE',
    'PDF_CORRUPT',
    'PDF_RENDER_ERROR',
    'REQUIRED_CONTENT_MISSING',
    'CONTENT_INSUFFICIENT',
    'PDF_INVALID_SIGNATURE',
    'PDF_NO_PAGES',
    'PDF_OBJECT_TOO_LARGE',
    'PDF_PIXEL_LIMIT_EXCEEDED',
    'PDF_TEXT_LIMIT_EXCEEDED',
    'PDF_TOO_LARGE',
    'PDF_TOO_MANY_OBJECTS',
    'PDF_TOO_MANY_PAGES',
)


def resolver_rechazo_contenido(codigos_razon):
    razones = set(codigos_razon)
    for codigo in PRIORIDAD_RAZONES_CORRECCION:
        if codigo in razones and codigo in OBSERVACION_POR_RAZON:
            return OBSERVACION_POR_RAZON[codigo]
    return (
        MotivoRechazoDocumento.OTHER,
        'El documento requiere correccion antes de continuar.',
    )


class ErrorClasificacionContenido(Exception):
    def __init__(self, codigo, *, temporal=False):
        self.codigo = codigo
        self.temporal = temporal
        super().__init__(codigo)


@dataclass(frozen=True)
class ResultadoClasificacionContenido:
    categoria: str
    confianza_categoria: Decimal
    legibilidad: Decimal
    completitud_extraccion: Decimal
    coincidencia_titular: str
    coincidencia_emisor: str
    fecha_periodo_presente: bool
    contenido_requerido_presente: bool
    senales_manipulacion_visible: bool | None
    codigos_razon: tuple[str, ...]
    campos_extraidos: dict
    paginas_analizadas: tuple[int, ...]
    resultado_general: str
    proveedor: str = ''
    modelo: str = ''
    metricas_uso: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ResultadoProcesamientoContenido:
    estado: str
    codigo: str
    procesamiento_id: object = None
    documento_id: object = None


class DisabledContentDocumentClassificationBackend:
    enabled = False

    def clasificar(self, **kwargs):
        raise ErrorClasificacionContenido('BACKEND_DISABLED')


class OpenAIContentDocumentClassificationBackend:
    enabled = True
    proveedor = 'openai'

    def __init__(self):
        self.modelo = settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MODEL

    def clasificar(self, *, paginas, tipo_esperado, contexto):
        if not settings.OPENAI_API_KEY or not self.modelo:
            raise ErrorClasificacionContenido('PROVIDER_ERROR', temporal=True)
        contexto_proveedor = dict(contexto)
        numero_declarado = contexto_proveedor.pop('holder_document_number', '')
        contexto_proveedor['holder_document_suffix'] = numero_declarado[-4:]
        contenido_usuario = [{
            'type': 'input_text',
            'text': json.dumps(
                {
                    'expected_document_type': tipo_esperado,
                    'declared_context': contexto_proveedor,
                    'page_text': [
                        {'page': p.numero, 'text': _minimizar_texto_proveedor(p.texto)}
                        for p in paginas
                        if p.texto
                    ],
                },
                ensure_ascii=True,
                separators=(',', ':'),
            ),
        }]
        for pagina in paginas:
            if pagina.imagen_png:
                contenido_usuario.append({
                    'type': 'input_image',
                    'image_url': (
                        'data:image/png;base64,'
                        + base64.b64encode(pagina.imagen_png).decode('ascii')
                    ),
                    'detail': 'high',
                })
        try:
            from openai import OpenAI

            cliente = OpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_TIMEOUT_SECONDS,
            )
            respuesta = cliente.responses.create(
                model=self.modelo,
                store=False,
                input=[
                    {
                        'role': 'system',
                        'content': [{
                            'type': 'input_text',
                            'text': (
                                'Clasifica evidencia documental educativa sin evaluar '
                                'solvencia, riesgo o autenticidad oficial. Para evidencia '
                                'de ingresos admite certificados laborales, comprobantes '
                                'de nomina, extractos y certificados de ingresos o de '
                                'ingresos y retenciones; no exijas un titulo literal. No infieras '
                                'datos ausentes. Los numeros de cuenta no deben '
                                'devolverse. Una duda legitima produce MANUAL_EXCEPTION; '
                                'un documento distinto o contradictorio produce '
                                'CORRECTION_REQUIRED. Usa puntajes entre 0 y 1.'
                            ),
                        }],
                    },
                    {'role': 'user', 'content': contenido_usuario},
                ],
                text={
                    'format': {
                        'type': 'json_schema',
                        'name': 'educational_content_classification',
                        'strict': True,
                        'schema': esquema_clasificacion_contenido(),
                    },
                },
            )
            payload = json.loads(respuesta.output_text)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ErrorClasificacionContenido('INVALID_RESPONSE') from error
        except Exception as error:
            raise ErrorClasificacionContenido('PROVIDER_ERROR', temporal=True) from error
        return normalizar_clasificacion(
            payload,
            proveedor=self.proveedor,
            modelo=self.modelo,
            metricas_uso=extraer_metricas_uso(respuesta),
        )


def esquema_clasificacion_contenido():
    nullable_text = {'type': ['string', 'null']}
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': [
            'document_category', 'category_confidence', 'legibility',
            'extraction_completeness', 'holder_match',
            'institution_or_issuer_match', 'date_or_period_present',
            'required_content_present', 'visible_tampering_signals',
            'reason_codes', 'extracted_fields', 'analyzed_pages',
            'overall_outcome',
        ],
        'properties': {
            'document_category': {
                'type': 'string', 'enum': sorted(CATEGORIAS_PERMITIDAS),
            },
            'category_confidence': {'type': 'number', 'enum': PUNTAJES_PERMITIDOS},
            'legibility': {'type': 'number', 'enum': PUNTAJES_PERMITIDOS},
            'extraction_completeness': {'type': 'number', 'enum': PUNTAJES_PERMITIDOS},
            'holder_match': {'type': 'string', 'enum': sorted(MATCHES)},
            'institution_or_issuer_match': {
                'type': 'string', 'enum': sorted(MATCHES),
            },
            'date_or_period_present': {'type': 'boolean'},
            'required_content_present': {'type': 'boolean'},
            'visible_tampering_signals': {'type': ['boolean', 'null']},
            'reason_codes': {
                'type': 'array',
                'items': {'type': 'string', 'enum': sorted(CODIGOS_RAZON)},
            },
            'extracted_fields': {
                'type': 'object',
                'additionalProperties': False,
                'required': [
                    'holder_name', 'holder_document_number', 'issuer_name',
                    'institution_name', 'program_name', 'date_or_period',
                    'evidence_kind', 'enrollment_reference',
                    'financial_values_present',
                ],
                'properties': {
                    'holder_name': nullable_text,
                    'holder_document_number': nullable_text,
                    'issuer_name': nullable_text,
                    'institution_name': nullable_text,
                    'program_name': nullable_text,
                    'date_or_period': nullable_text,
                    'evidence_kind': nullable_text,
                    'enrollment_reference': nullable_text,
                    'financial_values_present': {'type': ['boolean', 'null']},
                },
            },
            'analyzed_pages': {
                'type': 'array', 'items': {'type': 'integer'},
            },
            'overall_outcome': {
                'type': 'string', 'enum': sorted(RESULTADOS_PERMITIDOS),
            },
        },
    }


def _decimal(valor, campo):
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ErrorClasificacionContenido('INVALID_RESPONSE') from error
    if not numero.is_finite() or not Decimal('0') <= numero <= Decimal('1'):
        raise ErrorClasificacionContenido('INVALID_RESPONSE')
    return numero.quantize(Decimal('0.0001'))


def _texto(valor, limite=200):
    if valor is None:
        return ''
    valor = unicodedata.normalize('NFKC', str(valor))
    return re.sub(r"[^\w .,'\-/#]", '', valor, flags=re.UNICODE).strip()[:limite]


def _minimizar_texto_proveedor(texto):
    texto = str(texto or '')
    texto = re.sub(
        r'\b\d{8,}\b',
        lambda coincidencia: f'[NUMBER_END_{coincidencia.group(0)[-4:]}]',
        texto,
    )
    texto = re.sub(
        r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b',
        '[EMAIL_REDACTED]',
        texto,
        flags=re.IGNORECASE,
    )
    return texto


def normalizar_clasificacion(
    payload,
    *,
    proveedor='',
    modelo='',
    metricas_uso=None,
):
    if not isinstance(payload, dict):
        raise ErrorClasificacionContenido('INVALID_RESPONSE')
    campos_payload = {
        'document_category', 'category_confidence', 'legibility',
        'extraction_completeness', 'holder_match',
        'institution_or_issuer_match', 'date_or_period_present',
        'required_content_present', 'visible_tampering_signals',
        'reason_codes', 'extracted_fields', 'analyzed_pages',
        'overall_outcome',
    }
    campos_extraidos_esperados = {
        'holder_name', 'holder_document_number', 'issuer_name',
        'institution_name', 'program_name', 'date_or_period',
        'evidence_kind', 'enrollment_reference', 'financial_values_present',
    }
    if set(payload) != campos_payload:
        raise ErrorClasificacionContenido('INVALID_RESPONSE')
    campos = payload.get('extracted_fields')
    razones = payload.get('reason_codes')
    paginas = payload.get('analyzed_pages')
    if (
        not isinstance(campos, dict)
        or set(campos) != campos_extraidos_esperados
        or not isinstance(razones, list)
        or not isinstance(paginas, list)
    ):
        raise ErrorClasificacionContenido('INVALID_RESPONSE')
    categoria = payload.get('document_category')
    resultado = payload.get('overall_outcome')
    titular = payload.get('holder_match')
    emisor = payload.get('institution_or_issuer_match')
    if (
        categoria not in CATEGORIAS_PERMITIDAS
        or resultado not in RESULTADOS_PERMITIDOS
        or titular not in MATCHES
        or emisor not in MATCHES
        or any(codigo not in CODIGOS_RAZON for codigo in razones)
        or any(
            isinstance(p, bool)
            or not isinstance(p, int)
            or not 1 <= p <= settings.FINANCIACION_EDUCATIVA_PDF_MAX_PAGES
            for p in paginas
        )
        or not isinstance(payload.get('date_or_period_present'), bool)
        or not isinstance(payload.get('required_content_present'), bool)
        or payload.get('visible_tampering_signals') not in {True, False, None}
        or campos.get('financial_values_present') not in {True, False, None}
        or any(
            campos.get(nombre) is not None
            and not isinstance(campos.get(nombre), str)
            for nombre in campos_extraidos_esperados - {'financial_values_present'}
        )
    ):
        raise ErrorClasificacionContenido('INVALID_RESPONSE')
    return ResultadoClasificacionContenido(
        categoria=categoria,
        confianza_categoria=_decimal(payload.get('category_confidence'), 'category_confidence'),
        legibilidad=_decimal(payload.get('legibility'), 'legibility'),
        completitud_extraccion=_decimal(payload.get('extraction_completeness'), 'extraction_completeness'),
        coincidencia_titular=titular,
        coincidencia_emisor=emisor,
        fecha_periodo_presente=payload.get('date_or_period_present') is True,
        contenido_requerido_presente=payload.get('required_content_present') is True,
        senales_manipulacion_visible=(
            payload.get('visible_tampering_signals')
            if isinstance(payload.get('visible_tampering_signals'), bool)
            else None
        ),
        codigos_razon=tuple(dict.fromkeys(razones)),
        campos_extraidos={
            'holder_name': _texto(campos.get('holder_name')),
            'holder_document_number': _texto(campos.get('holder_document_number'), 40),
            'issuer_name': _texto(campos.get('issuer_name')),
            'institution_name': _texto(campos.get('institution_name')),
            'program_name': _texto(campos.get('program_name')),
            'date_or_period': _texto(campos.get('date_or_period'), 80),
            'evidence_kind': _texto(campos.get('evidence_kind'), 80),
            'enrollment_reference': _texto(
                campos.get('enrollment_reference'), 120
            ),
            'financial_values_present': (
                campos.get('financial_values_present')
                if isinstance(campos.get('financial_values_present'), bool)
                else None
            ),
        },
        paginas_analizadas=tuple(dict.fromkeys(paginas)),
        resultado_general=resultado,
        proveedor=_texto(proveedor, 60),
        modelo=_texto(modelo, 80),
        metricas_uso=dict(metricas_uso or {}),
    )


def _normalizar_comparacion(valor):
    valor = unicodedata.normalize('NFKD', str(valor or ''))
    valor = ''.join(c for c in valor if not unicodedata.combining(c))
    return re.sub(r'[^A-Z0-9 ]', ' ', valor.upper()).split()


def _coincide_texto(declarado, extraido):
    esperado = set(_normalizar_comparacion(declarado))
    observado = set(_normalizar_comparacion(extraido))
    if not esperado or not observado:
        return None
    comunes = esperado & observado
    return len(comunes) >= min(2, len(esperado), len(observado))


def _contexto_documento(documento):
    solicitud = documento.solicitud
    participante = documento.participante
    return {
        'holder_name': (
            f'{participante.nombres} {participante.apellidos}'
            if participante else f'{solicitud.nombres} {solicitud.apellidos}'
        ),
        'holder_document_number': (
            participante.numero_documento if participante else ''
        ),
        'institution_name': solicitud.institucion.nombre_comercial,
        'program_name': solicitud.nombre_curso,
        'academic_period': solicitud.periodo_academico,
        'enrollment_reference': solicitud.codigo_matricula,
    }


def _aplicar_consistencia_determinista(resultado, *, contexto, tipo):
    campos = resultado.campos_extraidos
    titular = _coincide_texto(contexto['holder_name'], campos['holder_name'])
    if titular is True:
        coincidencia_titular = 'MATCH'
    elif titular is False:
        coincidencia_titular = 'MISMATCH'
    else:
        coincidencia_titular = resultado.coincidencia_titular
    numero = re.sub(r'[^A-Z0-9]', '', campos['holder_document_number'].upper())
    esperado = re.sub(r'[^A-Z0-9]', '', contexto['holder_document_number'].upper())
    razones = list(resultado.codigos_razon)
    if (
        numero
        and esperado
        and not (
            numero == esperado
            or (len(numero) <= 4 and esperado.endswith(numero))
        )
    ):
        coincidencia_titular = 'MISMATCH'
        razones.append('DATA_MISMATCH')
    coincidencia_emisor = resultado.coincidencia_emisor
    if tipo == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE:
        institucion = _coincide_texto(
            contexto['institution_name'], campos['institution_name']
        )
        programa = _coincide_texto(contexto['program_name'], campos['program_name'])
        if institucion is False or programa is False:
            coincidencia_emisor = 'MISMATCH'
            razones.append('INSTITUTION_MISMATCH')
        elif institucion is True and programa is not False:
            coincidencia_emisor = 'MATCH'
        referencia = _normalizar_comparacion(campos.get('enrollment_reference'))
        referencia_esperada = _normalizar_comparacion(
            contexto.get('enrollment_reference')
        )
        if referencia and referencia_esperada and referencia != referencia_esperada:
            razones.append('DATA_MISMATCH')
            coincidencia_emisor = 'MISMATCH'
    return replace(
        resultado,
        coincidencia_titular=coincidencia_titular,
        coincidencia_emisor=coincidencia_emisor,
        codigos_razon=tuple(dict.fromkeys(razones)),
    )


def decidir_politica_contenido(resultado, *, tipo):
    razones = list(resultado.codigos_razon)
    categoria_valida = (
        resultado.categoria in CATEGORIAS_INGRESOS
        if tipo == TipoDocumentoFinanciacion.INCOME_CERTIFICATE
        else resultado.categoria == CategoriaContenidoDocumento.ENROLLMENT_EVIDENCE
    )
    if resultado.categoria == CategoriaContenidoDocumento.UNRELATED or not categoria_valida:
        razones.append('CATEGORY_MISMATCH')
        return EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED, razones
    if resultado.coincidencia_titular == 'MISMATCH':
        razones.append('DATA_MISMATCH')
        return EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED, razones
    if (
        tipo == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE
        and resultado.coincidencia_emisor == 'MISMATCH'
    ):
        razones.append('INSTITUTION_MISMATCH')
        return EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED, razones
    if resultado.legibilidad < Decimal(settings.FINANCIACION_EDUCATIVA_CONTENT_MIN_LEGIBILITY):
        razones.append('DOCUMENT_UNREADABLE')
        return EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED, razones
    campos = resultado.campos_extraidos
    campos_comunes_ingresos = bool(
        campos.get('holder_name')
        and campos.get('date_or_period')
        and (campos.get('issuer_name') or campos.get('institution_name'))
    )
    if resultado.categoria == CategoriaContenidoDocumento.EMPLOYMENT_CERTIFICATE:
        campos_minimos = campos_comunes_ingresos and bool(campos.get('evidence_kind'))
    elif resultado.categoria in CATEGORIAS_INGRESOS:
        campos_minimos = campos_comunes_ingresos and (
            campos.get('financial_values_present') is True
        )
    elif resultado.categoria == CategoriaContenidoDocumento.ENROLLMENT_EVIDENCE:
        campos_minimos = bool(
            campos.get('holder_name')
            and campos.get('institution_name')
            and campos.get('program_name')
            and campos.get('date_or_period')
            and campos.get('evidence_kind')
        )
    else:
        campos_minimos = False
    if not resultado.contenido_requerido_presente or not campos_minimos:
        razones.append('REQUIRED_CONTENT_MISSING')
        return EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED, razones
    if not resultado.fecha_periodo_presente:
        razones.append('DATE_OR_PERIOD_MISSING')
        return EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION, razones
    if resultado.senales_manipulacion_visible is True:
        razones.append('TAMPERING_SIGNALS')
        return EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION, razones
    umbral = Decimal(settings.FINANCIACION_EDUCATIVA_CONTENT_MIN_CONFIDENCE)
    if (
        resultado.confianza_categoria < umbral
        or resultado.completitud_extraccion < Decimal(
            settings.FINANCIACION_EDUCATIVA_CONTENT_MIN_COMPLETENESS
        )
        or resultado.coincidencia_titular != 'MATCH'
        or resultado.senales_manipulacion_visible is None
    ):
        razones.append('LOW_CONFIDENCE')
        return EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION, razones
    if (
        tipo == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE
        and resultado.coincidencia_emisor != 'MATCH'
    ):
        razones.append('INCONCLUSIVE')
        return EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION, razones
    return EstadoProcesamientoContenidoDocumento.ACCEPTED, ['ACCEPTED']


def obtener_backend_clasificacion_contenido():
    clase = import_string(settings.FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND)
    return clase()


def _leer_documento(documento):
    with documento.archivo.open('rb') as archivo:
        contenido = archivo.read(settings.FINANCIACION_EDUCATIVA_PDF_MAX_BYTES + 1)
    if not contenido:
        raise ErrorClasificacionContenido('READ_ERROR')
    return contenido


def _preparar_imagen(contenido):
    try:
        with Image.open(BytesIO(contenido)) as imagen:
            ancho, alto = imagen.size
            imagen.verify()
        with Image.open(BytesIO(contenido)) as imagen:
            imagen.load()
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise ErrorClasificacionContenido('IMAGE_CORRUPT') from error
    if ancho * alto > settings.FINANCIACION_EDUCATIVA_PDF_MAX_PIXELS_PER_PAGE:
        raise ErrorClasificacionContenido('IMAGE_TOO_LARGE')
    return (PaginaExtraida(numero=1, texto='', imagen_png=contenido),)


def _paginas_para_clasificacion(paginas):
    maximo = settings.FINANCIACION_EDUCATIVA_PDF_MAX_AI_PAGES
    seleccionadas = []
    for pagina in paginas:
        if pagina.imagen_png and pagina not in seleccionadas:
            seleccionadas.append(pagina)
        if len(seleccionadas) >= maximo:
            return tuple(seleccionadas)
    for pagina in paginas:
        if pagina not in seleccionadas:
            seleccionadas.append(pagina)
        if len(seleccionadas) >= maximo:
            break
    return tuple(seleccionadas)


def _iniciar(documento):
    with transaction.atomic():
        documento = (
            DocumentoFinanciacion.objects.select_for_update(of=('self',))
            .select_related('solicitud__institucion', 'participante')
            .get(pk=documento.pk)
        )
        existente = documento.procesamientos_contenido.order_by('-numero').first()
        if existente and existente.hash_original == documento.sha256:
            if existente.estado == EstadoProcesamientoContenidoDocumento.STARTED:
                antiguedad = (timezone.now() - existente.iniciado_en).total_seconds()
                if antiguedad < settings.FINANCIACION_EDUCATIVA_CONTENT_STALE_SECONDS:
                    return documento, existente, 'IN_PROGRESS'
                ProcesamientoContenidoDocumento.objects.finalizar_desde_servicio(
                    pk=existente.pk,
                    valores={
                        'estado': EstadoProcesamientoContenidoDocumento.RETRYING,
                        'codigos_razon': ['PDF_PROCESSING_TIMEOUT'],
                        'finalizado_en': timezone.now(),
                    },
                )
            elif existente.estado != EstadoProcesamientoContenidoDocumento.RETRYING:
                return documento, existente, existente.estado
        numero = (
            documento.procesamientos_contenido.aggregate(maximo=Max('numero'))['maximo']
            or 0
        ) + 1
        try:
            procesamiento = ProcesamientoContenidoDocumento.objects.create(
                documento=documento,
                numero=numero,
                hash_original=documento.sha256,
                content_type=documento.content_type,
                tamano_bytes=documento.tamano_bytes or 0,
                version_procesador=settings.FINANCIACION_EDUCATIVA_CONTENT_PROCESSOR_VERSION,
                version_esquema=settings.FINANCIACION_EDUCATIVA_CONTENT_SCHEMA_VERSION,
                version_politica=settings.FINANCIACION_EDUCATIVA_CONTENT_POLICY_VERSION,
            )
        except IntegrityError:
            return documento, documento.procesamientos_contenido.get(
                estado=EstadoProcesamientoContenidoDocumento.STARTED
            ), 'IN_PROGRESS'
    return documento, procesamiento, ''


def _campos_persistibles(resultado):
    campos = dict(resultado.campos_extraidos)
    numero = campos.pop('holder_document_number', '')
    if numero:
        campos['holder_document_hash'] = hmac.new(
            settings.FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY.encode('utf-8'),
            numero.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
    return campos


@transaction.atomic
def _finalizar(
    procesamiento,
    *,
    estado,
    resultado=None,
    razones=(),
    pdf=None,
    metadata_pdf=None,
):
    procesamiento = ProcesamientoContenidoDocumento.objects.select_for_update().get(
        pk=procesamiento.pk
    )
    documento = DocumentoFinanciacion.objects.select_for_update().get(
        pk=procesamiento.documento_id
    )
    if procesamiento.estado != EstadoProcesamientoContenidoDocumento.STARTED:
        return procesamiento
    if not documento.activo or documento.sha256 != procesamiento.hash_original:
        estado = EstadoProcesamientoContenidoDocumento.OBSOLETE
        razones = ('DOCUMENT_VERSION_OBSOLETE',)
        resultado = None
    valores = {
        'estado': estado,
        'codigos_razon': list(dict.fromkeys(razones)),
        'finalizado_en': timezone.now(),
    }
    if pdf:
        valores.update(
            numero_paginas=pdf.numero_paginas,
            pdf_cifrado=pdf.pdf_cifrado,
            contenido_activo_detectado=pdf.contenido_activo_detectado,
            metodo_extraccion=pdf.metodo_extraccion,
            paginas_analizadas=list(pdf.paginas_analizadas),
        )
    elif metadata_pdf:
        valores.update(
            pdf_cifrado=bool(metadata_pdf.get('pdf_cifrado')),
            contenido_activo_detectado=bool(
                metadata_pdf.get('contenido_activo_detectado')
            ),
        )
        caracteristicas = metadata_pdf.get('caracteristicas_seguridad', ())
        permitidas = {
            'ADDITIONAL_ACTION',
            'EMBEDDED_FILE',
            'JAVASCRIPT',
            'LAUNCH_ACTION',
            'OPEN_ACTION',
            'RICH_MEDIA',
            'SUBMIT_FORM',
            'XFA',
        }
        seguras = sorted({
            valor for valor in caracteristicas
            if isinstance(valor, str) and valor in permitidas
        })
        if seguras:
            valores['campos_estructurados'] = {
                'pdf_security_features': seguras,
            }
    if resultado:
        valores.update(
            clasificacion=resultado.categoria,
            paginas_analizadas=list(resultado.paginas_analizadas),
            campos_estructurados=_campos_persistibles(resultado),
            confianzas={
                'category': format(resultado.confianza_categoria, 'f'),
                'legibility': format(resultado.legibilidad, 'f'),
                'completeness': format(resultado.completitud_extraccion, 'f'),
                'holder_match': resultado.coincidencia_titular,
                'issuer_match': resultado.coincidencia_emisor,
                'tampering_signals': resultado.senales_manipulacion_visible,
                'provider': resultado.proveedor,
                'model': resultado.modelo,
            },
        )
        if not pdf and procesamiento.content_type in {'image/jpeg', 'image/png'}:
            valores.update(
                metodo_extraccion=MetodoExtraccionContenido.IMAGE,
                numero_paginas=1,
            )
    ProcesamientoContenidoDocumento.objects.finalizar_desde_servicio(
        pk=procesamiento.pk,
        valores=valores,
    )
    procesamiento.refresh_from_db()
    if estado == EstadoProcesamientoContenidoDocumento.ACCEPTED:
        documento.estado_validacion = EstadoValidacionDocumento.APPROVED
        documento.motivo_rechazo = ''
        documento.observacion_revision = (
            'Aceptacion automatica por clasificacion de contenido concluyente.'
        )
        documento.revisado_por = None
        documento.revisado_en = timezone.now()
    elif estado == EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED:
        documento.estado_validacion = EstadoValidacionDocumento.REJECTED
        (
            documento.motivo_rechazo,
            documento.observacion_revision,
        ) = resolver_rechazo_contenido(
            razones
        )
        documento.revisado_por = None
        documento.revisado_en = timezone.now()
    if estado in {
        EstadoProcesamientoContenidoDocumento.ACCEPTED,
        EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED,
    }:
        documento.full_clean()
        documento.save(update_fields=[
            'estado_validacion', 'motivo_rechazo', 'observacion_revision',
            'revisado_por', 'revisado_en', 'actualizado_en',
        ])
    return procesamiento


def procesar_contenido_documental(*, documento, backend=None):
    if documento.estado_escaneo != EstadoEscaneoDocumento.SAFE:
        return ResultadoProcesamientoContenido(
            estado='SECURITY_SCAN_REQUIRED', codigo='SECURITY_SCAN_REQUIRED',
            documento_id=documento.pk,
        )
    documento, procesamiento, omision = _iniciar(documento)
    if omision:
        return ResultadoProcesamientoContenido(
            estado=omision, codigo=omision, procesamiento_id=procesamiento.pk,
            documento_id=documento.pk,
        )
    pdf = None
    try:
        contenido = _leer_documento(documento)
        if documento.content_type == 'application/pdf':
            if not settings.FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED:
                raise ErrorClasificacionContenido('PDF_PROCESSING_DISABLED')
            pdf = procesar_pdf_seguro(contenido)
            paginas = pdf.paginas
        elif documento.content_type in {'image/jpeg', 'image/png'}:
            paginas = _preparar_imagen(contenido)
        else:
            raise ErrorClasificacionContenido('UNSUPPORTED_MEDIA_TYPE')
        backend = backend or obtener_backend_clasificacion_contenido()
        if not getattr(backend, 'enabled', True):
            raise ErrorClasificacionContenido('BACKEND_DISABLED')
        contexto = _contexto_documento(documento)
        paginas_clasificacion = _paginas_para_clasificacion(paginas)
        resultado = backend.clasificar(
            paginas=paginas_clasificacion,
            tipo_esperado=documento.tipo,
            contexto=contexto,
        )
        if not isinstance(resultado, ResultadoClasificacionContenido):
            raise ErrorClasificacionContenido('INVALID_RESPONSE')
        if (
            not resultado.paginas_analizadas
            or not set(resultado.paginas_analizadas).issubset(
                {pagina.numero for pagina in paginas_clasificacion}
            )
        ):
            raise ErrorClasificacionContenido('INVALID_RESPONSE')
        resultado = _aplicar_consistencia_determinista(
            resultado, contexto=contexto, tipo=documento.tipo
        )
        estado, razones = decidir_politica_contenido(resultado, tipo=documento.tipo)
        if (
            estado == EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION
            and procesamiento.numero
            < settings.FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS
        ):
            estado = EstadoProcesamientoContenidoDocumento.RETRYING
        final = _finalizar(
            procesamiento, estado=estado, resultado=resultado,
            razones=razones, pdf=pdf,
        )
    except ErrorProcesamientoPDF as error:
        estado = (
            EstadoProcesamientoContenidoDocumento.RETRYING
            if error.temporal
            and procesamiento.numero
            < settings.FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS
            else EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED
        )
        if error.temporal and (
            procesamiento.numero
            >= settings.FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS
        ):
            estado = EstadoProcesamientoContenidoDocumento.FAILED
        final = _finalizar(
            procesamiento,
            estado=estado,
            razones=(error.codigo,),
            pdf=pdf,
            metadata_pdf=error.metadata,
        )
    except ErrorClasificacionContenido as error:
        if error.temporal and (
            procesamiento.numero
            < settings.FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS
        ):
            estado = EstadoProcesamientoContenidoDocumento.RETRYING
        elif error.codigo == 'INVALID_RESPONSE' and (
            procesamiento.numero
            < settings.FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS
        ):
            estado = EstadoProcesamientoContenidoDocumento.RETRYING
        elif error.codigo in {'INVALID_RESPONSE', 'BACKEND_DISABLED'}:
            estado = EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION
        else:
            estado = EstadoProcesamientoContenidoDocumento.FAILED
        final = _finalizar(
            procesamiento, estado=estado, razones=(error.codigo,), pdf=pdf,
        )
    except (OSError, ValueError, ValidationError):
        final = _finalizar(
            procesamiento,
            estado=EstadoProcesamientoContenidoDocumento.FAILED,
            razones=('READ_ERROR',),
            pdf=pdf,
        )
    return ResultadoProcesamientoContenido(
        estado=final.estado,
        codigo=(final.codigos_razon or [''])[0],
        procesamiento_id=final.pk,
        documento_id=documento.pk,
    )
