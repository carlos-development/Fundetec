from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import F, OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from instituciones.models import Institucion
from financiacion_educativa.models_gobernanza_libro import (
    PropuestaPoliticaLibro, EventoAlcanceLibro, ControlPilotoLibro,
)
from financiacion_educativa.models_libro import PoliticaLibroEducativo
from financiacion_educativa.services import gobernanza_libro as gobierno
from financiacion_educativa.web.recaudos import _contexto
from .permissions import requiere_permisos_operativos


REGLAS = {
    'imputacion': ('Orden de imputacion', [('INTEREST_FIRST', 'Intereses y luego capital'), ('CAPITAL_FIRST', 'Capital y luego intereses')]),
    'vencidos': ('Orden de cuotas', [('OLDEST_FIRST', 'Cuota mas antigua primero')]),
    'excedente': ('Excedentes', [('UNALLOCATED', 'Sin destino; sin devolucion ni aplicacion automatica')]),
    'retroactividad': ('Fecha bancaria anterior al ultimo movimiento', [('REJECT_BEFORE_LAST', 'Bloquear y derivar a revision')]),
    'centavos': ('Precision bancaria y reproyeccion', [('EXACT_BANK_CONTRACT_HALF_UP', 'Banco exacto; bloquear residual incompatible con motor en pesos')]),
    'compensacion': ('Alcance de compensacion', [('LAST_FULL_ONLY', 'Completa del ultimo movimiento nuevo sin sucesores')]),
    'causacion': ('Causacion', [('SAVED_PERIODS_DAILY_30', 'Periodos guardados y DAILY_30, sin mora')]),
    'abono_vencido': ('Abono con vencimientos pendientes', [('BLOCK', 'Bloquear y derivar a revision')]),
    'intereses_anticipados': ('Intereses anticipados', [('BLOCK_REPROJECTION', 'Bloquear reproyeccion; no perder ni cobrar dos veces')]),
}


def instituciones_visibles(actor):
    actual = gobierno.actor_actual(actor)
    if actual.has_perm(gobierno.PERMISO_ALCANCES):
        return Institucion.objects.order_by('nombre_comercial')
    acciones = [a for a, p in gobierno.PERMISOS.items() if actual.has_perm(p)]
    actual_evento = EventoAlcanceLibro.objects.filter(usuario=actual, institucion_id=OuterRef('institucion_id'),
        accion=OuterRef('accion')).order_by('-pk')
    alcances = EventoAlcanceLibro.objects.filter(usuario=actual, accion__in=acciones).annotate(
        ultimo=Subquery(actual_evento.values('pk')[:1])).filter(pk=F('ultimo'), habilitado=True)
    return Institucion.objects.filter(pk__in=alcances.values('institucion_id')).order_by('nombre_comercial')


class InstitucionForm(forms.Form):
    institucion = forms.ModelChoiceField(queryset=Institucion.objects.none())

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['institucion'].queryset = instituciones_visibles(actor)


class PrepararForm(InstitucionForm):
    version = forms.RegexField(regex=r'^[A-Z0-9._-]+$', max_length=80, label='Version')
    vigente_desde = forms.DateField(label='Fecha bancaria desde', widget=forms.DateInput(attrs={'type': 'date'}))
    vigente_hasta = forms.DateField(required=False, label='Fecha bancaria hasta (inclusive)', widget=forms.DateInput(attrs={'type': 'date'}))
    es_sintetica = forms.BooleanField(required=False, initial=True, label='Politica exclusivamente sintetica de piloto')
    respaldo = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={'rows': 2}), label='Respaldo documental o referencia verificable')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for clave, (etiqueta, opciones) in REGLAS.items():
            self.fields[clave] = forms.ChoiceField(label=etiqueta, choices=[('', 'Seleccionar explicitamente'), *opciones])


class DecisionForm(forms.Form):
    accion = forms.ChoiceField(choices=[('REVIEW', 'Revisar favorablemente'), ('REJECT', 'Rechazar propuesta'),
        ('APPROVE', 'Aprobar version'), ('RETIRE', 'Retirar para nuevas escrituras')])
    respaldo = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={'rows': 2}))
    reemplaza = forms.ModelChoiceField(queryset=PoliticaLibroEducativo.objects.none(), required=False,
        label='Version a reemplazar (mismo intervalo completo)')
    aceptar_uso_real = forms.BooleanField(required=False, label='Confirmo el respaldo comercial y el uso real de la politica no sintetica')
    confirmar = forms.BooleanField(label='Confirmo la decision y su respaldo')


class ControlForm(InstitucionForm):
    activo = forms.ChoiceField(choices=[('OFF', 'Detener nuevas escrituras'), ('ON', 'Activar piloto limitado')])
    motivo = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={'rows': 2}))
    confirmar = forms.BooleanField(label='Confirmo el cambio del piloto')


class AlcanceForm(InstitucionForm):
    usuario = forms.ModelChoiceField(queryset=get_user_model().objects.filter(is_active=True).order_by('username'))
    accion = forms.ChoiceField(choices=EventoAlcanceLibro.Accion.choices)
    habilitado = forms.BooleanField(required=False, label='Habilitar este alcance')
    motivo = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={'rows': 2}))
    confirmar = forms.BooleanField(label='Confirmo la asignacion o revocacion')


@never_cache
@requiere_permisos_operativos()
@require_http_methods(['GET', 'POST'])
def listado_view(request):
    selector = InstitucionForm(request.GET or None, actor=request.user)
    form = PrepararForm(request.POST or None, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        datos = form.cleaned_data.copy()
        datos.pop('confirmar', None)
        reglas = {c: datos.pop(c) for c in REGLAS}
        institucion = datos.pop('institucion')
        try:
            propuesta = gobierno.preparar_politica(institucion_id=institucion.pk, reglas=reglas, actor=request.user, **datos)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('financiacion_educativa_web:operaciones:politica-detalle', propuesta_id=propuesta.pk)
    propuestas = []
    institucion = selector.cleaned_data['institucion'] if selector.is_valid() else None
    if institucion:
        gobierno.puede_consultar_politicas(request.user, institucion.pk)
        propuestas = PropuestaPoliticaLibro.objects.filter(institucion=institucion).prefetch_related('eventos').order_by('-pk')
    return render(request, 'financiacion_educativa/recaudos/politicas.html', _contexto(request,
        selector=selector, form=form, propuestas=propuestas, control=ControlPilotoLibro.objects.get(pk=1)))


@never_cache
@requiere_permisos_operativos()
@require_http_methods(['GET', 'POST'])
def detalle_view(request, propuesta_id):
    propuesta = get_object_or_404(PropuestaPoliticaLibro, pk=propuesta_id)
    gobierno.puede_consultar_politicas(request.user, propuesta.institucion_id)
    form = DecisionForm(request.POST or None)
    form.fields['reemplaza'].queryset = PoliticaLibroEducativo.objects.filter(
        institucion_id=propuesta.institucion_id, propuesta__eventos__tipo='APPROVED'
    ).exclude(propuesta__eventos__tipo='RETIRED')
    if request.method == 'POST' and request.POST.get('accion') in ('REVIEW', 'REJECT', 'APPROVE', 'RETIRE'):
        accion = 'REVIEW' if request.POST['accion'] in ('REVIEW', 'REJECT') else 'APPROVE'
        gobierno.exigir_alcance(request.user, propuesta.institucion_id, accion)
    if request.method == 'POST' and form.is_valid():
        datos = form.cleaned_data
        try:
            if datos['accion'] in ('REVIEW', 'REJECT'):
                gobierno.revisar_politica(propuesta_id=propuesta.pk, actor=request.user,
                    respaldo=datos['respaldo'], aceptar=datos['accion'] == 'REVIEW')
            elif datos['accion'] == 'APPROVE':
                gobierno.aprobar_politica(propuesta_id=propuesta.pk, actor=request.user, respaldo=datos['respaldo'],
                    reemplaza_id=getattr(datos['reemplaza'], 'pk', None), aceptar_uso_real=datos['aceptar_uso_real'])
            else:
                politica = getattr(propuesta, 'politica_aprobada', None)
                if not politica:
                    raise ValidationError('La propuesta no tiene una aprobacion que retirar.')
                gobierno.retirar_politica(politica_id=politica.pk, actor=request.user, respaldo=datos['respaldo'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('financiacion_educativa_web:operaciones:politica-detalle', propuesta_id=propuesta.pk)
    filas = [(REGLAS[k][0], dict(REGLAS[k][1])[v]) for k, v in propuesta.reglas.items()]
    return render(request, 'financiacion_educativa/recaudos/politica_detalle.html',
        _contexto(request, propuesta=propuesta, reglas=filas, eventos=propuesta.eventos.select_related('actor').order_by('pk'), form=form))


@never_cache
@requiere_permisos_operativos(gobierno.PERMISOS['CONTROL'])
@require_http_methods(['GET', 'POST'])
def control_view(request):
    form = ControlForm(request.POST or None, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        datos = form.cleaned_data
        try:
            gobierno.controlar_piloto(institucion_id=datos['institucion'].pk,
                activo=datos['activo'] == 'ON', actor=request.user, motivo=datos['motivo'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect('financiacion_educativa_web:operaciones:politicas-libro')
    return render(request, 'financiacion_educativa/recaudos/control_piloto.html',
        _contexto(request, form=form, control=ControlPilotoLibro.objects.get(pk=1)))


@never_cache
@requiere_permisos_operativos(gobierno.PERMISO_ALCANCES)
@require_http_methods(['GET', 'POST'])
def alcances_view(request):
    form = AlcanceForm(request.POST or None, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        datos = form.cleaned_data.copy()
        datos.pop('confirmar')
        gobierno.asignar_alcance(institucion_id=datos.pop('institucion').pk,
            usuario_id=datos.pop('usuario').pk, actor=request.user, **datos)
        return redirect('financiacion_educativa_web:operaciones:politicas-libro')
    return render(request, 'financiacion_educativa/recaudos/alcances_libro.html', _contexto(request, form=form))
