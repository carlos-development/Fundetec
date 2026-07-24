from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib import messages

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    ContractorBranding,
    ContractorOrganization,
    ContractorProductConfig,
    ContractorProfile,
    ConfiguracionPortalContratistas,
    InformacionLaboralSolicitudContratista,
)
from contractors.services.revision import (
    aprobar_documento_solicitud,
    marcar_solicitud_en_revision,
    rechazar_documento_solicitud,
    rechazar_solicitud_contratista,
)


class AdminContratistasBase(admin.ModelAdmin):
    readonly_fields = ('created_at', 'updated_at')
    actions = None

    def get_actions(self, request):
        acciones = super().get_actions(request)
        acciones.pop('delete_selected', None)
        return acciones


@admin.register(ConfiguracionPortalContratistas)
class ConfiguracionPortalContratistasAdmin(AdminContratistasBase):
    list_display = ('nombre_visible', 'host', 'slug', 'activo', 'monto_minimo', 'monto_maximo', 'updated_at')
    search_fields = ('nombre_visible', 'host', 'slug', 'correo_soporte')
    list_filter = ('activo',)
    fieldsets = (
        ('Portal unico', {
            'fields': ('nombre_visible', 'host', 'slug', 'activo'),
        }),
        ('Marca', {
            'fields': ('logo', 'color_primario', 'color_secundario', 'correo_soporte', 'texto_landing'),
        }),
        ('Condiciones financieras', {
            'fields': (
                'monto_minimo',
                'monto_maximo',
                'plazo_minimo_meses',
                'plazo_maximo_meses',
                'tasa_mensual',
                'tasa_comision',
                'comision_fija',
                'tasa_iva',
            ),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(ContractorOrganization)
class OrganizacionContratistaAdmin(AdminContratistasBase):
    list_display = ('name', 'slug', 'subdomain', 'is_active', 'created_at')
    search_fields = ('name', 'slug', 'subdomain')
    list_filter = ('is_active',)
    fieldsets = (
        ('Identificacion', {
            'fields': ('name', 'slug', 'subdomain'),
        }),
        ('Estado', {
            'fields': ('is_active',),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(ContractorBranding)
class MarcaContratistaAdmin(AdminContratistasBase):
    list_display = ('display_name', 'organization', 'is_active', 'support_email', 'updated_at')
    search_fields = ('display_name', 'organization__name', 'support_email')
    list_filter = ('is_active', 'organization')
    fieldsets = (
        ('Organizacion', {
            'fields': ('organization', 'is_active'),
        }),
        ('Marca', {
            'fields': ('display_name', 'logo', 'primary_color', 'secondary_color'),
        }),
        ('Contenido publico', {
            'fields': ('landing_copy', 'support_email'),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(ContractorProductConfig)
class ConfiguracionProductoContratistaAdmin(AdminContratistasBase):
    list_display = (
        'organization',
        'product_type',
        'min_amount',
        'max_amount',
        'min_term_months',
        'max_term_months',
        'monthly_rate',
        'is_active',
    )
    search_fields = ('organization__name', 'organization__subdomain', 'product_type')
    list_filter = ('is_active', 'product_type', 'allows_second_credit', 'allows_portfolio_takeover')
    fieldsets = (
        ('Organizacion y producto', {
            'fields': ('organization', 'product_type', 'is_active'),
        }),
        ('Limites de simulacion', {
            'fields': ('min_amount', 'max_amount', 'min_term_months', 'max_term_months'),
        }),
        ('Condiciones financieras', {
            'fields': ('monthly_rate', 'commission_rate', 'commission_amount', 'vat_rate'),
        }),
        ('Reglas habilitadas', {
            'fields': ('allows_second_credit', 'allows_portfolio_takeover'),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(ContractorProfile)
class PerfilContratistaAdmin(AdminContratistasBase):
    list_display = ('user', 'organization', 'role', 'is_active', 'created_at')
    search_fields = ('user__username', 'user__email', 'organization__name', 'organization__subdomain')
    list_filter = ('is_active', 'role', 'organization')
    fieldsets = (
        ('Usuario y organizacion', {
            'fields': ('user', 'organization', 'role'),
        }),
        ('Estado', {
            'fields': ('is_active',),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(ContractorApplication)
class PreSolicitudContratistaAdmin(AdminContratistasBase):
    actions = ('accion_marcar_en_revision', 'accion_rechazar_solicitud')
    list_display = (
        'document_number',
        'nombre_solicitante',
        'usuario',
        'organization',
        'status',
        'escenario_credito',
        'requested_amount',
        'term_months',
        'created_at',
    )
    search_fields = (
        'document_number',
        'first_name',
        'last_name',
        'email',
        'phone',
        'usuario__username',
        'usuario__email',
        'organization__name',
        'organization__subdomain',
    )
    list_filter = ('status', 'escenario_credito', 'organization', 'accepted_terms', 'created_at')
    readonly_fields = (
        'created_at',
        'updated_at',
        'source_subdomain',
        'ip_address',
        'user_agent',
        'simulation_payload',
        'revisado_en',
        'revisado_por',
    )
    fieldsets = (
        ('Organizacion y estado', {
            'fields': ('organization', 'configuracion_portal', 'product_config', 'usuario', 'status', 'escenario_credito', 'credito'),
        }),
        ('Solicitud', {
            'fields': ('requested_amount', 'term_months', 'estimated_monthly_payment', 'accepted_terms'),
        }),
        ('Solicitante', {
            'fields': (
                'document_type',
                'document_number',
                'first_name',
                'last_name',
                'phone',
                'email',
                'address',
            ),
        }),
        ('Trazabilidad', {
            'fields': ('source_subdomain', 'ip_address', 'user_agent', 'simulation_payload'),
        }),
        ('Revision interna', {
            'fields': ('revisado_en', 'revisado_por', 'notas_revision'),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def get_actions(self, request):
        acciones = super().get_actions(request)
        if not request.user.has_perm('contractors.can_review_contractor_application'):
            acciones.pop('accion_marcar_en_revision', None)
            acciones.pop('accion_rechazar_solicitud', None)
        return acciones

    @admin.display(description='Solicitante')
    def nombre_solicitante(self, obj):
        return f'{obj.first_name} {obj.last_name}'.strip()

    @admin.action(description='Marcar seleccionadas en revision')
    def accion_marcar_en_revision(self, request, queryset):
        procesadas = 0
        for solicitud in queryset:
            try:
                marcar_solicitud_en_revision(
                    solicitud,
                    request.user,
                    observacion='Marcada en revision desde admin.',
                )
                procesadas += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
        self.message_user(request, f'{procesadas} pre-solicitudes marcadas en revision.')

    @admin.action(description='Rechazar seleccionadas')
    def accion_rechazar_solicitud(self, request, queryset):
        procesadas = 0
        for solicitud in queryset:
            try:
                rechazar_solicitud_contratista(
                    solicitud,
                    request.user,
                    motivo='Rechazada desde admin.',
                )
                procesadas += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
        self.message_user(request, f'{procesadas} pre-solicitudes rechazadas.')


@admin.register(ContractorApplicationDocument)
class DocumentoSolicitudContratistaAdmin(AdminContratistasBase):
    actions = ('accion_aprobar_documento', 'accion_rechazar_documento')
    list_display = (
        'original_filename',
        'document_type',
        'estado_revision',
        'organizacion',
        'application',
        'uploaded_at',
    )
    search_fields = (
        'original_filename',
        'application__document_number',
        'application__first_name',
        'application__last_name',
        'application__organization__name',
        'application__organization__subdomain',
    )
    list_filter = (
        'document_type',
        'status',
        'application__organization',
        'uploaded_at',
    )
    readonly_fields = (
        'uploaded_at',
        'reviewed_at',
        'original_filename',
        'content_type',
        'file_size',
        'reviewed_by',
    )
    fieldsets = (
        ('Solicitud', {
            'fields': ('application',),
        }),
        ('Documento', {
            'fields': ('document_type', 'file', 'original_filename', 'content_type', 'file_size'),
        }),
        ('Revision', {
            'fields': ('status', 'reviewed_at', 'reviewed_by', 'review_notes'),
        }),
        ('Auditoria', {
            'fields': ('uploaded_at',),
        }),
    )

    @admin.display(description='Organizacion')
    def organizacion(self, obj):
        return obj.application.organization

    @admin.display(description='Estado')
    def estado_revision(self, obj):
        return obj.get_status_display()

    def get_actions(self, request):
        acciones = super().get_actions(request)
        if not request.user.has_perm('contractors.can_review_contractor_document'):
            acciones.pop('accion_aprobar_documento', None)
            acciones.pop('accion_rechazar_documento', None)
        return acciones

    @admin.action(description='Aprobar documentos seleccionados')
    def accion_aprobar_documento(self, request, queryset):
        procesados = 0
        for documento in queryset:
            try:
                aprobar_documento_solicitud(
                    documento,
                    request.user,
                    observacion='Aprobado desde admin.',
                )
                procesados += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
        self.message_user(request, f'{procesados} documentos aprobados.')

    @admin.action(description='Rechazar documentos seleccionados')
    def accion_rechazar_documento(self, request, queryset):
        procesados = 0
        for documento in queryset:
            try:
                rechazar_documento_solicitud(
                    documento,
                    request.user,
                    motivo='Rechazado desde admin.',
                )
                procesados += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
        self.message_user(request, f'{procesados} documentos rechazados.')


@admin.register(InformacionLaboralSolicitudContratista)
class InformacionLaboralSolicitudContratistaAdmin(AdminContratistasBase):
    list_display = (
        'solicitud',
        'cargo',
        'tipo_contrato',
        'empresa',
        'empresa_contratante_nombre',
        'fecha_inicio_contrato',
        'fecha_fin_contrato',
        'updated_at',
    )
    search_fields = (
        'solicitud__document_number',
        'solicitud__first_name',
        'solicitud__last_name',
        'cargo',
        'empresa__nombre',
        'empresa__nit',
        'empresa_contratante_nombre',
        'empresa_contratante_nit',
        'pagador_nombre',
        'pagador_email',
    )
    list_filter = (
        'tipo_contrato',
        'empresa',
        'empresa_contratante_nombre',
        'fecha_inicio_contrato',
        'fecha_fin_contrato',
        'created_at',
    )
    fieldsets = (
        ('Solicitud', {
            'fields': ('solicitud',),
        }),
        ('Contrato', {
            'fields': (
                'cargo',
                'tipo_contrato',
                'fecha_inicio_contrato',
                'fecha_fin_contrato',
                'valor_total_contrato',
                'valor_pagado_contrato',
                'valor_pendiente_cobrar',
            ),
        }),
        ('Empresa contratante y pagador', {
            'fields': (
                'empresa',
                'empresa_contratante_nombre',
                'empresa_contratante_nit',
                'pagador_nombre',
                'pagador_email',
                'pagador_telefono',
            ),
        }),
        ('Observaciones', {
            'fields': ('observaciones',),
        }),
        ('Auditoria', {
            'fields': ('created_at', 'updated_at'),
        }),
    )
