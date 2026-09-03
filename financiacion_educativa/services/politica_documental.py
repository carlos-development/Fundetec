from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError

from financiacion_educativa.choices import (
    EstadoEscaneoDocumento,
    EstadoEvidenciaMatricula,
    EstadoValidacionDocumento,
    RolParticipante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import EvidenciaMatricula
from financiacion_educativa.services.participantes import solicitud_requiere_tutor


CARAS_IDENTIFICACION_POR_TIPO = {
    TipoDocumentoIdentidad.CC: ('frente', 'reverso'),
    TipoDocumentoIdentidad.TI: ('frente', 'reverso'),
    TipoDocumentoIdentidad.PASSPORT: ('frente',),
    TipoDocumentoIdentidad.CE: ('frente',),
    TipoDocumentoIdentidad.RC: ('frente',),
    TipoDocumentoIdentidad.OTHER: ('frente',),
}

TIPOS_DOCUMENTO_CON_VALIDACION_VISUAL = frozenset({
    TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
    TipoDocumentoFinanciacion.STUDENT_ID_BACK,
    TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
    TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
    TipoDocumentoFinanciacion.DEBTOR_IDENTIFICATION,
    TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
})


@dataclass(frozen=True)
class RequisitoDocumentoPolitica:
    codigo: str
    descripcion: str
    tipo: str
    participante: object = None
    obligatorio: bool = True
    documento: object = None
    evidencia_matricula: object = None


def caras_identificacion_requeridas(tipo_documento):
    return CARAS_IDENTIFICACION_POR_TIPO.get(
        tipo_documento,
        ('frente',),
    )


def documento_requiere_validacion_visual(documento):
    if documento.tipo in TIPOS_DOCUMENTO_CON_VALIDACION_VISUAL:
        return True
    if documento.tipo == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE:
        return documento.content_type != 'application/pdf'
    return True


def _documento_activo(documentos, *, tipo, participante=None):
    participante_id = getattr(participante, 'pk', None)
    return next(
        (
            documento
            for documento in documentos
            if documento.tipo == tipo
            and documento.participante_id == participante_id
        ),
        None,
    )


def _requisitos_identidad(*, persona, prefijo, documentos):
    if not persona:
        return []
    tipos = {
        'STUDENT': {
            'frente': TipoDocumentoFinanciacion.STUDENT_ID_FRONT,
            'reverso': TipoDocumentoFinanciacion.STUDENT_ID_BACK,
        },
        'GUARDIAN': {
            'frente': TipoDocumentoFinanciacion.GUARDIAN_ID_FRONT,
            'reverso': TipoDocumentoFinanciacion.GUARDIAN_ID_BACK,
        },
    }[prefijo]
    titular = 'estudiante' if prefijo == 'STUDENT' else 'tutor'
    requisitos = []
    for cara in caras_identificacion_requeridas(persona.tipo_documento):
        tipo = tipos[cara]
        codigo_cara = 'FRONT' if cara == 'frente' else 'BACK'
        descripcion_cara = (
            'pagina biografica'
            if persona.tipo_documento == TipoDocumentoIdentidad.PASSPORT
            else ('frente' if cara == 'frente' else 'reverso')
        )
        requisitos.append(
            RequisitoDocumentoPolitica(
                codigo=f'{prefijo}_ID_{codigo_cara}',
                descripcion=(
                    f'Identificacion del {titular}: {descripcion_cara} '
                    'capturado por camara'
                ),
                tipo=tipo,
                participante=persona,
                documento=_documento_activo(
                    documentos,
                    tipo=tipo,
                    participante=persona,
                ),
            )
        )
    return requisitos


def construir_politica_documental(solicitud):
    roles = {
        asignacion.rol: asignacion.participante
        for asignacion in solicitud.roles_participantes.select_related(
            'participante'
        )
    }
    estudiante = roles.get(RolParticipante.STUDENT)
    tutor = roles.get(RolParticipante.GUARDIAN)
    deudor = roles.get(RolParticipante.PRINCIPAL_DEBTOR)
    documentos = list(
        solicitud.documentos.filter(activo=True).select_related('participante')
    )
    requisitos = _requisitos_identidad(
        persona=estudiante,
        prefijo='STUDENT',
        documentos=documentos,
    )
    if solicitud_requiere_tutor(solicitud):
        requisitos.extend(
            _requisitos_identidad(
                persona=tutor,
                prefijo='GUARDIAN',
                documentos=documentos,
            )
        )
    if deudor and deudor not in {estudiante, tutor}:
        requisitos.append(
            RequisitoDocumentoPolitica(
                codigo='DEBTOR_IDENTIFICATION',
                descripcion='Identificacion del responsable contractual aportada',
                tipo=TipoDocumentoFinanciacion.DEBTOR_IDENTIFICATION,
                participante=deudor,
                documento=_documento_activo(
                    documentos,
                    tipo=TipoDocumentoFinanciacion.DEBTOR_IDENTIFICATION,
                    participante=deudor,
                ),
            )
        )
    if deudor:
        requisitos.append(
            RequisitoDocumentoPolitica(
                codigo='INCOME_CERTIFICATE',
                descripcion=(
                    'Soporte de ingresos o certificacion bancaria del '
                    'responsable contractual aportado'
                ),
                tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
                participante=deudor,
                documento=_documento_activo(
                    documentos,
                    tipo=TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
                    participante=deudor,
                ),
            )
        )

    soporte = _documento_activo(
        documentos,
        tipo=TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE,
    )
    if soporte:
        evidencia = EvidenciaMatricula.objects.filter(
            solicitud=solicitud
        ).first()
        requisitos.append(
            RequisitoDocumentoPolitica(
                codigo='ENROLLMENT_EVIDENCE',
                descripcion='Soporte opcional de matricula aportado',
                tipo=TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE,
                obligatorio=False,
                documento=soporte,
                evidencia_matricula=evidencia,
            )
        )
    return requisitos


def requisito_listo_para_envio(requisito):
    if requisito.documento is None:
        return not requisito.obligatorio
    if requisito.tipo == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE:
        if (
            requisito.evidencia_matricula
            and requisito.evidencia_matricula.estado
            == EstadoEvidenciaMatricula.REJECTED
        ):
            return False
        if not settings.FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED:
            return requisito_listo_para_aprobacion(requisito)
    return (
        requisito.documento.estado_escaneo != EstadoEscaneoDocumento.BLOCKED
        and requisito.documento.estado_validacion
        != EstadoValidacionDocumento.REJECTED
    )


def requisito_listo_para_aprobacion(requisito):
    if requisito.documento is None:
        return not requisito.obligatorio
    documento_aprobado = (
        requisito.documento.estado_escaneo == EstadoEscaneoDocumento.SAFE
        and requisito.documento.estado_validacion
        == EstadoValidacionDocumento.APPROVED
    )
    if not documento_aprobado:
        return False
    if requisito.tipo == TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE:
        return bool(
            requisito.evidencia_matricula
            and requisito.evidencia_matricula.documento_soporte_id
            == requisito.documento.pk
            and requisito.evidencia_matricula.estado
            == EstadoEvidenciaMatricula.ACCEPTED
        )
    return True


def validar_expediente_para_aprobacion(solicitud):
    politica = construir_politica_documental(solicitud)
    faltantes = [
        requisito.codigo
        for requisito in politica
        if requisito.obligatorio and requisito.documento is None
    ]
    if faltantes:
        raise ValidationError(
            'El expediente no contiene todos los documentos obligatorios.'
        )
    no_resueltos = [
        requisito.codigo
        for requisito in politica
        if not requisito_listo_para_aprobacion(requisito)
    ]
    if no_resueltos:
        raise ValidationError(
            'Todos los documentos aportados deben estar seguros y aceptados.'
        )
    return politica
