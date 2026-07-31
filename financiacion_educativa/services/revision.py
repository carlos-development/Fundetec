from django.core.exceptions import ValidationError
from django.db import transaction

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoEvidenciaMatricula,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    MotivoDecisionRevisionEducativa,
    RequisitoCorreccionEducativa,
    RolParticipante,
    TipoDecisionRevisionEducativa,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import (
    CondicionesFinancieras,
    DecisionRevisionEducativa,
    EntregaCorreoEstadoSolicitud,
    EvidenciaMatricula,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.correos import normalizar_destinatario
from financiacion_educativa.services.entrega_invitaciones import (
    calcular_hmac_destinatario,
)
from financiacion_educativa.services.entrega_correos_estado import (
    ejecutar_entrega_correo_estado,
)
from financiacion_educativa.services.estados import transicionar_solicitud
from financiacion_educativa.services.participantes import (
    solicitud_requiere_tutor,
)


TIPOS_DOCUMENTALES_APROBACION = {
    TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
    TipoDocumentoFinanciacion.STUDENT_ID_BACK,
    TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
    TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE,
}


def _validar_revisor(actor):
    if (
        not actor
        or not actor.is_authenticated
        or not actor.has_perm(
            'financiacion_educativa.revisar_solicitud_financiacion'
        )
    ):
        raise ValidationError('No tienes permiso para decidir esta solicitud.')


def _validar_aprobacion(solicitud):
    tipos_requeridos = set(TIPOS_DOCUMENTALES_APROBACION)
    if solicitud_requiere_tutor(solicitud):
        tipos_requeridos.update({
            TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
            TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
        })
    documentos = solicitud.documentos.filter(
        activo=True,
        tipo__in=tipos_requeridos,
    )
    presentes = set(documentos.values_list('tipo', flat=True))
    if not tipos_requeridos.issubset(presentes):
        raise ValidationError(
            'El expediente no contiene todos los documentos obligatorios.'
        )
    if documentos.exclude(
        estado_escaneo=EstadoEscaneoDocumento.SAFE,
        estado_validacion=EstadoValidacionDocumento.APPROVED,
    ).exists():
        raise ValidationError(
            'Todos los documentos obligatorios deben estar seguros y aceptados.'
        )
    try:
        evidencia = solicitud.evidencia_matricula
    except EvidenciaMatricula.DoesNotExist as exc:
        raise ValidationError(
            'La evidencia de matricula no esta disponible.'
        ) from exc
    if evidencia.estado != EstadoEvidenciaMatricula.ACCEPTED:
        raise ValidationError(
            'La evidencia de matricula debe estar aceptada.'
        )


def _programar_correo(*, solicitud, decision):
    destinatario = normalizar_destinatario(solicitud.correo)
    entrega = EntregaCorreoEstadoSolicitud.objects.create(
        solicitud=solicitud,
        decision=decision,
        destinatario_hmac=calcular_hmac_destinatario(destinatario),
    )
    transaction.on_commit(
        lambda: ejecutar_entrega_correo_estado(entrega_id=entrega.pk),
        robust=True,
    )
    return entrega


@transaction.atomic
def decidir_solicitud(
    *,
    solicitud,
    actor,
    tipo,
    motivo,
    mensaje_solicitante='',
    observacion_interna='',
    requisitos_pendientes=(),
):
    _validar_revisor(actor)
    if tipo not in TipoDecisionRevisionEducativa.values:
        raise ValidationError({'tipo': 'Selecciona una decision valida.'})
    if motivo not in MotivoDecisionRevisionEducativa.values:
        raise ValidationError({'motivo': 'Selecciona un motivo valido.'})
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW:
        raise ValidationError(
            'La solicitud no se encuentra pendiente de revision.'
        )
    mensaje = ' '.join((mensaje_solicitante or '').split())[:500]
    observacion = ' '.join((observacion_interna or '').split())[:1000]
    requisitos = list(dict.fromkeys(requisitos_pendientes or ()))
    if any(
        requisito not in RequisitoCorreccionEducativa.values
        for requisito in requisitos
    ):
        raise ValidationError({
            'requisitos_pendientes': 'Selecciona requisitos validos.',
        })
    fotografia = None
    if tipo == TipoDecisionRevisionEducativa.APPROVED:
        if requisitos:
            raise ValidationError({
                'requisitos_pendientes': (
                    'Una aprobacion no admite requisitos pendientes.'
                ),
            })
        if motivo != MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED:
            raise ValidationError({
                'motivo': 'La aprobacion usa el motivo de requisitos verificados.',
            })
        _validar_aprobacion(solicitud)
        fotografia = CondicionesFinancieras.objects.select_for_update().filter(
            solicitud=solicitud,
            activa=True,
            es_legado=False,
        ).first()
        if not fotografia:
            raise ValidationError(
                'La aprobacion requiere una fotografia financiera activa.'
            )
        if not mensaje:
            mensaje = (
                'La financiacion fue aprobada y la institucion puede '
                'activar el curso.'
            )
        CondicionesFinancieras.objects.filter(pk=fotografia.pk).update(
            bloqueada=True
        )
        nuevo_estado = EstadoSolicitudFinanciacion.APPROVED
    elif tipo == TipoDecisionRevisionEducativa.REJECTED:
        if requisitos:
            raise ValidationError({
                'requisitos_pendientes': (
                    'Un rechazo no admite requisitos pendientes.'
                ),
            })
        if motivo == MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED:
            raise ValidationError({'motivo': 'Selecciona un motivo de rechazo.'})
        if not mensaje:
            raise ValidationError({
                'mensaje_solicitante': (
                    'El rechazo requiere un mensaje para el solicitante.'
                ),
            })
        nuevo_estado = EstadoSolicitudFinanciacion.REJECTED
    else:
        if motivo == MotivoDecisionRevisionEducativa.REQUIREMENTS_VERIFIED:
            raise ValidationError({
                'motivo': 'Selecciona el motivo de la correccion.'
            })
        if not mensaje:
            raise ValidationError({
                'mensaje_solicitante': (
                    'La correccion requiere una indicacion para el solicitante.'
                ),
            })
        if not requisitos:
            raise ValidationError({
                'requisitos_pendientes': (
                    'Selecciona al menos un requisito por corregir.'
                ),
            })
        nuevo_estado = EstadoSolicitudFinanciacion.CORRECTION_REQUIRED

    decision = DecisionRevisionEducativa(
        solicitud=solicitud,
        tipo=tipo,
        motivo=motivo,
        mensaje_solicitante=mensaje,
        observacion_interna=observacion,
        requisitos_pendientes=requisitos,
        fotografia_financiera=fotografia,
        responsable=actor,
    )
    decision.full_clean()
    decision.save()
    solicitud = transicionar_solicitud(
        solicitud=solicitud,
        nuevo_estado=nuevo_estado,
        actor=actor,
        motivo=f'Decision de revision: {tipo}.',
        metadata={
            'decision_id': str(decision.pk),
            'decision_type': tipo,
            'reason_code': motivo,
            'course_authorized': (
                tipo == TipoDecisionRevisionEducativa.APPROVED
            ),
        },
    )
    if tipo == TipoDecisionRevisionEducativa.APPROVED:
        solicitud.participantes.update(
            identidad_verificada=True,
            relacion_verificada=True,
            actualizado_por=actor,
        )
    _programar_correo(solicitud=solicitud, decision=decision)
    return decision
