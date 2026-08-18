from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .permissions import (
    SESSION_MEMBRESIA_INSTITUCIONAL_ID,
    requiere_contexto_institucional,
    resolver_contexto_institucional,
)


SECCIONES_PROXIMAS = (
    {
        'titulo': 'Solicitudes',
        'descripcion': 'Consulta institucional disponible en una fase posterior.',
        'icono': 'bi-file-earmark-text',
    },
    {
        'titulo': 'Seguimiento',
        'descripcion': 'Seguimiento operativo disponible en una fase posterior.',
        'icono': 'bi-signpost-split',
    },
    {
        'titulo': 'Reportes',
        'descripcion': 'Reportes institucionales disponibles en una fase posterior.',
        'icono': 'bi-bar-chart',
    },
    {
        'titulo': 'Integracion',
        'descripcion': 'Gestion de integracion disponible en una fase posterior.',
        'icono': 'bi-braces',
    },
)


def _contexto_dashboard(request, **adicional):
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
        **adicional,
    }


@never_cache
@requiere_contexto_institucional
@require_GET
def inicio_view(request):
    return render(
        request,
        'financiacion_educativa/dashboards/institucional/inicio.html',
        _contexto_dashboard(request, secciones_proximas=SECCIONES_PROXIMAS),
    )


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def seleccionar_institucion_view(request):
    resolucion = resolver_contexto_institucional(request)
    if not resolucion.membresias:
        raise PermissionDenied('No tienes acceso al panel institucional.')
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
        raise PermissionDenied('No tienes acceso al panel institucional.')
    request.session.pop(SESSION_MEMBRESIA_INSTITUCIONAL_ID, None)
    if len(resolucion.membresias) == 1:
        return redirect('financiacion_educativa_web:institucion:inicio')
    return redirect('financiacion_educativa_web:institucion:seleccionar')
