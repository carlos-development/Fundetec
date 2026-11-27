"""Read-only bank-date cohort; no projections or allocation of unapplied money."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Case, F, Q, Sum, Value, When
from django.http import QueryDict
from django.urls import reverse

from financiacion_educativa.models import CuotaAmortizacionEducativa
from financiacion_educativa.models_libro import LineaLibroEducativo
from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo
from financiacion_educativa.services.libro_pagos import anotar_cuotas, anotar_ingresos
from .reparto_intereses import CENTAVO, acumular_reparto, repartir_cuotas, tiene_aplicacion_registrada


SEGMENTOS = (
    ('CONFIRMED', 'Confirmados en banco', Q()),
    ('TO_APPLY', 'Pendientes de aplicar', Q(disponible_libro__gt=0)),
    ('APPLIED', 'Dinero aplicado', Q(aplicado_neto__gt=0)),
)
OPCIONES_SEGMENTO = [(codigo, etiqueta) for codigo, etiqueta, _ in SEGMENTOS]
CERO = Decimal('0.00')


def detallar_cuotas_aplicadas(pagina, convenios):
    ingresos = list(pagina)
    cuotas = defaultdict(list)
    signo = Case(When(movimiento__tipo='COMPENSATION', then=Value(-1)), default=Value(1))
    lineas = list(LineaLibroEducativo.objects.filter(
        movimiento__ingreso_id__in=[ingreso.pk for ingreso in ingresos],
        cuota__isnull=False,
    ).values('movimiento__ingreso_id', 'cuota_id', 'cuota__numero').annotate(
        aplicado=Sum(F('importe') * signo),
        interes=Sum(F('importe') * signo, filter=Q(concepto='INTEREST'), default=CERO),
    ).filter(aplicado__gt=0).order_by('cuota__numero', 'cuota_id'))
    # La cobertura de la cuota incluye abonos de otros ingresos y sus compensaciones.
    cubiertas = set(anotar_cuotas(CuotaAmortizacionEducativa.objects.filter(
        pk__in={linea['cuota_id'] for linea in lineas},
    )).filter(cubierta_libro=True).values_list('pk', flat=True))
    for linea in lineas:
        cuotas[linea['movimiento__ingreso_id']].append({
            'numero': linea['cuota__numero'],
            'cubierta': linea['cuota_id'] in cubiertas,
            'interes_neto': linea['interes'].quantize(CENTAVO),
        })
    for ingreso in ingresos:
        if hasattr(ingreso, 'aplicacion'):
            ingreso.cuotas_aplicadas = [{'numero': ingreso.aplicacion.cuota.numero, 'cubierta': True,
                                        'interes_neto': ingreso.aplicacion.interes_aplicado}]
        else:
            ingreso.cuotas_aplicadas = cuotas[ingreso.pk]
        repartir_cuotas(ingreso, convenios)


def cohorte_bancaria(datos):
    consulta = ConciliacionRecaudoEducativo.objects.filter(
        fecha_efectiva__gte=datos['desde'], fecha_efectiva__lte=datos['hasta'],
    )
    if datos.get('institucion'):
        consulta = consulta.filter(reporte__solicitud__institucion_id=datos['institucion'])
    return anotar_ingresos(consulta)


def filtrar_segmento(consulta, segmento):
    predicado = next(predicado for codigo, _, predicado in SEGMENTOS if codigo == segmento)
    return anotar_ingresos(consulta).filter(predicado)


def parametros_cohorte(datos):
    parametros = QueryDict(mutable=True)
    for campo in ('desde', 'hasta', 'institucion', 'estado'):
        if datos.get(campo):
            parametros[campo] = str(datos[campo])
    return parametros


def resumir_meses(consulta, convenios):
    # Un cursor lee cada ingreso con sus importes netos en una sola instantánea.
    filas = anotar_ingresos(consulta).order_by().annotate(aplicacion_registrada=tiene_aplicacion_registrada()).values_list(
        'fecha_efectiva', 'reporte__solicitud__institucion_id', 'monto_recibido',
        'capital_neto', 'interes_neto', 'aplicacion_registrada',
    )
    por_mes = {}
    for fecha, institucion_id, recibido, capital, interes, registrado in filas.iterator():
        inicio = fecha.replace(day=1)
        if inicio not in por_mes:
            por_mes[inicio] = dict(mes=inicio, confirmados=CERO, pendientes=CERO, aplicados=CERO,
                                   capital=CERO, intereses=CERO, cantidad_confirmados=0, cantidad_pendientes=0,
                                   cantidad_aplicados=0, reparto=None, tasas_reparto=set(), interes_sin_convenio=CERO)
        mes = por_mes[inicio]
        capital = capital.quantize(CENTAVO)
        interes = interes.quantize(CENTAVO)
        aplicado = capital + interes
        pendiente = recibido - aplicado
        mes['confirmados'] += recibido
        mes['capital'] += capital
        mes['intereses'] += interes
        mes['aplicados'] += aplicado
        mes['pendientes'] += pendiente
        mes['cantidad_confirmados'] += 1
        mes['cantidad_aplicados'] += int(aplicado > 0)
        mes['cantidad_pendientes'] += int(pendiente > 0)
        if registrado:
            acumular_reparto(mes, convenios, institucion_id, fecha, interes)
    return [por_mes[inicio] for inicio in sorted(por_mes)]


def construir_tablero(consulta, datos, convenios=None):
    meses = resumir_meses(consulta, convenios or {})
    campos = ('confirmados', 'pendientes', 'aplicados', 'capital', 'intereses')
    totales = {campo: sum((mes[campo] for mes in meses), CERO) for campo in campos}
    for campo in ('confirmados', 'pendientes', 'aplicados'):
        totales['cantidad_' + campo] = sum(mes['cantidad_' + campo] for mes in meses)
    indicadores = []
    ruta = reverse('financiacion_educativa_web:operaciones:tablero-recaudos')
    for (codigo, etiqueta, _), campo in zip(SEGMENTOS, ('confirmados', 'pendientes', 'aplicados')):
        parametros = parametros_cohorte(datos)
        parametros['estado'] = codigo
        indicadores.append({'codigo': codigo, 'etiqueta': etiqueta, 'importe': totales[campo],
            'cantidad': totales['cantidad_' + campo], 'url': ruta + '?' + parametros.urlencode() + '#movimientos',
            'activo': datos['estado'] == codigo})
    repartos = [mes['reparto'] for mes in meses if mes['reparto'] is not None]
    reparto_periodo = None
    if repartos:
        reparto_periodo = {campo: sum((r[campo] for r in repartos), CERO) for campo in ('aprobado', 'institucion')}
    tasas = {tasa for mes in meses for tasa in mes['tasas_reparto']}
    tasa = next(iter(tasas)) if len(tasas) == 1 else None
    return {'tasa_aprobado': tasa, 'tasa_institucion': 100 - tasa if tasa is not None else None,
            'varias_tasas': len(tasas) > 1, 'meses': meses, 'totales': totales, 'indicadores': indicadores, 'reparto_periodo': reparto_periodo,
            'interes_sin_convenio': sum((mes['interes_sin_convenio'] for mes in meses), CERO)}
