"""Read-only operational signals; bank movements belong to a contract, not a cuota."""
from decimal import Decimal

from django.db.models import Case, CharField, Count, IntegerField, OuterRef, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce

from financiacion_educativa.models import CuotaAmortizacionEducativa
from financiacion_educativa.models_recaudos import ReporteRecaudoEducativo


ESTADOS_RECAUDO = (('INCOME', 'Ingreso por aplicar'), ('REPORT', 'Reporte por verificar'),
                  ('NONE', 'Sin recaudo abierto'))


def reportes_abiertos(fotografia):
    return ReporteRecaudoEducativo.objects.filter(
        fotografia_id=fotografia.pk, solicitud_id=fotografia.solicitud_id,
        conciliacion__aplicacion__isnull=True, rechazo__isnull=True,
    )


def anotar_seguimiento(consulta, *, fotografia_ref, solicitud_ref, hoy):
    movimientos = ReporteRecaudoEducativo.objects.filter(
        fotografia_id=OuterRef(fotografia_ref), solicitud_id=OuterRef(solicitud_ref),
        rechazo__isnull=True, conciliacion__aplicacion__isnull=True,
    ).order_by()
    ingresos = movimientos.filter(conciliacion__isnull=False)
    reportes = movimientos.filter(conciliacion__isnull=True)
    pendientes = CuotaAmortizacionEducativa.objects.filter(
        fotografia_id=OuterRef(fotografia_ref), aplicacion_recaudo__isnull=True,
    ).order_by('numero')

    def cantidad(subconsulta):
        return Coalesce(Subquery(subconsulta.values('fotografia_id').annotate(n=Count('pk')).values('n')),
            Value(0), output_field=IntegerField())

    consulta = consulta.annotate(
        ingresos_abiertos=cantidad(ingresos), reportes_abiertos=cantidad(reportes),
        importe_ingresos=Coalesce(Subquery(ingresos.values('fotografia_id').annotate(
            total=Sum('conciliacion__monto_recibido')).values('total')), Decimal('0.00')),
        primera_pendiente=Subquery(pendientes.values('numero')[:1]),
        primera_fecha=Subquery(pendientes.values('fecha_vencimiento')[:1]),
        primera_importe=Subquery(pendientes.values('valor_cuota')[:1]),
        vencidas_obligacion=cantidad(pendientes.filter(fecha_vencimiento__lt=hoy).order_by()),
    )
    return consulta.annotate(
        estado_recaudo=Case(When(ingresos_abiertos__gt=0, then=Value('INCOME')),
            When(reportes_abiertos__gt=0, then=Value('REPORT')), default=Value('NONE'), output_field=CharField()),
        prioridad=Case(When(ingresos_abiertos__gt=0, then=Value(1)),
            When(reportes_abiertos__gt=0, then=Value(2)), default=Value(3), output_field=IntegerField()),
    )


def etiqueta_vencimiento(fecha, hoy):
    return 'Vencida' if fecha < hoy else 'Vence hoy' if fecha == hoy else 'Pr\u00f3ximo vencimiento'
