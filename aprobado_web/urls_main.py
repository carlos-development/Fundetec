"""URLConf publico de financiacion educativa."""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from .urls_common import common_urlpatterns
from .views import health_check_view, portal_entrypoint_view


urlpatterns = [
    *common_urlpatterns,

    path("health/", health_check_view, name="health"),

    # Continuacion del flujo educativo para usuarios
    path(
        "financiacion-educativa/",
        include("financiacion_educativa.web.urls"),
    ),

    # API institucional de financiacion educativa
    path(
        "api/v1/financiacion-educativa/",
        include("financiacion_educativa.api.urls"),
    ),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="api-schema"),

    # Entrada institucional; las solicitudes continuan desde una invitacion.
    path("", portal_entrypoint_view, name="home"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
