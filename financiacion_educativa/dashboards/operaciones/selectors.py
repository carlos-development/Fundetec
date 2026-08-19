from datetime import timedelta
from decimal import Decimal

from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    Exists,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
    When,
    JSONField,
)
from django.shortcuts import get_object_or_404
from django.utils import timezone

from financiacion_educativa.choices import (
    CodigoRazonAutomatizacionEducativa,
    EstadoOutboxCorreoEducativo,
    EstadoProcesoAutomatizacionEducativa,
    EstadoProcesamientoContenidoDocumento,
    EstadoPublicoSolicitud,
    EstadoSolicitudFinanciacion,
    EstadoValidacionIADocumento,
    EstadoEscaneoDocumento,
    EstadoValidacionDocumento,
)
from financiacion_educativa.models import (
    ArtefactoContractualEducativo,
    CondicionesFinancieras,
    DecisionRevisionEducativa,
    DecisionRevisionDocumentoOperativa,
    DocumentoFinanciacion,
    EtapaProcesoAutomatizacionEducativa,
    HistorialEstadoSolicitud,
    IntentoEscaneoDocumento,
    OutboxCorreoEducativo,
    ParticipanteFinanciacion,
    ProcesamientoContenidoDocumento,
    ProcesoAutomatizacionEducativa,
    ValidacionIADocumento,
    SolicitudFinanciacionEducativa,
)
from financiacion_educativa.services.estado_publico import MAPA_ESTADO_PUBLICO
from instituciones.models import Institucion


HORAS_SIN_MOVIMIENTO = 72

ESTADOS_INTERNOS_POR_PUBLICO = {
    publico: tuple(
        interno
        for interno, estado_publico in MAPA_ESTADO_PUBLICO.items()
        if estado_publico == publico
    )
    for publico in EstadoPublicoSolicitud.values
}

ESTADOS_PROCESO_EXCEPCION = (
    EstadoProcesoAutomatizacionEducativa.MANUAL_EXCEPTION,
    EstadoProcesoAutomatizacionEducativa.FAILED,
)

BANDEJAS_OPERATIVAS = (
    {
        'codigo': 'revision_manual',
        'titulo': 'Revision manual',
        'descripcion': 'Expedientes que requieren criterio humano.',
        'icono': 'bi-person-check',
    },
    {
        'codigo': 'correccion',
        'titulo': 'Correccion requerida',
        'descripcion': 'Solicitudes devueltas al solicitante.',
        'icono': 'bi-pencil-square',
    },
    {
        'codigo': 'error_automatizacion',
        'titulo': 'Error de automatizacion',
        'descripcion': 'Procesos fallidos o en excepcion manual.',
        'icono': 'bi-exclamation-triangle',
    },
    {
        'codigo': 'documento_inconcluso',
        'titulo': 'Validacion inconclusa',
        'descripcion': 'Documentos con validacion tecnica no concluyente.',
        'icono': 'bi-file-earmark-excel',
    },
    {
        'codigo': 'firma_pendiente',
        'titulo': 'Firma pendiente',
        'descripcion': 'Pagares enviados que esperan firma.',
        'icono': 'bi-pen',
    },
    {
        'codigo': 'firma_ambigua',
        'titulo': 'Firma en conciliacion',
        'descripcion': 'Envios cuyo resultado no fue concluyente.',
        'icono': 'bi-shield-exclamation',
    },
    {
        'codigo': 'correo_pendiente',
        'titulo': 'Correo pendiente',
        'descripcion': 'Mensajes pendientes o en reintento.',
        'icono': 'bi-envelope',
    },
    {
        'codigo': 'correo_conciliacion',
        'titulo': 'Correo en conciliacion',
        'descripcion': 'Entregas con resultado ambiguo.',
        'icono': 'bi-envelope-exclamation',
    },
    {
        'codigo': 'sin_movimiento',
        'titulo': 'Sin movimiento',
        'descripcion': f'Solicitudes sin cambios por {HORAS_SIN_MOVIMIENTO} horas.',
        'icono': 'bi-clock-history',
    },
)


def solicitudes_operativas():
    ultimo_proceso = ProcesoAutomatizacionEducativa.objects.filter(
        solicitud_id=OuterRef('pk')
    ).order_by('-creada_en', '-id')
    validaciones_inconclusas = ValidacionIADocumento.objects.filter(
        documento__solicitud_id=OuterRef('pk'),
        documento__activo=True,
        estado__in=(
            EstadoValidacionIADocumento.MANUAL_REVIEW,
            EstadoValidacionIADocumento.ERROR,
        ),
    )
    contenidos_inconclusos = ProcesamientoContenidoDocumento.objects.filter(
        documento__solicitud_id=OuterRef('pk'),
        documento__activo=True,
        estado__in=(
            EstadoProcesamientoContenidoDocumento.RETRYING,
            EstadoProcesamientoContenidoDocumento.MANUAL_EXCEPTION,
            EstadoProcesamientoContenidoDocumento.FAILED,
        ),
    )
    correo_pendiente = OutboxCorreoEducativo.objects.filter(
        solicitud_id=OuterRef('pk'),
        estado__in=(
            EstadoOutboxCorreoEducativo.PENDING,
            EstadoOutboxCorreoEducativo.RETRYING,
        ),
    )
    correo_ambiguo = OutboxCorreoEducativo.objects.filter(
        solicitud_id=OuterRef('pk'),
        estado=EstadoOutboxCorreoEducativo.AMBIGUOUS,
    )
    return SolicitudFinanciacionEducativa.objects.select_related(
        'institucion'
    ).annotate(
        estado_proceso_operativo=Subquery(
            ultimo_proceso.values('estado')[:1],
            output_field=CharField(),
        ),
        etapa_operativa=Subquery(
            ultimo_proceso.values('etapa_actual')[:1],
            output_field=CharField(),
        ),
        razon_operativa=Subquery(
            ultimo_proceso.values('codigo_razon')[:1],
            output_field=CharField(),
        ),
        documento_inconcluso=Case(
            When(
                Exists(validaciones_inconclusas) | Exists(contenidos_inconclusos),
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
        correo_pendiente=Exists(correo_pendiente),
        correo_ambiguo=Exists(correo_ambiguo),
    ).annotate(
        tiene_excepcion=Case(
            When(
                Q(estado_proceso_operativo__in=ESTADOS_PROCESO_EXCEPCION)
                | Q(
                    razon_operativa=(
                        CodigoRazonAutomatizacionEducativa.SIGNATURE_SEND_AMBIGUOUS
                    )
                ),
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        )
    )


def aplicar_bandeja(queryset, codigo):
    if codigo == 'revision_manual':
        return queryset.filter(
            estado=EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
        )
    if codigo == 'correccion':
        return queryset.filter(
            estado=EstadoSolicitudFinanciacion.CORRECTION_REQUIRED
        )
    if codigo == 'error_automatizacion':
        return queryset.filter(
            estado_proceso_operativo__in=ESTADOS_PROCESO_EXCEPCION
        )
    if codigo == 'documento_inconcluso':
        return queryset.filter(documento_inconcluso=True)
    if codigo == 'firma_pendiente':
        return queryset.filter(
            Q(estado=EstadoSolicitudFinanciacion.PENDING_SIGNATURE)
            | Q(
                estado_proceso_operativo=(
                    EstadoProcesoAutomatizacionEducativa.PENDING_SIGNATURE
                )
            )
        )
    if codigo == 'firma_ambigua':
        return queryset.filter(
            razon_operativa=(
                CodigoRazonAutomatizacionEducativa.SIGNATURE_SEND_AMBIGUOUS
            )
        )
    if codigo == 'correo_pendiente':
        return queryset.filter(correo_pendiente=True)
    if codigo == 'correo_conciliacion':
        return queryset.filter(correo_ambiguo=True)
    if codigo == 'sin_movimiento':
        limite = timezone.now() - timedelta(hours=HORAS_SIN_MOVIMIENTO)
        return queryset.filter(actualizada_en__lt=limite).exclude(
            estado__in=(
                EstadoSolicitudFinanciacion.APPROVED,
                EstadoSolicitudFinanciacion.REJECTED,
                EstadoSolicitudFinanciacion.CANCELLED,
                EstadoSolicitudFinanciacion.PAID,
            )
        )
    return queryset


def obtener_indicadores_globales():
    solicitudes = SolicitudFinanciacionEducativa.objects.all()
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
            filter=Q(estado__in=(
                EstadoSolicitudFinanciacion.PENDING_TERMS,
                EstadoSolicitudFinanciacion.PENDING_DOCUMENT,
                EstadoSolicitudFinanciacion.PENDING_GUARDIAN,
                EstadoSolicitudFinanciacion.PENDING_PROMISSORY_NOTE,
                EstadoSolicitudFinanciacion.ACTIVE,
                EstadoSolicitudFinanciacion.PAYMENT_REPORTED,
                EstadoSolicitudFinanciacion.PAYMENT_UNDER_REVIEW,
            )),
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
            'id', filter=Q(estado=EstadoSolicitudFinanciacion.APPROVED)
        ),
        cerradas=Count(
            'id',
            filter=Q(estado__in=(
                EstadoSolicitudFinanciacion.REJECTED,
                EstadoSolicitudFinanciacion.CANCELLED,
                EstadoSolicitudFinanciacion.PAID,
            )),
        ),
        valor_solicitado=Sum('valor_plan'),
    )
    indicadores['procesos_excepcion'] = ProcesoAutomatizacionEducativa.objects.filter(
        estado__in=ESTADOS_PROCESO_EXCEPCION
    ).values('solicitud_id').distinct().count()
    indicadores['correos_pendientes'] = OutboxCorreoEducativo.objects.filter(
        estado__in=(
            EstadoOutboxCorreoEducativo.PENDING,
            EstadoOutboxCorreoEducativo.RETRYING,
        )
    ).count()
    indicadores['correos_conciliacion'] = OutboxCorreoEducativo.objects.filter(
        estado=EstadoOutboxCorreoEducativo.AMBIGUOUS
    ).count()
    indicadores['valor_financiado'] = (
        CondicionesFinancieras.objects.filter(activa=True).aggregate(
            total=Sum('capital_financiado')
        )['total']
        or Decimal('0')
    )
    indicadores['valor_solicitado'] = (
        indicadores['valor_solicitado'] or Decimal('0')
    )
    return indicadores


def obtener_bandejas_operativas():
    base = solicitudes_operativas()
    return [
        {
            **bandeja,
            'cantidad': aplicar_bandeja(base, bandeja['codigo']).count(),
        }
        for bandeja in BANDEJAS_OPERATIVAS
    ]


def obtener_solicitudes_recientes(limite=6):
    return solicitudes_operativas()[:limite]


def obtener_distribucion_instituciones(limite=8):
    return Institucion.objects.annotate(
        total_solicitudes=Count('solicitudes_financiacion_educativa'),
        valor_solicitado=Sum(
            'solicitudes_financiacion_educativa__valor_plan'
        ),
    ).filter(total_solicitudes__gt=0).order_by(
        '-total_solicitudes', 'nombre_comercial'
    )[:limite]


def obtener_opciones_filtros():
    solicitudes = SolicitudFinanciacionEducativa.objects.all()

    def valores(campo):
        return tuple(
            (valor, valor)
            for valor in solicitudes.exclude(**{campo: ''})
            .order_by(campo)
            .values_list(campo, flat=True)
            .distinct()
        )

    return {
        'instituciones': tuple(
            (str(pk), nombre)
            for pk, nombre in Institucion.objects.order_by(
                'nombre_comercial'
            ).values_list('pk', 'nombre_comercial')
        ),
        'programas': valores('nombre_curso'),
        'periodos': valores('periodo_academico'),
        'sedes': valores('sede'),
    }


def filtrar_solicitudes_operativas(*, filtros):
    consulta = solicitudes_operativas()
    busqueda = filtros.get('q')
    if busqueda:
        consulta = consulta.filter(
            Q(referencia_externa__icontains=busqueda)
            | Q(nombres__icontains=busqueda)
            | Q(apellidos__icontains=busqueda)
            | Q(correo__icontains=busqueda)
            | Q(numero_documento_estudiante__icontains=busqueda)
        )
    if filtros.get('institucion'):
        consulta = consulta.filter(institucion_id=filtros['institucion'])
    if filtros.get('estado'):
        consulta = consulta.filter(
            estado__in=ESTADOS_INTERNOS_POR_PUBLICO[filtros['estado']]
        )
    for parametro, campo in (
        ('programa', 'nombre_curso'),
        ('periodo', 'periodo_academico'),
        ('sede', 'sede'),
    ):
        if filtros.get(parametro):
            consulta = consulta.filter(**{campo: filtros[parametro]})
    if filtros.get('etapa'):
        consulta = consulta.filter(etapa_operativa=filtros['etapa'])
    if filtros.get('excepcion') == 'si':
        consulta = consulta.filter(tiene_excepcion=True)
    elif filtros.get('excepcion') == 'no':
        consulta = consulta.filter(tiene_excepcion=False)
    if filtros.get('desde'):
        consulta = consulta.filter(creada_en__date__gte=filtros['desde'])
    if filtros.get('hasta'):
        consulta = consulta.filter(creada_en__date__lte=filtros['hasta'])
    if filtros.get('bandeja'):
        consulta = aplicar_bandeja(consulta, filtros['bandeja'])
    orden = filtros.get('orden') or '-creada_en'
    return consulta.order_by(orden, 'id')


def obtener_instituciones_operativas():
    return Institucion.objects.annotate(
        total_solicitudes=Count('solicitudes_financiacion_educativa'),
        solicitudes_activas=Count(
            'solicitudes_financiacion_educativa',
            filter=~Q(
                solicitudes_financiacion_educativa__estado__in=(
                    EstadoSolicitudFinanciacion.APPROVED,
                    EstadoSolicitudFinanciacion.REJECTED,
                    EstadoSolicitudFinanciacion.CANCELLED,
                    EstadoSolicitudFinanciacion.PAID,
                )
            ),
        ),
        valor_solicitado=Sum(
            'solicitudes_financiacion_educativa__valor_plan'
        ),
    ).order_by('nombre_comercial')


def obtener_solicitud_operativa(application_id):
    participantes = ParticipanteFinanciacion.objects.prefetch_related(
        'roles'
    ).order_by('creado_en', 'id')
    documentos = DocumentoFinanciacion.objects.filter(activo=True).prefetch_related(
        Prefetch(
            'intentos_escaneo',
            queryset=IntentoEscaneoDocumento.objects.order_by('-numero'),
            to_attr='intentos_operativos',
        ),
        Prefetch(
            'validaciones_ia',
            queryset=ValidacionIADocumento.objects.order_by('-numero'),
            to_attr='validaciones_operativas',
        ),
        Prefetch(
            'procesamientos_contenido',
            queryset=ProcesamientoContenidoDocumento.objects.order_by('-numero'),
            to_attr='contenidos_operativos',
        ),
    ).order_by('tipo', 'id')
    procesos = ProcesoAutomatizacionEducativa.objects.prefetch_related(
        Prefetch(
            'etapas',
            queryset=EtapaProcesoAutomatizacionEducativa.objects.order_by(
                'iniciada_en', 'id'
            ),
            to_attr='etapas_operativas',
        )
    ).order_by('-creada_en', '-id')
    artefactos = ArtefactoContractualEducativo.objects.select_related(
        'proceso_firma'
    ).order_by('-numero_version')
    consulta = SolicitudFinanciacionEducativa.objects.select_related(
        'institucion'
    ).prefetch_related(
        Prefetch('participantes', participantes, to_attr='participantes_operativos'),
        Prefetch('documentos', documentos, to_attr='documentos_operativos'),
        Prefetch(
            'fotografias_financieras',
            CondicionesFinancieras.objects.filter(activa=True),
            to_attr='fotografias_operativas',
        ),
        Prefetch(
            'artefactos_contractuales',
            artefactos,
            to_attr='artefactos_operativos',
        ),
        Prefetch(
            'procesos_automatizacion',
            procesos,
            to_attr='procesos_operativos',
        ),
        Prefetch(
            'decisiones_revision',
            DecisionRevisionEducativa.objects.select_related(
                'responsable'
            ).order_by('-creada_en'),
            to_attr='decisiones_operativas',
        ),
        Prefetch(
            'historial_estados',
            HistorialEstadoSolicitud.objects.order_by('creado_en', 'id'),
            to_attr='historial_operativo',
        ),
        Prefetch(
            'correos_outbox',
            OutboxCorreoEducativo.objects.only(
                'id',
                'solicitud_id',
                'tipo_evento',
                'codigo_mensaje',
                'estado',
                'intentos',
                'maximo_intentos',
                'codigo_ultimo_error',
                'creada_en',
                'actualizada_en',
                'enviada_en',
            ).order_by('-creada_en'),
            to_attr='outbox_operativo',
        ),
    )
    return get_object_or_404(consulta, pk=application_id)


def documentos_revision_operativa():
    ultima_validacion = ValidacionIADocumento.objects.filter(
        documento_id=OuterRef('pk')
    ).order_by('-numero', '-iniciado_en')
    return DocumentoFinanciacion.objects.filter(
        activo=True,
        estado_escaneo=EstadoEscaneoDocumento.SAFE,
        estado_validacion=EstadoValidacionDocumento.PENDING,
        solicitud__estado=EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW,
    ).select_related(
        'solicitud__institucion',
        'participante',
    ).annotate(
        validacion_estado=Subquery(
            ultima_validacion.values('estado')[:1],
            output_field=CharField(),
        ),
        validacion_confianza=Subquery(
            ultima_validacion.values('confianza')[:1],
        ),
        validacion_hallazgos=Subquery(
            ultima_validacion.values('hallazgos')[:1],
            output_field=JSONField(),
        ),
    )


def filtrar_documentos_revision(*, filtros):
    consulta = documentos_revision_operativa()
    if filtros.get('q'):
        valor = filtros['q']
        consulta = consulta.filter(
            Q(solicitud__referencia_externa__icontains=valor)
            | Q(solicitud__nombres__icontains=valor)
            | Q(solicitud__apellidos__icontains=valor)
        )
    if filtros.get('institucion'):
        consulta = consulta.filter(
            solicitud__institucion_id=filtros['institucion']
        )
    if filtros.get('tipo'):
        consulta = consulta.filter(tipo=filtros['tipo'])
    if filtros.get('estado'):
        consulta = consulta.filter(estado_validacion=filtros['estado'])
    if filtros.get('hallazgo'):
        consulta = consulta.filter(
            validacion_hallazgos__icontains=filtros['hallazgo']
        )
    if filtros.get('desde'):
        consulta = consulta.filter(cargado_en__date__gte=filtros['desde'])
    if filtros.get('hasta'):
        consulta = consulta.filter(cargado_en__date__lte=filtros['hasta'])
    if filtros.get('antiguedad'):
        limite = timezone.now() - timedelta(
            hours=int(filtros['antiguedad'])
        )
        consulta = consulta.filter(cargado_en__lte=limite)
    return consulta.order_by(filtros.get('orden') or 'cargado_en', 'id')


def obtener_documento_revision(documento_id, *, activo=True):
    consulta = DocumentoFinanciacion.objects.select_related(
        'solicitud__institucion',
        'participante',
    ).prefetch_related(
        Prefetch(
            'validaciones_ia',
            queryset=ValidacionIADocumento.objects.order_by('-numero'),
            to_attr='validaciones_operativas',
        ),
        Prefetch(
            'decisiones_operativas',
            queryset=(
                DecisionRevisionDocumentoOperativa.objects.select_related(
                    'actor'
                ).order_by('-creada_en')
            ),
            to_attr='decisiones_documentales_operativas',
        ),
    )
    if activo:
        consulta = consulta.filter(activo=True)
    return get_object_or_404(consulta, pk=documento_id)
