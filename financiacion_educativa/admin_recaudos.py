from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import models

from .models_recaudos import (
    ArchivoRecaudoEducativo, IntentoEscaneoRecaudo, CuentaRecaudoEducativa,
    ReceptorBrebEducativo, EventoReceptorBrebEducativo, ReporteRecaudoEducativo,
    ConciliacionRecaudoEducativo, RechazoReporteRecaudoEducativo,
)
from .services import recaudos
from .models_libro import PoliticaLibroEducativo, MovimientoLibroEducativo, LineaLibroEducativo
from .models_gobernanza_libro import (PropuestaPoliticaLibro, EventoPoliticaLibro,
    EventoAlcanceLibro, ControlPilotoLibro, EventoControlPilotoLibro)


class EvidenciaSoloLecturaAdmin(admin.ModelAdmin):
    actions = None

    def get_readonly_fields(self, request, obj=None):
        return ['archivo_privado' if isinstance(campo, models.FileField) else campo.name for campo in self.model._meta.fields]

    def get_fields(self, request, obj=None):
        return self.get_readonly_fields(request, obj)

    @admin.display(description='Archivo privado')
    def archivo_privado(self, obj):
        return 'Consulta protegida desde el flujo de recaudos; sin URL publica.'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for modelo in (PropuestaPoliticaLibro, EventoPoliticaLibro, EventoAlcanceLibro,
               ControlPilotoLibro, EventoControlPilotoLibro,
               PoliticaLibroEducativo, MovimientoLibroEducativo, LineaLibroEducativo,
               ArchivoRecaudoEducativo, IntentoEscaneoRecaudo, CuentaRecaudoEducativa,
               EventoReceptorBrebEducativo, ReporteRecaudoEducativo,
               ConciliacionRecaudoEducativo, RechazoReporteRecaudoEducativo):
    admin.site.register(modelo, EvidenciaSoloLecturaAdmin)


class ReceptorForm(forms.ModelForm):
    entidad = forms.CharField(max_length=80, label='Entidad bancaria (identificador estable)')
    identificador_cuenta = forms.CharField(max_length=80, label='Identificador completo y estable de cuenta')
    qr_subido = forms.FileField(label='QR oficial JPG/PNG/PDF')

    class Meta:
        model = ReceptorBrebEducativo
        fields = ('titular', 'llave')

    def clean_qr_subido(self):
        archivo = self.cleaned_data['qr_subido']
        recaudos.validar_archivo(archivo)
        return archivo

    def clean_entidad(self):
        return recaudos.normalizar_identificador_cuenta(self.cleaned_data['entidad'])

    def clean_identificador_cuenta(self):
        return recaudos.normalizar_identificador_cuenta(self.cleaned_data['identificador_cuenta'])


@admin.register(ReceptorBrebEducativo)
class ReceptorAdmin(EvidenciaSoloLecturaAdmin):
    form = ReceptorForm
    list_display = ('id', 'cuenta', 'titular', 'creado_en')
    actions = ('activar', 'deshabilitar')

    def has_add_permission(self, request):
        return request.user.is_active and request.user.has_perm(recaudos.PERMISO_CONFIGURAR)

    def get_readonly_fields(self, request, obj=None):
        return super().get_readonly_fields(request, obj) if obj else []

    def get_fields(self, request, obj=None):
        return super().get_fields(request, obj) if obj else ('entidad', 'identificador_cuenta', 'titular', 'llave', 'qr_subido')

    def save_model(self, request, obj, form, change):
        creado = recaudos.crear_receptor(actor=request.user, qr=form.cleaned_data['qr_subido'], **{
            campo: form.cleaned_data[campo] for campo in ('entidad', 'identificador_cuenta', 'titular', 'llave')
        })
        obj.__dict__.update(creado.__dict__)

    def has_configurar_permission(self, request):
        return request.user.is_active and request.user.has_perm(recaudos.PERMISO_CONFIGURAR)

    @admin.action(description='Activar version seleccionada (QR debe estar escaneado)', permissions=['configurar'])
    def activar(self, request, queryset):
        self._cambiar(request, queryset, True)

    @admin.action(description='Deshabilitar canal BRE-B', permissions=['configurar'])
    def deshabilitar(self, request, queryset):
        self._cambiar(request, queryset, False)

    def _cambiar(self, request, queryset, habilitado):
        if queryset.count() != 1:
            self.message_user(request, 'Seleccione una sola version.', messages.ERROR)
            return
        try:
            recaudos.activar_receptor(receptor_id=queryset.get().pk, actor=request.user,
                motivo='Cambio de receptor desde Django Admin por operador autorizado.', habilitado=habilitado)
        except ValidationError as error:
            self.message_user(request, '; '.join(error.messages), messages.ERROR)
