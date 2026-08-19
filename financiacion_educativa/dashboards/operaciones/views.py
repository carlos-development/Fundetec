from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .forms import FiltrosSolicitudesOperativasForm
from .permissions import (
    PERMISO_DOCUMENTOS,
    PERMISO_PROCESOS,
    PERMISO_SOLICITUDES,
    capacidades_operativas,
    requiere_permisos_operativos,
)
from .presenters import (
    presentar_detalle_solicitud,
    presentar_resumen_solicitud,
)
from .selectors import (
    filtrar_solicitudes_operativas,
    obtener_bandejas_operativas,
    obtener_distribucion_instituciones,
    obtener_indicadores_globales,
    obtener_instituciones_operativas,
    obtener_opciones_filtros,
    obtener_solicitud_operativa,
    obtener_solicitudes_recientes,
)


def _contexto(request, *, seccion='resumen', **adicional):
    return {
        'seccion_operativa': seccion,
        'capacidades_operativas': capacidades_operativas(request.user),
        **adicional,
    }


def _querystring_sin_pagina(request):
    parametros = request.GET.copy()
    parametros.pop('page', None)
    return parametros.urlencode()


@never_cache
@requiere_permisos_operativos(PERMISO_SOLICITUDES)
@require_GET
def inicio_view(request):
    capacidades = capacidades_operativas(request.user)
    recientes = []
    if capacidades['solicitudes']:
        recientes = [
            presentar_resumen_solicitud(solicitud)
            for solicitud in obtener_solicitudes_recientes()
        ]
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/inicio.html',
        _contexto(
            request,
            indicadores=obtener_indicadores_globales(),
            bandejas=(
                obtener_bandejas_operativas()
                if capacidades['procesos']
                else []
            ),
            solicitudes_recientes=recientes,
            distribucion_instituciones=obtener_distribucion_instituciones(),
        ),
    )


@never_cache
@requiere_permisos_operativos(PERMISO_SOLICITUDES)
@require_GET
def solicitudes_view(request):
    formulario = FiltrosSolicitudesOperativasForm(
        request.GET,
        opciones=obtener_opciones_filtros(),
    )
    estado_http = 200
    if formulario.is_valid():
        consulta = filtrar_solicitudes_operativas(
            filtros=formulario.cleaned_data
        )
        pagina_solicitada = formulario.cleaned_data.get('page') or 1
    else:
        consulta = filtrar_solicitudes_operativas(filtros={}).none()
        pagina_solicitada = 1
        estado_http = 400

    paginador = Paginator(consulta, 25)
    try:
        pagina = paginador.page(pagina_solicitada)
    except (EmptyPage, PageNotAnInteger):
        pagina = paginador.get_page(1)
        formulario.add_error('page', 'La pagina solicitada no es valida.')
        estado_http = 400
    pagina.object_list = [
        presentar_resumen_solicitud(solicitud)
        for solicitud in pagina.object_list
    ]
    bandeja = (
        formulario.cleaned_data.get('bandeja')
        if formulario.is_valid()
        else ''
    )
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/solicitudes_lista.html',
        _contexto(
            request,
            seccion='firmas' if bandeja == 'firma_pendiente' else 'solicitudes',
            formulario_filtros=formulario,
            pagina=pagina,
            querystring=_querystring_sin_pagina(request),
            bandeja_activa=bandeja,
        ),
        status=estado_http,
    )


@never_cache
@requiere_permisos_operativos(PERMISO_PROCESOS)
@require_GET
def bandejas_view(request):
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/bandejas.html',
        _contexto(
            request,
            seccion='bandejas',
            bandejas=obtener_bandejas_operativas(),
        ),
    )


@never_cache
@requiere_permisos_operativos(PERMISO_SOLICITUDES)
@require_GET
def instituciones_view(request):
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/instituciones.html',
        _contexto(
            request,
            seccion='instituciones',
            instituciones=obtener_instituciones_operativas(),
        ),
    )


@never_cache
@requiere_permisos_operativos(PERMISO_SOLICITUDES)
@require_GET
def solicitud_detalle_view(request, application_id):
    solicitud = obtener_solicitud_operativa(application_id)
    capacidades = capacidades_operativas(request.user)
    detalle = presentar_detalle_solicitud(
        solicitud,
        datos_integrales=capacidades['datos_integrales'],
        puede_ver_documentos=(
            capacidades['documentos']
            and request.user.has_perm(PERMISO_DOCUMENTOS)
        ),
        puede_ver_procesos=(
            capacidades['procesos']
            and request.user.has_perm(PERMISO_PROCESOS)
        ),
    )
    return render(
        request,
        'financiacion_educativa/dashboards/operaciones/solicitud_detalle.html',
        _contexto(
            request,
            seccion='solicitudes',
            solicitud=detalle,
        ),
    )
