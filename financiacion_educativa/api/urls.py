from django.urls import path

from .views import SolicitudDetalleAPIView, SolicitudListCreateAPIView


app_name = 'financiacion_educativa_api'

urlpatterns = [
    path(
        'solicitudes/',
        SolicitudListCreateAPIView.as_view(),
        name='solicitud-crear',
    ),
    path(
        'solicitudes/<uuid:application_id>/',
        SolicitudDetalleAPIView.as_view(),
        name='solicitud-detalle',
    ),
]
