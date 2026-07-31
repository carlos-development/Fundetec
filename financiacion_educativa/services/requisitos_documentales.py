from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoEvidenciaMatricula,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    RequisitoCorreccionEducativa,
    RolParticipante,
    TipoDecisionRevisionEducativa,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import EvidenciaMatricula, SolicitudFinanciacionEducativa
from financiacion_educativa.services.estados import transicionar_solicitud
from financiacion_educativa.services.participantes import (
    solicitud_requiere_tutor,
)
from financiacion_educativa.services.terminos import terminos_obligatorios_aceptados


@dataclass(frozen=True)
class RequisitoDocumental:
    codigo: str
    descripcion: str
    cumplido: bool


def _documento_apto_para_revision(solicitud, tipo, participante=None):
    return solicitud.documentos.filter(
        tipo=tipo,
        participante=participante,
        activo=True,
    ).exclude(
        estado_escaneo=EstadoEscaneoDocumento.BLOCKED,
    ).exclude(
        estado_validacion=EstadoValidacionDocumento.REJECTED,
    ).exists()


def _requisito_actualizado_despues_de_correccion(
    *,
    solicitud,
    requisito,
    decision,
    estudiante,
    tutor,
    deudor,
    evidencia,
):
    if requisito == RequisitoCorreccionEducativa.STUDENT:
        return bool(
            estudiante
            and estudiante.actualizado_en > decision.creada_en
        )
    if requisito == RequisitoCorreccionEducativa.GUARDIAN:
        return bool(tutor and tutor.actualizado_en > decision.creada_en)

    documentos = {
        RequisitoCorreccionEducativa.STUDENT_ID_FRONT: (
            TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            estudiante,
        ),
        RequisitoCorreccionEducativa.STUDENT_ID_BACK: (
            TipoDocumentoFinanciacion.STUDENT_ID_BACK,
            estudiante,
        ),
        RequisitoCorreccionEducativa.GUARDIAN_ID_FRONT: (
            TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
            tutor,
        ),
        RequisitoCorreccionEducativa.GUARDIAN_ID_BACK: (
            TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
            tutor,
        ),
        RequisitoCorreccionEducativa.INCOME_CERTIFICATE: (
            TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
            deudor,
        ),
    }
    if requisito in documentos:
        tipo, participante = documentos[requisito]
        return solicitud.documentos.filter(
            tipo=tipo,
            participante=participante,
            activo=True,
            actualizado_en__gt=decision.creada_en,
        ).exists()
    if requisito == RequisitoCorreccionEducativa.ENROLLMENT_EVIDENCE:
        return bool(
            evidencia
            and (
                evidencia.actualizada_en > decision.creada_en
                or evidencia.documento_soporte.actualizado_en
                > decision.creada_en
            )
        )
    return False


def _aplicar_correccion_vigente(
    *,
    solicitud,
    requisitos,
    estudiante,
    tutor,
    deudor,
    evidencia,
):
    if solicitud.estado != EstadoSolicitudFinanciacion.CORRECTION_REQUIRED:
        return requisitos
    decision = solicitud.decisiones_revision.filter(
        tipo=TipoDecisionRevisionEducativa.CORRECTION_REQUESTED,
    ).first()
    if not decision:
        return requisitos

    pendientes = set(decision.requisitos_pendientes)
    return [
        RequisitoDocumental(
            codigo=requisito.codigo,
            descripcion=requisito.descripcion,
            cumplido=(
                requisito.cumplido
                and (
                    requisito.codigo not in pendientes
                    or _requisito_actualizado_despues_de_correccion(
                        solicitud=solicitud,
                        requisito=requisito.codigo,
                        decision=decision,
                        estudiante=estudiante,
                        tutor=tutor,
                        deudor=deudor,
                        evidencia=evidencia,
                    )
                )
            ),
        )
        for requisito in requisitos
    ]


def calcular_requisitos_documentales(solicitud):
    roles = {
        asignacion.rol: asignacion.participante
        for asignacion in solicitud.roles_participantes.select_related(
            'participante'
        )
    }
    requisitos = []
    estudiante = roles.get(RolParticipante.STUDENT)
    deudor = roles.get(RolParticipante.PRINCIPAL_DEBTOR)
    tutor = roles.get(RolParticipante.GUARDIAN)

    requisitos.append(
        RequisitoDocumental(
            'TERMS',
            'Terminos y autorizaciones aceptados',
            terminos_obligatorios_aceptados(solicitud=solicitud),
        )
    )
    requisitos.append(
        RequisitoDocumental(
            'STUDENT',
            'Registrar la persona estudiante',
            estudiante is not None and estudiante.fecha_nacimiento is not None,
        )
    )
    requiere_tutor = solicitud_requiere_tutor(solicitud)
    if requiere_tutor:
        requisitos.append(
            RequisitoDocumental(
                'GUARDIAN',
                'Registrar tutor o representante declarado',
                tutor is not None,
            )
        )
    requisitos.append(
        RequisitoDocumental(
            'POTENTIAL_DEBTOR',
            'Definir el responsable contractual',
            deudor is not None,
        )
    )

    if estudiante:
        requisitos.append(
            RequisitoDocumental(
                'STUDENT_ID_FRONT',
                'Frente de la identificacion del estudiante capturado por camara',
                _documento_apto_para_revision(
                    solicitud,
                    TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                    estudiante,
                ),
            )
        )
        requisitos.append(
            RequisitoDocumental(
                'STUDENT_ID_BACK',
                'Reverso de la identificacion del estudiante capturado por camara',
                _documento_apto_para_revision(
                    solicitud,
                    TipoDocumentoFinanciacion.STUDENT_ID_BACK,
                    estudiante,
                ),
            )
        )
    if requiere_tutor and tutor:
        requisitos.append(
            RequisitoDocumental(
                'GUARDIAN_ID_FRONT',
                'Frente de la identificacion del tutor capturado por camara',
                _documento_apto_para_revision(
                    solicitud,
                    TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
                    tutor,
                ),
            )
        )
        requisitos.append(
            RequisitoDocumental(
                'GUARDIAN_ID_BACK',
                'Reverso de la identificacion del tutor capturado por camara',
                _documento_apto_para_revision(
                    solicitud,
                    TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
                    tutor,
                ),
            )
        )
    if deudor and deudor not in {estudiante, tutor}:
        requisitos.append(
            RequisitoDocumental(
                'DEBTOR_IDENTIFICATION',
                'Identificacion del responsable contractual aportada',
                _documento_apto_para_revision(
                    solicitud,
                    TipoDocumentoFinanciacion.DEBTOR_IDENTIFICATION,
                    deudor,
                ),
            )
        )
    if deudor:
        requisitos.append(
            RequisitoDocumental(
                'INCOME_CERTIFICATE',
                'Certificado de ingresos del responsable contractual aportado',
                _documento_apto_para_revision(
                    solicitud,
                    TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
                    deudor,
                ),
            )
        )

    try:
        evidencia = solicitud.evidencia_matricula
    except EvidenciaMatricula.DoesNotExist:
        evidencia = None
    evidencia_apta = bool(
        evidencia
        and evidencia.estado != EstadoEvidenciaMatricula.REJECTED
        and evidencia.documento_soporte.activo
        and evidencia.documento_soporte.estado_escaneo
        != EstadoEscaneoDocumento.BLOCKED
        and evidencia.documento_soporte.estado_validacion
        != EstadoValidacionDocumento.REJECTED
    )
    requisitos.append(
        RequisitoDocumental(
            'ENROLLMENT_EVIDENCE',
            'Evidencia de matricula aportada y sin bloqueos',
            evidencia_apta,
        )
    )
    return _aplicar_correccion_vigente(
        solicitud=solicitud,
        requisitos=requisitos,
        estudiante=estudiante,
        tutor=tutor,
        deudor=deudor,
        evidencia=evidencia,
    )


def fase_documental_completa(solicitud):
    requisitos = calcular_requisitos_documentales(solicitud)
    return bool(requisitos) and all(requisito.cumplido for requisito in requisitos)


@transaction.atomic
def completar_fase_documental(*, solicitud, actor):
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if not actor or not actor.is_authenticated or solicitud.usuario_id != actor.pk:
        raise ValidationError('La solicitud no esta disponible.')
    if solicitud.estado == EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW:
        return solicitud
    if solicitud.estado not in {
        EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
        EstadoSolicitudFinanciacion.CORRECTION_REQUIRED,
    }:
        raise ValidationError('La solicitud no esta en fase documental.')

    pendientes = [
        requisito.codigo
        for requisito in calcular_requisitos_documentales(solicitud)
        if not requisito.cumplido
    ]
    if pendientes:
        raise ValidationError({
            'requisitos': 'Aun existen requisitos documentales pendientes.',
        })
    return transicionar_solicitud(
        solicitud=solicitud,
        nuevo_estado=EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
        actor=actor,
        motivo='Fase documental completada; pendiente de revision de identidad.',
        metadata={'requisitos_completados': True},
    )
