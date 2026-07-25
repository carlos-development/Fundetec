from django.urls import path

from . import views


app_name = 'financiacion_educativa_web'

urlpatterns = [
    path(
        'continuar/<str:token>/',
        views.continuar_invitacion_view,
        name='continuar-invitacion',
    ),
    path('inicio/', views.inicio_continuacion_view, name='inicio'),
    path('acceso/', views.acceso_view, name='acceso'),
    path('registro/', views.registro_view, name='registro'),
    path('confirmar/', views.confirmar_asociacion_view, name='confirmar'),
    path(
        'solicitudes/<uuid:solicitud_id>/terminos/',
        views.terminos_view,
        name='terminos',
    ),
    path(
        'solicitudes/<uuid:solicitud_id>/siguiente/',
        views.siguiente_paso_view,
        name='siguiente',
    ),
    path(
        'solicitudes/<uuid:solicitud_id>/documentacion/',
        views.documentacion_view,
        name='documentacion',
    ),
    path(
        'solicitudes/<uuid:solicitud_id>/participantes/nuevo/',
        views.participante_view,
        name='participante-nuevo',
    ),
    path(
        'solicitudes/<uuid:solicitud_id>/participantes/<uuid:participante_id>/',
        views.participante_view,
        name='participante-editar',
    ),
    path(
        'solicitudes/<uuid:solicitud_id>/documentos/cargar/',
        views.cargar_documento_view,
        name='documento-cargar',
    ),
    path(
        (
            'solicitudes/<uuid:solicitud_id>/documentos/'
            '<uuid:documento_id>/reemplazar/'
        ),
        views.reemplazar_documento_view,
        name='documento-reemplazar',
    ),
    path(
        (
            'solicitudes/<uuid:solicitud_id>/documentos/'
            '<uuid:documento_id>/descargar/'
        ),
        views.descargar_documento_view,
        name='documento-descargar',
    ),
    path(
        'solicitudes/<uuid:solicitud_id>/matricula/',
        views.matricula_view,
        name='matricula',
    ),
    path(
        'solicitudes/<uuid:solicitud_id>/documentacion/completar/',
        views.completar_documentacion_view,
        name='documentacion-completar',
    ),
    path(
        'solicitudes/<uuid:solicitud_id>/finanzas/',
        views.finanzas_view,
        name='finanzas',
    ),
    path(
        'solicitudes/<uuid:solicitud_id>/finanzas/proyectar-abono/',
        views.proyectar_abono_view,
        name='proyectar-abono',
    ),
    path(
        'solicitudes/<uuid:solicitud_id>/finanzas/proyectar-pago-total/',
        views.proyectar_pago_total_view,
        name='proyectar-pago-total',
    ),
]
