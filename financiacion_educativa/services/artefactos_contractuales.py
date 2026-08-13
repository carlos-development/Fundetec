import hashlib
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import connection, transaction
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
    construir_datos_ficha_matricula,
    construir_mapeo_ficha_matricula,
)
from financiacion_educativa.services.formato_contractual import (
    formatear_cop,
    numero_cop_a_letras,
)


VERSION_PLANTILLA_FICHA = 'FO-AD-005-V2-EDU-1'


@dataclass(frozen=True)
class ArtefactosContractualesGenerados:
    pagare: ArtefactoContractualEducativo
    ficha_matricula: ArtefactoContractualEducativo


@dataclass(frozen=True)
class ArtefactoContractualPreparado:
    tipo: str
    numero_version: int
    numero_documento: str
    version_plantilla: str
    pdf: bytes


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
    datos_acreedor = {
        'acreedor_razon_social': str(
            settings.FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL
        ).strip(),
        'acreedor_nit': str(
            settings.FINANCIACION_EDUCATIVA_ACREEDOR_NIT
        ).strip(),
        'acreedor_representante': str(
            settings.FINANCIACION_EDUCATIVA_ACREEDOR_REPRESENTANTE_LEGAL
        ).strip(),
        'acreedor_domicilio': str(
            settings.FINANCIACION_EDUCATIVA_ACREEDOR_DOMICILIO
        ).strip(),
    }
    if not all(datos_acreedor.values()):
        raise ValidationError(
            'Configura todos los datos legales del acreedor educativo antes de generar.'
        )
    textos_juridicos = {
        'pagare_version_juridica': str(
            settings.FINANCIACION_EDUCATIVA_PAGARE_VERSION_JURIDICA
        ).strip(),
        'pagare_clausula_obligacion': str(
            settings.FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION
        ).strip(),
        'pagare_clausula_carta_instrucciones': str(
            settings.FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_CARTA_INSTRUCCIONES
        ).strip(),
        'pagare_clausula_incumplimiento': str(
            settings.FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_INCUMPLIMIENTO
        ).strip(),
    }
    if not all(textos_juridicos.values()):
        raise ValidationError(
            'Configura la version y las clausulas juridicas educativas aprobadas.'
        )
    if len(textos_juridicos['pagare_version_juridica']) > 18:
        raise ValidationError('La version juridica del pagare es demasiado larga.')
    responsable = _responsable_contractual(solicitud)
    intereses = fotografia.total_estimado - fotografia.capital_financiado
    otros_conceptos = fotografia.capital_financiado - fotografia.valor_financiado
    return {
        'solicitud': solicitud,
        'fotografia': fotografia,
        **datos_acreedor,
        **textos_juridicos,
        'responsable': responsable,
        'estudiante': _estudiante(solicitud),
        'generado_en': timezone.localtime(),
        'modo_educativo': True,
        'numero_pagare': '',
        'deudor_nombres': responsable.nombre_completo,
        'tipo_documento_deudor': responsable.get_tipo_documento_display(),
        'cedula_deudor': responsable.numero_documento,
        'direccion_deudor': solicitud.direccion,
        'ciudad_domicilio_visible': datos_acreedor['acreedor_domicilio'],
        'telefono_deudor': responsable.telefono or solicitud.celular,
        'email_deudor': responsable.correo or solicitud.correo,
        'beneficiario_razon_social': datos_acreedor['acreedor_razon_social'],
        'beneficiario_nit': datos_acreedor['acreedor_nit'],
        'beneficiario_representante_legal': datos_acreedor['acreedor_representante'],
        'beneficiario_domicilio': datos_acreedor['acreedor_domicilio'],
        'monto_numeros': formatear_cop(fotografia.capital_financiado),
        'monto_letras': numero_cop_a_letras(fotografia.capital_financiado),
        'tasa_interes': format(fotografia.tasa_interes_mensual, 'f'),
        'plazo_cuotas': fotografia.plazo_meses,
        'periodicidad': 'mensuales',
        'valor_cuota': formatear_cop(fotografia.valor_cuota_estimada),
        'fecha_primer_pago': fotografia.fecha_primer_vencimiento,
        'fecha_ultimo_pago': fotografia.fecha_ultimo_vencimiento,
        'modalidad_descuento': 'Plan de pagos de financiacion educativa',
        'capital_valor': formatear_cop(fotografia.capital_financiado),
        'intereses_valor': formatear_cop(intereses),
        'otros_conceptos_valor': formatear_cop(otros_conceptos),
        'ciudad_firma_visible': datos_acreedor['acreedor_domicilio'],
        'fecha_firma_texto': '',
        'membrete_url': (
            Path(settings.BASE_DIR) / 'static' / 'images' / 'membrete_aprobado.jpg'
        ).resolve().as_uri(),
        'fundetec_logo_url': (
            Path(settings.BASE_DIR) / 'static' / 'images' / 'fundetec-logo.png'
        ).resolve().as_uri(),
    }


def _renderizar_pdf(nombre_plantilla, contexto):
    if any(
        not getattr(bloque, '_from_testcase', False)
        for bloque in connection.atomic_blocks
    ):
        raise RuntimeError(
            'El PDF contractual debe renderizarse fuera de una transaccion.'
        )
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


def _preparar_artefacto(
    *,
    solicitud,
    tipo,
    version_plantilla,
    plantilla,
    contexto,
):
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
        {
            **contexto,
            'numero_documento': numero,
            'numero_pagare': numero,
        },
    )
    return ArtefactoContractualPreparado(
        tipo=tipo,
        numero_version=version,
        numero_documento=numero,
        version_plantilla=version_plantilla,
        pdf=pdf,
    )


def _persistir_artefacto(
    *,
    solicitud,
    fotografia,
    preparado,
    actor,
    archivos_creados,
):
    existente = ArtefactoContractualEducativo.objects.filter(
        solicitud=solicitud,
        tipo=preparado.tipo,
        vigente=True,
    ).first()
    if existente:
        if existente.fotografia_financiera_id != fotografia.pk:
            raise ValidationError(
                'Existe un artefacto vigente asociado a otras condiciones.'
            )
        return existente

    artefacto = ArtefactoContractualEducativo(
        solicitud=solicitud,
        fotografia_financiera=fotografia,
        tipo=preparado.tipo,
        numero_version=preparado.numero_version,
        numero_documento=preparado.numero_documento,
        version_plantilla=preparado.version_plantilla,
        hash_sha256=hashlib.sha256(preparado.pdf).hexdigest(),
        tamano_bytes=len(preparado.pdf),
        generado_por=actor,
    )
    artefacto.archivo.save(
        f'{preparado.numero_documento}.pdf',
        ContentFile(preparado.pdf),
        save=False,
    )
    archivos_creados.append(artefacto.archivo.name)
    artefacto.full_clean()
    artefacto.save()
    return artefacto


def generar_artefactos_contractuales(*, solicitud, actor=None):
    solicitud = SolicitudFinanciacionEducativa.objects.get(pk=solicitud.pk)
    if solicitud.estado != EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE:
        raise ValidationError(
            'Los documentos contractuales solo se generan despues de la aprobacion.'
        )
    fotografia = solicitud.fotografias_financieras.filter(
        activa=True,
        bloqueada=True,
        es_legado=False,
    ).first()
    if not fotografia:
        raise ValidationError(
            'No existen condiciones financieras definitivas y bloqueadas.'
        )
    existentes = {
        artefacto.tipo: artefacto
        for artefacto in ArtefactoContractualEducativo.objects.filter(
            solicitud=solicitud,
            vigente=True,
            tipo__in={
                TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
                TipoArtefactoContractualEducativo.ENROLLMENT_FORM,
            },
        )
    }
    pagare_existente = existentes.get(
        TipoArtefactoContractualEducativo.PROMISSORY_NOTE
    )
    ficha_existente = existentes.get(
        TipoArtefactoContractualEducativo.ENROLLMENT_FORM
    )
    if pagare_existente and ficha_existente:
        if any(
            artefacto.fotografia_financiera_id != fotografia.pk
            for artefacto in (pagare_existente, ficha_existente)
        ):
            raise ValidationError(
                'Existe un artefacto vigente asociado a otras condiciones.'
            )
        from financiacion_educativa.services.firma_zapsign import (
            preparar_proceso_firma,
        )

        preparar_proceso_firma(artefacto=pagare_existente)
        return ArtefactosContractualesGenerados(
            pagare=pagare_existente,
            ficha_matricula=ficha_existente,
        )
    contexto = _contexto_base(solicitud, fotografia)
    preparados = {
        TipoArtefactoContractualEducativo.PROMISSORY_NOTE: _preparar_artefacto(
            solicitud=solicitud,
            tipo=TipoArtefactoContractualEducativo.PROMISSORY_NOTE,
            version_plantilla=(
                f'PAGARE-2.0-EDU-{contexto["pagare_version_juridica"]}'
            ),
            plantilla='pagares/pagare_v2.0.html',
            contexto=contexto,
        ),
        TipoArtefactoContractualEducativo.ENROLLMENT_FORM: _preparar_artefacto(
            solicitud=solicitud,
            tipo=TipoArtefactoContractualEducativo.ENROLLMENT_FORM,
            version_plantilla=VERSION_PLANTILLA_FICHA,
            plantilla='financiacion_educativa/documentos/ficha_matricula_pdf.html',
            contexto={
                **contexto,
                'mapeo_ficha': construir_mapeo_ficha_matricula(solicitud),
                'ficha': construir_datos_ficha_matricula(solicitud),
            },
        ),
    }
    archivos_creados = []
    try:
        with transaction.atomic():
            solicitud = (
                SolicitudFinanciacionEducativa.objects.select_for_update().get(
                    pk=solicitud.pk
                )
            )
            if (
                solicitud.estado
                != EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE
            ):
                raise ValidationError(
                    'Los documentos contractuales solo se generan despues de la aprobacion.'
                )
            fotografia = solicitud.fotografias_financieras.select_for_update().get(
                pk=fotografia.pk,
                activa=True,
                bloqueada=True,
                es_legado=False,
            )
            pagare = _persistir_artefacto(
                solicitud=solicitud,
                fotografia=fotografia,
                preparado=preparados[
                    TipoArtefactoContractualEducativo.PROMISSORY_NOTE
                ],
                actor=actor,
                archivos_creados=archivos_creados,
            )
            ficha = _persistir_artefacto(
                solicitud=solicitud,
                fotografia=fotografia,
                preparado=preparados[
                    TipoArtefactoContractualEducativo.ENROLLMENT_FORM
                ],
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
