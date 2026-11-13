from django.core.paginator import Paginator
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from financiacion_educativa.services.recaudos import PERMISO_CONSULTAR
from financiacion_educativa.web.forms_recaudos import FiltrosTableroRecaudosForm
from financiacion_educativa.web.recaudos import _contexto, _habilitado
from .permissions import requiere_permisos_operativos
from .reporte_recaudos import CERO, cohorte_bancaria, construir_tablero, filtrar_segmento, parametros_cohorte


@never_cache
@requiere_permisos_operativos(PERMISO_CONSULTAR)
@require_GET
def tablero_view(request):
    _habilitado()
    hoy = timezone.localdate()
    parametros = request.GET.copy()
    for campo, valor in (('desde', hoy.replace(day=1).isoformat()), ('hasta', hoy.isoformat()),
                         ('estado', 'CONFIRMED')):
        parametros.setdefault(campo, valor)
    form = FiltrosTableroRecaudosForm(parametros)
    contexto = _contexto(request, seccion_operativa='tablero-recaudos', form=form)
    if not form.is_valid():
        return render(request, 'financiacion_educativa/recaudos/tablero.html', contexto, status=400)
    datos = form.cleaned_data
    consulta = cohorte_bancaria(datos)
    tablero = construir_tablero(consulta, datos)
    listado = filtrar_segmento(consulta, datos['estado'])
    campo = {'TO_APPLY': 'disponible_libro', 'APPLIED': 'aplicado_neto'}.get(datos['estado'], 'monto_recibido')
    total_listado = listado.aggregate(importe=Sum(campo, default=CERO))['importe']
    pagina = Paginator(listado.select_related('reporte__solicitud__institucion', 'aplicacion__cuota').order_by(
        '-fecha_efectiva', '-confirmada_en', 'pk'), 25).get_page(request.GET.get('page'))
    return render(request, 'financiacion_educativa/recaudos/tablero.html', {
        **contexto, **tablero, 'pagina': pagina, 'total_listado': total_listado,
        'etiqueta_listado': dict(form.fields['estado'].choices)[datos['estado']],
        'querystring': parametros_cohorte(datos).urlencode(),
    })
