from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


PERMISO_ACCESO = 'financiacion_educativa.acceder_dashboard_operativo'
PERMISO_SOLICITUDES = (
    'financiacion_educativa.consultar_solicitudes_operativas'
)
PERMISO_DOCUMENTOS = (
    'financiacion_educativa.consultar_documentos_validaciones_operativas'
)
PERMISO_PROCESOS = (
    'financiacion_educativa.consultar_procesos_excepciones_operativas'
)
PERMISO_DATOS_INTEGRALES = (
    'financiacion_educativa.consultar_datos_integrales_operativos'
)


def requiere_permisos_operativos(*permisos):
    requeridos = (PERMISO_ACCESO, *permisos)

    def decorador(view):
        @login_required
        @wraps(view)
        def protegida(request, *args, **kwargs):
            if not request.user.has_perms(requeridos):
                raise PermissionDenied(
                    'No tienes autorizacion para consultar este modulo.'
                )
            return view(request, *args, **kwargs)

        return protegida

    return decorador


def capacidades_operativas(usuario):
    return {
        'solicitudes': usuario.has_perm(PERMISO_SOLICITUDES),
        'documentos': usuario.has_perm(PERMISO_DOCUMENTOS),
        'procesos': usuario.has_perm(PERMISO_PROCESOS),
        'datos_integrales': usuario.has_perm(PERMISO_DATOS_INTEGRALES),
    }
