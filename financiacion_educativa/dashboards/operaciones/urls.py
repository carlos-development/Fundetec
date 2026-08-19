from django.urls import path

from . import views


app_name = 'operaciones'

urlpatterns = [
    path('', views.inicio_view, name='inicio'),
    path('solicitudes/', views.solicitudes_view, name='solicitudes'),
    path('bandejas/', views.bandejas_view, name='bandejas'),
    path('instituciones/', views.instituciones_view, name='instituciones'),
    path(
        'solicitudes/<uuid:application_id>/',
        views.solicitud_detalle_view,
        name='solicitud-detalle',
    ),
]
