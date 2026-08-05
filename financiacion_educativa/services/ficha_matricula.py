from dataclasses import dataclass

from financiacion_educativa.choices import RolParticipante
from financiacion_educativa.models import EvidenciaMatricula


@dataclass(frozen=True)
class CampoFichaMatricula:
    campo: str
    valor: str
    fuente: str
    transformacion: str
    disponible: bool
    faltante: str = ''


def _campo(campo, valor='', fuente='', transformacion='Sin transformacion', faltante=''):
    valor = str(valor or '').strip()
    return CampoFichaMatricula(
        campo=campo,
        valor=valor,
        fuente=fuente,
        transformacion=transformacion,
        disponible=bool(valor),
        faltante='' if valor else faltante,
    )


def _participante_por_rol(solicitud, rol):
    asignacion = (
        solicitud.roles_participantes.select_related('participante')
        .filter(rol=rol)
        .first()
    )
    return asignacion.participante if asignacion else None


def construir_mapeo_ficha_matricula(solicitud):
    estudiante = _participante_por_rol(solicitud, RolParticipante.STUDENT)
    tutor = _participante_por_rol(solicitud, RolParticipante.GUARDIAN)
    responsable = _participante_por_rol(
        solicitud,
        RolParticipante.PRINCIPAL_DEBTOR,
    )
    try:
        evidencia = solicitud.evidencia_matricula
    except EvidenciaMatricula.DoesNotExist:
        evidencia = None

    fecha_nacimiento = (
        estudiante.fecha_nacimiento.isoformat()
        if estudiante and estudiante.fecha_nacimiento
        else (
            solicitud.fecha_nacimiento_estudiante.isoformat()
            if solicitud.fecha_nacimiento_estudiante
            else ''
        )
    )
    identificacion_estudiante = (
        estudiante.numero_documento
        if estudiante
        else solicitud.numero_documento_estudiante
    )
    identificacion_tutor = tutor.numero_documento if tutor else ''

    mapeo = {
        'Datos personales': [
            _campo('Nombres', solicitud.nombres, 'Solicitud institucional'),
            _campo('Apellidos', solicitud.apellidos, 'Solicitud institucional'),
            _campo(
                'Identificacion',
                identificacion_estudiante,
                'Expediente del estudiante',
                'Tipo y numero sin separadores',
                'Identificacion del estudiante',
            ),
            _campo('Email', solicitud.correo, 'Solicitud institucional'),
            _campo('Celular', solicitud.celular, 'Solicitud institucional'),
            _campo(
                'Telefono',
                '',
                '',
                faltante='Telefono alterno del estudiante',
            ),
            _campo('Direccion', solicitud.direccion, 'Solicitud institucional'),
            _campo(
                'Fecha nacimiento',
                fecha_nacimiento,
                'Expediente del estudiante',
                'AAAA-MM-DD',
                'Fecha de nacimiento declarada',
            ),
            _campo(
                'Mun. nacimiento',
                '',
                '',
                faltante='Municipio de nacimiento',
            ),
            _campo(
                'Mun. expedicion',
                '',
                '',
                faltante='Municipio de expedicion del documento',
            ),
        ],
        'Matricula': [
            _campo('Programa', solicitud.nombre_curso, 'Solicitud institucional'),
            _campo(
                'Codigo matricula',
                solicitud.codigo_matricula
                or (evidencia.referencia_matricula if evidencia else ''),
                (
                    'Solicitud institucional'
                    if solicitud.codigo_matricula
                    else 'Datos de matricula declarados'
                ),
                faltante='Codigo o referencia oficial de matricula',
            ),
            _campo(
                'Sede-jornada',
                ' - '.join(
                    valor for valor in (solicitud.sede, solicitud.jornada) if valor
                ),
                'Solicitud institucional',
                faltante='Sede y jornada',
            ),
            _campo(
                'Fecha matricula',
                (
                    solicitud.fecha_matricula.isoformat()
                    if solicitud.fecha_matricula
                    else ''
                ),
                'Firma valida del pagare',
                faltante='Fecha oficial de matricula',
            ),
            _campo(
                'Periodo',
                solicitud.periodo_academico
                or (evidencia.periodo_academico if evidencia else ''),
                (
                    'Solicitud institucional'
                    if solicitud.periodo_academico
                    else 'Datos de matricula declarados'
                ),
                faltante='Periodo academico',
            ),
            _campo(
                'Fecha renovacion',
                '',
                '',
                faltante='Fecha oficial de renovacion',
            ),
        ],
    }
    if tutor:
        mapeo['Acudiente'] = [
            _campo(
                'Nombre completo',
                tutor.nombre_completo if tutor else '',
                'Expediente del tutor',
                faltante='Nombre del tutor o acudiente',
            ),
            _campo(
                'Identificacion',
                identificacion_tutor,
                'Expediente del tutor',
                faltante='Identificacion del tutor o acudiente',
            ),
            _campo(
                'Celular',
                tutor.telefono if tutor else '',
                'Expediente del tutor',
                faltante='Celular del tutor o acudiente',
            ),
            _campo('Telefono', '', '', faltante='Telefono alterno del acudiente'),
            _campo(
                'Parentesco',
                tutor.get_relacion_estudiante_display() if tutor else '',
                'Expediente del tutor',
                faltante='Relacion con el estudiante',
            ),
            _campo('Ocupacion', '', '', faltante='Ocupacion del acudiente'),
            _campo(
                'Email',
                tutor.correo if tutor else '',
                'Expediente del tutor',
                faltante='Correo del acudiente',
            ),
            _campo('Direccion', '', '', faltante='Direccion del acudiente'),
        ]
    mapeo.update({
        'Responsable contractual': [
            _campo(
                'Nombre completo',
                responsable.nombre_completo if responsable else '',
                'Expediente contractual',
                faltante='Responsable contractual',
            ),
            _campo(
                'Identificacion',
                responsable.numero_documento if responsable else '',
                'Expediente contractual',
                faltante='Identificacion del responsable',
            ),
            _campo(
                'Correo',
                responsable.correo if responsable else '',
                'Expediente contractual',
                faltante='Correo del responsable',
            ),
            _campo(
                'Celular',
                responsable.telefono if responsable else '',
                'Expediente contractual',
                faltante='Celular del responsable',
            ),
        ],
    })
    return mapeo
