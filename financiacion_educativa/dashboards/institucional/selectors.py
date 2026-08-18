from decimal import Decimal

from django.db.models import Count, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404

from financiacion_educativa.choices import (
    EstadoPublicoSolicitud,
    EstadoSolicitudFinanciacion,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    CondicionesFinancieras,
    DocumentoFinanciacion,
    HistorialEstadoSolicitud,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.estado_publico import MAPA_ESTADO_PUBLICO


ESTADOS_INTERNOS_POR_PUBLICO = {
    estado_publico: tuple(
        estado_interno
        for estado_interno, publico in MAPA_ESTADO_PUBLICO.items()
        if publico == estado_publico
    )
    for estado_publico in EstadoPublicoSolicitud.values
}

ESTADOS_SEGUIMIENTO = tuple(
    estado
    for estado in EstadoSolicitudFinanciacion.values
    if estado
    not in {
        EstadoSolicitudFinanciacion.APPROVED,
        EstadoSolicitudFinanciacion.REJECTED,
        EstadoSolicitudFinanciacion.CANCELLED,
        EstadoSolicitudFinanciacion.PAID,
    }
)

ORDENAMIENTOS_PERMITIDOS = {
    '-creada_en',
    'creada_en',
    '-actualizada_en',
    'referencia_externa',
    'nombre_curso',
    '-valor_plan',
    'valor_plan',
}


def solicitudes_de_institucion(*, institucion):
    return SolicitudFinanciacionEducativa.objects.filter(
        institucion=institucion
    )


def obtener_indicadores_institucionales(*, institucion):
    solicitudes = solicitudes_de_institucion(institucion=institucion)
    indicadores = solicitudes.aggregate(
        total=Count('id'),
        recibidas=Count(
            'id',
            filter=Q(
                estado=EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION
            ),
        ),
        en_proceso=Count(
            'id',
            filter=Q(
                estado__in=(
                    EstadoSolicitudFinanciacion.PENDING_TERMS,
                    EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
                    EstadoSolicitudFinanciacion.PENDING_GUARDIAN,
                    EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
                    EstadoSolicitudFinanciacion.ACTIVE,
                    EstadoSolicitudFinanciacion.PAYMENT_REPORTED,
                    EstadoSolicitudFinanciacion.PAYMENT_UNDER_REVIEW,
                )
            ),
        ),
        correccion=Count(
            'id',
            filter=Q(estado=EstadoSolicitudFinanciacion.CORRECTION_REQUIRED),
        ),
        revision_manual=Count(
            'id',
            filter=Q(
                estado=EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
            ),
        ),
        firma=Count(
            'id',
            filter=Q(estado=EstadoSolicitudFinanciacion.PENDING_SIGNATURE),
        ),
        aprobadas=Count(
            'id',
            filter=Q(estado=EstadoSolicitudFinanciacion.APPROVED),
        ),
        rechazadas_cerradas=Count(
            'id',
            filter=Q(
                estado__in=(
                    EstadoSolicitudFinanciacion.REJECTED,
                    EstadoSolicitudFinanciacion.CANCELLED,
                    EstadoSolicitudFinanciacion.PAID,
                )
            ),
        ),
        valor_solicitado=Sum('valor_plan'),
    )
    indicadores['valor_financiado'] = (
        CondicionesFinancieras.objects.filter(
            solicitud__institucion=institucion,
            activa=True,
        ).aggregate(total=Sum('capital_financiado'))['total']
        or Decimal('0')
    )
    indicadores['valor_solicitado'] = (
        indicadores['valor_solicitado'] or Decimal('0')
    )
    return indicadores


def obtener_solicitudes_recientes(*, institucion, limite=5):
    return solicitudes_de_institucion(institucion=institucion).only(
        'id',
        'referencia_externa',
        'nombres',
        'apellidos',
        'nombre_curso',
        'periodo_academico',
        'sede',
        'valor_plan',
        'plazo_meses',
        'estado',
        'creada_en',
        'actualizada_en',
    )[:limite]


def obtener_opciones_filtros(*, institucion):
    base = solicitudes_de_institucion(institucion=institucion)

    def valores(campo):
        return tuple(
            base.exclude(**{campo: ''})
            .order_by(campo)
            .values_list(campo, flat=True)
            .distinct()
        )

    return {
        'programas': valores('nombre_curso'),
        'periodos': valores('periodo_academico'),
        'sedes': valores('sede'),
    }


def filtrar_solicitudes_institucionales(
    *, institucion, filtros, solo_seguimiento=False
):
    consulta = solicitudes_de_institucion(institucion=institucion)
    if solo_seguimiento:
        consulta = consulta.filter(estado__in=ESTADOS_SEGUIMIENTO)

    busqueda = filtros.get('q')
    if busqueda:
        consulta = consulta.filter(
            Q(referencia_externa__icontains=busqueda)
            | Q(nombres__icontains=busqueda)
            | Q(apellidos__icontains=busqueda)
            | Q(correo__icontains=busqueda)
        )
    estado_publico = filtros.get('estado')
    if estado_publico:
        consulta = consulta.filter(
            estado__in=ESTADOS_INTERNOS_POR_PUBLICO[estado_publico]
        )
    for parametro, campo in (
        ('programa', 'nombre_curso'),
        ('periodo', 'periodo_academico'),
        ('sede', 'sede'),
    ):
        if filtros.get(parametro):
            consulta = consulta.filter(**{campo: filtros[parametro]})
    if filtros.get('desde'):
        consulta = consulta.filter(creada_en__date__gte=filtros['desde'])
    if filtros.get('hasta'):
        consulta = consulta.filter(creada_en__date__lte=filtros['hasta'])

    orden = filtros.get('orden') or '-creada_en'
    if orden not in ORDENAMIENTOS_PERMITIDOS:
        orden = '-creada_en'
    return consulta.only(
        'id',
        'referencia_externa',
        'nombres',
        'apellidos',
        'nombre_curso',
        'periodo_academico',
        'sede',
        'valor_plan',
        'plazo_meses',
        'estado',
        'creada_en',
        'actualizada_en',
    ).order_by(orden, 'id')


def obtener_solicitud_institucional(*, institucion, application_id):
    documentos = DocumentoFinanciacion.objects.filter(activo=True).only(
        'id',
        'solicitud_id',
        'tipo',
        'estado_escaneo',
        'estado_validacion',
    ).order_by('tipo', 'id')
    historial = HistorialEstadoSolicitud.objects.only(
        'id',
        'solicitud_id',
        'estado_nuevo',
        'creado_en',
    ).order_by('creado_en', 'id')
    fotografias = CondicionesFinancieras.objects.filter(activa=True).only(
        'id',
        'solicitud_id',
        'moneda',
        'valor_financiado',
        'capital_financiado',
        'plazo_meses',
        'valor_cuota_estimada',
        'total_estimado',
    )
    artefactos = ArtefactoContractualEducativo.objects.select_related(
        'proceso_firma',
        'fotografia_financiera',
    ).only(
        'id',
        'solicitud_id',
        'tipo',
        'vigente',
        'estado',
        'fotografia_financiera__id',
        'fotografia_financiera__activa',
        'fotografia_financiera__bloqueada',
        'fotografia_financiera__es_legado',
        'proceso_firma__id',
        'proceso_firma__estado',
        'proceso_firma__enviado_en',
        'proceso_firma__firmado_en',
        'proceso_firma__rechazado_en',
    ).order_by('-numero_version')
    consulta = solicitudes_de_institucion(institucion=institucion).prefetch_related(
        Prefetch('documentos', queryset=documentos, to_attr='documentos_dashboard'),
        Prefetch(
            'historial_estados',
            queryset=historial,
            to_attr='historial_dashboard',
        ),
        Prefetch(
            'fotografias_financieras',
            queryset=fotografias,
            to_attr='fotografias_dashboard',
        ),
        Prefetch(
            'artefactos_contractuales',
            queryset=artefactos,
            to_attr='artefactos_dashboard',
        ),
    )
    return get_object_or_404(consulta, pk=application_id)
