"""Consulta de contratos y calendario pactado, sin contabilidad de pagos."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import OuterRef, Subquery, Sum, Count
from django.shortcuts import get_object_or_404
from django.utils import timezone

from financiacion_educativa.choices import EstadoSolicitudFinanciacion
from financiacion_educativa.models import CondicionesFinancieras, CuotaAmortizacionEducativa
from financiacion_educativa.services.estado_publico import firmas_autorizadas_reales
from .selectors import filtrar_solicitudes_operativas


def obligaciones_autorizadas():
    firmas = firmas_autorizadas_reales()
    proximas = CuotaAmortizacionEducativa.objects.filter(
        fotografia_id=OuterRef('pk'), fecha_vencimiento__gte=timezone.localdate(),
    ).order_by('fecha_vencimiento', 'numero')
    return CondicionesFinancieras.objects.filter(
        pk__in=firmas.values('artefacto__fotografia_financiera_id'),
    ).exclude(solicitud__resultado_sandbox__activo=True).select_related(
        'solicitud__institucion',
    ).annotate(
        autorizada_en=Subquery(firmas.filter(
            artefacto__fotografia_financiera_id=OuterRef('pk'),
        ).order_by('-firmado_en', '-pk').values('firmado_en')[:1]),
        proximo_vencimiento=Subquery(proximas.values('fecha_vencimiento')[:1]),
        proxima_cuota=Subquery(proximas.values('valor_cuota')[:1]),
    ).order_by('-autorizada_en', 'pk')


def expedientes_en_tramite(*, filtros):
    # APPROVED sin evidencia coherente no es una obligacion ni se oculta en tramite.
    return filtrar_solicitudes_operativas(filtros=filtros).exclude(
        estado__in=(EstadoSolicitudFinanciacion.APPROVED,
                    EstadoSolicitudFinanciacion.REJECTED,
                    EstadoSolicitudFinanciacion.CANCELLED,
                    EstadoSolicitudFinanciacion.PAID),
    ).exclude(resultado_sandbox__activo=True)


def filtrar_obligaciones(*, filtros):
    consulta = obligaciones_autorizadas()
    if filtros.get('institucion'):
        consulta = consulta.filter(solicitud__institucion_id=filtros['institucion'])
    if filtros.get('estado') and filtros['estado'] != 'APPROVED':
        return consulta.none()
    if filtros.get('desde'):
        consulta = consulta.filter(autorizada_en__date__gte=filtros['desde'])
    if filtros.get('hasta'):
        consulta = consulta.filter(autorizada_en__date__lte=filtros['hasta'])
    return consulta


def indicadores_obligaciones(consulta):
    totales = consulta.aggregate(
        cantidad=Count('pk'), solicitado=Sum('valor_financiado'), capital=Sum('capital_financiado'),
    )
    for campo in ('solicitado', 'capital'):
        totales[campo] = totales[campo] or Decimal('0')
    cuotas = CuotaAmortizacionEducativa.objects.filter(
        fotografia_id__in=consulta.values('pk'),
        fecha_vencimiento__gte=timezone.localdate(),
        fecha_vencimiento__lte=timezone.localdate() + timedelta(days=30),
    ).aggregate(cantidad=Count('pk'), importe=Sum('valor_cuota'))
    totales['vencimientos_30_dias'] = cuotas['cantidad']
    totales['cuotas_30_dias'] = cuotas['importe'] or Decimal('0')
    return totales


def indicadores_tramite(consulta):
    totales = consulta.aggregate(cantidad=Count('pk'), solicitado=Sum('valor_plan'))
    fotografias = CondicionesFinancieras.objects.filter(
        solicitud_id__in=consulta.values('pk'), activa=True, es_legado=False,
    )
    capital = fotografias.aggregate(capital=Sum('capital_financiado'), cantidad=Count('pk'))
    totales['solicitado'] = totales['solicitado'] or Decimal('0')
    totales['capital'] = capital['capital'] or Decimal('0')
    totales['con_fotografia'] = capital['cantidad']
    return totales


def obtener_obligacion(application_id):
    return get_object_or_404(
        obligaciones_autorizadas().prefetch_related('cuotas'), solicitud_id=application_id,
    )
