from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.paginator import Paginator
from django.db.models import Count, F, Min, Sum, Q
from django.http import Http404, QueryDict
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from instituciones.models import Institucion
from financiacion_educativa.models import CuotaAmortizacionEducativa
from financiacion_educativa.services import recaudos
from financiacion_educativa.services.libro_pagos import anotar_cuotas, anotar_estado_actual, anotar_ingresos, resumen_ingresos
from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo
from financiacion_educativa.services.seguimiento_cartera import ESTADOS_RECAUDO, anotar_seguimiento, reportes_abiertos
from .cartera import obligaciones_autorizadas
from .permissions import capacidades_operativas, requiere_permisos_operativos, PERMISO_SOLICITUDES


BANDEJAS = (('vencidas', 'Vencidas sin aplicaci\u00f3n'), ('primeras', 'Primeras cuotas vencidas'),
            ('hoy', 'Vence hoy'), ('proximas', 'Pr\u00f3ximos vencimientos'), ('aplicados', 'Pagos aplicados'))
FILTROS = ('institucion', 'q', 'desde', 'hasta', 'estado_operativo', 'bandeja', 'vista', 'page')


class FiltrosSeguimientoForm(forms.Form):
    institucion = forms.ModelChoiceField(label='Instituci\u00f3n', required=False,
        queryset=Institucion.objects.order_by('nombre_comercial', 'pk'), empty_label='Todas las instituciones')
    q = forms.CharField(label='Referencia de obligaci\u00f3n', required=False, max_length=120)
    desde = forms.DateField(label='Vencimiento desde', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    hasta = forms.DateField(label='Vencimiento hasta', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    estado_operativo = forms.ChoiceField(label='Situaci\u00f3n del recaudo', required=False,
        choices=(('', 'Todas las situaciones'), *ESTADOS_RECAUDO))
    bandeja = forms.ChoiceField(choices=BANDEJAS, widget=forms.HiddenInput)
    vista = forms.ChoiceField(choices=(('cuotas', 'Por cuota'), ('obligaciones', 'Por obligaci\u00f3n')),
        widget=forms.HiddenInput)

    def clean(self):
        datos = super().clean()
        if datos.get('desde') and datos.get('hasta') and datos['desde'] > datos['hasta']:
            raise forms.ValidationError('La fecha inicial debe ser anterior a la final.')
        return datos


def parametros(datos, **cambios):
    salida = QueryDict(mutable=True)
    for campo in FILTROS:
        if datos.get(campo):
            valor = datos[campo]
            salida[campo] = str(getattr(valor, 'pk', valor))
    for campo, valor in cambios.items():
        if valor is None:
            salida.pop(campo, None)
        else:
            salida[campo] = str(valor)
    return salida


def enlace(nombre, datos, args=(), **cambios):
    query = parametros(datos, **cambios).urlencode()
    return reverse('financiacion_educativa_web:operaciones:' + nombre, args=args) + ('?' + query if query else '')


def regreso_seguimiento(request):
    # Fixed route and a closed whitelist, never an arbitrary next/return URL.
    if request.GET.get('origen') != 'cartera':
        return None
    return enlace('seguimiento-cartera', {k: request.GET[k] for k in FILTROS if k in request.GET})


def cuotas_seguimiento(datos, hoy):
    base = anotar_cuotas(CuotaAmortizacionEducativa.objects.filter(
        fotografia_id__in=obligaciones_autorizadas().values('pk'),
    ))
    # A superseding projection is not an unpaid copy of the original schedule.
    base = anotar_estado_actual(base, fotografia_ref='fotografia_id').filter(
        Q(estado_actual_libro__isnull=True) | ~Q(estado_actual_libro__proyectado=True))
    if datos.get('institucion'):
        base = base.filter(fotografia__solicitud__institucion=datos['institucion'])
    if datos.get('q'):
        base = base.filter(fotografia__solicitud__referencia_externa__icontains=datos['q'])
    if datos.get('desde'):
        base = base.filter(fecha_vencimiento__gte=datos['desde'])
    if datos.get('hasta'):
        base = base.filter(fecha_vencimiento__lte=datos['hasta'])
    base = anotar_seguimiento(base, fotografia_ref='fotografia_id',
        solicitud_ref='fotografia__solicitud_id', hoy=hoy)
    if datos.get('estado_operativo'):
        base = base.filter(estado_recaudo=datos['estado_operativo'])
    return base


def seleccionar_bandeja(base, bandeja, hoy):
    if bandeja == 'aplicados':
        return base.filter(Q(capital_cubierto__gt=0) | Q(interes_cubierto__gt=0))
    base = base.filter(cubierta_libro=False)
    if bandeja == 'primeras':
        return base.filter(numero=1, fecha_vencimiento__lt=hoy)
    if bandeja == 'hoy':
        return base.filter(fecha_vencimiento=hoy)
    if bandeja == 'proximas':
        return base.filter(fecha_vencimiento__gt=hoy, fecha_vencimiento__lte=hoy + timedelta(days=30))
    return base.filter(fecha_vencimiento__lt=hoy)


def resumen(consulta, aplicada=False):
    campos = dict(cuotas=Count('pk'), obligaciones=Count('fotografia_id', distinct=True),
        importe=Sum('valor_cuota', default=Decimal('0.00')))
    if aplicada:
        campos.update(capital=Sum('capital_cubierto', default=Decimal('0.00')),
            intereses=Sum('interes_cubierto', default=Decimal('0.00')))
    resultado = consulta.aggregate(**campos)
    if aplicada:
        resultado['importe'] = resultado['capital'] + resultado['intereses']
    return resultado


def _contexto(request, **kwargs):
    return {'seccion_operativa': 'seguimiento-cartera',
        'capacidades_operativas': capacidades_operativas(request.user), **kwargs}


def _formulario(request):
    datos = request.GET.copy()
    datos.setdefault('bandeja', 'vencidas')
    datos.setdefault('vista', 'cuotas')
    return FiltrosSeguimientoForm(datos)


@never_cache
@requiere_permisos_operativos(PERMISO_SOLICITUDES, recaudos.PERMISO_CONSULTAR)
@require_GET
def seguimiento_view(request):
    hoy, form = timezone.localdate(), _formulario(request)
    if not form.is_valid():
        return render(request, 'financiacion_educativa/dashboards/operaciones/seguimiento.html',
            _contexto(request, form=form), status=400)
    datos = {**form.cleaned_data, 'page': request.GET.get('page', '1')}
    proyectadas = anotar_estado_actual(anotar_seguimiento(obligaciones_autorizadas(),
        fotografia_ref='pk', solicitud_ref='solicitud_id', hoy=hoy)).filter(estado_actual_libro__proyectado=True)
    if datos.get('institucion'):
        proyectadas = proyectadas.filter(solicitud__institucion=datos['institucion'])
    if datos.get('q'):
        proyectadas = proyectadas.filter(solicitud__referencia_externa__icontains=datos['q'])
    if datos.get('estado_operativo'):
        proyectadas = proyectadas.filter(estado_recaudo=datos['estado_operativo'])
    proyecciones = []
    for foto in proyectadas:
        plan = foto.estado_actual_libro['plan']
        primera = plan[0] if plan else None
        if (datos.get('desde') or datos.get('hasta')) and not any(
            (not datos.get('desde') or fila['fecha'] >= datos['desde'].isoformat()) and
            (not datos.get('hasta') or fila['fecha'] <= datos['hasta'].isoformat()) for fila in plan):
            continue
        proyecciones.append({'referencia': foto.solicitud.referencia_externa,
            'capital': foto.estado_actual_libro['capital'], 'primera': primera,
            'cantidad': len(plan), 'url': enlace('seguimiento-detalle', datos, args=[foto.solicitud_id])})
    base = cuotas_seguimiento(datos, hoy)
    indicadores = []
    for codigo, nombre in BANDEJAS:
        cifras = resumen(seleccionar_bandeja(base, codigo, hoy), codigo == 'aplicados')
        indicadores.append({'codigo': codigo, 'nombre': nombre, **cifras,
            'url': enlace('seguimiento-cartera', datos, bandeja=codigo, vista='cuotas', page=None),
            'activo': datos['bandeja'] == codigo})
    seleccion = seleccionar_bandeja(base, datos['bandeja'], hoy)
    totales = resumen(seleccion, datos['bandeja'] == 'aplicados')
    fotos = anotar_seguimiento(obligaciones_autorizadas().filter(pk__in=seleccion.values('fotografia_id')),
        fotografia_ref='pk', solicitud_ref='solicitud_id', hoy=hoy)
    senales = fotos.aggregate(ingresos=Sum('ingresos_abiertos', default=0),
        reportes=Sum('reportes_abiertos', default=0), importe=Sum('importe_ingresos', default=Decimal('0.00')))
    if datos['vista'] == 'obligaciones':
        listado = seleccion.order_by().values('fotografia_id', 'fotografia__solicitud_id',
            'fotografia__solicitud__referencia_externa', 'fotografia__solicitud__institucion__nombre_comercial',
            'primera_pendiente', 'primera_fecha', 'primera_importe', 'vencidas_obligacion',
            'ingresos_abiertos', 'reportes_abiertos', 'importe_ingresos', 'estado_recaudo', 'prioridad',
        ).annotate(cantidad=Count('pk'), importe=Sum('valor_cuota'), fecha=Min('fecha_vencimiento')).order_by(
            'fecha', 'prioridad', 'fotografia_id')
        if datos['bandeja'] == 'aplicados':
            listado = listado.annotate(capital=Sum('capital_cubierto'),
                intereses=Sum('interes_cubierto')).annotate(importe=F('capital') + F('intereses'))
    else:
        listado = seleccion.select_related('fotografia__solicitud__institucion', 'aplicacion_recaudo').order_by(
            'fecha_vencimiento', 'prioridad', 'fotografia_id', 'numero')
    pagina = Paginator(listado, 25).get_page(request.GET.get('page'))
    filas = []
    for fila in pagina:
        if datos['vista'] == 'obligaciones':
            dato = {**fila, 'solicitud_id': fila['fotografia__solicitud_id'],
                'referencia': fila['fotografia__solicitud__referencia_externa'],
                'institucion': fila['fotografia__solicitud__institucion__nombre_comercial']}
        else:
            dato = {'solicitud_id': fila.fotografia.solicitud_id, 'referencia': fila.fotografia.solicitud.referencia_externa,
                'institucion': fila.fotografia.solicitud.institucion.nombre_comercial,
                'numero': fila.numero, 'fecha': fila.fecha_vencimiento, 'importe': fila.valor_cuota,
                'estado_recaudo': fila.estado_recaudo, 'primera_pendiente': fila.primera_pendiente}
            if datos['bandeja'] == 'aplicados':
                aplicacion = getattr(fila, 'aplicacion_recaudo', None)
                dato.update(importe=fila.capital_cubierto + fila.interes_cubierto,
                    capital=fila.capital_cubierto, intereses=fila.interes_cubierto,
                    fecha_bancaria=aplicacion.fecha_efectiva if aplicacion else None)
        dato.update(dias=max(0, (hoy - dato['fecha']).days),
            vencida=dato['fecha'] < hoy, vence_hoy=dato['fecha'] == hoy,
            estado=dict(ESTADOS_RECAUDO)[dato['estado_recaudo']],
            url=enlace('seguimiento-detalle', datos, args=[dato['solicitud_id']]))
        filas.append(dato)
    rapidos = []
    for nombre, dias in (('Vence hoy', 0), ('Pr\u00f3ximos 7 d\u00edas', 7), ('Pr\u00f3ximos 30 d\u00edas', 30)):
        rapidos.append({'nombre': nombre, 'url': enlace('seguimiento-cartera', datos,
            bandeja='hoy' if dias == 0 else 'proximas', desde=hoy if dias == 0 else hoy + timedelta(days=1),
            hasta=hoy + timedelta(days=dias), page=None)})
    return render(request, 'financiacion_educativa/dashboards/operaciones/seguimiento.html', _contexto(request,
        form=form, indicadores=indicadores, totales=totales, senales=senales, pagina=pagina, filas=filas,
        titulo=dict(BANDEJAS)[datos['bandeja']], aplicada=datos['bandeja'] == 'aplicados',
        por_obligacion=datos['vista'] == 'obligaciones', rapidos=rapidos,
        vista_cuotas=enlace('seguimiento-cartera', datos, vista='cuotas', page=None),
        vista_obligaciones=enlace('seguimiento-cartera', datos, vista='obligaciones', page=None),
        querystring=parametros(datos, page=None).urlencode(), proyecciones=proyecciones))


@never_cache
@requiere_permisos_operativos(PERMISO_SOLICITUDES, recaudos.PERMISO_CONSULTAR)
@require_GET
def detalle_view(request, application_id):
    form = _formulario(request)
    if not form.is_valid():
        raise Http404
    datos = {**form.cleaned_data, 'page': request.GET.get('page', '1')}
    foto = get_object_or_404(anotar_estado_actual(anotar_seguimiento(obligaciones_autorizadas(), fotografia_ref='pk',
        solicitud_ref='solicitud_id', hoy=timezone.localdate())), solicitud_id=application_id)
    pendientes = list(anotar_cuotas(foto.cuotas.all()).filter(cubierta_libro=False).order_by('numero'))
    movimientos = list(reportes_abiertos(foto).select_related('conciliacion').order_by('creado_en', 'pk'))
    disponibles = dict(anotar_ingresos(ConciliacionRecaudoEducativo.objects.filter(
        reporte__fotografia=foto)).values_list('pk', 'disponible_libro'))
    abiertos = []
    primera = pendientes[0] if pendientes else None
    for reporte in movimientos:
        ingreso = getattr(reporte, 'conciliacion', None)
        revision = bool(ingreso and (not primera or not reporte.admite_aplicacion_cuota
            or ingreso.monto_recibido != primera.valor_cuota or ingreso.monto_recibido != reporte.monto_reportado))
        abiertos.append({'importe': disponibles[ingreso.pk] if ingreso else reporte.monto_reportado,
            'confirmado': ingreso is not None, 'revision': revision,
            'url': enlace('recaudo-detalle', datos, args=[reporte.pk]) + '&origen=cartera'})
    abiertos.sort(key=lambda m: not m['confirmado'])
    url_obligacion = enlace('obligacion-detalle', datos, args=[application_id]) + '&origen=cartera'
    if len(abiertos) > 1:
        accion, url_accion = 'Ver recaudos abiertos', '#recaudos-abiertos'
    elif abiertos:
        accion = 'Ver ingreso por aplicar' if abiertos[0]['confirmado'] else 'Revisar reporte'
        url_accion = abiertos[0]['url']
    else:
        accion, url_accion = 'Ver obligaci\u00f3n', url_obligacion
    ingresos_libro = ConciliacionRecaudoEducativo.objects.filter(reporte__fotografia=foto)
    return render(request, 'financiacion_educativa/dashboards/operaciones/seguimiento_detalle.html',
        _contexto(request, fotografia=foto, pendientes=pendientes, abiertos=abiertos,
            resumen_libro=resumen_ingresos(ingresos_libro),
            proyeccion_libro=foto.estado_actual_libro if foto.estado_actual_libro and foto.estado_actual_libro['proyectado'] else None,
            libro_proyectado=foto.movimientos_libro.exists(),
            estado=dict(ESTADOS_RECAUDO)[foto.estado_recaudo], accion=accion, url_accion=url_accion,
            vuelta=enlace('seguimiento-cartera', datos), url_obligacion=url_obligacion, hoy=timezone.localdate()))
