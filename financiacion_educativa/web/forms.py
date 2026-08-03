from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils import timezone

from financiacion_educativa.choices import (
    RelacionEstudiante,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)
from financiacion_educativa.models import ParticipanteFinanciacion
from financiacion_educativa.services.participantes import solicitud_requiere_tutor


class AccesoFinanciacionForm(AuthenticationForm):
    username = forms.EmailField(
        label='Correo',
        widget=forms.EmailInput(
            attrs={
                'class': 'form-control',
                'autocomplete': 'email',
                'placeholder': 'tu@correo.com',
            }
        ),
    )
    password = forms.CharField(
        label='Contrasena',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'autocomplete': 'current-password',
                'placeholder': 'Ingresa tu contrasena',
            }
        ),
    )

    def clean(self):
        identificador = (self.cleaned_data.get('username') or '').strip().lower()
        if identificador:
            User = get_user_model()
            usuario = User.objects.filter(email__iexact=identificador).first()
            if usuario:
                self.cleaned_data['username'] = usuario.get_username()
        return super().clean()


class RegistroFinanciacionForm(UserCreationForm):
    email = forms.EmailField(
        label='Correo',
        widget=forms.EmailInput(
            attrs={
                'class': 'form-control',
                'autocomplete': 'email',
                'placeholder': 'tu@correo.com',
            }
        ),
    )
    first_name = forms.CharField(
        label='Nombre',
        max_length=150,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control',
                'autocomplete': 'given-name',
            }
        ),
    )
    last_name = forms.CharField(
        label='Apellido',
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control',
                'autocomplete': 'family-name',
            }
        ),
    )
    password1 = forms.CharField(
        label='Contrasena',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'autocomplete': 'new-password',
            }
        ),
    )
    password2 = forms.CharField(
        label='Confirmar contrasena',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'autocomplete': 'new-password',
            }
        ),
    )

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ('email', 'first_name', 'last_name')

    def __init__(self, *args, expected_email=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.expected_email = (expected_email or '').strip().casefold()

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        User = get_user_model()
        if not email or User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                'No fue posible crear la cuenta. Inicia sesion o recupera tu acceso.'
            )
        if self.expected_email and email.casefold() != self.expected_email:
            raise forms.ValidationError(
                'Usa el mismo correo al que fue enviada la invitacion.'
            )
        return email

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.email = self.cleaned_data['email']
        usuario.username = usuario.email
        usuario.first_name = (self.cleaned_data.get('first_name') or '').strip()[:150]
        usuario.last_name = (self.cleaned_data.get('last_name') or '').strip()[:150]
        if commit:
            usuario.save()
        return usuario


class EstudianteFinanciacionForm(forms.Form):
    tipo_documento = forms.ChoiceField(
        label='Tipo de identificacion',
        choices=TipoDocumentoIdentidad.choices,
    )
    numero_documento = forms.CharField(
        label='Numero de identificacion',
        max_length=40,
    )
    pais_expedicion = forms.CharField(
        label='Pais de expedicion (codigo de dos letras)',
        max_length=2,
        required=False,
        initial='CO',
    )
    fecha_nacimiento = forms.DateField(
        label='Fecha de nacimiento declarada',
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(
            format='%Y-%m-%d',
            attrs={'type': 'date'},
        ),
    )


class TutorFinanciacionForm(forms.Form):
    nombres = forms.CharField(label='Nombres', max_length=160)
    apellidos = forms.CharField(label='Apellidos', max_length=160)
    tipo_documento = forms.ChoiceField(
        label='Tipo de identificacion',
        choices=TipoDocumentoIdentidad.choices,
    )
    numero_documento = forms.CharField(
        label='Numero de identificacion',
        max_length=40,
    )
    pais_expedicion = forms.CharField(
        label='Pais de expedicion (codigo de dos letras)',
        max_length=2,
        required=False,
        initial='CO',
    )
    fecha_nacimiento = forms.DateField(
        label='Fecha de nacimiento declarada',
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(
            format='%Y-%m-%d',
            attrs={'type': 'date'},
        ),
    )
    correo = forms.EmailField(label='Correo', required=False)
    telefono = forms.CharField(label='Telefono', max_length=40, required=False)
    relacion_estudiante = forms.ChoiceField(
        label='Relacion con el estudiante',
        choices=[('', 'Selecciona una opcion'), *RelacionEstudiante.choices],
    )


TIPOS_DOCUMENTALES_USUARIO = (
    TipoDocumentoFinanciacion.INCOME_CERTIFICATE,
    TipoDocumentoFinanciacion.DEBTOR_IDENTIFICATION,
    TipoDocumentoFinanciacion.OTHER_EDUCATIONAL,
)


class DocumentoFinanciacionForm(forms.Form):
    tipo = forms.ChoiceField(
        label='Tipo de documento',
        choices=[
            (valor, TipoDocumentoFinanciacion(valor).label)
            for valor in TIPOS_DOCUMENTALES_USUARIO
        ],
    )
    participante = forms.ModelChoiceField(
        label='Titular del documento',
        queryset=ParticipanteFinanciacion.objects.none(),
        required=False,
        empty_label='Documento general de la solicitud',
    )
    archivo = forms.FileField(
        label='Archivo PDF, JPG o PNG',
        widget=forms.ClearableFileInput(
            attrs={'accept': '.pdf,.jpg,.jpeg,.png'}
        ),
    )

    def __init__(
        self,
        *args,
        solicitud,
        tipo_inicial='',
        participante_inicial='',
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.fields['participante'].queryset = solicitud.participantes.all()
        tipos = list(TIPOS_DOCUMENTALES_USUARIO)
        self.fields['tipo'].choices = [
            (valor, TipoDocumentoFinanciacion(valor).label)
            for valor in tipos
        ]
        if tipo_inicial in tipos:
            self.fields['tipo'].initial = tipo_inicial
        if participante_inicial:
            self.fields['participante'].initial = participante_inicial


class ReemplazoDocumentoForm(forms.Form):
    archivo = forms.FileField(
        label='Nuevo archivo PDF, JPG o PNG',
        widget=forms.ClearableFileInput(
            attrs={'accept': '.pdf,.jpg,.jpeg,.png'}
        ),
    )


class EvidenciaMatriculaForm(forms.Form):
    periodo_academico = forms.CharField(label='Periodo academico', max_length=80)
    referencia_matricula = forms.CharField(
        label='Referencia de matricula',
        max_length=120,
        required=False,
    )
    archivo = forms.FileField(
        label='Soporte de matricula (opcional)',
        required=False,
        help_text=(
            'La ficha y los datos de matricula no dependen de este adjunto.'
        ),
        widget=forms.ClearableFileInput(
            attrs={'accept': '.pdf,.jpg,.jpeg,.png'}
        ),
    )

    def __init__(
        self,
        *args,
        periodo_institucional='',
        codigo_institucional='',
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if periodo_institucional:
            self.fields['periodo_academico'].initial = periodo_institucional
            self.fields['periodo_academico'].disabled = True
        if codigo_institucional:
            self.fields['referencia_matricula'].initial = codigo_institucional
            self.fields['referencia_matricula'].disabled = True


class CrearFotografiaFinancieraForm(forms.Form):
    fecha_inicio_plan = forms.DateField(
        label='Fecha inicial del plan',
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='La primera cuota vence un mes despues de esta fecha.',
    )


class BaseProyeccionFinancieraForm(forms.Form):
    fecha_efectiva = forms.DateField(
        label='Fecha efectiva hipotetica',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    cuotas_cubiertas = forms.IntegerField(
        label='Cuotas hipoteticamente cubiertas',
        min_value=0,
        initial=0,
    )
    participante_pagante = forms.ModelChoiceField(
        label='Persona que realizaria el pago',
        queryset=ParticipanteFinanciacion.objects.none(),
        required=False,
        empty_label='Sin indicar',
    )

    def __init__(self, *args, solicitud, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['participante_pagante'].queryset = solicitud.participantes.all()


class ProyeccionAbonoForm(forms.Form):
    fecha_efectiva = forms.DateField(
        label='Fecha estimada del abono',
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='Se calcularan intereses con la politica diaria base 30.',
    )
    valor_pago = forms.DecimalField(
        label='Valor que deseas pagar',
        max_digits=14,
        decimal_places=0,
        min_value=1,
        help_text=(
            'Debe superar los intereses causados para reducir el capital.'
        ),
    )


class ProyeccionPagoTotalForm(BaseProyeccionFinancieraForm):
    pass
