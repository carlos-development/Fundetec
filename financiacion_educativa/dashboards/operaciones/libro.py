from uuid import uuid4

from django import forms
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from financiacion_educativa.models_libro import PoliticaLibroEducativo
from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo
from financiacion_educativa.services import libro_pagos as libro
from financiacion_educativa.services import gobernanza_libro as gobierno
from financiacion_educativa.services.aplicacion_cuotas import PERMISO_APLICAR
from financiacion_educativa.services.recaudos import PERMISO_CONSULTAR
from financiacion_educativa.web.recaudos import _contexto, _habilitado
from .permissions import requiere_permisos_operativos


class VistaPreviaLibroForm(forms.Form):
    modo = forms.ChoiceField(label='Operacion a simular', choices=libro.MODOS)
    politica = forms.ModelChoiceField(label='Politica aprobada', required=False,
        queryset=PoliticaLibroEducativo.objects.none(), empty_label='Escenario sintetico: no contabilizable')

    def __init__(self, *args, ingreso, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['politica'].queryset = gobierno.politicas_vigentes(
            ingreso.reporte.solicitud.institucion_id, ingreso.fecha_efectiva)


class ConfirmarLibroForm(forms.Form):
    token = forms.CharField(widget=forms.HiddenInput)
    clave = forms.UUIDField(widget=forms.HiddenInput)
    motivo = forms.CharField(label='Motivo y verificacion de la finalidad', max_length=1000, widget=forms.Textarea(attrs={'rows': 2}))
    confirmar = forms.BooleanField(label='Verifique la finalidad, la distribucion y el efecto proyectado.')


@never_cache
@requiere_permisos_operativos(PERMISO_CONSULTAR)
@require_GET
def consulta_view(request, ingreso_id):
    _habilitado()
    ingreso = get_object_or_404(libro.anotar_ingresos(ConciliacionRecaudoEducativo.objects.select_related(
        'reporte__solicitud__institucion', 'reporte__fotografia', 'aplicacion__cuota')), pk=ingreso_id)
    movimientos = ingreso.movimientos_libro.select_related('actor', 'politica').prefetch_related('lineas').order_by('secuencia')
    try:
        estado = libro.estado_libro(ingreso.reporte.fotografia)
    except ValidationError as error:
        estado, error_estado = None, error.messages
    else:
        error_estado = None
    ultima = ingreso.reporte.fotografia.movimientos_libro.order_by('-secuencia').first()
    compensable = ultima if (ultima and ultima.ingreso_id == ingreso.pk and ultima.tipo != 'COMPENSATION'
        and ultima.actor_id != request.user.pk) else None
    return render(request, 'financiacion_educativa/recaudos/libro.html', _contexto(request,
        ingreso=ingreso, movimientos=movimientos, estado_libro=estado, error_estado=error_estado,
        puede_aplicar=request.user.has_perm(PERMISO_APLICAR),
        compensable=compensable,
        puede_compensar=request.user.has_perm('financiacion_educativa.compensar_libro_educativo')))


@never_cache
@requiere_permisos_operativos(PERMISO_CONSULTAR, PERMISO_APLICAR)
@require_http_methods(['GET', 'POST'])
def preview_view(request, ingreso_id):
    _habilitado()
    ingreso = get_object_or_404(ConciliacionRecaudoEducativo, pk=ingreso_id)
    form = VistaPreviaLibroForm(request.GET or {'modo': 'PARTIAL'}, ingreso=ingreso)
    resultado = None
    confirmacion = None
    error = None
    if request.method == 'POST':
        confirmacion = ConfirmarLibroForm(request.POST)
        if confirmacion.is_valid():
            try:
                libro.contabilizar(ingreso_id=ingreso.pk, actor=request.user, **confirmacion.cleaned_data)
            except ValidationError as exc:
                confirmacion.add_error(None, exc)
            else:
                return redirect('financiacion_educativa_web:operaciones:libro-ingreso', ingreso_id=ingreso.pk)
    elif form.is_valid():
        try:
            resultado, token = libro.preparar_preview(ingreso_id=ingreso.pk, actor=request.user,
                modo=form.cleaned_data['modo'], politica_id=getattr(form.cleaned_data['politica'], 'pk', None))
            confirmacion = ConfirmarLibroForm(initial={'token': token, 'clave': uuid4()})
        except ValidationError as exc:
            error = exc.messages
    habilitada = False
    bloqueo = None
    if resultado and resultado['politica']:
        try:
            gobierno.exigir_contabilizacion(actor=request.user,
                institucion_id=ingreso.reporte.solicitud.institucion_id,
                fecha=ingreso.fecha_efectiva, politica_id=resultado['politica'])
        except ValidationError as exc:
            bloqueo = exc.messages
        else:
            habilitada = True
    return render(request, 'financiacion_educativa/recaudos/preview_libro.html', _contexto(request,
        ingreso=ingreso, form=form, resultado=resultado, confirmacion=confirmacion,
        habilitada=habilitada, error=error, bloqueo=bloqueo))


@never_cache
@requiere_permisos_operativos(PERMISO_CONSULTAR, 'financiacion_educativa.compensar_libro_educativo')
@require_http_methods(['GET', 'POST'])
def compensar_view(request, movimiento_id):
    from financiacion_educativa.models_libro import MovimientoLibroEducativo
    movimiento = get_object_or_404(MovimientoLibroEducativo, pk=movimiento_id)
    error = None
    form = ConfirmarLibroForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            try:
                snapshot = libro._cargar_token(form.cleaned_data['token'])
                if form.cleaned_data['confirmar'] is not True:
                    raise ValidationError('Confirme la compensacion.')
                libro.compensar(movimiento_id=movimiento.pk, actor=request.user,
                    clave=form.cleaned_data['clave'], motivo=form.cleaned_data['motivo'],
                    secuencia_esperada=snapshot.get('secuencia'), token=form.cleaned_data['token'])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                return redirect('financiacion_educativa_web:operaciones:libro-ingreso', ingreso_id=movimiento.ingreso_id)
    else:
        try:
            _, token = libro.preparar_compensacion(movimiento_id=movimiento.pk, actor=request.user)
        except ValidationError as exc:
            error = exc.messages
        else:
            form = ConfirmarLibroForm(initial={'token': token, 'clave': uuid4()})
    form.fields['confirmar'].label = 'Autorizo la compensacion completa del ultimo movimiento y su motivo.'
    return render(request, 'financiacion_educativa/recaudos/compensar_libro.html',
        _contexto(request, movimiento=movimiento, form=form, error=error))
