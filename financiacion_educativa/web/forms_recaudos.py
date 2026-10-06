from django import forms
from decimal import Decimal

from financiacion_educativa.models_recaudos import ReporteRecaudoEducativo
from financiacion_educativa.services.recaudos import monto_cop, fecha_transferencia


class DatosTransferenciaForm(forms.Form):
    monto = forms.DecimalField(label='Monto en COP', max_digits=16, decimal_places=2, min_value=Decimal('0.01'))
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


class ConfirmarRecaudoForm(DatosTransferenciaForm):
    fecha = forms.DateField(label='Fecha efectiva bancaria', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    referencia = forms.CharField(label='Referencia unica verificada en el banco', max_length=120)
    evidencia_id = forms.UUIDField(label='UUID de evidencia bancaria escaneada')
    observacion = forms.CharField(label='Verificacion bancaria / explicacion de diferencia de monto', max_length=1000, widget=forms.Textarea(attrs={'rows': 3}))
    justificacion_diferencia = forms.CharField(label='Si el monto difiere: explicacion obligatoria', required=False, max_length=500, widget=forms.Textarea(attrs={'rows': 2}))


class EvidenciaBancariaForm(forms.Form):
    archivo = forms.FileField(label='Evidencia del movimiento bancario', widget=forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,application/pdf'}))


class RechazarReporteForm(forms.Form):
    motivo = forms.CharField(label='Motivo para el pagador y siguiente paso', max_length=500, widget=forms.Textarea(attrs={'rows': 3}))


class FiltrosRecaudosForm(forms.Form):
    estado = forms.ChoiceField(required=False, choices=[('', 'Todos'), ('PENDING', 'Revision pendiente'), ('CONFIRMED', 'Ingreso confirmado'), ('REJECTED', 'No conciliado')])
    institucion = forms.UUIDField(required=False, label='Institucion (UUID)')
    desde = forms.DateField(required=False, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    hasta = forms.DateField(required=False, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))

    def clean(self):
        datos = super().clean()
        if datos.get('desde') and datos.get('hasta') and datos['desde'] > datos['hasta']:
            raise forms.ValidationError('La fecha inicial debe ser anterior a la final.')
        return datos
