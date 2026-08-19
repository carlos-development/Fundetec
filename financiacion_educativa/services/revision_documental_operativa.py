from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.html import strip_tags

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    MotivoDecisionRevisionEducativa,
    MotivoRechazoDocumento,
    RequisitoCorreccionEducativa,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import (
    DecisionRevisionDocumentoOperativa,
    DocumentoFinanciacion,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.documentos import revisar_documento
from financiacion_educativa.services.orquestacion_automatica import (
    programar_orquestacion_automatica,
)
from financiacion_educativa.services.requisitos_documentales import (
    fase_documental_completa,
)
from financiacion_educativa.services.revision import (
    solicitar_correccion_documental,
)


class ConflictoRevisionDocumental(ValidationError):
    pass


ESTADOS_SOLICITUD_REVISABLES = {
    EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
}

REQUISITO_POR_TIPO = {
    TipoDocumentoFinanciacion.STUDENT_ID_FRONT: RequisitoCorreccionEducativa.STUDENT_ID_FRONT,
    TipoDocumentoFinanciacion.STUDENT_ID_BACK: RequisitoCorreccionEducativa.STUDENT_ID_BACK,
    TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT: RequisitoCorreccionEducativa.GUARDIAN_ID_FRONT,
    TipoDocumentoFinanciacion.GUARDIAN_ID_BACK: RequisitoCorreccionEducativa.GUARDIAN_ID_BACK,
    TipoDocumentoFinanciacion.INCOME_CERTIFICATE: RequisitoCorreccionEducativa.INCOME_CERTIFICATE,
    TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE: RequisitoCorreccionEducativa.ENROLLMENT_EVIDENCE,
}

MOTIVO_SOLICITUD_POR_DOCUMENTO = {
    MotivoRechazoDocumento.UNREADABLE: MotivoDecisionRevisionEducativa.UNREADABLE_DOCUMENT,
    MotivoRechazoDocumento.INCOMPLETE: MotivoDecisionRevisionEducativa.INCOMPLETE_INFORMATION,
    MotivoRechazoDocumento.WRONG_DOCUMENT: MotivoDecisionRevisionEducativa.INCOMPLETE_INFORMATION,
    MotivoRechazoDocumento.EXPIRED: MotivoDecisionRevisionEducativa.INCOMPLETE_INFORMATION,
    MotivoRechazoDocumento.DATA_MISMATCH: MotivoDecisionRevisionEducativa.IDENTITY_MISMATCH,
    MotivoRechazoDocumento.OTHER: MotivoDecisionRevisionEducativa.OTHER,
}


@dataclass(frozen=True)
class ResultadoRevisionDocumento:
    decision: DecisionRevisionDocumentoOperativa
    repetida: bool
    continuacion_programada: bool = False


def _validar_actor(actor):
    if (
        not actor
        or not actor.is_authenticated
        or not actor.has_perm(
            'financiacion_educativa.decidir_revision_documental_operativa'
        )
    ):
        raise ValidationError(
            'No tienes permiso para decidir revisiones documentales.'
        )


def documento_admite_revision(documento):
    return bool(
        documento.activo
        and documento.solicitud.estado in ESTADOS_SOLICITUD_REVISABLES
        and documento.estado_escaneo == EstadoEscaneoDocumento.SAFE
        and documento.estado_validacion == EstadoValidacionDocumento.PENDING
    )


def _texto_plano(valor, *, campo, maximo, obligatorio=False):
    original = str(valor or '').strip()
    if original != strip_tags(original):
        raise ValidationError({campo: 'No se admite contenido HTML.'})
    texto = ' '.join(original.split())[:maximo]
    if obligatorio and not texto:
        raise ValidationError({campo: 'Este campo es obligatorio.'})
    return texto


def _decision_repetida(documento, accion, coincidencia):
    decision = documento.decisiones_operativas.order_by('-creada_en').first()
    if decision and decision.accion == accion:
        if all(getattr(decision, campo) == valor for campo, valor in coincidencia.items()):
            return decision
        raise ConflictoRevisionDocumental(
            'El documento ya tiene una decision con datos diferentes.'
        )
    return None


def _bloquear_documento(documento_id):
    documento_base = DocumentoFinanciacion.objects.only(
        'id', 'solicitud_id'
    ).get(pk=documento_id)
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=documento_base.solicitud_id
    )
    documento = DocumentoFinanciacion.objects.select_for_update().get(
        pk=documento_id
    )
    documento.solicitud = solicitud
    return documento, solicitud


def _validar_precondiciones(documento, solicitud, accion, coincidencia):
    repetida = _decision_repetida(documento, accion, coincidencia)
    if repetida:
        return repetida
    if not documento.activo:
        raise ConflictoRevisionDocumental('El documento ya fue reemplazado.')
    if solicitud.estado not in ESTADOS_SOLICITUD_REVISABLES:
        raise ConflictoRevisionDocumental(
            'La solicitud ya no admite revision documental.'
        )
    if documento.estado_validacion != EstadoValidacionDocumento.PENDING:
        raise ConflictoRevisionDocumental(
            'El documento ya tiene una decision diferente.'
        )
    if documento.estado_escaneo != EstadoEscaneoDocumento.SAFE:
        raise ConflictoRevisionDocumental(
            'El documento no ha superado el escaneo de seguridad.'
        )
    return None


@transaction.atomic
def aceptar_documento_operativo(*, documento_id, actor, observacion=''):
    _validar_actor(actor)
    documento, solicitud = _bloquear_documento(documento_id)
    observacion = _texto_plano(
        observacion,
        campo='observacion',
        maximo=500,
    )
    repetida = _validar_precondiciones(
        documento,
        solicitud,
        DecisionRevisionDocumentoOperativa.Accion.ACCEPTED,
        {'observacion_publica': observacion},
    )
    if repetida:
        return ResultadoRevisionDocumento(repetida, True, False)
    estado_documento_anterior = documento.estado_validacion
    estado_solicitud_anterior = solicitud.estado
    documento = revisar_documento(
        documento=documento,
        actor=actor,
        aceptar=True,
        observacion=observacion,
    )
    decision = DecisionRevisionDocumentoOperativa(
        documento=documento,
        solicitud=solicitud,
        institucion=solicitud.institucion,
        actor=actor,
        accion=DecisionRevisionDocumentoOperativa.Accion.ACCEPTED,
        observacion_publica=observacion,
        estado_documento_anterior=estado_documento_anterior,
        estado_documento_posterior=documento.estado_validacion,
        estado_solicitud_anterior=estado_solicitud_anterior,
        estado_solicitud_posterior=solicitud.estado,
    )
    decision.full_clean()
    decision.save()
    completa = fase_documental_completa(solicitud)
    if completa:
        transaction.on_commit(
            lambda: programar_orquestacion_automatica(
                solicitud_id=solicitud.pk
            )
        )
    return ResultadoRevisionDocumento(decision, False, completa)


@transaction.atomic
def solicitar_correccion_documento_operativo(
    *,
    documento_id,
    actor,
    motivo,
    mensaje_solicitante,
    nota_interna='',
):
    _validar_actor(actor)
    documento, solicitud = _bloquear_documento(documento_id)
    if motivo not in MotivoRechazoDocumento.values:
        raise ValidationError({'motivo': 'Selecciona un motivo valido.'})
    mensaje = _texto_plano(
        mensaje_solicitante,
        campo='mensaje_solicitante',
        maximo=500,
        obligatorio=True,
    )
    nota = _texto_plano(
        nota_interna,
        campo='nota_interna',
        maximo=1000,
    )
    repetida = _validar_precondiciones(
        documento,
        solicitud,
        DecisionRevisionDocumentoOperativa.Accion.CORRECTION_REQUESTED,
        {
            'motivo': motivo,
            'observacion_publica': mensaje,
            'nota_interna': nota,
        },
    )
    if repetida:
        return ResultadoRevisionDocumento(repetida, True, False)
    requisito = REQUISITO_POR_TIPO.get(documento.tipo)
    if not requisito:
        raise ValidationError(
            'El tipo documental no admite correccion operativa.'
        )
    estado_documento_anterior = documento.estado_validacion
    estado_solicitud_anterior = solicitud.estado
    documento = revisar_documento(
        documento=documento,
        actor=actor,
        aceptar=False,
        motivo_rechazo=motivo,
        observacion=mensaje,
    )
    decision_solicitud = solicitar_correccion_documental(
        solicitud=solicitud,
        actor=actor,
        motivo=MOTIVO_SOLICITUD_POR_DOCUMENTO[motivo],
        mensaje_solicitante=mensaje,
        observacion_interna=nota,
        requisitos_pendientes=[requisito],
    )
    solicitud.refresh_from_db(fields=['estado'])
    decision = DecisionRevisionDocumentoOperativa(
        documento=documento,
        solicitud=solicitud,
        institucion=solicitud.institucion,
        decision_solicitud=decision_solicitud,
        actor=actor,
        accion=(
            DecisionRevisionDocumentoOperativa.Accion.CORRECTION_REQUESTED
        ),
        motivo=motivo,
        observacion_publica=mensaje,
        nota_interna=nota,
        estado_documento_anterior=estado_documento_anterior,
        estado_documento_posterior=documento.estado_validacion,
        estado_solicitud_anterior=estado_solicitud_anterior,
        estado_solicitud_posterior=solicitud.estado,
    )
    decision.full_clean()
    decision.save()
    return ResultadoRevisionDocumento(decision, False, False)
