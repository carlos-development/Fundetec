from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from instituciones.models import MembresiaInstitucion
from instituciones.services.membresias import resolver_institucion_activa


SESSION_MEMBRESIA_INSTITUCIONAL_ID = (
    'financiacion_educativa_membresia_institucional_id'
)


def resolver_contexto_institucional(request):
    membresia_id = request.session.get(SESSION_MEMBRESIA_INSTITUCIONAL_ID)
    resolucion = resolver_institucion_activa(
        usuario=request.user,
        membresia_id=membresia_id,
    )
    if resolucion.seleccion_invalida:
        request.session.pop(SESSION_MEMBRESIA_INSTITUCIONAL_ID, None)
    if resolucion.membresia:
        identificador = str(resolucion.membresia.pk)
        if request.session.get(SESSION_MEMBRESIA_INSTITUCIONAL_ID) != identificador:
            request.session[SESSION_MEMBRESIA_INSTITUCIONAL_ID] = identificador
        request.membresia_institucional = resolucion.membresia
        request.institucion_activa = resolucion.membresia.institucion
    request.membresias_institucionales = resolucion.membresias
    return resolucion


def requiere_contexto_institucional(view):
    @login_required
    @wraps(view)
    def protegida(request, *args, **kwargs):
        resolucion = resolver_contexto_institucional(request)
        if not resolucion.membresias:
            raise PermissionDenied('No tienes acceso al panel del programa.')
        if resolucion.requiere_seleccion:
            return redirect(
                'financiacion_educativa_web:institucion:seleccionar'
            )
        return view(request, *args, **kwargs)

    return protegida


def membresia_tiene_rol(membresia, *roles):
    return bool(
        membresia
        and membresia.activa
        and membresia.institucion.activa
        and membresia.rol in roles
    )


def requiere_rol_institucional(*roles):
    desconocidos = set(roles) - set(MembresiaInstitucion.Rol.values)
    if desconocidos:
        raise ValueError('Se configuro un rol institucional desconocido.')

    def decorador(view):
        @requiere_contexto_institucional
        @wraps(view)
        def protegida(request, *args, **kwargs):
            if not membresia_tiene_rol(
                request.membresia_institucional,
                *roles,
            ):
                raise PermissionDenied(
                    'No tienes permiso para realizar esta operacion.'
                )
            return view(request, *args, **kwargs)

        return protegida

    return decorador
