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
]
