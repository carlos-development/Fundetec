from django import forms

from financiacion_educativa.choices import EstadoPublicoSolicitud


ORDENAMIENTOS_SOLICITUDES = (
    ('-creada_en', 'Mas recientes'),
    ('creada_en', 'Mas antiguas'),
    ('-actualizada_en', 'Actualizadas recientemente'),
    ('referencia_externa', 'Referencia A-Z'),
    ('nombre_curso', 'Curso A-Z'),
    ('-valor_plan', 'Mayor valor del plan'),
    ('valor_plan', 'Menor valor del plan'),
)

FILTROS_AVANZADOS = (
    'programa',
    'periodo',
    'sede',
    'desde',
    'hasta',
    'orden',
)


class FiltrosSolicitudesInstitucionalesForm(forms.Form):
    q = forms.CharField(
        required=False,
        max_length=120,
        label='Buscar',
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Referencia, nombre o correo',
                'autocomplete': 'off',
            }
        ),
    )
    estado = forms.ChoiceField(
        required=False,
        label='Estado',
        choices=(('', 'Todos los estados'),) + tuple(EstadoPublicoSolicitud.choices),
    )
    programa = forms.ChoiceField(required=False, label='Curso')
    periodo = forms.ChoiceField(required=False, label='Periodo academico')
    sede = forms.ChoiceField(required=False, label='Sede')
    desde = forms.DateField(
        required=False,
        label='Creada desde',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    hasta = forms.DateField(
        required=False,
        label='Creada hasta',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    orden = forms.ChoiceField(
        required=False,
        label='Ordenar por',
        choices=ORDENAMIENTOS_SOLICITUDES,
        initial='-creada_en',
    )
    page = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.HiddenInput,
    )

    def __init__(self, *args, opciones=None, **kwargs):
        super().__init__(*args, **kwargs)
        opciones = opciones or {}
        self.fields['programa'].choices = self._choices(
            opciones.get('programas', ()),
            'Todos los cursos',
        )
        self.fields['periodo'].choices = self._choices(
            opciones.get('periodos', ()),
            'Todos los periodos',
        )
        self.fields['sede'].choices = self._choices(
            opciones.get('sedes', ()),
            'Todas las sedes',
        )

    @staticmethod
    def _choices(valores, etiqueta_vacia):
        return [('', etiqueta_vacia), *((valor, valor) for valor in valores)]

    @property
    def cantidad_filtros_avanzados_activos(self):
        if not self.is_bound:
            return 0
        return sum(
            bool(str(self.data.get(nombre, '')).strip())
            for nombre in FILTROS_AVANZADOS
        )

    @property
    def filtros_avanzados_abiertos(self):
        return bool(self.cantidad_filtros_avanzados_activos)

    def clean_q(self):
        return ' '.join((self.cleaned_data.get('q') or '').split())

    def clean(self):
        datos = super().clean()
        desde = datos.get('desde')
        hasta = datos.get('hasta')
        if desde and hasta and desde > hasta:
            raise forms.ValidationError(
                'La fecha inicial no puede ser posterior a la fecha final.'
            )
        return datos
