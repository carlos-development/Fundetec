"""Read-only bank-date cohort; no projections or allocation of unapplied money."""

from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import QueryDict
from django.urls import reverse

from financiacion_educativa.models_recaudos import ConciliacionRecaudoEducativo


SEGMENTOS = (
    ('CONFIRMED', 'Confirmados en banco', Q()),
    ('TO_APPLY', 'Pendientes de aplicar', Q(aplicacion__isnull=True)),
    ('APPLIED', 'Aplicados a cuotas', Q(aplicacion__isnull=False)),
)
OPCIONES_SEGMENTO = [(codigo, etiqueta) for codigo, etiqueta, _ in SEGMENTOS]
CERO = Decimal('0.00')


def cohorte_bancaria(datos):
    consulta = ConciliacionRecaudoEducativo.objects.filter(
        fecha_efectiva__gte=datos['desde'], fecha_efectiva__lte=datos['hasta'],
    )
    if datos.get('institucion'):
        consulta = consulta.filter(reporte__solicitud__institucion_id=datos['institucion'])
    return consulta


def filtrar_segmento(consulta, segmento):
    predicado = next(predicado for codigo, _, predicado in SEGMENTOS if codigo == segmento)
    return consulta.filter(predicado)


def parametros_cohorte(datos):
    parametros = QueryDict(mutable=True)
    for campo in ('desde', 'hasta', 'institucion', 'estado'):
        if datos.get(campo):
            parametros[campo] = str(datos[campo])
    return parametros


def construir_tablero(consulta, datos):
    # All summary amounts come from one SQL statement and OneToOne joins only.
    meses = list(consulta.order_by().annotate(mes=TruncMonth('fecha_efectiva')).values('mes').annotate(
        confirmados=Sum('monto_recibido', default=CERO),
        pendientes=Sum('monto_recibido', filter=Q(aplicacion__isnull=True), default=CERO),
        aplicados=Sum('monto_recibido', filter=Q(aplicacion__isnull=False), default=CERO),
        capital=Sum('aplicacion__capital_aplicado', default=CERO),
        intereses=Sum('aplicacion__interes_aplicado', default=CERO),
        cantidad_confirmados=Count('pk'),
        cantidad_pendientes=Count('pk', filter=Q(aplicacion__isnull=True)),
        cantidad_aplicados=Count('pk', filter=Q(aplicacion__isnull=False)),
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
