from django import forms

from financiacion_educativa.choices import (
    EstadoPublicoSolicitud,
    EtapaAutomatizacionEducativa,
)


ORDENAMIENTOS = (
    ('-creada_en', 'Mas recientes'),
    ('creada_en', 'Mas antiguas'),
    ('-actualizada_en', 'Actualizadas recientemente'),
    ('institucion__nombre_comercial', 'Institucion A-Z'),
    ('referencia_externa', 'Referencia A-Z'),
    ('-valor_plan', 'Mayor valor'),
    ('valor_plan', 'Menor valor'),
)

BANDEJAS = (
    ('', 'Sin bandeja especifica'),
    ('revision_manual', 'Revision manual'),
    ('correccion', 'Correccion requerida'),
    ('error_automatizacion', 'Error de automatizacion'),
    ('documento_inconcluso', 'Validacion documental inconclusa'),
    ('firma_pendiente', 'Firma pendiente'),
    ('firma_ambigua', 'Firma en conciliacion'),
    ('correo_pendiente', 'Correo pendiente'),
    ('correo_conciliacion', 'Correo en conciliacion'),
    ('sin_movimiento', 'Sin movimiento'),
)

FILTROS_AVANZADOS = (
    'programa',
    'periodo',
    'sede',
    'etapa',
    'excepcion',
    'desde',
    'hasta',
    'orden',
)


class FiltrosSolicitudesOperativasForm(forms.Form):
    q = forms.CharField(
        required=False,
        max_length=120,
        label='Buscar',
        widget=forms.TextInput(attrs={
            'placeholder': 'Referencia, persona o documento',
            'autocomplete': 'off',
        }),
    )
    institucion = forms.ChoiceField(
        required=False,
        label='Institucion',
    )
    estado = forms.ChoiceField(
        required=False,
        label='Estado publico',
        choices=(('', 'Todos los estados'),) + tuple(
            EstadoPublicoSolicitud.choices
        ),
    )
    programa = forms.ChoiceField(required=False, label='Programa')
    periodo = forms.ChoiceField(required=False, label='Periodo academico')
    sede = forms.ChoiceField(required=False, label='Sede')
    etapa = forms.ChoiceField(
        required=False,
        label='Etapa operativa',
        choices=(('', 'Todas las etapas'),) + tuple(
            EtapaAutomatizacionEducativa.choices
        ),
    )
    excepcion = forms.ChoiceField(
        required=False,
        label='Con excepcion',
        choices=(('', 'Todos'), ('si', 'Si'), ('no', 'No')),
    )
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
        choices=ORDENAMIENTOS,
        initial='-creada_en',
    )
    bandeja = forms.ChoiceField(
        required=False,
        choices=BANDEJAS,
        widget=forms.HiddenInput,
    )
    page = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.HiddenInput,
    )

    def __init__(self, *args, opciones=None, **kwargs):
        super().__init__(*args, **kwargs)
        opciones = opciones or {}
        self.fields['institucion'].choices = self._choices(
            opciones.get('instituciones', ()),
            'Todas las instituciones',
        )
        self.fields['programa'].choices = self._choices(
            opciones.get('programas', ()),
            'Todos los programas',
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
    def _choices(valores, vacio):
        return [('', vacio), *valores]

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
