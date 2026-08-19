from django import forms

from financiacion_educativa.choices import (
    EstadoValidacionDocumento,
    EstadoPublicoSolicitud,
    EtapaAutomatizacionEducativa,
    MotivoRechazoDocumento,
    TipoDocumentoFinanciacion,
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


class FiltrosRevisionDocumentalForm(forms.Form):
    q = forms.CharField(
        required=False,
        max_length=120,
        label='Buscar',
        widget=forms.TextInput(attrs={
            'placeholder': 'Referencia o solicitante',
            'autocomplete': 'off',
        }),
    )
    institucion = forms.ChoiceField(required=False, label='Institucion')
    tipo = forms.ChoiceField(
        required=False,
        label='Tipo documental',
        choices=(('', 'Todos los tipos'),) + tuple(
            TipoDocumentoFinanciacion.choices
        ),
    )
    estado = forms.ChoiceField(
        required=False,
        label='Estado',
        choices=(('', 'Todos los estados'),) + tuple(
            EstadoValidacionDocumento.choices
        ),
    )
    hallazgo = forms.CharField(
        required=False,
        max_length=80,
        label='Hallazgo controlado',
    )
    desde = forms.DateField(
        required=False,
        label='Cargado desde',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    hasta = forms.DateField(
        required=False,
        label='Cargado hasta',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    antiguedad = forms.ChoiceField(
        required=False,
        label='Antiguedad',
        choices=(
            ('', 'Cualquier antiguedad'),
            ('24', 'Mas de 24 horas'),
            ('72', 'Mas de 72 horas'),
            ('168', 'Mas de 7 dias'),
        ),
    )
    orden = forms.ChoiceField(
        required=False,
        label='Ordenar por',
        choices=(
            ('cargado_en', 'Mas antiguos'),
            ('-cargado_en', 'Mas recientes'),
            ('solicitud__institucion__nombre_comercial', 'Institucion A-Z'),
            ('tipo', 'Tipo documental A-Z'),
        ),
        initial='cargado_en',
    )
    page = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.HiddenInput,
    )

    def __init__(self, *args, instituciones=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['institucion'].choices = [
            ('', 'Todas las instituciones'),
            *instituciones,
        ]

    @property
    def filtros_avanzados_abiertos(self):
        return bool(self.cantidad_filtros_avanzados_activos)

    @property
    def cantidad_filtros_avanzados_activos(self):
        return sum(
            bool(str(self.data.get(nombre, '')).strip())
            for nombre in ('estado', 'hallazgo', 'desde', 'hasta', 'antiguedad', 'orden')
        ) if self.is_bound else 0

    def clean_hallazgo(self):
        valor = (self.cleaned_data.get('hallazgo') or '').strip().upper()
        if valor and not valor.replace('_', '').replace('-', '').isalnum():
            raise forms.ValidationError('Usa un codigo de hallazgo valido.')
        return valor

    def clean(self):
        datos = super().clean()
        if datos.get('desde') and datos.get('hasta') and datos['desde'] > datos['hasta']:
            raise forms.ValidationError(
                'La fecha inicial no puede ser posterior a la fecha final.'
            )
        return datos


class AceptarDocumentoOperativoForm(forms.Form):
    observacion = forms.CharField(
        required=False,
        max_length=500,
        label='Observacion de la revision',
        widget=forms.Textarea(attrs={'rows': 3}),
    )


class CorreccionDocumentoOperativoForm(forms.Form):
    motivo = forms.ChoiceField(
        choices=MotivoRechazoDocumento.choices,
        label='Motivo',
    )
    mensaje_solicitante = forms.CharField(
        max_length=500,
        label='Indicacion para el solicitante',
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    nota_interna = forms.CharField(
        required=False,
        max_length=1000,
        label='Nota interna',
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    def clean(self):
        datos = super().clean()
        for campo in ('mensaje_solicitante', 'nota_interna'):
            valor = datos.get(campo) or ''
            if '<' in valor or '>' in valor:
                self.add_error(campo, 'No se admite contenido HTML.')
        return datos
