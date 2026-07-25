"""
URLConf del dominio principal (aprobado.com.co).
Scope principal: Libranza + paneles internos.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from .urls_common import common_urlpatterns
from .views import portal_entrypoint_view


urlpatterns = [
    *common_urlpatterns,

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

    # Producto principal
    path("libranza/", include("usuarios.urls_libranza")),

    # Roles administrativos internos
    path("gestion/", include("gestion_creditos.urls_gestion")),
    path("pagador/", include("gestion_creditos.urls_pagador")),
    path("ejecutivos/", include("gestion_creditos.urls_ejecutivos")),
    path("asesores/", include("gestion_creditos.urls_asesores")),

    # Billetera
    path("billetera/", include("gestion_creditos.urls_billetera")),
    path("inversionista/", include("gestion_creditos.urls_inversionista")),

    # Inicio segun host
    path("", portal_entrypoint_view, name="home"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
