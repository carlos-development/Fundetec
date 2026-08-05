import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEvidenciaMatricula,
    EstadoEscaneoDocumento,
    EstadoProcesoFirmaEducativa,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    EstadoValidacionIADocumento,
    OrigenIntentoEscaneoDocumento,
    OrigenValidacionIADocumento,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import (
    EvidenciaMatricula,
    ProcesoFirmaEducativa,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.artefactos_contractuales import (
    generar_artefactos_contractuales,
)
from financiacion_educativa.services.escaneo_documentos import (
    procesar_escaneo_documento,
)
from financiacion_educativa.services.estados import transicionar_solicitud
from financiacion_educativa.services.firma_zapsign import (
    FirmaEducativaError,
    enviar_pagare_educativo,
)
from financiacion_educativa.services.politica_documental import (
    construir_politica_documental,
    documento_requiere_validacion_visual,
    validar_expediente_para_aprobacion,
)
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)
from financiacion_educativa.services.validacion_documental_ia import (
    procesar_validacion_documental_ia,
)


logger = logging.getLogger(__name__)
TIPOS_IMAGEN = frozenset({'image/jpeg', 'image/png'})


@dataclass(frozen=True)
class ResultadoOrquestacionAutomatica:
    solicitud_id: object
    estado: str
    codigo: str


def _resultado(solicitud, codigo):
    return ResultadoOrquestacionAutomatica(
        solicitud_id=solicitud.pk,
        estado=solicitud.estado,
        codigo=codigo,
    )


def _documentos_del_expediente(solicitud):
    documentos = {}
    for requisito in construir_politica_documental(solicitud):
        if requisito.documento is not None:
            documentos[requisito.documento.pk] = requisito.documento
    return list(documentos.values())


def _ultima_validacion_ia(documento):
    return documento.validaciones_ia.order_by('-numero').first()


def _documento_concluyente(documento):
    if (
        documento.estado_escaneo != EstadoEscaneoDocumento.SAFE
        or documento.estado_validacion != EstadoValidacionDocumento.APPROVED
    ):
        return False
    if not documento_requiere_validacion_visual(documento):
        decision = (documento.resultado_procesamiento or {}).get(
            'automatic_document_policy',
            {},
        )
        return decision.get('decision') == 'AUTO_APPROVED'
    ultima = _ultima_validacion_ia(documento)
    return bool(
        documento.content_type in TIPOS_IMAGEN
        and ultima
        and ultima.estado == EstadoValidacionIADocumento.AUTO_APPROVED
    )


@transaction.atomic
def _registrar_revision_manual_por_formato(documento):
    documento = type(documento).objects.select_for_update().get(pk=documento.pk)
    resumen = dict(documento.resultado_procesamiento or {})
    resumen['automatic_document_policy'] = {
        'decision': 'MANUAL_REVIEW',
        'reason': 'CONTENT_VALIDATION_UNSUPPORTED_MEDIA_TYPE',
        'content_type': documento.content_type,
    }
    documento.resultado_procesamiento = resumen
    documento.save(update_fields=['resultado_procesamiento', 'actualizado_en'])


@transaction.atomic
def _aceptar_soporte_matricula_pdf(documento):
    documento = type(documento).objects.select_for_update().get(pk=documento.pk)
    if (
        documento.tipo != TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE
        or documento.content_type != 'application/pdf'
        or documento.estado_escaneo != EstadoEscaneoDocumento.SAFE
        or not documento.archivo
        or not documento.sha256
        or not documento.tamano_bytes
    ):
        _registrar_revision_manual_por_formato(documento)
        return
    evidencia = EvidenciaMatricula.objects.select_for_update().filter(
        solicitud=documento.solicitud,
        documento_soporte=documento,
    ).first()
    if not evidencia:
        _registrar_revision_manual_por_formato(documento)
        return
    evidencia.full_clean()
    resumen = dict(documento.resultado_procesamiento or {})
    resumen['automatic_document_policy'] = {
        'decision': 'AUTO_APPROVED',
        'reason': 'OPTIONAL_ENROLLMENT_PDF_WITH_DECLARED_DATA_AND_CLEAN_SCAN',
        'content_type': documento.content_type,
    }
    documento.resultado_procesamiento = resumen
    documento.estado_validacion = EstadoValidacionDocumento.APPROVED
    documento.revisado_por = None
    documento.revisado_en = timezone.now()
    documento.motivo_rechazo = ''
    documento.observacion_revision = (
        'Aceptacion automatica por politica de soporte opcional PDF, '
        'datos de matricula declarados y escaneo limpio.'
    )
    documento.full_clean()
    documento.save(
        update_fields=[
            'resultado_procesamiento',
            'estado_validacion',
            'revisado_por',
            'revisado_en',
            'motivo_rechazo',
            'observacion_revision',
            'actualizado_en',
        ]
    )


def _procesar_seguridad_e_ia(solicitud):
    documentos = _documentos_del_expediente(solicitud)
    for documento in documentos:
        if documento.estado_escaneo == EstadoEscaneoDocumento.PENDING_SECURITY_SCAN:
            procesar_escaneo_documento(
                documento=documento,
                origen=OrigenIntentoEscaneoDocumento.COMMAND,
            )

    solicitud = SolicitudFinanciacionEducativa.objects.get(pk=solicitud.pk)
    documentos = _documentos_del_expediente(solicitud)
    if any(
        documento.estado_escaneo != EstadoEscaneoDocumento.SAFE
        for documento in documentos
    ):
        return solicitud, False, 'SECURITY_REVIEW_REQUIRED'

    for documento in documentos:
        if documento.estado_validacion != EstadoValidacionDocumento.PENDING:
            continue
        if not documento_requiere_validacion_visual(documento):
            _aceptar_soporte_matricula_pdf(documento)
            continue
        ultima = _ultima_validacion_ia(documento)
        if ultima and ultima.estado == EstadoValidacionIADocumento.MANUAL_REVIEW:
            continue
        if documento.content_type not in TIPOS_IMAGEN:
            _registrar_revision_manual_por_formato(documento)
            continue
        procesar_validacion_documental_ia(
            documento=documento,
            origen=OrigenValidacionIADocumento.AUTOMATIC,
        )

    solicitud = SolicitudFinanciacionEducativa.objects.get(pk=solicitud.pk)
    documentos = _documentos_del_expediente(solicitud)
    if not documentos or any(
        not _documento_concluyente(documento)
        for documento in documentos
    ):
        return solicitud, False, 'MANUAL_REVIEW_REQUIRED'
    return solicitud, True, 'DOCUMENTS_CONCLUSIVE'


def _aceptar_evidencia_matricula_concluyente(solicitud):
    evidencia = EvidenciaMatricula.objects.select_for_update().select_related(
        'documento_soporte'
    ).filter(solicitud=solicitud).first()
    if not evidencia or not evidencia.documento_soporte_id:
        return
    if evidencia.estado == EstadoEvidenciaMatricula.ACCEPTED:
        return
    if evidencia.estado != EstadoEvidenciaMatricula.PENDING:
        raise ValidationError('La evidencia de matricula requiere revision manual.')
    if not _documento_concluyente(evidencia.documento_soporte):
        raise ValidationError('El soporte de matricula no es concluyente.')
    evidencia.estado = EstadoEvidenciaMatricula.ACCEPTED
    evidencia.revisado_por = None
    evidencia.revisado_en = timezone.now()
    evidencia.motivo_rechazo = ''
    evidencia.observacion_revision = (
        'Aceptacion automatica por validacion documental concluyente.'
    )
    evidencia.full_clean()
    evidencia.save(
        update_fields=[
            'estado',
            'revisado_por',
            'revisado_en',
            'motivo_rechazo',
            'observacion_revision',
            'actualizada_en',
        ]
    )


@transaction.atomic
def _aprobar_expediente_concluyente(solicitud):
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW:
        return solicitud
    _aceptar_evidencia_matricula_concluyente(solicitud)
    politica = validar_expediente_para_aprobacion(solicitud)
    if any(
        requisito.documento is not None
        and not _documento_concluyente(requisito.documento)
        for requisito in politica
    ):
        raise ValidationError(
            'La aprobacion automatica exige validaciones IA concluyentes.'
        )

    fotografia = solicitud.fotografias_financieras.select_for_update().filter(
        activa=True,
        es_legado=False,
    ).first()
    if not fotografia:
        fotografia = crear_fotografia_condiciones_financieras(
            solicitud,
            fecha_inicio_plan=timezone.localdate(),
            bloquear=True,
        )
    elif not fotografia.bloqueada:
        fotografia.bloqueada = True
        fotografia.full_clean()
        fotografia.save(update_fields=['bloqueada'])

    solicitud = transicionar_solicitud(
        solicitud=solicitud,
        nuevo_estado=EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
        motivo='Expediente aprobado por validacion automatica concluyente.',
        metadata={
            'automatic': True,
            'financial_snapshot_id': str(fotografia.pk),
            'course_authorized': False,
        },
    )
    generar_artefactos_contractuales(solicitud=solicitud)
    solicitud.participantes.update(
        identidad_verificada=True,
        relacion_verificada=True,
    )
    return solicitud


def _continuar_firma(solicitud):
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE:
        return _resultado(solicitud, 'NOT_PENDING_SIGNATURE_SEND')
    artefactos = generar_artefactos_contractuales(solicitud=solicitud)
    proceso = ProcesoFirmaEducativa.objects.get(artefacto=artefactos.pagare)
    try:
        proceso = enviar_pagare_educativo(proceso=proceso)
    except (FirmaEducativaError, ValidationError):
        solicitud.refresh_from_db()
        return _resultado(solicitud, 'SIGNATURE_SEND_RETRY_REQUIRED')
    solicitud.refresh_from_db()
    codigo = (
        'PENDING_SIGNATURE'
        if proceso.estado == EstadoProcesoFirmaEducativa.SENT
        else 'SIGNATURE_SEND_NOT_CONFIRMED'
    )
    return _resultado(solicitud, codigo)


def ejecutar_orquestacion_automatica(*, solicitud_id):
    solicitud = SolicitudFinanciacionEducativa.objects.get(pk=solicitud_id)
    if not settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED:
        return _resultado(solicitud, 'AUTOMATION_DISABLED')
    if solicitud.estado == EstadoSolicitudFinanciacion.APPROVED:
        return _resultado(solicitud, 'ALREADY_APPROVED')
    if solicitud.estado == EstadoSolicitudFinanciacion.PENDING_SIGNATURE:
        return _resultado(solicitud, 'ALREADY_PENDING_SIGNATURE')
    if solicitud.estado == EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE:
        return _continuar_firma(solicitud)
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW:
        return _resultado(solicitud, 'STATE_NOT_AUTOMATABLE')

    solicitud, concluyente, codigo = _procesar_seguridad_e_ia(solicitud)
    if not concluyente:
        return _resultado(solicitud, codigo)
    solicitud = _aprobar_expediente_concluyente(solicitud)
    return _continuar_firma(solicitud)


def ejecutar_orquestacion_automatica_segura(*, solicitud_id):
    try:
        return ejecutar_orquestacion_automatica(solicitud_id=solicitud_id)
    except Exception as error:
        logger.error(
            'Fallo controlado en orquestacion educativa automatica: '
            'solicitud=%s tipo=%s',
            solicitud_id,
            type(error).__name__,
        )
        solicitud = SolicitudFinanciacionEducativa.objects.filter(
            pk=solicitud_id
        ).first()
        if not solicitud:
            raise
        return _resultado(solicitud, 'AUTOMATION_ERROR')


def programar_orquestacion_automatica(*, solicitud_id):
    if not settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED:
        return False
    transaction.on_commit(
        lambda: ejecutar_orquestacion_automatica_segura(
            solicitud_id=solicitud_id
        ),
        robust=True,
    )
    return True
