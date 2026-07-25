from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm


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

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        User = get_user_model()
        if not email or User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                'No fue posible crear la cuenta. Inicia sesion o recupera tu acceso.'
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
