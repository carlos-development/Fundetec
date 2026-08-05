import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max
from django.template.loader import render_to_string
from django.utils import timezone

from financiacion_educativa.choices import (
    EstadoSolicitudFinanciacion,
    RolParticipante,
    TipoArtefactoContractualEducativo,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.ficha_matricula import (
    construir_mapeo_ficha_matricula,
)


VERSION_PLANTILLA_PAGARE = 'EDU-PAGARE-1.0'
VERSION_PLANTILLA_FICHA = 'EDU-FICHA-1.0'


@dataclass(frozen=True)
class ArtefactosContractualesGenerados:
    pagare: ArtefactoContractualEducativo
    ficha_matricula: ArtefactoContractualEducativo


def _responsable_contractual(solicitud):
    responsables = list(
        solicitud.participantes.filter(
            responsable_contractual=True,
            roles__rol=RolParticipante.PRINCIPAL_DEBTOR,
        ).distinct()[:2]
    )
    if len(responsables) != 1:
        raise ValidationError(
            'La solicitud debe tener un unico responsable contractual.'
        )
    return responsables[0]


def _estudiante(solicitud):
    asignacion = solicitud.roles_participantes.select_related(
        'participante'
    ).filter(rol=RolParticipante.STUDENT).first()
    if not asignacion:
        raise ValidationError('La solicitud no tiene un estudiante identificado.')
    return asignacion.participante


def _contexto_base(solicitud, fotografia):
    acreedor = str(
        getattr(
            settings,
            'FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL',
            '',
        )
    ).strip()
    if not acreedor:
        raise ValidationError(
            'Configura la razon social del acreedor educativo antes de generar.'
        )
    return {
        'solicitud': solicitud,
        'fotografia': fotografia,
        'acreedor_razon_social': acreedor,
        'responsable': _responsable_contractual(solicitud),
        'estudiante': _estudiante(solicitud),
        'generado_en': timezone.localtime(),
    }


def _renderizar_pdf(nombre_plantilla, contexto):
    from weasyprint import HTML

    html = render_to_string(nombre_plantilla, contexto)
    pdf = HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
    if not pdf or not pdf.startswith(b'%PDF'):
        raise ValidationError('No fue posible generar un PDF contractual valido.')
    return pdf


def _numero_documento(solicitud, tipo, version):
    prefijo = (
        'PE'
        if tipo == TipoArtefactoContractualEducativo.PROMISSORY_NOTE
        else 'FM'
    )
    return f'{prefijo}-{solicitud.pk.hex[:12].upper()}-V{version}'


def _crear_artefacto(
    *,
    solicitud,
    fotografia,
    tipo,
    version_plantilla,
    plantilla,
    contexto,
    actor,
    archivos_creados,
):
    existente = ArtefactoContractualEducativo.objects.filter(
        solicitud=solicitud,
        tipo=tipo,
        vigente=True,
    ).first()
    if existente:
        if existente.fotografia_financiera_id != fotografia.pk:
            raise ValidationError(
                'Existe un artefacto vigente asociado a otras condiciones.'
            )
        return existente

    version = (
        ArtefactoContractualEducativo.objects.filter(
            solicitud=solicitud,
            tipo=tipo,
        ).aggregate(maxima=Max('numero_version'))['maxima']
        or 0
    ) + 1
    numero = _numero_documento(solicitud, tipo, version)
    pdf = _renderizar_pdf(
        plantilla,
        {**contexto, 'numero_documento': numero},
    )
    artefacto = ArtefactoContractualEducativo(
        solicitud=solicitud,
        fotografia_financiera=fotografia,
        tipo=tipo,
        numero_version=version,
        numero_documento=numero,
        version_plantilla=version_plantilla,
        hash_sha256=hashlib.sha256(pdf).hexdigest(),
        tamano_bytes=len(pdf),
        generado_por=actor,
    )
    artefacto.archivo.save(
        f'{numero}.pdf',
        ContentFile(pdf),
        save=False,
    )
    archivos_creados.append(artefacto.archivo.name)
    artefacto.full_clean()
    artefacto.save()
    return artefacto


@transaction.atomic
def generar_artefactos_contractuales(*, solicitud, actor=None):
    solicitud = SolicitudFinanciacionEducativa.objects.select_for_update().get(
        pk=solicitud.pk
    )
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE:
        raise ValidationError(
            'Los documentos contractuales solo se generan despues de la aprobacion.'
        )
    fotografia = solicitud.fotografias_financieras.select_for_update().filter(
        activa=True,
        bloqueada=True,
        es_legado=False,
    ).first()
    if not fotografia:
        raise ValidationError(
            'No existen condiciones financieras definitivas y bloqueadas.'
        )
    contexto = _contexto_base(solicitud, fotografia)
    archivos_creados = []
    try:
        pagare = _crear_artefacto(
            solicitud=solicitud,
            fotografia=fotografia,
            tipo=TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
            version_plantilla=VERSION_PLANTILLA_PAGARE,
            plantilla='financiacion_educativa/documentos/pagare_educativo.html',
            contexto=contexto,
            actor=actor,
            archivos_creados=archivos_creados,
        )
        ficha = _crear_artefacto(
            solicitud=solicitud,
            fotografia=fotografia,
            tipo=TipoArtefactoContractualEducativo.ENROLLMENT_FORM,
            version_plantilla=VERSION_PLANTILLA_FICHA,
            plantilla='financiacion_educativa/documentos/ficha_matricula_pdf.html',
            contexto={
                **contexto,
                'mapeo_ficha': construir_mapeo_ficha_matricula(solicitud),
            },
            actor=actor,
            archivos_creados=archivos_creados,
        )
        from financiacion_educativa.services.firma_zapsign import (
            preparar_proceso_firma,
        )

        preparar_proceso_firma(artefacto=pagare)
    except Exception:
        almacenamiento = ArtefactoContractualEducativo._meta.get_field(
            'archivo'
        ).storage
        for nombre in archivos_creados:
            if almacenamiento.exists(nombre):
                almacenamiento.delete(nombre)
        raise
    return ArtefactosContractualesGenerados(
        pagare=pagare,
        ficha_matricula=ficha,
    )
