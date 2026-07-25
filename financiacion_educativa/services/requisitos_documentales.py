from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoEvidenciaMatricula,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    RolParticipante,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import (
    EvidenciaMatricula,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.estados import transicionar_solicitud
from financiacion_educativa.services.participantes import (
    estudiante_requiere_tutor,
    fecha_referencia_solicitud,
)


@dataclass(frozen=True)
class RequisitoDocumental:
    codigo: str
    descripcion: str
    cumplido: bool


def _documento_cumple(solicitud, tipo, participante=None):
    return solicitud.documentos.filter(
        tipo=tipo,
        participante=participante,
        activo=True,
        estado_escaneo=EstadoEscaneoDocumento.SAFE,
        estado_validacion=EstadoValidacionDocumento.APPROVED,
    ).exists()


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
            'STUDENT',
            'Registrar la persona estudiante',
            estudiante is not None and estudiante.fecha_nacimiento is not None,
        )
    )
    requiere_tutor = bool(
        estudiante
        and estudiante.fecha_nacimiento
        and estudiante_requiere_tutor(
            estudiante,
            fecha_referencia=fecha_referencia_solicitud(solicitud),
        )
    )
    requisitos.append(
        RequisitoDocumental(
            'GUARDIAN',
            'Registrar tutor o representante declarado',
            not requiere_tutor or tutor is not None,
        )
    )
    requisitos.append(
        RequisitoDocumental(
            'POTENTIAL_DEBTOR',
            'Identificar un posible deudor principal',
            deudor is not None,
        )
    )

    if estudiante:
        requisitos.append(
            RequisitoDocumental(
                'STUDENT_IDENTIFICATION',
                'Identificacion del estudiante aceptada',
                _documento_cumple(
                    solicitud,
                    TipoDocumentoFinanciacion.STUDENT_IDENTIFICATION,
                    estudiante,
                )
                or _documento_cumple(
                    solicitud,
                    TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
                    estudiante,
                ),
            )
        )
    if requiere_tutor and tutor:
        requisitos.append(
            RequisitoDocumental(
                'GUARDIAN_IDENTIFICATION',
                'Identificacion del tutor aceptada',
                _documento_cumple(
                    solicitud,
                    TipoDocumentoFinanciacion.GUARDIAN_IDENTIFICATION,
                    tutor,
                )
                or _documento_cumple(
                    solicitud,
                    TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
                    tutor,
                ),
            )
        )
    if deudor and deudor not in {estudiante, tutor}:
        requisitos.append(
            RequisitoDocumental(
                'DEBTOR_IDENTIFICATION',
                'Identificacion del posible deudor aceptada',
                _documento_cumple(
                    solicitud,
                    TipoDocumentoFinanciacion.DEBTOR_IDENTIFICATION,
                    deudor,
                ),
            )
        )

    try:
        evidencia = solicitud.evidencia_matricula
    except EvidenciaMatricula.DoesNotExist:
        evidencia = None
    evidencia_aceptada = bool(
        evidencia
        and evidencia.estado == EstadoEvidenciaMatricula.ACCEPTED
        and evidencia.documento_soporte.activo
        and evidencia.documento_soporte.estado_escaneo
        == EstadoEscaneoDocumento.SAFE
        and evidencia.documento_soporte.estado_validacion
        == EstadoValidacionDocumento.APPROVED
    )
    requisitos.append(
        RequisitoDocumental(
            'ENROLLMENT_EVIDENCE',
            'Evidencia de matricula aceptada',
            evidencia_aceptada,
        )
    )
    return requisitos


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
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_DOCUMENT:
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
