from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods

from .models import SolicitudFinanciacionEducativa
from .services.cancelacion_duplicados import cancelar_duplicado_sin_registro, es_superadministrador, verificar_sin_avance


class CancelarDuplicadoForm(forms.Form):
    referencia = forms.CharField(label='Referencia exacta de esta solicitud', strip=False,
        max_length=SolicitudFinanciacionEducativa._meta.get_field('referencia_externa').max_length)
    motivo = forms.CharField(label='Motivo de cancelacion', min_length=12, max_length=1000,
        widget=forms.Textarea(attrs={'rows': 4}))
    confirmar = forms.BooleanField(label='Confirmo que esta es la solicitud duplicada y que no debe continuar.')


class CancelacionDuplicadoAdminMixin:
    def get_urls(self):
        return [path('<uuid:object_id>/cancelar-duplicado/', self.admin_site.admin_view(self.cancelar_duplicado_view),
            name='financiacion_educativa_solicitud_cancelar_duplicado')] + super().get_urls()

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        contexto = dict(extra_context or {})
        obj = self.get_object(request, object_id) if object_id and es_superadministrador(request.user) else None
        contexto['can_cancel_unused_duplicate'] = bool(obj and obj.estado == 'PENDING_USER_REGISTRATION' and not obj.usuario_id)
        return super().changeform_view(request, object_id, form_url, contexto)

    @method_decorator(require_http_methods(['GET', 'POST']))
    def cancelar_duplicado_view(self, request, object_id):
        if not es_superadministrador(request.user):
            raise PermissionDenied('Se requiere un superadministrador activo.')
        solicitud = get_object_or_404(self.get_queryset(request), pk=object_id)
        form = CancelarDuplicadoForm(request.POST if request.method == 'POST' else None)
        bloqueo = None
        if request.method == 'POST' and form.is_valid():
            try:
                resultado = cancelar_duplicado_sin_registro(solicitud_id=solicitud.pk, actor=request.user, **form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                if not resultado.repetida:
                    self.log_change(request, solicitud, 'Duplicado cancelado antes del registro; motivo en historial de estados.')
                self.message_user(request, 'La solicitud ya estaba cancelada por esta misma accion.' if resultado.repetida
                    else 'Duplicado cancelado. Invitacion revocada y correos pendientes detenidos; historial conservado.')
                return redirect('admin:financiacion_educativa_solicitudfinanciacioneducativa_change', solicitud.pk)
        if request.method == 'GET':
            try:
                verificar_sin_avance(solicitud)
            except ValidationError as error:
                bloqueo = error.messages
        return TemplateResponse(request, 'admin/financiacion_educativa/solicitudfinanciacioneducativa/cancelar_duplicado.html',
            {**self.admin_site.each_context(request), 'opts': self.model._meta, 'original': solicitud,
                'title': 'Cancelar un duplicado sin registro', 'form': form, 'bloqueo': bloqueo})
