from django.urls import path

from . import views


app_name = 'operaciones'

urlpatterns = [
    path('', views.inicio_view, name='inicio'),
    path('solicitudes/', views.solicitudes_view, name='solicitudes'),
    path('bandejas/', views.bandejas_view, name='bandejas'),
    path('instituciones/', views.instituciones_view, name='instituciones'),
    path(
        'revision-documental/',
        views.revision_documental_view,
        name='revision-documental',
    ),
    path(
        'documentos/<uuid:application_id>/previsualizar/',
        views.previsualizar_documento_operativo_view,
        name='documento-previsualizar',
    ),
    path(
        'documentos/<uuid:application_id>/aceptar/',
        views.aceptar_documento_view,
        name='documento-aceptar',
    ),
    path(
        'documentos/<uuid:application_id>/solicitar-correccion/',
        views.solicitar_correccion_documento_view,
        name='documento-solicitar-correccion',
    ),
    path(
        'solicitudes/<uuid:application_id>/',
        views.solicitud_detalle_view,
        name='solicitud-detalle',
    ),
    path(
        'solicitudes/<uuid:application_id>/revision-documental/',
        views.revision_documento_view,
        name='revision-documento',
    ),
]
