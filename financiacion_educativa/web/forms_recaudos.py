from django import forms
from decimal import Decimal
from django.utils import timezone

from instituciones.models import Institucion
from financiacion_educativa.models_recaudos import ArchivoRecaudoEducativo, ReporteRecaudoEducativo
from financiacion_educativa.services.recaudos import monto_cop, fecha_transferencia, evidencias_bancarias_elegibles


class DatosTransferenciaForm(forms.Form):
    monto = forms.DecimalField(label='Monto en COP', max_digits=16, decimal_places=2, min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'inputmode': 'decimal', 'step': '0.01'}),
        help_text='Pesos colombianos, sin separadores de miles y con hasta dos decimales.')
    fecha = forms.DateField(label='Fecha de transferencia', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))

    def clean_monto(self):
        return monto_cop(self.cleaned_data['monto'])

    def clean_fecha(self):
        return fecha_transferencia(self.cleaned_data['fecha'])


class ReportarPagoForm(DatosTransferenciaForm):
    clave = forms.UUIDField(widget=forms.HiddenInput)
    receptor_id = forms.UUIDField(widget=forms.HiddenInput)
    proposito = forms.ChoiceField(label='Proposito declarado', choices=ReporteRecaudoEducativo.Proposito.choices)
    referencia = forms.CharField(label='Referencia bancaria (si esta disponible)', required=False, max_length=120)
    archivo = forms.FileField(label='Comprobante obligatorio', widget=forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,application/pdf'}))


class EvidenciaBancariaChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        fecha = timezone.localtime(obj.creado_en).strftime('%d/%m/%Y %H:%M')
        return f'{fecha} / {str(obj.pk)[:8]} / Sin hallazgos de seguridad'


class ConfirmarRecaudoForm(DatosTransferenciaForm):
    fecha = forms.DateField(label='Fecha efectiva bancaria', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    referencia = forms.CharField(label='Referencia unica verificada en el banco', max_length=120)
    evidencia_id = EvidenciaBancariaChoiceField(label='Evidencia bancaria segura',
        queryset=ArchivoRecaudoEducativo.objects.none(), empty_label='Selecciona una evidencia bancaria',
        error_messages={'invalid_choice': 'La evidencia no esta disponible para tu cuenta o no tiene un escaneo vigente limpio.'},
        help_text='Solo evidencia propia y bancaria. Reutilizar un soporte no acredita por si solo este movimiento.')
    observacion = forms.CharField(label='Observacion adicional (opcional)', required=False,
        max_length=1000, widget=forms.Textarea(attrs={'rows': 3}))
    justificacion_diferencia = forms.CharField(label='Si el monto difiere: explicacion obligatoria', required=False, max_length=500, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['evidencia_id'].queryset = evidencias_bancarias_elegibles(actor)

    def clean_evidencia_id(self):
        return self.cleaned_data['evidencia_id'].pk


class EvidenciaBancariaForm(forms.Form):
    archivo = forms.FileField(label='Evidencia del movimiento bancario', widget=forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,application/pdf'}))


class RechazarReporteForm(forms.Form):
    motivo = forms.CharField(label='Motivo para el pagador y siguiente paso', max_length=500, widget=forms.Textarea(attrs={'rows': 3}))


class FiltrosRecaudosForm(forms.Form):
    estado = forms.ChoiceField(required=False, choices=[('', 'Todos'), ('PENDING', 'Revision pendiente'), ('CONFIRMED', 'Ingreso confirmado'), ('REJECTED', 'No conciliado')])
    institucion = forms.ModelChoiceField(required=False, label='Institucion',
        queryset=Institucion.objects.none(), empty_label='Todas las instituciones')
    desde = forms.DateField(required=False, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    hasta = forms.DateField(required=False, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El dashboard operativo existente tiene alcance global por permiso.
        self.fields['institucion'].queryset = Institucion.objects.order_by('nombre_comercial', 'pk')

    def clean_institucion(self):
        institucion = self.cleaned_data['institucion']
        return institucion.pk if institucion else None

    def clean(self):
        datos = super().clean()
        if datos.get('desde') and datos.get('hasta') and datos['desde'] > datos['hasta']:
            raise forms.ValidationError('La fecha inicial debe ser anterior a la final.')
        return datos
