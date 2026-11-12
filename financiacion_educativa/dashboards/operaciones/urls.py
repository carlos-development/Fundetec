from django.urls import path

from . import views
from financiacion_educativa.web import recaudos


app_name = 'operaciones'

urlpatterns = [
    path('recaudos/', recaudos.bandeja_view, name='recaudos'),
    path('recaudos/evidencias/<uuid:archivo_id>/', recaudos.evidencia_propia_view, name='evidencia-propia'),
    path('recaudos/<uuid:reporte_id>/', recaudos.detalle_operativo_view, name='recaudo-detalle'),
    path('recaudos/<uuid:reporte_id>/aplicar-cuota/', recaudos.aplicar_cuota_view, name='recaudo-aplicar-cuota'),
    path('recaudos/<uuid:reporte_id>/confirmar/', recaudos.confirmar_view, name='recaudo-confirmar'),
    path('recaudos/<uuid:reporte_id>/rechazar/', recaudos.rechazar_view, name='recaudo-rechazar'),
    path('recaudos/<uuid:reporte_id>/evidencia/', recaudos.evidencia_view, name='recaudo-evidencia'),
    path('recaudos/<uuid:reporte_id>/soporte/', recaudos.soporte_operativo_view, name='recaudo-soporte'),
    path('', views.inicio_view, name='inicio'),
    path('solicitudes/', views.solicitudes_view, name='solicitudes'),
    path('bandejas/', views.bandejas_view, name='bandejas'),
    path('instituciones/', views.instituciones_view, name='instituciones'),
    path('obligaciones/', views.obligaciones_view, name='obligaciones'),
    path('obligaciones/<uuid:application_id>/', views.obligacion_detalle_view, name='obligacion-detalle'),
    path('obligaciones/<uuid:application_id>/registrar-pago-recibido/', recaudos.registrar_pago_recibido_view, name='registrar-pago-recibido'),
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
