import base64
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.module_loading import import_string

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoValidacionDocumento,
    EstadoValidacionIADocumento,
    MotivoRechazoDocumento,
    OrigenValidacionIADocumento,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import (
    DocumentoFinanciacion,
    ValidacionIADocumento,
)


HALLAZGOS_PERMITIDOS = frozenset({
    'NOT_IDENTITY_DOCUMENT',
    'NOT_COLOMBIAN_ID',
    'SIDE_MISMATCH',
    'MALFORMED_IMAGE',
    'IMAGE_TOO_SMALL',
    'LOW_QUALITY',
    'LOW_LEGIBILITY',
    'BLURRED',
    'TOO_DARK',
    'GLARE',
    'CROPPED',
    'OBSTRUCTED',
    'MISSING_VISIBLE_FIELDS',
    'TYPE_MISMATCH',
    'POSSIBLY_NOT_REAL',
    'DATA_MISMATCH',
    'INCONCLUSIVE',
})
DECISIONES_MODELO = frozenset({'ACCEPTED', 'REJECTED', 'MANUAL_REVIEW'})
TIPOS_IDENTIDAD = frozenset({
    TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
    TipoDocumentoFinanciacion.STUDENT_ID_BACK,
    TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
    TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
})
LADO_IDENTIDAD = {
    TipoDocumentoFinanciacion.STUDENT_ID_FRONT: 'front',
    TipoDocumentoFinanciacion.STUDENT_ID_BACK: 'back',
    TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT: 'front',
    TipoDocumentoFinanciacion.GUARDIAN_ID_BACK: 'back',
}


class ErrorValidacionDocumentalIA(Exception):
    def __init__(self, codigo):
        self.codigo = _texto_controlado(codigo, 60) or 'INTERNAL_ERROR'
        super().__init__(self.codigo)


@dataclass(frozen=True)
class ResultadoValidacionDocumentalIA:
    calidad: Decimal
    legibilidad: Decimal
    confianza: Decimal
    corresponde_tipo: bool | None
    indicios_imagen_real: bool | None
    datos_consistentes: bool | None
    hallazgos: tuple[str, ...] = ()
    proveedor: str = ''
    modelo: str = ''
    decision: str = ''
    es_documento_identidad: bool | None = None
    es_documento_colombiano: bool | None = None
    lado_correcto: bool | None = None
    campos_visibles: bool | None = None
    borrosa: bool | None = None
    oscura: bool | None = None
    reflejos: bool | None = None
    recortada: bool | None = None
    obstruida: bool | None = None
    razones: tuple[str, ...] = ()
    tipo_documento_visible: str = ''
    numero_documento_visible: str = ''
    nombres_visibles: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultadoProcesamientoValidacionIA:
    documento_id: object
    estado: str
    validacion_id: object = None
    procesado: bool = False
    codigo_error: str = ''


class DisabledDocumentAIValidationBackend:
    enabled = False
    proveedor = 'disabled'

    def validar(self, **kwargs):
        raise ErrorValidacionDocumentalIA('BACKEND_DISABLED')


class OpenAIDocumentAIValidationBackend:
    enabled = True
    proveedor = 'openai'

    def __init__(self):
        self.modelo = settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MODEL

    def validar(self, *, contenido, content_type, tipo_esperado, contexto):
        if not settings.OPENAI_API_KEY or not self.modelo:
            raise ErrorValidacionDocumentalIA('CONFIGURATION_ERROR')
        try:
            from openai import OpenAI

            cliente = OpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_TIMEOUT_SECONDS,
            )
            response = cliente.responses.create(
                model=self.modelo,
                store=False,
                input=[
                    {
                        'role': 'system',
                        'content': [
                            {
                                'type': 'input_text',
                                'text': (
                                    'Evalua solo evidencia visual. No afirmes autenticidad '
                                    'fisica, presencia, liveness ni consultas oficiales. '
                                    'Para identificaciones, comprueba si muestra una '
                                    'identificacion colombiana y el lado solicitado. Para '
                                    'otros tipos, evalua solo el tipo documental indicado. '
                                    'No infieras datos no visibles. Una duda debe producir '
                                    'MANUAL_REVIEW; usa '
                                    'REJECTED solo para una contradiccion visual concluyente.'
                                ),
                            },
                        ],
                    },
                    {
                        'role': 'user',
                        'content': [
                            {
                                'type': 'input_text',
                                'text': json.dumps(
                                    {
                                        'tipo_documental_esperado': tipo_esperado,
                                        'datos_declarados_para_comparacion': contexto,
                                    },
                                    ensure_ascii=True,
                                    separators=(',', ':'),
                                ),
                            },
                            {
                                'type': 'input_image',
                                'image_url': (
                                    f'data:{content_type};base64,'
                                    f'{base64.b64encode(contenido).decode("ascii")}'
                                ),
                                'detail': 'high',
                            },
                        ],
                    },
                ],
                text={
                    'format': {
                        'type': 'json_schema',
                        'name': 'document_validation',
                        'strict': True,
                        'schema': _esquema_respuesta(),
                    },
                },
            )
            payload = json.loads(response.output_text)
        except ErrorValidacionDocumentalIA:
            raise
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ErrorValidacionDocumentalIA('INVALID_RESPONSE') from exc
        except Exception as exc:
            raise ErrorValidacionDocumentalIA('PROVIDER_ERROR') from exc
        return normalizar_resultado_validacion(
            payload,
            proveedor=self.proveedor,
            modelo=self.modelo,
        )


def _esquema_respuesta():
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': [
            'quality_score',
            'legibility_score',
            'confidence',
            'document_type_match',
            'appears_real',
            'data_consistent',
            'finding_codes',
            'decision',
            'is_identity_document',
            'is_colombian_document',
            'side_matches',
            'required_fields_visible',
            'is_blurred',
            'is_too_dark',
            'has_glare',
            'is_cropped',
            'is_obstructed',
            'reason_codes',
            'visible_document_type',
            'visible_document_number',
            'visible_names',
        ],
        'properties': {
            'quality_score': {'type': 'number'},
            'legibility_score': {'type': 'number'},
            'confidence': {'type': 'number'},
            'document_type_match': {'type': ['boolean', 'null']},
            'appears_real': {'type': ['boolean', 'null']},
            'data_consistent': {'type': ['boolean', 'null']},
            'finding_codes': {
                'type': 'array',
                'items': {'type': 'string', 'enum': sorted(HALLAZGOS_PERMITIDOS)},
            },
            'decision': {'type': 'string', 'enum': sorted(DECISIONES_MODELO)},
            'is_identity_document': {'type': ['boolean', 'null']},
            'is_colombian_document': {'type': ['boolean', 'null']},
            'side_matches': {'type': ['boolean', 'null']},
            'required_fields_visible': {'type': ['boolean', 'null']},
            'is_blurred': {'type': ['boolean', 'null']},
            'is_too_dark': {'type': ['boolean', 'null']},
            'has_glare': {'type': ['boolean', 'null']},
            'is_cropped': {'type': ['boolean', 'null']},
            'is_obstructed': {'type': ['boolean', 'null']},
            'reason_codes': {
                'type': 'array',
                'items': {'type': 'string', 'enum': sorted(HALLAZGOS_PERMITIDOS)},
            },
            'visible_document_type': {'type': ['string', 'null']},
            'visible_document_number': {
                'type': ['string', 'null'],
            },
            'visible_names': {
                'type': 'array',
                'items': {'type': 'string'},
            },
        },
    }


def _texto_controlado(valor, limite):
    return re.sub(r'[^A-Za-z0-9._:\- ]+', '', str(valor or '')).strip()[:limite]


def _dato_visible(valor, limite):
    normalizado = unicodedata.normalize('NFKC', str(valor or ''))
    return re.sub(r"[^\w .,'\-]", '', normalizado, flags=re.UNICODE).strip()[:limite]


def _puntaje(valor, campo):
    if isinstance(valor, bool):
        raise ErrorValidacionDocumentalIA('INVALID_RESPONSE')
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ErrorValidacionDocumentalIA('INVALID_RESPONSE') from exc
    if not numero.is_finite() or numero < 0 or numero > 1:
        raise ErrorValidacionDocumentalIA('INVALID_RESPONSE')
    return numero.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def _booleano_nullable(valor):
    if valor is None or isinstance(valor, bool):
        return valor
    raise ErrorValidacionDocumentalIA('INVALID_RESPONSE')


def normalizar_resultado_validacion(payload, *, proveedor='', modelo=''):
    if not isinstance(payload, dict):
        raise ErrorValidacionDocumentalIA('INVALID_RESPONSE')
    required = {
        'quality_score',
        'legibility_score',
        'confidence',
        'document_type_match',
        'appears_real',
        'data_consistent',
        'finding_codes',
        'decision',
        'is_identity_document',
        'is_colombian_document',
        'side_matches',
        'required_fields_visible',
        'is_blurred',
        'is_too_dark',
        'has_glare',
        'is_cropped',
        'is_obstructed',
        'reason_codes',
        'visible_document_type',
        'visible_document_number',
        'visible_names',
    }
    if (
        set(payload) != required
        or not isinstance(payload['finding_codes'], list)
        or not isinstance(payload['reason_codes'], list)
        or not isinstance(payload['visible_names'], list)
        or len(payload['visible_names']) > 8
        or (
            payload['visible_document_type'] is not None
            and (
                not isinstance(payload['visible_document_type'], str)
                or len(payload['visible_document_type']) > 30
            )
        )
        or (
            payload['visible_document_number'] is not None
            and (
                not isinstance(payload['visible_document_number'], str)
                or len(payload['visible_document_number']) > 40
            )
        )
        or any(
            not isinstance(nombre, str) or len(nombre) > 100
            for nombre in payload['visible_names']
        )
        or payload['decision'] not in DECISIONES_MODELO
    ):
        raise ErrorValidacionDocumentalIA('INVALID_RESPONSE')
    hallazgos = tuple(dict.fromkeys(
        [*payload['finding_codes'], *payload['reason_codes']]
    ))
    if any(code not in HALLAZGOS_PERMITIDOS for code in hallazgos):
        raise ErrorValidacionDocumentalIA('INVALID_RESPONSE')
    return ResultadoValidacionDocumentalIA(
        calidad=_puntaje(payload['quality_score'], 'quality_score'),
        legibilidad=_puntaje(payload['legibility_score'], 'legibility_score'),
        confianza=_puntaje(payload['confidence'], 'confidence'),
        corresponde_tipo=_booleano_nullable(payload['document_type_match']),
        indicios_imagen_real=_booleano_nullable(payload['appears_real']),
        datos_consistentes=_booleano_nullable(payload['data_consistent']),
        hallazgos=hallazgos,
        proveedor=_texto_controlado(proveedor, 60),
        modelo=_texto_controlado(modelo, 80),
        decision=payload['decision'],
        es_documento_identidad=_booleano_nullable(payload['is_identity_document']),
        es_documento_colombiano=_booleano_nullable(payload['is_colombian_document']),
        lado_correcto=_booleano_nullable(payload['side_matches']),
        campos_visibles=_booleano_nullable(payload['required_fields_visible']),
        borrosa=_booleano_nullable(payload['is_blurred']),
        oscura=_booleano_nullable(payload['is_too_dark']),
        reflejos=_booleano_nullable(payload['has_glare']),
        recortada=_booleano_nullable(payload['is_cropped']),
        obstruida=_booleano_nullable(payload['is_obstructed']),
        razones=tuple(dict.fromkeys(payload['reason_codes'])),
        tipo_documento_visible=_dato_visible(
            payload['visible_document_type'],
            30,
        ),
        numero_documento_visible=_dato_visible(
            payload['visible_document_number'],
            40,
        ),
        nombres_visibles=tuple(
            _dato_visible(nombre, 100)
            for nombre in payload['visible_names']
            if _dato_visible(nombre, 100)
        ),
    )


def obtener_backend_validacion_ia():
    backend_class = import_string(
        settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND
    )
    return backend_class()


def _validar_permiso(actor, origen):
    if origen in {
        OrigenValidacionIADocumento.COMMAND,
        OrigenValidacionIADocumento.AUTOMATIC,
    } and actor is None:
        return
    if (
        not actor
        or not actor.is_authenticated
        or not actor.has_perm(
            'financiacion_educativa.procesar_validacion_ia_documento'
        )
    ):
        raise PermissionDenied('No tiene permiso para validar documentos con IA.')


@transaction.atomic
def _iniciar_validacion(*, documento_id, actor, origen):
    documento = DocumentoFinanciacion.objects.select_for_update().get(
        pk=documento_id
    )
    if documento.estado_escaneo != EstadoEscaneoDocumento.SAFE:
        return documento, None, 'SECURITY_SCAN_REQUIRED'
    if documento.estado_validacion != EstadoValidacionDocumento.PENDING:
        return documento, None, 'ALREADY_REVIEWED'
    if documento.content_type not in {'image/jpeg', 'image/png'}:
        return documento, None, 'UNSUPPORTED_MEDIA_TYPE'

    ahora = timezone.now()
    activa = documento.validaciones_ia.filter(
        estado=EstadoValidacionIADocumento.STARTED
    ).first()
    if activa:
        antiguedad = (ahora - activa.iniciado_en).total_seconds()
        if antiguedad < settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_STALE_SECONDS:
            return documento, activa, 'IN_PROGRESS'
        ValidacionIADocumento.objects.filter(pk=activa.pk).update(
            estado=EstadoValidacionIADocumento.ERROR,
            codigo_error='STALE_ATTEMPT',
            finalizado_en=ahora,
        )

    numero = (
        documento.validaciones_ia.aggregate(maximo=Max('numero'))['maximo'] or 0
    ) + 1
    if numero > settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MAX_ATTEMPTS:
        return documento, None, 'MAX_ATTEMPTS'
    try:
        with transaction.atomic():
            validacion = ValidacionIADocumento.objects.create(
                documento=documento,
                numero=numero,
                origen=origen,
                solicitado_por=actor,
            )
    except IntegrityError:
        validacion = documento.validaciones_ia.filter(
            estado=EstadoValidacionIADocumento.STARTED
        ).first()
        return documento, validacion, 'IN_PROGRESS'
    return documento, validacion, ''


def _contexto_declarado(documento):
    participante = documento.participante
    if not participante:
        return {}
    return {
        'nombres': participante.nombres,
        'apellidos': participante.apellidos,
        'tipo_documento': participante.tipo_documento,
        'numero_documento': participante.numero_documento,
        'fecha_nacimiento': (
            participante.fecha_nacimiento.isoformat()
            if participante.fecha_nacimiento
            else None
        ),
    }


def _prevalidar_imagen(contenido):
    try:
        with Image.open(BytesIO(contenido)) as imagen:
            formato = (imagen.format or '').upper()
            ancho, alto = imagen.size
            imagen.verify()
        with Image.open(BytesIO(contenido)) as imagen:
            imagen.load()
    except (OSError, ValueError, UnidentifiedImageError):
        return ResultadoValidacionDocumentalIA(
            calidad=Decimal('0'),
            legibilidad=Decimal('0'),
            confianza=Decimal('1'),
            corresponde_tipo=False,
            indicios_imagen_real=False,
            datos_consistentes=None,
            hallazgos=('MALFORMED_IMAGE',),
            proveedor='local-precheck',
            modelo='pillow',
            decision='REJECTED',
            razones=('MALFORMED_IMAGE',),
        )
    if formato not in {'JPEG', 'PNG'}:
        raise ErrorValidacionDocumentalIA('UNSUPPORTED_MEDIA_TYPE')
    if (
        ancho < settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_WIDTH
        or alto < settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_HEIGHT
    ):
        return ResultadoValidacionDocumentalIA(
            calidad=Decimal('0'),
            legibilidad=Decimal('0'),
            confianza=Decimal('1'),
            corresponde_tipo=None,
            indicios_imagen_real=None,
            datos_consistentes=None,
            hallazgos=('IMAGE_TOO_SMALL',),
            proveedor='local-precheck',
            modelo='pillow',
            decision='REJECTED',
            razones=('IMAGE_TOO_SMALL',),
        )
    return None


def _decision_modelo(resultado):
    if resultado.decision:
        return resultado.decision
    return 'ACCEPTED' if not resultado.hallazgos else 'MANUAL_REVIEW'


def _es_concluyente(
    resultado,
    *,
    requiere_identidad=False,
    requiere_documento_colombiano=False,
):
    concluyente = bool(
        _decision_modelo(resultado) == 'ACCEPTED'
        and resultado.confianza
        >= Decimal(settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_CONFIDENCE)
        and resultado.calidad
        >= Decimal(settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_QUALITY)
        and resultado.legibilidad
        >= Decimal(settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_LEGIBILITY)
        and resultado.corresponde_tipo is True
        and resultado.indicios_imagen_real is True
        and resultado.datos_consistentes is True
        and not resultado.hallazgos
    )
    if not concluyente or not requiere_identidad:
        return concluyente
    return bool(
        resultado.es_documento_identidad is True
        and (
            not requiere_documento_colombiano
            or resultado.es_documento_colombiano is True
        )
        and resultado.lado_correcto is True
        and resultado.campos_visibles is True
        and resultado.borrosa is False
        and resultado.oscura is False
        and resultado.reflejos is False
        and resultado.recortada is False
        and resultado.obstruida is False
    )


def _es_rechazo_concluyente(resultado, *, documento):
    hallazgos_permitidos = {
        'MALFORMED_IMAGE',
        'IMAGE_TOO_SMALL',
        'TYPE_MISMATCH',
    }
    if documento.tipo in TIPOS_IDENTIDAD:
        hallazgos_permitidos.update({
            'NOT_IDENTITY_DOCUMENT',
            'SIDE_MISMATCH',
        })
        if (
            documento.participante
            and documento.participante.tipo_documento
            in {TipoDocumentoIdentidad.CC, TipoDocumentoIdentidad.TI}
        ):
            hallazgos_permitidos.add('NOT_COLOMBIAN_ID')
    return bool(
        _decision_modelo(resultado) == 'REJECTED'
        and resultado.confianza
        >= Decimal(settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_MIN_CONFIDENCE)
        and hallazgos_permitidos.intersection(resultado.hallazgos)
        and 'POSSIBLY_NOT_REAL' not in resultado.hallazgos
    )


def _resultado_estructurado(resultado):
    return {
        'schema_version': '2',
        'decision': _decision_modelo(resultado),
        'is_identity_document': resultado.es_documento_identidad,
        'is_colombian_document': resultado.es_documento_colombiano,
        'side_matches': resultado.lado_correcto,
        'required_fields_visible': resultado.campos_visibles,
        'is_blurred': resultado.borrosa,
        'is_too_dark': resultado.oscura,
        'has_glare': resultado.reflejos,
        'is_cropped': resultado.recortada,
        'is_obstructed': resultado.obstruida,
        'reason_codes': list(resultado.razones),
        'visible_document_type': resultado.tipo_documento_visible,
        'visible_document_number': resultado.numero_documento_visible,
        'visible_names': list(resultado.nombres_visibles),
    }


@transaction.atomic
def _finalizar_validacion(*, validacion_id, resultado=None, codigo_error=''):
    validacion = ValidacionIADocumento.objects.select_for_update().get(
        pk=validacion_id
    )
    documento = DocumentoFinanciacion.objects.select_for_update().get(
        pk=validacion.documento_id
    )
    if validacion.estado != EstadoValidacionIADocumento.STARTED:
        return documento, validacion

    ahora = timezone.now()
    valores = {'finalizado_en': ahora}
    resumen = dict(documento.resultado_procesamiento or {})
    if codigo_error:
        valores.update(
            estado=EstadoValidacionIADocumento.ERROR,
            codigo_error=_texto_controlado(codigo_error, 60),
        )
        resumen['ai_validation'] = {
            'attempt': str(validacion.pk),
            'decision': 'MANUAL_REVIEW',
            'error_code': valores['codigo_error'],
        }
    else:
        concluyente = _es_concluyente(
            resultado,
            requiere_identidad=documento.tipo in TIPOS_IDENTIDAD,
            requiere_documento_colombiano=bool(
                documento.participante
                and documento.participante.tipo_documento
                in {TipoDocumentoIdentidad.CC, TipoDocumentoIdentidad.TI}
            ),
        )
        rechazo_concluyente = _es_rechazo_concluyente(
            resultado,
            documento=documento,
        )
        if concluyente:
            estado = EstadoValidacionIADocumento.AUTO_APPROVED
        elif rechazo_concluyente:
            estado = EstadoValidacionIADocumento.AUTO_REJECTED
        else:
            estado = EstadoValidacionIADocumento.MANUAL_REVIEW
        hallazgos = list(resultado.hallazgos)
        if not concluyente and not hallazgos:
            hallazgos = ['INCONCLUSIVE']
        valores.update(
            estado=estado,
            codigo_error='',
            proveedor=resultado.proveedor,
            modelo=resultado.modelo,
            calidad=resultado.calidad,
            legibilidad=resultado.legibilidad,
            confianza=resultado.confianza,
            corresponde_tipo=resultado.corresponde_tipo,
            indicios_imagen_real=resultado.indicios_imagen_real,
            datos_consistentes=resultado.datos_consistentes,
            hallazgos=hallazgos,
            resultado_estructurado=_resultado_estructurado(resultado),
        )
        resumen['ai_validation'] = {
            'attempt': str(validacion.pk),
            'provider': resultado.proveedor,
            'model': resultado.modelo,
            'decision': estado,
            'quality': format(resultado.calidad, 'f'),
            'legibility': format(resultado.legibilidad, 'f'),
            'confidence': format(resultado.confianza, 'f'),
            'document_type_match': resultado.corresponde_tipo,
            'appears_real': resultado.indicios_imagen_real,
            'data_consistent': resultado.datos_consistentes,
            'finding_codes': hallazgos,
            'structured_result': _resultado_estructurado(resultado),
        }
        documento.nivel_confianza = resultado.confianza
        if (
            concluyente
            and documento.estado_validacion == EstadoValidacionDocumento.PENDING
        ):
            documento.estado_validacion = EstadoValidacionDocumento.APPROVED
            documento.revisado_por = None
            documento.revisado_en = ahora
            documento.motivo_rechazo = ''
            documento.observacion_revision = (
                'Aceptacion automatica por validacion documental concluyente.'
            )
        elif (
            rechazo_concluyente
            and documento.estado_validacion == EstadoValidacionDocumento.PENDING
        ):
            documento.estado_validacion = EstadoValidacionDocumento.REJECTED
            documento.revisado_por = None
            documento.revisado_en = ahora
            documento.motivo_rechazo = (
                MotivoRechazoDocumento.WRONG_DOCUMENT
                if {
                    'NOT_IDENTITY_DOCUMENT',
                    'NOT_COLOMBIAN_ID',
                    'SIDE_MISMATCH',
                    'TYPE_MISMATCH',
                }.intersection(hallazgos)
                else MotivoRechazoDocumento.UNREADABLE
            )
            documento.observacion_revision = (
                'La captura no cumple una validacion visual concluyente. '
                'Realiza una nueva captura del documento solicitado.'
            )
    ValidacionIADocumento.objects.filter(pk=validacion.pk).update(**valores)
    validacion.refresh_from_db()
    documento.resultado_procesamiento = resumen
    documento.full_clean()
    documento.save(
        update_fields=[
            'resultado_procesamiento',
            'nivel_confianza',
            'estado_validacion',
            'revisado_por',
            'revisado_en',
            'motivo_rechazo',
            'observacion_revision',
            'actualizado_en',
        ]
    )
    return documento, validacion


def procesar_validacion_documental_ia(
    *,
    documento,
    actor=None,
    origen=OrigenValidacionIADocumento.ADMIN,
    backend=None,
):
    _validar_permiso(actor, origen)
    if (
        backend is None
        and not settings.FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED
    ):
        return ResultadoProcesamientoValidacionIA(
            documento_id=documento.pk,
            estado='DISABLED',
            codigo_error='DOCUMENT_AI_DISABLED',
        )
    backend = backend or obtener_backend_validacion_ia()
    if not getattr(backend, 'enabled', True):
        return ResultadoProcesamientoValidacionIA(
            documento_id=documento.pk,
            estado='DISABLED',
            codigo_error='BACKEND_DISABLED',
        )
    documento, validacion, omision = _iniciar_validacion(
        documento_id=documento.pk,
        actor=actor,
        origen=origen,
    )
    if omision:
        return ResultadoProcesamientoValidacionIA(
            documento_id=documento.pk,
            estado=omision,
            validacion_id=getattr(validacion, 'pk', None),
        )

    try:
        if not documento.archivo:
            raise ErrorValidacionDocumentalIA('READ_ERROR')
        with documento.archivo.open('rb') as archivo:
            contenido = archivo.read(
                settings.FINANCIACION_EDUCATIVA_DOCUMENT_MAX_BYTES + 1
            )
        if not contenido or len(contenido) > settings.FINANCIACION_EDUCATIVA_DOCUMENT_MAX_BYTES:
            raise ErrorValidacionDocumentalIA('READ_ERROR')
        resultado = _prevalidar_imagen(contenido)
        if resultado is None:
            contexto = _contexto_declarado(documento)
            contexto['lado_esperado'] = LADO_IDENTIDAD.get(documento.tipo)
            resultado = backend.validar(
                contenido=contenido,
                content_type=documento.content_type,
                tipo_esperado=documento.tipo,
                contexto=contexto,
            )
        if not isinstance(resultado, ResultadoValidacionDocumentalIA):
            raise ErrorValidacionDocumentalIA('INVALID_RESPONSE')
    except ErrorValidacionDocumentalIA as error:
        _, validacion = _finalizar_validacion(
            validacion_id=validacion.pk,
            codigo_error=error.codigo,
        )
        return ResultadoProcesamientoValidacionIA(
            documento_id=documento.pk,
            estado=validacion.estado,
            validacion_id=validacion.pk,
            procesado=True,
            codigo_error=error.codigo,
        )
    except (OSError, ValueError):
        _, validacion = _finalizar_validacion(
            validacion_id=validacion.pk,
            codigo_error='READ_ERROR',
        )
        return ResultadoProcesamientoValidacionIA(
            documento_id=documento.pk,
            estado=validacion.estado,
            validacion_id=validacion.pk,
            procesado=True,
            codigo_error='READ_ERROR',
        )
    except Exception:
        _, validacion = _finalizar_validacion(
            validacion_id=validacion.pk,
            codigo_error='INTERNAL_ERROR',
        )
        return ResultadoProcesamientoValidacionIA(
            documento_id=documento.pk,
            estado=validacion.estado,
            validacion_id=validacion.pk,
            procesado=True,
            codigo_error='INTERNAL_ERROR',
        )

    documento, validacion = _finalizar_validacion(
        validacion_id=validacion.pk,
        resultado=resultado,
    )
    return ResultadoProcesamientoValidacionIA(
        documento_id=documento.pk,
        estado=validacion.estado,
        validacion_id=validacion.pk,
        procesado=True,
    )
