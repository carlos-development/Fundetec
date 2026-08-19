from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .permissions import (
    SESSION_MEMBRESIA_INSTITUCIONAL_ID,
    requiere_contexto_institucional,
    resolver_contexto_institucional,
)
from .forms import FiltrosSolicitudesInstitucionalesForm
from .presenters import (
    presentar_detalle_solicitud,
    presentar_resumen_solicitud,
)
from .selectors import (
    filtrar_solicitudes_institucionales,
    obtener_indicadores_institucionales,
    obtener_opciones_filtros,
    obtener_solicitud_institucional,
    obtener_solicitudes_recientes,
)


SECCIONES_PROXIMAS = (
    {
        'titulo': 'Reportes',
        'descripcion': 'Reportes del programa disponibles en una fase posterior.',
        'icono': 'bi-bar-chart',
    },
    {
        'titulo': 'Integracion',
        'descripcion': 'Gestion de integracion disponible en una fase posterior.',
        'icono': 'bi-braces',
    },
)


def _contexto_dashboard(request, *, seccion_dashboard='inicio', **adicional):
    return {
        'membresia_institucional': getattr(
            request,
            'membresia_institucional',
            None,
        ),
        'institucion_activa': getattr(request, 'institucion_activa', None),
        'membresias_institucionales': getattr(
            request,
            'membresias_institucionales',
            (),
        ),
        'puede_cambiar_institucion': len(
            getattr(request, 'membresias_institucionales', ())
        ) > 1,
        'seccion_dashboard': seccion_dashboard,
        **adicional,
    }


@never_cache
@requiere_contexto_institucional
@require_GET
def inicio_view(request):
    institucion = request.institucion_activa
    recientes = [
        presentar_resumen_solicitud(solicitud)
        for solicitud in obtener_solicitudes_recientes(institucion=institucion)
    ]
    return render(
        request,
        'financiacion_educativa/dashboards/institucional/inicio.html',
        _contexto_dashboard(
            request,
            indicadores=obtener_indicadores_institucionales(
                institucion=institucion
            ),
            solicitudes_recientes=recientes,
            secciones_proximas=SECCIONES_PROXIMAS,
        ),
    )


def _querystring_sin_pagina(request):
    parametros = request.GET.copy()
    parametros.pop('page', None)
    return parametros.urlencode()


def _listado_solicitudes(request, *, solo_seguimiento=False):
    institucion = request.institucion_activa
    opciones = obtener_opciones_filtros(institucion=institucion)
    formulario = FiltrosSolicitudesInstitucionalesForm(
        request.GET,
        opciones=opciones,
    )
    estado_http = 200
    if formulario.is_valid():
        consulta = filtrar_solicitudes_institucionales(
            institucion=institucion,
            filtros=formulario.cleaned_data,
            solo_seguimiento=solo_seguimiento,
        )
        pagina_solicitada = formulario.cleaned_data.get('page') or 1
    else:
        consulta = filtrar_solicitudes_institucionales(
            institucion=institucion,
            filtros={},
            solo_seguimiento=solo_seguimiento,
        ).none()
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
    return render(
        request,
        'financiacion_educativa/dashboards/institucional/solicitudes_lista.html',
        _contexto_dashboard(
            request,
            seccion_dashboard=(
                'seguimiento' if solo_seguimiento else 'solicitudes'
            ),
            formulario_filtros=formulario,
            pagina=pagina,
            querystring=_querystring_sin_pagina(request),
            solo_seguimiento=solo_seguimiento,
        ),
        status=estado_http,
    )


@never_cache
@requiere_contexto_institucional
@require_GET
def solicitudes_view(request):
    return _listado_solicitudes(request)


@never_cache
@requiere_contexto_institucional
@require_GET
def seguimiento_view(request):
    return _listado_solicitudes(request, solo_seguimiento=True)


@never_cache
@requiere_contexto_institucional
@require_GET
def solicitud_detalle_view(request, application_id):
    solicitud = obtener_solicitud_institucional(
        institucion=request.institucion_activa,
        application_id=application_id,
    )
    detalle = presentar_detalle_solicitud(
        solicitud,
        rol=request.membresia_institucional.rol,
    )
    return render(
        request,
        'financiacion_educativa/dashboards/institucional/solicitud_detalle.html',
        _contexto_dashboard(
            request,
            seccion_dashboard='solicitudes',
            solicitud=detalle,
        ),
    )


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def seleccionar_institucion_view(request):
    resolucion = resolver_contexto_institucional(request)
    if not resolucion.membresias:
        raise PermissionDenied('No tienes acceso al panel del programa.')
    if len(resolucion.membresias) == 1:
        return redirect('financiacion_educativa_web:institucion:inicio')

    seleccion_invalida = False
    if request.method == 'POST':
        institucion_id = (request.POST.get('institucion_id') or '').strip()
        membresia = next(
            (
                candidata
                for candidata in resolucion.membresias
                if str(candidata.institucion_id) == institucion_id
            ),
            None,
        )
        if membresia is not None:
            request.session[SESSION_MEMBRESIA_INSTITUCIONAL_ID] = str(
                membresia.pk
            )
            return redirect('financiacion_educativa_web:institucion:inicio')
        request.session.pop(SESSION_MEMBRESIA_INSTITUCIONAL_ID, None)
        seleccion_invalida = True

    return render(
        request,
        'financiacion_educativa/dashboards/institucional/seleccionar.html',
        _contexto_dashboard(
            request,
            membresia_institucional=None,
            institucion_activa=None,
            membresias_institucionales=resolucion.membresias,
            puede_cambiar_institucion=False,
            seleccion_invalida=seleccion_invalida,
        ),
        status=400 if seleccion_invalida else 200,
    )


@never_cache
@login_required
@require_POST
def cambiar_institucion_view(request):
    resolucion = resolver_contexto_institucional(request)
    if not resolucion.membresias:
        raise PermissionDenied('No tienes acceso al panel del programa.')
    request.session.pop(SESSION_MEMBRESIA_INSTITUCIONAL_ID, None)
    if len(resolucion.membresias) == 1:
        return redirect('financiacion_educativa_web:institucion:inicio')
    return redirect('financiacion_educativa_web:institucion:seleccionar')
