from django.contrib import admin
from django.db import transaction

from .models import CredencialAPIInstitucion, Institucion, MembresiaInstitucion
from .services.membresias import (
    activar_membresia,
    cambiar_rol_membresia,
    crear_membresia,
    desactivar_membresia,
)


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


@admin.register(MembresiaInstitucion)
class MembresiaInstitucionAdmin(admin.ModelAdmin):
    list_display = (
        'usuario',
        'institucion',
        'rol',
        'activa',
        'activado_en',
        'desactivado_en',
        'creada_en',
    )
    list_filter = ('rol', 'activa', 'institucion')
    search_fields = (
        'usuario__username',
        'usuario__email',
        'institucion__nombre_comercial',
        'institucion__razon_social',
    )
    list_select_related = ('usuario', 'institucion', 'creado_por')
    readonly_fields = (
        'id',
        'creado_por',
        'invitado_en',
        'activado_en',
        'desactivado_en',
        'creada_en',
        'actualizada_en',
    )

    def get_readonly_fields(self, request, obj=None):
        campos = list(self.readonly_fields)
        if obj:
            campos.extend(['usuario', 'institucion'])
        return tuple(campos)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        if not change:
            guardada = crear_membresia(
                usuario=obj.usuario,
                institucion=obj.institucion,
                rol=obj.rol,
                actor=request.user,
                activa=obj.activa,
            )
        else:
            original = MembresiaInstitucion.objects.get(pk=obj.pk)
            guardada = original
            if original.rol != obj.rol:
                guardada = cambiar_rol_membresia(
                    membresia=guardada,
                    rol=obj.rol,
                    actor=request.user,
                )
            if original.activa != obj.activa:
                operacion = (
                    activar_membresia if obj.activa else desactivar_membresia
                )
                guardada = operacion(
                    membresia=guardada,
                    actor=request.user,
                )
        for campo in self.model._meta.concrete_fields:
            setattr(obj, campo.attname, getattr(guardada, campo.attname))
        obj._state.adding = False
        obj._state.db = guardada._state.db

    def has_delete_permission(self, request, obj=None):
        return False


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
