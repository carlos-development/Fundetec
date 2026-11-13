from functools import wraps
from django.conf import settings

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
PERMISO_ACCESO_REVISION_DOCUMENTAL = (
    'financiacion_educativa.acceder_revision_documental_operativa'
)
PERMISO_DECIDIR_REVISION_DOCUMENTAL = (
    'financiacion_educativa.decidir_revision_documental_operativa'
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
        'politicas_libro': usuario.is_active and usuario.has_perms((PERMISO_ACCESO,)) and any(
            usuario.has_perm('financiacion_educativa.' + permiso) for permiso in (
                'preparar_politicas_libro', 'revisar_politicas_libro', 'aprobar_politicas_libro',
                'controlar_piloto_libro', 'gestionar_alcances_libro', 'aplicar_cuotas_educativas', 'compensar_libro_educativo')),
        'seguimiento_cartera': usuario.is_active and usuario.has_perms((PERMISO_ACCESO, PERMISO_SOLICITUDES,
            'financiacion_educativa.consultar_recaudos_educativos')),
        'registrar_pagos': settings.FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED and usuario.is_active and usuario.has_perms((
            PERMISO_ACCESO, 'financiacion_educativa.consultar_recaudos_educativos',
            'financiacion_educativa.registrar_pagos_recibidos_educativos')),
        'recaudos': settings.FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED and usuario.is_active and usuario.has_perm('financiacion_educativa.consultar_recaudos_educativos'),
        'solicitudes': usuario.has_perm(PERMISO_SOLICITUDES),
        'documentos': usuario.has_perm(PERMISO_DOCUMENTOS),
        'procesos': usuario.has_perm(PERMISO_PROCESOS),
        'datos_integrales': usuario.has_perm(PERMISO_DATOS_INTEGRALES),
        'revision_documental': usuario.has_perm(
            PERMISO_ACCESO_REVISION_DOCUMENTAL
        ),
        'decidir_revision_documental': usuario.has_perm(
            PERMISO_DECIDIR_REVISION_DOCUMENTAL
        ),
    }
