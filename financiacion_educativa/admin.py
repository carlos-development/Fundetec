from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from .models import (
    ArtefactoContractualEducativo,
    ConfiguracionFinancieraEducativa,
    CondicionesFinancieras,
    Consentimiento,
    CuotaAmortizacionEducativa,
    DocumentoFinanciacion,
    DecisionRevisionEducativa,
    EntregaCorreoEstadoSolicitud,
    EnlaceCapturaMovil,
    EntregaInvitacionContinuacion,
    EtapaProcesoAutomatizacionEducativa,
    EvidenciaMatricula,
    EventoInvitacionContinuacion,
    EventoEnlaceCapturaMovil,
    EventoSeguridadFinanciacion,
    EventoParticipanteFinanciacion,
    HistorialEstadoSolicitud,
    IntentoEscaneoDocumento,
    InvitacionContinuacionSolicitud,
    ParticipanteFinanciacion,
    ProcesoFirmaEducativa,
    ProcesoAutomatizacionEducativa,
    ProcesamientoContenidoDocumento,
    EventoWebhookFirmaEducativa,
    ReaperturaEscaneoDocumento,
    RegistroIdempotenciaSolicitud,
    RolParticipanteFinanciacion,
    SolicitudFinanciacionEducativa,
    VersionTerminosFinanciacion,
    ValidacionIADocumento,
)
from .choices import (
    EstadoConfiguracionFinanciera,
    EstadoSolicitudFinanciacion,
    EstadoVersionTerminos,
    MotivoDecisionRevisionEducativa,
    RequisitoCorreccionEducativa,
    MotivoRechazoDocumento,
    OrigenEntregaInvitacion,
    TipoDecisionRevisionEducativa,
)
from .services.configuracion_financiera import (
    ConfiguracionFinancieraAmbigua,
    ConfiguracionFinancieraNoDisponible,
    activar_configuracion_financiera,
    retirar_configuracion_financiera,
    seleccionar_configuracion_vigente,
)
from .services.documentos import revisar_documento
from .services.escaneo_documentos import procesar_escaneo_documento
from .services.validacion_documental_ia import procesar_validacion_documental_ia
from .services.firma_zapsign import (
    FirmaEducativaError,
    enviar_pagare_educativo,
)
from .services.matricula import revisar_evidencia_matricula
from .services.terminos import publicar_version_terminos, retirar_version_terminos
from .services.orquestacion import (
    programar_invitacion_inicial,
    reemitir_invitacion_orquestada,
    revocar_invitacion_orquestada,
)
from .services.revision import decidir_solicitud


class DecisionRevisionAdminForm(forms.Form):
    tipo = forms.ChoiceField(
        label='Decision',
        choices=TipoDecisionRevisionEducativa.choices,
    )
    motivo = forms.ChoiceField(
        label='Motivo controlado',
        choices=MotivoDecisionRevisionEducativa.choices,
    )
    mensaje_solicitante = forms.CharField(
        label='Mensaje para el solicitante',
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    observacion_interna = forms.CharField(
        label='Observacion interna',
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    requisitos_pendientes = forms.MultipleChoiceField(
        label='Requisitos por corregir',
        choices=RequisitoCorreccionEducativa.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
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
    change_form_template = (
        'admin/financiacion_educativa/'
        'solicitudfinanciacioneducativa/change_form.html'
    )
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

    def get_urls(self):
        urls = super().get_urls()
        propios = [
            path(
                '<path:object_id>/revision/',
                self.admin_site.admin_view(self.revision_view),
                name='financiacion_educativa_solicitud_revision',
            ),
        ]
        return propios + urls

    def changeform_view(
        self,
        request,
        object_id=None,
        form_url='',
        extra_context=None,
    ):
        extra_context = dict(extra_context or {})
        extra_context['can_review_educational_application'] = bool(
            object_id
            and request.user.has_perm(
                'financiacion_educativa.revisar_solicitud_financiacion'
            )
        )
        return super().changeform_view(
            request,
            object_id,
            form_url,
            extra_context,
        )

    def revision_view(self, request, object_id):
        if not request.user.has_perm(
            'financiacion_educativa.revisar_solicitud_financiacion'
        ):
            raise PermissionDenied
        solicitud = get_object_or_404(
            SolicitudFinanciacionEducativa.objects.select_related(
                'institucion',
                'usuario',
            ),
            pk=object_id,
        )
        form = DecisionRevisionAdminForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                decidir_solicitud(
                    solicitud=solicitud,
                    actor=request.user,
                    **form.cleaned_data,
                )
            except ValidationError as error:
                form.add_error(None, '; '.join(error.messages))
            else:
                self.message_user(
                    request,
                    'La decision fue registrada y el correo quedo procesado.',
                )
                return redirect(
                    reverse(
                        'admin:financiacion_educativa_'
                        'solicitudfinanciacioneducativa_change',
                        args=[solicitud.pk],
                    )
                )
        fotografia = solicitud.fotografias_financieras.filter(
            activa=True,
            es_legado=False,
        ).first()
        contexto = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'original': solicitud,
            'title': 'Revision de solicitud educativa',
            'form': form,
            'documentos': solicitud.documentos.filter(
                activo=True
            ).select_related('participante').prefetch_related(
                Prefetch(
                    'validaciones_ia',
                    queryset=ValidacionIADocumento.objects.order_by('-numero'),
                    to_attr='validaciones_ia_recientes',
                )
            ),
            'fotografia': fotografia,
            'estado_revisable': (
                solicitud.estado
                == EstadoSolicitudFinanciacion.PENDING_MANUAL_REVIEW
            ),
        }
        return TemplateResponse(
            request,
            'admin/financiacion_educativa/'
            'solicitudfinanciacioneducativa/revision.html',
            contexto,
        )

    @admin.action(description='Programar invitacion inicial')
    def programar_invitacion_inicial_seleccionadas(self, request, queryset):
        programadas = 0
        for solicitud in queryset:
            try:
                resultado = programar_invitacion_inicial(
                    solicitud=solicitud,
                    actor=request.user,
                )
            except ValidationError as error:
                self.message_user(
                    request,
                    f'{solicitud.pk}: {"; ".join(error.messages)}',
                    level=messages.WARNING,
                )
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
            except ValidationError as error:
                self.message_user(
                    request,
                    f'{solicitud.pk}: {"; ".join(error.messages)}',
                    level=messages.WARNING,
                )
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


@admin.register(EnlaceCapturaMovil)
class EnlaceCapturaMovilAdmin(admin.ModelAdmin):
    list_display = (
        'solicitud',
        'persona',
        'estado',
        'estado_entrega',
        'vence_en',
        'enviada_en',
        'consumida_en',
        'creada_en',
    )
    list_filter = ('estado', 'estado_entrega', 'persona', 'creada_en')
    search_fields = ('solicitud__referencia_externa',)
    fields = (
        'id',
        'solicitud',
        'persona',
        'estado',
        'estado_entrega',
        'vence_en',
        'intentos_entrega',
        'codigo_ultimo_error',
        'entrega_iniciada_en',
        'enviada_en',
        'fallida_en',
        'revocada_en',
        'consumida_en',
        'creada_por',
        'consumida_por',
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


@admin.register(EventoEnlaceCapturaMovil)
class EventoEnlaceCapturaMovilAdmin(admin.ModelAdmin):
    list_display = ('enlace', 'tipo', 'actor', 'creado_en')
    list_filter = ('tipo', 'creado_en')
    search_fields = ('enlace__solicitud__referencia_externa',)
    fields = ('id', 'enlace', 'tipo', 'actor', 'metadata', 'creado_en')
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
    actions = (
        'solicitar_escaneo_seleccionados',
        'validar_con_ia_seleccionados',
        'aceptar_seleccionados',
        'rechazar_tipo_incorrecto',
    )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm(
            'financiacion_educativa.escanear_documento_financiacion'
        ):
            actions.pop('solicitar_escaneo_seleccionados', None)
        if not request.user.has_perm(
            'financiacion_educativa.revisar_documento_financiacion'
        ):
            actions.pop('aceptar_seleccionados', None)
            actions.pop('rechazar_tipo_incorrecto', None)
        if not request.user.has_perm(
            'financiacion_educativa.procesar_validacion_ia_documento'
        ):
            actions.pop('validar_con_ia_seleccionados', None)
        return actions

    @admin.action(description='Solicitar escaneo antivirus')
    def solicitar_escaneo_seleccionados(self, request, queryset):
        procesados = 0
        incidencias = []
        for documento in queryset:
            resultado = procesar_escaneo_documento(
                documento=documento,
                actor=request.user,
            )
            procesados += int(resultado.procesado)
            if resultado.codigo_error:
                incidencias.append(
                    f'{documento.pk}: {resultado.codigo_error}'
                )
            elif not resultado.procesado:
                incidencias.append(f'{documento.pk}: {resultado.estado}')
        self.message_user(request, f'Documentos procesados: {procesados}.')
        if incidencias:
            self.message_user(
                request,
                'No procesados: ' + '; '.join(incidencias),
                level=messages.WARNING,
            )

    @admin.action(description='Validar imagenes seguras con IA')
    def validar_con_ia_seleccionados(self, request, queryset):
        procesados = 0
        incidencias = []
        for documento in queryset:
            resultado = procesar_validacion_documental_ia(
                documento=documento,
                actor=request.user,
            )
            procesados += int(resultado.procesado)
            if resultado.codigo_error or not resultado.procesado:
                incidencias.append(
                    f'{documento.pk}: {resultado.codigo_error or resultado.estado}'
                )
        self.message_user(request, f'Validaciones IA procesadas: {procesados}.')
        if incidencias:
            self.message_user(
                request,
                'No concluyentes: ' + '; '.join(incidencias),
                level=messages.WARNING,
            )

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
            except ValidationError as error:
                self.message_user(
                    request,
                    f'{documento.pk}: {"; ".join(error.messages)}',
                    level=messages.WARNING,
                )
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
            except ValidationError as error:
                self.message_user(
                    request,
                    f'{documento.pk}: {"; ".join(error.messages)}',
                    level=messages.WARNING,
                )
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

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm(
            'financiacion_educativa.revisar_documento_financiacion'
        ):
            actions.pop('aceptar_seleccionadas', None)
            actions.pop('rechazar_soporte_incorrecto', None)
        return actions

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


@admin.register(IntentoEscaneoDocumento)
class IntentoEscaneoDocumentoAdmin(admin.ModelAdmin):
    list_display = (
        'documento',
        'numero',
        'estado',
        'origen',
        'proveedor',
        'codigo_error',
        'iniciado_en',
        'finalizado_en',
    )
    list_filter = ('estado', 'origen', 'proveedor', 'iniciado_en')
    search_fields = ('documento__solicitud__referencia_externa',)
    readonly_fields = tuple(
        field.name for field in IntentoEscaneoDocumento._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ValidacionIADocumento)
class ValidacionIADocumentoAdmin(admin.ModelAdmin):
    list_display = (
        'documento',
        'numero',
        'estado',
        'origen',
        'proveedor',
        'modelo',
        'confianza',
        'codigo_error',
        'iniciado_en',
        'finalizado_en',
    )
    list_filter = ('estado', 'origen', 'proveedor', 'iniciado_en')
    search_fields = ('documento__solicitud__referencia_externa',)
    readonly_fields = tuple(
        field.name for field in ValidacionIADocumento._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProcesamientoContenidoDocumento)
class ProcesamientoContenidoDocumentoAdmin(admin.ModelAdmin):
    list_display = (
        'documento',
        'numero',
        'estado',
        'clasificacion',
        'metodo_extraccion',
        'numero_paginas',
        'iniciado_en',
        'finalizado_en',
    )
    list_filter = ('estado', 'clasificacion', 'metodo_extraccion', 'iniciado_en')
    search_fields = ('documento__solicitud__referencia_externa', 'hash_original')
    readonly_fields = tuple(
        field.name for field in ProcesamientoContenidoDocumento._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReaperturaEscaneoDocumento)
class ReaperturaEscaneoDocumentoAdmin(admin.ModelAdmin):
    list_display = (
        'documento',
        'autorizado_por',
        'intentos_adicionales',
        'creado_en',
    )
    list_filter = ('creado_en',)
    search_fields = ('documento__solicitud__referencia_externa',)
    readonly_fields = tuple(
        field.name for field in ReaperturaEscaneoDocumento._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ArtefactoContractualEducativo)
class ArtefactoContractualEducativoAdmin(admin.ModelAdmin):
    list_display = (
        'numero_documento',
        'solicitud',
        'tipo',
        'numero_version',
        'estado',
        'vigente',
        'generado_en',
    )
    list_filter = ('tipo', 'estado', 'vigente', 'generado_en')
    search_fields = (
        'numero_documento',
        'solicitud__referencia_externa',
    )
    readonly_fields = tuple(
        field.name for field in ArtefactoContractualEducativo._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProcesoFirmaEducativa)
class ProcesoFirmaEducativaAdmin(admin.ModelAdmin):
    list_display = (
        'external_id',
        'solicitud',
        'estado',
        'intentos_envio',
        'enviado_en',
        'firmado_en',
    )
    list_filter = ('estado', 'proveedor', 'creado_en')
    search_fields = ('external_id', 'solicitud__referencia_externa')
    exclude = ('token_documento_externo',)
    readonly_fields = tuple(
        field.name
        for field in ProcesoFirmaEducativa._meta.fields
        if field.name != 'token_documento_externo'
    )
    actions = ('enviar_pagares_seleccionados',)

    @admin.action(description='Enviar pagares educativos seleccionados')
    def enviar_pagares_seleccionados(self, request, queryset):
        if not request.user.has_perm(
            'financiacion_educativa.gestionar_firma_educativa'
        ):
            raise PermissionDenied
        enviados = 0
        fallidos = 0
        for proceso in queryset:
            try:
                enviar_pagare_educativo(proceso=proceso)
            except (FirmaEducativaError, ValidationError):
                fallidos += 1
            else:
                enviados += 1
        self.message_user(
            request,
            f'Enviados: {enviados}. Fallidos: {fallidos}.',
            level=messages.WARNING if fallidos else messages.SUCCESS,
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser or request.user.has_perm(
            'financiacion_educativa.gestionar_firma_educativa'
        )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EventoWebhookFirmaEducativa)
class EventoWebhookFirmaEducativaAdmin(admin.ModelAdmin):
    list_display = (
        'tipo_evento',
        'estado',
        'codigo_resultado',
        'proceso',
        'recibido_en',
    )
    list_filter = ('tipo_evento', 'estado', 'recibido_en')
    readonly_fields = tuple(
        field.name for field in EventoWebhookFirmaEducativa._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
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
        'aplicable_hoy',
        'vigente_desde',
        'vigente_hasta',
        'originacion_visible',
        'interes_visible',
        'fondo_garantia_visible',
        'seguro_vida_deudores_visible',
    )
    list_filter = ('estado', 'moneda', 'metodo_calculo', 'vigente_desde')
    search_fields = ('codigo', 'proveedor_fondo_garantias', 'proveedor_seguro_vida')
    readonly_fields = ('id', 'creado_por', 'actualizado_por', 'creada_en', 'actualizada_en')
    actions = ('activar_seleccionadas', 'retirar_seleccionadas')

    @admin.display(description='Aplicable hoy', boolean=True)
    def aplicable_hoy(self, obj):
        hoy = timezone.localdate()
        return (
            obj.estado == EstadoConfiguracionFinanciera.ACTIVE
            and obj.vigente_desde <= hoy
            and (obj.vigente_hasta is None or obj.vigente_hasta >= hoy)
        )

    def changelist_view(self, request, extra_context=None):
        try:
            seleccionar_configuracion_vigente(
                fecha_aplicacion=timezone.localdate()
            )
        except ConfiguracionFinancieraNoDisponible:
            messages.warning(
                request,
                (
                    'No existe una politica EDU_STANDARD activa y vigente hoy. '
                    'Crea una version en borrador y activala, o ejecuta el '
                    'comando de configuracion inicial documentado.'
                ),
            )
        except ConfiguracionFinancieraAmbigua:
            messages.error(
                request,
                (
                    'Hay mas de una politica EDU_STANDARD aplicable hoy. '
                    'Retira la superposicion antes de calcular financiaciones.'
                ),
            )
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(description='Originacion (%)')
    def originacion_visible(self, obj):
        return f'{obj.porcentaje_originacion} %'

    @admin.display(description='Interes mensual (%)')
    def interes_visible(self, obj):
        return f'{obj.tasa_interes_mensual} %'

    @admin.display(description='Fondo de garantia')
    def fondo_garantia_visible(self, obj):
        return f'{obj.porcentaje_fondo_garantias} %'

    @admin.display(description='Seguro vida deudores')
    def seguro_vida_deudores_visible(self, obj):
        return f'{obj.porcentaje_seguro_vida} %'

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'proveedor_fondo_garantias' in form.base_fields:
            form.base_fields['proveedor_fondo_garantias'].label = (
                'Proveedor tecnico del fondo de garantia'
            )
        if 'proveedor_seguro_vida' in form.base_fields:
            form.base_fields['proveedor_seguro_vida'].label = (
                'Proveedor tecnico del seguro vida deudores'
            )
        return form

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


@admin.register(DecisionRevisionEducativa)
class DecisionRevisionEducativaAdmin(admin.ModelAdmin):
    list_display = (
        'solicitud',
        'tipo',
        'motivo',
        'responsable',
        'creada_en',
    )
    list_filter = ('tipo', 'motivo', 'creada_en')
    search_fields = ('solicitud__referencia_externa',)
    readonly_fields = tuple(
        field.name for field in DecisionRevisionEducativa._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EntregaCorreoEstadoSolicitud)
class EntregaCorreoEstadoSolicitudAdmin(admin.ModelAdmin):
    list_display = (
        'solicitud',
        'decision',
        'estado',
        'intentos',
        'enviada_en',
        'fallida_en',
    )
    list_filter = ('estado', 'creada_en')
    search_fields = ('solicitud__referencia_externa',)
    readonly_fields = tuple(
        field.name for field in EntregaCorreoEstadoSolicitud._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EventoSeguridadFinanciacion)
class EventoSeguridadFinanciacionAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'solicitud', 'actor', 'endpoint', 'creado_en')
    list_filter = ('tipo', 'creado_en')
    search_fields = ('solicitud__referencia_externa',)
    readonly_fields = tuple(
        field.name for field in EventoSeguridadFinanciacion._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProcesoAutomatizacionEducativa)
class ProcesoAutomatizacionEducativaAdmin(admin.ModelAdmin):
    list_display = (
        'solicitud',
        'version_expediente',
        'estado',
        'etapa_actual',
        'intento_actual',
        'actualizada_en',
    )
    list_filter = ('estado', 'etapa_actual', 'actualizada_en')
    search_fields = ('solicitud__referencia_externa',)
    readonly_fields = tuple(
        field.name for field in ProcesoAutomatizacionEducativa._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EtapaProcesoAutomatizacionEducativa)
class EtapaProcesoAutomatizacionEducativaAdmin(admin.ModelAdmin):
    list_display = (
        'proceso',
        'etapa',
        'estado',
        'intento',
        'codigo_razon',
        'finalizada_en',
    )
    list_filter = ('estado', 'etapa', 'finalizada_en')
    search_fields = ('proceso__solicitud__referencia_externa',)
    readonly_fields = tuple(
        field.name for field in EtapaProcesoAutomatizacionEducativa._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
