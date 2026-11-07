from dataclasses import dataclass
from io import BytesIO

from django.core.exceptions import ValidationError
from pypdf import PdfReader, PdfWriter

from financiacion_educativa.models import ConfiguracionAnexoMatriculaEducativa
from financiacion_educativa.choices import RolParticipante


class AnexoMatriculaError(ValidationError):
    def __init__(self, mensaje, codigo='ENROLLMENT_ANNEX_NOT_READY'):
        self.codigo = codigo
        super().__init__(mensaje, code=codigo)


@dataclass(frozen=True)
class PerfilAnexo:
    codigo: str
    version: str
    plantilla: str
    paginas: int


PERFILES = {
    'FUNDETEC_FO_AD_005': PerfilAnexo('FUNDETEC_FO_AD_005', 'FO-AD-005-V2-EDU-2',
        'financiacion_educativa/documentos/ficha_matricula_pdf.html', 1),
    'JOWALTH_ACADEMY': PerfilAnexo('JOWALTH_ACADEMY', 'JOW-MAT-1.0',
        'financiacion_educativa/documentos/anexo_matricula_jowalth_v1.html', 4),
}


def resolver_anexo(solicitud, *, bloquear=False):
    qs = ConfiguracionAnexoMatriculaEducativa.objects
    if bloquear:
        qs = qs.select_for_update()
    config = qs.filter(institucion_id=solicitud.institucion_id, activa=True).first()
    perfil = PERFILES.get(config.perfil) if config else None
    if not perfil:
        raise AnexoMatriculaError('Configura y activa el anexo de matricula de esta institucion en Admin. No se permite una ficha alternativa.')
    return perfil


def snapshot_anexo(solicitud, perfil):
    return {'institution_id': str(solicitud.institucion_id), 'profile': perfil.codigo, 'version': perfil.version}


def validar_anexo_artefacto(artefacto, *, bloquear=False):
    perfil = resolver_anexo(artefacto.solicitud, bloquear=bloquear)
    esperado = snapshot_anexo(artefacto.solicitud, perfil)
    if artefacto.anexo_matricula_snapshot == esperado:
        return perfil
    # Explicit legacy compatibility only for a configured Fundetec institution.
    # Never reinterpret or rewrite historical metadata.
    if (not artefacto.anexo_matricula_snapshot and perfil.codigo == 'FUNDETEC_FO_AD_005'
            and (artefacto.version_plantilla.startswith('PAQUETE-EDU-3.0-')
                 or artefacto.version_plantilla in {
                     perfil.version, 'PAGARE-2.0-EDU-1', 'FO-AD-005-V2-EDU-1'})):
        return perfil
    raise AnexoMatriculaError('El artefacto no corresponde al anexo institucional vigente. Requiere revision de Operaciones; no se regenerara automaticamente.')


def datos_jowalth(solicitud):
    roles = {}
    for asignacion in solicitud.roles_participantes.select_related('participante'):
        roles[asignacion.rol] = asignacion.participante
    estudiante = roles.get(RolParticipante.STUDENT)
    tutor = roles.get(RolParticipante.GUARDIAN)
    return {
        'nombres': estudiante.nombres if estudiante else solicitud.nombres,
        'apellidos': estudiante.apellidos if estudiante else solicitud.apellidos,
        'tipo_documento': estudiante.get_tipo_documento_display() if estudiante else solicitud.get_tipo_documento_estudiante_display(),
        'numero_documento': estudiante.numero_documento if estudiante else solicitud.numero_documento_estudiante,
        'fecha_nacimiento': estudiante.fecha_nacimiento if estudiante else solicitud.fecha_nacimiento_estudiante,
        'codigo_matricula': solicitud.codigo_matricula,
        'fecha_matricula': solicitud.fecha_matricula,
        'sede': solicitud.sede, 'periodo': solicitud.periodo_academico,
        'direccion': solicitud.direccion, 'celular': solicitud.celular, 'correo': solicitud.correo,
        'tutor': tutor, 'curso': solicitud.nombre_curso, 'jornada': solicitud.jornada,
        'valor_plan': solicitud.valor_plan,
    }


def validar_pdf_anexo(pdf, perfil):
    try:
        paginas = len(PdfReader(BytesIO(pdf), strict=True).pages)
        if paginas != perfil.paginas:
            raise ValueError('Unexpected page count')
    except Exception as error:
        raise AnexoMatriculaError('No fue posible generar el anexo institucional completo.',
                                 'ENROLLMENT_ANNEX_GENERATION_FAILED') from error


def unir_paquete_y_anexo(cuerpo, anexo):
    escritor = PdfWriter()
    for contenido in (cuerpo, anexo):
        lector = PdfReader(BytesIO(contenido), strict=True)
        for pagina in lector.pages:
            escritor.add_page(pagina)
    salida = BytesIO()
    escritor.write(salida)
    return salida.getvalue()
