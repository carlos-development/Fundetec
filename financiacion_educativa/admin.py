from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import (
    ConfiguracionFinancieraEducativa,
    CondicionesFinancieras,
    Consentimiento,
    CuotaAmortizacionEducativa,
    DocumentoFinanciacion,
    EntregaInvitacionContinuacion,
    EvidenciaMatricula,
    EventoInvitacionContinuacion,
    EventoParticipanteFinanciacion,
    HistorialEstadoSolicitud,
    InvitacionContinuacionSolicitud,
    ParticipanteFinanciacion,
    RegistroIdempotenciaSolicitud,
    RolParticipanteFinanciacion,
    SolicitudFinanciacionEducativa,
    VersionTerminosFinanciacion,
)
from .choices import (
    EstadoConfiguracionFinanciera,
    EstadoVersionTerminos,
    MotivoRechazoDocumento,
    OrigenEntregaInvitacion,
)
from .services.configuracion_financiera import (
    activar_configuracion_financiera,
    retirar_configuracion_financiera,
)
from .services.documentos import revisar_documento
from .services.matricula import revisar_evidencia_matricula
from .services.terminos import publicar_version_terminos, retirar_version_terminos
from .services.orquestacion import (
    programar_invitacion_inicial,
    reemitir_invitacion_orquestada,
    revocar_invitacion_orquestada,
)


class RolParticipanteInline(admin.TabularInline):
    model = RolParticipanteFinanciacion
    extra = 0
    can_delete = False
    readonly_fields = tuple(
        field.name for field in RolParticipanteFinanciacion._meta.fields
    )

    def has_add_permission(self, request, obj):
        return False


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
    actions = (
        'programar_invitacion_inicial_seleccionadas',
        'reemitir_invitacion_seleccionadas',
        'revocar_invitacion_seleccionadas',
    )

    @admin.display(description='Solicitante')
    def nombre_solicitante(self, obj):
        return f'{obj.nombres} {obj.apellidos}'.strip()

    def has_add_permission(self, request):
        return False

    @admin.action(description='Programar invitacion inicial')
    def programar_invitacion_inicial_seleccionadas(self, request, queryset):
        programadas = 0
        for solicitud in queryset:
            try:
                resultado = programar_invitacion_inicial(
                    solicitud=solicitud,
                    actor=request.user,
                )
            except ValidationError:
                continue
            programadas += int(resultado.creada)
        self.message_user(request, f'Invitaciones programadas: {programadas}.')

    @admin.action(description='Reemitir invitacion de continuacion')
    def reemitir_invitacion_seleccionadas(self, request, queryset):
        reemitidas = 0
        for solicitud in queryset:
            try:
                reemitir_invitacion_orquestada(
                    solicitud=solicitud,
                    origen=OrigenEntregaInvitacion.MANUAL_REISSUE,
                    actor=request.user,
                )
            except ValidationError:
                continue
            reemitidas += 1
        self.message_user(request, f'Invitaciones reemitidas: {reemitidas}.')

    @admin.action(description='Revocar invitacion de continuacion')
    def revocar_invitacion_seleccionadas(self, request, queryset):
        revocadas = 0
        for solicitud in queryset:
            try:
                invitacion = revocar_invitacion_orquestada(
                    solicitud=solicitud,
                    actor=request.user,
                )
            except ValidationError:
                continue
            revocadas += int(invitacion is not None)
        self.message_user(request, f'Invitaciones revocadas: {revocadas}.')


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
    CuotaAmortizacionEducativa,


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


@admin.register(EntregaInvitacionContinuacion)
class EntregaInvitacionContinuacionAdmin(admin.ModelAdmin):
    list_display = (
        'solicitud',
        'secuencia',
        'canal',
        'origen',
        'estado',
        'intentos',
        'programada_en',
        'enviada_en',
    )
    list_filter = ('estado', 'canal', 'origen', 'programada_en')
    search_fields = ('solicitud__referencia_externa',)
    fields = (
        'id',
        'solicitud',
        'invitacion',
        'secuencia',
        'canal',
        'origen',
        'estado',
        'reemplaza_a',
        'intentos',
        'codigo_ultimo_error',
        'programada_en',
        'iniciada_en',
        'enviada_en',
        'fallida_en',
        'cancelada_en',
        'creada_por',
        'creada_en',
        'actualizada_en',
    )
    readonly_fields = fields

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
        'identificacion_enmascarada',
        'solicitud',
        'identidad_verificada',
        'relacion_verificada',
    )
    list_filter = (
        'identidad_verificada',
        'relacion_verificada',
        'tipo_documento',
    )
    search_fields = (
        'nombres',
        'apellidos',
        'solicitud__referencia_externa',
    )
    readonly_fields = tuple(
        field.name for field in ParticipanteFinanciacion._meta.fields
    )
    inlines = (RolParticipanteInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RolParticipanteFinanciacion)
class RolParticipanteFinanciacionAdmin(admin.ModelAdmin):
    list_display = ('participante', 'rol', 'solicitud', 'declarado_por', 'creado_en')
    list_filter = ('rol', 'creado_en')
    search_fields = (
        'participante__nombres',
        'participante__apellidos',
        'solicitud__referencia_externa',
    )
    readonly_fields = tuple(
        field.name for field in RolParticipanteFinanciacion._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EventoParticipanteFinanciacion)
class EventoParticipanteFinanciacionAdmin(admin.ModelAdmin):
    list_display = ('participante', 'tipo', 'actor', 'creado_en')
    list_filter = ('tipo', 'creado_en')
    readonly_fields = tuple(
        field.name for field in EventoParticipanteFinanciacion._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
        'estado_escaneo',
        'estado_validacion',
        'activo',
        'origen_captura',
        'cargado_en',
    )
    list_filter = (
        'tipo',
        'estado_escaneo',
        'estado_validacion',
        'activo',
        'origen_captura',
        'cargado_en',
    )
    search_fields = (
        'solicitud__referencia_externa',
    )
    readonly_fields = (
        'id',
        'solicitud',
        'participante',
        'tipo',
        'archivo_privado',
        'referencia_almacenamiento',
        'nombre_seguro',
        'nombre_original',
        'content_type',
        'tamano_bytes',
        'cargado_por',
        'estado_escaneo',
        'escaneado_en',
        'referencia_escaneo',
        'estado_validacion',
        'revisado_por',
        'revisado_en',
        'motivo_rechazo',
        'observacion_revision',
        'activo',
        'reemplaza_a',
        'origen_captura',
        'sha256',
        'resultado_procesamiento',
        'nivel_confianza',
        'cargado_en',
        'actualizado_en',
    )
    fields = readonly_fields
    actions = ('aceptar_seleccionados', 'rechazar_tipo_incorrecto')

    @admin.display(description='Archivo privado')
    def archivo_privado(self, obj):
        return 'Almacenado' if obj.archivo else 'Referencia privada externa'

    @admin.action(description='Aceptar documentos seleccionados')
    def aceptar_seleccionados(self, request, queryset):
        aceptados = 0
        for documento in queryset:
            try:
                revisar_documento(
                    documento=documento,
                    actor=request.user,
                    aceptar=True,
                )
            except ValidationError:
                continue
            aceptados += 1
        self.message_user(request, f'Documentos aceptados: {aceptados}.')

    @admin.action(description='Rechazar por tipo documental incorrecto')
    def rechazar_tipo_incorrecto(self, request, queryset):
        rechazados = 0
        for documento in queryset:
            try:
                revisar_documento(
                    documento=documento,
                    actor=request.user,
                    aceptar=False,
                    motivo_rechazo=MotivoRechazoDocumento.WRONG_DOCUMENT,
                )
            except ValidationError:
                continue
            rechazados += 1
        self.message_user(request, f'Documentos rechazados: {rechazados}.')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EvidenciaMatricula)
class EvidenciaMatriculaAdmin(admin.ModelAdmin):
    list_display = (
        'solicitud',
        'programa_curso',
        'periodo_academico',
        'estado',
        'creada_en',
    )
    list_filter = ('estado', 'periodo_academico', 'creada_en')
    search_fields = ('solicitud__referencia_externa',)
    readonly_fields = tuple(
        field.name for field in EvidenciaMatricula._meta.fields
    )
    actions = ('aceptar_seleccionadas', 'rechazar_soporte_incorrecto')

    @admin.action(description='Aceptar evidencias seleccionadas')
    def aceptar_seleccionadas(self, request, queryset):
        aceptadas = 0
        for evidencia in queryset:
            try:
                revisar_evidencia_matricula(
                    evidencia=evidencia,
                    actor=request.user,
                    aceptar=True,
                )
            except ValidationError:
                continue
            aceptadas += 1
        self.message_user(request, f'Evidencias aceptadas: {aceptadas}.')

    @admin.action(description='Rechazar por soporte incorrecto')
    def rechazar_soporte_incorrecto(self, request, queryset):
        rechazadas = 0
        for evidencia in queryset:
            try:
                revisar_evidencia_matricula(
                    evidencia=evidencia,
                    actor=request.user,
                    aceptar=False,
                    motivo_rechazo=MotivoRechazoDocumento.WRONG_DOCUMENT,
                )
            except ValidationError:
                continue
            rechazadas += 1
        self.message_user(request, f'Evidencias rechazadas: {rechazadas}.')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CondicionesFinancieras)
class CondicionesFinancierasAdmin(admin.ModelAdmin):
    list_display = (
        'solicitud',
        'numero_version',
        'activa',
        'bloqueada',
        'es_legado',
        'valor_financiado',
        'valor_cuota_estimada',
        'version_regla',
        'fecha_calculo',
    )
    list_filter = (
        'activa',
        'bloqueada',
        'es_legado',
        'version_regla',
        'metodo_calculo',
        'moneda',
        'fecha_calculo',
    )
    search_fields = ('solicitud__referencia_externa', 'solicitud__institucion__nombre_comercial')
    readonly_fields = tuple(field.name for field in CondicionesFinancieras._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConfiguracionFinancieraEducativa)
class ConfiguracionFinancieraEducativaAdmin(admin.ModelAdmin):
    list_display = (
        'codigo',
        'version',
        'estado',
        'vigente_desde',
        'vigente_hasta',
        'originacion_visible',
        'interes_visible',
        'proveedor_fondo_garantias',
        'proveedor_seguro_vida',
    )
    list_filter = ('estado', 'moneda', 'metodo_calculo', 'vigente_desde')
    search_fields = ('codigo', 'proveedor_fondo_garantias', 'proveedor_seguro_vida')
    readonly_fields = ('id', 'creado_por', 'actualizado_por', 'creada_en', 'actualizada_en')
    actions = ('activar_seleccionadas', 'retirar_seleccionadas')

    @admin.display(description='Originacion (%)')
    def originacion_visible(self, obj):
        return f'{obj.porcentaje_originacion} %'

    @admin.display(description='Interes mensual (%)')
    def interes_visible(self, obj):
        return f'{obj.tasa_interes_mensual} %'

    def get_readonly_fields(self, request, obj=None):
        base = self.readonly_fields
        if obj and (
            obj.estado != EstadoConfiguracionFinanciera.DRAFT
            or obj.fotografias.exists()
        ):
            return tuple(field.name for field in obj._meta.fields)
        return base

    def save_model(self, request, obj, form, change):
        if not change:
            obj.creado_por = request.user
        obj.actualizado_por = request.user
        obj.full_clean()
        super().save_model(request, obj, form, change)

    @admin.action(description='Activar configuraciones seleccionadas')
    def activar_seleccionadas(self, request, queryset):
        activadas = 0
        for configuracion in queryset:
            try:
                activar_configuracion_financiera(
                    configuracion=configuracion,
                    actor=request.user,
                )
            except ValidationError:
                continue
            activadas += 1
        self.message_user(request, f'Configuraciones activadas: {activadas}.')

    @admin.action(description='Retirar configuraciones seleccionadas')
    def retirar_seleccionadas(self, request, queryset):
        retiradas = 0
        for configuracion in queryset:
            try:
                retirar_configuracion_financiera(
                    configuracion=configuracion,
                    actor=request.user,
                )
            except ValidationError:
                continue
            retiradas += 1
        self.message_user(request, f'Configuraciones retiradas: {retiradas}.')

    def has_delete_permission(self, request, obj=None):
        return bool(
            obj
            and obj.estado == EstadoConfiguracionFinanciera.DRAFT
            and not obj.fotografias.exists()
        )


@admin.register(CuotaAmortizacionEducativa)
class CuotaAmortizacionEducativaAdmin(admin.ModelAdmin):
    list_display = (
        'fotografia',
        'numero',
        'fecha_vencimiento',
        'saldo_inicial',
        'interes',
        'capital',
        'valor_cuota',
        'saldo_final',
    )
    list_filter = ('fecha_vencimiento',)
    search_fields = ('fotografia__solicitud__referencia_externa',)
    readonly_fields = tuple(
        field.name for field in CuotaAmortizacionEducativa._meta.fields
    )

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
