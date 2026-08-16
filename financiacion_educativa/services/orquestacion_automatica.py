import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import (
    EtapaAutomatizacionEducativa,
    EstadoEvidenciaMatricula,
    EstadoEscaneoDocumento,
    EstadoProcesoFirmaEducativa,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    EstadoValidacionIADocumento,
    EstadoProcesoAutomatizacionEducativa,
    EstadoProcesamientoContenidoDocumento,
    OrigenIntentoEscaneoDocumento,
    OrigenValidacionIADocumento,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import (
    DocumentoFinanciacion,
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
    marcar_envio_inconcluso_para_conciliacion,
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
from financiacion_educativa.services.clasificacion_contenido_documental import (
    procesar_contenido_documental,
)


logger = logging.getLogger(__name__)
TIPOS_IMAGEN = frozenset({'image/jpeg', 'image/png'})
TIPOS_CLASIFICACION_CONTENIDO = frozenset({
    TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
    TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE,
})


def _usa_clasificacion_contenido(documento):
    return bool(
        settings.FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED
        and documento.tipo in TIPOS_CLASIFICACION_CONTENIDO
    )


@dataclass(frozen=True)
class ResultadoOrquestacionAutomatica:
    solicitud_id: object
    estado: str
    codigo: str


@dataclass(frozen=True)
class SalidaEtapaPersistente:
    estado: str
    codigo: str
    siguiente_etapa: str = ''
    requisitos_correccion: tuple[str, ...] = ()


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


def _ultimo_procesamiento_contenido(documento):
    return documento.procesamientos_contenido.order_by('-numero').first()


def _documento_concluyente(documento):
    if (
        documento.estado_escaneo != EstadoEscaneoDocumento.SAFE
        or documento.estado_validacion != EstadoValidacionDocumento.APPROVED
    ):
        return False
    if _usa_clasificacion_contenido(documento):
        ultimo = _ultimo_procesamiento_contenido(documento)
        return bool(
            ultimo
            and ultimo.hash_original == documento.sha256
            and ultimo.estado
            == EstadoProcesamientoContenidoDocumento.ACCEPTED
        )
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
def _marcar_pdf_pendiente_de_procesamiento(documento):
    documento = type(documento).objects.select_for_update().get(pk=documento.pk)
    resumen = dict(documento.resultado_procesamiento or {})
    resumen['automatic_document_policy'] = {
        'decision': 'MANUAL_EXCEPTION',
        'reason': 'PDF_CONTENT_PROCESSING_REQUIRED',
        'content_type': documento.content_type,
    }
    documento.resultado_procesamiento = resumen
    documento.observacion_revision = (
        'El PDF supero el escaneo de seguridad, pero requiere inspeccion '
        'de contenido antes de cualquier aceptacion.'
    )
    documento.save(
        update_fields=[
            'resultado_procesamiento',
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
                origen=OrigenIntentoEscaneoDocumento.AUTOMATIC,
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
        if _usa_clasificacion_contenido(documento):
            procesar_contenido_documental(documento=documento)
            continue
        if not documento_requiere_validacion_visual(documento):
            _marcar_pdf_pendiente_de_procesamiento(documento)
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


def procesar_documento_automaticamente(*, documento_id):
    if not settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED:
        return 'AUTOMATION_DISABLED'
    documento = DocumentoFinanciacion.objects.select_related('solicitud').filter(
        pk=documento_id,
        activo=True,
    ).first()
    if not documento:
        return 'DOCUMENT_NOT_AVAILABLE'
    if documento.estado_escaneo == EstadoEscaneoDocumento.PENDING_SECURITY_SCAN:
        procesar_escaneo_documento(
            documento=documento,
            origen=OrigenIntentoEscaneoDocumento.AUTOMATIC,
        )
    documento.refresh_from_db()
    if documento.estado_escaneo != EstadoEscaneoDocumento.SAFE:
        return 'SECURITY_REVIEW_REQUIRED'
    if (
        documento.estado_validacion == EstadoValidacionDocumento.PENDING
        and _usa_clasificacion_contenido(documento)
    ):
        procesar_contenido_documental(documento=documento)
    elif (
        documento.estado_validacion == EstadoValidacionDocumento.PENDING
        and documento_requiere_validacion_visual(documento)
        and documento.content_type in TIPOS_IMAGEN
    ):
        procesar_validacion_documental_ia(
            documento=documento,
            origen=OrigenValidacionIADocumento.AUTOMATIC,
        )
    documento.refresh_from_db()
    if (
        documento.solicitud.estado
        == EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
    ):
        ejecutar_orquestacion_automatica_segura(
            solicitud_id=documento.solicitud_id
        )
    return 'DOCUMENT_PROCESSED'


def procesar_documento_automaticamente_seguro(*, documento_id):
    try:
        return procesar_documento_automaticamente(documento_id=documento_id)
    except Exception as error:
        logger.error(
            'Fallo controlado en procesamiento documental automatico: '
            'documento=%s tipo=%s',
            documento_id,
            type(error).__name__,
        )
        return 'AUTOMATION_ERROR'


def programar_procesamiento_documento_automatico(*, documento_id):
    if not settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED:
        return False
    documento = DocumentoFinanciacion.objects.filter(pk=documento_id).first()
    if not documento:
        return False
    from financiacion_educativa.services.cola_automatizacion import (
        encolar_proceso_automatizacion,
    )

    proceso, _ = encolar_proceso_automatizacion(
        solicitud_id=documento.solicitud_id
    )
    return proceso is not None


def _aceptar_evidencia_matricula_concluyente(solicitud):
    evidencia = (
        EvidenciaMatricula.objects.select_for_update(of=('self',))
        .select_related('documento_soporte')
        .filter(solicitud=solicitud)
        .first()
    )
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
def _aplicar_correccion_por_rechazo_automatico(solicitud):
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW:
        return solicitud, False
    rechazados = []
    for documento in _documentos_del_expediente(solicitud):
        ultima = _ultima_validacion_ia(documento)
        ultimo_contenido = _ultimo_procesamiento_contenido(documento)
        if (
            documento.estado_validacion == EstadoValidacionDocumento.REJECTED
            and (
                (
                    ultima
                    and ultima.estado == EstadoValidacionIADocumento.AUTO_REJECTED
                )
                or (
                    ultimo_contenido
                    and ultimo_contenido.estado
                    == EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED
                )
            )
        ):
            rechazados.append(documento.tipo)
    if not rechazados:
        return solicitud, False
    solicitud = transicionar_solicitud(
        solicitud=solicitud,
        nuevo_estado=EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
        motivo='La validacion visual concluyente requiere una nueva captura.',
        metadata={
            'automatic': True,
            'reason_code': 'DOCUMENT_AUTO_REJECTED',
            'document_types': sorted(set(rechazados)),
            'course_authorized': False,
        },
    )
    from financiacion_educativa.services.outbox_correos import (
        crear_correo_correccion_automatica,
    )

    proceso = solicitud.procesos_automatizacion.order_by(
        '-version_expediente'
    ).first()
    crear_correo_correccion_automatica(
        solicitud=solicitud,
        proceso_id=(proceso.pk if proceso else f'legacy-{solicitud.actualizada_en.isoformat()}'),
        requisitos=rechazados,
    )
    return solicitud, True


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
    from financiacion_educativa.services.outbox_correos import (
        crear_correo_continuacion_automatica,
    )

    crear_correo_continuacion_automatica(
        solicitud=solicitud,
        fotografia_id=fotografia.pk,
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
        solicitud, requiere_correccion = _aplicar_correccion_por_rechazo_automatico(
            solicitud
        )
        if requiere_correccion:
            return _resultado(solicitud, 'DOCUMENT_CORRECTION_REQUIRED')
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
    from financiacion_educativa.services.cola_automatizacion import (
        encolar_proceso_automatizacion,
    )

    proceso, _ = encolar_proceso_automatizacion(solicitud_id=solicitud_id)
    return proceso is not None


def _salida_continuar(siguiente, codigo):
    return SalidaEtapaPersistente(
        estado=EstadoProcesoAutomatizacionEducativa.QUEUED,
        codigo=codigo,
        siguiente_etapa=siguiente,
    )


def _etapa_escaneo(solicitud):
    documentos = _documentos_del_expediente(solicitud)
    for documento in documentos:
        if documento.estado_escaneo == EstadoEscaneoDocumento.PENDING_SECURITY_SCAN:
            procesar_escaneo_documento(
                documento=documento,
                origen=OrigenIntentoEscaneoDocumento.AUTOMATIC,
            )
    documentos = _documentos_del_expediente(
        SolicitudFinanciacionEducativa.objects.get(pk=solicitud.pk)
    )
    if any(d.estado_escaneo == EstadoEscaneoDocumento.BLOCKED for d in documentos):
        return SalidaEtapaPersistente(
            estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
            codigo='MALWARE_DETECTED',
        )
    if any(
        d.estado_escaneo != EstadoEscaneoDocumento.SAFE for d in documentos
    ):
        return SalidaEtapaPersistente(
            estado=EstadoProcesoAutomatizacionEducativa.RETRYING,
            codigo='SECURITY_SCAN_TEMPORARY_ERROR',
        )
    return _salida_continuar(
        EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION,
        'SECURITY_SCAN_COMPLETED',
    )


def _etapa_validacion(solicitud):
    documentos = _documentos_del_expediente(solicitud)
    hubo_error_ia = False
    hubo_error_contenido = False
    for documento in documentos:
        if documento.estado_validacion != EstadoValidacionDocumento.PENDING:
            continue
        if _usa_clasificacion_contenido(documento):
            resultado = procesar_contenido_documental(documento=documento)
            hubo_error_contenido = (
                hubo_error_contenido
                or resultado.estado == EstadoProcesamientoContenidoDocumento.RETRYING
            )
            continue
        if not documento_requiere_validacion_visual(documento):
            continue
        if documento.content_type not in TIPOS_IMAGEN:
            _registrar_revision_manual_por_formato(documento)
            continue
        ultima = _ultima_validacion_ia(documento)
        if ultima and ultima.estado in {
            EstadoValidacionIADocumento.AUTO_APPROVED,
            EstadoValidacionIADocumento.AUTO_REJECTED,
            EstadoValidacionIADocumento.MANUAL_REVIEW,
        }:
            continue
        resultado = procesar_validacion_documental_ia(
            documento=documento,
            origen=OrigenValidacionIADocumento.AUTOMATIC,
        )
        hubo_error_ia = hubo_error_ia or resultado.estado == 'ERROR'
    if hubo_error_contenido:
        return SalidaEtapaPersistente(
            estado=EstadoProcesoAutomatizacionEducativa.RETRYING,
            codigo='DOCUMENT_CONTENT_TEMPORARY_ERROR',
        )
    if hubo_error_ia:
        return SalidaEtapaPersistente(
            estado=EstadoProcesoAutomatizacionEducativa.RETRYING,
            codigo='DOCUMENT_AI_TEMPORARY_ERROR',
        )
    return _salida_continuar(
        EtapaAutomatizacionEducativa.DECISION,
        'DOCUMENT_VALIDATION_COMPLETED',
    )


def _requisitos_por_documentos(documentos):
    return tuple(sorted({documento.tipo for documento in documentos}))


def _etapa_decision(solicitud):
    documentos = _documentos_del_expediente(solicitud)
    rechazados = [
        documento
        for documento in documentos
        if documento.estado_validacion == EstadoValidacionDocumento.REJECTED
        and (
            (
                (ultima := _ultima_validacion_ia(documento))
                and ultima.estado == EstadoValidacionIADocumento.AUTO_REJECTED
            )
            or (
                (contenido := _ultimo_procesamiento_contenido(documento))
                and contenido.estado
                == EstadoProcesamientoContenidoDocumento.CORRECTION_REQUIRED
            )
        )
    ]
    if rechazados:
        solicitud, _ = _aplicar_correccion_por_rechazo_automatico(solicitud)
        return SalidaEtapaPersistente(
            estado=EstadoProcesoAutomatizacionEducativa.CORRECTION_REQUIRED,
            codigo='DOCUMENT_CORRECTION_REQUIRED',
            requisitos_correccion=_requisitos_por_documentos(rechazados),
        )
    reintentables = [
        documento
        for documento in documentos
        if (contenido := _ultimo_procesamiento_contenido(documento))
        and contenido.estado == EstadoProcesamientoContenidoDocumento.RETRYING
    ]
    if reintentables:
        return SalidaEtapaPersistente(
            estado=EstadoProcesoAutomatizacionEducativa.RETRYING,
            codigo='DOCUMENT_CONTENT_TEMPORARY_ERROR',
            requisitos_correccion=_requisitos_por_documentos(reintentables),
        )
    manuales = [
        documento
        for documento in documentos
        if (
            (
                (ultima := _ultima_validacion_ia(documento))
                and ultima.estado == EstadoValidacionIADocumento.MANUAL_REVIEW
            )
            or (
                (contenido := _ultimo_procesamiento_contenido(documento))
                and contenido.estado in {
                    EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION,
                    EstadoProcesamientoContenidoDocumento.FAILED,
                }
            )
        )
    ]
    if manuales:
        return SalidaEtapaPersistente(
            estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
            codigo='DOCUMENT_VALIDATION_INCONCLUSIVE',
            requisitos_correccion=_requisitos_por_documentos(manuales),
        )
    if not documentos or any(not _documento_concluyente(d) for d in documentos):
        return SalidaEtapaPersistente(
            estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
            codigo='DOCUMENT_RESULT_NOT_CONCLUSIVE',
        )
    return _salida_continuar(
        EtapaAutomatizacionEducativa.FINANCIAL_SNAPSHOT,
        'AUTOMATIC_DECISION_CONTINUE',
    )


def ejecutar_etapa_persistente(*, solicitud_id, etapa):
    solicitud = SolicitudFinanciacionEducativa.objects.get(pk=solicitud_id)
    if etapa == EtapaAutomatizacionEducativa.SECURITY_SCAN:
        return _etapa_escaneo(solicitud)
    if etapa == EtapaAutomatizacionEducativa.DOCUMENT_VALIDATION:
        return _etapa_validacion(solicitud)
    if etapa == EtapaAutomatizacionEducativa.DECISION:
        return _etapa_decision(solicitud)
    if etapa == EtapaAutomatizacionEducativa.FINANCIAL_SNAPSHOT:
        _aprobar_expediente_concluyente(solicitud)
        return _salida_continuar(
            EtapaAutomatizacionEducativa.CONTRACT_GENERATION,
            'FINANCIAL_SNAPSHOT_LOCKED',
        )
    if etapa == EtapaAutomatizacionEducativa.CONTRACT_GENERATION:
        generar_artefactos_contractuales(solicitud=solicitud)
        return _salida_continuar(
            EtapaAutomatizacionEducativa.SIGNATURE_SEND,
            'CONTRACTS_GENERATED',
        )
    if etapa == EtapaAutomatizacionEducativa.SIGNATURE_SEND:
        proceso_existente = solicitud.procesos_firma.order_by('-creado_en').first()
        if (
            proceso_existente
            and proceso_existente.estado == EstadoProcesoFirmaEducativa.SENDING
        ):
            marcar_envio_inconcluso_para_conciliacion(
                proceso=proceso_existente
            )
            return SalidaEtapaPersistente(
                estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
                codigo='SIGNATURE_SEND_AMBIGUOUS',
            )
        if (
            solicitud.estado == EstadoSolicitudFinanciacion.PENDING_SIGNATURE
            and proceso_existente
            and proceso_existente.estado == EstadoProcesoFirmaEducativa.SENT
        ):
            return SalidaEtapaPersistente(
                estado=EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE,
                codigo='PENDING_SIGNATURE',
                siguiente_etapa=EtapaAutomatizacionEducativa.WAITING_SIGNATURE,
            )
        resultado = _continuar_firma(solicitud)
        if resultado.codigo == 'PENDING_SIGNATURE':
            return SalidaEtapaPersistente(
                estado=EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE,
                codigo='PENDING_SIGNATURE',
                siguiente_etapa=EtapaAutomatizacionEducativa.WAITING_SIGNATURE,
            )
        proceso = solicitud.procesos_firma.order_by('-creado_en').first()
        if proceso and proceso.codigo_ultimo_error == 'SIGNATURE_SEND_AMBIGUOUS':
            return SalidaEtapaPersistente(
                estado=EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
                codigo='SIGNATURE_SEND_AMBIGUOUS',
            )
        return SalidaEtapaPersistente(
            estado=EstadoProcesoAutomatizacionEducativa.RETRYING,
            codigo='SIGNATURE_SEND_RETRY_REQUIRED',
        )
    raise ValidationError('La etapa de automatizacion no es valida.')
