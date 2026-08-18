from django.urls import path

from . import views


app_name = 'institucion'

urlpatterns = [
    path('', views.inicio_view, name='inicio'),
    path('solicitudes/', views.solicitudes_view, name='solicitudes'),
    path('seguimiento/', views.seguimiento_view, name='seguimiento'),
    path(
        'solicitudes/<uuid:application_id>/',
        views.solicitud_detalle_view,
        name='solicitud-detalle',
    ),
    path('seleccionar/', views.seleccionar_institucion_view, name='seleccionar'),
    path('cambiar/', views.cambiar_institucion_view, name='cambiar'),
]
