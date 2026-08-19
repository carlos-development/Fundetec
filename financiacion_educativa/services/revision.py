from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    MotivoDecisionRevisionEducativa,
    RequisitoCorreccionEducativa,
    RolParticipante,
    TipoDecisionRevisionEducativa,
)
from financiacion_educativa.models import (
    CondicionesFinancieras,
    DecisionRevisionEducativa,
    EntregaCorreoEstadoSolicitud,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.correos import normalizar_destinatario
from financiacion_educativa.services.artefactos_contractuales import (
    generar_artefactos_contractuales,
)
from financiacion_educativa.services.entrega_invitaciones import (
    calcular_hmac_destinatario,
)
from financiacion_educativa.services.outbox_correos import crear_correo_decision
from financiacion_educativa.services.estados import transicionar_solicitud
from financiacion_educativa.services.firma_zapsign import (
    validar_responsable_contractual_para_firma,
)
from financiacion_educativa.services.participantes import (
    solicitud_requiere_tutor,
)
from financiacion_educativa.services.orquestacion_automatica import (
    programar_orquestacion_automatica,
)
from financiacion_educativa.services.politica_documental import (
    validar_expediente_para_aprobacion,
)
from financiacion_educativa.services.reglas_financieras import (
    crear_fotografia_condiciones_financieras,
)


def _validar_revisor(actor):
    if (
        not actor
        or not actor.is_authenticated
        or not actor.has_perm(
            'financiacion_educativa.revisar_solicitud_financiacion'
        )
    ):
        raise ValidationError('No tienes permiso para decidir esta solicitud.')


def _validar_revisor_documental_operativo(actor):
    if (
        not actor
        or not actor.is_authenticated
        or not actor.has_perm(
            'financiacion_educativa.decidir_revision_documental_operativa'
        )
    ):
        raise ValidationError(
            'No tienes permiso para solicitar correcciones documentales.'
        )


def _validar_aprobacion(solicitud):
    validar_expediente_para_aprobacion(solicitud)


def _programar_correo(*, solicitud, decision):
    destinatario = normalizar_destinatario(solicitud.correo)
    entrega = EntregaCorreoEstadoSolicitud.objects.create(
        solicitud=solicitud,
        decision=decision,
        destinatario_hmac=calcular_hmac_destinatario(destinatario),
    )
    crear_correo_decision(
        solicitud=solicitud,
        decision=decision,
        entrega_legacy=entrega,
    )
    return entrega


@transaction.atomic
def _persistir_decision(
    *,
    solicitud,
    actor,
    tipo,
    motivo,
    mensaje_solicitante='',
    observacion_interna='',
    requisitos_pendientes=(),
    validar_permiso=True,
):
    if validar_permiso:
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
            fotografia = crear_fotografia_condiciones_financieras(
                solicitud,
                fecha_inicio_plan=timezone.localdate(),
                actor=actor,
                bloquear=True,
            )
        if not mensaje:
            mensaje = (
                'La revision fue aprobada. Ahora debes completar la firma '
                'del pagare antes de autorizar el curso.'
            )
        CondicionesFinancieras.objects.filter(pk=fotografia.pk).update(
            bloqueada=True
        )
        nuevo_estado = EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE
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
            'course_authorized': False,
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


def solicitar_correccion_documental(
    *,
    solicitud,
    actor,
    motivo,
    mensaje_solicitante,
    observacion_interna='',
    requisitos_pendientes=(),
):
    _validar_revisor_documental_operativo(actor)
    return _persistir_decision(
        solicitud=solicitud,
        actor=actor,
        tipo=TipoDecisionRevisionEducativa.CORRECTION_REQUESTED,
        motivo=motivo,
        mensaje_solicitante=mensaje_solicitante,
        observacion_interna=observacion_interna,
        requisitos_pendientes=requisitos_pendientes,
        validar_permiso=False,
    )


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
    if tipo == TipoDecisionRevisionEducativa.APPROVED:
        validar_responsable_contractual_para_firma(solicitud=solicitud)
    decision = _persistir_decision(
        solicitud=solicitud,
        actor=actor,
        tipo=tipo,
        motivo=motivo,
        mensaje_solicitante=mensaje_solicitante,
        observacion_interna=observacion_interna,
        requisitos_pendientes=requisitos_pendientes,
    )
    solicitud = SolicitudFinanciacionEducativa.objects.get(
        pk=decision.solicitud_id
    )
    if tipo == TipoDecisionRevisionEducativa.APPROVED:
        generar_artefactos_contractuales(
            solicitud=solicitud,
            actor=actor,
        )
        programar_orquestacion_automatica(solicitud_id=solicitud.pk)
    return decision
