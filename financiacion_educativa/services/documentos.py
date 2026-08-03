import hashlib
import os
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    MotivoRechazoDocumento,
    OrigenCapturaDocumento,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TIPOS_DOCUMENTO_IDENTIDAD_CAMARA,
)
from financiacion_educativa.models import (
    DocumentoFinanciacion,
    EXTENSION_DOCUMENTO_POR_MIME,
    SolicitudFinanciacionEducativa,
)


EXTENSIONES_POR_MIME = {
    'application/pdf': {'.pdf'},
    'image/jpeg': {'.jpg', '.jpeg'},
    'image/png': {'.png'},
}

ROL_POR_TIPO_DOCUMENTO = {
    TipoDocumentoFinanciacion.STUDENT_IDENTIFICATION: RolParticipante.STUDENT,
    TipoDocumentoFinanciacion.STUDENT_ID_FRONT: RolParticipante.STUDENT,
    TipoDocumentoFinanciacion.STUDENT_ID_BACK: RolParticipante.STUDENT,
    TipoDocumentoFinanciacion.GUARDIAN_IDENTIFICATION: RolParticipante.GUARDIAN,
    TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT: RolParticipante.GUARDIAN,
    TipoDocumentoFinanciacion.GUARDIAN_ID_BACK: RolParticipante.GUARDIAN,
    TipoDocumentoFinanciacion.DEBTOR_IDENTIFICATION: RolParticipante.PRINCIPAL_DEBTOR,
    TipoDocumentoFinanciacion.INCOME_CERTIFICATE: RolParticipante.PRINCIPAL_DEBTOR,
}

TIPOS_CAPTURA_CAMARA = TIPOS_DOCUMENTO_IDENTIDAD_CAMARA


def _leer_archivo(archivo):
    if not archivo:
        raise ValidationError({'archivo': 'El archivo es obligatorio.'})
    tamano = getattr(archivo, 'size', None)
    if tamano is None:
        raise ValidationError({'archivo': 'No fue posible determinar el tamano.'})
    if tamano <= 0:
        raise ValidationError({'archivo': 'El archivo esta vacio.'})
    if tamano > settings.FINANCIACION_EDUCATIVA_DOCUMENT_MAX_BYTES:
        raise ValidationError({'archivo': 'El archivo supera el limite permitido.'})

    posicion = archivo.tell() if hasattr(archivo, 'tell') else 0
    archivo.seek(0)
    contenido = archivo.read(settings.FINANCIACION_EDUCATIVA_DOCUMENT_MAX_BYTES + 1)
    archivo.seek(posicion or 0)
    if len(contenido) != tamano:
        raise ValidationError({'archivo': 'El archivo no pudo validarse completamente.'})
    return contenido


def detectar_mime_real(contenido):
    if (
        b'%PDF-' in contenido[:1024]
        and b'%%EOF' in contenido[-2048:]
    ):
        return 'application/pdf'
    if contenido.startswith(b'\x89PNG\r\n\x1a\n') and b'IEND' in contenido[-32:]:
        return 'image/png'
    if contenido.startswith(b'\xff\xd8\xff') and contenido.endswith(b'\xff\xd9'):
        return 'image/jpeg'
    raise ValidationError({'archivo': 'El formato real del archivo no esta permitido.'})


def calcular_sha256_archivo(archivo):
    return hashlib.sha256(_leer_archivo(archivo)).hexdigest()


def _validar_archivo(archivo):
    contenido = _leer_archivo(archivo)
    mime = detectar_mime_real(contenido)
    if mime not in settings.FINANCIACION_EDUCATIVA_ALLOWED_DOCUMENT_MIME_TYPES:
        raise ValidationError({'archivo': 'El formato del archivo no esta permitido.'})

    extension = os.path.splitext(getattr(archivo, 'name', ''))[1].lower()
    if extension not in EXTENSIONES_POR_MIME[mime]:
        raise ValidationError({
            'archivo': 'La extension no corresponde al contenido del archivo.',
        })
    return {
        'mime': mime,
        'extension': EXTENSION_DOCUMENTO_POR_MIME[mime],
        'tamano': len(contenido),
        'sha256': hashlib.sha256(contenido).hexdigest(),
    }


def _validar_propiedad(solicitud, actor):
    if actor is None:
        return
    if not actor.is_authenticated:
        raise ValidationError('Debes iniciar sesion para cargar documentos.')
    if not actor.is_staff and solicitud.usuario_id != actor.pk:
        raise ValidationError('La solicitud no esta disponible.')


def _validar_tipo_y_participante(solicitud, tipo, participante):
    if participante and participante.solicitud_id != solicitud.pk:
        raise ValidationError({'participante': 'El participante no esta disponible.'})
    rol_requerido = ROL_POR_TIPO_DOCUMENTO.get(tipo)
    if rol_requerido:
        if not participante:
            raise ValidationError({'participante': 'Selecciona la persona correspondiente.'})
        if not participante.roles.filter(rol=rol_requerido).exists():
            raise ValidationError({
                'participante': 'La persona no tiene el rol requerido para este documento.',
            })
    if tipo == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE and participante:
        raise ValidationError({
            'participante': 'La evidencia de matricula pertenece a la solicitud.',
        })


def _nombre_original_minimizado(extension):
    return f'documento{extension}'


@transaction.atomic
def registrar_documento(
    *,
    solicitud,
    tipo,
    origen_captura,
    participante=None,
    archivo=None,
    referencia_almacenamiento='',
    nombre_original='',
    content_type='',
    tamano_bytes=None,
    actor=None,
    reemplaza_a=None,
):
    _validar_propiedad(solicitud, actor)
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if actor is not None and solicitud.estado not in {
        EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
    }:
        raise ValidationError('La solicitud no admite cargas documentales.')
    _validar_tipo_y_participante(solicitud, tipo, participante)

    if archivo:
        datos_archivo = _validar_archivo(archivo)
        if tipo in TIPOS_CAPTURA_CAMARA:
            if origen_captura != OrigenCapturaDocumento.CAMERA:
                raise ValidationError({
                    'archivo': 'La identificacion debe capturarse desde la camara.',
                })
            if datos_archivo['mime'] not in {'image/jpeg', 'image/png'}:
                raise ValidationError({
                    'archivo': 'La captura de identificacion debe ser una imagen.',
                })
        elif origen_captura == OrigenCapturaDocumento.CAMERA:
            raise ValidationError({
                'tipo': 'El origen camara solo admite capturas de identificacion.',
            })
        existente_hash = solicitud.documentos.filter(
            sha256=datos_archivo['sha256']
        ).first()
        if existente_hash:
            if (
                existente_hash.activo
                and existente_hash.tipo == tipo
                and existente_hash.participante_id == getattr(participante, 'pk', None)
            ):
                return existente_hash
            raise ValidationError({'archivo': 'El archivo ya fue registrado.'})
        nombre_seguro = f'{uuid.uuid4().hex}{datos_archivo["extension"]}'
        archivo.name = nombre_seguro
        nombre_original = _nombre_original_minimizado(datos_archivo['extension'])
        content_type = datos_archivo['mime']
        tamano_bytes = datos_archivo['tamano']
        sha256 = datos_archivo['sha256']
    elif referencia_almacenamiento:
        nombre_seguro = ''
        sha256 = ''
    else:
        raise ValidationError({'archivo': 'El archivo es obligatorio.'})

    if solicitud.documentos.count() >= settings.FINANCIACION_EDUCATIVA_DOCUMENT_MAX_COUNT:
        raise ValidationError('Se alcanzo el limite de documentos de la solicitud.')

    activo_existente = solicitud.documentos.filter(
        participante=participante,
        tipo=tipo,
        activo=True,
    ).first()
    if activo_existente and activo_existente.pk != getattr(reemplaza_a, 'pk', None):
        raise ValidationError('Ya existe un documento activo para este requisito.')

    documento = DocumentoFinanciacion(
        solicitud=solicitud,
        participante=participante,
        tipo=tipo,
        archivo=archivo,
        referencia_almacenamiento=referencia_almacenamiento,
        nombre_seguro=nombre_seguro,
        nombre_original=nombre_original,
        content_type=content_type,
        tamano_bytes=tamano_bytes,
        cargado_por=actor,
        estado_escaneo=EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
        estado_validacion=EstadoValidacionDocumento.PENDING,
        origen_captura=origen_captura,
        sha256=sha256,
        resultado_procesamiento={},
        nivel_confianza=None,
        reemplaza_a=reemplaza_a,
    )
    documento.full_clean()
    documento.save()
    return documento


@transaction.atomic
def reemplazar_documento(
    *,
    documento,
    archivo,
    actor,
    origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
):
    anterior = DocumentoFinanciacion.objects.select_for_update().select_related(
        'solicitud',
        'participante',
    ).get(pk=documento.pk)
    _validar_propiedad(anterior.solicitud, actor)
    if not anterior.activo:
        raise ValidationError('El documento ya fue reemplazado.')
    if (
        anterior.tipo in TIPOS_CAPTURA_CAMARA
        and origen_captura != OrigenCapturaDocumento.CAMERA
    ):
        raise ValidationError(
            'La identificacion solo puede reemplazarse desde la camara.'
        )
    datos_archivo = _validar_archivo(archivo)
    if datos_archivo['sha256'] == anterior.sha256:
        return anterior

    anterior.activo = False
    anterior.save(update_fields=['activo', 'actualizado_en'])
    nuevo = registrar_documento(
        solicitud=anterior.solicitud,
        participante=anterior.participante,
        tipo=anterior.tipo,
        origen_captura=origen_captura,
        archivo=archivo,
        actor=actor,
        reemplaza_a=anterior,
    )
    return nuevo


def _validar_revisor(actor):
    if (
        not actor
        or not actor.is_authenticated
        or not actor.has_perm(
            'financiacion_educativa.revisar_documento_financiacion'
        )
    ):
        raise ValidationError('No tiene permiso para revisar documentos.')


@transaction.atomic
def revisar_documento(
    *,
    documento,
    actor,
    aceptar,
    motivo_rechazo='',
    observacion='',
):
    _validar_revisor(actor)
    documento = DocumentoFinanciacion.objects.select_for_update().get(pk=documento.pk)
    destino = (
        EstadoValidacionDocumento.APPROVED
        if aceptar
        else EstadoValidacionDocumento.REJECTED
    )
    if documento.estado_validacion == destino:
        return documento
    if documento.estado_validacion == EstadoValidacionDocumento.APPROVED:
        raise ValidationError('Un documento aceptado debe reemplazarse, no editarse.')
    if aceptar and documento.estado_escaneo != EstadoEscaneoDocumento.SAFE:
        raise ValidationError('El documento aun no supera el escaneo de seguridad.')
    if not aceptar and motivo_rechazo not in MotivoRechazoDocumento.values:
        raise ValidationError({'motivo_rechazo': 'Selecciona un motivo valido.'})

    documento.estado_validacion = destino
    documento.revisado_por = actor
    documento.revisado_en = timezone.now()
    documento.motivo_rechazo = '' if aceptar else motivo_rechazo
    documento.observacion_revision = re.sub(
        r'[\r\n\t]+',
        ' ',
        (observacion or '').strip(),
    )[:500]
    documento.full_clean()
    documento.save(
        update_fields=[
            'estado_validacion',
            'revisado_por',
            'revisado_en',
            'motivo_rechazo',
            'observacion_revision',
            'actualizado_en',
        ]
    )
    return documento
