from django.contrib import admin

from .models import CredencialAPIInstitucion, Institucion


@admin.register(Institucion)
class InstitucionAdmin(admin.ModelAdmin):
    list_display = (
        'nombre_comercial',
        'razon_social',
        'tipo_identificacion_tributaria',
        'numero_identificacion_tributaria',
        'activa',
        'creada_en',
    )
    list_filter = ('activa', 'tipo_identificacion_tributaria', 'creada_en')
    search_fields = (
        'nombre_comercial',
        'razon_social',
        'numero_identificacion_tributaria',
    )
    readonly_fields = ('id', 'creada_en', 'actualizada_en')


@admin.register(CredencialAPIInstitucion)
class CredencialAPIInstitucionAdmin(admin.ModelAdmin):
    list_display = (
        'nombre',
        'institucion',
        'prefijo_clave',
        'activa',
        'expira_en',
        'ultimo_uso_en',
    )
    list_filter = ('activa', 'institucion', 'creada_en')
    search_fields = ('nombre', 'prefijo_clave', 'institucion__nombre_comercial')
    exclude = ('secreto_hash',)
    readonly_fields = (
        'id',
        'institucion',
        'nombre',
        'prefijo_clave',
        'alcances',
        'activa',
        'expira_en',
        'ultimo_uso_en',
        'creada_en',
        'actualizada_en',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
