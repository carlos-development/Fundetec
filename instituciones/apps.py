from django.apps import AppConfig


class InstitucionesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'instituciones'

    def ready(self):
        from . import openapi  # noqa: F401
    verbose_name = 'Instituciones originadoras'
