from django.contrib import admin
from .models import ConfiguracionAnexoMatriculaEducativa
from .services.anexos_matricula import PERFILES


@admin.register(ConfiguracionAnexoMatriculaEducativa)
class AnexoInstitucionalAdmin(admin.ModelAdmin):
    list_display = ('institucion', 'perfil', 'version_anexo', 'activa', 'actualizada_por', 'actualizada_en')
    raw_id_fields = ('institucion',)
    readonly_fields = ('version_anexo', 'actualizada_por', 'actualizada_en')

    def version_anexo(self, obj):
        perfil = PERFILES.get(getattr(obj, 'perfil', ''))
        return perfil.version if perfil else 'Sin configurar'

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('financiacion_educativa.gestionar_anexos_matricula')

    def has_add_permission(self, request):
        return self.has_view_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.actualizada_por = request.user
        super().save_model(request, obj, form, change)
