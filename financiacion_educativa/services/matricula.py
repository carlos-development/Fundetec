import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoEvidenciaMatricula,
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    MotivoRechazoDocumento,
    OrigenCapturaDocumento,
    TipoDocumentoFinanciacion,
)
from financiacion_educativa.models import (
    EvidenciaMatricula,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.documentos import (
    registrar_documento,
    reemplazar_documento,
)


def _texto(valor, limite):
    return re.sub(r'[\r\n\t]+', ' ', (valor or '').strip())[:limite]


def _validar_propiedad(solicitud, actor):
    if not actor or not actor.is_authenticated:
        raise ValidationError('Debes iniciar sesion.')
    if not actor.is_staff and solicitud.usuario_id != actor.pk:
        raise ValidationError('La solicitud no esta disponible.')


@transaction.atomic
def registrar_o_actualizar_evidencia_matricula(
    *,
    solicitud,
    actor,
    institucion_declarada,
    programa_curso,
    periodo_academico,
    referencia_matricula='',
    archivo=None,
):
    _validar_propiedad(solicitud, actor)
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_DOCUMENT:
        raise ValidationError('La solicitud no admite cambios documentales.')

    valores = {
        'institucion_declarada': _texto(institucion_declarada, 200),
        'programa_curso': _texto(programa_curso, 200),
        'periodo_academico': _texto(periodo_academico, 80),
        'referencia_matricula': _texto(referencia_matricula, 120),
    }
    if not all(
        valores[campo]
        for campo in ('institucion_declarada', 'programa_curso', 'periodo_academico')
    ):
        raise ValidationError('Completa los datos obligatorios de matricula.')

    evidencia = EvidenciaMatricula.objects.select_for_update().filter(
        solicitud=solicitud
    ).first()
    if evidencia and archivo:
        documento = reemplazar_documento(
            documento=evidencia.documento_soporte,
            archivo=archivo,
            actor=actor,
        )
    elif evidencia:
        documento = evidencia.documento_soporte
    elif archivo:
        documento = registrar_documento(
            solicitud=solicitud,
            tipo=TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE,
            origen_captura=OrigenCapturaDocumento.USER_UPLOAD,
            archivo=archivo,
            actor=actor,
        )
    else:
        raise ValidationError({'archivo': 'Adjunta la evidencia de matricula.'})

    if evidencia:
        cambios = any(getattr(evidencia, campo) != valor for campo, valor in valores.items())
        cambios = cambios or evidencia.documento_soporte_id != documento.pk
        for campo, valor in valores.items():
            setattr(evidencia, campo, valor)
        evidencia.documento_soporte = documento
        if cambios:
            evidencia.estado = EstadoEvidenciaMatricula.PENDING
            evidencia.revisado_por = None
            evidencia.revisado_en = None
            evidencia.motivo_rechazo = ''
            evidencia.observacion_revision = ''
        evidencia.full_clean()
        evidencia.save()
    else:
        evidencia = EvidenciaMatricula(
            solicitud=solicitud,
            documento_soporte=documento,
            registrado_por=actor,
            **valores,
        )
        evidencia.full_clean()
        evidencia.save()
    return evidencia


@transaction.atomic
def revisar_evidencia_matricula(
    *,
    evidencia,
    actor,
    aceptar,
    motivo_rechazo='',
    observacion='',
):
    if not actor or not actor.is_authenticated or not actor.is_staff:
        raise ValidationError('La revision requiere un usuario administrativo.')
    evidencia = EvidenciaMatricula.objects.select_for_update().select_related(
        'documento_soporte'
    ).get(pk=evidencia.pk)
    destino = (
        EstadoEvidenciaMatricula.ACCEPTED
        if aceptar
        else EstadoEvidenciaMatricula.REJECTED
    )
    if evidencia.estado == destino:
        return evidencia
    if evidencia.estado == EstadoEvidenciaMatricula.ACCEPTED:
        raise ValidationError('Una evidencia aceptada debe reemplazarse para cambiar.')
    if (
        aceptar
        and evidencia.documento_soporte.estado_validacion
        != EstadoValidacionDocumento.APPROVED
    ):
        raise ValidationError('El documento de matricula aun no ha sido aceptado.')
    if not aceptar and motivo_rechazo not in MotivoRechazoDocumento.values:
        raise ValidationError({'motivo_rechazo': 'Selecciona un motivo valido.'})

    evidencia.estado = destino
    evidencia.revisado_por = actor
    evidencia.revisado_en = timezone.now()
    evidencia.motivo_rechazo = '' if aceptar else motivo_rechazo
    evidencia.observacion_revision = _texto(observacion, 500)
    evidencia.full_clean()
    evidencia.save()
    return evidencia
