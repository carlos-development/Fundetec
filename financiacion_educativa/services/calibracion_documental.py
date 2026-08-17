import json
import math
import os
import re
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string

from financiacion_educativa.choices import (
    CategoriaContenidoDocumento,
    EstadoProcesamientoContenidoDocumento,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.services.clasificacion_contenido_documental import (
    ErrorClasificacionContenido,
    ResultadoClasificacionContenido,
    _aplicar_consistencia_determinista,
    _paginas_para_clasificacion,
    _preparar_imagen,
    decidir_politica_contenido,
)
from financiacion_educativa.services.procesamiento_pdf import (
    ErrorProcesamientoPDF,
    procesar_pdf_seguro,
)
from financiacion_educativa.services.validacion_documental_ia import (
    ErrorValidacionDocumentalIA,
    IDENTITY_POLICY_VERSION,
    ResultadoValidacionDocumentalIA,
    _decision_modelo,
    _es_concluyente,
    _es_rechazo_concluyente,
    _prevalidar_imagen,
    _razones_inconclusas,
)


MANIFEST_VERSION = 'EDU_CALIBRATION_MANIFEST_V1'
PRIVATE_CONTEXT_VERSION = 'EDU_CALIBRATION_PRIVATE_CONTEXT_V1'
REPORT_VERSION = 'EDU_CALIBRATION_REPORT_V1'
REAL_IDENTITY_BACKEND = (
    'financiacion_educativa.services.validacion_documental_ia.'
    'OpenAIDocumentAIValidationBackend'
)
REAL_CONTENT_BACKEND = (
    'financiacion_educativa.services.clasificacion_contenido_documental.'
    'OpenAIContentDocumentClassificationBackend'
)
IDENTITY_TYPES = frozenset({
    TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
    TipoDocumentoFinanciacion.STUDENT_ID_BACK,
    TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
    TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
})
CONTENT_TYPES = frozenset({
    TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
    TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE,
})
EXPECTED_OUTCOMES = frozenset({'ACCEPT', 'CORRECTION', 'INCONCLUSIVE'})
FORMATS = {'JPEG': {'.jpg', '.jpeg'}, 'PNG': {'.png'}, 'PDF': {'.pdf'}}
SIDES = {'FRONT', 'BACK', 'NOT_APPLICABLE'}
CASE_KEYS = {
    'case_id', 'relative_path', 'expected_document_type', 'expected_side',
    'expected_outcome', 'expected_reasons', 'format', 'document_category',
    'holder_alias', 'notes',
}
PRIVATE_CONTEXT_KEYS = {
    'holder_name', 'holder_document_number', 'document_type', 'birth_date',
    'institution_name', 'program_name', 'academic_period',
    'enrollment_reference',
}
PII_PATTERNS = (
    re.compile(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}', re.I),
    re.compile(r'\b\d{7,}\b'),
)
CODE_PATTERN = re.compile(r'^[A-Z][A-Z0-9_\-]{0,59}$')
CASE_ID_PATTERN = re.compile(r'^CASE_[A-Z0-9][A-Z0-9_\-]{2,79}$')
ALIAS_PATTERN = re.compile(r'^ALIAS_[A-Z0-9][A-Z0-9_\-]{2,79}$')
PROHIBITED_PATH_PARTS = frozenset({
    'media',
    'private_uploads',
    'public',
    'static',
    'staticfiles',
})


class ErrorCalibracionDocumental(Exception):
    def __init__(self, codigo):
        self.codigo = _codigo(codigo)
        super().__init__(self.codigo)


@dataclass(frozen=True)
class CasoCalibracion:
    case_id: str
    relative_path: str
    expected_document_type: str
    expected_side: str
    expected_outcome: str
    expected_reasons: tuple[str, ...]
    formato: str
    document_category: str
    holder_alias: str
    notes: str


def _codigo(valor):
    limpio = re.sub(r'[^A-Z0-9_\-]', '', str(valor or '').upper())[:60]
    return limpio or 'CALIBRATION_ERROR'


def _is_within(path, parent):
    return path == parent or parent in path.parents


def _prohibited_roots():
    roots = [Path(settings.BASE_DIR)]
    for name in (
        'STATIC_ROOT',
        'MEDIA_ROOT',
        'FINANCIACION_EDUCATIVA_PRIVATE_ROOT',
    ):
        value = getattr(settings, name, None)
        if value:
            roots.append(Path(value))
    return tuple(root.expanduser().resolve(strict=False) for root in roots)


def validar_ruta_privada(path, *, tipo, debe_existir=True):
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ErrorCalibracionDocumental('PATH_MUST_BE_ABSOLUTE')
    if candidate.is_symlink():
        raise ErrorCalibracionDocumental('SYMLINK_NOT_ALLOWED')
    try:
        resolved = candidate.resolve(strict=debe_existir)
    except (OSError, RuntimeError) as error:
        raise ErrorCalibracionDocumental('PATH_NOT_FOUND') from error
    if any(_is_within(resolved, root) for root in _prohibited_roots()):
        raise ErrorCalibracionDocumental('PROHIBITED_PATH')
    if PROHIBITED_PATH_PARTS.intersection(
        part.casefold() for part in resolved.parts
    ):
        raise ErrorCalibracionDocumental('PROHIBITED_PATH')
    if tipo == 'directory' and (not resolved.exists() or not resolved.is_dir()):
        raise ErrorCalibracionDocumental('DIRECTORY_NOT_FOUND')
    if tipo == 'file' and (not resolved.exists() or not resolved.is_file()):
        raise ErrorCalibracionDocumental('FILE_NOT_FOUND')
    if tipo == 'output':
        parent = resolved.parent.resolve(strict=True)
        if any(_is_within(parent, root) for root in _prohibited_roots()):
            raise ErrorCalibracionDocumental('PROHIBITED_PATH')
        if resolved.exists() and resolved.is_dir():
            raise ErrorCalibracionDocumental('OUTPUT_MUST_BE_FILE')
    return resolved


def _leer_json_limitado(path, *, max_bytes=1024 * 1024):
    if path.stat().st_size > max_bytes:
        raise ErrorCalibracionDocumental('JSON_TOO_LARGE')
    try:
        with path.open('r', encoding='utf-8') as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ErrorCalibracionDocumental('INVALID_JSON') from error


def _contiene_pii(texto):
    return any(pattern.search(str(texto or '')) for pattern in PII_PATTERNS)


def _validar_relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ErrorCalibracionDocumental('INVALID_RELATIVE_PATH')
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts or '.' in relative.parts:
        raise ErrorCalibracionDocumental('INVALID_RELATIVE_PATH')
    return relative.as_posix()


def _validar_caso(data):
    if not isinstance(data, dict) or set(data) != CASE_KEYS:
        raise ErrorCalibracionDocumental('INVALID_CASE_SCHEMA')
    case_id = data['case_id']
    alias = data['holder_alias']
    if not isinstance(case_id, str) or not CASE_ID_PATTERN.fullmatch(case_id):
        raise ErrorCalibracionDocumental('INVALID_CASE_ID')
    if not isinstance(alias, str) or not ALIAS_PATTERN.fullmatch(alias):
        raise ErrorCalibracionDocumental('INVALID_HOLDER_ALIAS')
    document_type = data['expected_document_type']
    if document_type not in IDENTITY_TYPES | CONTENT_TYPES:
        raise ErrorCalibracionDocumental('INVALID_DOCUMENT_TYPE')
    side = data['expected_side']
    if side not in SIDES:
        raise ErrorCalibracionDocumental('INVALID_EXPECTED_SIDE')
    expected_side = (
        'FRONT' if document_type.endswith('_FRONT')
        else 'BACK' if document_type.endswith('_BACK')
        else 'NOT_APPLICABLE'
    )
    if side != expected_side:
        raise ErrorCalibracionDocumental('SIDE_TYPE_MISMATCH')
    expected = data['expected_outcome']
    if expected not in EXPECTED_OUTCOMES:
        raise ErrorCalibracionDocumental('INVALID_EXPECTED_OUTCOME')
    expected_reasons = data['expected_reasons']
    if (
        not isinstance(expected_reasons, list)
        or any(not isinstance(code, str) or not CODE_PATTERN.fullmatch(code)
               for code in expected_reasons)
    ):
        raise ErrorCalibracionDocumental('INVALID_EXPECTED_REASONS')
    formato = data['format']
    if formato not in FORMATS:
        raise ErrorCalibracionDocumental('INVALID_FORMAT')
    relative_path = _validar_relative_path(data['relative_path'])
    if Path(relative_path).suffix.lower() not in FORMATS[formato]:
        raise ErrorCalibracionDocumental('FORMAT_EXTENSION_MISMATCH')
    if document_type in IDENTITY_TYPES and formato == 'PDF':
        raise ErrorCalibracionDocumental('IDENTITY_PDF_NOT_ALLOWED')
    category = data['document_category']
    allowed_categories = {'IDENTITY', *CategoriaContenidoDocumento.values}
    if category not in allowed_categories:
        raise ErrorCalibracionDocumental('INVALID_DOCUMENT_CATEGORY')
    if document_type in IDENTITY_TYPES and category != 'IDENTITY':
        raise ErrorCalibracionDocumental('CATEGORY_TYPE_MISMATCH')
    notes = data['notes']
    if not isinstance(notes, str) or len(notes) > 300 or _contiene_pii(notes):
        raise ErrorCalibracionDocumental('UNSAFE_NOTES')
    return CasoCalibracion(
        case_id=case_id,
        relative_path=relative_path,
        expected_document_type=document_type,
        expected_side=side,
        expected_outcome=expected,
        expected_reasons=tuple(dict.fromkeys(expected_reasons)),
        formato=formato,
        document_category=category,
        holder_alias=alias,
        notes=notes,
    )


def cargar_manifest(path):
    data = _leer_json_limitado(path)
    if (
        not isinstance(data, dict)
        or set(data) != {'schema_version', 'cases'}
        or data.get('schema_version') != MANIFEST_VERSION
        or not isinstance(data.get('cases'), list)
        or not data['cases']
    ):
        raise ErrorCalibracionDocumental('INVALID_MANIFEST')
    cases = tuple(_validar_caso(item) for item in data['cases'])
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ErrorCalibracionDocumental('DUPLICATE_CASE_ID')
    return cases


def cargar_contexto_privado(path):
    if path is None:
        return {}
    data = _leer_json_limitado(path)
    if (
        not isinstance(data, dict)
        or set(data) != {'schema_version', 'cases'}
        or data.get('schema_version') != PRIVATE_CONTEXT_VERSION
        or not isinstance(data.get('cases'), dict)
    ):
        raise ErrorCalibracionDocumental('INVALID_PRIVATE_CONTEXT')
    result = {}
    for case_id, context in data['cases'].items():
        if (
            not CASE_ID_PATTERN.fullmatch(str(case_id))
            or not isinstance(context, dict)
            or not set(context).issubset(PRIVATE_CONTEXT_KEYS)
            or any(not isinstance(value, str) or len(value) > 200
                   for value in context.values())
        ):
            raise ErrorCalibracionDocumental('INVALID_PRIVATE_CONTEXT')
        result[case_id] = dict(context)
    return result


def _contexto_sintetico(case):
    return {
        'holder_name': case.holder_alias,
        'holder_document_number': 'SYNTHETIC0001',
        'document_type': TipoDocumentoIdentidad.CC,
        'birth_date': '2000-01-01',
        'institution_name': 'INSTITUTION_ALIAS_001',
        'program_name': 'PROGRAM_ALIAS_001',
        'academic_period': 'PERIOD_ALIAS_001',
        'enrollment_reference': 'ENROLLMENT_ALIAS_001',
    }


def _resolver_case_file(dataset, case):
    candidate = dataset.joinpath(*Path(case.relative_path).parts)
    if candidate.is_symlink():
        raise ErrorCalibracionDocumental('SYMLINK_NOT_ALLOWED')
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ErrorCalibracionDocumental('FILE_NOT_FOUND') from error
    if not _is_within(resolved, dataset) or not resolved.is_file():
        raise ErrorCalibracionDocumental('DATASET_ESCAPE_ATTEMPT')
    if resolved.stat().st_size <= 0:
        raise ErrorCalibracionDocumental('EMPTY_FILE')
    if resolved.stat().st_size > settings.FINANCIACION_EDUCATIVA_PDF_MAX_BYTES:
        raise ErrorCalibracionDocumental('FILE_TOO_LARGE')
    return resolved


def _load_backend(path, *, real_path):
    if path == real_path:
        return import_string(path)(), True
    if (
        getattr(
            settings,
            'FINANCIACION_EDUCATIVA_CALIBRATION_ALLOW_TEST_BACKENDS',
            False,
        )
        and path.startswith('financiacion_educativa.tests.')
    ):
        return import_string(path)(), False
    raise ErrorCalibracionDocumental('UNSAFE_CALIBRATION_BACKEND')


def _identity_decision(result, case, context):
    declared_type = context.get('document_type', TipoDocumentoIdentidad.CC)
    requires_colombian = declared_type in {
        TipoDocumentoIdentidad.CC,
        TipoDocumentoIdentidad.TI,
    }
    if _es_concluyente(
        result,
        requiere_identidad=True,
        requiere_documento_colombiano=requires_colombian,
    ):
        return 'ACCEPT', tuple(dict.fromkeys(result.hallazgos))
    document = SimpleNamespace(
        tipo=case.expected_document_type,
        participante=SimpleNamespace(tipo_documento=declared_type),
    )
    if _es_rechazo_concluyente(result, documento=document):
        return 'CORRECTION', tuple(dict.fromkeys(result.hallazgos))
    reasons = tuple(dict.fromkeys((
        *result.hallazgos,
        *_razones_inconclusas(result, requiere_identidad=True),
        'INCONCLUSIVE',
    )))
    return 'INCONCLUSIVE', reasons


def _identity_confidence(result):
    return {
        'overall': float(result.confianza),
        'quality': float(result.calidad),
        'legibility': float(result.legibilidad),
        'document_type': float(
            result.confianza_tipo_documental or result.confianza
        ),
        'side': float(result.confianza_lado or result.confianza),
        'visual_integrity': float(
            result.confianza_integridad_visual or result.confianza
        ),
        'data_match': float(result.confianza_datos or result.confianza),
        'physical_capture': float(
            result.confianza_captura_fisica or result.confianza
        ),
        'tampering': float(
            result.confianza_manipulacion or result.confianza
        ),
    }


def _content_confidence(result):
    return {
        'category': float(result.confianza_categoria),
        'legibility': float(result.legibilidad),
        'extraction_completeness': float(result.completitud_extraccion),
    }


def _read_bytes(path):
    with path.open('rb') as stream:
        return stream.read(settings.FINANCIACION_EDUCATIVA_PDF_MAX_BYTES + 1)


def _base_case_report(case, *, dry_run):
    return {
        'case_id': case.case_id,
        'expected_document_type': case.expected_document_type,
        'expected_side': case.expected_side,
        'format': case.formato,
        'document_category': case.document_category,
        'expected_outcome': case.expected_outcome,
        'expected_reasons': list(case.expected_reasons),
        'provider_result': 'NOT_CALLED' if dry_run else 'NOT_AVAILABLE',
        'schema_validation': 'NOT_RUN',
        'deterministic_outcome': 'NOT_EVALUATED',
        'matches_expected': None,
        'critical_false_accept': False,
        'reason_codes': [],
        'confidence': {},
        'latency_ms': 0,
        'page_count': 0,
        'image_count': 0,
        'retries': 0,
        'technical_error': None,
        'provider_usage': {},
    }


def _temporary_identity_error(error):
    return error.codigo in {'PROVIDER_ERROR', 'PROVIDER_TIMEOUT'}


def _run_identity(case, path, context, backend, max_attempts):
    report = _base_case_report(case, dry_run=False)
    content = _read_bytes(path)
    local_result = _prevalidar_imagen(content)
    result = local_result
    attempts = 0
    if result is None:
        while attempts < max_attempts:
            attempts += 1
            try:
                result = backend.validar(
                    contenido=content,
                    content_type=(
                        'image/jpeg' if case.formato == 'JPEG' else 'image/png'
                    ),
                    tipo_esperado=case.expected_document_type,
                    contexto=context,
                )
                if not isinstance(result, ResultadoValidacionDocumentalIA):
                    raise ErrorValidacionDocumentalIA('INVALID_RESPONSE')
                break
            except ErrorValidacionDocumentalIA as error:
                if _temporary_identity_error(error) and attempts < max_attempts:
                    continue
                report['technical_error'] = {
                    'classification': (
                        'TEMPORARY' if _temporary_identity_error(error)
                        else 'PERMANENT'
                    ),
                    'code': error.codigo,
                }
                report['schema_validation'] = (
                    'INVALID' if error.codigo == 'INVALID_RESPONSE' else 'NOT_RUN'
                )
                report['retries'] = max(attempts - 1, 0)
                return report
    report['schema_validation'] = 'VALID'
    report['provider_result'] = _decision_modelo(result)
    report['deterministic_outcome'], reasons = _identity_decision(
        result, case, context
    )
    report['reason_codes'] = list(reasons)
    report['confidence'] = _identity_confidence(result)
    report['page_count'] = 1
    report['image_count'] = 1
    report['retries'] = max(attempts - 1, 0)
    report['provider_usage'] = dict(result.metricas_uso)
    return report


def _content_pages(case, content):
    if case.formato == 'PDF':
        pdf = procesar_pdf_seguro(content)
        pages = _paginas_para_clasificacion(pdf.paginas)
        return pages, pdf.numero_paginas, sum(
            1 for page in pages if page.imagen_png
        )
    pages = _paginas_para_clasificacion(_preparar_imagen(content))
    return pages, 1, 1


def _run_content(case, path, context, backend, max_attempts):
    report = _base_case_report(case, dry_run=False)
    try:
        pages, page_count, image_count = _content_pages(case, _read_bytes(path))
    except ErrorProcesamientoPDF as error:
        if error.corregible:
            report['deterministic_outcome'] = 'CORRECTION'
            report['reason_codes'] = [error.codigo]
        else:
            report['deterministic_outcome'] = 'INCONCLUSIVE'
            report['technical_error'] = {
                'classification': 'TEMPORARY' if error.temporal else 'PERMANENT',
                'code': error.codigo,
            }
        return report
    except ErrorClasificacionContenido as error:
        report['deterministic_outcome'] = 'INCONCLUSIVE'
        report['technical_error'] = {
            'classification': 'TEMPORARY' if error.temporal else 'PERMANENT',
            'code': error.codigo,
        }
        return report
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        try:
            result = backend.clasificar(
                paginas=pages,
                tipo_esperado=case.expected_document_type,
                contexto=context,
            )
            if not isinstance(result, ResultadoClasificacionContenido):
                raise ErrorClasificacionContenido('INVALID_RESPONSE')
            break
        except ErrorClasificacionContenido as error:
            retryable = error.temporal or error.codigo == 'INVALID_RESPONSE'
            if retryable and attempts < max_attempts:
                continue
            report['technical_error'] = {
                'classification': 'TEMPORARY' if error.temporal else 'PERMANENT',
                'code': error.codigo,
            }
            report['schema_validation'] = (
                'INVALID' if error.codigo == 'INVALID_RESPONSE' else 'NOT_RUN'
            )
            report['retries'] = max(attempts - 1, 0)
            return report
    result = _aplicar_consistencia_determinista(
        result,
        contexto=context,
        tipo=case.expected_document_type,
    )
    policy, reasons = decidir_politica_contenido(
        result,
        tipo=case.expected_document_type,
    )
    outcome = {
        EstadoProcesamientoContenidoDocumento.ACCEPTED: 'ACCEPT',
        EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED: 'CORRECTION',
        EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION: 'INCONCLUSIVE',
    }.get(policy, 'INCONCLUSIVE')
    report.update({
        'provider_result': result.resultado_general,
        'schema_validation': 'VALID',
        'deterministic_outcome': outcome,
        'reason_codes': list(dict.fromkeys(reasons)),
        'confidence': _content_confidence(result),
        'page_count': page_count,
        'image_count': image_count,
        'retries': max(attempts - 1, 0),
        'provider_usage': dict(result.metricas_uso),
    })
    return report


def _p95(values):
    if not values:
        return 0
    ordered = sorted(values)
    index = max(math.ceil(0.95 * len(ordered)) - 1, 0)
    return ordered[index]


def calcular_metricas(reports):
    evaluated = [
        report for report in reports
        if (
            report['deterministic_outcome'] != 'NOT_EVALUATED'
            and report['technical_error'] is None
        )
    ]
    latencies = [r['latency_ms'] for r in evaluated]
    by_category = {}
    for report in evaluated:
        bucket = by_category.setdefault(report['document_category'], {
            'total': 0, 'accepted': 0, 'inconclusive': 0,
        })
        bucket['total'] += 1
        bucket['accepted'] += report['deterministic_outcome'] == 'ACCEPT'
        bucket['inconclusive'] += report['deterministic_outcome'] == 'INCONCLUSIVE'
    for bucket in by_category.values():
        total = bucket['total'] or 1
        bucket['acceptance_rate'] = round(bucket['accepted'] / total, 4)
        bucket['inconclusive_rate'] = round(bucket['inconclusive'] / total, 4)
    return {
        'total_cases': len(reports),
        'evaluated_cases': len(evaluated),
        'true_accepts': sum(
            r['expected_outcome'] == 'ACCEPT'
            and r['deterministic_outcome'] == 'ACCEPT'
            for r in evaluated
        ),
        'true_corrections': sum(
            r['expected_outcome'] == 'CORRECTION'
            and r['deterministic_outcome'] == 'CORRECTION'
            for r in evaluated
        ),
        'false_accepts': sum(r['critical_false_accept'] for r in evaluated),
        'false_corrections': sum(
            r['expected_outcome'] == 'ACCEPT'
            and r['deterministic_outcome'] == 'CORRECTION'
            for r in evaluated
        ),
        'inconclusive': sum(
            r['deterministic_outcome'] == 'INCONCLUSIVE'
            for r in evaluated
        ),
        'technical_errors': sum(
            r['technical_error'] is not None for r in reports
        ),
        'inconclusive_rate': round(
            sum(r['deterministic_outcome'] == 'INCONCLUSIVE' for r in evaluated)
            / (len(evaluated) or 1),
            4,
        ),
        'latency_ms': {
            'median': round(statistics.median(latencies), 2) if latencies else 0,
            'p95': round(_p95(latencies), 2),
        },
        'by_category': by_category,
    }


def _policy_proposal(metrics):
    recommendations = [
        'Mantener separados los umbrales de identidad y contenido.',
        'Solicitar nueva captura ante defectos visuales concluyentes.',
        'Usar MANUAL_EXCEPTION ante incertidumbre o contradiccion no concluyente.',
        'Reintentar solo errores tecnicos temporales.',
        'Exigir todas las dimensiones concluyentes para aceptacion automatica.',
    ]
    if metrics['false_accepts']:
        recommendations.insert(
            0,
            'Bloqueante: existen falsos aceptados; no habilitar automatizacion.',
        )
    return {
        'status': (
            'INSUFFICIENT_DATA'
            if not metrics['evaluated_cases'] else
            'BLOCKED_BY_FALSE_ACCEPTS'
            if metrics['false_accepts'] else
            'REQUIRES_HUMAN_REVIEW'
        ),
        'current_thresholds_unchanged': True,
        'never_accept_conditions': [
            'FACE_WITHOUT_DOCUMENT',
            'KEYBOARD_OR_UNRELATED_OBJECT',
            'FOREIGN_DOCUMENT',
            'WRONG_SIDE',
            'CONTRADICTORY_DATA',
        ],
        'recommendations': recommendations,
    }


def ejecutar_calibracion(
    *,
    dataset,
    manifest,
    output,
    private_context=None,
    dry_run=True,
    allow_real_openai=False,
    overwrite=False,
):
    dataset = validar_ruta_privada(dataset, tipo='directory')
    manifest = validar_ruta_privada(manifest, tipo='file')
    output = validar_ruta_privada(output, tipo='output', debe_existir=False)
    private_context = (
        validar_ruta_privada(private_context, tipo='file')
        if private_context else None
    )
    if _is_within(output, dataset):
        raise ErrorCalibracionDocumental('OUTPUT_INSIDE_DATASET')
    if output.exists() and not overwrite:
        raise ErrorCalibracionDocumental('OUTPUT_ALREADY_EXISTS')
    cases = cargar_manifest(manifest)
    private = cargar_contexto_privado(private_context)
    identity_path = settings.FINANCIACION_EDUCATIVA_CALIBRATION_IDENTITY_BACKEND
    content_path = settings.FINANCIACION_EDUCATIVA_CALIBRATION_CONTENT_BACKEND
    identity_backend = content_backend = None
    if not dry_run:
        identity_backend, identity_real = _load_backend(
            identity_path,
            real_path=REAL_IDENTITY_BACKEND,
        )
        content_backend, content_real = _load_backend(
            content_path,
            real_path=REAL_CONTENT_BACKEND,
        )
        if identity_real or content_real:
            if not (
                allow_real_openai
                and settings.FINANCIACION_EDUCATIVA_CALIBRATION_OPENAI_ENABLED
                and str(settings.OPENAI_API_KEY or '').strip()
            ):
                raise ErrorCalibracionDocumental('REAL_OPENAI_NOT_AUTHORIZED')
            if private_context is None:
                raise ErrorCalibracionDocumental('PRIVATE_CONTEXT_REQUIRED')
            if set(private) != {case.case_id for case in cases}:
                raise ErrorCalibracionDocumental(
                    'PRIVATE_CONTEXT_CASES_MISMATCH'
                )
    reports = []
    for case in cases:
        report = _base_case_report(case, dry_run=dry_run)
        started = time.monotonic()
        try:
            file_path = _resolver_case_file(dataset, case)
            if not dry_run:
                context = private.get(case.case_id, _contexto_sintetico(case))
                if case.expected_document_type in IDENTITY_TYPES:
                    report = _run_identity(
                        case,
                        file_path,
                        context,
                        identity_backend,
                        settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MAX_ATTEMPTS,
                    )
                else:
                    report = _run_content(
                        case,
                        file_path,
                        context,
                        content_backend,
                        settings.FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS,
                    )
        except ErrorCalibracionDocumental as error:
            report['technical_error'] = {
                'classification': 'PERMANENT',
                'code': error.codigo,
            }
        except (OSError, ValueError):
            report['technical_error'] = {
                'classification': 'PERMANENT',
                'code': 'CASE_PROCESSING_ERROR',
            }
        report['latency_ms'] = round((time.monotonic() - started) * 1000, 2)
        if report['deterministic_outcome'] != 'NOT_EVALUATED':
            report['matches_expected'] = (
                report['deterministic_outcome'] == case.expected_outcome
            )
            report['critical_false_accept'] = bool(
                report['deterministic_outcome'] == 'ACCEPT'
                and case.expected_outcome != 'ACCEPT'
            )
        reports.append(report)
    metrics = calcular_metricas(reports)
    result = {
        'schema_version': REPORT_VERSION,
        'manifest_version': MANIFEST_VERSION,
        'generated_at': timezone.now().isoformat(),
        'dry_run': dry_run,
        'configuration': {
            'model': settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MODEL,
            'identity_schema_version': '3',
            'identity_policy_version': IDENTITY_POLICY_VERSION,
            'content_schema_version': settings.FINANCIACION_EDUCATIVA_CONTENT_SCHEMA_VERSION,
            'identity_thresholds': {
                'confidence': settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_CONFIDENCE,
                'quality': settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_QUALITY,
                'legibility': settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_LEGIBILITY,
            },
            'content_thresholds': {
                'confidence': settings.FINANCIACION_EDUCATIVA_CONTENT_MIN_CONFIDENCE,
                'legibility': settings.FINANCIACION_EDUCATIVA_CONTENT_MIN_LEGIBILITY,
                'completeness': settings.FINANCIACION_EDUCATIVA_CONTENT_MIN_COMPLETENESS,
            },
            'timeout_seconds': settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_TIMEOUT_SECONDS,
            'identity_max_attempts': settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MAX_ATTEMPTS,
            'content_max_attempts': settings.FINANCIACION_EDUCATIVA_CONTENT_MAX_ATTEMPTS,
        },
        'metrics': metrics,
        'policy_proposal': _policy_proposal(metrics),
        'cases': reports,
    }
    output.parent.mkdir(parents=False, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix='.calibration-',
        suffix='.tmp',
        dir=output.parent,
    )
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write('\n')
        try:
            os.chmod(temporary_name, 0o600)
        except OSError:
            pass
        os.replace(temporary_name, output)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return result
