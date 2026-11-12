"""Read-only metrics over the portal's tenant-scoped, annotated cohort."""
from calendar import monthrange
from datetime import date

from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from financiacion_educativa.choices import EstadoSolicitudFinanciacion


INDICADORES = (
    ('recibidas', 'Solicitudes recibidas', Q()),
    ('aprobadas', 'Solicitudes aprobadas', Q(estado=EstadoSolicitudFinanciacion.APPROVED)),
    ('autorizadas', 'Cursos autorizados', Q(autorizacion_dashboard_en__isnull=False)),
    ('pendientes', 'Aprobadas con autorizaci\u00f3n pendiente',
        Q(estado=EstadoSolicitudFinanciacion.APPROVED, autorizacion_dashboard_en__isnull=True)),
)
OPCIONES_INDICADOR = tuple((codigo, etiqueta) for codigo, etiqueta, _ in INDICADORES)


def filtrar_por_indicador(consulta, codigo):
    if not codigo:
        return consulta
    return consulta.filter(dict((clave, predicado) for clave, _, predicado in INDICADORES)[codigo])


def _url_cohorte(nombre, filtros, indicador=None):
    parametros = QueryDict(mutable=True)
    for clave in ('desde', 'hasta', 'programa'):
        valor = filtros.get(clave)
        if valor:
            parametros[clave] = valor.isoformat() if isinstance(valor, date) else valor
    if indicador:
        parametros['indicador'] = indicador
    url = reverse('financiacion_educativa_web:institucion:' + nombre)
    return url + ('?' + parametros.urlencode() if parametros else '')


def url_reportes(filtros):
    return _url_cohorte('reportes', filtros)


def _presentar_cifras(totales, filtros):
    return [dict(codigo=codigo, etiqueta=etiqueta, total=totales[codigo],
        url=_url_cohorte('solicitudes', filtros, codigo)) for codigo, etiqueta, _ in INDICADORES]


def construir_reporte(consulta, filtros):
    # One SQL snapshot for both monthly series and totals; never fetch students.
    filas = list(consulta.order_by().annotate(
        mes=TruncMonth('creada_en', tzinfo=timezone.get_current_timezone()),
    ).values('mes').annotate(**{
        codigo: Count('pk', filter=predicado) for codigo, _, predicado in INDICADORES
    }).order_by('mes'))
    totales = {codigo: sum(fila[codigo] for fila in filas) for codigo, _, _ in INDICADORES}
    maximo = max((fila['recibidas'] for fila in filas), default=0) or 1
    periodos = []
    for fila in filas:
        mes = fila['mes'].date()
        cierre = date(mes.year, mes.month, monthrange(mes.year, mes.month)[1])
        cohorte_mes = {**filtros, 'desde': max(mes, filtros['desde']), 'hasta': min(cierre, filtros['hasta'])}
        cifras = _presentar_cifras(fila, cohorte_mes)
        for cifra in cifras:
            cifra['ancho'] = max(1, round(100 * cifra['total'] / maximo)) if cifra['total'] else 0
        periodos.append({'mes': mes, 'cifras': cifras})
    return {'indicadores': _presentar_cifras(totales, filtros), 'periodos': periodos,
        'desde': filtros['desde'], 'hasta': filtros['hasta'], 'consultado_en': timezone.now()}
