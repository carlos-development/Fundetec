from dataclasses import dataclass, field
from pathlib import PurePosixPath

from django.apps import apps
from django.db import models, transaction
from django.db.models.deletion import ProtectedError

from instituciones.models import (
    CredencialAPIInstitucion,
    Institucion,
    MembresiaInstitucion,
)

from financiacion_educativa.choices import EstadoEscaneoDocumento
from financiacion_educativa.models import (
    DocumentoFinanciacion,
    EntregaInvitacionContinuacion,
    SolicitudFinanciacionEducativa,
)


class ErrorLimpiezaSolicitudes(Exception):
    pass


@dataclass(frozen=True)
class EspecificacionDependencia:
    modelo: str
    filtro_solicitudes: str


@dataclass(frozen=True)
class ArchivoPrivadoPlanificado:
    modelo: str
    campo: str
    nombre: str
    tamano: int | None
    storage: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class PlanLimpiezaSolicitudes:
    institucion_id: object
    institucion_nombre: str
    solicitudes: tuple
    conteos: tuple
    archivos: tuple
    archivos_invalidos: tuple
    relaciones_protegidas: tuple
    credenciales: int
    credenciales_activas: int
    membresias: int
    usuarios_membresia: int


@dataclass
class ResultadoArchivos:
    eliminados: list = field(default_factory=list)
    preservados: list = field(default_factory=list)
    fallidos: list = field(default_factory=list)


@dataclass(frozen=True)
class ResultadoLimpiezaSolicitudes:
    plan: PlanLimpiezaSolicitudes
    eliminados_por_modelo: tuple
    archivos: ResultadoArchivos


# Orden de hojas a raiz. Los nombres forman parte de una validacion de cobertura
# por metadatos para que un modelo dependiente nuevo no se omita silenciosamente.
DEPENDENCIAS_EN_ORDEN = (
    EspecificacionDependencia(
        'EventoWebhookFirmaEducativa',
        'proceso__solicitud_id__in',
    ),
    EspecificacionDependencia('ProcesoFirmaEducativa', 'solicitud_id__in'),
    EspecificacionDependencia('OutboxCorreoEducativo', 'solicitud_id__in'),
    EspecificacionDependencia(
        'EntregaCorreoEstadoSolicitud',
        'solicitud_id__in',
    ),
    EspecificacionDependencia(
        'DecisionRevisionDocumentoOperativa',
        'solicitud_id__in',
    ),
    EspecificacionDependencia('DecisionRevisionEducativa', 'solicitud_id__in'),
    EspecificacionDependencia(
        'ArtefactoContractualEducativo',
        'solicitud_id__in',
    ),
    EspecificacionDependencia(
        'CuotaAmortizacionEducativa',
        'fotografia__solicitud_id__in',
    ),
    EspecificacionDependencia('CondicionesFinancieras', 'solicitud_id__in'),
    EspecificacionDependencia(
        'EtapaProcesoAutomatizacionEducativa',
        'proceso__solicitud_id__in',
    ),
    EspecificacionDependencia(
        'ProcesoAutomatizacionEducativa',
        'solicitud_id__in',
    ),
    EspecificacionDependencia('EvidenciaMatricula', 'solicitud_id__in'),
    EspecificacionDependencia(
        'ProcesamientoContenidoDocumento',
        'documento__solicitud_id__in',
    ),
    EspecificacionDependencia(
        'ValidacionIADocumento',
        'documento__solicitud_id__in',
    ),
    EspecificacionDependencia(
        'ReaperturaEscaneoDocumento',
        'documento__solicitud_id__in',
    ),
    EspecificacionDependencia(
        'IntentoEscaneoDocumento',
        'documento__solicitud_id__in',
    ),
    EspecificacionDependencia('DocumentoFinanciacion', 'solicitud_id__in'),
    EspecificacionDependencia('Consentimiento', 'solicitud_id__in'),
    EspecificacionDependencia(
        'EventoParticipanteFinanciacion',
        'participante__solicitud_id__in',
    ),
    EspecificacionDependencia(
        'RolParticipanteFinanciacion',
        'solicitud_id__in',
    ),
    EspecificacionDependencia('ParticipanteFinanciacion', 'solicitud_id__in'),
    EspecificacionDependencia(
        'EventoEnlaceCapturaMovil',
        'enlace__solicitud_id__in',
    ),
    EspecificacionDependencia('EnlaceCapturaMovil', 'solicitud_id__in'),
    EspecificacionDependencia(
        'EntregaInvitacionContinuacion',
        'solicitud_id__in',
    ),
    EspecificacionDependencia(
        'EventoInvitacionContinuacion',
        'invitacion__solicitud_id__in',
    ),
    EspecificacionDependencia(
        'InvitacionContinuacionSolicitud',
        'solicitud_id__in',
    ),
    EspecificacionDependencia(
        'RegistroIdempotenciaSolicitud',
        'solicitud_id__in',
    ),
    EspecificacionDependencia(
        'EventoSeguridadFinanciacion',
        'solicitud_id__in',
    ),
    EspecificacionDependencia('HistorialEstadoSolicitud', 'solicitud_id__in'),
)

MODELOS_CON_ARCHIVOS = (
    ('DocumentoFinanciacion', 'archivo', 'solicitud_id__in'),
    ('ArtefactoContractualEducativo', 'archivo', 'solicitud_id__in'),
    ('ArtefactoContractualEducativo', 'archivo_firmado', 'solicitud_id__in'),
)


def _modelo(nombre):
    return apps.get_model('financiacion_educativa', nombre)


def _modelos_dependientes_por_metadatos():
    solicitud = SolicitudFinanciacionEducativa
    encontrados = {solicitud}
    cambio = True
    while cambio:
        cambio = False
        for modelo in apps.get_app_config('financiacion_educativa').get_models():
            if modelo in encontrados:
                continue
            for campo in modelo._meta.get_fields():
                if (
                    isinstance(campo, (models.ForeignKey, models.OneToOneField))
                    and campo.remote_field.model in encontrados
                ):
                    encontrados.add(modelo)
                    cambio = True
                    break
    return encontrados


def validar_cobertura_dependencias():
    detectados = _modelos_dependientes_por_metadatos()
    declarados = {
        SolicitudFinanciacionEducativa,
        *(_modelo(item.modelo) for item in DEPENDENCIAS_EN_ORDEN),
    }
    faltantes = sorted(
        modelo._meta.label for modelo in detectados - declarados
    )
    sobrantes = sorted(
        modelo._meta.label for modelo in declarados - detectados
    )
    genericas = sorted(
        f'{modelo._meta.label}.{campo.name}'
        for modelo in detectados
        for campo in modelo._meta.private_fields
        if campo.__class__.__name__ in {'GenericForeignKey', 'GenericRelation'}
    )
    externas = sorted(
        f'{modelo._meta.label}.{campo.name}'
        for modelo in apps.get_models()
        if modelo._meta.app_label != 'financiacion_educativa'
        for campo in modelo._meta.get_fields()
        if (
            isinstance(campo, (models.ForeignKey, models.OneToOneField))
            and campo.remote_field.model in detectados
        )
    )
    if faltantes or sobrantes or genericas or externas:
        detalles = []
        if faltantes:
            detalles.append(f'modelos no cubiertos: {", ".join(faltantes)}')
        if sobrantes:
            detalles.append(f'modelos declarados de mas: {", ".join(sobrantes)}')
        if genericas:
            detalles.append(f'relaciones genericas: {", ".join(genericas)}')
        if externas:
            detalles.append(f'relaciones externas: {", ".join(externas)}')
        raise ErrorLimpiezaSolicitudes('; '.join(detalles))
    return detectados


def _nombre_archivo_valido(nombre):
    if not nombre or '\x00' in nombre:
        return False
    normalizado = nombre.replace('\\', '/')
    ruta = PurePosixPath(normalizado)
    return bool(
        not ruta.is_absolute()
        and ruta.parts
        and ruta.parts != ('.',)
        and '..' not in ruta.parts
    )


def _archivos_planificados(solicitud_ids):
    archivos = {}
    invalidos = []
    for modelo_nombre, campo_nombre, filtro in MODELOS_CON_ARCHIVOS:
        modelo = _modelo(modelo_nombre)
        campo = modelo._meta.get_field(campo_nombre)
        nombres = modelo.objects.filter(
            **{filtro: solicitud_ids}
        ).exclude(**{campo_nombre: ''}).exclude(
            **{f'{campo_nombre}__isnull': True}
        ).values_list(campo_nombre, flat=True)
        for nombre in nombres.iterator():
            nombre = str(nombre or '')
            if not _nombre_archivo_valido(nombre):
                invalidos.append(f'{modelo_nombre}.{campo_nombre}:{nombre!r}')
                continue
            clave = (modelo_nombre, campo_nombre, nombre)
            try:
                tamano = campo.storage.size(nombre)
            except (OSError, ValueError, NotImplementedError):
                tamano = None
            archivos[clave] = ArchivoPrivadoPlanificado(
                modelo=modelo_nombre,
                campo=campo_nombre,
                nombre=nombre,
                tamano=tamano,
                storage=campo.storage,
            )
    return tuple(archivos.values()), tuple(sorted(invalidos))


def _relaciones_protegidas(solicitud_ids):
    detectados = _modelos_dependientes_por_metadatos()
    relaciones = []
    conteos = {
        item.modelo: _modelo(item.modelo).objects.filter(
            **{item.filtro_solicitudes: solicitud_ids}
        ).count()
        for item in DEPENDENCIAS_EN_ORDEN
    }
    for modelo in detectados:
        for campo in modelo._meta.get_fields():
            if (
                isinstance(campo, (models.ForeignKey, models.OneToOneField))
                and campo.remote_field.model in detectados
                and campo.remote_field.on_delete is models.PROTECT
                and conteos.get(modelo.__name__, 0)
            ):
                relaciones.append(
                    f'{modelo.__name__}.{campo.name}'
                    f'->{campo.remote_field.model.__name__}'
                )
    return tuple(sorted(relaciones)), tuple(sorted(conteos.items()))


def construir_plan_limpieza(institucion, *, solicitud_ids=None):
    validar_cobertura_dependencias()
    solicitudes = SolicitudFinanciacionEducativa.objects.filter(
        institucion=institucion
    )
    if solicitud_ids is not None:
        solicitudes = solicitudes.filter(pk__in=solicitud_ids)
    solicitudes_plan = tuple(
        solicitudes.order_by('creada_en', 'id').values_list(
            'id', 'referencia_externa', 'estado'
        )
    )
    ids = tuple(item[0] for item in solicitudes_plan)
    protegidas, conteos = _relaciones_protegidas(ids)
    archivos, invalidos = _archivos_planificados(ids)
    membresias = MembresiaInstitucion.objects.filter(institucion=institucion)
    credenciales = CredencialAPIInstitucion.objects.filter(
        institucion=institucion
    )
    return PlanLimpiezaSolicitudes(
        institucion_id=institucion.pk,
        institucion_nombre=institucion.nombre_comercial,
        solicitudes=solicitudes_plan,
        conteos=conteos,
        archivos=archivos,
        archivos_invalidos=invalidos,
        relaciones_protegidas=protegidas,
        credenciales=credenciales.count(),
        credenciales_activas=credenciales.filter(activa=True).count(),
        membresias=membresias.count(),
        usuarios_membresia=membresias.values('usuario_id').distinct().count(),
    )


def _bloquear_ids_solicitudes(institucion):
    return tuple(
        SolicitudFinanciacionEducativa.objects.select_for_update()
        .filter(institucion=institucion)
        .order_by('id')
        .values_list('id', flat=True)
    )


def _desvincular_ciclos(solicitud_ids):
    documentos = DocumentoFinanciacion.objects.filter(
        solicitud_id__in=solicitud_ids,
        ultimo_intento_limpio__isnull=False,
    )
    for documento in documentos.iterator():
        documento.estado_escaneo = EstadoEscaneoDocumento.PENDING_SECURITY_SCAN
        documento.ultimo_intento_limpio = None
        documento.save(update_fields={
            'estado_escaneo',
            'ultimo_intento_limpio',
            'escaneo_requerido_desde',
            'actualizado_en',
        })
    DocumentoFinanciacion.objects.filter(
        solicitud_id__in=solicitud_ids,
        reemplaza_a__isnull=False,
    ).update(reemplaza_a=None)
    EntregaInvitacionContinuacion.objects.filter(
        solicitud_id__in=solicitud_ids,
        reemplaza_a__isnull=False,
    ).update(reemplaza_a=None)


def _eliminar_dependencia(especificacion, solicitud_ids):
    modelo = _modelo(especificacion.modelo)
    queryset = modelo.objects.filter(
        **{especificacion.filtro_solicitudes: solicitud_ids}
    )
    cantidad = queryset.count()
    if cantidad:
        queryset.delete()
    return cantidad


def _archivo_sigue_referenciado(nombre):
    for modelo_nombre, campo_nombre, _ in MODELOS_CON_ARCHIVOS:
        modelo = _modelo(modelo_nombre)
        if modelo.objects.filter(**{campo_nombre: nombre}).exists():
            return True
    return False


def _eliminar_archivos_planificados(archivos, resultado):
    eliminados = set()
    for archivo in archivos:
        clave = (id(archivo.storage), archivo.nombre)
        if clave in eliminados:
            continue
        if _archivo_sigue_referenciado(archivo.nombre):
            resultado.preservados.append(archivo.nombre)
            continue
        try:
            archivo.storage.delete(archivo.nombre)
        except Exception as error:  # El borrado fisico ocurre despues del commit.
            resultado.fallidos.append(
                (archivo.nombre, type(error).__name__)
            )
        else:
            resultado.eliminados.append(archivo.nombre)
            eliminados.add(clave)


@transaction.atomic
def ejecutar_limpieza_solicitudes(
    *,
    institucion_id,
    expected_count,
):
    validar_cobertura_dependencias()
    try:
        institucion = Institucion.objects.select_for_update().get(
            pk=institucion_id
        )
    except Institucion.DoesNotExist as error:
        raise ErrorLimpiezaSolicitudes(
            'La institucion indicada no existe.'
        ) from error
    solicitud_ids = _bloquear_ids_solicitudes(institucion)
    if len(solicitud_ids) != expected_count:
        raise ErrorLimpiezaSolicitudes(
            'La cantidad de solicitudes cambio antes de ejecutar la limpieza.'
        )
    plan = construir_plan_limpieza(
        institucion,
        solicitud_ids=solicitud_ids,
    )
    if plan.archivos_invalidos:
        raise ErrorLimpiezaSolicitudes(
            'Existen nombres de archivo no seguros; no se realizo la limpieza.'
        )
    credenciales_antes = tuple(
        CredencialAPIInstitucion.objects.filter(institucion=institucion)
        .order_by('id')
        .values_list('id', 'activa')
    )
    membresias_antes = tuple(
        MembresiaInstitucion.objects.filter(institucion=institucion)
        .order_by('id')
        .values_list('id', 'usuario_id', 'activa')
    )
    _desvincular_ciclos(solicitud_ids)
    eliminados = []
    try:
        for especificacion in DEPENDENCIAS_EN_ORDEN:
            eliminados.append((
                especificacion.modelo,
                _eliminar_dependencia(especificacion, solicitud_ids),
            ))
        solicitudes_eliminadas = (
            SolicitudFinanciacionEducativa.objects.filter(
                pk__in=solicitud_ids
            ).count()
        )
        SolicitudFinanciacionEducativa.objects.filter(
            pk__in=solicitud_ids
        ).delete()
        eliminados.append((
            'SolicitudFinanciacionEducativa',
            solicitudes_eliminadas,
        ))
    except ProtectedError as error:
        raise ErrorLimpiezaSolicitudes(
            'La limpieza fue bloqueada por una relacion protegida.'
        ) from error
    if SolicitudFinanciacionEducativa.objects.filter(
        institucion=institucion
    ).exists():
        raise ErrorLimpiezaSolicitudes(
            'Quedaron solicitudes de la institucion despues de la limpieza.'
        )
    if not Institucion.objects.filter(pk=institucion.pk).exists():
        raise ErrorLimpiezaSolicitudes('La institucion no fue preservada.')
    if credenciales_antes != tuple(
        CredencialAPIInstitucion.objects.filter(institucion=institucion)
        .order_by('id')
        .values_list('id', 'activa')
    ):
        raise ErrorLimpiezaSolicitudes('Las credenciales fueron modificadas.')
    if membresias_antes != tuple(
        MembresiaInstitucion.objects.filter(institucion=institucion)
        .order_by('id')
        .values_list('id', 'usuario_id', 'activa')
    ):
        raise ErrorLimpiezaSolicitudes('Las membresias fueron modificadas.')
    for especificacion in DEPENDENCIAS_EN_ORDEN:
        if _modelo(especificacion.modelo).objects.filter(
            **{especificacion.filtro_solicitudes: solicitud_ids}
        ).exists():
            raise ErrorLimpiezaSolicitudes(
                f'Quedaron registros de {especificacion.modelo}.'
            )
    resultado_archivos = ResultadoArchivos()
    transaction.on_commit(
        lambda: _eliminar_archivos_planificados(
            plan.archivos,
            resultado_archivos,
        )
    )
    return ResultadoLimpiezaSolicitudes(
        plan=plan,
        eliminados_por_modelo=tuple(eliminados),
        archivos=resultado_archivos,
    )
