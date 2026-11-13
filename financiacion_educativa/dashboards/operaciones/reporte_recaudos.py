"""Read-only bank-date cohort; no projections or allocation of unapplied money."""

from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import QueryDict
from django.urls import reverse

from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo
from financiacion_educativa.services.libro_pagos import anotar_ingresos


SEGMENTOS = (
    ('CONFIRMED', 'Confirmados en banco', Q()),
    ('TO_APPLY', 'Pendientes de aplicar', Q(disponible_libro__gt=0)),
    ('APPLIED', 'Dinero aplicado', Q(aplicado_neto__gt=0)),
)
OPCIONES_SEGMENTO = [(codigo, etiqueta) for codigo, etiqueta, _ in SEGMENTOS]
CERO = Decimal('0.00')


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


def construir_tablero(consulta, datos):
    consulta = anotar_ingresos(consulta)
    # All summary amounts come from one SQL statement and OneToOne joins only.
    meses = list(consulta.order_by().annotate(mes=TruncMonth('fecha_efectiva')).values('mes').annotate(
        confirmados=Sum('monto_recibido', default=CERO),
        pendientes=Sum('disponible_libro', default=CERO),
        aplicados=Sum('aplicado_neto', default=CERO),
        capital=Sum('capital_neto', default=CERO),
        intereses=Sum('interes_neto', default=CERO),
        cantidad_confirmados=Count('pk'),
        cantidad_pendientes=Count('pk', filter=Q(disponible_libro__gt=0)),
        cantidad_aplicados=Count('pk', filter=Q(aplicado_neto__gt=0)),
    ).order_by('mes'))
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
    return {'meses': meses, 'totales': totales, 'indicadores': indicadores}
