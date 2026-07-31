"""
Rutas compartidas entre dominios/subdominios.
"""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView


common_urlpatterns = [
    # Admin Django
    path("admin/", admin.site.urls),

    # Autenticacion provisional; no forma parte del flujo educativo.
    path("accounts/", include("allauth.urls")),

    # Legales
    path(
        "privacidad/",
        TemplateView.as_view(template_name="legal/politica_privacidad.html"),
        name="politica_privacidad",
    ),
    path(
        "terminos/",
        TemplateView.as_view(template_name="legal/terminos_condiciones.html"),
        name="terminos_condiciones",
    ),
]
