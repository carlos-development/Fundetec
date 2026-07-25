from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import (
    CondicionesFinancieras,
    Consentimiento,
    DocumentoFinanciacion,
    EventoInvitacionContinuacion,
    HistorialEstadoSolicitud,
    InvitacionContinuacionSolicitud,
    ParticipanteFinanciacion,
    RegistroIdempotenciaSolicitud,
    RolParticipanteFinanciacion,
    SolicitudFinanciacionEducativa,
    VersionTerminosFinanciacion,
)
from .choices import EstadoVersionTerminos
from .services.terminos import publicar_version_terminos, retirar_version_terminos


class RolParticipanteInline(admin.TabularInline):
    model = RolParticipanteFinanciacion
    extra = 0
    readonly_fields = ('id', 'creado_en')


@admin.register(SolicitudFinanciacionEducativa)
class SolicitudFinanciacionEducativaAdmin(admin.ModelAdmin):
    list_display = (
        'referencia_externa',
        'institucion',
        'nombre_solicitante',
        'valor_plan',
        'plazo_meses',
        'estado',
        'usuario',
        'creada_en',
    )
    list_filter = ('estado', 'institucion', 'tipo_curso', 'creada_en')
    search_fields = (
        'referencia_externa',
        'nombres',
        'apellidos',
        'correo',
        'celular',
        'institucion__nombre_comercial',
    )
    readonly_fields = (
        'id',
        'estado',
        'canal_origen',
        'correlation_id',
        'ip_origen',
        'user_agent_origen',
        'creada_en',
        'actualizada_en',
    )

    @admin.display(description='Solicitante')
    def nombre_solicitante(self, obj):
        return f'{obj.nombres} {obj.apellidos}'.strip()

    def has_add_permission(self, request):
        return False


@admin.register(RegistroIdempotenciaSolicitud)
class RegistroIdempotenciaSolicitudAdmin(admin.ModelAdmin):
    list_display = ('institucion', 'solicitud', 'creada_en', 'ultimo_reuso_en')
    list_filter = ('institucion', 'creada_en')
    search_fields = ('solicitud__referencia_externa', 'clave_hash', 'payload_hash')
    readonly_fields = tuple(
        field.name for field in RegistroIdempotenciaSolicitud._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InvitacionContinuacionSolicitud)
class InvitacionContinuacionSolicitudAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'solicitud',
        'estado',
        'proposito',
        'vence_en',
        'consumida_en',
        'creada_en',
    )
    list_filter = ('estado', 'proposito', 'creada_en', 'vence_en')
    search_fields = ('id', 'solicitud__referencia_externa')
    readonly_fields = tuple(
        field.name for field in InvitacionContinuacionSolicitud._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EventoInvitacionContinuacion)
class EventoInvitacionContinuacionAdmin(admin.ModelAdmin):
    list_display = ('invitacion', 'tipo', 'actor', 'creado_en')
    list_filter = ('tipo', 'creado_en')
    search_fields = ('invitacion__id', 'invitacion__solicitud__referencia_externa')
    readonly_fields = tuple(
        field.name for field in EventoInvitacionContinuacion._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VersionTerminosFinanciacion)
class VersionTerminosFinanciacionAdmin(admin.ModelAdmin):
    list_display = (
        'titulo',
        'version',
        'tipo',
        'obligatorio',
        'estado',
        'vigente_desde',
        'publicada_en',
    )
    list_filter = ('estado', 'tipo', 'obligatorio')
    search_fields = ('titulo', 'version', 'hash_integridad')
    actions = ('publicar_seleccionadas', 'retirar_seleccionadas')

    def get_readonly_fields(self, request, obj=None):
        base = (
            'id',
            'hash_integridad',
            'estado',
            'publicada_en',
            'vigente_desde',
            'retirada_en',
            'creada_en',
            'actualizada_en',
        )
        if obj and obj.estado != EstadoVersionTerminos.DRAFT:
            return tuple(field.name for field in self.model._meta.fields)
        return base

    @admin.action(description='Publicar versiones seleccionadas')
    def publicar_seleccionadas(self, request, queryset):
        publicadas = 0
        for version in queryset:
            try:
                publicar_version_terminos(version=version)
            except ValidationError:
                continue
            publicadas += 1
        self.message_user(request, f'Versiones publicadas: {publicadas}.')

    @admin.action(description='Retirar versiones seleccionadas')
    def retirar_seleccionadas(self, request, queryset):
        retiradas = 0
        for version in queryset:
            try:
                retirar_version_terminos(version=version)
            except ValidationError:
                continue
            retiradas += 1
        self.message_user(request, f'Versiones retiradas: {retiradas}.')

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ParticipanteFinanciacion)
class ParticipanteFinanciacionAdmin(admin.ModelAdmin):
    list_display = (
        'nombre_completo',
        'tipo_documento',
        'numero_documento',
        'solicitud',
        'responsable_contractual',
        'fecha_nacimiento_confirmada',
    )
    list_filter = (
        'responsable_contractual',
        'fecha_nacimiento_confirmada',
        'tipo_documento',
    )
    search_fields = (
        'nombres',
        'apellidos',
        'numero_documento',
        'solicitud__referencia_externa',
    )
    readonly_fields = ('id', 'creado_en', 'actualizado_en')
    inlines = (RolParticipanteInline,)


@admin.register(RolParticipanteFinanciacion)
class RolParticipanteFinanciacionAdmin(admin.ModelAdmin):
    list_display = ('participante', 'rol', 'solicitud', 'creado_en')
    list_filter = ('rol', 'creado_en')
    search_fields = (
        'participante__nombres',
        'participante__apellidos',
        'participante__numero_documento',
        'solicitud__referencia_externa',
    )
    readonly_fields = ('id', 'creado_en')


@admin.register(Consentimiento)
class ConsentimientoAdmin(admin.ModelAdmin):
    list_display = (
        'tipo',
        'version_texto',
        'solicitud',
        'participante',
        'usuario',
        'aceptado_en',
    )
    list_filter = ('tipo', 'version_texto', 'aceptado_en')
    search_fields = (
        'solicitud__referencia_externa',
        'participante__numero_documento',
        'usuario__email',
        'evidencia_hash',
    )
    readonly_fields = (
        'id',
        'solicitud',
        'participante',
        'usuario',
        'tipo',
        'version_texto',
        'aceptado_en',
        'ip_address',
        'user_agent',
        'evidencia_hash',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentoFinanciacion)
class DocumentoFinanciacionAdmin(admin.ModelAdmin):
    list_display = (
        'tipo',
        'solicitud',
        'participante',
        'estado_validacion',
        'origen_captura',
        'cargado_en',
    )
    list_filter = ('tipo', 'estado_validacion', 'origen_captura', 'cargado_en')
    search_fields = (
        'solicitud__referencia_externa',
        'participante__numero_documento',
        'nombre_original',
        'sha256',
    )
    readonly_fields = (
        'id',
        'sha256',
        'resultado_procesamiento',
        'nivel_confianza',
        'cargado_en',
        'actualizado_en',
    )


@admin.register(CondicionesFinancieras)
class CondicionesFinancierasAdmin(admin.ModelAdmin):
    list_display = (
        'solicitud',
        'valor_financiado',
        'plazo_meses',
        'tasa_interes_mensual',
        'tasa_comision',
        'valor_cuota_estimada',
        'version_regla',
        'fecha_calculo',
    )
    list_filter = ('version_regla', 'metodo_calculo', 'moneda', 'fecha_calculo')
    search_fields = ('solicitud__referencia_externa', 'solicitud__institucion__nombre_comercial')
    readonly_fields = tuple(field.name for field in CondicionesFinancieras._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HistorialEstadoSolicitud)
class HistorialEstadoSolicitudAdmin(admin.ModelAdmin):
    list_display = (
        'solicitud',
        'estado_anterior',
        'estado_nuevo',
        'actor',
        'creado_en',
    )
    list_filter = ('estado_nuevo', 'creado_en')
    search_fields = (
        'solicitud__referencia_externa',
        'motivo',
        'actor__email',
    )
    readonly_fields = tuple(field.name for field in HistorialEstadoSolicitud._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
